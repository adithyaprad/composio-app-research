from __future__ import annotations

import os

from agent.config import ConfigError, require_env
from agent.llm.base import LLMResult, system_with_json


class BedrockLLM:
    """AWS Bedrock Converse API. Works with any Converse-capable model id."""

    provider = "bedrock"

    def __init__(self, model: str, max_tokens: int):
        if not os.environ.get("AWS_PROFILE"):
            require_env("AWS_ACCESS_KEY_ID", "LLM_PROVIDER=bedrock")
            require_env("AWS_SECRET_ACCESS_KEY", "LLM_PROVIDER=bedrock")
        try:
            import boto3
        except ImportError as e:
            raise ConfigError("LLM_PROVIDER=bedrock needs boto3 (pip install boto3)") from e
        self.model = model
        self.max_tokens = max_tokens
        region = os.environ.get("AWS_REGION") or "ap-south-1"
        self._client = boto3.client("bedrock-runtime", region_name=region)

    def complete(self, system, messages, json_mode=True):
        resp = self._client.converse(
            modelId=self.model,
            system=[{"text": system_with_json(system, json_mode)}],
            messages=[{"role": m["role"], "content": [{"text": m["content"]}]} for m in messages],
            inferenceConfig={"maxTokens": self.max_tokens},
        )
        blocks = resp["output"]["message"]["content"]
        usage = resp.get("usage", {})
        stop = resp.get("stopReason")
        return LLMResult(
            text="".join(b.get("text", "") for b in blocks),
            input_tokens=usage.get("inputTokens", 0),
            output_tokens=usage.get("outputTokens", 0),
            stop_reason=stop,
            truncated=stop == "max_tokens",
        )
