from __future__ import annotations

import json

import pytest

from llm_deliberation.types import ModelResponse, Usage

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
    """

    def __init__(self, settings, *, state: dict):
        self.settings = settings
        self._state = state

    async def run_stage(
        self,
        stage: str,
        question: str,
        texts: dict[str, str],
        *,
        gemini_mode: str = "chain",
    ) -> ModelResponse:
        self._state["log"].append(stage)
        self._state.setdefault("gemini_modes", []).append((stage, gemini_mode))

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


@pytest.fixture
def fake_orchestrator_state():
    return {"log": [], "fail": set()}


@pytest.fixture
def service(tmp_path, monkeypatch, fake_orchestrator_state):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

    import llm_deliberation.service as service_module

    def factory(settings):
        return FakeOrchestrator(settings, state=fake_orchestrator_state)

    monkeypatch.setattr(service_module, "DeliberationOrchestrator", factory)

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
