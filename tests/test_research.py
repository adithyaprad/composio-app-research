import json

from agent.config import APPS_CSV, COVERAGE_CSV, Settings
from agent.fakes import FakeFetch, FakeLLM, FakeSearch
from agent.llm.base import LLMResult
from agent.research import Researcher, classify_result, gap_fill_urls, load_apps, load_coverage, pick_urls, run
from agent.tools.base import SearchResult
from agent.validate import normalize_url, validate_row

APPS = load_apps(APPS_CSV)
COVERAGE = load_coverage(COVERAGE_CSV)


def _apps(*slugs):
    return [a for a in APPS if a["slug"] in slugs]


def _researcher(tmp_path, llm=None, **kw):
    return Researcher(Settings(), llm or FakeLLM(), FakeSearch(), FakeFetch(), tmp_path, coverage=COVERAGE, **kw)


def _lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def test_coverage_csv_mapping():
    assert COVERAGE["zoho-crm"] == {"status": "none", "toolkit_slug": None, "sibling_slugs": [],
                                    "checked_at": "2026-11-23T10:41:38Z", "evidence_url": None}
    assert COVERAGE["close"]["sibling_slugs"] == ["close_mcp"]


def test_dry_run_end_to_end(tmp_path):
    summary = run(_apps("hubspot", "zoho-crm"), _researcher(tmp_path), tmp_path, concurrency=2)
    assert summary["ok"] == 2 and summary["failed_validation"] == 0
    rows = {r["slug"]: r for r in _lines(tmp_path / "rows.jsonl")}
    for r in rows.values():
        assert validate_row(r) == []
    assert rows["zoho-crm"]["composio_toolkit"]["status"] == "none"
    ev = json.loads((tmp_path / "evidence" / "hubspot.json").read_text())
    fetched = {p["final_url"] for p in ev["pages"]}
    assert all(i["url"] in fetched for items in rows["hubspot"]["evidence"].values() for i in items)
    assert ev["checks"]["dropped_evidence"] == [] and ev["checks"]["unverified_snippets"] == []
    assert {"sha256", "text_len", "fetched_at", "status"} <= set(ev["pages"][0])
    log = _lines(tmp_path / "log.jsonl")
    assert {rec["slug"] for rec in log} == {"hubspot", "zoho-crm"}
    assert all(rec["llm_calls"] == 1 and rec["fetch_calls"] >= 1 for rec in log)


def test_resume_skips_done_apps(tmp_path):
    run(_apps("hubspot"), _researcher(tmp_path), tmp_path)

    class CountingLLM(FakeLLM):
        calls = 0

        def complete(self, *a, **k):
            CountingLLM.calls += 1
            return super().complete(*a, **k)

    summary = run(_apps("hubspot", "stripe"), _researcher(tmp_path, CountingLLM()), tmp_path)
    assert summary["skipped_done"] == 1 and summary["ok"] == 1
    assert CountingLLM.calls == 1  # only stripe researched
    assert [r["slug"] for r in _lines(tmp_path / "rows.jsonl")] == ["hubspot", "stripe"]


class HallucinatingLLM(FakeLLM):
    """First answer cites an unfetched URL for auth; the retry answer is clean."""

    def __init__(self, fix_on_retry=True):
        self.calls = []
        self.fix_on_retry = fix_on_retry

    def complete(self, system, messages, json_mode=True):
        self.calls.append(messages)
        res = super().complete(system, messages, json_mode)
        if len(self.calls) == 1 or not self.fix_on_retry:
            out = json.loads(res.text)
            bogus = {"url": "https://invented.example.com/auth", "source_type": "official_docs"}
            out["row"]["evidence"]["auth"] = [bogus]
            return LLMResult(json.dumps(out), res.input_tokens, res.output_tokens)
        return res


def test_unfetched_evidence_triggers_retry_then_passes(tmp_path):
    llm = HallucinatingLLM()
    summary = run(_apps("stripe"), _researcher(tmp_path, llm), tmp_path)
    assert summary["ok"] == 1 and len(llm.calls) == 2
    assert "invented.example.com" in llm.calls[1][-1]["content"]  # errors fed back
    ev = json.loads((tmp_path / "evidence" / "stripe.json").read_text())
    assert ev["checks"]["retried"] is True


def test_still_invalid_goes_to_failures(tmp_path):
    summary = run(_apps("stripe"), _researcher(tmp_path, HallucinatingLLM(fix_on_retry=False)), tmp_path)
    assert summary["failed_validation"] == 1 and summary["ok"] == 0
    fail = _lines(tmp_path / "failures.jsonl")[0]
    assert fail["slug"] == "stripe" and any("evidence/auth" in e for e in fail["errors"])
    assert not (tmp_path / "rows.jsonl").exists()


def test_follow_up_round_fetches_more(tmp_path):
    class FollowUpLLM(FakeLLM):
        n = 0

        def complete(self, system, messages, json_mode=True):
            FollowUpLLM.n += 1
            res = super().complete(system, messages, json_mode)
            if FollowUpLLM.n == 1:
                out = json.loads(res.text)
                out["follow_up"] = {"queries": ["Stripe OAuth"], "urls": ["https://stripe.com/docs/connect"]}
                return LLMResult(json.dumps(out))
            return res

    r = _researcher(tmp_path, FollowUpLLM(), max_pages=2)
    out = r.research(_apps("stripe")[0])
    urls = [p["requested_url"] for p in out.evidence["pages"]]
    assert "https://stripe.com/docs/connect" in urls and len(urls) <= 2 + 3
    assert out.stats.llm_calls == 2 and not out.errors


FANBASIS = {"slug": "fanbasis", "name": "fanbasis", "source_hint": "fanbasis.com", "start_url": "https://fanbasis.com"}
# Real ddgs results from smoke-1 (shortened).
FANBASIS_SEARCHES = [
    {"query": "fanbasis API authentication", "results": [
        {"url": "https://www.bing.com/aclick?ld=abc", "title": "Spot Unauthorized API Access"},
        {"url": "https://dev-docs.fanbasis.com/", "title": "Login - FanBasis API Docsv3"},
        {"url": "https://github.com/FanBasis/fbnewapi", "title": "GitHub - FanBasis/fbnewapi"},
        {"url": "https://apidocs.fan/", "title": "Commas API Reference"}]},
    {"query": "fanbasis developer API access pricing", "results": [
        {"url": "https://legal.fanvue.com/api-policy", "title": "API Access & Usage Policy | Fanvue"},
        {"url": "https://www.plan.fanbase.io/", "title": "Fanbase Plans — APIs and Widgets"},
        {"url": "https://demo.embedded.fanbasis.io/docs/getting-started", "title": "Vite + React + TS"}]},
    {"query": "fanbasis MCP server", "results": [
        {"url": "https://mcpservers.org/", "title": "Awesome MCP Servers"},
        {"url": "https://supabase.com/blog/mcp-server", "title": "Supabase MCP Server"},
        {"url": "https://learn.microsoft.com/en-us/rest/api/fabric/articles/mcp-servers/fabric-mcp-servers-list",
         "title": "Fabric MCP Servers overview"},
        {"url": "https://glama.ai/mcp/servers/fanbasis-mcp", "title": "FanBasis MCP server", "snippet": ""},
        {"url": "https://mcp.so/server/x", "title": "Payments MCP", "snippet": "Unofficial FanBasis server"},
        {"url": "https://smithery.ai/server/y", "title": "fanbasis tools"}]},
    {"query": "Commas MCP server beta documentation", "results": [
        {"url": "https://commareports.com/docs/mcp", "title": "MCP server · Comma Docs"},
        {"url": "https://trade.3commas.io/docs/mcp", "title": "MCP Server for Crypto Trading Automation - 3Commas"},
        {"url": "https://developers.webflow.com/mcp/installing/beta", "title": "Beta server | Webflow"},
        {"url": "https://github.com/supabase/mcp", "title": "Supabase MCP"}]},
]


def test_relevance_filter_drops_unrelated_vendors():
    picked = pick_urls(FANBASIS, FANBASIS_SEARCHES, 20)
    assert picked[0] == "https://fanbasis.com"
    for bad in ("bing.com", "fanvue", "fanbase.io", "apidocs.fan", "supabase", "microsoft", "commareports",
                "3commas", "webflow", "https://mcpservers.org/"):
        assert not any(bad in u for u in picked), bad
    assert "https://dev-docs.fanbasis.com/" in picked
    assert "https://demo.embedded.fanbasis.io/docs/getting-started" in picked  # name token in host
    assert "https://github.com/FanBasis/fbnewapi" in picked  # doc host + name in URL
    third_party = [u for u in picked if any(d in u for d in ("glama.ai", "mcp.so", "smithery.ai"))]
    assert len(third_party) == 2  # capped, and only those naming the app


def test_relevance_ranks_official_first():
    picked = pick_urls(FANBASIS, FANBASIS_SEARCHES, 20)
    tiers = ["official" if "fanbasis.com" in u or "fanbasis.io" in u else "other" for u in picked]
    # within each query the best pick is official when one exists; all officials come before the leftovers
    assert tiers[:3] == ["official"] * 3
    assert picked.index("https://github.com/FanBasis/fbnewapi") > picked.index("https://dev-docs.fanbasis.com/")


def test_relevance_short_tld_not_matched():
    assert classify_result(FANBASIS, {"url": "https://apidocs.fan/", "title": "Commas API Reference"}) is None


def test_llms_txt_fetched_once_for_thin_docs_page(tmp_path):
    class ThinFetch(FakeFetch):
        def __init__(self):
            self.urls = []

        def fetch(self, url):
            self.urls.append(url)
            page = super().fetch(url)
            if url.endswith("/llms.txt"):
                page.text = "# Docs index\n- [Auth](https://docs.stripe.example.com/auth.md)\n" * 50
            return page

    fetch = ThinFetch()
    r = Researcher(Settings(), FakeLLM(), FakeSearch(), fetch, tmp_path, coverage=COVERAGE)
    out = r.research(_apps("stripe")[0])
    llms = [u for u in fetch.urls if u.endswith("/llms.txt")]
    assert llms == ["https://stripe.com/llms.txt"]  # start_url stripe.com/docs/api is the first thin docs page
    assert not out.errors


def test_pick_urls_prefers_start_url_and_official():
    app = {"start_url": "https://stripe.com", "source_hint": "stripe.com"}
    searches = [{"query": "Stripe API authentication", "results": [
        {"url": "https://blog.random.io/stripe"}, {"url": "https://docs.stripe.com/keys"}]}]
    # no pricing page in results -> <origin>/pricing synthesized as one extra page
    assert pick_urls(app, searches, 2) == ["https://stripe.com", "https://docs.stripe.com/keys",
                                          "https://stripe.com/pricing"]


# ---- gap-fill: leftover official MCP / auth search hits ----------------------------

ACME_PAY = {"id": 99, "slug": "acme-pay", "name": "Acme Pay", "category": "finance_and_fintech",
            "category_label": "Finance and Fintech", "source_hint": "acmepay.com", "start_url": "https://acmepay.com"}


def _hits(*pairs):
    return [{"query": "q", "results": [{"url": u, "title": t, "snippet": ""} for u, t in pairs]}]


class ScriptedSearch:
    """Fixed results per query keyword; no network."""

    name = "fake"

    def __init__(self, by_keyword):
        self.by_keyword = by_keyword

    def search(self, query, max_results=5):
        for kw, hits in self.by_keyword.items():
            if kw in query:
                return self.name, [SearchResult(u, t) for u, t in hits][:max_results]
        return self.name, []


class RecordingFetch(FakeFetch):
    def __init__(self, fail=()):
        self.urls, self.fail = [], fail

    def fetch(self, url):
        self.urls.append(url)
        page = super().fetch(url)
        if url in self.fail:
            page.status, page.text = 404, ""
        return page


class LabelLLM(FakeLLM):
    def __init__(self):
        self.prompts = []

    def complete(self, system, messages, json_mode=True):
        self.prompts.append(messages[-1]["content"])
        return super().complete(system, messages, json_mode)


ACME_SEARCH = {
    "authentication": [("https://docs.acmepay.com/api/reference", "API reference"),
                       ("https://docs.acmepay.com/oauth", "OAuth")],
    "MCP": [("https://docs.acmepay.com/mcp", "MCP server"),
            ("https://github.com/acmepay/mcp-server", "acmepay/mcp-server"),
            ("https://mcp.so/server/acmepay", "Acme Pay MCP")],
    "pricing": [("https://acmepay.com/pricing", "Pricing")],
}


def _acme_run(tmp_path, search, fetch=None, max_pages=1):
    fetch = fetch or RecordingFetch()
    llm = LabelLLM()
    r = Researcher(Settings(), llm, ScriptedSearch(search), fetch, tmp_path, coverage=COVERAGE,
                   max_pages=max_pages, follow_up=False)
    out = r.research(ACME_PAY)
    labels = [a["label"] for a in json.loads((tmp_path / "cache" / "acme-pay.llm.json").read_text())["attempts"]]
    return out, fetch, llm, labels


def test_gap_fill_fetches_leftover_official_hits_and_reasks_once(tmp_path):
    out, fetch, llm, labels = _acme_run(tmp_path, ACME_SEARCH)
    # max_pages=1: the cap holds start_url (+ the pricing page); the MCP/OAuth hits were left over
    assert fetch.urls[:2] == ["https://acmepay.com", "https://acmepay.com/pricing"]
    assert fetch.urls[2:5] == ["https://docs.acmepay.com/mcp", "https://docs.acmepay.com/oauth",
                               "https://github.com/acmepay/mcp-server"]
    assert "https://mcp.so/server/acmepay" not in fetch.urls  # third-party directory is not vendor-owned
    assert not any(u.endswith("/llms.txt") for u in fetch.urls)  # an MCP page was fetched: llms.txt skipped
    assert labels == ["initial", "gap_fill"] and out.stats.llm_calls == 2 and not out.errors
    assert "URL: https://docs.acmepay.com/oauth" in llm.prompts[1]


def test_gap_fill_no_candidates_no_extra_fetch_or_llm_call(tmp_path):
    search = {"authentication": [("https://docs.acmepay.com/api/reference", "API reference")],
              "MCP": [("https://mcp.so/server/acmepay", "Acme Pay MCP")]}
    out, fetch, _, labels = _acme_run(tmp_path, search)
    assert "https://mcp.so/server/acmepay" not in fetch.urls
    assert "https://docs.acmepay.com/api/reference" not in fetch.urls
    assert labels == ["initial"] and out.stats.llm_calls == 1


def test_gap_fill_without_usable_text_adds_no_llm_call(tmp_path):
    search = {"authentication": [("https://docs.acmepay.com/oauth", "OAuth")]}
    fetch = RecordingFetch(fail={"https://docs.acmepay.com/oauth"})
    _, fetch, _, labels = _acme_run(tmp_path, search, fetch)
    assert "https://docs.acmepay.com/oauth" in fetch.urls and labels == ["initial"]


def test_gap_fill_title_only_match():
    s = _hits(("https://docs.acmepay.com/guides/clients", "OAuth 2.0 for API clients"),
              ("https://docs.acmepay.com/guides/webhooks", "Webhooks"))
    assert gap_fill_urls(ACME_PAY, s, set(), None) == ["https://docs.acmepay.com/guides/clients"]


def test_gap_fill_sibling_hosts_and_vendor_repos():
    s = _hits(("https://developers.acmepay.io/authentication", "Authentication"),
              ("https://acmepay.readme.io/docs/mcp", "MCP"),
              ("https://github.com/acmepay/mcp-server", "MCP server"),
              ("https://github.com/someone-else/acmepay-mcp", "Acme Pay MCP"),
              ("https://readme.io/acmepay/oauth", "OAuth"),
              ("https://acmepayments-blog.com/oauth", "OAuth"))
    assert gap_fill_urls(ACME_PAY, s, set(), None) == ["https://acmepay.readme.io/docs/mcp",
                                                       "https://github.com/acmepay/mcp-server",
                                                       "https://developers.acmepay.io/authentication"]


def test_gap_fill_order_cap_and_login_pages():
    s = _hits(("https://developers.acmepay.io/mcp", "MCP"),
              ("https://acmepay.com/login", "Log in to Acme Pay"),
              ("https://docs.acmepay.com/authentication", "Authentication"),
              ("https://docs.acmepay.com/mcp", "MCP"),
              ("https://docs.acmepay.com/oauth", "OAuth"))
    assert gap_fill_urls(ACME_PAY, s, set(), None) == ["https://docs.acmepay.com/mcp",
                                                       "https://docs.acmepay.com/authentication",
                                                       "https://docs.acmepay.com/oauth"]


def test_gap_fill_skips_already_fetched():
    s = _hits(("https://docs.acmepay.com/oauth", "OAuth"), ("https://www.docs.acmepay.com/mcp/", "MCP"))
    seen = {normalize_url("https://docs.acmepay.com/oauth"), normalize_url("https://docs.acmepay.com/mcp")}
    assert gap_fill_urls(ACME_PAY, s, seen, None) == []


def test_gap_fill_mcp_resolved_still_fills_auth_only():
    s = _hits(("https://docs.acmepay.com/mcp", "MCP server"), ("https://docs.acmepay.com/oauth", "OAuth"),
              ("https://github.com/acmepay/mcp-server", "MCP server"))
    assert gap_fill_urls(ACME_PAY, s, set(), {"mcp_status": "official"}) == ["https://docs.acmepay.com/oauth"]
    assert gap_fill_urls(ACME_PAY, s, set(), {"mcp_status": "none"})[:2] == ["https://docs.acmepay.com/mcp",
                                                                            "https://docs.acmepay.com/oauth"]


def test_gap_fill_vendor_repo_when_official_host_is_github():
    app = dict(ACME_PAY, start_url="https://github.com/acmepay/cli", source_hint="")
    s = _hits(("https://github.com/acmepay/mcp-server", "MCP"), ("https://github.com/other/mcp", "MCP"))
    assert gap_fill_urls(app, s, set(), None) == ["https://github.com/acmepay/mcp-server"]
