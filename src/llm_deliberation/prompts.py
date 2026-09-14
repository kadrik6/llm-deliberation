from __future__ import annotations


BASE_SYSTEM = """
You are one component in a multi-model deliberation system.
Optimize for truth, decision quality, and calibrated uncertainty — not agreement.
Distinguish facts, assumptions, inference, and preference.
Do not defer to another candidate merely because it sounds confident.
Be concise enough that later reviewers can inspect every important claim.
""".strip()


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


def synthesis(
    question: str,
    revision_a: str,
    revision_b: str,
    red_team_report: str | None,
) -> str:
    red = red_team_report or "(No third-model red-team was used.)"
    return f"""
ORIGINAL QUESTION
{question}

REVISED CANDIDATE A
{revision_a}

REVISED CANDIDATE B
{revision_b}

RED-TEAM REPORT
{red}

Produce the final answer.

Rules:
- Do not use majority voting.
- Do not infer quality from writing style or confidence.
- Resolve disagreements by reasoning, evidence, assumptions, and feasibility.
- Do not mention model/provider names.
- Preserve meaningful uncertainty instead of smoothing it away.
- If an important factual claim requires current external verification and none
  was supplied, say so rather than inventing certainty.

Structure:
1. Final conclusion / recommendation.
2. Why this is the strongest answer.
3. Strongest argument against it.
4. Remaining uncertainty and what should be verified.
5. What would change the recommendation.
""".strip()
