"""Offline tests for the /costs web page. No live API calls --
FakeOrchestrator (see tests/conftest.py) populates the DB.
"""
from __future__ import annotations

import asyncio

from llm_deliberation.cost_report import STAGE0_PROVIDER_LABEL

from tests.test_web import _submit


def test_costs_page_renders_for_an_empty_database(client):
    response = client.get("/costs")
    assert response.status_code == 200
    assert "No runs in this window" in response.text


def test_costs_page_shows_the_disclaimer(client):
    body = client.get("/costs").text
    assert "Estimated, from this app&#39;s own calls; not provider billing." in body


def test_costs_page_lists_a_succeeded_run(client, service, fake_orchestrator_state):
    response = _submit(client, question="Should we do X?")
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    body = client.get("/costs").text
    assert run_id in body
    assert f'/runs/{run_id}' in body
    assert "Should we do X?" in body


def test_costs_page_shows_by_provider_section(client, service, fake_orchestrator_state):
    fake_orchestrator_state["responses"] = {
        "analysis_a": {"provider": "OpenAI"},
        "analysis_b": {"provider": "Anthropic"},
    }
    _submit(client, question="Q?")
    body = client.get("/costs").text
    assert "OpenAI" in body
    assert "Anthropic" in body


def test_costs_page_shows_stage0_cost_separately(client, service, fake_orchestrator_state):
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, question_framing_enabled=True
    )
    problem_framing = asyncio.run(service.run_question_framing(run_id))
    service.record_interview_answers(run_id, ["" for _ in problem_framing.questions])
    asyncio.run(service.run_framing_analysis(run_id, "a"))
    asyncio.run(service.run_framing_analysis(run_id, "b"))
    service.lock_question_framing(run_id, final_question="Q?", success_criteria=None)
    asyncio.run(service.start_run(run_id))

    body = client.get("/costs").text
    assert "Stage 0" in body
    assert STAGE0_PROVIDER_LABEL in body


def test_costs_page_has_no_stage0_row_for_an_ordinary_run(client, service, fake_orchestrator_state):
    _submit(client, question="Q?")
    body = client.get("/costs").text
    assert STAGE0_PROVIDER_LABEL not in body


def test_costs_page_escapes_unsafe_html_in_question_preview(client, service, fake_orchestrator_state):
    _submit(client, question="<script>alert(1)</script>")
    body = client.get("/costs").text
    assert "<script>alert(1)</script>" not in body


def test_costs_nav_link_appears_on_every_page(client):
    body = client.get("/").text
    assert '/costs' in body


def test_costs_renders_in_estonian_when_cookie_set(client, service, fake_orchestrator_state):
    client.cookies.set("ui_lang", "et")
    _submit(client, question="Q?")
    body = client.get("/costs").text
    assert "Maksumus" in body
    assert "mitte pakkuja arveldusandmed" in body


def test_costs_translation_keys_have_distinct_en_and_et_text():
    from llm_deliberation.web.i18n import TRANSLATIONS

    for key in (
        "nav_costs", "costs_heading", "costs_disclaimer", "costs_window_label",
        "costs_empty", "costs_total_heading", "costs_total_label",
        "costs_main_pipeline_label", "costs_stage0_label", "costs_stage0_note",
        "costs_calls_label", "costs_tokens_label", "costs_in_word", "costs_out_word",
        "costs_by_provider_heading", "costs_provider_label", "costs_cost_label",
        "costs_by_day_heading", "costs_day_label", "costs_by_run_heading",
    ):
        assert key in TRANSLATIONS
        assert TRANSLATIONS[key].get("en"), f"{key} missing an English translation"
        assert TRANSLATIONS[key].get("et"), f"{key} missing an Estonian translation"
        assert TRANSLATIONS[key]["en"] != TRANSLATIONS[key]["et"]


def test_costs_page_viewing_never_changes_recorded_cost(client, service, fake_orchestrator_state):
    response = _submit(client, question="Q?")
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    before = service.get_run(run_id).estimated_total_cost_usd

    client.get("/costs")
    client.get("/costs")

    after = service.get_run(run_id).estimated_total_cost_usd
    assert before == after
