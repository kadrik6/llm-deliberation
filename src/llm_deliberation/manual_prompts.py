"""Export the pipeline's real stage prompts as a paste-ready Markdown script,
so a deliberation can be run by hand in two chat apps (e.g. ChatGPT and
Claude, with their web search on) at no API cost.

Built from the same prompt builders the orchestrator uses, so a prompt
change is tested by hand exactly as the pipeline would send it. Earlier
stages' outputs are left as placeholders for the user to paste in.

    python -m llm_deliberation.manual_prompts "question" --language et -o script.md
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

    parts = [
        "# Manual deliberation run",
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
    parser.add_argument("-o", "--output", help="Write to this file instead of stdout.")
    args = parser.parse_args(argv)

    script = build_script(args.question, language=args.language, context=args.context)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(script)
    else:
        sys.stdout.write(script)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
