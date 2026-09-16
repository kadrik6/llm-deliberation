# ADR 008: Working language is a third, independent, persisted concept

## Context

Estonian-output runs on complex/context-heavy questions were repeatedly
hitting Anthropic's `max_output_tokens` ceiling (5000) during verbose
intermediate stages (`analysis_a/b`, `critique_*`, `red_team`,
`revision_a/b`), while the same kind of run completed reliably in English.
Increasing `MAX_OUTPUT_TOKENS` was ruled out (it does not fix a systematic
per-language token-density difference, and this app already bounds output
length deliberately -- see the reliability pass and ADR 005's timeout
reasoning). Adding an explicit translation stage was also ruled out: it is
a new paid provider call per run, a new place for meaning to drift, and it
would still need *something* to decide which language to translate into
and out of -- i.e. it doesn't remove the underlying problem, only adds a
call on top of it.

ADR 007 already separates **UI language** (interface chrome) from **run/
output language** (`store.RunRecord.language`, what the final user-facing
result is written in). This adds a third, orthogonal concept:

**Working language** -- the language verbose intermediate reasoning stages
actually use internally, independent of what language the final result
will be written in.

## Decision

- New field: `store.RunRecord.working_language` (`"en"` | `"et"` | `None`),
  an additive nullable column, same migration pattern as `language` /
  `max_run_cost_usd`.
- New run default (`orchestrator.default_working_language`): always
  `"en"`, regardless of output language. This is a pure efficiency/
  reliability choice, not a statement that Estonian is unsupported (see
  the Product principle below).
- New centralized policy function (`orchestrator.resolve_stage_language`):
  `WORKING_LANGUAGE_STAGES` (the seven verbose stages) resolve to the run's
  working language; `convergence_analysis` and `synthesis` always resolve
  to the run's output language, unconditionally. One function, one place --
  no per-prompt-builder conditionals (see `prompts.py`, which was not
  touched at all: only `base_system`'s language-instruction injection
  point changed, exactly as ADR 007 predicted for a future second axis).
- New instruction text (`prompts.working_language_override_instruction`),
  used only when a stage's resolved language differs from the run's output
  language. It explicitly tells the model the *input* may be in a
  different language than it is being asked to *respond* in, and to treat
  that input's wording/facts/names as authoritative -- a plain "write in
  English" instruction (the one used when output language already *is*
  English) gives no such signal and risks the model translating away
  Estonian-specific facts it was never asked to translate.
- The user's original Question/Context are still never translated,
  rewritten, or touched before storage or before being handed to any
  stage (unchanged from ADR 007) -- every verbose stage still receives the
  real Estonian text; only its own *response* language changes.

**Persisted as a concrete value, not `"auto"`.** The working language
actually used for a run is persisted as `"en"`/`"et"` at creation time, not
as a sentinel meaning "whatever the current default policy resolves to."
An `"auto"` value would need re-resolving at every future read, and if
`default_working_language`'s policy ever changed, an old run's *displayed*
working language would silently drift even though nothing about that run
itself changed -- directly contradicting the goal of knowing what language
was *actually* used. `NULL` remains the distinct "this run predates the
feature entirely" marker, not overloaded to also mean "auto."

**Legacy runs never get retroactively relabeled.** `service._execute`
computes `effective_working_language = run.working_language or
run.language`. For a `NULL` (legacy) row this makes working language equal
output language for every stage, so `resolve_stage_language` returns the
output language everywhere -- exactly that run's original, undifferentiated
behavior, not a new "this run used English" claim about data collected
before the split existed.

**Retry/resume/recovery preserve it.** Both read `run.working_language`
fresh from the DB on every `_execute()` invocation (same pattern
`run.language` already used), so a retried stage resolves identically every
time. The one bounded truncation-recovery retry
(`orchestrator.run_stage`) uses the *stage's own* resolved language for
both its system prompt and its recovery instruction -- an Estonian run's
English-working analysis stage gets an English "your response was cut off"
instruction, never an Estonian one that would pull the model back toward
switching languages mid-recovery.

## Why

- **Why not just raise `MAX_OUTPUT_TOKENS`:** ruled out explicitly --
  papers over a systematic token-density gap rather than addressing it, and
  a larger ceiling still eventually gets hit by a sufficiently complex
  question, just later.
- **Why not a dedicated translation stage:** a new paid call per run, a new
  place for meaning to drift between the "real" analysis and its
  translation, and it doesn't actually solve the problem -- the analysis
  stage would still need to think in *some* language, and if that's still
  Estonian, the token-density problem is unchanged; translating its output
  afterward doesn't make the analysis itself cheaper or more reliable.
- **Why English specifically as the default working language:** it is
  this project's already-required baseline (every run already needs
  `OPENAI_API_KEY`/`ANTHROPIC_API_KEY`, and English is these providers'
  best-supported language); no new dependency or key is introduced.
- **Why convergence/synthesis are hard-excluded from
  `WORKING_LANGUAGE_STAGES`, not just "usually" output-language:** they are
  the two stages a human actually reads as the Decision Dashboard (see the
  Product principle). Letting them ever resolve to the working language
  would leak English into an otherwise-Estonian dashboard -- the one
  outcome this feature must never produce.

## Trade-offs

- Per-stage language is not stored explicitly (no new column on `stages`);
  it is always re-derived via `resolve_stage_language(stage,
  output_language, working_language)` at read/render time. This is
  deliberate -- it is a pure function of two already-persisted run-level
  values, so storing it per-stage would just be redundant, harder-to-keep-
  consistent duplication (`web/presenter.artifact_language_tags` and
  `diagnostics.stage_efficiency_rows` both just call it fresh).
- Only two working-language values exist in practice today (`"en"` for
  every run). If a third output language were ever added whose best
  working language isn't English, `default_working_language` and
  `working_language_override_instruction`'s dict would need a new entry
  each -- both are single, explicit places to extend, not scattered
  conditionals.
- The working-language override instruction is English-only right now
  (`prompts._WORKING_LANGUAGE_OVERRIDE_INSTRUCTIONS` has one entry). A
  stage_language with no dedicated override text falls back to the plain
  output-language instruction rather than raising -- defensive, not
  exhaustively designed for a hypothetical third language yet.

## Failure modes

- Same probabilistic-instruction caveat as ADR 007: nothing enforces or
  retries on a model ignoring the working-language instruction. Unlike
  ADR 007's output-language instruction, this failure mode is lower-stakes
  here -- a verbose intermediate stage answering in Estonian anyway does
  not corrupt the user-facing dashboard (convergence/synthesis still
  re-request Estonian explicitly), it just forfeits some of the token-
  efficiency benefit for that one stage.
- The actual token/reliability benefit is not asserted by this ADR --
  see Section 12/`diagnostics.py`: it should be measured from real runs,
  not claimed in advance.

## How we plan to evaluate it

Via `llm_deliberation.diagnostics.stage_efficiency_rows` /
`format_efficiency_table` against real runs (no live API calls in the tool
itself): compare a legacy Estonian run's per-stage token usage and
finish_reason against a new working-language-English Estonian run's, for
the same or a similarly complex question. Would fit alongside the existing
evaluation plan (`evals/README.md`).
