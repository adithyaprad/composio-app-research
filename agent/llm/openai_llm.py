from __future__ import annotations

from agent.config import ConfigError, require_env
from agent.llm.base import LLMResult, system_with_json


class OpenAILLM:
    provider = "openai"

    def __init__(self, model: str, max_tokens: int):
        key = require_env("OPENAI_API_KEY", "LLM_PROVIDER=openai")
        try:
            import openai
        except ImportError as e:
            raise ConfigError("LLM_PROVIDER=openai needs the openai package (pip install openai)") from e
        self.model = model
        self.max_tokens = max_tokens
        self._client = openai.OpenAI(api_key=key)

    def complete(self, system, messages, json_mode=True):
        extra = {"response_format": {"type": "json_object"}} if json_mode else {}
        resp = self._client.chat.completions.create(
            model=self.model,
            max_completion_tokens=self.max_tokens,
            messages=[{"role": "system", "content": system_with_json(system, json_mode)}, *messages],
            **extra,
        )
        usage = resp.usage
        return LLMResult(
            resp.choices[0].message.content or "",
            getattr(usage, "prompt_tokens", 0) or 0,
            getattr(usage, "completion_tokens", 0) or 0,
        )
