"""Tests for scripts/score_gold.py. Fixtures are synthetic (Acme Pay), not gold apps."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from scripts.score_gold import (
    aggregate,
    load_gold,
    score_app,
    score_run,
)

ACME_DOCS = "https://developers.acmepay.example/docs"
ACME_MCP = "https://developers.acmepay.example/mcp"

ACME_GOLD = {
    "id": 1,
    "slug": "acme-pay",
    "name": "Acme Pay",
    "category": "finance_and_fintech",
    "one_liner": "Synthetic test fixture: a fictional payments API used only in scorer tests.",
    "auth_methods": ["oauth2", "api_key"],
    "primary_auth": "oauth2",
    "access": "self_serve",
    "api_types": ["rest"],
    "has_public_docs": True,
    "api_breadth": "moderate",
    "docs_url": ACME_DOCS,
    "mcp_status": "official",
    "mcp_url": ACME_MCP,
    "verdict": "buildable_now",
    "blocker": "none",
    "evidence": {
        key: [{"url": ACME_DOCS, "source_type": "official_docs", "note": "synthetic"}]
        for key in ("auth", "access", "api_surface", "mcp", "verdict")
    },
    "confidence": "high",
    "requires_end_user_admin": False,
    "composio_toolkit": {
        "status": "exists",
        "toolkit_slug": "acme_pay",
        "sibling_slugs": ["acme_pay_mcp"],
        "checked_at": "2026-11-23T10:41:38Z",
        "evidence_url": "https://docs.composio.dev/toolkits/acme_pay",
    },
    "notes": "",
    "label_notes": "Synthetic fixture; not a real product.",
}


def _agent(**overrides):
    row = copy.deepcopy(ACME_GOLD)
    row.pop("label_notes", None)
    row.pop("acceptable", None)
    row.pop("acceptable_alternatives", None)
    for key, value in overrides.items():
        row[key] = value
    return row


def test_perfect_match_is_row_correct():
    result = score_app(_agent(), ACME_GOLD)
    assert result["row_correct"] is True
    assert all(result["units"][unit]["match"] for unit in result["units"])
    assert result["coverage"]["match"] is True
    assert result["coverage"]["sibling_match"] is True


def test_auth_methods_are_set_equal_order_independent():
    result = score_app(_agent(auth_methods=["api_key", "oauth2"]), ACME_GOLD)
    assert result["units"]["auth"]["match"] is True
    assert result["row_correct"] is True


def test_missing_auth_method_fails_row():
    result = score_app(_agent(auth_methods=["oauth2"], primary_auth="oauth2"), ACME_GOLD)
    assert result["units"]["auth"]["match"] is False
    assert result["row_correct"] is False


def test_wrong_primary_auth_fails_row():
    result = score_app(_agent(primary_auth="api_key"), ACME_GOLD)
    assert result["units"]["auth"]["match"] is False
    assert result["row_correct"] is False


def test_minor_api_breadth_mismatch_does_not_fail_row():
    result = score_app(_agent(api_breadth="broad"), ACME_GOLD)
    assert result["minors"]["api_breadth"]["match"] is False
    assert result["row_correct"] is True


def test_gated_with_evidence_is_correct():
    gold = copy.deepcopy(ACME_GOLD)
    gold.update(
        {
            "access": "gated_partner",
            "verdict": "buildable_with_approval",
            "blocker": "gated_access",
            "label_notes": "Partner review is required before production credentials.",
        }
    )
    agent = _agent(access="gated_partner", verdict="buildable_with_approval", blocker="gated_access")
    result = score_app(agent, gold)
    assert result["units"]["access"]["match"] is True
    assert result["units"]["verdict"]["match"] is True
    assert result["units"]["blocker"]["match"] is True
    assert result["units"]["evidence"]["match"] is True
    assert result["row_correct"] is True


def test_missing_evidence_url_fails_row():
    agent = _agent()
    agent["evidence"]["auth"] = [{"url": "not-a-url", "source_type": "official_docs"}]
    result = score_app(agent, ACME_GOLD)
    assert result["units"]["evidence"]["match"] is False
    assert result["row_correct"] is False


def test_missing_row_is_a_miss():
    result = score_app(None, ACME_GOLD)
    assert result["missing"] is True
    assert result["row_correct"] is False
    assert result["units"]["access"]["match"] is False


def test_identity_mismatch_scores_zero():
    agent = _agent(name="Wrong Product")
    result = score_app(agent, ACME_GOLD)
    assert result["invalid"] is True
    assert result["identity_ok"] is False
    assert result["row_correct"] is False
    assert all(not result["units"][unit]["match"] for unit in result["units"])


def test_acceptable_set_counts_as_match():
    gold = copy.deepcopy(ACME_GOLD)
    gold["access"] = "self_serve_paid"
    gold["acceptable"] = {"access": ["self_serve_paid", "gated_sales"]}
    agent = _agent(access="gated_sales")
    result = score_app(agent, gold)
    assert result["units"]["access"]["match"] is True
    assert result["row_correct"] is True


def test_acceptable_alternative_coupled_fields():
    gold = copy.deepcopy(ACME_GOLD)
    gold["access"] = "self_serve"
    gold["verdict"] = "not_buildable"
    gold["blocker"] = "preview_or_unstable"
    gold["acceptable_alternatives"] = [
        {
            "access": "gated_sales",
            "verdict": "buildable_with_approval",
            "blocker": "gated_access",
        }
    ]
    agent = _agent(access="gated_sales", verdict="buildable_with_approval", blocker="gated_access")
    result = score_app(agent, gold)
    assert result["row_correct"] is True
    assert result["units"]["access"]["agent"] == "gated_sales"


def test_coverage_lookup_is_separate_from_row_accuracy():
    agent = _agent(
        composio_toolkit={
            "status": "none",
            "toolkit_slug": None,
            "sibling_slugs": [],
            "checked_at": "2026-11-23T10:41:38Z",
            "evidence_url": None,
        }
    )
    result = score_app(agent, ACME_GOLD)
    assert result["row_correct"] is True
    assert result["coverage"]["match"] is False


def test_coverage_sibling_miss_is_not_a_headline_coverage_miss():
    toolkit = copy.deepcopy(ACME_GOLD["composio_toolkit"])
    toolkit["sibling_slugs"] = []
    result = score_app(_agent(composio_toolkit=toolkit), ACME_GOLD)
    assert result["coverage"]["match"] is True
    assert result["coverage"]["sibling_match"] is False


def test_mcp_url_not_scored_when_gold_is_none():
    gold = copy.deepcopy(ACME_GOLD)
    gold["mcp_status"] = "none"
    gold["mcp_url"] = None
    agent = _agent(mcp_status="none", mcp_url=None)
    result = score_app(agent, gold)
    assert result["minors"]["mcp_url"]["applies"] is False
    assert result["minors"]["mcp_url"]["match"] is True
    assert result["row_correct"] is True


def test_one_liner_accepts_paraphrase_of_same_product():
    agent = _agent(one_liner="Acme Pay is a fictional payments platform used as a test double.")
    result = score_app(agent, ACME_GOLD)
    assert result["minors"]["one_liner"]["match"] is True


def test_score_run_writes_json_and_markdown(tmp_path: Path):
    gold_path = tmp_path / "gold.jsonl"
    gold_path.write_text(json.dumps(ACME_GOLD) + "\n", encoding="utf-8")
    run_dir = tmp_path / "pilot-acme"
    run_dir.mkdir()
    agent = _agent()
    (run_dir / "rows.jsonl").write_text(json.dumps(agent) + "\n", encoding="utf-8")

    report = score_run(run_dir, gold_path)
    assert report["run_id"] == "pilot-acme"
    assert report["summary"]["row_accuracy"] == 1.0
    assert report["summary"]["n_row_correct"] == 1
    loaded = load_gold(gold_path)
    assert "acme-pay" in loaded

    summary = aggregate([score_app(_agent(api_breadth="broad"), ACME_GOLD)])
    assert summary["row_accuracy"] == 1.0
    assert summary["field_accuracy"]["api_breadth"]["accuracy"] == 0.0
    minor_misses = [m for m in summary["misses"] if m["severity"] == "minor"]
    assert minor_misses and minor_misses[0]["field"] == "api_breadth"


def test_list_set_equality_for_api_types():
    gold = copy.deepcopy(ACME_GOLD)
    gold["api_types"] = ["rest", "graphql"]
    agent = _agent(api_types=["graphql", "rest"])
    result = score_app(agent, gold)
    assert result["units"]["api"]["match"] is True
