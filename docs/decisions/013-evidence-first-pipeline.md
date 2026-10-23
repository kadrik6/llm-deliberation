# ADR 013: Evidence-first pipeline -- facts from tools, judgment from models

Status: Proposed (design only -- no code). Builds on ADR 009 (Stage 0) and
ADR 012 (fact-check stage, `--context` first); does not replace either.
Each phase below ships only after the previous one passes its evaluation
gate.

## Context

Two runs and two manual experiments (21-22 Oct 2026) on the same
question -- "Millised on parimad võimalused Eesti riigieelarve tasakaalu
saamiseks?" -- point at one problem.

- Run `22ad158a3b2d` (economy profile, no tools), graded blind by a third
  model at roughly 5/10. Cross-critique improved the reasoning (it
  separated "balanced budget" from "deficit under 3%", withdrew an
  unsupported structural-deficit diagnosis), but factual errors went
  through, and the critic introduced some of its own. The grader's own
  web check (not independently re-verified here) flagged: a claim that
  land values had not been reassessed since the early 2000s (it reported
  a 2022 reassessment in use from 2024), a planned tax described as
  already in force, and an excessive-deficit-procedure premise nobody
  checked. A wrong
  correction delivered as critique ended up *more* credible than the
  original claim.
- Manual run of the same flow in the chat apps (ChatGPT and Claude, both
  with web search; independent answer -> cross-critique -> revision),
  graded blind by Gemini with search: Claude alone 8.5, Claude after
  deliberation 8.0, ChatGPT alone 7.0, ChatGPT after deliberation 7.5.
  Deliberation did not clearly beat a single model. The highest
  factual-accuracy score (9) went to the output that turned into a
  critique of the other answer rather than a rewritten answer.
- The judge rewarded confident euro figures over honest uncertainty: the
  most careful answer ranked last, while the top answer's figures were
  largely self-labelled "unverified estimates".

Diagnosis -- why several models do not yet beat one:

1. **Facts come from model memory.** No stage can look anything up. Models
   trained on overlapping data share blind spots (Estonian 2024-2026 tax
   changes), so errors are correlated, and debate makes a shared error
   more persuasive, not less.
2. **Critique wins by persuasion, not evidence.** A critique is free text;
   a revision may adopt any correction that sounds right. Nothing requires
   a factual correction to carry a source.
3. **Every model does the same job.** Both write a full answer, so their
   diversity is spent on wording. Nobody is responsible for data.
4. **There is no data step.** The question needs numbers (baseline
   deficit, net yearly effect of each measure). In CRISP-DM terms the
   pipeline goes from business understanding straight to modelling.
5. **Synthesis optimises for agreement**, and the evaluation rubric
   rewards confidence.

## Decision

Restructure the pipeline around one rule: **facts come from tools and
sources; models contribute reasoning and judgment.** Diversity between
models is valuable for interpretation, trade-offs and catching reasoning
errors -- not as a substitute for looking facts up.

Target pipeline:

| # | Stage | Role | New? |
|---|---|---|---|
| 0 | `question_framing` | What is being decided, what data it needs | ADR 009 |
| 1 | `evidence_gathering` | Builds a shared **evidence pack** | new |
| 2 | `analysis_a` / `analysis_b` | Independent answers from the same pack | changed |
| 3 | `critique_*` | Objections, each typed (see below) | changed |
| 4 | `revision_*` | May adopt a factual correction only with evidence | changed |
| 5 | `convergence_analysis` | As today (ADR 006) | -- |
| 6 | `claim_verification` | Every factual claim gets a verdict | ADR 012, generalised |
| 7 | `synthesis` | Built from verified claims only | changed |

### The evidence pack

A structured, persisted list produced once per run, before any analysis.
Each item: `id` (E1, E2, ...), the fact as one sentence, source title and
URL or document name, verbatim quote, source date, retrieval time. Plus a
`gaps` list: data the framing said is needed but nobody found.

Sources, in order of preference:
- `--context` documents the user supplies (ADR 012 Option B; ships first).
- Provider web search, used **only inside `evidence_gathering`**, with a
  hard cap on searches per run (ADR 012 Option A, moved into a single
  stage). Keeping search in one stage keeps its cost and its test fake in
  one place, instead of spreading per-claim searches across the pipeline.

Both analysts receive the same pack. ADR 001's independence still holds
for reasoning: the analysts never see each other's answer before
critique. They share facts, not conclusions.

### Claim discipline in analysis, critique and revision

- Analysis: every factual claim cites an evidence id (`[E3]`) or is
  marked `[unsourced]`. An analyst may add an unsourced claim; it is never
  silently treated as fact downstream.
- Critique: each objection is typed **factual** (must cite an evidence id
  or a new source with a quote), **reasoning** (logic, conflated
  concepts, unjustified ranking) or **emphasis** (a legitimate difference
  in priorities, not an error). A factual objection without a source is
  downgraded to a *question*.
- Revision: may adopt a factual correction only if it carries evidence;
  must list what it adopted and what it rejected, and why.

### Verification and synthesis

`claim_verification` (ADR 012's `fact_check`, widened) checks every
factual claim in the final revisions **and every factual correction the
critics made** against the pack: `supported` / `contradicted` /
`unverified`, with the evidence id. Checking the critics matters as much
as checking the analysts: run `22ad158a3b2d`'s worst errors were in
critique.

`synthesis` may state as fact only `supported` claims, shows `unverified`
ones as such, and ranks options only where it has a quantified effect.
Where the pack has no baseline or effect size, it says what data would be
needed instead of recommending "start here". Remaining disagreements are
reported as trade-offs (emphasis), not as one side being wrong.

### A cheaper mode: answer + verifier

Supported as a profile (working name `verify`): one model answers with
the evidence pack, a second model runs `claim_verification` on it and
reports errors with sources; no cross-critique or revision. About 3 calls
instead of 8-10. The manual experiment suggests most of the measurable
value is error-finding; this mode tests that directly and is the baseline
the full pipeline has to beat.

## Rollout, each phase gated on evaluation

1. **Prompt-only discipline** (no new stages): claim tagging,
   typed critiques, evidence-gated revisions, synthesis rules. Cheapest
   way to find out how much the persuasion problem alone costs.
   *Implemented in `prompts.py`.* With no evidence pack yet, claims carry
   labels instead of `[E#]` ids: `[source: ...]`, `[established]`,
   `[unverified]` (anything from the last two years), `[estimate]`,
   `[assumption]`. A factual objection without a source becomes a
   question to verify; a revision keeps an unsourced dispute visible as
   `[unverified, disputed]`; convergence analysis no longer lists
   differences of emphasis as unresolved disagreements; Stage 0's
   interview also asks for scope, ranking criteria and trusted sources.
   `python -m llm_deliberation.manual_prompts` exports these prompts for a
   manual run in chat apps, so the phase can be evaluated before any paid
   run.

   *First manual evaluation (22.10.2026, one question, one blind
   grader).* A single model scored highest (8.5 and 8.0); the two
   deliberated final answers scored 6.8 and 6.5. They scored well on
   honesty about uncertainty (9/10) but low on answering the question
   (5 and 4/10), because the original rule "rank options only where each
   is sized" made the synthesis drop the candidates' sized estimates and
   return a checklist. The rule now asks for a ranked list with labelled
   rough ranges, tells critics to challenge an estimate's basis rather
   than ask for its removal, and tells revisions to keep sized estimates.
   The grader also missed an under-stated GDP figure, so one grader is
   not enough for the phase gate.

   *Second manual evaluation (23.10.2026, same question, three graders:
   two Gemini chats and Perplexity).* With the ranking fix, the
   deliberated answers led on two graders, but Perplexity, the only grader
   that checked facts against sources, put ChatGPT's own first answer
   (web search on, 9/10) above the same model's deliberated final answer
   (8/10). The final answer had merged two candidates' ranges into one
   wide range and lost arithmetic coherence, and an `[unverified]` debt
   figure carried through to the end was wrong. The gain came from the
   prompt rules and web search, not from deliberation. The synthesis now
   keeps the better-based estimate instead of merging ranges, and
   `manual_prompts --pattern verify` exports the phase 4 candidate (answer,
   source-backed fact-check, correction) for the next manual evaluation.
2. **`--context` evidence pack + `claim_verification`** (ADR 012 Option B).
3. **`evidence_gathering` with capped web search** (ADR 012 Option A),
   including a priced, verified per-search fee in `RunBudgetGuard`.
4. **`verify` profile**, if phase 2 or 3 shows error-finding is where the
   value is.

## How we plan to evaluate it

The bar is not "better than the current pipeline" but **better than a
single strong model with web search on the same question.** If the
pipeline cannot beat that, extra models do not pay for themselves.

- Fixed question set (at least 5, mixing Estonian public-sector and
  business questions), same sources for every variant.
- Variants: single model + search; `verify` mode; full pipeline.
- Blind judge with web search, a different provider from both analysts,
  answers anonymised and order shuffled.
- Metrics, per answer: factual errors confirmed by the judge; **false
  corrections** (a critic or verifier calling a true claim wrong -- the
  failure this ADR exists to fix); share of claims left unsourced; whether
  options are ranked with quantified effects; usefulness to a decision
  maker (1-10).
- Rubric fix: confident unsourced figures are scored as a fault, not a
  strength, so the judge cannot reward the behaviour we are removing.

## Why

- More or bigger models do not fix point 1: a model without access to
  2024-2026 Estonian facts makes the same errors at any size.
- More debate rounds amplify points 1 and 2.
- Separating facts from judgment uses each part for what it is good at,
  and makes every factual statement auditable -- the main thing this tool
  offers that a chat app does not.

## Trade-offs

- More stages and more structure: longer runs, more prompt surface, more
  ways for structured output to fail parsing.
- Phase 1 depends on models following citation rules; compliance has to
  be measured (claim-tag coverage), not assumed.
- Quality is capped by the evidence pack. A wrong source in the pack is
  faithfully "supported" (the ADR 012 failure mode, unchanged).
- Web search (phase 3) brings latency, a per-call fee, and results that
  are not reproducible between runs; the pack is persisted so a run can be
  audited later even if the web changed.

## Failure modes

- An analyst ignores the pack and writes from memory with fake `[E#]`
  ids. Mitigation: `claim_verification` checks that each cited id exists
  and that the quote supports the claim.
- `evidence_gathering` finds plausible but outdated sources. Mitigation:
  source date is mandatory and shown in synthesis.
- Over-correction: so many claims end up `unverified` that the answer is
  unusable. That is a signal about the evidence pack, reported via
  `gaps`, not a reason to relax the rule.

## Cost

Not priced here; each phase's implementation ADR prices its own stages
with `cost_budget.estimate_run_cost_range`, as ADR 012 did. Phase 1 adds
no calls. Phase 2 adds about $0.14-0.42 per run (ADR 012's estimate).
Phase 3's search fee must be verified against the provider's published
rate before it goes into the budget guard.
