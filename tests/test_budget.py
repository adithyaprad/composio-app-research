"""Instant budget cap and hybrid (free-first) search/fetch escalation."""

import threading
import types

import pytest
from test_tools import FakeComposio, _resp

from agent.config import Settings
from agent.tools import HybridFetch, HybridSearch, get_fetch
from agent.tools.base import Page, SearchResult
from agent.tools.budget import BudgetExceeded, InstantBudget, current_app, parse_charge
from agent.tools.composio_tools import ComposioFetch, ComposioSearch
from agent.tools.keyless import THIN_CHARS

LONG = "Firecrawl rendered text. " * 100  # > THIN_CHARS


# ---- InstantBudget --------------------------------------------------------------

def test_budget_cap_enforced():
    b = InstantBudget(0.01)  # FIRECRAWL_SCRAPE listed at 0.003
    for _ in range(3):
        b.reserve("FIRECRAWL_SCRAPE")
        b.record("FIRECRAWL_SCRAPE", None)
    with pytest.raises(BudgetExceeded):
        b.reserve("FIRECRAWL_SCRAPE")  # 0.009 + 0.003 > 0.01
    snap = b.snapshot()
    assert snap["instant_calls"] == 3 and snap["instant_calls_by_tool"] == {"FIRECRAWL_SCRAPE": 3}
    assert snap["instant_est_cost_usd"] == pytest.approx(0.009) and snap["instant_charged_usd"] == 0


def test_budget_zero_disables(caplog):
    b = InstantBudget(0)
    for _ in range(3):
        with pytest.raises(BudgetExceeded):
            b.reserve("EXA_SEARCH")
    assert sum("disabled" in r.message for r in caplog.records) == 1  # logged once


def test_budget_actual_charge_preferred_and_unknown_tool_price():
    b = InstantBudget(1.0, {"EXA_SEARCH": 0.02})
    assert b.price("EXA_SEARCH") == 0.02 and b.price("NEW_TOOL") == 0.01
    b.reserve("EXA_SEARCH")
    b.record("EXA_SEARCH", 0.005)
    snap = b.snapshot()
    assert snap["instant_charged_usd"] == 0.005 and snap["instant_est_cost_usd"] == 0


def test_budget_release_frees_reservation():
    b = InstantBudget(0.003)
    b.reserve("FIRECRAWL_SCRAPE")
    with pytest.raises(BudgetExceeded):
        b.reserve("FIRECRAWL_SCRAPE")  # in-flight reservation counts
    b.release("FIRECRAWL_SCRAPE")
    b.reserve("FIRECRAWL_SCRAPE")


def test_budget_thread_safe():
    b = InstantBudget(0.30)  # exactly 100 Firecrawl calls

    def worker():
        for _ in range(50):
            try:
                b.reserve("FIRECRAWL_SCRAPE")
            except BudgetExceeded:
                continue
            b.record("FIRECRAWL_SCRAPE", None)

    threads = [threading.Thread(target=worker) for _ in range(8)]  # 400 attempts
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    snap = b.snapshot()
    assert snap["instant_calls"] == 100
    assert snap["instant_est_cost_usd"] <= 0.30 + 1e-9


def test_budget_per_app_stats():
    b = InstantBudget(1.0)
    token = current_app.set("hubspot")
    try:
        b.reserve("EXA_SEARCH")
        b.record("EXA_SEARCH", None)
    finally:
        current_app.reset(token)
    assert b.app_stats("hubspot")["instant_calls"] == 1
    assert b.app_stats("stripe")["instant_calls"] == 0


def test_parse_charge():
    assert parse_charge(types.SimpleNamespace(instant_charge=types.SimpleNamespace(amount="0.003"))) == 0.003
    assert parse_charge(types.SimpleNamespace(instant_charge={"amount": "x"})) is None
    assert parse_charge(types.SimpleNamespace()) is None


# ---- ComposioTool + budget ------------------------------------------------------

def _firecrawl_ok(slug, args):
    r = _resp({"data": {"markdown": LONG, "metadata": {"sourceURL": args["url"], "statusCode": 200}}})
    r.instant_charge = types.SimpleNamespace(amount="0.0025", currency="USD", charged_by="composio")
    return r


def test_composio_call_records_actual_charge():
    b = InstantBudget(1.0)
    client = FakeComposio(_firecrawl_ok)
    page = ComposioFetch("u", 50000, client, b).fetch("https://x.dev")
    assert page.method == "firecrawl_markdown"
    assert b.snapshot()["instant_charged_usd"] == 0.0025


def test_composio_call_skipped_when_over_budget():
    client = FakeComposio(_firecrawl_ok)
    with pytest.raises(BudgetExceeded):
        ComposioSearch("EXA_SEARCH", "u", client, InstantBudget(0.001)).search("q")
    assert client.session.calls == []  # nothing sent


def test_no_connection_releases_reservation():
    b = InstantBudget(1.0)

    def no_conn(slug, args):
        raise RuntimeError("400 No active connection found for toolkit(s) 'firecrawl'")

    from agent.tools import ProviderUnavailable

    with pytest.raises(ProviderUnavailable):
        ComposioFetch("u", 1000, FakeComposio(no_conn), b).fetch("https://x.dev")
    assert b.snapshot()["instant_calls"] == 0 and b._reserved == 0


# ---- HybridFetch ----------------------------------------------------------------

class StubHttp:
    name = "http"

    def __init__(self, pages):
        self.pages = pages

    def fetch(self, url):
        status, text = self.pages[url]
        return Page(url, url, status, "http", text=text, method="trafilatura" if status < 400 else "http_error",
                    error=None if status < 400 else f"HTTP {status}")


def _hybrid(pages, budget=None, behaviour=_firecrawl_ok):
    client = FakeComposio(behaviour)
    firecrawl = ComposioFetch("u", 50000, client, budget or InstantBudget(1.0))
    return HybridFetch(StubHttp(pages), firecrawl), client


def test_hybrid_rich_page_never_escalates():
    fetch, client = _hybrid({"https://a.dev/docs": (200, "x" * THIN_CHARS)})
    page = fetch.fetch("https://a.dev/docs")
    assert page.provider == "http" and client.session.calls == []


def test_hybrid_thin_page_escalates():
    fetch, client = _hybrid({"https://a.dev/docs": (200, "Loading...")})
    page = fetch.fetch("https://a.dev/docs")
    assert page.provider == "composio_firecrawl" and page.method == "firecrawl_markdown"
    assert len(client.session.calls) == 1


@pytest.mark.parametrize("status", [0, 401, 403, 429, 503])
def test_hybrid_blocked_page_escalates(status):
    fetch, client = _hybrid({"https://a.dev/p": (status, "")})
    assert fetch.fetch("https://a.dev/p").provider == "composio_firecrawl"


def test_hybrid_404_not_escalated():
    fetch, client = _hybrid({"https://a.dev/missing": (404, "")})
    assert fetch.fetch("https://a.dev/missing").status == 404 and client.session.calls == []


def test_hybrid_stops_on_budget_cap():
    pages = {f"https://a.dev/{i}": (200, "thin") for i in range(5)}
    fetch, client = _hybrid(pages, budget=InstantBudget(0.006))  # 2 Firecrawl calls
    providers = [fetch.fetch(f"https://a.dev/{i}").provider for i in range(5)]
    assert providers == ["composio_firecrawl"] * 2 + ["http"] * 3
    assert len(client.session.calls) == 2


def test_hybrid_stops_after_provider_unavailable(caplog):
    def no_conn(slug, args):
        raise RuntimeError("400 No active connection found for toolkit(s) 'firecrawl'")

    pages = {f"https://a.dev/{i}": (200, "thin") for i in range(4)}
    fetch, client = _hybrid(pages, behaviour=no_conn)
    results = [fetch.fetch(f"https://a.dev/{i}") for i in range(4)]
    assert all(p.provider == "http" and p.text == "thin" for p in results)  # free result kept
    assert len(client.session.calls) == 1  # Instant not retried for the rest of the run
    assert sum("unavailable, using http" in r.message for r in caplog.records) == 1


def test_hybrid_from_settings_with_key(monkeypatch):
    monkeypatch.setenv("COMPOSIO_API_KEY", "k")
    fetch = get_fetch(Settings())
    assert isinstance(fetch, HybridFetch) and fetch.paid is not None


# ---- HybridSearch ---------------------------------------------------------------

class StubSearch:
    name = "ddgs"

    def __init__(self, results):
        self.results = results

    def search(self, query, max_results=5):
        return "ddgs:yahoo", self.results


def test_hybrid_search_uses_composio_only_when_ddgs_empty():
    client = FakeComposio(lambda s, a: _resp({"results": [{"url": "https://exa.result"}]}))
    exa = ComposioSearch("EXA_SEARCH", "u", client, InstantBudget(1.0))
    assert HybridSearch(StubSearch([SearchResult("https://free")]), exa).search("q")[0] == "ddgs:yahoo"
    assert client.session.calls == []
    name, results = HybridSearch(StubSearch([]), exa).search("q")
    assert name == "composio_exa" and results[0].url == "https://exa.result"


# ---- run log + summary ------------------------------------------------------------

def test_instant_fields_in_log_and_summary(tmp_path):
    from test_research import COVERAGE, _apps, _lines

    from agent.fakes import FakeFetch, FakeLLM, FakeSearch
    from agent.research import Researcher, run

    budget = InstantBudget(0.30)

    class ChargingFetch(FakeFetch):
        """Every fetch for stripe 'escalates' to one billed Firecrawl call."""

        def fetch(self, url):
            if current_app.get() == "stripe":
                budget.reserve("FIRECRAWL_SCRAPE")
                budget.record("FIRECRAWL_SCRAPE", None)
            return super().fetch(url)

    r = Researcher(Settings(), FakeLLM(), FakeSearch(), ChargingFetch(), tmp_path, coverage=COVERAGE, budget=budget)
    summary = run(_apps("stripe", "hubspot"), r, tmp_path, concurrency=2)
    logs = {rec["slug"]: rec for rec in _lines(tmp_path / "log.jsonl")}
    n = logs["stripe"]["fetch_calls"]
    assert logs["stripe"]["instant_calls"] == n and logs["stripe"]["instant_calls_by_tool"] == {"FIRECRAWL_SCRAPE": n}
    assert logs["stripe"]["instant_est_cost_usd"] == pytest.approx(0.003 * n)
    assert logs["hubspot"]["instant_calls"] == 0 and logs["hubspot"]["instant_charged_usd"] == 0
    assert summary["instant_calls"] == n and summary["instant_budget_usd"] == 0.30
    assert summary["instant_est_cost_usd"] == pytest.approx(0.003 * n)
