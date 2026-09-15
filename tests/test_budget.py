"""Offline tests for the per-run cost budget: parsing/validation, the
pre-run estimate, and the runtime hard guard (cost_budget.py). No network
calls anywhere in this file -- RunBudgetGuard operates purely on Decimal
arithmetic, and the estimator only reads the static PRICING table.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from llm_deliberation.cost_budget import (
    BudgetExceededError,
    InvalidBudgetError,
    RunBudgetGuard,
    default_budget_from_float,
    estimate_max_call_cost_usd,
    estimate_run_cost_range,
    parse_budget_usd,
)
from llm_deliberation.orchestrator import ALL_STAGE_NAMES
from llm_deliberation.providers import ProviderGenerationError

# -- parse_budget_usd -------------------------------------------------------


def test_parse_budget_none_means_no_cap():
    assert parse_budget_usd(None) is None


def test_parse_budget_empty_string_means_no_cap():
    assert parse_budget_usd("") is None
    assert parse_budget_usd("   ") is None


def test_parse_budget_positive_value_parses_to_decimal():
    assert parse_budget_usd("0.75") == Decimal("0.75")


@pytest.mark.parametrize("raw", ["0", "-1", "-0.01", "abc", "NaN", "Infinity"])
def test_parse_budget_rejects_invalid_values(raw):
    with pytest.raises(InvalidBudgetError):
        parse_budget_usd(raw)


# -- pre-run estimate --------------------------------------------------------


def test_estimate_run_cost_range_is_a_low_high_pair():
    estimate = estimate_run_cost_range(
        openai_model="gpt-5.6-sol",
        anthropic_model="claude-opus-5",
        gemini_model="gemini-3.8-flash",
        convergence_provider="anthropic",
        convergence_model="claude-opus-5",
        red_team_enabled=True,
        max_output_tokens=5000,
    )
    assert Decimal(0) < estimate.low_usd < estimate.high_usd


def test_estimate_run_cost_range_red_team_adds_cost():
    kwargs = dict(
        openai_model="gpt-5.6-sol",
        anthropic_model="claude-opus-5",
        gemini_model="gemini-3.8-flash",
        convergence_provider="anthropic",
        convergence_model="claude-opus-5",
        max_output_tokens=5000,
    )
    without = estimate_run_cost_range(red_team_enabled=False, **kwargs)
    with_red_team = estimate_run_cost_range(red_team_enabled=True, **kwargs)
    assert with_red_team.high_usd > without.high_usd


def test_estimate_run_cost_range_scales_with_output_budget():
    kwargs = dict(
        openai_model="gpt-5.6-sol",
        anthropic_model="claude-opus-5",
        gemini_model="gemini-3.8-flash",
        convergence_provider="anthropic",
        convergence_model="claude-opus-5",
        red_team_enabled=True,
    )
    small = estimate_run_cost_range(max_output_tokens=1000, **kwargs)
    large = estimate_run_cost_range(max_output_tokens=10000, **kwargs)
    assert large.high_usd > small.high_usd


def test_estimate_run_cost_range_unpriced_model_contributes_zero_not_a_crash():
    estimate = estimate_run_cost_range(
        openai_model="totally-unpriced-model",
        anthropic_model="also-unpriced",
        gemini_model="unpriced-gemini",
        convergence_provider="anthropic",
        convergence_model="also-unpriced",
        red_team_enabled=False,
        max_output_tokens=5000,
    )
    assert estimate.low_usd == Decimal(0)
    assert estimate.high_usd == Decimal(0)


# -- estimate_max_call_cost_usd (runtime upper bound) ------------------------


def test_estimate_max_call_cost_usd_grows_with_output_tokens():
    small = estimate_max_call_cost_usd("gpt-5.6-sol", prompt_chars=100, max_output_tokens=100)
    large = estimate_max_call_cost_usd("gpt-5.6-sol", prompt_chars=100, max_output_tokens=100_000)
    assert large > small


def test_estimate_max_call_cost_usd_unpriced_model_is_zero():
    assert estimate_max_call_cost_usd("no-such-model", prompt_chars=1000, max_output_tokens=5000) == Decimal(0)


# -- RunBudgetGuard: the runtime hard guard ----------------------------------


def test_guard_with_no_budget_never_blocks():
    guard = RunBudgetGuard(budget_usd=None)
    guard.check(Decimal("999999"))  # must not raise
    assert guard.remaining_usd() is None


def test_guard_blocks_when_next_request_would_exceed_budget():
    guard = RunBudgetGuard(budget_usd=Decimal("0.75"), spent_usd=Decimal("0.61"))
    with pytest.raises(BudgetExceededError) as excinfo:
        guard.check(Decimal("0.18"))
    assert excinfo.value.spent_usd == Decimal("0.61")
    assert excinfo.value.budget_usd == Decimal("0.75")
    assert excinfo.value.estimated_next_usd == Decimal("0.18")


def test_guard_allows_exact_fit_at_the_boundary():
    guard = RunBudgetGuard(budget_usd=Decimal("0.75"), spent_usd=Decimal("0.60"))
    guard.check(Decimal("0.15"))  # 0.60 + 0.15 == 0.75, must not raise


def test_guard_record_actual_advances_spent():
    guard = RunBudgetGuard(budget_usd=Decimal("1.00"))
    guard.record_actual(Decimal("0.40"))
    guard.record_actual(Decimal("0.40"))
    assert guard.spent_usd == Decimal("0.80")
    with pytest.raises(BudgetExceededError):
        guard.check(Decimal("0.25"))  # 0.80 + 0.25 > 1.00


def test_guard_resync_overwrites_rather_than_accumulates():
    guard = RunBudgetGuard(budget_usd=Decimal("1.00"), spent_usd=Decimal("0.90"))
    guard.resync(Decimal("0.30"))  # authoritative DB total corrects drift
    assert guard.spent_usd == Decimal("0.30")


def test_default_budget_from_float_round_trips_cleanly():
    assert default_budget_from_float(0.1 + 0.2) == Decimal("0.3")


# -- end-to-end via DeliberationService (fake orchestrator, no network) -----


def test_run_with_no_budget_preserves_existing_behavior(service):
    run_id = service.create_run("Q?", "economy", red_team_enabled=True)
    record = asyncio.run(service.start_run(run_id))
    assert record.status == "succeeded"
    assert record.max_run_cost_usd is None


def test_positive_budget_persists_with_run(service):
    run_id = service.create_run("Q?", "economy", red_team_enabled=False, max_run_cost_usd="0.75")
    record = service.get_run(run_id)
    assert record.max_run_cost_usd == "0.75"


@pytest.mark.parametrize("raw", ["0", "-1", "not-a-number"])
def test_invalid_budget_rejected_by_create_run(service, raw):
    with pytest.raises(InvalidBudgetError):
        service.create_run("Q?", "economy", red_team_enabled=False, max_run_cost_usd=raw)


def test_sufficient_budget_allows_run_to_succeed(service):
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=True, max_run_cost_usd="10.00"
    )
    record = asyncio.run(service.start_run(run_id))
    assert record.status == "succeeded"


def test_insufficient_budget_blocks_a_stage_before_any_call(
    service, fake_orchestrator_state
):
    fake_orchestrator_state["upper_bound_cost_usd"] = Decimal("1.00")
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, max_run_cost_usd="0.0001"
    )
    record = asyncio.run(service.start_run(run_id))

    assert record.status == "failed"
    analysis_a = next(s for s in record.stages if s.name == "analysis_a")
    assert analysis_a.status == "failed"
    assert analysis_a.failure_reason == "run_budget_exceeded"
    assert analysis_a.estimated_cost_usd == 0.0
    # Zero additional provider calls: FakeOrchestrator.run_stage logs every
    # call it actually receives -- a blocked stage must never appear in it.
    assert "analysis_a" not in fake_orchestrator_state["log"]


def test_blocked_stage_causes_zero_additional_provider_calls(
    service, fake_orchestrator_state
):
    # Budget fits exactly one stage's upper bound, not two -- analysis_b
    # (the second name in its wave) must never be dispatched.
    fake_orchestrator_state["upper_bound_cost_usd"] = Decimal("0.50")
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, max_run_cost_usd="0.50"
    )
    record = asyncio.run(service.start_run(run_id))

    assert fake_orchestrator_state["log"].count("analysis_a") == 1
    assert "analysis_b" not in fake_orchestrator_state["log"]
    analysis_b = next(s for s in record.stages if s.name == "analysis_b")
    assert analysis_b.failure_reason == "run_budget_exceeded"


def test_required_stage_is_never_silently_skipped_due_to_budget(
    service, fake_orchestrator_state
):
    fake_orchestrator_state["upper_bound_cost_usd"] = Decimal("1.00")
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, max_run_cost_usd="0.0001"
    )
    record = asyncio.run(service.start_run(run_id))
    analysis_a = next(s for s in record.stages if s.name == "analysis_a")
    # "Skipped" (a distinct status from "failed") is reserved for an
    # explicit user action on an optional stage -- a required stage blocked
    # by budget must be "failed", never silently "skipped".
    assert analysis_a.status == "failed"
    assert record.status == "failed"


def test_optional_red_team_blocked_by_budget_is_failed_not_silently_skipped(
    service, fake_orchestrator_state
):
    # Every other stage has a negligible upper bound; only red_team's is
    # deliberately priced above the whole budget, so it -- and only it --
    # is blocked in its wave (see conftest.FakeOrchestrator.stage_upper_bound_cost).
    fake_orchestrator_state["upper_bound_cost_usd"] = {"red_team": Decimal("5.00")}
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=True, max_run_cost_usd="1.00"
    )
    record = asyncio.run(service.start_run(run_id))

    red_team = next(s for s in record.stages if s.name == "red_team")
    assert red_team.status == "failed"
    assert red_team.failure_reason == "run_budget_exceeded"
    critique_a = next(s for s in record.stages if s.name == "critique_a_of_b")
    assert critique_a.status == "succeeded"  # unaffected sibling in the same wave
    assert "red_team" not in fake_orchestrator_state["log"]
    # Still explicitly skippable afterward -- reuses the existing
    # SKIPPABLE_STAGE_NAMES / skip_stage() flow, no new mechanism needed.
    from llm_deliberation.orchestrator import SKIPPABLE_STAGE_NAMES

    assert "red_team" in SKIPPABLE_STAGE_NAMES


def test_optional_red_team_can_be_skipped_after_budget_block(
    service, fake_orchestrator_state
):
    fake_orchestrator_state["upper_bound_cost_usd"] = {"red_team": Decimal("5.00")}
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=True, max_run_cost_usd="1.00"
    )
    record = asyncio.run(service.start_run(run_id))
    red_team = next(s for s in record.stages if s.name == "red_team")
    assert red_team.failure_reason == "run_budget_exceeded"

    record = asyncio.run(service.skip_stage(run_id, red_team.id))
    red_team = next(s for s in record.stages if s.name == "red_team")
    assert red_team.status == "skipped"
    assert record.status == "succeeded"


def test_resume_works_after_budget_is_increased(service, fake_orchestrator_state):
    fake_orchestrator_state["upper_bound_cost_usd"] = Decimal("1.00")
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, max_run_cost_usd="0.0001"
    )
    record = asyncio.run(service.start_run(run_id))
    assert record.status == "failed"
    assert "analysis_a" not in fake_orchestrator_state["log"]

    service.update_run_budget(run_id, "10.00")
    fake_orchestrator_state["upper_bound_cost_usd"] = Decimal("0.0001")
    record = asyncio.run(service.resume_run(run_id))

    assert record.status == "succeeded"
    assert "analysis_a" in fake_orchestrator_state["log"]


def test_completed_stages_and_cost_survive_a_budget_block(
    service, fake_orchestrator_state
):
    fake_orchestrator_state["upper_bound_cost_usd"] = Decimal("0.0001")
    fake_orchestrator_state["responses"] = {
        "analysis_a": {"estimated_cost_usd": 0.40},
        "analysis_b": {"estimated_cost_usd": 0.40},
    }
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, max_run_cost_usd="0.80"
    )
    record = asyncio.run(service.start_run(run_id))

    analysis_a = next(s for s in record.stages if s.name == "analysis_a")
    analysis_b = next(s for s in record.stages if s.name == "analysis_b")
    assert analysis_a.status == "succeeded"
    assert analysis_b.status == "succeeded"
    assert record.estimated_total_cost_usd == pytest.approx(0.80)

    critique_a = next(s for s in record.stages if s.name == "critique_a_of_b")
    assert critique_a.failure_reason == "run_budget_exceeded"


def test_budget_check_itself_does_not_alter_accumulated_cost(
    service, fake_orchestrator_state
):
    fake_orchestrator_state["upper_bound_cost_usd"] = Decimal("1.00")
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, max_run_cost_usd="0.0001"
    )
    record = asyncio.run(service.start_run(run_id))
    assert record.estimated_total_cost_usd == 0.0


def test_truncation_recovery_respects_budget():
    """orchestrator.run_stage's own truncation-recovery call must refuse to
    fire when it would exceed the budget -- exercised directly against the
    real orchestrator machinery (not FakeOrchestrator), using a stand-in
    Provider so no network call happens.
    """
    from llm_deliberation.orchestrator import DeliberationOrchestrator
    from llm_deliberation.types import ModelResponse, Usage

    class _TruncatingProvider:
        provider_name = "OpenAI"
        model = "gpt-5.6-sol"
        max_output_tokens = 100

        def __init__(self):
            self.calls = 0

        def generate(self, *, system: str, prompt: str) -> ModelResponse:
            self.calls += 1
            return ModelResponse(
                provider="OpenAI",
                model=self.model,
                text="truncated partial answer",
                usage=Usage(input_tokens=10, output_tokens=100),
                estimated_cost_usd=0.05,
                requested_model=self.model,
                incomplete_reason="output_truncated",
            )

    class _Settings:
        openai_model = "gpt-5.6-sol"
        anthropic_model = "claude-opus-5"
        gemini_model = "gemini-3.8-flash"
        gemini_fallback_models: list[str] = []
        openai_effort = "high"
        anthropic_effort = "high"
        gemini_thinking_level = "high"
        max_output_tokens = 100
        provider_timeout_seconds = 5.0
        provider_max_retries = 0
        convergence_provider = "anthropic"
        convergence_model = "claude-opus-5"

    orchestrator = DeliberationOrchestrator.__new__(DeliberationOrchestrator)
    provider = _TruncatingProvider()
    orchestrator.a = provider
    orchestrator.settings = _Settings()

    guard = RunBudgetGuard(budget_usd=Decimal("0.05"))  # exactly the sunk cost, no room for recovery

    with pytest.raises(ProviderGenerationError) as excinfo:
        asyncio.run(
            orchestrator.run_stage(
                "analysis_a", "question?", {}, budget_guard=guard
            )
        )
    assert excinfo.value.reason == "run_budget_exceeded"
    assert excinfo.value.estimated_cost_usd == pytest.approx(0.05)  # sunk cost preserved
    assert provider.calls == 1  # the recovery call itself was never made
