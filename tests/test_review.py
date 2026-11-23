"""Pass-2 reviewer: rule checks, targeted re-research, logging, resume, and the CLI dry run.

Synthetic fixtures only (Acme Pay on acmepay.io). LLM, search and fetch are offline stubs.
"""

import ast
import copy
import csv
import json
import re
from pathlib import Path

import pytest

from agent import cli
from agent.config import APPS_CSV, Settings
from agent.llm.base import LLMResult
from agent.review import Reviewer, merge_fill, review_queries, review_run
from agent.review_rules import (
    RULES,
    deterministic_findings,
    evidence_findings,
    fields_for_error,
    scope,
    validator_findings,
)
from agent.tools.base import Page, SearchResult
from agent.validate import validate_row

APP = {"id": 7, "slug": "acme-pay", "name": "Acme Pay", "category": "finance_and_fintech",
       "category_label": "Finance and Fintech", "source_hint": "acmepay.io", "start_url": "https://acmepay.io"}
IDENTITY = {k: APP[k] for k in ("id", "slug", "name", "category")}

API = "https://docs.acmepay.io/api"
AUTH = "https://docs.acmepay.io/auth"
PRICING = "https://acmepay.io/pricing"
MCP = "https://docs.acmepay.io/mcp"
GRAPHQL = "https://docs.acmepay.io/graphql"
PARTNERS = "https://acmepay.io/partners"
STATUS = "https://docs.acmepay.io/changelog"

TEXTS = {
    API: "Acme Pay REST API reference. Create charges, refunds and payouts with simple JSON requests.",
    AUTH: "Authenticate every request with your secret API key in the Authorization header as a bearer token.",
    PRICING: "Start free. Sign up and create API keys from the dashboard in minutes.",
    MCP: "Connect your agent to the Acme Pay MCP server at https://mcp.acmepay.io/sse with your API key.",
    GRAPHQL: "The Acme Pay GraphQL API exposes reporting queries at https://api.acmepay.io/graphql.",
    PARTNERS: "Platforms must join the Acme Pay partner program and pass review before calling the API.",
    STATUS: "The Acme Pay API is in private beta and may change without notice.",
    "https://acmepay.io": "Acme Pay lets online businesses accept card payments and send payouts.",
}


def _ev(url, kind="official_docs"):
    return [{"url": url, "source_type": kind}]


CLEAN_ROW = {
    **IDENTITY,
    "one_liner": "Acme Pay is a payments API for accepting cards and sending payouts.",
    "auth_methods": ["api_key"], "primary_auth": "api_key", "access": "self_serve", "api_types": ["rest"],
    "has_public_docs": True, "api_breadth": "moderate", "docs_url": API, "mcp_status": "none", "mcp_url": None,
    "verdict": "buildable_now", "blocker": "none",
    "evidence": {"auth": _ev(AUTH), "access": _ev(PRICING, "official_other"), "api_surface": _ev(API),
                 "mcp": _ev(API), "verdict": _ev(API)},
    "confidence": "high", "requires_end_user_admin": False,
    "composio_toolkit": {"status": "none", "toolkit_slug": None, "sibling_slugs": [],
                         "checked_at": "2026-11-23T10:41:38Z", "evidence_url": None},
    "notes": "",
}
QUOTES = {"auth": (AUTH, "secret API key in the Authorization header"), "access": (PRICING, "create API keys from"),
          "api_surface": (API, "Acme Pay REST API reference"), "mcp": (API, "Acme Pay REST API reference"),
          "verdict": (API, "Create charges, refunds and payouts")}


def _prior(row=CLEAN_ROW, verified=True):
    """A pass-1 evidence file: the cited pages fetched OK and one verified snippet per claim."""
    urls = {i["url"] for items in row["evidence"].values() for i in items}
    return {
        "slug": APP["slug"], "searches": [],
        "pages": [{"requested_url": u, "final_url": u, "status": 200, "provider": "fake", "error": None,
                   "method": "fake", "sha256": "x", "text_len": 100} for u in sorted(urls)],
        "snippets": {c: [{"url": u, "quote": q, "verified": verified}] for c, (u, q) in QUOTES.items()},
        "checks": {},
    }


def _row(**kw):
    row = copy.deepcopy(CLEAN_ROW)
    row.update(kw)
    return row


# ---------- stubs ----------

class SiteSearch:
    name = "site"

    def __init__(self):
        self.queries = []

    def search(self, query, max_results=5):
        self.queries.append(query)
        q = query.lower()
        url = (MCP if "mcp" in q else GRAPHQL if "graphql" in q else PARTNERS if "partner" in q
               else PRICING if "pricing" in q else STATUS if "beta" in q else AUTH if "auth" in q else API)
        return self.name, [SearchResult(url, f"Acme Pay {url.rsplit('/', 1)[-1]}")][:max_results]


class SiteFetch:
    name = "site"

    def __init__(self):
        self.urls = []

    def fetch(self, url):
        self.urls.append(url)
        text = TEXTS.get(url.rstrip("/"))
        if text is None:
            return Page(url, url, 404, self.name, error="HTTP 404", method="http_error")
        return Page(url, url, 200, self.name, text=text, method="fake")


class ScriptedLLM:
    """Reviewer calls return `review`; fill calls pop from `fills` (the last one repeats)."""

    provider, model = "scripted", "scripted-1"

    def __init__(self, review=None, fills=()):
        self.review = review or {"rules": []}
        self.fills = list(fills)
        self.calls = []

    def complete(self, system, messages, json_mode=True):
        kind = "review" if "REVIEW CHECKLIST" in system else "fill"
        self.calls.append((kind, messages[-1]["content"]))
        if kind == "review":
            out = self.review
        else:
            out = self.fills.pop(0) if len(self.fills) > 1 else self.fills[0]
        return LLMResult(json.dumps(out), input_tokens=100, output_tokens=20, stop_reason="end_turn")

    def kinds(self):
        return [k for k, _ in self.calls]


def _reviewer(tmp_path, llm, search=None, fetch=None):
    return Reviewer(Settings(), llm, search or SiteSearch(), fetch or SiteFetch(), tmp_path)


def _fill(row_fields, snippets, evidence=None, notes="Rechecked by the pass-2 review.", sneaky=True):
    """A fill answer. With sneaky, it also tries to change frozen fields, which must be ignored."""
    row = dict(row_fields, notes=notes)
    if evidence:
        row["evidence"] = evidence
    if sneaky:
        row.update(one_liner="A different product description that must never be applied.", api_breadth="broad")
    return {"row": row, "snippets": {c: [{"url": u, "quote": q}] for c, (u, q) in snippets.items()}}


# ---------- clean rows ----------

def test_clean_row_fires_nothing_and_is_unchanged(tmp_path):
    llm, search, fetch = ScriptedLLM(), SiteSearch(), SiteFetch()
    out = _reviewer(tmp_path, llm, search, fetch).review(APP, copy.deepcopy(CLEAN_ROW), _prior(), [])
    assert out.status == "unchanged" and out.findings == [] and out.fields_changed == []
    assert out.row == CLEAN_ROW and not out.re_researched
    assert llm.kinds() == ["review"]  # the reviewer call only, no fill
    assert search.queries == [] and fetch.urls == []  # no re-research


def test_reviewer_prompt_lists_every_rule_and_shows_cached_text(tmp_path):
    llm = ScriptedLLM()
    cached = [Page(API, API, 200, "fake", text=TEXTS[API], method="pass1_cache")]
    _reviewer(tmp_path, llm).review(APP, copy.deepcopy(CLEAN_ROW), _prior(), cached)
    prompt = llm.calls[0][1]
    assert TEXTS[API] in prompt and "Acme Pay REST API reference" in prompt
    reviewer = _reviewer(tmp_path, llm)
    assert all(rid in reviewer.review_system for rid in RULES)


# ---------- each rule fires, and only its fields change ----------

FROZEN = ("one_liner", "api_breadth", "confidence", "requires_end_user_admin", "composio_toolkit",
          "id", "slug", "name", "category")

CASES = {
    "basic_transport_not_method": dict(
        row=dict(auth_methods=["api_key", "basic"], primary_auth="basic",
                 notes="Send your API key as the username with HTTP Basic auth; leave the password blank."),
        fill=_fill({"auth_methods": ["api_key"], "primary_auth": "api_key"}, {"auth": QUOTES["auth"]},
                   {"auth": _ev(AUTH)}),
        changed={"auth_methods", "primary_auth"}),
    "oauth_client_secret_not_api_key": dict(
        row=dict(auth_methods=["oauth2", "api_key"], primary_auth="oauth2",
                 notes="The dashboard secret key is used as the OAuth client_secret when requesting a token."),
        fill=_fill({"auth_methods": ["oauth2"], "primary_auth": "oauth2"}, {"auth": QUOTES["auth"]},
                   {"auth": _ev(AUTH)}),
        changed={"auth_methods"}),
    "mcp_only_oauth": dict(
        row=dict(auth_methods=["oauth2", "api_key"], primary_auth="oauth2",
                 notes="OAuth is only for the hosted MCP server; the REST API uses API keys."),
        fill=_fill({"auth_methods": ["api_key"], "primary_auth": "api_key"}, {"auth": QUOTES["auth"]},
                   {"auth": _ev(AUTH)}),
        changed={"auth_methods", "primary_auth"}),
    "sales_or_review_gate": dict(
        row=dict(notes="API access requires approval from the Acme Pay partner team after a sales call."),
        fill=_fill({"access": "gated_partner", "verdict": "buildable_with_approval", "blocker": "gated_access"},
                   {"access": (PARTNERS, "join the Acme Pay partner program and pass review"),
                    "verdict": (PARTNERS, "pass review before calling the API")},
                   {"access": _ev(PARTNERS, "official_other"), "verdict": _ev(PARTNERS, "official_other")}),
        changed={"access", "verdict", "blocker"}),
    "preview_not_buildable_now": dict(
        row=dict(notes="The Acme Pay API is currently in private beta."),
        fill=_fill({"verdict": "not_buildable", "blocker": "preview_or_unstable"},
                   {"verdict": (STATUS, "API is in private beta")}, {"verdict": _ev(STATUS)}),
        changed={"verdict", "blocker"}),
    "official_graphql_omitted": dict(
        row=dict(notes="Acme Pay also offers a GraphQL API for reporting."),
        fill=_fill({"api_types": ["rest", "graphql"]}, {"api_surface": (GRAPHQL, "GraphQL API exposes reporting")},
                   {"api_surface": _ev(API) + _ev(GRAPHQL)}),
        changed={"api_types"}),
    "public_docs_marked_false": dict(
        row=dict(has_public_docs=False, docs_url=None, notes="The API reference is published at docs.acmepay.io."),
        fill=_fill({"has_public_docs": True, "docs_url": API}, {"api_surface": QUOTES["api_surface"]},
                   {"api_surface": _ev(API)}),
        changed={"has_public_docs", "docs_url"}),
    "missed_vendor_mcp": dict(
        row=dict(notes="Acme Pay runs a remote MCP server at https://mcp.acmepay.io/sse for agents."),
        fill=_fill({"mcp_status": "official", "mcp_url": MCP}, {"mcp": (MCP, "Acme Pay MCP server")},
                   {"mcp": _ev(MCP)}),
        changed={"mcp_status", "mcp_url"}),
}


@pytest.mark.parametrize("rule", sorted(CASES))
def test_content_rule_fires_and_only_its_fields_change(tmp_path, rule):
    case = CASES[rule]
    row = _row(**case["row"])
    llm = ScriptedLLM(fills=[case["fill"]])
    out = _reviewer(tmp_path, llm).review(APP, copy.deepcopy(row), _prior(row), [])
    assert rule in [f.rule for f in out.findings]
    assert any(f.rule == rule and f.source in ("code", "validator") for f in out.findings)
    assert out.status == "changed" and out.re_researched and out.errors == []
    assert validate_row(out.row, IDENTITY) == []
    changed = set(out.fields_changed)
    assert case["changed"] <= changed
    allowed = set(out.fields_in_scope) | {"evidence", "notes"}
    assert changed <= allowed, changed - allowed
    for k in FROZEN:  # the fill tried to change one_liner / api_breadth: ignored
        assert out.row[k] == row[k]
    for k in set(RULES[rule][1]) - case["changed"]:
        assert out.row[k] == row[k] or k in out.fields_in_scope
    assert out.before and set(out.before) == changed and out.after["notes"] == "Rechecked by the pass-2 review."
    assert llm.kinds() == ["review", "fill"]


def test_unsupported_evidence_fires_and_replaces_only_that_claim(tmp_path):
    prior = _prior()
    prior["snippets"]["access"] = [{"url": PRICING, "quote": "a quote that is not on the page", "verified": False}]
    fill = _fill({"access": "self_serve", "verdict": "buildable_now", "blocker": "none"},
                 {"access": (PRICING, "Sign up and create API keys from the dashboard"),
                  "verdict": QUOTES["verdict"]},
                 {"access": _ev(PRICING, "official_docs"), "verdict": _ev(API)})
    llm = ScriptedLLM(fills=[fill])
    out = _reviewer(tmp_path, llm).review(APP, copy.deepcopy(CLEAN_ROW), prior, [])
    assert [f.rule for f in out.findings] == ["unsupported_evidence"]
    assert out.findings[0].fields == ("access",)
    assert out.claims == ["access", "verdict"]  # access is coupled to verdict / blocker
    assert out.status == "changed" and set(out.fields_changed) == {"evidence", "notes"}
    assert out.row["access"] == "self_serve" and out.row["evidence"]["access"] == _ev(PRICING, "official_docs")
    assert out.row["evidence"]["auth"] == CLEAN_ROW["evidence"]["auth"]  # frozen claim untouched
    assert set(out.before["evidence"]) == {"access"}


def test_evidence_findings_malformed_and_unfetched_urls():
    row = _row(evidence={**CLEAN_ROW["evidence"], "mcp": [{"url": "not a url", "source_type": "other"}]})
    (f,) = evidence_findings(row, None)
    assert f.rule == "unsupported_evidence" and "mcp: no well-formed" in f.reason
    assert f.fields == ("mcp_status", "mcp_url")
    prior = _prior()
    prior["pages"] = [p for p in prior["pages"] if p["final_url"] != AUTH]
    (f,) = evidence_findings(CLEAN_ROW, prior)
    assert "auth: evidence URL was not a fetched page" in f.reason
    assert evidence_findings(CLEAN_ROW, None) == [] and evidence_findings(CLEAN_ROW, _prior()) == []


def test_llm_reviewer_is_the_recall_net(tmp_path):
    review = {"rules": [{"id": "official_graphql_omitted", "reason": "docs describe a GraphQL API",
                         "quotes": [{"url": GRAPHQL, "quote": "The Acme Pay GraphQL API exposes reporting"}]}]}
    fill = CASES["official_graphql_omitted"]["fill"]
    llm, search = ScriptedLLM(review, [fill]), SiteSearch()
    out = _reviewer(tmp_path, llm, search).review(APP, copy.deepcopy(CLEAN_ROW), _prior(), [])
    assert [(f.rule, f.source) for f in out.findings] == [("official_graphql_omitted", "llm")]
    assert out.row["api_types"] == ["rest", "graphql"] and out.fields_in_scope == ["api_types"]
    assert any("GraphQL" in q for q in search.queries)  # field-specific query
    assert "The Acme Pay GraphQL API exposes reporting" in llm.calls[1][1]  # finding quoted to the fill


def test_llm_hits_that_cannot_apply_are_ignored(tmp_path):
    row = _row(mcp_status="official", mcp_url=MCP, evidence={**CLEAN_ROW["evidence"], "mcp": _ev(MCP)})
    review = {"rules": [{"id": "missed_vendor_mcp", "reason": "x"}, {"id": "made_up_rule", "reason": "y"}]}
    llm = ScriptedLLM(review)
    out = _reviewer(tmp_path, llm).review(APP, copy.deepcopy(row), _prior(row), [])
    assert out.status == "unchanged" and out.findings == []
    assert [i["id"] for i in out.ignored_llm] == ["missed_vendor_mcp", "made_up_rule"]


# ---------- precision: rows that must not be rewritten ----------

def test_basic_stays_for_username_and_password(tmp_path):
    row = _row(auth_methods=["basic"], primary_auth="basic",
               notes="Authenticate with your account username and password over HTTP Basic.")
    assert deterministic_findings(APP, row, _prior(row)) == []
    both = _row(auth_methods=["api_key", "basic"], primary_auth="api_key",
                notes="Use the API key as the username for scripts. Admin endpoints take a username and password.")
    assert [f.rule for f in deterministic_findings(APP, both, _prior(both))] == []
    out = _reviewer(tmp_path, ScriptedLLM()).review(APP, copy.deepcopy(row), _prior(row), [])
    assert out.status == "unchanged" and out.row["auth_methods"] == ["basic"]


def test_self_serve_token_exception_keeps_self_serve_paid(tmp_path):
    row = _row(access="self_serve_paid",
               notes="API access for public OAuth apps requires review by the Acme Pay partner team. "
                     "A personal access token is available right after online checkout.")
    assert "sales_or_review_gate" not in [f.rule for f in deterministic_findings(APP, row, _prior(row))]
    out = _reviewer(tmp_path, ScriptedLLM()).review(APP, copy.deepcopy(row), _prior(row), [])
    assert out.status == "unchanged" and out.row["access"] == "self_serve_paid"


@pytest.mark.parametrize("notes", [
    "The MCP server is in beta; the REST API is generally available.",
    "There is no GraphQL API.",
    "MCP (beta) is mentioned on the homepage, coming soon, with no server URL.",
    "A community MCP server exists at https://mcp.so/server/acme-pay-mcp.",
])
def test_notes_that_must_not_fire(notes):
    assert deterministic_findings(APP, _row(notes=notes), _prior()) == []


def test_mcp_notes_contradiction_folds_into_mcp_only_oauth():
    row = _row(auth_methods=["oauth2", "api_key"], primary_auth="oauth2",
               notes="OAuth is only for the hosted MCP server.")
    assert [f.rule for f in validator_findings(row, IDENTITY)] == ["mcp_only_oauth"]


def test_validator_errors_map_to_fields():
    assert fields_for_error("rule 1: primary_auth must be one of auth_methods") == ("auth_methods", "primary_auth")
    assert fields_for_error("schema: evidence/mcp: [] should be non-empty") == ("mcp_status", "mcp_url")
    assert fields_for_error("rule 8: gated access cannot be verdict buildable_now") == ("access", "verdict", "blocker")
    bad = _row(primary_auth="oauth2")
    (f,) = validator_findings(bad, IDENTITY)
    assert f.rule == "validation_failed" and f.fields == ("auth_methods", "primary_auth")
    assert scope([f]) == (["auth_methods", "primary_auth"], ["auth"])


# ---------- retry and fallback ----------

def test_unsupported_fix_keeps_pass1_values_with_a_note(tmp_path):
    row = _row(notes="Acme Pay also offers a GraphQL API for reporting.")
    bogus = _fill({"api_types": ["rest", "graphql"]}, {"api_surface": ("https://invented.example.com/x", "q")},
                  {"api_surface": _ev("https://invented.example.com/x")})
    llm = ScriptedLLM(fills=[bogus])
    out = _reviewer(tmp_path, llm).review(APP, copy.deepcopy(row), _prior(row), [])
    assert llm.kinds() == ["review", "fill", "fill"]  # one retry
    assert out.retried and out.status == "kept" and out.fields_changed == ["notes"]
    assert out.row["api_types"] == ["rest"] and "could not support a corrected value" in out.row["notes"]
    assert out.evidence["checks"]["fill_errors"]


def test_retry_fixes_the_fill(tmp_path):
    row = _row(**CASES["official_graphql_omitted"]["row"])
    bogus = _fill({"api_types": ["rest", "graphql"]}, {}, {"api_surface": _ev("https://invented.example.com/x")})
    llm = ScriptedLLM(fills=[bogus, CASES["official_graphql_omitted"]["fill"]])
    out = _reviewer(tmp_path, llm).review(APP, copy.deepcopy(row), _prior(row), [])
    assert out.retried and out.status == "changed" and out.row["api_types"] == ["rest", "graphql"]
    assert "invented.example.com" in llm.calls[2][1]  # dropped URL fed back


def test_merge_fill_only_touches_scope():
    llm_row = {"access": "gated_sales", "one_liner": "changed", "evidence": {"access": _ev(PARTNERS), "auth": []},
               "notes": "n"}
    merged = merge_fill(CLEAN_ROW, llm_row, ["access"], ["access"])
    assert merged["access"] == "gated_sales" and merged["one_liner"] == CLEAN_ROW["one_liner"]
    assert merged["evidence"]["auth"] == CLEAN_ROW["evidence"]["auth"] and merged["notes"] == "n"


def test_review_queries_are_field_specific_and_official_first():
    qs = review_queries(APP, ["auth", "mcp"], ["missed_vendor_mcp"])
    assert qs[:2] == ["Acme Pay API authentication OAuth API key", "Acme Pay official MCP server"]
    assert "site:acmepay.io MCP" in qs and "site:acmepay.io API authentication" in qs
    assert any("partner" in q for q in review_queries(APP, ["access"], ["sales_or_review_gate"]))
    assert len(review_queries(APP, ["auth", "access", "api_surface", "mcp", "verdict"], list(RULES))) <= 6


# ---------- run driver, resume, CLI ----------

def _lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def _input_dir(tmp_path, rows, prior=True):
    src = tmp_path / "pass1"
    (src / "evidence").mkdir(parents=True)
    (src / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    if prior:
        for r in rows:
            (src / "evidence" / f"{r['slug']}.json").write_text(json.dumps(_prior(r)))
    return src


APP2 = dict(APP, id=8, slug="acme-books", name="Acme Books", source_hint="acmebooks.io",
            start_url="https://acmebooks.io")


def test_review_run_writes_rows_log_and_resumes(tmp_path):
    row2 = {**_row(notes="Acme Books also offers a GraphQL API for reporting."), "id": 8, "slug": "acme-books",
            "name": "Acme Books"}
    src = _input_dir(tmp_path, [CLEAN_ROW, row2])
    out_dir = tmp_path / "pass2"
    fill = _fill({"api_types": ["rest", "graphql"]}, {"api_surface": (GRAPHQL, "GraphQL API exposes reporting")},
                 {"api_surface": _ev(GRAPHQL)})
    llm = ScriptedLLM(fills=[fill])
    summary = review_run([(APP, CLEAN_ROW), (APP2, row2)], _reviewer(out_dir, llm), src, out_dir, concurrency=2)
    assert summary["unchanged"] == 1 and summary["changed"] == 1 and summary["failed"] == 0
    assert summary["rules_fired"] == {"official_graphql_omitted": 1}
    rows = {r["slug"]: r for r in _lines(out_dir / "rows.jsonl")}
    assert rows["acme-pay"] == CLEAN_ROW and rows["acme-books"]["api_types"] == ["rest", "graphql"]
    recs = {r["slug"]: r for r in _lines(out_dir / "review.jsonl")}
    assert recs["acme-pay"]["rules_fired"] == [] and recs["acme-pay"]["changed"] is False
    b = recs["acme-books"]
    assert b["changed"] and b["re_researched"] and b["before"]["api_types"] == ["rest"]
    assert b["after"]["api_types"] == ["rest", "graphql"]
    for key in ("fields_changed", "search_calls", "fetch_calls", "llm_calls", "exa_calls", "instant_calls",
                "wall_s", "status", "findings", "est_cost_usd"):
        assert key in b
    ev = json.loads((out_dir / "evidence" / "acme-books.json").read_text())
    fetched = {p["final_url"] for p in ev["pages"]}
    assert all(i["url"] in fetched for items in rows["acme-books"]["evidence"].values() for i in items)
    assert ev["review"]["rules_fired"] == ["official_graphql_omitted"]
    assert {r["slug"] for r in _lines(out_dir / "log.jsonl")} == {"acme-pay", "acme-books"}

    llm2 = ScriptedLLM(fills=[fill])
    again = review_run([(APP, CLEAN_ROW), (APP2, row2)], _reviewer(out_dir, llm2), src, out_dir)
    assert again["skipped_done"] == 2 and llm2.calls == []
    assert len(_lines(out_dir / "rows.jsonl")) == 2


def test_cli_dry_run_needs_no_keys_and_writes_review_log(tmp_path, monkeypatch):
    apps_csv = tmp_path / "apps.csv"
    with apps_csv.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(APP))
        w.writeheader()
        w.writerows([APP, APP2])
    env = tmp_path / "empty.env"
    env.write_text("")
    monkeypatch.setenv("ENV_FILE", str(env))
    monkeypatch.setattr(cli, "APPS_CSV", apps_csv)
    row2 = {**_row(notes="Acme Books also offers a GraphQL API for reporting."), "id": 8, "slug": "acme-books",
            "name": "Acme Books"}
    src = _input_dir(tmp_path, [CLEAN_ROW, row2], prior=False)
    code = cli.main(["review", "--dry-run", "--from-dir", str(src), "--out-dir", str(tmp_path), "--run-id", "r2"])
    assert code == 0
    recs = {r["slug"]: r for r in _lines(tmp_path / "r2" / "review.jsonl")}
    assert recs["acme-pay"]["status"] == "unchanged"
    assert recs["acme-books"]["re_researched"] and recs["acme-books"]["rules_fired"] == ["official_graphql_omitted"]
    for r in _lines(tmp_path / "r2" / "rows.jsonl"):
        ident = {k: r[k] for k in ("id", "slug", "name", "category")}
        assert validate_row(r, ident) == []
    assert cli.main(["review", "--dry-run", "--from-dir", str(src), "--out-dir", str(tmp_path),
                     "--run-id", "r2"]) == 0  # resume: nothing left to do
    assert len(_lines(tmp_path / "r2" / "rows.jsonl")) == 2


# ---------- no real product names in fixtures or reviewer code ----------

def _strings(path: Path) -> str:
    tree = ast.parse(path.read_text())
    return "\n".join(n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str))


def test_no_names_from_the_app_list():
    with APPS_CSV.open(newline="") as f:
        apps = list(csv.DictReader(f))
    names = {a["name"] for a in apps} | {a["slug"] for a in apps}
    names |= {re.sub(r"\s*\(.*\)", "", n) for n in names}
    root = Path(__file__).resolve().parent.parent
    texts = {"tests/test_review.py": _strings(Path(__file__)),
             "agent/review.py": (root / "agent" / "review.py").read_text(),
             "agent/review_rules.py": (root / "agent" / "review_rules.py").read_text()}
    for where, text in texts.items():
        for n in names:
            assert not re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", text, re.I), f"{n!r} in {where}"
