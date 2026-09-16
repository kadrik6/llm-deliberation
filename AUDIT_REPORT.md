# LLM Deliberation — Release-Candidate Reliability Audit

Status: **J.1-J.5 implemented and offline-verified (2026-09-16). Not
committed. No live/paid API calls made yet — see the Implementation Status
and Revised Canary Plan addenda at the end of this file.**

Original investigation below is unchanged from the initial (pre-fix) pass.
All findings below come from
fresh reads of the current source (`orchestrator.py`, `service.py`,
`providers.py`, `store.py`, `config.py`, `cost_budget.py`, `readiness.py`,
`convergence.py`, `web/app.py`, `pricing.py`) and live, no-network
introspection of the installed SDKs — not from memory of earlier sessions in
this conversation.

---

## A. Current exact architecture

**Stage graph** (`orchestrator.WAVES`), 9 stages / 5 concurrency waves:

```
(analysis_a, analysis_b)
  -> (critique_a_of_b, critique_b_of_a, red_team)
  -> (revision_a, revision_b)
  -> (convergence_analysis,)
  -> (synthesis,)
```

`analysis_a/critique_a_of_b/revision_a/synthesis` = OpenAI. `analysis_b/
critique_b_of_a/revision_b` = Anthropic. `red_team` = Gemini
(`GeminiFallbackProvider`, optional). `convergence_analysis` = provider
chosen by `CONVERGENCE_PROVIDER` (default Anthropic, with native structured
output).

**Execution model.** `DeliberationService._execute()` (`service.py:276`) is
the single seam every entry point (start/resume/retry/skip) funnels through.
Per wave: classify each stage as done/skipped/pending → run a budget
admission pass (`RunBudgetGuard.check`) that can fail individual stages with
`run_budget_exceeded` without blocking siblings in the same wave → call
`repo.mark_stage_running()` synchronously for every admitted stage → dispatch
all admitted stages via a single `asyncio.gather(...)` → process results
sequentially back in the main coroutine (`mark_stage_succeeded` /
`mark_stage_failed`, budget resync). Only `orchestrator.run_stage` (an
`async def`) is inside `gather`; the actual blocking SDK call is pushed to a
real OS thread via `asyncio.to_thread` in `orchestrator._call()`
(`orchestrator.py:105-108`). **Verified: all DB writes happen sequentially in
the single main coroutine, never concurrently from worker threads** — no
SQLite race there.

**Persistence.** SQLite (`store.py`), one shared `sqlite3.connect(...,
check_same_thread=False)` connection, `runs` / `stages` / `artifacts` tables,
additive `ALTER TABLE` migrations. `estimated_cost_usd` on `stages`
*accumulates* across attempts (never overwritten), so sunk cost from a failed
retry is never dropped from the run total.

**Web layer** (`web/app.py`): 13 routes. Double-execution guard is an
**in-process, in-memory** `app.state.running: set[str]` via `reserve()` /
`release()` (`web/app.py:229-262`) — see finding D.2.

---

## B. Current SDK versions (reconfirmed this session)

`openai==3.13.0`, `anthropic==1.5.0`, `google-genai==2.23.0`,
`fastapi==0.141.1`, `pydantic==2.13.5`.

---

## C. Provider contract mismatches

| Provider | Contract point | Status |
|---|---|---|
| OpenAI | `responses.create(...)` kwargs, `Response.status`, `incomplete_details.reason`, `usage.input_tokens/output_tokens`, `.output_text` | **Verified correct**, empirically re-checked against installed SDK, no mismatch. |
| Anthropic | `messages.create(..., output_config={"effort", "format"})`, `message.content[].type=="text"`, `message.usage`, `message.stop_reason` | Matches code as read. `anthropic.transform_schema` usage for structured output confirmed pure/local (Task 5, not re-verified live again this pass). |
| Gemini generation | `interactions.create(...)` exception hierarchy (`google.genai._gaos.lib.compat_errors`) | Matches code (Task 4 fix), consistent with this session's read. |
| Gemini readiness | `models.get(...)` exception hierarchy (`google.genai.errors`) | Matches code, correctly distinct from the generation hierarchy above. |
| **Gemini API key resolution** | **MISMATCH — see below.** | **HIGH finding.** |

### HIGH: Gemini credential used for readiness ≠ credential used for real calls

- `readiness.check_gemini_readiness()` and `config.Settings.validate_keys()`
  both read **only** `os.getenv("GEMINI_API_KEY")` explicitly
  (`readiness.py:257`, `config.py:197`).
- `providers.GeminiProvider.generate()` constructs `genai.Client(http_options=
  {...})` **without an explicit `api_key=`** (`providers.py:275-281`), so the
  SDK resolves the credential itself via `get_env_api_key()`, which checks
  `GOOGLE_API_KEY` **first**, falling back to `GEMINI_API_KEY` only if unset
  (confirmed by direct SDK introspection).
- Consequence: if an operator's environment has both `GOOGLE_API_KEY` (e.g.
  left over from an unrelated Google Cloud/SDK setup) and `GEMINI_API_KEY`
  set to **different** values/projects, the readiness preflight validates
  `GEMINI_API_KEY` and reports "ready" — but the real, paid `red_team`/
  `convergence_analysis` call silently authenticates with `GOOGLE_API_KEY`
  instead. This is exactly the kind of gap that produces "readiness said
  ready, the real run still failed at a provider boundary" symptoms, and it
  is a **false negative risk too**: an operator with only `GOOGLE_API_KEY`
  set gets incorrectly blocked as "not configured" even though the SDK would
  have worked.
- Fix is small and targeted (see J.1).

---

## D. Retry/resume/skip risks

1. **Crash mid-stage → possible double-pay on resume (Medium/High, inherent).**
   If the process dies while a stage is `status='running'` (after
   `mark_stage_running`, before `mark_stage_succeeded`/`mark_stage_failed`),
   the row is orphaned in `running` forever. `resume_run()` treats any
   non-`succeeded`/non-`skipped` stage — `running` included — as pending
   (`service.py:330-340`), so the next resume issues a **new** paid call for
   it with no way to know whether the original call actually completed on
   the provider's side first. There is no idempotency key sent to any
   provider. This is a genuine, currently-unavoidable at-least-once cost
   risk without a much larger architecture change; the pragmatic mitigation
   is disclosure, not elimination (see J.3).

2. **Double-execution guard is process-local only (Medium, deployment
   constraint, not a bug).** `app.state.running` is a plain in-memory
   `set`. Correct and sufficient for a single-process `uvicorn` deployment
   (this app's actual target). Under a multi-worker deployment
   (`--workers N>1`, or multiple processes behind a load balancer) each
   worker has its own empty set, so two concurrent Resume/Retry clicks
   routed to different workers would **both** pass `reserve()` and execute
   `_execute()` concurrently against the same SQLite file from two
   processes. Not exercised by any test (all tests run in-process). This is
   a deployment constraint that should be **documented**, not re-engineered,
   for this project's scale (see J.5).

3. **`mark_stage_running` does not clear `failure_reason`** (`store.py:392-398`).
   Cosmetically stale during the brief `running` window; confirmed harmless
   — `pipeline.html` only reads `failure_reason` inside the `status ==
   'failed'` branch, never `running`, and a subsequent success/failure always
   overwrites it. No fix needed.

4. **retry_stage / reset_stage vs. an in-flight run** — protected correctly:
   `schedule()` closes the coroutine unstarted if the run is already
   reserved, so `reset_stage()` can never run concurrently with `_execute()`
   for the same run. Verified, no issue.

5. **Budget-blocked stage retry UX is already correct**: the per-stage Retry
   button is deliberately suppressed for `run_budget_exceeded`
   (`pipeline.html:106-116`) in favor of run-level "Resume run", and
   `resume_run()`'s uniform pending-stage handling re-admits it correctly
   once the stored budget is raised. Verified via code trace, no issue.

---

## E. Persistence risks

- SQLite opened with `check_same_thread=False`, one shared connection across
  `asyncio.to_thread` workers — safe as used today because all writes are
  serialized in the single main coroutine (see A). If that ever changes
  (e.g. a future move of `mark_stage_*` calls into the worker threads
  themselves), this would become a real race; worth a one-line comment
  guarding against that regression.
- **No `PRAGMA busy_timeout` is set.** Under any concurrent external access
  to the same DB file (a second process, a `sqlite3` CLI session, a backup
  tool with a held lock), the connection fails immediately with `database is
  locked` rather than waiting briefly. Cheap, safe fix (J.4).
- `estimated_cost_usd` accumulation semantics (never overwritten, always
  added to) are correct and already well-documented in `store.py`'s own
  comments — re-verified, no issue.
- Migrations are additive-only (`_STAGE_MIGRATION_COLUMNS` /
  `_RUN_MIGRATION_COLUMNS`), never rewrite historical rows — verified, no
  issue.

---

## F. Language risks

`resolve_stage_language()` remains the single centralized policy point;
`effective_working_language = run.working_language or run.language`
(`service.py:306`) correctly avoids assuming "en" for legacy runs. This was
spot-checked against the current code, not re-traced stage-by-stage line by
line in this pass (that was done exhaustively during the working-language
task itself) — no new issues found in the spot check.

---

## G. Cost-accounting risks

- The runtime hard guard's upper-bound estimate
  (`cost_budget.estimate_max_call_cost_usd`) always assumes the **full**
  configured `max_output_tokens` ceiling and a deliberately pessimistic
  chars/token ratio (3, vs. ~4 typical for English) — biased to
  **over-estimate**, never under-count. This can cause a run to be blocked
  slightly earlier than the true cost would require, but never lets real
  spend exceed the guard silently. Correct direction for a hard safety
  guard; re-verified, no issue.
- Decimal/float boundary (`default_budget_from_float`) is a single,
  documented, rounded (6dp) conversion point — re-verified, no issue.

---

## H. Where current tests give false confidence

This is the central concern behind "green offline suite, real runs still
fail at provider boundaries":

1. **No test constructs a real provider SDK client with the app's actual
   kwargs.** Every web/service-level test goes through `FakeOrchestrator`
   (`conftest.py`); provider-classifier tests construct SDK *exceptions*
   directly but never call `OpenAI(...)`, `anthropic.Anthropic(...)`, or
   `genai.Client(...)` for real. A signature drift in a future SDK upgrade
   (a renamed kwarg, a changed response attribute) — **exactly what caused
   the Task 4 and Task 5 incidents** — would pass all ~500 existing tests
   and only surface in a real run. Nothing in the suite would have caught
   either past incident before it happened in production.
2. **The Gemini API-key divergence (C) is untested and untestable by the
   current suite** — `test_readiness.py`'s autouse fixture stubs all three
   SDKs to "ready" by default, and no test ever exercises
   `GeminiProvider.generate()`'s actual client-construction call against
   real env vars.
3. Test suite is large (8,635 lines across 16 files) but not obviously
   redundant at a glance — `test_web.py` (1,828 lines) is the biggest single
   file and covers a wide surface (readiness UX, budget UX, retry/skip
   flows, i18n) rather than one narrow thing repeated; no file looked like
   pure duplication on inspection. Did not do a full per-test redundancy
   pass (out of scope for this report; flagged in section 14 of the brief as
   explicitly not the goal).

---

## I. Release blockers, ranked

**High**
1. Gemini credential mismatch between readiness/validate_keys and the real
   generation call (C). Silent, hard to debug in the field, directly
   produces "readiness said ready, real run failed" symptoms.
2. No SDK-contract smoke coverage — a class of regression (provider SDK
   kwarg/response-shape drift) that has **already caused two real incidents**
   in this project (Tasks 4 and 5) is still undetectable by the offline
   suite today.

**Medium**
3. Crash-mid-stage resume can double-pay for one stage, with no user-facing
   disclosure that this specific resume is a fresh paid attempt rather than
   a safe continuation (D.1).
4. Double-execution guard is process-local; safe only under a documented
   single-worker deployment (D.2) — currently undocumented.
5. No `PRAGMA busy_timeout` (E).

**Low**
6. `mark_stage_running` leaves a stale `failure_reason` during the `running`
   window — confirmed cosmetic, no functional impact (D.3).

No **critical** (run-cannot-complete-at-all) issues were found in this pass.
`orchestrator.py`/`service.py`'s wave loop, budget admission, and
retry/resume/skip state handling all traced correctly against the scenarios
checked.

---

## J. Exact proposed changes (pending your approval before any code is touched)

1. **Fix the Gemini credential mismatch (High #1).** In
   `providers.GeminiProvider.generate()`, pass `api_key=os.getenv(
   "GEMINI_API_KEY")` explicitly to `genai.Client(...)`, matching what
   `readiness.check_gemini_readiness()` already does — removes reliance on
   SDK env-precedence order entirely, so the credential the readiness check
   validated is guaranteed to be the one the real call uses. Single file,
   ~2 lines.
2. **Close the SDK-contract test gap (High #2).** Add
   `tests/release_gate/test_sdk_contracts.py`: for each provider, construct
   the real client with the exact kwargs production code uses (no network
   call — client construction alone doesn't call out), and assert via
   `inspect.signature`/type introspection that the response types
   (`Response`, `Message`, `Interaction`) still expose the exact attributes
   the code reads (`.status`, `.incomplete_details.reason`, `.content[].type`,
   `.stop_reason`, `.usage.input_tokens`, etc.). Runs offline, catches a
   version-upgrade contract break before a real run does.
3. **Disclose stale-`running` resume risk (Medium #3, optional — your
   call).** When `resume_run`/`retry_stage` encounters a stage already in
   `running` status, surface a one-line notice ("this stage was left running
   from a previous session; resuming will make a new paid attempt") rather
   than silently re-attempting. Small, UI + one service-layer check.
4. **Set `PRAGMA busy_timeout = 5000`** in `Repository.__init__` (E). One
   line, no behavior change under normal operation.
5. **Document the single-worker deployment constraint** (D.2) in
   README/docs — no code change.

I have **not** implemented any of these yet. Tell me which of 1–5 you want
done now; my recommendation is 1, 2, and 4 as the minimum before Gate B, with
3 and 5 as good but optional.

---

## K. Live-canary plan (proposed, NOT executed — awaiting your approval)

Both canaries: fresh dedicated SQLite DB (e.g. `data/canary-test.db`, never
the personal run history DB), `profile=economy` (cheapest per-token rates),
`max_output_tokens` overridden down to ~1000 for the canary run only (keeps
worst-case cost tight without touching the reliability logic being tested),
and each run given its own `max_run_cost_usd` hard cap so the app's *own*
budget guard is exercised live as a real safety net, not just trusted.

**CANARY 1 — full pipeline, red-team ON, English.**
9 real paid calls: analysis_a/critique_a_of_b/revision_a/synthesis (OpenAI,
`gpt-5.6-terra`), analysis_b/critique_b_of_a/revision_b/convergence_analysis
(Anthropic, `claude-sonnet-5`, convergence using native structured output),
red_team (Gemini, `gemini-3.8-flash`, 1 attempt expected in the happy path).
Purpose: prove a real deliberation reaches final synthesis end-to-end,
including the fixed Gemini fallback timing and the fixed Anthropic
structured-output convergence path, against real APIs.
**Worst-case cost** (all 9 calls hit the full 1000-token output cap):
≈ $0.09 output + ~20% margin for input tokens ≈ **$0.11–$0.15**.
Proposed `max_run_cost_usd = $0.50`.

**CANARY 2 — red-team OFF, output language Estonian, working language
English.** 8 calls (same as above minus red_team). Purpose: prove the
language-policy boundary (`resolve_stage_language`) live — verbose stages in
English, `convergence_analysis`/`synthesis` in Estonian — and prove a
red-team-disabled run still completes cleanly. Worst-case cost similar,
**≈ $0.10–$0.13**. Proposed `max_run_cost_usd = $0.50`.

**Proposed maximum total spend across both canaries: $1.00**, with each
run's own $0.50 cap enforced live by the app itself as a backstop, not just
by my own estimate.

For each canary I will record: run ID, every stage's provider/model,
input/output tokens, estimated cost, duration, and final status, and the
canary only counts as passed if it reaches `synthesis` successfully (per
your instruction — a partial run is not a pass). If either fails, I will do
root-cause analysis from the persisted stage provenance (`attempt_log`,
`failure_reason`) before proposing any patch — no blind retries.

---

## Next step

This is the full audit report (A–K). **I have not modified any code, and
will not make any live/paid API calls until you explicitly approve the
canary plan in K** (and tell me which of J's proposed changes, if any, to
implement first). Awaiting your decision.

---

## Implementation Status (2026-09-16) — J.1–J.5, all implemented

All five proposed fixes from Section J were implemented, per explicit
approval, with the additional regression coverage requested. **Not
committed, not pushed.** No live/paid API call has been made.

### J.1 — Gemini credential mismatch (High #1) — fixed

`providers.GeminiProvider.generate()` now passes `api_key=os.getenv(
"GEMINI_API_KEY")` explicitly to `genai.Client(...)`, matching
`readiness.check_gemini_readiness()` exactly. The SDK's own `GOOGLE_API_KEY`-
first env auto-detection can no longer be reached for this call.

New: `tests/test_gemini_credential.py` (3 tests) — only `GEMINI_API_KEY` set;
both set to *different* fake values (the central regression — proves
`GOOGLE_API_KEY` cannot silently override); and a same-credential proof
between `check_gemini_readiness()` and `GeminiProvider.generate()`. No
network calls (`google.genai.Client` replaced with a kwarg-capturing fake).

### J.2 — SDK contract release-gate suite (High #2) — added

New `tests/release_gate/` (21 tests) + `tests/release_gate/fixtures/`
(7 sanitized, no-secret JSON fixtures). Covers, per your checklist:

- **OpenAI**: real client construction (fake key, no network); `Responses.
  create` signature checked for `model/instructions/input/reasoning/
  max_output_tokens/store`; `Response.status`/`incomplete_details.reason`
  field presence; three fixture-driven adapter runs (completed, truncated,
  empty) through the real, unmodified `OpenAIProvider.generate()`.
- **Anthropic**: real client construction; `Messages.create` signature
  checked for `model/max_tokens/system/output_config/messages`;
  `OutputConfigParam`/`JSONOutputFormatParam` shape match production's
  `{"effort", "format": {"type": "json_schema", "schema": ...}}`; a live,
  no-network call to the real `anthropic.transform_schema(ConvergenceAnalysis)`
  (pure/local); `stop_reason`/`content`/`usage` field presence; two
  fixture-driven adapter runs (`end_turn`, `max_tokens`); **one full
  end-to-end offline proof** — a fixture-shaped structured-output response
  parsed by `AnthropicProvider.generate()` and the result fed straight into
  the real `convergence.parse_convergence_analysis()`, exercising the exact
  two components chained together in production, unmocked in between.
- **Gemini**: real client construction with explicit `api_key=`; the request
  body's `CreateModelInteractionParamsNonStreaming`/`GenerationConfig` shape
  checked for `model/system_instruction/input/generation_config/store` and
  `thinking_level/max_output_tokens`; two fixture-driven adapter runs
  (completed, incomplete/truncated); the generation exception hierarchy
  (`_gaos.lib.compat_errors`) and the readiness exception hierarchy
  (`google.genai.errors`) each re-verified against real exception instances
  through the real classifiers; **and a new structural assertion that the
  two hierarchies are not the same class** — a future SDK refactor merging
  them would now fail this test loudly instead of silently reintroducing the
  Task 4 bug.

Non-obvious finding surfaced while building the Gemini fixtures: the
installed SDK's `Interaction.output_text` is not a plain stored field — a
`@pydantic.model_validator(mode="after")` (`_populate_output_helpers`)
recomputes it from `steps` on every validation, discarding whatever raw
value (if any) was in the JSON body. Production code only ever reads the
already-recomputed `.output_text`, so this is **not a bug** in `providers.py`
— but it is exactly the kind of hidden SDK behavior that pure signature
introspection would have missed, which is why the fixtures route through
`steps` (matching a real API response) rather than a flat field.

No paid API calls anywhere in this suite.

### J.3 — Stale-running disclosure — added

New `web.app.stale_running_stage(record)`: true only when a stage is
persisted `running` and the run is **not** in `app.state.running` (i.e. no
execution is actually in flight in this process right now) — so it never
fires for a genuinely executing wave, only for one orphaned by a previous
process lifetime. Wired into all three render paths (`run_detail` full page,
`/runs/{id}/status` fragment, `/runs/{id}/events` SSE fragment). New i18n key
`stale_running_notice` (EN+ET) shown above "Resume run" in
`partials/pipeline.html`; all prior stage provenance is untouched — this is
disclosure only, no change to what Resume actually does.

New tests in `tests/test_web.py` (3 tests): notice appears for an orphaned
running stage; notice is absent while genuinely executing (`app.state.
running` populated); notice is absent when nothing is running.

### J.4 — SQLite busy_timeout — added

`Repository.__init__` now runs `PRAGMA busy_timeout = 5000` right after
`PRAGMA foreign_keys = ON`. The serialized single-coroutine write
architecture (Section A/E) is unchanged. New regression test
`tests/test_store.py::test_busy_timeout_pragma_is_set`.

### J.5 — Deployment documentation — added

New README section ("Supported deployment: exactly one application worker
process") directly under the Local Web UI instructions: states the
in-memory double-execution guard and readiness cache are process-local,
explicitly says not to run under `--workers N>1` or multiple replicas
against one DB file, and does not imply multi-worker safety anywhere.

### Changed / new files

```
M  README.md
M  src/llm_deliberation/providers.py
M  src/llm_deliberation/store.py
M  src/llm_deliberation/web/app.py
M  src/llm_deliberation/web/i18n.py
M  src/llm_deliberation/web/templates/partials/pipeline.html
M  tests/test_store.py
M  tests/test_web.py
?? tests/test_gemini_credential.py
?? tests/release_gate/__init__.py
?? tests/release_gate/test_sdk_contracts.py
?? tests/release_gate/fixtures/*.json  (7 files)
```

Nothing staged, committed, or pushed.

### Offline release gates

```
NORMAL TEST SUITE: PASS  (508 passed)
RELEASE GATE:       PASS  (21 passed, tests/release_gate/)
Combined:                 529 passed, 0 failed
```

Also run and passing:
- `py_compile` across every file in `src/` and `tests/` — OK.
- `node --check` on `src/llm_deliberation/web/static/app.js` (the only JS
  file in the project; unmodified this pass) — OK.
- Historical DB migration/read compatibility:
  `tests/test_store.py::test_run_created_before_max_run_cost_usd_column_existed_opens_correctly`
  and `tests/test_service.py::test_historical_run_without_language_column_loads_with_english_default`
  — both PASS (re-run in isolation, not just as part of the full suite).
- Retry/resume/skip workflow tests: 42 passed (`pytest -k "retry or resume or skip"`).
- SDK contract tests: 21 passed (`tests/release_gate/`).
- Gemini credential-precedence regression: 3 passed (`tests/test_gemini_credential.py`).

No test was weakened or deleted to reach green. No new failures were
introduced by J.1–J.5.

**No newly discovered High/Critical issues** beyond what was already in the
original report. One informational (non-bug) finding is noted above under
J.2 (the Gemini SDK's `output_text` derivation behavior).

---

## Revised Live-Canary Plan (supersedes the original Section K)

You did not approve the original `max_output_tokens ≈ 1000` blanket
proposal, and asked for the actual per-stage prompt/output contracts to be
inspected first. Findings, read directly from `prompts.py`:

| Stage | Prompt's own target | Notes |
|---|---|---|
| `analysis_a` / `analysis_b` | **1200–1800 words**, "preferably shorter" | |
| `critique_a_of_b` / `critique_b_of_a` | **600–900 words**, "preferably shorter" | |
| `red_team` | **500–800 words**, "preferably shorter" | |
| `revision_a` / `revision_b` | **600–900 words**, "preferably shorter" | |
| `convergence_analysis` | No word target; structured JSON, "one or two sentences" per field | A **real prior run** (`runs/deliberation-20260915-081905.md`, cited in the Task 5 investigation) used **4,109 of 5,000 output tokens (82%)** for this stage alone — the single largest real-world data point available. |
| `synthesis` | No fixed target — must fit a complete deliverable "first," open-ended by design | Structurally the most unbounded stage. |

**Conclusion: a low, uniform cap (e.g. 1000 tokens) would truncate
`convergence_analysis` almost certainly (real precedent: 4,109 tokens used)
and risk truncating `analysis_a/b` (1800 words ≈ ~2,500–2,700 tokens at a
~1.4–1.5 tokens/word ratio) even under normal, non-adversarial conditions —
exactly the artificial failure you told me to avoid.**

`max_output_tokens` is a **single global setting** in this application
(`MAX_OUTPUT_TOKENS`, default 5000 — there is no existing per-stage
override mechanism in production, so introducing one now would itself be an
unrequested architectural change). Checking the numbers: even fully
unmodified at the production default of 5000 for every stage, the **worst-
case** cost (every one of 9 calls maxing out its output budget, which will
not happen for a tiny canary question) is still small — see below. Per your
own stated preference ("prefer normal production stage limits if
affordable"), and since they are affordable:

**Revised proposal: do not override `max_output_tokens` at all. Run both
canaries against the exact same production default (5000) every real user
gets.** This is the only option with zero risk of an artificially induced
truncation failure, and it is also the simplest — no new, untested
"canary-only" code path to introduce.

### Recalculated cost, economy profile (`gpt-5.6-terra` / `claude-sonnet-5`
/ `gemini-3.8-flash`)

**Worst case** (every stage's call maxes out 5000 output tokens — not
expected, just the true ceiling):
- CANARY 1 (9 calls, red-team ON): ≈ **$0.46** output-token cost + ~15%
  input-token margin ≈ **$0.53**.
- CANARY 2 (8 calls, red-team OFF): ≈ **$0.51**.
- Combined worst-case ceiling: **≈ $1.05**.

**Realistic expected cost**, using each stage's own target range at a
representative midpoint (and `convergence_analysis` anchored to the real
4,109-token precedent, scaled down somewhat for a much smaller canary
question) for a small, low-complexity canary question:
- CANARY 1: ≈ **$0.15–$0.20**.
- CANARY 2: ≈ **$0.13–$0.18** (no Gemini call).
- Combined realistic estimate: **≈ $0.30–$0.35**.

### Revised hard caps

- Each canary's own `max_run_cost_usd` (the app's live, real budget guard,
  exercised as an actual safety net — not just my estimate): **$1.00** per
  run (comfortably above the $0.53/$0.51 worst-case ceilings above, with
  margin for the guard's own deliberately-conservative admission-time
  estimate — see `cost_budget.py` — so a legitimate run is never
  false-positive blocked near the ceiling).
- **Proposed maximum total spend across both canaries: $2.00** (was $1.00 in
  the original, now-superseded proposal — revised upward because the
  correct, truncation-safe limits cost more than the rejected artificial
  1000-token cap did, not because scope grew).

### Canary questions (proposed, for your review)

**CANARY 1** (`profile=economy`, `red_team=True`, `language=en`):
> "Should a 12-person nonprofit switch its donor database from spreadsheets
> to a dedicated CRM tool this quarter, given a limited budget and one
> part-time admin staff member?"

**CANARY 2** (`profile=economy`, `red_team=False`, `language=et`, working
language `en`):
> Same question, output language Estonian, working language English —
> exercises `resolve_stage_language` live end-to-end.

Both are small, concrete, low-ambiguity questions chosen to keep every
stage's real output naturally near the lower end of its own target range,
without needing any artificial token cap to do it.

Everything above is proposed only. **No live/paid API call has been made.**
Awaiting your review of the canary questions/limits/cost ceiling before I
run Canary 1.
