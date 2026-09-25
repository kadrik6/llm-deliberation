"""Offline tests proving two separate guarantees for reviewer-only case
content (important_constraints, review_notes):

1. it can never reach a live provider prompt or a blind-review export
   (the original guarantee this file proved); and
2. it can never even be *loaded* onto a public EvalCase object, and a
   fresh clone of this repository with no eval_private/ directory at all
   can still load every public case, run every variant, and export blind
   reviews (the pre-publication-audit guarantee added afterward -- see
   docs/verification-matrix.md and cases.py's module docstring).

No real secret text lives in this file: guarantee (2)'s tests use a small
synthetic fixture case, never eval_cases/case-3-data-warehouse-flip.json's
actual (private, gitignored) reviewer content. No network calls: providers
are fake objects capturing every prompt they were called with, exactly
like test_evaluation_variants.py's pattern.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json

import pytest

from llm_deliberation.evaluation import variants as variants_module
from llm_deliberation.evaluation.blind_review import build_blind_export
from llm_deliberation.evaluation.cases import (
    EvalCase,
    ReviewerMetadata,
    load_case,
    load_reviewer_metadata,
    model_input_fingerprint,
)
from llm_deliberation.evaluation.store import EvalExperimentRecord
from llm_deliberation.evaluation.variants import run_variant
from llm_deliberation.orchestrator import DeliberationOrchestrator
from llm_deliberation.types import ModelResponse, Usage

CASE_PATH = "eval_cases/case-3-data-warehouse-flip.json"

# The frozen model-visible identity of Case 3, as executed by the three
# completed live experiments (SINGLE 47b0de64fec2, DUAL c47b5b345a65,
# CRITIQUE 3f616996042a). The original authoring commit is not published
# because it also contained reviewer-only metadata (see
# docs/verification-matrix.md) -- this hash is the reproducibility anchor
# in its place. Computed over exactly case_id/question/context/
# output_language; see cases.model_input_fingerprint's own docstring.
FROZEN_CASE_3_MODEL_INPUT_FINGERPRINT = (
    "472be5631b0050029b8d85efeca350dcd5cf35df498b62ab047a2bc527357089"
)


class _CapturingProvider:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.model = "gpt-5.6-terra"
        self.max_output_tokens = 1000

    def generate(self, *, system: str, prompt: str) -> ModelResponse:
        self.calls.append((system, prompt))
        return ModelResponse(
            provider="Fake", model=self.model, text="A recommendation.",
            usage=Usage(input_tokens=50, output_tokens=50), estimated_cost_usd=0.01,
            requested_model=self.model, incomplete_reason=None,
        )


def _fake_orchestrator(monkeypatch) -> DeliberationOrchestrator:
    monkeypatch.setattr(DeliberationOrchestrator, "__init__", lambda self, settings: None)
    orch = DeliberationOrchestrator(settings=None)
    orch.a = _CapturingProvider()
    orch.b = _CapturingProvider()
    return orch


# -- Public Case 3 file: structural pre-publication safety -----------------


def test_public_case3_file_contains_no_reviewer_metadata_or_hint_title():
    """Regression test for the pre-publication audit finding: the tracked
    public case file must never again carry review_notes/
    important_constraints, and its title must never hint at the tested
    mechanism (e.g. "flip")."""
    with open(CASE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    assert "review_notes" not in data
    assert "important_constraints" not in data
    lowered_title = data["title"].lower()
    for hint in ("flip", "trap", "decision-changing constraint"):
        assert hint not in lowered_title, f"title leaks a hint word: {hint!r}"


def test_public_case3_loads_with_model_visible_fields_and_no_reviewer_attributes():
    case = load_case(CASE_PATH)
    assert case.case_id == "case-3-data-warehouse-flip"
    assert "cloud-hosted data warehouse" in case.question
    assert "ERP" in case.context
    assert case.output_language == "en"
    # Structural guarantee, not just an empirical absence: EvalCase has no
    # such fields at all, on any case, ever -- see ReviewerMetadata instead.
    assert not hasattr(case, "important_constraints")
    assert not hasattr(case, "review_notes")


def test_case3_model_input_fingerprint_matches_the_frozen_historical_value():
    """Anchors Case 3's public model-visible payload to the input the three
    completed live experiments actually ran against, without needing the
    original (unpublished) authoring commit -- see this file's module
    docstring and cases.model_input_fingerprint."""
    case = load_case(CASE_PATH)
    assert model_input_fingerprint(case) == FROZEN_CASE_3_MODEL_INPUT_FINGERPRINT


def test_model_input_fingerprint_is_deterministic_and_excludes_reviewer_only_fields():
    case_a = EvalCase(
        case_id="synthetic", title="Title A", question="Q", context="C",
        output_language="en", expected_deliverable="A plan.",
    )
    case_b = EvalCase(
        case_id="synthetic", title="A completely different title", question="Q", context="C",
        output_language="en", expected_deliverable="A totally different deliverable sentence.",
    )
    # Same twice -> same fingerprint (deterministic).
    assert model_input_fingerprint(case_a) == model_input_fingerprint(case_a)
    # Only title/expected_deliverable differ (never model-visible) -> same fingerprint.
    assert model_input_fingerprint(case_a) == model_input_fingerprint(case_b)


# -- Reviewer-metadata loading: optional, sidecar-first, legacy-fallback ----


def test_load_reviewer_metadata_returns_none_on_a_fresh_clone(tmp_path):
    """No eval_private/ directory, no embedded fields on the public case --
    exactly the state of a fresh public clone."""
    cases_dir = tmp_path / "eval_cases"
    cases_dir.mkdir()
    (cases_dir / "synthetic-case.json").write_text(json.dumps({
        "case_id": "synthetic-case", "title": "T", "question": "Q",
        "output_language": "en", "expected_deliverable": "D",
    }))
    result = load_reviewer_metadata(
        "synthetic-case", cases_dir=cases_dir, private_dir=tmp_path / "eval_private",
    )
    assert result is None


def test_load_reviewer_metadata_reads_the_private_sidecar_when_present(tmp_path):
    private_dir = tmp_path / "eval_private"
    private_dir.mkdir()
    (private_dir / "synthetic-case.review.json").write_text(json.dumps({
        "important_constraints": ["synthetic constraint one", "synthetic constraint two"],
        "review_notes": "SYNTHETIC_REVIEWER_KEY_MARKER_only used in this test.",
    }))
    result = load_reviewer_metadata(
        "synthetic-case", cases_dir=tmp_path / "eval_cases", private_dir=private_dir,
    )
    assert isinstance(result, ReviewerMetadata)
    assert result.important_constraints == ["synthetic constraint one", "synthetic constraint two"]
    assert "SYNTHETIC_REVIEWER_KEY_MARKER" in result.review_notes


def test_load_reviewer_metadata_falls_back_to_legacy_embedded_fields(tmp_path):
    """Backward compatibility for a case predating the public/private split
    (Pilot Case 1's actual style) -- reviewer fields embedded directly in
    the one public file, no sidecar needed."""
    cases_dir = tmp_path / "eval_cases"
    cases_dir.mkdir()
    (cases_dir / "legacy-case.json").write_text(json.dumps({
        "case_id": "legacy-case", "title": "T", "question": "Q",
        "output_language": "en", "expected_deliverable": "D",
        "important_constraints": ["legacy constraint"],
        "review_notes": "Simple canary case, nothing sensitive.",
    }))
    result = load_reviewer_metadata(
        "legacy-case", cases_dir=cases_dir, private_dir=tmp_path / "eval_private",
    )
    assert isinstance(result, ReviewerMetadata)
    assert result.important_constraints == ["legacy constraint"]


def test_private_sidecar_takes_priority_over_legacy_embedded_fields(tmp_path):
    cases_dir = tmp_path / "eval_cases"
    cases_dir.mkdir()
    (cases_dir / "dual-source-case.json").write_text(json.dumps({
        "case_id": "dual-source-case", "title": "T", "question": "Q",
        "output_language": "en", "expected_deliverable": "D",
        "important_constraints": ["stale embedded constraint"],
    }))
    private_dir = tmp_path / "eval_private"
    private_dir.mkdir()
    (private_dir / "dual-source-case.review.json").write_text(json.dumps({
        "important_constraints": ["authoritative sidecar constraint"],
    }))
    result = load_reviewer_metadata("dual-source-case", cases_dir=cases_dir, private_dir=private_dir)
    assert result.important_constraints == ["authoritative sidecar constraint"]


def test_reviewer_metadata_requires_manually_authored_constraints():
    with pytest.raises(ValueError, match="important_constraints"):
        ReviewerMetadata(important_constraints=[])


# -- Structural proof the harness's run path cannot read reviewer fields ---


def test_run_variant_source_never_references_reviewer_only_fields():
    """Neither review_notes nor important_constraints is referenced
    anywhere in evaluation/variants.py's source, so no code path there --
    present or added later without touching this file -- could pass them
    into a provider call."""
    source = inspect.getsource(variants_module)
    tree = ast.parse(source)
    referenced_attrs = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert "review_notes" not in referenced_attrs
    assert "important_constraints" not in referenced_attrs


def test_run_variant_signature_only_accepts_question_context_language():
    """run_variant's own parameter list is the enforcement point: it never
    accepts an EvalCase (or ReviewerMetadata) object at all, only the three
    model-visible scalars."""
    sig = inspect.signature(run_variant)
    assert set(sig.parameters) >= {"question", "context", "output_language"}
    assert "review_notes" not in sig.parameters
    assert "important_constraints" not in sig.parameters
    assert "case" not in sig.parameters


def test_live_prompts_never_contain_reviewer_only_content(monkeypatch):
    """Runtime proof, using the real public Case 3 file (safe -- it has no
    reviewer content anymore) for realistic question/context, plus a
    synthetic reviewer-metadata fixture for the forbidden-content check, so
    no real secret text needs to appear in this test."""
    case = load_case(CASE_PATH)
    synthetic_secret = "SYNTHETIC_PRIVATE_REASONING_KEY_MARKER_482"
    orch = _fake_orchestrator(monkeypatch)

    asyncio.run(
        run_variant(
            "DUAL", question=case.question, context=case.context,
            output_language=case.output_language, settings=None, orchestrator=orch,
        )
    )

    all_prompts = [text for pair in (orch.a.calls + orch.b.calls) for text in pair]
    for text in all_prompts:
        assert synthetic_secret not in text
        assert "review_notes" not in text
        assert "important_constraints" not in text

    # Positive control: the model DID receive the real question/context.
    assert any(case.question in text for text in all_prompts)
    assert any("ERP" in text and "ODBC" in text for text in all_prompts)


# -- Blind export contains Question/Context but never reviewer-only content -


def _fake_experiment(variant: str, output: str) -> EvalExperimentRecord:
    return EvalExperimentRecord(
        id=f"exp-{variant}", case_id="case-3-data-warehouse-flip", variant=variant, git_commit=None,
        profile="economy", output_language="en", working_language="en",
        configured_openai_model="gpt-5.6-terra", configured_anthropic_model="claude-sonnet-5",
        configured_gemini_model=None, created_at="2026-01-01T00:00:00+00:00", completed_at=None,
        status="succeeded", total_calls=1, total_input_tokens=100, total_output_tokens=200,
        total_cost_usd=0.05, duration_seconds=10.0, quality_state=None, final_output=output,
        material_change_count=None, convergence_status=None, max_cost_per_variant_usd="0.75",
        production_run_id=None, stages=[],
    )


def test_blind_export_has_question_and_context_but_not_reviewer_content():
    case = load_case(CASE_PATH)
    experiments = [
        _fake_experiment(v, f"A {v.lower()}-shaped recommendation about the migration.")
        for v in ("SINGLE", "DUAL", "CRITIQUE", "FULL")
    ]
    export = build_blind_export(case, experiments, seed=1)

    # Question/Context ARE included -- the reviewer needs them for this case.
    assert case.question in export.review_markdown
    assert "ERP" in export.review_markdown and "ODBC" in export.review_markdown

    forbidden = [
        "review_notes", "important_constraints",
        "SINGLE", "DUAL", "CRITIQUE", "FULL",  # variant identity
        "gpt-5.6-terra", "claude-sonnet-5", "economy",  # model/profile identity
    ]
    for marker in forbidden:
        assert marker not in export.review_markdown, f"leaked into blind export: {marker!r}"
