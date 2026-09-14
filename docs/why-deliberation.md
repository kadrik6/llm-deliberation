# Why deliberation?

This document explains the reasoning behind the pipeline, and is honest
about what that reasoning does and does not establish. If you only read one
architecture doc in this repository, read this one first, then
[`trust-model.md`](trust-model.md).

## The starting problem

A single LLM call, even from a strong frontier model, produces one pass of
reasoning shaped by one model's training data, one set of unstated
assumptions, and (with sampling) one particular path through the
probability distribution over plausible-sounding answers. For an
easy-to-verify factual lookup, that is usually fine. For a genuinely
difficult decision -- one with real tradeoffs, incomplete information, and
no single canonical right answer -- a single pass has no built-in mechanism
to notice its own blind spots, push back on its own framing, or flag which
parts of its confident-sounding answer are actually load-bearing
assumptions.

Asking the same model to "double check itself" mostly re-runs the same
model, with the same training-data biases and the same tendency toward
whatever framing it reached for first, now dressed up as a second opinion.

## Why independent generation, before cross-exposure

The pipeline starts by asking two different model families (currently
OpenAI and Anthropic) to answer the same question **independently** --
neither sees the other's answer at this stage. See
[ADR 001](decisions/001-independent-generation.md).

This ordering matters. If model B saw model A's answer before producing
its own, B's framing, structure, and conclusions would likely anchor
toward A's -- collapsing exactly the diversity of perspective the pipeline
is trying to use as a check. Independent generation is what makes the next
stage (cross-critique) meaningful: two answers that were never allowed to
converge on each other are a much more informative pair to compare than
one answer and a paraphrase of it.

## Why cross-critique

Once both independent answers exist, each model reviews the *other's*
answer (not its own) for logical gaps, unsupported claims, hidden
assumptions, and overconfidence. A model is, in general, better at
spotting problems in someone else's reasoning than in re-deriving its own
blind spots from scratch -- and a different model family is more likely to
notice a domain assumption, framing choice, or omission that the first
model's training simply didn't surface.

This is still not a guarantee of catching real errors. Cross-critique can
surface stylistic disagreement or plausible-sounding but wrong objections
just as easily as it surfaces a genuine gap. It is a structured
opportunity for disagreement, not a correctness proof.

## Why the third model is a red-team, not a voter

A third model *could* be used to rank the two answers, pick a winner, or
break ties. This project deliberately does not do that. See
[ADR 002](decisions/002-red-team-not-voting.md).

Voting and "judge" setups reward agreement and confident presentation, not
correctness. If both primary models share a correlated failure -- the same
outdated fact, the same common misconception in a domain, the same
framing assumption baked into how the question is usually discussed online
-- a vote does not catch that, and a judge model trained on similar data is
often prone to the same blind spot itself. Instead, the third model
("red-team") is explicitly instructed to look for assumptions and gaps
that **both** primary answers might share, and to name the single most
important issue the final synthesizer should resolve. It never declares a
winner.

The red-team stage is optional (`--no-red-team` / a UI toggle) precisely
because its marginal value for a given question is unproven and
task-dependent -- see the caveats below.

## Why outputs are persisted and inspectable

Every stage's raw output -- both independent analyses, both critiques, the
optional red-team report, both revisions, and the final synthesis -- is
written to SQLite as soon as that stage succeeds, and can be exported as a
single Markdown report. See [ADR 003](decisions/003-durable-sqlite-runs.md).

This is a deliberate rejection of "just show me the final answer." A
synthesis you cannot trace back to the reasoning and disagreement that
produced it is not meaningfully more auditable than a single model's
answer -- it is just a fancier-sounding one. Persisting every intermediate
artifact means a user (or a future evaluation harness) can check whether
the red-team's findings actually changed a revision, whether a critique
was acted on or correctly dismissed, and where the final answer's claims
actually came from.

## What this does *not* establish

This is the most important section of this document.

- **This project does not claim that combining multiple models is
  automatically more accurate, better calibrated, or more trustworthy
  than a single strong model call.** It might be, for some question types
  and not others. It might not be, at all, for some. No controlled
  comparison has been run yet.
- **The central hypothesis -- that structured disagreement between
  independent models produces more robust, more auditable answers than a
  single-model interaction -- is a testable claim this project has not yet
  tested.** See [`evals/README.md`](../evals/README.md) for the intended
  comparison design, which has not been executed.
- The pipeline costs several times more (API calls, tokens, latency) than
  a single call. Whether the result is several times better, marginally
  better, the same, or occasionally *worse* (e.g. more hedged, more
  diluted, less decisive) is an open question, not an assumption baked
  into the design.
- It is entirely plausible that a single, well-prompted frontier model
  outperforms this whole pipeline on many question types, especially ones
  where the model already has strong, well-calibrated knowledge and the
  main risk is not disagreement but simple factual lookup.

If you use this tool, read its output the way you would read notes from a
structured debate: as material for your own judgment, not as a verified
answer.
