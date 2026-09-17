"""Evaluation-only prompt builders -- used exclusively by reduced pipeline
variants (SINGLE, DUAL, CRITIQUE; see variants.py) to reach a final answer
without stages that variant doesn't run.

Kept entirely separate from the production llm_deliberation.prompts module:
production prompts are never edited or reused out of their real semantic
context here (e.g. production's `prompts.synthesis()` assumes revision-stage
inputs and a possible convergence/red-team context; calling it with raw
analysis text and pretending it was "revised" would be a real, if small,
lie to the model -- exactly what "do not fabricate artifacts merely because
the current synthesis expects them" rules out). These functions instead
explicitly and truthfully describe which stages ran and which did not.
"""

from __future__ import annotations


def single_direct_answer(question: str) -> str:
    """SINGLE variant: one model call, asked to produce the final
    deliverable directly -- no separate analysis/critique/revision stage
    exists in this variant."""
    return f"""
QUESTION
{question}

Produce the final, complete answer directly. There is no separate review
stage in this evaluation variant -- this is your one opportunity to reason
carefully and give your best, complete answer.

Rules:
- If the request contains a concrete deliverable (an email, letter, plan,
  checklist, document, draft, or code), produce the complete deliverable
  FIRST, before any extended explanation.
- Stay concise enough to finish completely within the available response
  length. A shorter, complete answer is better than a longer one that gets
  cut off before finishing.
- Preserve meaningful uncertainty instead of smoothing it away.
- If an important factual claim requires current external verification and
  none was supplied, say so rather than inventing certainty.

Structure:
- If the request contains a concrete deliverable, write it out in full
  first, then continue with the numbered structure below only if space
  remains.
- Otherwise, or after the deliverable:
  1. Final conclusion / recommendation.
  2. Core reasoning.
  3. Strongest counterargument or alternative.
  4. Remaining uncertainty and what would be verified.
""".strip()


def variant_synthesis(
    question: str,
    *,
    candidates: dict[str, str],
    stages_not_run: list[str],
) -> str:
    """DUAL/CRITIQUE variants: combine 1+ candidate texts (independent
    analyses for DUAL, revisions for CRITIQUE) into one final answer.

    `candidates` maps a plain label ("Candidate A", "Candidate B") to its
    full text -- always real, already-generated content from a real stage
    of this same variant, never fabricated. `stages_not_run` is a list of
    stage names this variant genuinely never ran (e.g. ["critique",
    "red_team", "convergence_analysis"] for DUAL) -- stated explicitly so
    the model never assumes cross-examination happened when it didn't.
    """
    candidate_sections = "\n\n".join(
        f"{label.upper()}\n{text}" for label, text in candidates.items()
    )
    not_run_note = (
        "The following stages were NOT part of this evaluation run -- do "
        "not assume they happened, and do not reference disagreements or "
        "critiques that were never actually produced: "
        + ", ".join(stages_not_run) + "."
        if stages_not_run
        else "Every normal deliberation stage ran for this evaluation."
    )
    return f"""
ORIGINAL QUESTION
{question}

{candidate_sections}

{not_run_note}

Produce the final answer, combining the candidate(s) above into your single
best answer.

Rules:
- If the request contains a concrete deliverable, produce the complete
  deliverable FIRST, before any extended explanation.
- Stay concise enough to finish completely within the available response
  length.
- Do not use majority voting between candidates merely because there is
  more than one.
- Resolve any disagreement between candidates by reasoning, evidence,
  assumptions, and feasibility -- not by picking the more confident-sounding
  one.
- Preserve meaningful uncertainty instead of smoothing it away.
- Do not mention model/provider names.

Structure:
- If the request contains a concrete deliverable, write it out in full
  first, then continue with the numbered structure below only if space
  remains.
- Otherwise, or after the deliverable:
  1. Final conclusion / recommendation.
  2. Why this is the strongest answer.
  3. Strongest argument against it.
  4. Remaining uncertainty and what should be verified.
""".strip()
