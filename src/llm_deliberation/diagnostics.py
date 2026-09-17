"""Developer-facing diagnostics for comparing stored run/stage data across
output-language and working-language combinations -- e.g. "did an Estonian
run's English-working-language analysis stage actually use fewer output
tokens / finish without truncation, compared to a legacy Estonian run that
worked entirely in Estonian?"

No live API calls anywhere in this module. Every value here is either
already persisted (token counts, cost, failure_reason) or a trivial,
already-existing pure function of persisted data
(orchestrator.resolve_stage_language) -- this module makes no estimates and
claims no percentage savings; it only lays out real, already-collected
numbers so a human (or a later test) can compare real runs. See
docs/decisions/008-working-language.md for why: the working-language
feature's actual token/reliability benefit should be measured from real
runs, not asserted in advance.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from llm_deliberation.config import Settings
from llm_deliberation.cost_budget import estimate_remaining_completion_reserve
from llm_deliberation.orchestrator import resolve_stage_language
from llm_deliberation.store import RunRecord


@dataclass(slots=True)
class StageEfficiencyRow:
    stage: str
    output_language: str
    # The language this specific stage actually used, resolved the same way
    # the real orchestrator resolves it (orchestrator.resolve_stage_language)
    # -- for a legacy run (working_language column is None) this always
    # equals output_language, exactly reproducing that run's real behavior.
    language_used: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    # "completed" for a normal success; otherwise the stored failure_reason
    # (e.g. "output_truncated") or, if that predates failure_reason
    # entirely, the raw stage status -- never invented.
    finish_reason: str
    # Working-language contract provenance (see language_detect.py,
    # orchestrator.run_stage) -- "matched" | "mismatched" | "uncertain" |
    # None (not applicable: no override was in effect for this stage, or
    # this run predates the feature). observed_language is the detector's
    # own best guess ("en"/"et"/None). Never inferred retroactively here --
    # this is exactly what was persisted at generation time.
    language_contract_status: str | None
    observed_language: str | None
    language_recovery_attempted: bool
    # The detector's *first* (pre-recovery) observation, when recovery was
    # attempted -- read from attempt_log's "initial" phase entry, never
    # inferred. None when recovery was not attempted (observed_language
    # above already *is* the only observation in that case) or the stored
    # attempt_log predates this detail.
    initial_observed_language: str | None


def stage_efficiency_rows(record: RunRecord) -> list[StageEfficiencyRow]:
    """One row per stage of `record`. Pure function of already-persisted
    data; makes no provider calls and mutates nothing."""
    effective_working_language = record.working_language or record.language
    rows: list[StageEfficiencyRow] = []
    for stage in record.stages:
        language_used = resolve_stage_language(
            stage.name,
            output_language=record.language,
            working_language=effective_working_language,
        )
        if stage.status == "succeeded" and stage.failure_reason is None:
            finish_reason = "completed"
        else:
            finish_reason = stage.failure_reason or stage.status
        initial_observed_language = None
        if stage.language_recovery_attempted and stage.attempt_log:
            initial_observed_language = stage.attempt_log[0].get("detected_language")
        rows.append(
            StageEfficiencyRow(
                stage=stage.name,
                output_language=record.language,
                language_used=language_used,
                input_tokens=stage.input_tokens,
                output_tokens=stage.output_tokens,
                estimated_cost_usd=stage.estimated_cost_usd,
                finish_reason=finish_reason,
                language_contract_status=stage.language_contract_status,
                observed_language=stage.observed_language,
                language_recovery_attempted=stage.language_recovery_attempted,
                initial_observed_language=initial_observed_language,
            )
        )
    return rows


def format_efficiency_table(rows: list[StageEfficiencyRow]) -> str:
    """Plain-text table for ad-hoc developer use (a shell one-liner, a
    notebook cell, or `llm-deliberate --diagnose-run RUN_ID`). Deliberately
    makes no comparative claim on its own -- comparing two such tables
    (e.g. one legacy Estonian run vs. one English-working-language run) is
    left to whoever is reading them.
    """
    if not rows:
        return "(no stages)"
    header = f"{'stage':<22} {'lang':<5} {'in':>7} {'out':>7} {'cost':>9}  finish_reason"
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(
            f"{row.stage:<22} {row.language_used:<5} {row.input_tokens:>7} "
            f"{row.output_tokens:>7} ${row.estimated_cost_usd:>8.4f}  {row.finish_reason}"
        )
    return "\n".join(lines)


def format_completion_reserve_report(record: RunRecord, settings: Settings) -> str:
    """Explains the completion-reserve state for a run right now -- what has
    been spent, what is estimated still required, and how much optional
    (red_team) headroom that leaves (see
    cost_budget.estimate_remaining_completion_reserve, service._execute's
    admission loop). Pure/read-only: reuses the same estimator the live
    admission check uses, against already-persisted state -- never a new
    estimate, never a provider call.

    Returns a short explanatory line when the run has no cost cap at all
    (max_run_cost_usd unset) -- the reserve concept is a no-op there, exactly
    as today's plain budget guard already is.
    """
    if not record.max_run_cost_usd:
        return "(no max_run_cost_usd set for this run -- completion reserve does not apply)"

    budget_usd = Decimal(record.max_run_cost_usd)
    spent_usd = Decimal(str(round(record.estimated_total_cost_usd, 6)))
    reserve = estimate_remaining_completion_reserve(
        record.stages,
        budget_usd=budget_usd,
        spent_usd=spent_usd,
        openai_model=settings.openai_model,
        anthropic_model=settings.anthropic_model,
        convergence_provider=settings.convergence_provider,
        convergence_model=settings.convergence_model,
        max_output_tokens=settings.max_output_tokens,
    )
    lines = [
        f"Spent: ${spent_usd:.4f}",
        f"Remaining run budget: ${reserve.remaining_budget_usd:.4f}",
        f"Estimated completion reserve: ${reserve.required_remaining_usd:.4f}",
        f"Optional spendable budget: ${reserve.optional_spendable_usd:.4f}",
    ]
    if reserve.reserved_stage_names:
        lines.append("Reserved stages:")
        lines.extend(f"- {name}" for name in reserve.reserved_stage_names)
    else:
        lines.append("Reserved stages: (none -- every required stage has already succeeded)")
    return "\n".join(lines)


def format_language_contract_report(rows: list[StageEfficiencyRow]) -> str:
    """Per-stage working-language contract detail -- e.g. was an Estonian-
    output run's English-working-language analysis stage actually answered
    in English, and if not, did the bounded recovery attempt fix it. Only
    covers stages the check actually applied to (language_contract_status is
    not None); every other stage (an English-only run, convergence_analysis,
    synthesis, or a stage predating this feature) is silently omitted, not
    padded with meaningless "n/a" rows. Especially useful for comparing how
    reliably different configured models actually follow this instruction
    (see AUDIT_REPORT.md's live-canary findings).
    """
    applicable = [row for row in rows if row.language_contract_status is not None]
    if not applicable:
        return "(no working-language contract checks applied to this run)"
    lines: list[str] = []
    for row in applicable:
        lines.append(f"{row.stage}:")
        lines.append(f"  requested working language: {row.language_used}")
        if row.language_recovery_attempted:
            lines.append(f"  observed language: {row.initial_observed_language or 'uncertain'}")
            lines.append("  recovery: attempted")
            lines.append(f"  final observed language: {row.observed_language or 'uncertain'}")
            status_label = (
                "matched after recovery" if row.language_contract_status == "matched"
                else row.language_contract_status
            )
        else:
            lines.append(f"  observed language: {row.observed_language or 'uncertain'}")
            lines.append("  recovery: not needed")
            status_label = row.language_contract_status
        lines.append(f"  status: {status_label}")
    return "\n".join(lines)
