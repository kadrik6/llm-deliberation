"""Offline regression tests for the fallback-aware red-team wall-clock
timeout policy (see providers.GeminiFallbackProvider.generate's docstring
for the allocation algorithm this protects, and docs/decisions for the
real-incident background: a preferred-model timeout consuming the entire
300s budget before either configured fallback model got a single attempt).

No network calls: a deterministic fake clock (a shared mutable dict, exactly
the pattern already established in test_gemini_fallback.py) drives all
timing, and sleep_fn is stubbed to a no-op so backoff delays never actually
elapse. Numbered comments correspond to the task brief's Section 12
checklist.
"""

from __future__ import annotations

import httpx
import pytest
from google.genai._gaos.lib import compat_errors as gaos_errors

from llm_deliberation.providers import GeminiFallbackProvider, ProviderGenerationError
from llm_deliberation.types import ModelResponse, Usage


def _gaos_response(status_code: int) -> httpx.Response:
    request = httpx.Request("POST", "https://example.invalid/v1/interactions")
    return httpx.Response(status_code=status_code, request=request, json={"error": {"message": "test"}})


def timeout_error() -> gaos_errors.APITimeoutError:
    request = httpx.Request("POST", "https://example.invalid/v1/interactions")
    return gaos_errors.APITimeoutError(request)


def auth_error() -> gaos_errors.AuthenticationError:
    return gaos_errors.AuthenticationError("invalid key", response=_gaos_response(401), body=None)


def make_response(model: str, cost: float = 0.01) -> ModelResponse:
    return ModelResponse(
        provider="Google", model=model, text=f"red-team output from {model}",
        usage=Usage(input_tokens=100, output_tokens=200), estimated_cost_usd=cost,
        requested_model=model,
    )


class FakeClock:
    """A shared, deterministic wall-clock -- advanced only when a
    TimedProvider's .generate() is actually called (simulating "this call
    took N seconds"), never by real sleeping (sleep_fn is always stubbed to
    a no-op in these tests)."""

    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class TimedProvider:
    """Stands in for a real GeminiProvider bound to one model. Each queued
    behavior is (simulated_seconds, outcome), where outcome is either an
    Exception instance or a ModelResponse. Advances the shared FakeClock by
    `simulated_seconds` on every call, whether it succeeds or raises --
    modeling "this attempt took N seconds of wall-clock time before
    resolving," the same assumption test_gemini_fallback.py's
    SlowFailingProvider already uses.
    """

    def __init__(self, model: str, clock: FakeClock, behaviors: list[tuple[float, object]]):
        self.model = model
        self._clock = clock
        self._behaviors = list(behaviors)
        self.calls = 0
        self.timeouts_seen: list[float | None] = []

    def generate(self, *, system: str, prompt: str, timeout_seconds: float | None = None) -> ModelResponse:
        self.calls += 1
        self.timeouts_seen.append(timeout_seconds)
        if not self._behaviors:
            raise AssertionError(f"{self.model} called more times than expected")
        seconds, outcome = self._behaviors.pop(0)
        self._clock.advance(seconds)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def build_provider(fakes: dict[str, TimedProvider], clock: FakeClock, **kwargs) -> GeminiFallbackProvider:
    return GeminiFallbackProvider(
        models=list(fakes.keys()),
        max_output_tokens=1000,
        sleep_fn=lambda s: None,
        jitter_fn=lambda: 0.5,  # deterministic: _jittered_delay -> base * 1.0
        provider_factory=lambda m: fakes[m],
        clock_fn=clock,
        deadline_seconds=kwargs.pop("deadline_seconds", 300.0),
        timeout_seconds=kwargs.pop("timeout_seconds", 180.0),
        **kwargs,
    )


# -- 1/2/9: preferred model cannot consume the whole budget; fallback runs --


def test_1_2_9_preferred_model_timeout_leaves_room_for_fallback_to_succeed():
    """Reproduces the reported incident's shape at 1/10th scale (30s budget,
    3s timeouts) so it runs instantly: the preferred model times out twice
    (its own allocated share), then the fallback gets a real attempt and
    succeeds -- the preferred model must NOT have been able to consume the
    whole 30s budget by itself.
    """
    clock = FakeClock()
    fakes = {
        "gemini-3.8-flash": TimedProvider(
            "gemini-3.8-flash", clock,
            [(3.0, timeout_error()), (3.0, timeout_error()), (3.0, timeout_error()), (3.0, timeout_error())],
        ),
        "gemini-3.7-flash": TimedProvider(
            "gemini-3.7-flash", clock, [(1.0, make_response("gemini-3.7-flash"))]
        ),
    }
    provider = build_provider(fakes, clock, deadline_seconds=30.0, timeout_seconds=180.0)

    response = provider.generate(system="sys", prompt="p")

    assert response.model == "gemini-3.7-flash"
    assert response.fallback_used is True
    assert fakes["gemini-3.7-flash"].calls >= 1  # fallback genuinely got a chance
    # The preferred model did not get to use the full 180s configured
    # timeout on every attempt -- its allocation was capped well below that.
    assert all(t < 180.0 for t in fakes["gemini-3.8-flash"].timeouts_seen)


# -- 3/4: effective timeout capped by remaining total / current-model allocation --


def test_3_effective_timeout_capped_by_remaining_total_budget():
    clock = FakeClock()
    fakes = {
        "gemini-3.8-flash": TimedProvider(
            "gemini-3.8-flash", clock, [(70.0, timeout_error()), (1.0, make_response("gemini-3.8-flash"))]
        ),
    }
    # Single-model chain: remaining_model_budget == remaining_total_budget,
    # isolating the "capped by remaining total" behavior specifically. 30s
    # remain after the first attempt (100 - 70), comfortably above
    # min_request_timeout_seconds so a second attempt is still sent.
    provider = build_provider(fakes, clock, deadline_seconds=100.0, timeout_seconds=180.0)

    provider.generate(system="sys", prompt="p")

    first_timeout, second_timeout = fakes["gemini-3.8-flash"].timeouts_seen
    assert first_timeout == pytest.approx(100.0, abs=0.01)  # capped by total budget, not the 180s ceiling
    assert second_timeout < 30.0  # less than what remained before backoff, capped by remaining total
    assert second_timeout > 15.0  # still above the minimum-meaningful floor


def test_4_effective_timeout_capped_by_current_model_allocation():
    clock = FakeClock()
    fakes = {
        "gemini-3.8-flash": TimedProvider("gemini-3.8-flash", clock, [(0.5, make_response("gemini-3.8-flash"))]),
        "gemini-3.7-flash": TimedProvider("gemini-3.7-flash", clock, []),
    }
    # Two models, ample total budget (300s) -- the *model* allocation
    # (300/2 = 150s), not the total, is the binding constraint here.
    provider = build_provider(fakes, clock, deadline_seconds=300.0, timeout_seconds=180.0)

    provider.generate(system="sys", prompt="p")

    assert fakes["gemini-3.8-flash"].timeouts_seen[0] == pytest.approx(150.0, abs=0.01)


# -- 5: request not sent if too little meaningful time remains --------------


def test_5_request_not_sent_when_remaining_time_below_minimum_meaningful():
    clock = FakeClock()
    fakes = {
        "gemini-3.8-flash": TimedProvider(
            "gemini-3.8-flash", clock, [(28.0, timeout_error())]
        ),
        "gemini-3.7-flash": TimedProvider("gemini-3.7-flash", clock, []),
    }
    provider = build_provider(
        fakes, clock, deadline_seconds=30.0, timeout_seconds=180.0, min_request_timeout_seconds=15.0,
    )

    with pytest.raises(ProviderGenerationError) as excinfo:
        provider.generate(system="sys", prompt="p")

    # Only 2s remained after the first (28s) attempt -- well below the 15s
    # minimum -- so no second attempt on gemini-3.8-flash, and the fallback
    # never got a call either (no time left at all for it).
    assert fakes["gemini-3.8-flash"].calls == 1
    assert fakes["gemini-3.7-flash"].calls == 0
    assert excinfo.value.reason == "red_team_budget_exhausted"


# -- 6: transient timeout triggers bounded fallback progression -------------


def test_6_timeout_is_treated_as_transient_and_progresses_to_fallback():
    clock = FakeClock()
    fakes = {
        "gemini-3.8-flash": TimedProvider(
            "gemini-3.8-flash", clock, [(1.0, timeout_error())] * 4  # exhausts its own retries
        ),
        "gemini-3.7-flash": TimedProvider(
            "gemini-3.7-flash", clock, [(1.0, make_response("gemini-3.7-flash"))]
        ),
    }
    provider = build_provider(fakes, clock, deadline_seconds=300.0)

    response = provider.generate(system="sys", prompt="p")

    assert response.model == "gemini-3.7-flash"
    assert fakes["gemini-3.8-flash"].calls == 4  # bounded, not unbounded


# -- 7: deterministic auth/config failure does not waste retries ------------


def test_7_deterministic_auth_failure_does_not_retry_or_fall_back():
    clock = FakeClock()
    fakes = {
        "gemini-3.8-flash": TimedProvider("gemini-3.8-flash", clock, [(0.2, auth_error())]),
        "gemini-3.7-flash": TimedProvider("gemini-3.7-flash", clock, []),
    }
    provider = build_provider(fakes, clock, deadline_seconds=300.0)

    with pytest.raises(ProviderGenerationError) as excinfo:
        provider.generate(system="sys", prompt="p")

    assert fakes["gemini-3.8-flash"].calls == 1  # no retry
    assert fakes["gemini-3.7-flash"].calls == 0  # no fallback
    assert excinfo.value.fallback_used is False


# -- 8: all attempts stay within the total wall-clock budget ----------------


def test_8_no_attempt_starts_with_an_effective_timeout_exceeding_remaining_budget():
    clock = FakeClock()
    fakes = {
        "gemini-3.8-flash": TimedProvider(
            "gemini-3.8-flash", clock, [(1.0, timeout_error())] * 4
        ),
        "gemini-3.7-flash": TimedProvider(
            "gemini-3.7-flash", clock, [(1.0, timeout_error())] * 4
        ),
        "gemini-3.6-flash": TimedProvider(
            "gemini-3.6-flash", clock, [(1.0, make_response("gemini-3.6-flash"))]
        ),
    }
    deadline = 300.0
    provider = build_provider(fakes, clock, deadline_seconds=deadline)

    start_time = clock.t
    response = provider.generate(system="sys", prompt="p")

    assert response.model == "gemini-3.6-flash"
    assert (clock.t - start_time) <= deadline
    for fake in fakes.values():
        for seen in fake.timeouts_seen:
            assert seen <= 180.0 + 1e-9  # never exceeds the configured ceiling either


# -- 10: third fallback reached when earlier models fail quickly ------------


def test_10_third_fallback_reached_when_earlier_models_fail_fast():
    clock = FakeClock()
    fakes = {
        "gemini-3.8-flash": TimedProvider("gemini-3.8-flash", clock, [(0.1, timeout_error())] * 4),
        "gemini-3.7-flash": TimedProvider("gemini-3.7-flash", clock, [(0.1, timeout_error())] * 4),
        "gemini-3.6-flash": TimedProvider("gemini-3.6-flash", clock, [(0.1, make_response("gemini-3.6-flash"))]),
    }
    provider = build_provider(fakes, clock, deadline_seconds=300.0)

    response = provider.generate(system="sys", prompt="p")

    assert response.model == "gemini-3.6-flash"
    assert fakes["gemini-3.6-flash"].calls == 1


# -- 11/12: global deadline exhaustion stops further calls, records truthfully --


def test_11_12_global_deadline_exhaustion_stops_calls_and_records_not_attempted():
    clock = FakeClock()
    fakes = {
        # A single attempt that alone consumes the *entire* 300s global
        # budget (not just this model's own ~100s allocation in a 3-model
        # chain) -- exercises the outer "deadline already gone" branch,
        # which marks every remaining model in the chain not_attempted in
        # one pass, rather than the inner per-model branch exercised by
        # test_5/test_14.
        "gemini-3.8-flash": TimedProvider("gemini-3.8-flash", clock, [(305.0, timeout_error())]),
        "gemini-3.7-flash": TimedProvider("gemini-3.7-flash", clock, []),
        "gemini-3.6-flash": TimedProvider("gemini-3.6-flash", clock, []),
    }
    provider = build_provider(fakes, clock, deadline_seconds=300.0)

    with pytest.raises(ProviderGenerationError) as excinfo:
        provider.generate(system="sys", prompt="p")

    assert fakes["gemini-3.7-flash"].calls == 0
    assert fakes["gemini-3.6-flash"].calls == 0
    not_attempted_entries = [a for a in excinfo.value.attempt_log if a["outcome"] == "not_attempted"]
    not_attempted_models = {a["model"] for a in not_attempted_entries}
    # Both never-called fallbacks are recorded truthfully as not attempted,
    # not silently dropped and not shown as "failed".
    assert not_attempted_models == {"gemini-3.7-flash", "gemini-3.6-flash"}
    assert all(a["estimated_cost_usd"] == 0.0 for a in not_attempted_entries)
    failed_entries = [a for a in excinfo.value.attempt_log if a["outcome"] == "failed"]
    assert {"gemini-3.7-flash", "gemini-3.6-flash"}.isdisjoint({a["model"] for a in failed_entries})


# -- 13/14: cost accounting ---------------------------------------------------


def test_13_cost_includes_every_actual_paid_attempt_exactly():
    clock = FakeClock()
    fakes = {
        "gemini-3.8-flash": TimedProvider(
            "gemini-3.8-flash", clock,
            [(0.1, timeout_error())] * 3 + [(0.1, make_response("gemini-3.8-flash", cost=0.02))],
        ),
    }
    provider = build_provider(fakes, clock, deadline_seconds=300.0)

    response = provider.generate(system="sys", prompt="p")

    assert response.estimated_cost_usd == pytest.approx(0.02)  # failed attempts had no usage-exposed cost
    assert response.model_attempts == 4


def test_14_not_attempted_fallback_adds_zero_cost():
    clock = FakeClock()
    fakes = {
        "gemini-3.8-flash": TimedProvider("gemini-3.8-flash", clock, [(28.0, timeout_error())]),
        "gemini-3.7-flash": TimedProvider("gemini-3.7-flash", clock, []),
    }
    provider = build_provider(fakes, clock, deadline_seconds=30.0, min_request_timeout_seconds=15.0)

    with pytest.raises(ProviderGenerationError) as excinfo:
        provider.generate(system="sys", prompt="p")

    assert fakes["gemini-3.7-flash"].calls == 0
    not_attempted = [a for a in excinfo.value.attempt_log if a["model"] == "gemini-3.7-flash"]
    assert len(not_attempted) == 1
    assert not_attempted[0]["estimated_cost_usd"] == 0.0
    assert excinfo.value.estimated_cost_usd == 0.0  # the one real attempt had no usage-exposed cost either


# -- 15: max-run-cost still blocks a Gemini fallback request ----------------


def test_15_run_cost_budget_blocks_fallback_even_with_wall_clock_time_remaining():
    from decimal import Decimal

    from llm_deliberation.cost_budget import RunBudgetGuard

    clock = FakeClock()
    fakes = {
        "gemini-3.8-flash": TimedProvider("gemini-3.8-flash", clock, [(0.1, timeout_error())] * 4),
        "gemini-3.7-flash": TimedProvider("gemini-3.7-flash", clock, [(0.1, make_response("gemini-3.7-flash"))]),
    }
    provider = build_provider(fakes, clock, deadline_seconds=300.0)  # plenty of wall-clock time
    guard = RunBudgetGuard(budget_usd=Decimal("0.0001"))  # but essentially no money left

    with pytest.raises(ProviderGenerationError) as excinfo:
        provider.generate(system="sys", prompt="p", budget_guard=guard)

    assert excinfo.value.reason == "run_budget_exceeded"
    assert fakes["gemini-3.7-flash"].calls == 0  # blocked before the fallback was ever called


# -- 16/17: UI hides raw SDK text but preserves it in provenance ------------


def test_16_17_main_ui_hides_raw_sdk_text_but_keeps_it_in_provenance(
    client, service, fake_orchestrator_state
):
    fake_orchestrator_state["fail_with"] = {
        "red_team": ProviderGenerationError(
            "Gemini red-team wall-clock budget of 300s was exhausted before the "
            "fallback chain could complete (2 real attempt(s) made across 2 "
            "logged, 1 model(s) never attempted).",
            requested_model="gemini-3.8-flash",
            attempts=2,
            attempt_log=[
                {
                    "model": "gemini-3.8-flash", "attempt_number": 1, "outcome": "failed",
                    "delay_before_seconds": 0.0, "http_code": None,
                    "error": "Request timed out. This is a client-side timeout. You can "
                             "increase the timeout by setting the `timeout` argument on "
                             "your request or in the client http options.",
                    "reason": "Gemini request timed out.", "estimated_cost_usd": 0.0,
                },
                {
                    "model": "gemini-3.8-flash", "attempt_number": 2, "outcome": "failed",
                    "delay_before_seconds": 2.1, "http_code": None,
                    "error": "Request timed out. This is a client-side timeout. You can "
                             "increase the timeout by setting the `timeout` argument on "
                             "your request or in the client http options.",
                    "reason": "Gemini request timed out.", "estimated_cost_usd": 0.0,
                },
                {
                    "model": "gemini-3.7-flash", "attempt_number": 0, "outcome": "not_attempted",
                    "delay_before_seconds": 0.0, "http_code": None, "error": None,
                    "reason": "red-team time budget exhausted", "estimated_cost_usd": 0.0,
                },
            ],
            fallback_used=True,
            fallback_reason="Gemini request timed out.",
            estimated_cost_usd=0.0,
            reason="red_team_budget_exhausted",
        )
    }
    response = _submit(client, question="Q?", red_team=True)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    body = client.get(f"/runs/{run_id}").text

    # 16: the raw SDK guidance text must not appear in the primary error area.
    assert "increase the timeout" not in body.lower()
    # 17: it remains available in per-attempt provenance (attempt_log,
    # rendered via row.attempts_by_model / artifact_fallback-note) -- not
    # deleted, just not the headline message.
    record = service.get_run(run_id)
    red_team_stage = next(s for s in record.stages if s.name == "red_team")
    assert red_team_stage.attempt_log is not None
    raw_errors = [a["error"] for a in red_team_stage.attempt_log if a.get("error")]
    assert any("increase the timeout" in e.lower() for e in raw_errors)
    # The not_attempted fallback is shown as such, never as "failed".
    assert "not attempted" in body.lower()
    assert "gemini-3.7-flash" in body


def _submit(client, *, question="Q?", profile="economy", red_team=False, context="", language="en"):
    data = {"question": question, "profile": profile, "context": context, "language": language}
    if red_team:
        data["red_team"] = "1"
    return client.post("/runs", data=data)


# -- 18/19: EN/ET user-facing timeout message ---------------------------------


def _budget_exhausted_fail_with(model_id: str = "gemini-3.8-flash") -> dict:
    return {
        "red_team": ProviderGenerationError(
            "technical message, not shown as the primary error",
            requested_model=model_id,
            attempts=2,
            attempt_log=[
                {
                    "model": model_id, "attempt_number": 1, "outcome": "failed",
                    "delay_before_seconds": 0.0, "http_code": None,
                    "error": "Request timed out. You can increase the timeout by setting "
                             "the `timeout` argument...",
                    "reason": "Gemini request timed out.", "estimated_cost_usd": 0.0,
                },
                {
                    "model": "gemini-3.7-flash", "attempt_number": 0, "outcome": "not_attempted",
                    "delay_before_seconds": 0.0, "http_code": None, "error": None,
                    "reason": "red-team time budget exhausted", "estimated_cost_usd": 0.0,
                },
            ],
            fallback_used=True,
            fallback_reason="Gemini request timed out.",
            estimated_cost_usd=0.0,
            reason="red_team_budget_exhausted",
        )
    }


def test_18_en_user_facing_timeout_message_renders(client, fake_orchestrator_state):
    fake_orchestrator_state["fail_with"] = _budget_exhausted_fail_with()
    response = _submit(client, question="Q?", red_team=True, language="en")
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    body = client.get(f"/runs/{run_id}").text

    assert "Gemini 3.8 Flash" in body
    assert "timed out before the fallback chain could complete." in body


def test_19_et_user_facing_timeout_message_renders(client, fake_orchestrator_state):
    client.get("/ui-language/et", follow_redirects=True)
    fake_orchestrator_state["fail_with"] = _budget_exhausted_fail_with()
    response = _submit(client, question="Q?", red_team=True, language="et")
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    body = client.get(f"/runs/{run_id}").text

    assert "Gemini 3.8 Flash" in body
    assert "aegus enne, kui kogu varumudelite ahel jõuti läbi proovida." in body


def test_general_message_used_when_no_specific_model_identifiable(client, fake_orchestrator_state):
    # budget_exhausted but no not_attempted entries at all (e.g. every
    # model was genuinely attempted right up to the deadline) -- falls back
    # to the general wording, not a fabricated specific model name.
    fake_orchestrator_state["fail_with"] = {
        "red_team": ProviderGenerationError(
            "technical message",
            requested_model="gemini-3.8-flash",
            attempts=1,
            attempt_log=[
                {
                    "model": "gemini-3.8-flash", "attempt_number": 1, "outcome": "failed",
                    "delay_before_seconds": 0.0, "http_code": None, "error": "timeout detail",
                    "reason": "Gemini request timed out.", "estimated_cost_usd": 0.0,
                },
            ],
            fallback_used=False,
            fallback_reason=None,
            estimated_cost_usd=0.0,
            reason="red_team_budget_exhausted",
        )
    }
    response = _submit(client, question="Q?", red_team=True)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    body = client.get(f"/runs/{run_id}").text

    assert "Gemini did not respond within the red-team time limit" in body


# -- 20: historical attempt logs (old shape, no not_attempted) still render --


def test_20_historical_attempt_log_without_not_attempted_entries_still_renders(
    client, fake_orchestrator_state
):
    fake_orchestrator_state["fail_with"] = {
        "red_team": ProviderGenerationError(
            "All configured Gemini models failed transiently: gemini-3.8-flash, gemini-3.7-flash.",
            requested_model="gemini-3.8-flash",
            attempts=8,
            attempt_log=[
                {
                    "model": "gemini-3.8-flash", "attempt_number": i + 1, "outcome": "failed",
                    "delay_before_seconds": 0.0, "http_code": 503, "error": "HTTP 503",
                    "reason": "HTTP 503 from Gemini (overloaded)", "estimated_cost_usd": 0.0,
                }
                for i in range(4)
            ]
            + [
                {
                    "model": "gemini-3.7-flash", "attempt_number": i + 5, "outcome": "failed",
                    "delay_before_seconds": 0.0, "http_code": 500, "error": "HTTP 500",
                    "reason": "HTTP 500 from Gemini (overloaded)", "estimated_cost_usd": 0.0,
                }
                for i in range(4)
            ],
            fallback_used=True,
            fallback_reason="HTTP 500 from Gemini (overloaded)",
            estimated_cost_usd=0.0,
            reason="provider_error",  # pre-existing typed reason, not the new one
        )
    }
    response = _submit(client, question="Q?", red_team=True)
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]

    detail = client.get(f"/runs/{run_id}")
    assert detail.status_code == 200
    body = detail.text
    assert "gemini-3.8-flash" in body
    assert "gemini-3.7-flash" in body
    # A full attempt_log renders the grouped per-model view (see
    # presenter.group_attempts_by_model), not the flat "Attempts (this
    # try): N" summary -- that summary is reserved for a historical stage
    # whose attempt_log itself is empty/absent (see
    # test_failed_red_team_stage_shows_recoverable_actions_and_fallback_provenance
    # in test_web.py for that other, still-unaffected case).
    assert "4 attempts" in body
    # The old failure_reason path renders the raw stored error, unaffected.
    assert "All configured Gemini models failed transiently" in body
