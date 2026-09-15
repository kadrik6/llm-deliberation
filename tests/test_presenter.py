"""Unit tests for the presenter's running-stage elapsed-time indicator.

No DB, no HTTP -- StageRecord/RunRecord are plain dataclasses, constructed
directly so the elapsed-time math can be checked against a controlled
started_at without depending on real wall-clock delays.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from llm_deliberation.store import RunRecord, StageRecord
from llm_deliberation.web import presenter

RUNNING_NOTE_RE = re.compile(r"^Running · (\d+m )?\d+s$")


def _stage(status: str, *, name: str = "analysis_a", started_at=None, completed_at=None, **overrides) -> StageRecord:
    defaults = dict(
        id=1,
        run_id="r1",
        name=name,
        provider=None,
        model=None,
        status=status,
        attempt=1,
        input_tokens=0,
        output_tokens=0,
        estimated_cost_usd=0.0,
        error=None,
        started_at=started_at,
        completed_at=completed_at,
    )
    defaults.update(overrides)
    return StageRecord(**defaults)


def _run(stages: list[StageRecord], *, status: str = "running") -> RunRecord:
    return RunRecord(
        id="r1",
        question="Q?",
        context=None,
        profile="economy",
        red_team_enabled=True,
        status=status,
        created_at="2026-01-01T00:00:00+00:00",
        started_at="2026-01-01T00:00:00+00:00",
        completed_at=None,
        estimated_total_cost_usd=0.0,
        stages=stages,
    )


def _row_for(groups: list[dict], name: str) -> dict:
    return next(r for group in groups for r in group["rows"] if r["name"] == name)


def test_stage_elapsed_seconds_is_derived_from_started_at():
    started = (datetime.now(timezone.utc) - timedelta(seconds=18)).isoformat()
    stage = _stage("running", started_at=started)

    elapsed = presenter.stage_elapsed_seconds(stage)

    assert elapsed is not None
    assert 15 <= elapsed <= 21  # generous tolerance for test execution time


def test_stage_elapsed_seconds_changes_when_started_at_changes():
    now = datetime.now(timezone.utc)
    recent = _stage("running", started_at=(now - timedelta(seconds=2)).isoformat())
    old = _stage("running", started_at=(now - timedelta(seconds=40)).isoformat())

    recent_elapsed = presenter.stage_elapsed_seconds(recent)
    old_elapsed = presenter.stage_elapsed_seconds(old)

    assert old_elapsed > recent_elapsed
    assert presenter.format_duration(old_elapsed) != presenter.format_duration(recent_elapsed)


def test_running_stage_gets_a_running_note():
    started = (datetime.now(timezone.utc) - timedelta(seconds=18)).isoformat()
    stage = _stage("running", started_at=started)
    run = _run([stage])

    row = _row_for(presenter.build_pipeline(run), "analysis_a")

    assert row["running_note"] is not None
    assert RUNNING_NOTE_RE.match(row["running_note"]), row["running_note"]


def test_non_running_stages_have_no_running_note():
    fixed_start = "2026-01-01T00:00:00+00:00"
    fixed_end = "2026-01-01T00:00:05+00:00"
    for status in ("pending", "succeeded", "failed", "skipped"):
        stage = _stage(
            status,
            started_at=None if status == "pending" else fixed_start,
            completed_at=None if status == "pending" else fixed_end,
        )
        row = _row_for(presenter.build_pipeline(_run([stage], status=status)), "analysis_a")
        assert row["running_note"] is None, f"status={status} should not show a running note"


def test_disabled_stage_has_no_running_note():
    # red_team missing entirely (disabled) -- build_pipeline synthesizes a
    # disabled row with no status at all.
    row = _row_for(presenter.build_pipeline(_run([])), "red_team")
    assert row["disabled"] is True
    assert row.get("running_note") is None


# -- deliberation quality (Section 14, items 12-16) -------------------------

_REQUIRED = presenter.REQUIRED_STAGE_ORDER


def _usable_required_stages() -> list[StageRecord]:
    return [_stage("succeeded", name=name, text=f"{name}-output") for name in _REQUIRED]


def test_running_run_has_no_quality_yet():
    stages = _usable_required_stages()
    run = _run(stages, status="running")
    assert presenter.compute_deliberation_quality(run) is None


def test_complete_run_with_red_team_and_convergence_is_complete():
    stages = _usable_required_stages() + [
        _stage("succeeded", name="red_team", text="red-team-output"),
        _stage("succeeded", name="convergence_analysis", text="{}"),
    ]
    run = _run(stages, status="succeeded")
    run.red_team_enabled = True

    quality = presenter.compute_deliberation_quality(run)

    assert quality == {"level": "complete", "reasons": []}


def test_red_team_disabled_from_the_start_is_not_degraded():
    # No red_team stage row at all -- this is the normal, expected shape for
    # a run created with red-team off, not a missing/failed evidence gap.
    stages = _usable_required_stages() + [
        _stage("succeeded", name="convergence_analysis", text="{}"),
    ]
    run = _run(stages, status="succeeded")
    run.red_team_enabled = False

    quality = presenter.compute_deliberation_quality(run)

    assert quality["level"] == "complete"


def test_configured_red_team_skipped_after_failure_is_degraded():
    stages = _usable_required_stages() + [
        _stage("skipped", name="red_team", error=None),
        _stage("succeeded", name="convergence_analysis", text="{}"),
    ]
    run = _run(stages, status="succeeded")
    run.red_team_enabled = True

    quality = presenter.compute_deliberation_quality(run)

    assert quality["level"] == "degraded"
    assert any(r["stage"] == "red_team" for r in quality["reasons"])


def test_skipped_convergence_analysis_is_degraded():
    stages = _usable_required_stages() + [
        _stage("succeeded", name="red_team", text="red-team-output"),
        _stage("skipped", name="convergence_analysis"),
    ]
    run = _run(stages, status="succeeded")
    run.red_team_enabled = True

    quality = presenter.compute_deliberation_quality(run)

    assert quality["level"] == "degraded"
    assert any(r["stage"] == "convergence_analysis" for r in quality["reasons"])


def test_missing_required_stage_is_incomplete():
    stages = [s for s in _usable_required_stages() if s.name != "revision_a"]
    stages.append(_stage("failed", name="revision_a", error="boom"))
    run = _run(stages, status="failed")

    quality = presenter.compute_deliberation_quality(run)

    assert quality["level"] == "incomplete"
    reasons_by_stage = {r["stage"]: r for r in quality["reasons"]}
    assert reasons_by_stage["revision_a"]["kind"] == "unavailable"


def test_truncated_required_stage_reports_truncated_kind():
    stages = [s for s in _usable_required_stages() if s.name != "revision_b"]
    stages.append(_stage("failed", name="revision_b", error="cut off", failure_reason="output_truncated"))
    run = _run(stages, status="failed")

    quality = presenter.compute_deliberation_quality(run)

    assert quality["level"] == "incomplete"
    reasons_by_stage = {r["stage"]: r for r in quality["reasons"]}
    assert reasons_by_stage["revision_b"]["kind"] == "truncated"


def test_succeeded_stage_with_empty_text_is_still_incomplete():
    # Legacy-data safety net: a stage recorded as "succeeded" with empty
    # text (predating this reliability pass, or any other gap) must never
    # be counted as usable evidence.
    stages = [s for s in _usable_required_stages() if s.name != "analysis_a"]
    stages.append(_stage("succeeded", name="analysis_a", text="   "))
    run = _run(stages, status="failed")

    quality = presenter.compute_deliberation_quality(run)

    assert quality["level"] == "incomplete"
    assert any(r["stage"] == "analysis_a" for r in quality["reasons"])
