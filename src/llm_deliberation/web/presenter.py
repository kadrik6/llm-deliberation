from __future__ import annotations

from datetime import datetime, timezone

from llm_deliberation.store import RunRecord

STATUS_SYMBOLS: dict[str, str] = {
    "pending": "○",  # ○
    "running": "●",  # ●
    "succeeded": "✓",  # ✓
    "failed": "✗",  # ✗
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


def elapsed_seconds(record: RunRecord) -> float | None:
    if not record.started_at:
        return None
    start = datetime.fromisoformat(record.started_at)
    end = (
        datetime.fromisoformat(record.completed_at)
        if record.completed_at
        else datetime.now(timezone.utc)
    )
    return max(0.0, (end - start).total_seconds())


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
            rows.append(
                {
                    "name": name,
                    "label": label,
                    "disabled": False,
                    "status": stage.status,
                    "symbol": STATUS_SYMBOLS.get(stage.status, "?"),
                    "error": stage.error,
                    "attempt": stage.attempt,
                }
            )
        groups.append({"title": title, "rows": rows})
    return groups
