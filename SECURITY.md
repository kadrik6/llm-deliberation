# Security

LLM Deliberation is a local-first, single-user experimental tool. This
document describes its actual security posture, not an aspirational one --
see [`docs/trust-model.md`](docs/trust-model.md) for the fuller threat
model and its explicit non-guarantees.

## Scope

This project is a personal research/portfolio tool, not a hosted service.
There are no user accounts, no multi-tenant deployment, and no production
SLA. Treat it accordingly.

## API key handling

- Provider API keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`)
  are read only from environment variables / a local `.env` file via
  [`python-dotenv`](https://pypi.org/project/python-dotenv/).
- `.env` is listed in `.gitignore` and must never be committed.
- Keys are never written to the SQLite database, never included in
  Markdown exports, never sent to the browser, and never logged.
- If you believe a key was accidentally committed to a fork or branch of
  this repository, **rotate that key immediately** with the provider --
  removing it from git history alone does not invalidate it.

## Local web UI

- `llm-deliberate-ui` binds to `127.0.0.1` only, by design, and is not
  meant to be exposed to a network or the internet.
- There is no authentication layer. Anyone with access to `127.0.0.1` on
  the machine running it (any local user, any process, any browser tab)
  can create and read deliberation runs.
- Do not put this behind a public reverse proxy without adding your own
  authentication first.

## Data sent to third parties

Questions, optional context, and every intermediate stage's text are sent
to the configured providers' APIs (OpenAI, Anthropic, and optionally
Google/Gemini) to generate a response. This is inherent to how the tool
works -- see the README's "Privacy & security limitations" section. Do not
submit data you are not willing to send to those providers under their
respective terms of service and data-retention policies.

## Reporting a vulnerability

If you find a security issue in this project's own code (not in a
third-party provider's API), please use GitHub's private vulnerability
reporting for this repository (Security tab -> "Report a vulnerability")
rather than opening a public issue, so it can be addressed before details
are public.
