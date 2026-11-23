"""Validate the frozen gold labels against the schema. Does not read data/runs/."""

from pathlib import Path

from scripts.score_gold import load_gold, validate_gold_file

GOLD = Path(__file__).resolve().parents[1] / "schema" / "gold_sample.jsonl"


def test_gold_file_validates_and_has_twenty_apps():
    errors = validate_gold_file(GOLD)
    assert errors == [], "\n".join(errors)
    gold = load_gold(GOLD)
    assert len(gold) == 20
    expected = {
        "hubspot",
        "dealcloud",
        "zendesk",
        "pylon",
        "slack",
        "whatsapp-business",
        "mailchimp",
        "linkedin-ads",
        "shopify",
        "fanbasis",
        "firecrawl",
        "clay",
        "github",
        "snowflake",
        "notion",
        "smartsheet",
        "stripe",
        "pitchbook",
        "reducto",
        "notebooklm",
    }
    assert set(gold) == expected
    for row in gold.values():
        assert row["evidence"]["auth"][0].get("note")
        assert "composio_toolkit" in row
