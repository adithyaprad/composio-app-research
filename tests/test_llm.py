import sys
import types

import pytest

from agent.config import ConfigError, Settings
from agent.llm import extract_json, get_llm

SECRET = "sk-test-SECRET-VALUE-123"


def _fake_module(monkeypatch, name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, name, mod)
    return mod


class _Recorder:
    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs


def test_anthropic_selected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    _fake_module(monkeypatch, "anthropic", Anthropic=_Recorder)
    llm = get_llm(Settings.from_env())
    assert llm.provider == "anthropic" and llm.model == "claude-sonnet-5-5"
    assert llm._client.kwargs == {"api_key": SECRET}


def test_openai_selected_with_model_override(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "my-model")
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    _fake_module(monkeypatch, "openai", OpenAI=_Recorder)
    llm = get_llm(Settings.from_env())
    assert (llm.provider, llm.model) == ("openai", "my-model")


def test_bedrock_default(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA" + SECRET)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", SECRET)
    calls = {}

    def client(service, region_name):
        calls.update(service=service, region=region_name)
        return object()

    _fake_module(monkeypatch, "boto3", client=client)
    llm = get_llm(Settings.from_env())
    assert llm.provider == "bedrock" and llm.model == "global.anthropic.claude-sonnet-5-5"
    assert calls == {"service": "bedrock-runtime", "region": "ap-south-1"}


def test_bedrock_converse_parsing(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "y")

    class Client:
        def converse(self, **kw):
            assert kw["messages"][0]["content"] == [{"text": "hi"}]
            return {"output": {"message": {"content": [{"text": '{"a": 1}'}]}},
                    "usage": {"inputTokens": 10, "outputTokens": 5}}

    _fake_module(monkeypatch, "boto3", client=lambda *a, **k: Client())
    res = get_llm(Settings.from_env()).complete("sys", [{"role": "user", "content": "hi"}])
    assert (res.text, res.input_tokens, res.output_tokens) == ('{"a": 1}', 10, 5)


@pytest.mark.parametrize("provider,var", [("anthropic", "ANTHROPIC_API_KEY"), ("openai", "OPENAI_API_KEY"),
                                          ("gemini", "GEMINI_API_KEY"), ("bedrock", "AWS_ACCESS_KEY_ID")])
def test_missing_key_clear_error_without_secrets(monkeypatch, provider, var):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv("COMPOSIO_API_KEY", SECRET)  # an unrelated secret must not leak either
    with pytest.raises(ConfigError) as exc:
        get_llm(Settings.from_env())
    assert var in str(exc.value)
    assert SECRET not in str(exc.value)


def test_unknown_provider_rejected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "llama")
    with pytest.raises(ConfigError, match="LLM_PROVIDER must be one of"):
        Settings.from_env()


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Sure!\n```json\n{"a": {"b": 2}}\n```') == {"a": {"b": 2}}
    assert extract_json('prefix {"a": 1} suffix {"b": 2}') == {"a": 1}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_cost_estimate(monkeypatch):
    monkeypatch.setenv("LLM_PRICE_IN_PER_MTOK", "3")
    monkeypatch.setenv("LLM_PRICE_OUT_PER_MTOK", "15")
    assert Settings.from_env().est_cost(1_000_000, 100_000) == 4.5
    assert Settings().est_cost(10, 10) is None
