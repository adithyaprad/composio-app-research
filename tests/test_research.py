import json

from agent.config import APPS_CSV, COVERAGE_CSV, Settings
from agent.fakes import FakeFetch, FakeLLM, FakeSearch
from agent.llm.base import LLMResult
from agent.research import Researcher, classify_result, load_apps, load_coverage, pick_urls, run
from agent.validate import validate_row

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
