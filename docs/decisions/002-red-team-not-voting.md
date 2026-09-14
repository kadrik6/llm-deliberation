# ADR 002: The third model is a red-team, not a voter

## Context

With two primary models producing independent analyses and critiquing
each other, a natural next idea is to add a third model as a tie-breaker
or judge: have it read both answers and rank them, pick a winner, or
average a score.

## Decision

The optional third model (currently Gemini) is not used as a voter or
judge. It is prompted specifically to find **assumptions and blind spots
that both primary candidates might share** -- correlated failure modes --
and to name the single most important issue the final synthesizer should
resolve. It never ranks the two answers or declares a winner. This stage
is optional (`--no-red-team` on the CLI, a switch in the web UI).

## Why

Majority voting and judge-style setups reward agreement and confident
presentation, not correctness. If both primary models share a correlated
error -- an outdated fact, a common misconception in a domain, a framing
assumption baked into how a topic is usually discussed in their training
data -- neither a vote nor a judge model (often trained on similar data)
is well-positioned to catch it: the judge is prone to the same blind spot.
Explicitly tasking a third model with *searching for shared gaps*, instead
of *scoring the two answers*, targets a different and arguably more useful
failure mode than voting does.

## Trade-offs

- Adds a third paid API call and more latency on top of an already
  multi-call pipeline; this is exactly why it is optional rather than
  mandatory.
- Does not catch errors unique to a single model -- that is already the
  job of the other primary model's critique, not the red-team's.
- Whether the red-team stage reliably finds *real* correlated blind spots,
  as opposed to generating plausible-sounding but generic caveats, is
  unverified. A model asked to "find shared assumptions" can produce
  text that sounds like a finding without being one.
- Using Gemini specifically (rather than a configurable third provider)
  is a current implementation choice, not a claim that Gemini is
  uniquely suited to this role.

## How we plan to evaluate it

For each run where red-team is enabled, check (ideally via blind human
review, unaware of which text came from which stage) whether the
subsequent revisions actually changed in response to the red-team's
findings, versus the red-team's report reading as decorative. Compare
final-answer quality with red-team on vs. off on the same questions. See
[`evals/README.md`](../../evals/README.md). **Not yet run.**
