from conftest import DOCS

from agent.tools.base import Page
from agent.validate import check_evidence, normalize_url, validate_row

TEXT = "Use OAuth 2.0 or a private app access token to authenticate requests."


def _pages():
    return [
        Page(requested_url="http://developers.hubspot.com/docs/api/overview/", final_url=DOCS, status=200,
             provider="http", text=TEXT),
        Page(requested_url="https://broken.example.com", final_url="https://broken.example.com", status=404,
             provider="http", text="Not found"),
    ]


def test_normalize_url():
    assert normalize_url("http://www.Example.com/a/#x") == normalize_url("https://example.com/a")


def test_fetched_evidence_kept(row):
    row, checks = check_evidence(row, {}, _pages())
    assert checks["dropped_evidence"] == []
    assert validate_row(row) == []


def test_unfetched_url_dropped_and_flagged(row):
    bogus = "https://made-up.example.com/auth"
    row["evidence"]["auth"] = [{"url": bogus, "source_type": "official_docs"}]
    row["evidence"]["access"].append({"url": "https://broken.example.com", "source_type": "official_docs"})
    row, checks = check_evidence(row, {}, _pages())
    dropped = {(d["claim"], d["url"]) for d in checks["dropped_evidence"]}
    assert ("auth", bogus) in dropped
    assert ("access", "https://broken.example.com") in dropped  # 404 pages do not count as fetched
    assert row["evidence"]["auth"] == []
    assert len(row["evidence"]["access"]) == 1
    # required evidence now empty -> row invalid -> research loop retries
    assert any("evidence/auth" in e for e in validate_row(row))


def test_snippets_verified_against_page_text(row):
    snippets = {
        "auth": [{"url": DOCS, "quote": "Use OAuth 2.0 or a private  app access token"}],
        "access": [{"url": DOCS, "quote": "Free signup gives API keys"}],
        "mcp": [{"url": "https://other.example.com", "quote": "x"}],
    }
    _, checks = check_evidence(row, snippets, _pages())
    assert snippets["auth"][0]["verified"] is True
    reasons = {(u["claim"], u["reason"]) for u in checks["unverified_snippets"]}
    assert ("access", "quote not in page text") in reasons
    assert ("mcp", "url not fetched") in reasons
    assert "no verified snippet for access" in checks["warnings"]


def test_unfetched_docs_url_flagged(row):
    row["docs_url"] = "https://developers.hubspot.com/other"
    _, checks = check_evidence(row, {}, _pages())
    assert checks["unfetched_urls"] == [{"field": "docs_url", "url": "https://developers.hubspot.com/other"}]
