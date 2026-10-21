"""Offline tests for the store.py query methods the cost report (see
cost_report.py) relies on -- list_runs_since, list_stage_cost_rows_since,
list_stage0_cost_rows_since. No network calls; pure SQLite reads/writes.
"""
from __future__ import annotations

from llm_deliberation.store import Repository, utc_now_iso


def _repo(tmp_path) -> Repository:
    return Repository(tmp_path / "db.sqlite3")


def test_list_runs_since_excludes_runs_before_the_cutoff(tmp_path):
    repo = _repo(tmp_path)
    repo.insert_run("old", question="old?", context=None, profile="economy", red_team_enabled=False)
    repo._conn.execute("UPDATE runs SET created_at = '2020-01-01T00:00:00+00:00' WHERE id = 'old'")
    repo.insert_run("new", question="new?", context=None, profile="economy", red_team_enabled=False)
    repo._conn.commit()

    runs = repo.list_runs_since("2025-01-01T00:00:00+00:00")
    assert [r.id for r in runs] == ["new"]


def test_list_runs_since_has_no_limit(tmp_path):
    """Unlike list_runs() (default limit=50), this must never silently
    drop a run inside its own window."""
    repo = _repo(tmp_path)
    for i in range(60):
        repo.insert_run(f"run{i}", question="Q?", context=None, profile="economy", red_team_enabled=False)
    runs = repo.list_runs_since("2000-01-01T00:00:00+00:00")
    assert len(runs) == 60


def test_list_stage_cost_rows_since_excludes_never_attempted_stages(tmp_path):
    """A 'pending' stage has a DB-default model_attempts of 1 despite
    never having been called -- it must never be counted."""
    repo = _repo(tmp_path)
    repo.insert_run("run1", question="Q?", context=None, profile="economy", red_team_enabled=False)
    repo.insert_stage("run1", "analysis_a")  # never started

    rows = repo.list_stage_cost_rows_since("2000-01-01T00:00:00+00:00")
    assert rows == []


def test_list_stage_cost_rows_since_includes_a_started_stage(tmp_path):
    repo = _repo(tmp_path)
    repo.insert_run("run1", question="Q?", context=None, profile="economy", red_team_enabled=False)
    stage_id = repo.insert_stage("run1", "analysis_a")
    repo.mark_stage_running(stage_id)
    repo.mark_stage_succeeded(
        stage_id, provider="OpenAI", model="gpt-5.6-terra", text="t",
        input_tokens=10, output_tokens=20, estimated_cost_usd=0.01,
    )

    rows = repo.list_stage_cost_rows_since("2000-01-01T00:00:00+00:00")
    assert len(rows) == 1
    row = rows[0]
    assert row.run_id == "run1"
    assert row.name == "analysis_a"
    assert row.provider == "OpenAI"
    assert row.input_tokens == 10
    assert row.output_tokens == 20
    assert row.estimated_cost_usd == 0.01
    assert row.model_attempts == 1


def test_list_stage_cost_rows_since_includes_a_failed_stage_with_sunk_cost(tmp_path):
    """A failed stage never gets its provider column set (mark_stage_failed
    doesn't take one), but it must still be counted for cost/tokens --
    only the provider attribution is handled later by cost_report.py."""
    repo = _repo(tmp_path)
    repo.insert_run("run1", question="Q?", context=None, profile="economy", red_team_enabled=False)
    stage_id = repo.insert_stage("run1", "analysis_a")
    repo.mark_stage_running(stage_id)
    repo.mark_stage_failed(stage_id, error="boom", estimated_cost_usd=0.02)

    rows = repo.list_stage_cost_rows_since("2000-01-01T00:00:00+00:00")
    assert len(rows) == 1
    assert rows[0].provider is None
    assert rows[0].estimated_cost_usd == 0.02


def test_list_stage_cost_rows_since_respects_the_cutoff(tmp_path):
    repo = _repo(tmp_path)
    repo.insert_run("run1", question="Q?", context=None, profile="economy", red_team_enabled=False)
    repo._conn.execute("UPDATE runs SET created_at = '2020-01-01T00:00:00+00:00' WHERE id = 'run1'")
    repo._conn.commit()
    stage_id = repo.insert_stage("run1", "analysis_a")
    repo.mark_stage_running(stage_id)
    repo.mark_stage_succeeded(
        stage_id, provider="OpenAI", model="m", text="t",
        input_tokens=1, output_tokens=1, estimated_cost_usd=0.01,
    )

    rows = repo.list_stage_cost_rows_since("2025-01-01T00:00:00+00:00")
    assert rows == []


def test_list_stage0_cost_rows_since_returns_the_combined_total(tmp_path):
    repo = _repo(tmp_path)
    repo.insert_run("run1", question="Q?", context=None, profile="economy", red_team_enabled=False)
    repo.insert_stage0("run1")
    repo.update_stage0_problem_framing(
        "run1", problem_type_json='{"classification":"simple","reason":"r","questions":["q"]}',
        estimated_cost_usd=0.01,
    )
    repo.record_stage0_cost("run1", 0.02)

    rows = repo.list_stage0_cost_rows_since("2000-01-01T00:00:00+00:00")
    assert len(rows) == 1
    assert rows[0].run_id == "run1"
    assert round(rows[0].estimated_cost_usd, 4) == 0.03


def test_list_stage0_cost_rows_since_excludes_runs_without_stage0(tmp_path):
    repo = _repo(tmp_path)
    repo.insert_run("run1", question="Q?", context=None, profile="economy", red_team_enabled=False)
    rows = repo.list_stage0_cost_rows_since("2000-01-01T00:00:00+00:00")
    assert rows == []
