# LLM Deliberation

> LLM Deliberation is a local-first experimental decision-support tool
> exploring whether structured disagreement between different LLM families
> can produce more robust and auditable answers than a single-model
> interaction.

That sentence is a hypothesis, not a claim of proven results. Please read
the caveats below before the feature list.

## What this is not, and what is not (yet) shown

- **This project does not claim that using multiple models is
  automatically more accurate, better calibrated, or more trustworthy
  than a single strong model.** It might help for some kinds of questions
  and not others, or not at all. That is genuinely unknown.
- **The central hypothesis above has not been empirically evaluated.**
  There is no benchmark, no scored comparison against a single-model
  baseline, and no published result in this repository claiming the
  pipeline "works better." [`docs/why-deliberation.md`](docs/why-deliberation.md)
  explains the reasoning behind the design and is explicit about what
  that reasoning does not establish; [`evals/README.md`](evals/README.md)
  describes the comparison this project intends to run, but has not run
  yet.
- **This is local software, not a local/offline LLM.** The CLI and the
  web UI run entirely on your machine, but every deliberation stage is a
  network request to a third-party provider's API (OpenAI, Anthropic,
  and optionally Google/Gemini). Your questions and generated text leave
  your machine. See [`docs/trust-model.md`](docs/trust-model.md).

If you're evaluating this repository as a portfolio project: the
interesting part is not "does this produce better answers" (unverified),
it's the architecture built to make that question answerable later --
durable, resumable, fully-inspectable runs instead of a black-box chat
response. See [Project status](#project-status) and
[Roadmap](#roadmap).

## Why this design

- **Why a single LLM response can be insufficient for a hard decision:**
  one pass of reasoning from one model, shaped by one training set and one
  set of unstated assumptions, has no built-in mechanism to notice its own
  blind spots or flag which of its confident claims are actually
  load-bearing.
- **Why independent generation before cross-exposure:** if one model saw
  the other's answer first, it would anchor toward it, collapsing the
  diversity of perspective the whole pipeline depends on. Independence is
  a prerequisite for the next stage to mean anything.
  ([ADR 001](docs/decisions/001-independent-generation.md))
- **Why cross-critique:** a different model family is more likely to
  notice a framing choice, omission, or unsupported claim than the model
  that produced it is likely to notice on its own.
- **Why the third model is a red-team, not a voter:** majority voting and
  judge setups reward agreement, not correctness -- if both primary
  models share a correlated blind spot, voting doesn't catch it. The
  optional third model is instructed to look for shared gaps, and never
  ranks or picks a winner.
  ([ADR 002](docs/decisions/002-red-team-not-voting.md))
- **Why every stage is persisted and inspectable:** a synthesis you can't
  trace back to the disagreement that produced it is not meaningfully
  more auditable than a single model's answer. Every independent
  analysis, critique, red-team report, and revision is stored, not just
  the final text.
- **Why convergence/change analysis is its own stage, not part of
  synthesis:** a model whose job is to produce one coherent final answer
  has every incentive to understate remaining disagreement. A separate,
  structured comparison of each candidate's before/after position -- run
  before synthesis ever sees the question -- records what changed, what
  seems to have caused it, and what's still unresolved without that
  pressure. ([ADR 006](docs/decisions/006-explicit-convergence-analysis.md))

Full reasoning: [`docs/why-deliberation.md`](docs/why-deliberation.md).

## Architecture

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

Both interfaces call the same service layer; neither talks to SQLite or
the model providers directly. Details: [`docs/architecture.md`](docs/architecture.md).

## Deliberation flow

```
question
  │
  ├───────────────┐
  ▼               ▼
analysis_a      analysis_b        independent -- neither sees the other
(OpenAI)        (Anthropic)
  │               │
  └───────┬───────┘
          │
          ├─────────────┬─────────────┐
          ▼             ▼             ▼
  critique_a_of_b  critique_b_of_a  red_team        optional; looks for
                                     (Gemini)         blind spots shared
                                                       by both -- does not
                                                       vote or rank
          │             │             │
          └──────┬──────┴──────┬──────┘
                 ▼              ▼
           revision_a      revision_b
                 │              │
                 └──────┬───────┘
                        ▼
              convergence_analysis        compares before/after positions;
              (Anthropic by default)       reports changes, agreements,
                        │                  and unresolved disagreement --
                        │                  never a recommendation
                        ▼
                    synthesis
                        │
                        ▼
        persisted run (SQLite) + optional Markdown export
```

## Screenshots

_Not yet included. Planned: the new-deliberation form, the live pipeline
view mid-run, and the final-answer page with expandable artifacts._

<!-- docs/images/new-run.png -->
<!-- docs/images/pipeline.png -->
<!-- docs/images/result.png -->

## Demo

**There is currently no public hosted demo of this project.** Nothing is
deployed anywhere. The only way to see the web UI today is to run it
yourself on your own machine -- see [Quick start](#quick-start) below.

`http://127.0.0.1:8765`, mentioned under Quick start, is **not** a link
to a hosted site. It is the local address the web UI binds to on your own
machine, and it only resolves to anything while you have
`llm-deliberate-ui` running yourself. See
[Privacy & security limitations](#privacy--security-limitations).

<!-- Planned, not built yet (see Roadmap): a read-only public demo mode
     that replays a handful of stored example runs from this repo --
     no API keys, no live model calls, no way to submit a new question. -->

## Quick start

Requires Python 3.11+ and API keys for OpenAI and Anthropic (Gemini only
if you plan to use the red-team stage). **Primary tested setup: Windows
11 + WSL2 (Ubuntu).** Other environments may work, but this is the only
one actually tested.

### Windows (WSL2 Ubuntu)

Open an **Ubuntu/WSL terminal** -- not PowerShell, not Command Prompt --
and run everything below inside it:

```bash
git clone https://github.com/kadrik6/llm-deliberation.git
cd llm-deliberation

python3 -m venv .venv
source .venv/bin/activate

pip install -e .

cp .env.example .env
# edit .env and add OPENAI_API_KEY / ANTHROPIC_API_KEY / (optional) GEMINI_API_KEY

llm-deliberate-ui
```

This matters because these are Bash commands for a WSL/Ubuntu shell, not
PowerShell: `source .venv/bin/activate` only works in a Unix-style
shell, so pasting these into PowerShell or Command Prompt will fail
partway through. Native Windows (PowerShell) isn't the primary tested
path yet.

`llm-deliberate-ui` starts a server inside WSL -- you don't need a
browser inside WSL itself. Once it's running, open your normal
**Windows** browser and go to `http://127.0.0.1:8765`; WSL2 forwards
that port to Windows automatically. See [Local web UI](#local-web-ui)
below for what that address is and isn't.

No WSL2 + Ubuntu yet? Install that first --
[Microsoft's WSL install guide](https://learn.microsoft.com/windows/wsl/install)
covers it; that setup itself is outside the scope of this README.

### Other environments

These paths are not as thoroughly tested as Windows 11 + WSL2 Ubuntu,
above.

```bash
git clone https://github.com/kadrik6/llm-deliberation.git
cd llm-deliberation

python -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\Activate.ps1     # native Windows PowerShell (untested)

pip install -e ".[dev]"

cp .env.example .env
# edit .env and add OPENAI_API_KEY / ANTHROPIC_API_KEY / (optional) GEMINI_API_KEY
```

**CLI:**

```bash
llm-deliberate --profile economy --no-red-team \
  "Should a small team build this product now, delay it, or reject it?"
```

### Local web UI

```bash
llm-deliberate-ui
# then open http://127.0.0.1:8765 in your browser
```

`http://127.0.0.1:8765` is a **local address, not a hosted URL** -- it
only exists while the command above is running on your own machine. There
is no public/hosted version of this UI to visit instead (see
[Demo](#demo)). If you're on the primary tested setup (Windows 11 +
WSL2), that "own machine" is WSL -- the server runs there, but the
address still opens the same way in your normal Windows browser; WSL2
forwards the port automatically, no extra setup needed. The web UI binds
to `127.0.0.1` only and has no authentication -- it is built for
single-user local use. See [`docs/trust-model.md`](docs/trust-model.md).

## Profiles

Model selection is presented as a profile, not raw model names, because
the meaningful choice for most users is "how much do I want to spend on
this question," not which exact model ID to pick:

| Profile | What it means |
|---|---|
| **Economy** | Fastest/cheapest. Good for routine deliberation. |
| **Balanced** | Stronger models for important questions. |
| **Max** | Highest-quality configuration for difficult decisions. |

Each profile maps to specific OpenAI/Anthropic/Gemini model IDs in
`src/llm_deliberation/config.py`, all overridable via `.env`
(`OPENAI_MODEL`, `ANTHROPIC_MODEL`, `GEMINI_MODEL`).

## Language support

The web UI supports English and Estonian, and the two are two genuinely
separate settings:

- **UI language** -- which language interface labels ("New", "History",
  "Final answer", "What changed?", ...) are shown in. A plain cookie, set
  from the small `EN | ET` switch in the top bar, affecting only your
  browser. It is never inferred from a run and never stored with one.
- **Run/output language** (`en` | `et`) -- the language every model-
  generated stage of *that run* is written in: both independent analyses,
  both critiques, the optional red-team report, both revisions, the
  convergence analysis, and the final synthesis. Chosen per run (defaults
  to whatever your current UI language is, but is independently
  overridable on the "New deliberation" form), and **persisted with the
  run** -- it survives retry, resume, a failed/skipped stage, server
  restarts, and shows up in history and exports.

A few things are true by design, not by accident:

- **The question you type is never translated or rewritten.** Whatever
  language you write your question in, the models receive it exactly as
  entered -- independent of the output language you chose. Asking an
  English question with Estonian output (or the reverse) is intentionally
  supported, not an edge case.
- **There is no automatic language detection.** The output language is
  always an explicit choice (yours, or the profile-independent default),
  never guessed from the question's language.
- **Internal schema/enum values are never translated.** The convergence
  analysis's structured JSON (`"convergence": "partial"`,
  `"material": true`, field names, stage identifiers) stays in stable
  English regardless of the run's output language -- only the
  human-readable string *values* inside it (a topic, a position, a
  reason) follow the run's language. See
  [ADR 007](docs/decisions/007-bilingual-support.md).
- **Historical runs are never translated retroactively.** A run created
  before this feature existed is treated as an English-output run (a
  stated default, not a guess from its content) and its stored text is
  never touched. Viewing an old run under an Estonian UI shows Estonian
  chrome around unchanged original content -- the UI language and a run's
  content are independent by construction, so this falls out for free
  rather than needing special-case handling.

## Red-team

An optional third model (`--red-team` / `--no-red-team`, or a switch in
the web UI) reviews both independent analyses. **It does not vote and
does not rank the two candidates.** Its job is to name assumptions or
blind spots the two primary models might share -- the kind of error a
vote between the two of them would never catch. See
[ADR 002](docs/decisions/002-red-team-not-voting.md) for the reasoning,
including why this is unproven and off by default is a reasonable
starting choice for some workflows.

### Gemini model fallback

The newest configured Gemini model is always tried first
(`GEMINI_MODEL`). If it returns a transient, provider-side failure --
HTTP 500/502/503/504 or a clearly-labeled high-demand response -- after
exhausting a small bounded retry budget (a few attempts with exponential
backoff and jitter, a matter of seconds, not an infinite loop), the app
automatically moves on to the next model in `GEMINI_FALLBACK_MODELS`
(comma-separated, oldest/cheapest last; see `.env.example`).

This is deliberately narrow:

- **Never** triggered by an invalid API key, billing issue, invalid
  request, unsupported parameter, or other deterministic client-side
  error -- those fail immediately, unmodified, so a real configuration
  problem is never hidden behind a silent retry.
- **Fallback does not imply the models are equivalent.** A different
  Gemini version can reason differently about the same prompt. Every run
  discloses, for the red-team stage, which model was *requested* vs.
  which one *actually* produced the output, why a fallback happened (if
  it did), and how many attempts it took -- in the running/result UI, the
  run detail page, and the Markdown export. If every configured model
  fails, the stage is left in a recoverable state with three explicit
  choices (retry the preferred model, retry the whole fallback chain, or
  skip red-team and let revision/synthesis continue without it) --
  nothing is skipped automatically.
- Token usage and estimated cost are always attributed to the model that
  actually ran, and a failed attempt's cost (on the rare occasion a
  failed call still reports usage) is never dropped from the run total.

See [ADR 005](docs/decisions/005-gemini-model-fallback.md) for the full
reasoning and [`docs/architecture.md`](docs/architecture.md) for where
this lives in the provider layer.

## Decision evolution

The tool records not just a final answer, but how the deliberation got
there: a `convergence_analysis` stage runs after both revisions succeed
and compares each candidate's original analysis to its revision, and the
two revisions to each other. It reports, as one validated structured
result (not free-form prose):

- **material position/conclusion changes** -- and what appears to have
  triggered each one (a peer critique, the red-team report, or the
  candidate's own reassessment -- or "uncertain" when the cause genuinely
  can't be traced, which is preferred over a fabricated one);
- **agreements reached** during deliberation;
- **unresolved disagreements** that survived it, with why they remain
  unresolved and what's at stake in leaving them unresolved;
- **remaining unknowns** that would help resolve a disagreement if they
  were available;
- **issues flagged as requiring human judgement** rather than more
  analysis.

In the web UI, a completed run's result page separates recommendation,
position changes, unresolved disagreement, remaining unknowns, and human
judgement so users can inspect not only what the system concluded, but
how the deliberation evolved -- with the full stage-by-stage trace still
available, one click away, for anyone who wants to audit it. This is a
presentation change only: the underlying structured artifact and every
other stage's raw text are unchanged.

**This is model-generated analytical metadata, not proof of causality or
correctness.** The analyst is the same kind of model as every other
stage, asked a narrower question -- it can miss a real change, flag a
cosmetic one as material, or mis-attribute a cause. It is disclosed as
such everywhere it's shown, and it never produces a recommendation; the
final synthesizer receives it as context and is explicitly instructed not
to silently manufacture consensus over a disagreement it reports.

`convergence_analysis` is its own persisted, retryable stage (like
red-team, it can be skipped after a failure so the rest of the run isn't
blocked -- the UI and Markdown export then say plainly "Change/convergence
analysis unavailable for this run" rather than pretending it succeeded).
By default it runs on Anthropic -- deliberately different from the final
synthesizer (OpenAI) -- configurable via `CONVERGENCE_PROVIDER` /
`CONVERGENCE_MODEL` in `.env`. See
[ADR 006](docs/decisions/006-explicit-convergence-analysis.md) for the
full reasoning and [`docs/architecture.md`](docs/architecture.md) for how
it fits into the stage graph.

## Cost & token tracking

Every stage's input/output token counts and an estimated USD cost
(`src/llm_deliberation/pricing.py` -- a hand-maintained snapshot, **not**
a billing authority) are recorded in SQLite as soon as that stage
succeeds, and the run's total is updated after every wave. The CLI prints
the total on completion; the web UI shows it live while a run is in
progress and in the run's history list.

## Retry & resume

Runs are durable: a stage's result is written to SQLite the moment it
succeeds, so a failure elsewhere in the pipeline never erases completed
work. If a stage fails (a transient provider error, a rate limit), you
can:

```bash
llm-deliberate --resume <run_id>
llm-deliberate --retry-stage <run_id> <stage_name>
llm-deliberate --list-runs
```

or use the "Retry failed stage" / "Resume run" buttons in the web UI.
Retrying only re-runs the targeted stage (and whatever it unblocks) --
already-succeeded, already-paid-for stages are never repeated. See
[ADR 003](docs/decisions/003-durable-sqlite-runs.md).

## Privacy & security limitations

- The application runs locally; **model requests do not.** Your question,
  optional context, and every generated stage's text are sent to the
  configured providers' APIs.
- API keys are read from `.env`/environment only, never written to
  SQLite, never included in Markdown exports, never sent to the browser,
  never logged.
- The web UI has no authentication and is meant for `127.0.0.1` only.
- Model output is rendered as sanitized HTML for readability (headings,
  lists, code blocks, etc.), never as raw executed HTML: the web UI parses
  it with a Markdown library, then strips anything not on a strict
  tag/attribute allowlist (`nh3`) before display -- a model producing
  adversarial HTML/script-like content cannot get it to execute. The
  underlying stored artifact and the Markdown export are always the
  original, unmodified text; only the browser view is transformed. See
  [`docs/trust-model.md`](docs/trust-model.md).
- No analytics, no telemetry, no external CDN dependencies in the web UI.
- SQLite and Markdown files are unencrypted local files; anyone with
  filesystem access can read them.

Full detail: [`docs/trust-model.md`](docs/trust-model.md) and
[`SECURITY.md`](SECURITY.md).

## Project status

Experimental / pre-1.0. Functionally working (CLI and web UI both tested,
including live smoke tests against real OpenAI/Anthropic calls), but:

- no empirical evaluation of the central hypothesis yet (see above);
- single-user, single-process design, not hardened for multi-user or
  networked deployment;
- no Windows launcher or editor integration yet (see Roadmap).

## Roadmap

- Evaluation harness implementing [`evals/README.md`](evals/README.md).
- A small JSON API alongside the HTML routes, so a future editor
  extension or Windows launcher can use `DeliberationService` without
  screen-scraping the web UI.
- Windows launcher (deliberately deferred, not started).
- Structured/JSON stage outputs for machine-checkable claims.
- Citation/evidence retrieval before synthesis.
- Configurable retention policy for stored runs.
- Read-only public demo mode: a hosted deployment that only replays a
  handful of stored example runs from this repo -- no API keys, no live
  model calls, no form to submit a new question. Not started; see
  [Demo](#demo).

## Evaluation plan

Not yet run. See [`evals/README.md`](evals/README.md) for the intended
comparison arms (single model / two independent models / + cross-critique
/ full workflow with red-team), candidate metrics (factual error rate,
unsupported claims, missed alternatives, answer quality, red-team
contribution, cost, latency), metrics specific to the convergence-analysis
stage (material-change frequency, convergence rate, agreement with blind
human evaluation, false-convergence/false-disagreement rates), and the
intent to score blindly where feasible.

## Development

```bash
pip install -e ".[dev]"
pytest -q                    # offline: every provider call is faked, no API keys needed
python -m compileall -q src
```

CI (`.github/workflows/tests.yml`) runs the same test suite on pushes and
pull requests and never requires or uses real provider API keys.

## License

[MIT](LICENSE)
