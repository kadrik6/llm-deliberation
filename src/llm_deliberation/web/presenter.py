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

# (stage_name, display_label) grouped for the pipeline view. Group titles
# are i18n keys (see web/i18n.py), translated in the template via |t.
# Individual row labels are left untranslated: several of them are literal
# provider names (OpenAI/Anthropic/Gemini), which are never translated, and
# splitting a provider name from a generic label ("Candidate A") within the
# same row would be inconsistent -- this is a deliberate, disclosed scope
# boundary for this iteration, not an oversight.
STAGE_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("stage_group_analysis", (("analysis_a", "OpenAI"), ("analysis_b", "Anthropic"))),
    (
        "stage_group_critique",
        (
            ("critique_a_of_b", "OpenAI → Anthropic"),
            ("critique_b_of_a", "Anthropic → OpenAI"),
        ),
    ),
    ("stage_group_red_team", (("red_team", "Gemini"),)),
    ("stage_group_revision", (("revision_a", "Candidate A"), ("revision_b", "Candidate B"))),
    ("stage_group_convergence", (("convergence_analysis", "Meta-analysis"),)),
    ("stage_group_synthesis", (("synthesis", "Synthesis"),)),
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


def build_pipeline(record: RunRecord) -> list[dict]:
    """Group a run's stages for display, matching STAGE_GROUPS order.

    A missing stage (only possible for "red_team" when red-team is disabled)
    is rendered as a disabled row rather than fabricated status data.
    """
    stages = {s.name: s for s in record.stages}
    groups: list[dict] = []
    for title, members in STAGE_GROUPS:
        rows = []
        for name, label in members:
            stage = stages.get(name)
            if stage is None:
                rows.append({"name": name, "label": label, "disabled": True})
                continue
            running_note = None
            if stage.status == "running":
                # Wall-clock time only -- this deliberately does not claim to
                # know which internal provider attempt/model is in flight.
                running_note = f"Running · {format_duration(stage_elapsed_seconds(stage))}"

            rows.append(
                {
                    "name": name,
                    "label": label,
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
                    "running_note": running_note,
                    # A skippable, failed stage gets a "Retry" + "Skip"
                    # pair instead of the single generic retry button.
                    # red_team additionally has a Gemini fallback chain, so
                    # it gets three buttons (retry preferred / retry chain /
                    # skip) instead of the generic pair -- see
                    # fallback_chain_retry below.
                    "skippable": name in SKIPPABLE_STAGE_NAMES and stage.status == "failed",
                    "fallback_chain_retry": name == "red_team",
                    "skip_label_key": _SKIP_LABEL_KEYS.get(name, "skip_and_continue"),
                }
            )
        groups.append({"title": title, "rows": rows})
    return groups
