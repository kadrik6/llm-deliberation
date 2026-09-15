# ADR 007: UI language and run/output language are separate, persisted settings

## Context

The application's interface and its model-generated content were both
English-only. Adding a second language (Estonian) raises an immediate
design question: is "language" one setting, or several? A naive
implementation could tie a run's content language to whatever language
the browser happened to be showing at submission time -- transient,
never persisted, and impossible to reconstruct correctly on retry,
resume, or when reopening the run later from a different browser/device.

## Decision

Two independent settings, never conflated:

1. **UI language** (`web/i18n.py`) -- which language interface chrome
   ("New", "Final answer", "What changed?", ...) renders in. A plain
   cookie (`ui_lang`), read per-request, set via a `GET /ui-language/{lang}`
   redirect (no JS, no account, no server-side storage). It affects
   nothing but template rendering.
2. **Run/output language** (`store.RunRecord.language`, `en` | `et`) --
   the language every user-facing generated stage of one specific run is
   written in. Chosen once, when the run is created (defaulting to the
   submitter's current UI language, but independently overridable), and
   persisted as a column on the `runs` row. It never changes after
   creation and is read back on every retry/resume so a multi-step run
   stays internally consistent even if the browser's UI language changes
   mid-run.

**How the instruction reaches the model:** `prompts.output_language_instruction(language)`
is one shared instruction, injected into `prompts.base_system(language)` --
the system prompt every stage already used, now parameterized by one
argument. No individual stage prompt (`independent_analysis`, `critique`,
`red_team`, `revision`, `convergence_analysis`, `synthesis`) was touched;
the language directive is added once, centrally, exactly where the prior
`BASE_SYSTEM` constant used to be a constant.

**The question is never translated.** `orchestrator._build_prompt` passes
the user's original question straight into every stage prompt, completely
independent of `language`. A Estonian question with English output (or
the reverse) is a deliberate, supported combination, not a special case
requiring extra code -- it falls out naturally from the question and the
output-language instruction being two unrelated prompt inputs.

**Schema/enum stability.** The output-language instruction explicitly
tells the model to keep JSON keys, schema field names, enum values, and
stage identifiers unchanged regardless of language -- the same
`ConvergenceAnalysis` schema (`convergence: "converged" | "partial" |
"diverged" | "insufficient_information"`) validates identically whether
the human-readable string values inside it are English or Estonian
prose. Display-time translation of the enum into a human-readable badge
label (`web/i18n.py`'s `convergence_partial` key, `report.py`'s parallel
label for the Markdown export) is a presentation concern, layered on top
of an always-English, always-validated stored value -- never a rewrite of
the value itself.

**Historical runs.** `runs.language` is added via the same in-place
`ALTER TABLE` migration pattern already used for the `stages` table's
fallback-provenance columns (see ADR 005), defaulting every pre-existing
row to `"en"` -- a stated default, never inferred from a run's stored
text. Their content is never touched.

## Why

- **Why not derive run language from UI language transiently:** a
  multi-stage, retryable, resumable run can span more than one browser
  session (a different device, days later, a different UI language
  preference by then). Only a persisted, explicit run-level field
  survives that; a cookie cannot.
- **Why not auto-detect the question's language:** detection is
  probabilistic and the brief this was built against explicitly rules it
  out -- an explicit choice is auditable ("why is this run in English"
  has one answer: someone chose it), a detected one is not.
- **Why one shared prompt instruction instead of per-stage wording:** six
  near-identical "write your answer in X" strings scattered across
  `prompts.py` would drift out of sync over time (a future edit to one
  stage's wording, forgotten in the other five). One function, one
  place, used by every stage identically, cannot drift.

## Trade-offs

- Only two languages are supported. Adding a third means extending
  `prompts.SUPPORTED_LANGUAGES`, `web/i18n.py`'s dictionaries, and
  `report.py`'s local label dict -- three places, by design kept small
  and explicit rather than backed by a heavier i18n framework this
  project doesn't otherwise need.
- `web/i18n.py` and `report.py`'s label dictionaries duplicate some
  concepts (e.g. "Final synthesis" / "Lõppsüntees" exists in both).
  Deliberate: `report.py` (and the CLI that calls it) has no dependency
  on the web/FastAPI/Jinja stack, and merging the two would either break
  that independence or force `web/i18n.py` to serve two very different
  callers.
- Several pipeline-view row labels (provider names mixed with generic
  terms like "Candidate A" in the same row) are left untranslated in this
  iteration rather than partially translating a mixed list inconsistently
  -- a disclosed scope boundary, not an oversight.
- Nothing stops a model from partially ignoring the output-language
  instruction on a given call (using this project's normal failure mode:
  a probabilistic instruction, not a hard constraint). No enforcement or
  retry-on-wrong-language exists.

## Failure modes

- A model could respond in the wrong language despite the instruction --
  most likely for a short or ambiguous question where the model leans on
  the question's own language instead. Not currently detected or
  retried automatically.
- Estonian grammatical number (the Decision Snapshot's counts) uses a
  fixed singular/plural noun pair per count category
  (`web/i18n.py:_COUNT_NOUNS`), not full partitive-case declension --
  correct for the common cases, not exhaustively verified against every
  Estonian numeral-agreement rule.

## How we plan to evaluate it

Not yet run. Would fit alongside the existing evaluation plan
(`evals/README.md`): rate of responses that actually stay in the
requested output language, human review of Estonian output quality
(distinct from "did it validate" -- the schema validates regardless of
language quality), and whether an English-question/Estonian-output (or
reverse) run produces a final answer a bilingual reviewer would rate as
equivalent in substance to the same run in a single language.
