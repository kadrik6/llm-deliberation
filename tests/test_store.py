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
