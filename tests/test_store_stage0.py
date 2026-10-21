"""Offline tests for the stage0 table and its Repository methods (ADR 009).

No provider calls -- this file only exercises store.py's persistence layer.
"""
from __future__ import annotations

import json

from llm_deliberation.store import Repository


def _repo_with_run(tmp_path, *, question_framing: bool = True) -> tuple[Repository, str]:
    repo = Repository(tmp_path / "db.sqlite3")
    repo.insert_run("run1", question="Q?", context=None, profile="economy", red_team_enabled=False)
    if question_framing:
        repo.insert_stage0("run1")
    return repo, "run1"


def test_a_run_with_question_framing_disabled_has_no_stage0_row(tmp_path):
    repo, run_id = _repo_with_run(tmp_path, question_framing=False)
    assert repo.get_stage0(run_id) is None


def test_insert_stage0_creates_an_unlocked_row(tmp_path):
    repo, run_id = _repo_with_run(tmp_path)
    stage0 = repo.get_stage0(run_id)
    assert stage0 is not None
    assert stage0.locked_at is None
    assert stage0.skipped is False
    assert stage0.estimated_cost_usd == 0.0
    assert stage0.problem_type_json is None
    assert stage0.interview_json is None


def test_update_stage0_problem_framing_accumulates_cost(tmp_path):
    repo, run_id = _repo_with_run(tmp_path)
    payload = json.dumps({"classification": "complicated", "reason": "r", "questions": ["q1"]})
    repo.update_stage0_problem_framing(run_id, problem_type_json=payload, estimated_cost_usd=0.01)
    repo.update_stage0_problem_framing(run_id, problem_type_json=payload, estimated_cost_usd=0.02)

    stage0 = repo.get_stage0(run_id)
    assert stage0.problem_type_json == payload
    assert stage0.estimated_cost_usd == 0.03


def test_update_stage0_interview_persists_questions_and_answers(tmp_path):
    repo, run_id = _repo_with_run(tmp_path)
    interview = json.dumps([{"question": "Q1?", "answer": "A1"}])
    repo.update_stage0_interview(run_id, interview_json=interview)
    assert repo.get_stage0(run_id).interview_json == interview


def test_update_stage0_framing_analysis_writes_the_correct_candidate_column(tmp_path):
    repo, run_id = _repo_with_run(tmp_path)
    payload_a = json.dumps({"is_right_problem": "yes"})
    payload_b = json.dumps({"is_right_problem": "no"})
    repo.update_stage0_framing_analysis(run_id, candidate="a", analysis_json=payload_a, estimated_cost_usd=0.01)
    repo.update_stage0_framing_analysis(run_id, candidate="b", analysis_json=payload_b, estimated_cost_usd=0.02)

    stage0 = repo.get_stage0(run_id)
    assert stage0.framing_analysis_a_json == payload_a
    assert stage0.framing_analysis_b_json == payload_b
    assert stage0.estimated_cost_usd == 0.03


def test_update_stage0_framing_analysis_rejects_an_unknown_candidate(tmp_path):
    repo, run_id = _repo_with_run(tmp_path)
    import pytest

    with pytest.raises(ValueError):
        repo.update_stage0_framing_analysis(run_id, candidate="c", analysis_json="{}", estimated_cost_usd=0.0)


def test_record_stage0_cost_never_touches_other_columns(tmp_path):
    repo, run_id = _repo_with_run(tmp_path)
    payload = json.dumps({"classification": "simple", "reason": "r", "questions": ["q1"]})
    repo.update_stage0_problem_framing(run_id, problem_type_json=payload, estimated_cost_usd=0.01)

    repo.record_stage0_cost(run_id, 0.05)

    stage0 = repo.get_stage0(run_id)
    assert stage0.problem_type_json == payload
    assert round(stage0.estimated_cost_usd, 4) == 0.06


def test_lock_stage0_sets_final_question_and_locked_at(tmp_path):
    repo, run_id = _repo_with_run(tmp_path)
    assert repo.get_stage0(run_id).locked_at is None

    repo.lock_stage0(run_id, final_question="Reframed Q?", success_criteria="Clear outcome", skipped=False)

    stage0 = repo.get_stage0(run_id)
    assert stage0.final_question == "Reframed Q?"
    assert stage0.success_criteria == "Clear outcome"
    assert stage0.skipped is False
    assert stage0.locked_at is not None


def test_lock_stage0_as_skip_still_sets_final_question_to_the_original(tmp_path):
    repo, run_id = _repo_with_run(tmp_path)
    repo.lock_stage0(run_id, final_question="Q?", success_criteria=None, skipped=True)

    stage0 = repo.get_stage0(run_id)
    assert stage0.skipped is True
    assert stage0.final_question == "Q?"
    assert stage0.success_criteria is None
    assert stage0.locked_at is not None


def test_sum_stage0_cost_is_zero_when_question_framing_was_never_enabled(tmp_path):
    repo, run_id = _repo_with_run(tmp_path, question_framing=False)
    assert repo.sum_stage0_cost(run_id) == 0.0


def test_sum_stage0_cost_reflects_accumulated_spend(tmp_path):
    repo, run_id = _repo_with_run(tmp_path)
    repo.update_stage0_problem_framing(run_id, problem_type_json="{}", estimated_cost_usd=0.02)
    repo.update_stage0_framing_analysis(run_id, candidate="a", analysis_json="{}", estimated_cost_usd=0.03)
    assert round(repo.sum_stage0_cost(run_id), 4) == 0.05


def test_get_run_full_populates_stage0(tmp_path):
    repo, run_id = _repo_with_run(tmp_path)
    repo.lock_stage0(run_id, final_question="Q?", success_criteria="crit", skipped=False)

    run = repo.get_run_full(run_id)
    assert run.stage0 is not None
    assert run.stage0.final_question == "Q?"


def test_get_run_full_leaves_stage0_none_when_not_enabled(tmp_path):
    repo, run_id = _repo_with_run(tmp_path, question_framing=False)
    run = repo.get_run_full(run_id)
    assert run.stage0 is None


def test_get_run_does_not_populate_stages_or_stage0(tmp_path):
    """get_run() stays the lighter fetch (matches the existing `stages`
    behavior) -- only get_run_full() does the extra queries."""
    repo, run_id = _repo_with_run(tmp_path)
    repo.lock_stage0(run_id, final_question="Q?", success_criteria=None, skipped=False)
    run = repo.get_run(run_id)
    assert run.stage0 is None
    assert run.stages == []


def test_an_existing_database_without_stage0_gets_the_table_on_reconnect(tmp_path):
    """Additive-migration safety: reconnecting to a pre-existing database
    (simulated by just opening a fresh connection to the same file -- the
    real scenario is an upgrade from a version before this table existed)
    does not fail and does not touch runs/stages data."""
    db_path = tmp_path / "db.sqlite3"
    repo1 = Repository(db_path)
    repo1.insert_run("run1", question="Q?", context=None, profile="economy", red_team_enabled=False)
    repo1.close()

    repo2 = Repository(db_path)
    run = repo2.get_run("run1")
    assert run.question == "Q?"
    assert repo2.get_stage0("run1") is None
    repo2.insert_stage0("run1")
    assert repo2.get_stage0("run1") is not None
