from __future__ import annotations

import asyncio
from decimal import Decimal

from llm_deliberation import convergence, prompts
from llm_deliberation.config import Settings
from llm_deliberation.cost_budget import BudgetExceededError, RunBudgetGuard, estimate_max_call_cost_usd
from llm_deliberation.language_detect import classify_language_contract
from llm_deliberation.providers import (
    AnthropicProvider,
    GeminiFallbackProvider,
    OpenAIProvider,
    Provider,
    ProviderGenerationError,
    budget_exceeded_to_provider_error,
)
from llm_deliberation.types import ModelResponse

# Stages grouped into waves that can run concurrently. A stage only depends
# on stages from earlier waves, never on siblings in its own wave.
WAVES: tuple[tuple[str, ...], ...] = (
    ("analysis_a", "analysis_b"),
    ("critique_a_of_b", "critique_b_of_a", "red_team"),
    ("revision_a", "revision_b"),
    ("convergence_analysis",),
    ("synthesis",),
)

# Which provider attribute (see DeliberationOrchestrator.__init__) handles
# each stage.
STAGE_PROVIDER: dict[str, str] = {
    "analysis_a": "a",
    "analysis_b": "b",
    "critique_a_of_b": "a",
    "critique_b_of_a": "b",
    "red_team": "red",
    "revision_a": "a",
    "revision_b": "b",
    "convergence_analysis": "convergence",
    "synthesis": "a",
}

ALL_STAGE_NAMES: tuple[str, ...] = tuple(
    name for wave in WAVES for name in wave
)

# Stages the pipeline treats as optional after a failure: the run can still
# succeed without them, with downstream stages proceeding on an explicit
# "this is absent" basis rather than an error. red_team can also be disabled
# from the start (see DeliberationService.create_run); convergence_analysis
# is always attempted but can be skipped once it has failed.
SKIPPABLE_STAGE_NAMES: frozenset[str] = frozenset({"red_team", "convergence_analysis"})

# Verbose, intermediate reasoning stages -- these may use a different
# ("working") language than the run's own output language, purely for
# token-efficiency/reliability (see prompts.working_language_override_instruction
# and docs/decisions/008-working-language.md). convergence_analysis and
# synthesis are deliberately NOT in this set: they are the user-facing
# stages and must always produce their explanatory text in the run's output
# language, regardless of what language earlier stages worked in.
WORKING_LANGUAGE_STAGES: frozenset[str] = frozenset(
    {
        "analysis_a",
        "analysis_b",
        "critique_a_of_b",
        "critique_b_of_a",
        "red_team",
        "revision_a",
        "revision_b",
    }
)


def default_working_language(output_language: str) -> str:
    """The working language a NEW run should use by default, given its
    output language. Currently always English: the working language is
    chosen purely as an internal token-efficiency/reliability choice for
    verbose intermediate stages, never because a non-English output
    language is itself unsupported (see the product principle -- Estonian
    stays a fully first-class output/UI language). If a third supported
    output language is ever added, this is the one place its default
    working language would be decided.
    """
    return "en"


def resolve_stage_language(stage: str, *, output_language: str, working_language: str) -> str:
    """The single centralized stage-language policy (see prompts.base_system,
    which consumes this). A verbose intermediate stage (WORKING_LANGUAGE_STAGES)
    resolves to `working_language`; every other stage (convergence_analysis,
    synthesis) always resolves to `output_language`.

    For a legacy run where `working_language == output_language` (see
    service._execute's fallback: a run created before this feature existed
    has no stored working_language, and falls back to its own output
    language), this returns `output_language` for every stage regardless of
    WORKING_LANGUAGE_STAGES membership -- i.e. no split behavior at all,
    exactly reproducing this run's original, single-language behavior.
    """
    if stage in WORKING_LANGUAGE_STAGES:
        return working_language
    return output_language


async def _call(provider: Provider, *, system: str, prompt: str, **kwargs: object) -> ModelResponse:
    # Provider SDK calls are synchronous. to_thread lets independent calls run
    # concurrently without coupling the project to provider-specific async APIs.
    return await asyncio.to_thread(
        provider.generate,
        system=system,
        prompt=prompt,
        **kwargs,
    )


def _build_prompt(stage: str, question: str, texts: dict[str, str]) -> str:
    if stage == "analysis_a" or stage == "analysis_b":
        return prompts.independent_analysis(question)
    if stage == "critique_a_of_b":
        return prompts.critique(question, texts["analysis_b"])
    if stage == "critique_b_of_a":
        return prompts.critique(question, texts["analysis_a"])
    if stage == "red_team":
        return prompts.red_team(question, texts["analysis_a"], texts["analysis_b"])
    if stage == "revision_a":
        return prompts.revision(
            question, texts["analysis_a"], texts["critique_b_of_a"], texts.get("red_team")
        )
    if stage == "revision_b":
        return prompts.revision(
            question, texts["analysis_b"], texts["critique_a_of_b"], texts.get("red_team")
        )
    if stage == "convergence_analysis":
        return prompts.convergence_analysis(
            question,
            texts["analysis_a"],
            texts["analysis_b"],
            texts["critique_a_of_b"],
            texts["critique_b_of_a"],
            texts.get("red_team"),
            texts["revision_a"],
            texts["revision_b"],
        )
    if stage == "synthesis":
        convergence_raw = texts.get("convergence_analysis")
        convergence_context = (
            convergence.render_for_prompt(convergence.parse_convergence_analysis(convergence_raw))
            if convergence_raw is not None
            else None
        )
        return prompts.synthesis(
            question,
            texts["revision_a"],
            texts["revision_b"],
            texts.get("red_team"),
            convergence_context,
        )
    raise ValueError(f"Unknown stage: {stage}")


def _build_convergence_provider(settings: Settings) -> Provider:
    if settings.convergence_provider == "openai":
        return OpenAIProvider(
            model=settings.convergence_model,
            max_output_tokens=settings.max_output_tokens,
            effort=settings.openai_effort,
            timeout_seconds=settings.provider_timeout_seconds,
            max_retries=settings.provider_max_retries,
        )
    if settings.convergence_provider == "anthropic":
        return AnthropicProvider(
            model=settings.convergence_model,
            max_output_tokens=settings.max_output_tokens,
            effort=settings.anthropic_effort,
            timeout_seconds=settings.provider_timeout_seconds,
            max_retries=settings.provider_max_retries,
            response_schema=_anthropic_convergence_schema(),
        )
    if settings.convergence_provider == "gemini":
        return GeminiFallbackProvider(
            models=[settings.convergence_model, *settings.gemini_fallback_models],
            max_output_tokens=settings.max_output_tokens,
            thinking_level=settings.gemini_thinking_level,
            timeout_seconds=settings.provider_timeout_seconds,
            max_retries=settings.provider_max_retries,
            min_request_timeout_seconds=settings.gemini_min_request_timeout_seconds,
        )
    raise ValueError(f"Unknown convergence_provider: {settings.convergence_provider!r}")


def _anthropic_convergence_schema() -> dict[str, object]:
    """The convergence_analysis Pydantic schema, translated into the shape
    Anthropic's native structured-output API expects -- see
    providers.AnthropicProvider's `response_schema` param. This is the one
    place the provider-independent `convergence.ConvergenceAnalysis` domain
    model gets translated into Anthropic-specific request plumbing (see
    convergence.py's module docstring); the domain model itself never
    imports or knows about the `anthropic` package. Pure/local schema
    conversion -- no network call, computed once per orchestrator/provider
    construction, not per request.
    """
    import anthropic

    return anthropic.transform_schema(convergence.ConvergenceAnalysis)


def _finalize_convergence_response(response: ModelResponse) -> ModelResponse:
    """Validate the raw response against ConvergenceAnalysis and replace
    .text with the canonical (pretty-printed, schema-valid) JSON.

    A response that isn't valid JSON matching the schema fails the stage --
    retryable, like any other provider error -- rather than persisting
    malformed analytical data. Re-raised as ProviderGenerationError (not the
    bare ConvergenceParseError) so the already-incurred cost of this call is
    not silently dropped from the run total: ConvergenceParseError itself
    carries no cost/model provenance (parse_convergence_analysis only ever
    sees raw text, never the ModelResponse), and service.py only persists
    cost/provenance from a failed stage for exception types it recognizes.

    Callers must never invoke this when `response.incomplete_reason` is
    already set (see run_stage, which only calls this once truncation has
    been ruled out) -- attempting to JSON-parse a response already known to
    be truncated would misclassify a plain output_truncated failure as
    structured_output_invalid, hiding the real cause.
    """
    try:
        analysis = convergence.parse_convergence_analysis(response.text)
    except convergence.ConvergenceParseError as exc:
        raise ProviderGenerationError(
            # The top-level message stays short/technical-but-not-huge (a
            # parser exception's own str(), which names the failure
            # location but not the surrounding text) -- see web/i18n.py's
            # convergence_structured_output_invalid_message for the actual
            # user-facing wording shown in the main UI instead of this.
            f"convergence_analysis response failed validation: {exc}",
            requested_model=response.requested_model or response.model,
            attempts=1,
            # A bounded excerpt of the actual raw response that failed to
            # parse/validate -- technical provenance only (never rendered
            # as the primary error; see partials/pipeline.html), but no
            # longer silently lost the way it was before this fix (see
            # convergence.ConvergenceParseError.raw_text_excerpt /
            # RAW_TEXT_EXCERPT_CHARS).
            attempt_log=[
                {
                    "model": response.model,
                    "attempt_number": 1,
                    "outcome": "failed",
                    "delay_before_seconds": 0.0,
                    "http_code": None,
                    "error": exc.raw_text_excerpt or None,
                    "reason": "response did not match the expected schema",
                    "estimated_cost_usd": response.estimated_cost_usd,
                }
            ],
            fallback_used=False,
            fallback_reason=None,
            estimated_cost_usd=response.estimated_cost_usd,
            reason="structured_output_invalid",
        ) from exc
    response.text = convergence.canonical_json(analysis)
    return response


class DeliberationOrchestrator:
    """Owns provider instances and knows how to execute a single stage.

    Multi-stage sequencing, persistence, and resumability live in
    DeliberationService; this class only knows how to run one named stage
    given the question and the text produced by prior stages.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

        self.a = OpenAIProvider(
            model=settings.openai_model,
            max_output_tokens=settings.max_output_tokens,
            effort=settings.openai_effort,
            timeout_seconds=settings.provider_timeout_seconds,
            max_retries=settings.provider_max_retries,
        )
        self.b = AnthropicProvider(
            model=settings.anthropic_model,
            max_output_tokens=settings.max_output_tokens,
            effort=settings.anthropic_effort,
            timeout_seconds=settings.provider_timeout_seconds,
            max_retries=settings.provider_max_retries,
        )
        self.red = GeminiFallbackProvider(
            models=[settings.gemini_model, *settings.gemini_fallback_models],
            max_output_tokens=settings.max_output_tokens,
            thinking_level=settings.gemini_thinking_level,
            timeout_seconds=settings.provider_timeout_seconds,
            max_retries=settings.provider_max_retries,
            min_request_timeout_seconds=settings.gemini_min_request_timeout_seconds,
        )
        self.convergence = _build_convergence_provider(settings)

    def stage_upper_bound_cost(
        self,
        stage: str,
        question: str,
        texts: dict[str, str],
        *,
        output_language: str = "en",
        working_language: str = "en",
    ) -> Decimal:
        """Conservative upper-bound cost of this stage's *initial* call, from
        the real prompt that would be sent -- used by service._execute to
        decide, before dispatch, whether admitting this stage into its wave
        would exceed the run budget (see cost_budget.py). For
        GeminiFallbackProvider (red_team), the bound uses the most expensive
        model in its configured chain, not just the preferred model: a
        fallback can and does substitute a *different* model, so pricing
        only the first one would understate the true worst case.
        """
        provider = getattr(self, STAGE_PROVIDER[stage])
        prompt = _build_prompt(stage, question, texts)
        stage_language = resolve_stage_language(
            stage, output_language=output_language, working_language=working_language
        )
        system = prompts.base_system(stage_language, output_language)
        prompt_chars = len(system) + len(prompt)
        if isinstance(provider, GeminiFallbackProvider):
            return max(
                estimate_max_call_cost_usd(
                    model, prompt_chars=prompt_chars, max_output_tokens=provider.max_output_tokens
                )
                for model in provider.models
            )
        return estimate_max_call_cost_usd(
            provider.model, prompt_chars=prompt_chars, max_output_tokens=provider.max_output_tokens
        )

    async def run_stage(
        self,
        stage: str,
        question: str,
        texts: dict[str, str],
        *,
        gemini_mode: str = "chain",
        output_language: str = "en",
        working_language: str = "en",
        budget_guard: RunBudgetGuard | None = None,
    ) -> ModelResponse:
        provider = getattr(self, STAGE_PROVIDER[stage])
        prompt = _build_prompt(stage, question, texts)
        # gemini_mode only means anything to GeminiFallbackProvider (used by
        # the red_team stage's "Retry preferred model" vs "Retry with
        # fallback chain" UI actions); every other provider ignores it.
        # budget_guard is passed through for the same reason: only
        # GeminiFallbackProvider has its own internal multi-call retry/
        # fallback loop that needs to re-check the budget between attempts
        # (see providers.GeminiFallbackProvider.generate) -- every other
        # provider here makes exactly one call, already admitted by
        # service._execute's pre-dispatch check before this coroutine was
        # even scheduled.
        kwargs: dict[str, object] = {"mode": gemini_mode} if stage == "red_team" else {}
        if stage == "red_team" and budget_guard is not None:
            kwargs["budget_guard"] = budget_guard
        # Centralized stage-language policy (see resolve_stage_language):
        # this is the ONLY place a stage's actual language is decided. The
        # user's original question/context is always passed through
        # unchanged regardless of stage_language, never translated (see
        # _build_prompt) -- only the model's own reasoning/response language
        # changes, per prompts.base_system's language_instruction.
        stage_language = resolve_stage_language(
            stage, output_language=output_language, working_language=working_language
        )
        system = prompts.base_system(stage_language, output_language)
        response = await _call(provider, system=system, prompt=prompt, **kwargs)

        # Bounded automatic recovery for a clearly truncated (output-length-
        # limited) response: exactly one retry of the same stage, never more.
        # Not applied to GeminiFallbackProvider -- that provider already
        # absorbs its own truncation detection into its existing per-model
        # retry-then-fallback budget (see providers.py), so adding a second,
        # separate recovery layer on top would double-apply retries for
        # red_team specifically. Not applied to empty_output either: an
        # empty response with no length-limit signal is a different failure
        # mode this iteration deliberately does not auto-retry (see
        # Section 3 of the reliability pass) -- the user can still Retry
        # manually via the existing durable stage-retry UI.
        if response.incomplete_reason == "output_truncated" and not isinstance(
            provider, GeminiFallbackProvider
        ):
            # Keyed by stage_language (the language this stage is actually
            # responding in), never output_language -- an Estonian-output
            # run's English-working-language stage must get an English
            # "your response was cut off, retry concisely" instruction too,
            # not an Estonian one that would pull the model back toward
            # switching languages mid-recovery (Section 9 of the working-
            # language work).
            recovery_system = system + "\n\n" + prompts.truncation_recovery_instruction(stage_language)
            sunk_cost = response.estimated_cost_usd
            initial_model = response.model
            # Provenance for the initial (truncated, paid, discarded) attempt
            # -- see providers.group_attempts_by_model's phase-aware sibling
            # (presenter.attempt_log_phases) for how this is rendered. Built
            # before the recovery call so it is available whether that call
            # succeeds, fails, or itself raises.
            initial_attempt = {
                "phase": "initial",
                "model": initial_model,
                "outcome": "output_truncated",
                "estimated_cost_usd": sunk_cost,
            }
            if budget_guard is not None:
                # The recovery call is itself a new paid request -- gated
                # exactly like any other (see Section 9 of the task brief:
                # truncation recovery must respect the same run budget).
                # sunk_cost is recorded first so the check below sees the
                # true accumulated total, including this stage's own
                # already-spent (truncated, discarded) first attempt.
                budget_guard.record_actual(Decimal(str(sunk_cost)))
                recovery_prompt_chars = len(recovery_system) + len(prompt)
                estimated_recovery_cost = estimate_max_call_cost_usd(
                    initial_model,
                    prompt_chars=recovery_prompt_chars,
                    max_output_tokens=provider.max_output_tokens,
                )
                try:
                    budget_guard.check(estimated_recovery_cost)
                except BudgetExceededError as budget_exc:
                    raise budget_exceeded_to_provider_error(
                        budget_exc,
                        requested_model=initial_model,
                        attempts=1,
                        attempt_log=[initial_attempt],
                        fallback_used=False,
                        fallback_reason=None,
                        sunk_cost_usd=sunk_cost,
                    ) from budget_exc
            try:
                response = await _call(provider, system=recovery_system, prompt=prompt, **kwargs)
            except Exception as exc:
                # The retry attempt itself raised (e.g. a transport error) --
                # preserve the first (discarded, truncated) attempt's cost
                # rather than letting it vanish along with a bare exception
                # that carries no cost/provenance of its own. attempts=2:
                # two real paid-attempt-eligible calls were made for this
                # stage execution (the first paid and truncated, the second
                # attempted but never returned a usable response) -- see
                # Section 3/4 of the follow-up reliability investigation:
                # `model_attempts`/`Attempts (this try)` must count actual
                # provider calls, not just "did the final call succeed".
                raise ProviderGenerationError(
                    f"{stage}: response was truncated, and the one bounded "
                    f"recovery retry failed: {exc}",
                    requested_model=initial_model,
                    attempts=2,
                    attempt_log=[
                        initial_attempt,
                        {
                            "phase": "recovery",
                            "model": initial_model,
                            "outcome": "provider_error",
                            "estimated_cost_usd": 0.0,
                            "error": str(exc),
                        },
                    ],
                    fallback_used=False,
                    fallback_reason=None,
                    estimated_cost_usd=sunk_cost,
                    reason="output_truncated",
                ) from exc
            # Whether the retry finally succeeded or is still incomplete, the
            # first attempt's cost was genuinely spent and must stay in the
            # run's cost accounting -- never silently dropped.
            recovery_cost = response.estimated_cost_usd
            response.model_attempts = 2
            response.attempt_log = [
                initial_attempt,
                {
                    "phase": "recovery",
                    "model": response.model,
                    "outcome": "succeeded" if response.incomplete_reason is None else response.incomplete_reason,
                    "estimated_cost_usd": recovery_cost,
                },
            ]
            response.estimated_cost_usd += sunk_cost

        # Working-language contract check -- bounded, at most one corrective
        # recovery call. Only runs when an override is actually in effect
        # (stage_language != output_language; for an English-only run these
        # are always equal, so this is a no-op there) and only once the
        # response is confirmed complete (never on an already-truncated
        # response -- the block above already handles that, and partial
        # text is not meaningful evidence for language detection either).
        # A real live canary run showed gpt-5.6-terra silently answering in
        # Estonian on this exact instruction despite it being verified
        # correct (Anthropic's claude-sonnet-5 complied on the same run) --
        # see AUDIT_REPORT.md's live-canary findings and language_detect.py.
        # GeminiFallbackProvider (red_team) gets detection but NOT the
        # active recovery call, for the same reason truncation-recovery
        # above already excludes it: it has its own internal retry/fallback
        # loop, and stacking a second, separate recovery layer on top would
        # double-apply retries for that one stage specifically.
        if (
            stage in WORKING_LANGUAGE_STAGES
            and stage_language != output_language
            and response.incomplete_reason is None
        ):
            status, observed, _evidence = classify_language_contract(
                response.text, expected_language=stage_language
            )
            if status != "mismatched" or isinstance(provider, GeminiFallbackProvider):
                response.language_contract_status = status
                response.observed_language = observed
            else:
                recovery_system = system + "\n\n" + prompts.language_recovery_instruction(
                    stage_language
                )
                sunk_cost = response.estimated_cost_usd
                initial_model = response.model
                prior_attempt_log = response.attempt_log
                prior_model_attempts = response.model_attempts
                initial_attempt = {
                    "phase": "initial",
                    "model": initial_model,
                    "outcome": "language_mismatch",
                    "detected_language": observed,
                    "estimated_cost_usd": sunk_cost,
                }
                if budget_guard is not None:
                    # Mirrors the truncation-recovery block above exactly:
                    # this stage's already-incurred (mismatched, but paid)
                    # cost is recorded first so the affordability check for
                    # the recovery call itself sees the true running total.
                    budget_guard.record_actual(Decimal(str(sunk_cost)))
                    recovery_prompt_chars = len(recovery_system) + len(prompt)
                    estimated_recovery_cost = estimate_max_call_cost_usd(
                        initial_model,
                        prompt_chars=recovery_prompt_chars,
                        max_output_tokens=provider.max_output_tokens,
                    )
                    try:
                        budget_guard.check(estimated_recovery_cost)
                    except BudgetExceededError as budget_exc:
                        raise budget_exceeded_to_provider_error(
                            budget_exc,
                            requested_model=initial_model,
                            attempts=prior_model_attempts,
                            attempt_log=(prior_attempt_log or []) + [initial_attempt],
                            fallback_used=False,
                            fallback_reason=None,
                            sunk_cost_usd=sunk_cost,
                        ) from budget_exc
                recovered = await _call(provider, system=recovery_system, prompt=prompt, **kwargs)
                recovery_cost = recovered.estimated_cost_usd
                if recovered.incomplete_reason is not None:
                    # The recovery call itself came back empty/truncated --
                    # that failure mode takes precedence and is handled by
                    # the normal incomplete_reason path in service._execute
                    # (this stage fails, exactly as any other truncated/empty
                    # response would). Best-effort language provenance is
                    # still preserved: the confident mismatch that triggered
                    # this recovery attempt, plus the fact a recovery was
                    # tried, so a human reviewing the failure sees the full
                    # picture rather than just "truncated".
                    recovery_failure_entry = {
                        "phase": "language_recovery",
                        "model": recovered.model,
                        "outcome": recovered.incomplete_reason,
                        "estimated_cost_usd": recovery_cost,
                    }
                    recovered.language_contract_status = "mismatched"
                    recovered.observed_language = observed
                    recovered.language_recovery_attempted = True
                    recovered.model_attempts = prior_model_attempts + 1
                    recovered.attempt_log = (prior_attempt_log or [initial_attempt]) + [
                        recovery_failure_entry
                    ]
                    recovered.estimated_cost_usd += sunk_cost
                    response = recovered
                else:
                    recovery_status, recovery_observed, _ev2 = classify_language_contract(
                        recovered.text, expected_language=stage_language
                    )
                    recovery_outcome = (
                        "succeeded" if recovery_status == "matched" else
                        "uncertain" if recovery_status == "uncertain" else
                        "language_mismatch"
                    )
                    recovery_attempt = {
                        "phase": "language_recovery",
                        "model": recovered.model,
                        "outcome": recovery_outcome,
                        "detected_language": recovery_observed,
                        "estimated_cost_usd": recovery_cost,
                    }
                    recovered.language_contract_status = recovery_status
                    recovered.observed_language = recovery_observed
                    recovered.language_recovery_attempted = True
                    recovered.model_attempts = prior_model_attempts + 1
                    recovered.attempt_log = (prior_attempt_log or [initial_attempt]) + [
                        recovery_attempt
                    ]
                    recovered.estimated_cost_usd += sunk_cost
                    response = recovered

        # Only ever attempt to parse/validate a response that is not itself
        # already known to be incomplete -- a response still truncated even
        # after the one bounded recovery retry above (or truncated but not
        # eligible for recovery, e.g. GeminiFallbackProvider, which absorbs
        # its own truncation handling) must surface as output_truncated,
        # never be fed to the JSON parser and misclassified as
        # structured_output_invalid. This was a real bug: previously this
        # call happened unconditionally, so a still-truncated recovery
        # attempt's partial JSON was parsed anyway and reported as a schema
        # validation failure instead of the truncation it actually was.
        if stage == "convergence_analysis" and response.incomplete_reason is None:
            response = _finalize_convergence_response(response)
        return response
