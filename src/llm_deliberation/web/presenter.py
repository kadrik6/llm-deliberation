from __future__ import annotations

from datetime import datetime, timezone

from llm_deliberation.store import RunRecord, StageRecord

STATUS_SYMBOLS: dict[str, str] = {
    "pending": "○",  # ○
    "running": "●",  # ●
    "succeeded": "✓",  # ✓
    "failed": "✗",  # ✗
    "skipped": "⏭",  # ⏭ -- deliberately skipped by the user, not a failure
}

# (stage_name, display_label) grouped for the pipeline view.
STAGE_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("Independent analysis", (("analysis_a", "OpenAI"), ("analysis_b", "Anthropic"))),
    (
        "Cross-critique",
        (
            ("critique_a_of_b", "OpenAI → Anthropic"),
            ("critique_b_of_a", "Anthropic → OpenAI"),
        ),
    ),
    ("Red-team", (("red_team", "Gemini"),)),
    ("Revision", (("revision_a", "Candidate A"), ("revision_b", "Candidate B"))),
    ("Final synthesis", (("synthesis", "Synthesis"),)),
)

# (stage_name, section_title) for the expandable artifact sections shown once
# a run has succeeded. Synthesis is shown separately as the dominant "final
# answer", so it is intentionally excluded here.
ARTIFACT_SECTIONS: tuple[tuple[str, str], ...] = (
    ("analysis_a", "Independent analysis A"),
    ("analysis_b", "Independent analysis B"),
    ("critique_a_of_b", "Cross-critique A → B"),
    ("critique_b_of_a", "Cross-critique B → A"),
    ("red_team", "Independent red-team"),
    ("revision_a", "Revised candidate A"),
    ("revision_b", "Revised candidate B"),
)

PROFILE_ORDER: tuple[str, ...] = ("economy", "balanced", "max")
PROFILE_BLURBS: dict[str, str] = {
    "economy": "Fastest/cheapest. Good for routine deliberation.",
    "balanced": "Stronger models for important questions.",
    "max": "Highest-quality configuration for difficult decisions.",
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
                    # Only the optional red-team stage offers "retry
                    # preferred model" / "retry fallback chain" / "skip" --
                    # every other stage keeps the single generic retry.
                    "skippable": name == "red_team" and stage.status == "failed",
                }
            )
        groups.append({"title": title, "rows": rows})
    return groups
