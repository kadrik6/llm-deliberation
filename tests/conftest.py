from __future__ import annotations

import json
import socket
from decimal import Decimal

import pytest

from llm_deliberation.types import ModelResponse, Usage

# -- network/credential safety net (applies to every test automatically) ---
#
# This whole test suite's one hard rule is "every provider call is faked,
# no live API calls" -- enforced so far only by convention (every test
# that touches a provider uses FakeOrchestrator or a hand-built stand-in).
# An accidental real call (e.g. a new test forgetting to fake its
# orchestrator, or a bug in a stand-in that lets a real SDK client get
# constructed) would previously either silently succeed against a real
# account or fail with a confusing provider-side error. These two autouse
# fixtures make that fail loudly and immediately instead:
#
# 1. Block real network sockets outright. FastAPI's TestClient (used by
#    every web-layer test) talks to the app in-process via an ASGI
#    transport -- it never opens a real socket -- and sqlite3 (this app's
#    only datastore) talks to a local file, not a socket, so neither is
#    affected. Only an actual outbound HTTP call (openai/anthropic/
#    google-genai's SDKs, or anything else) would ever reach this.
# 2. Neutralize the three provider API key env vars to an obviously-fake
#    sentinel by default, so a test that forgets to fake its own key never
#    silently inherits a real key from a developer's local `.env`. This
#    uses setenv, not delenv: a test/fixture (e.g. the `service` fixture
#    below) that sets its own fake value afterward still wins, regardless
#    of fixture ordering -- unlike delenv, which could race with a fixture
#    that already set a fake value and spuriously unset it again.


class BlockedNetworkAccess(RuntimeError):
    """Raised instead of actually connecting -- see _block_network_calls."""


def _blocked_connect(self, *args, **kwargs):
    raise BlockedNetworkAccess(
        "A test attempted to open a real network connection. Every "
        "provider call in this suite must be faked (see FakeOrchestrator "
        "below) -- no live API calls are allowed from tests."
    )


@pytest.fixture(autouse=True)
def _block_network_calls(monkeypatch):
    # Patches socket.socket's connect/connect_ex *instance methods*, not
    # the socket() constructor itself or socket.socketpair() -- asyncio's
    # own event loop creates a local socketpair() for its internal self-
    # pipe wakeup mechanism (an already-connected pair, never going through
    # .connect()), and FastAPI's TestClient talks to the app in-process via
    # an ASGI transport with no socket involved at all. Only an actual
    # outbound connection attempt (to anything, including localhost) calls
    # .connect()/.connect_ex() -- which is also what socket.create_connection()
    # and every higher-level HTTP client ultimately call through to, so
    # patching here catches all of them without needing to patch each one.
    monkeypatch.setattr(socket.socket, "connect", _blocked_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked_connect)


@pytest.fixture(autouse=True)
def _neutralize_real_api_keys(monkeypatch):
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.setenv(key, "sk-test-network-blocked-do-not-use")

# A minimal, schema-valid convergence_analysis payload. Every test that lets
# a run reach "succeeded" exercises this stage, so the fake needs a
# realistic default -- unlike every other stage's plain "{stage}-output"
# text, convergence_analysis's stored artifact must be parseable JSON (see
# llm_deliberation.convergence.ConvergenceAnalysis). Override per-test via
# fake_orchestrator_state["responses"]["convergence_analysis"].
DEFAULT_CONVERGENCE_JSON = json.dumps(
    {
        "convergence": "converged",
        "material_changes": [],
        "agreements_reached": [],
        "unresolved_disagreements": [],
        "remaining_unknowns": [],
        "human_judgement_required": [],
    }
)

# Stage 0 (question framing, see ADR 009) fake defaults -- schema-valid so
# question_framing.parse_* always succeeds unless a test overrides the
# response via state["responses"]["question_framing" | "framing_analysis_a"
# | "framing_analysis_b"].
DEFAULT_PROBLEM_FRAMING_JSON = json.dumps(
    {
        "classification": "complicated",
        "reason": "fake reason",
        "questions": ["Fake question 1?", "Fake question 2?", "Fake question 3?"],
    }
)

DEFAULT_FRAMING_ANALYSIS_JSON = json.dumps(
    {
        "hidden_assumptions": ["fake assumption"],
        "reframings": ["fake reframing"],
        "is_right_problem": "yes",
        "right_problem_note": "fake note",
        "premortem_reasons": ["fake reason 1", "fake reason 2", "fake reason 3"],
    }
)


class FakeOrchestrator:
    """Stands in for DeliberationOrchestrator: no network, deterministic.

    Each instance shares a `state` dict (injected via monkeypatch) so tests
    can control which stages fail and inspect the call log across the
    multiple service._execute() invocations a test may trigger (initial run,
    resume, retry).

    state["fail"]: set[str] of stage names that raise a plain RuntimeError.
    state["fail_with"]: dict[str, Exception] of stage names that raise a
        specific exception instance instead (e.g. ProviderGenerationError,
        to simulate the Gemini fallback chain exhausting itself).
    state["responses"]: dict[str, dict] of per-stage ModelResponse field
        overrides (e.g. to simulate a successful fallback), keyed by stage
        name.
    state["gemini_modes"]: list[(stage, gemini_mode)] observed, so tests can
        assert which retry mode the service actually requested.
    state["languages"]: list[(stage, resolved_language)] observed -- the
        actual per-stage language after applying
        orchestrator.resolve_stage_language(stage, output_language,
        working_language), i.e. exactly what the real orchestrator would
        have passed to prompts.base_system for that stage. This is what
        most existing tests care about ("which language did this stage
        actually get"), so the key/shape is unchanged from before working-
        language support existed -- only the *values* can now differ
        between stages within the same run.
    state["language_calls"]: list[(stage, output_language, working_language)]
        observed -- the raw, un-resolved arguments service.py passed, for
        tests that specifically want to assert on those rather than the
        already-resolved value.
    """

    def __init__(self, settings, *, state: dict):
        self.settings = settings
        self._state = state

    def stage_upper_bound_cost(
        self,
        stage: str,
        question: str,
        texts: dict[str, str],
        *,
        output_language: str = "en",
        working_language: str = "en",
    ) -> Decimal:
        # Deterministic and independent of question/context length --
        # FakeOrchestrator never touches real pricing/models. Tests that
        # exercise the real budget guard set state["upper_bound_cost_usd"]
        # to either a single Decimal (applied to every stage) or a
        # dict[str, Decimal] (per-stage, falling back to a tiny default for
        # any stage not listed) to control exactly which stage(s) a given
        # budget should or shouldn't admit.
        override = self._state.get("upper_bound_cost_usd")
        if isinstance(override, dict):
            return override.get(stage, Decimal("0.0001"))
        return override if override is not None else Decimal("0.0001")

    async def run_stage(
        self,
        stage: str,
        question: str,
        texts: dict[str, str],
        *,
        gemini_mode: str = "chain",
        output_language: str = "en",
        working_language: str = "en",
        budget_guard=None,
    ) -> ModelResponse:
        from llm_deliberation.orchestrator import resolve_stage_language

        resolved_language = resolve_stage_language(
            stage, output_language=output_language, working_language=working_language
        )
        self._state["log"].append(stage)
        # The effective question service._execute actually passed in for
        # this stage -- lets a test confirm a locked Stage 0 question was
        # (or wasn't) substituted for the run's original question (ADR 009).
        self._state.setdefault("questions", []).append(question)
        self._state.setdefault("gemini_modes", []).append((stage, gemini_mode))
        self._state.setdefault("languages", []).append((stage, resolved_language))
        self._state.setdefault("language_calls", []).append(
            (stage, output_language, working_language)
        )
        self._state.setdefault("budget_guards", []).append((stage, budget_guard))

        fail_with = self._state.get("fail_with", {})
        if stage in fail_with:
            raise fail_with[stage]
        if stage in self._state["fail"]:
            raise RuntimeError(f"simulated failure in stage '{stage}'")

        overrides = self._state.get("responses", {}).get(stage, {})
        default_text = DEFAULT_CONVERGENCE_JSON if stage == "convergence_analysis" else f"{stage}-output"
        defaults = dict(
            provider="Fake",
            model="fake-model",
            text=default_text,
            usage=Usage(input_tokens=10, output_tokens=20),
            estimated_cost_usd=0.001,
            requested_model="fake-model",
        )
        defaults.update(overrides)
        return ModelResponse(**defaults)

    async def run_question_framing(
        self, question: str, context: str | None, *, output_language: str = "en", budget_guard=None,
    ) -> ModelResponse:
        self._state["log"].append("question_framing")
        fail_with = self._state.get("fail_with", {})
        if "question_framing" in fail_with:
            raise fail_with["question_framing"]
        if "question_framing" in self._state["fail"]:
            raise RuntimeError("simulated failure in stage 'question_framing'")
        overrides = self._state.get("responses", {}).get("question_framing", {})
        defaults = dict(
            provider="Fake",
            model="fake-model",
            text=DEFAULT_PROBLEM_FRAMING_JSON,
            usage=Usage(input_tokens=10, output_tokens=20),
            estimated_cost_usd=0.001,
            requested_model="fake-model",
        )
        defaults.update(overrides)
        return ModelResponse(**defaults)

    async def run_framing_analysis(
        self,
        candidate: str,
        question: str,
        context: str | None,
        interview: list[tuple[str, str]],
        *,
        output_language: str = "en",
        budget_guard=None,
    ) -> ModelResponse:
        name = f"framing_analysis_{candidate}"
        self._state["log"].append(name)
        fail_with = self._state.get("fail_with", {})
        if name in fail_with:
            raise fail_with[name]
        if name in self._state["fail"]:
            raise RuntimeError(f"simulated failure in stage '{name}'")
        overrides = self._state.get("responses", {}).get(name, {})
        defaults = dict(
            provider="Fake",
            model="fake-model",
            text=DEFAULT_FRAMING_ANALYSIS_JSON,
            usage=Usage(input_tokens=10, output_tokens=20),
            estimated_cost_usd=0.001,
            requested_model="fake-model",
        )
        defaults.update(overrides)
        return ModelResponse(**defaults)


@pytest.fixture
def fake_orchestrator_state():
    return {"log": [], "fail": set()}


def _always_ready_readiness_report(settings, *, red_team_enabled: bool, force: bool = False):
    """Stands in for readiness.check_run_readiness: no network, always
    "ready" -- mirrors FakeOrchestrator's role for provider generation
    calls, but for the readiness preflight (see web/app.py's submit_run,
    which now gates run creation on DeliberationService.check_readiness).
    A test that specifically exercises the readiness-blocked path overrides
    this via its own monkeypatch.setattr on service_module.readiness's
    individual check_* functions or on this fixture's target directly,
    layered on top (see test_readiness.py's pattern).
    """
    from llm_deliberation.readiness import ProviderReadiness, ReadinessReport, _now_iso

    def _ready(provider: str, model: str, check_type: str) -> ProviderReadiness:
        return ProviderReadiness(
            provider=provider,
            configured_model=model,
            status="ready",
            checked_at=_now_iso(),
            check_type=check_type,
            paid_probe=False,
            estimated_probe_cost_usd=0.0,
            user_message="Provider readiness check passed.",
        )

    return ReadinessReport(
        openai=_ready("openai", settings.openai_model, "model_retrieve"),
        anthropic=_ready("anthropic", settings.anthropic_model, "model_retrieve"),
        gemini=_ready("gemini", settings.gemini_model, "model_get") if red_team_enabled else None,
    )


@pytest.fixture
def service(tmp_path, monkeypatch, fake_orchestrator_state):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

    import llm_deliberation.service as service_module

    def factory(settings):
        return FakeOrchestrator(settings, state=fake_orchestrator_state)

    monkeypatch.setattr(service_module, "DeliberationOrchestrator", factory)
    monkeypatch.setattr(service_module.readiness, "check_run_readiness", _always_ready_readiness_report)

    from llm_deliberation.service import DeliberationService

    db_path = tmp_path / "deliberation.db"
    return DeliberationService(db_path)


@pytest.fixture
def client(service):
    from fastapi.testclient import TestClient

    from llm_deliberation.web.app import create_app

    app = create_app(service=service)
    with TestClient(app, follow_redirects=True) as test_client:
        yield test_client
