# ADR 005: Gemini red-team model fallback is bounded, transient-only, and always disclosed

## Context

The Gemini red-team stage ([ADR 002](002-red-team-not-voting.md)) is
optional but, when enabled, calls a single configured model
(`GEMINI_MODEL`). In practice the newest Gemini model (`gemini-3.8-flash`
at time of writing) intermittently returns transient 5xx / high-demand
errors. Before this change, any such error simply failed the stage; the
user's only recourse was to click "Retry failed stage" and hope.

The operator wants the newest model attempted first (it is presumed
strongest), but wants a temporary outage of that specific model to
degrade gracefully instead of blocking the whole run.

## Decision

`GeminiFallbackProvider` (`src/llm_deliberation/providers.py`) wraps an
ordered chain of models -- the preferred model
(`config.Settings.gemini_model`) followed by
`config.Settings.gemini_fallback_models`, parsed from a comma-separated
`GEMINI_FALLBACK_MODELS` env var (default:
`gemini-3.7-flash,gemini-3.6-flash`; empty string disables fallback
entirely). It presents the same `Provider.generate()` interface as every
other provider, so nothing about `orchestrator.WAVES`,
`STAGE_PROVIDER`, or `DeliberationService`'s stage sequencing changes --
fallback is invisible to the rest of the architecture.

**What counts as fallback-eligible ("transient"):** HTTP 500/502/503/504
(`google.genai.errors.ServerError`) and non-HTTP errors that never
produced a response at all (e.g. a connection drop). **What never
triggers fallback:** any `google.genai.errors.ClientError` (HTTP 4xx --
bad/missing API key, billing, invalid request, unsupported parameters,
malformed prompt). This split is not a heuristic on error text; it is the
SDK's own `raise_error` status-code classification
(`classify_gemini_error`), so it can't be fooled by a misleading message
and can't accidentally swallow a real configuration problem.

**Retry budget:** each model in the chain gets its own bounded
retry-with-backoff (a handful of attempts, exponential backoff with
jitter, on the order of seconds -- not an infinite loop) before the next
model is tried. We checked whether `google-genai` (2.23.0) already retries
5xx responses internally: by default it does not
(`retry_args(None) -> stop_after_attempt(1)`); the `GeminiProvider` client
now pins `retry_options={"attempts": 1}` explicitly so that guarantee
does not depend on an undocumented library default, and our own retry
logic is unambiguously the only retry logic in play.

**Disclosure, not silence:** every attempt across every model is recorded
(`ProviderGenerationError.attempt_log` / `ModelResponse.attempt_log`) and
persisted on both success and failure (`stages.requested_model`,
`fallback_used`, `fallback_reason`, `model_attempts`, `attempt_log`).
Requested vs. actual model, the fallback reason, and attempt count are
shown in the running pipeline view, the finished run's artifact detail,
and the Markdown export -- a successful fallback is shown as a disclosure,
not an error banner.

**No silent skip:** if every configured model exhausts its retries, the
red-team stage is left `failed` with three explicit user actions --
"Retry preferred model" (`mode=preferred_only`, only the first model,
still with its own retries), "Retry with fallback chain"
(`mode=chain`, the normal full walk), and "Skip red-team and continue"
(a new `skipped` stage status, `DeliberationService.skip_stage`). Skipping
is deliberately not automatic: the pipeline only proceeds past a failed
red-team stage when a person clicks the button, but once they do,
revision/synthesis proceed exactly as when red-team is disabled from the
start (`texts.get("red_team")` already tolerates its absence).

**Cost accounting:** a successful response's `estimated_cost_usd` is
priced from the model that actually produced it, plus any cost billed by
failed attempts before it (in practice usually $0, since error responses
generally carry no usage metadata -- see
`providers._cost_of_failed_attempt`). This total, not just the successful
attempt's cost, is what is persisted and summed into the run total, so a
paid-but-failed attempt cannot silently vanish from the reported spend.

## Trade-offs

- Pricing for `gemini-3.7-flash` / `gemini-3.6-flash` in `pricing.py` is
  an estimate pending confirmed rate-card numbers, not a verified figure
  -- flagged in a code comment there.
- Applying the same bounded retry to every model in the chain (not just
  the preferred one) means a worst-case full-chain failure takes longer
  (up to roughly the sum of each model's retry budget) than failing fast
  on the first model alone. This was chosen for consistency and because a
  fallback model can be transiently overloaded too; it is still bounded,
  not unbounded.
- This mechanism is Gemini-specific (`GeminiFallbackProvider`), not a
  generic "any provider can have a fallback chain" abstraction -- OpenAI
  and Anthropic have no equivalent today. Generalizing it was judged
  premature: there is exactly one caller.

## How we plan to evaluate it

Watch how often the preferred model's retry budget is exhausted vs. how
often a single retry recovers it, once this ships against real traffic --
that ratio should inform whether the backoff schedule (2s/5s/10s) is
well-tuned. Not yet measured against production traffic.
