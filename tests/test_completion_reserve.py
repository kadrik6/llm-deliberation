"""Offline tests for the completion-reserve feature (Part A of the
next-phase task): cost_budget.estimate_remaining_completion_reserve (pure)
and its wiring into service._execute's red_team admission check.

No network calls: pure-function tests construct minimal StageRecord values
directly; service-level tests use the existing FakeOrchestrator fixture
pattern (see conftest.py), exactly like test_budget.py.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from llm_deliberation.cost_budget import (
    RESERVED_FOR_COMPLETION_STAGE_NAMES,
    estimate_remaining_completion_reserve,
)
from llm_deliberation.orchestrator import RED_TEAM_COMPLETION_RESERVE_SKIP_REASON
from llm_deliberation.store import StageRecord

_MODELS = dict(
    openai_model="gpt-5.6-terra",
    anthropic_model="claude-sonnet-5",
    convergence_provider="anthropic",
    convergence_model="claude-sonnet-5",
    max_output_tokens=5000,
)


def _stage(name: str, status: str, **overrides) -> StageRecord:
    defaults = dict(
        id=1, run_id="r1", name=name, provider=None, model=None, status=status,
        attempt=0, input_tokens=0, output_tokens=0, estimated_cost_usd=0.0,
        error=None, started_at=None, completed_at=None,
    )
    defaults.update(overrides)
    return StageRecord(**defaults)


# ======================================================================
# Pure estimate_remaining_completion_reserve() tests
# ======================================================================


def test_completed_stages_excluded_from_reserve():
    stages = [_stage(name, "succeeded") for name in RESERVED_FOR_COMPLETION_STAGE_NAMES]
    reserve = estimate_remaining_completion_reserve(
        stages, budget_usd=Decimal("1.00"), spent_usd=Decimal("0.50"), **_MODELS
    )
    assert reserve.required_remaining_usd == Decimal(0)
    assert reserve.reserved_stage_names == ()
    assert reserve.optional_spendable_usd == Decimal("0.50")


def test_skipped_stage_excluded_from_reserve():
    stages = [_stage("convergence_analysis", "skipped")]
    reserve = estimate_remaining_completion_reserve(
        stages, budget_usd=Decimal("1.00"), spent_usd=Decimal("0"), **_MODELS
    )
    assert "convergence_analysis" not in reserve.reserved_stage_names


def test_pending_required_stage_is_reserved():
    stages = [_stage(name, "succeeded") for name in RESERVED_FOR_COMPLETION_STAGE_NAMES if name != "revision_a"]
    stages.append(_stage("revision_a", "pending"))
    reserve = estimate_remaining_completion_reserve(
        stages, budget_usd=Decimal("5.00"), spent_usd=Decimal("0"), **_MODELS
    )
    assert reserve.reserved_stage_names == ("revision_a",)
    assert reserve.required_remaining_usd > 0


def test_failed_required_stage_is_still_reserved_pending_retry():
    """A required stage that has already failed must still be reserved for
    -- it needs a retry/resume to reach completion, not a write-off."""
    stages = [_stage("revision_a", "failed")]
    reserve = estimate_remaining_completion_reserve(
        stages, budget_usd=Decimal("5.00"), spent_usd=Decimal("0"), **_MODELS
    )
    assert "revision_a" in reserve.reserved_stage_names


def test_synthesis_always_included_when_not_yet_succeeded():
    stages = [_stage(name, "succeeded") for name in RESERVED_FOR_COMPLETION_STAGE_NAMES if name != "synthesis"]
    stages.append(_stage("synthesis", "pending"))
    reserve = estimate_remaining_completion_reserve(
        stages, budget_usd=Decimal("5.00"), spent_usd=Decimal("0"), **_MODELS
    )
    assert reserve.reserved_stage_names == ("synthesis",)


def test_red_team_never_appears_in_reserved_stages():
    """red_team is deliberately not in RESERVED_FOR_COMPLETION_STAGE_NAMES --
    it is the one stage eligible for a proactive, budget-driven auto-skip,
    never something the reserve itself protects."""
    stages = [_stage(name, "succeeded") for name in RESERVED_FOR_COMPLETION_STAGE_NAMES]
    stages.append(_stage("red_team", "pending"))
    reserve = estimate_remaining_completion_reserve(
        stages, budget_usd=Decimal("5.00"), spent_usd=Decimal("0"), **_MODELS
    )
    assert reserve.reserved_stage_names == ()
    assert reserve.required_remaining_usd == Decimal(0)


def test_exclude_stage_names_avoids_double_counting_current_wave():
    stages = [
        _stage(name, "succeeded")
        for name in RESERVED_FOR_COMPLETION_STAGE_NAMES
        if name not in ("critique_a_of_b", "critique_b_of_a")
    ]
    stages += [_stage("critique_a_of_b", "pending"), _stage("critique_b_of_a", "pending")]
    reserve_with_exclude = estimate_remaining_completion_reserve(
        stages, budget_usd=Decimal("5.00"), spent_usd=Decimal("0"),
        exclude_stage_names=frozenset({"critique_a_of_b", "critique_b_of_a"}),
        **_MODELS,
    )
    assert reserve_with_exclude.required_remaining_usd == Decimal(0)

    reserve_without_exclude = estimate_remaining_completion_reserve(
        stages, budget_usd=Decimal("5.00"), spent_usd=Decimal("0"), **_MODELS
    )
    assert reserve_without_exclude.required_remaining_usd > 0


def test_no_cap_run_reports_none_remaining_and_none_spendable():
    """Mirrors today's exact "no cap" semantics -- see RunBudgetGuard."""
    reserve = estimate_remaining_completion_reserve(
        [], budget_usd=None, spent_usd=Decimal("0"), **_MODELS
    )
    assert reserve.remaining_budget_usd is None
    assert reserve.optional_spendable_usd is None


def test_optional_spendable_can_go_negative_when_reserve_exceeds_remaining():
    """A negative optional_spendable_usd is the exact signal that even
    optional spend of $0 would already threaten completion -- callers must
    still handle this (see the service-level auto-skip test), not clamp it
    away here."""
    stages = [_stage("synthesis", "pending")]
    reserve = estimate_remaining_completion_reserve(
        stages, budget_usd=Decimal("0.001"), spent_usd=Decimal("0"), **_MODELS
    )
    assert reserve.optional_spendable_usd < 0


# ======================================================================
# service._execute integration -- red_team admission
# ======================================================================


def test_red_team_admitted_when_request_and_reserve_both_fit(service, fake_orchestrator_state):
    """Ample budget: red_team proceeds exactly as before (unchanged path)."""
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=True, max_run_cost_usd="5.00"
    )
    record = asyncio.run(service.start_run(run_id))

    red_team = next(s for s in record.stages if s.name == "red_team")
    assert red_team.status == "succeeded"
    assert "red_team" in fake_orchestrator_state["log"]
    assert record.status == "succeeded"


def test_red_team_skipped_when_it_would_consume_the_reserve(service, fake_orchestrator_state):
    """A tight budget where red_team's own (realistic, not artificially
    huge) upper bound would leave less than the required remaining stages
    need -- auto-skipped, run still completes."""
    fake_orchestrator_state["upper_bound_cost_usd"] = {"red_team": Decimal("0.30")}
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=True, max_run_cost_usd="0.35"
    )
    record = asyncio.run(service.start_run(run_id))

    red_team = next(s for s in record.stages if s.name == "red_team")
    assert red_team.status == "skipped"
    assert red_team.fallback_reason == RED_TEAM_COMPLETION_RESERVE_SKIP_REASON
    assert "red_team" not in fake_orchestrator_state["log"]
    assert record.status == "succeeded"


def test_no_budget_cap_preserves_current_red_team_behavior(service, fake_orchestrator_state):
    """No max_run_cost_usd at all -- the reserve check is a complete no-op
    (budget_usd is None), reproducing today's exact unbudgeted behavior."""
    run_id = service.create_run("Q?", "economy", red_team_enabled=True)
    record = asyncio.run(service.start_run(run_id))

    red_team = next(s for s in record.stages if s.name == "red_team")
    assert red_team.status == "succeeded"
    assert "red_team" in fake_orchestrator_state["log"]


def test_required_stage_can_consume_reserved_capacity_without_being_blocked(
    service, fake_orchestrator_state
):
    """The reserve is a protective calculation, never an artificial ceiling
    on the required stages it protects -- a required stage may legitimately
    spend right up to what was reserved for it."""
    fake_orchestrator_state["upper_bound_cost_usd"] = Decimal("0.05")
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, max_run_cost_usd="1.00"
    )
    record = asyncio.run(service.start_run(run_id))
    assert record.status == "succeeded"
    assert all(s.status == "succeeded" for s in record.stages)


def test_budget_shortfall_for_a_required_stage_still_pauses_cleanly(
    service, fake_orchestrator_state
):
    """Existing, unchanged behavior: when even a required stage cannot fit,
    the run pauses with the existing typed run_budget_exceeded reason --
    the completion-reserve feature only ever changes red_team's path."""
    fake_orchestrator_state["upper_bound_cost_usd"] = Decimal("5.00")
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, max_run_cost_usd="0.01"
    )
    record = asyncio.run(service.start_run(run_id))

    assert record.status == "failed"
    analysis_a = next(s for s in record.stages if s.name == "analysis_a")
    assert analysis_a.failure_reason == "run_budget_exceeded"


def test_raising_budget_and_resuming_continues_without_rerunning_successes(
    service, fake_orchestrator_state
):
    fake_orchestrator_state["upper_bound_cost_usd"] = Decimal("5.00")
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, max_run_cost_usd="0.01"
    )
    record = asyncio.run(service.start_run(run_id))
    assert record.status == "failed"
    assert "analysis_a" not in fake_orchestrator_state["log"]

    service.update_run_budget(run_id, "10.00")
    fake_orchestrator_state["upper_bound_cost_usd"] = Decimal("0.001")
    record = asyncio.run(service.resume_run(run_id))

    assert record.status == "succeeded"
    # Every stage's own log entry appears exactly once -- nothing reran.
    assert fake_orchestrator_state["log"].count("analysis_a") == 1


def test_exact_cost_accounting_unaffected_by_auto_skip(service, fake_orchestrator_state):
    fake_orchestrator_state["upper_bound_cost_usd"] = {"red_team": Decimal("0.30")}
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=True, max_run_cost_usd="0.35"
    )
    record = asyncio.run(service.start_run(run_id))

    red_team = next(s for s in record.stages if s.name == "red_team")
    assert red_team.estimated_cost_usd == 0.0  # never called, never charged
    # Total cost is exactly the sum of the OTHER (succeeded) stages' costs --
    # no phantom charge for the skipped stage.
    expected_total = sum(
        s.estimated_cost_usd for s in record.stages if s.name != "red_team"
    )
    assert record.estimated_total_cost_usd == pytest.approx(expected_total)
