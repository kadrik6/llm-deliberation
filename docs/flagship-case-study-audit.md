# FLAGSHIP CASE STUDY AUDIT

*(Positioning/case-study audit only. No files modified, no code written, no provider calls, no Case 3 semantic inspection, no push. Grounded in `README.md`, `docs/verification-matrix.md`, `docs/design/decision-cockpit-proposal.md`, `docs/why-deliberation.md`, `docs/architecture.md`, the ADRs, `evals/README.md`, the `src/llm_deliberation/evaluation/` package, current templates, and `git log`.)*

---

## 1. Recommended one-line positioning

**Five candidates:**

1. "A single AI answer usually hides its own disagreement and uncertainty. This tool doesn't: it shows where independent reasoning agreed, where it changed, what's still unresolved, and what remains a human call."
2. "A decision-support tool that turns one confident AI answer into a transparent one — showing where reasoning agreed, where it didn't, what changed along the way, and what's genuinely still up to you."
3. "For decisions that matter, one polished AI answer isn't enough — this tool exposes the disagreement, the changed reasoning, and the open questions a single response would have quietly hidden."
4. "An AI-assisted decision-support system built on one idea: a recommendation you can't see inside isn't one you can fully trust — so this one shows its disagreements, its revisions, and what still needs a human."
5. "A tool for hard, ambiguous decisions that makes the reasoning process itself inspectable — not just the conclusion, but where it disagreed, what changed, what's uncertain, and what's left for a person to judge."

**Recommended: #1.** It states the familiar baseline (a single AI answer) in one clause, the contrast in one clause, and then names exactly the four things the system actually, verifiably delivers — agreement state, change, unresolved disagreement, human judgment — each traceable to a real, working piece of the UI (Decision Snapshot, material-change cards, disagreement cards, "You Decide"). No jargon, no product buzzword, no architecture noun, and it reads the same to a policy analyst and a startup PM.

---

## 2. Core problem

**In plain language:** A single AI-generated answer, however fluent, is one pass of reasoning from one model, shaped by one training run and one set of unstated assumptions. Nothing about how confidently it's phrased tells you whether a different well-informed reasoner would object, which of its claims are actually load-bearing, or what it simply doesn't know. The polish of the output is not evidence of its robustness — and from the outside, a reader has no way to tell the difference.

**Who has this problem:** anyone who has to put their name behind a non-trivial written recommendation that used AI-assisted analysis as an input — a policy analyst drafting an option paper, a consultant preparing a client brief, a product or engineering lead facing an ambiguous build/delay/migrate call, a small team without a second in-house expert to sanity-check a decision. It is not the person looking up a fact, drafting boilerplate, or asking a trivia question — a single model is fine for that.

**What kind of decisions it matters for:** ones with real tradeoffs, incomplete information, and a cost to getting it wrong that exceeds the cost of a second, structured look — "should we migrate this system this quarter," "which vendor," "ship now or wait a quarter." Not decisions where correctness is cheaply checkable.

**Why "just ask again" isn't the same:** re-prompting the same or a different model produces another single, unstructured pass — same failure mode, no persisted record of where two independent reasoners actually differed, no mechanism that forces one to notice the other's blind spot, and no way to tell "this genuinely holds up better" from "this just sounds different." A human doing that comparison manually still has to do all the structuring work by hand, every time, with nothing saved.

**Why the human still has to decide:** the system deliberately never computes a confidence score, a correctness score, or a winner. Two models agreeing is evidence they agree — not evidence they're right, since they can share the same blind spot (this is the literal reasoning behind the red-team design, [ADR 002](decisions/002-red-team-not-voting.md): "majority voting rewards agreement, not correctness"). The system's job, as built, is to make the remaining judgment calls visible, not to make them.

---

## 3. Target audience

Two distinct audiences, worth naming separately:

- **The system's intended user:** someone preparing a consequential written recommendation under uncertainty who wants a second, structured opinion with visible disagreement — policy/government analysts, strategy/management consultants, product or engineering leads facing an ambiguous call, small teams without a second expert on hand.
- **The case-study page's reader:** a technically credible outsider evaluating this as a portfolio artifact — an engineering hiring manager, a product-minded technical lead, a recruiter with enough fluency to judge substance, or a potential collaborator. Needs to understand the value without LLM specialization, but the page must survive scrutiny from someone who has it.

---

## 4. Strongest differentiators

Ranked by value to an external technical reviewer — never ranking the LLM providers/architectures themselves:

1. **The verification matrix itself, including an honestly-reported external blocker.** `docs/verification-matrix.md` explicitly separates offline/live/historical-live/evaluation/blocked evidence for ~55 capability rows, and documents a real live incident (a Gemini API access restriction, HTTP 403, hit twice) transparently rather than hiding or working around it. Almost no side project audits its own claims this rigorously — this is the single hardest-to-fake signal in the repo.
2. **A completed blind evaluation with a locked, honestly-reported null result.** Pilot Case 1 ran four architectures on the same case, scored blind, mapping revealed only after scores were locked to a file — and the result was a ceiling effect (all four scored 15–16/16), reported as exactly that, not spun. Genuine pre-registered experimental discipline is rare in this space.
3. **Structured, schema-validated convergence/change analysis, not free-text summarization.** A dedicated stage compares before/after positions and emits a validated schema — material vs. non-material vs. unknown change, per-change trigger attribution, unresolved disagreements with why they're unresolved — never a recommendation. This is the backbone everything else (Decision Cockpit, the evaluation reports, "You Decide") derives from.
4. **Durable, resumable, cost-tracked execution with a real safety guard.** Per-stage SQLite persistence, typed retry/resume/skip, and a completion-reserve budget guard that auto-protects required stages from optional overspend before it's asked to — production-style reliability engineering applied to an experimental pipeline, not a happy-path demo.
5. **Terminology safeguards enforced by tests, not just prose.** Rendered output is tested to never claim "found N errors," a confidence percentage, or an agreement score — the project holds its own UI copy to the same evidentiary bar as its own docs.
6. **Explicit architectural reasoning with written rationale (ADRs).** Independent generation strictly before cross-exposure (anchoring), red-team-as-critic-not-voter (majority voting rewards agreement, not correctness) — both are non-obvious, deliberate design choices, not defaults.
7. **A live bug caught and fixed with a bounded mechanism.** A live canary caught a model silently answering in the wrong working language; the fix (deterministic language detection + one bounded corrective retry, never an unbounded loop) shows real production-realistic debugging, not just unit-test coverage.
8. **The Decision Cockpit UI**, built only after an explicit audit of what data is already structured vs. what would require fabricated inference — real, but ranked lower here since it's presentation on top of #3, and its own UX value is explicitly, honestly unproven.

---

## 5. 60–90 second first-impression flow

| Window | What the visitor sees | Exact message | Do NOT show yet |
|---|---|---|---|
| **0–10s** | Text only: one-line positioning + one supporting sentence. No screenshot yet. | "A single AI answer hides its own uncertainty. This shows it." | Any screenshot, any jargon, any model name. |
| **10–25s** | One screenshot: the **Decision Snapshot** from a real historical run (not Case 3) — convergence badge, 4 stat chips, quality indicator, red-team status line. | "Here's the state of the deliberation at a glance: agreement level, what changed, what's unresolved, what needs you." | Full answer text, cost numbers, technical trace, provider/model names. |
| **25–45s** | One material-change card (before → after, with trigger) OR one unresolved-disagreement card. | "A specific position that changed during deliberation — attributed to what triggered it, not just asserted." | The full change list, the full disagreement list. |
| **45–60s** | The **"You Decide"** section, compact. | "Everything that still needs a human decision, collected in one place — not buried in five pages." | Cost/trace detail. |
| **60–75s** | The cost/duration line + the collapsed **operational trace** summary (stages, provider attempts, retries — still collapsed). | "Every claim has a receipt: cost, retries, which provider actually produced each piece — persisted, not printed to a terminal and lost." | Raw JSON, expanded per-stage prose. |
| **75–90s** | A compact "what's actually been measured" callout — Pilot Case 1's honest ceiling-effect finding. | "And here's what's actually been measured, including a result that didn't clearly favor the more elaborate setup — reported honestly, not hidden." | Case 3 detail, deep methodology — one line only: "a harder evaluation case is in progress." |

---

## 6. Recommended demo story

**Choice: C — partly used, with a non-evaluation historical run carrying the 60–90s visual story, and Pilot Case 1 promoted explicitly into its own Evaluation section.**

Reasoning:
- The 90-second demo's job is to show *what the system reveals* (disagreement, change, human-judgment, traceability). A real canary run (e.g. **Canary 1**, which has genuine material changes, a real disagreement, an actual Gemini fallback event, and full Decision Cockpit rendering) does that job just as well as Pilot Case 1's FULL run — without dragging in variant-comparison methodology a first-time viewer doesn't need yet.
- Pilot Case 1's actual finding is nuanced ("all four architectures scored the same on this case — extra deliberation didn't show a clear win") and needs room to not be misread either way: skimmed carelessly it can look like "it doesn't work," compressed carelessly it risks being spun into "look how good FULL is." Neither is true. It needs the Evaluation section's space, not a 15-second beat.
- Using Pilot Case 1 as the Evaluation section's centerpiece is exactly right for what it actually is: real, methodologically serious (blind, locked scores, seeded mapping) evidence — and an honest null result is *more* credible portfolio material than a rigged "and it wins!" result would be. It directly proves differentiator #2.
- **Do not** use Pilot Case 1 to imply deliberation improves answers — it does not show that, and the case study must say so explicitly, in the Evaluation section itself.

---

## 7. Case-study page structure

| # | Section | Purpose (one sentence) | Content/evidence | Disclosure |
|---|---|---|---|---|
| 1 | **Hero** | Orient in 5 seconds. | One-line positioning + one supporting sentence; hero screenshot once captured. | Immediate |
| 2 | **The problem** | Explain why a single AI answer is sometimes insufficient. | Section 2's plain-language problem statement. | Immediate |
| 3 | **60-second demo** | Let the visitor *see* the value without reading prose. | The section 5 sequence, screenshots + short captions. | Immediate |
| 4 | **How it works** | Explain the process at two depths. | Level A (5–6 plain steps) immediate; Level B (real stage graph, providers) behind a "see the technical pipeline" expand. | A immediate, B progressive |
| 5 | **Decision Cockpit** | Show the actual UI artifact that embodies the positioning. | Decision Snapshot / You Decide / red-team status screenshots, link to the design doc. | Progressive |
| 6 | **Reliability / engineering** | Prove this isn't a happy-path demo. | Retry/resume, durable persistence, typed failure reasons, completion-reserve, test suite size/CI. | Progressive |
| 7 | **Evaluation** | Show measured, not assumed, evidence. | Pilot Case 1 full write-up (methodology + honest ceiling-effect result); link to verification matrix; one-line Case 3 status (in progress, externally blocked, no result). | Progressive |
| 8 | **Limitations / what is not proven** | Keep the honesty visible, not buried. | The verification matrix's "what we must NOT claim yet" list, in plain language. | Progressive, but placed **immediately after Evaluation** (see note) |
| 9 | **What I learned** | Personal reflection, credibility through specificity. | E.g. the live language-contract bug, the discipline of building a blind harness, the value of an explicit evidence taxonomy. | Progressive |
| 10 | **Technical architecture** | Serve the reader who wants the real stage graph, schema, ADRs. | Full pipeline diagram, provider layer, ADR links. | Deep/progressive |
| 11 | **Run locally** | Let a technical reviewer actually try it. | Quick start, kept close to today's README content. | Deep/progressive |

**One deviation from the given skeleton:** Limitations is moved to sit directly after Evaluation (not after "What I learned"), because it's evidence-adjacent — a reader who just saw Pilot Case 1's result should see the honest boundary of that result in the same breath, not several sections later.

---

## 8. Engineering → professional-signal mapping

| Category | Implementation | Professional signal |
|---|---|---|
| **Software engineering** | Shared service layer behind CLI + web UI, typed provider adapters, repository pattern over SQLite | Designs one core service for multiple interfaces instead of duplicating logic per surface |
| | Durable per-stage persistence + typed retry/resume/skip | Designs recoverable workflows; treats partial failure as first-class, not an afterthought |
| | 648+ deterministic offline tests, every provider call faked, CI on every push with zero live API dependency | Builds a fully offline, deterministic test suite for a system whose core behavior depends on non-deterministic external APIs |
| **AI systems** | Independent generation strictly before cross-exposure (ADR 001) | Understands and designs around a subtle failure mode (anchoring) a naive pipeline would reintroduce |
| | Red-team instructed to flag shared blind spots, never vote/rank (ADR 002) | Recognizes majority voting rewards agreement, not correctness — designs against the specific failure, not the popular pattern |
| | Native structured-output schema validation for convergence analysis | Understands the difference between "probably valid JSON" and "schema-guaranteed JSON," and uses the stronger guarantee |
| **Data / reliability** | Typed `failure_reason` taxonomy, transient vs. non-transient classified at the HTTP-code level | Builds precise, machine-actionable error taxonomies instead of one generic failure bucket |
| | Completion-reserve budget guard, admission-time, per wave | Designs cost safety margins in before being asked to, not after a bill surprise |
| | Additive-only historical DB migrations with dedicated compatibility tests | Treats schema evolution as an ongoing reliability concern, not a one-off script |
| **Evaluation** | Verification matrix distinguishing offline/live/historical/evaluation/blocked, with a proven no-double-counting rule | Distinguishes evidence from assumption and holds their own claims to the bar they'd expect from a review |
| | Blind harness: frozen cases, seeded mapping, scores locked before unblinding, AST/runtime-verified data separation | Designs measurable, pre-registered acceptance criteria and actively defends against their own confirmation bias |
| **Product thinking** | Decision Cockpit built only after an explicit A/B/C/D data-availability audit; refused to build Category D | Resists shipping a plausible-looking feature the data doesn't actually support |
| | Profiles (Economy/Balanced/Max) instead of raw model IDs | Translates a technical choice into the decision the user actually has |
| **Project management** | 30 commits as clear, single-purpose, reviewable milestones | Sequences open-ended work into shippable increments, not one large undifferentiated change |
| | Explicit phase-gating ("Phase 1 complete — design audit before Phase 2") | Manages scope deliberately; treats "stop and check" as part of the process |
| **Decision-support design** | "You Decide" never ranks/scores/recommends, fixed non-inferred order | Designs around what a decision-maker needs to weigh, not what's easy to compute |
| | Seven distinct, non-collapsible red-team outcome states | Refuses to collapse operationally different situations into one ambiguous status |
| **Responsible use / auditability** | Every claim traceable to a persisted artifact; full trace one click away | Builds for auditability by default, not bolted on |
| | Rendered-output tests scanning for forbidden overclaiming phrases | Treats responsible-language discipline as testable, not a style guideline nobody checks |
| | A maintained, public "what we cannot claim yet" list alongside the code | Practices intellectual honesty proactively, not only when challenged |

---

## 9. Screenshot plan (6 max, all from non-Case-3 runs)

| # | Screen/state | Must show | Crop out | Caption | Supports |
|---|---|---|---|---|---|
| 1 | Decision Snapshot, top of a completed result page (Canary 1 or Pilot 1 FULL) | Convergence badge, 4 stat chips, quality indicator, red-team status line | Full answer text below, browser chrome | "The first thing you see: agreement state, what changed, what's unresolved, whether anything needs your judgment — before reading a single sentence of the answer." | #2 (visible uncertainty), #5 (what remains uncertain) |
| 2 | One material-change card ("What changed?") | Candidate label, Before/After, trigger line, material-change label | The rest of the list, run metadata | "A specific position that changed during deliberation — and what appears to have caused it, disclosed as an attribution, not a proven fact." | #3 (what this does differently) |
| 3 | One unresolved-disagreement card | Topic, both positions side by side, why-unresolved, decision-impact | Unrelated sections | "Where independent analysis genuinely didn't converge — shown as a live disagreement, not smoothed over." | #2 (why a single answer is insufficient) |
| 4 | "You Decide" section, 2–3 items across all three categories | Heading, intro line, visible category labels ("Disagreement:", "Needs your judgement:", "Open question:") | The full detail sections above it | "Everything that still requires a human decision, collected in one place — not buried across five sections." | #5, human-in-the-loop positioning |
| 5 | Red-team status + collapsed operational-trace footer | Red-team status line (fallback-disclosure state from Canary 1 is a strong real reliability moment), collapsed trace summary line | Expanded trace content | "Cost, retries, and provenance stay visible but out of the way — expandable, never hidden." | #4/#6 (traceability, engineering) |
| 6 | A clean, rendered excerpt of the verification matrix (not a terminal) | Legend + 1–2 real rows (e.g. Pilot Case 1's locked scores + ceiling-effect note) | Any Case-3-specific row, dense unrelated rows | "The project keeps its own evidence honest — including a null result: no clear quality difference across four architectures on this pilot case." | #4 (what's verified), differentiators #1/#2 |

No terminal/pytest screenshots; a "648 tests, CI green" fact is better as a small text/badge line elsewhere than as an image.

---

## 10. 60–90 second demo script

> "So here's a project I built around something that bugged me about using AI for real decisions: you ask one model, it gives you one confident-sounding answer, and you have no way to tell from the outside whether that confidence is earned.
>
> What this does instead: take a hard question — say, whether to migrate a system this quarter — and run it through two models independently, without letting either see the other's answer first. Each one critiques the other's reasoning, an optional third model checks for blind spots they might share, and both positions get revised.
>
> The interesting part isn't the final answer — it's what the tool shows you along the way. This is the decision snapshot: how much the two analyses actually agreed, what positions changed and why, what's still genuinely disagreed on. You can see the exact claim that changed and what triggered it — and everything that's still a human call gets collected in one place instead of buried in five pages.
>
> This doesn't assume more models automatically means a better answer. I built a blind evaluation harness to actually test that — scored four setups on the same case without knowing which was which until after scoring — and on that case, the simplest setup scored the same as the most elaborate one. That's not a failure, that's the honest result, and it's the kind of thing you only find out by measuring it instead of assuming your own pipeline is better because it's more complicated.
>
> So the real deliverable isn't 'AI that's smarter.' It's a system that makes its own reasoning, its disagreements, and its limits visible enough that a human can actually decide how much to trust it."

~230 words, natural pace ≈ 80–90 seconds; trims cleanly to ~60s by cutting the third paragraph's last sentence and shortening the close.

---

## 11. Safe claims

- Runs a real, working multi-stage deliberation pipeline (independent analysis → cross-critique → optional red-team → revision → structured convergence analysis → synthesis), offline-tested (648+ deterministic tests) and live-verified against real OpenAI/Anthropic/Gemini APIs across multiple runs.
- Every stage's cost, tokens, provider, retries, and fallback behavior is persisted and auditable, not just the final answer.
- A real transient provider failure (Gemini HTTP 500) was recovered live via a disclosed model fallback; a real non-transient failure (HTTP 403) was correctly classified and failed cleanly, never masked or blindly retried.
- The system structurally distinguishes agreement from disagreement, material from non-material change, and a resolved point from an open human-judgment item — enforced in schema and tests, not just prose.
- A blind, pre-registered evaluation compared four architectures on one case, with scores locked before the labeling was revealed.
- On that one pilot case, all four architectures scored within a narrow band (15–16/16) — an honestly-reported ceiling effect, not a demonstrated advantage for the more elaborate setup.
- The project maintains and tests its own claims against a documented evidence taxonomy (offline/live/historical/evaluation/blocked), including a maintained list of what is not yet proven.
- Retry, resume, and skip are implemented and offline-tested; partial failure never silently discards already-completed, already-paid-for work.
- A second, harder evaluation case is designed and partially executed live (three of four architectures completed); the fourth is currently blocked by an external provider access restriction unrelated to the code.

## 12. Claims to avoid

| Do not claim | Precise alternative |
|---|---|
| "More accurate" / "more trustworthy" than a single model | "No accuracy comparison against a single-model baseline has been run yet." |
| "Better decisions" result from using this | "Designed to make the decision more inspectable; whether that changes decision quality has not been tested." |
| "Red-team review improves quality" | "Red-team's contribution to specific revisions is structurally tracked; no evidence connects it to a human-perceived quality improvement." |
| "FULL is superior" | "On the one pilot case measured, the full pipeline did not score higher than the simplest baseline — a ceiling effect, not evidence either way." |
| "Convergence means the answer is correct/true" | "Convergence means the models' revised positions agreed with each other, not that either is right." |
| "The Decision Cockpit improves comprehension / decisions" | "Designed for faster comprehension; not yet tested with real users — an open hypothesis, stated as such." |
| "Production-grade" | "Single-user, single-process, locally-run; explicitly not hardened for multi-user or networked deployment." |
| "Proven at scale" / "enterprise-ready" | Don't say — no evidence exists either way. |
| Anything implying Case 3 shows deliberation helps on harder problems | "A harder evaluation case is in progress; the full-pipeline variant is currently blocked by an external provider issue, so no comparison exists yet." |

---

## 13. README audit

**What should stay:** the opening intellectual-honesty framing ("this is a hypothesis, not a claim of proven results") — genuinely rare and valuable, keep the spirit; the ADR-linked "why this design" reasoning; the Privacy & security limitations section; the quick-start instructions (repositioned, not removed).

**Too technical too early:** the full ASCII architecture diagram and deliberation-flow diagram appear immediately after the opening disclaimer, before any plain-language "why would I care" framing — this is Level B content placed where Level A belongs. The detailed WSL2-specific setup walkthrough is excellent but competes for attention with positioning this early in the file.

**Missing — the biggest finding:**
- **No screenshots exist at all** ("Not yet included" placeholder), despite a fully functional, real Decision Cockpit UI. For a UI-centric project this is the single biggest gap between what's built and what's shown.
- **No mention anywhere of the evaluation harness, Pilot Case 1's completed result, or `docs/verification-matrix.md`.** The README's own "Evaluation plan" section still says "Not yet run" — this is now factually stale. The harness exists, has run twice, and produced a real, honestly-reported result. This is currently the project's strongest, most invisible differentiator.
- **No mention of the Decision Cockpit UI** — the README's "Decision evolution" section describes the underlying data, never the result-page UI that presents it.
- **No "what's actually been measured" summary** anywhere a reader can find without already knowing to look.

**Duplicated:** the evaluation intent is described in two places — the README's "Evaluation plan" section and `evals/README.md` — both stale, both saying "not yet run," neither cross-linked to the real, current status in `docs/verification-matrix.md`.

**What should move deeper:** the full WSL2/Windows setup walkthrough (keep a 3-line quick start, move the rest to a dedicated doc or a collapsed section); the full pipeline/architecture ASCII diagrams (keep a 5–6 step plain version, move the technical one to `docs/architecture.md`, already mostly the case).

**What would confuse a hiring manager:** leading with a hypothesis/uncertainty framing before establishing why the reader should care at all — the caveats are excellent but are currently sequenced *before* any statement of value, not after it; the detailed WSL2 setup instructions sit above the strongest content (Decision evolution, Red-team) in reading order; the total absence of any visual evidence for a UI-centric project.

**Where it undersells the project:** the evaluation harness and its real, completed, honest result; the Decision Cockpit; the verification-matrix discipline; and the git history itself (30 well-scoped, incremental commits — a real, checkable signal) — none of these are visible in the README today, and together they are the strongest material in the repo.

---

## 14. Proposed new README information architecture

1. **Title + one-line positioning** (section 1's recommendation) — replaces the current opening hypothesis sentence as the very first line.
2. **60-second overview (new)** — condensed textual version of section 5, with a hero screenshot once available.
3. **Honest framing: what this is and isn't** — keep the existing block's spirit, correct the now-stale "evaluation hasn't run" claim.
4. **How it works (Level A, plain language, 5–6 steps)** — no diagram, no model names foregrounded.
5. **What's been measured (new)** — short paragraph + links to `docs/verification-matrix.md` and Pilot Case 1's result (ceiling effect, stated honestly); one line on Case 3's in-progress/blocked status.
6. **Decision Cockpit (new)** — one paragraph + screenshot once captured, linking to the design doc.
7. **Screenshots** — replace the empty placeholder.
8. **Architecture (Level B, technical)** — current diagrams, moved here, linking to `docs/architecture.md`.
9. **Why this design** — keep, largely unchanged.
10. **Reliability / cost / retry / resume** — keep, condense/cross-link where the current separate sections overlap.
11. **Privacy & security limitations** — keep as-is.
12. **Project status / roadmap** — correct: the evaluation harness is no longer "roadmap," it's built and used.
13. **Quick start / run locally** — keep, moved below all positioning/evidence content.
14. **Development / CI / License** — keep at the end.

(Flagged, not actioned now: `evals/README.md` needs its own correction pass — it directly contradicts the real, current state of the evaluation harness.)

---

## 15. The single highest-impact packaging change to do first

**Rewrite the README's opening — title, one-line positioning, and the "what this is/isn't" framing — to correct the stale "no evaluation has run" claim and lead with the plain-language problem statement plus a pointer to `docs/verification-matrix.md` and Pilot Case 1's real, honestly-reported result.**

This beats capturing screenshots or building a full case-study page as the *first* move because: it's the cheapest possible change (text-only editing of existing files, no new tooling); it fixes an active factual inaccuracy that a technical reviewer could catch and discount the whole project for (the README claims the central hypothesis is untested while a real evaluation harness and a completed, real experiment already exist in the repo); it's the very first thing every single visitor reads, so nothing else matters if the opening loses them in the first 10 seconds; and screenshots, while valuable, are additive polish, while the current opening is actively misleading about the project's real state — a correctness problem, not just a presentation gap.

**Stopping here — no files modified other than this audit document, no implementation started, nothing pushed, nothing committed.**
