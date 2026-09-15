from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

from llm_deliberation.config import PROFILES, Settings, default_red_team_enabled
from llm_deliberation.orchestrator import (
    ALL_STAGE_NAMES,
    SKIPPABLE_STAGE_NAMES,
    WAVES,
    DeliberationOrchestrator,
)
from llm_deliberation.prompts import SUPPORTED_LANGUAGES
from llm_deliberation.providers import ProviderGenerationError
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
        language: str = "en",
    ) -> str:
        if profile not in PROFILES:
            valid = ", ".join(sorted(PROFILES))
            raise ValueError(f"Unknown profile '{profile}'. Choose one of: {valid}")
        if not question or not question.strip():
            raise ValueError("question must not be empty")
        if language not in SUPPORTED_LANGUAGES:
            valid = ", ".join(sorted(SUPPORTED_LANGUAGES))
            raise ValueError(f"Unknown language '{language}'. Choose one of: {valid}")

        enabled = default_red_team_enabled() if red_team_enabled is None else red_team_enabled

        run_id = uuid.uuid4().hex[:12]
        self.repo.insert_run(
            run_id,
            question=question,
            context=context,
            profile=profile,
            red_team_enabled=enabled,
            language=language,
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
                requested_model=stage.requested_model,
                fallback_used=stage.fallback_used,
                fallback_reason=stage.fallback_reason,
                model_attempts=stage.model_attempts,
                attempt_log=stage.attempt_log,
            )

        def optional_response(name: str) -> ModelResponse | None:
            stage = stages.get(name)
            return response(name) if stage is not None and stage.status == "succeeded" else None

        return RunResult(
            question=record.question,
            profile=record.profile,
            red_team_enabled=record.red_team_enabled,
            language=record.language,
            analysis_a=response("analysis_a"),
            analysis_b=response("analysis_b"),
            critique_a_of_b=response("critique_a_of_b"),
            critique_b_of_a=response("critique_b_of_a"),
            red_team=optional_response("red_team"),
            revision_a=response("revision_a"),
            revision_b=response("revision_b"),
            convergence=optional_response("convergence_analysis"),
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

    async def retry_stage(
        self, run_id: str, stage_id: int | str, *, gemini_mode: str = "chain"
    ) -> RunRecord:
        stage = self.repo.get_stage(run_id, stage_id)
        if stage.status == "succeeded":
            raise ValueError(
                f"Stage '{stage.name}' on run {run_id} already succeeded; nothing to retry."
            )
        if gemini_mode != "chain" and stage.name != "red_team":
            raise ValueError(
                f"gemini_mode={gemini_mode!r} only applies to the red_team stage, "
                f"not '{stage.name}'."
            )
        self.repo.reset_stage(stage.id)
        self.repo.update_run(run_id, status="running", set_started_if_unset=True)
        return await self._execute(run_id, gemini_mode=gemini_mode)

    async def skip_stage(self, run_id: str, stage_id: int | str) -> RunRecord:
        """Skip an optional stage and let the rest of the run continue.

        Only applies to SKIPPABLE_STAGE_NAMES (red_team, convergence_analysis)
        -- the stages the pipeline treats as optional after a failure (red_team
        can also be disabled entirely up front). Skipping reuses the same
        "absent, not an error" handling downstream stages already tolerate:
        revision/synthesis proceed without a red-team report, and synthesis
        proceeds without convergence context, exactly as if that stage had
        never been enabled.
        """
        stage = self.repo.get_stage(run_id, stage_id)
        if stage.name not in SKIPPABLE_STAGE_NAMES:
            raise ValueError(f"Stage '{stage.name}' cannot be skipped.")
        if stage.status == "succeeded":
            raise ValueError(
                f"Stage '{stage.name}' on run {run_id} already succeeded; nothing to skip."
            )
        reason = (
            "Skipped by user after the Gemini red-team stage could not complete."
            if stage.name == "red_team"
            else "Skipped by user after convergence analysis could not complete."
        )
        self.repo.mark_stage_skipped(stage.id, reason=reason)
        self.repo.update_run(run_id, status="running", set_started_if_unset=True)
        return await self._execute(run_id)

    async def _execute(self, run_id: str, *, gemini_mode: str = "chain") -> RunRecord:
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
                elif stage.status == "skipped":
                    # Resolved as deliberately absent, not pending and not a
                    # failure -- downstream prompts already tolerate a
                    # missing red-team report (texts.get("red_team")).
                    continue
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
                    orchestrator.run_stage(
                        name, effective_question, texts,
                        gemini_mode=gemini_mode, language=run.language,
                    )
                    for name in pending_names
                ),
                return_exceptions=True,
            )

            for name, outcome in zip(pending_names, results):
                stage = stages_by_name[name]
                if isinstance(outcome, BaseException):
                    if isinstance(outcome, ProviderGenerationError):
                        self.repo.mark_stage_failed(
                            stage.id,
                            error=str(outcome),
                            requested_model=outcome.requested_model,
                            fallback_used=outcome.fallback_used,
                            fallback_reason=outcome.fallback_reason,
                            model_attempts=outcome.attempts,
                            attempt_log=outcome.attempt_log,
                            estimated_cost_usd=outcome.estimated_cost_usd,
                            failure_reason=outcome.reason,
                        )
                    else:
                        self.repo.mark_stage_failed(
                            stage.id, error=str(outcome), failure_reason="provider_error"
                        )
                    run_failed = True
                elif outcome.incomplete_reason is not None:
                    # The provider call technically returned, but the
                    # artifact is empty or was truncated by an output-length
                    # ceiling even after the one bounded recovery attempt
                    # (see orchestrator.run_stage) -- this must NOT be
                    # recorded as a healthy stage. Cost/provenance are
                    # preserved exactly as a normal success would, since the
                    # call was genuinely made (and, for a truncated case, may
                    # include a discarded recovery attempt's sunk cost too).
                    reason_text = {
                        "empty_output": "Provider returned an empty response (no visible text).",
                        "output_truncated": (
                            "Provider response was truncated by the output length limit "
                            "(even after one automatic retry, where applicable)."
                        ),
                    }.get(outcome.incomplete_reason, "Provider response was unusable.")
                    self.repo.mark_stage_failed(
                        stage.id,
                        error=reason_text,
                        requested_model=outcome.requested_model,
                        fallback_used=outcome.fallback_used,
                        fallback_reason=outcome.fallback_reason,
                        model_attempts=outcome.model_attempts,
                        attempt_log=outcome.attempt_log,
                        estimated_cost_usd=outcome.estimated_cost_usd,
                        failure_reason=outcome.incomplete_reason,
                    )
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
                        requested_model=outcome.requested_model,
                        fallback_used=outcome.fallback_used,
                        fallback_reason=outcome.fallback_reason,
                        model_attempts=outcome.model_attempts,
                        attempt_log=outcome.attempt_log,
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
