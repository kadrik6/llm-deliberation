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
        rows.append(
            StageEfficiencyRow(
                stage=stage.name,
                output_language=record.language,
                language_used=language_used,
                input_tokens=stage.input_tokens,
                output_tokens=stage.output_tokens,
                estimated_cost_usd=stage.estimated_cost_usd,
                finish_reason=finish_reason,
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
