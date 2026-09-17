"""Offline tests for evaluation/blind_review.py."""

from __future__ import annotations

import json

from llm_deliberation.evaluation.blind_review import build_blind_export, write_blind_export
from llm_deliberation.evaluation.cases import load_case
from llm_deliberation.evaluation.store import EvalExperimentRecord


def _experiment(variant: str, output: str) -> EvalExperimentRecord:
    return EvalExperimentRecord(
        id=f"exp-{variant}", case_id="example-nonprofit-crm", variant=variant, git_commit=None,
        profile="economy", output_language="en", working_language="en",
        configured_openai_model="gpt-5.6-terra", configured_anthropic_model="claude-sonnet-5",
        configured_gemini_model=None, created_at="2026-01-01T00:00:00+00:00", completed_at=None,
        status="succeeded", total_calls=1, total_input_tokens=100, total_output_tokens=200,
        total_cost_usd=0.05, duration_seconds=10.0, quality_state="complete", final_output=output,
        material_change_count=None, convergence_status=None, max_cost_per_variant_usd="1.00",
        production_run_id=None, stages=[],
    )


def test_review_artifact_never_reveals_variant_or_model_names():
    # Deliberately content that does NOT itself contain any variant name, so
    # this test isolates what build_blind_export adds, not what the fake
    # test fixture's own text happens to say.
    case = load_case("eval_cases/example-nonprofit-crm.json")
    experiments = [
        _experiment("SINGLE", "A concise, one-shot recommendation."),
        _experiment("DUAL", "Two independent recommendations, combined."),
        _experiment("CRITIQUE", "A cross-examined, revised recommendation."),
        _experiment("FULL", "A fully deliberated recommendation."),
    ]
    export = build_blind_export(case, experiments, seed=42)

    for forbidden in ("SINGLE", "DUAL", "CRITIQUE", "FULL", "gpt-5.6-terra", "claude-sonnet-5", "economy"):
        assert forbidden not in export.review_markdown

    for label in export.mapping:
        assert f"Output {label}" in export.review_markdown


def test_mapping_correctly_identifies_each_labeled_output():
    case = load_case("eval_cases/example-nonprofit-crm.json")
    experiments = [
        _experiment("SINGLE", "SINGLE output text."),
        _experiment("FULL", "FULL output text."),
    ]
    export = build_blind_export(case, experiments, seed=7)

    for label, variant in export.mapping.items():
        expected_text = f"{variant} output text."
        assert expected_text in export.review_markdown
        assert f"Output {label}\n\n{expected_text}" in export.review_markdown


def test_same_seed_is_reproducible_different_seed_usually_differs():
    case = load_case("eval_cases/example-nonprofit-crm.json")
    experiments = [_experiment(v, f"{v} output.") for v in ("SINGLE", "DUAL", "CRITIQUE", "FULL")]

    export_1a = build_blind_export(case, experiments, seed=1)
    export_1b = build_blind_export(case, experiments, seed=1)
    assert export_1a.mapping == export_1b.mapping

    export_2 = build_blind_export(case, experiments, seed=2)
    assert export_1a.mapping != export_2.mapping  # not guaranteed in general, but true for these seeds


def test_write_blind_export_produces_two_separate_files(tmp_path):
    case = load_case("eval_cases/example-nonprofit-crm.json")
    experiments = [_experiment("SINGLE", "text A"), _experiment("FULL", "text B")]
    export = build_blind_export(case, experiments, seed=3)

    review_path = tmp_path / "review.md"
    mapping_path = tmp_path / "mapping.json"
    write_blind_export(export, review_path=review_path, mapping_path=mapping_path)

    review_text = review_path.read_text()
    assert "SINGLE" not in review_text and "FULL" not in review_text

    mapping_data = json.loads(mapping_path.read_text())
    assert mapping_data["mapping"] == export.mapping
    assert set(mapping_data["mapping"].values()) == {"SINGLE", "FULL"}
