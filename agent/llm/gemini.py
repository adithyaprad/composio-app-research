from __future__ import annotations

from agent.config import ConfigError, require_env
from agent.llm.base import LLMResult, system_with_json


class GeminiLLM:
    provider = "gemini"

    def __init__(self, model: str, max_tokens: int):
        key = require_env("GEMINI_API_KEY", "LLM_PROVIDER=gemini")
        try:
            from google import genai
            from google.genai import types
        except ImportError as e:
            raise ConfigError("LLM_PROVIDER=gemini needs the google-genai package (pip install google-genai)") from e
        self.model = model
        self.max_tokens = max_tokens
        self._types = types
        self._client = genai.Client(api_key=key)

    def complete(self, system, messages, json_mode=True):
        types = self._types
        contents = [
            types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[types.Part(text=m["content"])],
            )
            for m in messages
        ]
        config = types.GenerateContentConfig(
            system_instruction=system_with_json(system, json_mode),
            max_output_tokens=self.max_tokens,
            response_mime_type="application/json" if json_mode else None,
        )
        resp = self._client.models.generate_content(model=self.model, contents=contents, config=config)
        usage = resp.usage_metadata
        reason = resp.candidates[0].finish_reason if resp.candidates else None
        stop = getattr(reason, "name", None) or (str(reason) if reason else None)
        return LLMResult(
            resp.text or "",
            getattr(usage, "prompt_token_count", 0) or 0,
            getattr(usage, "candidates_token_count", 0) or 0,
            stop_reason=stop,
            truncated=stop == "MAX_TOKENS",
        )
