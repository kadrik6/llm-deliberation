# ADR 010: Truncation recovery gets a higher cap, and so does the default

Status: Implemented. CLI + web; no live API calls made while developing or
testing this (every provider call is faked -- see the test files listed
below).

## Context

Run `cc184d4f0302`: `analysis_b` (claude-opus-5, effort=high,
`MAX_OUTPUT_TOKENS` 5000) truncated twice -- the one bounded recovery
retry (see the reliability-pass truncation handling in
`orchestrator.run_stage`) ran with the exact same cap and truncated
again, wasting ~$0.26 total.

Investigation (read-only, no API calls): the stored stage had no persisted
token counts or text for the failed call (`mark_stage_failed` never
recorded either), but back-solving both attempts' real cost against the
known per-token pricing and the reconstructed prompt implied ~4985-4990
output tokens on **both** attempts -- essentially the entire 5000-token
cap, twice. `AnthropicProvider.generate()` passes `effort="high"` via
`output_config`, which shares one `max_tokens` budget between reasoning/
thinking tokens and the visible answer -- not a bound on visible text
alone. That is the signature of thinking consuming most of the budget
before the visible answer could finish; the existing recovery prompt's
"be more concise" instruction is aimed at verbose prose, not this.

**This is not the same problem ADR 008 already ruled out a cap increase
for.** ADR 008 considered raising the cap and rejected it for a
*systematic, language-density* truncation (Estonian-output runs hitting the ceiling
during verbose intermediate reasoning) -- rejected because a bigger
ceiling "still eventually gets hit by a sufficiently complex question,
just later," and because the real fix was giving verbose stages an
English working language instead (ADR 008's `working_language` feature).
Run `cc184d4f0302` had `language='et'`, `working_language='en'` --
ADR 008's own fix was already active, and the stage still truncated, in
English, from `effort=high` reasoning overhead. That is a different,
bounded mechanism (a fixed per-call reasoning-budget consumption, not an
open-ended density multiplier that scales with input complexity), so
raising the cap here is not "papering over" the thing ADR 008 rejected --
it addresses a failure mode ADR 008 never considered.

## Decision

### 1. The one bounded recovery retry gets a higher cap, passed per call

`orchestrator.run_stage`'s truncation-recovery retry now sends its single
retry with `max_output_tokens` raised by
`TRUNCATION_RECOVERY_MAX_OUTPUT_TOKENS_MULTIPLIER` (2x), passed as an
explicit **per-call override argument** to `Provider.generate(...,
max_output_tokens=...)` -- the same convention
`GeminiProvider.generate`'s existing `timeout_seconds` override already
uses -- rather than temporarily mutating `provider.max_output_tokens` and
restoring it afterward. `Provider.generate`'s signature (and
`OpenAIProvider`/`AnthropicProvider`'s implementations) now accept this
parameter; `GeminiProvider`/`GeminiFallbackProvider` are unaffected --
truncation-recovery is already skipped for `GeminiFallbackProvider` (it
has its own retry/fallback budget), so they never receive it.

The existing per-call budget-guard check for the recovery call
(`estimate_max_call_cost_usd`) already reads the raised value, so a budget
too tight for the bigger retry is still correctly blocked, never silently
exceeded.

### 2. Failed/truncated attempts now persist their real token counts

`run_stage` sums both attempts' `Usage` (not just cost) into the final
response when a retry happens, and `ProviderGenerationError` gained
`input_tokens`/`output_tokens` fields (0 by default, for the common case
where no call was ever sent). `store.mark_stage_failed` now accepts and
persists `input_tokens`/`output_tokens` (overwriting, not accumulating --
mirroring `mark_stage_succeeded`'s own existing convention). `service.py`'s
two call sites that previously dropped this information
(`ProviderGenerationError` and the still-incomplete-after-recovery branch)
now pass it through. Before this, a failed stage's token columns stayed at
0 forever, even when a real, paid call had genuinely used real tokens --
forcing exactly the back-solve-from-cost this investigation had to do.

### 3. `MAX_OUTPUT_TOKENS` default raised from 5000 to 16000

`config.py`'s default and `.env.example` both changed; `_HIGH_OUTPUT_FRACTION`
(1.0, i.e. the worst-case pre-run estimate still assumes 100% of the cap
is used) is **unchanged**. This does not increase real-world spend for a
normal, non-truncating response -- the existing prompts already target a
concise visible answer (e.g. "1200-1800 words, preferably shorter")
regardless of the ceiling; raising the ceiling only gives `effort=high`
reasoning more room before hitting it.

**It does make the worst-case pre-run/admission-time cost estimate ~3.2x
more conservative**, since that estimate is `_HIGH_OUTPUT_FRACTION *
max_output_tokens`. Measured directly (`balanced` profile,
red-team on, real historical per-stage costs from a completed run as the
"real spend" and the real `estimate_run_cost_range`/`stage_upper_bound_cost`
functions for the worst-case admission math):

| | low estimate | high estimate |
|---|---|---|
| cap=5000 (old) | $0.32 | $0.96 |
| cap=16000 (new) | $0.93 | $2.98 |

Simulating the full wave-by-wave admission sequence with
`max_run_cost_usd=$1.00` and that same real run's actual per-stage spend:
at cap=5000 the run gets admitted all the way to **synthesis** (the last
stage) before the budget guard blocks it; at cap=16000, the identical real
spend gets blocked at **`revision_b`, in wave 3** -- roughly halfway
through -- purely because the *admission-time estimate* is now bigger,
not because anything actually cost more.

### 4. New: refuse to start instead of stopping mid-run

`DeliberationService.estimate_worst_case_cost(profile, red_team_enabled)`
wraps `cost_budget.estimate_run_cost_range` (the same function `index.html`'s
per-profile cost display already uses) as a preflight, like
`check_readiness`. Both `cli.py` and `web/app.py`'s `submit_run` now call
it after the final `red_team_enabled` state is known (i.e. after the
Gemini-unavailable "start without red-team?" choice, if that path was
taken) and **before `create_run()` is ever called**: if the requested
`max_run_cost_usd` is below that estimate's high end, the run is refused
with a message naming the amount actually needed, instead of being
admitted and discovering partway through a wave that it cannot finish.

- CLI: prints the refusal to stderr and exits 2. No run row is created.
- Web: re-renders the submission form with a translated error (EN/ET),
  the typed values preserved. No run row is created.

A request with no `max_run_cost_usd` at all is never refused (nothing to
compare against) -- unchanged, uncapped behavior.

## Why

- **Why per-call `max_output_tokens` instead of mutating the provider
  instance:** simpler, and there is nothing to restore even if the call
  raises -- no `finally` block needed, no risk of a mutation surviving an
  exception path. Matches `GeminiProvider.generate`'s own existing
  per-call `timeout_seconds` override convention, rather than inventing a
  second pattern for the same kind of problem.
- **Why 2x (not a different multiplier) for the retry:** proportionate to
  the cost of being wrong in either direction -- too small risks
  reproducing the exact same truncation (as this run did, with an
  unchanged cap); too large pointlessly raises the worst-case admission
  estimate for a single bounded retry. 2x gave this specific incident's
  ~99.7-99.8%-of-cap attempts roughly 2x the headroom to actually finish.
- **Why overwrite (not accumulate) tokens on `mark_stage_failed`:**
  consistency with `mark_stage_succeeded`'s own existing convention for
  the same two columns, rather than introducing a new, asymmetric
  accumulation rule that exists nowhere else for tokens (cost already
  accumulates for a documented reason -- never drop real spend --
  but no equivalent token-level guarantee exists today for a stage
  retried across *separate* `retry_stage` invocations, and adding one was
  judged out of scope for this fix).
- **Why 16000, not a smaller number:** chosen to give reasoning-heavy
  responses comfortable headroom rather than a marginal bump, accepting
  the admission-time estimate trade-off explicitly (see the refusal gate
  below, which exists specifically to make that trade-off safe rather
  than silently surprising).
- **Why refuse before `create_run()` instead of lowering
  `_HIGH_OUTPUT_FRACTION`:** `_HIGH_OUTPUT_FRACTION=1.0` is the genuinely
  honest worst case -- a stage *can* use the entire cap, and the admission
  math exists specifically to protect against that. Lowering it to make
  budgets "feel" more permissive would make the guard wrong, not more
  useful. Refusing early when a budget is already insufficient for the
  real worst case fixes the actual problem (sunk cost on a run that can
  never finish) without weakening the guard itself.
- **Why this doesn't contradict ADR 008:** see Context above -- different
  failure mechanism (bounded reasoning-budget consumption vs. unbounded,
  input-complexity-scaling language density), and ADR 008's own fix
  (English working language) was already active on the run that motivated
  this ADR.

## Trade-offs

- A `--max-cost` / web budget that used to be sufficient may no longer be,
  purely because of the bigger worst-case estimate -- **not** because real
  spend increased. Documented here and in the refusal message itself
  ("This is a ceiling, not expected spend -- real cost is typically well
  below the worst case").
- The refusal gate adds one more preflight call
  (`estimate_worst_case_cost`) before every budgeted run -- free (no
  network, pure arithmetic from already-loaded `Settings`), but one more
  thing that can theoretically disagree with the actual mid-run guard if
  the two ever drift (they currently share the same underlying
  `estimate_max_call_cost_usd`/`_HIGH_OUTPUT_FRACTION` logic, so they
  shouldn't).
- `ProviderGenerationError.input_tokens`/`output_tokens` default to 0 for
  every one of the several raise sites that genuinely have no call to
  report (setup failure, budget-blocked-before-dispatch, generic
  exceptions) -- correct, not a gap, but worth knowing these fields are
  only ever non-zero for the two paths that actually thread real usage
  through (truncation-recovery's own raises, and the budget-exceeded-
  during-recovery path, which carries the sunk attempt's real tokens).

## Failure modes

- A stage whose real per-call cost estimate the admission math
  under-estimates for a different reason (e.g. an unusually large
  question) can still hit the *mid-run* `RunBudgetGuard` even after
  passing the pre-run refusal gate -- the refusal gate is a preflight
  convenience, not a replacement for the existing hard guard, which
  remains the actual backstop.
- The 2x retry multiplier is a single global constant, not tuned per
  provider or per effort level -- a provider/effort combination that
  needs more than 2x headroom would still truncate on the recovery
  attempt too (failing cleanly, as today, not looping further).

## How we plan to evaluate it

Not yet measured against further live runs (none were made while
developing this fix). The next real truncation incident is the natural
check: does the doubled-cap retry actually finish, and does the refusal
gate's message accurately describe what budget was actually needed.

## Test plan (all offline, every provider call faked)

- `tests/test_provider_completion.py` -- the retry is sent with the raised
  cap (recorded via the `max_output_tokens` kwarg actually passed to
  `generate()`, not by reading `provider.max_output_tokens`, which is now
  never mutated); the shared provider's own cap stays untouched across
  success, a second truncation, and the retry itself raising; both
  attempts' tokens survive, summed, in all three outcomes.
- `tests/test_budget.py` -- the recovery call's budget-guard check uses
  the *raised* cap's bigger estimate, not the original's (numbers chosen
  so the two disagree, confirmed to actually catch the bug by reverting
  the fix locally first); the sunk attempt's real tokens survive a
  budget-blocked recovery attempt.
- `tests/test_service.py` -- a truncated, failed stage persists its real
  (non-zero) token counts end to end through `service._execute`.
- `tests/test_config.py` -- `MAX_OUTPUT_TOKENS` defaults to 16000 when
  unset, and an explicit override is still respected (both with
  `load_dotenv` stubbed out, so these never depend on a developer's own
  local `.env` file).
- `tests/test_cli_budget_refusal.py` / the budget section of
  `tests/test_readiness_web.py` -- a `--max-cost`/web budget below the
  real worst case is refused with no run created, the message names the
  correct needed amount, the estimate reflects a Gemini-declined
  red_team's *lower* real worst case (not the originally-requested
  higher one), and a sufficient or absent budget is never refused.
