"""Offline schema/parsing tests for Stage 0 (question framing, ADR 009).

Mirrors tests/test_convergence.py's structure: no provider calls, pure
schema validation and canonical-JSON round-tripping.
"""
from __future__ import annotations

import json

import pytest

from llm_deliberation.question_framing import (
    FramingAnalysis,
    ProblemFraming,
    QuestionFramingParseError,
    canonical_json,
    framing_analysis_schema_for_prompt,
    parse_framing_analysis,
    parse_problem_framing,
    problem_framing_schema_for_prompt,
)


def _problem_framing_json(**overrides) -> str:
    data = {
        "classification": "complicated",
        "reason": "A reasonable reason.",
        "questions": ["Q1?", "Q2?", "Q3?", "Q4?", "Q5?"],
    }
    data.update(overrides)
    return json.dumps(data)


def _framing_analysis_json(**overrides) -> str:
    data = {
        "hidden_assumptions": ["assumption 1"],
        "reframings": ["reframing 1", "reframing 2"],
        "is_right_problem": "yes",
        "right_problem_note": "a note",
        "premortem_reasons": ["reason 1", "reason 2", "reason 3"],
    }
    data.update(overrides)
    return json.dumps(data)


# -- ProblemFraming -----------------------------------------------------


def test_parse_problem_framing_accepts_valid_payload():
    result = parse_problem_framing(_problem_framing_json())
    assert isinstance(result, ProblemFraming)
    assert result.classification == "complicated"
    assert len(result.questions) == 5


def test_parse_problem_framing_tolerates_a_wrapping_code_fence():
    raw = "```json\n" + _problem_framing_json() + "\n```"
    result = parse_problem_framing(raw)
    assert result.classification == "complicated"


def test_parse_problem_framing_rejects_invalid_json():
    with pytest.raises(QuestionFramingParseError) as exc_info:
        parse_problem_framing("not json at all")
    assert exc_info.value.raw_text_excerpt == "not json at all"


def test_parse_problem_framing_rejects_bad_classification():
    raw = _problem_framing_json(classification="chaotic")  # not a valid Cynefin value here
    with pytest.raises(QuestionFramingParseError):
        parse_problem_framing(raw)


def test_parse_problem_framing_rejects_empty_questions_list():
    raw = _problem_framing_json(questions=[])
    with pytest.raises(QuestionFramingParseError):
        parse_problem_framing(raw)


def test_parse_problem_framing_tolerates_fewer_than_five_questions():
    """The 5-8 guidance is prompt-level, not a hard schema floor -- a model
    giving 3 should not fail the stage (see ADR 009's MVP call-count
    trade-off and the module docstring)."""
    raw = _problem_framing_json(questions=["Only one?", "Two?", "Three?"])
    result = parse_problem_framing(raw)
    assert len(result.questions) == 3


def test_canonical_json_round_trips_problem_framing():
    result = parse_problem_framing(_problem_framing_json())
    reparsed = parse_problem_framing(canonical_json(result))
    assert reparsed == result


def test_problem_framing_schema_for_prompt_is_valid_json_schema():
    schema = json.loads(problem_framing_schema_for_prompt())
    assert schema["properties"]["classification"]
    assert schema["properties"]["questions"]


# -- FramingAnalysis ------------------------------------------------------


def test_parse_framing_analysis_accepts_valid_payload():
    result = parse_framing_analysis(_framing_analysis_json())
    assert isinstance(result, FramingAnalysis)
    assert result.is_right_problem == "yes"
    assert len(result.premortem_reasons) == 3


def test_parse_framing_analysis_rejects_invalid_is_right_problem():
    raw = _framing_analysis_json(is_right_problem="definitely")
    with pytest.raises(QuestionFramingParseError):
        parse_framing_analysis(raw)


def test_parse_framing_analysis_rejects_malformed_json():
    with pytest.raises(QuestionFramingParseError) as exc_info:
        parse_framing_analysis("{not valid")
    assert "not valid JSON" in str(exc_info.value)


def test_parse_framing_analysis_defaults_empty_lists_when_omitted():
    raw = json.dumps({"is_right_problem": "uncertain"})
    result = parse_framing_analysis(raw)
    assert result.hidden_assumptions == []
    assert result.reframings == []
    assert result.premortem_reasons == []
    assert result.right_problem_note == ""


def test_canonical_json_round_trips_framing_analysis():
    result = parse_framing_analysis(_framing_analysis_json())
    reparsed = parse_framing_analysis(canonical_json(result))
    assert reparsed == result


def test_framing_analysis_schema_for_prompt_is_valid_json_schema():
    schema = json.loads(framing_analysis_schema_for_prompt())
    assert schema["properties"]["is_right_problem"]
    assert schema["properties"]["premortem_reasons"]


# -- raw text excerpt bounding -------------------------------------------


def test_raw_text_excerpt_is_bounded_for_a_huge_malformed_response():
    huge = "x" * 50_000
    with pytest.raises(QuestionFramingParseError) as exc_info:
        parse_problem_framing(huge)
    assert len(exc_info.value.raw_text_excerpt) < 2100
    assert "truncated" in exc_info.value.raw_text_excerpt
