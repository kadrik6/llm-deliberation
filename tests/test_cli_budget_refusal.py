"""Offline tests for the CLI's pre-run budget-sufficiency refusal (ADR
010): --max-cost already below a run's own worst-case estimate is refused
before create_run() is ever called, instead of being admitted and only
discovering mid-run that it cannot finish. Every provider call is faked
via FakeOrchestrator (see tests/conftest.py) -- no live API calls.

MAX_OUTPUT_TOKENS is always set explicitly in these tests (never left to
whatever a developer's own local .env happens to have) so the worst-case
numbers asserted here are deterministic regardless of machine.
"""
from __future__ import annotations

import argparse
import asyncio

import pytest

from llm_deliberation import cli
from llm_deliberation import service as service_module
from tests.conftest import FakeOrchestrator, _always_ready_readiness_report


@pytest.fixture
def budget_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("MAX_OUTPUT_TOKENS", "16000")
    monkeypatch.setenv("LLM_DELIBERATION_DB", str(tmp_path / "deliberation.db"))

    state = {"log": [], "fail": set()}

    def factory(settings):
        return FakeOrchestrator(settings, state=state)

    monkeypatch.setattr(service_module, "DeliberationOrchestrator", factory)
    monkeypatch.setattr(service_module.readiness, "check_run_readiness", _always_ready_readiness_report)
    return state


def _args(**overrides) -> argparse.Namespace:
    defaults = dict(
        question="Q?",
        profile="balanced",
        red_team=True,
        context=None,
        output=None,
        resume=None,
        retry_stage=None,
        list_runs=False,
        diagnose_run=None,
        max_cost=None,
        check_providers=False,
        question_framing=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_max_cost_below_worst_case_is_refused_before_any_run_is_created(budget_env):
    args = _args(max_cost="1.00")  # balanced + red-team's real worst case is ~$2.98
    exit_code = asyncio.run(cli._run(args))

    assert exit_code == 2
    # No run was ever created -- the refusal happens before create_run().
    from llm_deliberation.service import DeliberationService

    service = DeliberationService()
    assert service.list_runs() == []
    assert budget_env["log"] == []  # zero provider calls


def test_max_cost_below_worst_case_message_names_the_needed_amount(budget_env, capsys):
    args = _args(max_cost="1.00")
    asyncio.run(cli._run(args))
    err = capsys.readouterr().err
    assert "--max-cost 1.00 is below" in err
    assert "$2.9840" in err
    assert "ceiling, not expected spend" in err


def test_max_cost_at_or_above_worst_case_is_accepted(budget_env):
    # Comfortably above the real ~$2.98 worst case for balanced + red-team.
    args = _args(max_cost="5.00")
    exit_code = asyncio.run(cli._run(args))
    assert exit_code == 0

    from llm_deliberation.service import DeliberationService

    service = DeliberationService()
    runs = service.list_runs()
    assert len(runs) == 1
    assert runs[0].status == "succeeded"


def test_no_max_cost_is_never_refused(budget_env):
    """No cap at all -- nothing to refuse, exactly today's behavior."""
    args = _args(max_cost=None)
    exit_code = asyncio.run(cli._run(args))
    assert exit_code == 0


def test_refusal_accounts_for_red_team_being_declined_via_the_gemini_prompt(
    budget_env, monkeypatch, capsys
):
    """The worst-case estimate must reflect the FINAL red_team_enabled
    state (after the Gemini-unavailable prompt may have turned it off),
    not the originally-requested one -- the refusal message must name the
    (lower) red-team-off estimate, not the red-team-on one."""
    from llm_deliberation.readiness import ProviderReadiness, ReadinessReport, _now_iso

    def _blocked_gemini(settings, *, red_team_enabled, force=False):
        return ReadinessReport(
            openai=ProviderReadiness(
                provider="openai", configured_model=settings.openai_model, status="ready",
                checked_at=_now_iso(), check_type="model_retrieve", paid_probe=False,
                estimated_probe_cost_usd=0.0, user_message="ok",
            ),
            anthropic=ProviderReadiness(
                provider="anthropic", configured_model=settings.anthropic_model, status="ready",
                checked_at=_now_iso(), check_type="model_retrieve", paid_probe=False,
                estimated_probe_cost_usd=0.0, user_message="ok",
            ),
            gemini=ProviderReadiness(
                provider="gemini", configured_model=settings.gemini_model, status="auth_error",
                checked_at=_now_iso(), check_type="model_get", paid_probe=False,
                estimated_probe_cost_usd=0.0, user_message="blocked",
            ),
        )

    monkeypatch.setattr(service_module.readiness, "check_run_readiness", _blocked_gemini)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "y")  # "Start without red-team? [y/N]"

    args = _args(max_cost="1.50", red_team=True)
    exit_code = asyncio.run(cli._run(args))

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "red-team=off" in err
    assert "$2.9232" in err  # the red-team-OFF estimate, not red-team-on's $2.9840
