from __future__ import annotations

from llm_deliberation.store import Repository


def test_insert_and_get_run_round_trip(tmp_path):
    repo = Repository(tmp_path / "db.sqlite3")
    repo.insert_run(
        "run1", question="Q?", context="ctx", profile="economy", red_team_enabled=True
    )

    run = repo.get_run("run1")
    assert run.question == "Q?"
    assert run.context == "ctx"
    assert run.profile == "economy"
    assert run.red_team_enabled is True
    assert run.status == "pending"


def test_stage_lifecycle(tmp_path):
    repo = Repository(tmp_path / "db.sqlite3")
    repo.insert_run("run1", question="Q?", context=None, profile="economy", red_team_enabled=False)
    stage_id = repo.insert_stage("run1", "analysis_a")

    stage = repo.get_stage("run1", "analysis_a")
    assert stage.status == "pending"
    assert stage.attempt == 0

    repo.mark_stage_running(stage_id)
    stage = repo.get_stage("run1", stage_id)
    assert stage.status == "running"
    assert stage.attempt == 1

    repo.mark_stage_succeeded(
        stage_id,
        provider="Fake",
        model="fake-model",
        text="hello",
        input_tokens=5,
        output_tokens=7,
        estimated_cost_usd=0.002,
    )
    stage = repo.get_stage("run1", stage_id)
    assert stage.status == "succeeded"
    assert stage.text == "hello"
    assert stage.estimated_cost_usd == 0.002

    # A later failed retry must not erase the artifact of a previous success
    # until reset_stage() is explicitly called.
    repo.mark_stage_failed(stage_id, error="boom")
    stage = repo.get_stage("run1", stage_id)
    assert stage.status == "failed"
    assert stage.error == "boom"

    repo.reset_stage(stage_id)
    stage = repo.get_stage("run1", stage_id)
    assert stage.status == "pending"
    assert stage.error is None


def test_sum_stage_costs(tmp_path):
    repo = Repository(tmp_path / "db.sqlite3")
    repo.insert_run("run1", question="Q?", context=None, profile="economy", red_team_enabled=False)
    s1 = repo.insert_stage("run1", "analysis_a")
    s2 = repo.insert_stage("run1", "analysis_b")

    repo.mark_stage_succeeded(
        s1, provider="Fake", model="m", text="a", input_tokens=1, output_tokens=1,
        estimated_cost_usd=0.01,
    )
    repo.mark_stage_succeeded(
        s2, provider="Fake", model="m", text="b", input_tokens=1, output_tokens=1,
        estimated_cost_usd=0.02,
    )

    assert repo.sum_stage_costs("run1") == 0.03 or abs(repo.sum_stage_costs("run1") - 0.03) < 1e-9


def test_unknown_run_raises_keyerror(tmp_path):
    repo = Repository(tmp_path / "db.sqlite3")
    try:
        repo.get_run("missing")
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_stage_cost_accumulates_across_a_failed_then_successful_attempt(tmp_path):
    """A paid-but-unusable attempt's cost must survive a later successful
    retry of the same stage -- never overwritten/dropped (see
    orchestrator.run_stage's bounded truncation recovery and
    service.retry_stage)."""
    repo = Repository(tmp_path / "db.sqlite3")
    repo.insert_run("run1", question="Q?", context=None, profile="economy", red_team_enabled=False)
    stage_id = repo.insert_stage("run1", "analysis_a")

    repo.mark_stage_failed(
        stage_id, error="truncated", estimated_cost_usd=0.05, failure_reason="output_truncated"
    )
    stage = repo.get_stage("run1", stage_id)
    assert stage.estimated_cost_usd == 0.05
    assert stage.failure_reason == "output_truncated"

    repo.reset_stage(stage_id)
    stage = repo.get_stage("run1", stage_id)
    assert stage.estimated_cost_usd == 0.05  # reset never clears accumulated cost
    assert stage.failure_reason is None  # but the classification is cleared

    repo.mark_stage_succeeded(
        stage_id, provider="Fake", model="m", text="ok", input_tokens=1, output_tokens=1,
        estimated_cost_usd=0.03,
    )
    stage = repo.get_stage("run1", stage_id)
    assert stage.status == "succeeded"
    assert stage.failure_reason is None
    assert abs(stage.estimated_cost_usd - (0.05 + 0.03)) < 1e-9


def test_run_created_before_max_run_cost_usd_column_existed_opens_correctly(tmp_path):
    """Reproduces an old database (pre-budget-feature) by building the
    `runs`/`stages` schema exactly as it existed before max_run_cost_usd was
    added, using raw sqlite3 -- then opens it through Repository and
    confirms the migration adds the column additively (NULL/None, never an
    invented default) without touching any pre-existing row's data. Mirrors
    the existing `language` column's own migration precedent.
    """
    import sqlite3

    db_path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE runs (
            id TEXT PRIMARY KEY,
            question TEXT NOT NULL,
            context TEXT,
            profile TEXT NOT NULL,
            red_team_enabled INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            estimated_total_cost_usd REAL NOT NULL DEFAULT 0.0,
            language TEXT NOT NULL DEFAULT 'en'
        );
        CREATE TABLE stages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            name TEXT NOT NULL,
            provider TEXT, model TEXT, status TEXT NOT NULL DEFAULT 'pending',
            attempt INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0, estimated_cost_usd REAL NOT NULL DEFAULT 0.0,
            error TEXT, started_at TEXT, completed_at TEXT
        );
        CREATE TABLE artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, stage_id INTEGER NOT NULL,
            artifact_type TEXT NOT NULL, text_content TEXT NOT NULL, created_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO runs (id, question, context, profile, red_team_enabled, status, "
        "created_at, estimated_total_cost_usd, language) "
        "VALUES ('old1', 'Old question?', NULL, 'economy', 0, 'succeeded', "
        "'2025-01-01T00:00:00+00:00', 0.0123, 'en')"
    )
    conn.commit()
    conn.close()

    repo = Repository(db_path)
    run = repo.get_run("old1")
    assert run.question == "Old question?"
    assert run.status == "succeeded"
    assert run.max_run_cost_usd is None  # additive migration, not an invented default
    assert abs(run.estimated_total_cost_usd - 0.0123) < 1e-9

    # Still fully writable through every existing code path.
    repo.update_run_budget("old1", "5.00")
    assert repo.get_run("old1").max_run_cost_usd == "5.00"


def test_stage_cost_accumulates_across_two_failed_attempts(tmp_path):
    repo = Repository(tmp_path / "db.sqlite3")
    repo.insert_run("run1", question="Q?", context=None, profile="economy", red_team_enabled=False)
    stage_id = repo.insert_stage("run1", "analysis_a")

    repo.mark_stage_failed(stage_id, error="e1", estimated_cost_usd=0.01)
    repo.reset_stage(stage_id)
    repo.mark_stage_failed(stage_id, error="e2", estimated_cost_usd=0.02)

    stage = repo.get_stage("run1", stage_id)
    assert abs(stage.estimated_cost_usd - 0.03) < 1e-9
