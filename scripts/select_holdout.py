#!/usr/bin/env python3
"""Select a 10-app held-out test set with a fixed seed.

Picks one app per category from the 100-app list, excluding the gold sample
and the pilot-10 apps. Selection is stratified random: categories stay in
apps.csv order, candidates inside a category are sorted by slug, then
random.Random(SEED).choice is used. Nothing is hand-picked.

The gold and pilot exclusion lists are recorded here so the draw is
reproducible without reading any run-row contents.
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

# Recorded 2026-11-23. Changing this changes the held-out set; do not silently reroll.
SEED = 20261123

# Frozen in schema/gold_sample.md (two apps per category).
GOLD_SLUGS = frozenset(
    {
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
)

# Slugs taken from data/runs/pilot-10* cache directory and *.llm.json names only
# (pilot-10, pilot-10b, pilot-10c, and pilot-10d all used the same 10 apps).
# Row contents were not read.
PILOT_SLUGS = frozenset(
    {
        "amazon-selling-partner",
        "datadog",
        "devin",
        "gohighlevel",
        "linear",
        "paygent-connect",
        "plain",
        "sherlock",
        "twenty",
        "twilio",
    }
)


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_apps(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def select(apps: list[dict[str, str]], seed: int = SEED) -> list[dict[str, str]]:
    excluded = GOLD_SLUGS | PILOT_SLUGS
    by_category: dict[str, list[dict[str, str]]] = {}
    category_order: list[str] = []
    for app in apps:
        slug = app["slug"]
        if slug in excluded:
            continue
        category = app["category"]
        if category not in by_category:
            category_order.append(category)
            by_category[category] = []
        by_category[category].append(app)

    rng = random.Random(seed)
    picked: list[dict[str, str]] = []
    for category in category_order:
        candidates = sorted(by_category[category], key=lambda row: row["slug"])
        if not candidates:
            raise SystemExit(f"no eligible apps left in category {category}")
        picked.append(rng.choice(candidates))
    return picked


def main(argv: list[str] | None = None) -> int:
    del argv
    apps_path = repo_root() / "data" / "apps.csv"
    apps = load_apps(apps_path)
    picked = select(apps)
    print(f"seed={SEED}")
    print(f"excluded gold={len(GOLD_SLUGS)} pilot={len(PILOT_SLUGS)}")
    print(f"selected {len(picked)} apps (one per category):")
    for app in picked:
        print(f"  {app['id']:>3}  {app['slug']:<28}  {app['category']:<36}  {app['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
