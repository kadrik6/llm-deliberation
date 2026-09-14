# llm-deliberation

A small local Python orchestrator for **multi-model deliberation**.

It does not run the frontier models locally. The CLI runs on your computer and
calls cloud APIs. The goal is to reduce anchoring and correlated mistakes by
giving different model families distinct roles.

## Workflow

```text
question
  │
  ├───────────────┐
  ▼               ▼
OpenAI          Anthropic
independent     independent
analysis A      analysis B
  │               │
  └───────┬───────┘
          │
          ├─────────────┐
          ▼             ▼
    cross-critiques   Gemini (optional)
                      shared-blind-spot
                      red-team
          │             │
          └──────┬──────┘
                 ▼
          A and B revise
                 │
                 ▼
          final synthesis
                 │
                 ▼
          Markdown report
```

The third model is deliberately **not a judge**. Its job is to identify
correlated failure modes: assumptions or blind spots shared by both main
candidates.

## Profiles

| Profile | OpenAI | Anthropic | Optional red-team |
|---|---|---|---|
| `economy` | GPT-5.6 Terra | Claude Sonnet 5 | Gemini 3.8 Flash |
| `balanced` | GPT-5.6 Sol | Claude Opus 5 | Gemini 3.8 Flash |
| `max` | GPT-5.6 Sol | Claude Fable 5 | Gemini 3.8 Flash |

`balanced` is the default so testing does not accidentally use Fable's higher
token price. All model IDs can be overridden in `.env`.

## WSL / Ubuntu setup

```bash
cd ~/projects
unzip llm-deliberation.zip
cd llm-deliberation

python3 -m venv .venv
source .venv/bin/activate

pip install -U pip
pip install -e .

cp .env.example .env
```

Open `.env` and add:

```dotenv
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
GEMINI_API_KEY=...
```

Never commit `.env`.

## Run with all three models

```bash
llm-deliberate --red-team \
  "Should a small team build this product now, delay it, or reject it?"
```

## Run with only the two primary models

```bash
llm-deliberate --no-red-team \
  "Should a small team build this product now, delay it, or reject it?"
```

## Maximum-quality Anthropic profile

```bash
llm-deliberate --profile max --red-team \
  "Your difficult question here"
```

## Cheaper prompt-development profile

```bash
llm-deliberate --profile economy --no-red-team \
  "Test question"
```

## Pipe a long question from a file

```bash
cat question.md | llm-deliberate --red-team
```

## Output

Every run writes a Markdown report under `runs/` unless `--output` is supplied.
It contains:

- both independent answers;
- both cross-critiques;
- the optional third-model red-team report;
- both revisions;
- final synthesis;
- token usage and an estimated per-call / total cost.

Only the final synthesis is printed prominently to the terminal.

## Privacy choice

The code explicitly sets `store=False` for OpenAI Responses and Gemini
Interactions requests where supported by their APIs. Prompts are still sent to
the cloud providers, so do not treat this as a fully local/private LLM system.

## Important design principle

Do **not** let candidate B see candidate A before B has produced an independent
answer. Otherwise B becomes anchored to A and much of the value of using
different model families disappears.

The red-team model sees both initial answers because its role is different:
find what *both* candidates may have missed.

## Pricing snapshot

The local cost estimator is a snapshot, not a billing authority. Provider
prices can change. Update `src/llm_deliberation/pricing.py` when needed.

Snapshot date: 2026-09-14.
