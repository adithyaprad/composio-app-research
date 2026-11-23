import types

import httpx
import pytest

from agent.config import Settings
from agent.tools import Fallback, ProviderUnavailable, get_fetch, get_search
from agent.tools.composio_tools import ComposioFetch, ComposioSearch
from agent.tools.keyless import DDGSSearch, HttpFetch


class FakeSession:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = []

    def execute(self, slug, arguments=None):
        self.calls.append((slug, arguments))
        return self.behaviour(slug, arguments)


class FakeComposio:
    """Stands in for composio.Composio: sessions.create(...) and tools.get_raw_composio_tool_by_slug(...)."""

    def __init__(self, behaviour, props=None):
        self.session = FakeSession(behaviour)
        self.created = []
        self.sessions = types.SimpleNamespace(create=self._create)
        self.tools = types.SimpleNamespace(
            get_raw_composio_tool_by_slug=lambda slug: types.SimpleNamespace(
                input_parameters={"properties": props or {}}))

    def _create(self, user_id, toolkits):
        self.created.append(toolkits)
        return self.session


def _resp(data=None, result_type="completed", error=None):
    return types.SimpleNamespace(data=data, result_type=result_type, error=error)


def _no_connection(slug, args):
    raise RuntimeError("No active connection found for toolkit(s) 'exa'")


@pytest.fixture
def fake_ddgs(monkeypatch):
    calls = []

    class DDGS:
        def text(self, query, max_results, backend):
            calls.append(backend)
            if backend != "yahoo":
                raise RuntimeError("No results found.")
            return [{"href": "https://docs.example.com", "title": "Docs", "body": "b"}]

    import ddgs

    monkeypatch.setattr(ddgs, "DDGS", DDGS)
    return calls


def test_composio_search_parses_results():
    client = FakeComposio(lambda s, a: _resp({"data": {"results": [{"url": "https://a.dev", "title": "A"}]}}),
                          props={"query": {}, "numResults": {}})
    name, results = ComposioSearch("EXA_SEARCH", "u", client).search("q", 3)
    assert name == "composio_exa" and results[0].url == "https://a.dev"
    assert client.created == [["exa"]]
    assert client.session.calls == [("EXA_SEARCH", {"query": "q", "numResults": 3})]


def test_composio_arg_names_resolved_from_schema():
    client = FakeComposio(lambda s, a: _resp({"results": []}), props={"query": {}, "num_results": {}})
    ComposioSearch("EXA_SEARCH", "u", client).search("q", 2)
    assert client.session.calls[0][1] == {"query": "q", "num_results": 2}


def test_composio_failed_result_with_410_is_unavailable():
    client = FakeComposio(lambda s, a: _resp(result_type="failed", error="HTTP 410 Gone: toolkit retired"))
    with pytest.raises(ProviderUnavailable):
        ComposioSearch("TAVILY_SEARCH", "u", client).search("q")


def test_auto_search_falls_back_once(fake_ddgs, caplog):
    client = FakeComposio(_no_connection)
    search = get_search(Settings(search_provider="auto", ddgs_backends=("duckduckgo", "yahoo")), client)
    assert isinstance(search, Fallback)
    for _ in range(3):
        name, results = search.search("hubspot api")
        assert name == "ddgs:yahoo" and results[0].url == "https://docs.example.com"
    assert len(client.session.calls) == 1  # Composio not retried after the first failure
    assert sum("falling back" in r.message for r in caplog.records) == 1
    assert fake_ddgs == ["duckduckgo", "yahoo"] * 3  # backend list falls through to yahoo


def test_auto_without_composio_key_uses_keyless():
    assert isinstance(get_search(Settings()), DDGSSearch)
    assert isinstance(get_fetch(Settings()), HttpFetch)


def test_ddgs_all_backends_empty(fake_ddgs):
    assert DDGSSearch(("bing",)).search("q") == ("ddgs", [])


def _http_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_auto_fetch_falls_back_to_http():
    def handler(req):
        if req.url.path == "/old":
            return httpx.Response(301, headers={"location": "https://docs.example.com/new"})
        html = "<html><body><article><h1>Auth</h1><p>" + "Use an API key in the header. " * 20
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html + "</p></article></body></html>")

    client = FakeComposio(_no_connection)
    fetch = Fallback(ComposioFetch("u", 5000, client), HttpFetch(client=_http_client(handler)), "fetch")
    page = fetch.fetch("https://docs.example.com/old")
    assert page.provider == "http" and page.status == 200 and page.ok
    assert page.final_url == "https://docs.example.com/new"
    assert "API key" in page.text
    assert page.meta()["text_len"] == len(page.text) and "text" not in page.meta()


def test_firecrawl_payload_parsed():
    data = {"data": {"markdown": "# Hello", "metadata": {"sourceURL": "https://x.dev/final", "statusCode": 200}}}
    client = FakeComposio(lambda s, a: _resp(data), props={"url": {}, "formats": {}, "onlyMainContent": {}})
    page = ComposioFetch("u", 5000, client).fetch("https://x.dev")
    assert (page.text, page.final_url, page.status) == ("# Hello", "https://x.dev/final", 200)
    assert client.session.calls[0][1] == {"url": "https://x.dev", "formats": ["markdown"], "onlyMainContent": True}


def test_http_fetch_network_error_recorded():
    def handler(req):
        raise httpx.ConnectError("boom")

    page = HttpFetch(client=_http_client(handler)).fetch("https://down.example.com")
    assert not page.ok and page.status == 0 and "ConnectError" in page.error
