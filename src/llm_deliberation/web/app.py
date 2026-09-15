from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Coroutine

import jinja2
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from llm_deliberation import convergence, report
from llm_deliberation.config import default_profile, default_red_team_enabled
from llm_deliberation.service import DeliberationService
from llm_deliberation.store import RunRecord
from llm_deliberation.web.markdown_render import render_markdown_safe, split_synthesis_sections
from llm_deliberation.web.presenter import (
    ARTIFACT_SECTIONS,
    PROFILE_BLURBS,
    PROFILE_ORDER,
    build_pipeline,
    elapsed_seconds,
    format_datetime,
    format_duration,
)

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

HOST = "127.0.0.1"
PORT = 8765


def _missing_keys() -> list[str]:
    missing = []
    if not os.getenv("OPENAI_API_KEY"):
        missing.append("OPENAI_API_KEY")
    if not os.getenv("ANTHROPIC_API_KEY"):
        missing.append("ANTHROPIC_API_KEY")
    return missing


def _sse(event: str, data: str) -> str:
    lines = data.splitlines() or [""]
    payload = "\n".join(f"data: {line}" for line in lines)
    return f"event: {event}\n{payload}\n\n"


def _profile_context(**overrides: object) -> dict:
    base = {
        "profiles": [(key, PROFILE_BLURBS[key]) for key in PROFILE_ORDER],
        "default_profile": default_profile(),
        "default_red_team": default_red_team_enabled(),
        "missing_keys": _missing_keys(),
        "error": None,
        "question_value": None,
        "context_value": None,
    }
    base.update(overrides)
    return base


def create_app(service: DeliberationService | None = None) -> FastAPI:
    """Build the FastAPI app around a DeliberationService instance.

    A factory (rather than a bare module-level app) so tests can point at an
    isolated, temporary-database service without touching the real one.
    """
    app = FastAPI(title="LLM Deliberation")
    app.state.service = service or DeliberationService()
    app.state.running = set()  # run_ids with an active background task

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["fmt_datetime"] = format_datetime
    # Browser-presentation-only Markdown rendering (see markdown_render.py's
    # module docstring): the stored artifact and Markdown export never go
    # through this -- only what a Jinja template chooses to pipe through it.
    templates.env.filters["render_markdown"] = render_markdown_safe
    # Best-effort structural split of already-rendered synthesis HTML into
    # a prominent section + collapsible detail sections. Returns None (no
    # split) when the structure isn't confidently detected -- see
    # split_synthesis_sections' docstring. Presentation reorganization only:
    # it never infers meaning, just groups by existing heading boundaries.
    templates.env.filters["split_sections"] = split_synthesis_sections
    # Fail loudly on a missing template variable instead of silently
    # rendering blank -- caught a real bug (missing run_id/status in the
    # run_detail context) during development.
    templates.env.undefined = jinja2.StrictUndefined
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def render_pipeline_fragment(record: RunRecord) -> str:
        groups = build_pipeline(record)
        return templates.get_template("partials/pipeline.html").render(
            groups=groups, run_id=record.id, status=record.status
        )

    def reserve(run_id: str) -> bool:
        running: set[str] = app.state.running
        if run_id in running:
            return False
        running.add(run_id)
        return True

    def release(run_id: str) -> None:
        app.state.running.discard(run_id)

    def schedule(
        background_tasks: BackgroundTasks, run_id: str, coro: Coroutine
    ) -> None:
        """Run `coro` in the background, guarded against double-execution.

        If run_id is already executing, the (not-yet-started) coroutine is
        discarded and nothing new is scheduled -- a double click on Resume
        or Retry becomes a harmless no-op instead of a race.
        """
        if not reserve(run_id):
            coro.close()
            return

        async def wrapper() -> None:
            try:
                await coro
            finally:
                release(run_id)

        background_tasks.add_task(wrapper)

    # -- new deliberation ----------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(request, "index.html", _profile_context())

    @app.post("/runs")
    async def submit_run(
        request: Request,
        background_tasks: BackgroundTasks,
        question: str = Form(...),
        context: str = Form(""),
        profile: str = Form(...),
        red_team: str | None = Form(None),
    ):
        svc: DeliberationService = request.app.state.service
        question = question.strip()
        context_value = context.strip() or None

        if not question:
            return templates.TemplateResponse(
                request,
                "index.html",
                _profile_context(
                    default_profile=profile,
                    default_red_team=bool(red_team),
                    error="Question cannot be empty.",
                    question_value=question,
                    context_value=context_value,
                ),
                status_code=400,
            )

        try:
            run_id = svc.create_run(
                question=question,
                profile=profile,
                red_team_enabled=bool(red_team),
                context=context_value,
            )
        except ValueError as exc:
            return templates.TemplateResponse(
                request,
                "index.html",
                _profile_context(
                    default_profile=profile,
                    default_red_team=bool(red_team),
                    error=str(exc),
                    question_value=question,
                    context_value=context_value,
                ),
                status_code=400,
            )

        schedule(background_tasks, run_id, svc.start_run(run_id))
        return RedirectResponse(url=f"/runs/{run_id}", status_code=303)

    # -- run detail / live status ---------------------------------------

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    async def run_detail(request: Request, run_id: str):
        svc: DeliberationService = request.app.state.service
        try:
            record = svc.get_run(run_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Run not found")

        context: dict = {
            "record": record,
            "run_id": run_id,
            "status": record.status,
            "cost": f"${record.estimated_total_cost_usd:.4f}",
            "elapsed": format_duration(elapsed_seconds(record)),
        }

        if record.status == "succeeded":
            result = svc.to_run_result(run_id)
            artifacts = {
                "analysis_a": result.analysis_a,
                "analysis_b": result.analysis_b,
                "critique_a_of_b": result.critique_a_of_b,
                "critique_b_of_a": result.critique_b_of_a,
                "revision_a": result.revision_a,
                "revision_b": result.revision_b,
            }
            if result.red_team is not None:
                artifacts["red_team"] = result.red_team
            evolution = None
            if result.convergence is not None:
                artifacts["convergence_analysis"] = result.convergence
                evolution = convergence.parse_convergence_analysis(result.convergence.text)
            context.update(
                {
                    "result": result,
                    "artifacts": artifacts,
                    "artifact_sections": ARTIFACT_SECTIONS,
                    "evolution": evolution,
                }
            )
        else:
            context["groups"] = build_pipeline(record)

        return templates.TemplateResponse(request, "run_detail.html", context)

    @app.get("/runs/{run_id}/status", response_class=HTMLResponse)
    async def run_status(request: Request, run_id: str):
        svc: DeliberationService = request.app.state.service
        try:
            record = svc.get_run(run_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Run not found")
        return HTMLResponse(render_pipeline_fragment(record))

    @app.get("/runs/{run_id}/events")
    async def run_events(request: Request, run_id: str):
        svc: DeliberationService = request.app.state.service
        try:
            svc.get_run(run_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Run not found")

        async def event_stream():
            while True:
                try:
                    record = svc.get_run(run_id)
                except KeyError:
                    return
                yield _sse("pipeline", render_pipeline_fragment(record))
                still_running = run_id in request.app.state.running
                if record.status in ("succeeded", "failed") and not still_running:
                    yield _sse("done", run_id)
                    return
                await asyncio.sleep(1.0)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # -- retry / resume ---------------------------------------------------

    @app.post("/runs/{run_id}/resume")
    async def resume(request: Request, run_id: str, background_tasks: BackgroundTasks):
        svc: DeliberationService = request.app.state.service
        try:
            svc.get_run(run_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Run not found")
        schedule(background_tasks, run_id, svc.resume_run(run_id))
        return RedirectResponse(url=f"/runs/{run_id}", status_code=303)

    @app.post("/runs/{run_id}/stages/{stage}/retry")
    async def retry_stage(
        request: Request,
        run_id: str,
        stage: str,
        background_tasks: BackgroundTasks,
        mode: str = Form("chain"),
    ):
        svc: DeliberationService = request.app.state.service
        try:
            stage_record = svc.repo.get_stage(run_id, stage)
        except KeyError:
            raise HTTPException(status_code=404, detail="Stage not found")
        if mode not in ("chain", "preferred_only"):
            raise HTTPException(status_code=400, detail=f"Unknown retry mode: {mode!r}")
        if mode != "chain" and stage_record.name != "red_team":
            raise HTTPException(
                status_code=400,
                detail=f"mode={mode!r} only applies to the red_team stage.",
            )
        schedule(background_tasks, run_id, svc.retry_stage(run_id, stage, gemini_mode=mode))
        return RedirectResponse(url=f"/runs/{run_id}", status_code=303)

    @app.post("/runs/{run_id}/stages/{stage}/skip")
    async def skip_stage(
        request: Request, run_id: str, stage: str, background_tasks: BackgroundTasks
    ):
        svc: DeliberationService = request.app.state.service
        try:
            svc.repo.get_stage(run_id, stage)
        except KeyError:
            raise HTTPException(status_code=404, detail="Stage not found")
        schedule(background_tasks, run_id, svc.skip_stage(run_id, stage))
        return RedirectResponse(url=f"/runs/{run_id}", status_code=303)

    # -- export / history -------------------------------------------------

    @app.get("/runs/{run_id}/export")
    async def export_run(request: Request, run_id: str):
        svc: DeliberationService = request.app.state.service
        try:
            result = svc.to_run_result(run_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Run not found")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        markdown = report.render_markdown(result)
        return Response(
            content=markdown,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="deliberation-{run_id}.md"'
            },
        )

    @app.get("/history", response_class=HTMLResponse)
    async def history(request: Request):
        svc: DeliberationService = request.app.state.service
        runs = svc.list_runs(limit=100)
        return templates.TemplateResponse(request, "history.html", {"runs": runs})

    return app


def main() -> None:
    import uvicorn

    url = f"http://{HOST}:{PORT}"
    print(f"llm-deliberation UI starting at {url}")
    print("Bound to 127.0.0.1 only (not reachable from other machines).")
    print("Press Ctrl+C to stop.")
    uvicorn.run(create_app(), host=HOST, port=PORT)


if __name__ == "__main__":
    main()
