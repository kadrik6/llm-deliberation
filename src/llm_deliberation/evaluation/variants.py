"""Controlled pipeline variants for the evaluation harness (Part B): SINGLE,
DUAL, CRITIQUE, FULL. See this module's design rationale in the task
history / AUDIT_REPORT.md's next-phase report (Part D, item 6).

Design principle: every stage a variant shares with production reuses the
real production execution primitive (`orchestrator.run_stage`, for
SINGLE/DUAL/CRITIQUE) or the real production entry point
(`service.DeliberationService.create_run`/`start_run`, for FULL) --
never a re-implementation. Only the "combine into a final answer" step for
SINGLE/DUAL/CRITIQUE is evaluation-only, since production's own synthesis
prompt assumes prerequisites (revisions, possibly convergence/red-team
context) these reduced variants never produce. See evaluation/prompts.py
for that one new prompt, and its docstring for why it is kept separate from
production prompts.py rather than reusing synthesis() out of context.

No fabricated artifacts: a stage this variant does not run is never
represented as having run, at any level (StageRecord-like results, the
final prompt, or reported metadata).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from llm_deliberation.config import Settings
from llm_deliberation.cost_budget import RunBudgetGuard
from llm_deliberation.evaluation.prompts import single_direct_answer, variant_synthesis
from llm_deliberation.orchestrator import DeliberationOrchestrator, default_working_language
from llm_deliberation.providers import ProviderGenerationError
from llm_deliberation.types import ModelResponse

VARIANT_NAMES: tuple[str, ...] = ("SINGLE", "DUAL", "CRITIQUE", "FULL")

# Which real production stages each variant actually runs, in order. FULL is
# deliberately not listed with an explicit stage set here -- it always runs
# the real, current production stage graph (orchestrator.ALL_STAGE_NAMES /
# WAVES) via DeliberationService, so there is nothing to keep in sync by
# hand; see run_full_variant.
VARIANT_STAGES: dict[str, tuple[str, ...]] = {
    "SINGLE": ("single_answer",),
    "DUAL": ("analysis_a", "analysis_b", "final_answer"),
    "CRITIQUE": (
        "analysis_a", "analysis_b",
        "critique_a_of_b", "critique_b_of_a",
        "revision_a", "revision_b",
        "final_answer",
    ),
}


@dataclass(slots=True)
class EvalStageResult:
    stage_name: str
    provider: str | None
    requested_model: str | None
    actual_model: str | None
    input_tokens: int
    output_tokens: int
    attempt_count: int
    cost_usd: float
    status: str  # "succeeded" | "failed"
    truncated: bool
    text: str | None
    error: str | None = None


@dataclass(slots=True)
class EvalRunResult:
    variant: str
    stages: list[EvalStageResult] = field(default_factory=list)
    final_output: str | None = None
    status: str = "succeeded"  # "succeeded" | "failed"
    quality_state: str | None = None
    convergence_status: str | None = None
    material_change_count: int | None = None
    duration_seconds: float = 0.0
    production_run_id: str | None = None  # FULL only -- see run_full_variant

    @property
    def total_calls(self) -> int:
        return sum(s.attempt_count for s in self.stages)

    @property
    def total_input_tokens(self) -> int:
        return sum(s.input_tokens for s in self.stages)

    @property
    def total_output_tokens(self) -> int:
        return sum(s.output_tokens for s in self.stages)

    @property
    def total_cost_usd(self) -> float:
        return sum(s.cost_usd for s in self.stages)


def _to_eval_stage_result(stage_name: str, response: ModelResponse) -> EvalStageResult:
    return EvalStageResult(
        stage_name=stage_name,
        provider=response.provider,
        requested_model=response.requested_model,
        actual_model=response.model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        attempt_count=response.model_attempts,
        cost_usd=response.estimated_cost_usd,
        status="succeeded" if response.incomplete_reason is None else "failed",
        truncated=response.incomplete_reason == "output_truncated",
        text=response.text,
    )


async def _run_shared_stage(
    orchestrator: DeliberationOrchestrator,
    stage_name: str,
    question: str,
    texts: dict[str, str],
    *,
    output_language: str,
    working_language: str,
    budget_guard: RunBudgetGuard | None,
) -> EvalStageResult:
    """Runs one real production stage (analysis_a/b, critique_a_of_b/b_of_a,
    revision_a/b) via the real orchestrator.run_stage -- identical to what
    production does for that same stage name, including truncation/language-
    contract recovery and budget-guard checks."""
    try:
        response = await orchestrator.run_stage(
            stage_name, question, texts,
            output_language=output_language, working_language=working_language,
            budget_guard=budget_guard,
        )
    except ProviderGenerationError as exc:
        return EvalStageResult(
            stage_name=stage_name, provider=None, requested_model=exc.requested_model,
            actual_model=None, input_tokens=0, output_tokens=0, attempt_count=exc.attempts,
            cost_usd=exc.estimated_cost_usd, status="failed", truncated=exc.reason == "output_truncated",
            text=None, error=str(exc),
        )
    except Exception as exc:  # any other transport/config error -- see service._execute's own generic branch
        return EvalStageResult(
            stage_name=stage_name, provider=None, requested_model=None,
            actual_model=None, input_tokens=0, output_tokens=0, attempt_count=1,
            cost_usd=0.0, status="failed", truncated=False, text=None, error=str(exc),
        )
    return _to_eval_stage_result(stage_name, response)


async def run_variant(
    variant: str,
    *,
    question: str,
    context: str | None,
    output_language: str,
    settings: Settings,
    orchestrator: DeliberationOrchestrator | None = None,
    db_path: Path | None = None,
    max_cost_per_variant_usd: Decimal | None = None,
) -> EvalRunResult:
    """Runs one evaluation variant. `orchestrator` lets a caller (or a test)
    inject a DeliberationOrchestrator with swapped-out fake providers,
    exactly like the existing offline provider tests (see
    test_provider_completion.py's `_orchestrator_with_provider`) -- when
    omitted, a real one is constructed from `settings` (real API calls).
    `db_path` is FULL-only: the dedicated evaluation-run-storage database
    (never the user's production data/deliberation.db -- see
    run_full_variant).
    """
    if variant not in VARIANT_NAMES:
        raise ValueError(f"Unknown evaluation variant: {variant!r}. Choose one of: {VARIANT_NAMES}")

    orch = orchestrator or DeliberationOrchestrator(settings)
    budget_guard = (
        RunBudgetGuard(budget_usd=max_cost_per_variant_usd, spent_usd=Decimal(0))
        if max_cost_per_variant_usd is not None
        else None
    )
    effective_question = f"{question}\n\nADDITIONAL CONTEXT:\n{context}" if context else question
    working_language = default_working_language(output_language)

    t0 = time.monotonic()
    if variant == "SINGLE":
        result = await _run_single(orch, effective_question, output_language, budget_guard)
    elif variant == "DUAL":
        result = await _run_dual(orch, effective_question, output_language, working_language, budget_guard)
    elif variant == "CRITIQUE":
        result = await _run_critique(orch, effective_question, output_language, working_language, budget_guard)
    else:  # FULL
        result = await run_full_variant(
            question=question, context=context, output_language=output_language,
            settings=settings, db_path=db_path, max_cost_per_variant_usd=max_cost_per_variant_usd,
        )
    result.duration_seconds = time.monotonic() - t0
    return result


async def _run_single(orch, question: str, output_language: str, budget_guard) -> EvalRunResult:
    provider = orch.a  # the "one strong model" -- see evaluation/prompts.py
    system = _base_system_for_output_language(output_language)
    prompt = single_direct_answer(question)
    stage = await _run_final_call(provider, "single_answer", system, prompt, budget_guard)
    status = "succeeded" if stage.status == "succeeded" else "failed"
    return EvalRunResult(
        variant="SINGLE", stages=[stage],
        final_output=stage.text if stage.status == "succeeded" else None,
        status=status,
    )


async def _run_dual(orch, question: str, output_language: str, working_language: str, budget_guard) -> EvalRunResult:
    texts: dict[str, str] = {}
    stages: list[EvalStageResult] = []
    for name in ("analysis_a", "analysis_b"):
        stage = await _run_shared_stage(
            orch, name, question, texts,
            output_language=output_language, working_language=working_language,
            budget_guard=budget_guard,
        )
        stages.append(stage)
        if stage.status != "succeeded":
            return EvalRunResult(variant="DUAL", stages=stages, status="failed")
        texts[name] = stage.text or ""

    system = _base_system_for_output_language(output_language)
    prompt = variant_synthesis(
        question,
        candidates={"Candidate A": texts["analysis_a"], "Candidate B": texts["analysis_b"]},
        stages_not_run=["critique", "revision", "red_team", "convergence_analysis"],
    )
    final_stage = await _run_final_call(orch.a, "final_answer", system, prompt, budget_guard)
    stages.append(final_stage)
    status = "succeeded" if final_stage.status == "succeeded" else "failed"
    return EvalRunResult(
        variant="DUAL", stages=stages,
        final_output=final_stage.text if final_stage.status == "succeeded" else None,
        status=status,
    )


async def _run_critique(orch, question: str, output_language: str, working_language: str, budget_guard) -> EvalRunResult:
    texts: dict[str, str] = {}
    stages: list[EvalStageResult] = []
    plan = (
        ("analysis_a",), ("analysis_b",),
        ("critique_a_of_b",), ("critique_b_of_a",),
        ("revision_a",), ("revision_b",),
    )
    for (name,) in plan:
        stage = await _run_shared_stage(
            orch, name, question, texts,
            output_language=output_language, working_language=working_language,
            budget_guard=budget_guard,
        )
        stages.append(stage)
        if stage.status != "succeeded":
            return EvalRunResult(variant="CRITIQUE", stages=stages, status="failed")
        texts[name] = stage.text or ""

    system = _base_system_for_output_language(output_language)
    prompt = variant_synthesis(
        question,
        candidates={"Revised Candidate A": texts["revision_a"], "Revised Candidate B": texts["revision_b"]},
        stages_not_run=["red_team", "convergence_analysis"],
    )
    final_stage = await _run_final_call(orch.a, "final_answer", system, prompt, budget_guard)
    stages.append(final_stage)
    status = "succeeded" if final_stage.status == "succeeded" else "failed"
    return EvalRunResult(
        variant="CRITIQUE", stages=stages,
        final_output=final_stage.text if final_stage.status == "succeeded" else None,
        status=status,
    )


def _base_system_for_output_language(output_language: str) -> str:
    from llm_deliberation.prompts import base_system

    # The eval-only final/single stage is always output-language-facing
    # (like production's own synthesis) -- stage_language == output_language,
    # same convention as orchestrator.resolve_stage_language uses for
    # convergence_analysis/synthesis.
    return base_system(output_language, output_language)


async def _run_final_call(provider, stage_name: str, system: str, prompt: str, budget_guard) -> EvalStageResult:
    from llm_deliberation.cost_budget import BudgetExceededError, estimate_max_call_cost_usd

    if budget_guard is not None:
        estimated = estimate_max_call_cost_usd(
            provider.model, prompt_chars=len(system) + len(prompt),
            max_output_tokens=provider.max_output_tokens,
        )
        try:
            budget_guard.check(estimated)
        except BudgetExceededError as exc:
            return EvalStageResult(
                stage_name=stage_name, provider=None, requested_model=provider.model,
                actual_model=None, input_tokens=0, output_tokens=0, attempt_count=0,
                cost_usd=0.0, status="failed", truncated=False, text=None, error=str(exc),
            )
    import asyncio

    try:
        response = await asyncio.to_thread(provider.generate, system=system, prompt=prompt)
    except Exception as exc:  # pragma: no cover - real transport errors, exercised live only
        return EvalStageResult(
            stage_name=stage_name, provider=None, requested_model=provider.model,
            actual_model=None, input_tokens=0, output_tokens=0, attempt_count=1,
            cost_usd=0.0, status="failed", truncated=False, text=None, error=str(exc),
        )
    if budget_guard is not None:
        budget_guard.record_actual(Decimal(str(response.estimated_cost_usd)))
    return _to_eval_stage_result(stage_name, response)


async def run_full_variant(
    *,
    question: str,
    context: str | None,
    output_language: str,
    settings: Settings,
    db_path: Path | None,
    max_cost_per_variant_usd: Decimal | None,
) -> EvalRunResult:
    """FULL: the real, current, unmodified production pipeline -- driven
    through the real DeliberationService entry points (create_run/
    start_run), never a re-implementation, so it can never silently drift
    from what a real user's run actually does. Uses a dedicated evaluation-
    run-storage database (never the user's production data/deliberation.db)
    so evaluation experiments never mix into ordinary run history."""
    from llm_deliberation.service import DeliberationService
    from llm_deliberation.web.presenter import compute_deliberation_quality

    if db_path is None:
        raise ValueError("FULL variant requires a dedicated db_path (never the production DB).")
    service = DeliberationService(db_path)
    run_id = service.create_run(
        question=question,
        profile=settings.profile,
        red_team_enabled=settings.red_team_enabled,
        context=context,
        language=output_language,
        max_run_cost_usd=str(max_cost_per_variant_usd) if max_cost_per_variant_usd is not None else None,
    )
    record = await service.start_run(run_id)

    stages = [
        EvalStageResult(
            stage_name=s.name, provider=s.provider, requested_model=s.requested_model,
            actual_model=s.model, input_tokens=s.input_tokens, output_tokens=s.output_tokens,
            attempt_count=s.model_attempts, cost_usd=s.estimated_cost_usd, status=s.status,
            truncated=(s.failure_reason == "output_truncated"), text=s.text, error=s.error,
        )
        for s in record.stages
    ]
    synthesis_stage = next((s for s in record.stages if s.name == "synthesis"), None)
    convergence_stage = next((s for s in record.stages if s.name == "convergence_analysis"), None)
    quality = compute_deliberation_quality(record)

    material_change_count = None
    if convergence_stage is not None and convergence_stage.status == "succeeded" and convergence_stage.text:
        from llm_deliberation import convergence as convergence_module

        try:
            analysis = convergence_module.parse_convergence_analysis(convergence_stage.text)
            material_change_count = sum(
                1 for c in analysis.material_changes if c.change_status == "material"
            )
        except convergence_module.ConvergenceParseError:
            material_change_count = None

    return EvalRunResult(
        variant="FULL",
        stages=stages,
        final_output=synthesis_stage.text if synthesis_stage and synthesis_stage.status == "succeeded" else None,
        status="succeeded" if record.status == "succeeded" else "failed",
        quality_state=quality["level"] if quality else None,
        convergence_status=convergence_stage.status if convergence_stage else None,
        material_change_count=material_change_count,
        production_run_id=run_id,
    )
