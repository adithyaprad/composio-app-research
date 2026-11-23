from __future__ import annotations

from agent.config import ConfigError, require_env
from agent.llm.base import LLMResult, system_with_json


class AnthropicLLM:
    provider = "anthropic"

    def __init__(self, model: str, max_tokens: int):
        key = require_env("ANTHROPIC_API_KEY", "LLM_PROVIDER=anthropic")
        try:
            import anthropic
        except ImportError as e:
            raise ConfigError("LLM_PROVIDER=anthropic needs the anthropic package (pip install anthropic)") from e
        self.model = model
        self.max_tokens = max_tokens
        self._client = anthropic.Anthropic(api_key=key)

    def complete(self, system, messages, json_mode=True):
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_with_json(system, json_mode),
            messages=messages,
        )
        text = "".join(getattr(b, "text", "") for b in resp.content)
        return LLMResult(text, resp.usage.input_tokens, resp.usage.output_tokens,
                         stop_reason=resp.stop_reason, truncated=resp.stop_reason == "max_tokens")
