from __future__ import annotations

import asyncio
import json

import pytest

from llm_deliberation.orchestrator import ALL_STAGE_NAMES
from llm_deliberation.providers import ProviderGenerationError


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
        if stage.name == "convergence_analysis":
            # Structured JSON, not the generic "{name}-output" fake text --
            # see conftest.DEFAULT_CONVERGENCE_JSON.
            assert stage.text is not None and stage.text.startswith("{")
        else:
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
    assert result.convergence is not None


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
    # Only the retried stage and the newly-unblocked downstream stages ran;
    # everything already succeeded (including sibling revision_b) was
    # skipped.
    assert set(fake_orchestrator_state["log"]) == {
        "revision_a",
        "convergence_analysis",
        "synthesis",
    }
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


# -- Gemini fallback provenance / skip / recoverable-failure -----------------


def test_fallback_metadata_persists_correctly_on_success(service, fake_orchestrator_state):
    fake_orchestrator_state["responses"] = {
        "red_team": {
            "provider": "Google",
            "model": "gemini-3.7-flash",
            "requested_model": "gemini-3.8-flash",
            "fallback_used": True,
            "fallback_reason": "HTTP 503 from Gemini (model is overloaded)",
            "model_attempts": 5,
            "estimated_cost_usd": 0.0123,
        }
    }
    run_id = service.create_run("Q?", "economy", red_team_enabled=True)
    record = asyncio.run(service.start_run(run_id))

    assert record.status == "succeeded"
    red = next(s for s in record.stages if s.name == "red_team")
    assert red.status == "succeeded"
    assert red.model == "gemini-3.7-flash"
    assert red.requested_model == "gemini-3.8-flash"
    assert red.fallback_used is True
    assert red.fallback_reason == "HTTP 503 from Gemini (model is overloaded)"
    assert red.model_attempts == 5
    assert red.estimated_cost_usd == pytest.approx(0.0123)

    # Cost accounting: the run total is attributed to the actual model used,
    # and the (failed-attempt-inclusive) cost is not dropped from the total.
    other_stage_cost = 0.001 * (len(ALL_STAGE_NAMES) - 1)
    assert record.estimated_total_cost_usd == pytest.approx(other_stage_cost + 0.0123)

    # RunResult / Markdown export must also carry the disclosure through.
    result = service.to_run_result(run_id)
    assert result.red_team.fallback_used is True
    assert result.red_team.requested_model == "gemini-3.8-flash"


def test_all_gemini_models_failing_leaves_a_recoverable_failed_stage(
    service, fake_orchestrator_state
):
    error = ProviderGenerationError(
        "All configured Gemini models failed transiently: gemini-3.8-flash, gemini-3.7-flash.",
        requested_model="gemini-3.8-flash",
        attempts=8,
        attempt_log=[{"model": "gemini-3.8-flash", "attempt_number": 1, "outcome": "failed"}],
        fallback_used=True,
        fallback_reason="HTTP 503 from Gemini (model is overloaded)",
        estimated_cost_usd=0.0,
    )
    fake_orchestrator_state["fail_with"] = {"red_team": error}

    run_id = service.create_run("Q?", "economy", red_team_enabled=True)
    record = asyncio.run(service.start_run(run_id))

    assert record.status == "failed"
    red = next(s for s in record.stages if s.name == "red_team")
    assert red.status == "failed"
    assert red.requested_model == "gemini-3.8-flash"
    assert red.fallback_used is True
    assert red.fallback_reason == "HTTP 503 from Gemini (model is overloaded)"
    assert red.model_attempts == 8
    assert red.attempt_log is not None and len(red.attempt_log) == 1

    # Other stages in the same wave, and earlier waves, are unaffected.
    by_name = {s.name: s for s in record.stages}
    assert by_name["critique_a_of_b"].status == "succeeded"
    assert by_name["critique_b_of_a"].status == "succeeded"
    assert by_name["revision_a"].status == "pending"


def test_skip_red_team_continues_the_remaining_pipeline(service, fake_orchestrator_state):
    fake_orchestrator_state["fail"] = {"red_team"}
    run_id = service.create_run("Q?", "economy", red_team_enabled=True)
    record = asyncio.run(service.start_run(run_id))
    assert record.status == "failed"

    red_stage_id = next(s.id for s in record.stages if s.name == "red_team")
    fake_orchestrator_state["log"].clear()

    record = asyncio.run(service.skip_stage(run_id, red_stage_id))

    assert record.status == "succeeded"
    by_name = {s.name: s for s in record.stages}
    assert by_name["red_team"].status == "skipped"
    # Revision, convergence analysis, and synthesis proceeded without
    # red-team, exactly like the red-team-disabled path -- and were not
    # rerun beyond what was needed.
    assert "red_team" not in fake_orchestrator_state["log"]
    assert set(fake_orchestrator_state["log"]) == {
        "revision_a",
        "revision_b",
        "convergence_analysis",
        "synthesis",
    }

    result = service.to_run_result(run_id)
    assert result.red_team is None


def test_skip_only_allowed_for_red_team_stage(service):
    run_id = service.create_run("Q?", "economy", red_team_enabled=False)
    record = asyncio.run(service.start_run(run_id))
    stage_id = next(s.id for s in record.stages if s.name == "analysis_a")

    with pytest.raises(ValueError):
        asyncio.run(service.skip_stage(run_id, stage_id))


def test_skip_succeeded_red_team_stage_raises(service):
    run_id = service.create_run("Q?", "economy", red_team_enabled=True)
    record = asyncio.run(service.start_run(run_id))
    assert record.status == "succeeded"
    red_stage_id = next(s.id for s in record.stages if s.name == "red_team")

    with pytest.raises(ValueError):
        asyncio.run(service.skip_stage(run_id, red_stage_id))


def test_retry_stage_rejects_gemini_mode_for_non_red_team_stage(service, fake_orchestrator_state):
    fake_orchestrator_state["fail"] = {"analysis_a"}
    run_id = service.create_run("Q?", "economy", red_team_enabled=False)
    asyncio.run(service.start_run(run_id))

    with pytest.raises(ValueError):
        asyncio.run(service.retry_stage(run_id, "analysis_a", gemini_mode="preferred_only"))


def test_retry_preferred_mode_is_forwarded_to_the_orchestrator(service, fake_orchestrator_state):
    fake_orchestrator_state["fail"] = {"red_team"}
    run_id = service.create_run("Q?", "economy", red_team_enabled=True)
    asyncio.run(service.start_run(run_id))

    fake_orchestrator_state["gemini_modes"].clear()
    fake_orchestrator_state["fail"] = set()
    asyncio.run(service.retry_stage(run_id, "red_team", gemini_mode="preferred_only"))

    modes = dict(fake_orchestrator_state["gemini_modes"])
    assert modes["red_team"] == "preferred_only"


def test_no_duplicate_rerun_of_already_successful_openai_anthropic_stages_after_skip(
    service, fake_orchestrator_state
):
    fake_orchestrator_state["fail"] = {"red_team"}
    run_id = service.create_run("Q?", "economy", red_team_enabled=True)
    record = asyncio.run(service.start_run(run_id))
    red_stage_id = next(s.id for s in record.stages if s.name == "red_team")

    fake_orchestrator_state["log"].clear()
    record = asyncio.run(service.skip_stage(run_id, red_stage_id))

    assert record.status == "succeeded"
    # analysis_a/b and both critiques already succeeded before the skip and
    # must not be re-run.
    for name in ("analysis_a", "analysis_b", "critique_a_of_b", "critique_b_of_a"):
        assert name not in fake_orchestrator_state["log"]


# -- convergence_analysis stage -----------------------------------------


def _convergence_json(**overrides) -> str:
    payload = {
        "convergence": "converged",
        "material_changes": [],
        "agreements_reached": [],
        "unresolved_disagreements": [],
        "remaining_unknowns": [],
        "human_judgement_required": [],
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_convergence_analysis_runs_only_after_both_revisions_succeed(
    service, fake_orchestrator_state
):
    run_id = service.create_run("Q?", "economy", red_team_enabled=True)
    record = asyncio.run(service.start_run(run_id))
    assert record.status == "succeeded"

    log = fake_orchestrator_state["log"]
    assert log.index("convergence_analysis") > log.index("revision_a")
    assert log.index("convergence_analysis") > log.index("revision_b")


def test_synthesis_does_not_start_before_convergence_analysis_resolves(
    service, fake_orchestrator_state
):
    run_id = service.create_run("Q?", "economy", red_team_enabled=True)
    record = asyncio.run(service.start_run(run_id))
    assert record.status == "succeeded"

    log = fake_orchestrator_state["log"]
    assert log.index("synthesis") > log.index("convergence_analysis")


@pytest.mark.parametrize(
    "level", ["converged", "partial", "diverged", "insufficient_information"]
)
def test_each_convergence_level_is_persisted_correctly(service, fake_orchestrator_state, level):
    fake_orchestrator_state["responses"] = {
        "convergence_analysis": {"text": _convergence_json(convergence=level)}
    }
    run_id = service.create_run("Q?", "economy", red_team_enabled=False)
    record = asyncio.run(service.start_run(run_id))

    assert record.status == "succeeded"
    result = service.to_run_result(run_id)
    assert result.convergence is not None
    stored = json.loads(result.convergence.text)
    assert stored["convergence"] == level


def test_material_change_is_persisted_correctly(service, fake_orchestrator_state):
    fake_orchestrator_state["responses"] = {
        "convergence_analysis": {
            "text": _convergence_json(
                convergence="partial",
                material_changes=[
                    {
                        "candidate": "A",
                        "before": "Vendor-hosted preferred.",
                        "after": "Customer-controlled preferred.",
                        "material": True,
                        "triggers": [
                            {
                                "source": "peer_critique",
                                "stage": "critique_b_of_a",
                                "summary": "B raised a custody risk.",
                            }
                        ],
                    }
                ],
            )
        }
    }
    run_id = service.create_run("Q?", "economy", red_team_enabled=False)
    asyncio.run(service.start_run(run_id))

    result = service.to_run_result(run_id)
    stored = json.loads(result.convergence.text)
    change = stored["material_changes"][0]
    assert change["candidate"] == "A"
    assert change["before"] == "Vendor-hosted preferred."
    assert change["after"] == "Customer-controlled preferred."
    assert change["material"] is True
    assert change["triggers"][0]["stage"] == "critique_b_of_a"


def test_no_change_case_is_represented_explicitly(service, fake_orchestrator_state):
    fake_orchestrator_state["responses"] = {
        "convergence_analysis": {
            "text": _convergence_json(
                convergence="converged",
                material_changes=[
                    {
                        "candidate": "A",
                        "before": "Phased rollout.",
                        "after": "Phased rollout.",
                        "material": False,
                        "triggers": [],
                    }
                ],
            )
        }
    }
    run_id = service.create_run("Q?", "economy", red_team_enabled=False)
    asyncio.run(service.start_run(run_id))

    result = service.to_run_result(run_id)
    stored = json.loads(result.convergence.text)
    assert stored["material_changes"][0]["material"] is False


def test_convergence_failure_persists_earlier_stages_and_is_independently_retryable(
    service, fake_orchestrator_state
):
    fake_orchestrator_state["fail"] = {"convergence_analysis"}
    run_id = service.create_run("Q?", "economy", red_team_enabled=True)
    record = asyncio.run(service.start_run(run_id))

    assert record.status == "failed"
    by_name = {s.name: s for s in record.stages}
    for name in (
        "analysis_a",
        "analysis_b",
        "critique_a_of_b",
        "critique_b_of_a",
        "red_team",
        "revision_a",
        "revision_b",
    ):
        assert by_name[name].status == "succeeded"
    assert by_name["convergence_analysis"].status == "failed"
    assert by_name["synthesis"].status == "pending"

    # Retry only reruns convergence_analysis and the synthesis it unblocks --
    # not revisions or anything earlier.
    fake_orchestrator_state["log"].clear()
    fake_orchestrator_state["fail"] = set()
    convergence_stage_id = by_name["convergence_analysis"].id
    record = asyncio.run(service.retry_stage(run_id, convergence_stage_id))

    assert record.status == "succeeded"
    assert set(fake_orchestrator_state["log"]) == {"convergence_analysis", "synthesis"}


def test_skip_convergence_analysis_allows_synthesis_but_marks_it_unavailable(
    service, fake_orchestrator_state
):
    fake_orchestrator_state["fail"] = {"convergence_analysis"}
    run_id = service.create_run("Q?", "economy", red_team_enabled=False)
    record = asyncio.run(service.start_run(run_id))
    assert record.status == "failed"

    stage_id = next(s.id for s in record.stages if s.name == "convergence_analysis")
    fake_orchestrator_state["log"].clear()
    record = asyncio.run(service.skip_stage(run_id, stage_id))

    assert record.status == "succeeded"
    by_name = {s.name: s for s in record.stages}
    assert by_name["convergence_analysis"].status == "skipped"
    assert "convergence_analysis" not in fake_orchestrator_state["log"]
    assert "synthesis" in fake_orchestrator_state["log"]

    result = service.to_run_result(run_id)
    assert result.convergence is None


def test_convergence_analysis_cost_is_included_in_run_total(service, fake_orchestrator_state):
    fake_orchestrator_state["responses"] = {
        "convergence_analysis": {"text": _convergence_json(), "estimated_cost_usd": 0.0234}
    }
    run_id = service.create_run("Q?", "economy", red_team_enabled=True)
    record = asyncio.run(service.start_run(run_id))

    assert record.status == "succeeded"
    other_stage_count = len(ALL_STAGE_NAMES) - 1  # every stage but convergence_analysis
    expected = 0.001 * other_stage_count + 0.0234
    assert record.estimated_total_cost_usd == pytest.approx(expected)

    result = service.to_run_result(run_id)
    assert result.estimated_total_cost_usd == pytest.approx(expected)


def test_red_team_disabled_runs_still_support_convergence_analysis(
    service, fake_orchestrator_state
):
    run_id = service.create_run("Q?", "economy", red_team_enabled=False)
    record = asyncio.run(service.start_run(run_id))

    assert record.status == "succeeded"
    stage_names = {s.name for s in record.stages}
    assert "convergence_analysis" in stage_names
    assert "red_team" not in stage_names

    result = service.to_run_result(run_id)
    assert result.convergence is not None
    assert result.red_team is None


def test_historical_run_missing_convergence_analysis_stage_opens_correctly(
    service, fake_orchestrator_state
):
    """A run created before this stage existed has no convergence_analysis
    row at all (not "failed", not "skipped" -- structurally absent, since
    there is no schema migration that retroactively inserts stage rows for
    old runs). Everything reading such a run must degrade gracefully."""
    run_id = service.create_run("Q?", "economy", red_team_enabled=True)
    record = asyncio.run(service.start_run(run_id))
    assert record.status == "succeeded"

    conv_stage = service.repo.get_stage(run_id, "convergence_analysis")
    service.repo._conn.execute("DELETE FROM artifacts WHERE stage_id = ?", (conv_stage.id,))
    service.repo._conn.execute("DELETE FROM stages WHERE id = ?", (conv_stage.id,))
    service.repo._conn.commit()

    record = service.get_run(run_id)
    assert all(s.name != "convergence_analysis" for s in record.stages)

    result = service.to_run_result(run_id)
    assert result.convergence is None
    # Every other stage is unaffected.
    assert result.synthesis.text == "synthesis-output"
    assert result.revision_a.text == "revision_a-output"


# -- bilingual support (run/output language) -----------------------------


def test_new_run_defaults_to_english_for_backward_compatibility(service):
    run_id = service.create_run("Q?", "economy", red_team_enabled=False)
    record = service.get_run(run_id)
    assert record.language == "en"


def test_estonian_run_persists_et(service):
    run_id = service.create_run("Q?", "economy", red_team_enabled=False, language="et")
    record = service.get_run(run_id)
    assert record.language == "et"


def test_english_run_persists_en(service):
    run_id = service.create_run("Q?", "economy", red_team_enabled=False, language="en")
    record = service.get_run(run_id)
    assert record.language == "en"


def test_unsupported_language_is_rejected(service):
    with pytest.raises(ValueError):
        service.create_run("Q?", "economy", red_team_enabled=False, language="fr")


def test_language_survives_retry(service, fake_orchestrator_state):
    fake_orchestrator_state["fail"] = {"revision_a"}
    run_id = service.create_run("Q?", "economy", red_team_enabled=False, language="et")
    asyncio.run(service.start_run(run_id))
    assert service.get_run(run_id).status == "failed"

    fake_orchestrator_state["fail"] = set()
    record = asyncio.run(service.retry_stage(run_id, "revision_a"))
    assert record.status == "succeeded"
    assert record.language == "et"


def test_language_survives_resume(service, fake_orchestrator_state):
    fake_orchestrator_state["fail"] = {"revision_a"}
    run_id = service.create_run("Q?", "economy", red_team_enabled=False, language="et")
    asyncio.run(service.start_run(run_id))
    assert service.get_run(run_id).status == "failed"

    fake_orchestrator_state["fail"] = set()
    record = asyncio.run(service.resume_run(run_id))
    assert record.status == "succeeded"
    assert record.language == "et"


def test_historical_run_without_language_column_loads_with_english_default(
    service, fake_orchestrator_state
):
    """Simulates a genuinely pre-migration row: language column present
    (SQLite can't easily drop it back out) but let's instead confirm the
    migration default applies by constructing a fresh Repository directly
    against a bare, pre-bilingual-support schema."""
    import sqlite3
    import tempfile

    from llm_deliberation.store import Repository

    db_path = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE runs (
            id TEXT PRIMARY KEY,
            question TEXT NOT NULL,
            context TEXT,
            profile TEXT NOT NULL,
            red_team_enabled INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            estimated_total_cost_usd REAL NOT NULL DEFAULT 0.0
        )
        """
    )
    conn.execute(
        "INSERT INTO runs (id, question, context, profile, red_team_enabled, status, "
        "created_at, estimated_total_cost_usd) VALUES "
        "('old1', 'Old question?', NULL, 'economy', 0, 'succeeded', '2025-01-01T00:00:00', 0.01)"
    )
    conn.commit()
    conn.close()

    repo = Repository(db_path)
    record = repo.get_run("old1")
    assert record.language == "en"
    assert record.question == "Old question?"
    repo.close()


def test_original_question_is_not_translated_or_rewritten(service, fake_orchestrator_state):
    original_question = "Kas peaksime selle süsteemi ise ehitama või sisse ostma?"
    run_id = service.create_run(
        original_question, "economy", red_team_enabled=False, language="en"
    )
    record = asyncio.run(service.start_run(run_id))
    assert record.status == "succeeded"
    assert record.question == original_question

    result = service.to_run_result(run_id)
    assert result.question == original_question


@pytest.mark.parametrize("language", ["en", "et"])
def test_output_language_reaches_every_user_facing_stage(
    service, fake_orchestrator_state, language
):
    run_id = service.create_run("Q?", "economy", red_team_enabled=True, language=language)
    record = asyncio.run(service.start_run(run_id))
    assert record.status == "succeeded"

    languages_used = dict(fake_orchestrator_state["languages"])
    for stage in (
        "analysis_a",
        "analysis_b",
        "critique_a_of_b",
        "critique_b_of_a",
        "red_team",
        "revision_a",
        "revision_b",
        "convergence_analysis",
        "synthesis",
    ):
        assert languages_used[stage] == language


def test_no_additional_deliberation_stage_was_introduced_for_bilingual_support(
    service, fake_orchestrator_state
):
    run_id = service.create_run("Q?", "economy", red_team_enabled=True, language="et")
    record = asyncio.run(service.start_run(run_id))
    assert record.status == "succeeded"
    assert set(fake_orchestrator_state["log"]) == set(ALL_STAGE_NAMES)
    assert len(fake_orchestrator_state["log"]) == len(ALL_STAGE_NAMES)
