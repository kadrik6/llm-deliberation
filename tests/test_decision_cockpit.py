"""Decision Cockpit Phase 1 -- see docs/design/decision-cockpit-proposal.md.

Covers the genuinely new pieces added this phase: a truthful, 7-state
red-team status (presenter.red_team_state), a compact operational trace
rollup (presenter.trace_summary), and the red-team/material-change
reference count (presenter.red_team_material_change_reference_count) --
plus their rendering in partials/result.html and the terminology
safeguards those renderings must respect.

Deliberately does NOT touch any Case 3 evaluation data, mapping, or
provider configuration. Every scenario here uses either direct presenter
unit tests against hand-built RunRecord/StageRecord objects, or the
existing FakeOrchestrator-backed web test fixtures (no network, no real
provider calls).
"""
from __future__ import annotations

import json

from llm_deliberation.providers import ProviderGenerationError
from llm_deliberation.store import RunRecord, StageRecord
from llm_deliberation.web.presenter import (
    RED_TEAM_STATE_COMPLETED,
    RED_TEAM_STATE_CONFIGURED_OFF,
    RED_TEAM_STATE_EXTERNALLY_BLOCKED,
    RED_TEAM_STATE_FAILED,
    RED_TEAM_STATE_SKIPPED,
    RED_TEAM_STATE_SKIPPED_FOR_BUDGET,
    RED_TEAM_STATE_UNAVAILABLE,
    red_team_material_change_reference_count,
    red_team_state,
    trace_summary,
)

from tests.test_web import _convergence_response, _submit


# -- presenter.red_team_state: direct unit tests -------------------------

def _stage(name="red_team", status="succeeded", **overrides) -> StageRecord:
    defaults = dict(
        id=1, run_id="r1", name=name, provider="Google", model="gemini-3.8-flash",
        status=status, attempt=1, input_tokens=10, output_tokens=20,
        estimated_cost_usd=0.001, error=None, started_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:00:01+00:00",
    )
    defaults.update(overrides)
    return StageRecord(**defaults)


def _run(stages: list[StageRecord], *, red_team_enabled: bool = True) -> RunRecord:
    return RunRecord(
        id="r1", question="Q?", context=None, profile="economy",
        red_team_enabled=red_team_enabled, status="succeeded", created_at="2026-01-01T00:00:00+00:00",
        started_at="2026-01-01T00:00:00+00:00", completed_at="2026-01-01T00:00:02+00:00",
        estimated_total_cost_usd=0.01, stages=stages,
    )


def test_red_team_state_configured_off_when_never_enabled():
    record = _run([], red_team_enabled=False)
    assert red_team_state(record) == {"status": RED_TEAM_STATE_CONFIGURED_OFF}


def test_red_team_state_unavailable_when_enabled_but_no_stage_row():
    """Enabled, but no red_team StageRecord exists yet (or ever) -- an
    honest "don't know" rather than conflating it with "off"."""
    record = _run([], red_team_enabled=True)
    assert red_team_state(record) == {"status": RED_TEAM_STATE_UNAVAILABLE}


def test_red_team_state_unavailable_while_pending_or_running():
    for status in ("pending", "running"):
        record = _run([_stage(status=status)])
        assert red_team_state(record) == {"status": RED_TEAM_STATE_UNAVAILABLE}


def test_red_team_state_completed_on_success():
    record = _run([_stage(status="succeeded")])
    assert red_team_state(record) == {"status": RED_TEAM_STATE_COMPLETED}


def test_red_team_state_skipped_for_budget():
    from llm_deliberation.orchestrator import RED_TEAM_COMPLETION_RESERVE_SKIP_REASON

    record = _run([_stage(status="skipped", fallback_reason=RED_TEAM_COMPLETION_RESERVE_SKIP_REASON)])
    assert red_team_state(record) == {"status": RED_TEAM_STATE_SKIPPED_FOR_BUDGET}


def test_red_team_state_plain_skip_with_no_attempt_history():
    """A stage skipped from "pending" (never attempted at all) must read as
    a plain skip, not a failure of any kind."""
    record = _run([_stage(status="skipped", fallback_reason="user_skip", attempt_log=None)])
    assert red_team_state(record) == {"status": RED_TEAM_STATE_SKIPPED}


def test_red_team_state_failed_for_a_non_access_error():
    record = _run([_stage(status="failed", attempt_log=[{"model": "gemini-3.8-flash", "outcome": "failed"}])])
    assert red_team_state(record) == {"status": RED_TEAM_STATE_FAILED}


def test_red_team_state_failed_with_no_attempt_log_at_all():
    """A historical run predating attempt_log storage: still a plain
    "failed", never crashes, never invents an externally_blocked claim."""
    record = _run([_stage(status="failed", attempt_log=None)])
    assert red_team_state(record) == {"status": RED_TEAM_STATE_FAILED}


def test_red_team_state_externally_blocked_on_a_failed_stage():
    record = _run(
        [_stage(status="failed", attempt_log=[{"model": "gemini-3.8-flash", "outcome": "failed", "http_code": 403}])]
    )
    assert red_team_state(record) == {"status": RED_TEAM_STATE_EXTERNALLY_BLOCKED, "http_code": 403}


def test_red_team_state_externally_blocked_survives_a_subsequent_skip():
    """The real, motivating scenario (Case 3 FULL): red_team fails with an
    HTTP 403, the run itself goes to "failed", and the user later skips the
    stage so the rest of the run can complete. store.mark_stage_skipped
    deliberately never clears attempt_log -- this must still be reported as
    externally_blocked, never silently downgraded to a plain "skipped" just
    because the terminal status changed."""
    record = _run(
        [
            _stage(
                status="skipped",
                fallback_reason="user_skip",
                attempt_log=[{"model": "gemini-3.8-flash", "outcome": "failed", "http_code": 403}],
            )
        ]
    )
    assert red_team_state(record) == {"status": RED_TEAM_STATE_EXTERNALLY_BLOCKED, "http_code": 403}


def test_red_team_state_401_also_counts_as_externally_blocked():
    record = _run([_stage(status="failed", attempt_log=[{"model": "gemini-3.8-flash", "http_code": 401}])])
    assert red_team_state(record) == {"status": RED_TEAM_STATE_EXTERNALLY_BLOCKED, "http_code": 401}


def test_red_team_state_uses_the_most_recent_attempt_log_entry():
    """A fallback chain that failed early with a 5xx and only later hit a
    403 (or vice versa) must be classified by its last, decisive attempt --
    not the first one in the log."""
    record = _run(
        [
            _stage(
                status="failed",
                attempt_log=[
                    {"model": "gemini-3.8-flash", "http_code": 503},
                    {"model": "gemini-3.7-flash", "http_code": 403},
                ],
            )
        ]
    )
    assert red_team_state(record) == {"status": RED_TEAM_STATE_EXTERNALLY_BLOCKED, "http_code": 403}


# -- presenter.trace_summary -----------------------------------------------

def test_trace_summary_counts_stages_attempts_and_retries():
    record = _run(
        [
            _stage(name="analysis_a", model_attempts=1),
            _stage(name="analysis_b", model_attempts=3),
            _stage(name="red_team", model_attempts=1),
        ]
    )
    assert trace_summary(record) == {
        "logical_stages": 3,
        "total_provider_attempts": 5,
        "stages_with_retry_or_fallback": 1,
    }


def test_trace_summary_on_a_run_with_no_stages_yet():
    record = _run([])
    assert trace_summary(record) == {
        "logical_stages": 0,
        "total_provider_attempts": 0,
        "stages_with_retry_or_fallback": 0,
    }


# -- presenter.red_team_material_change_reference_count -------------------

def _analysis(material_changes: list[dict]):
    from llm_deliberation.convergence import parse_convergence_analysis

    payload = {
        "convergence": "partial",
        "material_changes": material_changes,
        "agreements_reached": [],
        "unresolved_disagreements": [],
        "remaining_unknowns": [],
        "human_judgement_required": [],
    }
    return parse_convergence_analysis(json.dumps(payload))


def test_material_change_reference_count_is_none_without_evolution():
    """Never a fabricated 0 -- None means "no convergence data at all"."""
    assert red_team_material_change_reference_count(None) is None


def test_material_change_reference_count_zero_when_no_red_team_trigger():
    evolution = _analysis(
        [
            {
                "candidate": "A", "before": "x", "after": "y", "change_status": "material",
                "triggers": [{"source": "peer_critique", "summary": "B raised a concern."}],
            }
        ]
    )
    assert red_team_material_change_reference_count(evolution) == 0


def test_material_change_reference_count_counts_only_material_changes_with_a_red_team_trigger():
    evolution = _analysis(
        [
            {
                "candidate": "A", "before": "x", "after": "y", "change_status": "material",
                "triggers": [{"source": "red_team", "summary": "Red-team flagged a risk."}],
            },
            {
                # non_material: must not count even though it cites red_team.
                "candidate": "B", "before": "x", "after": "x", "change_status": "non_material",
                "triggers": [{"source": "red_team", "summary": "Red-team flagged a risk."}],
            },
            {
                "candidate": "A", "before": "p", "after": "q", "change_status": "material",
                "triggers": [{"source": "peer_critique", "summary": "Unrelated."}],
            },
        ]
    )
    assert red_team_material_change_reference_count(evolution) == 1


# -- Rendering: partials/result.html via the full web stack (FakeOrchestrator) --

def test_result_page_shows_configured_off_state(client, service):
    response = _submit(client, question="Q?", red_team=False)
    body = response.text
    assert "Red-team status" in body
    assert "Off (not configured for this run)" in body


def test_result_page_shows_completed_state_with_no_material_change_reference(client, service):
    response = _submit(client, question="Q?", red_team=True)
    body = response.text
    assert "Red-team status" in body
    assert "Completed" in body
    assert "Not referenced in any material change." in body


def test_result_page_shows_referenced_in_material_changes_when_red_team_triggered_a_change(
    client, service, fake_orchestrator_state
):
    fake_orchestrator_state["responses"] = {
        "convergence_analysis": _convergence_response(
            convergence="partial",
            material_changes=[
                {
                    "candidate": "A", "before": "x", "after": "y", "material": True,
                    "triggers": [{"source": "red_team", "summary": "Red-team flagged a risk."}],
                }
            ],
        )
    }
    response = _submit(client, question="Q?", red_team=True)
    body = response.text
    assert "Referenced in" in body
    assert "1 material change" in body
    # Must never be phrased as if red-team's own claim were a verified fact.
    assert "Found 1 error" not in body
    assert "errors found" not in body.lower()


def test_result_page_shows_skipped_for_budget_state_and_preserves_the_exact_notice(
    client, service, fake_orchestrator_state
):
    """The exact sentence existing tests assert on must survive, and the
    new state label must appear alongside it, not replace it."""
    from decimal import Decimal
    import asyncio

    fake_orchestrator_state["upper_bound_cost_usd"] = {"red_team": Decimal("0.30")}
    run_id = service.create_run("Q?", "economy", red_team_enabled=True, max_run_cost_usd="0.35")
    record = asyncio.run(service.start_run(run_id))
    assert record.status == "succeeded"

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    assert "Red-team was skipped to preserve enough budget to complete the final result." in body
    assert "Skipped to preserve budget" in body


def test_result_page_shows_externally_blocked_state_after_skipping_a_blocked_stage(
    client, service, fake_orchestrator_state
):
    """Reproduces the real, motivating Case 3 shape (an HTTP 403 from the
    provider, not a code defect) using only FakeOrchestrator -- no live
    provider call, no Case 3 data of any kind."""
    fake_orchestrator_state["fail_with"] = {
        "red_team": ProviderGenerationError(
            "Gemini rejected the request (HTTP 403).",
            requested_model="gemini-3.8-flash",
            attempts=1,
            attempt_log=[
                {
                    "model": "gemini-3.8-flash",
                    "attempt_number": 1,
                    "outcome": "failed",
                    "reason": "permission_denied",
                    "http_code": 403,
                }
            ],
            fallback_used=False,
            fallback_reason=None,
            estimated_cost_usd=0.0,
            reason="provider_error",
        )
    }
    response = _submit(client, question="Q?", red_team=True)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    assert service.get_run(run_id).status == "failed"

    skip_response = client.post(f"/runs/{run_id}/stages/red_team/skip")
    assert skip_response.status_code == 200
    assert service.get_run(run_id).status == "succeeded"

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    assert "Externally blocked by the provider" in body
    assert "(HTTP 403)" in body
    assert "not a code or model defect" in body
    # This is a degraded, not complete, result -- the existing quality
    # indicator must say so.
    assert "degraded" in body.lower() or "Degraded" in body


def test_result_page_shows_trace_summary_collapsed_by_default(client, service):
    response = _submit(client, question="Q?", red_team=True)
    body = response.text
    assert "Operational trace" in body
    assert "pipeline stage" in body
    assert "provider attempt" in body
    trace_pos = body.index("Operational trace")
    surrounding = body[max(0, trace_pos - 200):trace_pos]
    assert "<details" in surrounding
    assert "<details open" not in surrounding


# -- Terminology safeguards -------------------------------------------------

def test_no_forbidden_confidence_or_causal_language_anywhere_on_a_succeeded_run(
    client, service, fake_orchestrator_state
):
    """Model agreement is not correctness; a material change is not an
    "improvement"/"correction"/"fix"; red-team's contribution is never
    phrased as a verified "finding". These must never appear anywhere on a
    succeeded run's page, regardless of scenario."""
    fake_orchestrator_state["responses"] = {
        "convergence_analysis": _convergence_response(
            convergence="partial",
            material_changes=[
                {
                    "candidate": "A", "before": "x", "after": "y", "material": True,
                    "triggers": [{"source": "red_team", "summary": "Red-team flagged a risk."}],
                }
            ],
        )
    }
    response = _submit(client, question="Q?", red_team=True)
    body = response.text
    lowered = body.lower()
    for forbidden in (
        "found errors", "errors found", "issues found", "caught issues",
        "confidence score", "confidence:", "% confidence", "agreement score",
        "% agreement", "accuracy score", "proven correct", "verified correct",
    ):
        assert forbidden not in lowered, f"forbidden phrase present: {forbidden!r}"


# -- Historical-run compatibility ------------------------------------------

def test_red_team_state_and_trace_summary_on_a_legacy_run_missing_newer_fields():
    """A run/stage row predating attempt_log, model_attempts default, and
    language_contract_status must still render without crashing, preferring
    an honest "unavailable"/plain count over any fabricated status."""
    legacy_stage = StageRecord(
        id=1, run_id="r1", name="red_team", provider="Google", model="gemini-3.7-flash",
        status="failed", attempt=1, input_tokens=5, output_tokens=5,
        estimated_cost_usd=0.001, error="some legacy error text",
        started_at="2020-01-01T00:00:00+00:00", completed_at="2020-01-01T00:00:01+00:00",
        # attempt_log, requested_model, fallback fields all left at their
        # dataclass defaults -- exactly what a pre-migration row looks like.
    )
    record = _run([legacy_stage])
    assert red_team_state(record) == {"status": RED_TEAM_STATE_FAILED}
    assert trace_summary(record) == {
        "logical_stages": 1,
        "total_provider_attempts": 1,
        "stages_with_retry_or_fallback": 0,
    }
