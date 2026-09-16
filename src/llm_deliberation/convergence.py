"""Structured convergence/change analysis: schema, prompt support, parsing.

`convergence_analysis` is a distinct durable stage (see orchestrator.WAVES)
whose only job is to compare each candidate's original analysis to its
revision, and the two revisions to each other -- material position changes,
what seems to have triggered them, agreements reached, disagreements that
remain, and what would be needed to resolve them. It never produces a
recommendation; that stays the final synthesizer's job.

This is model-generated analytical metadata, not a ground-truth judgement:
the analyst is the same kind of model as every other stage, prompted for a
narrower task, and can misdetect a change or mis-attribute a cause. Nothing
here should be read as proof of causality.

This module defines the canonical, provider-independent schema/parsing
contract -- it knows nothing about Anthropic, OpenAI, or Gemini. For a
provider that supports native structured output (see
providers.AnthropicProvider's `response_schema` param and
orchestrator._build_convergence_provider), the provider's own constrained
decoding is the primary reliability guarantee: the model is restricted to
emitting text that already matches `ConvergenceAnalysis.model_json_schema()`,
so `parse_convergence_analysis` below is validating an already-constrained
response rather than being the *only* thing standing between free-form prose
and a broken artifact. For a provider without that support, the prompt still
asks for one bare JSON object matching the schema (`schema_for_prompt()`,
embedded in the prompt so it can't drift out of sync with this file), and
`parse_convergence_analysis` remains the sole validation boundary -- exactly
as before. Either way, a response that doesn't parse or doesn't match the
schema fails the stage, exactly like any other provider error -- never
silently accepted as malformed data, and never automatically retried with
another paid call just to "try JSON again" (see docs/decisions for the
reliability-pass reasoning this iteration adds to).
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

ConvergenceLevel = Literal["converged", "partial", "diverged", "insufficient_information"]
Candidate = Literal["A", "B"]
TriggerSource = Literal["peer_critique", "red_team", "own_reassessment", "uncertain", "other"]
ChangeStatus = Literal["material", "non_material", "unknown"]


class ChangeTrigger(BaseModel):
    source: TriggerSource = Field(
        description=(
            "What appears to have caused the change. Use 'uncertain' rather "
            "than guessing when the cause cannot be reliably traced to the "
            "material you were given."
        )
    )
    stage: str | None = Field(
        default=None,
        description=(
            "Stable stage name this trigger traces to, if identifiable "
            "(e.g. 'critique_b_of_a', 'red_team', 'critique_a_of_b'). "
            "Omit when the source is 'uncertain' or 'own_reassessment'."
        ),
    )
    summary: str = Field(description="One concise sentence explaining the trigger.")


class MaterialChange(BaseModel):
    candidate: Candidate
    before: str = Field(description="The candidate's original position/conclusion, concisely summarized.")
    after: str = Field(description="The candidate's revised position/conclusion, concisely summarized.")
    change_status: ChangeStatus = Field(
        description=(
            "'material' only for a substantive change to the position/conclusion -- not "
            "wording or emphasis. 'non_material' when the position is essentially "
            "unchanged. 'unknown' when there is not enough evidence to tell -- for "
            "example the original or revised position given to you is missing, empty, "
            "or unusable. Never use 'non_material' as a default when evidence is "
            "actually missing -- that would misrepresent an unknown as 'no change'."
        )
    )
    triggers: list[ChangeTrigger] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_material_bool(cls, data: object) -> object:
        """Accept older persisted artifacts that only ever had a `material`
        boolean (see the reliability-pass change adding `change_status`).
        Historical convergence artifacts are never rewritten in storage --
        this lets them keep parsing/rendering correctly instead, per this
        project's backward-compatibility requirements."""
        if isinstance(data, dict) and "change_status" not in data and "material" in data:
            data = dict(data)
            data["change_status"] = "material" if data.pop("material") else "non_material"
        return data

    @property
    def material(self) -> bool:
        """Backward-compatible boolean view (e.g. Jinja's `selectattr("material")`
        in result.html). True only for 'material' -- 'unknown' is deliberately
        NOT truthy here, since it is not evidence of a material change."""
        return self.change_status == "material"


class Agreement(BaseModel):
    topic: str
    shared_position: str


class UnresolvedDisagreement(BaseModel):
    topic: str
    candidate_a_position: str
    candidate_b_position: str
    why_unresolved: str
    decision_impact: str = Field(
        description="Why this disagreement matters for whoever has to act on the final answer."
    )


class RemainingUnknown(BaseModel):
    unknown: str
    why_it_matters: str
    evidence_needed: str


class HumanJudgementItem(BaseModel):
    issue: str
    why_models_cannot_resolve_it: str


class ConvergenceAnalysis(BaseModel):
    """Structured, validated result of the convergence_analysis stage.

    Analytical provenance about how the deliberation evolved -- not a
    second opinion, not a ground-truth judgement, and not proof of
    causality. See this module's docstring.
    """

    convergence: ConvergenceLevel = Field(
        description="Overall assessment of whether the two candidates' revised positions converged."
    )
    material_changes: list[MaterialChange] = Field(default_factory=list)
    agreements_reached: list[Agreement] = Field(default_factory=list)
    unresolved_disagreements: list[UnresolvedDisagreement] = Field(default_factory=list)
    remaining_unknowns: list[RemainingUnknown] = Field(default_factory=list)
    human_judgement_required: list[HumanJudgementItem] = Field(default_factory=list)


# A raw malformed response can be tens of thousands of characters (a real
# incident hit "Unterminated string starting at line 1 column 14896"). The
# full text is never dropped silently, but it also must never be dumped
# whole into the main UI or an unbounded DB column -- see the task brief's
# "do not expose giant raw JSON/parser stack traces in the main UI". This
# excerpt length is generous enough to actually diagnose where generation
# went wrong (see orchestrator._finalize_convergence_response, which stores
# it in the stage's attempt_log as technical provenance) without storing an
# unbounded blob.
RAW_TEXT_EXCERPT_CHARS = 2000


def _excerpt(text: str, limit: int = RAW_TEXT_EXCERPT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [truncated, {len(text)} chars total]"


class ConvergenceParseError(ValueError):
    """A convergence-analysis response wasn't valid JSON matching the schema.

    Carries a bounded excerpt of the raw text that failed to parse/validate
    (`raw_text_excerpt`) -- never the model's stored artifact (nothing here
    is persisted as the stage's successful output), just enough of the
    actual response for technical/provenance diagnosis. See RAW_TEXT_EXCERPT_CHARS.
    """

    def __init__(self, message: str, *, raw_text: str = ""):
        super().__init__(message)
        self.raw_text_excerpt = _excerpt(raw_text) if raw_text else ""


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def _strip_code_fence(text: str) -> str:
    """Defensively remove a wrapping ```json ... ``` fence, if present.

    The prompt explicitly asks for a bare JSON object, but models
    occasionally wrap structured output in a fence anyway; stripping it here
    is more robust than failing the stage over a purely cosmetic deviation.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _FENCE_RE.sub("", stripped).strip()
    return stripped


def parse_convergence_analysis(raw_text: str) -> ConvergenceAnalysis:
    """Parse and validate a model's raw response into ConvergenceAnalysis.

    Raises ConvergenceParseError (not a bare JSONDecodeError/ValidationError)
    so callers can catch one specific, clearly-named exception.
    """
    candidate_text = _strip_code_fence(raw_text)
    try:
        data = json.loads(candidate_text)
    except json.JSONDecodeError as exc:
        raise ConvergenceParseError(
            f"Response was not valid JSON: {exc}", raw_text=raw_text
        ) from exc
    try:
        return ConvergenceAnalysis.model_validate(data)
    except ValidationError as exc:
        raise ConvergenceParseError(
            f"Response did not match the expected schema: {exc}", raw_text=raw_text
        ) from exc


def canonical_json(analysis: ConvergenceAnalysis) -> str:
    """Pretty-printed canonical JSON -- persisted as the stage's artifact
    text, in place of the model's raw (possibly fenced/whitespace-noisy)
    response."""
    return json.dumps(analysis.model_dump(mode="json"), indent=2, ensure_ascii=False)


def schema_for_prompt() -> str:
    """Pretty-printed JSON Schema for embedding in the stage prompt.

    Generated from the model itself so the prompt can never drift out of
    sync with the actual validated schema.
    """
    return json.dumps(ConvergenceAnalysis.model_json_schema(), indent=2)


def render_for_prompt(analysis: ConvergenceAnalysis) -> str:
    """Human-readable digest of a validated analysis, for embedding as
    context in the final synthesis prompt (not the raw JSON -- easier for
    a text-generation call to read, and no less "validated": it's the same
    data, just formatted for a prose prompt instead of for storage)."""
    lines = [f"Convergence assessment: {analysis.convergence}"]

    material = [c for c in analysis.material_changes if c.change_status == "material"]
    unknown = [c for c in analysis.material_changes if c.change_status == "unknown"]
    if material:
        lines.append("\nMaterial changes:")
        for change in material:
            if change.triggers:
                trig = "; ".join(
                    t.summary + (" (cause uncertain)" if t.source == "uncertain" else "")
                    for t in change.triggers
                )
            else:
                trig = "cause not determined"
            lines.append(f"- Candidate {change.candidate}: {change.before} -> {change.after} ({trig})")
    else:
        lines.append("\nNo material position changes were identified.")
    if unknown:
        lines.append("\nChange status could not be determined (insufficient evidence) for:")
        for change in unknown:
            lines.append(f"- Candidate {change.candidate}: {change.before} -> {change.after}")

    if analysis.agreements_reached:
        lines.append("\nAgreements reached:")
        for agreement in analysis.agreements_reached:
            lines.append(f"- {agreement.topic}: {agreement.shared_position}")

    if analysis.unresolved_disagreements:
        lines.append("\nUnresolved disagreements:")
        for d in analysis.unresolved_disagreements:
            lines.append(
                f"- {d.topic}: A holds {d.candidate_a_position!r}; B holds "
                f"{d.candidate_b_position!r}. Decision impact: {d.decision_impact}"
            )
    else:
        lines.append("\nNo unresolved disagreements were identified.")

    if analysis.remaining_unknowns:
        lines.append("\nRemaining unknowns that would help resolve disagreement:")
        for u in analysis.remaining_unknowns:
            lines.append(f"- {u.unknown} ({u.why_it_matters})")

    if analysis.human_judgement_required:
        lines.append("\nIssues flagged as requiring human judgement, not further analysis:")
        for h in analysis.human_judgement_required:
            lines.append(f"- {h.issue}")

    return "\n".join(lines)
