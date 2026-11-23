import types

import httpx
import pytest

from agent.config import ConfigError, Settings
from agent.tools import Fallback, HybridFetch, ProviderUnavailable, get_fetch, get_search, keyless
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

    def _create(self, user_id, toolkits, **kwargs):
        self.created.append(toolkits)
        self.create_kwargs = kwargs
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
    assert client.create_kwargs == {"instant": {"return_instant_charge": True}}
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
    assert sum("unavailable, using" in r.message for r in caplog.records) == 1
    assert fake_ddgs == ["duckduckgo", "yahoo"] * 3  # backend list falls through to yahoo


def test_defaults_without_composio_key_are_free():
    assert Settings.from_env().search_provider == "ddgs"
    assert Settings.from_env().fetch_provider == "hybrid"
    assert isinstance(get_search(Settings()), DDGSSearch)
    fetch = get_fetch(Settings())
    assert isinstance(fetch, HybridFetch) and fetch.paid is None  # no key: never escalates
    assert isinstance(get_search(Settings(search_provider="auto")), DDGSSearch)
    assert isinstance(get_fetch(Settings(fetch_provider="auto")), HttpFetch)


def test_explicit_composio_without_key_is_config_error():
    with pytest.raises(ConfigError, match="COMPOSIO_API_KEY"):
        get_search(Settings(search_provider="composio_exa"))


def test_use_composio_exa_flag(monkeypatch):
    monkeypatch.setenv("USE_COMPOSIO_EXA", "true")
    assert Settings.from_env().search_provider == "auto"


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


# ---- thin-page extraction -------------------------------------------------------

JS_SHELL = (
    "<html><head><title>API overview</title><script>window.__NEXT_DATA__ = {big: 'state'};</script>"
    "<style>body{color:red}</style></head><body><div id='root'><nav>Docs Guides Reference</nav>"
    "<p>Loading...</p></div></body></html>"
)
NAV_HEAVY = "<html><body>" + "".join(f"<div><a href='/x{i}'>Endpoint number {i} reference</a></div>"
                                     for i in range(120)) + "<script>var x = 'SCRIPT_TEXT';</script></body></html>"
ARTICLE = ("<html><body><article><h1>Authentication</h1>"
           + "".join(f"<p>Rule {i}: request number {i} must include an API key in the Authorization header.</p>"
                     for i in range(30))
           + "</article></body></html>")
MARKDOWN = "# API overview\n\n" + "Authenticate with OAuth 2.0 or a private app access token. " * 40


def _site(routes):
    def handler(req):
        body, ctype = routes.get(str(req.url), ("not found", "text/plain"))
        status = 404 if body == "not found" else 200
        return httpx.Response(status, headers={"content-type": ctype}, text=body)
    return HttpFetch(client=_http_client(handler))


def test_rich_page_uses_trafilatura():
    page = _site({"https://docs.x.dev/auth": (ARTICLE, "text/html")}).fetch("https://docs.x.dev/auth")
    assert page.method == "trafilatura" and len(page.text) >= 1500 and page.text_source_url is None


def test_thin_docs_page_uses_markdown_twin():
    f = _site({"https://developers.x.com/docs/api/overview": (JS_SHELL, "text/html"),
               "https://developers.x.com/docs/api/overview.md": (MARKDOWN, "text/markdown")})
    page = f.fetch("https://developers.x.com/docs/api/overview")
    assert page.method == "md_twin" and page.text.startswith("# API overview")
    assert page.text_source_url == "https://developers.x.com/docs/api/overview.md"
    assert page.final_url == "https://developers.x.com/docs/api/overview"
    assert page.meta()["method"] == "md_twin"


def test_thin_page_without_twin_falls_back_to_visible_text(monkeypatch):
    # Simulate trafilatura rejecting the page (as on JS-rendered docs) so the fallback chain is exercised.
    monkeypatch.setattr(keyless, "_trafilatura", lambda html, url, recall: "short" if recall else "")
    f = _site({"https://docs.x.dev/ref": (NAV_HEAVY, "text/html")})  # .md twin 404s
    page = f.fetch("https://docs.x.dev/ref")
    assert page.method == "visible_text"
    assert "Endpoint number 119" in page.text and "SCRIPT_TEXT" not in page.text


def test_thin_page_keeps_longest_when_all_thin(monkeypatch):
    monkeypatch.setattr(keyless, "_trafilatura", lambda html, url, recall: "x" * (900 if recall else 300))
    page = _site({"https://www.x.com/p": (JS_SHELL, "text/html")}).fetch("https://www.x.com/p")
    assert page.method == "trafilatura_recall" and len(page.text) == 900


def test_md_twin_returning_html_is_ignored():
    f = _site({"https://docs.x.dev/a": (JS_SHELL, "text/html"), "https://docs.x.dev/a.md": (JS_SHELL, "text/html")})
    page = f.fetch("https://docs.x.dev/a")
    assert page.method != "md_twin" and "window.__NEXT_DATA__" not in page.text


def test_non_docs_host_skips_twin():
    calls = []

    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(200, headers={"content-type": "text/html"}, text=JS_SHELL)

    HttpFetch(client=_http_client(handler)).fetch("https://www.x.com/pricing")
    assert calls == ["https://www.x.com/pricing"]


def test_plain_text_is_raw():
    page = _site({"https://docs.x.dev/llms.txt": ("# Index\n- a", "text/plain")}).fetch("https://docs.x.dev/llms.txt")
    assert page.method == "raw" and page.text == "# Index\n- a"
