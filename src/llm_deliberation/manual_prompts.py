"""Export the pipeline's real stage prompts as a paste-ready Markdown script,
so a deliberation can be run by hand in two chat apps (e.g. ChatGPT and
Claude, with their web search on) at no API cost.

Built from the same prompt builders the orchestrator uses, so a prompt
change is tested by hand exactly as the pipeline would send it. Earlier
stages' outputs are left as placeholders for the user to paste in.

    python -m llm_deliberation.manual_prompts "question" --language et -o script.md
    python -m llm_deliberation.manual_prompts "question" --pattern verify -o verify.md

Three patterns: "deliberate" (the pipeline's stages), "verify" (one model
answers with web search, the other checks its claims, the first applies
only the sourced corrections; see prompts.verified_answer) and
"crosscheck" (both models answer, and each answer is checked once by its
own model and once by the other, to tell whether a gain comes from the
check itself or from a second model).
"""
from __future__ import annotations

import argparse
import sys

from llm_deliberation import prompts

_PASTE = "<<PASTE {what} HERE>>"


def _paste(what: str) -> str:
    return _PASTE.format(what=what)


def build_script(question: str, *, language: str = prompts.DEFAULT_LANGUAGE, context: str | None = None) -> str:
    """The whole manual run as one Markdown document. Every stage uses the
    run's own language (no working-language split): a person reads every
    stage here, unlike the pipeline's intermediate stages."""
    if language not in prompts.SUPPORTED_LANGUAGES:
        raise ValueError(f"Unknown language {language!r}. Choose one of: {', '.join(prompts.SUPPORTED_LANGUAGES)}")
    full_question = question if not context else f"{question}\n\nCONTEXT\n{context}"
    system = prompts.base_system(language, language)

    steps: list[tuple[str, str, str]] = [
        (
            "0. Optional: sharpen the question",
            "Either model, new chat. It replies with a problem type and "
            "interview questions (as JSON). Answer the questions yourself, "
            "then regenerate this script with your answers and any sources "
            "passed as --context.",
            prompts.problem_framing_and_interview(question, context),
        ),
        (
            "1a. Model A: independent analysis",
            "Model A (e.g. ChatGPT), new chat. Save the reply as **A1**.",
            prompts.independent_analysis(full_question),
        ),
        (
            "1b. Model B: independent analysis",
            "Model B (e.g. Claude), new chat. Save the reply as **B1**.",
            prompts.independent_analysis(full_question),
        ),
        (
            "2a. Model A critiques B",
            "Same chat as 1a. Save the reply as **KA** (A's critique of B).",
            prompts.critique(full_question, _paste("B1")),
        ),
        (
            "2b. Model B critiques A",
            "Same chat as 1b. Save the reply as **KB** (B's critique of A).",
            prompts.critique(full_question, _paste("A1")),
        ),
        (
            "3a. Model A revises",
            "Same chat as 1a. Save the reply as **A2**.",
            prompts.revision(full_question, _paste("A1"), _paste("KB"), None),
        ),
        (
            "3b. Model B revises",
            "Same chat as 1b. Save the reply as **B2**.",
            prompts.revision(full_question, _paste("B1"), _paste("KA"), None),
        ),
        (
            "4. Final answer",
            "Either model, new chat. This is the answer to grade.",
            prompts.synthesis(full_question, _paste("A2"), _paste("B2"), None),
        ),
    ]

    return _render("Manual deliberation run", question, system, steps)


def build_verify_script(question: str, *, language: str = prompts.DEFAULT_LANGUAGE, context: str | None = None) -> str:
    """The verify pattern as one Markdown document: answer, fact-check,
    corrected answer. Step 1's reply is also the single-model baseline for
    a blind comparison against step 3."""
    if language not in prompts.SUPPORTED_LANGUAGES:
        raise ValueError(f"Unknown language {language!r}. Choose one of: {', '.join(prompts.SUPPORTED_LANGUAGES)}")
    full_question = question if not context else f"{question}\n\nCONTEXT\n{context}"
    system = prompts.base_system(language, language)
    steps: list[tuple[str, str, str]] = [
        (
            "1. Model A: answer",
            "Model A (e.g. ChatGPT), new chat, web search on. Save the reply "
            "as **V1**. V1 is also the single-model baseline for grading.",
            prompts.verified_answer(full_question),
        ),
        (
            "2. Model B: fact-check",
            "Model B (e.g. Claude), new chat, web search on. Save the reply as **K**.",
            prompts.claim_check(full_question, _paste("V1")),
        ),
        (
            "3. Model A: apply the corrections",
            "Same chat as step 1. Save the reply as **V2**. Grade V1 against V2 blind.",
            prompts.apply_verified_corrections(full_question, _paste("V1"), _paste("K")),
        ),
    ]
    return _render("Manual verify run", question, system, steps)


def build_crosscheck_script(question: str, *, language: str = prompts.DEFAULT_LANGUAGE, context: str | None = None) -> str:
    """The verify pattern run both ways, plus a self-check control: six
    answers to grade blind (each model alone, after a self-check, after a
    check by the other model)."""
    if language not in prompts.SUPPORTED_LANGUAGES:
        raise ValueError(f"Unknown language {language!r}. Choose one of: {', '.join(prompts.SUPPORTED_LANGUAGES)}")
    full_question = question if not context else f"{question}\n\nCONTEXT\n{context}"
    system = prompts.base_system(language, language)
    steps: list[tuple[str, str, str]] = [
        (
            "1a. Model A: answer",
            "Model A (e.g. ChatGPT), new chat, web search on. Save as **A0** (graded: A alone).",
            prompts.verified_answer(full_question),
        ),
        (
            "1b. Model B: answer",
            "Model B (e.g. Claude), new chat, web search on. Save as **B0** (graded: B alone).",
            prompts.verified_answer(full_question),
        ),
    ]
    for writer, other in (("A", "B"), ("B", "A")):
        for checker, kind in ((writer, "self"), (other, "cross")):
            steps.append((
                f"2. Model {checker} checks {writer}0 ({kind}-check)",
                f"Model {checker}, **new** chat, web search on. Save as **K{writer}{checker}**.",
                prompts.claim_check(full_question, _paste(f"{writer}0")),
            ))
    for writer, other in (("A", "B"), ("B", "A")):
        for checker, label in ((writer, "self"), (other, "cross")):
            steps.append((
                f"3. Model {writer} applies K{writer}{checker}",
                f"Model {writer}, **new** chat (so the two corrections stay independent). "
                f"Save as **{writer}-{label}** (graded: {writer} after a {label}-check).",
                prompts.apply_verified_corrections(full_question, _paste(f"{writer}0"), _paste(f"K{writer}{checker}")),
            ))
    return _render("Manual cross-check run", question, system, steps)


def _render(heading: str, question: str, system: str, steps: list[tuple[str, str, str]]) -> str:
    parts = [
        f"# {heading}",
        "",
        f"Question: {question}",
        "",
        "Run each step in the chat named. Paste the system text first "
        "(as the first message, or into the app's custom instructions), "
        "then the step prompt. Replace each `<<PASTE ... HERE>>` with the "
        "saved reply it names. Keep web search on so labelled claims can "
        "be checked.",
        "",
        "## System text (every step)",
        "",
        "```text",
        system,
        "```",
    ]
    for title, where, body in steps:
        parts += ["", f"## {title}", "", where, "", "```text", body, "```"]
    return "\n".join(parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m llm_deliberation.manual_prompts",
        description="Write the pipeline's stage prompts as a paste-ready script for a manual run in chat apps. No API calls.",
    )
    parser.add_argument("question")
    parser.add_argument("--language", choices=sorted(prompts.SUPPORTED_LANGUAGES), default=prompts.DEFAULT_LANGUAGE)
    parser.add_argument("--context", help="Optional context/sources appended to the question.")
    parser.add_argument("--pattern", choices=["deliberate", "verify", "crosscheck"], default="deliberate")
    parser.add_argument("-o", "--output", help="Write to this file instead of stdout.")
    args = parser.parse_args(argv)

    build = {"verify": build_verify_script, "crosscheck": build_crosscheck_script}.get(args.pattern, build_script)
    script = build(args.question, language=args.language, context=args.context)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(script)
    else:
        sys.stdout.write(script)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
