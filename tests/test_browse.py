"""Pass-3 browser verification: flag selector, local-then-cloud fallback, scoped confirm/correct. Offline only:
fake browser drivers and a scripted LLM; no Chrome, no Browser Use Cloud, no network."""

from __future__ import annotations

import copy
import csv
import json
import re
import tokenize
from pathlib import Path

import pytest

from agent import browse, cli
from agent.browse import BrowserPool, Render, Verifier, browse_run, choose_urls, is_login_wall, render_problem
from agent.browse_flags import REASONS, flag_row
from agent.config import APPS_CSV, Settings
from agent.llm.base import LLMResult
from agent.validate import validate_row

APP = {"id": 7, "slug": "acme-pay", "name": "Acme Pay", "category": "finance_and_fintech",
       "category_label": "Finance and Fintech", "source_hint": "acmepay.io", "start_url": "https://acmepay.io"}
IDENTITY = {k: APP[k] for k in ("id", "slug", "name", "category")}

API = "https://docs.acmepay.io/api"
AUTH = "https://docs.acmepay.io/auth"
PRICING = "https://acmepay.io/pricing"
MCP = "https://docs.acmepay.io/mcp"
LOGIN = "https://acmepay.io/login"

LONG = " Acme Pay lets online businesses accept card payments, send payouts and reconcile balances." * 6
TEXTS = {
    API: "Acme Pay REST API reference. Create charges, refunds and payouts with simple JSON requests." + LONG,
    AUTH: "Authenticate every request with your secret API key in the Authorization header." + LONG,
    PRICING: "Start free. Sign up and create API keys from the dashboard in minutes." + LONG,
    MCP: "The Acme Pay MCP server is live at https://mcp.acmepay.io/sse. Install it with your API key." + LONG,
    "https://acmepay.io": "Acme Pay home." + LONG,
}

# Synthetic rendered pages: (title, visible text, html, http status).
CAPTCHA = ("Just a moment...", "Checking your browser before accessing acmepay.io. Verify you are human.",
           '<html><div class="cf-turnstile"></div><script src="/cdn-cgi/challenge-platform/x.js"></script></html>',
           403)
CAPTCHA_HTML_ONLY = ("Acme Pay", "Loading", '<div class="g-recaptcha" data-sitekey="x"></div>', 200)
BLOCKED = ("Access Denied", "Access Denied. You don't have permission to access this page. Reference #18.",
           "<html><h1>Access Denied</h1></html>", 200)
UNUSUAL = ("Acme Pay", "Our systems have detected unusual traffic from your network.", "<html></html>", 200)
EMPTY = ("Acme Pay", "  ", '<html><body><div id="root"></div></body></html>', 200)
DOCS_ABOUT_CAPTCHA = ("Fraud tools", "Acme Pay fraud tools can show a CAPTCHA challenge to risky shoppers." + LONG * 5,
                      "<html><body>fraud tools</body></html>", 200)


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


def _row(**kw):
    row = copy.deepcopy(CLEAN_ROW)
    row.update(kw)
    return row


def _evidence(row=CLEAN_ROW, **page_overrides):
    """A pass-2 evidence file: every cited page fetched OK (overrides per URL), one verified snippet per claim."""
    urls = sorted({i["url"] for items in row["evidence"].values() for i in items})
    pages = []
    for u in urls:
        meta = {"requested_url": u, "final_url": u, "status": 200, "provider": "exa", "error": None,
                "method": "exa_search", "sha256": "x", "text_len": 3000}
        meta.update(page_overrides.get(u, {}))
        pages.append(meta)
    return {"slug": APP["slug"], "searches": [], "pages": pages,
            "snippets": {c: [{"url": row["evidence"][c][0]["url"], "quote": "Acme Pay", "verified": True}]
                         for c in ("auth", "access", "api_surface", "mcp", "verdict")},
            "checks": {"retried": False, "validation_errors": []}}


# ---------- stubs ----------

class FakeDriver:
    """A Browser Use stand-in. `pages` maps (kind, url) or url -> (title, text, html, status)."""

    def __init__(self, kind, pages, log):
        self.kind, self.pages, self.log = kind, pages, log

    def start(self):
        self.log.append(("start", self.kind))

    def open(self, url):
        self.log.append(("open", self.kind, url))
        spec = self.pages.get((self.kind, url)) or self.pages.get(url)
        if spec is None:
            spec = ("Acme Pay", TEXTS.get(url, "Acme Pay page." + LONG), "<html></html>", 200)
        title, text, html, status = spec
        return Render(url, url, title, text, html, status, b"\xff\xd8jpeg\xff\xd9")

    def stop(self):
        self.log.append(("stop", self.kind))


def _pool_factory(pages=None, key=True, log=None):
    log = [] if log is None else log

    def make_pool():
        return BrowserPool(lambda kind: FakeDriver(kind, pages or {}, log), has_cloud_key=lambda: key)
    return make_pool, log


class ScriptedLLM:
    provider, model = "scripted", "s-1"

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    def complete(self, system, messages, json_mode=True):
        self.calls.append(messages[-1]["content"])
        out = self.outputs.pop(0) if self.outputs else {"rules": []}
        return LLMResult(json.dumps(out), input_tokens=100, output_tokens=50, stop_reason="end_turn")


def _fill(fields, snippets, evidence, notes="Checked in a real browser.", sneaky=True):
    row = dict(fields)
    if sneaky:  # out-of-scope fields the merge must ignore
        row.update(api_types=["graphql"], one_liner="Something else entirely, which must never be copied over.")
    row.update(evidence=evidence, notes=notes)
    return {"row": row, "snippets": {c: [{"url": u, "quote": q}] for c, (u, q) in snippets.items()}}


def _verifier(tmp_path, llm, make_pool):
    return Verifier(Settings.from_env(), llm, tmp_path, make_pool=make_pool)


# ---------- flag selector ----------

def test_clean_high_confidence_row_is_not_flagged():
    assert flag_row(CLEAN_ROW, _evidence(), {"rules_fired": [], "validation_errors": []}) == []


@pytest.mark.parametrize("confidence", ["medium", "low"])
def test_confidence_below_high_flags(confidence):
    flags = flag_row(_row(confidence=confidence), _evidence())
    assert [f.reason for f in flags] == ["confidence_not_high"]
    assert set(flags[0].claims) == {"access", "auth", "mcp", "verdict"}


@pytest.mark.parametrize("override", [
    {"text_len": 40}, {"status": 403, "text_len": 0}, {"status": 429}, {"error": "timeout", "status": 0},
    {"method": "http_error", "status": 404}, {"method": "exa_error", "text_len": 0},
])
def test_thin_or_blocked_cited_page_flags(override):
    flags = flag_row(CLEAN_ROW, _evidence(**{PRICING: override}))
    assert [f.reason for f in flags] == ["thin_or_blocked_page"]
    assert flags[0].claims == ("access",) and flags[0].urls == (PRICING,)


def test_thin_page_that_is_not_cited_for_a_deciding_claim_does_not_flag():
    row = _row(evidence={**CLEAN_ROW["evidence"], "api_surface": _ev("https://docs.acmepay.io/sdks")})
    assert flag_row(row, _evidence(row, **{"https://docs.acmepay.io/sdks": {"text_len": 10}})) == []


def test_a_good_record_of_the_same_page_wins():
    ev = _evidence()
    ev["pages"].append({**ev["pages"][0], "status": 403, "text_len": 0})
    assert flag_row(CLEAN_ROW, ev) == []


@pytest.mark.parametrize("notes,claims", [
    ("The Acme Pay MCP server is coming soon according to the blog.", ("mcp",)),
    ("Pricing page says to book a demo before API keys are issued.", ("access", "verdict")),
    ("Talk to sales for Enterprise plans.", ("access", "verdict")),
    ("The API is in private beta.", ("access", "verdict")),
    ("The pricing page is JS-only and returned no text.", ("access", "auth", "mcp")),
    ("Docs page could not be fetched (blocked by a bot wall).", ("access", "auth", "mcp")),
])
def test_unresolved_deciding_fact_in_notes_flags(notes, claims):
    flags = flag_row(_row(notes=notes), _evidence())
    assert [f.reason for f in flags] == ["unresolved_deciding_fact"]
    assert flags[0].claims == claims


def test_deciding_fact_in_a_quote_flags():
    ev = _evidence()
    ev["snippets"]["mcp"] = [{"url": API, "quote": "Acme Pay MCP (beta) is coming soon", "verified": True}]
    assert [f.reason for f in flag_row(CLEAN_ROW, ev)] == ["unresolved_deciding_fact"]


@pytest.mark.parametrize("review,checks", [
    ({"rules_fired": ["unsupported_evidence"]}, []),
    ({"rules_fired": ["validation_failed"]}, []),
    ({"rules_fired": [], "validation_errors": ["rule 1: primary_auth not in auth_methods"]}, []),
    ({}, ["schema: evidence/auth is too short"]),
])
def test_unsupported_or_invalid_flags(review, checks):
    ev = _evidence()
    ev["checks"]["validation_errors"] = checks
    assert [f.reason for f in flag_row(CLEAN_ROW, ev, review)] == ["unsupported_or_invalid"]


def test_every_reason_is_recorded_in_order():
    ev = _evidence(**{AUTH: {"text_len": 5}})
    flags = flag_row(_row(confidence="medium", notes="Book a demo to get access."), ev,
                     {"rules_fired": ["unsupported_evidence"]})
    assert [f.reason for f in flags] == list(REASONS)


# ---------- empty / captcha / blocked on synthetic pages ----------

@pytest.mark.parametrize("spec,problem", [
    (CAPTCHA, "captcha"), (CAPTCHA_HTML_ONLY, "captcha"), (BLOCKED, "blocked"), (UNUSUAL, "blocked"),
    (EMPTY, "empty"), (("Acme Pay", TEXTS[API], "<html></html>", 429), "blocked"),
    (DOCS_ABOUT_CAPTCHA, None), (("Acme Pay", TEXTS[PRICING], "<html></html>", 200), None),
])
def test_render_problem(spec, problem):
    title, text, html, status = spec
    assert render_problem(text, title, html, status) == problem


def test_choose_urls_caps_skips_login_and_prefers_pricing():
    row = _row(evidence={**CLEAN_ROW["evidence"], "access": _ev(LOGIN) + _ev(PRICING),
                         "mcp": _ev(MCP), "auth": _ev(AUTH)})
    flags = flag_row(_row(**{k: row[k] for k in ("evidence",)}, confidence="low"), None)
    urls = choose_urls(APP, row, flags, ["access", "auth", "mcp", "verdict"])
    assert urls == [PRICING, AUTH, MCP, API]
    assert is_login_wall(LOGIN) and is_login_wall("https://accounts.acmepay.io/x") and not is_login_wall(PRICING)


# ---------- per-row verification ----------

def test_unflagged_row_is_copied_unchanged_without_a_browser(tmp_path):
    make_pool, log = _pool_factory()
    llm = ScriptedLLM()
    out = _verifier(tmp_path, llm, make_pool).verify(APP, CLEAN_ROW, _evidence(), {"rules_fired": []})
    assert out.status == "copied" and out.row == CLEAN_ROW and out.flags == []
    assert log == [] and llm.calls == [] and out.cloud_sessions == 0


def test_local_success_confirms_only_in_scope_fields(tmp_path):
    make_pool, log = _pool_factory()
    row = _row(confidence="medium", notes="The Acme Pay MCP server is coming soon.",
               evidence={**CLEAN_ROW["evidence"], "mcp": _ev(MCP)})
    fill = _fill({"mcp_status": "official", "mcp_url": MCP, "confidence": "high"},
                 {"mcp": (MCP, "The Acme Pay MCP server is live")}, {"mcp": _ev(MCP)})
    llm = ScriptedLLM(fill)
    out = _verifier(tmp_path, llm, make_pool).verify(APP, row, _evidence(row))
    assert out.status == "changed" and out.errors == []
    assert ("start", "cloud") not in log and out.cloud_sessions == 0
    assert all(p["browser"] == "local" and p["why_cloud"] is None for p in out.pages)
    assert out.row["mcp_status"] == "official" and out.row["mcp_url"] == MCP
    # Out-of-scope fields stay identical even though the LLM returned them.
    for k in ("api_types", "one_liner", "has_public_docs", "docs_url", "api_breadth", "composio_toolkit"):
        assert out.row[k] == row[k]
    assert out.row["evidence"]["api_surface"] == row["evidence"]["api_surface"]
    assert "screenshot screenshots/acme-pay/" in out.row["evidence"]["mcp"][0]["note"]
    shot = out.pages[0]["screenshot"]
    assert shot.startswith("screenshots/acme-pay/") and (tmp_path / shot).is_file()
    assert "RENDERED PAGES" in llm.calls[0] and "EDITABLE FIELDS" in llm.calls[0]
    assert validate_row(out.row, IDENTITY) == []


def test_local_success_can_confirm_without_changes(tmp_path):
    make_pool, _ = _pool_factory()
    row = _row(confidence="medium")
    fill = _fill({k: row[k] for k in ("auth_methods", "primary_auth", "access", "mcp_status", "mcp_url",
                                      "verdict", "blocker", "confidence")},
                 {"auth": (AUTH, "secret API key in the Authorization header"),
                  "access": (PRICING, "create API keys from the dashboard"),
                  "mcp": (API, "Acme Pay REST API reference"), "verdict": (API, "Create charges, refunds")},
                 {"auth": _ev(AUTH), "access": _ev(PRICING, "official_other"), "mcp": _ev(API),
                  "verdict": _ev(API)}, notes="")
    out = _verifier(tmp_path, ScriptedLLM(fill), make_pool).verify(APP, row, _evidence(row))
    assert out.status == "confirmed" and out.fields_changed == ["evidence"]
    assert set(out.fields_confirmed) >= {"access", "auth_methods", "mcp_status", "verdict"}


@pytest.mark.parametrize("spec,why", [(EMPTY, "empty"), (CAPTCHA, "captcha"), (BLOCKED, "blocked")])
def test_bad_local_page_falls_back_to_cloud_for_that_url_only(tmp_path, spec, why):
    make_pool, log = _pool_factory({("local", PRICING): spec})
    row = _row(confidence="low")
    fill = _fill({"access": "self_serve", "verdict": "buildable_now", "blocker": "none"},
                 {"access": (PRICING, "Sign up and create API keys")}, {"access": _ev(PRICING, "official_other")})
    out = _verifier(tmp_path, ScriptedLLM(fill), make_pool).verify(APP, row, _evidence(row))
    by_url = {p["url"]: p for p in out.pages}
    assert by_url[PRICING]["browser"] == "cloud" and by_url[PRICING]["why_cloud"] == why
    assert by_url[PRICING]["local_problem"] == why and by_url[PRICING]["usable"]
    assert [e for e in log if e[:2] == ("open", "cloud")] == [("open", "cloud", PRICING)]
    assert log.count(("start", "cloud")) == 1 and out.cloud_sessions == 1
    assert all(p["browser"] == "local" for u, p in by_url.items() if u != PRICING)
    assert ("stop", "cloud") in log and ("stop", "local") in log


def test_one_cloud_session_is_reused_within_a_row(tmp_path):
    make_pool, log = _pool_factory({("local", PRICING): EMPTY, ("local", AUTH): CAPTCHA})
    row = _row(confidence="low")
    out = _verifier(tmp_path, ScriptedLLM(), make_pool).verify(APP, row, _evidence(row))
    assert sum(p["browser"] == "cloud" for p in out.pages) == 2
    assert log.count(("start", "cloud")) == 1 and out.cloud_sessions == 1


def test_missing_cloud_key_keeps_local_result_and_row(tmp_path):
    every = {u: EMPTY for u in (PRICING, AUTH, API, "https://acmepay.io")}
    make_pool, log = _pool_factory(every, key=False)
    llm = ScriptedLLM()
    row = _row(confidence="medium")
    out = _verifier(tmp_path, llm, make_pool).verify(APP, row, _evidence(row))
    assert out.status == "kept" and out.errors == [] and llm.calls == []
    assert all(p["cloud_skipped"] == "cloud_skipped_no_key" and p["browser"] == "local" for p in out.pages)
    assert ("start", "cloud") not in log and out.cloud_sessions == 0
    assert "Pass-3 browser check" in out.row["notes"] and "pass-2 values kept" in out.row["notes"]
    assert {k: v for k, v in out.row.items() if k != "notes"} == {k: v for k, v in row.items() if k != "notes"}


def test_cloud_start_failure_does_not_crash(tmp_path):
    class Failing(FakeDriver):
        def start(self):
            if self.kind == "cloud":
                raise RuntimeError("cloud unavailable")

    log = []

    def make_pool():
        return BrowserPool(lambda kind: Failing(kind, {("local", PRICING): EMPTY}, log), has_cloud_key=lambda: True)
    row = _row(confidence="low")
    out = _verifier(tmp_path, ScriptedLLM(), make_pool).verify(APP, row, _evidence(row))
    pricing = next(p for p in out.pages if p["url"] == PRICING)
    assert pricing["browser"] == "local" and "did not start" in pricing["cloud_error"]
    assert out.cloud_sessions == 0 and out.status == "kept"


def test_unsupported_change_retries_once_then_keeps_pass2_values(tmp_path):
    make_pool, _ = _pool_factory()
    bad = _fill({"access": "gated_sales", "verdict": "buildable_with_approval", "blocker": "gated_access"},
                {"access": ("https://acmepay.io/not-opened", "Contact sales")},
                {"access": _ev("https://acmepay.io/not-opened")})
    llm = ScriptedLLM(bad, bad)
    row = _row(confidence="medium")
    out = _verifier(tmp_path, llm, make_pool).verify(APP, row, _evidence(row))
    assert len(llm.calls) == 2 and out.retried and out.status == "kept"
    assert out.row["access"] == "self_serve" and "pass-2 values kept" in out.row["notes"]


# ---------- run driver, resume, CLI ----------

def _input_dir(tmp_path, rows, review=None):
    src = tmp_path / "pass2"
    (src / "evidence").mkdir(parents=True)
    (src / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    for r in rows:
        (src / "evidence" / f"{r['slug']}.json").write_text(json.dumps(_evidence(r)))
    if review:
        (src / "review.jsonl").write_text("".join(json.dumps(x) + "\n" for x in review))
    return src


def _lines(path):
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


APP2 = {**APP, "id": 8, "slug": "acme-books", "name": "Acme Books"}
ROW2 = {**_row(), "id": 8, "slug": "acme-books", "name": "Acme Books"}


def test_browse_run_writes_logs_and_resume_skips_done_rows(tmp_path):
    src = _input_dir(tmp_path, [CLEAN_ROW, ROW2], review=[{"slug": "acme-books",
                                                            "rules_fired": ["unsupported_evidence"]}])
    out_dir = tmp_path / "pass3"
    make_pool, log = _pool_factory()
    summary = browse_run([(APP, CLEAN_ROW), (APP2, ROW2)], _verifier(out_dir, ScriptedLLM(), make_pool), src,
                         out_dir)
    assert summary["copied"] == 1 and summary["kept"] == 1 and summary["flagged"] == 1
    assert summary["flag_reasons"] == {"unsupported_or_invalid": 1} and summary["cloud_sessions"] == 0
    rows = {r["slug"]: r for r in _lines(out_dir / "rows.jsonl")}
    assert rows["acme-pay"] == CLEAN_ROW
    flags = {r["slug"]: r for r in _lines(out_dir / "flags.jsonl")}
    assert flags["acme-pay"]["flagged"] is False and flags["acme-books"]["reasons"] == ["unsupported_or_invalid"]
    recs = {r["slug"]: r for r in _lines(out_dir / "browse.jsonl")}
    assert recs["acme-pay"]["status"] == "copied" and recs["acme-pay"]["flagged"] is False
    b = recs["acme-books"]
    for key in ("flag_reasons", "pages", "fields_changed", "fields_confirmed", "before", "after", "cloud_used",
                "cloud_sessions", "llm_calls", "est_cost_usd", "exa_cost_usd", "wall_s", "status"):
        assert key in b
    for p in b["pages"]:
        assert {"url", "browser", "why_cloud", "text_len", "screenshot", "title"} <= set(p)
    ev = json.loads((out_dir / "evidence" / "acme-books.json").read_text())
    assert ev["browse"]["flags"][0]["reason"] == "unsupported_or_invalid"

    log.clear()
    llm2 = ScriptedLLM()
    again = browse_run([(APP, CLEAN_ROW), (APP2, ROW2)], _verifier(out_dir, llm2, make_pool), src, out_dir)
    assert again["skipped_done"] == 2 and llm2.calls == [] and log == []
    assert len(_lines(out_dir / "rows.jsonl")) == 2


def test_cli_dry_run_needs_no_keys_or_chrome(tmp_path, monkeypatch):
    apps_csv = tmp_path / "apps.csv"
    with apps_csv.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(APP))
        w.writeheader()
        w.writerows([APP, APP2])
    env = tmp_path / "empty.env"
    env.write_text("")
    monkeypatch.setenv("ENV_FILE", str(env))
    monkeypatch.delenv("BROWSER_USE_API_KEY", raising=False)
    monkeypatch.setattr(cli, "APPS_CSV", apps_csv)

    def no_real_browser(*a, **k):
        raise AssertionError("dry run must not start a real browser")
    monkeypatch.setattr(browse, "BrowserUseDriver", no_real_browser)
    src = _input_dir(tmp_path, [CLEAN_ROW, {**ROW2, "confidence": "medium"}])
    args = ["browse", "--dry-run", "--from-dir", str(src), "--out-dir", str(tmp_path), "--run-id", "r3"]
    assert cli.main(args) == 0
    recs = {r["slug"]: r for r in _lines(tmp_path / "r3" / "browse.jsonl")}
    assert recs["acme-pay"]["status"] == "copied"
    assert recs["acme-books"]["flag_reasons"] == ["confidence_not_high"] and recs["acme-books"]["pages"]
    for r in _lines(tmp_path / "r3" / "rows.jsonl"):
        assert validate_row(r, {k: r[k] for k in ("id", "slug", "name", "category")}) == []
    assert cli.main(args) == 0  # resume: nothing left to do
    assert len(_lines(tmp_path / "r3" / "rows.jsonl")) == 2


# ---------- no real product names in browse code or fixtures ----------

def _strings(path: Path) -> str:
    """String literals (docstrings, prompts, fixtures) and comments: where a product name could be written."""
    with path.open("rb") as f:
        return "\n".join(tok.string for tok in tokenize.tokenize(f.readline)
                         if tok.type in (tokenize.STRING, tokenize.COMMENT, tokenize.FSTRING_MIDDLE))


def test_no_names_from_the_app_list():
    with APPS_CSV.open(newline="") as f:
        apps = list(csv.DictReader(f))
    names = {a["name"] for a in apps} | {a["slug"] for a in apps}
    names |= {re.sub(r"\s*\(.*\)", "", n) for n in names}
    root = Path(__file__).resolve().parent.parent
    texts = {str(p.relative_to(root)): _strings(p) for p in (
        Path(__file__), root / "agent" / "browse.py", root / "agent" / "browse_flags.py", root / "agent" / "fakes.py")}
    texts["agent/prompts.py (browse)"] = (root / "agent" / "prompts.py").read_text().split("pass-3")[-1]
    for where, text in texts.items():
        for n in names:
            assert not re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", text, re.I), f"{n!r} in {where}"
