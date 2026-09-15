from __future__ import annotations

import asyncio

from llm_deliberation import convergence, prompts
from llm_deliberation.config import Settings
from llm_deliberation.providers import (
    AnthropicProvider,
    GeminiFallbackProvider,
    OpenAIProvider,
    Provider,
    ProviderGenerationError,
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
        )
    if settings.convergence_provider == "gemini":
        return GeminiFallbackProvider(
            models=[settings.convergence_model, *settings.gemini_fallback_models],
            max_output_tokens=settings.max_output_tokens,
            thinking_level=settings.gemini_thinking_level,
            timeout_seconds=settings.provider_timeout_seconds,
            max_retries=settings.provider_max_retries,
        )
    raise ValueError(f"Unknown convergence_provider: {settings.convergence_provider!r}")


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
    """
    try:
        analysis = convergence.parse_convergence_analysis(response.text)
    except convergence.ConvergenceParseError as exc:
        raise ProviderGenerationError(
            f"convergence_analysis response failed validation: {exc}",
            requested_model=response.requested_model or response.model,
            attempts=1,
            attempt_log=[],
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
        )
        self.convergence = _build_convergence_provider(settings)

    async def run_stage(
        self,
        stage: str,
        question: str,
        texts: dict[str, str],
        *,
        gemini_mode: str = "chain",
        language: str = "en",
    ) -> ModelResponse:
        provider = getattr(self, STAGE_PROVIDER[stage])
        prompt = _build_prompt(stage, question, texts)
        # gemini_mode only means anything to GeminiFallbackProvider (used by
        # the red_team stage's "Retry preferred model" vs "Retry with
        # fallback chain" UI actions); every other provider ignores it.
        kwargs = {"mode": gemini_mode} if stage == "red_team" else {}
        # language only affects the shared system prompt (see
        # prompts.base_system) -- the user's original question is always
        # passed through unchanged, never translated (see _build_prompt).
        system = prompts.base_system(language)
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
            recovery_system = system + "\n\n" + prompts.truncation_recovery_instruction(language)
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

        if stage == "convergence_analysis":
            response = _finalize_convergence_response(response)
        return response
