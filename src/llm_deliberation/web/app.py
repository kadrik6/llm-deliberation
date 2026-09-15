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
from llm_deliberation.config import PROFILES, Settings, default_profile, default_red_team_enabled
from llm_deliberation.cost_budget import InvalidBudgetError, estimate_run_cost_range, format_usd
from llm_deliberation.prompts import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES
from llm_deliberation.readiness import ReadinessReport
from llm_deliberation.service import (
    MAX_QUESTION_LENGTH,
    QUESTION_LENGTH_WARNING_THRESHOLD,
    RECOMMENDED_QUESTION_LENGTH,
    DeliberationService,
    QuestionTooLongError,
)
from llm_deliberation.store import RunRecord
from llm_deliberation.web.i18n import (
    DEFAULT_UI_LANGUAGE,
    NATIVE_LANGUAGE_NAMES,
    SUPPORTED_UI_LANGUAGES,
    count_label,
    translate,
)
from llm_deliberation.web.markdown_render import render_markdown_safe, split_synthesis_sections
from llm_deliberation.web.presenter import (
    ARTIFACT_SECTIONS,
    PROFILE_BLURB_KEYS,
    PROFILE_ORDER,
    attempt_log_phases,
    build_pipeline,
    compute_deliberation_quality,
    elapsed_seconds,
    format_datetime,
    format_duration,
    group_attempts_by_model,
    is_terminal_run_status,
)

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

HOST = "127.0.0.1"
PORT = 8765

UI_LANG_COOKIE = "ui_lang"


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


def _ui_lang(request: Request) -> str:
    """The viewer's interface-chrome language, from a plain cookie only --
    never inferred from a run, never persisted server-side. See web/i18n.py.
    """
    lang = request.cookies.get(UI_LANG_COOKIE)
    return lang if lang in SUPPORTED_UI_LANGUAGES else DEFAULT_UI_LANGUAGE


def _cost_estimates(red_team_enabled: bool) -> dict[str, str]:
    """Deterministic pre-run cost range per profile (see cost_budget.py) at
    the given red-team on/off state. Not reactive to the actual typed
    question/context (see cost_budget.estimate_run_cost_range's docstring)
    -- a declared approximation, always shown as a range, never a single
    number claiming precision it doesn't have.
    """
    estimates = {}
    for profile in PROFILE_ORDER:
        settings = Settings.load(profile_override=profile, red_team_override=red_team_enabled)
        estimate = estimate_run_cost_range(
            openai_model=settings.openai_model,
            anthropic_model=settings.anthropic_model,
            gemini_model=settings.gemini_model,
            convergence_provider=settings.convergence_provider,
            convergence_model=settings.convergence_model,
            red_team_enabled=red_team_enabled,
            max_output_tokens=settings.max_output_tokens,
        )
        estimates[profile] = f"{format_usd(estimate.low_usd)}–{format_usd(estimate.high_usd)}"
    return estimates


def _profile_context(*, ui_lang: str, **overrides: object) -> dict:
    red_team_default = overrides.get("default_red_team", default_red_team_enabled())
    base = {
        "ui_lang": ui_lang,
        "profiles": [(key, PROFILE_BLURB_KEYS[key]) for key in PROFILE_ORDER],
        "default_profile": default_profile(),
        "default_red_team": default_red_team_enabled(),
        # New-run output language defaults to the viewer's UI language, but
        # remains a fully independent, overridable form field (see
        # index.html) -- never forced, never inferred from the question.
        "default_language": ui_lang if ui_lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE,
        "language_options": [(code, NATIVE_LANGUAGE_NAMES[code]) for code in SUPPORTED_LANGUAGES],
        "missing_keys": _missing_keys(),
        "error": None,
        "question_value": None,
        "context_value": None,
        # UX-only length guidance for the Question field (see service.py,
        # the single authoritative source of these numbers) -- never
        # enforced by the browser alone, see submit_run's server-side check.
        "question_max_length": MAX_QUESTION_LENGTH,
        "question_recommended_length": RECOMMENDED_QUESTION_LENGTH,
        "question_warn_threshold": QUESTION_LENGTH_WARNING_THRESHOLD,
        "cost_estimates": _cost_estimates(bool(red_team_default)),
        "max_run_cost_value": None,
        # None = "Check providers" not yet pressed for this page view (see
        # index.html); otherwise a readiness.ReadinessReport from a manual
        # "Check providers" click or a blocked submit_run attempt.
        "readiness": None,
        "gemini_unavailable_choice": False,
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
    templates.env.filters["group_attempts_by_model"] = group_attempts_by_model
    templates.env.filters["attempt_log_phases"] = attempt_log_phases

    @jinja2.pass_context
    def _translate_filter(context: jinja2.runtime.Context, key: str) -> str:
        return translate(key, context.get("ui_lang", DEFAULT_UI_LANGUAGE))

    @jinja2.pass_context
    def _count_label_filter(context: jinja2.runtime.Context, count: int, key: str) -> str:
        return count_label(key, count, context.get("ui_lang", DEFAULT_UI_LANGUAGE))

    # UI-chrome translation only (see web/i18n.py) -- {{ "key"|t }} reads
    # whatever "ui_lang" is in that template's own render context, never a
    # run's own output language.
    templates.env.filters["t"] = _translate_filter
    templates.env.filters["count_label"] = _count_label_filter
    # Fail loudly on a missing template variable instead of silently
    # rendering blank -- caught a real bug (missing run_id/status in the
    # run_detail context) during development.
    templates.env.undefined = jinja2.StrictUndefined
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def render_pipeline_fragment(record: RunRecord, ui_lang: str) -> str:
        groups = build_pipeline(record)
        return templates.get_template("partials/pipeline.html").render(
            groups=groups,
            run_id=record.id,
            status=record.status,
            ui_lang=ui_lang,
            # Included here (not only in run_detail.html) so a run that
            # transitions to "failed" over the live SSE connection -- which
            # replaces only this fragment's innerHTML, deliberately without a
            # page reload (see app.js) -- still gets an up-to-date quality
            # summary without requiring a manual refresh. None while still
            # running, exactly as on a fresh page load.
            quality=compute_deliberation_quality(record),
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
        return templates.TemplateResponse(
            request, "index.html", _profile_context(ui_lang=_ui_lang(request))
        )

    @app.post("/providers/check")
    async def check_providers(
        request: Request,
        profile: str = Form(...),
        red_team: str | None = Form(None),
        language: str = Form(DEFAULT_LANGUAGE),
        question: str = Form(""),
        context: str = Form(""),
        max_run_cost: str = Form(""),
    ):
        """Manual "Check providers" action -- always a forced recheck (a
        deliberate user click bypasses the readiness cache; see
        readiness.py). Re-renders the New Deliberation form with whatever
        the visitor had already typed preserved, exactly like submit_run's
        own validation-error round-trip.
        """
        svc: DeliberationService = request.app.state.service
        ui_lang = _ui_lang(request)
        report_result = svc.check_readiness(profile, bool(red_team), force=True)
        return templates.TemplateResponse(
            request,
            "index.html",
            _profile_context(
                ui_lang=ui_lang,
                default_profile=profile,
                default_red_team=bool(red_team),
                default_language=language,
                question_value=question.strip() or None,
                context_value=context.strip() or None,
                max_run_cost_value=max_run_cost.strip() or None,
                readiness=report_result,
                gemini_unavailable_choice=report_result.gemini_blocked,
            ),
        )

    @app.get("/ui-language/{lang}")
    async def set_ui_language(lang: str, next: str = "/"):
        """Switch the viewer's interface-chrome language. A plain cookie,
        set via a GET link (no JS required) -- this never touches any run's
        stored data or output language; see web/i18n.py."""
        if lang not in SUPPORTED_UI_LANGUAGES:
            raise HTTPException(status_code=404, detail="Unsupported UI language")
        response = RedirectResponse(url=next or "/", status_code=303)
        response.set_cookie(UI_LANG_COOKIE, lang, max_age=60 * 60 * 24 * 365, samesite="lax")
        return response

    @app.post("/runs")
    async def submit_run(
        request: Request,
        background_tasks: BackgroundTasks,
        question: str = Form(...),
        context: str = Form(""),
        profile: str = Form(...),
        red_team: str | None = Form(None),
        language: str = Form(DEFAULT_LANGUAGE),
        max_run_cost: str = Form(""),
        # Set only by the distinct "Start without red-team" button shown
        # after a Gemini-unavailable notice (see index.html) -- an explicit
        # user choice, never an automatic/silent fallback (Section 3 of the
        # task brief: "Do not silently disable red-team").
        confirm_no_red_team: str | None = Form(None),
    ):
        svc: DeliberationService = request.app.state.service
        ui_lang = _ui_lang(request)
        question = question.strip()
        context_value = context.strip() or None
        red_team_requested = bool(red_team) and not bool(confirm_no_red_team)

        def _rerender(*, error: str | None, status_code: int = 400, **extra: object):
            fields: dict[str, object] = {
                "ui_lang": ui_lang,
                "default_profile": profile,
                "default_red_team": red_team_requested,
                "default_language": language,
                "error": error,
                "question_value": question,
                "context_value": context_value,
                "max_run_cost_value": max_run_cost.strip() or None,
            }
            fields.update(extra)  # e.g. gemini_unavailable_choice overrides default_red_team to False
            return templates.TemplateResponse(
                request,
                "index.html",
                _profile_context(**fields),
                status_code=status_code,
            )

        if not question:
            return _rerender(error="Question cannot be empty.")

        # Provider readiness preflight -- uses the readiness cache (a fresh
        # cached result is reused, so pressing Start right after "Check
        # providers" never re-probes), and never creates a run or a paid
        # deliberation stage when a required provider isn't ready (Section 3
        # of the task brief).
        readiness_report = svc.check_readiness(profile, red_team_requested)
        if not readiness_report.required_ready:
            return _rerender(
                error=translate("readiness_required_failed_notice", ui_lang),
                readiness=readiness_report,
            )
        if readiness_report.gemini_blocked:
            # Explicit choice, never a silent downgrade: the form re-renders
            # with red-team's checkbox now off and a clear notice + a
            # distinct "Start without red-team" button (which sets
            # confirm_no_red_team on the next submit) alongside the normal
            # Start button (effectively "Check again" once red-team is
            # re-checked/re-enabled) and the user's browser Back button
            # ("Cancel").
            return _rerender(
                error=None,
                status_code=200,  # a choice to make, not a validation error
                readiness=readiness_report,
                gemini_unavailable_choice=True,
                default_red_team=False,
            )

        try:
            run_id = svc.create_run(
                question=question,
                profile=profile,
                red_team_enabled=red_team_requested,
                context=context_value,
                language=language,
                max_run_cost_usd=max_run_cost.strip() or None,
            )
        except QuestionTooLongError:
            # A UX safeguard (see service.MAX_QUESTION_LENGTH), never a
            # provider/model call -- create_run raises before any stage
            # row or run row is inserted, so no run exists at this point.
            return _rerender(error=translate("question_too_long_error", ui_lang))
        except InvalidBudgetError:
            return _rerender(error=translate("invalid_budget_error", ui_lang))
        except ValueError as exc:
            return _rerender(error=str(exc))

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
            "ui_lang": _ui_lang(request),
            "record": record,
            "run_id": run_id,
            "status": record.status,
            "cost": f"${record.estimated_total_cost_usd:.4f}",
            # Never the provider account's own balance (see cost_budget.py) --
            # only the application-side cap the user configured for this run,
            # or None when no cap was set (pre-budget-feature runs included).
            "max_run_cost": record.max_run_cost_usd,
            "elapsed": format_duration(elapsed_seconds(record)),
            # A terminal run (succeeded/failed) is rendered once and never
            # opens a live connection -- retryability (a failed run can
            # still be retried/resumed/skipped) is not "currently running".
            # See presenter.TERMINAL_RUN_STATUSES.
            "live_updates": not is_terminal_run_status(record.status),
            # None while running (nothing to report yet); once terminal, a
            # deterministic, derived-only evidence-quality summary -- see
            # presenter.compute_deliberation_quality. Never calls a model,
            # so viewing this page never changes a run's cost or quality.
            "quality": compute_deliberation_quality(record),
            # True once any stage was blocked by the run budget -- shown as
            # a dedicated notice + "Update budget" form (see run_detail.html)
            # rather than mixed into the per-stage error text.
            "budget_blocked": any(
                s.failure_reason == "run_budget_exceeded" for s in record.stages
            ),
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
        return HTMLResponse(render_pipeline_fragment(record, _ui_lang(request)))

    @app.get("/runs/{run_id}/events")
    async def run_events(request: Request, run_id: str):
        svc: DeliberationService = request.app.state.service
        try:
            svc.get_run(run_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Run not found")
        ui_lang = _ui_lang(request)

        async def event_stream():
            while True:
                try:
                    record = svc.get_run(run_id)
                except KeyError:
                    return
                yield _sse("pipeline", render_pipeline_fragment(record, ui_lang))
                still_running = run_id in request.app.state.running
                if is_terminal_run_status(record.status) and not still_running:
                    # Payload is the terminal status itself, not run_id --
                    # the client uses it to decide whether a full reload is
                    # actually needed (only "succeeded" switches the page
                    # from the pipeline view to the result-page layout; a
                    # "failed" run's pipeline fragment above already shows
                    # the final state, so no reload -- see app.js).
                    yield _sse("done", record.status)
                    return
                await asyncio.sleep(1.0)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # -- retry / resume ---------------------------------------------------

    @app.post("/runs/{run_id}/budget")
    async def update_budget(
        request: Request, run_id: str, max_run_cost: str = Form("")
    ):
        """"Increase budget" action on a run blocked by run_budget_exceeded
        (see service._execute / run_detail.html). Only changes the stored
        cap -- does not itself resume the run, so the two actions stay
        explicit and the durable resume/retry flow is entirely reused
        unchanged (Section 8 of the task brief's minimum acceptable
        behavior: "preserve all completed work; allow them to resume after
        increasing the stored budget").
        """
        svc: DeliberationService = request.app.state.service
        ui_lang = _ui_lang(request)
        try:
            svc.get_run(run_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Run not found")
        try:
            svc.update_run_budget(run_id, max_run_cost.strip() or None)
        except InvalidBudgetError:
            raise HTTPException(status_code=400, detail=translate("invalid_budget_error", ui_lang))
        return RedirectResponse(url=f"/runs/{run_id}", status_code=303)

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
        return templates.TemplateResponse(
            request, "history.html", {"runs": runs, "ui_lang": _ui_lang(request)}
        )

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
