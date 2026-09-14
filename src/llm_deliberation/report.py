from __future__ import annotations

from datetime import datetime
from pathlib import Path

from llm_deliberation.types import ModelResponse, RunResult


def _section(title: str, response: ModelResponse) -> str:
    fallback_line = ""
    if response.fallback_used:
        fallback_line = (
            f"_Requested model: `{response.requested_model}` · Fallback reason: "
            f"{response.fallback_reason} · Attempts (this try): {response.model_attempts}_\n\n"
        )
    return (
        f"## {title}\n\n"
        f"_Provider: {response.provider} · Model: `{response.model}` · "
        f"Input: {response.usage.input_tokens:,} tokens · "
        f"Output: {response.usage.output_tokens:,} tokens · "
        f"Estimated cost: ${response.estimated_cost_usd:.4f}_\n\n"
        f"{fallback_line}"
        f"{response.text}\n"
    )


def render_markdown(result: RunResult) -> str:
    parts = [
        "# LLM Deliberation Report",
        "",
        f"**Profile:** `{result.profile}`  ",
        f"**Red-team enabled:** `{result.red_team_enabled}`  ",
        f"**Estimated total API cost:** `${result.estimated_total_cost_usd:.4f}`",
        "",
        "## Question",
        "",
        result.question,
        "",
    ]
    if result.context:
        parts.extend(["## Context", "", result.context, ""])
    parts.extend([
        _section("Independent analysis A", result.analysis_a),
        _section("Independent analysis B", result.analysis_b),
        _section("A critiques B", result.critique_a_of_b),
        _section("B critiques A", result.critique_b_of_a),
    ])

    if result.red_team:
        parts.append(_section("Independent red-team", result.red_team))

    parts.extend(
        [
            _section("Revised candidate A", result.revision_a),
            _section("Revised candidate B", result.revision_b),
            _section("Final synthesis", result.synthesis),
        ]
    )
    return "\n".join(parts)


def save_report(result: RunResult, output: str | None = None) -> Path:
    if output:
        path = Path(output)
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = Path("runs") / f"deliberation-{stamp}.md"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(result), encoding="utf-8")
    return path
