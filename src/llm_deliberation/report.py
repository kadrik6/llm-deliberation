from __future__ import annotations

from datetime import datetime
from pathlib import Path

from llm_deliberation import convergence
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


def _bulleted(lines: list[str]) -> list[str]:
    return lines if lines else ["None identified."]


def _decision_evolution_section(convergence_response: ModelResponse | None) -> str:
    if convergence_response is None:
        return (
            "## Decision evolution\n\n"
            "Change/convergence analysis unavailable for this run.\n"
        )

    analysis = convergence.parse_convergence_analysis(convergence_response.text)
    lines = ["## Decision evolution", "", "### Convergence", "", analysis.convergence, ""]

    lines.append("### What changed")
    lines.append("")
    change_lines = []
    for change in analysis.material_changes:
        change_lines.append(
            f"- **Candidate {change.candidate}** "
            f"({'material' if change.material else 'not material'})"
        )
        change_lines.append(f"  - Before: {change.before}")
        change_lines.append(f"  - After: {change.after}")
        if change.triggers:
            trig = "; ".join(
                t.summary + (" (cause uncertain)" if t.source == "uncertain" else "")
                for t in change.triggers
            )
            change_lines.append(f"  - Trigger: {trig}")
    lines.extend(_bulleted(change_lines))
    lines.append("")

    lines.append("### Agreements reached")
    lines.append("")
    lines.extend(
        _bulleted([f"- **{a.topic}:** {a.shared_position}" for a in analysis.agreements_reached])
    )
    lines.append("")

    lines.append("### Unresolved disagreements")
    lines.append("")
    disagreement_lines = []
    for d in analysis.unresolved_disagreements:
        disagreement_lines.append(f"- **{d.topic}**")
        disagreement_lines.append(f"  - Candidate A: {d.candidate_a_position}")
        disagreement_lines.append(f"  - Candidate B: {d.candidate_b_position}")
        disagreement_lines.append(f"  - Why unresolved: {d.why_unresolved}")
        disagreement_lines.append(f"  - Decision impact: {d.decision_impact}")
    lines.extend(_bulleted(disagreement_lines))
    lines.append("")

    lines.append("### Remaining unknowns")
    lines.append("")
    lines.extend(
        _bulleted(
            [
                f"- **{u.unknown}** — {u.why_it_matters} (evidence needed: {u.evidence_needed})"
                for u in analysis.remaining_unknowns
            ]
        )
    )
    lines.append("")

    if analysis.human_judgement_required:
        lines.append("### Human judgement required")
        lines.append("")
        lines.extend(
            f"- **{h.issue}:** {h.why_models_cannot_resolve_it}"
            for h in analysis.human_judgement_required
        )
        lines.append("")

    return "\n".join(lines)


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
        ]
    )

    if result.convergence:
        parts.append(_section("Convergence analysis (raw)", result.convergence))

    parts.append(_section("Final synthesis", result.synthesis))
    parts.append(_decision_evolution_section(result.convergence))
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
