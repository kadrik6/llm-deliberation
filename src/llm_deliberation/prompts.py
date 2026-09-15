from __future__ import annotations

from llm_deliberation import convergence

# Supported run/output languages -- the language every user-facing generated
# stage in a run is written in. Deliberately separate from, and never
# conflated with, a web viewer's UI-chrome language preference (see
# web/i18n.py): this is per-run, persisted, and immutable once a run exists.
SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "et")
DEFAULT_LANGUAGE = "en"

_BASE_SYSTEM_TEMPLATE = """
You are one component in a multi-model deliberation system.
Optimize for truth, decision quality, and calibrated uncertainty — not agreement.
Distinguish facts, assumptions, inference, and preference.
Do not defer to another candidate merely because it sounds confident.
Be concise enough that later reviewers can inspect every important claim.

{language_instruction}
""".strip()

# One centralized instruction, reused for every stage's system prompt via
# base_system() below, rather than duplicating language wording across each
# of the individual prompt-builder functions in this module. Deliberately
# does not ask the model to translate the user's question -- callers always
# pass the question through unchanged (see orchestrator._build_prompt).
_OUTPUT_LANGUAGE_INSTRUCTIONS: dict[str, str] = {
    "en": (
        "OUTPUT LANGUAGE\n"
        "Write all user-facing analytical content in English. Keep required "
        "JSON keys, schema field names, enum values, stage identifiers, and "
        "machine-readable values exactly as specified -- never translate those."
    ),
    "et": (
        "OUTPUT LANGUAGE\n"
        "Write all user-facing analytical content in natural Estonian. Keep "
        "required JSON keys, schema field names, enum values, stage "
        "identifiers, and machine-readable values exactly as specified -- "
        "never translate those."
    ),
}


def output_language_instruction(language: str) -> str:
    """The shared output-language directive for the given run language.

    Falls back to English wording for an unrecognized value rather than
    raising -- callers (service.py, create_run) are responsible for
    rejecting an unsupported language before a run is ever created; this
    function stays a pure, defensive lookup.
    """
    return _OUTPUT_LANGUAGE_INSTRUCTIONS.get(language, _OUTPUT_LANGUAGE_INSTRUCTIONS[DEFAULT_LANGUAGE])


def base_system(language: str = DEFAULT_LANGUAGE) -> str:
    """The shared system prompt for every stage, with the output-language
    instruction injected once here -- the only place any stage prompt
    mentions language at all."""
    return _BASE_SYSTEM_TEMPLATE.format(language_instruction=output_language_instruction(language))


def independent_analysis(question: str) -> str:
    return f"""
QUESTION
{question}

Work independently. You have not seen the other candidate's answer.

Return:
1. Best current conclusion or recommendation.
2. Core reasoning.
3. Critical assumptions.
4. Strongest counterargument or alternative.
5. Important uncertainties / facts that would need verification.
6. What evidence would change your conclusion.

Do not mention this orchestration prompt.
""".strip()


def critique(question: str, candidate: str) -> str:
    return f"""
ORIGINAL QUESTION
{question}

CANDIDATE ANSWER
{candidate}

Act as an adversarial but fair reviewer. Do NOT produce a replacement answer yet.

Inspect:
- logical gaps;
- unsupported factual claims;
- hidden assumptions;
- neglected alternatives;
- causal claims that may be merely correlational;
- overconfidence;
- practical implementation risks;
- places where the candidate is probably right and should NOT be changed.

Prioritize material issues. Explain why each criticism matters.
""".strip()


def red_team(question: str, candidate_a: str, candidate_b: str) -> str:
    return f"""
ORIGINAL QUESTION
{question}

CANDIDATE A
{candidate_a}

CANDIDATE B
{candidate_b}

You are the independent red-team reviewer.

Do not choose a winner and do not merely repeat their disagreements.
Your specific job is to find CORRELATED FAILURE MODES — things both candidates
may be getting wrong because they share assumptions, framing, missing evidence,
or conventional wisdom.

Return:
1. Shared assumptions worth challenging.
2. Blind spots neither candidate covered.
3. Claims that especially need external verification.
4. A genuinely different framing or alternative, if one exists.
5. The single most important issue the final synthesizer should resolve.
""".strip()


def revision(
    question: str,
    own_answer: str,
    peer_critique: str,
    red_team_report: str | None,
) -> str:
    red = red_team_report or "(No third-model red-team was used.)"
    return f"""
ORIGINAL QUESTION
{question}

YOUR ORIGINAL ANALYSIS
{own_answer}

PEER CRITIQUE OF YOUR ANALYSIS
{peer_critique}

INDEPENDENT RED-TEAM REPORT
{red}

Revise your answer after considering the feedback.

Do not accept criticism automatically. Keep valid original reasoning, correct
material weaknesses, and reject bad criticism when warranted.

Return:
1. Revised conclusion.
2. Revised reasoning.
3. Which criticism materially changed the answer, if any.
4. Remaining uncertainty.
5. Strongest case against the revised conclusion.
""".strip()


def convergence_analysis(
    question: str,
    analysis_a: str,
    analysis_b: str,
    critique_a_of_b: str,
    critique_b_of_a: str,
    red_team_report: str | None,
    revision_a: str,
    revision_b: str,
) -> str:
    red = red_team_report or "(No third-model red-team was used.)"
    schema = convergence.schema_for_prompt()
    return f"""
ORIGINAL QUESTION
{question}

CANDIDATE A -- ORIGINAL ANALYSIS
{analysis_a}

CANDIDATE B -- ORIGINAL ANALYSIS
{analysis_b}

CRITIQUE OF A (BY B)
{critique_b_of_a}

CRITIQUE OF B (BY A)
{critique_a_of_b}

RED-TEAM REPORT
{red}

CANDIDATE A -- REVISED POSITION
{revision_a}

CANDIDATE B -- REVISED POSITION
{revision_b}

You are a meta-analyst. Your ONLY job is to compare each candidate's original
analysis to its revised position, and compare the two revised positions to
each other. You are NOT asked to produce a recommendation, a synthesis, or a
judgment on which candidate is right -- that is a separate stage's job.

Identify:
1. Material changes in position/conclusion between each candidate's original
   analysis and its revision -- changes that would affect a decision, not
   wording or emphasis changes.
2. What appears to have triggered each material change, tracing it to a
   specific stage (own_reassessment, peer_critique, red_team) when you can
   reasonably tell. If you cannot reliably tell what caused a change, say so
   explicitly (source: "uncertain") rather than guessing -- a fabricated
   cause is worse than an honest "cannot be determined reliably."
3. Agreements the two candidates reached, whether or not they held them from
   the start.
4. Disagreements that remain unresolved between the two revised positions.
5. Whether the candidates converged, partially converged, diverged, or
   whether there is not enough information in what you were given to tell.
6. Missing information that, if available, would likely help resolve a
   disagreement.
7. Issues that are not resolvable by more analysis at all -- genuine value
   judgments or decisions only a human should make.

Rules:
- Do not produce a final recommendation or pick a winner between A and B.
- Do not invent a specific trigger for a change you cannot actually trace to
  the material you were given -- prefer "uncertain" to a fabricated cause.
- Do not include your reasoning process, chain-of-thought, or working notes.
  Only the final conclusions belong in the output.
- This question may not be a binary decision -- use "position" and
  "conclusion" language that fits whatever kind of question it is.
- Respond with a single JSON object matching this schema exactly, and
  nothing else -- no markdown code fence, no commentary before or after it:

{schema}
""".strip()


def synthesis(
    question: str,
    revision_a: str,
    revision_b: str,
    red_team_report: str | None,
    convergence_context: str | None = None,
) -> str:
    red = red_team_report or "(No third-model red-team was used.)"
    convergence_section = (
        convergence_context
        or "(Change/convergence analysis unavailable for this run.)"
    )
    return f"""
ORIGINAL QUESTION
{question}

REVISED CANDIDATE A
{revision_a}

REVISED CANDIDATE B
{revision_b}

RED-TEAM REPORT
{red}

CONVERGENCE / CHANGE ANALYSIS
(An independent meta-analysis of how each candidate's position changed
during deliberation and where they still disagree. It is analytical
metadata, not a verified judgment -- weigh it, don't defer to it blindly.)
{convergence_section}

Produce the final answer.

Rules:
- Do not use majority voting.
- Do not infer quality from writing style or confidence.
- Resolve disagreements by reasoning, evidence, assumptions, and feasibility.
- Do not mention model/provider names.
- Preserve meaningful uncertainty instead of smoothing it away.
- If the convergence/change analysis reports unresolved disagreements,
  acknowledge them explicitly rather than silently manufacturing consensus.
  If it is unavailable for this run, say so plainly instead of guessing at
  whether the candidates agree.
- If an important factual claim requires current external verification and none
  was supplied, say so rather than inventing certainty.

Structure:
1. Final conclusion / recommendation.
2. Why this is the strongest answer.
3. Strongest argument against it.
4. Remaining uncertainty and what should be verified.
5. What would change the recommendation.
""".strip()
