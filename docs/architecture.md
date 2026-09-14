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
  status (`pending` / `running` / `succeeded` / `failed`), attempt count,
  token counts, estimated cost, error text, timestamps.
- **`artifacts`** -- the actual generated text for each successful stage,
  written in the same transaction as the stage being marked
  `"succeeded"`.

See [ADR 003](decisions/003-durable-sqlite-runs.md) for why this exists
instead of the original single in-memory pipeline.

## Provider layer

`src/llm_deliberation/providers.py` defines a small `Provider` interface
(`generate(system, prompt) -> ModelResponse`) with three implementations
(`OpenAIProvider`, `AnthropicProvider`, `GeminiProvider`). Each wraps the
corresponding SDK, reads token usage from the response, and estimates cost
from a hand-maintained pricing table (`pricing.py`). The orchestrator
picks which provider handles which stage
(`orchestrator.STAGE_PROVIDER`); prompts live in `prompts.py`.

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
