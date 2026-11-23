"""Pipeline quality: pricing page selection, dedupe across redirects, Page.method, short-name relevance."""

import httpx
import pytest
from test_research import COVERAGE, _apps
from test_tools import _http_client

from agent.config import Settings
from agent.fakes import FakeFetch, FakeLLM, FakeSearch
from agent.prompts import dedupe_pages, render_pages
from agent.research import (
    OFFICIAL,
    Researcher,
    classify_result,
    is_pricing_url,
    pick_urls,
    search_queries,
    token_in_label,
)
from agent.tools.base import Page
from agent.tools.keyless import HttpFetch

# ---- pricing ----------------------------------------------------------------------

APP = {"slug": "acme", "name": "Acme", "start_url": "https://acme.com", "source_hint": "acme.com"}


def test_search_queries_include_pricing_and_access():
    qs = search_queries(APP)
    assert len(qs) == 4
    assert any("pricing plans" in q and "signup" in q for q in qs)
    assert any("developer API access" in q for q in qs)


@pytest.mark.parametrize("url,expected", [
    ("https://acme.com/pricing", True), ("https://acme.com/plans/", True), ("https://acme.com/signup?x=1", True),
    ("https://acme.com/en/free-trial", True), ("https://acme.com/billing.html", True),
    ("https://acme.com/docs/api", False), ("https://acme.com/pricingstrategy-blog", False),
])
def test_is_pricing_url(url, expected):
    assert is_pricing_url(url) is expected


def _docs_results(n):
    return [{"url": f"https://docs.acme.com/api/{i}", "title": "Acme API"} for i in range(n)]


def test_pricing_page_reserved_within_cap():
    searches = [{"query": "Acme API authentication", "results": _docs_results(5)},
                {"query": "Acme developer API access documentation", "results": _docs_results(5)},
                {"query": "Acme pricing plans free trial signup", "results": [
                    {"url": "https://www.g2.com/acme-pricing", "title": "Acme pricing reviews"},
                    {"url": "https://docs.acme.com/api/9", "title": "Acme"},
                    {"url": "https://acme.com/pricing", "title": "Acme Pricing"}]}]
    picked = pick_urls(APP, searches, 4)
    assert len(picked) == 4 and "https://acme.com/pricing" in picked


def test_pricing_page_replaces_lowest_rank_when_full():
    searches = [{"query": "Acme API authentication", "results": _docs_results(6) + [
        {"url": "https://acme.com/signup", "title": "Sign up"}]}]
    picked = pick_urls(APP, searches, 3)
    assert len(picked) == 3 and picked[0] == "https://acme.com" and "https://acme.com/signup" in picked


def test_pricing_synthesized_when_none_found():
    picked = pick_urls(APP, [{"query": "q", "results": _docs_results(8)}], 3)
    assert len(picked) == 4 and picked[-1] == "https://acme.com/pricing"


def test_no_pricing_synthesis_in_follow_up():
    assert pick_urls(APP, [{"query": "q", "results": _docs_results(2)}], 3, include_start=False) == [
        "https://docs.acme.com/api/0", "https://docs.acme.com/api/1"]


# ---- dedupe -----------------------------------------------------------------------

def test_pick_urls_dedupes_normalized():
    searches = [{"query": "q", "results": [{"url": "http://www.acme.com/", "title": "Acme"},
                                           {"url": "https://acme.com/docs#auth", "title": "Acme"},
                                           {"url": "https://acme.com/docs/", "title": "Acme"}]}]
    picked = pick_urls(APP, searches, 10)
    assert picked == ["https://acme.com", "https://acme.com/docs#auth", "https://acme.com/pricing"]


class RedirectFetch(FakeFetch):
    """firecrawl.dev redirects to www.firecrawl.dev/home/ (a different normalized URL than the request)."""

    def __init__(self):
        self.urls = []

    def fetch(self, url):
        self.urls.append(url)
        page = super().fetch(url)
        if url == "https://firecrawl.dev":
            page.final_url = "https://www.firecrawl.dev/home/"
        return page


class RedirectSearch(FakeSearch):
    def search(self, query, max_results=5):
        name, results = super().search(query, max_results)
        from agent.tools.base import SearchResult

        return name, [SearchResult("https://firecrawl.dev/home", "Firecrawl"), *results]


def test_redirect_final_url_not_fetched_again(tmp_path):
    fetch = RedirectFetch()
    app = dict(_apps("firecrawl")[0], start_url="https://firecrawl.dev")
    # pick_urls cannot know about the redirect: both URLs are picked...
    assert "https://firecrawl.dev/home" in pick_urls(app, [{"query": "q", "results": [
        {"url": "https://firecrawl.dev/home", "title": "Firecrawl"}]}], 8)
    out = Researcher(Settings(), FakeLLM(), RedirectSearch(), fetch, tmp_path, coverage=COVERAGE).research(app)
    # ...but the second is skipped at fetch time because it equals the first page's final URL
    assert fetch.urls.count("https://firecrawl.dev") == 1
    assert "https://firecrawl.dev/home" not in fetch.urls
    assert not out.errors


def test_render_pages_dedupes_by_final_url():
    a = Page("https://x.dev", "https://www.x.dev/", 200, "http", text="short", method="trafilatura")
    b = Page("https://www.x.dev", "https://x.dev", 200, "http", text="much longer text", method="trafilatura")
    c = Page("https://x.dev/bad", "https://x.dev/docs", 500, "http", error="HTTP 500", method="http_error")
    d = Page("https://x.dev/docs/", "https://x.dev/docs", 200, "http", text="docs", method="trafilatura")
    assert dedupe_pages([a, b, c, d]) == [b, d]
    rendered = render_pages([a, b, c, d], 1000)
    assert rendered.count("=== PAGE") == 2 and "much longer text" in rendered and "short" not in rendered


# ---- Page.method ------------------------------------------------------------------

def _fetch_with(handler, url):
    return HttpFetch(client=_http_client(handler)).fetch(url)


@pytest.mark.parametrize("status", [403, 404, 429, 500])
def test_http_error_method(status):
    page = _fetch_with(lambda r: httpx.Response(status, headers={"content-type": "text/html"},
                                                text="<html><body><p>Access denied</p></body></html>"),
                       "https://x.dev/a")
    assert page.method == "http_error" and page.text == "" and not page.ok


def test_request_exception_method():
    def boom(req):
        raise httpx.ReadTimeout("slow")

    assert _fetch_with(boom, "https://x.dev/a").method == "http_error"


def test_method_never_blank(tmp_path):
    assert Page("u", "u", 200, "x").method == "unknown"  # default is non-empty even if a caller forgets

    class Exploding(FakeFetch):
        def fetch(self, url):
            if "pricing" in url:
                raise RuntimeError("boom")
            return super().fetch(url)

    out = Researcher(Settings(), FakeLLM(), FakeSearch(), Exploding(), tmp_path, coverage=COVERAGE).research(
        _apps("hubspot")[0])
    methods = [p["method"] for p in out.evidence["pages"]]
    assert methods and all(methods) and "fetch_error" in methods


# ---- short-name relevance -----------------------------------------------------------

def _app(slug, name, domain):
    return {"slug": slug, "name": name, "start_url": f"https://{domain}", "source_hint": domain}


PLAIN = _app("plain", "Plain", "plain.com")
CLOSE = _app("close", "Close", "close.com")
FRONT = _app("front", "Front", "front.com")
FANBASIS = _app("fanbasis", "fanbasis", "fanbasis.com")


@pytest.mark.parametrize("app,url,title", [
    (PLAIN, "https://plainenglish.io/blog/x", "Python tutorials | plainenglish.io"),
    (CLOSE, "https://foreclosure.com/listings", "Foreclosure listings"),
    (CLOSE, "https://disclose.io/docs", "Disclose vulnerability program"),
    (FRONT, "https://storefront.dev/docs/api", "Storefront API docs"),
    (FRONT, "https://github.com/acme/storefront-api", "storefront api"),
    (FANBASIS, "https://3commas.io/docs/mcp", "3Commas MCP server"),
    (FANBASIS, "https://commareports.com/docs/mcp", "MCP server · Comma Docs"),
])
def test_short_names_do_not_match_unrelated(app, url, title):
    assert classify_result(app, {"url": url, "title": title}) is None


@pytest.mark.parametrize("app,url,title", [
    (PLAIN, "https://www.plain.com/docs", ""),
    (PLAIN, "https://app.plain.com/x", ""),
    (PLAIN, "https://useplain.io/x", ""),
    (CLOSE, "https://developer.close.com/", ""),
    (FRONT, "https://dev.frontapp.com/reference", ""),
    (FANBASIS, "https://dev-docs.fanbasis.com/", ""),
])
def test_official_hosts_still_match(app, url, title):
    assert classify_result(app, {"url": url, "title": title}) == OFFICIAL


def test_short_name_doc_host_and_third_party_need_whole_word():
    gh = {"url": "https://github.com/team-plain/mcp-server", "title": "MCP server"}
    assert classify_result(PLAIN, gh) is not None
    blog = {"url": "https://example.org/x", "title": "Explained in plain English"}
    assert classify_result(PLAIN, blog) is not None  # whole word "plain" (third party is capped anyway)
    blog2 = {"url": "https://example.org/x", "title": "Plainly explained"}
    assert classify_result(PLAIN, blog2) is None


def test_token_in_label_rules():
    assert token_in_label("plain", "plain")
    assert token_in_label("plain", "team-plain")
    assert token_in_label("clay", "getclay")
    assert token_in_label("front", "frontapp")
    assert not token_in_label("plain", "plainenglish")
    assert not token_in_label("front", "storefront")
    assert not token_in_label("close", "foreclosure")
    assert token_in_label("fanbasis", "fanbasisapp")  # long tokens may prefix a label
    assert not token_in_label("fanbasis", "3commas")
