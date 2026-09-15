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


# Applied only when GEMINI_FALLBACK_MODELS is unset entirely. Set the env
# var to an empty string to disable fallback and only ever try GEMINI_MODEL.
DEFAULT_GEMINI_FALLBACK_MODELS: tuple[str, ...] = ("gemini-3.7-flash", "gemini-3.6-flash")

# Which provider runs the convergence_analysis stage. Default is Anthropic,
# not Gemini or OpenAI:
#  - different from the final synthesizer (OpenAI) -- a synthesis model
#    grading its own deliberation's convergence would be a weaker signal;
#  - ANTHROPIC_API_KEY is already mandatory for every run, so the default
#    path adds no new required setup (defaulting to Gemini would silently
#    require GEMINI_API_KEY even with red-team off).
# Fully overridable via CONVERGENCE_PROVIDER/CONVERGENCE_MODEL.
CONVERGENCE_PROVIDERS: tuple[str, ...] = ("openai", "anthropic", "gemini")
DEFAULT_CONVERGENCE_PROVIDER = "anthropic"


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_model_chain(raw: str | None) -> list[str]:
    """Parse a comma-separated model list, deduping while preserving order."""
    if not raw:
        return []
    seen: list[str] = []
    for part in raw.split(","):
        model = part.strip()
        if model and model not in seen:
            seen.append(model)
    return seen


def default_red_team_enabled() -> bool:
    """Red-team default when a caller doesn't specify one explicitly."""
    load_dotenv()
    return _bool_env("RED_TEAM_ENABLED", True)


def default_profile() -> str:
    load_dotenv()
    return os.getenv("LLM_PROFILE", "balanced")


@dataclass(slots=True)
class Settings:
    profile: str
    red_team_enabled: bool
    openai_model: str
    anthropic_model: str
    gemini_model: str
    gemini_fallback_models: list[str]
    convergence_provider: str
    convergence_model: str
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

        openai_model = os.getenv("OPENAI_MODEL") or defaults["openai_model"]
        anthropic_model = os.getenv("ANTHROPIC_MODEL") or defaults["anthropic_model"]
        gemini_model = os.getenv("GEMINI_MODEL") or defaults["gemini_model"]
        fallback_raw = os.getenv("GEMINI_FALLBACK_MODELS")
        fallback_models = (
            _parse_model_chain(fallback_raw)
            if fallback_raw is not None
            else list(DEFAULT_GEMINI_FALLBACK_MODELS)
        )
        # The preferred model is always tried first; never list it twice.
        gemini_fallback_models = [m for m in fallback_models if m != gemini_model]

        convergence_provider = (
            os.getenv("CONVERGENCE_PROVIDER") or DEFAULT_CONVERGENCE_PROVIDER
        ).strip().lower()
        if convergence_provider not in CONVERGENCE_PROVIDERS:
            valid = ", ".join(CONVERGENCE_PROVIDERS)
            raise ValueError(
                f"Unknown CONVERGENCE_PROVIDER '{convergence_provider}'. Choose one of: {valid}"
            )
        # Reuses that provider's already-resolved model for this profile by
        # default, so "max" gets a stronger convergence analyst and
        # "economy" a cheaper one without a second profile axis to maintain.
        _convergence_model_default = {
            "openai": openai_model,
            "anthropic": anthropic_model,
            "gemini": gemini_model,
        }[convergence_provider]
        convergence_model = os.getenv("CONVERGENCE_MODEL") or _convergence_model_default

        return cls(
            profile=profile,
            red_team_enabled=red_team,
            openai_model=openai_model,
            anthropic_model=anthropic_model,
            gemini_model=gemini_model,
            gemini_fallback_models=gemini_fallback_models,
            convergence_provider=convergence_provider,
            convergence_model=convergence_model,
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
        needs_gemini = self.red_team_enabled or self.convergence_provider == "gemini"
        if needs_gemini and not os.getenv("GEMINI_API_KEY"):
            missing.append("GEMINI_API_KEY")

        if missing:
            raise RuntimeError(
                "Missing API key(s): "
                + ", ".join(missing)
                + ". Copy .env.example to .env and add the keys, "
                  "or disable red-team if only GEMINI_API_KEY is missing."
            )
