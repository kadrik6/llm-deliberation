"""Structured question-framing: schema, prompt support, parsing.

Stage 0 (see ADR 009, docs/decisions/009-question-framing-stage0.md) is an
optional, off-by-default pre-run phase that runs before the main pipeline's
immutable question/context are acted on. Hypothesis, UNTESTED: a better-
framed question makes the deliberation more useful, especially when the
original question is vague. Nothing here treats that hypothesis as
established -- it motivates building the capability, not a claim that it
works.

MVP call shape (3 calls total, not 13 -- see ADR 009's call-count
trade-off):
1. One call returns BOTH the problem-type classification AND the full set
   of interview questions (ProblemFraming). The interview does NOT adapt to
   answers one question at a time; all questions are generated up front.
2. Candidate A, independently, returns a combined framing critique +
   premortem (FramingAnalysis).
3. Candidate B, independently, returns the same (FramingAnalysis).

Like convergence.py, this module is provider-independent -- it knows
nothing about Anthropic, OpenAI, or Gemini -- and model-generated
analytical metadata here is never a ground-truth judgement. Reframings are
always options for the user to accept, reject, or ignore at Lock; nothing
in this module or its callers ever applies one automatically. The problem-
type classification is advisory only: it is shown to the user as a note
and never changes the profile or skips a stage (see service.py/cli.py).
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

ProblemTypeClassification = Literal["simple", "complicated", "complex", "unclear"]
RightProblemAssessment = Literal["yes", "no", "uncertain"]


class ProblemFraming(BaseModel):
    """Result of the combined 0a (Cynefin classification) + 0b (interview
    questions) call."""

    classification: ProblemTypeClassification = Field(
        description=(
            "Cynefin framing of the decision/problem. Advisory only -- "
            "this application never uses it to change the profile or skip "
            "a stage."
        )
    )
    reason: str = Field(description="One or two sentences explaining the classification.")
    questions: list[str] = Field(
        min_length=1,
        description=(
            "5-8 questions (not strictly enforced -- a model giving a few "
            "fewer or more should not fail this stage) covering: what "
            "decision depends on the answer, which options are on the "
            "table, the real goal, constraints (time/money/people), and "
            "what would show the decision was right."
        ),
    )


class FramingAnalysis(BaseModel):
    """Result of one candidate's combined 0c (framing critique) + 0d
    (premortem) call. Produced independently by candidate A and candidate B
    (same independence rule as ADR 001) -- never shown to the other
    candidate, and never merged into one "meta" view here."""

    hidden_assumptions: list[str] = Field(default_factory=list)
    reframings: list[str] = Field(
        default_factory=list,
        description="2-3 alternative framings. Always options, never a recommendation.",
    )
    is_right_problem: RightProblemAssessment
    right_problem_note: str = Field(
        default="", description="One or two sentences backing is_right_problem."
    )
    premortem_reasons: list[str] = Field(
        default_factory=list,
        description="The 3 most likely reasons this decision looks wrong a year from now.",
    )


# See convergence.RAW_TEXT_EXCERPT_CHARS for the rationale -- a bounded
# excerpt of a malformed response, long enough to diagnose, never an
# unbounded blob stored or rendered in the main UI.
RAW_TEXT_EXCERPT_CHARS = 2000


def _excerpt(text: str, limit: int = RAW_TEXT_EXCERPT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [truncated, {len(text)} chars total]"


class QuestionFramingParseError(ValueError):
    """A Stage 0 response wasn't valid JSON matching its schema. Carries a
    bounded excerpt of the raw text that failed to parse/validate -- see
    convergence.ConvergenceParseError, the exact template this mirrors."""

    def __init__(self, message: str, *, raw_text: str = ""):
        super().__init__(message)
        self.raw_text_excerpt = _excerpt(raw_text) if raw_text else ""


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def _strip_code_fence(text: str) -> str:
    """Defensively remove a wrapping ```json ... ``` fence -- see
    convergence._strip_code_fence, the same defensive tolerance."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _FENCE_RE.sub("", stripped).strip()
    return stripped


def _parse(raw_text: str, model: type[BaseModel]) -> BaseModel:
    candidate_text = _strip_code_fence(raw_text)
    try:
        data = json.loads(candidate_text)
    except json.JSONDecodeError as exc:
        raise QuestionFramingParseError(
            f"Response was not valid JSON: {exc}", raw_text=raw_text
        ) from exc
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise QuestionFramingParseError(
            f"Response did not match the expected schema: {exc}", raw_text=raw_text
        ) from exc


def parse_problem_framing(raw_text: str) -> ProblemFraming:
    return _parse(raw_text, ProblemFraming)  # type: ignore[return-value]


def parse_framing_analysis(raw_text: str) -> FramingAnalysis:
    return _parse(raw_text, FramingAnalysis)  # type: ignore[return-value]


def canonical_json(model: BaseModel) -> str:
    """Pretty-printed canonical JSON -- persisted in place of the model's
    raw (possibly fenced/whitespace-noisy) response, exactly like
    convergence.canonical_json."""
    return json.dumps(model.model_dump(mode="json"), indent=2, ensure_ascii=False)


def problem_framing_schema_for_prompt() -> str:
    return json.dumps(ProblemFraming.model_json_schema(), indent=2)


def framing_analysis_schema_for_prompt() -> str:
    return json.dumps(FramingAnalysis.model_json_schema(), indent=2)
