"""Offline tests for orchestrator.run_stage's working-language contract
check and bounded corrective recovery (see AUDIT_REPORT.md's live-canary
findings: gpt-5.6-terra answered Estonian on an English-working-language
stage despite a verified-correct instruction, while claude-sonnet-5
complied on the same run).

No network calls: providers are stood in for by plain fake objects
returning canned ModelResponse values, following the exact pattern already
established in test_provider_completion.py's truncation-recovery tests.
"""

from __future__ import annotations

import asyncio

import pytest

from llm_deliberation import prompts
from llm_deliberation.cost_budget import RunBudgetGuard
from llm_deliberation.orchestrator import DeliberationOrchestrator
from llm_deliberation.providers import GeminiFallbackProvider, ProviderGenerationError
from llm_deliberation.types import ModelResponse, Usage

_ENGLISH_TEXT = (
    "The committee reviewed the proposal carefully and concluded that the "
    "budget assumptions were reasonable, although the timeline should be "
    "extended by two weeks to accommodate the vendor's onboarding process. "
    "This recommendation reflects the strongest available evidence and "
    "should be revisited if new information becomes available before the "
    "next quarterly review."
)

_ESTONIAN_TEXT = (
    "Komisjon vaatas ettepaneku hoolikalt läbi ja jõudis järeldusele, et "
    "eelarve eeldused olid mõistlikud, kuigi ajakava tuleks pikendada kahe "
    "nädala võrra, et võtta arvesse tarnija sisseelamisprotsessi. See "
    "soovitus kajastab parimaid olemasolevaid tõendeid ja seda tuleks "
    "uuesti kaaluda, kui enne järgmist kvartaliülevaatust ilmneb uut teavet."
)


class _FixedProvider:
    """Stands in for OpenAIProvider/AnthropicProvider: returns one canned
    ModelResponse per call, consumed in order (see
    test_provider_completion.py's identical helper)."""

    def __init__(self, responses: list[ModelResponse]):
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []
        self.max_output_tokens = 1000

    def generate(self, *, system: str, prompt: str) -> ModelResponse:
        self.calls.append((system, prompt))
        if not self._responses:
            raise AssertionError("provider called more times than expected")
        return self._responses.pop(0)


def _response(text: str, *, model: str = "gpt-5.6-terra", cost: float = 0.02) -> ModelResponse:
    return ModelResponse(
        provider="OpenAI",
        model=model,
        text=text,
        usage=Usage(input_tokens=50, output_tokens=200),
        estimated_cost_usd=cost,
        requested_model=model,
        incomplete_reason=None,
    )


def _orchestrator_with_provider(monkeypatch, *, a=None, b=None, red=None) -> DeliberationOrchestrator:
    monkeypatch.setattr(DeliberationOrchestrator, "__init__", lambda self, settings: None)
    orch = DeliberationOrchestrator(settings=None)
    if a is not None:
        orch.a = a
    if b is not None:
        orch.b = b
    if red is not None:
        orch.red = red
    return orch


# -- 1/6: matched output causes no retry ------------------------------------


def test_matched_working_language_causes_no_retry(monkeypatch):
    provider = _FixedProvider([_response(_ENGLISH_TEXT)])
    orch = _orchestrator_with_provider(monkeypatch, a=provider)

    response = asyncio.run(
        orch.run_stage(
            "analysis_a", "Estonian question here", {},
            output_language="et", working_language="en",
        )
    )

    assert len(provider.calls) == 1
    assert response.language_contract_status == "matched"
    assert response.observed_language == "en"
    assert response.language_recovery_attempted is False
    assert response.text == _ENGLISH_TEXT


# -- 7: uncertain output causes no automatic retry ---------------------------


def test_uncertain_language_causes_no_automatic_retry(monkeypatch):
    provider = _FixedProvider([_response("OK, agreed.")])
    orch = _orchestrator_with_provider(monkeypatch, a=provider)

    response = asyncio.run(
        orch.run_stage(
            "analysis_a", "Estonian question here", {},
            output_language="et", working_language="en",
        )
    )

    assert len(provider.calls) == 1
    assert response.language_contract_status == "uncertain"
    assert response.observed_language is None
    assert response.language_recovery_attempted is False


# -- 2/8: confident mismatch causes exactly one recovery ---------------------


def test_confident_mismatch_triggers_exactly_one_recovery(monkeypatch):
    provider = _FixedProvider([_response(_ESTONIAN_TEXT, cost=0.03), _response(_ENGLISH_TEXT, cost=0.02)])
    orch = _orchestrator_with_provider(monkeypatch, a=provider)

    response = asyncio.run(
        orch.run_stage(
            "analysis_a", "Estonian question here", {},
            output_language="et", working_language="en",
        )
    )

    assert len(provider.calls) == 2
    assert response.language_contract_status == "matched"
    assert response.observed_language == "en"
    assert response.language_recovery_attempted is True
    assert response.text == _ENGLISH_TEXT
    # -- 11: recovery cost accumulates correctly --
    assert response.estimated_cost_usd == pytest.approx(0.03 + 0.02)
    # -- 12: recovery provenance is persisted (on the ModelResponse, which
    # service._execute passes straight through to store.mark_stage_succeeded) --
    assert response.model_attempts == 2
    assert response.attempt_log == [
        {
            "phase": "initial", "model": "gpt-5.6-terra", "outcome": "language_mismatch",
            "detected_language": "et", "estimated_cost_usd": 0.03,
        },
        {
            "phase": "language_recovery", "model": "gpt-5.6-terra", "outcome": "succeeded",
            "detected_language": "en", "estimated_cost_usd": 0.02,
        },
    ]


# -- 9/10: recovery uses same stage/provider/model and a stronger instruction


def test_recovery_uses_same_provider_and_stronger_instruction(monkeypatch):
    provider = _FixedProvider([_response(_ESTONIAN_TEXT), _response(_ENGLISH_TEXT)])
    orch = _orchestrator_with_provider(monkeypatch, a=provider)

    asyncio.run(
        orch.run_stage(
            "analysis_a", "Estonian question here", {},
            output_language="et", working_language="en",
        )
    )

    assert len(provider.calls) == 2  # same provider instance, called twice
    first_system, first_prompt = provider.calls[0]
    second_system, second_prompt = provider.calls[1]
    assert first_prompt == second_prompt  # same stage/question, unchanged
    assert prompts.language_recovery_instruction("en") in second_system
    assert prompts.language_recovery_instruction("en") not in first_system


# -- 13: second mismatch does not trigger a third call -----------------------


def test_recovery_is_exactly_one_attempt_even_if_still_mismatched(monkeypatch):
    provider = _FixedProvider([_response(_ESTONIAN_TEXT, cost=0.01), _response(_ESTONIAN_TEXT, cost=0.01)])
    orch = _orchestrator_with_provider(monkeypatch, a=provider)

    response = asyncio.run(
        orch.run_stage(
            "analysis_a", "Estonian question here", {},
            output_language="et", working_language="en",
        )
    )

    assert len(provider.calls) == 2  # never a third call
    assert response.language_contract_status == "mismatched"
    assert response.observed_language == "et"
    assert response.language_recovery_attempted is True
    # The artifact is preserved, not discarded -- see Section 6/J's decision
    # (preserve artifact; mark mismatch; never fail the stage for this alone).
    assert response.text == _ESTONIAN_TEXT
    assert response.incomplete_reason is None


# -- English-only run: no override in effect, check never runs --------------


def test_no_working_language_override_means_no_language_check(monkeypatch):
    provider = _FixedProvider([_response(_ESTONIAN_TEXT)])  # even "wrong" text is untouched
    orch = _orchestrator_with_provider(monkeypatch, a=provider)

    response = asyncio.run(
        orch.run_stage(
            "analysis_a", "English question here", {},
            output_language="en", working_language="en",
        )
    )

    assert len(provider.calls) == 1  # never retried
    assert response.language_contract_status is None
    assert response.observed_language is None
    assert response.language_recovery_attempted is False


# -- 15: synthesis output-language logic remains unchanged ------------------


def test_synthesis_stage_is_never_language_checked(monkeypatch):
    # synthesis is not a WORKING_LANGUAGE_STAGES member -- resolve_stage_language
    # always resolves it to output_language, so stage_language == output_language
    # and the check's top-level condition is False regardless of content.
    provider = _FixedProvider([_response(_ESTONIAN_TEXT)])
    orch = _orchestrator_with_provider(monkeypatch, a=provider)

    response = asyncio.run(
        orch.run_stage(
            "synthesis", "Estonian question here",
            {"revision_a": "a", "revision_b": "b"},
            output_language="et", working_language="en",
        )
    )

    assert len(provider.calls) == 1
    assert response.language_contract_status is None
    assert response.language_recovery_attempted is False


# -- 16/17: the enforcement layer is provider-independent --------------------


def test_enforcement_layer_is_identical_for_a_different_provider_slot(monkeypatch):
    """Runs the exact same mismatch/recovery scenario through orch.b instead
    of orch.a (a different provider slot, standing in for "the other
    configured model") to prove the logic is not specific to any one
    provider -- only GeminiFallbackProvider gets different treatment, and
    only for the active-recovery call (see the detection-only test below)."""
    provider = _FixedProvider([_response(_ESTONIAN_TEXT, cost=0.03), _response(_ENGLISH_TEXT, cost=0.02)])
    orch = _orchestrator_with_provider(monkeypatch, b=provider)

    response = asyncio.run(
        orch.run_stage(
            "analysis_b", "Estonian question here", {},
            output_language="et", working_language="en",
        )
    )

    assert len(provider.calls) == 2
    assert response.language_contract_status == "matched"
    assert response.language_recovery_attempted is True


# -- red_team (GeminiFallbackProvider): detection only, no active recovery --


class _FakeGeminiFallback(GeminiFallbackProvider):
    """A GeminiFallbackProvider subclass (so isinstance checks match) whose
    generate() is replaced with a canned single response -- proves the
    language-contract block detects a mismatch but does NOT issue an active
    recovery call for this provider type, mirroring the existing
    truncation-recovery exclusion for the same architectural reason."""

    def __init__(self, response: ModelResponse):
        self._response = response
        self.calls = 0
        self.max_output_tokens = 1000

    def generate(self, *, system, prompt, mode="chain", budget_guard=None):
        self.calls += 1
        return self._response


def test_red_team_gets_detection_but_not_active_recovery(monkeypatch):
    fake_red = _FakeGeminiFallback(_response(_ESTONIAN_TEXT, model="gemini-3.8-flash"))
    orch = _orchestrator_with_provider(monkeypatch, red=fake_red)

    response = asyncio.run(
        orch.run_stage(
            "red_team", "Estonian question here",
            {"analysis_a": "a", "analysis_b": "b"},
            output_language="et", working_language="en",
        )
    )

    assert fake_red.calls == 1  # no second call
    assert response.language_contract_status == "mismatched"
    assert response.observed_language == "et"
    assert response.language_recovery_attempted is False
    assert response.text == _ESTONIAN_TEXT  # artifact preserved regardless


# -- budget guard applies to the recovery call, exactly like truncation -----


def test_language_recovery_respects_run_budget_guard(monkeypatch):
    from decimal import Decimal

    provider = _FixedProvider([_response(_ESTONIAN_TEXT, cost=0.5)])
    orch = _orchestrator_with_provider(monkeypatch, a=provider)
    guard = RunBudgetGuard(budget_usd=Decimal("0.5"), spent_usd=Decimal("0"))

    with pytest.raises(ProviderGenerationError) as excinfo:
        asyncio.run(
            orch.run_stage(
                "analysis_a", "Estonian question here", {},
                output_language="et", working_language="en",
                budget_guard=guard,
            )
        )

    assert excinfo.value.reason == "run_budget_exceeded"
    assert len(provider.calls) == 1  # the recovery call itself was never sent
