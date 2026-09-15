"""Offline tests for the convergence_analysis stage's schema, parsing, and
prompt wiring.

No network calls: these exercise the Pydantic schema, JSON parse/validate/
canonicalize pipeline, and the orchestrator's pure prompt-building functions
directly -- no provider, no DB, no HTTP.
"""

from __future__ import annotations

import json
import re

import pytest

from llm_deliberation.convergence import (
    ConvergenceAnalysis,
    ConvergenceParseError,
    canonical_json,
    parse_convergence_analysis,
    render_for_prompt,
    schema_for_prompt,
)
from llm_deliberation.orchestrator import _build_prompt, _finalize_convergence_response
from llm_deliberation.providers import ProviderGenerationError
from llm_deliberation.types import ModelResponse, Usage

MINIMAL = {
    "convergence": "converged",
    "material_changes": [],
    "agreements_reached": [],
    "unresolved_disagreements": [],
    "remaining_unknowns": [],
    "human_judgement_required": [],
}


def _with(**overrides) -> dict:
    data = json.loads(json.dumps(MINIMAL))
    data.update(overrides)
    return data


# -- schema / parsing -----------------------------------------------------


def test_minimal_payload_parses():
    analysis = parse_convergence_analysis(json.dumps(MINIMAL))
    assert analysis.convergence == "converged"
    assert analysis.material_changes == []


def test_malformed_json_raises_convergence_parse_error():
    with pytest.raises(ConvergenceParseError):
        parse_convergence_analysis("not json at all")


def test_invalid_convergence_value_raises():
    with pytest.raises(ConvergenceParseError):
        parse_convergence_analysis(json.dumps(_with(convergence="maybe")))


def test_missing_required_field_raises():
    data = json.loads(json.dumps(MINIMAL))
    del data["convergence"]
    with pytest.raises(ConvergenceParseError):
        parse_convergence_analysis(json.dumps(data))


def test_code_fence_is_stripped_before_parsing():
    fenced = "```json\n" + json.dumps(MINIMAL) + "\n```"
    analysis = parse_convergence_analysis(fenced)
    assert analysis.convergence == "converged"


@pytest.mark.parametrize(
    "level", ["converged", "partial", "diverged", "insufficient_information"]
)
def test_every_convergence_level_is_representable(level):
    analysis = parse_convergence_analysis(json.dumps(_with(convergence=level)))
    assert analysis.convergence == level


def test_material_change_with_before_after_and_trigger_persists_correctly():
    payload = _with(
        material_changes=[
            {
                "candidate": "A",
                "before": "Vendor-hosted deployment preferred for speed.",
                "after": "Customer-controlled deployment preferred for data custody.",
                "material": True,
                "triggers": [
                    {
                        "source": "peer_critique",
                        "stage": "critique_b_of_a",
                        "summary": "B raised a data custody risk A had not addressed.",
                    }
                ],
            }
        ]
    )
    analysis = parse_convergence_analysis(json.dumps(payload))
    change = analysis.material_changes[0]
    assert change.candidate == "A"
    assert change.before == "Vendor-hosted deployment preferred for speed."
    assert change.after == "Customer-controlled deployment preferred for data custody."
    assert change.material is True
    assert change.triggers[0].source == "peer_critique"
    assert change.triggers[0].stage == "critique_b_of_a"


def test_no_change_case_is_representable_with_material_false():
    payload = _with(
        material_changes=[
            {
                "candidate": "B",
                "before": "Phased rollout.",
                "after": "Phased rollout.",
                "material": False,
                "triggers": [],
            }
        ]
    )
    analysis = parse_convergence_analysis(json.dumps(payload))
    assert analysis.material_changes[0].material is False
    assert analysis.material_changes[0].triggers == []


def test_partial_convergence_with_unresolved_disagreement():
    payload = _with(
        convergence="partial",
        unresolved_disagreements=[
            {
                "topic": "Data custody",
                "candidate_a_position": "Customer-controlled deployment preferred.",
                "candidate_b_position": "Vendor-hosted acceptable with safeguards.",
                "why_unresolved": "Depends on regulatory posture not stated in the question.",
                "decision_impact": "Affects contract structure and cost.",
            }
        ],
    )
    analysis = parse_convergence_analysis(json.dumps(payload))
    assert analysis.convergence == "partial"
    assert len(analysis.unresolved_disagreements) == 1
    assert analysis.unresolved_disagreements[0].topic == "Data custody"


def test_trigger_source_can_be_marked_uncertain_instead_of_invented():
    payload = _with(
        material_changes=[
            {
                "candidate": "A",
                "before": "X",
                "after": "Y",
                "material": True,
                "triggers": [
                    {
                        "source": "uncertain",
                        "stage": None,
                        "summary": "The specific trigger cannot be determined reliably.",
                    }
                ],
            }
        ]
    )
    analysis = parse_convergence_analysis(json.dumps(payload))
    trigger = analysis.material_changes[0].triggers[0]
    assert trigger.source == "uncertain"
    assert trigger.stage is None


def test_human_judgement_required_items_are_representable():
    payload = _with(
        human_judgement_required=[
            {
                "issue": "Risk tolerance for vendor lock-in",
                "why_models_cannot_resolve_it": "A values/strategy tradeoff, not a factual question.",
            }
        ]
    )
    analysis = parse_convergence_analysis(json.dumps(payload))
    assert analysis.human_judgement_required[0].issue == "Risk tolerance for vendor lock-in"


def test_canonical_json_round_trips():
    analysis = ConvergenceAnalysis.model_validate(MINIMAL)
    round_tripped = parse_convergence_analysis(canonical_json(analysis))
    assert round_tripped == analysis


def test_schema_for_prompt_mentions_every_top_level_field():
    schema_text = schema_for_prompt()
    for field in (
        "convergence",
        "material_changes",
        "agreements_reached",
        "unresolved_disagreements",
        "remaining_unknowns",
        "human_judgement_required",
    ):
        assert field in schema_text


def test_render_for_prompt_mentions_disagreements_and_unknowns():
    payload = _with(
        convergence="diverged",
        unresolved_disagreements=[
            {
                "topic": "Approach",
                "candidate_a_position": "A",
                "candidate_b_position": "B",
                "why_unresolved": "reasons",
                "decision_impact": "impact",
            }
        ],
        remaining_unknowns=[
            {"unknown": "market size", "why_it_matters": "sizing", "evidence_needed": "survey"}
        ],
    )
    analysis = parse_convergence_analysis(json.dumps(payload))
    rendered = render_for_prompt(analysis)
    assert "diverged" in rendered
    assert "Approach" in rendered
    assert "market size" in rendered


# -- orchestrator finalize step (parse-as-a-gate) --------------------------


def _resp(text: str) -> ModelResponse:
    return ModelResponse(
        provider="Anthropic",
        model="claude-sonnet-5",
        text=text,
        usage=Usage(100, 200),
        estimated_cost_usd=0.01,
        requested_model="claude-sonnet-5",
    )


def test_finalize_convergence_response_replaces_text_with_canonical_json():
    response = _resp("  " + json.dumps(MINIMAL) + "  ")
    finalized = _finalize_convergence_response(response)
    assert finalized.text == canonical_json(ConvergenceAnalysis.model_validate(MINIMAL))


def test_finalize_convergence_response_raises_on_malformed_output():
    response = _resp("this is not json")
    # Raised as ProviderGenerationError (not the bare ConvergenceParseError)
    # specifically so cost/model provenance survives -- see the next test.
    with pytest.raises(ProviderGenerationError):
        _finalize_convergence_response(response)


def test_finalize_convergence_response_preserves_cost_on_validation_failure():
    # Regression test: a malformed response is still a *paid* API call. Its
    # cost must not silently disappear from the run total just because the
    # content didn't parse.
    response = ModelResponse(
        provider="Anthropic",
        model="claude-sonnet-5",
        text="Sorry, I can't produce that.",
        usage=Usage(input_tokens=5000, output_tokens=800),
        estimated_cost_usd=0.0182,
        requested_model="claude-sonnet-5",
    )
    with pytest.raises(ProviderGenerationError) as excinfo:
        _finalize_convergence_response(response)

    assert excinfo.value.estimated_cost_usd == pytest.approx(0.0182)
    assert excinfo.value.requested_model == "claude-sonnet-5"
    assert excinfo.value.fallback_used is False


# -- prompt building (pure, no provider/network) ---------------------------


def _texts(**overrides) -> dict[str, str]:
    base = dict(
        analysis_a="A's original analysis.",
        analysis_b="B's original analysis.",
        critique_a_of_b="A's critique of B.",
        critique_b_of_a="B's critique of A.",
        revision_a="A's revised position.",
        revision_b="B's revised position.",
    )
    base.update(overrides)
    return base


def test_convergence_analysis_prompt_includes_all_inputs_and_schema():
    prompt = _build_prompt("convergence_analysis", "Should we do X?", _texts())
    assert "Should we do X?" in prompt
    assert "A's original analysis." in prompt
    assert "B's original analysis." in prompt
    assert "A's critique of B." in prompt
    assert "B's critique of A." in prompt
    assert "A's revised position." in prompt
    assert "B's revised position." in prompt
    assert "No third-model red-team was used." in prompt
    assert '"convergence"' in prompt  # schema embedded
    assert "chain-of-thought" in prompt.lower()


def test_convergence_analysis_prompt_labels_candidates_without_provider_branding():
    # Candidates are presented as "CANDIDATE A"/"CANDIDATE B" only -- the
    # prompt template itself must never tell the convergence analyst which
    # provider produced which candidate. (The underlying analysis/revision
    # *text* could in principle self-identify -- out of this function's
    # control -- but the template's own labeling must stay neutral.)
    prompt = _build_prompt("convergence_analysis", "Q?", _texts())
    assert "CANDIDATE A" in prompt
    assert "CANDIDATE B" in prompt
    for brand in ("OpenAI", "Anthropic", "Gemini", "GPT", "Claude"):
        assert brand not in prompt


def test_convergence_analysis_prompt_includes_red_team_when_present():
    prompt = _build_prompt(
        "convergence_analysis", "Q?", _texts(red_team="Shared blind spot report.")
    )
    assert "Shared blind spot report." in prompt


def test_synthesis_prompt_includes_convergence_context_when_present():
    convergence_json = json.dumps(
        {
            "convergence": "partial",
            "material_changes": [],
            "agreements_reached": [],
            "unresolved_disagreements": [
                {
                    "topic": "Data custody",
                    "candidate_a_position": "A prefers X",
                    "candidate_b_position": "B prefers Y",
                    "why_unresolved": "no shared evidence",
                    "decision_impact": "affects contract terms",
                }
            ],
            "remaining_unknowns": [],
            "human_judgement_required": [],
        }
    )
    prompt = _build_prompt(
        "synthesis", "Q?", _texts(convergence_analysis=convergence_json)
    )
    assert "Data custody" in prompt
    assert "partial" in prompt
    assert "Change/convergence analysis unavailable for this run." not in prompt


def test_synthesis_prompt_states_unavailable_when_convergence_missing():
    prompt = _build_prompt("synthesis", "Q?", _texts())  # no "convergence_analysis" key
    assert "Change/convergence analysis unavailable for this run." in prompt


def test_synthesis_prompt_does_not_imply_convergence_when_unavailable():
    # A skipped/absent convergence_analysis stage must not read as "the
    # candidates converged" -- it must read as "we don't know." Whole-word
    # match (via \b) so "disagree" in the surrounding static boilerplate
    # doesn't false-positive against "agree".
    prompt = _build_prompt("synthesis", "Q?", _texts())
    convergence_section = prompt.split("CONVERGENCE / CHANGE ANALYSIS")[1].split(
        "Produce the final answer."
    )[0]
    assert "unavailable" in convergence_section.lower()
    for word in ("converged", "agree", "consensus"):
        assert not re.search(rf"\b{word}\b", convergence_section, re.IGNORECASE)
    # The instruction telling the synthesizer how to handle this case must
    # itself be present and explicit, not just the placeholder text.
    normalized = " ".join(prompt.split())
    assert "say so plainly instead of guessing at whether the candidates agree" in normalized


def test_synthesis_receives_validated_canonical_text_not_raw_model_output():
    """End-to-end (no network): a raw, whitespace/fence-noisy but
    schema-valid response from the convergence provider must be
    canonicalized by _finalize_convergence_response before it can ever
    reach the synthesis prompt -- synthesis must never see the model's
    literal raw text."""
    raw_noisy_text = "```json\n  " + json.dumps(MINIMAL) + "  \n```"
    response = _resp(raw_noisy_text)

    finalized = _finalize_convergence_response(response)
    assert finalized.text != raw_noisy_text  # not the raw text
    assert finalized.text == canonical_json(ConvergenceAnalysis.model_validate(MINIMAL))

    # What actually flows into texts["convergence_analysis"] in the service
    # layer is exactly this canonicalized text (see service._execute).
    prompt = _build_prompt("synthesis", "Q?", _texts(convergence_analysis=finalized.text))
    assert "```" not in prompt  # the fence never survived into the prompt
    assert "converged" in prompt
