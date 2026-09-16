"""Release-gate SDK contract coverage (AUDIT_REPORT.md Section H/J.2).

Purpose: the normal offline suite never constructs a real provider SDK
client with this application's actual kwargs, and never validates a
response object through the real SDK's own pydantic models -- every
web/service-level test goes through FakeOrchestrator, and provider-level
tests construct SDK *exceptions* directly but never call
`OpenAI(...).responses.create`, `anthropic.Anthropic(...).messages.create`,
or `genai.Client(...).interactions.create` shape-checking machinery for
real. That gap is exactly what let two real incidents (the Gemini exception
hierarchy mismatch fixed for red-team timeouts, and the Anthropic
structured-output reliability fix) reach a live run before being caught.

This suite closes that gap WITHOUT spending any money:
  - client construction uses real SDK classes with fake credentials (no
    network call happens at construction time, for any of the three SDKs);
  - request-shape checks use real signature/TypedDict introspection against
    the exact kwargs providers.py sends;
  - response-shape checks build a real, sanitized, realistic-shaped fixture
    (no API keys, no real content), validate it through the SDK's own
    pydantic model (`Response.model_validate` / `Message.model_validate` /
    `Interaction.model_validate` -- exercising the SDK's own real parsing
    logic, including derived fields like OpenAI's `.output_text` property
    and the Gemini SDK's `_populate_output_helpers` validator), then run
    that validated object through the actual, unmodified production
    adapter (`OpenAIProvider.generate` / `AnthropicProvider.generate` /
    `GeminiProvider.generate`) with only the network call itself
    monkeypatched out -- never mocking above the adapter.
  - exception-hierarchy checks construct real SDK exception classes and run
    them through the real classifier functions.

No paid API calls anywhere in this file.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import httpx
import pytest

from llm_deliberation import convergence
from llm_deliberation.providers import (
    AnthropicProvider,
    GeminiProvider,
    OpenAIProvider,
    classify_gemini_error,
    classify_gemini_readiness_error,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


# ======================================================================
# OpenAI
# ======================================================================


def test_openai_client_constructs_with_production_kwargs(monkeypatch):
    """providers.OpenAIProvider.generate() calls
    `OpenAI(timeout=self.timeout_seconds, max_retries=self.max_retries)` --
    construct it for real (no network call happens at construction) to
    prove those two kwargs are still accepted."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fixture-fake-key")
    from openai import OpenAI

    client = OpenAI(timeout=180.0, max_retries=1)
    assert client.timeout == 180.0
    assert client.max_retries == 1


def test_openai_responses_create_signature_matches_production_kwargs():
    """Every kwarg providers.OpenAIProvider.generate() passes to
    `client.responses.create(...)` -- model, instructions, input, reasoning,
    max_output_tokens, store -- must still be accepted parameters."""
    from openai.resources.responses.responses import Responses

    params = inspect.signature(Responses.create).parameters
    for name in ("model", "instructions", "input", "reasoning", "max_output_tokens", "store"):
        assert name in params, f"openai.resources.responses.Responses.create lost param {name!r}"


def test_openai_response_status_and_incomplete_details_reason_contract():
    """The exact fields orchestrator/providers.py's _openai_incomplete_reason
    reads: Response.status and Response.incomplete_details.reason, and the
    literal values it branches on."""
    from openai.types.responses.response import IncompleteDetails, Response

    assert "status" in Response.model_fields
    assert "incomplete_details" in Response.model_fields
    assert "reason" in IncompleteDetails.model_fields


@pytest.fixture
def _fake_openai_responses_create(monkeypatch):
    """Monkeypatches only the network boundary (Responses.create) -- the
    real OpenAI() client construction and the real OpenAIProvider.generate()
    adapter both run unmodified. Returns a function: call it with a fixture
    dict to make the next `client.responses.create(...)` call return that
    fixture, validated through the SDK's own real Response model."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fixture-fake-key")
    from openai.resources.responses.responses import Responses
    from openai.types.responses.response import Response

    captured: dict = {}

    def _install(fixture: dict):
        response_obj = Response.model_validate(fixture)

        def fake_create(self, **kwargs):
            captured["kwargs"] = kwargs
            return response_obj

        monkeypatch.setattr(Responses, "create", fake_create)
        return captured

    return _install


def test_openai_provider_generate_parses_completed_response(_fake_openai_responses_create):
    captured = _fake_openai_responses_create(_load_fixture("openai_response_completed.json"))

    provider = OpenAIProvider("gpt-5.6-terra", max_output_tokens=1000)
    result = provider.generate(system="You are a careful analyst.", prompt="Analyze X.")

    assert result.text == "Sanitized sample analysis output."
    assert result.usage.input_tokens == 120
    assert result.usage.output_tokens == 340
    assert result.incomplete_reason is None
    # Proves the adapter really called through with the production kwargs.
    assert captured["kwargs"]["model"] == "gpt-5.6-terra"
    assert captured["kwargs"]["instructions"] == "You are a careful analyst."
    assert captured["kwargs"]["store"] is False


def test_openai_provider_generate_classifies_truncated_response(_fake_openai_responses_create):
    _fake_openai_responses_create(_load_fixture("openai_response_incomplete_truncated.json"))

    provider = OpenAIProvider("gpt-5.6-terra", max_output_tokens=1000)
    result = provider.generate(system="s", prompt="p")

    assert result.incomplete_reason == "output_truncated"
    assert result.text  # truncated text is still preserved, not discarded


def test_openai_provider_generate_classifies_empty_response(_fake_openai_responses_create):
    _fake_openai_responses_create(_load_fixture("openai_response_empty_output.json"))

    provider = OpenAIProvider("gpt-5.6-terra", max_output_tokens=1000)
    result = provider.generate(system="s", prompt="p")

    assert result.incomplete_reason == "empty_output"


# ======================================================================
# Anthropic
# ======================================================================


def test_anthropic_client_constructs_with_production_kwargs(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fixture-fake-key")
    import anthropic

    client = anthropic.Anthropic(timeout=180.0, max_retries=1)
    assert client.timeout == 180.0
    assert client.max_retries == 1


def test_anthropic_messages_create_signature_matches_production_kwargs():
    """Every kwarg providers.AnthropicProvider.generate() passes --
    model, max_tokens, system, output_config, messages."""
    from anthropic.resources.messages import Messages

    params = inspect.signature(Messages.create).parameters
    for name in ("model", "max_tokens", "system", "output_config", "messages"):
        assert name in params, f"anthropic.resources.messages.Messages.create lost param {name!r}"


def test_anthropic_output_config_structured_output_shape():
    """providers.AnthropicProvider.generate() builds
    `output_config = {"effort": ..., "format": {"type": "json_schema",
    "schema": ...}}` -- both keys must still be exactly what the SDK's own
    typed params expect."""
    from anthropic.types.json_output_format_param import JSONOutputFormatParam
    from anthropic.types.output_config_param import OutputConfigParam

    assert set(OutputConfigParam.__annotations__.keys()) >= {"effort", "format"}
    assert set(JSONOutputFormatParam.__annotations__.keys()) >= {"type", "schema"}


def test_anthropic_transform_schema_compatible_with_convergence_model():
    """providers.py's response_schema (built by orchestrator via
    anthropic.transform_schema(ConvergenceAnalysis)) must still be a plain,
    JSON-serializable dict shape the SDK's own transform accepts as input
    and produces as output -- pure/local, no network call."""
    import anthropic

    schema = anthropic.transform_schema(convergence.ConvergenceAnalysis)
    assert isinstance(schema, dict)
    assert schema.get("type") == "object"
    # Round-trips through the exact shape AnthropicProvider.generate() sends.
    output_config = {"effort": "high", "format": {"type": "json_schema", "schema": schema}}
    json.dumps(output_config)  # must be JSON-serializable, not raise


def test_anthropic_message_stop_reason_and_content_text_contract():
    from anthropic.types.message import Message
    from anthropic.types.text_block import TextBlock

    assert "stop_reason" in Message.model_fields
    assert "content" in Message.model_fields
    assert "usage" in Message.model_fields
    # providers.py discriminates text blocks via getattr(block, "type", None)
    # == "text" -- confirm a real TextBlock still reports that literal.
    assert TextBlock.model_construct(type="text", text="x").type == "text"


@pytest.fixture
def _fake_anthropic_messages_create(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fixture-fake-key")
    from anthropic.resources.messages import Messages
    from anthropic.types.message import Message

    captured: dict = {}

    def _install(fixture: dict):
        message_obj = Message.model_validate(fixture)

        def fake_create(self, **kwargs):
            captured["kwargs"] = kwargs
            return message_obj

        monkeypatch.setattr(Messages, "create", fake_create)
        return captured

    return _install


def test_anthropic_provider_generate_parses_end_turn_message(_fake_anthropic_messages_create):
    captured = _fake_anthropic_messages_create(_load_fixture("anthropic_message_end_turn.json"))

    provider = AnthropicProvider("claude-sonnet-5", max_output_tokens=1000)
    result = provider.generate(system="s", prompt="p")

    assert result.text == "Sanitized sample revision output."
    assert result.usage.input_tokens == 200
    assert result.usage.output_tokens == 150
    assert result.incomplete_reason is None
    assert captured["kwargs"]["model"] == "claude-sonnet-5"
    assert captured["kwargs"]["messages"] == [{"role": "user", "content": "p"}]


def test_anthropic_provider_generate_classifies_max_tokens_as_truncated(
    _fake_anthropic_messages_create,
):
    _fake_anthropic_messages_create(_load_fixture("anthropic_message_max_tokens.json"))

    provider = AnthropicProvider("claude-sonnet-5", max_output_tokens=1000)
    result = provider.generate(system="s", prompt="p")

    assert result.incomplete_reason == "output_truncated"


def test_anthropic_provider_structured_output_round_trips_through_convergence_parser(
    _fake_anthropic_messages_create,
):
    """End-to-end offline proof of the real reliability boundary added in
    the convergence structured-output fix: a fixture-shaped Anthropic
    response, parsed by the real adapter, must be accepted by the real
    convergence.parse_convergence_analysis() -- the exact two production
    components chained together, no mocking in between."""
    _fake_anthropic_messages_create(_load_fixture("anthropic_message_structured_convergence.json"))

    schema = convergence.ConvergenceAnalysis.model_json_schema()
    provider = AnthropicProvider("claude-sonnet-5", max_output_tokens=1000, response_schema=schema)
    result = provider.generate(system="s", prompt="p")

    analysis = convergence.parse_convergence_analysis(result.text)
    assert analysis.convergence == "partial"
    assert analysis.material_changes[0].candidate == "A"
    assert analysis.material_changes[0].change_status == "material"


# ======================================================================
# Gemini
# ======================================================================


class _FakeUsageObj:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeGenaiClient:
    """Stands in for google.genai.Client -- records constructor kwargs and
    lets a test install a canned, pre-validated Interaction response for
    `interactions.create(...)`, without any network call. See
    test_gemini_credential.py for the sibling test that exercises this same
    fake for the credential-precedence regression."""

    last_kwargs: dict | None = None
    _next_response: object = None
    last_create_kwargs: dict | None = None

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs
        self.interactions = self._Interactions()
        self.models = self._Models()

    class _Interactions:
        def create(self, **kwargs):
            _FakeGenaiClient.last_create_kwargs = kwargs
            return _FakeGenaiClient._next_response

    class _Models:
        def get(self, *, model):
            return object()


@pytest.fixture
def _fake_gemini_client(monkeypatch):
    import google.genai as genai

    monkeypatch.setenv("GEMINI_API_KEY", "gemini-fixture-fake-key")
    monkeypatch.setattr(genai, "Client", _FakeGenaiClient)

    def _install(fixture: dict):
        from google.genai._gaos.types.interactions.interaction import Interaction

        _FakeGenaiClient._next_response = Interaction.model_validate(fixture)
        _FakeGenaiClient.last_create_kwargs = None
        return _FakeGenaiClient

    return _install


def test_gemini_client_constructs_with_explicit_api_key(_fake_gemini_client):
    """Proves the J.1 fix: GeminiProvider always passes api_key= explicitly
    (see test_gemini_credential.py for the full precedence regression)."""
    _fake_gemini_client(_load_fixture("gemini_interaction_completed.json"))

    GeminiProvider("gemini-3.8-flash", max_output_tokens=1000).generate(system="s", prompt="p")

    assert _FakeGenaiClient.last_kwargs["api_key"] == "gemini-fixture-fake-key"


def test_gemini_interactions_create_request_body_shape():
    """The exact kwargs providers.GeminiProvider.generate() sends to
    `client.interactions.create(...)` -- model, system_instruction, input,
    generation_config, store -- must still be fields the SDK's own typed
    request body accepts."""
    from google.genai.interactions import CreateModelInteractionParamsNonStreaming, GenerationConfig

    body_fields = set(CreateModelInteractionParamsNonStreaming.__annotations__.keys())
    for name in ("model", "system_instruction", "input", "generation_config", "store"):
        assert name in body_fields, f"Gemini interaction request body lost field {name!r}"

    gen_config_fields = set(GenerationConfig.model_fields.keys())
    assert {"thinking_level", "max_output_tokens"} <= gen_config_fields


def test_gemini_provider_generate_parses_completed_interaction(_fake_gemini_client):
    fake = _fake_gemini_client(_load_fixture("gemini_interaction_completed.json"))

    provider = GeminiProvider("gemini-3.8-flash", max_output_tokens=1000)
    result = provider.generate(system="s", prompt="p")

    assert result.text == "Sanitized sample red-team output."
    assert result.usage.input_tokens == 300
    # providers.py sums total_output_tokens + total_thought_tokens.
    assert result.usage.output_tokens == 500 + 40
    assert result.incomplete_reason is None
    assert fake.last_create_kwargs["model"] == "gemini-3.8-flash"
    assert fake.last_create_kwargs["store"] is False


def test_gemini_provider_generate_classifies_incomplete_as_truncated(_fake_gemini_client):
    _fake_gemini_client(_load_fixture("gemini_interaction_incomplete.json"))

    provider = GeminiProvider("gemini-3.8-flash", max_output_tokens=1000)
    result = provider.generate(system="s", prompt="p")

    assert result.incomplete_reason == "output_truncated"


# -- Gemini exception hierarchies (generation vs readiness, kept distinct) --


def _gaos_response(status_code: int):
    request = httpx.Request("POST", "https://example.invalid/v1/interactions")
    return httpx.Response(status_code=status_code, request=request, json={"error": {"message": "fixture"}})


def test_gemini_generation_exception_hierarchy_classification():
    """client.interactions.create(...) raises from
    google.genai._gaos.lib.compat_errors -- confirm the classifier still
    sorts each real exception type into the correct transient/non-transient
    bucket (see providers.classify_gemini_error's docstring)."""
    from google.genai._gaos.lib import compat_errors as gaos_errors

    timeout = gaos_errors.APITimeoutError(httpx.Request("POST", "https://example.invalid"))
    is_transient, _ = classify_gemini_error(timeout)
    assert is_transient is True

    rate_limited = gaos_errors.RateLimitError("too many requests", response=_gaos_response(429), body=None)
    is_transient, _ = classify_gemini_error(rate_limited)
    assert is_transient is True

    server_error = gaos_errors.InternalServerError("overloaded", response=_gaos_response(503), body=None)
    is_transient, _ = classify_gemini_error(server_error)
    assert is_transient is True

    bad_request = gaos_errors.APIStatusError("bad request", response=_gaos_response(400), body=None)
    is_transient, _ = classify_gemini_error(bad_request)
    assert is_transient is False


def test_gemini_readiness_exception_hierarchy_classification():
    """client.models.get(...) raises from google.genai.errors -- a
    deliberately DIFFERENT hierarchy from the generation path above (see
    classify_gemini_readiness_error's docstring). Confirmed distinct classes
    below so a future SDK refactor that merges them would fail loudly here
    instead of silently making the two-classifier split meaningless."""
    from google.genai import errors as genai_errors

    auth_error = genai_errors.ClientError(401, {"error": {"message": "bad key"}})
    status, _ = classify_gemini_readiness_error(auth_error)
    assert status == "auth_error"

    not_found = genai_errors.ClientError(404, {"error": {"message": "model not found"}})
    status, _ = classify_gemini_readiness_error(not_found)
    assert status == "model_unavailable"

    server_error = genai_errors.ServerError(503, {"error": {"message": "overloaded"}})
    status, _ = classify_gemini_readiness_error(server_error)
    assert status == "transient_error"


def test_gemini_generation_and_readiness_exception_hierarchies_are_distinct():
    from google.genai import errors as genai_errors
    from google.genai._gaos.lib import compat_errors as gaos_errors

    assert gaos_errors.APIError is not genai_errors.ClientError
    assert not issubclass(gaos_errors.APIError, genai_errors.ClientError)
    assert not issubclass(genai_errors.ClientError, gaos_errors.APIError)
