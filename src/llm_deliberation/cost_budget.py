"""Application-side, per-run cost budget: a hard safety guard the app enforces
on itself, never the provider's own account balance (see docs/architecture.md
and README's Cost section).

Three distinct things live here, all Decimal-based to avoid float comparison
error at the hard-guard boundary:

- `parse_budget_usd` -- validates a user-supplied "Max run cost (USD)" value.
- `estimate_run_cost_range` -- a deterministic, declared-approximate pre-run
  estimate shown before Start (see Section 7 of the reliability/cost pass).
- `RunBudgetGuard` / `estimate_max_call_cost_usd` -- the runtime hard guard,
  checked before every paid provider call (initial + truncation recovery +
  Gemini fallback-chain retries), using the *actual* prompt about to be sent
  rather than a generic estimate.

Nothing here touches the existing float-based `pricing.estimate_cost`, which
computes *actual* cost from real provider-reported token usage after a call
completes -- that accounting must stay exactly as accurate as it already is.
This module only ever estimates a call's cost *before* it is made.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from llm_deliberation.pricing import PRICING


def _new_lock() -> threading.Lock:
    return threading.Lock()

# Chars-per-token used only for the runtime upper-bound guard's input-token
# estimate. English text averages roughly 4 chars/token; 3 is used instead to
# deliberately bias the estimate high (an "upper bound" that occasionally
# undercounts real cost is worse than one that occasionally leaves a little
# budget headroom unspent) -- not a tokenizer-accurate count, and never
# presented as one.
CONSERVATIVE_CHARS_PER_TOKEN = Decimal(3)


class InvalidBudgetError(ValueError):
    """Raised by parse_budget_usd for a non-empty, non-positive, or
    unparsable budget value. A ValueError subclass so existing `except
    ValueError` call sites (e.g. web/app.py's submit_run) keep working
    without special-casing, mirroring service.QuestionTooLongError's own
    precedent for a narrowly-scoped validation error."""


def parse_budget_usd(raw: str | None) -> Decimal | None:
    """Parse a user/env-supplied "max run cost" value.

    None or an empty/whitespace-only string means "no budget" (current,
    uncapped behavior) -- this is the explicit product decision from the
    task brief, not an invented default cap. Any other value must parse as a
    positive Decimal; anything else (zero, negative, non-numeric, NaN,
    Infinity) raises InvalidBudgetError with a message safe to show the user.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise InvalidBudgetError(f"'{raw}' is not a valid dollar amount.") from exc
    if not value.is_finite() or value <= 0:
        raise InvalidBudgetError("Max run cost must be a positive dollar amount.")
    return value


def format_usd(amount: Decimal) -> str:
    return f"${amount.quantize(Decimal('0.0001')):,}"


# -- pre-run estimate ---------------------------------------------------

# Deliberately simple, declared-approximate "typical prompt size" per stage,
# in characters -- NOT reactive to the actual typed question/context (see
# cost_budget module docstring / Section 7 of the task brief: "does not need
# false precision" and question/context size is explicitly optional to
# incorporate). Reflects how much text each stage's prompt tends to carry
# forward from earlier stages (critique/revision/convergence/synthesis see
# progressively more prior output), not any single run's real content.
_STAGE_TYPICAL_INPUT_CHARS: dict[str, int] = {
    "analysis_a": 900,
    "analysis_b": 900,
    "critique_a_of_b": 2400,
    "critique_b_of_a": 2400,
    "red_team": 3200,
    "revision_a": 4000,
    "revision_b": 4000,
    "convergence_analysis": 8000,
    "synthesis": 6000,
}

# Fraction of max_output_tokens assumed actually used for the low/high ends
# of the pre-run range. The high end intentionally matches the runtime
# upper-bound guard's own worst case (100% of the configured ceiling) so the
# two numbers never contradict each other; the low end (30%) is a rough
# "typical, not maxed-out" assumption for ordinary responses.
_LOW_OUTPUT_FRACTION = Decimal("0.3")
_HIGH_OUTPUT_FRACTION = Decimal("1.0")


@dataclass(slots=True)
class CostEstimate:
    low_usd: Decimal
    high_usd: Decimal

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{format_usd(self.low_usd)}-{format_usd(self.high_usd)}"


def _rate_for(model: str) -> tuple[Decimal, Decimal] | None:
    rates = PRICING.get(model)
    if not rates:
        return None
    return Decimal(str(rates[0])), Decimal(str(rates[1]))


def _stage_cost(model: str, input_chars: int, output_tokens: Decimal) -> Decimal:
    rates = _rate_for(model)
    if rates is None:
        return Decimal(0)
    input_rate, output_rate = rates
    input_tokens = Decimal(input_chars) / CONSERVATIVE_CHARS_PER_TOKEN
    return (input_tokens / Decimal(1_000_000) * input_rate) + (
        output_tokens / Decimal(1_000_000) * output_rate
    )


def estimate_run_cost_range(
    *,
    openai_model: str,
    anthropic_model: str,
    gemini_model: str,
    convergence_provider: str,
    convergence_model: str,
    red_team_enabled: bool,
    max_output_tokens: int,
) -> CostEstimate:
    """Deterministic low/high pre-run estimate, from profile pricing + which
    stages are enabled -- see the module docstring and Section 7. Always a
    range, never a single number, and always disclosed as an estimate (see
    web/i18n.py's cost_estimate_disclaimer) -- never a guarantee.
    """
    stage_models: dict[str, str] = {
        "analysis_a": openai_model,
        "analysis_b": anthropic_model,
        "critique_a_of_b": openai_model,
        "critique_b_of_a": anthropic_model,
        "revision_a": openai_model,
        "revision_b": anthropic_model,
        "synthesis": openai_model,
        "convergence_analysis": convergence_model if convergence_provider else anthropic_model,
    }
    if red_team_enabled:
        stage_models["red_team"] = gemini_model

    low = Decimal(0)
    high = Decimal(0)
    max_out = Decimal(max_output_tokens)
    for stage, model in stage_models.items():
        chars = _STAGE_TYPICAL_INPUT_CHARS[stage]
        low += _stage_cost(model, chars, max_out * _LOW_OUTPUT_FRACTION)
        high += _stage_cost(model, chars, max_out * _HIGH_OUTPUT_FRACTION)
    return CostEstimate(low_usd=low, high_usd=high)


# -- runtime hard guard ---------------------------------------------------


def estimate_max_call_cost_usd(model: str, *, prompt_chars: int, max_output_tokens: int) -> Decimal:
    """Conservative upper bound for one specific, about-to-be-sent call:
    the full configured output-token ceiling (worst case: no truncation
    headroom left) priced against the *actual* prompt text length already
    built for this call. See CONSERVATIVE_CHARS_PER_TOKEN for why chars/3
    rather than a real tokenizer is used.
    """
    return _stage_cost(model, prompt_chars, Decimal(max_output_tokens))


class BudgetExceededError(RuntimeError):
    """Raised by RunBudgetGuard.check() when committing to a next call would
    exceed the run's configured budget. Carries enough for a caller to build
    a ProviderGenerationError (reusing existing persistence/UI machinery --
    see providers.ProviderGenerationError and service._execute) without this
    module needing to import providers.py itself (providers.py is free to
    import cost_budget.py, not the reverse, avoiding a cycle).
    """

    def __init__(
        self,
        *,
        spent_usd: Decimal,
        budget_usd: Decimal,
        estimated_next_usd: Decimal,
    ):
        self.spent_usd = spent_usd
        self.budget_usd = budget_usd
        self.estimated_next_usd = estimated_next_usd
        super().__init__(
            f"Continuing may exceed the run budget of {format_usd(budget_usd)}: "
            f"already spent {format_usd(spent_usd)}, next request estimated up to "
            f"{format_usd(estimated_next_usd)}."
        )


@dataclass(slots=True)
class RunBudgetGuard:
    """Tracks accumulated spend for one run execution and enforces the hard
    cap before each new paid provider call. `budget_usd is None` means no
    cap -- every check() call is then a no-op, exactly preserving current
    (pre-budget-feature) behavior for old/unbudgeted runs.

    `spent_usd` starts from the run's already-persisted cost total (so a
    resumed run correctly accounts for everything spent before this
    execution) and is advanced explicitly via record_actual() as stages
    complete -- never inferred, never re-derived from provider responses
    here (that remains store.py/service.py's job).

    A single instance is shared across every stage in a wave, and stages
    within a wave run concurrently via `asyncio.to_thread` -- real OS
    threads, not just cooperative coroutines -- so check()/record_actual()
    guard their read-modify-write of spent_usd with a lock rather than
    assuming GIL atomicity is enough for `self.spent_usd += x`.
    """

    budget_usd: Decimal | None
    spent_usd: Decimal = field(default=Decimal(0))
    _lock: object = field(default_factory=_new_lock, repr=False, compare=False)

    def remaining_usd(self) -> Decimal | None:
        with self._lock:
            return None if self.budget_usd is None else self.budget_usd - self.spent_usd

    def check(self, estimated_next_usd: Decimal) -> None:
        if self.budget_usd is None:
            return
        with self._lock:
            spent = self.spent_usd
        if spent + estimated_next_usd > self.budget_usd:
            raise BudgetExceededError(
                spent_usd=spent,
                budget_usd=self.budget_usd,
                estimated_next_usd=estimated_next_usd,
            )

    def record_actual(self, amount_usd: Decimal) -> None:
        with self._lock:
            self.spent_usd += amount_usd

    def resync(self, spent_usd: Decimal) -> None:
        """Overwrite spent_usd with an authoritative total (e.g. re-derived
        from store.Repository.sum_stage_costs after a wave completes) rather
        than accumulating -- used once per wave, after every stage in it has
        been durably persisted, so any drift between live record_actual()
        calls and the real database total is corrected rather than compounded.
        """
        with self._lock:
            self.spent_usd = spent_usd


def default_budget_from_float(value: float) -> Decimal:
    """Convert the existing float-accumulated run cost (store.py's
    estimated_total_cost_usd, REAL column) into a Decimal for the guard's
    starting point. A documented, narrow float->Decimal boundary -- see the
    module docstring: the guard's own arithmetic is exact Decimal, only this
    one conversion of an already-existing float total is not, and rounding
    to 6 decimal places (well below a cent) keeps it a non-issue for a
    dollar-scale budget comparison.
    """
    return Decimal(str(round(value, 6)))
