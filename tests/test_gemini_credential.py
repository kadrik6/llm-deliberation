"""Regression coverage for the Gemini credential-precedence fix (AUDIT_REPORT.md
Section C / J.1).

Before this fix, GeminiProvider.generate() constructed `genai.Client(...)`
without an explicit `api_key=`, so google-genai 2.23.0's own env
auto-detection resolved the credential -- and that auto-detection checks
GOOGLE_API_KEY *before* GEMINI_API_KEY. Meanwhile readiness.py and
config.validate_keys() both validate only GEMINI_API_KEY. An operator with
both variables set to different values would see readiness report "ready"
for one credential while the real, paid generation call silently used the
other.

These tests never touch the network: `google.genai.Client` is replaced with
a fake that only records the kwargs it was constructed with, for both the
readiness code path and the generation code path.
"""

from __future__ import annotations

import google.genai as genai

from llm_deliberation.providers import GeminiProvider
from llm_deliberation.readiness import check_gemini_readiness, invalidate_readiness_cache


class _FakeUsage:
    total_input_tokens = 1
    total_output_tokens = 1
    total_thought_tokens = 0


class _FakeInteraction:
    output_text = "ok"
    status = "completed"
    usage = _FakeUsage()


class _FakeInteractions:
    def create(self, **kwargs):
        return _FakeInteraction()


class _FakeModels:
    def get(self, *, model):
        return object()


class _FakeGenaiClient:
    """Stands in for google.genai.Client: records constructor kwargs
    instead of making any real network call, for both the
    `models.get(...)` (readiness) and `interactions.create(...)`
    (generation) call sites."""

    last_kwargs: dict | None = None

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs
        self.interactions = _FakeInteractions()
        self.models = _FakeModels()


def _reset_capture() -> None:
    _FakeGenaiClient.last_kwargs = None


def test_generation_client_receives_gemini_api_key_when_only_that_is_set(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-only-fake-key")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(genai, "Client", _FakeGenaiClient)
    _reset_capture()

    GeminiProvider("gemini-3.8-flash", max_output_tokens=100).generate(system="s", prompt="p")

    assert _FakeGenaiClient.last_kwargs is not None
    assert _FakeGenaiClient.last_kwargs["api_key"] == "gemini-only-fake-key"


def test_generation_client_prefers_gemini_api_key_over_google_api_key(monkeypatch):
    """The central regression: with BOTH variables set to *different* fake
    values, the real generation call must use GEMINI_API_KEY -- never let
    GOOGLE_API_KEY silently override it, even though the underlying SDK's
    own env auto-detection (bypassed here by the explicit api_key= kwarg)
    would otherwise prefer GOOGLE_API_KEY."""
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-real-fake-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "google-different-fake-key")
    monkeypatch.setattr(genai, "Client", _FakeGenaiClient)
    _reset_capture()

    GeminiProvider("gemini-3.8-flash", max_output_tokens=100).generate(system="s", prompt="p")

    used_key = _FakeGenaiClient.last_kwargs["api_key"]
    assert used_key == "gemini-real-fake-key"
    assert used_key != "google-different-fake-key"


def test_readiness_and_generation_resolve_the_same_credential(monkeypatch):
    """Proves the two code paths are consistent: whichever credential
    readiness.check_gemini_readiness() validated is exactly the credential
    GeminiProvider.generate() will really use -- no possibility of
    "readiness said ready" and "the real call used something else"."""
    monkeypatch.setenv("GEMINI_API_KEY", "shared-fake-key-xyz")
    monkeypatch.setenv("GOOGLE_API_KEY", "unrelated-other-fake-key")
    monkeypatch.setattr(genai, "Client", _FakeGenaiClient)
    invalidate_readiness_cache()

    _reset_capture()
    check_gemini_readiness("gemini-credential-test-model", force=True)
    readiness_key = _FakeGenaiClient.last_kwargs["api_key"]

    _reset_capture()
    GeminiProvider("gemini-credential-test-model", max_output_tokens=100).generate(
        system="s", prompt="p"
    )
    generation_key = _FakeGenaiClient.last_kwargs["api_key"]

    assert readiness_key == generation_key == "shared-fake-key-xyz"
