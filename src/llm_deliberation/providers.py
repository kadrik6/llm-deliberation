from __future__ import annotations

from abc import ABC, abstractmethod

from llm_deliberation.pricing import estimate_cost
from llm_deliberation.types import ModelResponse, Usage


class Provider(ABC):
    provider_name: str

    def __init__(self, model: str, max_output_tokens: int):
        self.model = model
        self.max_output_tokens = max_output_tokens

    @abstractmethod
    def generate(self, *, system: str, prompt: str) -> ModelResponse:
        raise NotImplementedError

    def _result(self, text: str, usage: Usage) -> ModelResponse:
        return ModelResponse(
            provider=self.provider_name,
            model=self.model,
            text=text.strip(),
            usage=usage,
            estimated_cost_usd=estimate_cost(self.model, usage),
        )


class OpenAIProvider(Provider):
    provider_name = "OpenAI"

    def __init__(
        self,
        model: str,
        max_output_tokens: int,
        effort: str = "high",
    ):
        super().__init__(model, max_output_tokens)
        self.effort = effort

    def generate(self, *, system: str, prompt: str) -> ModelResponse:
        from openai import OpenAI

        client = OpenAI()
        response = client.responses.create(
            model=self.model,
            instructions=system,
            input=prompt,
            reasoning={"effort": self.effort},
            max_output_tokens=self.max_output_tokens,
            store=False,
        )

        usage_obj = getattr(response, "usage", None)
        usage = Usage(
            input_tokens=int(getattr(usage_obj, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage_obj, "output_tokens", 0) or 0),
        )
        return self._result(response.output_text or "", usage)


class AnthropicProvider(Provider):
    provider_name = "Anthropic"

    def __init__(
        self,
        model: str,
        max_output_tokens: int,
        effort: str = "high",
    ):
        super().__init__(model, max_output_tokens)
        self.effort = effort

    def generate(self, *, system: str, prompt: str) -> ModelResponse:
        import anthropic

        client = anthropic.Anthropic()
        message = client.messages.create(
            model=self.model,
            max_tokens=self.max_output_tokens,
            system=system,
            output_config={"effort": self.effort},
            messages=[{"role": "user", "content": prompt}],
        )

        text = "\n".join(
            block.text for block in message.content
            if getattr(block, "type", None) == "text"
        )
        usage_obj = getattr(message, "usage", None)
        usage = Usage(
            input_tokens=int(getattr(usage_obj, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage_obj, "output_tokens", 0) or 0),
        )
        return self._result(text, usage)


class GeminiProvider(Provider):
    provider_name = "Google"

    def __init__(
        self,
        model: str,
        max_output_tokens: int,
        thinking_level: str = "high",
    ):
        super().__init__(model, max_output_tokens)
        self.thinking_level = thinking_level

    def generate(self, *, system: str, prompt: str) -> ModelResponse:
        from google import genai

        # v1 is GA; store=False avoids retaining the Interaction object
        # for server-side conversation state.
        client = genai.Client(http_options={"api_version": "v1"})
        interaction = client.interactions.create(
            model=self.model,
            system_instruction=system,
            input=prompt,
            generation_config={
                "thinking_level": self.thinking_level,
                "max_output_tokens": self.max_output_tokens,
            },
            store=False,
        )

        usage_obj = getattr(interaction, "usage", None)
        usage = Usage(
            input_tokens=int(getattr(usage_obj, "total_input_tokens", 0) or 0),
            output_tokens=int(getattr(usage_obj, "total_output_tokens", 0) or 0)
            + int(getattr(usage_obj, "total_thought_tokens", 0) or 0),
        )
        return self._result(interaction.output_text or "", usage)
