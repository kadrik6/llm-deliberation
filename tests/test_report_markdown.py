"""Offline tests for report.render_markdown's Stage 0 fields (ADR 009) --
"store both the original and the locked question, and show both in the
report/export". No DB, no HTTP, no network: RunResult is built directly.
"""
from __future__ import annotations

from llm_deliberation.report import render_markdown
from llm_deliberation.types import ModelResponse, RunResult, Usage


def _response(text: str = "text") -> ModelResponse:
    return ModelResponse(provider="Fake", model="fake-model", text=text, usage=Usage(1, 1))


def _run_result(**overrides) -> RunResult:
    defaults = dict(
        question="Original question?",
        profile="economy",
        red_team_enabled=False,
        language="en",
        analysis_a=_response(),
        analysis_b=_response(),
        critique_a_of_b=_response(),
        critique_b_of_a=_response(),
        red_team=None,
        revision_a=_response(),
        revision_b=_response(),
        convergence=None,
        synthesis=_response(),
    )
    defaults.update(overrides)
    return RunResult(**defaults)


def test_without_stage0_the_report_shows_only_the_original_question():
    result = _run_result()
    md = render_markdown(result)
    assert "Original question?" in md
    assert "Locked question" not in md
    assert "Success criterion" not in md


def test_a_locked_reframed_question_shows_both_questions():
    result = _run_result(question="Original question?", locked_question="Reframed question?")
    md = render_markdown(result)
    assert "## Question" in md
    assert "Original question?" in md
    assert "Locked question" in md
    assert "Reframed question?" in md


def test_a_locked_success_criterion_is_shown():
    result = _run_result(locked_question="Reframed question?", success_criteria="A clear outcome")
    md = render_markdown(result)
    assert "Success criterion" in md
    assert "A clear outcome" in md


def test_a_skipped_stage0_shows_the_skipped_note_not_a_duplicate_question():
    result = _run_result(
        question="Original question?",
        locked_question="Original question?",
        question_framing_skipped=True,
    )
    md = render_markdown(result)
    assert "skipped" in md.lower()
    # The original question still appears exactly once (from the Question
    # section) -- the skip note must not repeat it as a second "Locked
    # question" heading.
    assert md.count("## Locked question") == 0


def test_estonian_language_uses_estonian_labels():
    result = _run_result(
        language="et", locked_question="Ümbersõnastatud küsimus?", success_criteria="Selge tulemus",
    )
    md = render_markdown(result)
    assert "Lukustatud küsimus" in md
    assert "Õnnestumiskriteerium" in md
