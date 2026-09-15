from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Callable

from llm_deliberation.pricing import estimate_cost
from llm_deliberation.types import ModelResponse, Usage


class Provider(ABC):
    provider_name: str

    def __init__(self, model: str, max_output_tokens: int):
        self.model = model
        self.max_output_tokens = max_output_tokens

    @abstractmethod
    def generate(self, *, system: str, prompt: str) -> ModelResponse:
        raise NotImplementedError

    def _result(
        self, text: str, usage: Usage, *, incomplete_reason: str | None = None
    ) -> ModelResponse:
        return ModelResponse(
            provider=self.provider_name,
            model=self.model,
            text=text.strip(),
            usage=usage,
            estimated_cost_usd=estimate_cost(self.model, usage),
            requested_model=self.model,
            incomplete_reason=incomplete_reason,
        )


def _openai_incomplete_reason(response: object, text: str) -> str | None:
    """Classify an OpenAI Responses API result as empty/truncated/complete.

    Inspects the actual SDK response fields (openai 3.13.0,
    openai.types.responses.response.Response): `status` is one of
    'completed' | 'failed' | 'in_progress' | 'cancelled' | 'queued' |
    'incomplete', and `incomplete_details.reason` (only set when status ==
    'incomplete') is one of 'max_output_tokens' | 'max_messages' |
    'content_filter' | 'steered'. Any 'incomplete' status is bucketed as
    output_truncated -- for this app's workload (no tools, no multi-turn
    steering) 'max_output_tokens' is overwhelmingly the practical case, and
    the other reasons still describe a response that did not finish, so the
    same bucket is not misleading. A whitespace-only response is always
    empty_output, independent of status, since an empty artifact is unusable
    even when the provider considers the call "completed".
    """
    if not text.strip():
        return "empty_output"
    if getattr(response, "status", None) == "incomplete":
        return "output_truncated"
    return None


class OpenAIProvider(Provider):
    provider_name = "OpenAI"

    def __init__(
        self,
        model: str,
        max_output_tokens: int,
        effort: str = "high",
    ):
        super().__init__(model, max_output_tokens)
        self.effort = effort

    def generate(self, *, system: str, prompt: str) -> ModelResponse:
        from openai import OpenAI

        client = OpenAI()
        response = client.responses.create(
            model=self.model,
            instructions=system,
            input=prompt,
            reasoning={"effort": self.effort},
            max_output_tokens=self.max_output_tokens,
            store=False,
        )

        usage_obj = getattr(response, "usage", None)
        usage = Usage(
            input_tokens=int(getattr(usage_obj, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage_obj, "output_tokens", 0) or 0),
        )
        text = response.output_text or ""
        return self._result(text, usage, incomplete_reason=_openai_incomplete_reason(response, text))


_ANTHROPIC_TRUNCATION_STOP_REASONS = frozenset({"max_tokens", "model_context_window_exceeded"})


def _anthropic_incomplete_reason(message: object, text: str) -> str | None:
    """Classify an Anthropic Messages result (anthropic 1.5.0).

    `stop_reason` is one of 'end_turn' | 'max_tokens' | 'stop_sequence' |
    'tool_use' | 'pause_turn' | 'refusal' | 'model_context_window_exceeded'.
    Only the two length-related reasons count as truncation here: 'end_turn'
    and 'stop_sequence' are ordinary successful stops (never treated as
    errors, per this project's requirements); 'tool_use' does not occur in
    this app (no tools are ever offered to the model); 'refusal' and
    'pause_turn' produce genuine model content (a refusal message, or a
    resumable long-running turn) and are left as ordinary completions rather
    than invented failure categories outside this iteration's scope.
    """
    if not text.strip():
        return "empty_output"
    if getattr(message, "stop_reason", None) in _ANTHROPIC_TRUNCATION_STOP_REASONS:
        return "output_truncated"
    return None


class AnthropicProvider(Provider):
    provider_name = "Anthropic"

    def __init__(
        self,
        model: str,
        max_output_tokens: int,
        effort: str = "high",
    ):
        super().__init__(model, max_output_tokens)
        self.effort = effort

    def generate(self, *, system: str, prompt: str) -> ModelResponse:
        import anthropic

        client = anthropic.Anthropic()
        message = client.messages.create(
            model=self.model,
            max_tokens=self.max_output_tokens,
            system=system,
            output_config={"effort": self.effort},
            messages=[{"role": "user", "content": prompt}],
        )

        text = "\n".join(
            block.text for block in message.content
            if getattr(block, "type", None) == "text"
        )
        usage_obj = getattr(message, "usage", None)
        usage = Usage(
            input_tokens=int(getattr(usage_obj, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage_obj, "output_tokens", 0) or 0),
        )
        return self._result(
            text, usage, incomplete_reason=_anthropic_incomplete_reason(message, text)
        )


_GEMINI_TRUNCATION_STATUSES = frozenset({"incomplete", "budget_exceeded"})


def _gemini_incomplete_reason(interaction: object, text: str) -> str | None:
    """Classify a Gemini Interaction result (google-genai 2.23.0).

    `interaction.status` is one of 'in_progress' | 'requires_action' |
    'completed' | 'failed' | 'cancelled' | 'incomplete' | 'budget_exceeded' |
    'queued'. Only 'incomplete' (the SDK's generic not-finished status) and
    'budget_exceeded' (a token/resource ceiling was hit) indicate truncation;
    the ModelOutputStep type exposes no finer-grained per-step finish reason
    to distinguish safety-blocks from length limits. 'failed'/'cancelled'
    are not handled here: in practice the SDK raises an exception for those
    rather than returning them on a normal call, so classify_gemini_error's
    existing exception-based transient/non-transient split already covers
    that path; if the SDK ever did return one as a non-exception result, it
    would just look like ordinary content here -- no worse than before this
    change, since nothing was checked at all previously.
    """
    if not text.strip():
        return "empty_output"
    if getattr(interaction, "status", None) in _GEMINI_TRUNCATION_STATUSES:
        return "output_truncated"
    return None


class GeminiProvider(Provider):
    provider_name = "Google"

    def __init__(
        self,
        model: str,
        max_output_tokens: int,
        thinking_level: str = "high",
    ):
        super().__init__(model, max_output_tokens)
        self.thinking_level = thinking_level

    def generate(self, *, system: str, prompt: str) -> ModelResponse:
        from google import genai

        # v1 is GA; store=False avoids retaining the Interaction object
        # for server-side conversation state. retry_options is pinned to a
        # single attempt (no SDK-level retries) rather than left at the
        # library default: as of google-genai 2.23.0 the default is already
        # "no retries" (retry_args(None) -> stop_after_attempt(1)), but
        # pinning it explicitly means GeminiFallbackProvider's own bounded
        # retry-then-fallback policy is always the only retry logic in play,
        # even if the SDK's default ever changes.
        client = genai.Client(
            http_options={"api_version": "v1", "retry_options": {"attempts": 1}}
        )
        interaction = client.interactions.create(
            model=self.model,
            system_instruction=system,
            input=prompt,
            generation_config={
                "thinking_level": self.thinking_level,
                "max_output_tokens": self.max_output_tokens,
            },
            store=False,
        )

        usage_obj = getattr(interaction, "usage", None)
        usage = Usage(
            input_tokens=int(getattr(usage_obj, "total_input_tokens", 0) or 0),
            output_tokens=int(getattr(usage_obj, "total_output_tokens", 0) or 0)
            + int(getattr(usage_obj, "total_thought_tokens", 0) or 0),
        )
        text = interaction.output_text or ""
        return self._result(text, usage, incomplete_reason=_gemini_incomplete_reason(interaction, text))


class ProviderGenerationError(RuntimeError):
    """Raised when a provider call ultimately fails, with cost/provenance attached.

    Originally introduced for GeminiFallbackProvider (which model was
    requested, how many attempts were made, a full per-attempt log), but
    also reused by orchestrator._finalize_convergence_response for a
    convergence_analysis response that fails schema validation: any
    fallback-agnostic caller can pass fallback_used=False, fallback_reason=
    None, attempts=1, attempt_log=[] and still get the important property --
    an already-incurred cost is carried on the exception, not dropped, so
    the service layer can persist it instead of falling back to a
    flattened error string with no audit trail.
    """

    def __init__(
        self,
        message: str,
        *,
        requested_model: str,
        attempts: int,
        attempt_log: list[dict],
        fallback_used: bool,
        fallback_reason: str | None,
        estimated_cost_usd: float = 0.0,
        reason: str = "provider_error",
    ):
        super().__init__(message)
        self.requested_model = requested_model
        self.attempts = attempts
        self.attempt_log = attempt_log
        self.fallback_used = fallback_used
        self.fallback_reason = fallback_reason
        self.estimated_cost_usd = estimated_cost_usd
        # Typed failure classification for user-facing code (see store.py's
        # StageRecord.failure_reason) -- "empty_output" | "output_truncated" |
        # "structured_output_invalid" | "provider_error" (the default: a
        # transport/API/config failure, or anything else not classified more
        # specifically). Callers must never derive this by parsing `message`.
        self.reason = reason


def classify_gemini_error(exc: BaseException) -> tuple[bool, str]:
    """Classify a Gemini SDK exception as transient (fallback-eligible) or not.

    Returns (is_transient, human_reason).

    - google.genai.errors.ClientError (HTTP 4xx: bad/missing API key,
      billing, invalid request, unsupported parameters, malformed prompt)
      is a deterministic configuration problem. Never transient -- retrying
      or falling back would hide a real misconfiguration.
    - google.genai.errors.ServerError (HTTP 5xx: 500/502/503/504 and other
      server-side statuses) is provider-side and transient by definition.
    - Anything else (e.g. a network-level timeout that never became an HTTP
      response) is treated as transient too: it is not evidence of a
      deterministic client-side problem, so refusing to retry would just
      surface confusing infrastructure noise as if it were a config error.
    """
    from google.genai import errors as genai_errors

    if isinstance(exc, genai_errors.ClientError):
        return False, str(exc)
    if isinstance(exc, genai_errors.ServerError):
        code = getattr(exc, "code", "unknown")
        message = (getattr(exc, "message", None) or "").strip() or "no message"
        return True, f"HTTP {code} from Gemini ({message})"
    return True, f"unexpected error contacting Gemini: {exc}"


def _cost_of_failed_attempt(exc: BaseException, model: str) -> float:
    """Best-effort cost of a failed attempt, when usage info is exposed.

    In practice the Gemini SDK does not attach token usage to error
    responses -- a failed call has no response body to report usage from.
    This exists so that if a future error shape *does* expose partial
    usage (e.g. billed input tokens on a request that failed mid-generation),
    it is added to the stage's cost rather than silently dropped, per this
    project's auditability requirements. Returns 0.0 whenever no usage
    metadata is present, which is the common case today.
    """
    details = getattr(exc, "details", None)
    if not isinstance(details, dict):
        return 0.0
    usage_meta = details.get("usageMetadata")
    if not isinstance(usage_meta, dict):
        return 0.0
    usage = Usage(
        input_tokens=int(usage_meta.get("promptTokenCount", 0) or 0),
        output_tokens=int(usage_meta.get("candidatesTokenCount", 0) or 0),
    )
    return estimate_cost(model, usage)


@dataclass(slots=True)
class _Attempt:
    model: str
    attempt_number: int
    outcome: str  # "succeeded" | "failed"
    delay_before_seconds: float = 0.0
    http_code: int | None = None
    error: str | None = None
    reason: str | None = None
    estimated_cost_usd: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT_GEMINI_RETRY_DELAYS: tuple[float, ...] = (2.0, 5.0, 10.0)

# Wall-clock ceiling for the *whole* chain attempt (every model, every retry),
# not just a per-model retry-count bound. A real run observed 2+ hours end to
# end with red-team/Gemini fallback in the loop: the existing per-model retry
# count is bounded, but nothing previously bounded how long the *individual
# provider calls themselves* could take, so a slow-but-not-erroring provider
# (or several attempts that are each individually slow) had no overall
# ceiling. 300s is sized against this policy's own worst case: 3 models x
# (4 attempts each + up to ~17s of jittered sleep) is already close to this
# budget if each call takes roughly 20s, which is a reasonable upper bound
# for a single "high effort" text generation call -- so one ordinary
# transient error/retry sequence should still complete comfortably inside
# the deadline, while a genuinely stuck/slow provider is bounded rather than
# left to run for hours. Checked before starting each new attempt (not via
# preemptive cancellation of an in-flight call, which the provider SDKs do
# not support here), so the true worst case is this deadline plus the
# duration of whichever single attempt was already in flight when it was
# last checked -- an accepted, deliberate soft bound.
DEFAULT_GEMINI_DEADLINE_SECONDS: float = 300.0


class GeminiFallbackProvider(Provider):
    """Gemini provider with an ordered model fallback chain.

    Tries `models[0]` (the operator's preferred/newest model) first, with a
    small bounded retry-with-backoff policy applied to *each* model in the
    chain before moving to the next one. Only HTTP 5xx / provider-side
    failures (see `classify_gemini_error`) trigger a retry or fallback --
    auth, billing, and other deterministic 4xx client errors raise
    immediately with no fallback, so real configuration problems are never
    hidden. The retry budget is bounded (a handful of attempts per model,
    exponential backoff with jitter), so a fully-down provider fails within
    a predictable amount of time rather than looping forever.

    `mode="chain"` (default) walks the whole configured chain.
    `mode="preferred_only"` tries only `models[0]`, still with its own
    bounded retries -- used by the "Retry preferred model" UI action so a
    user can explicitly ask for just the newest model without immediately
    accepting a fallback.
    """

    provider_name = "Google"

    def __init__(
        self,
        models: list[str],
        max_output_tokens: int,
        thinking_level: str = "high",
        retry_delays: tuple[float, ...] = DEFAULT_GEMINI_RETRY_DELAYS,
        sleep_fn: Callable[[float], None] = time.sleep,
        jitter_fn: Callable[[], float] = random.random,
        provider_factory: Callable[[str], Provider] | None = None,
        deadline_seconds: float = DEFAULT_GEMINI_DEADLINE_SECONDS,
        clock_fn: Callable[[], float] = time.monotonic,
    ):
        if not models:
            raise ValueError("GeminiFallbackProvider requires at least one model")
        super().__init__(models[0], max_output_tokens)
        self.models = list(models)
        self.thinking_level = thinking_level
        self.retry_delays = tuple(retry_delays)
        self._sleep = sleep_fn
        self._jitter = jitter_fn
        self.deadline_seconds = deadline_seconds
        self._clock = clock_fn
        factory = provider_factory or (
            lambda m: GeminiProvider(m, max_output_tokens, thinking_level=thinking_level)
        )
        self._providers = {m: factory(m) for m in self.models}

    def _jittered_delay(self, base: float) -> float:
        # Full-ish jitter in [0.5x, 1.5x) the base delay so simultaneous
        # callers hitting the same overloaded model don't retry in lockstep.
        return base * (0.5 + self._jitter())

    def generate(self, *, system: str, prompt: str, mode: str = "chain") -> ModelResponse:
        if mode not in ("chain", "preferred_only"):
            raise ValueError(f"Unknown Gemini fallback mode: {mode!r}")
        chain = self.models if mode == "chain" else self.models[:1]

        attempt_log: list[_Attempt] = []
        failed_cost = 0.0
        last_reason: str | None = None
        last_incomplete_reason: str | None = None
        start = self._clock()

        def deadline_exceeded() -> bool:
            return (self._clock() - start) >= self.deadline_seconds

        for model in chain:
            provider = self._providers[model]
            for retry_index in range(len(self.retry_delays) + 1):
                if deadline_exceeded():
                    raise ProviderGenerationError(
                        f"Gemini red-team wall-clock budget of {self.deadline_seconds:.0f}s "
                        f"was exceeded before all attempts/models were tried "
                        f"({len(attempt_log)} attempt(s) so far).",
                        requested_model=self.models[0],
                        attempts=len(attempt_log),
                        attempt_log=[a.to_dict() for a in attempt_log],
                        fallback_used=len(attempt_log) > 0
                        and attempt_log[-1].model != self.models[0],
                        fallback_reason=last_reason,
                        estimated_cost_usd=failed_cost,
                        reason=last_incomplete_reason or "provider_error",
                    )

                delay_before = 0.0
                if retry_index > 0:
                    delay_before = self._jittered_delay(self.retry_delays[retry_index - 1])
                    self._sleep(delay_before)

                attempt_number = len(attempt_log) + 1
                try:
                    response = provider.generate(system=system, prompt=prompt)
                except Exception as exc:
                    is_transient, reason = classify_gemini_error(exc)
                    attempt_cost = _cost_of_failed_attempt(exc, model)
                    failed_cost += attempt_cost
                    last_reason = reason
                    attempt_log.append(
                        _Attempt(
                            model=model,
                            attempt_number=attempt_number,
                            outcome="failed",
                            delay_before_seconds=round(delay_before, 3),
                            http_code=getattr(exc, "code", None),
                            error=str(exc),
                            reason=reason,
                            estimated_cost_usd=attempt_cost,
                        )
                    )
                    if not is_transient:
                        raise ProviderGenerationError(
                            f"Gemini generation failed for model '{model}' with a "
                            f"non-transient error (no retry, no fallback attempted): {exc}",
                            requested_model=self.models[0],
                            attempts=attempt_number,
                            attempt_log=[a.to_dict() for a in attempt_log],
                            fallback_used=False,
                            fallback_reason=None,
                            estimated_cost_usd=failed_cost,
                        ) from exc
                    if retry_index < len(self.retry_delays):
                        continue  # retry the same model after backoff
                    break  # exhausted retries for this model; advance the chain
                else:
                    if response.incomplete_reason is not None:
                        # The call itself succeeded, but the artifact is empty
                        # or was truncated -- treated exactly like a transient
                        # failure of this attempt: same bounded retry/fallback
                        # budget, cost never dropped. See providers.py module
                        # docstring / Section 1-3 of the reliability pass.
                        last_incomplete_reason = response.incomplete_reason
                        reason = f"{model} returned {response.incomplete_reason.replace('_', ' ')}"
                        last_reason = reason
                        failed_cost += response.estimated_cost_usd
                        attempt_log.append(
                            _Attempt(
                                model=model,
                                attempt_number=attempt_number,
                                outcome="failed",
                                delay_before_seconds=round(delay_before, 3),
                                reason=reason,
                                estimated_cost_usd=response.estimated_cost_usd,
                            )
                        )
                        if retry_index < len(self.retry_delays):
                            continue  # retry the same model after backoff
                        break  # exhausted retries for this model; advance the chain

                    attempt_log.append(
                        _Attempt(
                            model=model,
                            attempt_number=attempt_number,
                            outcome="succeeded",
                            delay_before_seconds=round(delay_before, 3),
                            estimated_cost_usd=response.estimated_cost_usd,
                        )
                    )
                    fallback_used = model != self.models[0]
                    response.requested_model = self.models[0]
                    response.fallback_used = fallback_used
                    response.fallback_reason = last_reason if fallback_used else None
                    response.model_attempts = attempt_number
                    response.attempt_log = [a.to_dict() for a in attempt_log]
                    response.estimated_cost_usd = failed_cost + response.estimated_cost_usd
                    return response

        raise ProviderGenerationError(
            f"All configured Gemini models failed transiently: {', '.join(chain)}.",
            requested_model=self.models[0],
            attempts=len(attempt_log),
            attempt_log=[a.to_dict() for a in attempt_log],
            fallback_used=len(chain) > 1,
            fallback_reason=last_reason,
            estimated_cost_usd=failed_cost,
            reason=last_incomplete_reason or "provider_error",
        )
