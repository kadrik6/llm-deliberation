"""Offline, integration-level tests for DeliberationService.cost_report --
drives real (fake-orchestrator) runs through the actual service/store
layers, then checks the aggregation. No network calls (FakeOrchestrator,
see tests/conftest.py).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from llm_deliberation.cost_report import STAGE0_PROVIDER_LABEL


def test_cost_report_is_empty_for_a_fresh_database(service):
    report = service.cost_report()
    assert report.by_run == []
    assert report.total_cost_usd == 0.0


def test_cost_report_reflects_a_succeeded_run(service, fake_orchestrator_state):
    run_id = service.create_run("Q?", "economy", red_team_enabled=False)
    record = asyncio.run(service.start_run(run_id))
    assert record.status == "succeeded"

    report = service.cost_report()
    assert len(report.by_run) == 1
    assert report.by_run[0].run_id == run_id
    assert round(report.total_cost_usd, 4) == round(record.estimated_total_cost_usd, 4)
    assert report.stage0_cost_usd == 0.0


def test_cost_report_separates_stage0_from_main_pipeline(service, fake_orchestrator_state):
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, question_framing_enabled=True
    )
    problem_framing = asyncio.run(service.run_question_framing(run_id))
    service.record_interview_answers(run_id, ["" for _ in problem_framing.questions])
    asyncio.run(service.run_framing_analysis(run_id, "a"))
    asyncio.run(service.run_framing_analysis(run_id, "b"))
    service.lock_question_framing(run_id, final_question="Q?", success_criteria=None)
    record = asyncio.run(service.start_run(run_id))

    report = service.cost_report()
    run_total = report.by_run[0]
    assert run_total.main_pipeline_cost_usd == record.estimated_total_cost_usd
    assert run_total.stage0_cost_usd == record.stage0.estimated_cost_usd
    assert run_total.stage0_cost_usd > 0
    assert any(p.provider == STAGE0_PROVIDER_LABEL for p in report.by_provider)


def test_cost_report_by_provider_matches_fake_orchestrators_providers(service, fake_orchestrator_state):
    run_id = service.create_run("Q?", "economy", red_team_enabled=True)
    fake_orchestrator_state["responses"] = {
        "analysis_a": {"provider": "OpenAI"},
        "analysis_b": {"provider": "Anthropic"},
        "red_team": {"provider": "Google"},
    }
    asyncio.run(service.start_run(run_id))

    report = service.cost_report()
    labels = {p.provider for p in report.by_provider}
    assert "OpenAI" in labels
    assert "Anthropic" in labels
    assert "Gemini" in labels  # "Google" internally, displayed as "Gemini"
    assert "Google" not in labels


def test_cost_report_excludes_runs_outside_the_window(service, fake_orchestrator_state):
    run_id = service.create_run("Q?", "economy", red_team_enabled=False)
    asyncio.run(service.start_run(run_id))

    future = datetime.now(timezone.utc) + timedelta(days=31)
    report = service.cost_report(now=future)
    assert report.by_run == []


def test_cost_report_window_is_injectable_for_deterministic_tests(service, fake_orchestrator_state):
    run_id = service.create_run("Q?", "economy", red_team_enabled=False)
    asyncio.run(service.start_run(run_id))

    fixed_now = datetime.now(timezone.utc)
    report = service.cost_report(now=fixed_now, window_days=30)
    assert report.until == fixed_now.isoformat()
    assert len(report.by_run) == 1


def test_cost_report_reflects_a_failed_runs_sunk_cost(service, fake_orchestrator_state):
    fake_orchestrator_state["fail"] = {"revision_a"}
    run_id = service.create_run("Q?", "economy", red_team_enabled=False)
    record = asyncio.run(service.start_run(run_id))
    assert record.status == "failed"

    report = service.cost_report()
    assert report.by_run[0].status == "failed"
    assert report.by_run[0].total_cost_usd == record.estimated_total_cost_usd
    assert report.by_run[0].total_cost_usd > 0  # earlier stages still cost something


def test_cost_report_multiple_runs_aggregate_correctly(service, fake_orchestrator_state):
    run_id_1 = service.create_run("Q1?", "economy", red_team_enabled=False)
    asyncio.run(service.start_run(run_id_1))
    run_id_2 = service.create_run("Q2?", "economy", red_team_enabled=False)
    asyncio.run(service.start_run(run_id_2))

    report = service.cost_report()
    assert len(report.by_run) == 2
    assert len(report.by_day) == 1  # both created "now", same UTC day
    run_ids = {r.run_id for r in report.by_run}
    assert run_ids == {run_id_1, run_id_2}
