"""Regression test: Stage 0 (question framing, ADR 009) stays unreachable
from the evaluation harness, exactly as the user explicitly required --
"Keep Stage 0 out of the evaluation harness for now". FULL is the only
variant that persists a real run row (via DeliberationService.create_run/
start_run); it must never create a stage0 row, since it never passes
question_framing_enabled. No provider calls (FakeOrchestrator, see
tests/conftest.py); no Case 3 data.
"""
from __future__ import annotations

import asyncio

from llm_deliberation.config import Settings
from llm_deliberation.evaluation.variants import run_variant


def test_full_variant_never_creates_a_stage0_row(service, fake_orchestrator_state, tmp_path):
    settings = Settings.load(profile_override="economy", red_team_override=False)
    db_path = tmp_path / "eval_full.db"

    result = asyncio.run(
        run_variant(
            "FULL",
            question="Q?",
            context=None,
            output_language="en",
            settings=settings,
            db_path=db_path,
        )
    )

    from llm_deliberation.service import DeliberationService

    eval_service = DeliberationService(db_path)
    record = eval_service.get_run(result.production_run_id)
    assert record.stage0 is None
