# Architecture

This document describes how the code is actually organized. For *why* it
is organized this way, see [`why-deliberation.md`](why-deliberation.md)
(the reasoning behind the deliberation pipeline itself) and the
[ADRs](decisions/) (the reasoning behind specific structural decisions).

## Layers

```
        CLI (llm-deliberate)      Browser UI (llm-deliberate-ui)
                 │                          │
                 └────────────┬─────────────┘
                              ▼
                    DeliberationService
                 create_run / start_run / resume_run /
                 retry_stage / get_run / list_runs
                              │
              ┌───────────────┴───────────────┐
              ▼                                ▼
      Repository (SQLite)              DeliberationOrchestrator
   runs / stages / artifacts           provider adapters:
   data/deliberation.db                OpenAI, Anthropic, Gemini
```

`DeliberationService` is the only seam either interface uses -- neither
the CLI (`src/llm_deliberation/cli.py`) nor the web app
(`src/llm_deliberation/web/app.py`) talks to the repository or the
orchestrator directly. That is also the intended integration point for
anything added later (an editor extension, a Windows launcher): they
would depend on `DeliberationService`, not reimplement orchestration.

## Deliberation flow (stage graph)

```
question
  │
  ├───────────────┐
  ▼               ▼
analysis_a      analysis_b        (independent -- see ADR 001)
(OpenAI)        (Anthropic)
  │               │
  └───────┬───────┘
          │
          ├─────────────┬─────────────┐
          ▼             ▼             ▼
  critique_a_of_b  critique_b_of_a  red_team        (red_team optional --
  (OpenAI reviews  (Anthropic       (Gemini looks     see ADR 002)
   analysis_b)      reviews          for assumptions
                     analysis_a)     shared by both)
          │             │             │
          └──────┬──────┴──────┬──────┘
                 ▼              ▼
           revision_a      revision_b
           (OpenAI)        (Anthropic)
                 │              │
                 └──────┬───────┘
                        ▼
                    synthesis
                    (OpenAI)
                        │
                        ▼
              persisted run (SQLite)
              + optional Markdown export
```

Stages are grouped into four "waves" that can run concurrently within a
wave but not across waves (`src/llm_deliberation/orchestrator.py`,
`WAVES`):

1. `analysis_a`, `analysis_b`
2. `critique_a_of_b`, `critique_b_of_a`, `red_team` (if enabled)
3. `revision_a`, `revision_b`
4. `synthesis`

`DeliberationService._execute` walks these waves in order. A stage whose
status is already `"succeeded"` is skipped and its stored text is reused
as input to later stages -- this is what makes resume and retry cheap:
completed, already-paid-for work is never silently redone. If any stage in
a wave fails, later waves are not attempted and the run is marked
`"failed"`, but every stage that already succeeded keeps its persisted
result.

## Persistence model

SQLite (`data/deliberation.db`, stdlib `sqlite3`, no ORM) has three
tables:

- **`runs`** -- one row per deliberation: question, optional context,
  profile, red-team flag, status, timestamps, aggregated estimated cost.
- **`stages`** -- one row per stage per run: name, provider, model,
  status (`pending` / `running` / `succeeded` / `failed` / `skipped`),
  attempt count, token counts, estimated cost, error text, timestamps,
  plus Gemini fallback provenance (`requested_model`, `fallback_used`,
  `fallback_reason`, `model_attempts`, `attempt_log` -- the last a JSON
  array of every attempt made across the whole fallback chain, kept even
  when the stage ultimately fails, since these fields are populated on
  both `mark_stage_succeeded` and `mark_stage_failed`). `skipped` is
  distinct from `failed`: it is the optional red-team stage's "user
  explicitly chose to continue without it" state, and is treated like a
  disabled red-team stage by downstream waves -- not a run failure.
- **`artifacts`** -- the actual generated text for each successful stage,
  written in the same transaction as the stage being marked
  `"succeeded"`.

See [ADR 003](decisions/003-durable-sqlite-runs.md) for why this exists
instead of the original single in-memory pipeline.

## Provider layer

`src/llm_deliberation/providers.py` defines a small `Provider` interface
(`generate(system, prompt) -> ModelResponse`) with three single-model
implementations (`OpenAIProvider`, `AnthropicProvider`, `GeminiProvider`).
Each wraps the corresponding SDK, reads token usage from the response, and
estimates cost from a hand-maintained pricing table (`pricing.py`). The
orchestrator picks which provider handles which stage
(`orchestrator.STAGE_PROVIDER`); prompts live in `prompts.py`.

The red-team stage's provider is `GeminiFallbackProvider`, which wraps an
ordered list of `GeminiProvider` instances (preferred model first,
`config.Settings.gemini_fallback_models` after) instead of a single model.
It still implements the same `generate(system, prompt) -> ModelResponse`
interface, so the orchestrator's `STAGE_PROVIDER`/`WAVES` wiring is
unaware fallback exists at all -- `run_stage` only passes through an extra
`gemini_mode` ("chain" vs "preferred_only") used by the two distinct
"Retry" actions on a failed red-team stage. Fallback is only attempted for
provider-side/transient errors (`classify_gemini_error`, HTTP 5xx or an
unexpected non-HTTP error); a 4xx `ClientError` (bad key, billing, invalid
request) raises immediately with no retry and no fallback. Each model in
the chain gets its own small bounded retry budget (backoff with jitter)
before the next model is tried; a `ProviderGenerationError` carries the
full attempt trail (requested model, actual attempts, per-attempt HTTP
codes/reasons/costs) so the service layer can persist it rather than
collapse it into a single error string. See
[ADR 005](decisions/005-gemini-model-fallback.md).

Model selection is profile-based (`economy` / `balanced` / `max`,
`config.py`), not exposed as raw per-call model picking in the primary UI
-- see the README's "Profiles" section for why.

## Web layer

`src/llm_deliberation/web/app.py` is a thin FastAPI layer: routes read
from `DeliberationService` (`get_run`, `list_runs`, `to_run_result`) or
schedule exactly one service call (`start_run` / `resume_run` /
`retry_stage`) as a `BackgroundTasks` job, guarded by an in-process
`set[str]` of currently-executing run IDs so a duplicate click cannot
launch the same run twice. `web/presenter.py` only reshapes already-loaded
`RunRecord`/`StageRecord` data for the Jinja templates -- it contains no
orchestration logic. Live pipeline updates use Server-Sent Events, with a
plain HTML fragment endpoint (`/runs/{id}/status`) as the same data source
for a no-JS fallback.

## Known scope limits

- Single-process, single-writer SQLite usage is assumed; there is no
  plan to support concurrent multi-process writers.
- No JSON API exists yet for non-browser clients (a future editor
  extension or Windows launcher would need one, or would need to embed
  `DeliberationService` directly, as the CLI and web app both do).
- No evaluation harness exists yet -- see
  [`evals/README.md`](../evals/README.md).
