from __future__ import annotations

import argparse
import asyncio
import sys

from llm_deliberation.config import PROFILES, Settings
from llm_deliberation.cost_budget import BudgetExceededError, InvalidBudgetError
from llm_deliberation.cost_report import CostReport
from llm_deliberation.providers import ProviderGenerationError
from llm_deliberation.question_framing import FramingAnalysis, QuestionFramingParseError
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
        epilog=(
            "Also available: `llm-deliberate costs` -- an estimated cost "
            "report from this app's own persisted calls (not provider "
            "billing), grouped by provider, by day, and by run."
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
        "--diagnose-run",
        metavar="RUN_ID",
        help="Print a per-stage language/token/cost/finish-reason table for "
             "an existing run and exit -- no API calls, developer diagnostic "
             "only (see diagnostics.py).",
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
    parser.add_argument(
        "--question-framing",
        dest="question_framing",
        action="store_true",
        help="Enable optional Stage 0 question framing before the "
             "deliberation (problem-type note, an interview, independent "
             "framing critique + premortem per candidate). Off by default: "
             "untested hypothesis, adds extra paid calls (see ADR 009). "
             "CLI only for now.",
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


def _costs_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="llm-deliberate costs",
        description=(
            "Estimated cost report, from this app's own persisted calls -- "
            "never provider billing. Grouped by provider, by day, and by "
            "run, over a fixed trailing window."
        ),
    )


def _fmt_usd(amount: float) -> str:
    return f"${amount:.4f}"


def _print_cost_report(report: CostReport) -> None:
    print("Cost report -- estimated, from this app's own calls; not provider billing")
    since_date = report.since[:10]
    until_date = report.until[:10]
    print(f"Window: last {report.window_days} days ({since_date} to {until_date})")
    print()

    if not report.by_run:
        print(f"No runs in the last {report.window_days} days.")
        return

    print(f"TOTAL: {_fmt_usd(report.total_cost_usd)}")
    # Calls/tokens are only ever tallied from main-pipeline stage rows (see
    # cost_report.py's module docstring) -- shown on this line, not next to
    # the word "TOTAL", so they are never read as covering Stage 0 too.
    print(
        f"  Main pipeline: {_fmt_usd(report.main_pipeline_cost_usd)}  |  "
        f"{report.total_calls} calls (main pipeline only)  |  "
        f"{report.total_input_tokens:,} input / {report.total_output_tokens:,} output tokens "
        "(main pipeline only)"
    )
    if report.stage0_cost_usd:
        print(
            f"  Stage 0:       {_fmt_usd(report.stage0_cost_usd)} "
            "(combined per run, not split by provider or call)"
        )

    print("\nBy provider:")
    for p in report.by_provider:
        if p.calls:
            print(
                f"  {p.provider:<55} {_fmt_usd(p.estimated_cost_usd):>10}  {p.calls:>3} calls  "
                f"{p.input_tokens:,} in / {p.output_tokens:,} out"
            )
        else:
            # Stage 0's combined bucket -- no call count or tokens to show
            # (see STAGE0_PROVIDER_LABEL's docstring in cost_report.py).
            print(f"  {p.provider:<55} {_fmt_usd(p.estimated_cost_usd):>10}")

    print("\nBy day:")
    for d in report.by_day:
        print(f"  {d.day:<20} {_fmt_usd(d.estimated_cost_usd):>10}")

    print("\nBy run:")
    for r in report.by_run:
        print(
            f"  {r.run_id}  {r.created_at[:16]}  {r.status:<10} "
            f"{_fmt_usd(r.total_cost_usd):>10}  {r.question_preview}"
        )


async def _run_costs(argv: list[str]) -> int:
    """`llm-deliberate costs` -- a separate, minimal entry point (not the
    main flat --flag parser above) since this is a distinct verb, not a
    deliberation option. Dispatched from main() before the regular parser
    ever sees argv (see main()'s own docstring note). No provider calls,
    no API keys required -- purely reads already-persisted data."""
    _costs_parser().parse_args(argv)  # only ever used for --help/-h here
    service = DeliberationService()
    try:
        report = service.cost_report()
    finally:
        service.close()
    _print_cost_report(report)
    return 0


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


def _print_framing_analysis(label: str, analysis: FramingAnalysis) -> None:
    print(f"\n--- {label} ---")
    if analysis.hidden_assumptions:
        print("Hidden assumptions:")
        for item in analysis.hidden_assumptions:
            print(f"  - {item}")
    if analysis.reframings:
        print("Possible reframings (options, not a recommendation):")
        for item in analysis.reframings:
            print(f"  - {item}")
    note = f" -- {analysis.right_problem_note}" if analysis.right_problem_note else ""
    print(f"Right problem? {analysis.is_right_problem}{note}")
    if analysis.premortem_reasons:
        print("If this decision looks wrong a year from now, most likely because:")
        for item in analysis.premortem_reasons:
            print(f"  - {item}")


async def _run_question_framing_cli(service: DeliberationService, run_id: str) -> None:
    """Stage 0's interactive CLI flow (see ADR 009) -- up to 3 paid calls:
    one combined problem-type + interview-questions call, then candidate A
    and candidate B each independently return a combined framing critique +
    premortem. CLI only for now; the web wizard is a deferred phase.

    sys.stdin.isatty() is checked once, up front: a non-interactive
    invocation (piped stdin) cannot usefully answer 5-8 interview
    questions, so it skips the two framing-analysis calls entirely (they
    would only ever see empty answers) and locks with the original
    question unchanged -- 1 paid call instead of 3, not 3 calls spent on
    answers that were never actually given.
    """
    print("\n=== Stage 0: Question framing (optional, untested hypothesis) ===")
    problem_framing = await service.run_question_framing(run_id)
    print(
        f"\nProblem type (advisory only -- never changes what runs next): "
        f"{problem_framing.classification}"
    )
    print(f"  {problem_framing.reason}")

    run = service.get_run(run_id)

    if not sys.stdin.isatty():
        print(
            "\nNon-interactive input detected -- skipping the interview and "
            "the independent framing critique/premortem (answers would be "
            "empty). Locking with the original question, unchanged."
        )
        service.record_interview_answers(run_id, ["" for _ in problem_framing.questions])
        service.lock_question_framing(
            run_id, final_question=run.question, success_criteria=None,
        )
        print(f"\nLocked. Proceeding with: {run.question}")
        return

    print("\nA few questions to help sharpen this question before analysis begins:")
    answers: list[str] = []
    for i, question_text in enumerate(problem_framing.questions, start=1):
        print(f"\n{i}. {question_text}")
        answers.append(input("   > ").strip())
    service.record_interview_answers(run_id, answers)

    print("\nRunning independent framing critique + premortem (candidates A and B)...")
    analysis_a, analysis_b = await asyncio.gather(
        service.run_framing_analysis(run_id, "a"),
        service.run_framing_analysis(run_id, "b"),
    )
    _print_framing_analysis("Candidate A", analysis_a)
    _print_framing_analysis("Candidate B", analysis_b)

    print(
        "\nYou can keep the original question, or write a reframed one. "
        "The reframings above are options -- none of them is a recommendation."
    )
    print(f"\nOriginal question: {run.question}")
    final_question = run.question
    new_question = input("Final question [press Enter to keep the original]: ").strip()
    if new_question:
        final_question = new_question
    success_criteria = input(
        "Success criterion -- what would show this decision was right? [optional]: "
    ).strip()
    service.lock_question_framing(
        run_id, final_question=final_question, success_criteria=success_criteria or None,
    )
    print(f"\nLocked. Proceeding with: {final_question}")


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

    if args.diagnose_run:
        from llm_deliberation.diagnostics import (
            format_completion_reserve_report,
            format_efficiency_table,
            format_language_contract_report,
            stage_efficiency_rows,
        )

        try:
            record = service.get_run(args.diagnose_run)
        except KeyError:
            print(f"Error: unknown run_id {args.diagnose_run!r}", file=sys.stderr)
            return 2
        print(f"Run {record.id} | output_language={record.language} | "
              f"working_language={record.working_language or '(legacy: ' + record.language + ')'}")
        rows = stage_efficiency_rows(record)
        print(format_efficiency_table(rows))
        print()
        print("Working-language contract:")
        print(format_language_contract_report(rows))
        print()
        print("Completion reserve:")
        diag_settings = Settings.load(profile_override=record.profile, red_team_override=record.red_team_enabled)
        print(format_completion_reserve_report(record, diag_settings))
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
            question_framing_enabled=args.question_framing,
        )
    except InvalidBudgetError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(f"Run ID: {run_id}")
    if args.max_cost:
        print(f"Max run cost: ${args.max_cost}")

    if args.question_framing:
        try:
            await _run_question_framing_cli(service, run_id)
        except (ProviderGenerationError, QuestionFramingParseError, BudgetExceededError) as exc:
            print(f"\nStage 0 (question framing) failed: {exc}", file=sys.stderr)
            print(
                f"Run {run_id} was created but not started. Re-run without "
                "--question-framing to analyze the original question "
                "directly, or try again.",
                file=sys.stderr,
            )
            return 1

    print("Running deliberation...")
    record = await service.start_run(run_id)
    return _print_run_outcome(service, record, args.output)


def main() -> None:
    # "costs" is a distinct verb, not a deliberation option -- dispatched
    # here, before the main flat parser ever sees argv, so it is never
    # mistaken for a (very short, literal) question via the positional
    # `question` argument below.
    if len(sys.argv) > 1 and sys.argv[1] == "costs":
        try:
            raise SystemExit(asyncio.run(_run_costs(sys.argv[2:])))
        except KeyboardInterrupt:
            raise SystemExit(130)
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            raise SystemExit(1)

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
