from __future__ import annotations

from datetime import datetime, timezone

from llm_deliberation.orchestrator import SKIPPABLE_STAGE_NAMES
from llm_deliberation.store import RunRecord, StageRecord

# A run in one of these statuses is done for good: nothing will ever change
# it further without an explicit user action (retry/resume/skip). Used to
# gate every live-update mechanism (SSE, no-JS meta-refresh) so a finished
# run is rendered once and left alone -- retryability is not the same thing
# as "currently running", and must not keep a page polling/reconnecting.
# "pending" and "running" are the only non-terminal run statuses (see
# service.py); "skipped" is a *stage*-level status, never a run-level one.
TERMINAL_RUN_STATUSES: frozenset[str] = frozenset({"succeeded", "failed"})


def is_terminal_run_status(status: str) -> bool:
    return status in TERMINAL_RUN_STATUSES


STATUS_SYMBOLS: dict[str, str] = {
    "pending": "○",  # ○
    "running": "●",  # ●
    "succeeded": "✓",  # ✓
    "failed": "✗",  # ✗
    "skipped": "⏭",  # ⏭ -- deliberately skipped by the user, not a failure
}

# (stage_name, display_label, label_is_key) grouped for the pipeline view.
# Group titles are i18n keys (see web/i18n.py), translated in the template
# via |t. Individual row labels are a per-row mix: several are literal
# provider names (OpenAI/Anthropic/Gemini, or "OpenAI → Anthropic"), which
# are proper nouns and never translated (label_is_key=False, rendered as-is);
# the rest ("Candidate A/B", "Meta-analysis", "Synthesis") are UI-owned
# generic terms and must follow the UI language, so they are i18n keys
# instead (label_is_key=True, translated in the template via |t) -- see the
# bilingual UI audit: a mixed label must not stay English merely because
# half of it (the provider name) is untranslatable.
STAGE_GROUPS: tuple[tuple[str, tuple[tuple[str, str, bool], ...]], ...] = (
    (
        "stage_group_analysis",
        (("analysis_a", "OpenAI", False), ("analysis_b", "Anthropic", False)),
    ),
    (
        "stage_group_critique",
        (
            ("critique_a_of_b", "OpenAI → Anthropic", False),
            ("critique_b_of_a", "Anthropic → OpenAI", False),
        ),
    ),
    ("stage_group_red_team", (("red_team", "Gemini", False),)),
    (
        "stage_group_revision",
        (
            ("revision_a", "pipeline_row_candidate_a", True),
            ("revision_b", "pipeline_row_candidate_b", True),
        ),
    ),
    ("stage_group_convergence", (("convergence_analysis", "pipeline_row_meta_analysis", True),)),
    ("stage_group_synthesis", (("synthesis", "pipeline_row_synthesis", True),)),
)

# (stage_name, section_title_key) for the expandable artifact sections shown
# once a run has succeeded. section_title_key is an i18n key (web/i18n.py),
# translated in the template via |t. Synthesis is shown separately as the
# dominant "final answer", so it is intentionally excluded here.
# convergence_analysis's raw artifact is structured JSON, not prose -- see
# result.html, which renders it in a <pre> rather than through the Markdown
# pipeline.
ARTIFACT_SECTIONS: tuple[tuple[str, str], ...] = (
    ("analysis_a", "artifact_analysis_a"),
    ("analysis_b", "artifact_analysis_b"),
    ("critique_a_of_b", "artifact_critique_a_of_b"),
    ("critique_b_of_a", "artifact_critique_b_of_a"),
    ("red_team", "artifact_red_team"),
    ("revision_a", "artifact_revision_a"),
    ("revision_b", "artifact_revision_b"),
    ("convergence_analysis", "artifact_convergence_analysis"),
)

# Skip-button i18n key per skippable stage (see SKIPPABLE_STAGE_NAMES),
# translated in the template via |t. Stages not listed here never show a
# skip button at all.
_SKIP_LABEL_KEYS: dict[str, str] = {
    "red_team": "skip_red_team_and_continue",
    "convergence_analysis": "skip_convergence_and_continue",
}

# i18n key for a skipped stage's explanatory note, keyed by stage name (see
# SKIPPABLE_STAGE_NAMES). Deliberately independent of the stage's persisted
# `fallback_reason` value: that column holds free English text written by
# service.skip_stage() at skip time (kept as-is, untranslated, for
# historical/debug purposes -- see mark_stage_skipped), while the *visible*
# note is a UI-owned sentence derived purely from which stage was skipped,
# so it renders correctly in either UI language for both old and new runs.
_SKIP_NOTE_KEYS: dict[str, str] = {
    "red_team": "skip_note_red_team",
    "convergence_analysis": "skip_note_convergence_analysis",
}

# Stages a deliberation cannot produce a trustworthy final answer without --
# none of these are in SKIPPABLE_STAGE_NAMES, so a failure in any of them
# already halts the run (see service._execute's run_failed gating) rather
# than letting downstream stages (especially convergence_analysis) run on
# missing evidence. Order matches the pipeline's own display order, purely
# for a stable, readable list of reasons.
REQUIRED_STAGE_ORDER: tuple[str, ...] = (
    "analysis_a", "analysis_b",
    "critique_a_of_b", "critique_b_of_a",
    "revision_a", "revision_b",
    "synthesis",
)

# i18n key (web/i18n.py) used to name each required stage in a quality-issue
# bullet. Reuses the existing artifact-section titles where one exists;
# synthesis has no artifact section of its own (see ARTIFACT_SECTIONS), so it
# reuses its stage-group title instead of inventing a new translated string.
_REQUIRED_STAGE_LABEL_KEYS: dict[str, str] = {
    "analysis_a": "artifact_analysis_a",
    "analysis_b": "artifact_analysis_b",
    "critique_a_of_b": "artifact_critique_a_of_b",
    "critique_b_of_a": "artifact_critique_b_of_a",
    "revision_a": "artifact_revision_a",
    "revision_b": "artifact_revision_b",
    "synthesis": "stage_group_synthesis",
}

_OPTIONAL_STAGE_LABEL_KEYS: dict[str, str] = {
    "red_team": "artifact_red_team",
    "convergence_analysis": "artifact_convergence_analysis",
}


def _stage_is_usable(stage: StageRecord | None) -> bool:
    """A stage counts as usable evidence only once it has actually succeeded
    with non-empty text -- never inferred from "provider technically
    responded". Works unmodified for historical runs that predate
    failure_reason/incomplete_reason: a pre-existing empty artifact (always
    derivable from stored text) is still caught; a pre-existing *truncated*
    one cannot be detected retroactively (no signal was ever stored for it),
    so it is treated as usable -- a neutral legacy default, never an invented
    truncation claim about data this code cannot actually verify.
    """
    return stage is not None and stage.status == "succeeded" and bool(stage.text and stage.text.strip())


def compute_deliberation_quality(record: RunRecord) -> dict | None:
    """Deterministic, derived-only evidence-quality summary for a run.

    Returns None while a run is still in progress (nothing to report yet --
    see TERMINAL_RUN_STATUSES); once terminal (succeeded or failed), returns
    {"level": "complete" | "degraded" | "incomplete", "reasons": [...]}.
    Never calls a model: entirely computed from already-persisted stage
    state, so viewing a run never changes its cost or quality (see
    web/tests -- "viewing a run does not alter quality or cost").

    - "incomplete": at least one required, non-skippable stage (see
      REQUIRED_STAGE_ORDER) is missing/empty/truncated/failed. This is what
      keeps a pipeline defect (an unusable candidate artifact) from ever
      being described as if it were a genuine open question about the
      user's actual request -- see convergence.render_for_prompt /
      remaining_unknowns, which never receive pipeline-evidence gaps in the
      first place because convergence_analysis's wave never runs without
      all of these already succeeded.
    - "degraded": every required stage is usable, but red_team (only when it
      was actually configured on for this run) or convergence_analysis was
      skipped after failing -- optional evidence intentionally sacrificed to
      let the run still reach a synthesized answer. A red_team that was
      simply disabled from the start is normal, not degraded: no stage row
      for it exists at all in that case.
    - "complete": every required stage usable, nothing optional skipped.

    Each reason is {"stage": name, "label_key": i18n key, "kind":
    "unavailable" | "truncated" | "skipped"} -- the template composes the
    final bullet text from two small translated fragments (the stage's
    existing artifact-title key, plus a "quality_status_*" suffix key) so no
    new per-stage translated string needs to exist for this feature alone.
    """
    if not is_terminal_run_status(record.status):
        return None

    stages = {s.name: s for s in record.stages}

    reasons: list[dict] = []
    for name in REQUIRED_STAGE_ORDER:
        stage = stages.get(name)
        if _stage_is_usable(stage):
            continue
        kind = "truncated" if stage is not None and stage.failure_reason == "output_truncated" else "unavailable"
        reasons.append({"stage": name, "label_key": _REQUIRED_STAGE_LABEL_KEYS[name], "kind": kind})

    if reasons:
        return {"level": "incomplete", "reasons": reasons}

    degraded: list[dict] = []
    red_team_stage = stages.get("red_team")
    if record.red_team_enabled and red_team_stage is not None and red_team_stage.status == "skipped":
        degraded.append({"stage": "red_team", "label_key": _OPTIONAL_STAGE_LABEL_KEYS["red_team"], "kind": "skipped"})
    convergence_stage = stages.get("convergence_analysis")
    if convergence_stage is not None and convergence_stage.status == "skipped":
        degraded.append(
            {
                "stage": "convergence_analysis",
                "label_key": _OPTIONAL_STAGE_LABEL_KEYS["convergence_analysis"],
                "kind": "skipped",
            }
        )

    if degraded:
        return {"level": "degraded", "reasons": degraded}

    return {"level": "complete", "reasons": []}


PROFILE_ORDER: tuple[str, ...] = ("economy", "balanced", "max")
# i18n keys (web/i18n.py) for each profile's blurb, translated in the
# template via |t.
PROFILE_BLURB_KEYS: dict[str, str] = {
    "economy": "profile_economy_blurb",
    "balanced": "profile_balanced_blurb",
    "max": "profile_max_blurb",
}


def _elapsed_since(started_at: str | None, completed_at: str | None) -> float | None:
    if not started_at:
        return None
    start = datetime.fromisoformat(started_at)
    end = datetime.fromisoformat(completed_at) if completed_at else datetime.now(timezone.utc)
    return max(0.0, (end - start).total_seconds())


def elapsed_seconds(record: RunRecord) -> float | None:
    return _elapsed_since(record.started_at, record.completed_at)


def stage_elapsed_seconds(stage: StageRecord) -> float | None:
    """How long a stage has been running, using only its stored started_at.

    Only meaningful while status == "running" (completed_at is still unset
    then, so this measures "so far", not a finished duration). Generic
    across every stage -- it says nothing about internal provider retries,
    just wall-clock time since the stage started.
    """
    return _elapsed_since(stage.started_at, stage.completed_at)


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    total = int(seconds)
    minutes, secs = divmod(total, 60)
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def format_datetime(value: str | None) -> str:
    if not value:
        return "–"
    return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")


def group_attempts_by_model(attempt_log: list[dict] | None) -> list[dict]:
    """Group a flat Gemini attempt_log (see providers._Attempt.to_dict) by
    model, in first-seen/chain order.

    The raw log is a flat, chronological list of every attempt across every
    model in the fallback chain. Rendering it flat produces exactly the
    confusing provenance the reliability pass calls out (a single "Attempts:
    7 / Fallback reason: ..." line mixing failure causes from more than one
    model). Grouping it here lets the template show, per model actually
    tried: how many attempts, and that model's own final outcome/reason --
    e.g. "Gemini 3.8 Flash: 4 attempts, failed: provider overload" followed
    by "Gemini 3.7 Flash: 3 attempts, succeeded".
    """
    if not attempt_log or attempt_log_phases(attempt_log) is not None:
        # Phase-based logs (orchestrator.run_stage's truncation recovery --
        # see attempt_log_phases) are rendered by a dedicated template block
        # instead: grouping them by model would collapse "initial" and
        # "recovery" into one misleading group, since both phases use the
        # exact same model.
        return []
    groups: list[dict] = []
    index: dict[str, int] = {}
    for entry in attempt_log:
        model = entry.get("model")
        if model not in index:
            index[model] = len(groups)
            groups.append({"model": model, "count": 0, "outcome": None, "reason": None})
        group = groups[index[model]]
        group["count"] += 1
        group["outcome"] = entry.get("outcome")
        group["reason"] = entry.get("reason")
    return groups


def attempt_log_phases(attempt_log: list[dict] | None) -> list[dict] | None:
    """The phase-based view of an attempt_log, if it is one -- else None.

    Distinguishes orchestrator.run_stage's OpenAI/Anthropic truncation-
    recovery log (entries shaped {"phase": "initial"|"recovery", "model",
    "outcome", "estimated_cost_usd"}, at most 2 entries, always the same
    model in both) from GeminiFallbackProvider's per-model retry/fallback
    log (entries shaped {"model", "attempt_number", "outcome", "reason",
    ...}, no "phase" key). Used to answer, in the UI, "was this stage
    retried once concisely after truncating, and what happened each time" --
    see the reliability-pass follow-up: two real paid attempts must never be
    collapsed into a single misleading "Attempts (this try): 1".
    """
    if not attempt_log or "phase" not in attempt_log[0]:
        return None
    return attempt_log


def build_pipeline(record: RunRecord) -> list[dict]:
    """Group a run's stages for display, matching STAGE_GROUPS order.

    A missing stage (only possible for "red_team" when red-team is disabled)
    is rendered as a disabled row rather than fabricated status data.
    """
    stages = {s.name: s for s in record.stages}
    groups: list[dict] = []
    for title, members in STAGE_GROUPS:
        rows = []
        for name, label, label_is_key in members:
            stage = stages.get(name)
            if stage is None:
                rows.append({"name": name, "label": label, "label_is_key": label_is_key, "disabled": True})
                continue
            # Just the elapsed duration -- no English text baked in here.
            # The "Running"/"Käib" prefix is composed in the template from
            # the existing status_running translation (see
            # partials/pipeline.html), so this stays UI-language-agnostic.
            running_elapsed = None
            if stage.status == "running":
                # Wall-clock time only -- this deliberately does not claim to
                # know which internal provider attempt/model is in flight.
                running_elapsed = format_duration(stage_elapsed_seconds(stage))

            rows.append(
                {
                    "name": name,
                    "label": label,
                    "label_is_key": label_is_key,
                    "disabled": False,
                    "status": stage.status,
                    "symbol": STATUS_SYMBOLS.get(stage.status, "?"),
                    "error": stage.error,
                    "attempt": stage.attempt,
                    "model": stage.model,
                    "requested_model": stage.requested_model,
                    "fallback_used": stage.fallback_used,
                    "fallback_reason": stage.fallback_reason,
                    "model_attempts": stage.model_attempts,
                    "attempt_log": stage.attempt_log,
                    "failure_reason": stage.failure_reason,
                    "attempts_by_model": group_attempts_by_model(stage.attempt_log),
                    "attempt_phases": attempt_log_phases(stage.attempt_log),
                    "running_elapsed": running_elapsed,
                    # A skippable, failed stage gets a "Retry" + "Skip"
                    # pair instead of the single generic retry button.
                    # red_team additionally has a Gemini fallback chain, so
                    # it gets three buttons (retry preferred / retry chain /
                    # skip) instead of the generic pair -- see
                    # fallback_chain_retry below.
                    "skippable": name in SKIPPABLE_STAGE_NAMES and stage.status == "failed",
                    "fallback_chain_retry": name == "red_team",
                    "skip_label_key": _SKIP_LABEL_KEYS.get(name, "skip_and_continue"),
                    "skip_note_key": _SKIP_NOTE_KEYS.get(name),
                }
            )
        groups.append({"title": title, "rows": rows})
    return groups
