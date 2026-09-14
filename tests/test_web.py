from __future__ import annotations


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
    # Only the retried stage and the downstream stage it unblocked ran --
    # not analysis/critique/revision_b, which had already succeeded.
    assert set(fake_orchestrator_state["log"]) == {"revision_a", "synthesis"}


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
