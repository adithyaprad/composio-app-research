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
    stop_reason: str | None = None  # provider's raw value, e.g. "end_turn", "max_tokens", "length"
    truncated: bool = False  # output hit the max-token limit


class LLM(Protocol):
    provider: str
    model: str

    def complete(self, system: str, messages: list[dict[str, str]], json_mode: bool = True) -> LLMResult:
        """messages: [{"role": "user" | "assistant", "content": str}, ...]"""
        ...


def system_with_json(system: str, json_mode: bool) -> str:
    return f"{system}\n\n{JSON_INSTRUCTION}" if json_mode else system


def extract_json(text: str) -> dict[str, Any]:
    """Parse the outermost JSON object in `text`, tolerating code fences and surrounding prose.

    Only the object starting at the first "{" is tried. Falling back to later "{"
    would return a nested object (e.g. one evidence item) when the outer object is
    truncated or malformed, which silently produced all-null rows.
    """
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)(?:```|\Z)", text, re.DOTALL)
    candidates = [text] + ([fenced.group(1)] if fenced else [])
    decoder = json.JSONDecoder()
    last_error = "no '{' found"
    for cand in candidates:
        start = cand.find("{")
        if start < 0:
            continue
        try:
            obj, _ = decoder.raw_decode(cand, start)
        except json.JSONDecodeError as e:
            last_error = f"{e.msg} at char {e.pos} of {len(cand)}"
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError(f"could not parse a JSON object from model output ({last_error})")
