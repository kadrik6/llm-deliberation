"""Offline tests for evaluation/reporting.py and evaluation/planning.py."""

from __future__ import annotations

from decimal import Decimal

import pytest

from llm_deliberation.config import Settings
from llm_deliberation.evaluation.cases import load_case
from llm_deliberation.evaluation.planning import format_experiment_plan, plan_experiment_batch
from llm_deliberation.evaluation.reporting import (
    build_aggregate_report,
    build_case_report,
    format_aggregate_report,
    format_case_report,
)
from llm_deliberation.evaluation.store import EvalExperimentRecord, EvalStageRow


def _experiment(variant: str, *, status="succeeded", cost=0.05, calls=1, material_changes=None) -> EvalExperimentRecord:
    stages = [
        EvalStageRow(
            stage_name="single_answer", provider="OpenAI", requested_model="gpt-5.6-terra",
            actual_model="gpt-5.6-terra", input_tokens=100, output_tokens=200, attempt_count=1,
            cost_usd=cost, status=status, truncated=False, text="output", error=None,
        )
    ]
    return EvalExperimentRecord(
        id=f"exp-{variant}-{status}", case_id="case-1", variant=variant, git_commit="abc",
        profile="economy", output_language="en", working_language="en",
        configured_openai_model="gpt-5.6-terra", configured_anthropic_model="claude-sonnet-5",
        configured_gemini_model=None, created_at="2026-01-01T00:00:00+00:00", completed_at=None,
        status=status, total_calls=calls, total_input_tokens=100, total_output_tokens=200,
        total_cost_usd=cost, duration_seconds=5.0, quality_state="complete" if status == "succeeded" else None,
        final_output="output" if status == "succeeded" else None, material_change_count=material_changes,
        convergence_status=None, max_cost_per_variant_usd="1.00", production_run_id=None, stages=stages,
    )


def test_case_report_reflects_completion_and_failure_notes():
    experiments = [_experiment("SINGLE"), _experiment("DUAL", status="failed")]
    rows = build_case_report(experiments, reviews_by_experiment={})

    single_row = next(r for r in rows if r.variant == "SINGLE")
    assert single_row.completed is True
    assert single_row.failure_note is None

    dual_row = next(r for r in rows if r.variant == "DUAL")
    assert dual_row.completed is False
    assert dual_row.failure_note is not None


def test_case_report_never_declares_a_winner():
    rows = build_case_report([_experiment("SINGLE"), _experiment("FULL")], reviews_by_experiment={})
    text = format_case_report(rows)
    for banned in ("best", "winner", "wins", "SINGLE is best", "FULL is best"):
        assert banned.lower() not in text.lower()


def test_aggregate_report_computes_descriptive_stats_only():
    experiments = [
        _experiment("SINGLE", cost=0.01, material_changes=None),
        _experiment("SINGLE", cost=0.03, material_changes=None),
        _experiment("FULL", cost=0.30, material_changes=2),
        _experiment("FULL", cost=0.30, material_changes=0),
    ]
    stats = build_aggregate_report(experiments)
    single_stats = next(s for s in stats if s.variant == "SINGLE")
    full_stats = next(s for s in stats if s.variant == "FULL")

    assert single_stats.n == 2
    assert single_stats.mean_cost_usd == pytest.approx(0.02)
    assert single_stats.material_change_frequency is None  # never recorded for SINGLE

    assert full_stats.n == 2
    assert full_stats.material_change_frequency == pytest.approx(0.5)  # 1 of 2 had >=1 material change

    text = format_aggregate_report(stats)
    for banned in ("best", "winner"):
        assert banned.lower() not in text.lower()


def test_aggregate_report_completion_rate_accounts_for_failures():
    experiments = [_experiment("DUAL"), _experiment("DUAL", status="failed")]
    stats = build_aggregate_report(experiments)
    dual_stats = next(s for s in stats if s.variant == "DUAL")
    assert dual_stats.completion_rate == pytest.approx(0.5)


# -- planning -------------------------------------------------------------


def test_plan_never_makes_a_network_call_and_reports_expected_totals():
    case = load_case("eval_cases/example-nonprofit-crm.json")
    settings = Settings.load(profile_override="economy", red_team_override=False)

    plan = plan_experiment_batch(
        [case], ["SINGLE", "DUAL", "FULL"], settings,
        max_cost_per_variant_usd=Decimal("1.00"), max_cost_per_case_usd=Decimal("3.00"),
        max_total_experiment_budget_usd=Decimal("10.00"),
    )

    assert plan.case_ids == ("example-nonprofit-crm",)
    assert plan.variants == ("SINGLE", "DUAL", "FULL")
    # SINGLE=1, DUAL=3, FULL=8 (red-team off) -> 12 calls for 1 case.
    assert plan.expected_calls_total == 12
    assert plan.estimated_cost_high_usd > 0
    assert plan.estimated_cost_high_usd >= plan.estimated_cost_low_usd


def test_plan_flags_when_estimate_exceeds_total_budget():
    case = load_case("eval_cases/example-nonprofit-crm.json")
    settings = Settings.load(profile_override="economy", red_team_override=True)

    plan = plan_experiment_batch(
        [case, case, case], ["FULL"], settings,
        max_cost_per_variant_usd=Decimal("1.00"), max_cost_per_case_usd=Decimal("1.00"),
        max_total_experiment_budget_usd=Decimal("0.01"),
    )
    assert plan.exceeds_total_budget() is True


def test_plan_rejects_unknown_variant():
    case = load_case("eval_cases/example-nonprofit-crm.json")
    settings = Settings.load(profile_override="economy", red_team_override=False)
    with pytest.raises(ValueError):
        plan_experiment_batch(
            [case], ["QUINTUPLE"], settings,
            max_cost_per_variant_usd=Decimal("1.00"), max_cost_per_case_usd=Decimal("1.00"),
            max_total_experiment_budget_usd=Decimal("10.00"),
        )


def test_format_experiment_plan_is_human_readable():
    case = load_case("eval_cases/example-nonprofit-crm.json")
    settings = Settings.load(profile_override="economy", red_team_override=False)
    plan = plan_experiment_batch(
        [case], ["SINGLE"], settings,
        max_cost_per_variant_usd=Decimal("1.00"), max_cost_per_case_usd=Decimal("1.00"),
        max_total_experiment_budget_usd=Decimal("10.00"),
    )
    text = format_experiment_plan(plan)
    assert "Estimated cost" in text
    assert "SINGLE" in text
