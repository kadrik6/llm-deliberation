"""Offline tests for provider readiness preflight (readiness.py). No network
calls: SDK client construction/`.models.retrieve()`/`.models.get()` calls are
monkeypatched to raise real SDK exception *types* (constructed directly, not
via an actual HTTP round-trip -- these classes take a message/response/body
in their constructor, no network needed), matching the pattern already used
by conftest.py's FakeOrchestrator and test_provider_completion.py's
SimpleNamespace stand-ins for provider responses.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from llm_deliberation import readiness
from llm_deliberation.config import Settings
from llm_deliberation.providers import (
    classify_anthropic_readiness_error,
    classify_gemini_readiness_error,
    classify_openai_readiness_error,
)


@pytest.fixture(autouse=True)
def _reset_readiness_cache():
    readiness.invalidate_readiness_cache()
    yield
    readiness.invalidate_readiness_cache()


@pytest.fixture(autouse=True)
def _api_keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")


@pytest.fixture(autouse=True)
def _stub_every_provider_ready(monkeypatch):
    """Every test in this file must be fully offline (no paid API calls --
    see the task brief's hard constraint). check_run_readiness always
    checks OpenAI and Anthropic (they're required), so a test that only
    means to exercise, say, Gemini's failure path would otherwise let the
    other two providers' `.models.retrieve()` fire a REAL network call
    against a fake key -- free of charge, but a real, non-deterministic
    network dependency a unit test must never have. This stubs all three to
    "ready" by default; a test overrides just the one(s) it cares about via
    its own monkeypatch.setattr call, layered on top of this one.
    """
    import openai
    import anthropic
    from google.genai import models as genai_models

    monkeypatch.setattr(
        openai.resources.models.Models, "retrieve", lambda self, model, **kw: SimpleNamespace(id=model)
    )
    monkeypatch.setattr(
        anthropic.resources.models.Models,
        "retrieve",
        lambda self, model_id, **kw: SimpleNamespace(id=model_id),
    )
    monkeypatch.setattr(
        genai_models.Models, "get", lambda self, **kw: SimpleNamespace(name=kw.get("model"))
    )


def _httpx_response(status_code: int) -> httpx.Response:
    request = httpx.Request("GET", "https://example.invalid/v1/models/x")
    return httpx.Response(status_code=status_code, request=request, json={"error": {"message": "x"}})


# -- 1: missing API key is a config-validity failure, not a live check ------


def test_openai_missing_key_is_config_missing_not_a_network_call(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = readiness.check_openai_readiness("gpt-5.6-sol")
    assert result.status == "unavailable"
    assert result.check_type == "config_missing"
    assert not result.paid_probe


def test_anthropic_missing_key_is_config_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = readiness.check_anthropic_readiness("claude-opus-5")
    assert result.status == "unavailable"
    assert result.check_type == "config_missing"


def test_gemini_missing_key_is_config_missing(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = readiness.check_gemini_readiness("gemini-3.8-flash")
    assert result.status == "unavailable"
    assert result.check_type == "config_missing"


# -- 2: valid configuration -> ready, via the free models.retrieve/get call -


def test_openai_ready_when_models_retrieve_succeeds(monkeypatch):
    import openai

    monkeypatch.setattr(
        openai.resources.models.Models, "retrieve", lambda self, model, **kw: SimpleNamespace(id=model)
    )
    result = readiness.check_openai_readiness("gpt-5.6-sol")
    assert result.status == "ready"
    assert result.ready
    assert result.check_type == "model_retrieve"
    assert not result.paid_probe
    assert result.estimated_probe_cost_usd == 0.0
    # Never claims a guarantee about future calls.
    assert "does not guarantee" in result.user_message


def test_anthropic_ready_when_models_retrieve_succeeds(monkeypatch):
    import anthropic

    monkeypatch.setattr(
        anthropic.resources.models.Models,
        "retrieve",
        lambda self, model_id, **kw: SimpleNamespace(id=model_id),
    )
    result = readiness.check_anthropic_readiness("claude-opus-5")
    assert result.status == "ready"
    assert not result.paid_probe


def test_gemini_ready_when_models_get_succeeds(monkeypatch):
    from google.genai import models as genai_models

    monkeypatch.setattr(genai_models.Models, "get", lambda self, **kw: SimpleNamespace(name=kw.get("model")))
    result = readiness.check_gemini_readiness("gemini-3.8-flash")
    assert result.status == "ready"
    assert not result.paid_probe


# -- 3: auth failure ----------------------------------------------------


def test_openai_auth_failure_classified_as_auth_error(monkeypatch):
    import openai

    def _raise(self, model, **kw):
        raise openai.AuthenticationError("Incorrect API key", response=_httpx_response(401), body=None)

    monkeypatch.setattr(openai.resources.models.Models, "retrieve", _raise)
    result = readiness.check_openai_readiness("gpt-5.6-sol")
    assert result.status == "auth_error"
    assert result.check_type == "model_retrieve"


def test_anthropic_auth_failure_classified_as_auth_error(monkeypatch):
    import anthropic

    def _raise(self, model_id, **kw):
        raise anthropic.AuthenticationError(
            "invalid x-api-key", response=_httpx_response(401), body=None
        )

    monkeypatch.setattr(anthropic.resources.models.Models, "retrieve", _raise)
    result = readiness.check_anthropic_readiness("claude-opus-5")
    assert result.status == "auth_error"


# -- 4: billing failure --------------------------------------------------


def test_openai_billing_rejection_detected_from_rate_limit_body(monkeypatch):
    import openai

    def _raise(self, model, **kw):
        raise openai.RateLimitError(
            "You exceeded your current quota, please check your plan and billing details.",
            response=_httpx_response(429),
            body={"error": {"type": "insufficient_quota", "code": "insufficient_quota"}},
        )

    monkeypatch.setattr(openai.resources.models.Models, "retrieve", _raise)
    result = readiness.check_openai_readiness("gpt-5.6-sol")
    assert result.status == "billing_error"


def test_anthropic_billing_rejection_detected_from_bad_request_message(monkeypatch):
    import anthropic

    def _raise(self, model_id, **kw):
        raise anthropic.BadRequestError(
            "Your credit balance is too low to access the Anthropic API.",
            response=_httpx_response(400),
            body=None,
        )

    monkeypatch.setattr(anthropic.resources.models.Models, "retrieve", _raise)
    result = readiness.check_anthropic_readiness("claude-opus-5")
    assert result.status == "billing_error"


def test_openai_ordinary_rate_limit_is_not_misclassified_as_billing(monkeypatch):
    import openai

    def _raise(self, model, **kw):
        raise openai.RateLimitError(
            "Rate limit reached for requests",
            response=_httpx_response(429),
            body={"error": {"type": "requests", "code": "rate_limit_exceeded"}},
        )

    monkeypatch.setattr(openai.resources.models.Models, "retrieve", _raise)
    result = readiness.check_openai_readiness("gpt-5.6-sol")
    assert result.status == "rate_limited"


# -- 5: model unavailable -------------------------------------------------


def test_openai_model_not_found_is_model_unavailable(monkeypatch):
    import openai

    def _raise(self, model, **kw):
        raise openai.NotFoundError("model not found", response=_httpx_response(404), body=None)

    monkeypatch.setattr(openai.resources.models.Models, "retrieve", _raise)
    result = readiness.check_openai_readiness("no-such-model")
    assert result.status == "model_unavailable"


def test_gemini_model_not_found_is_model_unavailable(monkeypatch):
    from google.genai import errors as genai_errors
    from google.genai import models as genai_models

    def _raise(self, **kw):
        raise genai_errors.ClientError(404, {"error": {"message": "model not found"}})

    monkeypatch.setattr(genai_models.Models, "get", _raise)
    result = readiness.check_gemini_readiness("no-such-model")
    assert result.status == "model_unavailable"


# -- 6: transient provider failure ----------------------------------------


def test_openai_server_error_is_transient(monkeypatch):
    import openai

    def _raise(self, model, **kw):
        raise openai.InternalServerError("upstream error", response=_httpx_response(500), body=None)

    monkeypatch.setattr(openai.resources.models.Models, "retrieve", _raise)
    result = readiness.check_openai_readiness("gpt-5.6-sol")
    assert result.status == "transient_error"


def test_gemini_server_error_is_transient(monkeypatch):
    from google.genai import errors as genai_errors
    from google.genai import models as genai_models

    def _raise(self, **kw):
        raise genai_errors.ServerError(503, {"error": {"message": "overloaded"}})

    monkeypatch.setattr(genai_models.Models, "get", _raise)
    result = readiness.check_gemini_readiness("gemini-3.8-flash")
    assert result.status == "transient_error"


# -- 7/8: required-provider readiness gates a run; no paid stage created ----


def test_required_provider_failure_reported_in_readiness_report(monkeypatch):
    import openai

    def _raise(self, model, **kw):
        raise openai.AuthenticationError("bad key", response=_httpx_response(401), body=None)

    monkeypatch.setattr(openai.resources.models.Models, "retrieve", _raise)

    settings = Settings.load(profile_override="economy", red_team_override=False)
    report = readiness.check_run_readiness(settings, red_team_enabled=False)
    assert not report.required_ready
    assert report.required_failures[0].provider == "openai"
    assert report.required_failures[0].status == "auth_error"


def test_check_readiness_blocks_run_start(tmp_path, monkeypatch):
    """DeliberationService.check_readiness surfaces a required-provider
    failure the web layer is expected to check *before* calling
    create_run/start_run -- see web/app.py's submit_run. Deliberately does
    NOT use the `service` fixture (which stubs readiness.check_run_readiness
    to always-ready for every *other* test's convenience -- see
    conftest.py's _always_ready_readiness_report); this test wants the real
    check_readiness -> readiness.check_run_readiness path, with only the
    underlying SDK call itself mocked out (no network).
    """
    import openai

    from llm_deliberation.service import DeliberationService

    def _raise(self, model, **kw):
        raise openai.AuthenticationError("bad key", response=_httpx_response(401), body=None)

    monkeypatch.setattr(openai.resources.models.Models, "retrieve", _raise)

    svc = DeliberationService(tmp_path / "deliberation.db")
    report = svc.check_readiness("economy", False)
    assert not report.required_ready
    assert report.required_failures[0].provider == "openai"
    assert report.required_failures[0].status == "auth_error"


# -- 9/10: gemini + red-team interaction ------------------------------------


def test_gemini_failure_with_red_team_enabled_is_reported_as_gemini_blocked(monkeypatch):
    from google.genai import errors as genai_errors
    from google.genai import models as genai_models

    def _raise(self, **kw):
        raise genai_errors.ClientError(401, {"error": {"message": "bad key"}})

    monkeypatch.setattr(genai_models.Models, "get", _raise)

    settings = Settings.load(profile_override="economy", red_team_override=True)
    report = readiness.check_run_readiness(settings, red_team_enabled=True)
    assert report.gemini_blocked
    assert report.gemini.status == "auth_error"
    # Required providers are untouched by a Gemini-only failure.
    assert report.required_ready


def test_gemini_not_checked_when_red_team_disabled(monkeypatch):
    calls = {"count": 0}

    def _count_call(self, **kw):
        calls["count"] += 1
        return SimpleNamespace(name=kw.get("model"))

    from google.genai import models as genai_models

    monkeypatch.setattr(genai_models.Models, "get", _count_call)

    settings = Settings.load(profile_override="economy", red_team_override=False)
    report = readiness.check_run_readiness(settings, red_team_enabled=False)
    assert report.gemini is None
    assert calls["count"] == 0


# -- 11/12/13: caching --------------------------------------------------


def test_readiness_result_is_cached(monkeypatch):
    calls = {"count": 0}

    def _count_call(self, model, **kw):
        calls["count"] += 1
        return SimpleNamespace(id=model)

    import openai

    monkeypatch.setattr(openai.resources.models.Models, "retrieve", _count_call)

    readiness.check_openai_readiness("gpt-5.6-sol")
    readiness.check_openai_readiness("gpt-5.6-sol")
    assert calls["count"] == 1  # second call served from cache


def test_fresh_cache_prevents_duplicate_probe(monkeypatch):
    calls = {"count": 0}

    def _count_call(self, model, **kw):
        calls["count"] += 1
        return SimpleNamespace(id=model)

    import openai

    monkeypatch.setattr(openai.resources.models.Models, "retrieve", _count_call)
    monkeypatch.setenv("READINESS_CACHE_TTL_SECONDS", "600")

    for _ in range(5):
        readiness.check_openai_readiness("gpt-5.6-sol")
    assert calls["count"] == 1


def test_explicit_recheck_bypasses_cache(monkeypatch):
    calls = {"count": 0}

    def _count_call(self, model, **kw):
        calls["count"] += 1
        return SimpleNamespace(id=model)

    import openai

    monkeypatch.setattr(openai.resources.models.Models, "retrieve", _count_call)

    readiness.check_openai_readiness("gpt-5.6-sol")
    readiness.check_openai_readiness("gpt-5.6-sol", force=True)
    assert calls["count"] == 2


# -- 14: model change invalidates cache key ---------------------------------


def test_model_change_invalidates_cache_key(monkeypatch):
    calls = []

    def _record_call(self, model, **kw):
        calls.append(model)
        return SimpleNamespace(id=model)

    import openai

    monkeypatch.setattr(openai.resources.models.Models, "retrieve", _record_call)

    readiness.check_openai_readiness("gpt-5.6-sol")
    readiness.check_openai_readiness("gpt-5.6-terra")  # different model -> different cache key
    assert calls == ["gpt-5.6-sol", "gpt-5.6-terra"]


def test_credential_change_invalidates_cache_key(monkeypatch):
    calls = {"count": 0}

    def _count_call(self, model, **kw):
        calls["count"] += 1
        return SimpleNamespace(id=model)

    import openai

    monkeypatch.setattr(openai.resources.models.Models, "retrieve", _count_call)

    readiness.check_openai_readiness("gpt-5.6-sol")
    monkeypatch.setenv("OPENAI_API_KEY", "a-different-test-key")
    readiness.check_openai_readiness("gpt-5.6-sol")
    assert calls["count"] == 2


# -- 15: API keys never exposed in readiness data ---------------------------


def test_api_key_never_appears_in_readiness_result(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-super-secret-value-12345")
    import openai

    def _raise(self, model, **kw):
        raise openai.AuthenticationError(
            "Incorrect API key provided: sk-***", response=_httpx_response(401), body=None
        )

    monkeypatch.setattr(openai.resources.models.Models, "retrieve", _raise)
    result = readiness.check_openai_readiness("gpt-5.6-sol")
    dump = repr(result) + result.user_message + (result.technical_detail or "")
    assert "sk-super-secret-value-12345" not in dump


def test_cache_key_never_contains_raw_api_key():
    fp = readiness._credential_fingerprint("OPENAI_API_KEY")
    import os

    assert os.environ["OPENAI_API_KEY"] not in fp
    assert len(fp) == 12  # truncated hash, not the raw value


# -- classifier unit tests (used directly by providers.py) ------------------


def test_classify_gemini_readiness_error_rate_limit_vs_billing():
    from google.genai import errors as genai_errors

    rate_limited = genai_errors.ClientError(429, {"error": {"message": "too many requests"}})
    status, _ = classify_gemini_readiness_error(rate_limited)
    assert status == "rate_limited"

    billing = genai_errors.ClientError(429, {"error": {"message": "quota exceeded, check billing"}})
    status, _ = classify_gemini_readiness_error(billing)
    assert status == "billing_error"


def test_classify_openai_readiness_error_unknown_status_error():
    import openai

    exc = openai.ConflictError("conflict", response=_httpx_response(409), body=None)
    status, message = classify_openai_readiness_error(exc)
    assert status == "unknown"
    assert "409" in message


def test_classify_anthropic_readiness_error_permission_denied():
    import anthropic

    exc = anthropic.PermissionDeniedError(
        "no access to this model", response=_httpx_response(403), body=None
    )
    status, _ = classify_anthropic_readiness_error(exc)
    assert status == "permission_error"


# -- 34: "ready" never claims a guarantee ------------------------------------


def test_ready_status_never_promises_future_success(monkeypatch):
    import openai

    monkeypatch.setattr(
        openai.resources.models.Models, "retrieve", lambda self, model, **kw: SimpleNamespace(id=model)
    )
    result = readiness.check_openai_readiness("gpt-5.6-sol")
    lowered = result.user_message.lower()
    assert "will definitely work" not in lowered
    assert "guarantee" in lowered
