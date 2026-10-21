# ADR 011: CLI gets a --language flag, and refuses to silently guess

Status: Implemented. CLI only (the web form already had a language
selector). No live API calls made while developing or testing this --
every test uses `FakeOrchestrator`.

## Context

A real run (`22ad158a3b2d`, CLI, `economy` profile, no red-team) was
created from an Estonian question and came back with an English final
synthesis. `--diagnose-run` showed `output_language=en`,
`working_language=en`, every stage `finish_reason=completed` -- not a
model failure or a language-contract mismatch (ADR 008's working-language
machinery), since there was no mismatch to detect: the run's own
`language` field was `"en"`, and the model correctly wrote in English
because that is what it was told to do.

Root cause: `cli.py` never exposed an output-language choice at all.
`service.create_run()`'s `language` parameter defaults to `"en"`, and
`cli.py`'s call to it never passed one. The web form already has a
language dropdown (`Form(DEFAULT_LANGUAGE)` in `submit_run`); the CLI
simply never grew the equivalent flag.

## Decision

- New `--language et|en` CLI flag. Resolution order: `--language` (if
  passed) > `LLM_LANGUAGE` env var (if set) > `"en"`. Implemented with
  `default=None` at the argparse level specifically so `_run()` can tell
  "the flag was genuinely omitted" apart from "it resolved to `en`" --
  both look identical once resolved, but only the first case is eligible
  for the refusal check below.
- **New refusal check, local and free (no network, no paid call):** when
  `--language` was not passed AND the *resolved* language is `"en"` AND
  `language_detect.detect_language(question)` confidently returns `"et"`,
  the run is refused (exit 2) with a message naming both ways to proceed
  (`--language et` or `--language en`), instead of silently defaulting to
  English for a question that reads as confidently Estonian. Reuses the
  existing, already-conservative `detect_language()` heuristic (ADR-less,
  but documented in `language_detect.py`) used for the working-language
  contract check -- no new detection logic, no new dependency.
- The check is skipped whenever `LLM_LANGUAGE=et` is already configured:
  the *resolved* language is what's compared against the detected one,
  not literally whether the flag was typed. If the standing environment
  default is already Estonian, there is no "silently defaulting to
  English" happening, so nothing to refuse.
- An explicit choice -- `--language en`, even for a confidently-Estonian
  question -- is never second-guessed. The feature's job is to stop a
  *silent* default, not to override a decision the user actually made.

## Why

- **Why compare against the resolved language, not literally "was
  --language typed":** the request's own wording ("if --language is not
  given") is satisfied by the common case (nothing configured at all,
  hardcoded `en` fallback), but a literal "any time the flag wasn't typed"
  reading would also refuse a user who has `LLM_LANGUAGE=et` permanently
  set in their shell profile -- forcing them to retype `--language et` on
  every single Estonian run, defeating the entire point of having an
  env-var default. Comparing against the resolved value fixes the actual
  failure mode (silently ending up with English you never chose) without
  punishing the user who already chose Estonian, just not on this one
  command line.
- **Why reuse `detect_language()` instead of a lighter/different
  heuristic:** it already exists, is already tuned specifically to prefer
  "uncertain" over a false positive (same project, same risk), and reusing
  it means this feature inherits that caution for free rather than
  re-deriving a separate, possibly less careful threshold.
- **Why not try to catch the original real-world question
  (`"Millised on parimad võimalused eesti riigieelarve tasakaalu
  saamiseks"`):** at 8 words, `detect_language()` returns "uncertain"
  (its `MIN_WORDS_FOR_CONFIDENCE` is 25) -- this feature does not cover
  it. Lowering the threshold specifically for this one call site would
  diverge from the deliberately-tuned, shared threshold for no good
  reason and risk false positives on short English questions too; the
  honest answer is that a confident local heuristic cannot help on text
  this short, and the user still needs to know to pass `--language et`
  for a short Estonian question -- this ADR does not claim otherwise.

## Trade-offs

- A genuinely short Estonian question (common in practice) is not caught
  -- see above. The flag still fixes the *configuration gap* (there is
  now a way to ask for Estonian output at all); the refusal is a narrower,
  best-effort safety net on top of that, not a substitute for it.
- One more local computation (`detect_language`) on every CLI run that
  doesn't pass `--language` -- negligible cost (pure Python, no I/O), but
  it does mean a sufficiently long, confidently-Estonian question submitted
  purely by accident (e.g. pasted into the wrong terminal) now stops the
  run rather than proceeding -- the intended behavior, but worth knowing
  it is a new stopping point that did not exist before.

## Failure modes

- A question that mixes Estonian and English, or is Estonian but short,
  can still silently default to English -- `detect_language()`'s
  "uncertain" path is unchanged and deliberately permissive (see its own
  module docstring: a false "mismatched" is worse than a missed case).
- `LLM_LANGUAGE` set to an unsupported value (anything other than `en`/
  `et`) is not validated at the CLI layer at all -- it falls through to
  `service.create_run()`'s existing `ValueError` ("Unknown language..."),
  surfaced the same way any other `create_run` misconfiguration already
  is. No special-casing added for this, since that existing path already
  produces a clear, actionable error.

## How we plan to evaluate it

Not yet measured against further live runs. The next Estonian-question
CLI run (long enough to trigger the check) is the natural test: does the
refusal fire, and does `--language et` then produce the Estonian output
the original incident was missing.

## Test plan (all offline, every provider call faked)

`tests/test_cli_language.py`: `--language et`/`--language en` both thread
through to the created run; `LLM_LANGUAGE` is used when the flag is
omitted; an explicit flag beats the env var; the real argparse parser
rejects an unsupported `--language` value; the status line prints the
resolved language; a long, confidently-Estonian question with no flag and
no env var is refused (exit 2, no run created, zero provider calls), with
a message naming both `--language et` and `--language en`; the refusal
does not fire when the flag is explicit (either value), when
`LLM_LANGUAGE=et` is already set, for an English question, or for the
real-world short Estonian question that originally motivated this (the
test explicitly asserts `detect_language()` itself returns "uncertain"
for it, confirming the documented limitation rather than just asserting
the CLI's behavior in isolation).
