from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


PROFILES = {
    # Strong everyday default without Fable's higher token price.
    "balanced": {
        "openai_model": "gpt-5.6-sol",
        "anthropic_model": "claude-opus-5",
        "gemini_model": "gemini-3.8-flash",
    },
    # Strongest verified Anthropic general-access model in this starter.
    "max": {
    "openai_model": "gpt-6-astra",
    "anthropic_model": "claude-fable-5",
    "gemini_model": "gemini-3.8-flash",
    },
    # Cheaper profile for experimentation and prompt iteration.
    "economy": {
        "openai_model": "gpt-5.6-terra",
        "anthropic_model": "claude-sonnet-5",
        "gemini_model": "gemini-3.8-flash",
    },
}


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class Settings:
    profile: str
    red_team_enabled: bool
    openai_model: str
    anthropic_model: str
    gemini_model: str
    openai_effort: str
    anthropic_effort: str
    gemini_thinking_level: str
    max_output_tokens: int

    @classmethod
    def load(
        cls,
        *,
        profile_override: str | None = None,
        red_team_override: bool | None = None,
    ) -> "Settings":
        load_dotenv()

        profile = profile_override or os.getenv("LLM_PROFILE", "balanced")
        if profile not in PROFILES:
            valid = ", ".join(PROFILES)
            raise ValueError(f"Unknown profile '{profile}'. Choose one of: {valid}")

        defaults = PROFILES[profile]
        red_team = (
            red_team_override
            if red_team_override is not None
            else _bool_env("RED_TEAM_ENABLED", True)
        )

        return cls(
            profile=profile,
            red_team_enabled=red_team,
            openai_model=os.getenv("OPENAI_MODEL") or defaults["openai_model"],
            anthropic_model=os.getenv("ANTHROPIC_MODEL") or defaults["anthropic_model"],
            gemini_model=os.getenv("GEMINI_MODEL") or defaults["gemini_model"],
            openai_effort=os.getenv("OPENAI_EFFORT", "high"),
            anthropic_effort=os.getenv("ANTHROPIC_EFFORT", "high"),
            gemini_thinking_level=os.getenv("GEMINI_THINKING_LEVEL", "high"),
            max_output_tokens=int(os.getenv("MAX_OUTPUT_TOKENS", "5000")),
        )

    def validate_keys(self) -> None:
        missing: list[str] = []
        if not os.getenv("OPENAI_API_KEY"):
            missing.append("OPENAI_API_KEY")
        if not os.getenv("ANTHROPIC_API_KEY"):
            missing.append("ANTHROPIC_API_KEY")
        if self.red_team_enabled and not os.getenv("GEMINI_API_KEY"):
            missing.append("GEMINI_API_KEY")

        if missing:
            raise RuntimeError(
                "Missing API key(s): "
                + ", ".join(missing)
                + ". Copy .env.example to .env and add the keys, "
                  "or disable red-team if only GEMINI_API_KEY is missing."
            )
