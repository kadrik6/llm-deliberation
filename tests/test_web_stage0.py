"""Offline tests for how the web UI displays a Stage 0 result (ADR 009).

Phase 2 is CLI-only for *creating*/running Stage 0 (no web wizard yet --
see the ADR's deferred-phase note), but a run that had Stage 0 enabled and
locked via the CLI/service layer must still display correctly when viewed
through the web UI (run_detail, the shareable report, "You Decide"). These
tests drive Stage 0 directly through the service layer (bypassing the
nonexistent web wizard) and then assert on the rendered HTML, exactly the
way a CLI-created run would actually be viewed in a browser. No provider
calls: FakeOrchestrator throughout (see tests/conftest.py).
"""
from __future__ import annotations

import asyncio


def _run_id(response) -> str:
    return str(response.url).rstrip("/").rsplit("/", 1)[-1]


def test_run_detail_shows_the_locked_question(client, service, fake_orchestrator_state):
    run_id = service.create_run(
        "Original Q?", "economy", red_team_enabled=False, question_framing_enabled=True
    )
    service.lock_question_framing(run_id, final_question="Reframed Q?", success_criteria=None)
    asyncio.run(service.start_run(run_id))

    body = client.get(f"/runs/{run_id}").text
    assert "Original Q?" in body
    assert "Reframed Q?" in body


def test_run_detail_does_not_show_a_locked_question_block_when_skipped(
    client, service, fake_orchestrator_state
):
    run_id = service.create_run(
        "Original Q?", "economy", red_team_enabled=False, question_framing_enabled=True
    )
    service.lock_question_framing(
        run_id, final_question="Original Q?", success_criteria=None, skipped=True
    )
    asyncio.run(service.start_run(run_id))

    body = client.get(f"/runs/{run_id}").text
    assert "question-framing-locked" not in body


def test_run_detail_has_no_stage0_markup_for_an_ordinary_run(client, service, fake_orchestrator_state):
    run_id = service.create_run("Q?", "economy", red_team_enabled=False)
    asyncio.run(service.start_run(run_id))

    body = client.get(f"/runs/{run_id}").text
    assert "question-framing-locked" not in body


def test_you_decide_shows_the_locked_success_criterion(client, service, fake_orchestrator_state):
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, question_framing_enabled=True
    )
    service.lock_question_framing(run_id, final_question="Q?", success_criteria="A clear outcome")
    asyncio.run(service.start_run(run_id))

    body = client.get(f"/runs/{run_id}").text
    assert "A clear outcome" in body


def test_you_decide_has_no_success_criterion_block_without_stage0(client, service, fake_orchestrator_state):
    run_id = service.create_run("Q?", "economy", red_team_enabled=False)
    asyncio.run(service.start_run(run_id))

    body = client.get(f"/runs/{run_id}").text
    assert "locked-success-criterion" not in body


def test_report_route_shows_both_questions_and_success_criterion(client, service, fake_orchestrator_state):
    run_id = service.create_run(
        "Original Q?", "economy", red_team_enabled=False, question_framing_enabled=True
    )
    service.lock_question_framing(run_id, final_question="Reframed Q?", success_criteria="A clear outcome")
    asyncio.run(service.start_run(run_id))

    body = client.get(f"/runs/{run_id}/report").text
    assert "Original Q?" in body
    assert "Reframed Q?" in body
    assert "A clear outcome" in body


def test_report_route_shows_skipped_note_not_a_duplicate_question(client, service, fake_orchestrator_state):
    run_id = service.create_run(
        "Original Q?", "economy", red_team_enabled=False, question_framing_enabled=True
    )
    service.lock_question_framing(
        run_id, final_question="Original Q?", success_criteria=None, skipped=True
    )
    asyncio.run(service.start_run(run_id))

    body = client.get(f"/runs/{run_id}/report").text
    assert "skipped" in body.lower()


def test_report_route_has_no_stage0_section_for_an_ordinary_run(client, service, fake_orchestrator_state):
    run_id = service.create_run("Q?", "economy", red_team_enabled=False)
    asyncio.run(service.start_run(run_id))

    body = client.get(f"/runs/{run_id}/report").text
    assert "locked-success-criterion" not in body


def test_report_route_escapes_unsafe_html_in_the_locked_question(client, service, fake_orchestrator_state):
    malicious = "<script>alert(1)</script>"
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, question_framing_enabled=True
    )
    service.lock_question_framing(run_id, final_question=malicious, success_criteria=None)
    asyncio.run(service.start_run(run_id))

    body = client.get(f"/runs/{run_id}/report").text
    assert "<script>alert(1)</script>" not in body
