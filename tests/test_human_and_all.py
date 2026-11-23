"""needs_human flags, the fixed-seed spot check, input CSV validation, the final export and `agent all`.
Offline only: synthetic apps, fake LLM / tools / browser."""

from __future__ import annotations

import copy
import csv
import json
import re
import tokenize
from pathlib import Path

import pytest

from agent import browse, cli
from agent.config import APPS_CSV, ConfigError, Settings
from agent.human import REASONS, classify_needs_human, export_final, schema_columns, write_needs_human, write_spot_check
from agent.research import APP_COLUMNS, validate_apps_csv
from agent.validate import validate_row

API = "https://docs.acmepay.io/api"
APP = {"id": 7, "slug": "acme-pay", "name": "Acme Pay", "category": "finance_and_fintech",
       "category_label": "Finance and Fintech", "source_hint": "acmepay.io", "start_url": "https://acmepay.io"}
ROW = {
    "id": 7, "slug": "acme-pay", "name": "Acme Pay", "category": "finance_and_fintech",
    "one_liner": "Acme Pay is a payments API for accepting cards and sending payouts.",
    "auth_methods": ["api_key"], "primary_auth": "api_key", "access": "self_serve", "api_types": ["rest"],
    "has_public_docs": True, "api_breadth": "moderate", "docs_url": API, "mcp_status": "none", "mcp_url": None,
    "verdict": "buildable_now", "blocker": "none",
    "evidence": {k: [{"url": API, "source_type": "official_docs"}]
                 for k in ("auth", "access", "api_surface", "mcp", "verdict")},
    "confidence": "high", "requires_end_user_admin": False,
    "composio_toolkit": {"status": "none", "toolkit_slug": None, "sibling_slugs": [],
                         "checked_at": "2026-11-23T10:41:38Z", "evidence_url": None},
    "notes": "",
}


def _row(**kw) -> dict:
    return {**copy.deepcopy(ROW), **kw}


def _ids(rec: dict) -> list[str]:
    return [r["id"] for r in rec["reasons"]]


def _write_csv(path: Path, rows: list[dict], fields=APP_COLUMNS) -> Path:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fields), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path


def _lines(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


# ---------- validate_apps_csv ----------

def test_valid_csv_and_link_alias(tmp_path):
    two = [APP, {**APP, "id": 8, "slug": "acme-books", "name": "Acme Books", "start_url": ""}]
    apps = validate_apps_csv(_write_csv(tmp_path / "a.csv", two))
    assert [a["id"] for a in apps] == [7, 8] and apps[1]["start_url"] == ""
    assert list(apps[0]) == list(APP_COLUMNS)

    minimal = [{"id": "3", "slug": "acme-pay", "name": "Acme Pay", "category": "finance_and_fintech",
                "link": "https://acmepay.io/"}]
    apps = validate_apps_csv(_write_csv(tmp_path / "b.csv", minimal, ("id", "slug", "name", "category", "link")))
    assert apps == [{"id": 3, "slug": "acme-pay", "name": "Acme Pay", "category": "finance_and_fintech",
                     "category_label": "", "source_hint": "", "start_url": "https://acmepay.io/"}]


def test_real_app_list_validates():
    assert len(validate_apps_csv(APPS_CSV)) == 100


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        validate_apps_csv(tmp_path / "nope.csv")


def test_empty_file(tmp_path):
    (tmp_path / "e.csv").write_text("")
    with pytest.raises(ConfigError, match="no header"):
        validate_apps_csv(tmp_path / "e.csv")


def test_not_utf8(tmp_path):
    (tmp_path / "x.csv").write_bytes(b"id,slug,name,category,start_url\n1,acme,\xff\xfe,crm_and_sales,\n")
    with pytest.raises(ConfigError, match="UTF-8"):
        validate_apps_csv(tmp_path / "x.csv")


def test_missing_columns_listed(tmp_path):
    path = _write_csv(tmp_path / "m.csv", [APP], ("id", "slug", "name"))
    with pytest.raises(ConfigError) as e:
        validate_apps_csv(path)
    msg = str(e.value)
    assert "category, start_url" in msg and "Required: id, slug, name, category, start_url" in msg
    assert "'link' is accepted" in msg


@pytest.mark.parametrize("change, expect", [
    ({"slug": "Acme_Pay"}, r"line 2: slug 'Acme_Pay' must be lowercase"),
    ({"slug": ""}, r"line 2: slug '' must be"),
    ({"name": ""}, r"line 2: name is empty"),
    ({"category": "payments"}, r"line 2: category 'payments' is not one of: crm_and_sales, "),
    ({"start_url": "acmepay.io"}, r"line 2: start_url 'acmepay.io' must start with http:// or https://"),
    ({"start_url": "ftp://acmepay.io"}, r"must start with http:// or https://"),
    ({"start_url": "https://"}, r"line 2: start_url 'https://' has no valid hostname"),
    ({"start_url": "https://acme pay/"}, r"has no valid hostname"),
    ({"id": "abc"}, r"line 2: id 'abc' must be an integer from 1 to 100"),
    ({"id": "0"}, r"id '0' must be an integer from 1 to 100"),
    ({"id": "101"}, r"id '101' must be"),
])
def test_bad_values_name_the_line(tmp_path, change, expect):
    with pytest.raises(ConfigError, match=expect):
        validate_apps_csv(_write_csv(tmp_path / "bad.csv", [{**APP, **change}]))


def test_duplicates_and_all_errors_reported(tmp_path):
    rows = [APP, {**APP, "name": "Acme Pay 2"}, {**APP, "id": 9, "slug": "acme-x", "category": "nope"}]
    with pytest.raises(ConfigError) as e:
        validate_apps_csv(_write_csv(tmp_path / "d.csv", rows))
    msg = str(e.value)
    assert "line 3: duplicate id 7 (first on line 2)" in msg
    assert "line 3: duplicate slug 'acme-pay' (first on line 2)" in msg
    assert "line 4: category 'nope'" in msg and "3 invalid value(s)" in msg


def test_cli_rejects_bad_input_before_any_stage(tmp_path, capsys):
    bad = _write_csv(tmp_path / "bad.csv", [{**APP, "slug": "Bad Slug"}])
    args = ["all", "--dry-run", "--input", str(bad), "--run-id", "x", "--out-dir", str(tmp_path / "runs")]
    assert cli.main(args) == 2
    assert "line 2: slug 'Bad Slug'" in capsys.readouterr().err
    assert not (tmp_path / "runs" / "x").exists()


# ---------- classify_needs_human ----------

def test_clean_high_confidence_row_needs_no_human():
    rec = classify_needs_human(_row(), browse={"flagged": False, "status": "copied", "pages": []})
    assert rec["needs_human"] is False and rec["reason"] is None and rec["reasons"] == []
    assert rec["slug"] == "acme-pay" and rec["evidence_urls"] == [API]
    assert (rec["confidence"], rec["access"], rec["verdict"]) == ("high", "self_serve", "buildable_now")


@pytest.mark.parametrize("fields", [
    {"access": "gated_sales", "verdict": "buildable_with_approval", "blocker": "gated_access"},
    {"access": "gated_partner", "verdict": "buildable_with_approval", "blocker": "gated_access"},
    {"verdict": "buildable_with_approval"},
])
def test_sales_or_partner_gate(fields):
    rec = classify_needs_human(_row(**fields))
    assert _ids(rec) == ["sales_or_partner_gate"] and rec["reason"] == REASONS["sales_or_partner_gate"]


@pytest.mark.parametrize("conf", ["medium", "low"])
def test_confidence_not_high(conf):
    assert _ids(classify_needs_human(_row(confidence=conf))) == ["confidence_not_high"]


@pytest.mark.parametrize("fields", [{"access": "unknown"}, {"primary_auth": "unknown"}, {"mcp_status": "unknown"}])
def test_unknown_deciding_field(fields):
    rec = classify_needs_human(_row(**fields))
    assert _ids(rec) == ["unknown_deciding_field"] and "=unknown" in rec["reasons"][0]["detail"]


@pytest.mark.parametrize("b, fires", [
    ({"flagged": True, "status": "kept", "pages": [{"usable": True}]}, True),
    ({"flagged": True, "status": "failed", "pages": []}, True),
    ({"flagged": True, "status": "confirmed", "pages": [{"usable": False}]}, True),
    ({"flagged": True, "status": "confirmed", "pages": [{"usable": True}]}, False),
    ({"flagged": False, "status": "copied", "pages": []}, False),
])
def test_browse_unresolved(b, fires):
    assert _ids(classify_needs_human(_row(), browse=b)) == (["browse_unresolved"] if fires else [])


def test_browse_block_from_evidence_file_is_used():
    ev = {"browse": {"flagged": True, "pages": []}}
    assert _ids(classify_needs_human(_row(), evidence=ev)) == ["browse_unresolved"]


@pytest.mark.parametrize("notes", [
    "The pricing page and the API docs conflict on which plan includes the API.",
    "Rate limits are inconsistent in the docs (100 vs 1,000 per minute).",
    "The API reference sits behind a login, so it could not be read.",
    "The v1 API is deprecated in favour of v2.",
    "Customers are migrating to the new platform API by next year.",
    "The browser pass could not confirm the plan that includes API access.",
    "An earlier claim about partner review was not re-checked in the rendered pages.",
])
def test_conflicting_or_caveated_docs(notes):
    rec = classify_needs_human(_row(notes=notes))
    assert _ids(rec) == ["conflicting_or_caveated_docs"]


@pytest.mark.parametrize("notes", [
    "Whether creating a key needs admin rights is not shown in the fetched pages.",
    "Browser pass confirmed self-serve signup and API key auth.",
    "Webhooks exist alongside the REST API.",
])
def test_plain_notes_do_not_fire(notes):
    assert classify_needs_human(_row(notes=notes))["needs_human"] is False


def test_multiple_reasons_join():
    rec = classify_needs_human(_row(access="gated_sales", verdict="buildable_with_approval", blocker="gated_access",
                                    confidence="low", mcp_status="unknown"))
    assert _ids(rec) == ["sales_or_partner_gate", "confidence_not_high", "unknown_deciding_field"]
    assert rec["reason"].count("; ") == 2


def _run_dir(tmp_path: Path, n: int = 14) -> Path:
    run = tmp_path / "run"
    (run / "evidence").mkdir(parents=True)
    rows, browse_lines = [], []
    for i in range(1, n + 1):
        slug = f"acme-{i:02d}"
        row = _row(id=i, slug=slug, name=f"Acme {i}", confidence="medium" if i % 3 == 0 else "high")
        rows.append(row)
        shot = f"screenshots/{slug}/01-abc.jpg"
        (run / "evidence" / f"{slug}.json").write_text(json.dumps({
            "snippets": {"auth": [{"url": API, "quote": "Authenticate with an API key.", "screenshot": shot}]},
            "browse": {"flagged": i % 3 == 0, "pages": [{"url": API, "usable": True, "screenshot": shot}]}}))
        browse_lines.append({"slug": slug, "flagged": i % 3 == 0, "status": "confirmed" if i % 3 == 0 else "copied",
                             "pages": [{"url": API, "usable": True}] if i % 3 == 0 else []})
    (run / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (run / "browse.jsonl").write_text("".join(json.dumps(b) + "\n" for b in browse_lines))
    return run


def test_write_needs_human(tmp_path):
    run = _run_dir(tmp_path)
    summary = write_needs_human(run)
    assert summary["total"] == 14 and summary["needs_human_count"] == 4
    assert summary["by_reason"] == {"confidence_not_high": 4}
    lines = _lines(run / "needs_human.jsonl")
    assert len(lines) == 14 and json.loads((run / "needs_human.json").read_text())["rows"] == lines


# ---------- spot check ----------

def test_spot_check_is_deterministic_and_excludes_gold_and_holdout(tmp_path):
    run = _run_dir(tmp_path)
    write_needs_human(run)
    gold, holdout = tmp_path / "gold.jsonl", tmp_path / "holdout.jsonl"
    gold.write_text("".join(json.dumps({"slug": f"acme-{i:02d}"}) + "\n" for i in (1, 2, 3)))
    holdout.write_text(json.dumps({"slug": "acme-04"}) + "\n")

    md = write_spot_check(run, gold, holdout, n=6, seed=42)
    data = json.loads((run / "spot_check_6.json").read_text())
    picked = [it["slug"] for it in data["items"]]
    assert md == run / "spot_check_6.md"
    assert data["status"] == "pending_human_check" and data["seed"] == 42 and data["excluded_count"] == 4
    assert len(picked) == 6 and not {"acme-01", "acme-02", "acme-03", "acme-04"} & set(picked)
    import random
    assert picked == random.Random(42).sample(sorted(f"acme-{i:02d}" for i in range(5, 15)), 6)

    first = md.read_text()
    (run / "rows.jsonl").write_text("".join(reversed((run / "rows.jsonl").read_text().splitlines(True))))
    write_spot_check(run, gold, holdout, n=6, seed=42)
    assert md.read_text() == first  # input order does not change the sample

    assert "pending human verification" in first and "not self-graded" in first
    assert first.count("- [ ] Confirmed against official docs") == 6
    assert first.count("- [ ] Needs correction (notes: ___)") == 6
    assert "[x]" not in first.lower()
    assert "> Authenticate with an API key." in first and "screenshots/acme-" in first
    item = data["items"][0]
    assert item["human_check"] == {"confirmed": None, "needs_correction": None, "notes": ""}
    assert {"auth_methods", "primary_auth", "access", "api_types", "has_public_docs", "mcp_status", "mcp_url",
            "verdict", "blocker", "confidence", "needs_human", "needs_human_reason", "notes"} <= set(item)


# ---------- export ----------

def test_export_final(tmp_path):
    rows = [_row(id=9, slug="acme-books", name="Acme Books", auth_methods=["oauth2", "api_key"],
                 primary_auth="oauth2", notes='Has "quotes", commas\nand a newline.'), _row()]
    jpath, cpath = export_final(rows, tmp_path / "out")
    data = json.loads(jpath.read_text())
    assert [r["id"] for r in data] == [7, 9] and data[1] == rows[0]
    for r in data:
        assert validate_row(r, {k: r[k] for k in ("id", "slug", "name", "category")}) == []
    with cpath.open(newline="") as f:
        reader = csv.DictReader(f)
        got = list(reader)
    assert reader.fieldnames == schema_columns()
    assert got[1]["auth_methods"] == "oauth2;api_key" and got[1]["has_public_docs"] == "true"
    assert got[1]["mcp_url"] == "" and got[1]["notes"] == rows[0]["notes"]
    assert json.loads(got[1]["evidence"]) == rows[0]["evidence"]
    assert json.loads(got[0]["composio_toolkit"]) == ROW["composio_toolkit"]


# ---------- agent all ----------

def test_all_dry_run_end_to_end(tmp_path, monkeypatch, capsys):
    env = tmp_path / "empty.env"
    env.write_text("")
    monkeypatch.setenv("ENV_FILE", str(env))
    monkeypatch.delenv("BROWSER_USE_API_KEY", raising=False)
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: Settings()))

    def no_real_browser(*a, **k):
        raise AssertionError("dry run must not start a real browser")
    monkeypatch.setattr(browse, "BrowserUseDriver", no_real_browser)

    apps = [APP, {**APP, "id": 8, "slug": "acme-books", "name": "Acme Books", "category": "ecommerce",
                  "start_url": "https://acmebooks.io"}]
    inp = _write_csv(tmp_path / "custom.csv", apps)
    out = tmp_path / "runs"
    args = ["all", "--dry-run", "--input", str(inp), "--run-id", "e2e", "--out-dir", str(out)]
    assert cli.main(args) == 0
    printed = json.loads(capsys.readouterr().out)
    run = out / "e2e"
    for name in ("rows.jsonl", "final.json", "final.csv", "needs_human.jsonl", "needs_human.json", "summary.json",
                 "pass1/input.csv", "pass1/rows.jsonl", "pass2/rows.jsonl", "pass3/rows.jsonl",
                 "pass3/browse.jsonl"):
        assert (run / name).is_file(), name
    rows = _lines(run / "rows.jsonl")
    assert sorted(r["slug"] for r in rows) == ["acme-books", "acme-pay"]
    for r in rows:
        assert validate_row(r, {k: r[k] for k in ("id", "slug", "name", "category")}) == []
    assert len(json.loads((run / "final.json").read_text())) == 2
    summary = json.loads((run / "summary.json").read_text())
    assert summary == printed and summary["ok"] is True and summary["rows_final"] == 2
    assert set(summary["stages"]) == {"run", "review", "browse"}
    assert all("wall_s" in s for s in summary["stages"].values())
    assert summary["totals"]["input_tokens"] > 0 and summary["needs_human_count"] == len(_lines(
        run / "needs_human.jsonl")) - sum(not x["needs_human"] for x in _lines(run / "needs_human.jsonl"))

    # resume: every stage skips what it already did
    assert cli.main(args) == 0
    again = json.loads(capsys.readouterr().out)
    assert [again["stages"][k]["skipped_done"] for k in ("run", "review", "browse")] == [2, 2, 2]
    assert len(_lines(run / "rows.jsonl")) == 2


def test_review_picks_up_input_csv_of_the_input_run(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "APPS_CSV", tmp_path / "missing.csv")  # fallback would fail if it were used
    src = tmp_path / "src"
    src.mkdir()
    _write_csv(src / "input.csv", [APP])
    (src / "rows.jsonl").write_text(json.dumps(ROW) + "\n")
    assert cli.input_csv_for(None, src) == src / "input.csv"
    assert cli.input_csv_for(tmp_path / "x.csv", src) == tmp_path / "x.csv"
    assert cli.input_csv_for(None, tmp_path) == tmp_path / "missing.csv"


# ---------- no real product names in the new code or fixtures ----------

def _strings(path: Path) -> str:
    with path.open("rb") as f:
        return "\n".join(tok.string for tok in tokenize.tokenize(f.readline)
                         if tok.type in (tokenize.STRING, tokenize.COMMENT, tokenize.FSTRING_MIDDLE))


def test_no_names_from_the_app_list():
    root = Path(__file__).resolve().parent.parent
    names = set()
    for p in (APPS_CSV, root / "schema" / "gold_sample.jsonl", root / "schema" / "holdout_sample.jsonl"):
        if p.suffix == ".csv":
            with p.open(newline="") as f:
                names |= {a[k] for a in csv.DictReader(f) for k in ("name", "slug")}
        else:
            names |= {json.loads(x)["slug"] for x in p.read_text().splitlines() if x.strip()}
    names |= {re.sub(r"\s*\(.*\)", "", n) for n in names}
    for path in (Path(__file__), root / "agent" / "human.py", root / "agent" / "pipeline.py"):
        text = _strings(path)
        for n in names:
            assert not re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", text, re.I), f"{n!r} in {path.name}"
