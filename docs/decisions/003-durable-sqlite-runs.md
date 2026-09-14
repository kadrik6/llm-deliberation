# ADR 003: Durable SQLite-backed runs instead of one in-memory pipeline

## Context

The original implementation ran the entire 8-stage pipeline as a single
in-memory `async` function: one crash, one provider error, or one rate
limit anywhere in the sequence lost the whole deliberation, including
stages that had already succeeded and already been paid for.

## Decision

Every run and every stage is persisted to a local SQLite database
(`data/deliberation.db`) immediately after that stage succeeds. A
`DeliberationService` (`create_run` / `start_run` / `resume_run` /
`retry_stage` / `get_run` / `list_runs`) is the single seam both the CLI
and the web UI go through. Stages track an explicit status
(`pending` / `running` / `succeeded` / `failed`); resuming or retrying a
run re-runs only stages that have not already succeeded.

## Why

LLM calls cost real money and take real time. For a local, single-user
tool, losing several already-completed, already-paid-for stages because a
later stage hit a transient provider error is avoidable waste, not an
acceptable cost of simplicity. SQLite is more than sufficient to make runs
durable and resumable without introducing any external infrastructure --
no queue, no separate worker process, no database server.

## Trade-offs

- Replaced one simple `async` function with a repository layer, a
  service layer, and an explicit per-stage state machine -- more code and
  more moving parts than the original single-shot pipeline.
- Assumes a single-process, single-writer workload. This is documented as
  a constraint (see [`docs/trust-model.md`](../trust-model.md)), not
  solved for multi-writer or distributed use -- there is no plan to add
  that in this project's current scope.
- Does not solve in-flight crash recovery cleanly: a stage that was
  `"running"` when the process died has no result recorded and needs an
  explicit resume/retry; there is no automatic stale-run detection.

## How we plan to evaluate it

This is primarily a reliability/engineering decision, not a research
hypothesis, so it is validated differently from the other ADRs: by the
test suite (`tests/test_service.py`, `tests/test_store.py` -- persistence,
resume, and retry behavior, including "a later failure must not erase
already-completed work") and by ordinary use, rather than by a comparison
study.
