"""Offline tests for the working-language feature: an Estonian-output run
uses English internally for verbose intermediate stages (token-efficiency/
reliability), while convergence_analysis and synthesis stay in Estonian.
No network calls anywhere -- FakeOrchestrator (via the `service`/`client`
fixtures) and pure prompt/policy-function inspection only.

Numbered comments below correspond to the 20 items in the task brief's
Section 11 checklist.
"""

from __future__ import annotations

import asyncio

import pytest

from llm_deliberation.orchestrator import (
    ALL_STAGE_NAMES,
    WORKING_LANGUAGE_STAGES,
    default_working_language,
    resolve_stage_language,
)
from llm_deliberation.prompts import base_system


# -- 1/2: new-run defaults ----------------------------------------------


def test_1_new_english_run_has_matching_output_and_working_language(service):
    run_id = service.create_run("Q?", "economy", red_team_enabled=False, language="en")
    record = service.get_run(run_id)
    assert record.language == "en"
    assert record.working_language == "en"


def test_2_new_estonian_run_defaults_working_language_to_english(service):
    run_id = service.create_run("Q?", "economy", red_team_enabled=False, language="et")
    record = service.get_run(run_id)
    assert record.language == "et"
    assert record.working_language == "en"


def test_default_working_language_function_directly():
    assert default_working_language("en") == "en"
    assert default_working_language("et") == "en"


# -- 3: historical run with no working_language stays legacy-compatible ----


def test_3_historical_run_without_working_language_column_stays_legacy_compatible(
    tmp_path,
):
    import sqlite3

    from llm_deliberation.store import Repository

    db_path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE runs (
            id TEXT PRIMARY KEY, question TEXT NOT NULL, context TEXT,
            profile TEXT NOT NULL, red_team_enabled INTEGER NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT,
            completed_at TEXT, estimated_total_cost_usd REAL NOT NULL DEFAULT 0.0,
            language TEXT NOT NULL DEFAULT 'en'
        );
        CREATE TABLE stages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, name TEXT NOT NULL,
            provider TEXT, model TEXT, status TEXT NOT NULL DEFAULT 'pending',
            attempt INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0, estimated_cost_usd REAL NOT NULL DEFAULT 0.0,
            error TEXT, started_at TEXT, completed_at TEXT
        );
        CREATE TABLE artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, stage_id INTEGER NOT NULL,
            artifact_type TEXT NOT NULL, text_content TEXT NOT NULL, created_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO runs (id, question, context, profile, red_team_enabled, status, "
        "created_at, estimated_total_cost_usd, language) "
        "VALUES ('old-et', 'Vana küsimus?', NULL, 'economy', 0, 'succeeded', "
        "'2025-01-01T00:00:00+00:00', 0.01, 'et')"
    )
    conn.commit()
    conn.close()

    repo = Repository(db_path)
    run = repo.get_run("old-et")
    assert run.working_language is None  # NOT falsely labelled "en"

    # Legacy fallback: effective working language == output language, so
    # every stage resolves to "et" -- the run's real, original behavior.
    effective_working_language = run.working_language or run.language
    for stage in ALL_STAGE_NAMES:
        assert (
            resolve_stage_language(
                stage, output_language=run.language, working_language=effective_working_language
            )
            == "et"
        )


# -- 4-9: centralized stage-language policy + resulting system prompts -----


@pytest.mark.parametrize(
    "stage", ["analysis_a", "analysis_b", "critique_a_of_b", "critique_b_of_a", "red_team", "revision_a", "revision_b"]
)
def test_4_to_7_verbose_stages_resolve_to_english_on_an_estonian_run(stage):
    resolved = resolve_stage_language(stage, output_language="et", working_language="en")
    assert resolved == "en"

    system_prompt = base_system(resolved, "et")
    assert "WORKING LANGUAGE OVERRIDE" in system_prompt
    # Strengthened wording (see AUDIT_REPORT.md's live-canary findings: a
    # real run showed a model silently mirroring the source language
    # despite the original, weaker "Perform this intermediate analysis in
    # concise English" wording) -- now explicitly rules out mirroring.
    assert "in English" in system_prompt
    assert "Do not mirror" in system_prompt
    assert "Estonian" in system_prompt  # mentions the original may be Estonian
    # And must NOT contain a contradictory plain "write in Estonian" directive.
    assert "Write all user-facing analytical content in natural Estonian" not in system_prompt


@pytest.mark.parametrize("stage", ["convergence_analysis", "synthesis"])
def test_8_9_convergence_and_synthesis_resolve_to_estonian_on_an_estonian_run(stage):
    resolved = resolve_stage_language(stage, output_language="et", working_language="en")
    assert resolved == "et"

    system_prompt = base_system(resolved, "et")
    assert "Write all user-facing analytical content in natural Estonian" in system_prompt
    assert "WORKING LANGUAGE OVERRIDE" not in system_prompt


def test_no_contradictory_language_instructions_anywhere_in_one_system_prompt():
    # A single system prompt must never contain both "respond in Estonian"
    # and "respond in English" directives at once (Section 8's concern).
    for stage in ALL_STAGE_NAMES:
        resolved = resolve_stage_language(stage, output_language="et", working_language="en")
        system_prompt = base_system(resolved, "et")
        mentions_estonian_directive = "natural Estonian" in system_prompt
        mentions_english_directive = (
            "Write all user-facing analytical content in English" in system_prompt
            or "Write your entire response -- all analytical prose -- in English" in system_prompt
        )
        assert mentions_estonian_directive != mentions_english_directive


def test_english_run_is_byte_for_byte_unaffected_by_working_language_feature():
    # For output_language == working_language == "en" (every stage on an
    # English run), the system prompt must be identical to the pre-working-
    # language wording -- no override text leaks in.
    for stage in ALL_STAGE_NAMES:
        resolved = resolve_stage_language(stage, output_language="en", working_language="en")
        assert resolved == "en"
        system_prompt = base_system(resolved, "en")
        assert "WORKING LANGUAGE OVERRIDE" not in system_prompt
        assert "Write all user-facing analytical content in English" in system_prompt


def test_working_language_stages_set_matches_the_task_brief():
    assert WORKING_LANGUAGE_STAGES == frozenset(
        {
            "analysis_a", "analysis_b", "critique_a_of_b", "critique_b_of_a",
            "red_team", "revision_a", "revision_b",
        }
    )
    assert "convergence_analysis" not in WORKING_LANGUAGE_STAGES
    assert "synthesis" not in WORKING_LANGUAGE_STAGES


# -- 4 (continued): original Estonian question/context reach the analysis prompt unchanged --


def test_original_estonian_question_reaches_independent_analysis_prompt_unchanged():
    from llm_deliberation.prompts import independent_analysis

    question = "Kas peaksime selle süsteemi ise ehitama või sisse ostma?"
    prompt = independent_analysis(question)
    assert question in prompt  # untranslated, verbatim


# -- 10: synthesis receives the original Estonian question as authoritative -


def test_10_synthesis_prompt_receives_original_estonian_question_unchanged():
    from llm_deliberation.prompts import synthesis

    question = "Mis on parim strateegia turule sisenemiseks?"
    prompt = synthesis(question, "rev a (english)", "rev b (english)", None)
    assert question in prompt


# -- 11: convergence schema stays English regardless of content language ---


def test_11_convergence_schema_keys_and_enums_unchanged_with_estonian_content():
    import json

    from llm_deliberation.convergence import parse_convergence_analysis

    payload = {
        "convergence": "partial",
        "material_changes": [
            {
                "candidate": "A",
                "before": "Enne: soovitasin X",
                "after": "Pärast: soovitan Y",
                "change_status": "material",
                "triggers": [
                    {
                        "source": "peer_critique",
                        "stage": "critique_b_of_a",
                        "summary": "B osutas riskile, mida A algselt ei arvestanud",
                    }
                ],
            }
        ],
        "agreements_reached": [
            {"topic": "Eelarve", "shared_position": "Mõlemad nõustuvad, et Z on oluline"}
        ],
        "unresolved_disagreements": [
            {
                "topic": "Ajakava",
                "candidate_a_position": "Kiire",
                "candidate_b_position": "Aeglane",
                "why_unresolved": "Vastuolulised andmed",
                "decision_impact": "Kõrge",
            }
        ],
        "remaining_unknowns": [
            {
                "unknown": "Kui suur on eelarve?",
                "why_it_matters": "Mõjutab valikut",
                "evidence_needed": "Kinnitatud eelarve",
            }
        ],
        "human_judgement_required": [
            {"issue": "Riskivalmiduse valik", "why_models_cannot_resolve_it": "See on väärtushinnang"}
        ],
    }
    analysis = parse_convergence_analysis(json.dumps(payload))
    # Schema-level enum/field names stay the stable English values...
    assert analysis.convergence == "partial"
    assert analysis.material_changes[0].triggers[0].source == "peer_critique"
    assert analysis.material_changes[0].change_status == "material"
    # ...while the explanatory prose fields carry the Estonian content
    # unchanged, proving the schema validates regardless of content language.
    assert "soovitasin" in analysis.material_changes[0].before
    assert "eelarve" in analysis.remaining_unknowns[0].unknown.lower()


# -- 12: truncation recovery for an ET analysis stage stays English --------


def test_12_truncation_recovery_for_estonian_run_analysis_stays_english():
    import asyncio as _asyncio

    from llm_deliberation.orchestrator import DeliberationOrchestrator
    from llm_deliberation.types import ModelResponse, Usage

    class _TruncatingProvider:
        provider_name = "OpenAI"
        model = "gpt-5.6-sol"
        max_output_tokens = 100

        def __init__(self):
            self.calls: list[str] = []

        def generate(self, *, system: str, prompt: str) -> ModelResponse:
            self.calls.append(system)
            incomplete = len(self.calls) == 1
            return ModelResponse(
                provider="OpenAI", model=self.model, text="partial answer" if incomplete else "final answer",
                usage=Usage(input_tokens=10, output_tokens=100), estimated_cost_usd=0.01,
                requested_model=self.model,
                incomplete_reason="output_truncated" if incomplete else None,
            )

    orchestrator = DeliberationOrchestrator.__new__(DeliberationOrchestrator)
    provider = _TruncatingProvider()
    orchestrator.a = provider

    response = _asyncio.run(
        orchestrator.run_stage(
            "analysis_a", "Kas peaksime X tegema?", {},
            output_language="et", working_language="en",
        )
    )
    assert response.incomplete_reason is None
    assert len(provider.calls) == 2
    initial_system, recovery_system = provider.calls
    # Both calls used the English working-language override, never Estonian.
    for system in (initial_system, recovery_system):
        assert "WORKING LANGUAGE OVERRIDE" in system
        assert "Write all user-facing analytical content in natural Estonian" not in system
    # The recovery instruction itself is phrased in English (not Estonian),
    # so it doesn't pull the model toward switching languages mid-recovery.
    assert "your previous response to this exact task was cut off" in recovery_system
    assert "OLULINE:" not in recovery_system  # the Estonian recovery instruction's own heading


# -- 13: retry/resume preserve the same working language --------------------


def test_13_retry_preserves_the_same_working_language(service, fake_orchestrator_state):
    fake_orchestrator_state["fail"] = {"revision_a"}
    run_id = service.create_run("Q?", "economy", red_team_enabled=False, language="et")
    asyncio.run(service.start_run(run_id))
    assert service.get_run(run_id).status == "failed"

    fake_orchestrator_state["fail"] = set()
    asyncio.run(service.retry_stage(run_id, "revision_a"))

    languages_used = dict(fake_orchestrator_state["languages"])
    assert languages_used["revision_a"] == "en"  # working language, unchanged by retry
    assert languages_used["synthesis"] == "et"  # output language, unchanged by retry


def test_13_resume_preserves_the_same_working_language(service, fake_orchestrator_state):
    fake_orchestrator_state["fail"] = {"revision_a"}
    run_id = service.create_run("Q?", "economy", red_team_enabled=False, language="et")
    asyncio.run(service.start_run(run_id))
    assert service.get_run(run_id).status == "failed"

    fake_orchestrator_state["fail"] = set()
    record = asyncio.run(service.resume_run(run_id))
    assert record.status == "succeeded"

    languages_used = dict(fake_orchestrator_state["languages"])
    assert languages_used["revision_a"] == "en"
    assert languages_used["analysis_a"] == "en"
    assert languages_used["synthesis"] == "et"
    assert languages_used["convergence_analysis"] == "et"


# -- 14: full deliberation trace shows the real English artifact unchanged --


def test_14_full_trace_shows_original_english_intermediate_artifact_unchanged(
    client, fake_orchestrator_state
):
    fake_orchestrator_state["responses"] = {
        "analysis_a": {"text": "This is the English intermediate analysis."},
    }
    response = client.post(
        "/runs", data={"question": "Küsimus?", "profile": "economy", "language": "et"}
    )
    body = response.text
    assert "This is the English intermediate analysis." in body  # verbatim, not translated
    assert "(EN)" in body  # per-artifact language tag, since it differs from record.language


# -- 15: main ET Decision Dashboard can contain Estonian convergence content -


def test_15_estonian_decision_dashboard_shows_estonian_convergence_content(
    client, fake_orchestrator_state
):
    import json

    estonian_convergence = json.dumps(
        {
            "convergence": "partial",
            "material_changes": [],
            "agreements_reached": [
                {"topic": "Eelarve", "shared_position": "Kokkulepe saavutati eelarve osas"}
            ],
            "unresolved_disagreements": [],
            "remaining_unknowns": [],
            "human_judgement_required": [],
        }
    )
    fake_orchestrator_state["responses"] = {
        "convergence_analysis": {"text": estonian_convergence},
        "synthesis": {"text": "See on lõplik eestikeelne vastus."},
    }
    response = client.post(
        "/runs", data={"question": "Küsimus?", "profile": "economy", "language": "et"}
    )
    body = response.text
    assert "Kokkulepe saavutati eelarve osas" in body
    assert "See on lõplik eestikeelne vastus." in body


# -- 16: no translation/model stage was added --------------------------------


def test_16_no_additional_stage_was_introduced_for_working_language(
    service, fake_orchestrator_state
):
    run_id = service.create_run("Q?", "economy", red_team_enabled=True, language="et")
    record = asyncio.run(service.start_run(run_id))
    assert record.status == "succeeded"
    assert set(fake_orchestrator_state["log"]) == set(ALL_STAGE_NAMES)
    assert len(fake_orchestrator_state["log"]) == len(ALL_STAGE_NAMES)  # each stage ran exactly once


# -- 17: no extra provider call from working-language handling --------------


def test_17_no_extra_provider_call_from_working_language_handling(
    service, fake_orchestrator_state
):
    run_id_en = service.create_run("Q?", "economy", red_team_enabled=False, language="en")
    asyncio.run(service.start_run(run_id_en))
    en_call_count = len(fake_orchestrator_state["log"])

    fake_orchestrator_state["log"].clear()
    run_id_et = service.create_run("Q?", "economy", red_team_enabled=False, language="et")
    asyncio.run(service.start_run(run_id_et))
    et_call_count = len(fake_orchestrator_state["log"])

    assert en_call_count == et_call_count  # identical call count regardless of language split


# -- 18: old run rendering/export remains compatible -------------------------


def test_18_run_predating_working_language_column_renders_and_exports(
    tmp_path, monkeypatch
):
    import sqlite3

    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    db_path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE runs (
            id TEXT PRIMARY KEY, question TEXT NOT NULL, context TEXT,
            profile TEXT NOT NULL, red_team_enabled INTEGER NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT,
            completed_at TEXT, estimated_total_cost_usd REAL NOT NULL DEFAULT 0.0,
            language TEXT NOT NULL DEFAULT 'en'
        );
        CREATE TABLE stages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, name TEXT NOT NULL,
            provider TEXT, model TEXT, status TEXT NOT NULL DEFAULT 'pending',
            attempt INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0, estimated_cost_usd REAL NOT NULL DEFAULT 0.0,
            error TEXT, started_at TEXT, completed_at TEXT
        );
        CREATE TABLE artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, stage_id INTEGER NOT NULL,
            artifact_type TEXT NOT NULL, text_content TEXT NOT NULL, created_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO runs (id, question, context, profile, red_team_enabled, status, "
        "created_at, started_at, completed_at, estimated_total_cost_usd, language) "
        "VALUES ('old-run', 'Old Q?', NULL, 'economy', 0, 'succeeded', "
        "'2025-01-01T00:00:00+00:00', '2025-01-01T00:00:00+00:00', "
        "'2025-01-01T00:01:00+00:00', 0.01, 'en')"
    )
    from llm_deliberation.orchestrator import ALL_STAGE_NAMES as _STAGES

    stage_id = 1
    for name in _STAGES:
        if name == "red_team":
            continue
        conn.execute(
            "INSERT INTO stages (id, run_id, name, provider, model, status, attempt, "
            "input_tokens, output_tokens, estimated_cost_usd, started_at, completed_at) "
            "VALUES (?, 'old-run', ?, 'Fake', 'fake-model', 'succeeded', 1, 10, 20, 0.001, "
            "'2025-01-01T00:00:00+00:00', '2025-01-01T00:00:30+00:00')",
            (stage_id, name),
        )
        text = (
            '{"convergence": "converged", "material_changes": [], "agreements_reached": [], '
            '"unresolved_disagreements": [], "remaining_unknowns": [], "human_judgement_required": []}'
            if name == "convergence_analysis"
            else f"{name}-output"
        )
        conn.execute(
            "INSERT INTO artifacts (run_id, stage_id, artifact_type, text_content, created_at) "
            "VALUES ('old-run', ?, 'response_text', ?, '2025-01-01T00:00:30+00:00')",
            (stage_id, text),
        )
        stage_id += 1
    conn.commit()
    conn.close()

    from fastapi.testclient import TestClient

    from llm_deliberation.service import DeliberationService
    from llm_deliberation.web.app import create_app

    svc = DeliberationService(db_path)
    app = create_app(service=svc)
    with TestClient(app, follow_redirects=True) as test_client:
        detail = test_client.get("/runs/old-run")
        assert detail.status_code == 200
        assert "synthesis-output" in detail.text

        export = test_client.get("/runs/old-run/export")
        assert export.status_code == 200
        assert "synthesis-output" in export.text

    record = svc.get_run("old-run")
    assert record.working_language is None
    result = svc.to_run_result("old-run")
    assert result.synthesis.text == "synthesis-output"


# -- 19: output-language ET export still has Estonian report headings -------


def test_19_estonian_export_keeps_estonian_report_headings(client, fake_orchestrator_state):
    response = client.post(
        "/runs", data={"question": "Küsimus?", "profile": "economy", "language": "et"}
    )
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    export = client.get(f"/runs/{run_id}/export")
    assert export.status_code == 200
    assert "Lõppsüntees" in export.text  # Estonian "Final synthesis" heading
    assert "Küsimus" in export.text  # Estonian "Question" heading


# -- 20: provider/model IDs remain untouched ---------------------------------


def test_20_provider_and_model_ids_remain_untouched(service, fake_orchestrator_state):
    run_id = service.create_run("Q?", "economy", red_team_enabled=False, language="et")
    record = asyncio.run(service.start_run(run_id))
    for stage in record.stages:
        assert stage.provider == "Fake"
        assert stage.model == "fake-model"  # unchanged by which language the stage used


# -- diagnostic utility (Section 12) -----------------------------------------


def test_diagnostic_stage_efficiency_rows_reflect_per_stage_language(
    service, fake_orchestrator_state
):
    from llm_deliberation.diagnostics import format_efficiency_table, stage_efficiency_rows

    run_id = service.create_run("Q?", "economy", red_team_enabled=False, language="et")
    record = asyncio.run(service.start_run(run_id))

    rows = stage_efficiency_rows(record)
    by_stage = {row.stage: row for row in rows}
    assert by_stage["analysis_a"].language_used == "en"
    assert by_stage["synthesis"].language_used == "et"
    assert by_stage["analysis_a"].output_language == "et"
    assert by_stage["analysis_a"].finish_reason == "completed"
    assert by_stage["analysis_a"].input_tokens == 10
    assert by_stage["analysis_a"].output_tokens == 20

    table = format_efficiency_table(rows)
    assert "analysis_a" in table
    assert "en" in table
    assert "completed" in table


def test_diagnostic_table_reflects_truncated_finish_reason(service, fake_orchestrator_state):
    from llm_deliberation.diagnostics import stage_efficiency_rows

    fake_orchestrator_state["responses"] = {"analysis_a": {"incomplete_reason": "output_truncated"}}
    run_id = service.create_run("Q?", "economy", red_team_enabled=False, language="et")
    record = asyncio.run(service.start_run(run_id))
    assert record.status == "failed"

    rows = stage_efficiency_rows(record)
    analysis_a = next(row for row in rows if row.stage == "analysis_a")
    assert analysis_a.finish_reason == "output_truncated"


def test_diagnostic_makes_no_provider_call_and_is_pure(service, fake_orchestrator_state):
    from llm_deliberation.diagnostics import stage_efficiency_rows

    run_id = service.create_run("Q?", "economy", red_team_enabled=False, language="et")
    record = asyncio.run(service.start_run(run_id))
    fake_orchestrator_state["log"].clear()

    stage_efficiency_rows(record)  # no provider call should result from this
    assert fake_orchestrator_state["log"] == []
