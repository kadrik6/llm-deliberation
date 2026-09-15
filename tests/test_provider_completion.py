"""Offline tests for provider-side completion/truncation detection and the
orchestrator's one bounded truncation-recovery retry (reliability pass,
Sections 1-3). No network calls: SDK response objects are stood in for by
minimal plain objects exposing only the fields the classifiers actually
read (see providers._openai_incomplete_reason / _anthropic_incomplete_reason
/ _gemini_incomplete_reason), so these tests never depend on any real SDK
call succeeding or even being importable at call time.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from llm_deliberation import prompts
from llm_deliberation.orchestrator import DeliberationOrchestrator
from llm_deliberation.providers import (
    GeminiFallbackProvider,
    ProviderGenerationError,
    _anthropic_incomplete_reason,
    _gemini_incomplete_reason,
    _openai_incomplete_reason,
)
from llm_deliberation.types import ModelResponse, Usage

# -- 1: whitespace-only output is rejected as unusable, per provider -------


def test_openai_whitespace_only_is_empty_output():
    response = SimpleNamespace(status="completed", incomplete_details=None)
    assert _openai_incomplete_reason(response, "   \n\t  ") == "empty_output"


def test_anthropic_whitespace_only_is_empty_output():
    message = SimpleNamespace(stop_reason="end_turn")
    assert _anthropic_incomplete_reason(message, "") == "empty_output"


def test_gemini_whitespace_only_is_empty_output():
    interaction = SimpleNamespace(status="completed")
    assert _gemini_incomplete_reason(interaction, "  ") == "empty_output"


# -- 2: a normal, complete response succeeds (None == usable) -------------


def test_openai_normal_completed_response_is_usable():
    response = SimpleNamespace(status="completed", incomplete_details=None)
    assert _openai_incomplete_reason(response, "a full answer") is None


def test_anthropic_end_turn_is_usable():
    message = SimpleNamespace(stop_reason="end_turn")
    assert _anthropic_incomplete_reason(message, "a full answer") is None


def test_anthropic_stop_sequence_is_usable_not_an_error():
    # An ordinary successful stop must never be treated as a failure.
    message = SimpleNamespace(stop_reason="stop_sequence")
    assert _anthropic_incomplete_reason(message, "a full answer") is None


def test_gemini_completed_status_is_usable():
    interaction = SimpleNamespace(status="completed")
    assert _gemini_incomplete_reason(interaction, "a full answer") is None


# -- 3: max-output-token / truncated responses are not normal success -----


def test_openai_incomplete_status_is_truncated():
    response = SimpleNamespace(
        status="incomplete",
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
    )
    assert _openai_incomplete_reason(response, "partial text that got cut") == "output_truncated"


def test_anthropic_max_tokens_is_truncated():
    message = SimpleNamespace(stop_reason="max_tokens")
    assert _anthropic_incomplete_reason(message, "partial text that got cut") == "output_truncated"


def test_anthropic_context_window_exceeded_is_truncated():
    message = SimpleNamespace(stop_reason="model_context_window_exceeded")
    assert _anthropic_incomplete_reason(message, "partial text") == "output_truncated"


@pytest.mark.parametrize("status", ["incomplete", "budget_exceeded"])
def test_gemini_incomplete_statuses_are_truncated(status):
    interaction = SimpleNamespace(status=status)
    assert _gemini_incomplete_reason(interaction, "partial text that got cut") == "output_truncated"


# -- 4: a paid truncated response preserves cost ---------------------------


class _FixedProvider:
    """Stands in for OpenAIProvider/AnthropicProvider: returns one canned
    ModelResponse per call, consumed in order."""

    def __init__(self, responses: list[ModelResponse]):
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def generate(self, *, system: str, prompt: str) -> ModelResponse:
        self.calls.append((system, prompt))
        if not self._responses:
            raise AssertionError("provider called more times than expected")
        return self._responses.pop(0)


def _truncated_response(cost: float = 0.01) -> ModelResponse:
    return ModelResponse(
        provider="OpenAI",
        model="gpt-5.6-sol",
        text="this got cut off before finishing",
        usage=Usage(input_tokens=50, output_tokens=5000),
        estimated_cost_usd=cost,
        requested_model="gpt-5.6-sol",
        incomplete_reason="output_truncated",
    )


def _complete_response(cost: float = 0.02) -> ModelResponse:
    return ModelResponse(
        provider="OpenAI",
        model="gpt-5.6-sol",
        text="a complete, concise answer",
        usage=Usage(input_tokens=50, output_tokens=200),
        estimated_cost_usd=cost,
        requested_model="gpt-5.6-sol",
        incomplete_reason=None,
    )


def _orchestrator_with_provider(monkeypatch, provider) -> DeliberationOrchestrator:
    """A DeliberationOrchestrator whose .a provider is swapped for a fake,
    without touching __init__ (which would try to build real SDK clients)."""
    monkeypatch.setattr(
        DeliberationOrchestrator, "__init__", lambda self, settings: None
    )
    orch = DeliberationOrchestrator(settings=None)
    orch.a = provider
    return orch


def test_paid_truncated_response_without_recovery_keeps_its_cost(monkeypatch):
    # Simulates a stage where recovery itself is not exercised (e.g. because
    # the caller only wants to confirm the failed attempt's cost survives) --
    # here, both calls are truncated, so the final ModelResponse must carry
    # the summed cost of both attempts, never just the last one.
    provider = _FixedProvider([_truncated_response(0.01), _truncated_response(0.015)])
    orch = _orchestrator_with_provider(monkeypatch, provider)

    import asyncio

    response = asyncio.run(
        orch.run_stage("analysis_a", "Q?", {}, language="en")
    )

    assert response.incomplete_reason == "output_truncated"
    assert response.estimated_cost_usd == pytest.approx(0.01 + 0.015)
    assert len(provider.calls) == 2


# -- 5: bounded recovery succeeds when the second response is complete ----


def test_recovery_retry_succeeds_and_preserves_first_attempt_cost(monkeypatch):
    provider = _FixedProvider([_truncated_response(0.01), _complete_response(0.02)])
    orch = _orchestrator_with_provider(monkeypatch, provider)

    import asyncio

    response = asyncio.run(orch.run_stage("analysis_a", "Q?", {}, language="en"))

    assert response.incomplete_reason is None
    assert response.text == "a complete, concise answer"
    # Sunk cost of the discarded truncated attempt is added to the
    # successful retry's cost, never dropped.
    assert response.estimated_cost_usd == pytest.approx(0.01 + 0.02)
    assert len(provider.calls) == 2
    # The recovery call's system prompt carries the concise-retry
    # instruction, distinct from the first call's system prompt.
    first_system, _ = provider.calls[0]
    second_system, _ = provider.calls[1]
    assert first_system != second_system
    assert prompts.truncation_recovery_instruction("en") in second_system

    # Provenance: exactly 2 real attempts, distinguishable initial/recovery
    # phases -- never collapsed into a misleading "Attempts (this try): 1"
    # (see the follow-up reliability investigation, Section 3/4).
    assert response.model_attempts == 2
    assert response.attempt_log == [
        {"phase": "initial", "model": "gpt-5.6-sol", "outcome": "output_truncated", "estimated_cost_usd": 0.01},
        {"phase": "recovery", "model": "gpt-5.6-sol", "outcome": "succeeded", "estimated_cost_usd": 0.02},
    ]


# -- 6: a second truncation results in a failed (still-incomplete) stage --


def test_recovery_retry_still_truncated_stays_incomplete(monkeypatch):
    provider = _FixedProvider([_truncated_response(0.01), _truncated_response(0.02)])
    orch = _orchestrator_with_provider(monkeypatch, provider)

    import asyncio

    response = asyncio.run(orch.run_stage("analysis_a", "Q?", {}, language="en"))

    assert response.incomplete_reason == "output_truncated"
    assert response.estimated_cost_usd == pytest.approx(0.01 + 0.02)
    assert len(provider.calls) == 2  # never more than the one bounded retry
    assert response.model_attempts == 2
    assert response.attempt_log == [
        {"phase": "initial", "model": "gpt-5.6-sol", "outcome": "output_truncated", "estimated_cost_usd": 0.01},
        {"phase": "recovery", "model": "gpt-5.6-sol", "outcome": "output_truncated", "estimated_cost_usd": 0.02},
    ]


def test_recovery_retry_that_raises_preserves_sunk_cost(monkeypatch):
    class RaisingSecondCall(_FixedProvider):
        def generate(self, *, system, prompt):
            self.calls.append((system, prompt))
            if len(self.calls) == 1:
                return _truncated_response(0.01)
            raise RuntimeError("transport error on retry")

    provider = RaisingSecondCall([])
    orch = _orchestrator_with_provider(monkeypatch, provider)

    import asyncio

    with pytest.raises(ProviderGenerationError) as excinfo:
        asyncio.run(orch.run_stage("analysis_a", "Q?", {}, language="en"))

    assert excinfo.value.estimated_cost_usd == pytest.approx(0.01)
    assert excinfo.value.reason == "output_truncated"
    assert excinfo.value.attempts == 2
    assert excinfo.value.attempt_log[0] == {
        "phase": "initial", "model": "gpt-5.6-sol", "outcome": "output_truncated", "estimated_cost_usd": 0.01,
    }
    assert excinfo.value.attempt_log[1]["phase"] == "recovery"
    assert excinfo.value.attempt_log[1]["outcome"] == "provider_error"


# -- 7: recovery never loops indefinitely ----------------------------------


def test_recovery_is_exactly_one_attempt_even_if_always_truncated(monkeypatch):
    # An unlimited supply of truncated responses would raise
    # AssertionError("provider called more times than expected") from
    # _FixedProvider once it runs past 2 calls -- proving the recovery loop
    # really does stop at one retry, not iterate until success.
    provider = _FixedProvider([_truncated_response(), _truncated_response()])
    orch = _orchestrator_with_provider(monkeypatch, provider)

    import asyncio

    response = asyncio.run(orch.run_stage("analysis_a", "Q?", {}, language="en"))
    assert response.incomplete_reason == "output_truncated"
    assert len(provider.calls) == 2


def test_empty_output_is_never_auto_retried(monkeypatch):
    empty_response = ModelResponse(
        provider="OpenAI",
        model="gpt-5.6-sol",
        text="",
        usage=Usage(input_tokens=50, output_tokens=0),
        estimated_cost_usd=0.001,
        requested_model="gpt-5.6-sol",
        incomplete_reason="empty_output",
    )
    provider = _FixedProvider([empty_response])
    orch = _orchestrator_with_provider(monkeypatch, provider)

    import asyncio

    response = asyncio.run(orch.run_stage("analysis_a", "Q?", {}, language="en"))
    assert response.incomplete_reason == "empty_output"
    assert len(provider.calls) == 1  # no automatic recovery for empty_output


def test_recovery_is_skipped_for_gemini_fallback_provider(monkeypatch):
    """GeminiFallbackProvider absorbs truncation into its own retry/fallback
    budget (see test_gemini_fallback.py) -- the orchestrator must not add a
    second, separate recovery layer on top of it for red_team."""
    from tests.test_gemini_fallback import FakeSingleModelProvider, make_response

    truncated = ModelResponse(
        provider="Google",
        model="gemini-3.8-flash",
        text="cut off",
        usage=Usage(input_tokens=10, output_tokens=10),
        estimated_cost_usd=0.001,
        requested_model="gemini-3.8-flash",
        incomplete_reason="output_truncated",
    )
    fake = FakeSingleModelProvider("gemini-3.8-flash", [truncated, make_response("gemini-3.8-flash")])
    red = GeminiFallbackProvider(
        models=["gemini-3.8-flash"],
        max_output_tokens=1000,
        sleep_fn=lambda s: None,
        provider_factory=lambda m: fake,
    )

    monkeypatch.setattr(DeliberationOrchestrator, "__init__", lambda self, settings: None)
    orch = DeliberationOrchestrator(settings=None)
    orch.red = red

    import asyncio

    response = asyncio.run(orch.run_stage("red_team", "Q?", {"analysis_a": "a", "analysis_b": "b"}, language="en"))
    assert response.incomplete_reason is None
    assert fake.calls == 2  # handled entirely inside GeminiFallbackProvider's own loop


# -- bounded provider timeout/retries (Section 6: the 38-minute investigation)
# openai 3.13.0 and anthropic 1.5.0 both default to a 600s read timeout with
# up to 2 automatic SDK-level retries (openai._constants.DEFAULT_TIMEOUT /
# DEFAULT_MAX_RETRIES and the anthropic equivalents) -- confirmed by direct
# inspection, not assumed. Without an explicit override, a single stalled
# call is effectively unbounded from this application's perspective, and
# the one-shot truncation recovery above can issue a second such call on
# top of that. These tests confirm the client is always constructed with an
# explicit, deterministic bound instead of silently inheriting the SDK
# default -- no real network call is made in any of them.


def test_openai_provider_sets_an_explicit_timeout_and_max_retries(monkeypatch):
    from llm_deliberation.providers import OpenAIProvider

    captured = {}

    class FakeUsage:
        input_tokens = 1
        output_tokens = 1

    class FakeResponse:
        status = "completed"
        output_text = "ok"
        usage = FakeUsage()

    class FakeResponses:
        def create(self, **kwargs):
            return FakeResponse()

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.responses = FakeResponses()

    monkeypatch.setattr("openai.OpenAI", FakeClient)
    provider = OpenAIProvider("gpt-5.6-sol", 5000, timeout_seconds=42.0, max_retries=3)
    provider.generate(system="s", prompt="p")

    assert captured["timeout"] == 42.0
    assert captured["max_retries"] == 3


def test_openai_provider_defaults_are_bounded_not_the_sdk_default(monkeypatch):
    from llm_deliberation.providers import (
        DEFAULT_PROVIDER_MAX_RETRIES,
        DEFAULT_PROVIDER_TIMEOUT_SECONDS,
        OpenAIProvider,
    )

    captured = {}

    class FakeUsage:
        input_tokens = 1
        output_tokens = 1

    class FakeResponse:
        status = "completed"
        output_text = "ok"
        usage = FakeUsage()

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.responses = type("R", (), {"create": lambda self, **kw: FakeResponse()})()

    monkeypatch.setattr("openai.OpenAI", FakeClient)
    OpenAIProvider("gpt-5.6-sol", 5000).generate(system="s", prompt="p")

    assert captured["timeout"] == DEFAULT_PROVIDER_TIMEOUT_SECONDS
    assert captured["max_retries"] == DEFAULT_PROVIDER_MAX_RETRIES
    # Deterministic bound, well under the SDK's own 600s/2-retries default.
    assert DEFAULT_PROVIDER_TIMEOUT_SECONDS < 600
    assert DEFAULT_PROVIDER_MAX_RETRIES <= 2


def test_anthropic_provider_sets_an_explicit_timeout_and_max_retries(monkeypatch):
    from llm_deliberation.providers import AnthropicProvider

    captured = {}

    class FakeBlock:
        type = "text"
        text = "ok"

    class FakeUsage:
        input_tokens = 1
        output_tokens = 1

    class FakeMessage:
        content = [FakeBlock()]
        stop_reason = "end_turn"
        usage = FakeUsage()

    class FakeMessages:
        def create(self, **kwargs):
            return FakeMessage()

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.messages = FakeMessages()

    monkeypatch.setattr("anthropic.Anthropic", FakeClient)
    provider = AnthropicProvider("claude-opus-5", 5000, timeout_seconds=42.0, max_retries=3)
    provider.generate(system="s", prompt="p")

    assert captured["timeout"] == 42.0
    assert captured["max_retries"] == 3


def test_gemini_provider_sets_an_explicit_http_timeout(monkeypatch):
    from llm_deliberation.providers import GeminiProvider

    captured = {}

    class FakeUsage:
        total_input_tokens = 1
        total_output_tokens = 1
        total_thought_tokens = 0

    class FakeInteraction:
        status = "completed"
        output_text = "ok"
        usage = FakeUsage()

    class FakeInteractions:
        def create(self, **kwargs):
            return FakeInteraction()

    class FakeClient:
        def __init__(self, http_options=None, **kwargs):
            captured.update(http_options or {})
            self.interactions = FakeInteractions()

    import google.genai

    monkeypatch.setattr(google.genai, "Client", FakeClient)
    provider = GeminiProvider("gemini-3.8-flash", 5000, timeout_seconds=30.0)
    provider.generate(system="s", prompt="p")

    assert captured["timeout"] == 30000  # milliseconds


def test_settings_env_vars_override_provider_timeout_and_retries(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "90")
    monkeypatch.setenv("PROVIDER_MAX_RETRIES", "0")
    from llm_deliberation.config import Settings

    settings = Settings.load(profile_override="economy")
    assert settings.provider_timeout_seconds == 90.0
    assert settings.provider_max_retries == 0


# -- provenance distinguishes initial generation from recovery -------------
# (presenter.attempt_log_phases -- Section 4/11)


def test_attempt_log_phases_recognizes_a_recovery_log():
    from llm_deliberation.web.presenter import attempt_log_phases

    log = [
        {"phase": "initial", "model": "claude-opus-5", "outcome": "output_truncated", "estimated_cost_usd": 0.01},
        {"phase": "recovery", "model": "claude-opus-5", "outcome": "succeeded", "estimated_cost_usd": 0.02},
    ]
    assert attempt_log_phases(log) == log


def test_attempt_log_phases_returns_none_for_gemini_style_log():
    from llm_deliberation.web.presenter import attempt_log_phases

    gemini_log = [{"model": "gemini-3.8-flash", "attempt_number": 1, "outcome": "succeeded"}]
    assert attempt_log_phases(gemini_log) is None


def test_attempt_log_phases_returns_none_for_empty_or_missing_log():
    from llm_deliberation.web.presenter import attempt_log_phases

    assert attempt_log_phases(None) is None
    assert attempt_log_phases([]) is None


def test_group_attempts_by_model_ignores_phase_based_logs():
    from llm_deliberation.web.presenter import group_attempts_by_model

    log = [
        {"phase": "initial", "model": "claude-opus-5", "outcome": "output_truncated", "estimated_cost_usd": 0.01},
        {"phase": "recovery", "model": "claude-opus-5", "outcome": "succeeded", "estimated_cost_usd": 0.02},
    ]
    # Grouping by model would collapse both phases into one misleading
    # "2 attempts" group -- the dedicated phase-based template rendering
    # must be used instead (see partials/pipeline.html / result.html).
    assert group_attempts_by_model(log) == []
