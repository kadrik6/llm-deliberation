from __future__ import annotations

import json


def _submit(client, *, question="What should we do?", profile="economy", red_team=False, context=""):
    data = {"question": question, "profile": profile, "context": context}
    if red_team:
        data["red_team"] = "1"
    return client.post("/runs", data=data)


def test_home_page_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "Deliberate" in body
    assert "Economy" in body and "Balanced" in body and "Max" in body
    assert "Independent red-team" in body


def test_submitting_a_run_calls_the_service_correctly(client, service):
    response = _submit(client, question="Ship on Friday?", profile="economy", red_team=False, context="Q3 deadline")
    assert response.status_code == 200
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    record = service.get_run(run_id)
    assert record.question == "Ship on Friday?"
    assert record.context == "Q3 deadline"
    assert record.profile == "economy"
    assert record.red_team_enabled is False
    assert record.status == "succeeded"  # FakeOrchestrator resolves instantly


def test_run_detail_renders_persisted_stages_and_artifacts(client):
    response = _submit(client, question="Q?", red_team=True)
    body = response.text
    assert "Final answer" in body
    assert "synthesis-output" in body
    assert "Independent analysis A" in body
    assert "Independent red-team" in body
    assert "analysis_a-output" in body  # inside the expandable <details> section


def test_red_team_disabled_is_represented_correctly(client, service, fake_orchestrator_state):
    # Force a failure so the run stays in the non-succeeded pipeline view,
    # where the red-team lane (or its absence) is actually rendered.
    fake_orchestrator_state["fail"] = {"analysis_a"}
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    record = service.get_run(run_id)
    assert all(s.name != "red_team" for s in record.stages)

    detail = client.get(f"/runs/{run_id}")
    assert "disabled" in detail.text
    assert "Gemini" in detail.text


def test_failed_stage_renders_without_losing_completed_artifacts(client, service, fake_orchestrator_state):
    fake_orchestrator_state["fail"] = {"revision_a"}
    response = _submit(client, question="Q?", red_team=True)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    record = service.get_run(run_id)
    assert record.status == "failed"
    by_name = {s.name: s for s in record.stages}
    assert by_name["analysis_a"].status == "succeeded"
    assert by_name["analysis_a"].text == "analysis_a-output"
    assert by_name["revision_a"].status == "failed"

    detail = client.get(f"/runs/{run_id}")
    assert detail.status_code == 200
    assert "Status: failed" in detail.text
    assert "Retry failed stage" in detail.text
    assert "simulated failure" in detail.text

    # Completed stages must still be intact after rendering the failure.
    record_after = service.get_run(run_id)
    after_by_name = {s.name: s for s in record_after.stages}
    assert after_by_name["analysis_a"].status == "succeeded"
    assert after_by_name["analysis_a"].text == "analysis_a-output"


def test_retry_endpoint_calls_the_correct_service_operation(client, service, fake_orchestrator_state):
    fake_orchestrator_state["fail"] = {"revision_a"}
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    assert service.get_run(run_id).status == "failed"

    fake_orchestrator_state["log"].clear()
    fake_orchestrator_state["fail"] = set()

    retry_response = client.post(f"/runs/{run_id}/stages/revision_a/retry")
    assert retry_response.status_code == 200

    record = service.get_run(run_id)
    assert record.status == "succeeded"
    # Only the retried stage and the downstream stages it unblocked ran --
    # not analysis/critique/revision_b, which had already succeeded.
    assert set(fake_orchestrator_state["log"]) == {
        "revision_a",
        "convergence_analysis",
        "synthesis",
    }


def test_history_lists_persisted_runs(client):
    _submit(client, question="First question")
    _submit(client, question="Second question")

    response = client.get("/history")
    assert response.status_code == 200
    assert "First question" in response.text
    assert "Second question" in response.text


def test_export_returns_the_stored_run_as_markdown(client):
    response = _submit(client, question="Export me", red_team=True)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    export = client.get(f"/runs/{run_id}/export")
    assert export.status_code == 200
    assert "markdown" in export.headers["content-type"]
    assert "# LLM Deliberation Report" in export.text
    assert "synthesis-output" in export.text
    assert "Final synthesis" in export.text


def test_export_of_unfinished_run_returns_conflict(client, service, fake_orchestrator_state):
    fake_orchestrator_state["fail"] = {"analysis_a"}
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    export = client.get(f"/runs/{run_id}/export")
    assert export.status_code == 409


def test_unknown_run_returns_404(client):
    response = client.get("/runs/does-not-exist")
    assert response.status_code == 404


# -- Gemini fallback UI: recoverable failure, retry modes, skip --------------


def test_failed_red_team_stage_shows_recoverable_actions_and_fallback_provenance(
    client, service, fake_orchestrator_state
):
    from llm_deliberation.providers import ProviderGenerationError

    fake_orchestrator_state["fail_with"] = {
        "red_team": ProviderGenerationError(
            "All configured Gemini models failed transiently: gemini-3.8-flash, gemini-3.7-flash.",
            requested_model="gemini-3.8-flash",
            attempts=8,
            attempt_log=[],
            fallback_used=True,
            fallback_reason="HTTP 503 from Gemini (model is overloaded)",
            estimated_cost_usd=0.0,
        )
    }
    response = _submit(client, question="Q?", red_team=True)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    assert "Retry preferred model" in body
    assert "Retry with fallback chain" in body
    assert "Skip red-team and continue" in body
    assert "gemini-3.8-flash" in body
    assert "HTTP 503 from Gemini" in body
    # The only failed stage is red_team, so the generic single-button retry
    # (used for non-red-team failures) must not appear at all here -- it's
    # replaced by the three red-team-specific actions.
    assert "Retry failed stage" not in body
    # Attempt count must be explicit that it only covers the most recent
    # try, not a cumulative total across separate user-triggered retries.
    assert "Attempts (this try): 8" in body


def test_successful_fallback_is_disclosed_not_presented_as_an_error(
    client, service, fake_orchestrator_state
):
    fake_orchestrator_state["responses"] = {
        "red_team": {
            "provider": "Google",
            "model": "gemini-3.7-flash",
            "requested_model": "gemini-3.8-flash",
            "fallback_used": True,
            "fallback_reason": "HTTP 503 from Gemini (model is overloaded)",
            "model_attempts": 5,
        }
    }
    response = _submit(client, question="Q?", red_team=True)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    assert "Requested" in body
    assert "gemini-3.8-flash" in body
    assert "gemini-3.7-flash" in body
    assert "Fallback reason" in body
    assert "HTTP 503 from Gemini" in body
    assert "Attempts (this try): 5" in body
    # Not rendered as a failure: the run and stage both succeeded.
    assert "Status: failed" not in body


def test_skip_endpoint_lets_the_pipeline_continue(client, service, fake_orchestrator_state):
    fake_orchestrator_state["fail"] = {"red_team"}
    response = _submit(client, question="Q?", red_team=True)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    assert service.get_run(run_id).status == "failed"

    fake_orchestrator_state["log"].clear()
    skip_response = client.post(f"/runs/{run_id}/stages/red_team/skip")
    assert skip_response.status_code == 200

    record = service.get_run(run_id)
    assert record.status == "succeeded"
    by_name = {s.name: s for s in record.stages}
    assert by_name["red_team"].status == "skipped"
    assert "red_team" not in fake_orchestrator_state["log"]


def test_retry_preferred_model_mode_is_rejected_for_non_red_team_stage(
    client, service, fake_orchestrator_state
):
    fake_orchestrator_state["fail"] = {"analysis_a"}
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    retry_response = client.post(
        f"/runs/{run_id}/stages/analysis_a/retry", data={"mode": "preferred_only"}
    )
    assert retry_response.status_code == 400


def test_running_stage_shows_elapsed_time_derived_from_started_at(client, service):
    import re
    from datetime import datetime, timedelta, timezone

    run_id = service.create_run("Q?", "economy", red_team_enabled=False)
    stage = service.repo.get_stage(run_id, "analysis_a")
    service.repo.mark_stage_running(stage.id)
    # Backdate started_at (test-only manipulation) so the elapsed value is
    # deterministic instead of ~0s, without adding any new persistence.
    backdated = (datetime.now(timezone.utc) - timedelta(seconds=18)).isoformat()
    service.repo._conn.execute(
        "UPDATE stages SET started_at = ? WHERE id = ?", (backdated, stage.id)
    )
    service.repo._conn.commit()
    service.repo.update_run(run_id, status="running", set_started_if_unset=True)

    running_note = re.compile(r"Running · (\d+m )?\d+s")

    detail = client.get(f"/runs/{run_id}")
    assert detail.status_code == 200
    assert running_note.search(detail.text)

    # The same data source SSE polls every second.
    fragment = client.get(f"/runs/{run_id}/status")
    assert running_note.search(fragment.text)

    # Only the one running stage shows it -- every other (pending) stage row
    # must not.
    assert len(running_note.findall(fragment.text)) == 1


def test_export_markdown_discloses_fallback(client, service, fake_orchestrator_state):
    fake_orchestrator_state["responses"] = {
        "red_team": {
            "provider": "Google",
            "model": "gemini-3.7-flash",
            "requested_model": "gemini-3.8-flash",
            "fallback_used": True,
            "fallback_reason": "HTTP 503 from Gemini (model is overloaded)",
            "model_attempts": 5,
        }
    }
    response = _submit(client, question="Q?", red_team=True)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    export = client.get(f"/runs/{run_id}/export")
    assert export.status_code == 200
    assert "Requested model: `gemini-3.8-flash`" in export.text
    assert "Fallback reason: HTTP 503 from Gemini (model is overloaded)" in export.text
    assert "Attempts (this try): 5" in export.text


# -- Markdown rendering in the browser (storage/export stay raw) ------------

_MARKDOWN_TEXT = (
    "## Conclusion\n\n"
    "This is the **recommended** approach, with some *caveats*.\n\n"
    "- first point\n"
    "- second point\n"
)


def test_stored_artifact_stays_raw_markdown_in_sqlite(service, fake_orchestrator_state):
    fake_orchestrator_state["responses"] = {"synthesis": {"model": "fake-model", "text": _MARKDOWN_TEXT}}
    run_id = service.create_run("Q?", "economy", red_team_enabled=False)
    import asyncio

    record = asyncio.run(service.start_run(run_id))
    assert record.status == "succeeded"

    stage = service.repo.get_stage(run_id, "synthesis")
    assert stage.text == _MARKDOWN_TEXT  # byte-identical raw Markdown, not HTML

    # A second, independent read from the repository confirms nothing about
    # the stored row was mutated by having been displayed in the browser.
    reread = service.repo.get_stage(run_id, "synthesis")
    assert reread.text == _MARKDOWN_TEXT


def test_markdown_export_still_contains_raw_markdown_syntax(client, service, fake_orchestrator_state):
    fake_orchestrator_state["responses"] = {"synthesis": {"model": "fake-model", "text": _MARKDOWN_TEXT}}
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    export = client.get(f"/runs/{run_id}/export")
    assert export.status_code == 200
    assert _MARKDOWN_TEXT in export.text  # raw syntax, unrendered
    assert "<h2>" not in export.text
    assert "<strong>" not in export.text


def test_browser_view_renders_markdown_as_html(client, service, fake_orchestrator_state):
    fake_orchestrator_state["responses"] = {"synthesis": {"model": "fake-model", "text": _MARKDOWN_TEXT}}
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    assert "<h2>Conclusion</h2>" in body
    assert "<strong>recommended</strong>" in body
    assert "<em>caveats</em>" in body
    assert "<li>first point</li>" in body
    # The visible rendered-answer div specifically must not contain raw
    # Markdown syntax (the hidden copy-source <pre> legitimately does --
    # that is checked separately in test_copy_final_answer_source_still_
    # holds_raw_markdown).
    import re

    rendered_answer = re.search(
        r'<div class="answer-text markdown-body">(.*?)</div>', body, re.S
    ).group(1)
    assert "## Conclusion" not in rendered_answer
    assert "**recommended**" not in rendered_answer


def test_unsafe_html_in_model_output_cannot_execute(client, service, fake_orchestrator_state):
    import re

    malicious = "Findings: <script>alert('xss')</script> and <img src=x onerror=\"alert(1)\">."
    fake_orchestrator_state["responses"] = {"synthesis": {"model": "fake-model", "text": malicious}}
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    detail = client.get(f"/runs/{run_id}")
    body = detail.text

    # The page legitimately has exactly one <script> tag, for the static
    # app.js asset -- none may originate from model output.
    assert body.lower().count("<script") == 1
    assert '<script src="/static/app.js">' in body

    # The *rendered* answer (what actually becomes live markup in the DOM)
    # must contain neither the script nor the <img onerror=...> tag.
    rendered_answer = re.search(
        r'<div class="answer-text markdown-body">(.*?)</div>', body, re.S
    ).group(1)
    assert "<script" not in rendered_answer.lower()
    assert "<img" not in rendered_answer.lower()
    assert "onerror" not in rendered_answer.lower()
    # nh3 removes a stripped <script>'s contents too, not just the tag.
    assert "alert('xss')" not in rendered_answer

    # The hidden copy-source <pre> legitimately still contains the original
    # text -- but Jinja's autoescaping renders it as inert HTML-entity text
    # (e.g. &lt;script&gt;), never as a live tag.
    assert "&lt;script&gt;" in body
    assert "<script>alert" not in body  # never an unescaped, live tag

    # Storage and export are untouched by the sanitizer -- the raw payload
    # is still there as plain text data, just never rendered as markup.
    stage = service.get_run(run_id)
    synthesis_stage = next(s for s in stage.stages if s.name == "synthesis")
    assert synthesis_stage.text == malicious
    export = client.get(f"/runs/{run_id}/export")
    assert malicious in export.text


def test_copy_final_answer_source_still_holds_raw_markdown(client, service, fake_orchestrator_state):
    fake_orchestrator_state["responses"] = {"synthesis": {"model": "fake-model", "text": _MARKDOWN_TEXT}}
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    assert '<pre id="final-answer-text" hidden>' in body
    # The exact raw Markdown text (the JS copy handler reads .textContent
    # from this element) must be present, unrendered, inside it.
    assert f'<pre id="final-answer-text" hidden>{_MARKDOWN_TEXT}</pre>' in body


# -- Decision evolution (convergence_analysis) in the web UI -----------------


def _convergence_response(**overrides) -> dict:
    payload = {
        "convergence": "converged",
        "material_changes": [],
        "agreements_reached": [],
        "unresolved_disagreements": [],
        "remaining_unknowns": [],
        "human_judgement_required": [],
    }
    payload.update(overrides)
    return {"text": json.dumps(payload)}


def test_ui_renders_what_changed_correctly(client, service, fake_orchestrator_state):
    fake_orchestrator_state["responses"] = {
        "convergence_analysis": _convergence_response(
            convergence="partial",
            material_changes=[
                {
                    "candidate": "A",
                    "before": "Vendor-hosted deployment preferred.",
                    "after": "Customer-controlled deployment preferred.",
                    "material": True,
                    "triggers": [
                        {
                            "source": "peer_critique",
                            "stage": "critique_b_of_a",
                            "summary": "B raised a data custody risk.",
                        }
                    ],
                },
                {
                    "candidate": "B",
                    "before": "Safeguard-based approach.",
                    "after": "Safeguard-based approach.",
                    "material": False,
                    "triggers": [],
                },
            ],
        )
    }
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    assert "Decision evolution" in body
    assert "Convergence: Partial" in body
    assert "Candidate A" in body
    assert "Vendor-hosted deployment preferred." in body
    assert "Customer-controlled deployment preferred." in body
    assert "B raised a data custody risk." in body
    assert "Material change:</strong> Yes" in body
    assert "Material change:</strong> No" in body


def test_ui_renders_unresolved_disagreements_correctly(client, service, fake_orchestrator_state):
    fake_orchestrator_state["responses"] = {
        "convergence_analysis": _convergence_response(
            convergence="partial",
            unresolved_disagreements=[
                {
                    "topic": "Data custody",
                    "candidate_a_position": "Customer-controlled deployment preferred.",
                    "candidate_b_position": "Vendor-hosted processing acceptable with safeguards.",
                    "why_unresolved": "Depends on regulatory posture not stated in the question.",
                    "decision_impact": "Affects contract structure and cost.",
                }
            ],
        )
    }
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    assert "Where they still disagree" in body
    assert "Data custody" in body
    assert "Customer-controlled deployment preferred." in body
    assert "Vendor-hosted processing acceptable with safeguards." in body
    assert "Depends on regulatory posture" in body
    assert "Affects contract structure and cost." in body


def test_ui_renders_agreements_unknowns_and_human_judgement(client, service, fake_orchestrator_state):
    fake_orchestrator_state["responses"] = {
        "convergence_analysis": _convergence_response(
            agreements_reached=[{"topic": "Rollout pace", "shared_position": "Phased rollout."}],
            remaining_unknowns=[
                {
                    "unknown": "Data residency law",
                    "why_it_matters": "Determines legal custody model.",
                    "evidence_needed": "Jurisdiction analysis.",
                }
            ],
            human_judgement_required=[
                {
                    "issue": "Risk tolerance for vendor lock-in",
                    "why_models_cannot_resolve_it": "A values tradeoff.",
                }
            ],
        )
    }
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    assert "Agreements reached" in body
    assert "Rollout pace" in body
    assert "Remaining unknowns" in body
    assert "Data residency law" in body
    assert "Human judgement required" in body
    assert "Risk tolerance for vendor lock-in" in body


def test_ui_shows_no_material_changes_and_no_disagreements_explicitly(
    client, service, fake_orchestrator_state
):
    fake_orchestrator_state["responses"] = {"convergence_analysis": _convergence_response()}
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    assert "Convergence: Converged" in body
    assert "No material position changes were identified." in body
    assert "No unresolved disagreements were identified." in body


def test_failed_convergence_analysis_offers_retry_and_skip(client, service, fake_orchestrator_state):
    fake_orchestrator_state["fail"] = {"convergence_analysis"}
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    assert "Retry failed stage" in body
    assert "Skip convergence analysis and continue" in body
    # convergence_analysis is not a Gemini-fallback stage -- it must not get
    # the red_team-specific three-button UI.
    assert "Retry preferred model" not in body
    assert "Retry with fallback chain" not in body


def test_skip_convergence_analysis_endpoint_marks_result_unavailable(
    client, service, fake_orchestrator_state
):
    fake_orchestrator_state["fail"] = {"convergence_analysis"}
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    assert service.get_run(run_id).status == "failed"

    fake_orchestrator_state["log"].clear()
    skip_response = client.post(f"/runs/{run_id}/stages/convergence_analysis/skip")
    assert skip_response.status_code == 200

    record = service.get_run(run_id)
    assert record.status == "succeeded"
    by_name = {s.name: s for s in record.stages}
    assert by_name["convergence_analysis"].status == "skipped"

    detail = client.get(f"/runs/{run_id}")
    assert "Change/convergence analysis unavailable for this run." in detail.text


def test_export_markdown_includes_decision_evolution_section(
    client, service, fake_orchestrator_state
):
    fake_orchestrator_state["responses"] = {
        "convergence_analysis": _convergence_response(
            convergence="diverged",
            unresolved_disagreements=[
                {
                    "topic": "Approach",
                    "candidate_a_position": "A's stance",
                    "candidate_b_position": "B's stance",
                    "why_unresolved": "fundamental values difference",
                    "decision_impact": "changes the recommendation entirely",
                }
            ],
        )
    }
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    export = client.get(f"/runs/{run_id}/export")
    assert export.status_code == 200
    assert "## Decision evolution" in export.text
    assert "### Convergence" in export.text
    assert "diverged" in export.text
    assert "### Unresolved disagreements" in export.text
    assert "Approach" in export.text
    assert "fundamental values difference" in export.text
    # The raw structured artifact is also present as its own section.
    assert "## Convergence analysis (raw)" in export.text


def test_export_markdown_states_unavailable_when_convergence_skipped(
    client, service, fake_orchestrator_state
):
    fake_orchestrator_state["fail"] = {"convergence_analysis"}
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    stage_id = next(
        s.id for s in service.get_run(run_id).stages if s.name == "convergence_analysis"
    )
    import asyncio

    asyncio.run(service.skip_stage(run_id, stage_id))

    export = client.get(f"/runs/{run_id}/export")
    assert export.status_code == 200
    assert "Change/convergence analysis unavailable for this run." in export.text


def test_historical_run_missing_convergence_analysis_opens_correctly_everywhere(
    client, service, fake_orchestrator_state
):
    """Compatibility check: a run created before this stage existed (no
    convergence_analysis row at all, not skipped/failed) must not crash the
    run detail page, the Markdown export, or the history list, and must
    show the neutral "unavailable" message rather than implying anything
    about convergence."""
    response = _submit(client, question="Q?", red_team=True)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    assert service.get_run(run_id).status == "succeeded"

    conv_stage = service.repo.get_stage(run_id, "convergence_analysis")
    service.repo._conn.execute("DELETE FROM artifacts WHERE stage_id = ?", (conv_stage.id,))
    service.repo._conn.execute("DELETE FROM stages WHERE id = ?", (conv_stage.id,))
    service.repo._conn.commit()

    detail = client.get(f"/runs/{run_id}")
    assert detail.status_code == 200
    assert "Change/convergence analysis unavailable for this run." in detail.text
    assert "synthesis-output" in detail.text  # rest of the run still renders

    export = client.get(f"/runs/{run_id}/export")
    assert export.status_code == 200
    assert "Change/convergence analysis unavailable for this run." in export.text

    history = client.get("/history")
    assert history.status_code == 200


def test_historical_run_missing_convergence_analysis_shows_disabled_row_when_not_succeeded(
    client, service, fake_orchestrator_state
):
    """Same historical-run scenario, but for a run still in the (non-
    succeeded) pipeline view -- the stage must render as a disabled row,
    the same treatment already used for a disabled red_team, not crash."""
    fake_orchestrator_state["fail"] = {"revision_a"}
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    assert service.get_run(run_id).status == "failed"

    conv_stage = service.repo.get_stage(run_id, "convergence_analysis")
    service.repo._conn.execute("DELETE FROM artifacts WHERE stage_id = ?", (conv_stage.id,))
    service.repo._conn.execute("DELETE FROM stages WHERE id = ?", (conv_stage.id,))
    service.repo._conn.commit()

    detail = client.get(f"/runs/{run_id}")
    assert detail.status_code == 200
    assert "Retry failed stage" in detail.text  # revision_a's own failure UI
