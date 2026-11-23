"""Provider-agnostic LLM layer: one `complete()` call, picked by LLM_PROVIDER.

Each adapter imports its SDK lazily, so only the chosen provider must be installed.
"""

from __future__ import annotations

from agent.config import Settings
from agent.llm.base import LLM, LLMResult, extract_json

DEFAULT_MODELS = {
    "bedrock": "global.anthropic.claude-sonnet-5-5",
    "anthropic": "claude-sonnet-5-5",
    "openai": "gpt-5.1",
    "gemini": "gemini-2.5-pro",
}


def get_llm(settings: Settings) -> LLM:
    model = settings.llm_model or DEFAULT_MODELS[settings.llm_provider]
    if settings.llm_provider == "bedrock":
        from agent.llm.bedrock import BedrockLLM

        return BedrockLLM(model, settings.llm_max_tokens)
    if settings.llm_provider == "anthropic":
        from agent.llm.anthropic_llm import AnthropicLLM

        return AnthropicLLM(model, settings.llm_max_tokens)
    if settings.llm_provider == "openai":
        from agent.llm.openai_llm import OpenAILLM

        return OpenAILLM(model, settings.llm_max_tokens)
    from agent.llm.gemini import GeminiLLM

    return GeminiLLM(model, settings.llm_max_tokens)


__all__ = ["DEFAULT_MODELS", "LLM", "LLMResult", "extract_json", "get_llm"]
