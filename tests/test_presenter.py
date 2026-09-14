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
