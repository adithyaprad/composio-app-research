"""Validate the held-out labels against the schema. Does not read data/runs/."""

from pathlib import Path

HOLDOUT = Path(__file__).resolve().parents[1] / "schema" / "holdout_sample.jsonl"

EXPECTED = {
    "zoho-crm",
    "help-scout",
    "vonage",
    "pinterest",
    "ecwid",
    "apify",
    "vercel",
    "harvest",
    "binance",
    "mermaid-cli",
}


def _load_rows(path: Path) -> list[dict]:
    import json

    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        rows.append(json.loads(text))
    return rows


def test_holdout_file_has_ten_seeded_apps_and_evidence():
    rows = _load_rows(HOLDOUT)
    assert len(rows) == 10
    slugs = [row["slug"] for row in rows]
    assert slugs == [
        "zoho-crm",
        "help-scout",
        "vonage",
        "pinterest",
        "ecwid",
        "apify",
        "vercel",
        "harvest",
        "binance",
        "mermaid-cli",
    ]
    assert set(slugs) == EXPECTED
    for row in rows:
        assert row["evidence"]["auth"][0].get("url")
        assert row["evidence"]["access"][0].get("url")
        assert row["evidence"]["api_surface"][0].get("url")
        assert row["evidence"]["mcp"][0].get("url")
        assert row["evidence"]["verdict"][0].get("url")
        assert "composio_toolkit" in row
        assert row.get("label_notes")


def test_holdout_validates_like_gold_when_scorer_is_present():
    try:
        from scripts.score_gold import load_gold, validate_gold_file
    except ImportError:
        return
    errors = validate_gold_file(HOLDOUT)
    assert errors == [], "\n".join(errors)
    assert set(load_gold(HOLDOUT)) == EXPECTED
