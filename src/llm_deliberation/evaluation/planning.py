"""Pre-execution experiment planning (Part B, Section 20). Every batch of
live experiments must be plannable -- cases, variants, expected calls,
configured models, estimated cost, and a hard maximum -- BEFORE anything
runs. No live experiment starts implicitly: `plan_experiment_batch` never
makes a network call and never executes a variant; it only estimates.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from llm_deliberation.config import Settings
from llm_deliberation.cost_budget import CostEstimate, estimate_max_call_cost_usd
from llm_deliberation.evaluation.cases import EvalCase
from llm_deliberation.evaluation.variants import VARIANT_NAMES, VARIANT_STAGES

# Declared-approximate typical prompt sizes (chars) for the two evaluation-
# only synthetic stages (see evaluation/prompts.py) -- same convention and
# purpose as cost_budget._STAGE_TYPICAL_INPUT_CHARS, kept separate since
# these two stage names only ever exist in the evaluation harness, never in
# production.
_EVAL_STAGE_TYPICAL_INPUT_CHARS: dict[str, int] = {
    "single_answer": 900,
    "final_answer": 5000,  # carries forward 1-2 full candidate texts
}

# Real production per-stage typical chars, duplicated narrowly here rather
# than importing cost_budget's private table, since planning only needs
# four of the nine production stage names and this keeps the two modules'
# private internals independent.
_SHARED_STAGE_TYPICAL_INPUT_CHARS: dict[str, int] = {
    "analysis_a": 900,
    "analysis_b": 900,
    "critique_a_of_b": 2400,
    "critique_b_of_a": 2400,
    "revision_a": 4000,
    "revision_b": 4000,
}


@dataclass(slots=True)
class VariantCostEstimate:
    variant: str
    expected_calls: int
    estimate: CostEstimate


@dataclass(slots=True)
class ExperimentPlan:
    case_ids: tuple[str, ...]
    variants: tuple[str, ...]
    configured_openai_model: str
    configured_anthropic_model: str
    configured_gemini_model: str
    per_variant_estimates: tuple[VariantCostEstimate, ...]
    expected_calls_total: int
    estimated_cost_low_usd: Decimal
    estimated_cost_high_usd: Decimal
    max_cost_per_variant_usd: Decimal
    max_cost_per_case_usd: Decimal
    max_total_experiment_budget_usd: Decimal

    def exceeds_total_budget(self) -> bool:
        return self.estimated_cost_high_usd > self.max_total_experiment_budget_usd


def _variant_cost_estimate(variant: str, settings: Settings) -> VariantCostEstimate:
    stage_names = VARIANT_STAGES.get(variant)
    if stage_names is None:  # FULL: reuse the real production pre-run estimator
        from llm_deliberation.cost_budget import estimate_run_cost_range

        estimate = estimate_run_cost_range(
            openai_model=settings.openai_model, anthropic_model=settings.anthropic_model,
            gemini_model=settings.gemini_model, convergence_provider=settings.convergence_provider,
            convergence_model=settings.convergence_model, red_team_enabled=settings.red_team_enabled,
            max_output_tokens=settings.max_output_tokens,
        )
        expected_calls = 8 + (1 if settings.red_team_enabled else 0)
        return VariantCostEstimate(variant="FULL", expected_calls=expected_calls, estimate=estimate)

    stage_models = {
        "analysis_a": settings.openai_model, "analysis_b": settings.anthropic_model,
        "critique_a_of_b": settings.openai_model, "critique_b_of_a": settings.anthropic_model,
        "revision_a": settings.openai_model, "revision_b": settings.anthropic_model,
        "single_answer": settings.openai_model, "final_answer": settings.openai_model,
    }
    low = Decimal(0)
    high = Decimal(0)
    for stage in stage_names:
        chars = _SHARED_STAGE_TYPICAL_INPUT_CHARS.get(stage) or _EVAL_STAGE_TYPICAL_INPUT_CHARS[stage]
        model = stage_models[stage]
        high += estimate_max_call_cost_usd(model, prompt_chars=chars, max_output_tokens=settings.max_output_tokens)
        low += estimate_max_call_cost_usd(
            model, prompt_chars=chars, max_output_tokens=int(settings.max_output_tokens * 0.3)
        )
    return VariantCostEstimate(
        variant=variant, expected_calls=len(stage_names), estimate=CostEstimate(low_usd=low, high_usd=high)
    )


def plan_experiment_batch(
    cases: list[EvalCase],
    variants: list[str],
    settings: Settings,
    *,
    max_cost_per_variant_usd: Decimal,
    max_cost_per_case_usd: Decimal,
    max_total_experiment_budget_usd: Decimal,
) -> ExperimentPlan:
    """Pure estimate: no network call, no experiment execution. Multiplies
    each variant's own typical-chars cost estimate by the number of cases
    (every case gets every requested variant once)."""
    unknown = set(variants) - set(VARIANT_NAMES)
    if unknown:
        raise ValueError(f"Unknown variant(s): {sorted(unknown)}. Choose from: {VARIANT_NAMES}")
    if not cases:
        raise ValueError("plan_experiment_batch requires at least one case.")

    per_variant = tuple(_variant_cost_estimate(v, settings) for v in variants)
    n_cases = len(cases)

    total_calls = sum(v.expected_calls for v in per_variant) * n_cases
    total_low = sum((v.estimate.low_usd for v in per_variant), Decimal(0)) * n_cases
    total_high = sum((v.estimate.high_usd for v in per_variant), Decimal(0)) * n_cases

    return ExperimentPlan(
        case_ids=tuple(c.case_id for c in cases),
        variants=tuple(variants),
        configured_openai_model=settings.openai_model,
        configured_anthropic_model=settings.anthropic_model,
        configured_gemini_model=settings.gemini_model,
        per_variant_estimates=per_variant,
        expected_calls_total=total_calls,
        estimated_cost_low_usd=total_low,
        estimated_cost_high_usd=total_high,
        max_cost_per_variant_usd=max_cost_per_variant_usd,
        max_cost_per_case_usd=max_cost_per_case_usd,
        max_total_experiment_budget_usd=max_total_experiment_budget_usd,
    )


def format_experiment_plan(plan: ExperimentPlan) -> str:
    lines = [
        f"Cases: {len(plan.case_ids)} ({', '.join(plan.case_ids)})",
        f"Variants: {', '.join(plan.variants)}",
        f"Configured models: openai={plan.configured_openai_model}, "
        f"anthropic={plan.configured_anthropic_model}, gemini={plan.configured_gemini_model}",
        f"Expected total calls: {plan.expected_calls_total}",
        f"Estimated cost: ${plan.estimated_cost_low_usd:.4f} - ${plan.estimated_cost_high_usd:.4f}",
        f"Max cost per variant: ${plan.max_cost_per_variant_usd}",
        f"Max cost per case: ${plan.max_cost_per_case_usd}",
        f"Max total experiment budget: ${plan.max_total_experiment_budget_usd}",
        f"Estimated high exceeds total budget: {plan.exceeds_total_budget()}",
        "",
        "Per-variant estimate:",
    ]
    for v in plan.per_variant_estimates:
        lines.append(f"  {v.variant}: {v.expected_calls} calls/case, {v.estimate}")
    return "\n".join(lines)
