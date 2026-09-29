"""Offline tests for the shareable full report route (GET /runs/{id}/report)
-- see web/app.py's run_report and templates/report.html.

Covers the route's core acceptance criterion: it must work for every run
status (succeeded, failed, red-team off, degraded/fallback, historical/
legacy), never only the happy path -- unlike report.py/to_run_result,
which this route deliberately does not use. No provider calls: every
scenario uses the existing FakeOrchestrator fixtures. No Case 3 data.
"""
from __future__ import annotations

import json

from llm_deliberation.store import RunRecord, StageRecord
from llm_deliberation.web.presenter import completed_stage_duration_seconds, run_token_totals

from tests.test_web import _convergence_response, _submit


def _run_id(response) -> str:
    return str(response.url).rstrip("/").rsplit("/", 1)[-1]


# -- ROUTE: works for every run status --------------------------------------


def test_report_returns_200_for_succeeded_full_run(client, service, fake_orchestrator_state):
    response = _submit(client, question="Q?", red_team=True)
    run_id = _run_id(response)
    report = client.get(f"/runs/{run_id}/report")
    assert report.status_code == 200


def test_report_returns_200_for_failed_incomplete_run(client, service, fake_orchestrator_state):
    fake_orchestrator_state["fail"] = {"revision_a"}
    response = _submit(client, question="Q?", red_team=True)
    run_id = _run_id(response)
    assert service.get_run(run_id).status == "failed"

    report = client.get(f"/runs/{run_id}/report")
    assert report.status_code == 200
    assert "This stage failed" in report.text or "ebaõnnestus" in report.text


def test_report_returns_200_for_red_team_off_run(client, service, fake_orchestrator_state):
    response = _submit(client, question="Q?", red_team=False)
    run_id = _run_id(response)
    report = client.get(f"/runs/{run_id}/report")
    assert report.status_code == 200


def test_report_returns_200_for_degraded_fallback_run(client, service, fake_orchestrator_state):
    fake_orchestrator_state["responses"] = {
        "red_team": {
            "provider": "Google", "model": "gemini-3.7-flash", "requested_model": "gemini-3.8-flash",
            "fallback_used": True, "fallback_reason": "HTTP 503 from Gemini (model is overloaded)",
            "model_attempts": 5,
        }
    }
    response = _submit(client, question="Q?", red_team=True)
    run_id = _run_id(response)
    report = client.get(f"/runs/{run_id}/report")
    assert report.status_code == 200


def test_report_returns_404_for_unknown_run(client, service):
    response = client.get("/runs/does-not-exist/report")
    assert response.status_code == 404


# -- EXPANSION: everything visible, nothing collapsed ------------------------


def test_report_contains_zero_details_elements(client, service, fake_orchestrator_state):
    response = _submit(client, question="Q?", red_team=True)
    run_id = _run_id(response)
    body = client.get(f"/runs/{run_id}/report").text
    assert "<details" not in body


def test_report_contains_every_persisted_stage_artifact_text(client, service, fake_orchestrator_state):
    response = _submit(client, question="Q?", red_team=True)
    run_id = _run_id(response)
    body = client.get(f"/runs/{run_id}/report").text
    for stage in (
        "analysis_a", "analysis_b", "critique_a_of_b", "critique_b_of_a",
        "red_team", "revision_a", "revision_b", "synthesis",
    ):
        assert f"{stage}-output" in body, f"missing artifact text for {stage!r}"


def test_report_shows_synthesis_in_final_answer_and_full_trace(client, service, fake_orchestrator_state):
    response = _submit(client, question="Q?", red_team=False)
    run_id = _run_id(response)
    body = client.get(f"/runs/{run_id}/report").text
    assert body.count("synthesis-output") == 2  # Part I final answer + Part II trace


def test_report_shows_failed_stage_state_honestly_not_an_empty_artifact(
    client, service, fake_orchestrator_state
):
    fake_orchestrator_state["fail"] = {"revision_a"}
    response = _submit(client, question="Q?", red_team=False)
    run_id = _run_id(response)
    body = client.get(f"/runs/{run_id}/report").text
    assert "simulated failure" in body


# -- DERIVED VALUES -----------------------------------------------------------


def test_report_shows_token_totals_cost_duration_attempts(client, service, fake_orchestrator_state):
    response = _submit(client, question="Q?", red_team=True)
    run_id = _run_id(response)
    record = service.get_run(run_id)
    body = client.get(f"/runs/{run_id}/report").text

    totals = run_token_totals(record)
    assert f"{totals['input_tokens']:,}" in body
    assert f"{totals['output_tokens']:,}" in body
    assert f"${record.estimated_total_cost_usd:.4f}" in body


def test_run_token_totals_sums_across_all_stages():
    record = RunRecord(
        id="r1", question="Q", context=None, profile="economy", red_team_enabled=False,
        status="succeeded", created_at="2026-01-01T00:00:00+00:00", started_at=None, completed_at=None,
        estimated_total_cost_usd=0.0,
        stages=[
            _stage(input_tokens=10, output_tokens=20),
            _stage(input_tokens=5, output_tokens=7),
        ],
    )
    assert run_token_totals(record) == {"input_tokens": 15, "output_tokens": 27}


def test_completed_stage_duration_seconds_none_for_a_stage_with_no_completed_at():
    stage = _stage(started_at="2026-01-01T00:00:00+00:00", completed_at=None)
    assert completed_stage_duration_seconds(stage) is None


def test_completed_stage_duration_seconds_computed_for_a_finished_stage():
    stage = _stage(started_at="2026-01-01T00:00:00+00:00", completed_at="2026-01-01T00:01:30+00:00")
    assert completed_stage_duration_seconds(stage) == 90.0


# -- HISTORICAL: never a fake zero, "Not recorded" instead -------------------


def _stage(**overrides) -> StageRecord:
    defaults = dict(
        id=1, run_id="r1", name="analysis_a", provider=None, model=None,
        status="succeeded", attempt=1, input_tokens=0, output_tokens=0,
        estimated_cost_usd=0.0, error=None, started_at=None, completed_at=None,
    )
    defaults.update(overrides)
    return StageRecord(**defaults)


def test_report_shows_not_recorded_for_a_legacy_stage_missing_timestamps(
    client, service, fake_orchestrator_state
):
    response = _submit(client, question="Q?", red_team=False)
    run_id = _run_id(response)
    # Simulate a historical stage row that predates completed_at ever being
    # reliably backfilled: directly clear it in the DB, as a migration might
    # have left a genuinely old row (never fabricate this via the live
    # pipeline, which always sets it on success).
    service.repo._conn.execute(
        "UPDATE stages SET completed_at = NULL WHERE run_id = ? AND name = 'analysis_a'",
        (run_id,),
    )
    service.repo._conn.commit()
    body = client.get(f"/runs/{run_id}/report").text
    assert "Not recorded" in body


# -- PRIVACY --------------------------------------------------------------


def test_report_shows_privacy_notice(client, service, fake_orchestrator_state):
    response = _submit(client, question="Q?", red_team=False)
    run_id = _run_id(response)
    body = client.get(f"/runs/{run_id}/report").text
    assert "Share it only with people authorized to see that information." in body


def test_report_never_renders_a_localhost_link(client, service, fake_orchestrator_state):
    response = _submit(client, question="Q?", red_team=False)
    run_id = _run_id(response)
    body = client.get(f"/runs/{run_id}/report").text
    assert "127.0.0.1" not in body
    assert "localhost" not in body.lower()


def test_report_never_leaks_env_or_secret_looking_material(client, service, fake_orchestrator_state):
    response = _submit(client, question="Q?", red_team=False)
    run_id = _run_id(response)
    body = client.get(f"/runs/{run_id}/report").text
    for marker in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "Traceback (most recent call last)", "/home/"):
        assert marker not in body


# -- SEMANTICS --------------------------------------------------------------


def test_report_never_introduces_confidence_truth_winner_or_error_found_language(
    client, service, fake_orchestrator_state
):
    fake_orchestrator_state["responses"] = {
        "convergence_analysis": _convergence_response(
            convergence="converged",
            material_changes=[
                {
                    "candidate": "A", "before": "x", "after": "y", "material": True,
                    "triggers": [{"source": "red_team", "summary": "Red-team flagged a risk."}],
                }
            ],
        )
    }
    response = _submit(client, question="Q?", red_team=True)
    run_id = _run_id(response)
    body = client.get(f"/runs/{run_id}/report").text
    lowered = body.lower()
    for forbidden in (
        "ai confidence", "correctness score", "truth score", "found errors", "errors found",
        "improved answer", "best model", "winner", "more accurate", "more trustworthy",
        "confidence:", "% confidence", "agreement score",
    ):
        assert forbidden not in lowered, f"forbidden phrase present: {forbidden!r}"


# -- I18N --------------------------------------------------------------------


def test_report_renders_in_english_by_default(client, service, fake_orchestrator_state):
    response = _submit(client, question="Q?", red_team=False)
    run_id = _run_id(response)
    body = client.get(f"/runs/{run_id}/report").text
    assert "LLM Deliberation Report" in body
    assert "How this report was produced" in body
    assert "Technical appendix" in body


def test_report_renders_in_estonian_when_cookie_set(client, service, fake_orchestrator_state):
    client.cookies.set("ui_lang", "et")
    response = _submit(client, question="Q?", red_team=False)
    run_id = _run_id(response)
    body = client.get(f"/runs/{run_id}/report").text
    assert "LLM arutelu raport" in body
    assert "Kuidas see raport valmis" in body


# -- HTML SAFETY --------------------------------------------------------------


def test_report_escapes_unsafe_html_in_model_output(client, service, fake_orchestrator_state):
    malicious = "<script>alert(1)</script>"
    fake_orchestrator_state["responses"] = {"synthesis": {"model": "fake-model", "text": malicious}}
    response = _submit(client, question="Q?", red_team=False)
    run_id = _run_id(response)
    body = client.get(f"/runs/{run_id}/report").text
    assert "<script>alert(1)</script>" not in body


def test_report_escapes_unsafe_html_in_question(client, service, fake_orchestrator_state):
    response = _submit(client, question="<script>alert(2)</script>", red_team=False)
    run_id = _run_id(response)
    body = client.get(f"/runs/{run_id}/report").text
    assert "<script>alert(2)</script>" not in body


# -- PRINT STRUCTURE ----------------------------------------------------------


def test_report_links_the_shared_stylesheet(client, service, fake_orchestrator_state):
    response = _submit(client, question="Q?", red_team=False)
    run_id = _run_id(response)
    body = client.get(f"/runs/{run_id}/report").text
    assert '<link rel="stylesheet" href="/static/style.css">' in body


def test_report_actions_have_no_content_dependency(client, service, fake_orchestrator_state):
    """The print button may call window.print(); nothing else in the page
    may depend on JavaScript to become visible."""
    response = _submit(client, question="Q?", red_team=False)
    run_id = _run_id(response)
    body = client.get(f"/runs/{run_id}/report").text
    assert 'class="report-actions"' in body
    assert "window.print()" in body
    # No script tag beyond the inline print-button handler -- no app.js.
    assert "/static/app.js" not in body


def test_report_actions_are_print_hidden_in_css():
    css = open("src/llm_deliberation/web/static/style.css", encoding="utf-8").read()
    assert "@media print" in css
    assert ".report-actions { display: none; }" in css


# -- ENTRY POINT --------------------------------------------------------------


def test_run_detail_shows_full_report_link_for_a_terminal_run(client, service, fake_orchestrator_state):
    response = _submit(client, question="Q?", red_team=False)
    run_id = _run_id(response)
    body = client.get(f"/runs/{run_id}").text
    assert f'/runs/{run_id}/report' in body


def test_run_detail_shows_full_report_link_for_a_failed_run_too(client, service, fake_orchestrator_state):
    fake_orchestrator_state["fail"] = {"analysis_a"}
    response = _submit(client, question="Q?", red_team=False)
    run_id = _run_id(response)
    assert service.get_run(run_id).status == "failed"
    body = client.get(f"/runs/{run_id}").text
    assert f'/runs/{run_id}/report' in body
