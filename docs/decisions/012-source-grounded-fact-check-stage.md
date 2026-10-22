# ADR 012: Source-grounded fact-check stage before synthesis (proposal)

Status: Decided (design only -- no code written, no stage implemented yet).
`--context` ships first; provider web search is deferred to a later
follow-up, not abandoned. This ADR compares both candidates and prices
each, then records the decision and why the cheaper, zero-new-dependency
option goes first.

## Context

Nothing in the current pipeline (`analysis_a/b` -> `critique_a_of_b/b_of_a`
-> `revision_a/b` -> `convergence_analysis` -> `synthesis` [-> `red_team`])
ever checks a claim against a source. Every stage is the model's own
reasoning from the question (and, if supplied, `--context`); critique
corrections are just as unsourced as the original analyses. A real run
(`22ad158a3b2d`, the Estonia state-budget question) illustrates the gap
well: its synthesis's own section 4, "Remaining uncertainty and what should
be verified," is effectively an admission that several load-bearing
premises (the size of the cyclically-adjusted deficit, the status of recent
tax measures, debt projections) were never checked against anything --
the model correctly flagged them as needing verification, but nothing
upstream had tried.

Requirement for the new stage, as given: every material claim and every
critique correction must end up with either a source or an explicit
"unverified" label. This ADR compares the two realistic ways to source
that verification and prices each, then decides which to build first.

A useful existing hook: `convergence_analysis` (ADR 006) already extracts
`evolution.material_changes` (each tagged `material: bool`) and
`evolution.unresolved_disagreements` as structured data -- this is already
exactly "the critique corrections that mattered," pre-extracted from raw
prose. A fact-check stage placed *after* `convergence_analysis` can consume
that structured list directly instead of re-deriving "what changed and
why" from scratch, which keeps its own input small regardless of which
sourcing option is chosen below.

## Decision

Build Option B (`--context`) first. Option A (provider web search) stays
documented below as the planned second iteration, not a rejected
alternative -- see "Why `--context` first" for the reasoning and for what
would make it worth picking up.

Both options share the same stage shape: a new `fact_check` stage, run
after `convergence_analysis` and before `synthesis`, that takes (a) the
final revisions, (b) `convergence_analysis`'s `material_changes` and
`unresolved_disagreements`, and (c) whatever source material is available
to it, and emits one verdict per claim/correction: a source (quote +
identifier) or `unverified`. `synthesis`'s own prompt would then need to
carry those verdicts into the final answer -- a downstream prompt change,
out of scope for this ADR, but worth naming as a consequence either option
incurs.

### Option B: user-supplied sources via `--context` -- decided, first

The `fact_check` call is restricted, by prompt instruction only, to citing
material that is textually present in the run's own `--context` field (no
browsing, no new tool). Any claim or correction not supported by something
in `--context` is marked `unverified`.

**Cost per run (declared-approximate, same method as
`cost_budget.estimate_run_cost_range`):** assigning the stage to
`claude-opus-5` ($5.00/$25.00 per 1M input/output tokens), typical input
~12,000 characters (the `material_changes`/`unresolved_disagreements` list
plus a typical few paragraphs of user-pasted context, assumed ~4,000
characters), output scaled the same 0.3x/1.0x of `MAX_OUTPUT_TOKENS`
(16,000 default) as every other stage:

- Low: ~$0.14. High: ~$0.42.
- No new cost dimension: this is ordinary token cost, fits the *existing*
  `cost_budget.py`/`RunBudgetGuard` machinery with no new estimation path
  needed.
- Input cost is dominated by output, not input, at these sizes
  (`CONSERVATIVE_CHARS_PER_TOKEN`'s own 3-chars/token input estimate means
  even a much longer `--context` moves the total only slightly) -- e.g.
  doubling the assumed context length to ~8,000 characters changes the
  estimate by roughly a tenth of a cent. A genuinely long `--context`
  (many pages) would need re-checking against this, but ordinary use
  shouldn't move the number much.
- **Estimate: ~$0.14-$0.42 per run**, roughly a 5-14% increase over the
  balanced+red-team worst case used in `tests/test_cli_budget_refusal.py`
  (~$2.98).

### Option A: provider web search -- deferred, planned second iteration

The `fact_check` call uses the assigned provider's own web-search/browsing
tool-use capability to look up each material claim or correction live and
attach a URL, or mark it `unverified` if nothing adequate turns up within a
bounded number of search attempts per claim.

**Cost per run (same method):** assigning the stage to `claude-opus-5` as
well, typical input inflated to ~18,000 characters (the same claim list
plus inserted search-result snippets for ~5-8 claims):

- Token cost alone: ~$0.15 (low) to ~$0.43 (high).
- **Plus a per-search-call fee that `pricing.PRICING` does not model at
  all today** -- web-search tool use is commonly billed per call,
  independent of token price, order-of-magnitude $0.01-$0.03/call on
  current public pricing pages. ~5-10 search calls per run (one or two
  per material claim) adds roughly **$0.05-$0.30 per run** on top of the
  token cost above.
- **Combined estimate: ~$0.20-$0.73 per run**, roughly a 7-25% increase
  over the same ~$2.98 baseline.
- This per-call figure is an assumption, not a verified number: it would
  need checking against the actual configured provider/model's current
  published rate before being trusted inside a real budget guard.

## Why `--context` first

- **Cost is a secondary factor, not the deciding one.** Both options are
  small next to a run's existing ~$3 total (~$0.14-$0.42 vs. ~$0.20-$0.73)
  -- the gap between them is a few tenths of a cent to a few tenths of a
  dollar, not the reason to pick one over the other.
- **The deciding factor is build/verification risk, and `--context` has
  far less of it.** Option B needs no new provider capability (an
  existing field gets a new consumer), no new budget-guard code path (it
  fits `cost_budget.py`/`RunBudgetGuard` exactly as every other stage
  does today), and no new kind of test fake (`FakeOrchestrator` already
  covers it). Option A needs all three: a verified web-search tool-use
  integration per provider, a new cost-estimation path for a fee
  `pricing.PRICING` doesn't model, and a new fake for the search-tool
  response shape before it can be tested offline at all.
- **The honest-`unverified`-by-default behavior is a feature here, not
  just a limitation.** A run with no `--context` coming back mostly
  `unverified` is consistent with this project's standing preference
  (also `language_detect.py`'s) for a correct "uncertain" over a false
  positive -- it tells the user plainly that nothing was actually checked,
  rather than silently skipping verification.
- **What would change this:** if, after shipping Option B, most real runs
  still come back mostly `unverified` because users aren't supplying
  `--context` (the known trade-off below), that is the concrete signal to
  build Option A next -- at that point the web-search integration and
  budget-guard work pay for themselves because the cheaper option
  demonstrably isn't reaching most runs.

## Trade-offs

**Option B (`--context`), decided:**
- (+) No new provider capability -- an existing field gets a new consumer.
  Fully deterministic and testable with the existing `FakeOrchestrator`
  pattern, no new kind of fake needed.
- (+) Fits the existing `cost_budget.py` estimation path exactly as-is.
- (-) Verification quality is capped by whatever the user pasted into
  `--context` *before the run started*. `--context` cannot be added or
  edited later -- `resume_run()`/`retry_stage()` take only a `run_id`, not
  new context -- so a user who didn't anticipate which claims would need
  sourcing has no way to supply it mid-run.
- (-) A run with no `--context` (today's default) would come back almost
  entirely `unverified` -- an honest result, but it means the feature's
  practical value depends entirely on the user remembering to paste
  source material in up front. This is the specific thing to watch after
  shipping (see "What would change this" above).

**Option A (web search), deferred:**
- (+) Works with zero user effort, including the common case of no
  `--context` supplied at all.
- (+) Can check a claim against current real-world facts, not just
  whatever the user happened to paste.
- (-) Requires the provider's web-search tool to actually be available and
  wired up for whichever model/account is configured -- not guaranteed for
  every provider this app supports today; a new integration, not just a
  prompt change.
- (-) Needs a new budget-guard code path: `RunBudgetGuard` (ADR 010) only
  prices token usage today, and has no notion of a per-call tool fee.
- (-) Not reproducible the way every other stage is today: testing it
  offline needs a new kind of fake (a fake search-tool response), not just
  `FakeOrchestrator`'s existing fake-text pattern.
- (-) Adds real wall-clock latency per search round-trip, on top of
  generation time.

## Failure modes

- Either option: a claim that is true but phrased differently from its
  source (paraphrase, unit conversion, different currency) may be marked
  `unverified` even though a source exists -- a false negative, which this
  project's existing philosophy (ADR-less `language_detect.py`'s own
  "uncertain is safer than a false positive" framing) would treat as the
  acceptable failure direction over a false "verified."
- Option B specifically: if `--context` itself contains an error, the
  stage will faithfully mark a wrong claim "verified" against it -- the
  stage only checks internal consistency with `--context`, never
  `--context`'s own accuracy.
- Option A specifically (for when it is built): a web search can return a
  plausible-looking but low-quality or outdated source; "has a URL" is not
  the same as "is correct." The stage's output would be a source attached,
  not a correctness guarantee.

## How we plan to evaluate it

Not yet -- this ADR is pre-implementation. The Option B implementation ADR
needs to cover: exact stage placement and prompt, how `synthesis` consumes
`fact_check`'s verdicts, and what a sourced-vs-sourceless real run actually
looks like. After it ships, watch the metric named above (what fraction of
real runs come back mostly `unverified` for lack of `--context`) to decide
whether/when to pick up Option A; if that happens, its own follow-up ADR
would still need a verified, provider-sourced per-call search fee before
that number is trusted inside a real budget guard.

## Test plan

None yet; no code exists for this stage. The Option B implementation ADR
would need to specify, at minimum: offline coverage via `FakeOrchestrator`,
a case where `--context` is absent (confirms it degrades to `unverified`
rather than silently omitting the claim), and a case where `--context` is
present and does/doesn't support a given claim. Option A's test plan
(a fake for the search-tool response shape, same absent/empty-result
coverage) is deferred along with the feature itself.
