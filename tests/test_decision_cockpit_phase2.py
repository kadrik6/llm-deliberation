"""Decision Cockpit Phase 2 -- "You Decide" completion.

See docs/design/decision-cockpit-proposal.md's Section 8 and the Phase 2
design audit. Covers presenter.you_decide_items (assembly from three
already-structured ConvergenceAnalysis fields) and its rendering as a
compact index in partials/convergence_summary.html.

Deliberately does NOT touch Case 3 (no Case 3 variant ever reached
convergence_analysis -- SINGLE/DUAL/CRITIQUE have no convergence stage,
FULL was blocked earlier at red_team). Every scenario here uses either a
lightweight synthetic stand-in object (for the historical-missing-field
degradation tests, which have no real-world precedent -- see the historical
schema audit in presenter.you_decide_items' own docstring) or the existing
FakeOrchestrator-backed web test fixtures (no network, no real provider
calls).
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from llm_deliberation.convergence import parse_convergence_analysis
from llm_deliberation.web.presenter import (
    YOU_DECIDE_DISAGREEMENT,
    YOU_DECIDE_HUMAN_JUDGEMENT,
    YOU_DECIDE_UNKNOWN,
    you_decide_items,
)

from tests.test_web import _convergence_response, _submit


def _evolution(**overrides):
    payload = {
        "convergence": "partial",
        "material_changes": [],
        "agreements_reached": [],
        "unresolved_disagreements": [],
        "remaining_unknowns": [],
        "human_judgement_required": [],
    }
    payload.update(overrides)
    return parse_convergence_analysis(json.dumps(payload))


def _disagreement(decision_impact="Affects cost.", **overrides):
    d = {
        "topic": "t", "candidate_a_position": "a", "candidate_b_position": "b",
        "why_unresolved": "w", "decision_impact": decision_impact,
    }
    d.update(overrides)
    return d


def _human_judgement(issue="Risk appetite.", why="A values tradeoff.", **overrides):
    h = {"issue": issue, "why_models_cannot_resolve_it": why}
    h.update(overrides)
    return h


def _unknown(unknown="Data residency law.", why_it_matters="Determines custody.", **overrides):
    u = {"unknown": unknown, "why_it_matters": why_it_matters, "evidence_needed": "Legal review."}
    u.update(overrides)
    return u


# -- presenter.you_decide_items: composition scenarios ---------------------

def test_only_disagreement_items_present():
    evolution = _evolution(unresolved_disagreements=[_disagreement("Affects contract structure.")])
    items = you_decide_items(evolution)
    assert items == [{"category": YOU_DECIDE_DISAGREEMENT, "primary": "Affects contract structure.", "detail": None}]


def test_only_human_judgement_items_present():
    evolution = _evolution(human_judgement_required=[_human_judgement("Vendor lock-in risk.", "A values call.")])
    items = you_decide_items(evolution)
    assert items == [
        {"category": YOU_DECIDE_HUMAN_JUDGEMENT, "primary": "Vendor lock-in risk.", "detail": "A values call."}
    ]


def test_only_unknown_items_present():
    evolution = _evolution(remaining_unknowns=[_unknown("Donor record count.", "Sizes the migration effort.")])
    items = you_decide_items(evolution)
    assert items == [
        {"category": YOU_DECIDE_UNKNOWN, "primary": "Donor record count.", "detail": "Sizes the migration effort."}
    ]


def test_all_three_present_in_stable_fixed_order():
    """Regardless of how many items each category has, the output is always
    grouped disagreement-then-human_judgement-then-unknown -- never
    interleaved, never sorted by any inferred importance."""
    evolution = _evolution(
        unresolved_disagreements=[_disagreement("D1"), _disagreement("D2")],
        human_judgement_required=[_human_judgement("H1", "why1")],
        remaining_unknowns=[_unknown("U1", "why-u1"), _unknown("U2", "why-u2")],
    )
    items = you_decide_items(evolution)
    categories = [item["category"] for item in items]
    assert categories == [
        YOU_DECIDE_DISAGREEMENT, YOU_DECIDE_DISAGREEMENT,
        YOU_DECIDE_HUMAN_JUDGEMENT,
        YOU_DECIDE_UNKNOWN, YOU_DECIDE_UNKNOWN,
    ]
    # Within a category, source-list order is preserved, not re-sorted.
    assert [i["primary"] for i in items if i["category"] == YOU_DECIDE_DISAGREEMENT] == ["D1", "D2"]
    assert [i["primary"] for i in items if i["category"] == YOU_DECIDE_UNKNOWN] == ["U1", "U2"]


def test_all_three_empty_returns_empty_list():
    evolution = _evolution()
    assert you_decide_items(evolution) == []


def test_disagreement_item_never_leaks_topic_or_positions():
    """A compact index, not a second full-detail copy -- only decision_impact
    is used, never topic/candidate_a_position/candidate_b_position/
    why_unresolved (those stay in "Where models still disagree")."""
    evolution = _evolution(
        unresolved_disagreements=[
            _disagreement(
                "Affects budget.", topic="SECRET_TOPIC", candidate_a_position="SECRET_A",
                candidate_b_position="SECRET_B", why_unresolved="SECRET_WHY",
            )
        ]
    )
    items = you_decide_items(evolution)
    assert len(items) == 1
    serialized = json.dumps(items[0])
    assert "SECRET_TOPIC" not in serialized
    assert "SECRET_A" not in serialized
    assert "SECRET_B" not in serialized
    assert "SECRET_WHY" not in serialized


def test_unknown_item_never_leaks_evidence_needed():
    """Only .unknown and .why_it_matters are used, never .evidence_needed
    (that stays in "What should you find out next?")."""
    evolution = _evolution(remaining_unknowns=[_unknown("U", "why", evidence_needed="SECRET_EVIDENCE")])
    items = you_decide_items(evolution)
    assert "SECRET_EVIDENCE" not in json.dumps(items[0])


# -- Historical-schema degradation (synthetic; no real occurrence exists) --
#
# Pydantic requires all five fields read here, and a persisted artifact is
# only ever written after passing validation -- a live audit (documented in
# you_decide_items' own docstring) found zero real historical instances
# missing any of them. These tests use a lightweight duck-typed stand-in
# (not a real ConvergenceAnalysis, which Pydantic would refuse to construct
# missing a required field) purely to prove the *defensive* code path
# degrades gracefully rather than crashing, should this ever occur.

class _FakeEvolution:
    def __init__(self, disagreements=(), judgements=(), unknowns=()):
        self.unresolved_disagreements = list(disagreements)
        self.human_judgement_required = list(judgements)
        self.remaining_unknowns = list(unknowns)


def test_historical_missing_decision_impact_drops_that_item_not_crashes():
    fake = _FakeEvolution(disagreements=[SimpleNamespace(topic="t")])  # no decision_impact at all
    assert you_decide_items(fake) == []


def test_historical_missing_why_models_cannot_resolve_it_keeps_partial_item():
    fake = _FakeEvolution(judgements=[SimpleNamespace(issue="Risk appetite.")])  # no why_ field
    items = you_decide_items(fake)
    assert items == [{"category": YOU_DECIDE_HUMAN_JUDGEMENT, "primary": "Risk appetite.", "detail": None}]


def test_historical_missing_why_it_matters_keeps_partial_item():
    fake = _FakeEvolution(unknowns=[SimpleNamespace(unknown="Donor count.")])  # no why_it_matters
    items = you_decide_items(fake)
    assert items == [{"category": YOU_DECIDE_UNKNOWN, "primary": "Donor count.", "detail": None}]


# -- Rendering via the full web stack (FakeOrchestrator, no network) -------

def test_result_page_renders_you_decide_with_category_labels_as_text(client, service, fake_orchestrator_state):
    fake_orchestrator_state["responses"] = {
        "convergence_analysis": _convergence_response(
            convergence="partial",
            unresolved_disagreements=[_disagreement("Affects contract structure.")],
            human_judgement_required=[_human_judgement("Risk tolerance for vendor lock-in", "A values tradeoff.")],
            remaining_unknowns=[_unknown("Data residency law", "Determines legal custody model.")],
        )
    }
    response = _submit(client, question="Q?", red_team=False)
    body = response.text

    assert "You decide" in body
    # Category labels are visible plain text, not color-only.
    assert "Disagreement:" in body
    assert "Needs your judgement:" in body
    assert "Open question:" in body
    assert "Affects contract structure." in body
    assert "Risk tolerance for vendor lock-in" in body
    assert "A values tradeoff." in body
    assert "Data residency law" in body
    assert "Determines legal custody model." in body


def test_you_decide_uses_details_above_anchor_not_new_prose(client, service, fake_orchestrator_state):
    """Duplication safeguard: a disagreement/unknown item repeated here must
    be framed as a pointer back to its detailed section, never as new
    independent explanatory prose written by interpreting model content."""
    fake_orchestrator_state["responses"] = {
        "convergence_analysis": _convergence_response(
            convergence="partial",
            unresolved_disagreements=[_disagreement("Affects contract structure.")],
            remaining_unknowns=[_unknown("Data residency law", "Determines legal custody model.")],
        )
    }
    response = _submit(client, question="Q?", red_team=False)
    body = response.text
    assert body.count("Details above") == 2  # one for the disagreement item, one for the unknown item
    assert 'href="#disagreement-map"' in body
    assert 'href="#remaining-unknowns"' in body


def test_you_decide_empty_state_uses_the_exact_mandated_wording(client, service, fake_orchestrator_state):
    fake_orchestrator_state["responses"] = {"convergence_analysis": _convergence_response()}
    response = _submit(client, question="Q?", red_team=False)
    body = response.text
    assert "No additional human judgement items were recorded." in body
    # Must never claim more than the data supports.
    for overclaim in ("Nothing left to decide", "The decision is resolved", "No uncertainty remains"):
        assert overclaim not in body


def test_you_decide_absent_entirely_when_evolution_unavailable(client, service, fake_orchestrator_state):
    # Force convergence_analysis to fail, then skip it so the run still
    # reaches "succeeded" without ever producing an evolution object.
    fake_orchestrator_state["fail"] = {"convergence_analysis"}
    response = _submit(client, question="Q?", red_team=False)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    assert service.get_run(run_id).status == "failed"

    skip_response = client.post(f"/runs/{run_id}/stages/convergence_analysis/skip")
    assert skip_response.status_code == 200
    assert service.get_run(run_id).status == "succeeded"

    detail = client.get(f"/runs/{run_id}")
    body = detail.text
    assert "You decide" not in body
    assert "No additional human judgement items were recorded." not in body


# -- Semantic safety ---------------------------------------------------------

def test_you_decide_never_introduces_confidence_ranking_or_resolution_language(
    client, service, fake_orchestrator_state
):
    fake_orchestrator_state["responses"] = {
        "convergence_analysis": _convergence_response(
            convergence="converged",  # even a "converged" run must not claim resolution here
            unresolved_disagreements=[_disagreement("Affects contract structure.")],
            human_judgement_required=[_human_judgement("Risk tolerance for vendor lock-in", "A values tradeoff.")],
            remaining_unknowns=[_unknown("Data residency law", "Determines legal custody model.")],
        )
    }
    response = _submit(client, question="Q?", red_team=False)
    body = response.text
    lowered = body.lower()
    for forbidden in (
        "ai recommends", "best choice", "we suggest", "we recommend",
        "confidence:", "% confidence", "agreement score", "% agreement",
        "resolved because", "priority:", "severity:",
    ):
        assert forbidden not in lowered, f"forbidden phrase present: {forbidden!r}"


def test_you_decide_html_escapes_model_text_safely(client, service, fake_orchestrator_state):
    fake_orchestrator_state["responses"] = {
        "convergence_analysis": _convergence_response(
            convergence="partial",
            unresolved_disagreements=[_disagreement("<script>alert(1)</script>")],
        )
    }
    response = _submit(client, question="Q?", red_team=False)
    body = response.text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


# -- Translation coverage ---------------------------------------------------

def test_you_decide_category_labels_have_en_and_et_translations():
    from llm_deliberation.web.i18n import TRANSLATIONS

    for key in (
        "you_decide_category_disagreement",
        "you_decide_category_human_judgement",
        "you_decide_category_unknown",
        "you_decide_see_above",
        "you_decide_empty_state",
        "human_judgement_intro",
    ):
        assert key in TRANSLATIONS
        assert TRANSLATIONS[key].get("en"), f"{key} missing an English translation"
        assert TRANSLATIONS[key].get("et"), f"{key} missing an Estonian translation"
        # Distinct text, not one language silently falling back to the other.
        assert TRANSLATIONS[key]["en"] != TRANSLATIONS[key]["et"]


def test_you_decide_renders_in_estonian_ui(client, service, fake_orchestrator_state):
    fake_orchestrator_state["responses"] = {
        "convergence_analysis": _convergence_response(
            convergence="partial",
            human_judgement_required=[_human_judgement("Risk tolerance for vendor lock-in", "A values tradeoff.")],
        )
    }
    client.cookies.set("ui_lang", "et")
    response = _submit(client, question="Q?", red_team=False)
    body = response.text
    assert "Sina otsustad" in body  # human_judgement_heading, Estonian
    assert "Vajab sinu otsust:" in body
