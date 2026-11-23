#!/usr/bin/env python3
"""Fetch the Composio toolkit catalog and match it against the 100-app research set.

Usage (from the repo root, with COMPOSIO_API_KEY in the environment):

    python scripts/composio_coverage.py           # reuse cached catalog
    python scripts/composio_coverage.py --refresh # refetch the catalog

Never prints secret values.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
APPS_PATH = ROOT / "data" / "apps.csv"
ALIASES_PATH = ROOT / "data" / "composio_aliases.json"
CATALOG_PATH = ROOT / "data" / "composio_toolkits.json"
COVERAGE_PATH = ROOT / "data" / "composio_coverage.csv"
DOCS_URL = "https://docs.composio.dev/toolkits/{slug}"

# Shared parent domains that must not be treated as a product match.
GENERIC_HOSTS = {
    "github.com",
    "github.io",
    "google.com",
    "cloud.google.com",
    "developers.google.com",
    "zoho.com",
    "microsoft.com",
    "learn.microsoft.com",
    "salesforce.com",
    "force.com",
    "facebook.com",
    "developers.facebook.com",
    "meta.com",
    "amazon.com",
    "amazon.in",
    "aws.amazon.com",
    "apple.com",
    "adobe.com",
    "atlassian.com",
    "linkedin.com",
    "composio.dev",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def norm(value: str) -> str:
    text = (value or "").lower().replace("&", " and ")
    text = re.sub(r"['’`]", "", text)
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def compact(value: str) -> str:
    return norm(value).replace(" ", "")


def underscore_slug(app_slug: str) -> str:
    return app_slug.replace("-", "_")


def load_aliases() -> dict[str, dict[str, Any]]:
    raw = json.loads(ALIASES_PATH.read_text())
    return {k: v for k, v in raw.items() if not k.startswith("_") and isinstance(v, dict)}


def slim_toolkit(item: dict[str, Any]) -> dict[str, Any]:
    meta = item.get("meta") or {}
    categories = []
    for cat in meta.get("categories") or []:
        if isinstance(cat, dict):
            categories.append(cat.get("id") or cat.get("name"))
        else:
            categories.append(cat)
    return {
        "slug": item.get("slug"),
        "name": item.get("name"),
        "app": item.get("app"),
        "type": item.get("type"),
        "toolkit_type": item.get("toolkit_type"),
        "no_auth": item.get("no_auth"),
        "auth_schemes": item.get("auth_schemes") or [],
        "description": meta.get("description") or "",
        "app_url": meta.get("app_url"),
        "tools_count": meta.get("tools_count"),
        "triggers_count": meta.get("triggers_count"),
        "categories": categories,
        "version": meta.get("version"),
    }


def fetch_catalog() -> dict[str, Any]:
    try:
        from composio import Composio
    except ImportError:
        die("The composio package is missing. Install it with: pip install -r requirements.txt")

    import os

    if not (os.environ.get("COMPOSIO_API_KEY") or "").strip():
        die("COMPOSIO_API_KEY is not set. Load it from a local env file; do not paste it into the command.")

    client = Composio()
    items: list[dict[str, Any]] = []
    cursor = None
    pages = 0
    while True:
        kwargs: dict[str, Any] = {"limit": 1000, "sort_by": "alphabetically"}
        if cursor:
            kwargs["cursor"] = cursor
        page = client.toolkits.list(**kwargs)
        data = page.model_dump()
        batch = data.get("items") or []
        items.extend(batch)
        pages += 1
        cursor = data.get("next_cursor")
        print(f"fetched catalog page {pages}: {len(batch)} items (running {len(items)})")
        if not cursor or not batch:
            break

    slim = [slim_toolkit(t) for t in items]
    snapshot = {
        "fetched_at": utc_now(),
        "source": "composio.toolkits.list",
        "api": "v3.1",
        "total": len(slim),
        "toolkits": slim,
    }
    CATALOG_PATH.write_text(json.dumps(snapshot, indent=2) + "\n")
    print(f"wrote {CATALOG_PATH.relative_to(ROOT)} ({len(slim)} toolkits)")
    return snapshot


def load_catalog(refresh: bool) -> dict[str, Any]:
    if refresh or not CATALOG_PATH.exists():
        return fetch_catalog()
    snapshot = json.loads(CATALOG_PATH.read_text())
    print(f"reusing cached catalog from {snapshot.get('fetched_at')} ({snapshot.get('total')} toolkits)")
    return snapshot


def indexes(toolkits: list[dict[str, Any]]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    by_slug = {t["slug"]: t for t in toolkits if t.get("slug")}
    by_compact_slug: dict[str, list[dict[str, Any]]] = {}
    by_compact_name: dict[str, list[dict[str, Any]]] = {}
    by_norm_name: dict[str, list[dict[str, Any]]] = {}
    by_app: dict[str, list[dict[str, Any]]] = {}
    for t in toolkits:
        by_compact_slug.setdefault(compact(t["slug"]), []).append(t)
        by_compact_name.setdefault(compact(t["name"]), []).append(t)
        by_norm_name.setdefault(norm(t["name"]), []).append(t)
        if t.get("app"):
            # App grouping is used only for sibling discovery, not primary matching.
            # Indexing it as a name would treat pinterest_ads as a hit for Pinterest.
            by_app.setdefault(t["app"], []).append(t)
    return {
        "by_slug": by_slug,
        "by_compact_slug": by_compact_slug,
        "by_compact_name": by_compact_name,
        "by_norm_name": by_norm_name,
        "by_app": by_app,
    }


def unique(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        slug = item["slug"]
        if slug in seen:
            continue
        seen.add(slug)
        out.append(item)
    return out


def auto_candidates(app: dict[str, str], idx: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Return exact automatic matches and the reason. Prefix/host hits are not auto-accepted."""
    name = re.sub(r"\([^)]*\)", "", app["name"]).strip()
    keys_compact = {compact(app["slug"]), compact(name), compact(app["name"])}
    keys_norm = {norm(app["slug"]), norm(name), norm(app["name"])}
    hits: list[dict[str, Any]] = []
    reasons: list[str] = []

    underscored = underscore_slug(app["slug"])
    by_slug = idx["by_slug"]
    if app["slug"] in by_slug:
        hits.append(by_slug[app["slug"]])
        reasons.append("slug_exact")
    if underscored in by_slug:
        hits.append(by_slug[underscored])
        reasons.append("slug_underscore")

    for key in keys_compact:
        if not key:
            continue
        for t in idx["by_compact_slug"].get(key, []):
            hits.append(t)
            reasons.append("compact_slug")
        for t in idx["by_compact_name"].get(key, []):
            hits.append(t)
            reasons.append("compact_name")

    for key in keys_norm:
        if not key:
            continue
        for t in idx["by_norm_name"].get(key, []):
            hits.append(t)
            reasons.append("norm_name")

    hits = unique(hits)

    # If nothing exact, accept a unique MCP-only toolkit named {base}_mcp.
    if not hits:
        bases = {underscored, compact(app["slug"]), compact(name)}
        mcp_hits = []
        for base in bases:
            if not base:
                continue
            candidate = by_slug.get(f"{base}_mcp") or by_slug.get(f"{underscore_slug(base)}_mcp")
            if candidate:
                mcp_hits.append(candidate)
            for t in idx["by_compact_slug"].get(compact(base + "mcp"), []):
                if t.get("toolkit_type") == "mcp" or t["slug"].endswith("_mcp"):
                    mcp_hits.append(t)
        mcp_hits = unique(mcp_hits)
        if len(mcp_hits) == 1:
            return mcp_hits, "unique_mcp_sibling"

    return hits, ",".join(dict.fromkeys(reasons)) if hits else ""


def prefer_primary(matches: list[dict[str, Any]]) -> dict[str, Any]:
    api = [t for t in matches if t.get("toolkit_type") == "api"]
    pool = api or matches
    # Prefer the shortest slug among api toolkits (often the unsuffixed product slug).
    return sorted(pool, key=lambda t: (len(t["slug"]), t["slug"]))[0]


def is_auto_sibling(primary: dict[str, Any], other: dict[str, Any]) -> bool:
    """Same product, different surface or auth — not a related but distinct app."""
    if other.get("app") and primary.get("app") and other["app"] != primary["app"]:
        return False
    slug = other["slug"]
    base = primary["slug"]
    if slug == f"{base}_mcp" or slug.startswith(f"{base}_") and slug.endswith("_mcp"):
        return True
    if slug.startswith(f"{base}_") and slug.endswith(("_basic", "_api_key")):
        return True
    if slug in {f"{base}bot", f"{base}_bot"}:
        return True
    return False


def siblings_for(primary: dict[str, Any], matches: list[dict[str, Any]], idx: dict[str, Any], extra: list[str]) -> list[str]:
    found = []
    app_name = primary.get("app")
    if app_name:
        found.extend(t for t in idx["by_app"].get(app_name, []) if is_auto_sibling(primary, t))
    mcp = idx["by_slug"].get(f"{primary['slug']}_mcp")
    if mcp:
        found.append(mcp)
    by_slug = idx["by_slug"]
    for slug in extra:
        if slug in by_slug:
            found.append(by_slug[slug])
    slugs = []
    seen = {primary["slug"]}
    for t in unique(found):
        if t["slug"] in seen:
            continue
        seen.add(t["slug"])
        slugs.append(t["slug"])
    return slugs


def review_hints(app: dict[str, str], toolkits: list[dict[str, Any]], accepted: set[str]) -> list[str]:
    """Loose hits shown for human review, never used as auto-accept."""
    name = re.sub(r"\([^)]*\)", "", app["name"]).strip()
    app_c = compact(name)
    slug_c = compact(app["slug"])
    hints = []
    if len(app_c) < 5 and len(slug_c) < 5:
        needle = app_c or slug_c
        if len(needle) < 4:
            return []
    for t in toolkits:
        if t["slug"] in accepted:
            continue
        tc_slug = compact(t["slug"])
        tc_name = compact(t["name"])
        reason = None
        if len(app_c) >= 5 and (app_c in tc_slug or app_c in tc_name or tc_slug.startswith(app_c) or tc_name.startswith(app_c)):
            reason = "name_prefix_or_contains"
        elif len(slug_c) >= 5 and (slug_c in tc_slug or tc_slug.startswith(slug_c)):
            reason = "slug_prefix_or_contains"
        if reason:
            hints.append(f"{t['slug']} ({t['name']}, {reason})")
    return hints[:12]


def match_app(app: dict[str, str], aliases: dict[str, dict[str, Any]], idx: dict[str, Any], toolkits: list[dict[str, Any]], checked_at: str) -> dict[str, Any]:
    alias = aliases.get(app["slug"])
    auto, auto_reason = auto_candidates(app, idx)

    if alias is not None:
        target = alias.get("slug")
        note = alias.get("note") or ""
        extra = list(alias.get("siblings") or [])
        if target is None:
            hints = review_hints(app, toolkits, set())
            return row(
                app,
                status="none",
                toolkit_slug=None,
                siblings=[],
                checked_at=checked_at,
                reason="alias_none",
                notes=note,
                review="; ".join(hints),
            )
        primary = idx["by_slug"].get(target)
        if not primary:
            return row(
                app,
                status="unknown",
                toolkit_slug=target,
                siblings=[],
                checked_at=checked_at,
                reason="alias_missing_from_catalog",
                notes=note,
                review="",
            )
        sibs = siblings_for(primary, auto, idx, extra)
        return row(
            app,
            status="exists",
            toolkit_slug=primary["slug"],
            siblings=sibs,
            checked_at=checked_at,
            reason="alias",
            notes=note,
            review="",
        )

    if not auto:
        hints = review_hints(app, toolkits, set())
        return row(
            app,
            status="none",
            toolkit_slug=None,
            siblings=[],
            checked_at=checked_at,
            reason="no_auto_match",
            notes="",
            review="; ".join(hints),
        )

    # If auto matches collapse to one product family (same app field or *_mcp siblings), accept.
    primary = prefer_primary(auto)
    family = siblings_for(primary, auto, idx, [])
    family_set = {primary["slug"], *family}
    leftover = [t["slug"] for t in auto if t["slug"] not in family_set]
    if leftover:
        return row(
            app,
            status="unknown",
            toolkit_slug=None,
            siblings=[],
            checked_at=checked_at,
            reason="ambiguous_auto_match",
            notes=f"Automatic matches did not collapse to one product: {', '.join(t['slug'] for t in auto)}",
            review="; ".join(leftover),
        )

    return row(
        app,
        status="exists",
        toolkit_slug=primary["slug"],
        siblings=family,
        checked_at=checked_at,
        reason=auto_reason,
        notes="",
        review="",
    )


def row(app: dict[str, str], status: str, toolkit_slug: str | None, siblings: list[str], checked_at: str, reason: str, notes: str, review: str) -> dict[str, Any]:
    evidence = DOCS_URL.format(slug=toolkit_slug) if status == "exists" and toolkit_slug else ""
    return {
        "id": app["id"],
        "slug": app["slug"],
        "name": app["name"],
        "category": app["category"],
        "status": status,
        "toolkit_slug": toolkit_slug or "",
        "sibling_slugs": ",".join(siblings),
        "evidence_url": evidence,
        "checked_at": checked_at,
        "match_reason": reason,
        "notes": notes,
        "review_hints": review,
    }


def write_coverage(rows: list[dict[str, Any]]) -> None:
    fields = [
        "id",
        "slug",
        "name",
        "category",
        "status",
        "toolkit_slug",
        "sibling_slugs",
        "evidence_url",
        "checked_at",
        "match_reason",
        "notes",
        "review_hints",
    ]
    with COVERAGE_PATH.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {COVERAGE_PATH.relative_to(ROOT)} ({len(rows)} rows)")


def summarize(rows: list[dict[str, Any]]) -> None:
    exists = [r for r in rows if r["status"] == "exists"]
    none = [r for r in rows if r["status"] == "none"]
    unknown = [r for r in rows if r["status"] == "unknown"]
    print(f"\ncoverage: {len(exists)} exists, {len(none)} none, {len(unknown)} unknown, of {len(rows)}")

    from collections import Counter

    by_cat_exists = Counter(r["category"] for r in exists)
    by_cat_total = Counter(r["category"] for r in rows)
    print("\nper category (exists / total):")
    for cat, total in by_cat_total.items():
        print(f"  {cat:40} {by_cat_exists[cat]:2d} / {total}")

    print("\nmatched:")
    for r in exists:
        sibs = f"  siblings={r['sibling_slugs']}" if r["sibling_slugs"] else ""
        print(f"  {r['slug']:28} -> {r['toolkit_slug']:24} ({r['match_reason']}){sibs}")

    print("\nnot in catalog:")
    for r in none:
        hint = f"  review: {r['review_hints']}" if r["review_hints"] else ""
        note = f"  note: {r['notes']}" if r["notes"] else ""
        print(f"  {r['slug']:28} ({r['match_reason']}){note}{hint}")

    if unknown:
        print("\nunknown / unresolved:")
        for r in unknown:
            print(f"  {r['slug']:28} ({r['match_reason']}) {r['notes']} {r['review_hints']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Match the 100-app set to the Composio catalog.")
    parser.add_argument("--refresh", action="store_true", help="Refetch the catalog instead of reusing the cache.")
    args = parser.parse_args()

    if not APPS_PATH.exists():
        die(f"missing {APPS_PATH}")
    if not ALIASES_PATH.exists():
        die(f"missing {ALIASES_PATH}")

    snapshot = load_catalog(refresh=args.refresh)
    toolkits = snapshot.get("toolkits") or []
    checked_at = snapshot.get("fetched_at") or utc_now()
    aliases = load_aliases()
    apps = list(csv.DictReader(APPS_PATH.open()))
    idx = indexes(toolkits)

    # Fail early if an alias points at a typo, except explicit none.
    for app_slug, alias in aliases.items():
        target = alias.get("slug")
        if target and target not in idx["by_slug"]:
            die(f"alias {app_slug} points at unknown catalog slug {target}")
        for sib in alias.get("siblings") or []:
            if sib not in idx["by_slug"]:
                die(f"alias {app_slug} sibling {sib} is not in the catalog")

    rows = [match_app(app, aliases, idx, toolkits, checked_at) for app in apps]
    write_coverage(rows)
    summarize(rows)
    if any(r["status"] == "unknown" for r in rows):
        die("one or more apps were left unknown; add an alias or inspect the match.", code=2)


if __name__ == "__main__":
    main()
