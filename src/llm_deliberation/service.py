from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

from llm_deliberation.config import PROFILES, Settings, default_red_team_enabled
from llm_deliberation.orchestrator import ALL_STAGE_NAMES, WAVES, DeliberationOrchestrator
from llm_deliberation.store import DEFAULT_DB_PATH, Repository, RunRecord, utc_now_iso
from llm_deliberation.types import ModelResponse, RunResult, Usage


def _resolve_db_path(db_path: str | Path | None) -> str | Path:
    if db_path is not None:
        return db_path
    env_path = os.getenv("LLM_DELIBERATION_DB")
    if env_path:
        return env_path
    return DEFAULT_DB_PATH


class DeliberationService:
    """Durable, resumable deliberation runs backed by SQLite.

    This is the seam future consumers (browser UI, Windows launcher, editor
    integrations) are meant to use instead of talking to the orchestrator or
    the database directly.
    """

    def __init__(self, db_path: str | Path | None = None):
        self.repo = Repository(_resolve_db_path(db_path))

    def close(self) -> None:
        self.repo.close()

    # -- creation & inspection ----------------------------------------

    def create_run(
        self,
        question: str,
        profile: str,
        red_team_enabled: bool | None = None,
        context: str | None = None,
    ) -> str:
        if profile not in PROFILES:
            valid = ", ".join(sorted(PROFILES))
            raise ValueError(f"Unknown profile '{profile}'. Choose one of: {valid}")
        if not question or not question.strip():
            raise ValueError("question must not be empty")

        enabled = default_red_team_enabled() if red_team_enabled is None else red_team_enabled

        run_id = uuid.uuid4().hex[:12]
        self.repo.insert_run(
            run_id,
            question=question,
            context=context,
            profile=profile,
            red_team_enabled=enabled,
        )
        for name in ALL_STAGE_NAMES:
            if name == "red_team" and not enabled:
                continue
            self.repo.insert_stage(run_id, name)
        return run_id

    def get_run(self, run_id: str) -> RunRecord:
        return self.repo.get_run_full(run_id)

    def list_runs(
        self, *, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[RunRecord]:
        return self.repo.list_runs(status=status, limit=limit, offset=offset)

    def to_run_result(self, run_id: str) -> RunResult:
        """Reconstruct a RunResult (for Markdown export) from stored data.

        Requires every non-optional stage to have succeeded.
        """
        record = self.get_run(run_id)
        stages = {s.name: s for s in record.stages}

        def response(name: str) -> ModelResponse:
            stage = stages.get(name)
            if stage is None or stage.status != "succeeded" or stage.text is None:
                raise ValueError(
                    f"Run {run_id} stage '{name}' has not succeeded; "
                    "cannot build a full result yet."
                )
            return ModelResponse(
                provider=stage.provider or "",
                model=stage.model or "",
                text=stage.text,
                usage=Usage(
                    input_tokens=stage.input_tokens, output_tokens=stage.output_tokens
                ),
                estimated_cost_usd=stage.estimated_cost_usd,
            )

        red_stage = stages.get("red_team")
        red_team = (
            response("red_team")
            if red_stage is not None and red_stage.status == "succeeded"
            else None
        )

        return RunResult(
            question=record.question,
            profile=record.profile,
            red_team_enabled=record.red_team_enabled,
            analysis_a=response("analysis_a"),
            analysis_b=response("analysis_b"),
            critique_a_of_b=response("critique_a_of_b"),
            critique_b_of_a=response("critique_b_of_a"),
            red_team=red_team,
            revision_a=response("revision_a"),
            revision_b=response("revision_b"),
            synthesis=response("synthesis"),
            context=record.context,
        )

    # -- execution ------------------------------------------------------

    async def start_run(self, run_id: str) -> RunRecord:
        run = self.repo.get_run(run_id)
        if run.status != "pending":
            raise ValueError(
                f"Run {run_id} is '{run.status}', not 'pending'. "
                "Use resume_run() or retry_stage() instead."
            )
        self.repo.update_run(run_id, status="running", set_started_if_unset=True)
        return await self._execute(run_id)

    async def resume_run(self, run_id: str) -> RunRecord:
        run = self.repo.get_run(run_id)
        if run.status == "succeeded":
            return self.get_run(run_id)
        self.repo.update_run(run_id, status="running", set_started_if_unset=True)
        return await self._execute(run_id)

    async def retry_stage(self, run_id: str, stage_id: int | str) -> RunRecord:
        stage = self.repo.get_stage(run_id, stage_id)
        if stage.status == "succeeded":
            raise ValueError(
                f"Stage '{stage.name}' on run {run_id} already succeeded; nothing to retry."
            )
        self.repo.reset_stage(stage.id)
        self.repo.update_run(run_id, status="running", set_started_if_unset=True)
        return await self._execute(run_id)

    async def _execute(self, run_id: str) -> RunRecord:
        run = self.repo.get_run(run_id)
        try:
            settings = Settings.load(
                profile_override=run.profile, red_team_override=run.red_team_enabled
            )
            settings.validate_keys()
            orchestrator = DeliberationOrchestrator(settings)
        except Exception as exc:
            # Setup failed before any stage could even attempt to run (e.g. a
            # missing API key). Surface it on the first not-yet-succeeded stage
            # so the run doesn't get stuck in "running" with nowhere to show
            # the error, and mark the run itself failed.
            stages = self.repo.list_stages(run_id)
            first_pending = next((s for s in stages if s.status != "succeeded"), None)
            if first_pending is not None:
                self.repo.mark_stage_failed(first_pending.id, error=str(exc))
            self.repo.update_run(run_id, status="failed")
            return self.get_run(run_id)

        effective_question = run.question
        if run.context:
            effective_question = f"{run.question}\n\nADDITIONAL CONTEXT:\n{run.context}"

        stages_by_name = {s.name: s for s in self.repo.list_stages(run_id)}
        texts: dict[str, str] = {}
        run_failed = False

        for wave in WAVES:
            wave_stage_names = [name for name in wave if name in stages_by_name]
            if not wave_stage_names:
                continue

            pending_names: list[str] = []
            for name in wave_stage_names:
                stage = stages_by_name[name]
                if stage.status == "succeeded" and stage.text is not None:
                    texts[name] = stage.text
                else:
                    pending_names.append(name)

            if run_failed:
                break
            if not pending_names:
                continue

            for name in pending_names:
                self.repo.mark_stage_running(stages_by_name[name].id)

            results = await asyncio.gather(
                *(
                    orchestrator.run_stage(name, effective_question, texts)
                    for name in pending_names
                ),
                return_exceptions=True,
            )

            for name, outcome in zip(pending_names, results):
                stage = stages_by_name[name]
                if isinstance(outcome, BaseException):
                    self.repo.mark_stage_failed(stage.id, error=str(outcome))
                    run_failed = True
                else:
                    self.repo.mark_stage_succeeded(
                        stage.id,
                        provider=outcome.provider,
                        model=outcome.model,
                        text=outcome.text,
                        input_tokens=outcome.usage.input_tokens,
                        output_tokens=outcome.usage.output_tokens,
                        estimated_cost_usd=outcome.estimated_cost_usd,
                    )
                    texts[name] = outcome.text

            self.repo.update_run(
                run_id, estimated_total_cost_usd=self.repo.sum_stage_costs(run_id)
            )

            if run_failed:
                break

        total_cost = self.repo.sum_stage_costs(run_id)
        if run_failed:
            self.repo.update_run(
                run_id, status="failed", estimated_total_cost_usd=total_cost
            )
        else:
            self.repo.update_run(
                run_id,
                status="succeeded",
                completed_at=utc_now_iso(),
                estimated_total_cost_usd=total_cost,
            )
        return self.get_run(run_id)
