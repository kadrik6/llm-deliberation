# Decision Cockpit — Design & Architecture Proposal

Status: **Phase 1 and Phase 2 (partially) implemented, not yet human
UX-tested.** See "Phase 1 implementation status" and "Phase 2
implementation status" immediately below for exactly what changed and what
did not. This document itself is otherwise unchanged from the original
proposal — everything below those status blocks is the original design
text, kept as-is for reference; it is not a record of what was actually
built.

## Phase 1 implementation status

Scope of this pass: only the genuinely missing pieces of Sections 1–3's
Level 1/Level 2 design (most of that surface already existed in
`result.html`/`convergence_summary.html`/`quality_indicator.html` from
earlier, unsummarized work in this same project) plus a truthful red-team
status and a compact trace rollup. No provider calls were made, no
evaluation data or blind mapping (Pilot Case 1 or Case 3) was touched, and
no production prompt/provider logic changed.

| Element | Status | Notes |
|---|---|---|
| Decision Snapshot — convergence badge + material-change/disagreement/unknown/human-judgement counts (Level 1) | **IMPLEMENTED** (predates this phase) | Already present in `result.html`'s `.decision-snapshot` block; not modified this phase beyond adding the red-team-state/trace-summary blocks alongside it |
| What Changed / Where Disagree / Remaining Unknowns / Human Judgement (Level 2) | **IMPLEMENTED** (predates this phase) | Already present in `partials/convergence_summary.html`; not modified this phase |
| Quality indicator (complete/degraded/incomplete) | **IMPLEMENTED** (predates this phase) | `partials/quality_indicator.html`, `presenter.compute_deliberation_quality`; not modified this phase |
| Truthful red-team status (7 distinct states, never collapsed) | **IMPLEMENTED** (this phase) | `presenter.red_team_state`; correctly reclassifies `externally_blocked` even after a subsequent user skip (attempt_log survives `mark_stage_skipped`) — the exact shape of the real Case 3 FULL failure |
| Red-team ↔ material-change aggregate ("Referenced in N material changes") | **IMPLEMENTED** (this phase) | `presenter.red_team_material_change_reference_count` — Category A per Section 1's own audit (row 58); never phrased as "found N errors" |
| Compact operational trace rollup (logical stages / provider attempts / retries-or-fallbacks) | **IMPLEMENTED** (this phase) | `presenter.trace_summary`; rendered as a collapsed `<details>`, low in the page hierarchy, plain counts only |
| Per-stage duration for a *completed* stage (Section 1, Category B gap) | **DEFERRED** | Not touched this phase; `stage_elapsed_seconds` still only covers a *running* stage, as before |
| Red-team findings as a structured list (Category D) | **NOT IMPLEMENTED — correctly deferred** | No parsing of red-team free text was added; still requires a schema change this phase deliberately did not make |
| Specific-finding → specific-change linkage (Category C) | **NOT IMPLEMENTED — correctly deferred** | Would require semantic judgment; out of scope |
| Evaluation Dashboard (Section 4/13, phase 4) | **DEFERRED** | No new route, no new template |
| Constraint-coverage matrix / manual scoring UI (Section 6, phase 5) | **DEFERRED** | No schema or UI changes |
| Text-only vs. visual Decision Cockpit user study (Section 12) | **DEFERRED — remains a future hypothesis** | Not started; no data gathered this phase |

**Explicitly still unproven, future hypotheses only** (per Section 12 —
this phase gathered no evidence toward any of these and makes no claim
about them): that the cockpit improves decision quality, time-to-
understanding, or calibration versus a text-only view; that a visual
presentation is better than plain text for this task. Nothing implemented
this phase was evaluated against these questions. The Decision Snapshot is
*structurally designed* for rapid scanning (a badge + a handful of short
stat chips, no interaction required); whether it is actually understood in
around 10 seconds by a real reader remains an untested UX hypothesis, not
a measured result.

---

## Phase 2 implementation status

Scope of this pass, per the Phase 2 design audit: **only** "Complete the
'You Decide' section." No other Phase 2/3 element (progressive disclosure,
decision-evolution flow diagram, Evaluation Dashboard, constraint-coverage
matrix, etc.) was touched. No provider calls were made, no evaluation
data/mapping was touched, no prompt/provider/evaluation-semantics change
occurred.

| Element | Status | Notes |
|---|---|---|
| "You Decide" assembled from all three designed-for fields (§2.5, §8) | **IMPLEMENTED** (this phase) | `presenter.you_decide_items`; previously only `human_judgement_required` was shown — `unresolved_disagreements[].decision_impact` and `remaining_unknowns[].why_it_matters` are now included, each item tagged by a visible text category label |
| Compact index, not a duplicate detail copy | **IMPLEMENTED** (this phase) | Only one field per source is reused (never topic/positions/why_unresolved/evidence_needed); a "Details above" anchor links each reused item back to its full detail section, framing it as "what to weigh," never as independent new evidence |
| Fixed, non-inferred category order (disagreement → human judgement → unknown) | **IMPLEMENTED** (this phase) | No priority/severity/confidence signal exists in the schema or was introduced |
| Honest, narrow empty-state message | **IMPLEMENTED** (this phase) | "No additional human judgement items were recorded." — deliberately not "nothing left to decide" or "no uncertainty remains," which would claim more than an empty list supports |
| Level-2 progressive disclosure (the five sections are still always fully expanded, on every viewport) | **DEFERRED** | Identified as a real gap in the Phase 2 design audit; explicitly out of scope for this pass per the approval instructions — to be reassessed after this change, not bundled into it |
| Decision-evolution flow diagram (§5) | **DEFERRED** | Not part of this pass's approved scope |

**Structural UI review only, not human usability testing.** This
environment has no rendered-browser/screenshot tool. What was actually
done: the rendered HTML of three existing, non-Case-3 historical runs
(Canary 1, Canary 2 rerun, Pilot Case 1's FULL production run) was
inspected for DOM structure, section order, absence of duplicate ids, and
absence of a duplicated bordered sub-box per category. This confirms the
markup is well-formed and positioned as intended — it does **not** confirm
that a human reader finds the page easy to scan, uncluttered, or faster to
understand than before. No claim of validated visual hierarchy or
human-tested usability is made anywhere in this document.

**Case 3 status, precisely** (previously stated imprecisely as "unrun"):
SINGLE/DUAL/CRITIQUE completed live; FULL has no complete result because
Gemini red-team was externally blocked (HTTP 403) on two genuine attempts;
blind review has not started; no mapping has been created or revealed; no
semantic comparison of any Case 3 output has been performed. Nothing in
Phase 2 touched Case 3 in any way — no Case 3 variant ever reaches
`convergence_analysis` in the first place (SINGLE/DUAL/CRITIQUE have no
convergence stage at all; FULL was blocked earlier, at `red_team`), so
"You Decide" has no Case 3 data to draw from even in principle.

---

Product reframe this proposal serves: LLM Deliberation moves from *"multiple
models produce a final answer"* to *"a human decision-support system that
makes the reasoning process, changes, disagreements, uncertainty,
constraints, and cost visible."* Two hard constraints follow from that and
are treated as invariants throughout this document: (1) never imply
consensus = truth, more agents = better, or FULL = better; (2)
visualization is decision support, not decoration — every visual must carry
real, persisted information, never a decorative gauge.

---

## 1. Current-data audit

Read fresh from `convergence.py`, `store.py`, `presenter.py`,
`orchestrator.py`, `cost_budget.py`, `evaluation/` (this session's own
code, re-verified just now against the live schema, not from memory of old
docs).

**Cross-cutting caveat, applies to every "A" item below sourced from
`convergence_analysis`:** the whole convergence record is, by its own
module docstring, *"model-generated analytical metadata... not a
ground-truth judgement."* "Already structured" describes the **format**
(a validated Pydantic schema), never the **truth** of its content. This
distinction must survive into the UI's terminology (see Section 8).

| Field | Class | Source |
|---|---|---|
| Final recommendation / synthesis | **A** | `StageRecord.text` where `name="synthesis"` |
| Convergence state | **A** | `ConvergenceAnalysis.convergence` (`"converged"\|"partial"\|"diverged"\|"insufficient_information"`) |
| Agreements | **A** | `ConvergenceAnalysis.agreements_reached` (list of `{topic, shared_position}`) |
| Unresolved disagreements | **A** | `ConvergenceAnalysis.unresolved_disagreements` (list of `{topic, candidate_a_position, candidate_b_position, why_unresolved, decision_impact}`) |
| Material changes | **A** | `ConvergenceAnalysis.material_changes` filtered `change_status=="material"` |
| Non-material changes | **A** | same list filtered `change_status=="non_material"` (plus `"unknown"` — a real third state, see Section 8) |
| Change trigger (peer critique / red-team / other) | **A** | `MaterialChange.triggers[].source` — enum `peer_critique\|red_team\|own_reassessment\|uncertain\|other`; a change can have >1 trigger |
| Unknowns / missing facts | **A** | `ConvergenceAnalysis.remaining_unknowns` (list of `{unknown, why_it_matters, evidence_needed}`) |
| Human-judgement items | **A** | `ConvergenceAnalysis.human_judgement_required` (list of `{issue, why_models_cannot_resolve_it}`) |
| Constraint list (evaluation only) | **A** | `EvalCase.important_constraints` — manually authored, version-controlled |
| **Constraint coverage** (was this constraint addressed?) | **D** (production) / **C** (evaluation) | No scoring mechanism exists anywhere in the codebase today. See Section 6. |
| Stage success/failure/skipped | **A** | `StageRecord.status` |
| Requested vs. actual model | **A** | `StageRecord.requested_model` / `.model` |
| Requested vs. observed working language | **A** | `StageRecord.language_contract_status` / `.observed_language` (this session's own Part A/language-enforcement work) |
| Retries / recoveries / fallbacks | **A** | `StageRecord.model_attempts`, `.attempt_log` (phase- or fallback-chain-shaped), `.fallback_used`, `.fallback_reason` |
| Cost per stage | **A** | `StageRecord.estimated_cost_usd` |
| Total cost | **A** | `RunRecord.estimated_total_cost_usd` |
| Duration per stage | **B** | `StageRecord.started_at`/`.completed_at` exist; the subtraction is not currently computed/rendered anywhere for a *completed* stage (only for a `running` one, via `presenter.stage_elapsed_seconds`) |
| Total duration | **A** | already computed: `presenter.elapsed_seconds(record)` from `RunRecord.started_at`/`.completed_at` |
| Input tokens | **A** | `StageRecord.input_tokens` |
| Output tokens | **A** | `StageRecord.output_tokens` |
| Provider-call count | **A** | `StageRecord.model_attempts` (or `len(attempt_log)` for a fallback-chain stage) |
| Red-team **findings** (as a structured list) | **D** | `red_team`'s prompt (`prompts.red_team`) asks for free-form numbered prose, not a validated schema — unlike `convergence_analysis`, there is **no Pydantic structure for red-team output**. Treating its five numbered sections as a "list of findings" today would require parsing free text — not available as structured data. |
| Whether *a* red-team finding affected a revision (aggregate signal) | **A** | `MaterialChange.triggers[].source == "red_team"` — real, structured, already demonstrated in Pilot Case 1's process-value report (3 of 6 material changes had a red-team trigger) |
| Whether *a specific* red-team finding (by content) drove a *specific* revision change | **C** | Would require matching free-text red-team prose against a trigger's own `summary` field — itself a model interpretation, not a verified link |
| Whether a change survived into final synthesis | **C** | No structural link exists between `synthesis` text and any specific `MaterialChange` — would require semantic comparison. (Already disclosed honestly this way in the Pilot Case 1 report — no invention there.) |
| Initial vs. revised position | **A** | `MaterialChange.before` / `.after` — already part of the validated schema |

**Bottom line:** the overwhelming majority of what a "Decision Cockpit"
needs is **already structured (A)**, persisted, and tested. The two real
gaps are (1) per-stage duration formatting (a two-line **B** fix, not a
schema change) and (2) constraint coverage and red-team-findings-as-a-list,
both genuinely **C/D** — never to be faked into fake structure.

---

## 2. Critique of the proposed layout, then a revised information architecture

**What's right:** the sequencing (conclusion → agreement → change →
disagreement → red-team → unknowns → human-decision → full answer → trace)
already matches how a careful human reader would want to triage a long
report, and it extends a pattern this codebase **already has** —
`result.html` already does lead-paragraph-then-collapsible-details for the
synthesis, and `presenter.input_preview` already does deterministic
collapse-by-default for long Question/Context text. This proposal is an
extension of an existing convention, not a new one.

**What I'd change:**

1. **Merge "DECISION SNAPSHOT" and "DELIBERATION AT A GLANCE" into one
   strip.** Two separate full-width blocks risk pushing the
   agreement/change/unknowns counts below the fold on a phone before the
   user has even finished the conclusion — defeating the 5–10 second goal.
   One compact strip: conclusion lead paragraph on top, four small stat
   chips (agreement state, material changes, unknowns, human-judgement
   items) directly underneath, same viewport.
2. **Never show "WHAT THE RED TEAM ADDED" when red-team was disabled from
   the start.** Only render it when a `red_team` stage row exists and
   succeeded. If it exists but was *skipped* (manually or by the
   completion-reserve auto-skip — see this session's Part A work), show a
   single muted line reusing the existing `red_team_skipped_for_budget_notice`
   /`skip_note_red_team` copy, not an empty section — an empty section
   visually implies "nothing was found," which is a different (false)
   claim from "this was never run."
3. **Keep disagreement cards and red-team cards visually distinct types,**
   not the same card style under different headings. A disagreement is
   between A and B; a red-team item is a third party's critique. Using the
   same visual pattern for both invites the reader to conflate them (see
   Section 8's semantic-distinction rules).
4. **Pull the synthesis's own lead paragraph up into the Level-1 strip**
   (the code already extracts this via `split_sections`/first heading+body
   in `result.html` — reuse it), and keep the **full** synthesis at Level 3
   exactly where the proposal puts it. The deliverable itself should never
   be more than one scroll away from the top, even while all the new
   evidence structure sits between the snapshot and the full answer.
5. **"YOU DECIDE" should be assembled from three existing fields, not just
   `human_judgement_required`:** that field, plus each unresolved
   disagreement's own `decision_impact`, plus each remaining unknown's
   `why_it_matters` — all three are already "why does this matter to a
   human" fields sitting in different parts of the schema today, scattered
   and under-surfaced in the current `result.html`.

### Revised information architecture (what belongs where)

| Level | Content | Format |
|---|---|---|
| **1 — 10s overview** | Synthesis lead paragraph + 4 stat chips (agreement state, material changes, unknowns, human-judgement count) | Always visible, no interaction needed |
| **2 — decision evidence** | What Changed (per-candidate change cards + flow), Where Models Disagree (cards), What The Red Team Added (conditional), What We Still Don't Know, You Decide | `<details>`-based, open by default on desktop, collapsed by default on narrow screens (reuses the existing collapse convention) |
| **3 — full answer** | Complete synthesis body | Existing `<details>` sections, unchanged |
| **4 — trace/provenance** | Existing "Full trace" artifacts section, extended with per-stage duration (the one new **B** field), language-contract status, completion-reserve notice | Existing collapsed section, unchanged in kind |

---

## 3. ASCII wireframe — Decision Cockpit (desktop; narrow layout stacks every row to one column, same order)

```
┌──────────────────────────────────────────────────────────────────┐
│ DECISION SNAPSHOT                                                  │
│                                                                      │
│ "Do not commit to a full CRM cutover this quarter by default..."   │  ← synthesis lead paragraph
│                                                                      │
│ [◐ Partial agreement]  [⇄ 3 material changes]  [? 6 unknowns]      │  ← stat chips, real counts
│ [⚖ 2 items need your judgement]                                    │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│ WHAT CHANGED                                                        │
│                                                                      │
│  Candidate A                                Candidate B             │
│  ┌────────────────────────┐                ┌───────────────────┐  │
│  │ Before: "Pilot CRM now" │                │ Before: "..."      │  │
│  │ After:  "Assess first"  │                │ After:  "..."      │  │
│  │ ⤷ peer critique, red-team│               │ ⤷ peer critique     │  │
│  └────────────────────────┘                └───────────────────┘  │
│  (2 more material changes — expand)                                │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│ WHERE MODELS STILL DISAGREE (2)                                    │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ Topic: donor-data migration timing                          │  │
│  │ A holds: "..."          B holds: "..."                      │  │
│  │ Why unresolved: ...      Why it matters to you: ...          │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│ WHAT THE RED TEAM ADDED   (only rendered if red-team actually ran) │
│  ⚑ "Shared assumption worth challenging: ..."                      │
│    → contributed to 3 material change(s) above (per convergence)   │
│    → whether it appears in the final synthesis: not verifiable     │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│ WHAT WE STILL DON'T KNOW (6)                                       │
│  • "Number of donor/gift records" — matters because: ...            │
│  (5 more — expand)                                                  │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│ YOU DECIDE                                                          │
│  • Timing tradeoff: pilot now vs. next-quarter launch (disagreement)│
│  • Whether existing donation platform already covers the need       │
│    (human-judgement item — models cannot resolve this)              │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│ ▸ FULL ANSWER (expand)                                              │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│ ▸ TRACE / PROVENANCE (expand) — stages, models, tokens, cost,      │
│   duration, retries, language contract, budget notices             │
└──────────────────────────────────────────────────────────────────┘
```

**What's above the fold:** only the Decision Snapshot block.
**Graphical:** the change-flow arrows, the stat chips, the cost/duration
bars in Trace. **Text:** everything else — disagreement cards, red-team
items, unknowns, You Decide, full answer. **Collapsed by default (narrow
screens only):** What Changed / Where Disagree / Red Team / Don't Know /
You Decide, each its own `<details>`. **What would overload the user:**
showing all `material_changes` (not just a few) expanded by default, or
adding numeric "confidence" anywhere models don't actually report one.

---

## 4. Evaluation Dashboard wireframe (separate page/section, evaluation-only)

```
┌──────────────────────────────────────────────────────────────────┐
│ Case: example-nonprofit-crm         RESOURCE USE (observed, not    │
│                                      matched-compute)               │
│                                                                      │
│         SINGLE   DUAL   CRITIQUE   FULL                             │
│ Calls     █       ███     ███████   ████████████                   │
│ In tok    █       ████    ██████████████  ████████████████████     │
│ Out tok   ██      ████████ █████████████  █████████████            │
│ Cost      █       ████     █████████       █████████████            │
│ Duration  ██      ███████  ██████████████  ████████████████████    │
│                                                                      │
│ Completed:  ✓        ✓         ✓              ✓                    │
│ Truncated:  —        —         —              —                     │
│ Retries:    —        —         —          red_team: 4 attempts      │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│ DELIBERATION DEPTH (process signals — not correctness)             │
│                     DUAL   CRITIQUE   FULL                          │
│ Material changes     n/a     n/a        6                          │
│ Unresolved disagree. n/a     n/a        2                          │
│ Red-team contribution n/a    n/a        3 of 6 changes              │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│ PILOT HUMAN REVIEW SCORE   (only after human scoring exists —      │
│ explicitly labeled "pilot", never "objective quality")             │
│         SINGLE  DUAL  CRITIQUE  FULL                                 │
│ Score     ?       ?      ?        ?     ← blank until you score    │
└──────────────────────────────────────────────────────────────────┘
```

Every bar is a **small multiple sharing one x-axis** (the four variants)
with its **own** y-axis — never one combined/dual-axis chart, and never a
composite "value" score.

---

## 5. Visualization type per metric

| Concept | Type | Notes |
|---|---|---|
| Agreement / disagreement state | Categorical badge (`converged`/`partial`/`diverged`/`insufficient_information`) + separate counts | No invented percentage — there is no valid denominator |
| Material changes | Change cards: before → after, source badge(s) | Small CSS/SVG arrow, not a chart |
| Decision evolution (A/B → critique → revision → convergence → synthesis) | Semantic HTML flow (ordered list + CSS grid/flex), each node/edge only rendered if the underlying stage actually ran | Ships with a hidden textual equivalent (`<table>` or definition list) for screen readers |
| Unknown vs. human-judgement vs. system-failure vs. low-confidence | Distinct icon + distinct color per category, never shared | See Section 8 |
| Cost / latency per stage | Horizontal CSS width-% bars, one row per stage, **two separate bar sets** (cost, duration) | No pie chart — ordering (stage sequence) matters more than proportion |
| Evaluation resource use (Part 4) | Small-multiple bars, one per metric, shared x-axis (variant) | No dual-axis, no composite score |
| Cost/value view (Part 5) | Aligned small multiples, one metric per mini-chart | Never combine incompatible units on one axis |
| Constraint coverage (Part 6) | Matrix: rows=constraints, columns=blind-label outputs, cells=✓/✕/? | Evaluation-only, human-scored (see Section 6) |

---

## 6. Constraint coverage — scoring options

1. **Manual human scoring (recommended for now).** Extend the existing
   `eval_reviews` table (already has `dimension`/`rating`/`comment` —
   built this session) with one review row per `(experiment, constraint)`
   pair, `dimension="constraint:<constraint text>"`, `rating` ∈
   {"addressed", "partially addressed", "not addressed", "unclear"}. No
   schema change needed, just a UI form and a query. **Human review remains
   authoritative, exactly as instructed.**
2. **Deterministic exact-evidence heuristic.** A keyword/regex presence
   check (e.g. does the output contain "budget" near the "limited budget"
   constraint). Explicitly **not recommended as authoritative** — prone to
   both false positives (the word appears without the constraint being
   genuinely addressed) and false negatives (addressed without using the
   expected words). Could be offered only as a **non-authoritative
   pre-filter** to speed up the human reviewer's scan ("this constraint's
   keywords weren't found — check carefully"), never displayed as a score
   itself.
3. **Future assisted semantic scoring.** An LLM-based grader. Explicitly
   deferred — would need its own validation study against human-scored
   agreement before being trusted for anything, and using an LLM to grade
   LLM output reintroduces exactly the "does more process actually help"
   question this whole harness exists to answer independently. Not in
   scope now.

---

## 7. Terminology / visual safeguards against overclaiming

| Must never be conflated with | Concrete UI rule |
|---|---|
| Model agreement ≠ correctness | Convergence badge always paired with the existing `evolution.material_changes`-style disclaimer already used in `convergence_summary.html`: "analytical metadata, not a verified judgement" |
| Convergence ≠ truth | Never label the convergence badge "Verified" or "Confirmed" — only the schema's own enum words |
| Material change ≠ improvement | Change cards show before/after neutrally; never a ✓/✗ or up/down arrow implying the after-state is better |
| Red-team contribution ≠ discovered factual error | Copy always says "flagged" / "raised", never "found a bug" or "caught an error" |
| High cost ≠ high quality | Cost bars use a neutral color scale (not red-to-green), and the Evaluation Dashboard never sorts variants by cost as if lower/higher were self-evidently worse/better |
| Human review score ≠ objective truth | Always labeled "Pilot human review score", one reviewer, one case — never "Quality Score" |
| Unknown ≠ model disagreement | Distinct icon (see Section 5): "?" (gray) for unknowns vs. a two-sided comparison layout for disagreements |
| System failure ≠ reasoning uncertainty | Failure uses the existing red `✗`/`status-failed` styling (already established in `presenter.STATUS_SYMBOLS`); uncertainty (an `"uncertain"` trigger source, or `change_status=="unknown"`) uses a distinct neutral/amber treatment, never red |

---

## 8. "You Decide" — design

Purpose: make the page feel like *"here is what the deliberation found,
where it changed, what remains uncertain, and what you need to judge"* —
not *"AI has decided."* Content assembled (deterministically, no LLM call)
from three already-structured fields:

- every `unresolved_disagreements[].decision_impact` — *why this
  disagreement matters for whoever acts on the answer*;
- every `human_judgement_required[].issue` (+ `why_models_cannot_resolve_it`
  as a one-line "why you, not the models");
- every `remaining_unknowns[].why_it_matters` — framed as "worth checking
  before you act."

Rendered as a plain list of **things to weigh**, never as ranked
recommendations, never phrased as "we suggest" — each item ends with *why
it's yours to decide*, not what to decide.

---

## 9. Mobile / cognitive load — exact ordering

Same Level 1–4 structure as Section 2's table, single column on narrow
viewports, each Level-2 block its own native `<details>` (keyboard/
screen-reader accessible for free, no JS needed). Level 1 never collapses.
Level 3/4 already collapse today — unchanged.

---

## 10. Technical implementation options

**Recommendation: no new dependency.** Every visual this proposal needs is
one of: a bar (CSS `width: N%`), a small flow diagram (CSS grid/flex or a
handful of inline `<svg>` lines/arrowheads), a card grid (CSS grid), or a
categorical badge (styled `<span>`). None require real charting
capability — no continuous scales, no zoom/pan, no tooltips-on-hundreds-of-
points. Semantic HTML + the existing `static/style.css` + a handful of new
CSS classes covers all of Sections 3–5.

**If a chart library were ever justified** (not now): only once the
Evaluation Dashboard has many cases × many variants and a genuine scatter
plot (human score vs. cost, dozens of points) becomes useful. Even then:
prefer a small, dependency-free approach (hand-rolled SVG scatter, a few
dozen points is trivial) before reaching for a library, given this
project's explicit "prefer no new dependency" stance (already established
this session for the language detector). If one were ever added: bundle
size and offline/privacy implications (a CDN-hosted script matters for a
locally-run tool with no telemetry), ongoing maintenance/version-pinning
burden, and accessibility (canvas-based libraries typically need extra ARIA
work SVG/HTML don't) would all need to be weighed explicitly before
choosing one — none of that is warranted at current data volumes.

---

## 11. Accessibility plan

- Every visual has a text label carrying the same information (counts,
  states spelled out, not just an icon).
- Category distinctions (Section 7/8) use icon **and** shape/position, not
  color alone.
- The decision-evolution flow diagram ships with a hidden equivalent
  (`<table>` or definition list) for screen readers — the graphical version
  is progressive enhancement, not the only source of the information.
- Every new collapsible section is a native `<details>`/`<summary>` (already
  the codebase's convention) — free keyboard navigation, no custom JS
  tab-trapping needed.
- Respect `prefers-reduced-motion` for any transition (expand/collapse
  should not animate for users who've asked not to).
- Reuse the app's existing dark-mode/light-mode token approach rather than
  hardcoding new colors — every new badge/card color needs both a light and
  dark definition.
- Every layout must hold at ~360–400px width with no horizontal scroll
  (matches this project's own existing responsive constraints elsewhere in
  the codebase).

---

## 12. Future experiment: text-only vs. visual Decision Cockpit

**Not implemented now.** Proposed as a later, dedicated study:

- **Design:** within-subjects preferred (each participant sees both a
  text-only and a cockpit version, of *different* cases, order randomized)
  to control for individual variance; real human participants, never an
  LLM judge standing in for a person.
- **Measures** (as given): time-to-understand-the-decision,
  recall-of-important-constraints, ability-to-identify-unresolved-
  disagreement, ability-to-identify-missing-information, ability-to-
  identify-what-changed-after-critique, confidence calibration, subjective
  cognitive load (e.g. a short NASA-TLX-style scale), usefulness.
- **Persistence:** reuse the `eval_reviews`-shaped mechanism, extended with
  a `study="text_vs_cockpit"` tag, so this doesn't need new infrastructure
  when it's eventually run.

---

## 13. Deliverable summary

**Files likely to change (future implementation, not now):**

- `src/llm_deliberation/web/templates/partials/result.html` — restructure
  to insert the Level-1/Level-2 cockpit blocks between the existing lead
  paragraph and the full-answer details.
- `src/llm_deliberation/web/templates/partials/convergence_summary.html` —
  likely absorbed/expanded into the new cockpit blocks.
- `src/llm_deliberation/web/presenter.py` — new pure functions: e.g.
  `build_decision_cockpit(record)`, `stage_duration_seconds(stage)` (the
  one genuinely new **B** computation), grouping helpers for material
  changes by candidate/trigger.
- `src/llm_deliberation/web/i18n.py` — new EN/ET keys for every new
  heading/label.
- `src/llm_deliberation/web/static/style.css` — new classes for cards, the
  flow diagram, bars, badges (light + dark).
- `src/llm_deliberation/evaluation/reporting.py` — extend with an
  HTML-rendering path (the text reports already built this session stay
  as-is for CLI/scripting use).
- New: `src/llm_deliberation/web/templates/eval_dashboard.html` +a small
  new route in `web/app.py` — evaluation currently has **no web surface at
  all** (CLI/Python-only per this session's Phase 2/3 build), so the
  Evaluation Dashboard is new surface area, not a restructure.
- Possibly a new `eval_constraint_scores` table, or reuse `eval_reviews`
  with a structured `dimension` naming convention (Section 6) — a small
  decision to make at implementation time, not now.
- No production schema changes required for the cockpit itself — every
  field it needs is already persisted.

**Recommended phased implementation order (small phases):**

1. Decision Snapshot + at-a-glance stat strip (Level 1) — all Category A
   data, zero new dependencies, highest UX return for lowest risk.
2. What Changed / Where Disagree / What We Don't Know / You Decide (Level
   2, still all Category A data).
3. Red-team block (careful conditional rendering) + a terminology/
   semantic-distinction pass across the whole page + an accessibility pass.
4. Evaluation Dashboard — resource small-multiples only (Category A/B
   data, no human-review dependency yet).
5. Constraint-coverage matrix (manual scoring UI) + wiring real human
   review scores into the Evaluation Dashboard once they exist — starting
   with Pilot Case 1's own scores, once you've finished blind-scoring them.
6. (Future, separate task) the text-only-vs-cockpit user study.

**Risks / misleading-visualization traps:**

- Reading "converged" as proof of correctness rather than model agreement.
- Reading material-change *count* as a quality proxy ("more changes =
  better process" is not established by anything in this codebase).
- Reading a red-team trigger tally as "bugs found" rather than "flagged and
  attributed by a model."
- A cost/duration bar's visual size implying "bigger = better" merely
  through salience.
- Rendering a *skipped* stage's section as empty (implying "nothing to
  report") rather than explicitly "not run this time" — these are different
  claims and must look different.
- An agreement badge styled like a percentage, inviting a statistical
  reading it can't support.
- Trusting the convergence analyst's own trigger attribution as verified
  causal fact rather than one model's fallible interpretation (the schema
  itself allows `"uncertain"` for exactly this reason).
- A single pilot reviewer's score being generalized as "the" quality
  verdict for an architecture rather than one data point on one case.

---

Nothing in this proposal has been implemented. No files listed above have
been created or modified. The private blind mapping for PILOT EVALUATION
CASE 1 was not opened. No provider calls were made.
