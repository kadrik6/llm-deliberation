from __future__ import annotations

import argparse
import asyncio
import sys

from llm_deliberation.config import PROFILES, Settings
from llm_deliberation.cost_budget import InvalidBudgetError
from llm_deliberation.report import save_report
from llm_deliberation.service import DeliberationService
from llm_deliberation.store import RunRecord


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
        "--context",
        help="Optional additional context appended to the question.",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Markdown report path. Default: runs/deliberation-<timestamp>.md",
    )
    parser.add_argument(
        "--resume",
        metavar="RUN_ID",
        help="Resume an existing run instead of creating a new one.",
    )
    parser.add_argument(
        "--retry-stage",
        nargs=2,
        metavar=("RUN_ID", "STAGE_NAME"),
        help="Retry one failed stage of an existing run, then continue.",
    )
    parser.add_argument(
        "--list-runs",
        action="store_true",
        help="List recent runs and exit.",
    )
    parser.add_argument(
        "--max-cost",
        metavar="USD",
        help="Application-side hard cost cap for this run (e.g. 0.75). "
             "The app stops before sending a request that could exceed it. "
             "Never the provider account's own balance.",
    )
    parser.add_argument(
        "--check-providers",
        action="store_true",
        help="Run the provider readiness preflight and exit -- does not "
             "create a run or send any generation request.",
    )
    return parser


def _print_readiness_report(report) -> None:
    for label, result in (
        ("OpenAI", report.openai),
        ("Anthropic", report.anthropic),
        ("Gemini", report.gemini),
    ):
        if result is None:
            continue
        marker = "OK" if result.ready else "FAIL"
        print(f"  [{marker}] {label} ({result.configured_model}): {result.user_message}")


def _print_run_outcome(service: DeliberationService, record: RunRecord, output: str | None) -> int:
    if record.status != "succeeded":
        print("\nDeliberation did not complete.", file=sys.stderr)
        for stage in record.stages:
            if stage.status == "failed":
                print(f"  [{stage.name}] {stage.error}", file=sys.stderr)
        print(
            f"\nRun ID: {record.id}\n"
            f"Estimated cost so far: ${record.estimated_total_cost_usd:.4f}\n"
            f"Retry with: llm-deliberate --resume {record.id}",
            file=sys.stderr,
        )
        return 1

    result = service.to_run_result(record.id)
    path = save_report(result, output)

    print("\n=== FINAL SYNTHESIS ===\n")
    print(result.synthesis.text)
    print(f"\nEstimated total API cost: ${result.estimated_total_cost_usd:.4f}")
    print(f"Full report: {path}")
    print(f"Run ID: {record.id}")
    return 0


async def _run(args: argparse.Namespace) -> int:
    service = DeliberationService()

    if args.check_providers:
        settings = Settings.load(profile_override=args.profile, red_team_override=args.red_team)
        report = service.check_readiness(settings.profile, settings.red_team_enabled, force=True)
        print(f"Provider readiness (profile={settings.profile}):")
        _print_readiness_report(report)
        if not report.required_ready:
            return 1
        if report.gemini_blocked:
            print("  Gemini red-team is currently unavailable; a run would still "
                  "proceed if red-team is disabled.")
        return 0

    if args.list_runs:
        for run in service.list_runs():
            print(
                f"{run.id}  {run.status:<10} {run.profile:<10} "
                f"${run.estimated_total_cost_usd:.4f}  {run.question[:60]}"
            )
        return 0

    if args.retry_stage:
        run_id, stage_name = args.retry_stage
        record = await service.retry_stage(run_id, stage_name)
        return _print_run_outcome(service, record, args.output)

    if args.resume:
        record = await service.resume_run(args.resume)
        return _print_run_outcome(service, record, args.output)

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

    # Readiness preflight -- see readiness.py. A required-provider failure
    # stops before any run/stage row is created; a Gemini-only failure with
    # red-team requested is surfaced as an explicit choice rather than
    # silently disabling it (see Section 3 of the readiness/budget work).
    readiness_report = service.check_readiness(settings.profile, settings.red_team_enabled)
    if not readiness_report.required_ready:
        print("Provider readiness check failed -- deliberation not started:", file=sys.stderr)
        _print_readiness_report(readiness_report)
        return 1
    if readiness_report.gemini_blocked:
        print("Gemini red-team is currently unavailable:", file=sys.stderr)
        print(f"  {readiness_report.gemini.user_message}", file=sys.stderr)
        answer = "n"
        if sys.stdin.isatty():
            answer = input("Start without red-team? [y/N] ").strip().lower()
        if answer != "y":
            print("Cancelled. Re-run with --no-red-team, or try again once Gemini is ready.")
            return 1
        settings.red_team_enabled = False

    if settings.red_team_enabled:
        chain = ", ".join([settings.gemini_model, *settings.gemini_fallback_models])
        red_team_status = f"on (preferred: {settings.gemini_model}; chain: {chain})"
    else:
        red_team_status = "off"
    print(
        f"Profile={settings.profile} | "
        f"A={settings.openai_model} | "
        f"B={settings.anthropic_model} | "
        f"red-team={red_team_status}"
    )

    try:
        run_id = service.create_run(
            question=question,
            profile=settings.profile,
            red_team_enabled=settings.red_team_enabled,
            context=args.context,
            max_run_cost_usd=args.max_cost,
        )
    except InvalidBudgetError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(f"Run ID: {run_id}")
    if args.max_cost:
        print(f"Max run cost: ${args.max_cost}")
    print("Running deliberation...")

    record = await service.start_run(run_id)
    return _print_run_outcome(service, record, args.output)


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
