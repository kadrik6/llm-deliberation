from __future__ import annotations

import asyncio

from llm_deliberation import prompts
from llm_deliberation.config import Settings
from llm_deliberation.providers import (
    AnthropicProvider,
    GeminiProvider,
    OpenAIProvider,
    Provider,
)
from llm_deliberation.types import ModelResponse, RunResult


async def _call(provider: Provider, *, system: str, prompt: str) -> ModelResponse:
    # Provider SDK calls are synchronous. to_thread lets independent calls run
    # concurrently without coupling the project to provider-specific async APIs.
    return await asyncio.to_thread(
        provider.generate,
        system=system,
        prompt=prompt,
    )


class DeliberationOrchestrator:
    def __init__(self, settings: Settings):
        self.settings = settings

        self.a = OpenAIProvider(
            model=settings.openai_model,
            max_output_tokens=settings.max_output_tokens,
            effort=settings.openai_effort,
        )
        self.b = AnthropicProvider(
            model=settings.anthropic_model,
            max_output_tokens=settings.max_output_tokens,
            effort=settings.anthropic_effort,
        )
        self.red = GeminiProvider(
            model=settings.gemini_model,
            max_output_tokens=settings.max_output_tokens,
            thinking_level=settings.gemini_thinking_level,
        )

    async def run(self, question: str) -> RunResult:
        system = prompts.BASE_SYSTEM

        # Stage 1: true independent generation.
        analysis_a, analysis_b = await asyncio.gather(
            _call(
                self.a,
                system=system,
                prompt=prompts.independent_analysis(question),
            ),
            _call(
                self.b,
                system=system,
                prompt=prompts.independent_analysis(question),
            ),
        )

        # Stage 2: cross-review and optional third-model red-team.
        jobs = [
            _call(
                self.a,
                system=system,
                prompt=prompts.critique(question, analysis_b.text),
            ),
            _call(
                self.b,
                system=system,
                prompt=prompts.critique(question, analysis_a.text),
            ),
        ]
        if self.settings.red_team_enabled:
            jobs.append(
                _call(
                    self.red,
                    system=system,
                    prompt=prompts.red_team(
                        question,
                        analysis_a.text,
                        analysis_b.text,
                    ),
                )
            )

        stage2 = await asyncio.gather(*jobs)
        critique_a_of_b = stage2[0]
        critique_b_of_a = stage2[1]
        red_team = stage2[2] if self.settings.red_team_enabled else None
        red_text = red_team.text if red_team else None

        # Stage 3: each candidate revises its own answer using peer feedback
        # and the shared-blind-spot report.
        revision_a, revision_b = await asyncio.gather(
            _call(
                self.a,
                system=system,
                prompt=prompts.revision(
                    question,
                    analysis_a.text,
                    critique_b_of_a.text,
                    red_text,
                ),
            ),
            _call(
                self.b,
                system=system,
                prompt=prompts.revision(
                    question,
                    analysis_b.text,
                    critique_a_of_b.text,
                    red_text,
                ),
            ),
        )

        # Stage 4: final synthesis. Candidate names are intentionally generic.
        synthesis = await _call(
            self.a,
            system=system,
            prompt=prompts.synthesis(
                question,
                revision_a.text,
                revision_b.text,
                red_text,
            ),
        )

        return RunResult(
            question=question,
            profile=self.settings.profile,
            red_team_enabled=self.settings.red_team_enabled,
            analysis_a=analysis_a,
            analysis_b=analysis_b,
            critique_a_of_b=critique_a_of_b,
            critique_b_of_a=critique_b_of_a,
            red_team=red_team,
            revision_a=revision_a,
            revision_b=revision_b,
            synthesis=synthesis,
        )
