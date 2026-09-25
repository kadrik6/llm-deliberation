"""Offline tests for evaluation/store.py (dedicated evaluation persistence)
and evaluation/cases.py (version-controlled case format)."""

from __future__ import annotations

import json

import pytest

from llm_deliberation.evaluation.cases import EvalCase, load_case, load_cases
from llm_deliberation.evaluation.store import EvalStageRow, EvaluationRepository


def test_experiment_round_trip(tmp_path):
    repo = EvaluationRepository(tmp_path / "eval.db")
    stages = [
        EvalStageRow(
            stage_name="single_answer", provider="OpenAI", requested_model="gpt-5.6-terra",
            actual_model="gpt-5.6-terra", input_tokens=100, output_tokens=400,
            attempt_count=1, cost_usd=0.02, status="succeeded", truncated=False,
            text="the answer", error=None,
        )
    ]
    experiment_id = repo.save_experiment(
        case_id="example-nonprofit-crm", variant="SINGLE", git_commit="abc123",
        profile="economy", output_language="en", working_language="en",
        configured_openai_model="gpt-5.6-terra", configured_anthropic_model=None,
        configured_gemini_model=None, status="succeeded", stages=stages,
        final_output="the answer", quality_state=None, material_change_count=None,
        convergence_status=None, duration_seconds=12.5,
        max_cost_per_variant_usd="1.00", production_run_id=None,
    )

    record = repo.get_experiment(experiment_id)
    assert record.case_id == "example-nonprofit-crm"
    assert record.variant == "SINGLE"
    assert record.total_calls == 1
    assert record.total_cost_usd == pytest.approx(0.02)
    assert len(record.stages) == 1
    assert record.stages[0].stage_name == "single_answer"


def test_list_experiments_filters_by_case_id(tmp_path):
    repo = EvaluationRepository(tmp_path / "eval.db")
    for variant in ("SINGLE", "DUAL"):
        repo.save_experiment(
            case_id="case-1", variant=variant, git_commit=None, profile="economy",
            output_language="en", working_language="en", configured_openai_model=None,
            configured_anthropic_model=None, configured_gemini_model=None,
            status="succeeded", stages=[], final_output=None, quality_state=None,
            material_change_count=None, convergence_status=None, duration_seconds=1.0,
            max_cost_per_variant_usd=None, production_run_id=None,
        )
    repo.save_experiment(
        case_id="case-2", variant="SINGLE", git_commit=None, profile="economy",
        output_language="en", working_language="en", configured_openai_model=None,
        configured_anthropic_model=None, configured_gemini_model=None,
        status="succeeded", stages=[], final_output=None, quality_state=None,
        material_change_count=None, convergence_status=None, duration_seconds=1.0,
        max_cost_per_variant_usd=None, production_run_id=None,
    )

    case_1_experiments = repo.list_experiments(case_id="case-1")
    assert len(case_1_experiments) == 2
    assert all(e.case_id == "case-1" for e in case_1_experiments)


def test_reviews_round_trip(tmp_path):
    repo = EvaluationRepository(tmp_path / "eval.db")
    experiment_id = repo.save_experiment(
        case_id="case-1", variant="SINGLE", git_commit=None, profile="economy",
        output_language="en", working_language="en", configured_openai_model=None,
        configured_anthropic_model=None, configured_gemini_model=None,
        status="succeeded", stages=[], final_output=None, quality_state=None,
        material_change_count=None, convergence_status=None, duration_seconds=1.0,
        max_cost_per_variant_usd=None, production_run_id=None,
    )
    repo.save_review(
        experiment_id=experiment_id, reviewer="alice", dimension="usefulness",
        rating="high", numeric_rating=4.0, comment="Covers the main tradeoff well.",
    )
    reviews = repo.list_reviews(experiment_id)
    assert len(reviews) == 1
    assert reviews[0]["dimension"] == "usefulness"
    assert reviews[0]["rating"] == "high"


def test_experiments_are_independent_of_production_run_history(tmp_path):
    """Evaluation persistence lives in a completely separate database file
    from production runs -- confirmed here by simply using a fresh path
    with none of store.py's schema in it at all."""
    repo = EvaluationRepository(tmp_path / "eval_only.db")
    tables = {
        r[0]
        for r in repo._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert tables == {"eval_experiments", "eval_stage_results", "eval_reviews", "sqlite_sequence"}
    assert "runs" not in tables
    assert "stages" not in tables


# -- case format --------------------------------------------------------


def test_load_case_round_trip(tmp_path):
    """EvalCase is public-only -- see test_evaluation_case_separation.py for
    ReviewerMetadata/load_reviewer_metadata's own round-trip and
    empty-important_constraints-rejection tests. A case file may still
    carry legacy embedded reviewer fields (Pilot Case 1's style); load_case
    simply never reads them onto the returned object."""
    path = tmp_path / "good-case.json"
    path.write_text(json.dumps({
        "case_id": "good-case", "title": "Good", "question": "Q?",
        "context": "Some context.", "output_language": "et",
        "expected_deliverable": "A recommendation.",
        "important_constraints": ["budget is limited"],
        "review_notes": "example",
    }))
    case = load_case(path)
    assert isinstance(case, EvalCase)
    assert case.case_id == "good-case"
    assert case.output_language == "et"
    assert not hasattr(case, "important_constraints")
    assert not hasattr(case, "review_notes")


def test_load_cases_from_the_real_eval_cases_directory():
    """The example case committed to eval_cases/ must itself be valid."""
    cases = load_cases()
    assert any(c.case_id == "example-nonprofit-crm" for c in cases)


def test_load_cases_from_empty_directory_returns_empty_list(tmp_path):
    assert load_cases(tmp_path / "does-not-exist") == []
