# ADR 009: Question framing (Stage 0) is a pre-run phase, not a pipeline wave

Status: **Approved and implemented (Phase 2).** CLI only; the web wizard is
a deferred phase (see "Deferred: the web wizard" below). Off by default
everywhere; excluded from the evaluation harness.

## Context

The pipeline today starts from a question the user has already written.
`run.question`/`run.context` are captured once in `service.create_run()`
and never revised afterward (see ADR 003). Nothing upstream of
`analysis_a`/`analysis_b` ever questions whether the question itself is
well-posed.

Hypothesis, **untested**: a better-framed question makes the deliberation
more useful, especially when the initial question is vague. Pilot Case 1's
ceiling effect may partly reflect an already well-posed question. Nothing
in this ADR treats that hypothesis as established; it motivates building an
optional capability to test it, not a claim that the capability works.

The original design brief proposed five sub-steps (0a problem type, 0b an
8-question adaptive interview, 0c framing critique, 0d premortem, 0e lock)
run before the existing pipeline. The Phase 1 draft of this ADR estimated
that shape at up to ~13 extra paid calls per run. On review, that was
judged too expensive for an unproven hypothesis, so the call shape below
was cut to an MVP of **3 calls total** before Phase 2 implementation began.
Constraints set in advance and preserved through that cut: Stage 0 is
optional and off by default; it must never touch Case 3 or be reachable
from the evaluation harness until explicitly enabled there later; its
calls must count against the run's cost budget and completion reserve
exactly like every other stage's.

## Decision

**Stage 0 is a distinct pre-run phase that runs against an
already-created, still-`pending` run, before `start_run()` is ever called
— not a new wave prepended to `orchestrator.WAVES`.**

### Call shape: 3 calls, not 13

1. **Problem type + interview, combined (1 call).** One model call returns
   both the Cynefin classification (simple/complicated/complex/unclear,
   with a reason — advisory only, never changes the profile or skips a
   stage) **and** the full set of 5–8 interview questions in one response
   (`question_framing.ProblemFraming`). The user answers all of them at
   once (CLI: one `input()` per question in sequence, no model call
   between them).
2. **Candidate A framing analysis (1 call).** Framing critique (hidden
   assumptions, 2–3 reframings, whether this is the right problem at all)
   **and** premortem (the 3 most likely reasons the decision looks wrong a
   year from now), combined into one `question_framing.FramingAnalysis`
   call, informed by the user's interview answers.
3. **Candidate B framing analysis (1 call).** Same combined shape as (2),
   run independently — same "A and B never see each other's output" rule
   as ADR 001. (2) and (3) run concurrently (`asyncio.gather`).

**Trade-off accepted:** the interview no longer adapts question-by-question
to the user's answers — all 5–8 questions are generated up front from the
question/context alone, before any answer exists. An adaptive, one-
question-at-a-time interview (the original 0b design) is a later option if
the 3-call MVP shows the hypothesis is worth the extra cost; it is not
implemented here.

### Architecture

1. `service.create_run()` gains an opt-in `question_framing_enabled: bool =
   False` parameter (CLI flag `--question-framing`, default off). When
   enabled, it additionally inserts one row into a new `stage0` table
   (additive, `CREATE TABLE IF NOT EXISTS` — no column-migration machinery
   needed, since it is a brand-new table). The run row itself is otherwise
   unchanged: same `question`, `context`, `profile`, budget cap.
2. The run sits in `pending` until the CLI's interactive flow locks Stage 0
   (or the flag was never passed, in which case `start_run()` follows
   immediately, exactly as before). No new run-status value is introduced.
3. Two new `DeliberationOrchestrator` methods (`run_question_framing`,
   `run_framing_analysis`) run the 3 calls above, reusing the existing two
   primary providers (`self.a`/OpenAI for the problem-type+interview call;
   `self.a`/`self.b` for the two independent framing-analysis calls — no
   third provider needed). Each call is validated against its own small
   Pydantic schema (`question_framing.py`, mirroring `convergence.py`'s
   `model_json_schema()`-in-prompt / `parse_*` / typed-`ParseError`-with-
   bounded-excerpt pattern exactly). A budget-guarded call helper
   (`_call_budgeted`) checks the shared guard before sending and records
   actual cost after — simpler than `run_stage`'s machinery: no
   truncation/language-contract recovery (see Trade-offs).
4. **Each call's result is persisted to the `stage0` row as soon as it
   completes** (incremental `UPDATE`s, not one write at Lock) — this is the
   mechanism that makes Stage 0's real spend visible to the run's budget
   guard and completion reserve the whole way through, and makes a
   failure's sunk cost recoverable rather than dropped.
5. `stage0.locked_at` is the one commit marker. Setting it (the CLI's final
   prompt) also writes `final_question` and `success_criteria`. "Skip
   Stage 0" is a degenerate lock (`skipped=True`, `final_question =
   run.question`, `locked_at = now()`), so downstream logic never branches
   on skipped vs. completed — only on whether `locked_at` is set and what
   `final_question` is.
6. `service._execute()`'s `effective_question` assembly gains one
   additional check: if a locked `stage0` row exists, use
   `stage0.final_question` instead of `run.question`. **`run.question`
   itself is never mutated** — both the original and the locked question
   are always available: `run.question` (original, immutable, as always)
   and `stage0.final_question` (locked). The CLI, the Markdown export, the
   web report route, and `run_detail.html` all show both when a lock
   exists, so the reframing stays traceable rather than silently replacing
   the record of what the user originally asked.
7. The locked success criterion (`stage0.success_criteria`) is shown in
   "You Decide" (`web/templates/partials/result.html` and `report.html`),
   so a viewer can check the actual result against the criterion that was
   set *before* the deliberation ran — never used by any code to score or
   grade the result; purely a human-readable reminder.
8. Budget/cost: Stage 0 calls are checked against a `RunBudgetGuard`
   constructed from `sum_stage0_cost(run_id)`; `_execute`'s own guard
   construction now sums `sum_stage_costs(run_id) + sum_stage0_cost(run_id)`,
   so Stage 0 and the main pipeline share one budget pool, never two
   separate caps. A failed Stage 0 call's already-incurred cost is still
   recorded (`record_stage0_cost`), never dropped.
9. Evaluation harness / Case 3: `evaluation/variants.py` calls
   `service.create_run(...)`/`service.start_run(...)` directly and never
   passes `question_framing_enabled=True`. No change was made to the
   harness, to `cases.py`, or to Case 3's data/mapping/variants. See "A
   future FULL vs. FULL+Stage0 comparison" below for how this could change
   later, by explicit decision only.

### CLI flow (Phase 2 — the only implemented entry point)

`cli.py`'s `--question-framing` flag drives an interactive loop between
`create_run()` and `start_run()`, reusing the same `sys.stdin.isatty()`
gating convention as the existing "Start without red-team?" prompt (a
non-interactive/piped invocation gets empty answers and keeps the original
question rather than hanging on a read that cannot succeed):

```
create_run(question_framing_enabled=True)
  -> run_question_framing(run_id)       # call 1: classification + questions
  -> [user answers each question]       # no model call
  -> record_interview_answers(run_id, answers)
  -> run_framing_analysis(run_id, "a")  # call 2
  -> run_framing_analysis(run_id, "b")  # call 3, concurrent with call 2
  -> [user writes/keeps final question + optional success criterion]
  -> lock_question_framing(run_id, final_question=..., success_criteria=...)
start_run(run_id)
```

A failure at any Stage 0 call raises `ProviderGenerationError`; the CLI
prints it, leaves the run created-but-not-started, and exits non-zero.
There is no retry-Stage-0 entry point in Phase 2 — the user re-runs the
command (a reasonable scope cut: Stage 0 is a single, short, interactive
sequence, not a long-running pipeline with per-stage retry UI).

### Deferred: the web wizard

**No web UI exists for creating or running Stage 0 in Phase 2.** The
`index.html` submission form has no Stage 0 toggle, and
`question_framing_enabled` can only be set via the CLI flag. This was an
explicit scope cut for Phase 2, not a technical limitation of the design:
the Decision above (an already-created, still-`pending` run; incremental
persistence keyed by `run_id`; no new run-status value) was deliberately
chosen so a future web wizard can be added later without revisiting any of
it. A plausible later shape: `POST /runs` redirects to
`/runs/{run_id}/framing` instead of `/runs/{run_id}` when Stage 0 is
requested, with `start_run()` deferred until a final "Lock" POST — exactly
the CLI's own `create_run → ... → lock → start_run` sequence, just driven
by page loads instead of `input()` calls.

**A CLI-created, Stage0-locked run is still fully viewable on the web**:
`run_detail.html` shows the locked question (when not skipped) alongside
the original, the shareable report (`/runs/{run_id}/report`) shows both
questions and the success criterion, and "You Decide" shows the locked
success criterion. Only the *creation*/interview flow is CLI-only; display
of an already-locked result required no wizard and was implemented in
Phase 2.

## Why

- **Why not a wave-0 stage set, inside the existing `_execute` loop:**
  every stage today is a single atomic request/response round trip, and
  there is no "awaiting user input" run status anywhere in the codebase.
  Running Stage 0 before `start_run()` against an already-created run
  needed none of that machinery to change.
- **Why `run.question` stays immutable and `effective_question` grows a
  second source instead:** this project's own established principle,
  repeated across ADR 003/006/008, is that a run's original stored input is
  never silently rewritten. Showing both the original and the locked
  question everywhere (CLI, report, web) keeps the reframing traceable
  instead of making it look like the user's own words were edited.
- **Why cut to 3 calls instead of ~13:** the hypothesis this whole feature
  exists to test is explicitly unvalidated. Spending up to 13 extra paid
  calls to test an unvalidated hypothesis inverts the right order of
  operations — the MVP's job is to find out cheaply whether framing help is
  even worth the cost of a fuller, adaptive version.
- **Why CLI-only for Phase 2:** the web wizard is real, non-trivial work
  (a new multi-step flow, a new redirect branch in `submit_run`, new
  templates) that provides no additional signal about the hypothesis
  itself. Validating the capability's usefulness doesn't require it to be
  reachable from every surface on day one.
- **Why incremental persistence instead of all-or-nothing at Lock:** a real
  paid call that happens before Lock still happened. If nothing is written
  until Lock, a failed or abandoned Stage 0 silently drops that spend from
  the run's recorded total — the same "never drop already-incurred cost"
  principle `mark_stage_succeeded`/`mark_stage_failed` already apply to
  retries.
- **Why advisory-only for 0a:** encoding problem-type as a free-text note
  next to the result, never as a branch in `_execute`, keeps "simple
  problems might not need this" a suggestion a human reads rather than an
  automated shortcut this pipeline is not validated to take.

## Schema (implemented)

New table (`store.SCHEMA`, additive):

```sql
CREATE TABLE IF NOT EXISTS stage0 (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    skipped INTEGER NOT NULL DEFAULT 0,
    problem_type_json TEXT,          -- {classification, reason, questions[]}
    interview_json TEXT,             -- [{question, answer}, ...]
    framing_analysis_a_json TEXT,    -- {hidden_assumptions[], reframings[], is_right_problem, right_problem_note, premortem_reasons[]}
    framing_analysis_b_json TEXT,    -- same shape, candidate B
    final_question TEXT,
    success_criteria TEXT,
    locked_at TEXT,
    estimated_cost_usd REAL NOT NULL DEFAULT 0.0
);
```

`question_framing.py` (new module, mirrors `convergence.py`'s
provider-independent boundary): `ProblemFraming` (`classification`,
`reason`, `questions: list[str]`) and `FramingAnalysis`
(`hidden_assumptions`, `reframings`, `is_right_problem`,
`right_problem_note`, `premortem_reasons`), each with `parse_*` functions
raising `QuestionFramingParseError` (bounded raw-text excerpt, same
template as `ConvergenceParseError`), plus `canonical_json`/
`*_schema_for_prompt` helpers.

`store.RunRecord` gained `stage0: Stage0Record | None = None`, populated by
`get_run_full()` (one more query) — `get_run()` stays the lighter fetch,
matching the existing `stages` convention. `types.RunResult` gained
`locked_question`, `success_criteria`, `question_framing_skipped` for the
Markdown export.

## Files changed (Phase 2)

- `src/llm_deliberation/question_framing.py` — new module.
- `src/llm_deliberation/store.py` — `stage0` table; `Stage0Record`;
  `RunRecord.stage0`; `insert_stage0`, `get_stage0`,
  `update_stage0_problem_framing`, `update_stage0_interview`,
  `update_stage0_framing_analysis`, `record_stage0_cost`, `lock_stage0`,
  `sum_stage0_cost`; `get_run_full` populates `stage0`.
- `src/llm_deliberation/prompts.py` — `problem_framing_and_interview`,
  `framing_critique_and_premortem`.
- `src/llm_deliberation/orchestrator.py` — `_call_budgeted`,
  `_finalize_question_framing_response`,
  `DeliberationOrchestrator.run_question_framing`/`run_framing_analysis`.
  No change to `WAVES`.
- `src/llm_deliberation/service.py` — `create_run(question_framing_enabled=...)`;
  `run_question_framing`, `record_interview_answers`,
  `run_framing_analysis`, `lock_question_framing`,
  `_stage0_orchestrator`/`_stage0_budget_guard`/`_require_stage0`;
  `_execute`'s `effective_question` prefers a locked `stage0`; its budget
  guard includes `sum_stage0_cost`; `to_run_result` populates
  `locked_question`/`success_criteria`/`question_framing_skipped`.
- `src/llm_deliberation/cli.py` — `--question-framing` flag;
  `_run_question_framing_cli`/`_print_framing_analysis`.
- `src/llm_deliberation/types.py` — `RunResult` gains the three Stage 0
  fields above.
- `src/llm_deliberation/report.py` — Markdown export shows the locked
  question (or a skipped note) and the success criterion.
- `src/llm_deliberation/web/templates/run_detail.html` — shows the locked
  question (when not skipped) alongside the original.
- `src/llm_deliberation/web/templates/report.html` — "Question framing
  (Stage 0)" section (both questions/skipped note); success criterion in
  the report's own "You Decide" section.
- `src/llm_deliberation/web/templates/partials/result.html` — success
  criterion shown next to "You Decide".
- `src/llm_deliberation/web/i18n.py` — new EN/ET labels for all of the
  above.
- `src/llm_deliberation/web/static/style.css` — `.locked-success-criterion`
  styling (reuses the existing `.human-judgement`-family box).
- **Not touched:** `evaluation/*.py`, any Case 3 data/fixtures/mapping,
  `web/app.py` (no new routes — Phase 2 is CLI-only), `web/templates/index.html`
  (no Stage 0 toggle yet).
- **Tests (offline only, every provider call faked):**
  `tests/test_question_framing.py`, `tests/test_store_stage0.py`,
  `tests/test_service_stage0.py`, `tests/test_cli_stage0.py`,
  `tests/test_report_markdown.py`, `tests/test_web_stage0.py`,
  `tests/test_evaluation_stage0_exclusion.py`; `tests/conftest.py`'s
  `FakeOrchestrator` gained `run_question_framing`/`run_framing_analysis`
  fakes and `questions` call-logging so a test can assert which question
  (original vs. locked) a run actually used.

## Trade-offs

- The interview no longer adapts to answers (see "Call shape" above) — a
  real reduction in what Stage 0 can do, accepted to keep the MVP's cost
  to 3 calls instead of up to 13.
- Stage 0 has no truncation-recovery or language-contract-recovery of its
  own (unlike `run_stage`'s main-pipeline machinery) — a failed call simply
  raises and the user re-runs the CLI command. Reasonable for a short,
  interactive, 3-call sequence; would need revisiting if Stage 0 ever grows
  longer or non-interactive (e.g. the deferred web wizard, at scale).
- Persisting incrementally (vs. only at Lock) means an abandoned,
  never-locked Stage 0 leaves a permanent "in progress forever" row.
  Acceptable: it costs nothing to leave around, and `locked_at IS NULL` is
  an honest, correct description of what happened.
- Reusing the same two primary providers for the two independent framing-
  analysis calls means they are not independent of the model families that
  later run `analysis_a`/`analysis_b` — the same trade-off ADR 006 already
  accepted for `convergence_analysis` defaulting to Anthropic.
- CLI-only means Stage 0 is not yet reachable by anyone who only uses the
  web UI — an accepted, explicit scope cut (see "Deferred: the web
  wizard").

## Failure modes

- A reframing a user adopts could be worse than their original question
  (the hypothesis is explicitly unvalidated) — this is why reframings are
  always presented as options, never a recommendation, and the original
  question is always visible and always one of the choices at Lock.
- 0a's advisory note could be misread as a recommendation to skip stages
  even though the code never acts on it — the CLI's own output says
  "advisory only" directly next to it.
- A long interview (slow answers, or a model that always asks the full 8
  questions) still measurably delays getting to an answer, even cut to one
  combined call for the questions themselves — the user-facing anchoring/
  run-length risk the original design brief called out.
- Structured-output parse failures in any of the 3 calls fail that call
  the same way a main-pipeline stage fails (retryable via re-running the
  CLI command, bounded raw-text excerpt in the error) — never silently
  coerced into a fabricated classification or critique.

## A future FULL vs. FULL+Stage0 comparison

Not implemented, and deliberately out of scope for Phase 2 (per explicit
instruction: Stage 0 stays out of the evaluation harness for now). If and
when the user decides to measure the hypothesis, the natural shape is a
new evaluation variant alongside `SINGLE`/`DUAL`/`CRITIQUE`/`FULL` in
`evaluation/variants.py` — e.g. `FULL_STAGE0` — that calls
`service.create_run(..., question_framing_enabled=True)`, drives the same
3-call flow this ADR implements (non-interactively: a fixed or scripted
set of interview answers rather than real `input()`, since the evaluation
harness never prompts a human), locks automatically, then calls
`start_run()` exactly like `FULL` does today. A blind review would compare
`FULL` and `FULL_STAGE0` outputs for the *same* underlying question —
never retrofitted onto Case 3's existing locked data/mapping, which stays
untouched regardless of when or whether this comparison is built. The
comparison's whole point is the hypothesis stated in Context: does a
Stage-0-framed question produce a deliberation blind reviewers rate as
more useful than the same original question without framing. Until that
work happens, this ADR's claims about Stage 0's value remain exactly what
Context says they are — an untested hypothesis, not a result.
