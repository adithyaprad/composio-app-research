"""Tests for scripts/patterns.py. Fixtures are synthetic (acme-pay, beta-crm, ...), not research apps."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.patterns import (
    build_report,
    field_reliability,
    main,
    preview_hits,
    render_markdown,
)


def make_row(i: int, slug: str, **over) -> dict:
    row = {
        "id": i,
        "slug": slug,
        "name": slug.replace("-", " ").title(),
        "category": "finance_and_fintech",
        "one_liner": "Synthetic fixture.",
        "auth_methods": ["oauth2", "api_key"],
        "primary_auth": "oauth2",
        "access": "self_serve",
        "api_types": ["rest"],
        "has_public_docs": True,
        "api_breadth": "broad",
        "docs_url": f"https://docs.{slug}.example/",
        "mcp_status": "official",
        "mcp_url": f"https://docs.{slug}.example/mcp",
        "verdict": "buildable_now",
        "blocker": "none",
        "evidence": {},
        "confidence": "high",
        "requires_end_user_admin": False,
        "composio_toolkit": {"status": "exists", "toolkit_slug": slug, "sibling_slugs": [],
                             "checked_at": "2026-01-01T00:00:00Z", "evidence_url": None},
        "notes": "",
    }
    row.update(over)
    return row


UNCOVERED = {"status": "none", "toolkit_slug": None, "sibling_slugs": [],
             "checked_at": "2026-01-01T00:00:00Z", "evidence_url": None}

ROWS = [
    make_row(1, "acme-pay"),
    make_row(2, "beta-crm", category="crm_and_sales", auth_methods=["oauth2"], access="gated_partner",
             verdict="buildable_with_approval", blocker="gated_access", confidence="medium",
             requires_end_user_admin=True),
    make_row(3, "gamma-docs", category="crm_and_sales", auth_methods=["api_key"], primary_auth="api_key",
             mcp_status="none", mcp_url=None, composio_toolkit=UNCOVERED,
             notes="An MCP server is coming soon. The REST API is stable."),
    make_row(4, "delta-shop", category="ecommerce", composio_toolkit=UNCOVERED,
             notes="Webhooks are in beta. Nothing else to add."),
    make_row(5, "epsilon-mail", category="ecommerce", access="gated_sales", verdict="buildable_with_approval",
             blocker="gated_access", composio_toolkit=UNCOVERED, api_breadth="narrow"),
    make_row(6, "zeta-ads", category="ecommerce", composio_toolkit=UNCOVERED, confidence="low",
             auth_methods=["oauth2"]),
]

NEEDS_HUMAN = [
    {"slug": "acme-pay", "needs_human": False, "reasons": []},
    {"slug": "beta-crm", "needs_human": True, "reasons": [{"id": "sales_or_partner_gate", "detail": "x"},
                                                          {"id": "confidence_not_high", "detail": "y"}]},
    {"slug": "epsilon-mail", "needs_human": True, "reasons": [{"id": "sales_or_partner_gate", "detail": "x"}]},
    {"slug": "zeta-ads", "needs_human": True, "reasons": [{"id": "confidence_not_high", "detail": "z"}]},
]


def score_json(acc: dict[str, float], coverage: float = 1.0, run_id: str = "synthetic") -> dict:
    return {
        "run_id": run_id,
        "summary": {
            "n_gold": 10,
            "n_row_correct": 5,
            "row_accuracy": 0.5,
            "field_accuracy": {k: {"matches": 0, "applies": 10, "accuracy": v} for k, v in acc.items()},
            "coverage_accuracy": {"matches": 10, "applies": 10, "accuracy": coverage},
        },
    }


ALL_GOOD = {k: 1.0 for k in ("auth", "access", "api", "mcp_status", "verdict", "blocker", "api_breadth",
                             "requires_end_user_admin")}


def write_run(tmp_path: Path, rows=ROWS, needs_human=NEEDS_HUMAN) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    (run / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    if needs_human is not None:
        (run / "needs_human.jsonl").write_text("".join(json.dumps(r) + "\n" for r in needs_human),
                                               encoding="utf-8")
    return run


def write_scores(tmp_path: Path, gold: dict, holdout: dict) -> tuple[Path, Path]:
    g, h = tmp_path / "gold.json", tmp_path / "holdout.json"
    g.write_text(json.dumps(gold), encoding="utf-8")
    h.write_text(json.dumps(holdout), encoding="utf-8")
    return g, h


@pytest.fixture
def report(tmp_path):
    run = write_run(tmp_path)
    gold, holdout = write_scores(tmp_path, score_json(ALL_GOOD), score_json(ALL_GOOD))
    return build_report(run, gold, holdout)


def test_distributions(report):
    d = report["distributions"]
    assert report["meta"]["total_apps"] == 6
    assert d["primary_auth"]["oauth2"] == {"count": 5, "total": 6, "pct": 83.3}
    assert d["primary_auth"]["api_key"]["count"] == 1
    assert d["access"]["gated_sales"]["count"] == 1
    assert d["access"]["gated_partner"]["count"] == 1
    assert d["auth_methods"]["combos"]["oauth2_only"]["count"] == 2
    assert d["auth_methods"]["combos"]["oauth2_and_api_key"]["count"] == 3
    assert d["auth_methods"]["combos"]["api_key_only"]["count"] == 1
    assert d["auth_methods"]["method_count"]["single"]["count"] == 3
    assert d["auth_methods"]["methods"]["api_key"]["count"] == 4
    assert d["verdict"]["buildable_now"]["count"] == 4
    assert d["confidence"]["low"]["count"] == 1
    assert d["composio_toolkit_status"]["none"]["count"] == 4
    assert d["api_breadth"]["caveated"] is True


def test_crosstabs(report):
    x = report["crosstabs"]
    assert x["by_category"]["ecommerce"]["total"] == 3
    assert x["by_category"]["ecommerce"]["buildable_now"]["count"] == 2
    assert x["by_category"]["crm_and_sales"]["needs_human"]["count"] == 1
    assert x["access_vs_verdict_and_blocker"]["x_verdict"]["counts"]["gated_sales"] == {
        "buildable_now": 0, "buildable_with_approval": 1}
    assert x["composio_vs_verdict"]["x_mcp_status"]["counts"]["none"]["official"] == 3
    hard = x["what_makes_an_app_hard"]
    assert hard["gating"]["gated_buildable_with_approval"]["pct"] == 100.0
    assert hard["oauth2_only"]["among_gated"]["count"] == 1
    assert hard["confidence_not_high"]["all"]["count"] == 2
    assert x["auth_vs_access_and_admin"]["x_requires_end_user_admin"]["counts"]["oauth2"]["true"] == 1


def test_preview_detection():
    assert preview_hits("MCP server is coming soon.") == {"terms": ["coming_soon"], "mcp_related": True}
    hit = preview_hits("Webhooks are in Beta. The MCP server is GA.")
    assert hit == {"terms": ["beta"], "mcp_related": False}
    assert preview_hits("Join the wait-list for the preview")["terms"] == ["preview", "waitlist"]
    assert preview_hits("alphabetical betamax")["terms"] == []
    assert preview_hits(None) == {"terms": [], "mcp_related": False}


def test_preview_counts_in_report(report):
    mcp = report["distributions"]["mcp_status"]
    assert mcp["notes_any_preview_term"]["count"] == 2
    assert mcp["notes_preview_terms"]["coming_soon"]["count"] == 1
    assert mcp["notes_preview_terms"]["beta"]["count"] == 1
    assert mcp["notes_preview_term_with_mcp"]["count"] == 1


def test_needs_human_and_uncovered_overlap(report):
    d = report["distributions"]["needs_human"]
    assert d["total"]["count"] == 3
    assert d["by_reason"]["sales_or_partner_gate"]["count"] == 2
    assert d["by_reason"]["confidence_not_high"]["count"] == 2
    assert d["by_reason"]["browse_unresolved"]["count"] == 0
    unc = report["crosstabs"]["composio_vs_verdict"]["uncovered_slice"]
    assert unc["total"] == 4
    assert unc["buildable_now"]["count"] == 3
    assert unc["official_mcp"]["count"] == 3
    assert unc["buildable_now_and_official_mcp"]["count"] == 2
    assert unc["needs_human"]["count"] == 2
    mcp = next(p for p in report["patterns"] if p["id"] == "official_mcp_and_uncovered")
    assert (mcp["count"], mcp["total"]) == (2, 4)
    assert [e["slug"] for e in mcp["examples"]] == ["delta-shop", "zeta-ads"]


def test_missing_needs_human_file_is_empty(tmp_path):
    run = write_run(tmp_path, needs_human=None)
    rep = build_report(run, None, None)
    assert rep["distributions"]["needs_human"]["total"]["count"] == 0
    assert rep["validation"]["gold"] is None
    assert all(p["reliability"] in ("high", "moderate", "caveated") for p in rep["patterns"])


def test_patterns_shape_and_examples(report):
    patterns = report["patterns"]
    assert 4 <= len(patterns) <= 6
    for p in patterns:
        assert set(p) >= {"id", "title", "headline", "count", "total", "pct", "supporting_stats",
                          "examples", "reliability", "caveat"}
        assert 1 <= len(p["examples"]) <= 3
    # High confidence preferred, then id: zeta-ads (low) ranks after acme-pay.
    auth = next(p for p in patterns if p["id"] == "auth_landscape")
    assert auth["examples"][0]["slug"] == "acme-pay"
    gated = next(p for p in patterns if p["id"] == "buildable_vs_gated")
    assert [e["slug"] for e in gated["examples"]] == ["acme-pay", "epsilon-mail", "beta-crm"]


def test_all_high_scores_mean_no_caveats(report):
    assert report["validation"]["caveated_fields"] == []
    assert all(p["reliability"] == "high" and p["caveat"] is None for p in report["patterns"])


def test_low_accuracy_scores_mark_caveats(tmp_path):
    run = write_run(tmp_path)
    gold_acc = {**ALL_GOOD, "requires_end_user_admin": 0.7, "api_breadth": 0.65, "verdict": 0.85}
    holdout_acc = {**ALL_GOOD, "requires_end_user_admin": 0.1, "auth": 0.6, "mcp_status": 0.8}
    gold, holdout = write_scores(tmp_path, score_json(gold_acc), score_json(holdout_acc))
    rep = build_report(run, gold, holdout)
    fields = rep["validation"]["fields"]
    assert fields["requires_end_user_admin"]["level"] == "caveated"
    assert fields["auth"]["level"] == "caveated"
    assert fields["api_breadth"]["level"] == "caveated"
    assert fields["mcp_status"]["level"] == "moderate"
    assert fields["access"]["level"] == "high"
    assert set(rep["validation"]["caveated_fields"]) == {
        "auth_methods / primary_auth", "api_breadth", "requires_end_user_admin"}
    by_id = {p["id"]: p for p in rep["patterns"]}
    assert by_id["auth_landscape"]["reliability"] == "caveated"
    assert "holdout 60%" in by_id["auth_landscape"]["caveat"]
    admin = by_id["admin_and_breadth"]
    assert admin["reliability"] == "caveated"
    assert "gold 70% / holdout 10%" in admin["caveat"]
    assert "rough signals" in admin["headline"]
    assert by_id["official_mcp_and_uncovered"]["reliability"] == "moderate"
    assert "holdout 80%" in by_id["official_mcp_and_uncovered"]["caveat"]
    md = render_markdown(rep)
    assert "requires_end_user_admin ⚠" in md


def test_field_reliability_without_scores():
    rel = field_reliability(None, None)
    assert all(info["level"] == "unscored" for info in rel.values())


def test_cli_writes_outputs(tmp_path, capsys):
    run = write_run(tmp_path)
    gold, holdout = write_scores(tmp_path, score_json(ALL_GOOD), score_json(ALL_GOOD))
    out = tmp_path / "out"
    code = main(["--run-dir", str(run), "--out-dir", str(out), "--gold-score", str(gold),
                 "--holdout-score", str(holdout)])
    assert code == 0
    data = json.loads((out / "patterns.json").read_text(encoding="utf-8"))
    md = (out / "patterns.md").read_text(encoding="utf-8")
    assert data["meta"]["total_apps"] == 6
    assert "generated_at" not in data["meta"]
    assert str(tmp_path) not in json.dumps(data["meta"])
    assert md.startswith("# Patterns across 6 apps")
    for heading in ("## Headline patterns", "## Distributions", "### By category",
                    "### What makes an app hard", "## Validation & field caveats"):
        assert heading in md
    assert "patterns over 6 apps" in capsys.readouterr().out
    # Deterministic: a second run produces identical files.
    main(["--run-dir", str(run), "--out-dir", str(out), "--gold-score", str(gold), "--holdout-score", str(holdout)])
    assert (out / "patterns.md").read_text(encoding="utf-8") == md
