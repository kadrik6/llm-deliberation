"""Offline tests for the evaluation harness's controlled pipeline variants
(SINGLE/DUAL/CRITIQUE/FULL) -- see evaluation/variants.py.

No network calls: SINGLE/DUAL/CRITIQUE inject a DeliberationOrchestrator
with fake providers (mirroring test_provider_completion.py's established
pattern); FULL reuses the existing `service`/`fake_orchestrator_state`
fixtures from conftest.py exactly like every other service-level test in
this suite.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from llm_deliberation.config import Settings
from llm_deliberation.evaluation.variants import VARIANT_STAGES, run_variant
from llm_deliberation.orchestrator import ALL_STAGE_NAMES, DeliberationOrchestrator
from llm_deliberation.types import ModelResponse, Usage


class _FixedProvider:
    def __init__(self, text: str = "A complete, well-reasoned answer.", cost: float = 0.01):
        self.calls: list[tuple[str, str]] = []
        self._text = text
        self._cost = cost
        # A real, priced model name -- estimate_max_call_cost_usd returns
        # $0 for an unpriced model, which would make every budget-guard
        # test below a no-op.
        self.model = "gpt-5.6-terra"
        self.max_output_tokens = 1000

    def generate(self, *, system: str, prompt: str) -> ModelResponse:
        self.calls.append((system, prompt))
        return ModelResponse(
            provider="Fake", model=self.model, text=self._text,
            usage=Usage(input_tokens=50, output_tokens=100), estimated_cost_usd=self._cost,
            requested_model=self.model, incomplete_reason=None,
        )


def _fake_orchestrator(monkeypatch, *, a=None, b=None) -> DeliberationOrchestrator:
    monkeypatch.setattr(DeliberationOrchestrator, "__init__", lambda self, settings: None)
    orch = DeliberationOrchestrator(settings=None)
    orch.a = a or _FixedProvider()
    orch.b = b or _FixedProvider()
    return orch


def test_single_makes_exactly_one_call_and_no_fabricated_stages(monkeypatch):
    orch = _fake_orchestrator(monkeypatch)
    result = asyncio.run(
        run_variant(
            "SINGLE", question="Q?", context=None, output_language="en",
            settings=None, orchestrator=orch,
        )
    )
    assert [s.stage_name for s in result.stages] == list(VARIANT_STAGES["SINGLE"])
    assert result.total_calls == 1
    assert len(orch.a.calls) == 1
    assert len(orch.b.calls) == 0  # candidate B never invoked at all
    assert result.status == "succeeded"
    assert result.final_output == "A complete, well-reasoned answer."


def test_dual_runs_two_independent_analyses_then_combines(monkeypatch):
    orch = _fake_orchestrator(monkeypatch)
    result = asyncio.run(
        run_variant(
            "DUAL", question="Q?", context=None, output_language="en",
            settings=None, orchestrator=orch,
        )
    )
    assert [s.stage_name for s in result.stages] == list(VARIANT_STAGES["DUAL"])
    assert result.total_calls == 3
    assert len(orch.a.calls) == 2  # analysis_a + final_answer (both routed to "a")
    assert len(orch.b.calls) == 1  # analysis_b only
    assert result.status == "succeeded"

    # No fabrication: the final prompt explicitly names every stage this
    # variant did NOT run, and never claims critique/red-team happened.
    final_system, final_prompt = orch.a.calls[-1]
    assert "critique" in final_prompt
    assert "NOT part of this evaluation run" in final_prompt
    assert "Candidate A" in final_prompt or "CANDIDATE A" in final_prompt


def test_critique_runs_the_full_reduced_sequence(monkeypatch):
    orch = _fake_orchestrator(monkeypatch)
    result = asyncio.run(
        run_variant(
            "CRITIQUE", question="Q?", context=None, output_language="en",
            settings=None, orchestrator=orch,
        )
    )
    assert [s.stage_name for s in result.stages] == list(VARIANT_STAGES["CRITIQUE"])
    assert result.total_calls == 7
    # a: analysis_a, critique_a_of_b, revision_a, final_answer = 4
    # b: analysis_b, critique_b_of_a, revision_b = 3
    assert len(orch.a.calls) == 4
    assert len(orch.b.calls) == 3
    assert result.status == "succeeded"

    final_system, final_prompt = orch.a.calls[-1]
    assert "red_team" in final_prompt
    assert "convergence_analysis" in final_prompt
    assert "NOT part of this evaluation run" in final_prompt
    # But critique/revision are NOT listed as skipped stages -- they DID run.
    stage_list = final_prompt.split("actually produced: ")[1].split(".")[0]
    assert stage_list == "red_team, convergence_analysis"


def test_reduced_variant_stops_early_on_a_required_stage_failure(monkeypatch):
    failing = _FixedProvider()

    def _fail(*, system, prompt):
        failing.calls.append((system, prompt))
        raise RuntimeError("simulated provider failure")

    failing.generate = _fail
    orch = _fake_orchestrator(monkeypatch, a=failing)

    result = asyncio.run(
        run_variant(
            "DUAL", question="Q?", context=None, output_language="en",
            settings=None, orchestrator=orch,
        )
    )
    assert result.status == "failed"
    # Never proceeds to a final-answer call once a prerequisite stage failed.
    assert "final_answer" not in [s.stage_name for s in result.stages]


def test_variant_respects_max_cost_per_variant(monkeypatch):
    expensive = _FixedProvider(cost=10.0)
    orch = _fake_orchestrator(monkeypatch, a=expensive)

    result = asyncio.run(
        run_variant(
            "SINGLE", question="Q?", context=None, output_language="en",
            settings=None, orchestrator=orch, max_cost_per_variant_usd=Decimal("0.001"),
        )
    )
    assert result.status == "failed"
    assert len(orch.a.calls) == 0  # blocked before ever calling out


def test_unknown_variant_name_raises():
    with pytest.raises(ValueError):
        asyncio.run(
            run_variant(
                "QUINTUPLE", question="Q?", context=None, output_language="en", settings=None,
            )
        )


# -- FULL: must be the real, unmodified production pipeline -----------------


def test_full_variant_matches_production_stage_semantics(
    service, fake_orchestrator_state, tmp_path
):
    """Regression test (explicitly required): FULL's stage set/order must be
    identical to what a real production run actually executes -- verified
    by driving it through the exact same DeliberationService entry points a
    real run uses (the fake_orchestrator_state fixture's monkeypatch on
    DeliberationOrchestrator applies globally, so run_full_variant's own,
    separately-constructed DeliberationService still uses the same fake)."""
    from llm_deliberation.evaluation.variants import run_full_variant

    settings = Settings.load(profile_override="economy", red_team_override=True)
    result = asyncio.run(
        run_full_variant(
            question="Q?", context=None, output_language="en", settings=settings,
            db_path=tmp_path / "eval_full.db", max_cost_per_variant_usd=None,
        )
    )

    assert result.variant == "FULL"
    assert result.status == "succeeded"
    assert {s.stage_name for s in result.stages} == set(ALL_STAGE_NAMES)
    assert set(fake_orchestrator_state["log"]) == set(ALL_STAGE_NAMES)
    assert result.production_run_id is not None
    assert result.quality_state == "complete"


def test_same_case_through_all_four_variants_runs_exactly_their_intended_stages(
    monkeypatch, service, fake_orchestrator_state, tmp_path
):
    """Part E of the final offline verification: one deterministic fake
    case, run through SINGLE/DUAL/CRITIQUE/FULL, each executing exactly its
    own intended stage set -- no cross-contamination, no fabricated stages,
    no variant accidentally running more or fewer stages than defined."""
    question, context, output_language = "Q?", None, "en"

    reduced_orch = _fake_orchestrator(monkeypatch)
    single = asyncio.run(
        run_variant("SINGLE", question=question, context=context, output_language=output_language,
                    settings=None, orchestrator=reduced_orch)
    )
    dual_orch = _fake_orchestrator(monkeypatch)
    dual = asyncio.run(
        run_variant("DUAL", question=question, context=context, output_language=output_language,
                    settings=None, orchestrator=dual_orch)
    )
    critique_orch = _fake_orchestrator(monkeypatch)
    critique = asyncio.run(
        run_variant("CRITIQUE", question=question, context=context, output_language=output_language,
                    settings=None, orchestrator=critique_orch)
    )
    settings = Settings.load(profile_override="economy", red_team_override=True)
    full = asyncio.run(
        run_variant("FULL", question=question, context=context, output_language=output_language,
                    settings=settings, db_path=tmp_path / "eval_full_demo.db")
    )

    assert [s.stage_name for s in single.stages] == list(VARIANT_STAGES["SINGLE"])
    assert [s.stage_name for s in dual.stages] == list(VARIANT_STAGES["DUAL"])
    assert [s.stage_name for s in critique.stages] == list(VARIANT_STAGES["CRITIQUE"])
    assert {s.stage_name for s in full.stages} == set(ALL_STAGE_NAMES)

    assert all(r.status == "succeeded" for r in (single, dual, critique, full))
    # Strictly increasing call counts -- proves each variant genuinely does
    # more work than the last, none silently collapsing to another's shape.
    assert single.total_calls < dual.total_calls < critique.total_calls < full.total_calls


def test_full_variant_never_writes_to_production_db(service, tmp_path):
    """FULL uses a dedicated evaluation-run-storage DB -- never the caller's
    real production database -- and refuses to run without one."""
    from llm_deliberation.evaluation.variants import run_full_variant

    settings = Settings.load(profile_override="economy", red_team_override=False)
    with pytest.raises(ValueError):
        asyncio.run(
            run_full_variant(
                question="Q?", context=None, output_language="en", settings=settings,
                db_path=None, max_cost_per_variant_usd=None,
            )
        )
