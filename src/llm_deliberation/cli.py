from __future__ import annotations

import argparse
import asyncio
import sys

from llm_deliberation.config import PROFILES, Settings
from llm_deliberation.orchestrator import DeliberationOrchestrator
from llm_deliberation.report import save_report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm-deliberate",
        description=(
            "Run independent multi-model analysis, cross-critique, optional "
            "third-model red-team, revision, and final synthesis."
        ),
    )
    parser.add_argument(
        "question",
        nargs="?",
        help="Question to deliberate. If omitted, read from stdin.",
    )
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        help="Model profile. Overrides LLM_PROFILE.",
    )
    parser.add_argument(
        "--red-team",
        dest="red_team",
        action="store_true",
        help="Enable Gemini as the third-model red-team.",
    )
    parser.add_argument(
        "--no-red-team",
        dest="red_team",
        action="store_false",
        help="Disable the third model and use only the two primary models.",
    )
    parser.set_defaults(red_team=None)
    parser.add_argument(
        "-o",
        "--output",
        help="Markdown report path. Default: runs/deliberation-<timestamp>.md",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    question = args.question
    if not question:
        if sys.stdin.isatty():
            question = input("Question: ").strip()
        else:
            question = sys.stdin.read().strip()

    if not question:
        print("Error: question is empty.", file=sys.stderr)
        return 2

    settings = Settings.load(
        profile_override=args.profile,
        red_team_override=args.red_team,
    )
    settings.validate_keys()

    print(
        f"Profile={settings.profile} | "
        f"A={settings.openai_model} | "
        f"B={settings.anthropic_model} | "
        f"red-team={'on (' + settings.gemini_model + ')' if settings.red_team_enabled else 'off'}"
    )
    print("Running deliberation...")

    orchestrator = DeliberationOrchestrator(settings)
    result = await orchestrator.run(question)
    path = save_report(result, args.output)

    print("\n=== FINAL SYNTHESIS ===\n")
    print(result.synthesis.text)
    print(
        f"\nEstimated total API cost: "
        f"${result.estimated_total_cost_usd:.4f}"
    )
    print(f"Full report: {path}")
    return 0


def main() -> None:
    args = _parser().parse_args()
    try:
        raise SystemExit(asyncio.run(_run(args)))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
