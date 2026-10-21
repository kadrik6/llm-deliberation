"""Offline tests for the `llm-deliberate costs` CLI command. No live API
calls -- FakeOrchestrator populates the DB (see tests/conftest.py); the
costs command itself makes no provider calls at all (it only reads
already-persisted data), so most tests here don't even need the fake.
"""
from __future__ import annotations

import asyncio

import pytest

from llm_deliberation import cli
from llm_deliberation import service as service_module
from tests.conftest import FakeOrchestrator


@pytest.fixture
def costs_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("LLM_DELIBERATION_DB", str(tmp_path / "deliberation.db"))

    state = {"log": [], "fail": set()}

    def factory(settings):
        return FakeOrchestrator(settings, state=state)

    monkeypatch.setattr(service_module, "DeliberationOrchestrator", factory)
    return state


def test_run_costs_on_an_empty_database_shows_the_empty_state(costs_env, capsys):
    exit_code = asyncio.run(cli._run_costs([]))
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "No runs in the last 30 days." in out
    assert "estimated, from this app's own calls; not provider billing" in out


def test_run_costs_reflects_a_real_run(costs_env, capsys):
    service = service_module.DeliberationService()
    run_id = service.create_run("Should we do X?", "economy", red_team_enabled=False)
    asyncio.run(service.start_run(run_id))
    service.close()

    exit_code = asyncio.run(cli._run_costs([]))
    assert exit_code == 0
    out = capsys.readouterr().out
    assert run_id in out
    assert "By provider:" in out
    assert "By day:" in out
    assert "By run:" in out
    assert "Should we do X?" in out


def test_run_costs_shows_stage0_as_a_combined_line(costs_env, capsys):
    service = service_module.DeliberationService()
    run_id = service.create_run(
        "Q?", "economy", red_team_enabled=False, question_framing_enabled=True
    )
    problem_framing = asyncio.run(service.run_question_framing(run_id))
    service.record_interview_answers(run_id, ["" for _ in problem_framing.questions])
    asyncio.run(service.run_framing_analysis(run_id, "a"))
    asyncio.run(service.run_framing_analysis(run_id, "b"))
    service.lock_question_framing(run_id, final_question="Q?", success_criteria=None)
    asyncio.run(service.start_run(run_id))
    service.close()

    exit_code = asyncio.run(cli._run_costs([]))
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Stage 0:" in out
    assert "combined per run, not split by provider or call" in out


def test_main_dispatches_costs_before_the_normal_parser(costs_env, monkeypatch, capsys):
    """Regression: without the early dispatch in main(), argparse would
    treat the literal string "costs" as the positional `question` argument
    and try to run a deliberation about it."""
    monkeypatch.setattr(cli.sys, "argv", ["llm-deliberate", "costs"])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "Cost report" in out
    assert "Run ID:" not in out  # never entered the deliberation flow


def test_costs_help_flag_does_not_crash(costs_env, capsys):
    with pytest.raises(SystemExit) as exc_info:
        asyncio.run(cli._run_costs(["--help"]))
    assert exc_info.value.code == 0
