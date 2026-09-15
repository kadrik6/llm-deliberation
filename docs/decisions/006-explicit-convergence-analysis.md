# ADR 006: Convergence/change analysis is a separate, structured, durable stage

## Context

The pipeline produces independent analyses, cross-critique, optional
red-team review, revisions, and a final synthesis. Nothing in that flow
explicitly records *how the deliberation changed anything*: whether a
candidate's position actually moved between its original analysis and its
revision, what seems to have caused that movement, where the two
candidates ended up agreeing, and where they still disagree after seeing
each other's critique. That information exists implicitly in the stored
text of `analysis_a`/`analysis_b`/`revision_a`/`revision_b`, but nothing
compares them, and a reader would have to do that comparison by hand.

The final synthesizer is not a good place to derive this from, because a
synthesis model is specifically trying to produce one coherent final
answer -- exactly the task that rewards smoothing over disagreement rather
than reporting it.

## Decision

`convergence_analysis` is a new durable stage
(`src/llm_deliberation/orchestrator.py`, `WAVES`), running after both
revisions succeed and before synthesis. Its only job is comparison:
`analysis_a` → `revision_a`, `analysis_b` → `revision_b`, and
`revision_a` ↔ `revision_b`. It returns one validated structured object
(`ConvergenceAnalysis`, `src/llm_deliberation/convergence.py`, a Pydantic
model) -- convergence level, material changes with their likely trigger,
agreements reached, unresolved disagreements, remaining unknowns, and
issues flagged as requiring human judgement rather than more analysis. It
never produces a recommendation; that stays synthesis's job.

**Independence from synthesis:** a separate model call
(`Settings.convergence_provider`, default `anthropic` -- different from
the synthesizer's `openai`) performs this comparison, and its structured
result is persisted as its own artifact *before* synthesis ever runs.
Synthesis receives it as read-only context (a rendered digest, embedded in
the prompt) and is explicitly instructed not to silently manufacture
consensus over reported unresolved disagreement -- but `convergence_analysis`
does not depend on synthesis, and synthesis does not own or rewrite its
conclusions. They are two separate, separately-inspectable artifacts.

**Structured, not free-form prose:** the schema is a Pydantic model, and
the same object is used to (a) generate the JSON Schema embedded in the
stage's own prompt (`ConvergenceAnalysis.model_json_schema()`, so the
prompt cannot drift out of sync with the schema), (b) validate the
response before persisting it, and (c) drive the web UI's "Decision
evolution" section and the Markdown export's equivalent section. No new
provider-specific structured-output/tool-calling API was introduced: the
model is asked for one bare JSON object in prose, and the orchestrator
parses + validates it with the existing plain-text `Provider.generate()`
interface completely unchanged -- see "Trade-offs" below.

**Explicit uncertainty over fabricated causality:** `ChangeTrigger.source`
includes `"uncertain"` as a first-class value, and the prompt explicitly
instructs the model to prefer it over guessing a specific cause it cannot
actually trace to the stage material it was given.

**Not a ground-truth judge:** the analysis is produced by the same kind of
model as every other stage, prompted for a narrower task. It can miss a
real change, invent one that isn't material, or mis-attribute a cause.
Nothing in the schema, the prompt, or the UI claims otherwise -- this is
disclosed as analytical metadata, not verified fact, everywhere it's
surfaced.

**Durability:** like every other stage, it persists immediately on
success (provider/model/tokens/cost, and the canonical validated JSON as
the stored artifact text -- no new table or columns), survives a later
synthesis failure, is individually retryable, and is in
`SKIPPABLE_STAGE_NAMES` (see ADR 005 for the retry/skip pattern this
reuses) so a run isn't stuck forever if this specific stage can't
complete. Skipping it is never automatic and is disclosed everywhere the
result is shown: "Change/convergence analysis unavailable for this run."

## Why

- **Why a separate stage instead of asking synthesis to report on
  convergence too:** a single model call under pressure to produce "the
  final answer" has every incentive to understate disagreement it would
  otherwise have to explicitly hold open. Separating "what changed and
  where do they still disagree" from "what's the answer" means the first
  question gets asked without that pressure.
- **Why structured output instead of another prose stage:** "did they
  converge" and "what changed" are exactly the kind of claims that are
  useful to filter, diff across runs, or render distinctly in the UI --
  none of which works well against unstructured prose. A validated schema
  also gives a concrete, checkable definition of what the stage is
  actually supposed to answer.
- **Why Anthropic by default, not Gemini:** Gemini is already the
  optional red-team provider, off by default in some configurations and
  requiring its own API key. Defaulting convergence analysis to it would
  make an always-on stage silently depend on optional configuration.
  Anthropic's key is already mandatory for every run.

## Trade-offs

- Prompted-JSON-plus-validation is simpler than wiring three providers'
  native structured-output APIs, but is inherently less reliable than a
  provider-enforced schema: a model can still return malformed JSON,
  which fails the stage (retryable) rather than being coerced into shape.
  This was judged an acceptable trade for not adding three separate
  structured-output code paths to the provider layer.
- Using the same provider (Anthropic) as `revision_b`/`critique_b_of_a`
  for the default convergence analyst means it is not fully external to
  the deliberation -- it doesn't share a call with those stages, but it
  is the same model family. Full independence would mean defaulting to a
  fourth distinct model, at additional cost, for marginal benefit; this
  is configurable (`CONVERGENCE_PROVIDER=gemini` with your own key) for
  anyone who wants stricter separation.
- One more paid API call per run. Tracked exactly like every other
  stage's cost (see the README's cost-tracking section) -- not hidden,
  not estimated separately from the run total.
- "Uncertain" trigger attribution is honest but less satisfying to read
  than a confident (possibly wrong) causal story. This is the intended
  trade: see requirement 4 in the original design brief -- a fabricated
  cause is worse than an honest "cannot be determined reliably."

## Failure modes

- The analyst model can miss a real material change (false negative) or
  flag a cosmetic wording change as material (false positive).
- It can report "converged" when a blind human reviewer would see
  meaningful remaining disagreement (false convergence), or the reverse
  (false disagreement) -- see `evals/README.md`.
- Trigger attribution can be wrong even when not marked "uncertain": the
  model can confidently point to the wrong prior stage as the cause of a
  change.
- Structured-output parsing can fail on a model response that is
  substantively fine but formatted wrong (e.g. explanatory text before the
  JSON despite instructions not to). This fails the stage rather than
  guessing at a repair, and is retryable.

## How we plan to evaluate it

Not yet run. See `evals/README.md` for the metrics this stage adds to the
evaluation plan: frequency of material changes, frequency of unresolved
disagreement, convergence rate, agreement with blind human evaluation on
convergence/divergence, false-convergence and false-disagreement rates,
and the incremental cost/latency of this stage specifically.
