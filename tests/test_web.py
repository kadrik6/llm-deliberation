from __future__ import annotations

import json
import re
from pathlib import Path


def _submit(
    client, *, question="What should we do?", profile="economy", red_team=False,
    context="", language="en",
):
    data = {"question": question, "profile": profile, "context": context, "language": language}
    if red_team:
        data["red_team"] = "1"
    return client.post("/runs", data=data)


def test_home_page_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "Start deliberation" in body
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


def test_full_deliberation_trace_contains_every_artifact_and_stays_secondary(client):
    """Point 8/9/12: the full trace (all raw artifacts + provider metadata)
    must remain present and inspectable, but visually/structurally after
    the analytical overview, under its own heading."""
    response = _submit(client, question="Q?", red_team=True)
    body = response.text

    trace_heading_pos = body.index("Full deliberation trace")
    assert '<section class="artifacts">' in body
    # The trace heading comes after the analytical overview sections.
    assert body.index("What changed?") < trace_heading_pos
    assert body.index("You decide") < trace_heading_pos

    for label in (
        "Independent analysis A",
        "Independent analysis B",
        "Cross-critique A → B",
        "Cross-critique B → A",
        "Independent red-team",
        "Revised candidate A",
        "Revised candidate B",
        "Convergence analysis (raw)",
    ):
        assert label in body
    # Provider/model/cost metadata for at least one artifact is present.
    assert "Fake" in body and "fake-model" in body

    # The normal user can understand the result without opening any of
    # these -- they are all inside collapsed <details>, not expanded by
    # default (no "open" attribute).
    trace_section = body[body.index('<section class="artifacts">'):]
    assert "<details open" not in trace_section


def test_raw_convergence_json_is_not_used_as_the_main_summary(client, service, fake_orchestrator_state):
    fake_orchestrator_state["responses"] = {
        "convergence_analysis": _convergence_response(
            convergence="partial",
            unresolved_disagreements=[
                {
                    "topic": "Data custody",
                    "candidate_a_position": "a",
                    "candidate_b_position": "b",
                    "why_unresolved": "w",
                    "decision_impact": "d",
                }
            ],
        )
    }
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    # The raw JSON field name appears exactly once -- inside the collapsed
    # raw artifact in the full trace -- never inside the human-readable
    # dashboard sections built from the parsed object (those use the
    # rendered label "Where they still disagree" instead).
    raw_json_marker = "unresolved_disagreements"
    assert body.count(raw_json_marker) == 1
    raw_pos = body.index(raw_json_marker)
    trace_pos = body.index('<section class="artifacts">')
    assert raw_pos > trace_pos  # only inside the full trace, not above it


def test_result_page_markup_has_no_duplicate_ids(client, service, fake_orchestrator_state):
    fake_orchestrator_state["responses"] = {
        "convergence_analysis": _convergence_response(
            convergence="partial",
            material_changes=[
                {"candidate": "A", "before": "x", "after": "y", "material": True, "triggers": []}
            ],
            unresolved_disagreements=[
                {
                    "topic": "t",
                    "candidate_a_position": "a",
                    "candidate_b_position": "b",
                    "why_unresolved": "w",
                    "decision_impact": "d",
                }
            ],
        )
    }
    response = _submit(client, question="Q?", red_team=True)
    body = response.text

    ids = re.findall(r'\bid="([^"]+)"', body)
    assert len(ids) == len(set(ids)), f"duplicate id attributes found: {ids}"


def test_no_new_stage_is_invoked_for_the_dashboard(service, fake_orchestrator_state):
    """The dashboard is presentation-only: it must not add a new LLM stage
    or change which stages run."""
    import asyncio

    from llm_deliberation.orchestrator import ALL_STAGE_NAMES

    run_id = service.create_run("Q?", "economy", red_team_enabled=True)
    record = asyncio.run(service.start_run(run_id))

    assert record.status == "succeeded"
    assert set(fake_orchestrator_state["log"]) == set(ALL_STAGE_NAMES)
    assert len(fake_orchestrator_state["log"]) == len(ALL_STAGE_NAMES)


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


_SECTIONED_SYNTHESIS = (
    "## Final conclusion\n\nShip the phased rollout.\n\n"
    "## Why this is the strongest answer\n\nBoth analyses converged on it.\n\n"
    "## Strongest argument against it\n\nRegulatory risk remains.\n\n"
    "## Remaining uncertainty\n\nData residency law is unclear.\n\n"
    "## What would change the recommendation\n\nNew regulatory guidance.\n"
)


def test_structured_synthesis_renders_prominent_headline_and_collapsible_rest(
    client, service, fake_orchestrator_state
):
    fake_orchestrator_state["responses"] = {
        "synthesis": {"model": "fake-model", "text": _SECTIONED_SYNTHESIS}
    }
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    assert '<p class="answer-lead-label">Final conclusion</p>' in body
    assert "Ship the phased rollout." in body
    # The remaining sections are present but tucked into collapsible
    # <details>, each labeled with the model's own heading text.
    for heading in (
        "Why this is the strongest answer",
        "Strongest argument against it",
        "Remaining uncertainty",
        "What would change the recommendation",
    ):
        assert f"<summary>{heading}</summary>" in body
    # The prominent headline itself is not also duplicated as a <details>.
    assert "<summary>Final conclusion</summary>" not in body
    # Full raw text is still preserved verbatim for copying.
    assert _SECTIONED_SYNTHESIS in body


def test_unstructured_synthesis_falls_back_to_full_block_unsectioned(client, service):
    # Default FakeOrchestrator text ("synthesis-output") has no headings --
    # confirms the safe fallback path (today's behavior) is unchanged.
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    assert "synthesis-output" in body
    assert "answer-lead-label" not in body
    assert "answer-details" not in body


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
    assert "What changed?" in body
    assert "Partial convergence" in body
    assert "Candidate A" in body
    assert "Vendor-hosted deployment preferred." in body
    assert "Customer-controlled deployment preferred." in body
    assert "B raised a data custody risk." in body
    assert "Material change:</strong> Yes" in body
    assert "Material change:</strong> No" in body
    # Material vs non-material changes are visually distinguishable in markup
    # (not color-only -- the Yes/No text above is the accessible signal).
    assert 'class="change-card is-material"' in body
    assert 'class="change-card is-non-material"' in body


def test_ui_groups_multiple_changes_for_the_same_candidate(client, service, fake_orchestrator_state):
    fake_orchestrator_state["responses"] = {
        "convergence_analysis": _convergence_response(
            convergence="partial",
            material_changes=[
                {
                    "candidate": "A",
                    "before": "Prefers X.",
                    "after": "Prefers Y.",
                    "material": True,
                    "triggers": [],
                },
                {
                    "candidate": "A",
                    "before": "Timeline: 3 months.",
                    "after": "Timeline: 6 months.",
                    "material": True,
                    "triggers": [],
                },
            ],
        )
    }
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    # "Candidate A" is grouped once as a heading, not repeated per change.
    assert body.count("Candidate A") == 1
    assert "Prefers Y." in body
    assert "Timeline: 6 months." in body


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
    assert "Candidate A" in body and "Candidate B" in body
    assert "Customer-controlled deployment preferred." in body
    assert "Vendor-hosted processing acceptable with safeguards." in body
    assert "Depends on regulatory posture" in body
    assert "Affects contract structure and cost." in body
    # Both positions render inside the responsive comparison grid, not a
    # winner/loser framing.
    assert '<div class="disagreement-positions">' in body


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
    assert "What should you find out next?" in body
    assert "Data residency law" in body
    assert "Determines legal custody model." in body  # why_it_matters
    assert "Jurisdiction analysis." in body  # evidence_needed
    assert "You decide" in body
    assert "Risk tolerance for vendor lock-in" in body
    assert "A values tradeoff." in body


def test_ui_shows_no_material_changes_and_no_disagreements_explicitly(
    client, service, fake_orchestrator_state
):
    fake_orchestrator_state["responses"] = {"convergence_analysis": _convergence_response()}
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    assert "Full convergence" in body
    assert "No material position changes were identified." in body
    assert "No material disagreements remain." in body
    assert "No shared agreements were identified." in body
    assert "No outstanding unknowns were identified." in body
    assert "No issues were flagged as requiring human judgement." in body


def test_decision_snapshot_shows_correct_counts_for_partial_convergence(
    client, service, fake_orchestrator_state
):
    fake_orchestrator_state["responses"] = {
        "convergence_analysis": _convergence_response(
            convergence="partial",
            material_changes=[
                {"candidate": "A", "before": "x", "after": "y", "material": True, "triggers": []},
                {"candidate": "B", "before": "x", "after": "x", "material": False, "triggers": []},
            ],
            unresolved_disagreements=[
                {
                    "topic": "t1",
                    "candidate_a_position": "a",
                    "candidate_b_position": "b",
                    "why_unresolved": "w",
                    "decision_impact": "d",
                }
            ]
            * 3,
            remaining_unknowns=[
                {"unknown": "u", "why_it_matters": "m", "evidence_needed": "e"}
            ]
            * 5,
            human_judgement_required=[
                {"issue": "i", "why_models_cannot_resolve_it": "r"}
            ]
            * 3,
        )
    }
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    assert "Partial convergence" in body
    # Only the ONE material:true change counts as a material change, not
    # both list entries.
    assert "<strong>1 material change</strong>" in body
    assert "<strong>3 unresolved disagreements</strong>" in body
    assert "<strong>5 missing facts</strong>" in body
    assert "<strong>3 human judgement items</strong>" in body


def test_decision_snapshot_full_convergence_shows_no_disagreement_warning(
    client, service, fake_orchestrator_state
):
    fake_orchestrator_state["responses"] = {
        "convergence_analysis": _convergence_response(convergence="converged")
    }
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    assert "Full convergence" in body
    assert "<strong>0 unresolved disagreements</strong>" in body
    assert "No material disagreements remain." in body


def test_decision_snapshot_diverged_renders_correctly(client, service, fake_orchestrator_state):
    fake_orchestrator_state["responses"] = {
        "convergence_analysis": _convergence_response(convergence="diverged")
    }
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    assert "Diverged" in body
    assert 'class="convergence-badge convergence-diverged"' in body


def test_decision_snapshot_insufficient_information_renders_correctly(
    client, service, fake_orchestrator_state
):
    fake_orchestrator_state["responses"] = {
        "convergence_analysis": _convergence_response(convergence="insufficient_information")
    }
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    assert "Insufficient information" in body
    assert 'class="convergence-badge convergence-insufficient-information"' in body


def test_decision_snapshot_degrades_gracefully_when_convergence_missing(
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

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    assert "Convergence analysis unavailable" in body
    assert "Change/convergence analysis unavailable for this run." in body
    # No fabricated counts, and none of the five analytical sections (which
    # would have nothing real to show) render at all.
    assert "material change" not in body
    assert "What changed?" not in body
    assert "Where they still disagree" not in body
    assert "You decide" not in body
    # The rest of the page (full trace, synthesis) is unaffected.
    assert "Full deliberation trace" in body


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


# -- bilingual support: UI language vs run/output language -----------------


def test_ui_language_switch_sets_cookie_and_redirects(client):
    response = client.get("/ui-language/et?next=/history", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/history"
    assert response.cookies.get("ui_lang") == "et"


def test_ui_language_switch_rejects_unsupported_language(client):
    response = client.get("/ui-language/fr")
    assert response.status_code == 404


def test_estonian_ui_renders_translated_primary_labels(client):
    client.cookies.set("ui_lang", "et")
    body = client.get("/").text
    assert "Uus arutelu" in body
    assert "Alusta arutelu" in body
    assert "Profiil" in body
    assert "Keel" in body


def test_english_ui_renders_english_labels_by_default(client):
    body = client.get("/").text
    assert "New deliberation" in body
    assert "Start deliberation" in body
    assert "Profile" in body


def test_ui_language_is_independent_of_run_output_language(
    client, service, fake_orchestrator_state
):
    """UI: Estonian, Question: English, Output language: English -- one of
    the four combinations that must be valid (see the brief's examples)."""
    client.cookies.set("ui_lang", "et")
    response = _submit(client, question="Should we build or buy?", language="en")
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    assert service.get_run(run_id).language == "en"
    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    # UI chrome is Estonian...
    assert "Lõppvastus" in body
    # ...independent of the run's own (English) output language.
    languages_used = dict(fake_orchestrator_state["languages"])
    assert languages_used["synthesis"] == "en"


def test_opening_english_run_under_estonian_ui_does_not_modify_artifact_content(
    client, service, fake_orchestrator_state
):
    fake_orchestrator_state["responses"] = {"synthesis": {"model": "fake-model", "text": "The English answer."}}
    response = _submit(client, question="Q?", language="en")
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    before = service.get_run(run_id)
    before_synthesis_text = next(s.text for s in before.stages if s.name == "synthesis")

    client.cookies.set("ui_lang", "et")
    detail = client.get(f"/runs/{run_id}")
    assert detail.status_code == 200
    assert "The English answer." in detail.text
    assert "Lõppvastus" in detail.text  # chrome translated

    after = service.get_run(run_id)
    after_synthesis_text = next(s.text for s in after.stages if s.name == "synthesis")
    assert after_synthesis_text == before_synthesis_text == "The English answer."


def test_opening_estonian_run_under_english_ui_does_not_modify_artifact_content(
    client, service, fake_orchestrator_state
):
    estonian_text = "See on eestikeelne vastus."
    fake_orchestrator_state["responses"] = {"synthesis": {"model": "fake-model", "text": estonian_text}}
    response = _submit(client, question="Q?", language="et")
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    before = service.get_run(run_id)
    before_synthesis_text = next(s.text for s in before.stages if s.name == "synthesis")

    # UI stays English (default, no cookie set).
    detail = client.get(f"/runs/{run_id}")
    assert detail.status_code == 200
    assert estonian_text in detail.text
    assert "Final answer" in detail.text  # chrome stays English

    after = service.get_run(run_id)
    after_synthesis_text = next(s.text for s in after.stages if s.name == "synthesis")
    assert after_synthesis_text == before_synthesis_text == estonian_text


def test_history_shows_run_language(client, service):
    _submit(client, question="English question", language="en")
    _submit(client, question="Eestikeelne küsimus", language="et")

    body = client.get("/history").text
    # Full translated words, not raw "en"/"et" codes -- see the bilingual
    # UI audit (Section 1: language enum display).
    assert "English" in body
    assert "Estonian" in body


def test_export_preserves_estonian_content_with_english_report_default(
    client, service, fake_orchestrator_state
):
    """Export headings follow the run's own language (point 8) -- for an
    Estonian run, the report.py structural headings switch to Estonian too,
    while the model-generated content is exactly what was persisted."""
    estonian_text = "Sisuline järeldus eesti keeles."
    fake_orchestrator_state["responses"] = {"synthesis": {"model": "fake-model", "text": estonian_text}}
    response = _submit(client, question="Küsimus?", language="et")
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    export = client.get(f"/runs/{run_id}/export")
    assert export.status_code == 200
    assert estonian_text in export.text  # model content unchanged
    assert "# LLM arutelu raport" in export.text  # report's own heading, Estonian
    assert "## Lõppsüntees" in export.text


def test_export_of_english_run_keeps_english_report_headings(
    client, service, fake_orchestrator_state
):
    response = _submit(client, question="Q?", language="en")
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    export = client.get(f"/runs/{run_id}/export")
    assert export.status_code == 200
    assert "# LLM Deliberation Report" in export.text
    assert "## Final synthesis" in export.text


# -- failed-run terminal-state / SSE reload-loop regression tests -----------
#
# Root cause: a failed run rendered the same live-update trigger
# (#pipeline-container + EventSource) as an actively running one. The SSE
# stream for an already-terminal run emits "pipeline" then "done" almost
# instantly, and the client's "done" handler unconditionally called
# window.location.reload() -- which re-opened the page, re-triggered the
# same EventSource, got "done" again, reloaded again, forever. This only
# affected failed runs because succeeded runs render result.html (no
# #pipeline-container, no EventSource, at all).


def test_is_terminal_run_status_classifies_correctly():
    from llm_deliberation.web.presenter import is_terminal_run_status

    assert is_terminal_run_status("succeeded") is True
    assert is_terminal_run_status("failed") is True
    assert is_terminal_run_status("pending") is False
    assert is_terminal_run_status("running") is False
    # A stage-level status, never a run-level one -- must not be treated
    # as if it were a terminal run status by accident.
    assert is_terminal_run_status("skipped") is False


def test_opening_already_failed_run_returns_200(client, service, fake_orchestrator_state):
    fake_orchestrator_state["fail"] = {"revision_a"}
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    assert service.get_run(run_id).status == "failed"

    detail = client.get(f"/runs/{run_id}")
    assert detail.status_code == 200


def test_failed_run_renders_completed_earlier_stages(client, service, fake_orchestrator_state):
    fake_orchestrator_state["fail"] = {"revision_a"}
    response = _submit(client, question="Q?", red_team=True)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    # The pipeline view (used for both in-progress and failed runs, before
    # and after this fix) shows each stage's status/label, not its full
    # generated text -- that only appears once a run succeeds (result.html).
    # A completed earlier stage must show as succeeded here.
    record = service.get_run(run_id)
    by_name = {s.name: s for s in record.stages}
    for name in ("analysis_a", "analysis_b", "critique_a_of_b", "critique_b_of_a"):
        assert by_name[name].status == "succeeded"

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    assert body.count("status-succeeded") >= 4
    assert "OpenAI" in body and "Anthropic" in body


def test_failed_stage_and_its_message_are_visible(client, service, fake_orchestrator_state):
    fake_orchestrator_state["fail"] = {"revision_a"}
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    # Jinja HTML-escapes the stored error text (the apostrophe becomes
    # &#39;), so check the substring that survives escaping.
    assert "simulated failure in stage" in body
    assert "revision_a" in body
    assert "Status: failed" in body


def test_failed_run_missing_downstream_artifacts_does_not_crash_rendering(
    client, service, fake_orchestrator_state
):
    # revision_a fails, so revision_b/convergence_analysis/synthesis never
    # ran -- the page must render these as pending, not crash.
    fake_orchestrator_state["fail"] = {"revision_a"}
    response = _submit(client, question="Q?", red_team=True)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    record = service.get_run(run_id)
    by_name = {s.name: s for s in record.stages}
    assert by_name["convergence_analysis"].status == "pending"
    assert by_name["synthesis"].status == "pending"

    detail = client.get(f"/runs/{run_id}")
    assert detail.status_code == 200  # no crash despite missing artifacts


def test_failed_run_does_not_render_live_update_trigger(client, service, fake_orchestrator_state):
    fake_orchestrator_state["fail"] = {"revision_a"}
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    # No element app.js's EventSource-opening code can find.
    assert 'id="pipeline-container"' not in body
    assert "data-run-id=" not in body
    # No no-JS polling fallback either.
    assert 'http-equiv="refresh"' not in body
    # The pipeline content itself is still rendered, just not wrapped in a
    # live-triggering container.
    assert 'id="pipeline-result"' in body
    assert "Status: failed" in body


def test_running_run_still_renders_live_update_trigger(client, service):
    """Point 12 / control case: a genuinely non-terminal run must keep its
    existing live-update behavior -- this fix must not disable it."""
    run_id = service.create_run("Q?", "economy", red_team_enabled=False)
    stage = service.repo.get_stage(run_id, "analysis_a")
    service.repo.mark_stage_running(stage.id)
    service.repo.update_run(run_id, status="running", set_started_if_unset=True)

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    assert 'id="pipeline-container"' in body
    assert f'data-run-id="{run_id}"' in body
    assert 'http-equiv="refresh"' in body


def test_sse_stream_for_already_failed_run_terminates_after_one_done_event(
    client, service, fake_orchestrator_state
):
    """Server-side half of the fix: even if a connection were opened
    against an already-terminal run, the stream must send exactly one
    "pipeline" event, one "done" event carrying the terminal status, and
    then end -- never loop, never send a second "pipeline" after "done"."""
    fake_orchestrator_state["fail"] = {"revision_a"}
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    assert service.get_run(run_id).status == "failed"

    with client.stream("GET", f"/runs/{run_id}/events") as r:
        raw = "".join(r.iter_lines())
    assert raw.count("event: pipeline") == 1
    assert raw.count("event: done") == 1
    assert raw.index("event: pipeline") < raw.index("event: done")
    # The "done" payload is the terminal status, not the run id -- the
    # client uses it to decide whether a reload is actually warranted.
    assert "data: failed" in raw


def test_sse_done_payload_is_succeeded_for_a_successful_run(
    client, service, fake_orchestrator_state
):
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    assert service.get_run(run_id).status == "succeeded"

    with client.stream("GET", f"/runs/{run_id}/events") as r:
        raw = "".join(r.iter_lines())
    assert "data: succeeded" in raw


def test_app_js_only_reloads_on_succeeded_done_and_closes_on_error():
    """No JS test runner exists in this project (deliberately not adding
    one for a bug fix) -- this asserts the actual shipped source contains
    the specific guard that prevents the reload loop, so a future edit
    that removes the guard breaks a test instead of silently reintroducing
    the bug."""
    app_js = (
        Path(__file__).resolve().parents[1]
        / "src" / "llm_deliberation" / "web" / "static" / "app.js"
    ).read_text()

    done_handler = app_js.split('addEventListener("done"', 1)[1].split("});", 1)[0]
    assert 'event.data === "succeeded"' in done_handler
    assert "window.location.reload()" in done_handler

    # onerror must close the connection -- EventSource auto-reconnects by
    # default, which would otherwise keep hammering a dead/terminal stream.
    onerror_handler = app_js.split("source.onerror", 1)[1]
    assert "source.close()" in onerror_handler.split("};", 1)[0]


def test_manual_retry_moves_a_failed_run_back_to_an_active_state(
    client, service, fake_orchestrator_state
):
    fake_orchestrator_state["fail"] = {"revision_a"}
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    assert service.get_run(run_id).status == "failed"

    fake_orchestrator_state["fail"] = set()
    retry_response = client.post(f"/runs/{run_id}/stages/revision_a/retry")
    assert retry_response.status_code == 200
    assert service.get_run(run_id).status == "succeeded"


def test_viewing_a_failed_run_does_not_change_accumulated_cost(
    client, service, fake_orchestrator_state
):
    fake_orchestrator_state["fail"] = {"revision_a"}
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    cost_before = service.get_run(run_id).estimated_total_cost_usd

    for _ in range(3):
        assert client.get(f"/runs/{run_id}").status_code == 200

    assert service.get_run(run_id).estimated_total_cost_usd == cost_before


def test_successful_run_live_behavior_and_rendering_are_unchanged(client, service):
    """Control case: a succeeded run must keep behaving exactly as before
    this fix -- result.html, no pipeline-container, no live trigger, ever."""
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    assert service.get_run(run_id).status == "succeeded"

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    assert "Final answer" in body
    assert 'id="pipeline-container"' not in body
    assert 'id="pipeline-result"' not in body  # that id is failed-run-only


# -- deliberation quality indicator + privacy disclosure --------------------
# (Section 14, items 26-30)


def test_quality_indicator_renders_complete_for_a_healthy_run(client, service):
    response = _submit(client, question="Q?", red_team=True)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    body = client.get(f"/runs/{run_id}").text
    assert "Deliberation quality" in body
    assert "Complete" in body


def test_quality_indicator_renders_incomplete_with_reasons(
    client, service, fake_orchestrator_state
):
    fake_orchestrator_state["responses"] = {
        "revision_a": {"incomplete_reason": "output_truncated"}
    }
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    body = client.get(f"/runs/{run_id}").text
    assert "Deliberation quality" in body
    assert "Incomplete" in body
    # The bullet names the affected artifact and that it was truncated, not
    # just a raw error string.
    assert "Revised candidate A" in body
    assert "output truncated" in body


def test_quality_indicator_renders_degraded_when_red_team_is_skipped(
    client, service, fake_orchestrator_state
):
    fake_orchestrator_state["fail"] = {"red_team"}
    response = _submit(client, question="Q?", red_team=True)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    client.post(f"/runs/{run_id}/stages/red_team/skip")
    body = client.get(f"/runs/{run_id}").text

    assert "Deliberation quality" in body
    assert "Degraded" in body


def test_quality_indicator_labels_render_in_estonian(client, service, fake_orchestrator_state):
    fake_orchestrator_state["responses"] = {
        "revision_a": {"incomplete_reason": "output_truncated"}
    }
    client.cookies.set("ui_lang", "et")
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    body = client.get(f"/runs/{run_id}").text
    assert "Arutelu kvaliteet" in body
    assert "Puudulik" in body


def test_privacy_disclosure_renders_on_the_new_deliberation_form_in_both_ui_languages(client):
    en_body = client.get("/").text
    assert "sent to the selected AI providers" in en_body

    client.cookies.set("ui_lang", "et")
    et_body = client.get("/").text
    assert "saadetakse töötlemiseks valitud" in et_body


# -- bilingual UI audit: UI-owned strings vs persisted run content ----------
# (focused follow-up audit; see the reliability-pass tests above for the
# earlier round of fixes)

_FORMERLY_HARDCODED_ENGLISH_LABELS = (
    "Economy", "Balanced", "Max",  # profile names (index.html picker)
    "Requested:", "Used:", "Fallback reason:", "Attempts (this try):",  # provenance labels
    "Starting...",  # new-run submit button
    "Candidate A", "Candidate B", "Meta-analysis", "Synthesis",  # pipeline row labels
)


def test_estonian_ui_has_no_known_hard_coded_english_labels(
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
            # No attempt_log -- exercises the backward-compat flat display,
            # the exact spot that still had raw English labels.
        }
    }
    client.cookies.set("ui_lang", "et")
    succeeded = _submit(client, question="Q?", red_team=True)
    succeeded_run_id = str(succeeded.url).rstrip("/").rsplit("/", 1)[-1]

    # A separate, failed run exercises the pipeline view (STAGE_GROUPS row
    # labels, the failed-branch provenance block) -- a succeeded run never
    # renders partials/pipeline.html at all.
    fake_orchestrator_state["fail"] = {"analysis_a"}
    failed = _submit(client, question="Q?", red_team=False)
    failed_run_id = str(failed.url).rstrip("/").rsplit("/", 1)[-1]

    succeeded_body = client.get(f"/runs/{succeeded_run_id}").text
    failed_body = client.get(f"/runs/{failed_run_id}").text
    home_body = client.get("/").text
    history_body = client.get("/history").text

    for label in _FORMERLY_HARDCODED_ENGLISH_LABELS:
        assert label not in succeeded_body, f"found hard-coded English label {label!r} in succeeded run (ET UI)"
        assert label not in failed_body, f"found hard-coded English label {label!r} in failed/pipeline run (ET UI)"
        assert label not in home_body, f"found hard-coded English label {label!r} on home page (ET UI)"
        assert label not in history_body, f"found hard-coded English label {label!r} in history (ET UI)"


def test_english_ui_still_shows_normal_english_labels(client, service, fake_orchestrator_state):
    fake_orchestrator_state["fail"] = {"analysis_a"}
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    body = client.get(f"/runs/{run_id}").text
    assert "Economy" in body
    assert "Candidate A" in body
    assert "Meta-analysis" in body
    assert "Synthesis" in body


def test_profile_balanced_renders_translated_in_estonian_ui(client, service):
    client.cookies.set("ui_lang", "et")
    response = _submit(client, question="Q?", profile="balanced")
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    body = client.get(f"/runs/{run_id}").text
    assert "Tasakaalustatud" in body
    assert "Balanced" not in body


def test_language_en_renders_as_inglise_in_estonian_ui(client, service):
    client.cookies.set("ui_lang", "et")
    response = _submit(client, question="Q?", language="en")
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    body = client.get(f"/runs/{run_id}").text
    assert "inglise" in body


def test_status_enum_renders_translated_in_estonian_ui(client, service, fake_orchestrator_state):
    fake_orchestrator_state["fail"] = {"analysis_a"}
    client.cookies.set("ui_lang", "et")
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    body = client.get(f"/runs/{run_id}").text
    assert "Ebaõnnestus" in body or "ebaõnnestus" in body


def test_convergence_enum_renders_translated_in_estonian_ui(
    client, service, fake_orchestrator_state
):
    fake_orchestrator_state["responses"] = {
        "convergence_analysis": _convergence_response(convergence="partial")
    }
    client.cookies.set("ui_lang", "et")
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    body = client.get(f"/runs/{run_id}").text
    assert "Osaline konsensus" in body


def test_quality_enum_renders_translated_in_estonian_ui(
    client, service, fake_orchestrator_state
):
    fake_orchestrator_state["responses"] = {
        "revision_a": {"incomplete_reason": "output_truncated"}
    }
    client.cookies.set("ui_lang", "et")
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    body = client.get(f"/runs/{run_id}").text
    assert "Arutelu kvaliteet" in body
    assert "Puudulik" in body


def test_english_historical_model_content_is_byte_for_byte_unchanged_in_estonian_ui(
    client, service, fake_orchestrator_state
):
    exact_text = 'Recommendation — proceed with "Option B" (see § 3.2), 50% confidence.'
    fake_orchestrator_state["responses"] = {"synthesis": {"text": exact_text}}
    response = _submit(client, question="Q?", language="en")
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    client.cookies.set("ui_lang", "et")
    body = client.get(f"/runs/{run_id}").text
    assert exact_text in body

    stored = service.get_run(run_id)
    stored_text = next(s.text for s in stored.stages if s.name == "synthesis")
    assert stored_text == exact_text


def test_language_mismatch_notice_appears_when_ui_and_run_language_differ(
    client, service
):
    client.cookies.set("ui_lang", "et")
    response = _submit(client, question="Q?", language="en")
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    body = client.get(f"/runs/{run_id}").text
    assert "Kasutajaliides on praegu eesti keeles" in body


def test_language_mismatch_notice_absent_when_ui_and_run_language_match(
    client, service
):
    client.cookies.set("ui_lang", "et")
    response = _submit(client, question="Q?", language="et")
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    body = client.get(f"/runs/{run_id}").text
    assert "Kasutajaliides on praegu eesti keeles" not in body
    assert "the interface is currently in" not in body.lower()


def test_provider_and_model_names_remain_unchanged_in_estonian_ui(
    client, service, fake_orchestrator_state
):
    client.cookies.set("ui_lang", "et")

    # The pipeline view's STAGE_GROUPS labels a *real* orchestrator would use
    # ("OpenAI", "Anthropic", "Gemini") -- these are proper nouns and must
    # never be translated.
    fake_orchestrator_state["fail"] = {"analysis_a"}
    failed = _submit(client, question="Q?", red_team=True)
    failed_run_id = str(failed.url).rstrip("/").rsplit("/", 1)[-1]
    failed_body = client.get(f"/runs/{failed_run_id}").text
    assert "OpenAI" in failed_body
    assert "Anthropic" in failed_body
    assert "Gemini" in failed_body

    # The actual per-stage provenance shown on a succeeded run's full trace
    # (FakeOrchestrator reports "Fake"/"fake-model") must also stay untranslated.
    fake_orchestrator_state["fail"] = set()
    succeeded = _submit(client, question="Q?", red_team=False)
    succeeded_run_id = str(succeeded.url).rstrip("/").rsplit("/", 1)[-1]
    succeeded_body = client.get(f"/runs/{succeeded_run_id}").text
    assert "Fake" in succeeded_body
    assert "fake-model" in succeeded_body


def test_persisted_enum_and_profile_values_remain_untranslated_in_storage(
    client, service
):
    client.cookies.set("ui_lang", "et")
    response = _submit(client, question="Q?", profile="balanced", language="en")
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    client.get(f"/runs/{run_id}")  # viewing in ET UI must not touch storage

    record = service.get_run(run_id)
    assert record.profile == "balanced"
    assert record.language == "en"


def test_switching_ui_language_triggers_no_new_stage_or_model_call(
    client, service, fake_orchestrator_state
):
    response = _submit(client, question="Q?", red_team=True)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    log_after_run = list(fake_orchestrator_state["log"])

    client.get(f"/runs/{run_id}")
    client.cookies.set("ui_lang", "et")
    client.get(f"/runs/{run_id}")
    client.cookies.set("ui_lang", "en")
    client.get(f"/runs/{run_id}")

    assert fake_orchestrator_state["log"] == log_after_run


# -- Question-length UX guidance (form-level) -------------------------------
# (Section 11, items 1-5, 11-14)


def test_question_helper_text_renders_in_english(client):
    body = client.get("/").text
    assert "Move background, constraints, and supporting" in body


def test_question_helper_text_renders_in_estonian(client):
    client.cookies.set("ui_lang", "et")
    body = client.get("/").text
    assert "Taust, piirangud ja toetavad detailid" in body


def test_context_helper_text_renders_in_english(client):
    body = client.get("/").text
    assert "Use this for background, facts, constraints, examples" in body


def test_context_helper_text_renders_in_estonian(client):
    client.cookies.set("ui_lang", "et")
    body = client.get("/").text
    assert "Lisa siia taust, faktid, piirangud, näited" in body


def test_question_field_has_a_4000_character_browser_maxlength(client):
    body = client.get("/").text
    assert re.search(r'<textarea id="question"[^>]*maxlength="4000"', body)


def test_question_field_carries_soft_and_hard_warning_thresholds_and_text(client):
    """The 1200/2000-character warning logic is wired into the DOM via
    data-* attributes app.js reads (see static/app.js) -- this is the
    server-rendered half of that logic; app.js's own input-event handling
    is checked in test_app_js_updates_counter_from_question_textarea_value."""
    body = client.get("/").text
    assert 'data-recommended-length="2000"' in body
    assert 'data-warn-threshold="1200"' in body
    assert "This question is getting long" in body  # soft warning text (EN)
    assert "longer than the recommended 2000" in body  # hard warning text (EN)


def test_question_field_warning_text_is_estonian_under_estonian_ui(client):
    client.cookies.set("ui_lang", "et")
    body = client.get("/").text
    assert "Küsimus muutub üsna pikaks" in body
    assert "Küsimus on pikem kui soovituslik 2000" in body


def test_question_char_counter_renders_with_recommended_target(client):
    body = client.get("/").text
    assert '<span id="question-char-count">0</span> / 2000' in body


def test_app_js_updates_counter_from_question_textarea_value():
    """Static check that the counter is driven by the live textarea value
    (not a stale/hard-coded number) and updates on every keystroke -- there
    is no in-process JS engine in this test suite, so this verifies the
    actual wiring in the shipped script, the same way this codebase already
    verifies other prompt/script-level invariants by inspecting source."""
    js_source = (Path(__file__).parent.parent / "src" / "llm_deliberation" / "web" / "static" / "app.js").read_text()
    assert 'getElementById("question")' in js_source
    assert "questionField.value.length" in js_source
    assert 'addEventListener("input"' in js_source
    assert "questionCount.textContent" in js_source


def test_question_between_1200_and_2000_chars_is_accepted_by_the_form(client, service):
    question = "A" * 1500
    response = _submit(client, question=question)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    assert service.get_run(run_id).question == question


def test_question_between_2000_and_4000_chars_is_accepted_by_the_form(client, service):
    question = "A" * 2500
    response = _submit(client, question=question)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    assert service.get_run(run_id).question == question


def test_oversized_question_submission_shows_translated_error_and_creates_no_run(
    client, service
):
    question = "A" * 4001
    response = _submit(client, question=question)
    assert response.status_code == 400
    assert "Question is too long" in response.text
    assert service.list_runs() == []


def test_oversized_question_submission_shows_estonian_error(client, service):
    client.cookies.set("ui_lang", "et")
    question = "A" * 4001
    response = _submit(client, question=question)
    assert response.status_code == 400
    assert "Küsimus on liiga pikk" in response.text


def test_oversized_question_submission_makes_no_provider_call(
    client, service, fake_orchestrator_state
):
    question = "A" * 4001
    _submit(client, question=question)
    assert fake_orchestrator_state["log"] == []


def test_context_persistence_unaffected_by_question_length_guidance_over_http(
    client, service
):
    long_context = ("background detail " * 300).strip()
    response = _submit(client, question="Short focused question?", context=long_context)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    assert service.get_run(run_id).context == long_context
