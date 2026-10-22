"""Offline tests for the CLI's --language flag and its Estonian-question
refusal check. Every provider call is faked via FakeOrchestrator (see
tests/conftest.py) -- no live API calls.

Root cause this addresses: cli.py never exposed an output-language choice
at all (only the web form had one) -- every CLI run silently defaulted to
English regardless of what language the question was written in. Found
via --diagnose-run on a real run (22ad158a3b2d) whose Estonian question
got an English synthesis.
"""
from __future__ import annotations

import argparse
import asyncio

import pytest

from llm_deliberation import cli
from llm_deliberation import service as service_module
from tests.conftest import FakeOrchestrator, _always_ready_readiness_report

# 27 words, well over MIN_WORDS_FOR_CONFIDENCE (25) -- confidently detected
# as Estonian by language_detect.detect_language() (verified directly:
# returns ("et", ...)). Deliberately NOT the short real-world question
# that originally motivated this (see test_short_estonian_question_is_
# never_refused_even_though_it_is_estonian below for why).
_LONG_ESTONIAN_QUESTION = (
    "Palun selgita, mis on need peamised võimalused, kuidas Eesti riik "
    "saaks oma eelarve tasakaalu parandada, kui arvestada praegust "
    "majanduslikku olukorda ja tulevasi väljakutseid, mida tuleks "
    "kõigepealt kaaluda"
)

# The actual real-world question that originally surfaced this gap
# (run 22ad158a3b2d) -- only 8 words, too short for detect_language() to
# classify confidently (returns None, "uncertain"). Kept here specifically
# to prove the new refusal does NOT cover this exact case, honestly.
_SHORT_ESTONIAN_QUESTION = "Millised on parimad võimalused eesti riigieelarve tasakaalu saamiseks"


@pytest.fixture
def language_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("LLM_DELIBERATION_DB", str(tmp_path / "deliberation.db"))
    monkeypatch.delenv("LLM_LANGUAGE", raising=False)

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


# -- the --language flag itself ---------------------------------------------


def test_language_defaults_to_en_when_nothing_is_set(language_env):
    args = _args(question="Hello?")
    exit_code = asyncio.run(cli._run(args))
    assert exit_code == 0

    from llm_deliberation.service import DeliberationService

    record = DeliberationService().list_runs()[0]
    assert record.language == "en"


def test_explicit_language_et_is_used(language_env):
    args = _args(question=_LONG_ESTONIAN_QUESTION, language="et")
    exit_code = asyncio.run(cli._run(args))
    assert exit_code == 0

    from llm_deliberation.service import DeliberationService

    record = DeliberationService().list_runs()[0]
    assert record.language == "et"


def test_llm_language_env_var_is_used_when_flag_is_omitted(language_env, monkeypatch):
    monkeypatch.setenv("LLM_LANGUAGE", "et")
    args = _args(question=_LONG_ESTONIAN_QUESTION)
    exit_code = asyncio.run(cli._run(args))
    assert exit_code == 0

    from llm_deliberation.service import DeliberationService

    record = DeliberationService().list_runs()[0]
    assert record.language == "et"


def test_explicit_flag_wins_over_llm_language_env_var(language_env, monkeypatch):
    monkeypatch.setenv("LLM_LANGUAGE", "et")
    args = _args(question="Hello?", language="en")
    exit_code = asyncio.run(cli._run(args))
    assert exit_code == 0

    from llm_deliberation.service import DeliberationService

    record = DeliberationService().list_runs()[0]
    assert record.language == "en"


def test_language_choices_are_rejected_by_the_real_parser():
    with pytest.raises(SystemExit):
        cli._parser().parse_args(["Q?", "--language", "fr"])


def test_language_status_line_is_printed(language_env, capsys):
    args = _args(question="Hello?", language="et")
    asyncio.run(cli._run(args))
    out = capsys.readouterr().out
    assert "language=et" in out


# -- the Estonian-question refusal -------------------------------------------


def test_long_estonian_question_without_language_flag_is_refused(language_env):
    before = len(service_module.DeliberationService().list_runs())
    args = _args(question=_LONG_ESTONIAN_QUESTION)
    exit_code = asyncio.run(cli._run(args))

    assert exit_code == 2
    assert len(service_module.DeliberationService().list_runs()) == before  # no run created
    assert language_env["log"] == []  # zero provider calls


def test_refusal_message_hints_at_the_fix(language_env, capsys):
    args = _args(question=_LONG_ESTONIAN_QUESTION)
    asyncio.run(cli._run(args))
    err = capsys.readouterr().err
    assert "looks Estonian" in err
    assert "--language et" in err
    assert "--language en" in err


def test_explicit_language_et_is_never_refused_for_an_estonian_question(language_env):
    args = _args(question=_LONG_ESTONIAN_QUESTION, language="et")
    exit_code = asyncio.run(cli._run(args))
    assert exit_code == 0


def test_explicit_language_en_is_respected_even_for_an_estonian_question(language_env):
    """An explicit choice is never second-guessed, even one that will
    produce English output for Estonian input -- "no silent inference"
    means the CHECK is skipped once the user has decided, not that the
    tool overrides a decision it disagrees with."""
    args = _args(question=_LONG_ESTONIAN_QUESTION, language="en")
    exit_code = asyncio.run(cli._run(args))
    assert exit_code == 0

    from llm_deliberation.service import DeliberationService

    record = DeliberationService().list_runs()[0]
    assert record.language == "en"


def test_llm_language_et_env_var_is_never_refused_for_an_estonian_question(
    language_env, monkeypatch
):
    """If LLM_LANGUAGE=et is already configured, there is no English-by-
    default mismatch to warn about at all."""
    monkeypatch.setenv("LLM_LANGUAGE", "et")
    args = _args(question=_LONG_ESTONIAN_QUESTION)
    exit_code = asyncio.run(cli._run(args))
    assert exit_code == 0


def test_an_english_question_is_never_refused(language_env):
    args = _args(question="What should we do about rising shipping costs this quarter?")
    exit_code = asyncio.run(cli._run(args))
    assert exit_code == 0


def test_short_estonian_question_is_refused_via_estonian_letters(language_env):
    """The exact real-world question (run 22ad158a3b2d) that originally
    surfaced the missing --language flag. Too short for detect_language()
    to classify, so the refusal relies on its Estonian-only letters."""
    from llm_deliberation.language_detect import detect_language

    detected, _evidence = detect_language(_SHORT_ESTONIAN_QUESTION)
    assert detected is None  # confirms the premise of this test

    args = _args(question=_SHORT_ESTONIAN_QUESTION)
    exit_code = asyncio.run(cli._run(args))
    assert exit_code == 2
    assert language_env["log"] == []


def test_short_question_without_estonian_letters_is_not_refused(language_env):
    """Known gap: a short Estonian question with no õäöüšž and too few
    words for detect_language() still defaults to English unrefused."""
    args = _args(question="Kas Eesti peaks makse muutma")
    assert asyncio.run(cli._run(args)) == 0


def test_llm_language_en_env_var_is_still_refused_for_an_estonian_question(
    language_env, monkeypatch
):
    """.env.example ships LLM_LANGUAGE=en, so that value is not treated as
    a deliberate choice -- only the --language flag is."""
    monkeypatch.setenv("LLM_LANGUAGE", "en")
    args = _args(question=_SHORT_ESTONIAN_QUESTION)
    assert asyncio.run(cli._run(args)) == 2


def test_llm_language_in_dotenv_file_is_read(language_env, tmp_path, monkeypatch):
    """LLM_LANGUAGE set only in .env (not exported) must still apply:
    the language is resolved before Settings.load() would load .env."""
    from dotenv import dotenv_values

    from llm_deliberation import config as config_module

    env_file = tmp_path / ".env"
    env_file.write_text("LLM_LANGUAGE=et\n", encoding="utf-8")

    # Stand-in for load_dotenv() pointed at this test's own .env, going
    # through monkeypatch so the value cannot leak into later tests.
    def fake_load_dotenv(*_args, **_kwargs):
        for key, value in dotenv_values(env_file).items():
            monkeypatch.setenv(key, value)
        return True

    monkeypatch.setattr(config_module, "load_dotenv", fake_load_dotenv)
    args = _args(question=_SHORT_ESTONIAN_QUESTION)
    assert asyncio.run(cli._run(args)) == 0

    from llm_deliberation.service import DeliberationService

    record = DeliberationService().list_runs()[0]
    assert record.language == "et"
