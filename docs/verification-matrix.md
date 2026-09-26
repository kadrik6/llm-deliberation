# LLM Deliberation — Verification / Evidence Matrix

Documentation-only audit. Every row below was checked against the actual
repository, git history, persisted experiment/run databases, or a fresh
offline test run at the time of writing — not against memory of earlier
summaries in this project's history. Where a claim could not be confirmed
this way, it is marked `NOT VERIFIED` or `UNKNOWN` rather than assumed.

**As of:** commit `ff0eda2` (the public, sanitized Case 3 commit — see §9
for why the original authoring commit is not published), 2026-09-26.

## Legend

| Marker | Meaning |
|---|---|
| **YES** | Confirmed by the evidence cited in this row, at the stated category (offline/live/evaluation) |
| **NO** | Explicitly not present / not true |
| **PARTIAL** | Some but not all of the claim is confirmed — the row explains exactly what's missing |
| **HISTORICAL** | Was live-verified previously, but not re-tested in the current environment/configuration; still valid evidence, just not freshly repeated |
| **BLOCKED** | Cannot currently be re-tested live due to an external dependency (not a code/model failure) |
| **N/A** | The category doesn't apply to this capability |

**Column meaning:** *Offline verified* = deterministic/unit/integration test coverage. *Live verified* = actually exercised against real provider APIs, outside the evaluation harness (production canaries/probes). *Evaluation evidence* = observed during a SINGLE/DUAL/CRITIQUE/FULL evaluation experiment specifically.

A `YES` in one column never implies a `YES` in another. "Offline verified" does not mean "live verified." "Material change occurred" does not mean "quality improved." "Converged" does not mean "correct." "More calls" does not mean "a better answer."

---

## 1. Core execution

| Capability | Offline | Live | Eval. evidence | Evidence | Commit / experiment / canary | Limitation |
|---|---|---|---|---|---|---|
| 9-stage graph, 5 waves | YES | YES | YES | `orchestrator.py` `WAVES`; live: all 9 stages completed in Pilot Case 1 FULL and Canary 1 | `3d4a7880d851` (Pilot 1), Canary 1 run `04b5ea950a54` | — |
| `analysis_a` / `analysis_b` | YES | YES | YES | Succeeded in every live run checked (Pilot 1, Canary 1/2, Case 3 all variants including both failed FULL attempts) | multiple, see above | — |
| Cross critique (`critique_a_of_b`/`b_of_a`) | YES | YES | YES | Same runs; both Case 3 FULL attempts reached and passed critique before failing later | Case 3 `7f61fff17b1b`, `350276ef7d32` | — |
| Revisions (`revision_a`/`b`) | YES | YES | YES (Pilot 1 FULL only) | Succeeded in Pilot 1 FULL and both canaries | `3d4a7880d851`, Canary 1/2 | Not reached in either Case 3 FULL attempt (blocked earlier at red_team) |
| `red_team` — direct preferred-model success | YES | **NOT VERIFIED** | **NOT VERIFIED** | Every live red_team execution on record fell back to a different model; a clean first-attempt success on `gemini-3.8-flash` itself has not been observed | — | See "Gemini preferred model" below |
| `red_team` — fallback-on-transient-error path | YES | **HISTORICAL** | HISTORICAL | Canary 1: real HTTP 500, 3 attempts, fell back `gemini-3.8-flash`→`gemini-3.6-flash`. Pilot 1 FULL: same fallback, 4 attempts | Canary 1 `04b5ea950a54`; Pilot 1 `3d4a7880d851` | Not re-observed in current environment (Gemini currently blocked, see §2) |
| `red_team` — non-transient-error, no-fallback path | YES | **YES (new)** | YES | Case 3, both FULL attempts: HTTP 403 correctly classified non-transient, zero retry/fallback attempted, exactly as designed | Case 3 `7f61fff17b1b`, `350276ef7d32` | The stage itself was blocked (see §2) — but the *guard code path* is now live-confirmed twice, independent of that |
| `convergence_analysis` (native structured output) | YES | HISTORICAL | YES | Pilot 1 FULL: 6 material changes parsed via real schema; Canary 1/2 also succeeded | `3d4a7880d851`; Canary 1/2 | Not exercised in Case 3 (FULL never reached it) |
| `synthesis` | YES | HISTORICAL | YES | Pilot 1 FULL, Canary 1/2 all succeeded | same as above | Not exercised in Case 3 |
| Manual retry/skip (user-facing action) | YES | **NOT VERIFIED** | N/A | 48 offline tests via `-k "retry or resume or skip"`; no live run in this project's history has actually exercised the web-facing retry/skip endpoints against real providers | — | Every live run so far completed or failed cleanly without a human clicking Retry/Skip |
| Resume after budget block | YES | **NOT VERIFIED** | N/A | Offline-tested; no live run has hit `run_budget_exceeded` (all live runs stayed well under budget) | — | — |
| Completion-reserve auto-skip of red_team | YES | **NOT VERIFIED** | N/A | 16 offline tests (`test_completion_reserve.py`); every live run's actual spend stayed far below its cap, so the reserve was never close to intervening | — | Live-active-but-never-triggered is not the same as live-proven-to-intervene correctly |
| Stale-`running`-stage disclosure | YES | **NOT VERIFIED** | N/A | Offline-tested; no live process crash has occurred mid-stage to exercise this | — | — |

---

## 2. Providers

| Capability | Offline | Live | Eval. evidence | Evidence | Commit / experiment / canary | Limitation |
|---|---|---|---|---|---|---|
| OpenAI generation | YES | YES | YES | Every live run in this project's history used it successfully | Pilot 1 (4/4), Case 3 (SINGLE/DUAL/CRITIQUE + both FULL attempts' early stages), Canary 1/2, live language probe | — |
| Anthropic generation | YES | YES | YES | Same as above | same | — |
| Anthropic structured convergence | YES | HISTORICAL | YES | Pilot 1 FULL: real schema-validated JSON, 6 material changes | `3d4a7880d851`; Canary 1/2 | Not exercised in Case 3 |
| Gemini generation (any success) | YES | HISTORICAL | HISTORICAL | Canary 1 and Pilot 1 FULL both eventually succeeded — via fallback, not the preferred model | Canary 1, Pilot 1 | Currently **BLOCKED** (below) |
| Gemini preferred model, direct | YES | **NOT VERIFIED** | **NOT VERIFIED** | No recorded live run has had `gemini-3.8-flash` succeed on the first attempt | — | Every live attempt on record needed a fallback or failed outright |
| Gemini fallback chain | YES | HISTORICAL | HISTORICAL | Canary 1 (transient 500 → fallback success), Pilot 1 (same pattern) | Canary 1 `04b5ea950a54`, Pilot 1 `3d4a7880d851` | Not re-observed in current environment |
| **Current Gemini availability** | N/A | **BLOCKED** | **BLOCKED** | Free `models.get` readiness check succeeds (`status: ready`) for `gemini-3.8-flash`; the generation endpoint (`interactions.create`) returns `HTTP 403 permission_denied — "Your project has been denied access. Please contact support."`, identical on two separate attempts | Case 3 `7f61fff17b1b`, `350276ef7d32` | Per the user: billing/access is deliberately not being restored right now. This is an external account-side restriction, not a code, model, or application defect — the readiness/generation asymmetry is itself evidence the failure is generation-API-specific, not a broad account outage |

---

## 3. Reliability

| Capability | Offline | Live | Evidence | Limitation |
|---|---|---|---|---|
| Provider retries (transient errors) | YES | HISTORICAL | Canary 1's red_team retried 3x on real HTTP 500 | Not re-observed currently |
| Fallback chain (model substitution) | YES | HISTORICAL | Canary 1, Pilot 1 | Not re-observed currently |
| Truncation detection | YES | **NOT VERIFIED** | Checked all live/production-shaped DBs (`canary1/2`, `pilot_eval_case_1_full_run`, both `case3_full_run*`) for `failure_reason='output_truncated'` — zero rows found | No live run has ever actually truncated |
| Truncation *recovery* (the bounded retry) | YES | **NOT VERIFIED** | Same check — the recovery branch has never fired live because truncation has never occurred live | — |
| Failed-stage handling (typed `failure_reason`) | YES | YES | Case 3 FULL ×2: `failure_reason=provider_error`, correctly typed and rendered, not silently swallowed | — |
| Stale-running handling | YES | **NOT VERIFIED** | No live crash has occurred | — |
| SQLite persistence (serialized writes) | YES | YES (implicitly, via every live run) | Every persisted run in this audit is proof the write path works under real concurrent-stage load | — |
| Historical DB compatibility (additive migrations) | YES | N/A | 3 dedicated offline tests, part of the 605 | — |
| `PRAGMA busy_timeout` | YES | **NOT VERIFIED** | Offline test confirms the pragma is set; no live concurrent-access contention has ever occurred to exercise it | — |
| Single-worker deployment constraint | N/A (documentation) | N/A | README section added; this is a stated operational constraint, not a tested behavior | Enforced by not using `--workers N>1`, never mechanically tested |

---

## 4. Budget / cost

| Capability | Offline | Live | Eval. evidence | Evidence | Limitation |
|---|---|---|---|---|---|
| Cost tracking (per-stage, accumulated) | YES | YES | YES | Every persisted experiment/run has exact per-stage and total costs (e.g. Pilot 1 FULL: $0.3148 across 12 calls) | — |
| Run-budget admission (hard cap) | YES | **NOT VERIFIED** | N/A | No live run has ever approached its cap (all stayed at 2–42% of the cap they were given) | The guard's *blocking* behavior is only offline-proven |
| Completion reserve (calculation) | YES | YES (computed, inert) | N/A | Live runs had a reserve computed each time (since `max_run_cost_usd` was always set); it never needed to act | See §1 — computed ≠ intervened |
| Optional-stage skip due to reserve | YES | **NOT VERIFIED** | N/A | 16 offline tests | Never triggered in any live run |
| Live budget exhaustion behavior | YES | **NOT VERIFIED** | N/A | Offline-only | — |
| Persisted token/cost provenance | YES | YES | YES | Confirmed across every experiment cited in this document | — |

---

## 5. Language

| Capability | Offline | Live | Eval. evidence | Evidence | Limitation |
|---|---|---|---|---|---|
| Requested working-language resolution | YES | YES | N/A (Pilot 1/Case 3 are English-only cases) | Canary 2 rerun: all 6 working-language stages resolved to `en` correctly on an Estonian-output run | — |
| Observed-language detection (matched case) | YES | YES | N/A | Live language probe (1 stage) + Canary 2 rerun (6 stages): 7 of 7 matched on the first attempt | — |
| Mismatch detection (confident mismatch) | YES | HISTORICAL | N/A | The *original* Canary 2 run (before the fix) showed 3 of 6 stages mismatched — this is what motivated the fix | Pre-fix run, superseded by the rerun |
| Corrective language-recovery branch | YES | **NOT VERIFIED** | N/A | 10 offline tests (`test_language_contract.py`) | Every live run since the fix has matched on the first attempt — the recovery call itself has never fired live |
| Historical/live language-probe evidence | N/A | YES | N/A | Documented in this project's own audit report | One data point, Case A (matched immediately) only |

---

## 6. Security / output

| Capability | Offline | Live | Evidence | Limitation |
|---|---|---|---|---|
| Markdown → sanitized HTML (`nh3`) | YES | YES (implicitly, every rendered run) | 25 dedicated tests (`test_markdown_render.py`); every live synthesis output has passed through this path to be displayed | — |
| HTML safety / allowlist | YES | YES (implicitly) | Same file, explicit allowlist configuration | Not adversarially fuzz-tested against a real live-generated malicious payload — no live output has ever contained one |
| Raw-diagnostic bounding (e.g. convergence parse-error excerpts) | YES | **NOT VERIFIED** | Offline-tested (`RAW_TEXT_EXCERPT_CHARS`); no live run has ever hit a parse error to exercise it | — |
| API-key/credential separation (Gemini) | YES | YES | `test_gemini_credential.py` (offline) + confirmed live this session that `GEMINI_API_KEY` alone is set, `GOOGLE_API_KEY` unset — no ambiguity to test against | — |

---

## 7. Evaluation harness

| Capability | Offline | Live | Evidence | Limitation |
|---|---|---|---|---|
| Case loader (`EvalCase`, JSON format) | YES | N/A | Loads both committed cases correctly, round-trip tested | — |
| SINGLE variant | YES | YES (×2 cases) | Pilot 1 `b65259c44f54`, Case 3 `47b0de64fec2` | — |
| DUAL variant | YES | YES (×2 cases) | Pilot 1 `1f0b2d60084a`, Case 3 `c47b5b345a65` | — |
| CRITIQUE variant | YES | YES (×2 cases) | Pilot 1 `8ff2b5585238`, Case 3 `3f616996042a` | — |
| FULL variant | YES | YES (Pilot 1 only) / **BLOCKED (Case 3)** | Pilot 1 `3d4a7880d851` succeeded; Case 3 both attempts failed at `red_team`, external cause (§2) | Case 3 FULL not yet demonstrated |
| Experiment DB (`eval_experiments`/`eval_stage_results`) | YES | YES | Both `data/pilot_eval_case_1.db` and `data/case3_eval.db` contain real, queryable records | — |
| Blind-review exporter | YES | YES (Pilot 1) / **INVALID artifact produced once (Case 3)** | Pilot 1 export leak-checked clean; a Case 3 export was generated automatically after FULL's first failure and correctly identified, by manual inspection, as containing a blank 4th output — it was never used for scoring | The auto-export script didn't originally gate on all-variants-succeeded; this is a tooling gap in throwaway scripts, not the harness's `build_blind_export`/`write_blind_export` functions themselves, which behaved correctly on the data they were given |
| Randomized mapping (seeded) | YES | YES | Pilot 1 seed `20260917`; Case 3's (invalid) export used seed `20260925` | — |
| Score-lock-before-unblind discipline | YES | YES | Pilot 1: scores locked to a timestamped file before the mapping was opened, verified by inspection | — |
| `eval_reviews` persistence | YES | YES | Pilot 1: 32 review rows (8 per variant × 4 variants) persisted, confirmed by direct query in this audit | — |
| Model/reviewer data separation | YES | YES | 5 dedicated tests (`test_evaluation_case_separation.py`): AST-level, signature-level, and runtime proof that `review_notes`/`important_constraints` never reach a live prompt | — |
| Operational reporting (cost/latency/calls) | YES | YES | This document's own tables, sourced from persisted DB records | — |

---

## 8. Pilot Case 1

| Item | Status | Evidence |
|---|---|---|
| All four variants executed | YES | `b65259c44f54`, `1f0b2d60084a`, `8ff2b5585238`, `3d4a7880d851` |
| Locked human scores | YES | SINGLE 15/16, DUAL 16/16, CRITIQUE 16/16, FULL 15/16 — 32 `eval_reviews` rows, re-confirmed by direct query for this audit |
| Cost/duration/calls | YES | SINGLE $0.0181/31s/1; DUAL $0.0928/114s/3; CRITIQUE $0.2111/244s/7; FULL $0.3148/420s/12 |
| Ceiling effect | **OBSERVED** | All four scores fell in a 15–16/16 band; this case did not discriminate architecture quality |
| Material changes (FULL) | YES (structured) | 6 total, all `change_status="material"` |
| Red-team-triggered material changes | YES (structured) | 3 of 6 changes listed `red_team` as ≥1 trigger source |
| Unresolved disagreements | YES (structured) | 2, `convergence="converged"` |
| What was NOT established | Explicit | No architecture superiority; no statistical significance; no generalization beyond this one case (see the project's own Pilot Case 1 findings report) |

---

## 9. Case 3

| Item | Status | Evidence |
|---|---|---|
| Frozen commit (historical, **not published**) | `96313d1b8a56d9713a7c9addb0762264fa0f8c88` | The local commit the three live experiments below were actually executed from. Not reachable from this public branch — a pre-publication audit found it tracked the case's full reviewer-only scoring key (private reasoning key, stress-test rubric, human-coding sheet) directly in the public `eval_cases/` file. See the public sanitized commit below. |
| Public sanitized commit | `ff0eda2` | Introduces the same case under `case_id`/`question`/`context`/`output_language`, reviewer-only fields moved to a gitignored `eval_private/` sidecar — see `src/llm_deliberation/evaluation/cases.py`'s `ReviewerMetadata`/`load_reviewer_metadata`. |
| Model-visible input identity | **VERIFIED** | SHA-256 of `{case_id, question, context, output_language}` is identical whether computed from the historical private commit or the public sanitized commit: `472be5631b0050029b8d85efeca350dcd5cf35df498b62ab047a2bc527357089` (see `cases.model_input_fingerprint`). This is the reproducibility anchor now that the original authoring commit is intentionally not public. |
| Case-file/model separation tests | YES | 13 tests, `test_evaluation_case_separation.py` (rewritten during the pre-publication audit to use only synthetic fixture content — no real reviewer secret text in test source), part of the 612-test suite |
| SINGLE live result | YES | `47b0de64fec2`, succeeded, $0.0257/38s/1 call |
| DUAL live result | YES | `c47b5b345a65`, succeeded, $0.1413/164s/4 calls |
| CRITIQUE live result | YES | `3f616996042a`, succeeded, $0.2938/326s/8 calls |
| FULL status | **NOT ATTEMPTED (blocked)**, not "failed" in the quality sense | Two genuine attempts (`7f61fff17b1b`, `350276ef7d32`) both stopped at `red_team` on an external HTTP 403; not a code, model, or application defect |
| Current Gemini limitation | BLOCKED by user choice (billing) | See §2 |
| Blind-review status | **NOT STARTED** | No valid 4-way export exists; the one auto-generated artifact is invalid and unused (see §7) |
| What must remain uninspected/unblinded | SINGLE/DUAL/CRITIQUE outputs (content), any mapping, any stage-level constraint-detection coding | Deliberately not inspected in this audit — confirmed: this document contains zero semantic commentary on any Case 3 output |

---

## 10. Decision Cockpit

| Item | Status | Evidence |
|---|---|---|
| Structured data already exists | YES, extensively | See `docs/design/decision-cockpit-proposal.md`'s own audit (Section 1): synthesis, convergence state, agreements, disagreements, material changes with before/after and trigger source, unknowns, human-judgement items, stage status/models/tokens/cost, language contract, retries/fallbacks — all Category A in that document |
| Which observability claims are supported | Process data is real and persisted (cost, latency, material changes, trigger attribution) | Confirmed again in this audit via direct DB queries (§8) |
| Which UX-value claims remain hypotheses | Everything about whether *visualizing* this data helps a human decide faster/better | **Nothing has been implemented or tested** — no cockpit code exists; Pilot Case 1's reviewer only ever saw plain final text, never any process visualization |

---

## What we can safely say publicly

1. The system has been exercised end-to-end with real OpenAI and Anthropic APIs across multiple independent live runs (two release canaries, a live language probe, and two full evaluation experiments).
2. Gemini generation and its fallback chain have both succeeded in real, live runs historically (a genuine HTTP 500 was recovered from automatically by falling back to a different model).
3. The application's non-transient-error handling for Gemini has now also been confirmed live: a real HTTP 403 was correctly classified as non-retryable and the system failed cleanly with a typed reason, without masking the problem or silently retrying.
4. The evaluation harness supports frozen, version-controlled cases; deterministic, seeded blind-label randomization; a verified separation between what a model receives and what only a human reviewer sees; persisted per-stage cost/token/duration provenance; and locked human review scores recorded before any unblinding.
5. One full pilot case (four architectures, human-scored, unblinded) has been completed end-to-end, including cost/latency/call-count comparison and structured process-evidence extraction (material changes, disagreements, convergence state).
6. The 605-test offline suite, alongside dedicated release-gate, completion-reserve, and evaluation-harness tests that are part of that same suite (not additional to it), currently all pass.
7. Historical DB migrations remain additive-only and have dedicated compatibility tests confirming old-schema data still loads correctly.

## What we must NOT claim yet

- That deliberation (more stages/models) improves answer accuracy in general.
- That FULL is better than SINGLE, DUAL, or CRITIQUE — Pilot Case 1 showed a narrow, ceiling-effected score band, not a demonstrated advantage.
- That red-team review improves final-answer quality — its material-change contributions are structurally real, but no evidence connects them to anything the one human reviewer explicitly valued.
- That convergence (`"converged"`) indicates the answer is correct, only that the models' revised positions agreed with each other.
- That the Decision Cockpit would improve human decisions — no version of it exists yet, and nothing has tested this.
- Any conclusion about Case 3 (which architecture handled the flip constraint, or whether deliberation helps on harder cases) — FULL has not run, and no blind review has started.

## Remaining evidence gaps (ranked by usefulness, not urgency)

1. **Live corrective language-recovery branch never observed.** Every live run has matched on the first attempt. Requires a real provider call where a model actually mismatches — cannot be forced without either an adversarial prompt (out of scope) or waiting for a natural occurrence. Matters because this is the one language-policy branch with zero live evidence, offline-only.
2. **Live user-facing retry/skip/resume actions never observed.** All live runs so far completed or failed cleanly without needing a human-triggered action. Requires either a deliberately-broken live scenario or waiting for a natural failure to click through. Matters because this is the primary recovery UX a real user would depend on.
3. **Completion-reserve auto-skip never triggered live.** Requires a live run configured with a tight-enough budget to force the boundary condition — cheap and safe to construct deliberately (a small, controlled paid call). Matters because it's a new safety feature whose *intervention* behavior is currently offline-only.
4. **Case 3 FULL blocked externally.** Requires Gemini billing/access to be restored — not something to force; already scoped as deferred by user choice, not a code gap.
5. **Gemini preferred-model direct success never observed.** Every live Gemini call on record needed a fallback or failed. Low priority to chase deliberately — it doesn't indicate a problem (fallback is working correctly), just an evidence gap.
6. **Truncation and its recovery branch never observed live.** No live run has ever produced a response long enough to truncate at `max_output_tokens=5000`. Deliberately *not* worth forcing per this project's own stated principle (don't manufacture an artificial truncation just to test it) — offline coverage is considered sufficient here.

I am not recommending re-testing Gemini fallback/red-team success live merely for completeness — strong historical live evidence already exists for that path (twice), and repeating it would mostly duplicate cost without adding new information.

---

## Git / file discipline

```
$ git diff -- docs/design/decision-cockpit-proposal.md
(no output — file untouched)

$ git status --short
?? docs/design/
?? docs/verification-matrix.md
```

Only this new file was added; the pre-existing uncommitted Decision Cockpit proposal was not modified or included.
