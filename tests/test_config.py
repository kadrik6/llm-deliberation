"""Offline tests for config.Settings.load's defaults. No network calls.

Settings.load() calls load_dotenv() internally, which reloads a real local
`.env` file from disk on every call -- a plain monkeypatch.delenv() is not
enough to test "the env var is genuinely unset", since the very next
load_dotenv() call repopulates it from that file. These tests stub
config.load_dotenv to a no-op instead, so they reflect this repo's code
defaults, never whatever a developer happens to have in their own local
`.env`.
"""
from __future__ import annotations

from llm_deliberation import config
from llm_deliberation.config import Settings


def test_max_output_tokens_defaults_to_16000_when_unset(monkeypatch):
    """Regression (ADR 010): raised from 5000 after a real analysis stage
    (run cc184d4f0302) truncated twice at that cap, each attempt consuming
    ~99.7-99.8% of it on reasoning/thinking before finishing -- effort=high
    (the default everywhere) shares the cap between thinking and the
    visible answer, it is not a bound on visible text alone."""
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    monkeypatch.delenv("MAX_OUTPUT_TOKENS", raising=False)
    settings = Settings.load(profile_override="economy", red_team_override=False)
    assert settings.max_output_tokens == 16000


def test_max_output_tokens_still_respects_an_explicit_override(monkeypatch):
    monkeypatch.setenv("MAX_OUTPUT_TOKENS", "8000")
    settings = Settings.load(profile_override="economy", red_team_override=False)
    assert settings.max_output_tokens == 8000
