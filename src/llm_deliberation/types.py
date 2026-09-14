from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


@dataclass(slots=True)
class ModelResponse:
    provider: str
    model: str
    text: str
    usage: Usage = field(default_factory=Usage)
    estimated_cost_usd: float = 0.0


@dataclass(slots=True)
class RunResult:
    question: str
    profile: str
    red_team_enabled: bool
    analysis_a: ModelResponse
    analysis_b: ModelResponse
    critique_a_of_b: ModelResponse
    critique_b_of_a: ModelResponse
    red_team: ModelResponse | None
    revision_a: ModelResponse
    revision_b: ModelResponse
    synthesis: ModelResponse
    context: str | None = None

    @property
    def all_responses(self) -> list[ModelResponse]:
        items = [
            self.analysis_a,
            self.analysis_b,
            self.critique_a_of_b,
            self.critique_b_of_a,
            self.revision_a,
            self.revision_b,
            self.synthesis,
        ]
        if self.red_team is not None:
            items.append(self.red_team)
        return items

    @property
    def estimated_total_cost_usd(self) -> float:
        return sum(item.estimated_cost_usd for item in self.all_responses)
