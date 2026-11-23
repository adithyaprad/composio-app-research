"""LLM output handling: raw attempts saved, truncation, unwrapping, unparseable output."""

import json

import pytest
from test_research import _apps, _lines, _researcher

from agent.fakes import FakeLLM
from agent.llm.base import LLMResult, extract_json
from agent.research import run


class ScriptedLLM(FakeLLM):
    """Each call takes the next transform from `script` and applies it to a valid FakeLLM answer.

    A transform gets the valid output dict and returns an LLMResult.
    """

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def complete(self, system, messages, json_mode=True):
        self.calls.append(messages)
        good = json.loads(super().complete(system, messages, json_mode).text)
        step = self.script.pop(0) if self.script else (lambda out: LLMResult(json.dumps(out), 100, 50, "end_turn"))
        return step(good)


def ok(out):
    return LLMResult(json.dumps(out), 100, 50, "end_turn")


def top_level(out):
    return LLMResult(json.dumps({**out["row"], "snippets": out["snippets"]}), 100, 50, "end_turn")


def truncated(out):
    text = json.dumps(out)
    return LLMResult(text[: len(text) // 2], 100, 8000, "max_tokens", truncated=True)


def garbage(out):
    return LLMResult('{"row": {"one_liner": "He said "hi" to me"}}', 100, 20, "end_turn")


def test_extract_json_does_not_fall_back_to_nested_object():
    """Regression for the all-null firecrawl row: a broken outer object must not yield an inner one."""
    text = '{"row": {"one_liner": "x", "evidence": {"auth": [{"url": "https://a", "source_type": "official_docs"}'
    with pytest.raises(ValueError, match="could not parse"):
        extract_json(text)


def test_top_level_fields_accepted(tmp_path):
    llm = ScriptedLLM(top_level)
    summary = run(_apps("firecrawl"), _researcher(tmp_path, llm), tmp_path)
    assert summary["ok"] == 1 and len(llm.calls) == 1
    row = _lines(tmp_path / "rows.jsonl")[0]
    assert row["access"] == "self_serve" and row["slug"] == "firecrawl"


def test_wrapped_row_accepted(tmp_path):
    llm = ScriptedLLM(lambda out: ok({"result": out}))
    assert run(_apps("firecrawl"), _researcher(tmp_path, llm), tmp_path)["ok"] == 1


def test_truncated_output_retries_compact(tmp_path):
    llm = ScriptedLLM(truncated, ok)
    summary = run(_apps("firecrawl"), _researcher(tmp_path, llm), tmp_path)
    assert summary["ok"] == 1 and len(llm.calls) == 2
    retry_msg = llm.calls[1][-1]["content"]
    assert "cut off at the max-token limit" in retry_msg and "more compact" in retry_msg
    assert "None is not of type" not in retry_msg
    saved = json.loads((tmp_path / "cache" / "firecrawl.llm.json").read_text())
    assert [a["stop_reason"] for a in saved["attempts"]] == ["max_tokens", "end_turn"]
    assert saved["attempts"][0]["truncated"] is True and saved["attempts"][0]["text"]
    assert [a["label"] for a in saved["attempts"]] == ["initial", "retry"]


def test_unparseable_output_explicit_error(tmp_path):
    llm = ScriptedLLM(garbage, garbage)
    summary = run(_apps("firecrawl"), _researcher(tmp_path, llm), tmp_path)
    assert summary["failed_validation"] == 1
    assert "could not be parsed as JSON" in llm.calls[1][-1]["content"]
    fail = _lines(tmp_path / "failures.jsonl")[0]
    assert len(fail["errors"]) == 1 and fail["errors"][0].startswith("output: your answer could not be parsed")
    assert len(json.loads((tmp_path / "cache" / "firecrawl.llm.json").read_text())["attempts"]) == 2


def test_json_without_row_fields_explicit_error(tmp_path):
    llm = ScriptedLLM(lambda out: ok({"answer": "I could not find anything"}), ok)
    summary = run(_apps("firecrawl"), _researcher(tmp_path, llm), tmp_path)
    assert summary["ok"] == 1
    assert "no row fields (top-level keys: answer)" in llm.calls[1][-1]["content"]


def test_empty_response_retry_has_nonempty_assistant_turn(tmp_path):
    llm = ScriptedLLM(lambda out: LLMResult("", 10, 0, "end_turn"), ok)
    assert run(_apps("firecrawl"), _researcher(tmp_path, llm), tmp_path)["ok"] == 1
    assert llm.calls[1][-2] == {"role": "assistant", "content": "(empty response)"}
