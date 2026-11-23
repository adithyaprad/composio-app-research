"""Pipeline quality: pricing page selection, dedupe across redirects, Page.method, short-name relevance."""

import json

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
    mcp_links,
    official_host,
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
    assert qs[:4] == ["Acme API authentication", "Acme developer API access documentation",
                      "Acme pricing plans free trial signup", "Acme official MCP server"]
    assert qs[4:] == ["site:acme.com MCP"]  # root host: no parent query


def test_search_queries_site_mcp_on_subdomain_and_parent():
    app = dict(APP, start_url="https://docs.acme.com/api")
    qs = search_queries(app)
    assert "site:docs.acme.com MCP" in qs and "site:acme.com MCP" in qs and len(qs) <= 6


def test_search_queries_no_site_query_on_shared_doc_host():
    qs = search_queries(dict(APP, start_url="https://github.com/acme/cli"))
    assert not any(q.startswith("site:") for q in qs)
    # a vendor subdomain of a doc host is still searched, but not the shared parent
    qs = search_queries(dict(APP, start_url="https://acme.stoplight.io/docs"))
    assert "site:acme.stoplight.io MCP" in qs and "site:stoplight.io MCP" not in qs


BLANK_START = {"id": 84, "slug": "paygent-connect", "name": "Paygent Connect", "category": "finance_and_fintech",
               "category_label": "Finance and Fintech", "source_hint": "paygent (NMI-powered)", "start_url": ""}


def test_search_queries_blank_start_url_use_hint_and_category():
    qs = search_queries(BLANK_START)
    assert len(qs) == 4  # no official host known: no site: query
    for q in qs:
        assert '"Paygent Connect"' in q and "NMI" in q and "Finance" in q and "Fintech" in q
    assert not any(" and " in q or "powered" in q for q in qs)


def test_host_like_hint_is_a_domain_hint():
    app = dict(BLANK_START, source_hint="gopaygent.com (NMI-powered)")
    assert official_host(app) == "gopaygent.com"
    assert "site:gopaygent.com MCP" in search_queries(app)
    assert classify_result(app, {"url": "https://docs.gopaygent.com/api", "title": ""}) == OFFICIAL
    assert official_host(dict(BLANK_START, source_hint="github.com/foo/bar")) == "github.com"
    assert official_host(BLANK_START) == ""  # "paygent (NMI-powered)" has no host


def test_start_url_queries_unchanged_by_hint():
    app = dict(BLANK_START, start_url="https://gopaygent.com")
    assert search_queries(app)[0] == "Paygent Connect API authentication"


def test_research_prompt_shows_category_label():
    from agent.prompts import research_prompt

    assert '"category_label": "Finance and Fintech"' in research_prompt(BLANK_START, [], 100, False)


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


# ---- llms.txt before settling on mcp_status none -----------------------------------

def _long(url, text="Long docs page. " * 200):
    return Page(url, url, 200, "fake", text=text, method="fake")


def test_mcp_llms_txt_proposed_without_thin_pages():
    pages = [_long("https://docs.acme.com/api/auth"), _long("https://acme.com/pricing")]
    row = {"mcp_status": "none", "docs_url": "https://docs.acme.com/api"}
    assert Researcher.mcp_llms_txt_urls(APP, pages, row) == ["https://docs.acme.com/llms.txt",
                                                            "https://acme.com/llms.txt"]
    assert Researcher.mcp_llms_txt_urls(APP, pages, None) == ["https://acme.com/llms.txt",
                                                             "https://docs.acme.com/llms.txt"]


def test_mcp_llms_txt_not_proposed_when_resolved_or_off_domain():
    pages = [_long("https://docs.acme.com/api/auth")]
    assert Researcher.mcp_llms_txt_urls(APP, pages, {"mcp_status": "official", "docs_url": None}) == []
    with_mcp = [*pages, _long("https://docs.acme.com/mcp")]
    assert Researcher.mcp_llms_txt_urls(APP, with_mcp, {"mcp_status": "none", "docs_url": None}) == []
    other = {"slug": "acme", "name": "Acme", "start_url": "", "source_hint": "acme"}
    assert Researcher.mcp_llms_txt_urls(other, [_long("https://blog.example.org/docs/acme")],
                                        {"mcp_status": "none", "docs_url": "https://blog.example.org/docs"}) == []


def test_mcp_links_from_llms_txt():
    page = _long("https://docs.acme.com/llms.txt", "# Acme\n- [Auth](/api/auth.md)\n- [MCP server](/mcp/setup.md)\n"
                 "- [Remote MCP](https://mcp.acme.com/sse)\n- [Ref](https://docs.acme.com/api)\n"
                 "- [Again](https://docs.acme.com/mcp/setup.md)\n- [Third](https://acme.com/mcp-3)")
    assert mcp_links(page) == ["https://docs.acme.com/mcp/setup.md", "https://mcp.acme.com/sse"]


class LlmsTxtFetch(FakeFetch):
    """Non-thin pages; llms.txt links an MCP docs page that search never surfaced."""

    def __init__(self, llms_text):
        self.urls, self.llms_text = [], llms_text

    def fetch(self, url):
        self.urls.append(url)
        page = super().fetch(url)
        page.text += " Filler." * 300
        if url.endswith("/llms.txt"):
            page.text = self.llms_text
        if "/mcp" in url:
            page.text = "Connect your agent to the Stripe MCP server at https://mcp.stripe.example.com. " * 30
        return page


class LabelLLM(FakeLLM):
    def __init__(self):
        self.prompts = []

    def complete(self, system, messages, json_mode=True):
        self.prompts.append(messages[-1]["content"])
        return super().complete(system, messages, json_mode)


def test_llms_txt_mcp_link_fetched_and_llm_asked_again(tmp_path):
    fetch = LlmsTxtFetch("# Stripe docs\n- [API](https://stripe.com/docs/api)\n- [MCP](https://stripe.com/docs/mcp)\n")
    llm = LabelLLM()
    out = Researcher(Settings(), llm, FakeSearch(), fetch, tmp_path, coverage=COVERAGE).research(_apps("stripe")[0])
    assert fetch.urls.count("https://stripe.com/llms.txt") == 1 and "https://stripe.com/docs/mcp" in fetch.urls
    assert out.stats.llm_calls == 2 and "URL: https://stripe.com/docs/mcp" in llm.prompts[1]
    labels = [a["label"] for a in json.loads((tmp_path / "cache" / "stripe.llm.json").read_text())["attempts"]]
    assert labels == ["initial", "mcp_check"] and not out.errors


def test_llms_txt_without_mcp_costs_no_extra_llm_call(tmp_path):
    fetch = LlmsTxtFetch("# Stripe docs\n- [API](https://stripe.com/docs/api)\n")
    out = Researcher(Settings(), FakeLLM(), FakeSearch(), fetch, tmp_path, coverage=COVERAGE).research(
        _apps("stripe")[0])
    assert "https://stripe.com/llms.txt" in fetch.urls and out.stats.llm_calls == 1


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
