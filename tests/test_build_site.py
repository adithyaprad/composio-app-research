"""Tests for scripts/build_site.py and scripts/check_site.py.

Helper tests use synthetic fixtures (acme-pay, beta-crm, ...). The build tests read the
real repo data offline and compare key numbers with the source files.
"""

from __future__ import annotations

import base64
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


def _embedded(html: str, element_id: str) -> str:
    found = re.search(rf'<script type="[^"]+" id="{element_id}">(.*?)</script>', html, re.S)
    assert found, element_id
    return found.group(1)


def test_every_row_is_rendered_server_side(built):
    html = (built / "index.html").read_text()
    assert html.count('<tr class="row"') == 100
    assert len(json.loads(_embedded(html, "dl-rows"))) == 100


# Python mirror of the page's JS download builders (same rules as agent.human.export_final).
def _js_cell(key: str, v) -> str:
    if v is None:
        return ""
    if key in ("auth_methods", "api_types"):
        return ";".join(map(str, v))
    if key in ("evidence", "composio_toolkit") or isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _js_quote(s: str) -> str:
    return '"' + s.replace('"', '""') + '"' if re.search(r'[",\r\n]', s) else s


def test_embedded_downloads_equal_site_files_byte_for_byte(built):
    """The lone page can rebuild every download, and each equals the file in site/ (scripts/check_downloads.py
    checks the same thing in a real browser)."""
    html = (built / "index.html").read_text(encoding="utf-8")
    rows = json.loads(_embedded(html, "dl-rows"))
    cols = json.loads(_embedded(html, "dl-cols"))
    needs = json.loads(_embedded(html, "dl-needs"))
    files = {
        "data/final.json": json.dumps(rows, indent=2, ensure_ascii=False) + "\n",
        "data/needs_human.json": json.dumps(needs, indent=2, ensure_ascii=False) + "\n",
        "data/final.csv": "".join(",".join(_js_quote(c) for c in line) + "\r\n" for line in
                                  [cols] + [[_js_cell(c, r.get(c)) for c in cols] for r in rows]),
        "data/patterns.json": _embedded(html, "dl-patterns"),
        "data/schema.md": _embedded(html, "dl-schema"),
        "llms.txt": _embedded(html, "dl-llms"),
    }
    for rel, text in files.items():
        assert text.encode("utf-8") == (built / rel).read_bytes(), rel


def test_download_links_resolve_and_name_their_files(built):
    html = (built / "index.html").read_text()
    links = re.findall(r'<a [^>]*href="([^"]+)"[^>]*download="([^"]+)"[^>]*data-dl="([^"]+)"', html)
    assert {name for _, _, name in links} == {"final.json", "final.csv", "schema.md", "patterns.json",
                                              "needs_human.json", "llms.txt"}
    for href, download, name in links:
        assert (built / href).is_file(), href
        assert download == name == Path(href).name


def test_no_native_select_and_custom_filters_present(built):
    html = (built / "index.html").read_text()
    assert "<select" not in html
    chips = re.findall(r'<button type="button" class="fchip" data-filter="([a-z_]+)"[^>]*'
                       r'aria-pressed="(true|false)"', html)
    assert {k for k, _ in chips} == {"verdict", "access", "mcp_status", "needs_human", "composio", "category"}
    assert sum(p == "true" for _, p in chips) == 6  # one "All" chip pressed per group
    assert 'id="clear"' in html


@pytest.mark.parametrize("value,cls", [(0.95, "good"), (0.9, "good"), (0.89, "mid"), (0.8, "mid"), (0.79, "weak")])
def test_accuracy_colour_bands(value, cls):
    assert bs.acc_class(value) == cls


def test_accuracy_cells_use_green_class_at_ninety(built):
    html = (built / "index.html").read_text()
    for src in re.findall(r'<td class="acc good[^"]*"><span class="bar"[^>]*></span><span data-src="([^"]+)"', html):
        assert float(cs.evaluate(src)) >= 0.9
    assert re.search(r'<td class="acc good', html)
    assert "green " in html and "amber " in html and "red under" in html


def test_diagram_nodes_have_one_panel_each_and_no_duplicate_explanations(built):
    html = (built / "index.html").read_text()
    nodes = re.findall(r'<button type="button" class="node node-(stage|human)" id="nb-([a-z0-9]+)" '
                       r'data-panel="sp-([a-z0-9]+)" aria-controls="sp-[a-z0-9]+" aria-expanded="(true|false)"', html)
    assert len(nodes) == 12 and sum(kind == "human" for kind, *_ in nodes) == 4
    assert [e for *_, e in nodes].count("true") == 1 and nodes[0][3] == "true"
    assert html.count('<section class="sp ') == 12
    for _, key, panel, _ in nodes:
        assert key == panel
        assert re.search(rf'<section class="sp sp-(stage|human)" id="sp-{key}"', html), key
    for heading in ("What it does", "Why it exists", "How it helps accuracy"):
        assert html.count(f"<h5>{heading}</h5>") == 12
    assert "What each stage does" not in html and "stage-d" not in html


def test_resume_hidden_with_warning_when_file_missing(built, capsys, tmp_path):
    assert 'href="resume.pdf"' not in (built / "index.html").read_text()
    out = tmp_path / "site"
    bs.write_site(ROOT, out)
    assert "Resume button hidden" in capsys.readouterr().err


def test_resume_hidden_when_url_empty(tmp_path, monkeypatch, capsys):
    out = tmp_path / "site"
    out.mkdir()
    (out / "resume.pdf").write_bytes(b"%PDF-1.4 placeholder")
    monkeypatch.setenv("RESUME_URL", "")
    bs.write_site(ROOT, out)
    assert 'href="resume.pdf"' not in (out / "index.html").read_text()
    assert "RESUME_URL is empty" in capsys.readouterr().err


def test_resume_linked_and_static_asset_kept_when_present(tmp_path, monkeypatch):
    monkeypatch.delenv("RESUME_URL", raising=False)
    out = tmp_path / "site"
    out.mkdir()
    (out / "resume.pdf").write_bytes(b"%PDF-1.4 placeholder")
    bs.write_site(ROOT, out)
    bs.write_site(ROOT, out)  # a rebuild must not delete the static asset
    assert (out / "resume.pdf").read_bytes() == b"%PDF-1.4 placeholder"
    html = (out / "index.html").read_text()
    assert '<a class="btn btn-big btn-second" href="resume.pdf" download="resume.pdf" data-resume>' in html
    b64 = re.search(r'<script type="application/pdf;base64" id="dl-resume">([^<]+)</script>', html).group(1)
    assert base64.b64decode(b64) == b"%PDF-1.4 placeholder"  # downloads from the lone page too
    assert cs.run(out, verbose=False)["local_bad"] == []


def test_links_section_last_and_repo_links_early(built):
    html = (built / "index.html").read_text()
    sections = re.findall(r'<section id="([a-z]+)"', html)
    assert sections[-1] == "links"
    assert 'href="#links"' in html
    repo = f'href="{bs.REPO_URL}"'
    assert html.index(repo) < html.index('<section id="accuracy"')  # top nav and hero
    assert 'class="top-gh"' in html and html.count(bs.esc(bs.REPO_LABEL)) >= 3


def test_no_leaks_and_no_remote_resources(built, page):
    for f in built.rglob("*"):
        if f.is_file() and f.name not in bs.STATIC_ASSETS:
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
    # site/resume.pdf is a hand-placed static asset and deliberately not scanned.
    for rel in ("scripts/build_site.py", "scripts/check_site.py", "scripts/check_downloads.py",
                "tests/test_build_site.py"):
        # The repo host's name is allowed on the single REPO_LABEL line (it is the repo host, not a research result).
        text = "\n".join(line for line in (ROOT / rel).read_text().splitlines()
                         if not line.startswith("REPO_LABEL = "))
        for name in names:
            if len(name) > 3:
                assert not re.search(rf"\b{re.escape(name)}\b", text), (rel, name)


def test_tools_section_lists_logged_models_and_no_proof_run_card(built):
    html = (built / "index.html").read_text()
    assert '<section id="tools"' in html and '<section id="composio"' not in html
    assert 'href="#tools"' in html
    for _, model in bs.pass_models(bs.ROOT if hasattr(bs, "ROOT") else ROOT):
        assert bs.esc(model) in html
    assert "A live end-to-end run" not in html and "proof-e2e" not in html
