from __future__ import annotations

from datetime import datetime
from pathlib import Path

from llm_deliberation import convergence
from llm_deliberation.types import ModelResponse, RunResult

# Structural headings/labels report.py writes itself, keyed by the run's own
# `language` field (never a viewer preference -- a file export has no
# viewer). Deliberately a small, self-contained dict rather than importing
# web/i18n.py: report.py (and the CLI that calls it) has no dependency on
# the web/FastAPI/Jinja stack, and this keeps it that way. The actual
# model-generated artifact text is never touched here regardless of
# language -- only the report's own wrapper text switches.
_LABELS: dict[str, dict[str, str]] = {
    "report_title": {"en": "LLM Deliberation Report", "et": "LLM arutelu raport"},
    "profile_label": {"en": "Profile", "et": "Profiil"},
    "red_team_enabled_label": {"en": "Red-team enabled", "et": "Punane meeskond sees"},
    "total_cost_label": {"en": "Estimated total API cost", "et": "Hinnanguline kogumaksumus"},
    "question_heading": {"en": "Question", "et": "Küsimus"},
    "context_heading": {"en": "Context", "et": "Kontekst"},
    "analysis_a_heading": {"en": "Independent analysis A", "et": "Sõltumatu analüüs A"},
    "analysis_b_heading": {"en": "Independent analysis B", "et": "Sõltumatu analüüs B"},
    "critique_a_of_b_heading": {"en": "A critiques B", "et": "A kritiseerib B-d"},
    "critique_b_of_a_heading": {"en": "B critiques A", "et": "B kritiseerib A-d"},
    "red_team_heading": {"en": "Independent red-team", "et": "Sõltumatu punane meeskond"},
    "revision_a_heading": {"en": "Revised candidate A", "et": "Täiendatud kandidaat A"},
    "revision_b_heading": {"en": "Revised candidate B", "et": "Täiendatud kandidaat B"},
    "convergence_raw_heading": {
        "en": "Convergence analysis (raw)", "et": "Konsensuse analüüs (toorandmed)",
    },
    "synthesis_heading": {"en": "Final synthesis", "et": "Lõppsüntees"},
    "provider_label": {"en": "Provider", "et": "Pakkuja"},
    "model_label": {"en": "Model", "et": "Mudel"},
    "input_tokens_label": {"en": "Input", "et": "Sisend"},
    "output_tokens_label": {"en": "Output", "et": "Väljund"},
    "tokens_word": {"en": "tokens", "et": "märki"},
    "estimated_cost_label": {"en": "Estimated cost", "et": "Hinnanguline maksumus"},
    "requested_model_label": {"en": "Requested model", "et": "Soovitud mudel"},
    "fallback_reason_label": {"en": "Fallback reason", "et": "Varulahenduse põhjus"},
    "attempts_label": {"en": "Attempts (this try)", "et": "Katsed (see kord)"},
    "decision_evolution_heading": {"en": "Decision evolution", "et": "Otsuse areng"},
    "convergence_unavailable": {
        "en": "Change/convergence analysis unavailable for this run.",
        "et": "Muutuste ja konsensuse analüüs pole selle arutelu jaoks saadaval.",
    },
    "convergence_subheading": {"en": "Convergence", "et": "Konsensus"},
    "convergence_converged": {"en": "converged", "et": "konsensus saavutatud"},
    "convergence_partial": {"en": "partial", "et": "osaline"},
    "convergence_diverged": {"en": "diverged", "et": "seisukohad jäid lahku"},
    "convergence_insufficient_information": {
        "en": "insufficient_information", "et": "info on ebapiisav",
    },
    "what_changed_subheading": {"en": "What changed", "et": "Mis muutus"},
    "candidate_word": {"en": "Candidate", "et": "Kandidaat"},
    "material_word": {"en": "material", "et": "sisuline"},
    "not_material_word": {"en": "not material", "et": "mitte-sisuline"},
    "before_word": {"en": "Before", "et": "Enne"},
    "after_word": {"en": "After", "et": "Pärast"},
    "trigger_word": {"en": "Trigger", "et": "Põhjus"},
    "cause_uncertain": {"en": "cause uncertain", "et": "põhjus ebaselge"},
    "agreements_subheading": {"en": "Agreements reached", "et": "Kokkulepped"},
    "none_identified": {"en": "None identified.", "et": "Midagi ei tuvastatud."},
    "unresolved_subheading": {
        "en": "Unresolved disagreements", "et": "Lahendamata erimeelsused",
    },
    "candidate_a_word": {"en": "Candidate A", "et": "Kandidaat A"},
    "candidate_b_word": {"en": "Candidate B", "et": "Kandidaat B"},
    "why_unresolved_word": {"en": "Why unresolved", "et": "Miks lahendamata"},
    "decision_impact_word": {"en": "Decision impact", "et": "Mõju otsusele"},
    "remaining_unknowns_subheading": {"en": "Remaining unknowns", "et": "Puuduv info"},
    "evidence_needed_word": {"en": "evidence needed", "et": "vajalikud tõendid"},
    "human_judgement_subheading": {
        "en": "Human judgement required", "et": "Vajab inimese otsust",
    },
}


def _label(key: str, language: str) -> str:
    entry = _LABELS[key]
    return entry.get(language) or entry["en"]


def _section(title: str, response: ModelResponse, language: str) -> str:
    fallback_line = ""
    if response.fallback_used:
        fallback_line = (
            f"_{_label('requested_model_label', language)}: `{response.requested_model}` · "
            f"{_label('fallback_reason_label', language)}: {response.fallback_reason} · "
            f"{_label('attempts_label', language)}: {response.model_attempts}_\n\n"
        )
    return (
        f"## {title}\n\n"
        f"_{_label('provider_label', language)}: {response.provider} · "
        f"{_label('model_label', language)}: `{response.model}` · "
        f"{_label('input_tokens_label', language)}: {response.usage.input_tokens:,} "
        f"{_label('tokens_word', language)} · "
        f"{_label('output_tokens_label', language)}: {response.usage.output_tokens:,} "
        f"{_label('tokens_word', language)} · "
        f"{_label('estimated_cost_label', language)}: ${response.estimated_cost_usd:.4f}_\n\n"
        f"{fallback_line}"
        f"{response.text}\n"
    )


def _bulleted(lines: list[str], language: str) -> list[str]:
    return lines if lines else [_label("none_identified", language)]


def _decision_evolution_section(
    convergence_response: ModelResponse | None, language: str
) -> str:
    heading = _label("decision_evolution_heading", language)
    if convergence_response is None:
        return f"## {heading}\n\n{_label('convergence_unavailable', language)}\n"

    analysis = convergence.parse_convergence_analysis(convergence_response.text)
    convergence_display = _label(f"convergence_{analysis.convergence}", language)
    lines = [
        f"## {heading}", "",
        f"### {_label('convergence_subheading', language)}", "",
        convergence_display, "",
    ]

    lines.append(f"### {_label('what_changed_subheading', language)}")
    lines.append("")
    change_lines = []
    material_word = _label("material_word", language)
    not_material_word = _label("not_material_word", language)
    for change in analysis.material_changes:
        change_lines.append(
            f"- **{_label('candidate_word', language)} {change.candidate}** "
            f"({material_word if change.material else not_material_word})"
        )
        change_lines.append(f"  - {_label('before_word', language)}: {change.before}")
        change_lines.append(f"  - {_label('after_word', language)}: {change.after}")
        if change.triggers:
            uncertain_note = f" ({_label('cause_uncertain', language)})"
            trig = "; ".join(
                t.summary + (uncertain_note if t.source == "uncertain" else "")
                for t in change.triggers
            )
            change_lines.append(f"  - {_label('trigger_word', language)}: {trig}")
    lines.extend(_bulleted(change_lines, language))
    lines.append("")

    lines.append(f"### {_label('agreements_subheading', language)}")
    lines.append("")
    lines.extend(
        _bulleted(
            [f"- **{a.topic}:** {a.shared_position}" for a in analysis.agreements_reached],
            language,
        )
    )
    lines.append("")

    lines.append(f"### {_label('unresolved_subheading', language)}")
    lines.append("")
    disagreement_lines = []
    candidate_a_word = _label("candidate_a_word", language)
    candidate_b_word = _label("candidate_b_word", language)
    for d in analysis.unresolved_disagreements:
        disagreement_lines.append(f"- **{d.topic}**")
        disagreement_lines.append(f"  - {candidate_a_word}: {d.candidate_a_position}")
        disagreement_lines.append(f"  - {candidate_b_word}: {d.candidate_b_position}")
        disagreement_lines.append(
            f"  - {_label('why_unresolved_word', language)}: {d.why_unresolved}"
        )
        disagreement_lines.append(
            f"  - {_label('decision_impact_word', language)}: {d.decision_impact}"
        )
    lines.extend(_bulleted(disagreement_lines, language))
    lines.append("")

    lines.append(f"### {_label('remaining_unknowns_subheading', language)}")
    lines.append("")
    evidence_needed_word = _label("evidence_needed_word", language)
    lines.extend(
        _bulleted(
            [
                f"- **{u.unknown}** — {u.why_it_matters} ({evidence_needed_word}: {u.evidence_needed})"
                for u in analysis.remaining_unknowns
            ],
            language,
        )
    )
    lines.append("")

    if analysis.human_judgement_required:
        lines.append(f"### {_label('human_judgement_subheading', language)}")
        lines.append("")
        lines.extend(
            f"- **{h.issue}:** {h.why_models_cannot_resolve_it}"
            for h in analysis.human_judgement_required
        )
        lines.append("")

    return "\n".join(lines)


def render_markdown(result: RunResult) -> str:
    language = result.language
    parts = [
        f"# {_label('report_title', language)}",
        "",
        f"**{_label('profile_label', language)}:** `{result.profile}`  ",
        f"**{_label('red_team_enabled_label', language)}:** `{result.red_team_enabled}`  ",
        f"**{_label('total_cost_label', language)}:** `${result.estimated_total_cost_usd:.4f}`",
        "",
        f"## {_label('question_heading', language)}",
        "",
        result.question,
        "",
    ]
    if result.context:
        parts.extend([f"## {_label('context_heading', language)}", "", result.context, ""])
    parts.extend([
        _section(_label("analysis_a_heading", language), result.analysis_a, language),
        _section(_label("analysis_b_heading", language), result.analysis_b, language),
        _section(_label("critique_a_of_b_heading", language), result.critique_a_of_b, language),
        _section(_label("critique_b_of_a_heading", language), result.critique_b_of_a, language),
    ])

    if result.red_team:
        parts.append(_section(_label("red_team_heading", language), result.red_team, language))

    parts.extend(
        [
            _section(_label("revision_a_heading", language), result.revision_a, language),
            _section(_label("revision_b_heading", language), result.revision_b, language),
        ]
    )

    if result.convergence:
        parts.append(
            _section(_label("convergence_raw_heading", language), result.convergence, language)
        )

    parts.append(_section(_label("synthesis_heading", language), result.synthesis, language))
    parts.append(_decision_evolution_section(result.convergence, language))
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
