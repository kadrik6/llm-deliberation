"""Deterministic evaluation reporting (Part B, Sections 17-18). Descriptive
only -- explicitly never computes or states a "winning" variant (see
Section 18: "the human decides after seeing the evidence"). Process-value
metrics (material revisions, convergence state) are labeled as process
signals, never correctness metrics (Section 17).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from llm_deliberation.evaluation.store import EvalExperimentRecord


@dataclass(slots=True)
class VariantRow:
    variant: str
    completed: bool
    quality: str | None
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    duration_seconds: float | None
    material_revisions: int | None
    red_team_ran: bool
    failure_note: str | None
    human_review_count: int


def build_case_report(
    experiments: list[EvalExperimentRecord], reviews_by_experiment: dict[str, list[dict]]
) -> list[VariantRow]:
    """One row per experiment (typically one per variant for a single
    case). `reviews_by_experiment` maps experiment_id -> the list already
    returned by EvaluationRepository.list_reviews -- purely to count how
    many reviews exist, never to compute a score from them."""
    rows: list[VariantRow] = []
    for exp in experiments:
        failure_note = None
        if exp.status != "succeeded":
            failed_stages = [s.stage_name for s in exp.stages if s.status != "succeeded"]
            failure_note = f"did not complete: {', '.join(failed_stages)}" if failed_stages else "did not complete"
        red_team_ran = any(s.stage_name == "red_team" and s.status == "succeeded" for s in exp.stages)
        rows.append(
            VariantRow(
                variant=exp.variant,
                completed=exp.status == "succeeded",
                quality=exp.quality_state,
                calls=exp.total_calls,
                input_tokens=exp.total_input_tokens,
                output_tokens=exp.total_output_tokens,
                cost_usd=exp.total_cost_usd,
                duration_seconds=exp.duration_seconds,
                material_revisions=exp.material_change_count,
                red_team_ran=red_team_ran,
                failure_note=failure_note,
                human_review_count=len(reviews_by_experiment.get(exp.id, [])),
            )
        )
    return rows


def format_case_report(rows: list[VariantRow]) -> str:
    if not rows:
        return "(no experiments)"
    header = (
        f"{'variant':<10} {'done':<5} {'quality':<10} {'calls':>5} "
        f"{'in_tok':>7} {'out_tok':>7} {'cost':>9} {'dur_s':>7} "
        f"{'mat_rev':>7} {'red_team':>8} {'reviews':>7}  note"
    )
    lines = [header, "-" * len(header)]
    for r in rows:
        lines.append(
            f"{r.variant:<10} {str(r.completed):<5} {str(r.quality):<10} {r.calls:>5} "
            f"{r.input_tokens:>7} {r.output_tokens:>7} ${r.cost_usd:>8.4f} "
            f"{(r.duration_seconds or 0):>7.1f} {str(r.material_revisions):>7} "
            f"{str(r.red_team_ran):>8} {r.human_review_count:>7}  {r.failure_note or ''}"
        )
    return "\n".join(lines)


@dataclass(slots=True)
class AggregateStats:
    variant: str
    n: int
    completion_rate: float
    mean_cost_usd: float
    median_cost_usd: float
    mean_duration_seconds: float
    median_duration_seconds: float
    mean_calls: float
    median_calls: float
    material_change_frequency: float | None  # fraction of experiments with >=1 material change


def build_aggregate_report(experiments: list[EvalExperimentRecord]) -> list[AggregateStats]:
    """Descriptive aggregates grouped by variant, across every experiment
    passed in (typically: every experiment across every case for a given
    batch). Never ranks variants against each other -- callers decide what
    to do with these numbers."""
    by_variant: dict[str, list[EvalExperimentRecord]] = {}
    for exp in experiments:
        by_variant.setdefault(exp.variant, []).append(exp)

    stats: list[AggregateStats] = []
    for variant, exps in sorted(by_variant.items()):
        n = len(exps)
        completed = [e for e in exps if e.status == "succeeded"]
        costs = [e.total_cost_usd for e in exps]
        durations = [e.duration_seconds for e in exps if e.duration_seconds is not None]
        calls = [e.total_calls for e in exps]
        material_known = [e for e in exps if e.material_change_count is not None]
        material_freq = (
            sum(1 for e in material_known if e.material_change_count > 0) / len(material_known)
            if material_known
            else None
        )
        stats.append(
            AggregateStats(
                variant=variant,
                n=n,
                completion_rate=len(completed) / n if n else 0.0,
                mean_cost_usd=statistics.fmean(costs) if costs else 0.0,
                median_cost_usd=statistics.median(costs) if costs else 0.0,
                mean_duration_seconds=statistics.fmean(durations) if durations else 0.0,
                median_duration_seconds=statistics.median(durations) if durations else 0.0,
                mean_calls=statistics.fmean(calls) if calls else 0.0,
                median_calls=statistics.median(calls) if calls else 0.0,
                material_change_frequency=material_freq,
            )
        )
    return stats


def format_aggregate_report(stats: list[AggregateStats]) -> str:
    if not stats:
        return "(no experiments)"
    header = (
        f"{'variant':<10} {'n':>3} {'completion':>10} {'mean_cost':>10} "
        f"{'median_cost':>11} {'mean_dur':>9} {'mean_calls':>10} {'material_freq':>13}"
    )
    lines = [header, "-" * len(header)]
    for s in stats:
        freq = "n/a" if s.material_change_frequency is None else f"{s.material_change_frequency:.0%}"
        lines.append(
            f"{s.variant:<10} {s.n:>3} {s.completion_rate:>9.0%} ${s.mean_cost_usd:>9.4f} "
            f"${s.median_cost_usd:>10.4f} {s.mean_duration_seconds:>8.1f}s {s.mean_calls:>10.1f} {freq:>13}"
        )
    return "\n".join(lines)
