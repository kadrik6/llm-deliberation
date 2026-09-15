# Trust model

This document describes what this project trusts, what it does not, and
what it does not guarantee. It is written for someone deciding whether to
run this tool, extend it, or expose it beyond their own machine.

## What runs where

- **The application** (CLI and web UI) runs entirely on your machine.
  There is no hosted backend, no telemetry, and no external service this
  project depends on other than the LLM providers you configure.
- **Model requests do not run locally.** Every analysis, critique,
  red-team, revision, and synthesis call is a network request to the
  configured provider's API (OpenAI, Anthropic, and optionally
  Google/Gemini). This is **not** an offline or local-LLM system --
  "local-first" here describes where the application and its data live,
  not where inference happens.

## Threat model

This is a single-user, local, personal tool. The assumed environment is:
one person, one machine, running the CLI or the web UI for themselves.

- The **web UI binds to `127.0.0.1` only** by default and has no
  authentication. Anything that can reach `127.0.0.1` on that machine
  (any local user or process, any browser tab) can create runs, read
  history, and trigger retries. This is an intentional scope limit, not an
  oversight -- see [ADR 004](decisions/004-browser-ui-over-editor-extension.md).
  Do not expose it on a network or behind a reverse proxy without adding
  your own authentication layer first.
- There is **no rate limiting or cost cap**. Cost is estimated and
  recorded per stage and per run (see the README's cost-tracking section),
  but nothing in the application will stop you from starting a run that
  costs more than you intended, or from starting many runs in a row.
- The application **does not execute anything the models suggest.** It is
  a decision-support tool that displays and stores text; it does not run
  shell commands, apply patches, or take actions on your system based on
  model output. Treat all model output -- including the red-team's
  findings -- as advisory text you are responsible for evaluating, not as
  verified instructions.

## What is and isn't trusted internally

- **Model output is treated as untrusted content**, not as trusted
  application data. For browser display only, each stage's raw text is
  converted to HTML by a Markdown parser and then passed through a strict
  tag/attribute allowlist (`nh3.clean`, Python bindings for Mozilla's
  actively-maintained Ammonia sanitizer) -- headings, emphasis, lists,
  blockquotes, code, links, and tables render, and everything else
  (`<script>`, `<iframe>`, `<style>`, `<form>` and its controls, event
  handler attributes, `javascript:`/`data:` links, arbitrary raw HTML the
  model or a prompt-injection attempt tried to include) is removed --
  script/style/iframe/form and similar tags have their contents dropped
  too, not just the tag -- before the result is ever marked safe for
  Jinja to render (`src/llm_deliberation/web/markdown_render.py`). No
  template
  applies `|safe` to raw model text directly; the only place output is
  trusted is that one function, after both the parse and the sanitize
  step have already run. The canonical stored artifact (SQLite) and the
  Markdown export are unaffected by this -- both keep the original text
  exactly as the model produced it.
- **API keys are trusted secrets, kept out of every other layer.** They
  are read once from the environment/`.env` (via `python-dotenv`) at the
  point a provider call is made, and are never written to the SQLite
  database, never included in a Markdown export, and never sent to the
  browser. See [`SECURITY.md`](../SECURITY.md).
- **The SQLite database and Markdown exports are unencrypted local
  files.** Anyone with filesystem access to `data/deliberation.db` or
  `runs/*.md` can read every question, context, and generated answer you
  have ever run. This project does not add its own encryption-at-rest;
  rely on your operating system's disk-level protections if that matters
  to you.

## Known non-guarantees

- No guarantee that model output is factually correct, complete, or free
  of fabricated claims -- from any stage, including the "final" synthesis.
- No guarantee that the red-team stage actually catches a correlated
  blind spot on any given run; it is a structured attempt, not a proof.
- No guarantee of availability, correctness, or continued pricing from any
  third-party provider. Cost figures are local estimates from a
  hand-maintained pricing table (`src/llm_deliberation/pricing.py`), not a
  billing authority -- see the README.
- No built-in multi-user isolation, audit logging beyond what is described
  above, or production-grade error recovery. A process crash mid-run
  leaves that run in a `"running"` state in SQLite until you explicitly
  resume or retry it.

If your use case needs guarantees beyond these, this project -- in its
current, experimental state -- is not the right fit without further work.
