from __future__ import annotations

from llm_deliberation.types import Usage

# USD per 1M tokens.
# Snapshot: 2026-09-14.
# Keep this table explicit and easy to update as provider pricing changes.
PRICING: dict[str, tuple[float, float]] = {
    "gpt-5.6-sol": (4.00, 20.00),
    "gpt-5.6-terra": (2.00, 12.00),
    "gpt-5.6-luna": (0.20, 1.20),
    "claude-fable-5": (10.00, 50.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "gemini-3.8-flash": (0.75, 3.75),
}


def estimate_cost(model: str, usage: Usage) -> float:
    rates = PRICING.get(model)
    if not rates:
        return 0.0
    input_rate, output_rate = rates
    return (
        usage.input_tokens / 1_000_000 * input_rate
        + usage.output_tokens / 1_000_000 * output_rate
    )
