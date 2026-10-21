"""Offline, pure-function tests for cost_report.build_cost_report -- no DB,
no network, no provider calls. Rows are constructed directly.
"""
from __future__ import annotations

from datetime import datetime, timezone

from llm_deliberation.cost_report import (
    STAGE0_PROVIDER_LABEL,
    UNKNOWN_PROVIDER_LABEL,
    build_cost_report,
    cutoff_iso,
)
from llm_deliberation.store import RunRecord, Stage0CostRow, StageCostRow

NOW = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)


def _run(run_id="r1", created_at="2026-10-20T00:00:00+00:00", status="succeeded",
         estimated_total_cost_usd=0.0, question="Q?") -> RunRecord:
    return RunRecord(
        id=run_id, question=question, context=None, profile="economy", red_team_enabled=False,
        status=status, created_at=created_at, started_at=None, completed_at=None,
        estimated_total_cost_usd=estimated_total_cost_usd,
    )


def _stage_row(**overrides) -> StageCostRow:
    defaults = dict(
        run_id="r1", run_created_at="2026-10-20T00:00:00+00:00", name="analysis_a",
        provider="OpenAI", input_tokens=100, output_tokens=200, estimated_cost_usd=0.01,
        model_attempts=1,
    )
    defaults.update(overrides)
    return StageCostRow(**defaults)


def test_cutoff_iso_subtracts_the_window():
    cutoff = cutoff_iso(NOW, window_days=30)
    assert cutoff.startswith("2026-09-21")


def test_empty_inputs_produce_a_zeroed_report():
    report = build_cost_report(runs=[], stage_rows=[], stage0_rows=[], now=NOW)
    assert report.total_cost_usd == 0.0
    assert report.by_provider == []
    assert report.by_day == []
    assert report.by_run == []


def test_main_pipeline_cost_comes_from_the_run_record_not_resummed(tmp_path=None):
    """runs.estimated_total_cost_usd is already the maintained running
    total (see service._execute) -- the report must use it directly, not
    re-derive it from stage rows (which would double-count nothing here,
    but IS the single source of truth already used by run_detail.html)."""
    runs = [_run(estimated_total_cost_usd=0.25)]
    report = build_cost_report(runs=runs, stage_rows=[], stage0_rows=[], now=NOW)
    assert report.main_pipeline_cost_usd == 0.25
    assert report.total_cost_usd == 0.25


def test_stage0_cost_is_separate_from_main_pipeline_and_never_merged():
    runs = [_run(estimated_total_cost_usd=0.25)]
    stage0_rows = [Stage0CostRow(run_id="r1", run_created_at="2026-10-20T00:00:00+00:00", estimated_cost_usd=0.05)]
    report = build_cost_report(runs=runs, stage_rows=[], stage0_rows=stage0_rows, now=NOW)
    assert report.main_pipeline_cost_usd == 0.25
    assert report.stage0_cost_usd == 0.05
    assert round(report.total_cost_usd, 4) == 0.30


def test_stage0_appears_as_its_own_provider_bucket_not_split():
    runs = [_run(estimated_total_cost_usd=0.0)]
    stage_rows = [_stage_row(provider="OpenAI", estimated_cost_usd=0.10)]
    stage0_rows = [Stage0CostRow(run_id="r1", run_created_at="2026-10-20T00:00:00+00:00", estimated_cost_usd=0.04)]
    report = build_cost_report(runs=runs, stage_rows=stage_rows, stage0_rows=stage0_rows, now=NOW)

    labels = {p.provider: p for p in report.by_provider}
    assert "OpenAI" in labels
    assert STAGE0_PROVIDER_LABEL in labels
    assert labels[STAGE0_PROVIDER_LABEL].estimated_cost_usd == 0.04
    assert labels[STAGE0_PROVIDER_LABEL].calls == 0  # no call count tracked for stage0


def test_no_stage0_bucket_when_stage0_cost_is_zero():
    """A run with Stage 0 enabled but never spending anything must not
    produce a spurious $0.00 'Stage 0' row."""
    runs = [_run()]
    stage0_rows = [Stage0CostRow(run_id="r1", run_created_at="2026-10-20T00:00:00+00:00", estimated_cost_usd=0.0)]
    report = build_cost_report(runs=runs, stage_rows=[], stage0_rows=stage0_rows, now=NOW)
    assert all(p.provider != STAGE0_PROVIDER_LABEL for p in report.by_provider)


def test_known_stages_are_attributed_by_the_deterministic_role_mapping_when_provider_is_unset():
    """A failed stage never gets `provider` set (mark_stage_failed doesn't
    take one) -- the report must still attribute it correctly via the
    stage-name mapping, never silently drop its cost."""
    rows = [
        _stage_row(name="analysis_a", provider=None, estimated_cost_usd=0.01),
        _stage_row(name="analysis_b", provider=None, estimated_cost_usd=0.02),
        _stage_row(name="red_team", provider=None, estimated_cost_usd=0.03),
    ]
    report = build_cost_report(runs=[_run()], stage_rows=rows, stage0_rows=[], now=NOW)
    labels = {p.provider: p.estimated_cost_usd for p in report.by_provider}
    assert labels["OpenAI"] == 0.01
    assert labels["Anthropic"] == 0.02
    assert labels["Gemini"] == 0.03


def test_a_failed_convergence_analysis_with_no_recorded_provider_is_unknown_not_guessed():
    """convergence_analysis's provider is configurable -- unlike every
    other stage, there is no single correct fallback, so this must be
    reported honestly as unknown rather than assigned a guess."""
    rows = [_stage_row(name="convergence_analysis", provider=None, estimated_cost_usd=0.01)]
    report = build_cost_report(runs=[_run()], stage_rows=rows, stage0_rows=[], now=NOW)
    labels = {p.provider: p.estimated_cost_usd for p in report.by_provider}
    assert labels[UNKNOWN_PROVIDER_LABEL] == 0.01
    assert "OpenAI" not in labels
    assert "Anthropic" not in labels
    assert "Gemini" not in labels


def test_google_provider_name_displays_as_gemini():
    """providers.GeminiFallbackProvider.provider_name is 'Google' -- the
    report must show the same user-facing label as the rest of the app
    ('Gemini'), never the internal SDK string."""
    rows = [_stage_row(name="red_team", provider="Google", estimated_cost_usd=0.03)]
    report = build_cost_report(runs=[_run()], stage_rows=rows, stage0_rows=[], now=NOW)
    labels = {p.provider for p in report.by_provider}
    assert "Gemini" in labels
    assert "Google" not in labels


def test_provider_totals_sum_tokens_and_calls_across_multiple_stages():
    rows = [
        _stage_row(name="analysis_a", provider="OpenAI", input_tokens=100, output_tokens=200, model_attempts=1),
        _stage_row(name="revision_a", provider="OpenAI", input_tokens=50, output_tokens=60, model_attempts=2),
    ]
    report = build_cost_report(runs=[_run()], stage_rows=rows, stage0_rows=[], now=NOW)
    openai = next(p for p in report.by_provider if p.provider == "OpenAI")
    assert openai.input_tokens == 150
    assert openai.output_tokens == 260
    assert openai.calls == 3  # model_attempts summed, not stage-row count


def test_by_day_groups_by_the_runs_created_at_date():
    runs = [
        _run(run_id="r1", created_at="2026-10-20T08:00:00+00:00", estimated_total_cost_usd=0.10),
        _run(run_id="r2", created_at="2026-10-20T20:00:00+00:00", estimated_total_cost_usd=0.05),
        _run(run_id="r3", created_at="2026-10-19T08:00:00+00:00", estimated_total_cost_usd=0.07),
    ]
    report = build_cost_report(runs=runs, stage_rows=[], stage0_rows=[], now=NOW)
    days = {d.day: d.estimated_cost_usd for d in report.by_day}
    assert round(days["2026-10-20"], 4) == 0.15
    assert round(days["2026-10-19"], 4) == 0.07


def test_by_day_includes_stage0_cost_for_that_runs_day():
    runs = [_run(run_id="r1", created_at="2026-10-20T08:00:00+00:00", estimated_total_cost_usd=0.10)]
    stage0_rows = [Stage0CostRow(run_id="r1", run_created_at="2026-10-20T08:00:00+00:00", estimated_cost_usd=0.02)]
    report = build_cost_report(runs=runs, stage_rows=[], stage0_rows=stage0_rows, now=NOW)
    assert round(report.by_day[0].estimated_cost_usd, 4) == 0.12


def test_by_run_is_sorted_newest_first():
    runs = [
        _run(run_id="old", created_at="2026-10-19T00:00:00+00:00"),
        _run(run_id="new", created_at="2026-10-20T00:00:00+00:00"),
    ]
    report = build_cost_report(runs=runs, stage_rows=[], stage0_rows=[], now=NOW)
    assert [r.run_id for r in report.by_run] == ["new", "old"]


def test_by_run_total_cost_is_main_pipeline_plus_stage0():
    runs = [_run(run_id="r1", estimated_total_cost_usd=0.10)]
    stage0_rows = [Stage0CostRow(run_id="r1", run_created_at="2026-10-20T00:00:00+00:00", estimated_cost_usd=0.03)]
    report = build_cost_report(runs=runs, stage_rows=[], stage0_rows=stage0_rows, now=NOW)
    assert round(report.by_run[0].total_cost_usd, 4) == 0.13


def test_by_run_question_preview_is_truncated_for_a_long_question():
    long_question = "x" * 300
    runs = [_run(run_id="r1", question=long_question)]
    report = build_cost_report(runs=runs, stage_rows=[], stage0_rows=[], now=NOW)
    preview = report.by_run[0].question_preview
    assert len(preview) < len(long_question)
    assert preview.endswith("…")


def test_by_run_short_question_is_shown_in_full():
    runs = [_run(run_id="r1", question="Short Q?")]
    report = build_cost_report(runs=runs, stage_rows=[], stage0_rows=[], now=NOW)
    assert report.by_run[0].question_preview == "Short Q?"


def test_total_calls_tokens_exclude_stage0():
    """Stage 0 has no per-call/token tracking -- the grand totals must
    never silently include a fabricated contribution from it."""
    runs = [_run()]
    stage_rows = [_stage_row(input_tokens=10, output_tokens=20, model_attempts=1)]
    stage0_rows = [Stage0CostRow(run_id="r1", run_created_at="2026-10-20T00:00:00+00:00", estimated_cost_usd=0.03)]
    report = build_cost_report(runs=runs, stage_rows=stage_rows, stage0_rows=stage0_rows, now=NOW)
    assert report.total_input_tokens == 10
    assert report.total_output_tokens == 20
    assert report.total_calls == 1


def test_report_carries_the_window_boundaries():
    report = build_cost_report(runs=[], stage_rows=[], stage0_rows=[], now=NOW, window_days=30)
    assert report.window_days == 30
    assert report.since.startswith("2026-09-21")
    assert report.until.startswith("2026-10-21")
