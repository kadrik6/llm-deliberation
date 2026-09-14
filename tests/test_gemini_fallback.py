"""Offline tests for the Gemini red-team model fallback chain.

No network calls: each fake per-model provider is a plain Python object
whose .generate() either raises a canned google.genai error or returns a
canned ModelResponse. Real sleeping is stubbed out via sleep_fn so these
tests run instantly regardless of the configured backoff delays.
"""

from __future__ import annotations

import pytest
from google.genai import errors as genai_errors

from llm_deliberation.providers import (
    GeminiFallbackProvider,
    ProviderGenerationError,
    classify_gemini_error,
)
from llm_deliberation.types import ModelResponse, Usage


def server_error(code: int = 503, message: str = "model is overloaded") -> genai_errors.ServerError:
    return genai_errors.ServerError(code, {"message": message, "status": "UNAVAILABLE"}, None)


def client_error(code: int = 401, message: str = "invalid API key") -> genai_errors.ClientError:
    return genai_errors.ClientError(code, {"message": message, "status": "UNAUTHENTICATED"}, None)


class FakeSingleModelProvider:
    """Stands in for a real GeminiProvider bound to one model.

    `behaviors` is a list of either an Exception instance to raise or a
    ModelResponse to return, consumed one per call. Calling past the end of
    the list raises AssertionError, catching tests that expect fewer calls
    than actually happen.
    """

    def __init__(self, model: str, behaviors: list):
        self.model = model
        self._behaviors = list(behaviors)
        self.calls = 0

    def generate(self, *, system: str, prompt: str) -> ModelResponse:
        self.calls += 1
        if not self._behaviors:
            raise AssertionError(f"{self.model} called more times than expected")
        behavior = self._behaviors.pop(0)
        if isinstance(behavior, BaseException):
            raise behavior
        return behavior


def make_response(model: str, cost: float = 0.01) -> ModelResponse:
    return ModelResponse(
        provider="Google",
        model=model,
        text=f"red-team output from {model}",
        usage=Usage(input_tokens=100, output_tokens=200),
        estimated_cost_usd=cost,
        requested_model=model,
    )


def build_provider(fakes: dict[str, FakeSingleModelProvider], **kwargs) -> GeminiFallbackProvider:
    sleeps: list[float] = kwargs.pop("sleeps", None)
    if sleeps is None:
        sleeps = []
    return GeminiFallbackProvider(
        models=list(fakes.keys()),
        max_output_tokens=1000,
        sleep_fn=lambda s: sleeps.append(s),
        jitter_fn=lambda: 0.5,  # deterministic: _jittered_delay -> base * 1.0
        provider_factory=lambda m: fakes[m],
        **kwargs,
    )


# -- classification -----------------------------------------------------


def test_classify_server_error_is_transient():
    is_transient, reason = classify_gemini_error(server_error(503))
    assert is_transient
    assert "503" in reason


@pytest.mark.parametrize("code", [400, 401, 402, 403, 404, 429])
def test_classify_client_error_is_never_transient(code):
    is_transient, _ = classify_gemini_error(client_error(code, "bad request"))
    assert not is_transient


# -- provider fallback behavior ------------------------------------------


def test_preferred_model_succeeds_no_fallback():
    fakes = {"gemini-3.8-flash": FakeSingleModelProvider("gemini-3.8-flash", [make_response("gemini-3.8-flash")])}
    provider = build_provider(fakes)

    response = provider.generate(system="sys", prompt="p")

    assert response.model == "gemini-3.8-flash"
    assert response.requested_model == "gemini-3.8-flash"
    assert response.fallback_used is False
    assert response.fallback_reason is None
    assert response.model_attempts == 1


def test_preferred_model_transient_failure_then_succeeds_on_retry():
    sleeps: list[float] = []
    fakes = {
        "gemini-3.8-flash": FakeSingleModelProvider(
            "gemini-3.8-flash", [server_error(503), make_response("gemini-3.8-flash")]
        ),
        "gemini-3.7-flash": FakeSingleModelProvider("gemini-3.7-flash", []),
    }
    provider = build_provider(fakes, sleeps=sleeps)

    response = provider.generate(system="sys", prompt="p")

    assert response.model == "gemini-3.8-flash"
    assert response.fallback_used is False
    assert response.model_attempts == 2
    assert fakes["gemini-3.7-flash"].calls == 0
    assert len(sleeps) == 1  # one backoff before the retry


def test_preferred_model_exhausts_retries_first_fallback_succeeds():
    fakes = {
        "gemini-3.8-flash": FakeSingleModelProvider(
            "gemini-3.8-flash", [server_error(503)] * 4  # initial + 3 retries, all fail
        ),
        "gemini-3.7-flash": FakeSingleModelProvider("gemini-3.7-flash", [make_response("gemini-3.7-flash")]),
    }
    provider = build_provider(fakes)

    response = provider.generate(system="sys", prompt="p")

    assert response.model == "gemini-3.7-flash"
    assert response.requested_model == "gemini-3.8-flash"
    assert response.fallback_used is True
    assert "503" in response.fallback_reason
    assert response.model_attempts == 5  # 4 failed + 1 succeeded
    assert fakes["gemini-3.8-flash"].calls == 4


def test_first_fallback_fails_second_fallback_succeeds():
    fakes = {
        "gemini-3.8-flash": FakeSingleModelProvider("gemini-3.8-flash", [server_error(503)] * 4),
        "gemini-3.7-flash": FakeSingleModelProvider("gemini-3.7-flash", [server_error(500)] * 4),
        "gemini-3.6-flash": FakeSingleModelProvider("gemini-3.6-flash", [make_response("gemini-3.6-flash")]),
    }
    provider = build_provider(fakes)

    response = provider.generate(system="sys", prompt="p")

    assert response.model == "gemini-3.6-flash"
    assert response.fallback_used is True
    assert response.model_attempts == 9  # 4 + 4 + 1
    assert fakes["gemini-3.8-flash"].calls == 4
    assert fakes["gemini-3.7-flash"].calls == 4
    assert fakes["gemini-3.6-flash"].calls == 1


def test_auth_error_raises_immediately_with_no_fallback():
    fakes = {
        "gemini-3.8-flash": FakeSingleModelProvider("gemini-3.8-flash", [client_error(401, "invalid API key")]),
        "gemini-3.7-flash": FakeSingleModelProvider("gemini-3.7-flash", []),
    }
    provider = build_provider(fakes)

    with pytest.raises(ProviderGenerationError) as excinfo:
        provider.generate(system="sys", prompt="p")

    assert "invalid API key" in str(excinfo.value)
    assert excinfo.value.fallback_used is False
    assert excinfo.value.attempts == 1
    assert fakes["gemini-3.7-flash"].calls == 0  # never even tried


def test_all_models_fail_raises_provider_generation_error_with_full_provenance():
    fakes = {
        "gemini-3.8-flash": FakeSingleModelProvider("gemini-3.8-flash", [server_error(503)] * 4),
        "gemini-3.7-flash": FakeSingleModelProvider("gemini-3.7-flash", [server_error(500)] * 4),
    }
    provider = build_provider(fakes)

    with pytest.raises(ProviderGenerationError) as excinfo:
        provider.generate(system="sys", prompt="p")

    err = excinfo.value
    assert err.requested_model == "gemini-3.8-flash"
    assert err.attempts == 8
    assert len(err.attempt_log) == 8
    assert all(a["outcome"] == "failed" for a in err.attempt_log)
    assert err.fallback_used is True


def test_retry_preferred_only_mode_never_advances_chain():
    fakes = {
        "gemini-3.8-flash": FakeSingleModelProvider("gemini-3.8-flash", [server_error(503)] * 4),
        "gemini-3.7-flash": FakeSingleModelProvider("gemini-3.7-flash", [make_response("gemini-3.7-flash")]),
    }
    provider = build_provider(fakes)

    with pytest.raises(ProviderGenerationError):
        provider.generate(system="sys", prompt="p", mode="preferred_only")

    assert fakes["gemini-3.7-flash"].calls == 0


def test_cost_uses_the_actual_model_that_succeeded():
    fakes = {
        "gemini-3.8-flash": FakeSingleModelProvider("gemini-3.8-flash", [server_error(503)] * 4),
        "gemini-3.7-flash": FakeSingleModelProvider(
            "gemini-3.7-flash", [make_response("gemini-3.7-flash", cost=0.0042)]
        ),
    }
    provider = build_provider(fakes)

    response = provider.generate(system="sys", prompt="p")

    assert response.model == "gemini-3.7-flash"
    assert response.estimated_cost_usd == pytest.approx(0.0042)


def test_failed_attempt_cost_is_not_dropped_when_usage_is_exposed():
    # A failed attempt that nonetheless carries usage metadata (rare, but
    # possible in principle) must not silently disappear from the run cost.
    error_with_usage = server_error(503)
    error_with_usage.details = {
        "message": "model overloaded",
        "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 0},
    }
    fakes = {
        "gemini-3.8-flash": FakeSingleModelProvider(
            "gemini-3.8-flash", [error_with_usage] + [server_error(503)] * 3
        ),
        "gemini-3.7-flash": FakeSingleModelProvider(
            "gemini-3.7-flash", [make_response("gemini-3.7-flash", cost=0.005)]
        ),
    }
    provider = build_provider(fakes)

    response = provider.generate(system="sys", prompt="p")

    # gemini-3.8-flash pricing: 0.75 USD / 1M input tokens -> 1000 tokens = 0.00075
    assert response.estimated_cost_usd == pytest.approx(0.005 + 0.00075)
