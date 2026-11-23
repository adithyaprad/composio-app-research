"""Tests for scripts/build_site.py and scripts/check_site.py.

Helper tests use synthetic fixtures (acme-pay, beta-crm, ...). The build tests read the
real repo data offline and compare key numbers with the source files.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from scripts import build_site as bs
from scripts import check_site as cs

ROOT = Path(__file__).resolve().parent.parent


def make_row(i: int, slug: str, **over) -> dict:
    row = {
        "id": i, "slug": slug, "name": slug.replace("-", " ").title(), "category": "finance_and_fintech",
        "one_liner": "Synthetic fixture row.", "auth_methods": ["oauth2", "api_key"], "primary_auth": "oauth2",
        "access": "self_serve", "api_types": ["rest"], "has_public_docs": True, "api_breadth": "broad",
        "docs_url": f"https://docs.{slug}.example/", "mcp_status": "official", "mcp_url": f"https://{slug}.example/mcp",
        "verdict": "buildable_now", "blocker": "none", "evidence": {}, "confidence": "high",
        "requires_end_user_admin": False,
        "composio_toolkit": {"status": "exists", "toolkit_slug": slug, "sibling_slugs": [], "checked_at": "x",
                             "evidence_url": None},
        "notes": "",
    }
    row.update(over)
    return row


NONE_TK = {"status": "none", "toolkit_slug": None, "sibling_slugs": [], "checked_at": "x", "evidence_url": None}
ROWS = [
    make_row(1, "acme-pay", composio_toolkit=NONE_TK),
    make_row(2, "beta-crm", category="crm_and_sales", access="gated_sales", verdict="buildable_with_approval",
             blocker="gated_access"),
    make_row(3, "gamma-desk", category="crm_and_sales", access="gated_partner", verdict="buildable_with_approval",
             blocker="gated_access", composio_toolkit=NONE_TK, auth_methods=["oauth2"]),
    make_row(4, "delta-mail", auth_methods=["api_key"], primary_auth="api_key", mcp_status="none", mcp_url=None,
             composio_toolkit=NONE_TK),
    make_row(5, "epsilon-ads", access="self_serve_paid", composio_toolkit=NONE_TK, mcp_status="community"),
]


# ---------------------------------------------------------------- helpers on synthetic fixtures


def test_easy_wins_need_buildable_official_and_uncovered():
    assert [r["slug"] for r in bs.easy_wins(ROWS)] == ["acme-pay"]


def test_outreach_split_by_gate():
    out = bs.outreach(ROWS)
    assert [r["slug"] for r in out["gated_sales"]] == ["beta-crm"]
    assert [r["slug"] for r in out["gated_partner"]] == ["gamma-desk"]
    assert out["other"] == []


def test_category_access_sorted_most_self_serve_first():
    cats = bs.category_access(ROWS, ["crm_and_sales", "finance_and_fintech"])
    assert [(c["category"], c["self_serve"], c["gated"], c["total"]) for c in cats] == [
        ("finance_and_fintech", 3, 0, 3), ("crm_and_sales", 0, 2, 2)]


def test_auth_summary_counts_combos():
    a = bs.auth_summary(ROWS)
    assert a == {"total": 5, "oauth2_primary": 4, "api_key_primary": 1, "oauth2_and_api_key": 3,
                 "api_key_only": 1, "oauth2_only": 1}


@pytest.mark.parametrize("accs,tag", [([0.95, 0.6], "rough"), ([0.85, 1.0], "moderate"), ([0.9, 0.9], "strong")])
def test_tag_for_uses_weakest_accuracy(accs, tag):
    assert bs.tag_for(accs) == tag


@pytest.mark.parametrize("unit,gold,agent,expected", [
    ("api", {"api_types": ["rest", "graphql"], "has_public_docs": True},
     {"api_types": ["rest"], "has_public_docs": True}, "agent listed REST only; gold also has GraphQL"),
    ("access", "gated_partner", "self_serve", "agent said self-serve; gold says partner-gated"),
    ("mcp_status", "official", "none", "agent said no MCP; gold says official MCP"),
    ("verdict", "not_buildable", "buildable_now", "agent said buildable now; gold says not buildable"),
    ("blocker", "preview_or_unstable", "none", "agent said no blocker; gold says preview / unstable"),
    ("auth", {"auth_methods": ["api_key", "jwt_service_account"], "primary_auth": "jwt_service_account"},
     {"auth_methods": ["api_key", "jwt_service_account"], "primary_auth": "api_key"},
     "same methods, but agent picked API key as primary; gold picks JWT / service account"),
    ("auth", {"auth_methods": ["oauth2", "api_key"], "primary_auth": "oauth2"},
     {"auth_methods": ["api_key"], "primary_auth": "api_key"},
     "agent listed API key only; gold also has OAuth2 (gold's primary is OAuth2)"),
    ("auth", {"auth_methods": ["custom_other"], "primary_auth": "custom_other"},
     {"auth_methods": ["api_key"], "primary_auth": "api_key"}, "agent said API key; gold says Custom"),
])
def test_miss_reason_is_generated_from_values(unit, gold, agent, expected):
    assert bs.miss_reason(unit, gold, agent) == expected


def _score(correct: dict[str, bool], misses: dict[str, list[str]] | None = None) -> dict:
    misses = misses or {}
    apps = []
    for slug, ok in correct.items():
        units = {u: {"match": u not in misses.get(slug, []), "gold": "self_serve", "agent": "gated_sales"}
                 for u in bs.CRITICAL}
        apps.append({"slug": slug, "name": slug.title(), "row_correct": ok, "units": units, "label_notes": "n"})
    return {"apps": apps}


def test_regressions_and_critical_misses():
    before = _score({"acme-pay": True, "beta-crm": True, "gamma-desk": False})
    after = _score({"acme-pay": True, "beta-crm": False, "gamma-desk": False},
                   {"beta-crm": ["access"], "gamma-desk": ["access", "verdict"]})
    assert bs.regressions(before, after) == ["beta-crm"]
    misses = bs.critical_misses(after)
    assert [(m["slug"], [u["unit"] for u in m["units"]]) for m in misses] == [
        ("beta-crm", ["access"]), ("gamma-desk", ["access", "verdict"])]


def test_changed_rows_excludes_scored_and_lists_fields():
    before = [make_row(1, "acme-pay"), make_row(2, "beta-crm"), make_row(3, "gamma-desk")]
    after = [make_row(1, "acme-pay", mcp_status="none", mcp_url=None), make_row(2, "beta-crm"),
             make_row(3, "gamma-desk", mcp_url="https://gamma-desk.example/new-mcp")]
    out = bs.changed_rows(before, after, ("mcp_status", "mcp_url"), exclude={"gamma-desk"})
    assert [(c["slug"], c["fields"]) for c in out] == [("acme-pay", ["mcp_status", "mcp_url"])]


def test_gold_audit_corrections_count_only_scored_fields():
    md = """# Audit

## Fields changed

| App | Field | Old | New |
|---|---|---|---|
| Acme Pay | `mcp_status` | `none` | `official` |
| Acme Pay | `mcp_url` | `null` | url |
| Beta CRM | `auth_methods` | a | b |
| Gamma Desk | `evidence.verdict` note; `notes` | a | b |

## Rows confirmed unchanged

| App | Field |
|---|---|
| Delta Mail | `access` |
"""
    assert bs.gold_audit_corrections(md) == ["Acme Pay", "Beta CRM"]


def test_relativize_strips_root_and_replaces_input():
    obj = {"run_dir": "/x/repo/data/runs/r", "input": "/tmp/apps.csv", "nested": ["/x/repo/a.json", 3]}
    out = bs.relativize(obj, "/x/repo", {"/tmp/apps.csv": "data/runs/r/input.csv"})
    assert out == {"run_dir": "data/runs/r", "input": "data/runs/r/input.csv", "nested": ["a.json", 3]}


def test_short_label_and_url():
    assert bs.short_label("Data, SEO and Scraping") == "Data"
    assert bs.short_label("Ecommerce") == "Ecommerce"
    assert bs.short_url("https://www.acme-pay.example/docs/api/?x=1") == "acme-pay.example/docs/api"


def test_number_span_formats():
    assert bs.n(0.95, "f.json:a", "pct") == '<span data-src="f.json:a" data-fmt="pct">95%</span>'
    assert ">$0.36<" in bs.n(0.3553, "f.json:a", "usd")
    assert ">27<" in bs.n(1643.48, "f.json:a", "min")


def test_ensure_fails_the_build():
    with pytest.raises(SystemExit):
        bs.ensure(False, "holdout did not move")


def test_check_site_filters_and_compare(tmp_path, monkeypatch):
    rows = [{"slug": "acme-pay", "tags": ["a", "b"], "k": {"s": "x"}},
            {"slug": "beta-crm", "tags": ["b"], "k": {"s": "y"}}]
    assert cs.matches(rows[0], "tags@=a&k.s=x|z")
    assert not cs.matches(rows[1], "tags@=a")
    assert cs.matches(rows[1], "tags==b")
    (tmp_path / "data").mkdir()
    (tmp_path / "data/r.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    (tmp_path / "data/s.json").write_text(json.dumps({"acc": 0.95, "a": 1, "b": 2}))
    monkeypatch.setattr(cs, "ROOT", tmp_path)
    assert cs.evaluate("data/r.jsonl:count(tags@=b)") == 2
    assert cs.evaluate("data/s.json:add(a,b)") == 3
    assert cs.compare({"src": "data/s.json:acc", "fmt": "pct", "text": "95%"}) is None
    assert cs.compare({"src": "data/s.json:acc", "fmt": "pct", "text": "90%"})
    assert cs.compare({"src": "data/r.jsonl:count()", "fmt": "int", "text": "3"})


# ---------------------------------------------------------------- build from the real repo data


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("site") / "site"
    bs.write_site(ROOT, out)
    return out


def _jsonl(rel: str) -> list[dict]:
    return [json.loads(x) for x in (ROOT / rel).read_text().splitlines() if x.strip()]


class _Collect(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: dict[str, list[str]] = {}
        self.tags: list[tuple[str, dict]] = []
        self._stack: list[str | None] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append((tag, a))
        if tag in cs.VOID:
            return
        self._stack.append(a.get("data-src"))
        if a.get("data-src"):
            self.values.setdefault(a["data-src"], []).append("")

    def handle_endtag(self, tag):
        if tag not in cs.VOID and self._stack:
            self._stack.pop()

    def handle_data(self, data):
        for src in self._stack:
            if src:
                self.values[src][-1] += data


@pytest.fixture(scope="module")
def page(built) -> _Collect:
    p = _Collect()
    p.feed((built / "index.html").read_text())
    return p


def shown(page: _Collect, src: str) -> str:
    assert src in page.values, src
    return page.values[src][0].strip()


def test_key_numbers_match_source_files(page):
    rows = _jsonl(bs.P_ROWS)
    needs = json.loads((ROOT / bs.P_NEEDS).read_text())
    assert shown(page, bs.rows_src("")) == str(len(rows)) == "100"
    assert shown(page, bs.rows_src("verdict=buildable_now")) == str(sum(r["verdict"] == "buildable_now" for r in rows))
    assert shown(page, bs.rows_src("mcp_status=official")) == str(sum(r["mcp_status"] == "official" for r in rows))
    assert shown(page, f"{bs.P_NEEDS}:needs_human_count") == str(needs["needs_human_count"])
    easy = [r for r in rows if r["verdict"] == "buildable_now" and r["mcp_status"] == "official"
            and r["composio_toolkit"]["status"] == "none"]
    easy_src = bs.rows_src("verdict=buildable_now&mcp_status=official&composio_toolkit.status=none")
    assert shown(page, easy_src) == str(len(easy))


@pytest.mark.parametrize("kind", ["gold", "holdout"])
def test_field_accuracies_per_pass_match_scores(page, kind):
    for p in (1, 2, 3):
        score = json.loads((ROOT / bs.score_path(kind, p)).read_text())
        for key, entry in score["summary"]["field_accuracy"].items():
            if key in bs.CRITICAL or key in bs.MINOR:
                assert shown(page, bs.score_src(kind, p, key)) == f"{round(entry['accuracy'] * 100)}%"
        assert shown(page, f"{bs.score_path(kind, p)}:summary.n_row_correct") == str(score["summary"]["n_row_correct"])


def test_spot_check_numbers(page):
    spot = json.loads((ROOT / bs.P_SPOT).read_text())["result"]
    assert shown(page, f"{bs.P_SPOT}:result.rows_correct") == str(spot["rows_correct"]) == "10"
    assert shown(page, f"{bs.P_SPOT}:result.rows_checked") == "10"
    assert shown(page, f"{bs.P_SPOT}:result.claims_correct") == str(spot["claims_correct"]) == "40"
    assert shown(page, f"{bs.P_SPOT}:result.claims_checked") == "40"


def test_every_row_is_rendered_server_side(built):
    html = (built / "index.html").read_text()
    assert html.count('<tr class="row"') == 100
    data = json.loads(re.search(r'<script type="application/json" id="rows-data">(.*?)</script>', html, re.S)
                      .group(1).replace("<\\/", "</"))
    assert len(data) == 100


def test_no_leaks_and_no_remote_resources(built, page):
    for f in built.rglob("*"):
        if f.is_file():
            text = f.read_text()
            assert "/home/" not in text and "/tmp/" not in text, f
    for tag, attrs in page.tags:
        if tag in ("script", "img", "iframe"):
            assert not attrs.get("src"), (tag, attrs)
        if tag == "link":
            assert not re.match(r"^(https?:)?//", attrs.get("href") or ""), attrs
    assert "http://" not in "".join(a.get("src", "") or "" for _, a in page.tags)


def test_side_files_written(built):
    for rel in ("data/final.json", "data/final.csv", "data/needs_human.json", "data/patterns.json",
                "data/schema.md", "llms.txt"):
        assert (built / rel).is_file(), rel
    assert len(json.loads((built / "data/final.json").read_text())) == 100
    llms = (built / "llms.txt").read_text()
    assert llms.startswith("# ") and "data/final.json" in llms and bs.REPO_URL in llms


def test_check_site_finds_no_problems_on_fresh_build(built):
    report = cs.run(built, verbose=False)
    assert report["numbers"] > 300
    assert report["mismatches"] == []
    assert report["unsourced"] == []
    assert report["anchors_bad"] == [] and report["local_bad"] == [] and report["remote_resources"] == []


def test_check_site_catches_a_tampered_number(built, tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    for f in built.rglob("*"):
        if f.is_file():
            (site / f.relative_to(built)).parent.mkdir(parents=True, exist_ok=True)
            (site / f.relative_to(built)).write_bytes(f.read_bytes())
    html = (site / "index.html").read_text()
    src = bs.rows_src("verdict=buildable_now")
    tampered = re.sub(r'(data-src="' + re.escape(bs.esc(src)) + r'" data-fmt="int">)(\d+)', r"\g<1>99", html, count=1)
    assert tampered != html
    (site / "index.html").write_text(tampered)
    assert cs.run(site, verbose=False)["mismatches"]


def test_committed_site_is_up_to_date():
    assert bs.check(ROOT, ROOT / "site") == 0


def test_no_research_app_names_in_generator_or_tests():
    names = [r["name"] for r in _jsonl(bs.P_ROWS)]
    for rel in ("scripts/build_site.py", "scripts/check_site.py", "tests/test_build_site.py"):
        text = (ROOT / rel).read_text()
        for name in names:
            if len(name) > 3:
                assert not re.search(rf"\b{re.escape(name)}\b", text), (rel, name)
