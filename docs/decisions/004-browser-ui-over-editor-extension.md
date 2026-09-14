# ADR 004: Local browser UI before an editor extension

## Context

Once the CLI and the durable `DeliberationService` backend were working,
the next milestone was a lower-friction interface so normal use did not
require terminal commands. Two candidates were considered: a local browser
UI (FastAPI + server-rendered HTML), or a VS Code extension.

## Decision

Build a local, `127.0.0.1`-only browser UI first (FastAPI + Jinja2 +
Server-Sent Events, no frontend build pipeline). Defer any editor
integration to a later, separate milestone.

## Why

The tool's primary loop so far is open-ended decision support: a
question, optional context, independent analyses, critiques, and a final
answer to read, compare, and export. That maps more naturally onto a
general document/comparison surface than onto an editor's code-centric
one. A browser UI is also reachable regardless of which editor (or none)
someone uses, and `DeliberationService` already presented a clean seam a
thin web layer could sit on without touching orchestration logic --
described in the project README's architecture diagram.

## Trade-offs

- A browser UI does not get automatic access to open files, selections,
  diffs, or workspace state the way an editor extension would -- there is
  no way to say "deliberate about this diff" without the user manually
  pasting it in.
- If real-world use turns out to be dominated by code-review-shaped
  requests ("help me with this diff/file", "should I merge this"), an
  editor extension may turn out to be more valuable than the browser UI,
  and this decision would need revisiting.
- The browser UI has no authentication (see
  [`docs/trust-model.md`](../trust-model.md)), which is an acceptable
  scope limit for a personal local tool but would need addressing before
  any multi-user or networked deployment.

## How we plan to evaluate it

Informal, based on actual usage: whether requests in practice skew toward
general research/decision questions (favors the current browser-first
choice) or toward "help me with this specific file/diff" questions (would
favor building the editor integration sooner rather than later). No
formal evaluation is planned for this decision beyond that.
