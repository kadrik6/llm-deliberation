from __future__ import annotations

import asyncio

import pytest

from llm_deliberation.orchestrator import ALL_STAGE_NAMES


def test_create_run_creates_all_stages_when_red_team_enabled(service):
    run_id = service.create_run("Q?", "economy", red_team_enabled=True)
    record = service.get_run(run_id)

    assert record.status == "pending"
    stage_names = {s.name for s in record.stages}
    assert stage_names == set(ALL_STAGE_NAMES)
    assert all(s.status == "pending" for s in record.stages)


def test_red_team_disabled_does_not_create_or_require_gemini_stage(
    service, fake_orchestrator_state
):
    run_id = service.create_run("Q?", "economy", red_team_enabled=False)
    record = service.get_run(run_id)

    stage_names = {s.name for s in record.stages}
    assert "red_team" not in stage_names
    assert stage_names == set(ALL_STAGE_NAMES) - {"red_team"}

    # No GEMINI_API_KEY needed: start_run must succeed even without one set.
    import os

    os.environ.pop("GEMINI_API_KEY", None)

    result = asyncio.run(service.start_run(run_id))
    assert result.status == "succeeded"
    assert "red_team" not in fake_orchestrator_state["log"]


def test_stage_persistence_after_successful_run(service):
    run_id = service.create_run("Q?", "economy", red_team_enabled=True)
    record = asyncio.run(service.start_run(run_id))

    assert record.status == "succeeded"
    assert record.started_at is not None
    assert record.completed_at is not None

    for stage in record.stages:
        assert stage.status == "succeeded"
        assert stage.text == f"{stage.name}-output"
        assert stage.provider == "Fake"
        assert stage.model == "fake-model"
        assert stage.input_tokens == 10
        assert stage.output_tokens == 20
        assert stage.estimated_cost_usd == pytest.approx(0.001)

    # RunResult reconstruction should not raise once every stage succeeded.
    result = service.to_run_result(run_id)
    assert result.synthesis.text == "synthesis-output"
    assert result.red_team is not None


def test_completed_stage_survives_later_failure(service, fake_orchestrator_state):
    fake_orchestrator_state["fail"] = {"revision_a"}

    run_id = service.create_run("Q?", "economy", red_team_enabled=True)
    record = asyncio.run(service.start_run(run_id))

    assert record.status == "failed"
    by_name = {s.name: s for s in record.stages}

    # Earlier waves completed and must not be wiped out by the later failure.
    for name in ("analysis_a", "analysis_b", "critique_a_of_b", "critique_b_of_a", "red_team"):
        assert by_name[name].status == "succeeded"
        assert by_name[name].text == f"{name}-output"

    assert by_name["revision_a"].status == "failed"
    assert "simulated failure" in by_name["revision_a"].error

    # revision_b was attempted concurrently with revision_a and should have
    # succeeded on its own.
    assert by_name["revision_b"].status == "succeeded"

    # synthesis never ran because its wave was never reached.
    assert by_name["synthesis"].status == "pending"


def test_retry_does_not_rerun_completed_unrelated_stages(service, fake_orchestrator_state):
    fake_orchestrator_state["fail"] = {"revision_a"}
    run_id = service.create_run("Q?", "economy", red_team_enabled=True)
    asyncio.run(service.start_run(run_id))

    fake_orchestrator_state["log"].clear()
    fake_orchestrator_state["fail"] = set()  # simulate the underlying issue being fixed

    record = asyncio.run(service.retry_stage(run_id, "revision_a"))

    assert record.status == "succeeded"
    # Only the retried stage and the newly-unblocked synthesis stage ran;
    # everything already succeeded (including sibling revision_b) was
    # skipped.
    assert set(fake_orchestrator_state["log"]) == {"revision_a", "synthesis"}
    assert fake_orchestrator_state["log"].count("revision_a") == 1
    assert fake_orchestrator_state["log"].count("revision_b") == 0


def test_retry_stage_by_row_id_also_works(service, fake_orchestrator_state):
    fake_orchestrator_state["fail"] = {"analysis_a"}
    run_id = service.create_run("Q?", "economy", red_team_enabled=False)
    record = asyncio.run(service.start_run(run_id))
    assert record.status == "failed"

    stage_id = next(s.id for s in record.stages if s.name == "analysis_a")
    fake_orchestrator_state["fail"] = set()
    record = asyncio.run(service.retry_stage(run_id, stage_id))
    assert record.status == "succeeded"


def test_run_cost_is_aggregated_correctly(service):
    run_id = service.create_run("Q?", "economy", red_team_enabled=True)
    record = asyncio.run(service.start_run(run_id))

    assert record.status == "succeeded"
    assert len(record.stages) == len(ALL_STAGE_NAMES)
    expected = 0.001 * len(ALL_STAGE_NAMES)
    assert record.estimated_total_cost_usd == pytest.approx(expected)


def test_list_runs_orders_recent_first(service):
    first = service.create_run("Q1", "economy", red_team_enabled=False)
    second = service.create_run("Q2", "economy", red_team_enabled=False)

    runs = service.list_runs()
    ids = [r.id for r in runs]
    assert ids.index(second) < ids.index(first)


def test_retry_succeeded_stage_raises(service):
    run_id = service.create_run("Q?", "economy", red_team_enabled=False)
    asyncio.run(service.start_run(run_id))

    with pytest.raises(ValueError):
        asyncio.run(service.retry_stage(run_id, "analysis_a"))
