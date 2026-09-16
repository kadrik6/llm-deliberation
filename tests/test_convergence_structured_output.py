"""Offline regression tests for convergence_analysis's structured-output
reliability fix: Anthropic native structured output (output_config.format)
as the primary reliability boundary, correct output_truncated vs
structured_output_invalid classification, bounded raw-text provenance, and
the friendly main-UI failure message.

No network calls: AnthropicProvider's SDK client is always monkeypatched
(the same pattern test_provider_completion.py already uses), and
higher-level tests use the FakeOrchestrator-backed `client`/`service`
fixtures. Numbered comments correspond to the task brief's Section 12
checklist.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from llm_deliberation import convergence
from llm_deliberation.config import Settings
from llm_deliberation.convergence import ConvergenceAnalysis, ConvergenceParseError, parse_convergence_analysis
from llm_deliberation.orchestrator import (
    DeliberationOrchestrator,
    _anthropic_convergence_schema,
    _build_convergence_provider,
    _finalize_convergence_response,
)
from llm_deliberation.providers import AnthropicProvider, ProviderGenerationError
from llm_deliberation.types import ModelResponse, Usage

VALID_PAYLOAD = {
    "convergence": "partial",
    "material_changes": [],
    "agreements_reached": [],
    "unresolved_disagreements": [],
    "remaining_unknowns": [],
    "human_judgement_required": [],
}


def _submit(client, *, question="Q?", profile="economy", red_team=False, context="", language="en"):
    data = {"question": question, "profile": profile, "context": context, "language": language}
    if red_team:
        data["red_team"] = "1"
    return client.post("/runs", data=data)


# -- 1/2: Anthropic uses native structured output; schema converts correctly -


def test_1_build_convergence_provider_sets_response_schema_for_anthropic():
    settings = Settings.load(profile_override="economy", red_team_override=False)
    settings.convergence_provider = "anthropic"
    settings.convergence_model = "claude-opus-5"
    provider = _build_convergence_provider(settings)
    assert isinstance(provider, AnthropicProvider)
    assert provider.response_schema is not None


def test_2_anthropic_schema_is_converted_correctly():
    schema = _anthropic_convergence_schema()
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["convergence"]
    assert set(schema["properties"].keys()) == {
        "convergence", "material_changes", "agreements_reached",
        "unresolved_disagreements", "remaining_unknowns", "human_judgement_required",
    }
    # Nested models get the same strict treatment.
    assert schema["$defs"]["Agreement"]["additionalProperties"] is False


def test_2_schema_conversion_makes_no_network_call(monkeypatch):
    # Purely local Pydantic/SDK introspection -- guards against a future SDK
    # change accidentally making this a live call.
    called = {"anthropic_client_constructed": False}

    class _Guard:
        def __init__(self, *a, **kw):
            called["anthropic_client_constructed"] = True

    monkeypatch.setattr("anthropic.Anthropic", _Guard)
    _anthropic_convergence_schema()
    assert called["anthropic_client_constructed"] is False


# -- 3: valid provider structured output becomes ConvergenceAnalysis --------


class _FakeTextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FakeUsage:
    def __init__(self, input_tokens=100, output_tokens=200):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeMessage:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_FakeTextBlock(text)]
        self.stop_reason = stop_reason
        self.usage = _FakeUsage()


def _install_fake_anthropic_client(monkeypatch, message: _FakeMessage, captured: dict):
    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return message

    class FakeClient:
        def __init__(self, **kwargs):
            self.messages = FakeMessages()

    monkeypatch.setattr("anthropic.Anthropic", FakeClient)


def test_3_valid_structured_output_becomes_convergence_analysis(monkeypatch):
    captured = {}
    _install_fake_anthropic_client(monkeypatch, _FakeMessage(json.dumps(VALID_PAYLOAD)), captured)

    provider = AnthropicProvider(
        "claude-opus-5", 5000, response_schema=_anthropic_convergence_schema()
    )
    response = provider.generate(system="s", prompt="p")

    # The request actually included native structured output.
    assert captured["output_config"]["format"]["type"] == "json_schema"
    assert captured["output_config"]["format"]["schema"]["type"] == "object"

    analysis = parse_convergence_analysis(response.text)
    assert analysis.convergence == "partial"


def test_3_no_response_schema_omits_structured_output_request(monkeypatch):
    captured = {}
    _install_fake_anthropic_client(monkeypatch, _FakeMessage("free-form prose"), captured)

    AnthropicProvider("claude-opus-5", 5000).generate(system="s", prompt="p")  # no response_schema

    assert "format" not in captured["output_config"]


# -- 4/5: working-language interaction (schema canonical, text may vary) ----


def test_4_et_run_keeps_schema_canonical_with_estonian_text_values():
    payload = {
        **VALID_PAYLOAD,
        "agreements_reached": [{"topic": "Eelarve", "shared_position": "Kokkulepe saavutati"}],
    }
    analysis = parse_convergence_analysis(json.dumps(payload))
    assert analysis.convergence == "partial"  # canonical English enum, unchanged
    assert analysis.agreements_reached[0].shared_position == "Kokkulepe saavutati"


def test_5_en_run_produces_english_text_values():
    payload = {
        **VALID_PAYLOAD,
        "agreements_reached": [{"topic": "Budget", "shared_position": "Agreement was reached"}],
    }
    analysis = parse_convergence_analysis(json.dumps(payload))
    assert analysis.agreements_reached[0].shared_position == "Agreement was reached"


# -- 6: truncated response -> output_truncated, never a JSON validation error -


def _build_bare_orchestrator(provider: AnthropicProvider) -> DeliberationOrchestrator:
    orch = DeliberationOrchestrator.__new__(DeliberationOrchestrator)
    orch.convergence = provider
    return orch


def test_6_truncated_response_is_output_truncated_not_structured_output_invalid():
    class _TruncatingConvergenceProvider:
        provider_name = "Anthropic"
        model = "claude-opus-5"
        max_output_tokens = 5000

        def __init__(self):
            self.calls = 0

        def generate(self, *, system, prompt):
            self.calls += 1
            return ModelResponse(
                provider="Anthropic", model=self.model,
                text='{"convergence": "partial", "material_changes": [{"candidate": "A", "before": "cut off mid-strin',
                usage=Usage(input_tokens=14709, output_tokens=5000), estimated_cost_usd=0.07,
                requested_model=self.model, incomplete_reason="output_truncated",
            )

    provider = _TruncatingConvergenceProvider()
    orch = _build_bare_orchestrator(provider)
    texts = {
        "analysis_a": "a", "analysis_b": "b", "critique_a_of_b": "c", "critique_b_of_a": "d",
        "revision_a": "e", "revision_b": "f",
    }
    response = asyncio.run(orch.run_stage("convergence_analysis", "Q?", texts))

    # _finalize_convergence_response must NOT have been invoked -- the
    # response comes back exactly as the provider returned it (still
    # incomplete, still the truncated raw text), never re-classified as a
    # JSON/schema validation failure. The one bounded truncation-recovery
    # retry (see orchestrator.run_stage) still applies to convergence_analysis
    # like any other stage -- our stub always returns truncated, so both the
    # initial attempt and that one recovery attempt happen (2 calls), and the
    # *still*-truncated recovery response is what must not reach the parser
    # -- this is exactly the bug this fix closes.
    assert response.incomplete_reason == "output_truncated"
    assert response.text.startswith('{"convergence"')  # untouched, not canonical_json()'d
    assert provider.calls == 2


# -- 7: complete but schema-invalid response -> structured_output_invalid ---


def test_7_complete_schema_invalid_response_is_structured_output_invalid():
    response = ModelResponse(
        provider="Anthropic", model="claude-opus-5", text="not json at all",
        usage=Usage(input_tokens=100, output_tokens=50), estimated_cost_usd=0.02,
        requested_model="claude-opus-5", incomplete_reason=None,
    )
    with pytest.raises(ProviderGenerationError) as excinfo:
        _finalize_convergence_response(response)
    assert excinfo.value.reason == "structured_output_invalid"


# -- 8/9: cost and model provenance survive a structured-output failure -----


def test_8_paid_cost_survives_structured_validation_failure():
    response = ModelResponse(
        provider="Anthropic", model="claude-opus-5", text="{broken",
        usage=Usage(input_tokens=14709, output_tokens=4109), estimated_cost_usd=0.0705,
        requested_model="claude-opus-5",
    )
    with pytest.raises(ProviderGenerationError) as excinfo:
        _finalize_convergence_response(response)
    assert excinfo.value.estimated_cost_usd == pytest.approx(0.0705)


def test_9_requested_and_used_model_provenance_survives_failure():
    response = ModelResponse(
        provider="Anthropic", model="claude-opus-5", text="{broken",
        usage=Usage(input_tokens=100, output_tokens=50), estimated_cost_usd=0.01,
        requested_model="claude-opus-5",
    )
    with pytest.raises(ProviderGenerationError) as excinfo:
        _finalize_convergence_response(response)
    assert excinfo.value.requested_model == "claude-opus-5"


# -- 10/11: no automatic repair loop; malformed JSON never silently succeeds -


def test_10_no_automatic_repair_retry_on_schema_failure():
    class _OnceProvider:
        provider_name = "Anthropic"
        model = "claude-opus-5"
        max_output_tokens = 5000

        def __init__(self):
            self.calls = 0

        def generate(self, *, system, prompt):
            self.calls += 1
            return ModelResponse(
                provider="Anthropic", model=self.model, text="not valid json",
                usage=Usage(input_tokens=100, output_tokens=50), estimated_cost_usd=0.01,
                requested_model=self.model, incomplete_reason=None,
            )

    provider = _OnceProvider()
    orch = _build_bare_orchestrator(provider)
    texts = {
        "analysis_a": "a", "analysis_b": "b", "critique_a_of_b": "c", "critique_b_of_a": "d",
        "revision_a": "e", "revision_b": "f",
    }
    with pytest.raises(ProviderGenerationError):
        asyncio.run(orch.run_stage("convergence_analysis", "Q?", texts))

    assert provider.calls == 1  # exactly one call -- no "try JSON again" retry


def test_11_malformed_json_cannot_silently_become_a_successful_artifact():
    with pytest.raises(ConvergenceParseError):
        parse_convergence_analysis("this is not json")


# -- 12/13: prompt instructs concise, non-reproducing field values ----------


def test_12_convergence_prompt_instructs_conciseness():
    from llm_deliberation import prompts

    text = prompts.convergence_analysis("Q?", "a", "b", "c", "d", None, "e", "f")
    lowered = text.lower()
    assert "concise" in lowered
    assert "one or two sentences" in lowered


def test_13_convergence_prompt_instructs_against_reproducing_source_material():
    from llm_deliberation import prompts

    text = prompts.convergence_analysis("Q?", "a", "b", "c", "d", None, "e", "f")
    assert "reproducing" in text.lower() or "quoting" in text.lower()


# -- 14/15: successful/skipped convergence interacts correctly with synthesis -


def test_14_successful_structured_convergence_text_is_usable_by_synthesis():
    from llm_deliberation import convergence as conv_module

    response = ModelResponse(
        provider="Anthropic", model="claude-opus-5", text=json.dumps(VALID_PAYLOAD),
        usage=Usage(input_tokens=100, output_tokens=50), estimated_cost_usd=0.01,
        requested_model="claude-opus-5", incomplete_reason=None,
    )
    finalized = _finalize_convergence_response(response)
    # The canonical (pretty-printed, schema-valid) JSON is what gets stored
    # and later fed into synthesis's prompt via convergence.render_for_prompt.
    analysis = conv_module.parse_convergence_analysis(finalized.text)
    rendered = conv_module.render_for_prompt(analysis)
    assert "Convergence assessment: partial" in rendered


def test_15_skipped_convergence_still_allows_synthesis(client, fake_orchestrator_state):
    fake_orchestrator_state["fail"] = {"convergence_analysis"}
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    skip_response = client.post(f"/runs/{run_id}/stages/convergence_analysis/skip")
    assert skip_response.status_code == 200
    body = client.get(f"/runs/{run_id}").text
    assert "Final answer" in body  # synthesis still ran and completed


# -- 16/17: legacy compatibility ---------------------------------------------


def test_16_legacy_convergence_json_with_material_boolean_still_renders():
    legacy = json.dumps(
        {
            "convergence": "converged",
            "material_changes": [
                {"candidate": "A", "before": "x", "after": "y", "material": True, "triggers": []}
            ],
            "agreements_reached": [], "unresolved_disagreements": [],
            "remaining_unknowns": [], "human_judgement_required": [],
        }
    )
    analysis = parse_convergence_analysis(legacy)
    assert analysis.material_changes[0].change_status == "material"
    assert analysis.material_changes[0].material is True


def test_17_historical_run_with_legacy_convergence_artifact_is_untouched(tmp_path):
    import sqlite3

    from llm_deliberation.service import DeliberationService

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
    legacy_json = json.dumps(
        {
            "convergence": "converged",
            "material_changes": [{"candidate": "A", "before": "x", "after": "y", "material": True}],
            "agreements_reached": [], "unresolved_disagreements": [],
            "remaining_unknowns": [], "human_judgement_required": [],
        }
    )
    conn.execute(
        "INSERT INTO runs (id, question, context, profile, red_team_enabled, status, "
        "created_at, started_at, completed_at, estimated_total_cost_usd, language) "
        "VALUES ('old-run', 'Old Q?', NULL, 'economy', 0, 'succeeded', "
        "'2025-01-01T00:00:00+00:00', '2025-01-01T00:00:00+00:00', "
        "'2025-01-01T00:01:00+00:00', 0.01, 'en')"
    )
    from llm_deliberation.orchestrator import ALL_STAGE_NAMES

    stage_id = 1
    for name in ALL_STAGE_NAMES:
        if name == "red_team":
            continue
        conn.execute(
            "INSERT INTO stages (id, run_id, name, provider, model, status, attempt, "
            "input_tokens, output_tokens, estimated_cost_usd, started_at, completed_at) "
            "VALUES (?, 'old-run', ?, 'Anthropic', 'claude-opus-5', 'succeeded', 1, 10, 20, 0.001, "
            "'2025-01-01T00:00:00+00:00', '2025-01-01T00:00:30+00:00')",
            (stage_id, name),
        )
        text = legacy_json if name == "convergence_analysis" else f"{name}-output"
        conn.execute(
            "INSERT INTO artifacts (run_id, stage_id, artifact_type, text_content, created_at) "
            "VALUES ('old-run', ?, 'response_text', ?, '2025-01-01T00:00:30+00:00')",
            (stage_id, text),
        )
        stage_id += 1
    conn.commit()
    conn.close()

    svc = DeliberationService(db_path)
    result = svc.to_run_result("old-run")
    assert result.convergence.text == legacy_json  # byte-for-byte unchanged
    evolution = parse_convergence_analysis(result.convergence.text)
    assert evolution.material_changes[0].change_status == "material"


# -- 18/19: main UI message + provenance -------------------------------------


def test_18_main_ui_uses_human_readable_failure_message(client, fake_orchestrator_state):
    from llm_deliberation.providers import ProviderGenerationError

    fake_orchestrator_state["fail_with"] = {
        "convergence_analysis": ProviderGenerationError(
            "convergence_analysis response failed validation: Response was not valid JSON: "
            "Unterminated string starting at line 1 column 14896",
            requested_model="claude-opus-5",
            attempts=1,
            attempt_log=[
                {
                    "model": "claude-opus-5", "attempt_number": 1, "outcome": "failed",
                    "delay_before_seconds": 0.0, "http_code": None,
                    "error": '{"convergence": "partial", "material_changes": [{"candidate": "A"'
                             ' truncated raw json excerpt here...',
                    "reason": "response did not match the expected schema",
                    "estimated_cost_usd": 0.0705,
                }
            ],
            fallback_used=False, fallback_reason=None,
            estimated_cost_usd=0.0705,
            reason="structured_output_invalid",
        )
    }
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    body = client.get(f"/runs/{run_id}").text

    assert "Convergence analysis could not produce a valid structured result." in body
    assert "Your completed analyses and revisions are preserved." in body
    assert "Unterminated string starting at" not in body
    assert "14896" not in body


def test_19_raw_technical_error_remains_in_provenance(client, service, fake_orchestrator_state):
    from llm_deliberation.providers import ProviderGenerationError

    raw_excerpt = '{"convergence": "partial", "material_changes": [{"candidate": "A", "before": "unterminated...'
    fake_orchestrator_state["fail_with"] = {
        "convergence_analysis": ProviderGenerationError(
            "convergence_analysis response failed validation: Response was not valid JSON",
            requested_model="claude-opus-5",
            attempts=1,
            attempt_log=[
                {
                    "model": "claude-opus-5", "attempt_number": 1, "outcome": "failed",
                    "delay_before_seconds": 0.0, "http_code": None,
                    "error": raw_excerpt,
                    "reason": "response did not match the expected schema",
                    "estimated_cost_usd": 0.0705,
                }
            ],
            fallback_used=False, fallback_reason=None,
            estimated_cost_usd=0.0705,
            reason="structured_output_invalid",
        )
    }
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    record = service.get_run(run_id)
    stage = next(s for s in record.stages if s.name == "convergence_analysis")
    assert stage.attempt_log[0]["error"] == raw_excerpt
    assert stage.estimated_cost_usd == pytest.approx(0.0705)


def test_19_et_message_renders(client, fake_orchestrator_state):
    from llm_deliberation.providers import ProviderGenerationError

    client.get("/ui-language/et", follow_redirects=True)
    fake_orchestrator_state["fail_with"] = {
        "convergence_analysis": ProviderGenerationError(
            "technical",
            requested_model="claude-opus-5", attempts=1, attempt_log=[],
            fallback_used=False, fallback_reason=None, estimated_cost_usd=0.01,
            reason="structured_output_invalid",
        )
    }
    response = _submit(client, question="Q?", red_team=False, language="et")
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    body = client.get(f"/runs/{run_id}").text
    assert "Konsensuse analüüs ei suutnud luua korrektset struktureeritud tulemust." in body
    assert "Sinu valminud analüüsid ja täiendused on säilinud." in body


# -- 20: no new deliberation stage or translation call ------------------------


def test_20_no_new_deliberation_stage_introduced():
    from llm_deliberation.orchestrator import ALL_STAGE_NAMES

    assert ALL_STAGE_NAMES == (
        "analysis_a", "analysis_b", "critique_a_of_b", "critique_b_of_a", "red_team",
        "revision_a", "revision_b", "convergence_analysis", "synthesis",
    )


def test_20_structured_output_wiring_adds_no_extra_provider_call(client, fake_orchestrator_state):
    from llm_deliberation.orchestrator import ALL_STAGE_NAMES

    response = _submit(client, question="Q?", red_team=True)
    assert response.status_code == 200
    assert len(fake_orchestrator_state["log"]) == len(ALL_STAGE_NAMES)
    assert set(fake_orchestrator_state["log"]) == set(ALL_STAGE_NAMES)
