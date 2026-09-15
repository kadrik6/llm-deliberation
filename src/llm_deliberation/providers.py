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

    def _result(self, text: str, usage: Usage) -> ModelResponse:
        return ModelResponse(
            provider=self.provider_name,
            model=self.model,
            text=text.strip(),
            usage=usage,
            estimated_cost_usd=estimate_cost(self.model, usage),
            requested_model=self.model,
        )


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
        return self._result(response.output_text or "", usage)


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
        return self._result(text, usage)


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
        return self._result(interaction.output_text or "", usage)


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
    ):
        super().__init__(message)
        self.requested_model = requested_model
        self.attempts = attempts
        self.attempt_log = attempt_log
        self.fallback_used = fallback_used
        self.fallback_reason = fallback_reason
        self.estimated_cost_usd = estimated_cost_usd


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
    ):
        if not models:
            raise ValueError("GeminiFallbackProvider requires at least one model")
        super().__init__(models[0], max_output_tokens)
        self.models = list(models)
        self.thinking_level = thinking_level
        self.retry_delays = tuple(retry_delays)
        self._sleep = sleep_fn
        self._jitter = jitter_fn
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

        for model in chain:
            provider = self._providers[model]
            for retry_index in range(len(self.retry_delays) + 1):
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
        )
