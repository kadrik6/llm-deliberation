"""Read-only cost aggregation, derived entirely from already-persisted
stage/stage0 cost/token/call data (see store.py) -- no new schema, no
network calls, no provider billing API. Shared by the web `/costs` page
and the `llm-deliberate costs` CLI command so both render the exact same
numbers from the exact same derivation.

Every figure here is an application-side estimate, not a provider invoice
-- the same declared-approximate pricing already used throughout
cost_budget.py (see that module's own disclaimer). Every caller of this
module's output must keep that labeled as "estimated, from this app's own
calls; not provider billing" -- never presented as a reconciled billing
figure.

Scope, by design: the last COST_REPORT_WINDOW_DAYS days, grouped by
provider, by day (the run's own created_at date), and by run. Stage 0 (see
ADR 009) has no per-call or per-provider breakdown in the schema -- its
3 calls are accumulated into one `stage0.estimated_cost_usd` column, never
split -- so it is reported as one combined figure, never a fabricated
per-provider split.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from llm_deliberation.orchestrator import STAGE_PROVIDER
from llm_deliberation.store import RunRecord, Stage0CostRow, StageCostRow

COST_REPORT_WINDOW_DAYS = 30

# providers.GeminiFallbackProvider.provider_name is "Google" (the SDK-
# oriented name stored on each succeeded stage) -- "Gemini" is the
# user-facing label everywhere else in this app (e.g.
# web.presenter.STAGE_GROUPS's red_team row), so the cost report uses the
# same display name instead of exposing the internal SDK string.
_DISPLAY_PROVIDER_NAME: dict[str, str] = {"Google": "Gemini"}

# Deterministic stage-name -> provider-role mapping (see
# orchestrator.STAGE_PROVIDER): every stage except convergence_analysis
# always uses the same provider regardless of success/failure, since which
# SDK client would be used is fixed before the call is ever sent. Used only
# as a fallback when a stage's own `provider` column is unset -- i.e. a
# stage that incurred cost but failed before mark_stage_succeeded recorded
# which provider actually answered (mark_stage_failed never sets it).
_ROLE_DISPLAY_NAME: dict[str, str] = {"a": "OpenAI", "b": "Anthropic", "red": "Gemini"}

# convergence_analysis's provider is configurable (openai/anthropic/gemini
# -- see Settings.convergence_provider) and deliberately excluded from
# _ROLE_DISPLAY_NAME above: unlike every other stage, there is no single
# correct fallback to guess for it, so a failed convergence_analysis stage
# with no recorded provider is reported honestly as unknown rather than
# assigned a potentially-wrong provider.
UNKNOWN_PROVIDER_LABEL = "Unknown (stage failed before its provider was recorded)"

STAGE0_PROVIDER_LABEL = "Stage 0 (combined per run, not split by provider or call)"

_QUESTION_PREVIEW_CHARS = 160


def _question_preview(question: str) -> str:
    """Deliberately duplicated from web.presenter.history_question_preview
    rather than imported: this module must stay usable from cli.py without
    pulling in the web package, matching report.py's own existing
    boundary (see that module's docstring)."""
    if len(question) <= _QUESTION_PREVIEW_CHARS:
        return question
    return question[:_QUESTION_PREVIEW_CHARS].rstrip() + "…"


def _provider_label(row: StageCostRow) -> str:
    if row.provider:
        return _DISPLAY_PROVIDER_NAME.get(row.provider, row.provider)
    role = STAGE_PROVIDER.get(row.name)
    if role is None:
        return UNKNOWN_PROVIDER_LABEL
    return _ROLE_DISPLAY_NAME.get(role, UNKNOWN_PROVIDER_LABEL)


def _day_key(created_at: str) -> str:
    # A plain string slice of the ISO timestamp's date portion
    # ("YYYY-MM-DD...") -- never a timezone-reparse that could shift the
    # date; every created_at in this app is already UTC (see utc_now_iso).
    return created_at[:10]


def cutoff_iso(now: datetime, window_days: int = COST_REPORT_WINDOW_DAYS) -> str:
    return (now - timedelta(days=window_days)).isoformat()


@dataclass(slots=True)
class ProviderTotal:
    provider: str
    estimated_cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0


@dataclass(slots=True)
class DayTotal:
    day: str
    estimated_cost_usd: float = 0.0


@dataclass(slots=True)
class RunTotal:
    run_id: str
    created_at: str
    status: str
    question_preview: str
    main_pipeline_cost_usd: float
    stage0_cost_usd: float

    @property
    def total_cost_usd(self) -> float:
        return self.main_pipeline_cost_usd + self.stage0_cost_usd


@dataclass(slots=True)
class CostReport:
    """Estimated, from this app's own calls; not provider billing (see
    module docstring) -- every renderer of this report must keep that
    disclaimer next to the figures below."""

    window_days: int
    since: str
    until: str
    total_cost_usd: float
    main_pipeline_cost_usd: float
    stage0_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    total_calls: int
    by_provider: list[ProviderTotal] = field(default_factory=list)
    by_day: list[DayTotal] = field(default_factory=list)
    by_run: list[RunTotal] = field(default_factory=list)


def build_cost_report(
    *,
    runs: list[RunRecord],
    stage_rows: list[StageCostRow],
    stage0_rows: list[Stage0CostRow],
    now: datetime,
    window_days: int = COST_REPORT_WINDOW_DAYS,
) -> CostReport:
    """Pure aggregation over already-fetched rows -- no DB access here
    (that's store.py's job; see DeliberationService.cost_report, the
    caller that fetches these three lists and calls this function). `now`
    is a required, explicit parameter (never datetime.now() called inside
    this function) so tests can pass a fixed clock and get a deterministic
    window boundary."""
    since = now - timedelta(days=window_days)

    stage0_cost_by_run: dict[str, float] = {
        row.run_id: row.estimated_cost_usd for row in stage0_rows
    }

    provider_totals: dict[str, ProviderTotal] = {}
    for row in stage_rows:
        label = _provider_label(row)
        total = provider_totals.setdefault(label, ProviderTotal(provider=label))
        total.estimated_cost_usd += row.estimated_cost_usd
        total.input_tokens += row.input_tokens
        total.output_tokens += row.output_tokens
        total.calls += row.model_attempts

    stage0_total_cost = sum(stage0_cost_by_run.values())
    if stage0_total_cost:
        # A distinct bucket, never merged into OpenAI/Anthropic: Stage 0's
        # 3 calls are not individually costed in the schema, so splitting
        # this total across providers would be a fabricated number, not a
        # derived one (see module docstring).
        provider_totals[STAGE0_PROVIDER_LABEL] = ProviderTotal(
            provider=STAGE0_PROVIDER_LABEL, estimated_cost_usd=stage0_total_cost,
        )

    day_totals: dict[str, float] = defaultdict(float)
    run_totals: list[RunTotal] = []
    main_pipeline_total = 0.0
    for run in runs:
        stage0_cost = stage0_cost_by_run.get(run.id, 0.0)
        day_totals[_day_key(run.created_at)] += run.estimated_total_cost_usd + stage0_cost
        main_pipeline_total += run.estimated_total_cost_usd
        run_totals.append(
            RunTotal(
                run_id=run.id,
                created_at=run.created_at,
                status=run.status,
                question_preview=_question_preview(run.question),
                main_pipeline_cost_usd=run.estimated_total_cost_usd,
                stage0_cost_usd=stage0_cost,
            )
        )

    return CostReport(
        window_days=window_days,
        since=since.isoformat(),
        until=now.isoformat(),
        total_cost_usd=main_pipeline_total + stage0_total_cost,
        main_pipeline_cost_usd=main_pipeline_total,
        stage0_cost_usd=stage0_total_cost,
        total_input_tokens=sum(row.input_tokens for row in stage_rows),
        total_output_tokens=sum(row.output_tokens for row in stage_rows),
        total_calls=sum(row.model_attempts for row in stage_rows),
        by_provider=sorted(
            provider_totals.values(), key=lambda p: p.estimated_cost_usd, reverse=True
        ),
        by_day=[DayTotal(day=d, estimated_cost_usd=c) for d, c in sorted(day_totals.items())],
        by_run=sorted(run_totals, key=lambda r: r.created_at, reverse=True),
    )
