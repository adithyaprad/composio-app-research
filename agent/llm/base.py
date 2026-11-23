from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

JSON_INSTRUCTION = "Respond with a single JSON object only. No prose, no markdown fences."


@dataclass
class LLMResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class LLM(Protocol):
    provider: str
    model: str

    def complete(self, system: str, messages: list[dict[str, str]], json_mode: bool = True) -> LLMResult:
        """messages: [{"role": "user" | "assistant", "content": str}, ...]"""
        ...


def system_with_json(system: str, json_mode: bool) -> str:
    return f"{system}\n\n{JSON_INSTRUCTION}" if json_mode else system


def extract_json(text: str) -> dict[str, Any]:
    """Parse the first JSON object in `text`, tolerating code fences and surrounding prose."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidates = [text] + ([fenced.group(1)] if fenced else [])
    decoder = json.JSONDecoder()
    for cand in candidates:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        for m in re.finditer(r"\{", cand):
            try:
                obj, _ = decoder.raw_decode(cand, m.start())
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj
    raise ValueError("no JSON object found in model output")
