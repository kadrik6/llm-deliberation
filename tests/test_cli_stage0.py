"""Offline tests for the CLI's --question-framing interactive flow (ADR
009). Every provider call is faked via the same FakeOrchestrator used by
the web/service test suite (see tests/conftest.py) -- no live API calls.

cli._run() is tested directly (not main(), which wraps it in SystemExit),
with sys.stdin.isatty()/input() monkeypatched to drive the interactive
prompts deterministically.
"""
from __future__ import annotations

import argparse
import asyncio

import pytest

from llm_deliberation import cli
from llm_deliberation import service as service_module
from tests.conftest import FakeOrchestrator, _always_ready_readiness_report


@pytest.fixture
def stage0_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("LLM_DELIBERATION_DB", str(tmp_path / "deliberation.db"))

    state = {"log": [], "fail": set()}

    def factory(settings):
        return FakeOrchestrator(settings, state=state)

    monkeypatch.setattr(service_module, "DeliberationOrchestrator", factory)
    monkeypatch.setattr(service_module.readiness, "check_run_readiness", _always_ready_readiness_report)
    return state


def _args(**overrides) -> argparse.Namespace:
    defaults = dict(
        question="Should we do X?",
        profile=None,
        language=None,
        red_team=False,
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


def test_question_framing_off_by_default_skips_stage0_entirely(stage0_env, capsys):
    args = _args(question_framing=False)
    exit_code = asyncio.run(cli._run(args))
    assert exit_code == 0
    assert "Stage 0" not in capsys.readouterr().out


def test_question_framing_non_interactive_locks_the_original_question(
    stage0_env, monkeypatch, capsys, tmp_path
):
    """Piped/non-interactive stdin (sys.stdin.isatty() is False): the
    interview and the two framing-analysis calls are skipped entirely
    (answers would be empty), and Stage 0 locks with the original question
    unchanged -- 1 paid call, not 3."""
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)

    args = _args(question_framing=True, question="Should we do X?")
    exit_code = asyncio.run(cli._run(args))
    assert exit_code == 0

    out = capsys.readouterr().out
    assert "Stage 0" in out
    assert "Non-interactive input detected" in out
    assert "Candidate A" not in out
    assert "Candidate B" not in out
    assert "Locked. Proceeding with: Should we do X?" in out


def test_question_framing_non_interactive_never_calls_framing_analysis(
    stage0_env, monkeypatch
):
    """Regression for the skip itself (not just its printed note): the two
    framing-analysis calls must never actually be dispatched when
    non-interactive."""
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)

    args = _args(question_framing=True)
    asyncio.run(cli._run(args))

    assert "question_framing" in stage0_env["log"]
    assert "framing_analysis_a" not in stage0_env["log"]
    assert "framing_analysis_b" not in stage0_env["log"]


def test_question_framing_interactive_locks_a_reframed_question(stage0_env, monkeypatch, capsys):
    answers = iter([
        "answer 1", "answer 2", "answer 3",  # interview answers
        "Reframed question?",  # final question prompt
        "A clear outcome",  # success criterion prompt
    ])
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_args, **_kwargs: next(answers))

    args = _args(question_framing=True)
    exit_code = asyncio.run(cli._run(args))
    assert exit_code == 0
    assert "Locked. Proceeding with: Reframed question?" in capsys.readouterr().out


def test_question_framing_interactive_can_keep_the_original_question(stage0_env, monkeypatch, capsys):
    answers = iter([
        "answer 1", "answer 2", "answer 3",
        "",  # press Enter -- keep the original
        "",  # no success criterion
    ])
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_args, **_kwargs: next(answers))

    args = _args(question_framing=True, question="Original question?")
    exit_code = asyncio.run(cli._run(args))
    assert exit_code == 0
    assert "Locked. Proceeding with: Original question?" in capsys.readouterr().out


def test_question_framing_failure_aborts_before_start_run(stage0_env, monkeypatch, capsys):
    from llm_deliberation.providers import ProviderGenerationError

    error = ProviderGenerationError(
        "response did not match the expected schema",
        requested_model="fake-model",
        attempts=1,
        attempt_log=[],
        fallback_used=False,
        fallback_reason=None,
        estimated_cost_usd=0.02,
        reason="structured_output_invalid",
    )
    stage0_env["fail_with"] = {"question_framing": error}
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)

    args = _args(question_framing=True)
    exit_code = asyncio.run(cli._run(args))

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "Stage 0 (question framing) failed" in err
    assert "was created but not started" in err


def test_question_framing_parse_failure_shows_friendly_message_not_a_crash(
    stage0_env, monkeypatch, capsys
):
    """Regression: a malformed response that FakeOrchestrator hands back
    verbatim (i.e. one that never went through the real orchestrator's
    own validation) raises a bare QuestionFramingParseError from inside
    service.run_question_framing's own re-parse -- cli.py must catch this
    specific type too, not just ProviderGenerationError, and show the same
    friendly message instead of letting it propagate as an unhandled
    exception."""
    stage0_env["responses"] = {"question_framing": {"text": "not valid json at all"}}
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)

    args = _args(question_framing=True)
    exit_code = asyncio.run(cli._run(args))

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "Stage 0 (question framing) failed" in err
    assert "was created but not started" in err


def test_question_framing_budget_exceeded_shows_friendly_message_not_a_crash(
    stage0_env, monkeypatch, capsys
):
    """Defensive coverage for the same friendly-message path if a bare
    BudgetExceededError were ever raised directly (today it is always
    converted to ProviderGenerationError inside orchestrator._call_budgeted,
    but cli.py's except clause must not depend on that staying true)."""
    from llm_deliberation.cost_budget import BudgetExceededError
    from decimal import Decimal

    async def _raise_budget_exceeded(self, run_id):
        raise BudgetExceededError(
            spent_usd=Decimal("0.50"), budget_usd=Decimal("0.50"), estimated_next_usd=Decimal("0.10"),
        )

    monkeypatch.setattr(service_module.DeliberationService, "run_question_framing", _raise_budget_exceeded)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)

    args = _args(question_framing=True)
    exit_code = asyncio.run(cli._run(args))

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "Stage 0 (question framing) failed" in err
    assert "was created but not started" in err


def test_question_framing_does_not_affect_a_run_without_the_flag(stage0_env, capsys):
    """Byte-for-byte identical to pre-feature behavior when the flag is
    omitted -- no stage0 row, no extra calls."""
    args = _args(question_framing=False)
    asyncio.run(cli._run(args))
    assert "question_framing" not in stage0_env["log"]
