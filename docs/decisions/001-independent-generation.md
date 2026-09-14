# ADR 001: Independent generation before cross-exposure

## Context

The pipeline needs a starting pair of candidate answers from two different
model families before any cross-critique can happen. The two obvious
options were: (a) generate both answers independently, from the same
prompt, with no visibility into each other; or (b) generate one answer,
then show it to the second model and ask for its own take.

## Decision

Model A (OpenAI) and Model B (Anthropic) each receive the exact same
`independent_analysis` prompt for a given question and produce their first
answer with **no knowledge of the other model's output**. Only after both
independent answers exist does the pipeline move to cross-critique.

## Why

If model B saw model A's answer first, B's framing, emphasis, and even
conclusions would likely anchor toward A's -- this is a well-known effect
of showing a prior answer before asking for an independent judgment, not
specific to LLMs. Anchoring would collapse the diversity of perspective
that makes the later cross-critique stage meaningful: critiquing a
paraphrase of your own answer is much less informative than critiquing an
answer that was never allowed to converge with yours in the first place.
Independent generation is a prerequisite for the rest of the pipeline to
do anything useful.

## Trade-offs

- Costs roughly 2x the tokens/latency of a single model call for this
  stage alone, compared to using one strong model.
- Independence of *process* does not guarantee independence of
  *reasoning*: two model families can still share correlated blind spots
  (same training-data era, same common framing of a topic). This is only
  partially addressed by the optional red-team stage
  ([ADR 002](002-red-team-not-voting.md)), not solved by independent
  generation alone.
- Neither independent answer is guaranteed to be "right" -- independence
  only guarantees the two answers weren't derived from each other.

## How we plan to evaluate it

Compare, on a fixed question set: (a) a single strong model call, (b) two
independent models synthesized without critique, (c) the full
critique+revision pipeline. Score blindly (rater unaware of which arm
produced which answer) for reasoning quality, missed alternatives, and
factual error rate where checkable. See
[`evals/README.md`](../../evals/README.md). **Not yet run.**
