# Evaluation plan (scaffold -- not yet implemented)

This project's central hypothesis -- that structured disagreement between
independently-generated LLM answers produces more robust, more auditable
answers than a single-model interaction -- has **not** been empirically
tested. See [`docs/why-deliberation.md`](../docs/why-deliberation.md) for
the reasoning behind the design, and its explicit list of what that
reasoning does not establish.

This file records the intended design for testing that hypothesis, before
it is built, so the gap between "designed to help" and "shown to help" is
visible to anyone reading this repository. Nothing in this directory runs
yet.

## Comparison arms

The same fixed question set would be run through each of these
configurations, using the existing pipeline's stages (no new orchestration
needed -- these arms are subsets of the existing stage graph):

1. **Single strong LLM baseline.** One call, one model, best-available
   profile, no deliberation. The reference point everything else has to
   beat.
2. **Two independent LLMs + synthesis.** `analysis_a` + `analysis_b`,
   synthesized directly, with no cross-critique or revision in between.
   Isolates the value of independent generation alone.
3. **+ cross-critique + revision.** The current pipeline with red-team
   disabled: independent analysis, cross-critique, revision, synthesis.
4. **Full workflow, red-team enabled.** Everything in (3) plus the
   optional third-model red-team stage before revision.

Running all four arms on the same question set is what would let a reader
attribute any quality difference to a specific stage, rather than to "the
pipeline" as an undifferentiated whole.

## Candidate metrics

- **Factual error rate.** For questions with checkable facts: rate of
  claims contradicted by a reliable source.
- **Unsupported claims.** Claims stated with unwarranted confidence and no
  justification, reasoning, or acknowledged uncertainty.
- **Missed alternatives.** For decision-support questions: alternatives a
  domain-competent rater would expect to see considered, that the answer
  omitted.
- **Answer quality.** Rubric-scored: reasoning quality, calibration
  (does confidence match actual support?), actionability.
- **Red-team contribution.** Specifically for arm 4: did the red-team's
  findings materially change a revision (coded by a rater blind to which
  stage produced which text), or did the revision read as if the red-team
  report were decorative?
- **Cost.** Already tracked automatically per stage and per run in SQLite
  (`estimated_cost_usd`) -- no new instrumentation needed to report this.
- **Latency.** Already available from each stage's `started_at` /
  `completed_at` timestamps.

### Convergence-analysis-specific metrics

`convergence_analysis` (see
[ADR 006](../docs/decisions/006-explicit-convergence-analysis.md)) makes
several new claims explicit and checkable -- these can only be measured
once the harness below exists, and are listed here so this stage's own
reliability is evaluated with the same rigor as everything else, not
assumed correct because it produces structured output:

- **Frequency of material position changes.** Across a question set, how
  often does the stage report at least one `material: true` change? A
  rate near 0% or 100% would itself be a signal the prompt or model isn't
  discriminating well between substantive and cosmetic changes.
- **Frequency of unresolved disagreement.** How often do the two revised
  positions still disagree, per this stage's own report.
- **Convergence rate.** Distribution across `converged` / `partial` /
  `diverged` / `insufficient_information` over the question set.
- **Agreement with blind human evaluation.** Have a human rater (blind to
  which text is "before" and "after", and blind to the model's own
  convergence label) independently judge whether a material change
  occurred and whether the two final positions actually agree; compare to
  this stage's output.
- **False convergence.** The system reports `converged` or `partial` while
  blind human evaluators identify a meaningful remaining disagreement the
  report missed or understated. This is the failure mode this stage
  exists specifically to catch in the *synthesizer* -- it needs to be
  checked for in this stage too, not assumed absent.
- **False disagreement.** The reverse: the system reports unresolved
  disagreement or non-convergence where a blind human rater sees the
  positions as substantively the same.
- **Trigger-attribution accuracy.** When the stage attributes a change to
  a specific prior stage (not marked `"uncertain"`), does a human rater
  agree that stage plausibly caused it? Separately, how often is
  `"uncertain"` used -- a rate of zero across many runs would be a signal
  the model is over-claiming causal attribution rather than genuinely
  admitting uncertainty.
- **Incremental cost/latency.** This stage's own `estimated_cost_usd` and
  `started_at`/`completed_at` duration, isolated from the rest of the
  run's total -- already available per-stage with no new instrumentation,
  same as the metrics above.

## Blind evaluation

Where feasible, raters (human or a separate model acting as judge) should
score answer text with stage labels, model names, and arm identity
stripped, so a rater cannot favor "the one that looks like it came from
the deliberation pipeline" simply because it is longer or more hedged.
This matters most for "answer quality" and "red-team contribution", which
are the metrics most susceptible to that kind of bias.

## Question set

Not yet defined. Needs to be:

- Fixed and versioned, so results are comparable across runs of the eval.
- Domain-diverse, to avoid over-fitting conclusions to one topic area.
- Split into (a) genuinely open decision-support questions with no single
  correct answer, to measure reasoning quality and missed alternatives,
  and (b) questions with checkable facts, to separate factual-accuracy
  failure modes from reasoning-quality ones.

## Status

Not implemented. No results exist. Any apparent advantage of the full
pipeline over a single-model call, anywhere else in this repository's
documentation, is a design hypothesis, not a measured result, until this
plan (or something like it) has actually been run.
