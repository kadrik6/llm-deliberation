"""Offline tests for Stage 0 (question framing) at the service layer (ADR
009). Every provider call is faked via FakeOrchestrator (see
tests/conftest.py) -- no live API calls anywhere here.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from llm_deliberation.providers import ProviderGenerationError


def test_create_run_without_question_framing_creates_no_stage0_row(service):
    run_id = service.create_run("Q?", "economy", red_team_enabled=False)
    record = service.get_run(run_id)
    assert record.stage0 is None


def test_create_run_with_question_framing_enabled_creates_an_unlocked_stage0_row(service):
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, question_framing_enabled=True
    )
    record = service.get_run(run_id)
    assert record.stage0 is not None
    assert record.stage0.locked_at is None


def test_run_question_framing_persists_and_returns_the_parsed_result(service, fake_orchestrator_state):
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, question_framing_enabled=True
    )
    result = asyncio.run(service.run_question_framing(run_id))

    assert result.classification == "complicated"
    assert len(result.questions) >= 1
    record = service.get_run(run_id)
    assert record.stage0.problem_type_json is not None
    assert json.loads(record.stage0.problem_type_json)["classification"] == "complicated"


def test_run_question_framing_without_enabling_stage0_raises(service):
    run_id = service.create_run("Q?", "economy", red_team_enabled=False)
    with pytest.raises(ValueError):
        asyncio.run(service.run_question_framing(run_id))


def test_record_interview_answers_persists_question_answer_pairs(service, fake_orchestrator_state):
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, question_framing_enabled=True
    )
    problem_framing = asyncio.run(service.run_question_framing(run_id))
    answers = [f"answer {i}" for i in range(len(problem_framing.questions))]

    service.record_interview_answers(run_id, answers)

    record = service.get_run(run_id)
    interview = json.loads(record.stage0.interview_json)
    assert len(interview) == len(problem_framing.questions)
    assert interview[0]["answer"] == "answer 0"
    assert interview[0]["question"] == problem_framing.questions[0]


def test_record_interview_answers_rejects_a_mismatched_count(service, fake_orchestrator_state):
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, question_framing_enabled=True
    )
    asyncio.run(service.run_question_framing(run_id))
    with pytest.raises(ValueError):
        service.record_interview_answers(run_id, ["only one answer"])


def test_run_framing_analysis_runs_independently_for_each_candidate(service, fake_orchestrator_state):
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, question_framing_enabled=True
    )
    problem_framing = asyncio.run(service.run_question_framing(run_id))
    service.record_interview_answers(run_id, ["" for _ in problem_framing.questions])

    analysis_a = asyncio.run(service.run_framing_analysis(run_id, "a"))
    analysis_b = asyncio.run(service.run_framing_analysis(run_id, "b"))

    assert analysis_a.is_right_problem == "yes"
    assert analysis_b.is_right_problem == "yes"
    assert "framing_analysis_a" in fake_orchestrator_state["log"]
    assert "framing_analysis_b" in fake_orchestrator_state["log"]

    record = service.get_run(run_id)
    assert record.stage0.framing_analysis_a_json is not None
    assert record.stage0.framing_analysis_b_json is not None


def test_run_framing_analysis_rejects_an_unknown_candidate(service, fake_orchestrator_state):
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, question_framing_enabled=True
    )
    with pytest.raises(ValueError):
        asyncio.run(service.run_framing_analysis(run_id, "c"))


def test_a_failed_question_framing_call_still_records_its_sunk_cost(service, fake_orchestrator_state):
    error = ProviderGenerationError(
        "response did not match the expected schema",
        requested_model="fake-model",
        attempts=1,
        attempt_log=[],
        fallback_used=False,
        fallback_reason=None,
        estimated_cost_usd=0.05,
        reason="structured_output_invalid",
    )
    fake_orchestrator_state["fail_with"] = {"question_framing": error}

    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, question_framing_enabled=True
    )
    with pytest.raises(ProviderGenerationError):
        asyncio.run(service.run_question_framing(run_id))

    record = service.get_run(run_id)
    assert record.stage0.problem_type_json is None
    assert round(record.stage0.estimated_cost_usd, 4) == 0.05


def test_a_fenced_problem_framing_response_still_allows_recording_answers(
    service, fake_orchestrator_state
):
    """Regression: parse_problem_framing tolerates a ```json fence, but the
    stored artifact must be the canonical (fence-free) JSON -- not the raw,
    possibly-fenced response.text -- so a later plain json.loads() (see
    record_interview_answers) never breaks on a fence the parser itself
    already stripped once."""
    fenced = "```json\n" + json.dumps(
        {"classification": "complicated", "reason": "r", "questions": ["Q1?", "Q2?"]}
    ) + "\n```"
    fake_orchestrator_state["responses"] = {"question_framing": {"text": fenced}}

    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, question_framing_enabled=True
    )
    asyncio.run(service.run_question_framing(run_id))

    record = service.get_run(run_id)
    assert "```" not in record.stage0.problem_type_json
    json.loads(record.stage0.problem_type_json)  # must not raise

    # The actual regression this bug caused: record_interview_answers reads
    # stage0.problem_type_json back with a bare json.loads().
    service.record_interview_answers(run_id, ["answer 1", "answer 2"])
    interview = json.loads(service.get_run(run_id).stage0.interview_json)
    assert interview[0]["answer"] == "answer 1"


def test_a_fenced_framing_analysis_response_is_stored_canonically(service, fake_orchestrator_state):
    fenced = "```json\n" + json.dumps(
        {"is_right_problem": "yes", "premortem_reasons": ["r1", "r2", "r3"]}
    ) + "\n```"
    fake_orchestrator_state["responses"] = {"framing_analysis_a": {"text": fenced}}

    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, question_framing_enabled=True
    )
    asyncio.run(service.run_framing_analysis(run_id, "a"))

    record = service.get_run(run_id)
    assert "```" not in record.stage0.framing_analysis_a_json
    json.loads(record.stage0.framing_analysis_a_json)  # must not raise


def test_a_locally_malformed_question_framing_response_still_records_cost(
    service, fake_orchestrator_state
):
    """Regression: FakeOrchestrator hands back a response verbatim (no
    validation of its own -- see conftest.py), so a malformed text reaches
    service.run_question_framing's own re-parse and raises a bare
    QuestionFramingParseError there, NOT a ProviderGenerationError. That
    call was still paid for; the cost must not be dropped just because this
    second, local parse failed."""
    from llm_deliberation.question_framing import QuestionFramingParseError

    fake_orchestrator_state["responses"] = {
        "question_framing": {"text": "not valid json at all", "estimated_cost_usd": 0.03},
    }
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, question_framing_enabled=True
    )

    with pytest.raises(QuestionFramingParseError):
        asyncio.run(service.run_question_framing(run_id))

    record = service.get_run(run_id)
    assert record.stage0.problem_type_json is None
    assert round(record.stage0.estimated_cost_usd, 4) == 0.03


def test_a_locally_malformed_framing_analysis_response_still_records_cost(
    service, fake_orchestrator_state
):
    from llm_deliberation.question_framing import QuestionFramingParseError

    fake_orchestrator_state["responses"] = {
        "framing_analysis_b": {"text": "not valid json at all", "estimated_cost_usd": 0.04},
    }
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, question_framing_enabled=True
    )

    with pytest.raises(QuestionFramingParseError):
        asyncio.run(service.run_framing_analysis(run_id, "b"))

    record = service.get_run(run_id)
    assert record.stage0.framing_analysis_b_json is None
    assert round(record.stage0.estimated_cost_usd, 4) == 0.04


def test_lock_question_framing_sets_final_question(service):
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, question_framing_enabled=True
    )
    service.lock_question_framing(run_id, final_question="Reframed Q?", success_criteria="A clear outcome")

    record = service.get_run(run_id)
    assert record.stage0.locked_at is not None
    assert record.stage0.final_question == "Reframed Q?"
    assert record.stage0.success_criteria == "A clear outcome"
    assert record.question == "Q?"  # original, never mutated


def test_lock_question_framing_rejects_an_empty_final_question(service):
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, question_framing_enabled=True
    )
    with pytest.raises(ValueError):
        service.lock_question_framing(run_id, final_question="   ", success_criteria=None)


def test_locked_question_framing_is_used_as_the_effective_question(service, fake_orchestrator_state):
    """_execute must prefer stage0.final_question over run.question once
    locked, and must never mutate run.question itself (see ADR 009)."""
    run_id = service.create_run(
        "Original Q?", "economy", red_team_enabled=False, question_framing_enabled=True
    )
    service.lock_question_framing(run_id, final_question="Reframed Q?", success_criteria=None)

    asyncio.run(service.start_run(run_id))

    record = service.get_run(run_id)
    assert record.question == "Original Q?"
    assert fake_orchestrator_state["questions"][0] == "Reframed Q?"


def test_an_unlocked_stage0_falls_back_to_the_original_question(service, fake_orchestrator_state):
    """Stage 0 enabled but never locked (e.g. the CLI flow was aborted)
    must not block or corrupt the main pipeline -- it simply falls back to
    run.question, exactly like a run with Stage 0 disabled."""
    run_id = service.create_run(
        "Original Q?", "economy", red_team_enabled=False, question_framing_enabled=True
    )
    asyncio.run(service.start_run(run_id))

    assert fake_orchestrator_state["questions"][0] == "Original Q?"


def test_skipped_question_framing_uses_the_original_question(service, fake_orchestrator_state):
    run_id = service.create_run(
        "Original Q?", "economy", red_team_enabled=False, question_framing_enabled=True
    )
    service.lock_question_framing(
        run_id, final_question="Original Q?", success_criteria=None, skipped=True
    )
    asyncio.run(service.start_run(run_id))

    record = service.get_run(run_id)
    assert record.stage0.skipped is True
    assert fake_orchestrator_state["questions"][0] == "Original Q?"


def test_to_run_result_includes_both_questions_and_the_success_criterion(service, fake_orchestrator_state):
    run_id = service.create_run(
        "Original Q?", "economy", red_team_enabled=False, question_framing_enabled=True
    )
    service.lock_question_framing(run_id, final_question="Reframed Q?", success_criteria="Clear outcome")
    asyncio.run(service.start_run(run_id))

    result = service.to_run_result(run_id)
    assert result.question == "Original Q?"
    assert result.locked_question == "Reframed Q?"
    assert result.success_criteria == "Clear outcome"
    assert result.question_framing_skipped is False


def test_to_run_result_has_no_stage0_fields_when_never_enabled(service, fake_orchestrator_state):
    run_id = service.create_run("Q?", "economy", red_team_enabled=False)
    asyncio.run(service.start_run(run_id))

    result = service.to_run_result(run_id)
    assert result.locked_question is None
    assert result.success_criteria is None
    assert result.question_framing_skipped is False


def test_stage0_cost_counts_against_the_shared_run_budget(service, fake_orchestrator_state):
    """Stage 0 and the main pipeline share one budget pool -- a tightly
    budgeted run must be blockable by Stage 0 spend alone (see ADR 009)."""
    run_id = service.create_run(
        "Q?",
        "economy",
        red_team_enabled=False,
        question_framing_enabled=True,
        max_run_cost_usd="0.002",
    )
    fake_orchestrator_state["responses"] = {
        "question_framing": {"estimated_cost_usd": 0.0019},
    }
    asyncio.run(service.run_question_framing(run_id))

    record = service.get_run(run_id)
    assert round(record.stage0.estimated_cost_usd, 4) == 0.0019

    # The main pipeline's own guard must see this spend and have almost no
    # room left -- exercised indirectly via start_run with a cheap stage
    # upper bound that still exceeds the tiny remainder.
    fake_orchestrator_state["upper_bound_cost_usd"] = __import__("decimal").Decimal("0.01")
    result = asyncio.run(service.start_run(run_id))
    assert result.status == "failed"
    failed_stage = next(s for s in result.stages if s.status == "failed")
    assert failed_stage.failure_reason == "run_budget_exceeded"
