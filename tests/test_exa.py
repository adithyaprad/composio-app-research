"""Direct Exa provider: parsing, shared text cache, cost tracking, budget cap and fallbacks. Offline only."""

import json
import logging

import httpx
import pytest

from agent.config import Settings
from agent.tools import Fallback, ProviderUnavailable, UseFallback, get_fetch, get_search, make_exa
from agent.tools.base import Page, SearchResult
from agent.tools.budget import ExaBudget, current_app
from agent.tools.exa import ExaClient, ExaFetch, ExaSearch
from agent.tools.keyless import DDGSSearch, HttpFetch

DOCS = "https://docs.acmepay.io/api"
AUTH = "https://docs.acmepay.io/api/authentication"
PRICING = "https://acmepay.io/pricing"
TEXT = "Acme Pay REST API. Authenticate with OAuth 2.0 or a secret API key. " * 40


class ExaStub:
    """MockTransport handler that records requests and answers per endpoint."""

    def __init__(self, search=None, contents=None):
        self.calls: list[tuple[str, dict]] = []
        self.search = search or (lambda body: (200, {
            "results": [
                {"url": DOCS, "title": "Acme Pay API", "text": TEXT, "highlights": ["OAuth 2.0 or API key"]},
                {"url": AUTH, "title": "Authentication", "text": TEXT},
                {"url": "https://acmepay.io/blog/no-text", "title": "Blog"},
            ],
            "costDollars": {"total": 0.007, "search": {"neural": 0.007}},
        }))
        self.contents = contents or (lambda body: (200, {
            "results": [{"url": body["urls"][0], "title": "Pricing", "text": TEXT}],
            "statuses": [{"id": body["urls"][0], "status": "success"}],
            "costDollars": {"total": 0.001},
        }))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        endpoint = request.url.path.strip("/")
        self.calls.append((endpoint, body))
        status, data = (self.search if endpoint == "search" else self.contents)(body)
        return httpx.Response(status, json=data)

    def endpoints(self):
        return [e for e, _ in self.calls]


def _client(stub, budget_usd=5.0) -> ExaClient:
    return ExaClient("test-key", ExaBudget(budget_usd), transport=httpx.MockTransport(stub))


class StubFree:
    """Free fallback for both search and fetch."""

    name = "free"

    def __init__(self):
        self.calls = 0

    def search(self, query, max_results=5):
        self.calls += 1
        return "ddgs:yahoo", [SearchResult("https://free.example")]

    def fetch(self, url):
        self.calls += 1
        return Page(url, url, 200, "http", text="free text", method="trafilatura")


# ---- search + cache ----------------------------------------------------------------

def test_search_request_and_parsing():
    stub = ExaStub()
    name, results = ExaSearch(_client(stub)).search("Acme Pay API authentication", 5)
    assert name == "exa"
    assert [r.url for r in results] == [DOCS, AUTH, "https://acmepay.io/blog/no-text"]
    assert results[0].title == "Acme Pay API" and results[0].snippet == "OAuth 2.0 or API key"
    assert results[1].snippet.startswith("Acme Pay REST API")  # no highlight: text excerpt
    (endpoint, body), = stub.calls
    assert endpoint == "search"
    assert body == {"query": "Acme Pay API authentication", "numResults": 5, "type": "auto",
                    "contents": {"text": {"maxCharacters": 20000}}}


def test_search_sends_key_header_only():
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, json={"results": []})

    ExaSearch(ExaClient("test-key", ExaBudget(1), transport=httpx.MockTransport(handler))).search("q")
    assert seen["x-api-key"] == "test-key" and seen["content-type"] == "application/json"


def test_fetch_of_searched_url_uses_cache():
    stub = ExaStub()
    client = _client(stub)
    ExaSearch(client).search("q")
    fetch = ExaFetch(client)
    page = fetch.fetch("https://www.docs.acmepay.io/api/")  # normalized like the rest of the agent
    assert page.ok and page.text == TEXT and page.provider == "exa" and page.method == "exa_search"
    assert page.requested_url == "https://www.docs.acmepay.io/api/" and page.final_url == DOCS
    assert stub.endpoints() == ["search"]  # no second request
    assert client.budget.snapshot()["exa_calls"] == 1


def test_fetch_of_new_url_calls_contents_then_caches():
    stub = ExaStub()
    client = _client(stub)
    fetch = ExaFetch(client)
    page = fetch.fetch(PRICING)
    assert page.ok and page.method == "exa_text" and page.status == 200
    assert stub.calls == [("contents", {"urls": [PRICING], "text": {"maxCharacters": 20000}})]
    again = fetch.fetch(PRICING)
    assert again.method == "exa_cache" and again.text == TEXT
    assert stub.endpoints() == ["contents"]


def test_search_result_without_text_is_fetched():
    stub = ExaStub()
    client = _client(stub)
    ExaSearch(client).search("q")
    ExaFetch(client).fetch("https://acmepay.io/blog/no-text")
    assert stub.endpoints() == ["search", "contents"]


# ---- cost + budget ---------------------------------------------------------------------

def test_reported_cost_summed_per_app_and_run():
    client = _client(ExaStub())
    token = current_app.set("acmepay")
    try:
        ExaSearch(client).search("a")
        ExaSearch(client).search("b")
        ExaFetch(client).fetch(PRICING)
    finally:
        current_app.reset(token)
    snap = client.budget.snapshot()
    assert snap["exa_calls"] == 3 and snap["exa_calls_by_endpoint"] == {"search": 2, "contents": 1}
    assert snap["exa_cost_usd"] == pytest.approx(0.015) and snap["exa_listed_estimate_usd"] == 0
    assert snap["exa_budget_usd"] == 5.0
    assert client.budget.app_stats("acmepay")["exa_cost_usd"] == pytest.approx(0.015)
    assert client.budget.app_stats("other")["exa_calls"] == 0


def test_missing_cost_uses_listed_estimate():
    stub = ExaStub(search=lambda body: (200, {"results": []}))
    client = _client(stub)
    ExaSearch(client).search("q")
    snap = client.budget.snapshot()
    assert snap["exa_cost_usd"] == pytest.approx(0.007) and snap["exa_listed_estimate_usd"] == pytest.approx(0.007)


def test_budget_cap_skips_exa_and_uses_fallback(caplog):
    stub = ExaStub()
    free = StubFree()
    search = Fallback(ExaSearch(_client(stub, budget_usd=0.015)), free, "search")
    with caplog.at_level(logging.WARNING):
        names = [search.search(f"q{i}")[0] for i in range(4)]
    assert names == ["exa", "exa", "ddgs:yahoo", "ddgs:yahoo"]
    assert stub.endpoints() == ["search", "search"] and free.calls == 2
    assert sum("Exa cap" in r.message for r in caplog.records) == 1


def test_budget_zero_disables_exa():
    stub = ExaStub()
    free = StubFree()
    page = Fallback(ExaFetch(_client(stub, budget_usd=0)), free, "fetch").fetch(PRICING)
    assert page.provider == "http" and stub.calls == []


# ---- errors ------------------------------------------------------------------------------

def test_auth_error_disables_exa_for_search_and_fetch(caplog):
    stub = ExaStub(search=lambda b: (401, {"error": "Invalid API key"}),
                   contents=lambda b: (401, {"error": "Invalid API key"}))
    client = _client(stub)
    free = StubFree()
    search = Fallback(ExaSearch(client), free, "search")
    fetch = Fallback(ExaFetch(client), free, "fetch")
    with caplog.at_level(logging.WARNING):
        assert search.search("q")[0] == "ddgs:yahoo"
        assert search.search("q2")[0] == "ddgs:yahoo"
        assert fetch.fetch(PRICING).provider == "http"
    assert len(stub.calls) == 1  # fetch did not send a request either
    assert client.budget.snapshot()["exa_calls"] == 0  # unbilled, reservation released
    assert client.budget._reserved == 0
    assert "test-key" not in caplog.text


def test_single_5xx_falls_back_for_one_call_then_recovers():
    answers = iter([(503, {}), (200, {"results": [{"url": DOCS, "text": TEXT}]})])
    stub = ExaStub(search=lambda b: next(answers))
    free = StubFree()
    search = Fallback(ExaSearch(_client(stub)), free, "search")
    assert search.search("q")[0] == "ddgs:yahoo"
    assert search.search("q")[0] == "exa"
    assert search.disabled_reason is None


def test_repeated_transport_errors_disable_exa():
    def boom(request):
        raise httpx.ConnectError("down")

    client = ExaClient("k", ExaBudget(1), transport=httpx.MockTransport(boom))
    with pytest.raises(UseFallback):
        ExaSearch(client).search("q")
    with pytest.raises(UseFallback):
        ExaSearch(client).search("q")
    with pytest.raises(ProviderUnavailable):
        ExaSearch(client).search("q")
    assert client.budget._reserved == 0


def test_contents_4xx_returns_failed_page_without_disabling():
    stub = ExaStub(contents=lambda b: (400, {"error": "invalid url"}))
    client = _client(stub)
    fetch = Fallback(ExaFetch(client), StubFree(), "fetch")
    page = fetch.fetch("https://docs.acmepay.io/bad")
    assert not page.ok and page.status == 400 and page.method == "exa_error" and page.provider == "exa"
    assert fetch.disabled_reason is None and client.disabled_reason is None


def test_contents_page_404_is_failed_page_but_blocked_page_uses_http():
    def contents(body):
        code = 404 if body["urls"][0].endswith("gone") else 403
        return 200, {"results": [], "statuses": [{"id": body["urls"][0], "status": "error",
                                                  "error": {"tag": "CRAWL_FAILED", "httpStatusCode": code}}]}

    free = StubFree()
    fetch = Fallback(ExaFetch(_client(ExaStub(contents=contents))), free, "fetch")
    gone = fetch.fetch("https://docs.acmepay.io/gone")
    assert gone.status == 404 and gone.method == "exa_error" and free.calls == 0
    blocked = fetch.fetch("https://docs.acmepay.io/blocked")
    assert blocked.provider == "http" and blocked.ok and free.calls == 1


# ---- wiring -------------------------------------------------------------------------------

def test_defaults_follow_exa_key(monkeypatch):
    assert Settings.from_env().search_provider == "ddgs"
    assert Settings.from_env().fetch_provider == "hybrid"
    monkeypatch.setenv("EXA_API_KEY", "k")
    s = Settings.from_env()
    assert (s.search_provider, s.fetch_provider, s.exa_budget_usd) == ("exa", "exa", 5.0)
    monkeypatch.setenv("SEARCH_PROVIDER", "ddgs")
    monkeypatch.setenv("EXA_BUDGET_USD", "0")
    s = Settings.from_env()
    assert s.search_provider == "ddgs" and s.exa_budget_usd == 0


def test_exa_without_key_falls_back_to_free(caplog):
    settings = Settings(search_provider="exa", fetch_provider="exa")
    assert make_exa(settings) is None
    with caplog.at_level(logging.WARNING):
        assert isinstance(get_search(settings), DDGSSearch)
        assert isinstance(get_fetch(settings), HttpFetch)
    assert "EXA_API_KEY is not set" in caplog.text


def test_exa_wiring_shares_one_client(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "k")
    stub = ExaStub()
    settings = Settings.from_env()
    exa = make_exa(settings, ExaBudget(1), transport=httpx.MockTransport(stub))
    search, fetch = get_search(settings, exa=exa), get_fetch(settings, exa=exa)
    assert isinstance(search, Fallback) and isinstance(search.fallback, DDGSSearch)
    assert isinstance(fetch, Fallback) and isinstance(fetch.fallback, HttpFetch)
    search.search("q")
    assert fetch.fetch(AUTH).method == "exa_search"
    assert stub.endpoints() == ["search"]


# ---- run log + summary ----------------------------------------------------------------------

def test_exa_fields_in_log_and_summary(tmp_path):
    from test_research import COVERAGE, _apps, _lines

    from agent.fakes import FakeFetch, FakeLLM, FakeSearch
    from agent.research import Researcher, run

    exa_budget = ExaBudget(5.0)

    class ExaBilledSearch(FakeSearch):
        """Every search for stripe is one Exa /search with a reported cost."""

        def search(self, query, max_results=5):
            if current_app.get() == "stripe":
                exa_budget.reserve("search")
                exa_budget.record("search", 0.005)
            return super().search(query, max_results)

    r = Researcher(Settings(), FakeLLM(), ExaBilledSearch(), FakeFetch(), tmp_path, coverage=COVERAGE,
                   exa_budget=exa_budget)
    summary = run(_apps("stripe", "hubspot"), r, tmp_path, concurrency=2)
    logs = {rec["slug"]: rec for rec in _lines(tmp_path / "log.jsonl")}
    n = logs["stripe"]["search_calls"]
    assert logs["stripe"]["exa_calls"] == n and logs["stripe"]["exa_calls_by_endpoint"] == {"search": n}
    assert logs["stripe"]["exa_cost_usd"] == pytest.approx(0.005 * n)
    assert logs["hubspot"]["exa_calls"] == 0 and logs["hubspot"]["instant_calls"] == 0
    assert summary["exa_calls"] == n and summary["exa_budget_usd"] == 5.0
    assert summary["exa_cost_usd"] == pytest.approx(0.005 * n)
    assert "est_cost_usd" in summary  # still the LLM token estimate
