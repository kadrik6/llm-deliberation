from __future__ import annotations

import pytest

from llm_deliberation.types import ModelResponse, Usage


class FakeOrchestrator:
    """Stands in for DeliberationOrchestrator: no network, deterministic.

    Each instance shares a `state` dict (injected via monkeypatch) so tests
    can control which stages fail and inspect the call log across the
    multiple service._execute() invocations a test may trigger (initial run,
    resume, retry).
    """

    def __init__(self, settings, *, state: dict):
        self.settings = settings
        self._state = state

    async def run_stage(self, stage: str, question: str, texts: dict[str, str]) -> ModelResponse:
        self._state["log"].append(stage)
        if stage in self._state["fail"]:
            raise RuntimeError(f"simulated failure in stage '{stage}'")
        return ModelResponse(
            provider="Fake",
            model="fake-model",
            text=f"{stage}-output",
            usage=Usage(input_tokens=10, output_tokens=20),
            estimated_cost_usd=0.001,
        )


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
