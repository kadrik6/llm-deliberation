"""Provider readiness preflight.

Three distinct things, kept separate on purpose (see the task's design
report / docs/architecture.md's "Known scope limits" for the full framing):

A. Configuration validity -- is an API key present at all, syntactically.
   Checked locally, no network call, `check_type="config_missing"` when it
   fails.
B. Provider readiness -- does the configured credential/model actually work
   against the provider's own API right now. Checked here via the cheapest
   non-generation call each SDK exposes (`models.retrieve`/`models.get`),
   which is free (no inference, no token cost) on all three providers as of
   openai 3.13.0 / anthropic 1.5.0 / google-genai 2.23.0 -- see
   providers.classify_{openai,anthropic,gemini}_readiness_error's
   docstrings for exactly what each call can and cannot detect (notably:
   none of them are guaranteed to surface a billing/quota rejection, since
   that class of error is not guaranteed to be checked on a non-generation
   endpoint by any of the three providers).
C. Runtime health -- NOT this module's concern. A later real generation call
   can still fail (overload, timeout, rate limit, a transient 5xx) even
   after this check passes "ready". `ProviderReadiness.user_message` never
   claims otherwise -- see READY_DISCLAIMER below, always shown next to a
   "ready" result in the UI (web/i18n.py's readiness_disclaimer).

Caching: in-memory only, no new SQLite table (see the task's design report,
Section 4 -- a persisted-but-possibly-stale "ready" would be actively
misleading, and there's no auditability need beyond what a failed run's own
stage error already records). A 10-minute TTL by default
(READINESS_CACHE_TTL_SECONDS env override), keyed by
(provider, model, credential fingerprint) -- never the raw API key, only a
truncated SHA-256 of it, so a changed key naturally invalidates any cached
result without this module ever storing or logging the key itself.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from llm_deliberation.config import Settings
from llm_deliberation.providers import (
    READINESS_STATUSES,
    classify_anthropic_readiness_error,
    classify_gemini_readiness_error,
    classify_openai_readiness_error,
)

DEFAULT_READINESS_CACHE_TTL_SECONDS = 600.0

READY_DISCLAIMER = (
    "Provider readiness check passed. This does not guarantee every later "
    "request will succeed -- a real generation call can still fail from "
    "overload, a timeout, or a transient provider error."
)


def _cache_ttl_seconds() -> float:
    raw = os.getenv("READINESS_CACHE_TTL_SECONDS")
    if not raw:
        return DEFAULT_READINESS_CACHE_TTL_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_READINESS_CACHE_TTL_SECONDS


def _credential_fingerprint(env_var: str) -> str:
    """Non-reversible, truncated fingerprint of an API key's current value --
    part of the cache key only, never logged or stored anywhere else. Returns
    the stable placeholder "unset" when the variable isn't set, so "no key"
    and "some specific key" are always distinct cache entries.
    """
    value = os.getenv(env_var)
    if not value:
        return "unset"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class ProviderReadiness:
    provider: str  # "openai" | "anthropic" | "gemini"
    configured_model: str
    status: str  # one of providers.READINESS_STATUSES
    checked_at: str  # UTC ISO 8601
    check_type: str  # "config_missing" | "model_retrieve" | "model_get"
    paid_probe: bool
    estimated_probe_cost_usd: float | None
    user_message: str
    # Raw diagnostic (repr of the underlying exception, if any) -- for
    # trace/debug display only, never shown as the primary user-facing
    # message and never containing the API key itself (SDK exceptions do
    # not include it in their string representation).
    technical_detail: str | None = None

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def __post_init__(self) -> None:
        if self.status not in READINESS_STATUSES:
            raise ValueError(f"Unknown readiness status: {self.status!r}")


class _ReadinessCache:
    def __init__(self) -> None:
        self._store: dict[tuple[str, str, str], tuple[float, ProviderReadiness]] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple[str, str, str]) -> ProviderReadiness | None:
        with self._lock:
            entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, result = entry
        if time.monotonic() >= expires_at:
            return None
        return result

    def set(self, key: tuple[str, str, str], result: ProviderReadiness) -> None:
        expires_at = time.monotonic() + _cache_ttl_seconds()
        with self._lock:
            self._store[key] = (expires_at, result)

    def invalidate_all(self) -> None:
        with self._lock:
            self._store.clear()


_CACHE = _ReadinessCache()


def invalidate_readiness_cache() -> None:
    """Drop every cached readiness result. Called for "Check again", and by
    service.py after a real run hits a provider-side error, per the task
    brief's cache-invalidation rules ("previous real run gets a provider
    auth/billing/model-access error"). Coarser than invalidating one
    provider's entry (the whole cache is cleared, not just the affected
    provider/model) -- a deliberate simplification: cross-provider precision
    would need threading provider/model identity through every call site
    that can raise a provider error, for a cache that already expires within
    10 minutes on its own.
    """
    _CACHE.invalidate_all()


def _missing_key_result(provider: str, model: str, env_var: str) -> ProviderReadiness:
    return ProviderReadiness(
        provider=provider,
        configured_model=model,
        status="unavailable",
        checked_at=_now_iso(),
        check_type="config_missing",
        paid_probe=False,
        estimated_probe_cost_usd=0.0,
        user_message=f"{env_var} is not set. Add it to .env before starting a run.",
        technical_detail=None,
    )


def check_openai_readiness(model: str, *, force: bool = False) -> ProviderReadiness:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return _missing_key_result("openai", model, "OPENAI_API_KEY")

    cache_key = ("openai", model, _credential_fingerprint("OPENAI_API_KEY"))
    if not force:
        cached = _CACHE.get(cache_key)
        if cached is not None:
            return cached

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, timeout=15.0, max_retries=0)
        client.models.retrieve(model)
    except Exception as exc:
        status, message = classify_openai_readiness_error(exc)
        result = ProviderReadiness(
            provider="openai",
            configured_model=model,
            status=status,
            checked_at=_now_iso(),
            check_type="model_retrieve",
            paid_probe=False,
            estimated_probe_cost_usd=0.0,
            user_message=message,
            technical_detail=repr(exc),
        )
    else:
        result = ProviderReadiness(
            provider="openai",
            configured_model=model,
            status="ready",
            checked_at=_now_iso(),
            check_type="model_retrieve",
            paid_probe=False,
            estimated_probe_cost_usd=0.0,
            user_message=READY_DISCLAIMER,
        )
    _CACHE.set(cache_key, result)
    return result


def check_anthropic_readiness(model: str, *, force: bool = False) -> ProviderReadiness:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return _missing_key_result("anthropic", model, "ANTHROPIC_API_KEY")

    cache_key = ("anthropic", model, _credential_fingerprint("ANTHROPIC_API_KEY"))
    if not force:
        cached = _CACHE.get(cache_key)
        if cached is not None:
            return cached

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key, timeout=15.0, max_retries=0)
        client.models.retrieve(model_id=model)
    except Exception as exc:
        status, message = classify_anthropic_readiness_error(exc)
        result = ProviderReadiness(
            provider="anthropic",
            configured_model=model,
            status=status,
            checked_at=_now_iso(),
            check_type="model_retrieve",
            paid_probe=False,
            estimated_probe_cost_usd=0.0,
            user_message=message,
            technical_detail=repr(exc),
        )
    else:
        result = ProviderReadiness(
            provider="anthropic",
            configured_model=model,
            status="ready",
            checked_at=_now_iso(),
            check_type="model_retrieve",
            paid_probe=False,
            estimated_probe_cost_usd=0.0,
            user_message=READY_DISCLAIMER,
        )
    _CACHE.set(cache_key, result)
    return result


def check_gemini_readiness(model: str, *, force: bool = False) -> ProviderReadiness:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return _missing_key_result("gemini", model, "GEMINI_API_KEY")

    cache_key = ("gemini", model, _credential_fingerprint("GEMINI_API_KEY"))
    if not force:
        cached = _CACHE.get(cache_key)
        if cached is not None:
            return cached

    try:
        from google import genai

        client = genai.Client(
            api_key=api_key, http_options={"api_version": "v1", "timeout": 15_000}
        )
        client.models.get(model=model)
    except Exception as exc:
        status, message = classify_gemini_readiness_error(exc)
        result = ProviderReadiness(
            provider="gemini",
            configured_model=model,
            status=status,
            checked_at=_now_iso(),
            check_type="model_get",
            paid_probe=False,
            estimated_probe_cost_usd=0.0,
            user_message=message,
            technical_detail=repr(exc),
        )
    else:
        result = ProviderReadiness(
            provider="gemini",
            configured_model=model,
            status="ready",
            checked_at=_now_iso(),
            check_type="model_get",
            paid_probe=False,
            estimated_probe_cost_usd=0.0,
            user_message=READY_DISCLAIMER,
        )
    _CACHE.set(cache_key, result)
    return result


@dataclass(slots=True)
class ReadinessReport:
    """Every readiness result relevant to one run configuration. Gemini is
    None entirely (not "checked and failed") when red-team is disabled --
    see Section 3 of the task brief: "If red-team is disabled in the run
    configuration: do not perform a Gemini readiness check."
    """

    openai: ProviderReadiness
    anthropic: ProviderReadiness
    gemini: ProviderReadiness | None

    @property
    def required_ready(self) -> bool:
        return self.openai.ready and self.anthropic.ready

    @property
    def required_failures(self) -> list[ProviderReadiness]:
        return [r for r in (self.openai, self.anthropic) if not r.ready]

    @property
    def gemini_blocked(self) -> bool:
        return self.gemini is not None and not self.gemini.ready


def check_run_readiness(
    settings: Settings, *, red_team_enabled: bool, force: bool = False
) -> ReadinessReport:
    """Check every provider `settings` would actually use for a run with
    `red_team_enabled`. OpenAI and Anthropic are always required (this
    project's fixed two-model core, see docs/architecture.md); Gemini is
    only checked when red-team is actually on for this run -- a run that
    disabled it never needs GEMINI_API_KEY, and must not be blocked as if
    it did.
    """
    openai_result = check_openai_readiness(settings.openai_model, force=force)
    anthropic_result = check_anthropic_readiness(settings.anthropic_model, force=force)
    gemini_result = (
        check_gemini_readiness(settings.gemini_model, force=force) if red_team_enabled else None
    )
    return ReadinessReport(openai=openai_result, anthropic=anthropic_result, gemini=gemini_result)
