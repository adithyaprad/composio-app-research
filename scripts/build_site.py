#!/usr/bin/env python3
"""Build the case-study page (site/index.html) and its data files from repo data.

Every number on the page is computed here from files in the repo and wrapped in
an element with a `data-src` attribute that names its source, so
scripts/check_site.py can recompute it independently. Example apps, misses and
fix lists are picked from the data; no app is named in this script. Sentences
that depend on the data (for example "the holdout did not move") are checked
here, and the build fails if the data contradicts them.

Output is deterministic: the only date on the page is derived from the data.

    .venv/bin/python scripts/build_site.py            # write site/
    .venv/bin/python scripts/build_site.py --check    # exit 1 if site/ is stale
"""

from __future__ import annotations

import argparse
import base64
import csv
import filecmp
import html
import json
import os
import re
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.human import REASONS as NEEDS_HUMAN_TEXT  # noqa: E402
from agent.human import export_final, schema_columns  # noqa: E402
from scripts.patterns import CAVEAT_BELOW, auth_combo, pick_examples  # noqa: E402
from scripts.patterns import MODERATE_BELOW as PATTERNS_MODERATE_BELOW  # noqa: E402

REPO_URL = "https://github.com/adithyaprad/composio-app-research"
# Label for the repo buttons. The host shares its name with one of the researched apps; this is the
# one place that name may appear in this file (tests/test_build_site.py exempts this line only).
REPO_LABEL = "GitHub repo"
# Resume link in the closing Links section: a file in site/ (or an absolute URL). Override with env RESUME_URL.
# When empty, or when the file is missing from the site folder, the button is hidden and a warning is printed.
RESUME_URL = "resume.pdf"
SITE_DIR = ROOT / "site"
GENERATED = ("index.html", "llms.txt", "data")  # what write_site owns and replaces
STATIC_ASSETS = ("resume.pdf",)  # placed in site/ by hand; kept on rebuild, never generated or read
TITLE = "Can these {n} apps become agent toolkits?"

# Repo-relative source paths. These strings are also the file part of every data-src key.
P_APPS = "data/apps.csv"
P_ROWS = "data/runs/pass3/rows.jsonl"
P_ROWS2 = "data/runs/pass2/rows.jsonl"
P_EVIDENCE = "data/runs/pass3/evidence"
P_NEEDS = "data/runs/pass3/needs_human.json"
P_NEEDS_L = "data/runs/pass3/needs_human.jsonl"
P_FLAGS = "data/runs/pass3/flags.jsonl"
P_BROWSE = "data/runs/pass3/browse.jsonl"
P_LOG1 = "data/runs/pass1/log.jsonl"
P_REVIEW = "data/runs/pass2/review.jsonl"
P_SPOT = "data/runs/pass3/spot_check_10.json"
P_PATTERNS = "data/patterns/patterns.json"
P_COVERAGE = "data/composio_coverage.csv"
P_TOOLKITS = "data/composio_toolkits.json"
P_ALIASES = "data/composio_aliases.json"
P_GOLD = "schema/gold_sample.jsonl"
P_HOLDOUT = "schema/holdout_sample.jsonl"
P_AUDIT = "schema/GOLD_AUDIT.md"
P_SCHEMA = "schema/app_row.schema.json"
P_SCHEMA_README = "schema/README.md"
P_PROOF = "data/runs/proof-e2e/summary.json"
P_PROOF_ROWS = "data/runs/proof-e2e/rows.jsonl"
P_PROOF_FLAGS = "data/runs/proof-e2e/pass3/flags.jsonl"
SCREENSHOT_BASE = f"{REPO_URL}/blob/main/data/runs/pass3/"

PASSES = (1, 2, 3)
SETS = ("gold", "holdout")
CRITICAL = ("auth", "access", "api", "mcp_status", "verdict", "blocker", "evidence")
MINOR = ("one_liner", "api_breadth", "docs_url", "mcp_url", "requires_end_user_admin")
SCORED_ENUMS = ("auth_methods", "primary_auth", "access", "api_types", "has_public_docs",
                "mcp_status", "verdict", "blocker")
FIELD_LABEL = {
    "auth": "Auth", "access": "Access", "api": "API types", "mcp_status": "MCP status", "verdict": "Verdict",
    "blocker": "Blocker", "evidence": "Evidence present", "one_liner": "One-liner", "api_breadth": "API breadth",
    "docs_url": "Docs URL", "mcp_url": "MCP URL", "requires_end_user_admin": "Needs customer admin",
    "coverage": "Composio coverage",
}
WEAK_BELOW = CAVEAT_BELOW  # scripts/patterns.py thresholds, echoed in patterns.json meta.thresholds
MODERATE_BELOW = PATTERNS_MODERATE_BELOW

AUTH = {"oauth2": "OAuth2", "api_key": "API key", "basic": "Basic", "jwt_service_account": "JWT / service account",
        "custom_other": "Custom", "none": "None", "unknown": "Unknown"}
ACCESS = {"self_serve": "Self-serve", "self_serve_paid": "Self-serve (paid)", "gated_partner": "Partner-gated",
          "gated_sales": "Sales-gated", "no_public_api": "No public API"}
API = {"rest": "REST", "graphql": "GraphQL", "soap": "SOAP", "sdk_only": "SDK only", "webhook_only": "Webhooks only",
       "cli_only": "CLI", "none": "None"}
MCP = {"official": "Official", "community": "Community", "none": "None"}
VERDICT = {"buildable_now": "Buildable now", "buildable_with_approval": "Needs approval",
           "not_buildable": "Not buildable"}
BLOCKER = {"none": "None", "no_public_api": "No public API", "gated_access": "Gated access",
           "auth_undocumented": "Auth undocumented", "docs_insufficient": "Docs insufficient",
           "api_too_narrow": "API too narrow", "terms_restrict_automation": "Terms restrict automation",
           "preview_or_unstable": "Preview / unstable", "other": "Other"}
BREADTH = {"narrow": "narrow", "moderate": "moderate", "broad": "broad", "unknown": "unknown"}
CLAIMS = ("auth", "access", "api_surface", "mcp", "verdict", "one_liner")
CLAIM_LABEL = {"auth": "Auth", "access": "Access", "api_surface": "API surface", "mcp": "MCP", "verdict": "Verdict",
               "one_liner": "One-liner"}
SELF_SERVE = ("self_serve", "self_serve_paid")
GATED = ("gated_sales", "gated_partner")
BROWSE_STATUS = {"copied": "not flagged, copied from pass 2", "confirmed": "flagged, pass-2 values confirmed",
                 "changed": "flagged, values changed", "kept": "flagged, pass-2 values kept",
                 "failed": "flagged, browser check failed"}
SELECTOR_TEXT = {
    "confidence_not_high": "confidence is medium or low",
    "unresolved_deciding_fact": "notes mention a demo/sales gate, a coming-soon or beta MCP/API, or an unreadable page",
    "unsupported_or_invalid": "pass 2 logged unsupported evidence or validation errors",
    "thin_or_blocked_page": "a page cited for a deciding claim came back thin, blocked or failed",
}
RULE_TEXT = {
    "basic_transport_not_method": "HTTP Basic used only to send a key is not its own login method",
    "oauth_client_secret_not_api_key": "a secret used only as an OAuth client secret is not an API key",
    "mcp_only_oauth": "OAuth that only signs in to an MCP server is not API auth",
    "sales_or_review_gate": "a sales or app-review gate with no self-serve path is gated access",
    "preview_not_buildable_now": "a preview or waitlisted API is not buildable now",
    "official_graphql_omitted": "an official GraphQL API is missing from the API types",
    "public_docs_marked_false": "public docs exist but were marked absent",
    "missed_vendor_mcp": "a vendor-published MCP server was missed",
    "unsupported_evidence": "a claim's evidence was missing or did not support it",
}


# Inline SVG glyphs (24px grid, stroke icons). Decorative: always aria-hidden.
ICON_PATHS = {
    "list": '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/>'
            '<circle cx="4.5" cy="18" r="1"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="M20.5 20.5l-4.8-4.8"/>',
    "rules": '<path d="M12 3l8 3v6c0 4.6-3.3 7.9-8 9-4.7-1.1-8-4.4-8-9V6z"/><path d="M8.8 12.2l2.2 2.2 4.4-4.6"/>',
    "filter": '<path d="M3.5 5h17l-6.5 7.6V19l-4 2v-8.4z"/>',
    "browser": '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 9h18"/>'
               '<circle cx="6.5" cy="6.5" r=".6"/><circle cx="9" cy="6.5" r=".6"/>',
    "rows": '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 10h18M3 15h18M9 4v16"/>',
    "target": '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="1"/>',
    "chart": '<path d="M4 20V11M10 20V5M16 20v-6M2.5 20.5h19"/>',
    "person": '<circle cx="12" cy="8" r="3.8"/><path d="M4.5 20.5c.6-3.9 3.7-5.8 7.5-5.8s6.9 1.9 7.5 5.8"/>',
    "check": '<path d="M5 12.5l4.2 4.2L19 7"/>',
    "ext": '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14.5V19a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h4.5"/>',
    "loop": '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.8 4.5v4.2h-4.2"/>',
    "grid": '<rect x="4" y="4" width="6.5" height="6.5" rx="1.2"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.2"/>'
            '<rect x="4" y="13.5" width="6.5" height="6.5" rx="1.2"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.2"/>',
    "plug": '<path d="M9 3v5M15 3v5M6.5 8h11v3.5a5.5 5.5 0 0 1-11 0zM12 17v4"/>',
    "spark": '<path d="M12 3.5l1.9 5 5 1.9-5 1.9-1.9 5-1.9-5-5-1.9 5-1.9z"/><path d="M18.5 16.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"/>',
    "download": '<path d="M12 4v11M7 10.5l5 5 5-5M4.5 20h15"/>',
    "file": '<path d="M14 3.5H7a1.5 1.5 0 0 0-1.5 1.5v14A1.5 1.5 0 0 0 7 20.5h10a1.5 1.5 0 0 0 1.5-1.5V8z"/>'
            '<path d="M14 3.5V8h4.5"/>',
    "x": '<path d="M7 7l10 10M17 7L7 17"/>',
    "chevron": '<path d="M9 6l6 6-6 6"/>',
}
GITHUB_SVG = ('<svg class="gh" viewBox="0 0 16 16" width="18" height="18" aria-hidden="true" focusable="false">'
              '<path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 '
              '0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 '
              '1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 '
              '0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 '
              '2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 '
              '3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/>'
              '</svg>')


def icon(name: str, cls: str = "ic") -> str:
    return (f'<svg class="{cls}" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" '
            f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" '
            f'focusable="false">{ICON_PATHS[name]}</svg>')


RENDERED_NOTE = re.compile(r"\s*\(rendered in browser(?:; screenshot [^)]*)?\)")


class BuildError(SystemExit):
    pass


def ensure(cond: bool, message: str) -> None:
    """A data-dependent sentence on the page must be true of the data."""
    if not cond:
        raise BuildError(f"build_site: data contradicts the page: {message}")


# ---------------------------------------------------------------- loading


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def score_path(kind: str, n: int) -> str:
    return f"data/scores/{'holdout-' if kind == 'holdout' else ''}pass{n}.json"


def load_sources(root: Path) -> dict[str, Any]:
    p = lambda rel: root / rel  # noqa: E731
    rows = sorted(read_jsonl(p(P_ROWS)), key=lambda r: r["id"])
    evidence = {}
    for row in rows:
        ev = read_json(p(P_EVIDENCE) / f"{row['slug']}.json")
        evidence[row["slug"]] = {"snippets": ev.get("snippets") or {}}
    return {
        "apps": read_csv(p(P_APPS)),
        "rows": rows,
        "rows2": read_jsonl(p(P_ROWS2)),
        "evidence": evidence,
        "needs": read_json(p(P_NEEDS)),
        "flags": read_jsonl(p(P_FLAGS)),
        "browse": read_jsonl(p(P_BROWSE)),
        "log1": read_jsonl(p(P_LOG1)),
        "review": read_jsonl(p(P_REVIEW)),
        "spot": read_json(p(P_SPOT)),
        "patterns": read_json(p(P_PATTERNS)),
        "coverage": read_csv(p(P_COVERAGE)),
        "toolkits_total": read_json(p(P_TOOLKITS)).get("total"),
        "aliases": read_json(p(P_ALIASES)),
        "gold": read_jsonl(p(P_GOLD)),
        "holdout": read_jsonl(p(P_HOLDOUT)),
        "audit": p(P_AUDIT).read_text(encoding="utf-8"),
        "schema": read_json(p(P_SCHEMA)),
        "schema_readme": p(P_SCHEMA_README).read_text(encoding="utf-8"),
        "readme": p("README.md").read_text(encoding="utf-8"),
        "scores": {kind: {n: read_json(p(score_path(kind, n))) for n in PASSES} for kind in SETS},
        "root": root,
        "proof": read_json(p(P_PROOF)),
        "proof_rows": read_jsonl(p(P_PROOF_ROWS)),
        "proof_flags": read_jsonl(p(P_PROOF_FLAGS)),
    }


# ---------------------------------------------------------------- pure helpers (tested on synthetic fixtures)


def category_labels(schema_readme: str) -> dict[str, str]:
    """`category` enum -> label, from the category table in schema/README.md."""
    section = schema_readme.split("### category", 1)[-1].split("###", 1)[0]
    return dict(re.findall(r"^\|\s*`([a-z_]+)`\s*\|\s*([^|]+?)\s*\|", section, flags=re.M))


def short_label(label: str) -> str:
    """First segment of a category label ("Data, SEO and Scraping" -> "Data") for narrow table cells."""
    return re.split(r",| and ", label, maxsplit=1)[0].strip()


def enum_meanings(schema_readme: str, heading: str) -> dict[str, str]:
    """value -> meaning from the `### <heading>` table in schema/README.md."""
    parts = schema_readme.split(f"### {heading}\n", 1)
    if len(parts) < 2:
        return {}
    section = parts[1].split("\n### ", 1)[0].split("\n## ", 1)[0]
    return dict(re.findall(r"^\|\s*`([a-z0-9_]+)`\s*\|\s*(.+?)\s*\|\s*$", section, flags=re.M))


def is_self_serve(row: dict) -> bool:
    return row.get("access") in SELF_SERVE


def is_gated(row: dict) -> bool:
    return row.get("access") in GATED


def composio_status(row: dict) -> str:
    return (row.get("composio_toolkit") or {}).get("status") or "unknown"


def easy_wins(rows: list[dict]) -> list[dict]:
    """Buildable now, official MCP, and no Composio toolkit yet."""
    return [r for r in rows if r.get("verdict") == "buildable_now" and r.get("mcp_status") == "official"
            and composio_status(r) == "none"]


def outreach(rows: list[dict]) -> dict[str, list[dict]]:
    """Buildable only with vendor approval, split by the kind of gate."""
    gated = [r for r in rows if r.get("verdict") == "buildable_with_approval"]
    return {access: [r for r in gated if r.get("access") == access] for access in GATED} | {
        "other": [r for r in gated if r.get("access") not in GATED]}


def category_access(rows: list[dict], categories: list[str]) -> list[dict]:
    """Self-serve vs gated count per category, most self-serve first (ties keep schema order)."""
    out = []
    for i, cat in enumerate(categories):
        in_cat = [r for r in rows if r.get("category") == cat]
        out.append({"category": cat, "order": i, "total": len(in_cat),
                    "self_serve": sum(is_self_serve(r) for r in in_cat), "gated": sum(is_gated(r) for r in in_cat)})
    return sorted(out, key=lambda c: (-c["self_serve"], c["gated"], c["order"]))


def auth_summary(rows: list[dict]) -> dict[str, int]:
    combos = Counter(auth_combo(r) for r in rows)
    return {"total": len(rows), "oauth2_primary": sum(r.get("primary_auth") == "oauth2" for r in rows),
            "api_key_primary": sum(r.get("primary_auth") == "api_key" for r in rows),
            "oauth2_and_api_key": combos["oauth2_and_api_key"], "api_key_only": combos["api_key_only"],
            "oauth2_only": combos["oauth2_only"]}


def acc_class(value: float) -> str:
    """Colour band for an accuracy cell: good (green) at 90%+, mid (amber) from 80%, weak (red) below."""
    return "weak" if value < WEAK_BELOW else "mid" if value < MODERATE_BELOW else "good"


def acc_legend() -> str:
    """'green 90%+, amber 80–89%, red under 80%' with every threshold sourced from patterns.json."""
    hi = f"{P_PATTERNS}:meta.thresholds.moderate_below"
    lo = f"{P_PATTERNS}:meta.thresholds.caveated_below"
    return (f'<span class="lg lg-good">green {n(MODERATE_BELOW, hi, "pct")}+</span>, '
            f'<span class="lg lg-mid">amber {n(WEAK_BELOW, lo, "pctn")}–'
            f'{n(round(MODERATE_BELOW - 0.01, 2), f"{P_PATTERNS}:minus(meta.thresholds.moderate_below,0.01)", "pct")}</span>, '
            f'<span class="lg lg-weak">red under {n(WEAK_BELOW, lo, "pct")}</span>')


def tag_for(accuracies: list[float]) -> str:
    """strong / moderate / rough from the weakest field accuracy a finding relies on."""
    low = min(accuracies)
    return "rough" if low < WEAK_BELOW else "moderate" if low < MODERATE_BELOW else "strong"


def field_acc(score: dict, key: str) -> float:
    summary = score["summary"]
    entry = summary["coverage_accuracy"] if key == "coverage" else summary["field_accuracy"][key]
    return entry["accuracy"]


def _join(values: list[str], labels: dict[str, str]) -> str:
    names = [labels.get(v, v) for v in values]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _set_reason(agent: list[str], gold: list[str], labels: dict[str, str]) -> str:
    a, g = list(agent), list(gold)
    missing = [v for v in g if v not in a]
    extra = [v for v in a if v not in g]
    if missing and not extra:
        return f"agent listed {_join(a, labels)} only; gold also has {_join(missing, labels)}"
    if extra and not missing:
        return f"agent added {_join(extra, labels)}; gold does not count it"
    return f"agent said {_join(a, labels)}; gold says {_join(g, labels)}"


def miss_reason(unit: str, gold: Any, agent: Any) -> str:
    """One generic sentence explaining a critical miss, generated from gold vs agent values."""
    if unit == "auth":
        a_methods, g_methods = agent["auth_methods"], gold["auth_methods"]
        a_primary, g_primary = agent["primary_auth"], gold["primary_auth"]
        if sorted(a_methods) == sorted(g_methods):
            if a_primary == g_primary:
                return "auth differs"
            return f"same methods, but agent picked {AUTH.get(a_primary)} as primary; gold picks {AUTH.get(g_primary)}"
        reason = _set_reason(a_methods, g_methods, AUTH)
        if a_primary != g_primary and len(g_methods) > 1:
            reason += f" (gold's primary is {AUTH.get(g_primary)})"
        return reason
    if unit == "api":
        parts = []
        if sorted(agent["api_types"]) != sorted(gold["api_types"]):
            parts.append(_set_reason(agent["api_types"], gold["api_types"], API))
        if agent.get("has_public_docs") != gold.get("has_public_docs"):
            parts.append("public-docs flag differs")
        return "; ".join(parts) or "API differs"
    if unit == "access":
        return f"agent said {ACCESS.get(agent, agent).lower()}; gold says {ACCESS.get(gold, gold).lower()}"
    if unit == "mcp_status":
        word = {"official": "official MCP", "community": "community MCP only", "none": "no MCP"}
        return f"agent said {word.get(agent, agent)}; gold says {word.get(gold, gold)}"
    if unit == "verdict":
        return f"agent said {VERDICT.get(agent, agent).lower()}; gold says {VERDICT.get(gold, gold).lower()}"
    if unit == "blocker":
        word = lambda v: "no blocker" if v == "none" else BLOCKER.get(v, v).lower()  # noqa: E731
        return f"agent said {word(agent)}; gold says {word(gold)}"
    if unit == "evidence":
        return "required evidence URLs missing"
    return "value differs"


def critical_misses(score: dict) -> list[dict]:
    """Per app with at least one critical miss: the missed units, values and a generated reason."""
    out = []
    for app in score["apps"]:
        units = []
        for unit in CRITICAL:
            u = (app.get("units") or {}).get(unit)
            if u and not u.get("match"):
                units.append({"unit": unit, "gold": u.get("gold"), "agent": u.get("agent"),
                              "reason": miss_reason(unit, u.get("gold"), u.get("agent"))})
        if units:
            out.append({"slug": app["slug"], "name": app["name"], "units": units,
                        "label_notes": app.get("label_notes") or ""})
    return out


def row_correct(score: dict) -> list[str]:
    return [a["slug"] for a in score["apps"] if a.get("row_correct")]


def regressions(before: dict, after: dict) -> list[str]:
    """Slugs that were whole-row correct before and are not after."""
    now = set(row_correct(after))
    return [s for s in row_correct(before) if s not in now]


def changed_rows(before: list[dict], after: list[dict], fields: tuple[str, ...], exclude: set[str]) -> list[dict]:
    """Rows (not in `exclude`) where any of `fields` differs between two runs."""
    old = {r["slug"]: r for r in before}
    out = []
    for row in after:
        prev = old.get(row["slug"])
        if prev is None or row["slug"] in exclude:
            continue
        diff = [f for f in fields if prev.get(f) != row.get(f)]
        if diff:
            out.append({"slug": row["slug"], "name": row["name"], "fields": diff,
                        "before": {f: prev.get(f) for f in diff}, "after": {f: row.get(f) for f in diff}})
    return out


def gold_audit_corrections(audit_md: str) -> list[str]:
    """Apps whose scored label changed in the audit's "Fields changed" table (evidence/notes-only rows excluded)."""
    section = audit_md.split("## Fields changed", 1)[-1].split("\n## ", 1)[0]
    apps: list[str] = []
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or set(cells[0]) <= {"-", " "} or cells[0] == "App":
            continue
        if any(tok in SCORED_ENUMS for tok in re.findall(r"`([^`]+)`", cells[1])) and cells[0] not in apps:
            apps.append(cells[0])
    return apps


def relativize(value: Any, root: str, replacements: dict[str, str] | None = None) -> Any:
    """Replace absolute paths under `root` (and any explicit replacements) with repo-relative ones."""
    if isinstance(value, dict):
        return {k: relativize(v, root, replacements) for k, v in value.items()}
    if isinstance(value, list):
        return [relativize(v, root, replacements) for v in value]
    if isinstance(value, str):
        for old, new in (replacements or {}).items():
            if value == old:
                return new
        prefix = root.rstrip("/") + "/"
        if value.startswith(prefix):
            return value[len(prefix):]
        if value == root.rstrip("/"):
            return "."
    return value


def data_as_of(sources: dict) -> str:
    """Latest scored_at / spot-check date in the data (YYYY-MM-DD)."""
    dates = [s["scored_at"][:10] for kind in SETS for s in sources["scores"][kind].values()]
    dates.append(str(sources["spot"].get("checked_at"))[:10])
    return max(dates)


# ---------------------------------------------------------------- html helpers


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def fmt_value(value: float, fmt: str) -> str:
    if fmt == "pct":
        return f"{round(value * 100)}%"
    if fmt == "pctn":  # a percentage shown without its % sign (e.g. the "80" in "80–89%")
        return f"{round(value * 100)}"
    if fmt == "usd":
        return f"${value:.2f}"
    if fmt == "min":
        return f"{value / 60:.0f}"
    if fmt == "s":
        return f"{value:.0f}"
    return f"{int(value):,}" if float(value).is_integer() else f"{value}"


def n(value: float, src: str, fmt: str = "int", cls: str = "") -> str:
    """A data-driven number; check_site.py recomputes `src` and compares it with the text shown."""
    klass = f' class="{cls}"' if cls else ""
    return f'<span{klass} data-src="{esc(src)}" data-fmt="{fmt}">{fmt_value(value, fmt)}</span>'


def link(url: str | None, text: str, cls: str = "") -> str:
    if not url:
        return esc(text)
    klass = f' class="{cls}"' if cls else ""
    return f'<a{klass} href="{esc(url)}" target="_blank" rel="noopener">{esc(text)}</a>'


def chip(text: str, kind: str = "") -> str:
    return f'<span class="chip {kind}">{esc(text)}</span>'


def first_url(row: dict, claim: str) -> str | None:
    items = (row.get("evidence") or {}).get(claim) or []
    return items[0].get("url") if items else None


def names(rows: list[dict]) -> str:
    return ", ".join(f'<span data-text>{esc(r["name"])}</span>' for r in rows)


def rows_src(filt: str) -> str:
    return f"{P_ROWS}:count({filt})"


def score_src(kind: str, n_pass: int, key: str) -> str:
    if key == "coverage":
        return f"{score_path(kind, n_pass)}:summary.coverage_accuracy.accuracy"
    return f"{score_path(kind, n_pass)}:summary.field_accuracy.{key}.accuracy"


def acc_n(scores: dict, kind: str, n_pass: int, key: str) -> str:
    return n(field_acc(scores[kind][n_pass], key), score_src(kind, n_pass, key), "pct")


def set_size_n(scores: dict, kind: str) -> str:
    return n(scores[kind][3]["summary"]["n_gold"], f"{score_path(kind, 3)}:summary.n_gold")


def as_of_span(m: dict) -> str:
    return f'<span data-src="as_of()" data-fmt="date">{esc(m["as_of"])}</span>'


def coverage_facts(src: dict) -> dict[str, Any]:
    """Composio coverage numbers as page spans: toolkit count, covered / uncovered / total, aliases, snapshot date."""
    cov = src["coverage"]
    covered = sum(r["status"] == "exists" for r in cov)
    uncovered = sum(r["status"] == "none" for r in cov)
    aliases = [k for k in src["aliases"] if not k.startswith("_")]
    snapshot = max(r["checked_at"] for r in cov)[:10]
    return {
        "covered": covered, "uncovered": uncovered, "total": len(cov),
        "toolkits_n": n(src["toolkits_total"], f"{P_TOOLKITS}:total"),
        "covered_n": n(covered, f"{P_COVERAGE}:count(status=exists)"),
        "uncovered_n": n(uncovered, f"{P_COVERAGE}:count(status=none)"),
        "total_n": n(len(cov), f"{P_COVERAGE}:count()"),
        "aliases_n": n(len(aliases), f"{P_ALIASES}:nkeys()"),
        "date": f'<span data-src="{P_COVERAGE}:max_date(checked_at)" data-fmt="date">{esc(snapshot)}</span>',
    }


def cat_size_n(m: dict) -> str:
    sizes = Counter(r["category"] for r in m["rows"])
    ensure(len(set(sizes.values())) == 1, "every category has the same number of apps")
    return n(max(sizes.values()), f"{P_ROWS}:max_per(category)")


def rows_ok_n(scores: dict, kind: str, n_pass: int) -> str:
    return n(scores[kind][n_pass]["summary"]["n_row_correct"], f"{score_path(kind, n_pass)}:summary.n_row_correct")


# ---------------------------------------------------------------- model


def build_model(src: dict) -> dict[str, Any]:
    rows = src["rows"]
    total = len(rows)
    thresholds = src["patterns"]["meta"]["thresholds"]
    ensure(thresholds == {"caveated_below": WEAK_BELOW, "moderate_below": MODERATE_BELOW},
           "accuracy colour thresholds match patterns.json")
    labels = category_labels(src["schema_readme"])
    categories = list(labels)
    nh_rows = {r["slug"]: r for r in src["needs"]["rows"]}
    browse = {b["slug"]: b for b in src["browse"]}
    gold_slugs = {g["slug"] for g in src["gold"]}
    holdout_slugs = {g["slug"] for g in src["holdout"]}
    scores = src["scores"]
    misses = {kind: critical_misses(scores[kind][3]) for kind in SETS}
    miss_by_slug: dict[str, list[tuple[str, dict]]] = {}
    for kind in SETS:
        for m in misses[kind]:
            for u in m["units"]:
                miss_by_slug.setdefault(m["slug"], []).append((kind, u))
    return {
        "rows": rows, "total": total, "labels": labels, "categories": categories, "nh": nh_rows,
        "browse": browse, "gold_slugs": gold_slugs, "holdout_slugs": holdout_slugs, "scores": scores,
        "misses": misses, "miss_by_slug": miss_by_slug,
        "regressions": regressions(scores["gold"][2], scores["gold"][3]),
        "holdout_regressions": regressions(scores["holdout"][2], scores["holdout"][3]),
        "easy": easy_wins(rows), "outreach": outreach(rows),
        "cat_access": category_access(rows, categories), "auth": auth_summary(rows),
        "as_of": data_as_of(src),
    }


# ---------------------------------------------------------------- sections


def section_hero(src: dict, m: dict) -> str:
    rows, total = m["rows"], m["total"]
    g3, h3 = m["scores"]["gold"][3], m["scores"]["holdout"][3]
    tot = n(total, rows_src(""))
    a = m["auth"]
    oauth_rows = [r for r in rows if r["primary_auth"] == "oauth2"]
    key_only = [r for r in rows if auth_combo(r) == "api_key_only"]
    auth_acc = [field_acc(g3, "auth"), field_acc(h3, "auth")]
    auth_tag = tag_for(auth_acc)
    ensure(a["oauth2_primary"] > a["api_key_primary"], "OAuth2 is the most common primary auth")

    # (b) categories
    ca = m["cat_access"]
    top_self = max(c["self_serve"] for c in ca)
    top_gated = max(c["gated"] for c in ca)
    most_self = [c for c in ca if c["self_serve"] == top_self]
    most_gated = [c for c in ca if c["gated"] == top_gated]
    bars = []
    for c in ca:
        cat = c["category"]
        filt_self = f"category={cat}&access=self_serve|self_serve_paid"
        filt_gated = f"category={cat}&access=gated_sales|gated_partner"
        w_self = 100 * c["self_serve"] / max(c["total"], 1)
        w_gated = 100 * c["gated"] / max(c["total"], 1)
        bars.append(
            f'<li><span class="cb-label">{esc(m["labels"][cat])}</span>'
            f'<span class="cb-bar" aria-hidden="true"><span class="cb-self" style="width:{w_self:.0f}%"></span>'
            f'<span class="cb-gated" style="width:{w_gated:.0f}%"></span></span>'
            f'<span class="cb-num">{n(c["self_serve"], rows_src(filt_self))}'
            f'<span class="sep">/</span>{n(c["gated"], rows_src(filt_gated), cls="gated-n")}</span></li>')
    acc_acc = [field_acc(g3, "access"), field_acc(h3, "access")]
    acc_tag = tag_for(acc_acc)

    def cat_names(cs: list[dict]) -> str:
        return " and ".join(esc(m["labels"][c["category"]]) for c in cs)

    # (c) blockers
    blockers = Counter(r["blocker"] for r in rows)
    non_none = sorted(b for b in blockers if b != "none")
    ensure(non_none == ["gated_access"], "gated access is the only blocker in the final data")
    sales = [r for r in rows if r["access"] == "gated_sales"]
    partner = [r for r in rows if r["access"] == "gated_partner"]
    blk_acc = [field_acc(g3, "blocker"), field_acc(h3, "blocker")]
    blk_tag = tag_for(blk_acc)

    # (d) easy wins vs outreach
    easy = m["easy"]
    out = m["outreach"]
    cf = coverage_facts(src)
    ensure(all(composio_status(r) == "none" for r in easy), "every easy win is outside Composio's coverage")
    gated_total = len(out["gated_sales"]) + len(out["gated_partner"])
    ensure(not out["other"], "every buildable_with_approval row is sales- or partner-gated")
    win_acc = [field_acc(g3, "verdict"), field_acc(h3, "verdict"), field_acc(g3, "mcp_status"),
               field_acc(h3, "mcp_status")]
    win_tag = tag_for(win_acc)

    def tag(level: str, why: str) -> str:
        return f'<p class="tag-line"><span class="tag tag-{level}">{level}</span> {why}</p>'

    def pct_pair(kind_key: str) -> str:
        return (f'{n(field_acc(g3, kind_key), score_src("gold", 3, kind_key), "pct")} on gold, '
                f'{n(field_acc(h3, kind_key), score_src("holdout", 3, kind_key), "pct")} on holdout')

    easy_f = "verdict=buildable_now&mcp_status=official&composio_toolkit.status=none"
    primaries = Counter(r["primary_auth"] for r in rows).most_common()
    auth_bar = ('<div class="stack" role="img" aria-label="Primary auth: '
                + esc(", ".join(f"{AUTH.get(k, k)} {v}" for k, v in primaries)) + '">'
                + "".join(f'<span class="seg seg-{esc(k)}" style="width:{100 * v / total:.0f}%" '
                          f'title="{esc(AUTH.get(k, k))}: {v}"></span>' for k, v in primaries) + "</div>"
                + '<p class="stack-key">' + " ".join(
                    f'<span><i class="seg-{esc(k)}"></i>{esc(AUTH.get(k, k))} '
                    f'{n(v, rows_src(f"primary_auth={k}"))}</span>' for k, v in primaries) + "</p>")
    cards = f"""
<div class="cards">
  <article class="card">
    <h3 class="q">Which auth dominates?</h3>
    <p class="big">{n(a['oauth2_primary'], rows_src('primary_auth=oauth2'))}<span class="of">/{tot}</span></p>
    <p class="big-label">use OAuth2 as the primary auth method</p>
    {auth_bar}
    <ul class="facts">
      <li>{n(a['oauth2_and_api_key'], rows_src('auth_methods@=oauth2&auth_methods@=api_key'))} offer both OAuth2 and an API key</li>
      <li>{n(a['api_key_only'], rows_src('auth_methods==api_key'))} are API-key only</li>
      <li>{n(a['oauth2_only'], rows_src('auth_methods==oauth2'))} are OAuth2 only</li>
    </ul>
    <p class="eg">OAuth2-first: {names(pick_examples(oauth_rows, 3))}. Key only: {names(pick_examples(key_only, 2))}.</p>
    {tag(auth_tag, f"Auth scored {pct_pair('auth')}. Which method is primary is the weakest call.")}
  </article>
  <article class="card card-wide">
    <h3 class="q">Which categories are self-serve vs gated?</h3>
    <p class="legend-mini"><span class="key key-self"></span>self-serve <span class="key key-gated"></span>gated, out of {cat_size_n(m)} apps per category</p>
    <ol class="cat-bars">{''.join(bars)}</ol>
    <div class="callouts">
      <p class="callout c-self"><span class="co-k">Most self-serve</span>{cat_names(most_self)}
        <span class="co-n">{n(top_self, rows_src(f"category={most_self[0]['category']}&access=self_serve|self_serve_paid"))} of {n(most_self[0]['total'], rows_src(f"category={most_self[0]['category']}"))}{' each' if len(most_self) > 1 else ''}</span></p>
      <p class="callout c-gated"><span class="co-k">Most gated</span>{cat_names(most_gated)}
        <span class="co-n">{n(top_gated, rows_src(f"category={most_gated[0]['category']}&access=gated_sales|gated_partner"))} of {n(most_gated[0]['total'], rows_src(f"category={most_gated[0]['category']}"))}{' each' if len(most_gated) > 1 else ''}</span></p>
    </div>
    {tag(acc_tag, f"Access scored {pct_pair('access')}.")}
  </article>
  <article class="card">
    <h3 class="q">What is the most common blocker?</h3>
    <p class="big">{n(blockers['gated_access'], rows_src('blocker=gated_access'))}<span class="of">/{tot}</span></p>
    <p class="big-label">are blocked by gated access, the only blocker found</p>
    <ul class="facts">
      <li>{n(len(sales), rows_src('access=gated_sales'))} behind a sales gate (contract or demo), e.g. {names(pick_examples(sales, 2))}</li>
      <li>{n(len(partner), rows_src('access=gated_partner'))} behind a partner or app review, e.g. {names(pick_examples(partner, 2))}</li>
      <li>{n(blockers['none'], rows_src('blocker=none'))} have no blocker at all</li>
    </ul>
    {tag(blk_tag, f"Blocker scored {pct_pair('blocker')}.")}
  </article>
  <article class="card card-wide">
    <h3 class="q">Easy wins vs outreach?</h3>
    <div class="split">
      <div><p class="big good">{n(len(easy), rows_src(easy_f))}</p>
        <p class="big-label">easy wins, found among the <strong>{cf['uncovered_n']} of {cf['total_n']}</strong> apps Composio has no toolkit for yet: buildable now, with an official MCP server</p>
        <p class="eg">e.g. {names(pick_examples(easy, 3))}</p></div>
      <div><p class="big warn">{n(gated_total, rows_src('verdict=buildable_with_approval'))}</p>
        <p class="big-label">need outreach first: a sales or partner gate, whether or not Composio covers them</p>
        <p class="eg">e.g. {names(pick_examples(out['gated_sales'], 1) + pick_examples(out['gated_partner'], 2))}</p></div>
    </div>
    <p class="howknow"><span class="hk-k">How we know coverage</span>The Composio SDK catalog snapshot ({cf['toolkits_n']} toolkits, <code>data/composio_toolkits.json</code>, {cf['date']}) matched to the apps by slug and name, plus a manual alias map (<code>data/composio_aliases.json</code>), in <code>scripts/composio_coverage.py</code>. <a href="#tools">Details</a> · <a href="#next">Both lists</a></p>
    {tag(win_tag, f"Verdict scored {pct_pair('verdict')}; MCP status {pct_pair('mcp_status')}.")}
  </article>
</div>"""

    stats = f"""
<dl class="stats">
  <div><dt>buildable now</dt><dd>{n(sum(r['verdict'] == 'buildable_now' for r in rows), rows_src('verdict=buildable_now'))}<span class="of">/{tot}</span></dd></div>
  <div><dt>have an official MCP server</dt><dd>{n(sum(r['mcp_status'] == 'official' for r in rows), rows_src('mcp_status=official'))}<span class="of">/{tot}</span></dd></div>
  <div><dt>easy wins not on Composio</dt><dd>{n(len(easy), rows_src(easy_f))}</dd></div>
  <div><dt>flagged for a human check</dt><dd class="human-n">{n(src['needs']['needs_human_count'], f'{P_NEEDS}:needs_human_count')}<span class="of">/{n(src['needs']['total'], f'{P_NEEDS}:total')}</span></dd></div>
</dl>"""

    return f"""
<section id="patterns" class="hero" aria-labelledby="h-patterns">
  <p class="eyebrow">Case study · Composio app research</p>
  <h1 id="h-patterns">{TITLE.format(n=tot)}</h1>
  <div class="sub-row">
    <p class="subtitle">Which apps an agent toolkit can be built for today, which need outreach first, and how far to trust the answers.</p>
    <div class="hero-btns"><a class="btn btn-ghost" href="https://composio.notion.site/AI-Product-Ops-Intern-The-take-home-assignment-391f261a6dfe80e9946bee899d3a3762" target="_blank" rel="noopener"><svg class="btn-ic" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8M8 17h5"/></svg><span>Assignment brief</span></a>
    <a class="btn btn-ghost" href="{esc(REPO_URL)}" target="_blank" rel="noopener">{GITHUB_SVG}<span>{esc(REPO_LABEL)}</span></a></div>
  </div>
  <p class="lead">An agent pipeline researched {tot} apps (auth, access, API, MCP, Composio coverage, a buildability verdict and the evidence behind each), checked itself in three loops, and was scored against hand-labelled answers.</p>
  <h2 class="kicker">The patterns</h2>
  {cards}
  {stats}
</section>"""


def section_accuracy(src: dict, m: dict) -> str:
    s = m["scores"]
    rowsx = []
    for key in CRITICAL:
        cells = []
        for kind in SETS:
            vals = [field_acc(s[kind][p], key) for p in PASSES]
            for i, p in enumerate(PASSES):
                v = vals[i]
                cls = acc_class(v)
                delta = ""
                if i > 0 and v != vals[i - 1]:
                    delta = '<span class="up" aria-label="up">▲</span>' if v > vals[i - 1] else \
                        '<span class="down" aria-label="down">▼</span>'
                cells.append(f'<td class="acc {cls}{" set-start" if i == 0 else ""}">'
                             f'<span class="bar" style="--w:{v * 100:.0f}%"></span>'
                             f'{n(v, score_src(kind, p, key), "pct")}{delta}</td>')
        rowsx.append(f'<tr><th scope="row">{FIELD_LABEL[key]}</th>{"".join(cells)}</tr>')

    def whole(kind: str) -> str:
        return " → ".join(n(s[kind][p]["summary"]["n_row_correct"], f"{score_path(kind, p)}:summary.n_row_correct")
                          for p in PASSES)

    g = [s["gold"][p]["summary"]["n_row_correct"] for p in PASSES]
    h = [s["holdout"][p]["summary"]["n_row_correct"] for p in PASSES]
    n_gold = s["gold"][3]["summary"]["n_gold"]
    n_hold = s["holdout"][3]["summary"]["n_gold"]
    ensure(g[1] - g[0] > 0 and g[1] - g[0] > g[2] - g[1], "pass 2 gave the main whole-row lift on gold")
    ensure(g[2] <= g[1] and len(m["regressions"]) >= 1, "pass 3 did not add whole-row accuracy on gold")
    ensure(len(set(h)) == 1, "the holdout whole-row score did not move")
    gains = {k: field_acc(s["gold"][2], k) - field_acc(s["gold"][1], k) for k in CRITICAL}
    best = max(CRITICAL, key=lambda k: (gains[k], -CRITICAL.index(k)))
    ensure(gains[best] > 0, "pass 2 raised at least one gold field")
    hgains = {k: field_acc(s["holdout"][3], k) - field_acc(s["holdout"][1], k) for k in CRITICAL}
    hbest = max(CRITICAL, key=lambda k: (hgains[k], -CRITICAL.index(k)))
    browse = src["browse"]
    pages_local = sum(b.get("pages_local", 0) for b in browse)
    pages_cloud = sum(b.get("pages_cloud", 0) for b in browse)
    flagged = sum(b.get("flagged", False) for b in browse)
    spot = src["spot"]["result"]
    ng = n(n_gold, f"{score_path('gold', 3)}:summary.n_gold")
    nh = n(n_hold, f"{score_path('holdout', 3)}:summary.n_gold")

    holdout_extra = ""
    if hgains[hbest] > 0:
        holdout_extra = (f", even though holdout {FIELD_LABEL[hbest].lower()} rose "
                         f"{n(field_acc(s['holdout'][1], hbest), score_src('holdout', 1, hbest), 'pct')} → "
                         f"{n(field_acc(s['holdout'][3], hbest), score_src('holdout', 3, hbest), 'pct')}")
    return f"""
<section id="accuracy" aria-labelledby="h-accuracy">
  <h2 id="h-accuracy">How accurate is it?</h2>
  <p class="section-lead">Field accuracy against hand-labelled answers, after each loop: a gold set of {ng} apps and a sealed holdout of {nh} apps the rules never saw. Colours: {acc_legend()}.</p>
  <div class="acc-layout">
    <div class="table-wrap acc-wrap">
      <table class="acc-table">
        <caption class="sr-only">Critical-field accuracy by pass on the gold and holdout sets</caption>
        <thead>
          <tr><td></td><th scope="colgroup" colspan="3" class="set-start">Gold ({ng} apps)</th><th scope="colgroup" colspan="3" class="set-start">Holdout ({nh} apps)</th></tr>
          <tr><th scope="col">Field</th>{''.join(f'<th scope="col"{" class=set-start" if p == 1 else ""}><span class="pl">Pass {p}</span><span class="ps">P{p}</span></th>' for _ in SETS for p in PASSES)}</tr>
        </thead>
        <tbody>{''.join(rowsx)}</tbody>
      </table>
    </div>
    <div class="acc-side">
      <p class="whole"><strong>Whole rows fully right</strong> (every critical field)<br>
        gold {whole('gold')} of {ng}<br>holdout {whole('holdout')} of {nh}</p>
      <p class="badge-spot" title="{esc(src['spot']['scope_note'])}"><span class="human-tag">human</span>
        Spot-check: {n(spot['rows_correct'], f'{P_SPOT}:result.rows_correct')}/{n(spot['rows_checked'], f'{P_SPOT}:result.rows_checked')} rows,
        {n(spot['claims_correct'], f'{P_SPOT}:result.claims_correct')}/{n(spot['claims_checked'], f'{P_SPOT}:result.claims_checked')} claims
        <span class="scope" data-text>{esc(src['spot']['scope_note'])}</span></p>
    </div>
  </div>
  <ul class="takeaways">
    <li><strong>The rule checker (pass 2) gave the main lift.</strong> Gold whole rows went {n(g[0], f"{score_path('gold', 1)}:summary.n_row_correct")} → {n(g[1], f"{score_path('gold', 2)}:summary.n_row_correct")}, and {FIELD_LABEL[best].lower()} went {n(field_acc(s['gold'][1], best), score_src('gold', 1, best), 'pct')} → {n(field_acc(s['gold'][2], best), score_src('gold', 2, best), 'pct')}.</li>
    <li><strong>The browser pass added proof, not accuracy.</strong> It re-read the {n(flagged, f'{P_BROWSE}:count(flagged=true)')} flagged rows on {n(pages_local, f'{P_BROWSE}:sum(pages_local)')} local and {n(pages_cloud, f'{P_BROWSE}:sum(pages_cloud)')} cloud-rendered pages, with quotes and screenshots, but gold whole rows went {n(g[1], f"{score_path('gold', 2)}:summary.n_row_correct")} → {n(g[2], f"{score_path('gold', 3)}:summary.n_row_correct")} ({n(len(m['regressions']), f"{score_path('gold', 2)}:regressions({score_path('gold', 3)})")} regression).</li>
    <li><strong>The holdout did not move.</strong> It stayed at {n(h[0], f"{score_path('holdout', 3)}:summary.n_row_correct")} of {nh} whole rows in all three passes{holdout_extra}. Gold informed some checker rules, so the holdout is the more honest number.</li>
  </ul>
</section>"""


def _scored_note(m: dict, slug: str) -> str:
    by_kind: dict[str, list[str]] = {}
    for kind, u in m["miss_by_slug"].get(slug, []):
        label = FIELD_LABEL[u["unit"]].lower()
        part = f"{label}: {u['reason']}" if u["unit"] in ("auth", "api") else \
            f"{label} (gold: {_val(u['unit'], u['gold']).lower()})"
        by_kind.setdefault(kind, []).append(part)
    return "".join(f'<span class="warn-note">{esc(kind)} label disagrees on {esc("; ".join(parts))}</span>'
                   for kind, parts in by_kind.items())


def section_next(src: dict, m: dict) -> str:
    easy = m["easy"]
    out = m["outreach"]
    cf = coverage_facts(src)
    easy_f = "verdict=buildable_now&mcp_status=official&composio_toolkit.status=none"
    gated_total = len(out["gated_sales"]) + len(out["gated_partner"])
    gated_uncovered = sum(composio_status(r) == "none" for g in GATED for r in out[g])

    def check_row(label: str, value: str, url: str | None) -> str:
        val = link(url, value) if url else esc(value)
        return (f'<li class="ck">{icon("check", "ck-ic")}<span class="ck-l">{label}</span>'
                f'<span class="ck-v">{val}</span></li>')

    build_cards = []
    for r in easy:
        api = " + ".join(API.get(x, x) for x in r["api_types"])
        build_cards.append(
            f'<article class="bcard"><header class="bc-head"><div><h4 class="bc-name" data-text>{esc(r["name"])}</h4>'
            f'<p class="bc-cat">{esc(m["labels"][r["category"]])}</p></div>'
            f'<span class="auth-chip" title="Primary auth">{esc(AUTH.get(r["primary_auth"], r["primary_auth"]))}</span>'
            f'</header><ul class="checks">'
            f'{check_row("Self-serve credentials", ACCESS.get(r["access"], r["access"]), first_url(r, "access"))}'
            f'{check_row("Documented public API", api, r.get("docs_url") or first_url(r, "api_surface"))}'
            f'{check_row("Official MCP server", "MCP link", r.get("mcp_url") or first_url(r, "mcp"))}'
            f'</ul>{_scored_note(m, r["slug"])}</article>')

    groups = []
    gate_text = {"gated_sales": ("Sales gate", "Sales contract or demo",
                                 "A contract, a demo or a sales-provisioned account comes before any API credentials."),
                 "gated_partner": ("Partner program", "Partner or app review",
                                   "The vendor reviews the app or approves a partner before it may call the API.")}
    for access in GATED:
        title, need, blurb = gate_text[access]
        rows = sorted(out[access], key=lambda r: (composio_status(r) != "none", r["id"]))
        cards = []
        for r in rows:
            has = composio_status(r) == "exists"
            cards.append(
                f'<article class="ocard"><div class="oc-main"><h5 class="oc-name" data-text>{esc(r["name"])}</h5>'
                f'<p class="oc-cat">{esc(m["labels"][r["category"]])}</p></div>'
                f'<p class="oc-need">{icon("person", "oc-ic")}{need}</p>'
                f'<p class="oc-meta">{link(first_url(r, "access"), "Gate evidence")}'
                f'<span class="cov {"cov-yes" if has else "cov-no"}">{"on Composio" if has else "not on Composio"}</span></p>'
                f'{_scored_note(m, r["slug"])}</article>')
        filt = f"verdict=buildable_with_approval&access={access}"
        groups.append(
            f'<div class="ogroup"><h4 class="og-title">{title} '
            f'<span class="count">{n(len(out[access]), rows_src(filt))}</span></h4>'
            f'<p class="og-blurb">{blurb} {n(sum(composio_status(r) == "none" for r in out[access]), rows_src(filt + "&composio_toolkit.status=none"))} '
            f'of these have no Composio toolkit yet.</p><div class="ocards">{"".join(cards)}</div></div>')

    return f"""
<section id="next" aria-labelledby="h-next">
  <h2 id="h-next">What Composio should do next</h2>
  <div class="handover">
    <p class="ho-lead">This is the hand-over. Per the catalog snapshot ({cf['date']}), Composio has no toolkit yet for <strong>{cf['uncovered_n']} of the {cf['total_n']}</strong> apps.
    <strong>{n(len(easy), rows_src(easy_f))}</strong> of them can be built today; the <strong>{n(gated_total, rows_src('verdict=buildable_with_approval'))}</strong> gated apps need outreach first ({n(gated_uncovered, rows_src('verdict=buildable_with_approval&composio_toolkit.status=none'))} of those are also uncovered).</p>
    <p class="ho-note">Gated with evidence is a correct finding, not a failure: every outreach card links the vendor page that shows the gate. Every check links its own evidence.</p>
  </div>
  <div class="nx-head nx-good"><h3>{icon("check", "nx-ic")}Ready to build <span class="count">{n(len(easy), rows_src(easy_f))}</span></h3>
    <p>Not on Composio, buildable now, official MCP server. Each check links the page that proves it.</p></div>
  <div class="bcards">{''.join(build_cards)}</div>
  <div class="nx-head nx-warn"><h3>{icon("person", "nx-ic")}Needs outreach <span class="count">{n(gated_total, rows_src('verdict=buildable_with_approval'))}</span></h3>
    <p>A documented API exists, but a person at the vendor has to say yes first. Apps without a Composio toolkit are listed first.</p></div>
  <div class="ogroups">{''.join(groups)}</div>
</section>"""


def _detail(src: dict, m: dict, r: dict) -> str:
    slug = r["slug"]
    snippets = src["evidence"][slug]["snippets"]
    nh = m["nh"].get(slug) or {}
    b = m["browse"].get(slug) or {}
    claims = []
    for claim in CLAIMS:
        items = (r.get("evidence") or {}).get(claim) or []
        if not items:
            continue
        lis = []
        for it in items:
            quotes = [s.get("quote") for s in snippets.get(claim, []) if s.get("url") == it.get("url") and s.get("quote")]
            shots = sorted({s.get("screenshot") for s in snippets.get(claim, [])
                            if s.get("url") == it.get("url") and s.get("screenshot")})
            q = "".join(f"<blockquote>{esc(_clip(x))}</blockquote>" for x in dict.fromkeys(quotes))
            shot = "".join(f' · {link(SCREENSHOT_BASE + s, "screenshot")}' for s in shots)
            text = RENDERED_NOTE.sub("", it.get("note") or "").strip()
            rendered = '<span class="chip">rendered in browser</span>' if text != (it.get("note") or "").strip() else ""
            note = f'<span class="ev-note">{esc(text)} {rendered}</span>' if text else ""
            lis.append(f'<li>{link(it.get("url"), short_url(it.get("url")), "ev-url")}{shot}{note}{q}</li>')
        claims.append(f'<div class="ev-claim"><h5>{CLAIM_LABEL[claim]}</h5><ul>{"".join(lis)}</ul></div>')
    reasons = "".join(f'<li><code>{esc(x["id"])}</code> {esc(NEEDS_HUMAN_TEXT.get(x["id"], ""))}'
                      f'<span class="muted"> ({esc(x.get("detail"))})</span></li>' for x in nh.get("reasons") or [])
    status = b.get("status", "copied")
    browse_line = esc(BROWSE_STATUS.get(status, status))
    if b.get("flagged"):
        pages = b.get("pages") or []
        local = sum(p.get("browser") == "local" for p in pages)
        cloud = sum(p.get("browser") == "cloud" for p in pages)
        browse_line += (f" · flag reasons: {esc(', '.join(b.get('flag_reasons') or []))}"
                        f" · {n(local, f'{P_BROWSE}:count_pages(slug={slug};browser=local)')} local / "
                        f"{n(cloud, f'{P_BROWSE}:count_pages(slug={slug};browser=cloud)')} cloud pages")
        changed = [f for f in b.get("fields_changed") or [] if f not in ("evidence", "notes")]
        if status == "changed" and changed:
            browse_line += f" · changed: {esc(', '.join(changed))}"
    scored = ""
    for kind, members in (("gold", m["gold_slugs"]), ("holdout", m["holdout_slugs"])):
        if slug in members:
            ms = [u for k, u in m["miss_by_slug"].get(slug, []) if k == kind]
            verdict = "every critical field matches the hand label" if not ms else \
                "misses: " + "; ".join(f"{FIELD_LABEL[u['unit']].lower()} ({u['reason']})" for u in ms)
            scored += f"<p><strong>Scored in the {kind} set:</strong> {esc(verdict)}</p>"
    tk = r.get("composio_toolkit") or {}
    tk_line = (link(tk.get("evidence_url"), f"toolkit {tk.get('toolkit_slug')}") if tk.get("status") == "exists"
               else "no toolkit in the catalog snapshot")
    return f"""<div class="detail-inner">
  <div class="detail-grid">
    <div>
      <p class="d-one">{esc(r["one_liner"])}</p>
      <p class="d-notes"><strong>Notes.</strong> {esc(r.get("notes") or "None.")}</p>
      <p><strong>Docs:</strong> {link(r.get("docs_url"), r.get("docs_url") or "none")}</p>
      <p><strong>Composio:</strong> {tk_line}</p>
      <p><strong>Browser pass:</strong> {browse_line}</p>
      {scored}
      {f'<div class="d-human"><strong>Flagged for a human check:</strong><ul>{reasons}</ul></div>' if reasons else ''}
    </div>
    <div class="evidence">{"".join(claims)}</div>
  </div>
</div>"""


def short_url(url: str | None, limit: int = 72) -> str:
    """Host + path without scheme, query or a trailing slash; long paths are cut in the middle."""
    text = re.sub(r"^https?://(www\.)?", "", url or "").split("?", 1)[0].split("#", 1)[0].rstrip("/")
    return text if len(text) <= limit else text[: limit // 2 - 1] + "…" + text[-(limit // 2 - 1):]


def _clip(text: str, limit: int = 320) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


DD_CHEV = '<svg class="dd-chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>'
DD_TICK = '<svg class="dd-tick" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>'


def section_findings(src: dict, m: dict) -> str:
    rows = m["rows"]
    start = {a["slug"]: (a.get("start_url") or "").strip() for a in src["apps"]}
    body = []
    for r in rows:
        slug = r["slug"]
        nh = m["nh"].get(slug) or {}
        flagged = bool(nh.get("needs_human"))
        reasons = [x["id"] for x in nh.get("reasons") or []]
        others = [AUTH.get(x, x) for x in r["auth_methods"] if x != r["primary_auth"]]
        auth_html = link(first_url(r, "auth"), AUTH.get(r["primary_auth"], r["primary_auth"]))
        if others:
            auth_html += f'<span class="sub">+ {esc(", ".join(others))}</span>'
        api_html = link(first_url(r, "api_surface"), " + ".join(API.get(x, x) for x in r["api_types"]))
        api_html += f'<span class="sub" title="API breadth (a rough estimate)">' \
                    f'~{esc(BREADTH.get(r["api_breadth"], r["api_breadth"]))}</span>'
        mcp_url = r.get("mcp_url") or first_url(r, "mcp")
        mcp_html = link(mcp_url, MCP.get(r["mcp_status"], r["mcp_status"]),
                        f"mcp-{r['mcp_status']}") if mcp_url else esc(MCP.get(r["mcp_status"]))
        vcls = "v-now" if r["verdict"] == "buildable_now" else "v-appr" if r["verdict"] == "buildable_with_approval" \
            else "v-no"
        verdict_html = f'<a class="pill {vcls}" href="{esc(first_url(r, "verdict"))}" target="_blank" rel="noopener">' \
                       f'{esc(VERDICT.get(r["verdict"]))}</a>' if first_url(r, "verdict") else \
            f'<span class="pill {vcls}">{esc(VERDICT.get(r["verdict"]))}</span>'
        if r["blocker"] != "none":  # blocker shares the verdict cell; it is "none" exactly when buildable now
            verdict_html += f'<span class="sub blk">blocker: {esc(BLOCKER.get(r["blocker"], r["blocker"]).lower())}</span>'
        acls = "a-self" if r["access"] in SELF_SERVE else "a-gated"
        access_html = link(first_url(r, "access"), ACCESS.get(r["access"], r["access"]), acls)
        has_tk = composio_status(r) == "exists"
        flag_html = (f'<span class="flag" title="{esc("; ".join(reasons))}">check<span class="sr-only">: '
                     f'{esc(", ".join(reasons))}</span></span>' if flagged else '<span class="muted">—</span>')
        home = start.get(slug) or r.get("docs_url")
        name = esc(r["name"])
        name_html = (f'<a class="app-link" href="{esc(home)}" target="_blank" rel="noopener" '
                     f'title="Open {name} ({esc(short_url(home))})">{name}{icon("ext", "ext-ic")}</a>'
                     if home else f'<span class="app-name">{name}</span>')
        ctrl = f'aria-expanded="false" aria-controls="d-{esc(slug)}"'
        body.append(
            f'<tr class="row" id="app-{esc(slug)}" data-slug="{esc(slug)}">'
            f'<th scope="row" class="c-app" data-label="App"><div class="app-cell">'
            f'<button type="button" class="exp" {ctrl} title="Details" aria-label="Details for {name}">'
            f'{icon("chevron", "chev")}</button>{name_html}</div></th>'
            f'<td class="c-cat" data-label="Category"><span title="{esc(m["labels"][r["category"]])}">'
            f'{esc(short_label(m["labels"][r["category"]]))}</span></td>'
            f'<td class="c-one" data-label="What it does"><span class="clamp">{esc(r["one_liner"])}</span></td>'
            f'<td data-label="Auth">{auth_html}</td>'
            f'<td data-label="Access">{access_html}</td>'
            f'<td data-label="API">{api_html}</td>'
            f'<td data-label="MCP">{mcp_html}</td>'
            f'<td class="c-verdict" data-label="Verdict">{verdict_html}</td>'
            f'<td data-label="Composio">{"covered" if has_tk else chip("not yet", "hl")}</td>'
            f'<td class="c-hum{"" if flagged else " c-none"}" data-label="Human">{flag_html}</td>'
            f'<td data-label="Conf."><span class="conf conf-{esc(r["confidence"])}">{esc(r["confidence"])}</span></td>'
            f'<td class="c-more"><button type="button" class="exp exp-more" {ctrl}>'
            f'<span class="when-closed">Show details</span><span class="when-open">Hide details</span>'
            f'{icon("chevron", "chev")}</button></td>'
            f'</tr>'
            f'<tr class="detail" id="d-{esc(slug)}" data-slug="{esc(slug)}"><td colspan="11">{_detail(src, m, r)}</td></tr>')

    present = lambda field, labels: [(k, v) for k, v in labels.items() if any(r[field] == k for r in rows)]  # noqa: E731

    def group(key: str, label: str, options: list[tuple[str, str, str, int, str]], cls: str = "") -> str:
        """options: (value, text, data-src filter, count, title). An in-page dropdown, not a native <select>."""
        items = [f'<li role="option" class="dd-opt" data-value="" aria-selected="true" tabindex="-1">{DD_TICK}'
                 f'<span class="dd-text">All</span></li>']
        for value, text, src_key, count, title in options:
            tip = f' title="{esc(title)}"' if title else ""
            items.append(f'<li role="option" class="dd-opt" data-value="{esc(value)}" aria-selected="false" tabindex="-1"'
                         f'{tip}>{DD_TICK}<span class="dd-text">{esc(text)}</span>'
                         f'<span class="fc-n">{n(count, src_key)}</span></li>')
        return (f'<div class="dd" data-filter="{key}"><span class="fg-label" id="fl-{key}">{label}</span>'
                f'<button type="button" class="dd-btn" aria-haspopup="listbox" aria-expanded="false" '
                f'aria-labelledby="fl-{key} dv-{key}"><span class="dd-val" id="dv-{key}">All</span>{DD_CHEV}</button>'
                f'<ul class="dd-menu" role="listbox" aria-labelledby="fl-{key}" hidden>{"".join(items)}</ul></div>')

    def enum_opts(field: str, labels: dict[str, str]) -> list[tuple[str, str, str, int, str]]:
        return [(k, v, rows_src(f"{field}={k}"), sum(r[field] == k for r in rows), "") for k, v in present(field, labels)]

    nh_flag = {s: bool(x.get("needs_human")) for s, x in m["nh"].items()}
    filters = "".join([
        group("verdict", "Verdict", enum_opts("verdict", VERDICT)),
        group("access", "Access", enum_opts("access", ACCESS)),
        group("mcp_status", "MCP", enum_opts("mcp_status", MCP)),
        group("needs_human", "Human check", [
            ("true", "Flagged", f"{P_NEEDS_L}:count(needs_human=true)", sum(nh_flag.values()), ""),
            ("false", "Not flagged", f"{P_NEEDS_L}:count(needs_human=false)", sum(not v for v in nh_flag.values()), "")]),
        group("composio", "Composio", [
            ("exists", "Covered", rows_src("composio_toolkit.status=exists"),
             sum(composio_status(r) == "exists" for r in rows), ""),
            ("none", "Not yet", rows_src("composio_toolkit.status=none"),
             sum(composio_status(r) == "none" for r in rows), "")]),
        group("category", "Category", [
            (c, short_label(m["labels"][c]), rows_src(f"category={c}"), sum(r["category"] == c for r in rows),
             m["labels"][c]) for c in m["categories"]]),
    ])
    total = m["total"]
    return f"""
<section id="findings" aria-labelledby="h-findings">
  <h2 id="h-findings">Findings: all {n(total, rows_src(''))} apps</h2>
  <p class="section-lead">One row per app. The app name opens its website; every field links to its first evidence page. Open a row (the chevron, or anywhere on the row) for the quoted snippets, notes, the browser-pass result and any human-check reasons. API breadth is a rough sketch (it scored lowest of the fields).</p>
  <div class="toolbar">
    <div class="tb-top">
      <label class="search">{icon("search", "s-ic")}<span class="sr-only">Search apps</span><input type="search" id="q" placeholder="Search name or description" autocomplete="off"></label>
      <p class="count-line" aria-live="polite">Showing <strong id="shown" data-src="{esc(rows_src(''))}" data-fmt="int">{total}</strong> of {n(total, rows_src(''))}</p>
      <button type="button" class="clear-btn" id="clear" disabled>{icon("x", "x-ic")}Clear filters</button>
    </div>
    <div class="filters">{filters}</div>
    <p class="dl">{icon("download", "dl-ic")}Download the table: <a href="data/final.json" download="final.json" data-dl="final.json">final.json</a> · <a href="data/final.csv" download="final.csv" data-dl="final.csv">final.csv</a> · <a href="data/schema.md" download="schema.md" data-dl="schema.md">schema.md</a></p>
  </div>
  <div class="table-wrap findings-wrap">
    <table class="findings">
      <caption class="sr-only">All apps with auth, access, API, MCP, verdict, blocker, Composio coverage, human-check flag and confidence</caption>
      <thead><tr>
        <th scope="col" class="c-app">App</th><th scope="col">Category</th><th scope="col">What it does</th>
        <th scope="col">Auth</th><th scope="col">Access</th><th scope="col" title="API types; ~breadth is a rough estimate">API</th>
        <th scope="col">MCP</th><th scope="col">Verdict · blocker</th><th scope="col">Composio</th>
        <th scope="col" title="Flagged for a human check">Human</th><th scope="col">Conf.</th>
      </tr></thead>
      <tbody data-text>{''.join(body)}</tbody>
    </table>
    <p class="empty" id="empty" hidden>No apps match these filters. <button type="button" class="linkish" data-clear>Clear filters</button></p>
  </div>
  <nav class="pager" id="pager" aria-label="Table pages"></nav>
</section>"""


def section_agent(src: dict, m: dict) -> str:
    log1 = src["log1"]
    review = src["review"]
    browse = src["browse"]
    flags = src["flags"]
    s = m["scores"]
    model = Counter(x.get("llm_model") for x in log1).most_common(1)[0][0]
    provider = Counter(x.get("llm_provider") for x in log1).most_common(1)[0][0]
    apps = src["apps"]
    cf = coverage_facts(src)
    changed2 = sum(r["status"] == "changed" for r in review)
    unchanged2 = sum(r["status"] == "unchanged" for r in review)
    rule_counts = Counter(rule for r in review for rule in r.get("rules_fired") or [])
    flagged = sum(f["flagged"] for f in flags)
    reason_counts = Counter(x for f in flags for x in f.get("reasons") or [])
    st = Counter(b["status"] for b in browse)
    pl = sum(b.get("pages_local", 0) for b in browse)
    pc = sum(b.get("pages_cloud", 0) for b in browse)
    sessions = sum(b.get("cloud_sessions", 0) for b in browse)
    cost3 = sum(b.get("est_cost_usd") or 0 for b in browse)
    nh = src["needs"]
    spot = src["spot"]["result"]
    fixes = changed_rows(src["rows2"], m["rows"], ("mcp_status", "mcp_url"), m["gold_slugs"] | m["holdout_slugs"])
    corrections = gold_audit_corrections(src["audit"])
    per_cat = Counter(g["category"] for g in src["gold"])
    rows_ok = lambda kind, p: rows_ok_n(s, kind, p)  # noqa: E731
    lifts = [k for k in CRITICAL if field_acc(s["gold"][2], k) > field_acc(s["gold"][1], k)]
    ensure(bool(lifts), "pass 2 lifted at least one gold field")
    lift_txt = ", ".join(f"{FIELD_LABEL[k].lower()} {acc_n(s, 'gold', 1, k)} → {acc_n(s, 'gold', 2, k)}" for k in lifts)
    ensure(len(m["regressions"]) >= 1, "pass 3 has a gold regression to report")
    exa1 = sum(x.get("exa_cost_usd") or 0 for x in log1)
    regress_src = f"{score_path('gold', 2)}:regressions({score_path('gold', 3)})"

    def files(*names: str) -> str:
        return "".join(f"<code>{esc(x)}</code>" for x in names)

    def tiles(*pairs: tuple[str, str]) -> str:
        return '<dl class="sp-tiles">' + "".join(f"<div><dd>{v}</dd><dt>{k}</dt></div>" for k, v in pairs) + "</dl>"

    reasons_li = "".join(f"<li>{n(c, f'{P_FLAGS}:count(reasons@={k})')} {esc(SELECTOR_TEXT.get(k, k))}</li>"
                         for k, c in reason_counts.most_common())
    rules_li = "".join(f"<li>{n(c, f'{P_REVIEW}:count(rules_fired@={k})')} {esc(RULE_TEXT.get(k, k))}</li>"
                       for k, c in sorted(rule_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:4])
    nh_li = "".join(f"<li>{n(v, f'{P_NEEDS}:by_reason.{k}')} {esc(NEEDS_HUMAN_TEXT.get(k, k))}</li>"
                    for k, v in sorted(nh["by_reason"].items(), key=lambda kv: -kv[1]))

    # key, kind, icon, title, short metric, loop label, detail dict
    nodes = [
        ("input", "stage", "list", "Input", f"{n(len(apps), f'{P_APPS}:count()')} apps + hints", "", {
            "what": "The assignment list as a CSV: id, slug, name, category and a website hint for each app. The input is validated before any stage runs.",
            "why": "Every run, and every rerun, covers exactly the same apps with the same identities.",
            "acc": f"Identity fields are copied from the CSV, never written by the LLM, and Composio coverage is a catalog lookup ({cf['covered_n']} of {cf['total_n']} covered), so none of those can be hallucinated.",
            "io": (files("assignment list"), files("data/apps.csv", "data/composio_coverage.csv")),
            "tiles": [("apps", n(len(apps), f"{P_APPS}:count()")),
                      ("categories", n(len({a['category'] for a in apps}), f"{P_APPS}:ndistinct(category)")),
                      ("already on Composio", cf["covered_n"])]}),
        ("pass1", "stage", "search", "Pass 1 · Research agent",
         f"{n(sum(x.get('fetch_calls', 0) for x in log1), f'{P_LOG1}:sum(fetch_calls)')} pages read", "", {
            "what": f"Searches the web through Exa, reads official pages, and has an LLM ({esc(provider.title())}, <code>{esc(model)}</code>) fill one schema row per app from the fetched text only, quoting the sentence behind each claim.",
            "why": "A complete first answer for all apps, with evidence attached, that later loops can check instead of redo.",
            "acc": f"Evidence URLs must be pages the run actually fetched, quotes must appear in the page text, and consistency validators send a bad row back for one retry. Baseline: {rows_ok('gold', 1)} of {set_size_n(s, 'gold')} gold rows and {rows_ok('holdout', 1)} of {set_size_n(s, 'holdout')} holdout rows fully right.",
            "io": (files("data/apps.csv"), files("pass1/rows.jsonl", "pass1/evidence/", "pass1/log.jsonl")),
            "tiles": [("searches", n(sum(x.get("search_calls", 0) for x in log1), f"{P_LOG1}:sum(search_calls)")),
                      ("page fetches", n(sum(x.get("fetch_calls", 0) for x in log1), f"{P_LOG1}:sum(fetch_calls)")),
                      ("Exa spend", n(exa1, f"{P_LOG1}:sum(exa_cost_usd)", "usd"))]}),
        ("pass2", "stage", "rules", "Pass 2 · Rule checker",
         f"{n(changed2, f'{P_REVIEW}:count(status=changed)')} rows fixed",
         "re-researches only flagged fields", {
            "what": "Re-checks every row against general rules (no app names). Code fires a rule only when the notes or a vendor quote show the pattern; one LLM reviewer call per row can name more. Only the fields behind a fired rule are re-researched; every other field stays byte-identical.",
            "why": "Pass 1 made the same kinds of mistake again and again. Fixing those habits is cheaper and safer than redoing all apps.",
            "acc": f"The main lift: gold whole rows {rows_ok('gold', 1)} → {rows_ok('gold', 2)} of {set_size_n(s, 'gold')}; {lift_txt}. The sealed holdout stayed at {rows_ok('holdout', 2)} of {set_size_n(s, 'holdout')}. Most-fired rules:<ul>{rules_li}</ul>",
            "io": (files("pass1/rows.jsonl"), files("pass2/rows.jsonl", "pass2/review.jsonl")),
            "tiles": [("rows changed", n(changed2, f"{P_REVIEW}:count(status=changed)")),
                      ("left unchanged", n(unchanged2, f"{P_REVIEW}:count(status=unchanged)")),
                      ("rules fired", n(len(rule_counts), f"{P_REVIEW}:distinct(rules_fired)"))]}),
        ("selector", "stage", "filter", "Selector",
         f"{n(flagged, f'{P_FLAGS}:count(flagged=true)')} rows flagged", "", {
            "what": "Code picks the rows that still look shaky after pass 2. A row can have several reasons:",
            "why": "A real browser is slow and costs money, so it is spent only where a row is unsure.",
            "acc": f"Focuses the browser on {n(flagged, f'{P_FLAGS}:count(flagged=true)')} of {n(len(flags), f'{P_FLAGS}:count()')} rows; the rest are copied through untouched.<ul>{reasons_li}</ul>",
            "io": (files("pass2/rows.jsonl", "pass2/review.jsonl"), files("pass3/flags.jsonl")),
            "tiles": [("flagged", n(flagged, f"{P_FLAGS}:count(flagged=true)")),
                      ("copied through", n(st["copied"], f"{P_BROWSE}:count(status=copied)"))]}),
        ("pass3", "stage", "browser", "Pass 3 · Browser Use",
         f"{n(pl, f'{P_BROWSE}:sum(pages_local)')} + {n(pc, f'{P_BROWSE}:sum(pages_cloud)')} pages rendered",
         "rewrites only flagged fields", {
            "what": f"Renders each flagged row's pricing, signup, auth and MCP pages in local headless Chromium and saves a screenshot; only blocked, captcha or empty pages are retried on Browser Use Cloud ({n(sessions, f'{P_BROWSE}:sum(cloud_sessions)')} sessions). The same LLM reads the rendered text and may confirm or fix only the flagged fields, with quotes found on the page. It reads pages; it does not click or navigate.",
            "why": "A simple page fetch misses JavaScript docs, pricing tables, captchas and coming-soon banners.",
            "acc": f"Added proof more than accuracy: {n(st['confirmed'], f'{P_BROWSE}:count(status=confirmed)')} rows confirmed and {n(st['changed'], f'{P_BROWSE}:count(status=changed)')} changed, but gold whole rows went {rows_ok('gold', 2)} → {rows_ok('gold', 3)} ({n(len(m['regressions']), regress_src)} regression). Outside the scored sets it fixed {n(len(fixes), f'{P_ROWS2}:changed({P_ROWS};mcp_status,mcp_url;exclude_scored)')} MCP answers.",
            "io": (files("pass2/rows.jsonl", "pass3/flags.jsonl"), files("pass3/rows.jsonl", "pass3/browse.jsonl", "pass3/screenshots/")),
            "tiles": [("confirmed", n(st["confirmed"], f"{P_BROWSE}:count(status=confirmed)")),
                      ("changed", n(st["changed"], f"{P_BROWSE}:count(status=changed)")),
                      ("LLM cost", n(cost3, f"{P_BROWSE}:sum(est_cost_usd)", "usd"))]}),
        ("final", "stage", "rows", "Final rows",
         f"{n(sum(r['verdict'] == 'buildable_now' for r in m['rows']), rows_src('verdict=buildable_now'))} buildable now", "", {
            "what": "The final row per app, exported for people and agents, plus a needs-human flag for every row a person should look at.",
            "why": "One artifact that both the table on this page and any agent can read.",
            "acc": f"Flags are general rules only, so uncertainty is visible instead of hidden: {n(nh['needs_human_count'], f'{P_NEEDS}:needs_human_count')} of {n(nh['total'], f'{P_NEEDS}:total')} rows are flagged for a human check.",
            "io": (files("pass3/rows.jsonl"), files("final.json", "final.csv", "needs_human.json")),
            "tiles": [("rows", n(len(m["rows"]), rows_src(""))),
                      ("buildable now", n(sum(r["verdict"] == "buildable_now" for r in m["rows"]), rows_src("verdict=buildable_now"))),
                      ("flagged for a human", n(nh["needs_human_count"], f"{P_NEEDS}:needs_human_count"))]}),
        ("scoring", "stage", "target", "Scoring",
         f"gold {set_size_n(s, 'gold')} + holdout {set_size_n(s, 'holdout')}", "", {
            "what": "<code>scripts/score_gold.py</code> compares each pass with the locked hand labels, field by field, and counts a row only when every critical field is right and has evidence.",
            "why": "Turns 'looks better' into a number that can go down as well as up.",
            "acc": f"Gold whole rows {rows_ok('gold', 1)} → {rows_ok('gold', 2)} → {rows_ok('gold', 3)} of {set_size_n(s, 'gold')}; holdout {rows_ok('holdout', 1)} → {rows_ok('holdout', 2)} → {rows_ok('holdout', 3)} of {set_size_n(s, 'holdout')}.",
            "io": (files("pass*/rows.jsonl", "gold_sample.jsonl", "holdout_sample.jsonl"), files("data/scores/*.json")),
            "tiles": [("gold apps", set_size_n(s, "gold")), ("holdout apps", set_size_n(s, "holdout")),
                      ("gold rows right", rows_ok("gold", 3))]}),
        ("patterns", "stage", "chart", "Patterns + this page",
         f"{n(len(src['patterns']['patterns']), f'{P_PATTERNS}:len(patterns)')} headline patterns", "", {
            "what": "<code>scripts/patterns.py</code> clusters the final rows; <code>scripts/build_site.py</code> renders this page; <code>scripts/check_site.py</code> recomputes every number on it from its source file.",
            "why": "The point of the exercise is the patterns, not the rows, and they must not drift from the data.",
            "acc": "No number on this page is typed by hand, and sentences that depend on the data are asserted when the page is built.",
            "io": (files("final rows", "scores"), files("data/patterns/patterns.json", "site/")),
            "tiles": [("headline patterns", n(len(src["patterns"]["patterns"]), f"{P_PATTERNS}:len(patterns)"))]}),
    ]
    humans = {
        "final": [
            ("review", "Needs-human review", f"{n(nh['needs_human_count'], f'{P_NEEDS}:needs_human_count')} rows flagged", {
                "what": "A person looks at every flagged row: sales or partner gates, rows below high confidence, and rows whose notes report conflicting or caveated docs.",
                "why": "A bot cannot sit through a demo or pass a partner review, and some judgement calls need a person.",
                "acc": f"Reasons overlap, so they add up to more than the flagged rows:<ul>{nh_li}</ul>",
                "io": (files("pass3/rows.jsonl"), files("needs_human.json")),
                "tiles": [("rows flagged", n(nh["needs_human_count"], f"{P_NEEDS}:needs_human_count"))]}),
            ("spot", "Spot-check", f"{n(spot['rows_checked'], f'{P_SPOT}:result.rows_checked')} rows by hand", {
                "what": f"{n(spot['rows_checked'], f'{P_SPOT}:result.rows_checked')} random final rows outside gold and holdout (fixed seed): a person opened every evidence link and compared it with the claim.",
                "why": "Scores only cover the labelled apps; this checks the rest of the table.",
                "acc": f"{n(spot['rows_correct'], f'{P_SPOT}:result.rows_correct')}/{n(spot['rows_checked'], f'{P_SPOT}:result.rows_checked')} rows and {n(spot['claims_correct'], f'{P_SPOT}:result.claims_correct')}/{n(spot['claims_checked'], f'{P_SPOT}:result.claims_checked')} claims confirmed. <span data-text>{esc(src['spot']['scope_note'])}</span>",
                "io": (files("final rows"), files("spot_check_10.json")),
                "tiles": [("rows right", n(spot["rows_correct"], f"{P_SPOT}:result.rows_correct")),
                          ("claims right", n(spot["claims_correct"], f"{P_SPOT}:result.claims_correct"))]}),
        ],
        "scoring": [
            ("gold", "Gold labels + audit", f"{set_size_n(s, 'gold')} apps, blind", {
                "what": f"{set_size_n(s, 'gold')} apps, {n(max(per_cat.values()), f'{P_GOLD}:max_per(category)')} per category, labelled by hand from official docs without looking at the agent's rows, then audited field by field.",
                "why": "A score is only as good as its answer key.",
                "acc": f"The audit corrected {n(len(corrections), f'{P_AUDIT}:corrected_apps()')} apps' scored labels and clarified the rubric before the labels were locked.",
                "io": (files("official docs"), files("gold_sample.jsonl", "GOLD_AUDIT.md")),
                "tiles": [("apps", set_size_n(s, "gold")), ("labels corrected", n(len(corrections), f"{P_AUDIT}:corrected_apps()"))]}),
            ("holdout", "Sealed holdout", f"{set_size_n(s, 'holdout')} apps, sealed", {
                "what": f"{set_size_n(s, 'holdout')} more apps from a seeded random draw, one per category, labelled and locked before any pass-2 rule was written.",
                "why": "Gold informed some rules, so it flatters the checker; the holdout never did.",
                "acc": f"The honest generalisation number: {rows_ok('holdout', 1)} → {rows_ok('holdout', 2)} → {rows_ok('holdout', 3)} of {set_size_n(s, 'holdout')} whole rows.",
                "io": (files("official docs"), files("holdout_sample.jsonl")),
                "tiles": [("apps", set_size_n(s, "holdout")), ("rows right", rows_ok("holdout", 3))]}),
        ],
    }
    arrows = {"input": "a-right", "pass1": "a-right", "pass2": "a-right", "selector": "a-right", "pass3": "a-down",
              "final": "a-left", "scoring": "a-left", "patterns": ""}

    panels = []
    first = True

    def node_button(key: str, kind: str, ic: str, title: str, metric: str) -> str:
        nonlocal first
        sel = "true" if first else "false"
        first = False
        tag = '<span class="human-tag">human</span>' if kind == "human" else ""
        return (f'<button type="button" class="node node-{kind}" id="nb-{key}" data-panel="sp-{key}" aria-controls="sp-{key}" '
                f'aria-expanded="{sel}"><span class="n-ic">{icon(ic)}</span><span class="n-body">{tag}'
                f'<span class="n-title">{title}</span><span class="n-metric">{metric}</span></span></button>')

    def panel(key: str, kind: str, ic: str, title: str, d: dict, feeds: str = "") -> str:
        tag = '<span class="human-tag">human</span>' if kind == "human" else ""
        feed = f'<span class="sp-feeds">feeds {feeds}</span>' if feeds else ""
        return (f'<section class="sp sp-{kind}" id="sp-{key}" aria-labelledby="sph-{key}">'
                f'<header class="sp-head"><span class="sp-ic">{icon(ic)}</span><div><h4 id="sph-{key}">{tag}{title}</h4>'
                f'<p class="sp-io"><span class="io-k">In</span>{d["io"][0]}<span class="io-arrow" aria-hidden="true">→</span>'
                f'<span class="io-k">Out</span>{d["io"][1]}{feed}</p></div></header>'
                f'<div class="sp-grid"><div><h5>What it does</h5><p>{d["what"]}</p></div>'
                f'<div><h5>Why it exists</h5><p>{d["why"]}</p></div>'
                f'<div class="sp-acc"><h5>How it helps accuracy</h5><div>{d["acc"]}</div></div></div>'
                f'{tiles(*d["tiles"])}</section>')

    items = []
    for key, kind, ic, title, metric, loop, d in nodes:
        loop_html = (f'<p class="loop">{icon("loop", "loop-ic")}<span>{loop}</span></p>' if loop else "")
        att = " has-att" if key in humans else ""
        items.append(f'<div class="nwrap nw-{key} {arrows[key]}{att}">{loop_html}{node_button(key, kind, ic, title, metric)}</div>')
        panels.append(panel(key, kind, ic, title, d))
        if key in humans:
            hb = []
            for hkey, htitle, hmetric, hd in humans[key]:
                hb.append(node_button(hkey, "human", "person", htitle, hmetric))
                panels.append(panel(hkey, "human", "person", htitle, hd, feeds=title))
            items.append(f'<div class="hgroup hg-{key}"><span class="hg-feeds">feeds {title}</span>{"".join(hb)}</div>')
    legend = ('<div class="flow-legend" aria-hidden="true"><p class="fl-t">How to read it</p>'
              '<p><i class="sw sw-stage"></i>Agent stage</p><p><i class="sw sw-human"></i>Human step, feeding the stage above it</p>'
              f'<p>{icon("loop", "loop-ic")}Loop back: a pass rewrites only the fields it checked</p>'
              '<p class="fl-hint">Select any box for what it does, why it exists and its numbers.</p></div>')
    outputs = [Path(v).name for v in src["proof"]["outputs"].values()]
    return f"""
<section id="agent" aria-labelledby="h-agent">
  <h2 id="h-agent">How the agent works</h2>
  <p class="section-lead">A pipeline of loops, not a single run. The agent fills every row, a rule checker and a browser pass re-check it, and people write the answer keys and review what the agent flags. Select a box to see its details.</p>
  <div class="flow2" role="group" aria-label="Pipeline diagram: select a stage for details">{''.join(items)}{legend}</div>
  <div class="stage-panels" aria-live="polite">{''.join(panels)}</div>
  <h3 class="sub-h">Run it with one command</h3>
  <pre class="code"><code>python -m agent all --input apps.csv --run-id my-run</code></pre>
  <p class="code-note">Runs research → rule check → browser check into <code>data/runs/my-run/pass1..3</code>, then writes <code>{'</code>, <code>'.join(esc(o) for o in outputs)}</code>. <code>--dry-run</code> runs offline with fakes.</p>
</section>"""


def md_inline(text: str) -> str:
    """Escape text and turn `code` spans into <code>."""
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", esc(text))


def readme_instant_tools(src: dict) -> str:
    """The tools README's "What Composio unlocks" paragraph says were run through a Composio session."""
    found = re.search(r"^\*\*What Composio unlocks:\*\*\s*(.+?) run through a Composio session(.*)$",
                      src["readme"], flags=re.M)
    ensure(found is not None, "README says which tools ran through a Composio session")
    ensure("No active connection found" in found.group(2), "README reports the 'No active connection' failure")
    return esc(found.group(1).strip())


PASS_MODEL_LOGS = (("Pass 1 · research", "data/runs/pass1/log.jsonl"),
                   ("Pass 2 · rule checker", "data/runs/pass2/review.jsonl"),
                   ("Pass 3 · browser judge", "data/runs/pass3/browse.jsonl"))


def pass_models(root: Path) -> list[tuple[str, str]]:
    """The LLM model id(s) each pass actually logged, read from its run log."""
    out = []
    for label, rel in PASS_MODEL_LOGS:
        path = root / rel
        ids = sorted({r.get("llm_model") for r in read_jsonl(path) if r.get("llm_model")}) if path.is_file() else []
        out.append((label, ", ".join(ids) or "not logged"))
    return out


def browser_use_version(root: Path) -> str:
    req = root / "requirements.txt"
    mt = re.search(r"^browser-use==(\S+)", req.read_text(), re.M) if req.is_file() else None
    return mt.group(1) if mt else ""


def section_tools(src: dict, m: dict) -> str:
    cf = coverage_facts(src)
    root = src.get("root", Path("."))
    bu = browser_use_version(root)

    def card(ic: str, title: str, role: str, body: str) -> str:
        return (f'<article class="cuse cuse-ok"><header class="cu-head"><span class="cu-ic">{icon(ic)}</span>'
                f'<span class="cu-badge">{role}</span></header><h3>{title}</h3>{body}</article>')

    models = "".join(f'<li><span class="tl-k">{esc(k)}</span><code>{esc(v)}</code></li>' for k, v in pass_models(root))
    status = ('<ul class="tl-list">'
              f'<li class="tl-ok">{icon("check", "ch-ic")}<span><strong>SDK catalog</strong> for the coverage check: {cf["covered_n"]} of {cf["total_n"]} apps already covered. Used, worked.</span></li>'
              f'<li class="tl-no">{icon("x", "ch-ic")}<span><strong>Session tool execution</strong> for search and fetch: tried, every call returned "No active connection found".</span></li>'
              f'<li class="tl-ok">{icon("check", "ch-ic")}<span><strong>Composio agent skill</strong> (<code>.agents/skills/composio</code>) used while building the agent.</span></li></ul>')
    return f"""
<section id="tools" aria-labelledby="h-tools">
  <h2 id="h-tools">Tools used</h2>
  <p class="section-lead">What each tool did, read from the repo's code, config and run logs.</p>
  <div class="cuse-grid">
    {card("spark", "AWS Bedrock", "LLM", f'<p>Every LLM call, through the Bedrock Converse API. Model id each pass logged:</p><ul class="tl-models">{models}</ul>')}
    {card("grid", "Exa API", "Search + fetch", '<p>Web search and page text for the research and checker passes, called directly. Free fallback when Exa is unavailable: <code>ddgs</code> search and plain <code>httpx</code> + <code>trafilatura</code> fetch.</p>')}
    {card("browser", "Browser Use", "Browser check", f'<p>Pass 3 opens the cited vendor pages with the open-source <code>browser-use=={esc(bu)}</code> library in a local headless Chromium, and keeps rendered text plus a screenshot. Browser Use Cloud is used only for a page that comes back blocked, behind a captcha or empty.</p>')}
    {card("plug", "Composio", "Coverage + build", status)}
  </div>
</section>"""


def _val(unit: str, v: Any) -> str:
    if unit == "auth":
        primary = AUTH.get(v["primary_auth"], v["primary_auth"])
        rest = [AUTH.get(x, x) for x in v["auth_methods"] if x != v["primary_auth"]]
        return primary + (f" (+ {', '.join(rest)})" if rest else "") if v["primary_auth"] in v["auth_methods"] \
            else ", ".join(AUTH.get(x, x) for x in v["auth_methods"])
    if unit == "api":
        return " + ".join(API.get(x, x) for x in v["api_types"])
    labels = {"access": ACCESS, "mcp_status": MCP, "verdict": VERDICT, "blocker": BLOCKER}.get(unit, {})
    return labels.get(v, str(v))


HUMAN_REASON_CARD = {  # needs_human reason id -> (card title, plain explanation)
    "confidence_not_high": ("Not confident enough", "The agent's confidence was still below high after all three passes."),
    "sales_or_partner_gate": ("Needs vendor outreach", "Access needs a sales demo or a manual partner review. A bot can't "
                              "do that, so a person decides whether to start it."),
    "conflicting_or_caveated_docs": ("Docs conflict or have caveats", "Conflicting docs, a login wall, a deprecation, "
                                     "or a claim the browser pass couldn't confirm."),
}


def section_humans(src: dict, m: dict) -> str:
    needs = src["needs"]
    total = needs["needs_human_count"]
    by_reason = needs["by_reason"]
    reason_sum = sum(by_reason.values())
    ensure(reason_sum > total, "needs-human reasons overlap (they sum to more than the flagged rows)")
    ensure(set(by_reason) <= set(HUMAN_REASON_CARD), "every needs-human reason has a card title")
    reasons = "".join(
        f'<li class="hr-card"><span class="hr-num">{n(v, f"{P_NEEDS}:by_reason.{k}")}</span>'
        f'<div><h4>{esc(HUMAN_REASON_CARD[k][0])}</h4><p>{esc(HUMAN_REASON_CARD[k][1])}</p></div></li>'
        for k, v in sorted(by_reason.items(), key=lambda kv: -kv[1]))
    rows = m["rows"]
    flagged = {s for s, r in m["nh"].items() if r.get("needs_human")}
    by_cat = sorted(((c, sum(1 for r in rows if r["category"] == c and r["slug"] in flagged),
                      sum(1 for r in rows if r["category"] == c)) for c in m["categories"]),
                    key=lambda t: (-t[1], m["categories"].index(t[0])))
    cats = "".join(
        f'<li><span class="hc-name">{esc(m["labels"][c])}</span>'
        f'<span class="hc-bar" aria-hidden="true"><span style="width:{100 * k / max(t, 1):.0f}%"></span></span>'
        f'<span class="hc-num">{n(k, f"{P_PATTERNS}:crosstabs.by_category.{c}.needs_human.count")}</span></li>'
        for c, k, t in by_cat)
    s = m["scores"]
    return f"""
<section id="humans" aria-labelledby="h-humans">
  <h2 id="h-humans">Where a human was needed</h2>
  <p class="section-lead">People wrote the answer keys, and people are the referee whenever the agent is unsure. A flag means <em>a person should look</em>, not <em>the agent failed</em>.</p>

  <div class="h-summary">
    <p class="h-big">{n(total, f'{P_NEEDS}:needs_human_count')}<span class="of"> / {n(needs['total'], f'{P_NEEDS}:total')}</span></p>
    <p class="h-big-text"><strong>rows were flagged for a person to check.</strong> A row can have more than one reason, so the reasons below add up to {n(reason_sum, f'{P_NEEDS}:sum(by_reason)')}.</p>
  </div>

  <h3 class="h-sub">Why rows were flagged</h3>
  <ul class="h-reasons">{reasons}</ul>

  <h3 class="h-sub">The four human steps</h3>
  <ol class="h-steps">
    <li><span class="hs-n" aria-hidden="true"></span><h4>Gold labels and audit</h4><p>{n(s['gold'][3]['summary']['n_gold'], f"{score_path('gold', 3)}:summary.n_gold")} apps labelled blind, then every field re-checked against official docs.</p></li>
    <li><span class="hs-n" aria-hidden="true"></span><h4>Sealed holdout</h4><p>{n(s['holdout'][3]['summary']['n_gold'], f"{score_path('holdout', 3)}:summary.n_gold")} apps labelled and locked before any checker rule existed.</p></li>
    <li><span class="hs-n" aria-hidden="true"></span><h4>Needs-human review</h4><p>{n(total, f'{P_NEEDS}:needs_human_count')} flagged rows listed in <a href="data/needs_human.json">needs_human.json</a> with reasons and evidence links.</p></li>
    <li><span class="hs-n" aria-hidden="true"></span><h4>Spot-check</h4><p>{n(src['spot']['result']['rows_checked'], f'{P_SPOT}:result.rows_checked')} random rows outside gold and holdout. Every deciding claim opened and compared by hand.</p></li>
  </ol>

  <h3 class="h-sub">Flagged rows by category <span class="h-sub-note">out of {cat_size_n(m)} apps each</span></h3>
  <ul class="h-cats">{cats}</ul>
</section>"""


def DOWNLOADS(m: dict) -> list[tuple[str, str]]:  # noqa: N802 - reads as a constant table
    return [("data/final.json", f"all {n(len(m['rows']), rows_src(''))} rows with evidence"),
            ("data/final.csv", "same rows, one line each"),
            ("data/schema.md", "what each field means"),
            ("data/patterns.json", "counts and cross-tabs"),
            ("data/needs_human.json", "rows flagged for a person"),
            ("llms.txt", "entry point for agents")]


def download_payloads(src: dict, out_files: dict[str, str]) -> str:
    """Data the download buttons need when the page is opened alone (no data/ folder).

    final.json / final.csv are rebuilt in the browser from the full rows (also the table's filter data);
    needs_human.json from its minified object; patterns.json (it has floats JS would reformat), schema.md
    and llms.txt are embedded as their exact text. tests/test_build_site.py checks the result byte-for-byte.
    """
    def js(obj: Any) -> str:
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        return text.replace("</", "<\\/").replace("<!--", "<\\u0021--")

    def raw(text: str) -> str:
        ensure("</script" not in text.lower() and "<!--" not in text, "embedded text has no script terminator")
        return text

    rows = sorted(src["rows"], key=lambda r: r["id"])
    parts = [
        ("application/json", "dl-rows", js(rows)),
        ("application/json", "dl-cols", js(schema_columns())),
        ("application/json", "dl-needs", js(src["needs"])),
        ("text/plain", "dl-patterns", raw(out_files["data/patterns.json"])),
        ("text/plain", "dl-schema", raw(out_files["data/schema.md"])),
        ("text/plain", "dl-llms", raw(out_files["llms.txt"])),
    ]
    return "".join(f'<script type="{t}" id="{i}">{body}</script>' for t, i, body in parts)


def section_proof(src: dict, m: dict) -> str:
    return f"""
<section id="proof" aria-labelledby="h-proof">
  <h2 id="h-proof">Proof: run it yourself</h2>
  <div class="panel">
    <h3>One command</h3>
    <pre class="code"><code>git clone {esc(REPO_URL)}
cd composio-app-research
python -m venv .venv &amp;&amp; .venv/bin/pip install -r requirements.txt
.venv/bin/python -m agent all --dry-run --limit 2 --run-id try   # offline, no keys
.venv/bin/python -m agent all --input apps.csv --run-id my-run   # live</code></pre>
    <p class="small">Source and README: {link(REPO_URL, REPO_URL.removeprefix('https://'))}</p>
    <p class="small agents-note"><strong>For agents:</strong> read <a href="llms.txt">llms.txt</a>, then <a href="data/final.json">data/final.json</a>; field meanings are in <a href="data/schema.md">data/schema.md</a>.</p>
  </div>
  <div class="downloads">
    <h3>Downloads</h3>
    <ul class="dl-list">{''.join(
        f'<li><a class="dl-card" href="{href}" download="{Path(href).name}" data-dl="{Path(href).name}">'
        f'<span class="dl-ic">{icon("file")}</span><span class="dl-name">{Path(href).name}</span>'
        f'<span class="dl-desc">{desc}</span>{icon("download", "dl-go")}</a></li>'
        for href, desc in DOWNLOADS(m))}</ul>
    <p class="dl-note">The files also sit in <code>data/</code> next to this page; the buttons work even when only this page is opened.</p>
  </div>
</section>"""


def section_limits(src: dict, m: dict) -> str:
    s = m["scores"]
    g3, h3 = s["gold"][3], s["holdout"][3]
    h = [s["holdout"][p]["summary"]["n_row_correct"] for p in PASSES]
    ensure(len(set(h)) == 1, "holdout stayed flat")
    ensure(field_acc(h3, "auth") < WEAK_BELOW, "auth is weak on the holdout")
    ensure(min(field_acc(g3, "requires_end_user_admin"), field_acc(h3, "requires_end_user_admin")) < WEAK_BELOW,
           "requires_end_user_admin is weak")
    ensure(min(field_acc(g3, "api_breadth"), field_acc(h3, "api_breadth")) < WEAK_BELOW, "api_breadth is weak")
    items = [
        "Pass 3 only reads pages: it does not click, navigate, follow links, open tabs or expand sections, so a fact one click away is missed.",
        f"Auth is the weakest critical field on the holdout ({acc_n(s, 'holdout', 3, 'auth')}); which method is "
        "primary is a judgement call the rules do not settle.",
        f"Needs-customer-admin scored {acc_n(s, 'gold', 3, 'requires_end_user_admin')} on gold and "
        f"{acc_n(s, 'holdout', 3, 'requires_end_user_admin')} on holdout; API breadth "
        f"{acc_n(s, 'gold', 3, 'api_breadth')} on gold. Treat both as rough.",
        f"The answer keys are small: {set_size_n(s, 'gold')} gold and {set_size_n(s, 'holdout')} holdout apps.",
        f"Gold informed some checker rules; the holdout is the cleaner test and stayed at "
        f"{rows_ok_n(s, 'holdout', 3)} of {set_size_n(s, 'holdout')} whole rows.",
        "Evidence scoring checks that each claim has a URL; whether the page truly supports the claim was checked by people on samples only.",
        "Composio Instant tool execution was never connected for this project, so it did no research work.",
        f"A snapshot: vendor docs change, and this data is as of {as_of_span(m)}.",
    ]
    return f"""
<section id="limits" aria-labelledby="h-limits">
  <h2 id="h-limits">Limits</h2>
  <ul class="limits">{''.join(f'<li>{i}</li>' for i in items)}</ul>
</section>"""


def section_links(src: dict, m: dict) -> str:
    repo_path = REPO_URL.removeprefix("https://")
    resume = src.get("resume")
    b64 = src.get("resume_b64")
    dl = (' download="resume.pdf" data-resume' if b64 else ' target="_blank" rel="noopener"')
    resume_btn = (f'<a class="btn btn-big btn-second" href="{esc(resume)}"{dl}>'
                  f'{icon("file", "btn-ic")}<span class="b-text"><span class="b-title">Resume</span>'
                  f'<span class="b-sub">PDF download</span></span></a>') if resume else ""
    if b64:
        resume_btn += f'<script type="application/pdf;base64" id="dl-resume">{b64}</script>'
    return f"""
<section id="links" class="links" aria-labelledby="h-links">
  <h2 id="h-links">Links</h2>
  <p class="section-lead">The code, the data and the full history behind this page.</p>
  <div class="link-row">
    <a class="btn btn-big btn-primary" href="{esc(REPO_URL)}" target="_blank" rel="noopener">{GITHUB_SVG}<span class="b-text"><span class="b-title">{esc(REPO_LABEL)}</span><span class="b-sub">{esc(repo_path)}</span></span>{icon("ext", "b-ext")}</a>
    {resume_btn}
  </div>
</section>"""


NAV = (("patterns", "Patterns"), ("accuracy", "Accuracy"), ("next", "Next steps"), ("findings", "Findings"),
       ("agent", "Agent"), ("tools", "Tools"), ("humans", "Humans"),
       ("proof", "Proof"), ("limits", "Limits"), ("links", "Links"))


def render_page(src: dict, out: Path) -> str:
    m = build_model(src)
    nav = "".join(f'<li><a href="#{k}">{t}</a></li>' for k, t in NAV)
    body = "".join(fn(src, m) for fn in (section_hero, section_accuracy, section_next, section_findings, section_agent,
                                         section_tools, section_humans, section_proof,
                                         section_limits, section_links))
    side = {rel: (out / rel).read_text(encoding="utf-8") for rel in ("data/patterns.json", "data/schema.md", "llms.txt")}
    description = ("Research on 100 apps for Composio: auth, access, API, MCP and buildability, with the agent "
                   "pipeline, verification and evidence.")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(TITLE.format(n=m["total"]))}</title>
<meta name="description" content="{esc(description)}">
<link rel="alternate" type="text/plain" href="llms.txt" title="llms.txt">
<script>document.documentElement.classList.add('js')</script>
<style>{CSS}</style>
</head>
<body>
<a class="skip" href="#findings">Skip to the findings table</a>
<header class="top">
  <div class="top-in">
    <a class="brand" href="#patterns">{n(m["total"], rows_src(""))} apps → agent toolkits</a>
    <nav aria-label="Sections"><ul>{nav}</ul></nav>
    <a class="top-gh" href="{esc(REPO_URL)}" target="_blank" rel="noopener" aria-label="{esc(REPO_LABEL)}" title="{esc(REPO_LABEL)}">{GITHUB_SVG}</a>
  </div>
</header>
<main>{body}</main>
<footer class="foot">
  <p>Data as of {as_of_span(m)} · {link(REPO_URL, 'Source repo')} · Generated by <code>scripts/build_site.py</code> · <a href="llms.txt">llms.txt</a></p>
</footer>
{download_payloads(src, side)}
<script>{JS}</script>
</body>
</html>
"""


# ---------------------------------------------------------------- side files


def schema_md(src: dict) -> str:
    schema = src["schema"]
    readme = src["schema_readme"]
    bullets = dict(re.findall(r"^- \*\*([a-z_]+)\*\* — (.+)$", readme, flags=re.M))

    def first_sentence(text: str) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        m = re.match(r"(.+?[.!?])(\s|$)", text)
        return m.group(1) if m else text

    def type_of(spec: dict) -> str:
        if "enum" in spec:
            return " / ".join(f"`{v}`" for v in spec["enum"])
        if spec.get("type") == "array" and "enum" in spec.get("items", {}):
            return "list of " + " / ".join(f"`{v}`" for v in spec["items"]["enum"])
        t = spec.get("type")
        return " or ".join(t) if isinstance(t, list) else str(t)

    lines = [
        "# Row schema (short form)",
        "",
        f"Generated by `scripts/build_site.py` from `{P_SCHEMA}` and `{P_SCHEMA_README}`. "
        f"Full definitions and scoring rules live in the repo: {REPO_URL}/tree/main/schema",
        "",
        "## How to read a row",
        "",
        "Each object in `final.json` (one line in `final.csv`) is one app. `verdict` answers \"could an agent toolkit "
        "be built today?\" and `blocker` names the single main reason when it cannot. `access` says how a developer "
        "gets credentials from the vendor; `gated_sales` / `gated_partner` with evidence is a correct finding, not a "
        "failure. `auth_methods` lists only auth for the product's own API (not MCP-only OAuth or dashboard login), "
        "and `primary_auth` is the one a multi-tenant toolkit would build first. Every claim in `evidence` cites a "
        "page the agent actually fetched; the quoted snippets are in the repo under `data/runs/pass3/evidence/`. "
        "`composio_toolkit` is a catalog lookup, never researched. `confidence` and `notes` carry the caveats.",
        "",
        "## Fields",
        "",
        "| Field | Type / values | Meaning |",
        "|---|---|---|",
    ]
    for field, spec in schema["properties"].items():
        meaning = spec.get("description") or bullets.get(field) or ""
        if field == "evidence":
            meaning = ("Per claim (`auth`, `access`, `api_surface`, `mcp`, `verdict`; optional `one_liner`): a list of "
                       "`{url, source_type, note}`.")
        elif field == "composio_toolkit":
            meaning = ("Catalog lookup: `status` (`exists` / `none` / `unknown`), `toolkit_slug`, `sibling_slugs`, "
                       "`checked_at`, `evidence_url`.")
        typ = "object" if field in ("evidence", "composio_toolkit") else type_of(spec)
        lines.append(f"| `{field}` | {typ} | {first_sentence(meaning).replace('|', '/')} |")
    for heading in ("access", "verdict", "blocker", "mcp_status"):
        meanings = enum_meanings(readme, heading)
        if not meanings:
            continue
        lines += ["", f"## `{heading}` values", "", "| Value | Meaning |", "|---|---|"]
        lines += [f"| `{k}` | {first_sentence(v).replace('|', '/')} |" for k, v in meanings.items()]
    return "\n".join(lines) + "\n"


def llms_txt(src: dict) -> str:
    m = build_model(src)
    rows, total = m["rows"], m["total"]
    g = [m["scores"]["gold"][p]["summary"]["n_row_correct"] for p in PASSES]
    h = [m["scores"]["holdout"][p]["summary"]["n_row_correct"] for p in PASSES]
    ng = m["scores"]["gold"][3]["summary"]["n_gold"]
    nho = m["scores"]["holdout"][3]["summary"]["n_gold"]
    a = m["auth"]
    out = m["outreach"]
    spot = src["spot"]["result"]
    return f"""# {TITLE.format(n=total)}

> Case study: an agent pipeline researched {total} apps for Composio (auth, access, API surface, MCP, Composio coverage, buildability verdict, evidence per claim), re-checked itself with a rule checker and a Browser Use pass, and was scored against hand-labelled gold ({ng} apps) and sealed holdout ({nho} apps) sets.

Headline numbers (generated from the repo data, as of {m['as_of']}):

- Buildable now: {sum(r['verdict'] == 'buildable_now' for r in rows)}/{total}; need vendor approval: {sum(r['verdict'] == 'buildable_with_approval' for r in rows)} ({len(out['gated_sales'])} sales-gated, {len(out['gated_partner'])} partner-gated)
- OAuth2 is the primary auth for {a['oauth2_primary']}/{total}; {a['oauth2_and_api_key']} offer both OAuth2 and an API key; {a['api_key_only']} are API-key only
- Official MCP server: {sum(r['mcp_status'] == 'official' for r in rows)}/{total}
- Already on Composio: {sum(composio_status(r) == 'exists' for r in rows)}/{total}; easy wins (buildable now + official MCP + not on Composio): {len(m['easy'])}
- Flagged for a human check: {src['needs']['needs_human_count']}/{src['needs']['total']}
- Whole rows fully right, pass 1 -> 2 -> 3: gold {g[0]} -> {g[1]} -> {g[2]} of {ng}; holdout {h[0]} -> {h[1]} -> {h[2]} of {nho}
- Human spot-check: {spot['rows_correct']}/{spot['rows_checked']} rows, {spot['claims_correct']}/{spot['claims_checked']} claims

## Page

- [Case study page](index.html): patterns, accuracy, next steps, the findings table, how the agent works, human checks and limits

## Data

- [data/final.json](data/final.json): all {total} final rows (JSON array, sorted by id)
- [data/final.csv](data/final.csv): the same rows as CSV (`;`-joined lists, JSON for evidence and composio_toolkit)
- [data/schema.md](data/schema.md): field names, allowed values and meanings
- [data/patterns.json](data/patterns.json): distributions, cross-tabs and headline patterns with accuracy caveats
- [data/needs_human.json](data/needs_human.json): rows flagged for a human check, with reasons and evidence URLs

## Source

- [Repository]({REPO_URL}): research agent, rule checker, browser pass, scorer and this page's generator (`scripts/build_site.py`)
"""


def resume_href(out: Path) -> str | None:
    """RESUME_URL if it can be linked: an absolute URL, or a relative file that exists in the output folder."""
    url = os.environ.get("RESUME_URL", RESUME_URL).strip()
    if not url:
        print("build_site: warning: RESUME_URL is empty; Resume button hidden", file=sys.stderr)
        return None
    if not re.match(r"^https?://", url) and not (out / url).is_file():
        print(f"build_site: warning: {url} is missing from the site folder; Resume button hidden", file=sys.stderr)
        return None
    return url


def write_site(root: Path, out: Path) -> None:
    src = load_sources(root)
    out.mkdir(parents=True, exist_ok=True)
    # Remove only generated output; static assets placed in site/ by hand (STATIC_ASSETS) are kept.
    for name in GENERATED:
        target = out / name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    (out / "data").mkdir()
    src["resume"] = resume_href(out)
    # Embed a local resume PDF so its button downloads even when index.html is opened on its own.
    if src["resume"] and not re.match(r"^https?://", src["resume"]):
        src["resume_b64"] = base64.b64encode((out / src["resume"]).read_bytes()).decode("ascii")
    files = {
        "data/schema.md": schema_md(src),
        "llms.txt": llms_txt(src),
    }
    export_final(src["rows"], out / "data")
    shutil.copyfile(root / P_NEEDS, out / "data" / "needs_human.json")
    shutil.copyfile(root / P_PATTERNS, out / "data" / "patterns.json")
    for rel, text in files.items():
        (out / rel).write_text(text, encoding="utf-8")
    (out / "index.html").write_text(render_page(src, out), encoding="utf-8")
    generated = [p for p in out.rglob("*") if p.is_file() and p.relative_to(out).as_posix() not in STATIC_ASSETS]
    leaks = [p for p in generated if re.search(r"/home/|/tmp/|/Users/", p.read_text(encoding="utf-8"))]
    if leaks:
        raise BuildError(f"build_site: absolute machine paths leaked into {', '.join(map(str, leaks))}")


def diff_dirs(a: Path, b: Path) -> list[str]:
    files_a = {p.relative_to(a).as_posix() for p in a.rglob("*") if p.is_file()}
    files_b = {p.relative_to(b).as_posix() for p in b.rglob("*") if p.is_file()}
    out = sorted(files_a ^ files_b)
    out += sorted(f for f in files_a & files_b if not filecmp.cmp(a / f, b / f, shallow=False))
    return out


def check(root: Path, site: Path) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp) / "site"
        fresh.mkdir()
        for name in STATIC_ASSETS:  # static assets are not generated; give the fresh build the same ones
            if (site / name).is_file():
                shutil.copyfile(site / name, fresh / name)
        write_site(root, fresh)
        if not site.is_dir():
            print(f"build_site --check: {site} does not exist; run scripts/build_site.py")
            return 1
        stale = diff_dirs(fresh, site)
    if stale:
        print("build_site --check: site/ is stale; rerun scripts/build_site.py. Differs: " + ", ".join(stale))
        return 1
    print("build_site --check: site/ is up to date")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="rebuild into a temp dir and fail if site/ differs")
    ap.add_argument("--out", type=Path, default=SITE_DIR, help="output folder (default site/)")
    args = ap.parse_args(argv)
    if args.check:
        return check(ROOT, args.out)
    write_site(ROOT, args.out)
    print(f"build_site: wrote {args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out.name}/")
    return 0


CSS = r"""
:root{--bg:#fff;--fg:#0f172a;--muted:#475569;--subtle:#64748b;--line:#e2e8f0;--line2:#cbd5e1;--soft:#f8fafc;
--accent:#4f46e5;--accent-d:#4338ca;--accent-soft:#eef2ff;--human:#a21caf;--human-soft:#fdf4ff;--good:#15803d;
--good-soft:#f0fdf4;--warn:#b45309;--warn-soft:#fffbeb;--bad:#b91c1c;--bad-soft:#fef2f2;--r:10px;
--shadow:0 1px 2px rgba(15,23,42,.05),0 1px 3px rgba(15,23,42,.06);
--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace}
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:72px;-webkit-text-size-adjust:100%}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}*{transition:none!important;animation:none!important}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 var(--sans);overflow-x:hidden}
a{color:var(--accent-d);text-decoration:underline;text-decoration-color:rgba(67,56,202,.35);text-underline-offset:2px}
a:hover{text-decoration-color:currentColor}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}
code{font:0.86em var(--mono);background:var(--soft);border:1px solid var(--line);border-radius:4px;padding:0 4px;
overflow-wrap:anywhere}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);border:0}
.skip{position:absolute;left:-999px;top:8px;background:var(--fg);color:#fff;padding:8px 12px;border-radius:6px;z-index:50}
.skip:focus{left:8px}
.muted{color:var(--subtle)}.small{font-size:.875rem}
.top{position:sticky;top:0;z-index:20;background:rgba(255,255,255,.92);backdrop-filter:saturate(180%) blur(8px);
border-bottom:1px solid var(--line)}
.top-in{max-width:1160px;margin:0 auto;padding:0 24px;display:flex;align-items:center;gap:24px;height:56px}
.brand{font-weight:650;color:var(--fg);text-decoration:none;white-space:nowrap;font-size:.95rem}
.top nav{flex:1;min-width:0;overflow-x:auto;scrollbar-width:none}
.top nav::-webkit-scrollbar{display:none}
.top nav ul{display:flex;gap:2px;list-style:none;margin:0;padding:0;justify-content:flex-end}
.top nav a{display:block;padding:6px 10px;border-radius:6px;color:var(--muted);text-decoration:none;font-size:.875rem;
white-space:nowrap}
.top nav a:hover{color:var(--fg);background:var(--soft)}
.top nav a.active{color:var(--accent-d);background:var(--accent-soft);font-weight:600}
main{max-width:1160px;margin:0 auto;padding:0 24px}
section{padding:56px 0 8px;border-top:1px solid var(--line)}
section.hero{border-top:0;padding-top:48px}
h1,h2,h3,h4,h5{line-height:1.25;margin:0 0 8px;letter-spacing:-.01em}
h1{font-size:2.5rem;font-weight:750;letter-spacing:-.025em}
h2{font-size:1.6rem;font-weight:700}
h3{font-size:1.1rem;font-weight:650}
h4{font-size:1rem;font-weight:650;margin-top:16px}
h5{font-size:.8rem;text-transform:uppercase;letter-spacing:.06em;color:var(--subtle);margin:0 0 4px}
p{margin:0 0 12px}
.eyebrow{font-size:.8rem;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--accent);margin-bottom:12px}
.subtitle{font-size:1.2rem;color:var(--muted);max-width:60ch;margin-bottom:16px}
.lead{font-size:1.02rem;max-width:75ch;color:var(--fg)}
.kicker{font-size:.8rem;text-transform:uppercase;letter-spacing:.08em;color:var(--subtle);margin:32px 0 12px;font-weight:600}
.section-lead{color:var(--muted);max-width:80ch;margin-bottom:24px}
.more{margin-top:8px;font-weight:550}
.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
.card-wide{grid-column:span 2}
.card-wide .cat-bars{grid-template-columns:1fr 1fr;grid-template-rows:repeat(5,auto);grid-auto-flow:column;column-gap:32px}
.stack{display:flex;height:12px;border-radius:4px;overflow:hidden;margin:2px 0 6px;background:var(--soft)}
.stack .seg{display:block;height:100%}
.stack .seg+.seg{border-left:2px solid #fff}
.seg-oauth2{background:var(--accent)}.seg-api_key{background:#93c5fd}.seg-none{background:var(--line2)}
.seg-basic,.seg-jwt_service_account,.seg-custom_other,.seg-unknown{background:#cbd5e1}
.stack-key{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:.8rem;color:var(--muted);margin-bottom:12px}
.stack-key i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;vertical-align:0}
.callouts{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:4px 0 12px}
.callout{margin:0;border-radius:8px;padding:10px 14px;font-size:.9rem;font-weight:600;line-height:1.4}
.callout .co-k{display:block;font-size:.7rem;text-transform:uppercase;letter-spacing:.06em;font-weight:700;margin-bottom:2px}
.callout .co-n{display:block;font-weight:400;color:var(--muted);font-size:.84rem}
.c-self{background:var(--good-soft);border:1px solid #bbf7d0}.c-self .co-k{color:var(--good)}
.c-gated{background:var(--warn-soft);border:1px solid #fde68a}.c-gated .co-k{color:var(--warn)}
.chip.bad{background:var(--bad-soft);border-color:#fecaca;color:var(--bad)}
.panel-flat{margin-top:20px;box-shadow:none}
.agents-note{margin-top:12px;padding-top:12px;border-top:1px dashed var(--line);margin-bottom:0}
.card{border:1px solid var(--line);border-radius:var(--r);padding:20px 22px;box-shadow:var(--shadow);background:#fff;
display:flex;flex-direction:column}
.card .q{font-size:1.02rem;margin-bottom:12px}
.big{font-size:2.4rem;font-weight:750;line-height:1;letter-spacing:-.02em;margin:0 0 4px;font-variant-numeric:tabular-nums}
.big .of,.stats .of{font-size:.5em;color:var(--subtle);font-weight:600;margin-left:2px}
.big.good{color:var(--good)}.big.warn{color:var(--warn)}
.big-label{color:var(--muted);font-size:.92rem;margin-bottom:12px}
.facts{margin:0 0 12px;padding-left:18px;font-size:.92rem}
.facts li{margin:2px 0}
.facts span[data-src]{font-weight:650}
.eg{font-size:.875rem;color:var(--muted)}
.tag-line{margin-top:auto;padding-top:12px;border-top:1px dashed var(--line);font-size:.84rem;color:var(--muted);margin-bottom:0}
.tag{display:inline-block;font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;
border-radius:999px;padding:2px 8px;margin-right:4px;vertical-align:1px}
.tag-strong{background:var(--good-soft);color:var(--good);border:1px solid #bbf7d0}
.tag-moderate{background:var(--warn-soft);color:var(--warn);border:1px solid #fde68a}
.tag-rough{background:var(--bad-soft);color:var(--bad);border:1px solid #fecaca}
.split{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.legend-mini{font-size:.8rem;color:var(--muted);margin-bottom:8px}
.key{display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 4px 0 8px;vertical-align:-1px}
.key:first-child{margin-left:0}
.key-self{background:#16a34a}.key-gated{background:#f59e0b}
.cat-bars{list-style:none;margin:0 0 12px;padding:0;display:grid;gap:5px}
.cat-bars li{display:grid;grid-template-columns:minmax(0,15em) 1fr 3.2em;gap:10px;align-items:center;font-size:.82rem}
.cb-label{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--fg)}
.cb-bar{display:flex;height:10px;background:var(--soft);border-radius:3px;overflow:hidden;border:1px solid var(--line)}
.cb-self{background:#16a34a}.cb-gated{background:#f59e0b}
.cb-num{text-align:right;font-variant-numeric:tabular-nums;color:var(--muted);white-space:nowrap}
.cb-num .sep{margin:0 2px;color:var(--line2)}
.cb-num span[data-src]:first-child{color:var(--good);font-weight:650}
.cb-num .gated-n{color:var(--warn);font-weight:650}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:0;margin:24px 0 0;border:1px solid var(--line);
border-radius:var(--r);overflow:hidden;background:var(--soft)}
.stats>div{padding:18px 20px;border-left:1px solid var(--line)}
.stats>div:first-child{border-left:0}
.stats dt{font-size:.84rem;color:var(--muted);order:2}
.stats dd{margin:0 0 2px;font-size:2rem;font-weight:750;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.stats>div{display:flex;flex-direction:column}
.stats dd{order:1}
.human-n{color:var(--human)}
.human-tag{display:inline-block;font-size:.66rem;font-weight:750;text-transform:uppercase;letter-spacing:.08em;
color:#fff;background:var(--human);border-radius:4px;padding:1px 6px;margin-right:6px;vertical-align:1px}
.acc-layout{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:24px;align-items:start}
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
.acc-table{border-collapse:collapse;width:100%;font-size:.92rem;font-variant-numeric:tabular-nums}
.acc-table th,.acc-table td{padding:7px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
.acc-table thead th{font-size:.78rem;color:var(--subtle);font-weight:600}
.acc-table thead tr:first-child th{color:var(--fg);font-size:.84rem}
.acc-table tbody th{font-weight:600}
.set-start{border-left:1px solid var(--line)}
td.acc{position:relative;min-width:76px}
td.acc .bar{display:block;height:3px;border-radius:2px;background:linear-gradient(90deg,var(--accent) var(--w),var(--line) var(--w));
margin-bottom:4px;max-width:64px}
td.acc.mid span[data-src]{color:var(--warn);font-weight:650}
td.acc.weak span[data-src]{color:var(--bad);font-weight:700}
td.acc.mid .bar{background:linear-gradient(90deg,#f59e0b var(--w),var(--line) var(--w))}
td.acc.weak .bar{background:linear-gradient(90deg,#dc2626 var(--w),var(--line) var(--w))}
.up,.down{font-size:.62rem;margin-left:4px;vertical-align:1px}
.acc-table .ps{display:none}
@media (max-width:760px){.acc-table .pl{display:none}.acc-table .ps{display:inline}}
.up{color:var(--good)}.down{color:var(--bad)}
.acc-side{display:grid;gap:12px}
.whole{border:1px solid var(--line);border-radius:var(--r);padding:14px 16px;background:var(--soft);font-size:.92rem;
font-variant-numeric:tabular-nums;margin:0;line-height:1.7}
.badge-spot{border:1px solid #f0abfc;background:var(--human-soft);border-radius:var(--r);padding:14px 16px;margin:0;
font-size:.92rem;font-weight:600}
.badge-spot .scope{display:block;font-weight:400;color:var(--muted);font-size:.82rem;margin-top:6px}
.takeaways{margin:24px 0 0;padding:0;list-style:none;display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
.takeaways li{background:var(--soft);border:1px solid var(--line);border-radius:10px;padding:14px 16px;font-size:.93rem;color:var(--muted)}
.takeaways strong{color:var(--fg);display:block;margin-bottom:2px}
.two-col{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px;align-items:start}
.two-col>*,.cards>*,.use-grid>*,.fixes>*,.acc-layout>*{min-width:0}
.panel{border:1px solid var(--line);border-radius:var(--r);padding:20px 22px;background:#fff;box-shadow:var(--shadow)}

.panel h3 .count,h4 .count{display:inline-block;min-width:1.8em;text-align:center;font-size:.8rem;background:var(--soft);
border:1px solid var(--line);border-radius:999px;padding:1px 8px;margin-left:6px;vertical-align:2px;color:var(--muted)}
.panel h4:first-child{margin-top:0}
.group-note{font-size:.875rem;color:var(--muted)}
.item-list{list-style:none;margin:0 0 8px;padding:0}
.item-list li{padding:9px 0;border-top:1px solid var(--line)}
.item-list li:first-child{border-top:0}
.item-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.item-name{font-weight:650}
.item-meta{font-size:.84rem;color:var(--muted);margin-top:2px}
.warn-note{display:block;font-size:.8rem;color:var(--warn);background:var(--warn-soft);border-radius:6px;padding:3px 8px;margin-top:6px}
.chip{display:inline-block;font-size:.72rem;font-weight:600;padding:1px 7px;border-radius:999px;background:var(--soft);
border:1px solid var(--line);color:var(--muted);white-space:nowrap}
.chip.ok{background:var(--good-soft);border-color:#bbf7d0;color:var(--good)}
.chip.hl{background:var(--accent-soft);border-color:#c7d2fe;color:var(--accent-d)}
.toolbar{display:grid;gap:12px;margin-bottom:12px}
.count-line{margin:0;font-size:.9rem;color:var(--muted)}
.findings-wrap{--bleed:clamp(0px,calc((100vw - 1176px) / 2),120px);border:1px solid var(--line);border-radius:var(--r);
margin:0 calc(-1 * var(--bleed))}
.findings{border-collapse:separate;border-spacing:0;width:100%;font-size:.84rem;min-width:1060px}
.findings thead th{position:sticky;top:0;background:var(--soft);z-index:2;text-align:left;font-size:.7rem;
text-transform:uppercase;letter-spacing:.04em;color:var(--subtle);padding:10px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
.findings td,.findings tbody th{padding:9px 8px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left;font-weight:400}
.findings tr.row td a{white-space:nowrap}
.findings td .sub{max-width:120px}
.findings tr.row:hover td,.findings tr.row:hover th{background:#fafbff}
.findings .c-app{position:sticky;left:0;background:#fff;z-index:1;min-width:150px;max-width:170px;border-right:1px solid var(--line);
padding-left:12px}
.findings thead .c-app{z-index:3;background:var(--soft)}
.findings .c-cat{color:var(--muted)}
.findings .c-one{white-space:normal;min-width:190px;max-width:220px;color:var(--muted)}
.clamp{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.findings .sub{display:block;font-size:.76rem;color:var(--subtle)}
.pill{display:inline-block;font-size:.76rem;font-weight:600;border-radius:999px;padding:2px 8px;text-decoration:none;white-space:nowrap}
.v-now{background:var(--good-soft);color:var(--good);border:1px solid #bbf7d0}
.v-appr{background:var(--warn-soft);color:var(--warn);border:1px solid #fde68a}
.v-no{background:var(--bad-soft);color:var(--bad);border:1px solid #fecaca}
.findings td a:not(.pill){color:var(--fg);text-decoration-color:var(--line2);text-decoration-thickness:1px}
.findings td a:not(.pill):hover{color:var(--accent-d);text-decoration-color:currentColor}
.findings td a.a-gated{color:var(--warn);font-weight:600;text-decoration-color:#fcd34d}
.findings td a.mcp-official{font-weight:600}
.findings td a.mcp-community,.findings td a.mcp-none{color:var(--subtle)}
.findings .detail a{color:var(--accent-d)}
.flag{display:inline-block;font-size:.72rem;font-weight:700;color:var(--human);background:var(--human-soft);border:1px solid #f0abfc;
border-radius:999px;padding:1px 8px;cursor:help;text-transform:uppercase;letter-spacing:.04em}
.conf{font-size:.78rem}.conf-high{color:var(--good)}.conf-medium{color:var(--warn);font-weight:600}.conf-low{color:var(--bad);font-weight:700}
.js tr.detail{display:none}
.js tr.detail.open{display:table-row}
.findings tr[hidden]{display:none!important}
tr.detail>td{background:var(--soft);padding:0;border-bottom:1px solid var(--line2)}
.detail-inner{position:sticky;left:0;max-width:min(1350px,calc(100vw - 48px));padding:16px 18px 18px}
.detail-grid{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,7fr);gap:24px;font-size:.86rem}
.detail-grid p{margin-bottom:8px}
.d-one{font-size:.95rem;color:var(--fg)}
.d-human{background:var(--human-soft);border:1px solid #f5d0fe;border-radius:8px;padding:10px 12px}
.d-human ul{margin:4px 0 0;padding-left:16px}
.evidence{display:grid;gap:10px}
.ev-claim ul{list-style:none;margin:0;padding:0}
.ev-claim li{margin-bottom:6px}
.ev-url{overflow-wrap:anywhere;font-size:.82rem}
.ev-note{display:block;color:var(--muted)}
blockquote{margin:4px 0 6px;padding:8px 12px;border:1px solid var(--line);color:var(--fg);background:#fff;border-radius:8px;
font-size:.84rem}
blockquote cite{display:block;font-style:normal;font-size:.76rem;color:var(--subtle);overflow-wrap:anywhere}
.empty{padding:24px;text-align:center;color:var(--muted)}
.sub-h{margin-top:24px}
pre.code{background:#0f172a;color:#e2e8f0;border-radius:var(--r);padding:14px 16px;overflow-x:auto;font:0.86rem/1.6 var(--mono);margin:0 0 8px}
pre.code code{background:none;border:0;padding:0;color:inherit;overflow-wrap:normal;white-space:pre}
.code-note{font-size:.9rem;color:var(--muted)}
details{border:1px solid var(--line);border-radius:var(--r);background:#fff}
details>summary{cursor:pointer;padding:12px 16px;font-weight:600;list-style:none;display:block}
details>summary::-webkit-details-marker{display:none}
details>summary::before{content:"";display:inline-block;width:0;height:0;border-left:6px solid var(--subtle);
border-top:5px solid transparent;border-bottom:5px solid transparent;transition:transform .15s;margin-right:10px;
vertical-align:1px}
details[open]>summary::before{transform:rotate(90deg)}
details>div{padding:0 16px 14px;font-size:.9rem;color:var(--muted)}
details>div ul{padding-left:18px;margin:6px 0 0}
details.v{margin-bottom:12px}
details.v>summary{font-size:1.02rem;padding:14px 18px}
.v-body{padding:0 18px 18px!important;color:var(--fg)!important;font-size:.92rem!important}
.v-body.two-col{display:grid}
.v-body .full{grid-column:1/-1}
.mini{border-collapse:collapse;width:100%;font-size:.86rem;font-variant-numeric:tabular-nums}
.mini th,.mini td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left}
.mini thead th{font-size:.74rem;text-transform:uppercase;letter-spacing:.04em;color:var(--subtle)}
.mini tbody th{font-weight:550}
.mini tr.total th,.mini tr.total td{font-weight:700;border-top:2px solid var(--line2)}
.mini td.acc{min-width:0}
.mini .num,.rules .num{text-align:right}
.misses{border-collapse:collapse;width:100%;font-size:.86rem;margin:8px 0 16px}
.misses th,.misses td{padding:10px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left}
.misses thead th{font-size:.74rem;text-transform:uppercase;letter-spacing:.04em;color:var(--subtle)}
.misses tbody th{white-space:nowrap;font-weight:650}
.miss-list{list-style:none;margin:0;padding:0}
.miss-list li{margin-bottom:6px}
.why{display:block;color:var(--muted);font-size:.82rem}
.val-bad{color:var(--bad);font-weight:600}.val-good{color:var(--good);font-weight:600}
.label-note{color:var(--muted);font-size:.82rem;max-width:420px}
.fixes{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.fix{border:1px solid var(--line);border-radius:var(--r);padding:14px 16px;background:var(--soft)}
.fix h5{font-size:.95rem;text-transform:none;letter-spacing:0;color:var(--fg)}
.fix ul{padding-left:18px;margin:4px 0 8px}
.fix .changes{list-style:none;padding:0}
.fix .changes code{font-size:.8em}
.fix .changes span{overflow-wrap:anywhere}
.pages li{overflow-wrap:anywhere;font-size:.84rem}
.scope-note{background:var(--human-soft);border:1px solid #f5d0fe;padding:10px 14px;border-radius:8px}
.spot{list-style:none;padding:0;margin:8px 0 0;display:grid;grid-template-columns:repeat(2,1fr);gap:6px 20px}
.spot li{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;padding:6px 0;border-bottom:1px solid var(--line)}
.spot .chip{margin-left:auto}
.plain-list{padding-left:18px;margin:0;max-width:85ch}
.plain-list li{margin-bottom:10px}
.hbars{list-style:none;padding:0;margin:8px 0 16px;display:grid;gap:8px}
.hbars li{display:grid;grid-template-columns:minmax(0,1fr) 120px 2.2em;gap:10px;align-items:center;font-size:.86rem}
.hbars.cat{grid-template-columns:1fr 1fr;grid-template-rows:repeat(5,auto);grid-auto-flow:column;column-gap:40px}
.hbars.cat li{grid-template-columns:minmax(0,1fr) 180px 2.2em}
.hb-label code{display:block;width:max-content;margin-top:2px;color:var(--subtle)}
.hb-bar{height:8px;background:var(--soft);border:1px solid var(--line);border-radius:3px;overflow:hidden}
.hb-bar span{display:block;height:100%;background:var(--human)}
.hb-num{text-align:right;font-weight:650;font-variant-numeric:tabular-nums}
.hb-label code{font-size:.78em}
.steps{padding-left:20px;margin:8px 0 12px}
.steps li{margin-bottom:8px;font-size:.92rem}
.mini-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:12px 0}
.mini-stats>div{background:var(--soft);border:1px solid var(--line);border-radius:8px;padding:10px 12px;display:flex;flex-direction:column}
.mini-stats dd{order:1;margin:0;font-size:1.4rem;font-weight:750;font-variant-numeric:tabular-nums}
.mini-stats dt{order:2;font-size:.76rem;color:var(--muted)}
.downloads{margin-top:24px}
.dl-list{list-style:none;padding:0;margin:8px 0 12px;display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.limits{padding-left:18px;max-width:90ch}
.limits li{margin-bottom:8px}
.foot{max-width:1160px;margin:48px auto 0;padding:0 24px 32px;color:var(--muted);font-size:.875rem}
.foot p{border-top:1px solid var(--line);padding-top:24px}
/* ---- v2: buttons and repo links ---- */
.btn{display:inline-flex;align-items:center;gap:8px;font-weight:600;text-decoration:none;border-radius:10px;
line-height:1.2;white-space:nowrap;transition:background .15s,border-color .15s,box-shadow .15s,transform .15s}
.btn-ghost{padding:9px 14px;border:1px solid var(--line2);color:var(--fg);background:#fff;font-size:.9rem;box-shadow:var(--shadow)}
.btn-ghost:hover{border-color:var(--fg);box-shadow:0 2px 8px rgba(15,23,42,.1)}
.btn .gh{flex:none}
.sub-row{display:flex;align-items:flex-start;gap:24px;justify-content:space-between}
.sub-row .btn{margin-top:2px;flex:none}
.top-gh{display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;border-radius:8px;
color:var(--fg);flex:none;border:1px solid var(--line)}
.top-gh:hover{background:var(--soft);border-color:var(--line2)}
/* ---- v2: hero coverage note ---- */
.big-label strong{color:var(--fg)}
.howknow{font-size:.82rem;color:var(--muted);background:var(--soft);border:1px solid var(--line);border-radius:8px;
padding:10px 12px;margin:2px 0 12px;line-height:1.5}
.hk-k{display:block;font-size:.66rem;text-transform:uppercase;letter-spacing:.07em;font-weight:700;color:var(--subtle);margin-bottom:3px}
.howknow code{font-size:.8em}
/* ---- v2: accuracy colours (green 90%+) ---- */
td.acc .bar{background:linear-gradient(90deg,#16a34a var(--w),var(--line) var(--w))}
td.acc.good span[data-src]{color:var(--good);font-weight:600}
.lg{font-weight:650;white-space:nowrap}.lg-good{color:var(--good)}.lg-mid{color:var(--warn)}.lg-weak{color:var(--bad)}
/* ---- v2: what Composio should do next ---- */
.handover{border:1px solid #c7d2fe;background:linear-gradient(180deg,var(--accent-soft),#fff 85%);border-radius:14px;
padding:18px 22px;margin:4px 0 28px}
.ho-lead{font-size:1.05rem;color:var(--fg);margin-bottom:8px;max-width:95ch}
.ho-note{font-size:.88rem;color:var(--muted);margin:0}
.nx-head{display:flex;align-items:baseline;gap:6px 16px;flex-wrap:wrap;margin:0 0 14px}
.nx-head h3{display:flex;align-items:center;gap:10px;margin:0;font-size:1.2rem}
.nx-head p{margin:0;color:var(--muted);font-size:.9rem}
.nx-ic{width:28px;height:28px;padding:5px;border-radius:8px;flex:none}
.nx-good .nx-ic{color:var(--good);background:var(--good-soft)}
.nx-warn .nx-ic{color:var(--warn);background:var(--warn-soft)}
.nx-head .count,.og-title .count{display:inline-block;min-width:1.8em;text-align:center;font-size:.8rem;background:var(--soft);
border:1px solid var(--line);border-radius:999px;padding:1px 8px;color:var(--muted);font-weight:650}
.bcards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-bottom:40px}
.bcard{border:1px solid var(--line);border-radius:12px;background:#fff;box-shadow:var(--shadow);
padding:14px 16px;display:flex;flex-direction:column;gap:10px;min-width:0}
.bc-head{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}
.bc-name{font-size:1.02rem;margin:0}
.bc-cat{font-size:.78rem;color:var(--subtle);margin:2px 0 0}
.auth-chip{font-size:.72rem;font-weight:700;color:var(--accent-d);background:var(--accent-soft);border:1px solid #c7d2fe;
border-radius:999px;padding:2px 9px;white-space:nowrap}
.checks{list-style:none;margin:0;padding:0;display:grid;gap:5px}
.ck{display:grid;grid-template-columns:18px minmax(0,1fr) auto;gap:8px;align-items:center;font-size:.83rem;
padding:6px 9px;background:var(--good-soft);border-radius:7px}
.ck-ic{width:16px;height:16px;color:var(--good);stroke-width:2.8}
.ck-l{color:var(--fg)}
.ck-v{font-weight:600;white-space:nowrap;text-align:right}
.ck-v a{color:var(--good);text-decoration-color:rgba(21,128,61,.4)}
.bcard .warn-note{margin-top:0}
.ogroups{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:24px;align-items:start}
.og-title{display:flex;align-items:center;gap:8px;margin:0 0 4px;font-size:1.02rem}
.og-blurb{font-size:.86rem;color:var(--muted);margin-bottom:10px}
.ocards{display:grid;gap:8px}
.ocard{border:1px solid var(--line);border-radius:10px;background:#fff;padding:10px 14px;
display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 12px;align-items:center}
.oc-name{font-size:.95rem;text-transform:none;letter-spacing:0;color:var(--fg);margin:0}
.oc-cat{margin:0;font-size:.76rem;color:var(--subtle)}
.oc-need{margin:0;display:inline-flex;align-items:center;gap:5px;font-size:.76rem;font-weight:650;color:var(--warn);
background:var(--warn-soft);border:1px solid #fde68a;border-radius:999px;padding:2px 9px 2px 6px;white-space:nowrap;justify-self:end}
.oc-ic{width:14px;height:14px}
.oc-meta{grid-column:1/-1;margin:0;display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:.82rem}
.cov{font-size:.72rem;font-weight:650;border-radius:999px;padding:1px 8px;border:1px solid var(--line);color:var(--muted);background:var(--soft)}
.cov-yes{color:var(--good);background:var(--good-soft);border-color:#bbf7d0}
.cov-no{color:var(--accent-d);background:var(--accent-soft);border-color:#c7d2fe}
.ocard .warn-note{grid-column:1/-1;margin-top:2px}
/* ---- v2: findings toolbar ---- */
.toolbar{display:grid;grid-template-columns:minmax(0,1fr);gap:12px;margin-bottom:14px}
.toolbar>*,.filters>*{min-width:0}
.tb-top{display:flex;gap:10px 16px;align-items:center;flex-wrap:wrap}
.search{position:relative;flex:1 1 320px;max-width:440px;display:block}
.search input{width:100%;font:inherit;font-size:.95rem;padding:9px 12px 9px 38px;border:1px solid var(--line2);border-radius:10px;
background:#fff;color:var(--fg);-webkit-appearance:none;appearance:none;box-shadow:var(--shadow)}
.search input:focus{outline:2px solid var(--accent);outline-offset:1px;border-color:var(--accent)}
.s-ic{position:absolute;left:12px;top:50%;transform:translateY(-50%);width:17px;height:17px;color:var(--subtle);pointer-events:none}
.count-line{margin:0 0 0 auto}
.clear-btn{display:inline-flex;align-items:center;gap:6px;font:inherit;font-size:.84rem;font-weight:600;height:34px;
border:1px solid var(--line2);background:#fff;border-radius:999px;padding:0 14px 0 10px;color:var(--fg);cursor:pointer}
.clear-btn:hover:not(:disabled){border-color:var(--bad);color:var(--bad)}
.clear-btn:disabled{opacity:.45;cursor:default}
.x-ic{width:15px;height:15px}
.filters{display:flex;flex-wrap:wrap;gap:14px 28px;padding:14px 16px;border:1px solid var(--line);border-radius:12px;background:var(--soft)}
.fgroup{display:flex;flex-direction:column;gap:6px;min-width:0}
.fg-wide{flex:1 1 100%}
.fg-label{font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--subtle)}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.fchip{display:inline-flex;align-items:center;gap:7px;height:32px;padding:0 12px;border-radius:999px;border:1px solid var(--line2);
background:#fff;font:inherit;font-size:.84rem;color:var(--fg);cursor:pointer;white-space:nowrap;transition:background .12s,border-color .12s}
.fchip:hover{border-color:var(--accent);color:var(--accent-d)}
.fchip:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.fchip[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);color:#fff}
.fc-n{font-size:.72rem;font-weight:700;color:var(--muted);background:var(--soft);border:1px solid var(--line);border-radius:999px;
padding:0 6px;line-height:1.5;font-variant-numeric:tabular-nums}
.fchip[aria-pressed="true"] .fc-n{background:rgba(255,255,255,.2);border-color:transparent;color:#fff}
.dl{margin:0;font-size:.88rem;color:var(--muted);display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.dl-ic{width:16px;height:16px;color:var(--subtle)}
.linkish{all:unset;cursor:pointer;color:var(--accent-d);text-decoration:underline;font-weight:600}
/* ---- v2: findings rows ---- */
.app-cell{display:flex;align-items:flex-start;gap:6px}
.exp{all:unset;box-sizing:border-box;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;width:24px;height:24px;
border-radius:6px;color:var(--subtle);flex:none;margin:-2px 0 0 -4px;border:1px solid transparent}
.exp:hover{background:var(--accent-soft);color:var(--accent-d)}
.exp:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.chev{width:16px;height:16px;transition:transform .15s}
.exp[aria-expanded="true"] .chev{transform:rotate(90deg)}
.exp[aria-expanded="true"]{color:var(--accent-d);background:var(--accent-soft);border-color:#c7d2fe}
.app-link{font-weight:650;color:var(--accent-d);text-decoration:none}
.app-link:hover,.app-link:focus-visible{text-decoration:underline;text-underline-offset:3px}
.ext-ic{width:12px;height:12px;margin-left:4px;vertical-align:-1px;stroke-width:2.2}
.findings tr.row{cursor:pointer}
.findings tr.row:hover td,.findings tr.row:hover th{background:#f5f7ff}
.findings tr.row.is-open td,.findings tr.row.is-open th{background:var(--accent-soft)}
.findings td a:not(.pill){text-decoration-color:#94a3b8;text-underline-offset:3px}
.c-more{display:none}
.when-open{display:none}
.exp-more[aria-expanded="true"] .when-open{display:inline}
.exp-more[aria-expanded="true"] .when-closed{display:none}
/* ---- v2: pipeline diagram ---- */
.flow2{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));column-gap:44px;row-gap:50px;padding-top:60px;margin:8px 0 0}
.nwrap{position:relative;min-width:0;display:flex}
.nw-input{grid-area:1/1}.nw-pass1{grid-area:1/2}.nw-pass2{grid-area:1/3}.nw-selector{grid-area:1/4}.nw-pass3{grid-area:1/5}
.nw-final{grid-area:2/5}.nw-scoring{grid-area:2/4}.nw-patterns{grid-area:2/3}
.hg-final{grid-area:3/5}.hg-scoring{grid-area:3/4}
.flow-legend{grid-area:2/1/4/3;align-self:start}
.node{all:unset;box-sizing:border-box;cursor:pointer;width:100%;display:flex;gap:10px;align-items:flex-start;padding:12px;
border:1px solid var(--line);border-radius:12px;background:#fff;box-shadow:var(--shadow);transition:border-color .15s,box-shadow .15s}
.node:hover{border-color:#a5b4fc;box-shadow:0 4px 14px rgba(79,70,229,.12)}
.node:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.node[aria-expanded="true"]{border-color:var(--accent);box-shadow:0 0 0 3px rgba(79,70,229,.18)}
.n-ic{flex:none;width:34px;height:34px;border-radius:9px;display:grid;place-items:center;background:var(--accent-soft);color:var(--accent-d)}
.node[aria-expanded="true"] .n-ic{background:var(--accent);color:#fff}
.n-ic .ic{width:19px;height:19px}
.n-body{display:flex;flex-direction:column;gap:3px;min-width:0}
.n-title{font-weight:700;font-size:.86rem;line-height:1.25;color:var(--fg)}
.n-metric{font-size:.78rem;color:var(--muted);line-height:1.35}
.n-metric span[data-src]{font-weight:700;color:var(--fg)}
.node-human{background:var(--human-soft);border-color:#f0abfc}
.node-human .n-ic{background:#fae8ff;color:var(--human)}
.node-human:hover{border-color:var(--human);box-shadow:0 4px 14px rgba(162,28,175,.12)}
.node-human[aria-expanded="true"]{border-color:var(--human);box-shadow:0 0 0 3px rgba(162,28,175,.18)}
.node-human[aria-expanded="true"] .n-ic{background:var(--human);color:#fff}
.node .human-tag{align-self:flex-start;margin:0 0 2px}
.nwrap::before,.nwrap::after,.hgroup::before,.hgroup::after{position:absolute;pointer-events:none}
.a-right::before{content:"";left:calc(100% + 4px);top:50%;width:30px;height:2px;background:#94a3b8}
.a-right::after{content:"";left:calc(100% + 32px);top:50%;margin-top:-5px;border:5px solid transparent;border-left:8px solid #94a3b8;border-right:0}
.a-left::before{content:"";right:calc(100% + 4px);top:50%;width:30px;height:2px;background:#94a3b8}
.a-left::after{content:"";right:calc(100% + 32px);top:50%;margin-top:-5px;border:5px solid transparent;border-right:8px solid #94a3b8;border-left:0}
.a-down::before{content:"";top:calc(100% + 4px);left:50%;width:2px;height:36px;margin-left:-1px;background:#94a3b8}
.a-down::after{content:"";top:calc(100% + 38px);left:50%;margin-left:-5px;border:5px solid transparent;border-top:8px solid #94a3b8;border-bottom:0}
.hgroup{position:relative;display:grid;gap:10px;align-content:start;min-width:0}
.hgroup::before{content:"";bottom:calc(100% + 4px);left:50%;width:2px;height:36px;margin-left:-1px;background:var(--human)}
.hgroup::after{content:"";bottom:calc(100% + 38px);left:50%;margin-left:-5px;border:5px solid transparent;border-bottom:8px solid var(--human);border-top:0}
.hg-feeds{position:absolute;bottom:calc(100% + 15px);left:calc(50% + 10px);font-size:.7rem;font-weight:700;color:var(--human);
white-space:nowrap;text-transform:uppercase;letter-spacing:.05em}
.loop{position:absolute;bottom:calc(100% + 8px);left:6px;right:6px;margin:0;display:flex;align-items:center;gap:6px;
font-size:.72rem;line-height:1.25;color:var(--accent-d);font-weight:600;border:1.5px dashed #a5b4fc;border-bottom:0;
border-radius:12px 12px 0 0;padding:6px 10px 10px;background:#fff}
.loop::after{content:"";position:absolute;left:-6.5px;bottom:-7px;border:5.5px solid transparent;border-top:8px solid #818cf8;border-bottom:0}
.hg-feeds{left:auto!important;right:calc(50% + 10px)}
.loop-ic{width:15px;height:15px;flex:none}
.flow-legend{border:1px dashed var(--line2);border-radius:12px;padding:14px 16px;font-size:.82rem;color:var(--muted);background:#fff}
.flow-legend p{margin:0 0 8px;display:flex;align-items:center;gap:8px}
.flow-legend .fl-t{font-size:.68rem;text-transform:uppercase;letter-spacing:.07em;font-weight:700;color:var(--subtle)}
.flow-legend .fl-hint{margin:10px 0 0;padding-top:10px;border-top:1px solid var(--line);color:var(--fg);font-weight:600}
.flow-legend .loop-ic{color:var(--accent-d)}
.sw{display:inline-block;width:14px;height:14px;border-radius:4px;flex:none}
.sw-stage{background:var(--accent-soft);border:1.5px solid var(--accent)}
.sw-human{background:var(--human-soft);border:1.5px solid var(--human)}
.stage-panels{margin-top:22px;scroll-margin-top:80px}
.js .sp:not(.open){display:none}
.sp{border:1px solid var(--line);border-radius:14px;background:#fff;box-shadow:var(--shadow);
padding:18px 22px 20px;margin-bottom:12px;scroll-margin-top:80px}
.sp-human{border-top-color:var(--human)}
.sp-head{display:flex;gap:14px;align-items:flex-start;margin-bottom:14px}
.sp-ic{flex:none;width:42px;height:42px;border-radius:11px;display:grid;place-items:center;background:var(--accent-soft);color:var(--accent-d)}
.sp-human .sp-ic{background:#fae8ff;color:var(--human)}
.sp-head h4{margin:0 0 6px;font-size:1.15rem}
.sp-head h4 .human-tag{vertical-align:3px}
.sp-io{display:flex;flex-wrap:wrap;gap:6px;align-items:center;font-size:.8rem;color:var(--muted);margin:0}
.io-k{font-size:.64rem;text-transform:uppercase;font-weight:750;letter-spacing:.06em;color:var(--subtle)}
.io-arrow{color:var(--subtle);margin:0 2px}
.sp-feeds{margin-left:8px;color:var(--human);font-weight:650}
.sp-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:22px;font-size:.88rem}
.sp-grid h5{margin-bottom:6px}
.sp-grid p,.sp-acc>div{color:var(--fg);margin:0}
.sp-acc ul{margin:6px 0 0;padding-left:18px}
.sp-acc li{margin-bottom:2px}
.sp-tiles{display:flex;flex-wrap:wrap;gap:10px;margin:18px 0 0}
.sp-tiles>div{display:flex;flex-direction:column;background:var(--soft);border:1px solid var(--line);border-radius:10px;padding:8px 14px;min-width:120px}
.sp-tiles dd{margin:0;font-size:1.3rem;font-weight:750;order:1;font-variant-numeric:tabular-nums}
.sp-tiles dt{order:2;font-size:.76rem;color:var(--muted)}
/* ---- v2: how Composio was used ---- */
.takeaway{display:flex;gap:10px;align-items:flex-start;background:var(--soft);border:1px solid var(--line);border-radius:12px;
padding:12px 16px;margin:4px 0 20px;color:var(--fg);font-size:.95rem}
.tk-ic{flex:none;color:var(--accent);width:20px;height:20px;margin-top:2px}
.tl-models,.tl-list{list-style:none;margin:6px 0 0;padding:0;display:grid;gap:8px}\n.tl-models li{display:flex;flex-direction:column;gap:2px}\n.tl-k{font-size:.82rem;color:var(--muted)}\n.tl-models code{font:.84rem var(--mono);word-break:break-all}\n.tl-list li{display:flex;gap:8px;align-items:flex-start}\n.tl-list .ch-ic{flex:none;margin-top:4px}\n.tl-ok .ch-ic{color:var(--good)}\n.tl-no .ch-ic{color:var(--bad)}\n.cuse-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.cuse{border:1px solid var(--line);border-radius:14px;padding:18px 20px;background:#fff;box-shadow:var(--shadow);display:flex;flex-direction:column;min-width:0}
.cu-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.cu-ic{width:42px;height:42px;border-radius:11px;display:grid;place-items:center}
.cuse-ok .cu-ic{background:var(--good-soft);color:var(--good)}
.cuse-bad .cu-ic{background:var(--bad-soft);color:var(--bad)}
.cu-badge{font-size:.7rem;font-weight:750;text-transform:uppercase;letter-spacing:.06em;border-radius:999px;padding:3px 10px}
.cuse-ok .cu-badge{background:var(--good-soft);color:var(--good);border:1px solid #bbf7d0}
.cuse-bad .cu-badge{background:var(--bad-soft);color:var(--bad);border:1px solid #fecaca}
.cuse h3{font-size:1.05rem;margin-bottom:6px}
.cuse p{color:var(--muted);font-size:.9rem;margin-bottom:0}
.cu-stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:14px 0 0}
.cu-stats>div{display:flex;flex-direction:column;background:var(--soft);border:1px solid var(--line);border-radius:9px;padding:8px 12px}
.cu-stats dd{margin:0;font-size:1.25rem;font-weight:750;order:1;font-variant-numeric:tabular-nums}
.cu-stats dd .of{font-size:.6em;color:var(--subtle);font-weight:600;margin-left:1px}
.cu-stats dt{order:2;font-size:.74rem;color:var(--muted)}
.cu-chain{list-style:none;margin:16px 0 0;padding:0;display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.cu-chain li{display:inline-flex;align-items:center;gap:6px;font-size:.8rem;font-weight:600;border-radius:9px;padding:7px 11px;border:1px solid}
.cu-chain .ch-arr{border:0;padding:0;color:var(--subtle);font-weight:400}
.ch-no{color:var(--bad);background:var(--bad-soft);border-color:#fecaca;text-decoration:line-through;text-decoration-thickness:1px}
.ch-yes{color:var(--good);background:var(--good-soft);border-color:#bbf7d0}
.ch-ic{width:14px;height:14px;stroke-width:2.6}
/* ---- v2: downloads ---- */
.dl-list{list-style:none;padding:0;margin:8px 0 10px;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.dl-card{display:grid;grid-template-columns:auto minmax(0,1fr) auto;column-gap:12px;row-gap:1px;align-items:center;padding:12px 14px;
border:1px solid var(--line);border-radius:12px;text-decoration:none;color:var(--fg);background:#fff;box-shadow:var(--shadow);
transition:border-color .15s,box-shadow .15s}
.dl-card:hover{border-color:var(--accent);box-shadow:0 4px 14px rgba(79,70,229,.12)}
.dl-card .dl-ic{grid-row:1/3;width:38px;height:38px;border-radius:10px;display:grid;place-items:center;background:var(--accent-soft);color:var(--accent-d)}
.dl-name{font-family:var(--mono);font-weight:650;font-size:.88rem;color:var(--accent-d)}
.dl-desc{grid-column:2;font-size:.8rem;color:var(--muted)}
.dl-go{grid-row:1/3;grid-column:3;width:18px;height:18px;color:var(--subtle)}
.dl-card:hover .dl-go{color:var(--accent-d)}
.dl-note{font-size:.84rem;color:var(--muted)}
/* ---- v2: closing links ---- */
.links{padding-bottom:16px}
.link-row{display:flex;flex-wrap:wrap;gap:16px;margin-top:4px}
.btn-big{padding:16px 22px;border-radius:14px;gap:14px;min-width:300px}
.btn-big:hover{transform:translateY(-1px)}
.btn-primary{background:var(--fg);color:#fff;box-shadow:0 8px 22px rgba(15,23,42,.2)}
.btn-primary:hover{background:#1e293b}
.btn-primary:focus-visible{outline:3px solid var(--accent);outline-offset:3px}
.btn-big .gh{width:30px;height:30px}
.b-text{display:flex;flex-direction:column;gap:3px;text-align:left;min-width:0;white-space:normal}
.b-sub{overflow-wrap:anywhere}
.b-title{font-size:1.08rem;font-weight:700}
.b-sub{font-size:.8rem;font-weight:500;opacity:.8;font-family:var(--mono)}
.b-ext{width:16px;height:16px;margin-left:auto;opacity:.75}
.btn-second{background:#fff;color:var(--fg);border:1px solid var(--line2);box-shadow:var(--shadow);min-width:240px}
.btn-second:hover{border-color:var(--fg)}
.btn-second .b-sub{font-family:var(--sans);opacity:1;color:var(--muted)}
.btn-ic{width:28px;height:28px;color:var(--accent-d)}
@media (max-width:1080px){
 .acc-layout{grid-template-columns:1fr}
 .acc-side{grid-template-columns:1fr 1fr}
}
@media (max-width:860px){
 .cards,.two-col,.takeaways,.fixes,.stage-list{grid-template-columns:1fr}
 .card-wide{grid-column:auto}
 .card-wide .cat-bars,.hbars.cat{grid-template-columns:1fr;grid-auto-flow:row;grid-template-rows:none}
 .stats{grid-template-columns:1fr 1fr}
 .stats>div:nth-child(3){border-left:0}
 .stats>div:nth-child(n+3){border-top:1px solid var(--line)}
 .brand{display:none}
 .top nav ul{justify-content:flex-start}
}
@media (max-width:760px){
 h1{font-size:1.95rem}
 h2{font-size:1.35rem}
 main{padding:0 16px}
 .top-in{padding:0 8px}
 section{padding-top:40px}
 .subtitle{font-size:1.05rem}
 .card,.panel{padding:16px}
 .acc-table th,.acc-table td{padding:6px 4px;font-size:.8rem}
 .acc-table td.acc{min-width:0}
 .acc-table td.acc .bar{max-width:40px}
 .acc-table thead tr:first-child th{font-size:.74rem}
 .acc-table tbody th{font-size:.78rem;white-space:normal}
 .up,.down{margin-left:1px}
 .acc-side{grid-template-columns:1fr}
 .spot,.dl-list,.mini-stats{grid-template-columns:1fr 1fr}
 .split{grid-template-columns:1fr 1fr;gap:12px}
 .cat-bars li{grid-template-columns:minmax(0,1fr) 72px 3em}
 .hbars.cat li,.hbars li{grid-template-columns:minmax(0,1fr) 72px 2.2em}
 /* findings table becomes one card per app */
 .findings-wrap{border:0;overflow:visible}
 .findings{min-width:0;display:block;font-size:.88rem}
 .findings thead{display:none}
 .findings tbody{display:grid;gap:10px}
 .findings tr.row{display:flex;flex-wrap:wrap;align-items:center;gap:6px 14px;border:1px solid var(--line);
  border-radius:var(--r);background:#fff;box-shadow:var(--shadow);padding:12px 14px}
 .findings tr.row[hidden]{display:none}
 .findings tr.row>*{display:block;border:0;padding:0;max-width:none;min-width:0;white-space:normal;background:none!important}
 .findings tr.row>[data-label]{order:3;font-size:.84rem}
 .findings tr.row>[data-label]::before{content:attr(data-label);display:inline;margin-right:5px;font-size:.66rem;
  text-transform:uppercase;letter-spacing:.05em;color:var(--subtle);font-weight:600}
 .findings tr.row>.c-app{order:0;position:static;flex:1 1 auto;border:0;font-size:1rem}
 .findings tr.row>.c-app::before{display:none}
 .findings tr.row>.c-verdict{order:1}
 .findings tr.row>.c-verdict::before{display:none}
 .findings tr.row>.c-cat{order:2;flex:0 0 100%;margin-top:-6px;padding-left:15px;color:var(--subtle);font-size:.78rem}
 .findings tr.row>.c-cat::before{display:none}
 .findings tr.row>.c-one,.findings tr.row>.c-none{display:none}
 .findings .sub{display:inline;margin-left:4px}
 .findings .c-verdict{text-align:right}
 .findings .c-verdict .blk{display:block;max-width:none;margin:3px 0 0}
 .findings tr.detail{display:block;margin-top:-10px}
 .js .findings tr.detail{display:none}
 .js .findings tr.detail.open{display:block}
 .findings tr.detail>td{display:block;border:1px solid var(--line);border-top:0;border-radius:0 0 var(--r) var(--r)}
 .detail-inner{position:static;max-width:none;padding:12px}
 .detail-grid{grid-template-columns:minmax(0,1fr);overflow-wrap:anywhere}
 .findings tbody{grid-template-columns:minmax(0,1fr)}
 .findings tbody>*{min-width:0}
 .misses thead{display:none}
 .misses tr{display:block;border-bottom:1px solid var(--line);padding:8px 0}
 .misses th,.misses td{display:block;border:0;padding:4px 0}
 .label-note{max-width:none}
}
@media (max-width:420px){
 .stats dd{font-size:1.6rem}
 .stats>div{padding:14px}
 .big{font-size:2rem}
 .spot,.dl-list{grid-template-columns:1fr}
}
@media (max-width:1180px){
 .brand{display:none}
 .top nav ul{justify-content:flex-start}
}
@media (max-width:1080px){
 .bcards{grid-template-columns:repeat(2,minmax(0,1fr))}
 .sp-grid{grid-template-columns:1fr 1fr}
 .sp-grid .sp-acc{grid-column:1/-1}
 .dl-list{grid-template-columns:repeat(2,minmax(0,1fr))}
}
@media (max-width:900px){
 .flow2{grid-template-columns:minmax(0,1fr);row-gap:40px;padding-top:0}
 .flow2>*{grid-area:auto!important}
 .flow-legend{order:99}
 .nwrap{flex-direction:column}
 .loop{position:static;border:1.5px dashed #a5b4fc;border-radius:10px;padding:6px 10px;margin:0 0 6px}
 .loop::after{content:none}
 .a-right::before,.a-left::before{left:50%;right:auto;top:calc(100% + 4px);width:2px;height:28px;margin:0 0 0 -1px}
 .a-right::after,.a-left::after{left:50%;right:auto;top:calc(100% + 30px);margin:0 0 0 -5px;border:5px solid transparent;
  border-top:8px solid #94a3b8;border-bottom:0}
 .a-down::before{height:28px}.a-down::after{top:calc(100% + 30px)}
 .nwrap.has-att::before,.nwrap.has-att::after{content:none}
 .hgroup{margin-top:-6px}
 .hgroup::before{height:26px;bottom:calc(100% + 4px)}
 .hgroup::after{bottom:calc(100% + 26px)}
 .hg-feeds{bottom:calc(100% + 9px)}
 .nw-scoring::before,.nw-patterns::before{content:"";top:auto;bottom:calc(100% + 12px);left:50%;width:2px;height:24px;margin-left:-1px;background:#94a3b8}
 .nw-scoring::after,.nw-patterns::after{content:"";top:auto;bottom:calc(100% + 4px);left:50%;right:auto;margin:0 0 0 -5px;
  border:5px solid transparent;border-top:8px solid #94a3b8;border-bottom:0}
 .nw-scoring.has-att::before,.nw-scoring.has-att::after{content:""}
 .cuse-grid,.ogroups{grid-template-columns:minmax(0,1fr)}
}
@media (max-width:760px){
 .sub-row{flex-direction:column;gap:4px}
 .sub-row .btn{margin:0 0 8px}
 .bcards{grid-template-columns:minmax(0,1fr)}
 .sp{padding:16px}
 .sp-grid{grid-template-columns:minmax(0,1fr);gap:14px}
 .handover{padding:14px 16px}
 .ho-lead{font-size:.98rem}
 .tb-top{gap:10px}
 .search{flex:1 1 100%;max-width:none}
 .count-line{margin:0}
 .clear-btn{margin-left:auto}
 .filters{padding:12px;gap:12px}
 .fgroup{flex:1 1 100%}
 .chips{flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none;margin:0 -12px;padding:2px 12px;
  -webkit-mask-image:linear-gradient(90deg,transparent,#000 12px,#000 calc(100% - 28px),transparent);
  mask-image:linear-gradient(90deg,transparent,#000 12px,#000 calc(100% - 28px),transparent)}
 .chips::-webkit-scrollbar{display:none}
 .fchip{flex:none}
 .findings .c-app .exp{display:none}
 .findings tr.row>.c-cat{padding-left:0!important}
 .findings tr.row>.c-more{display:block;order:5;flex:0 0 auto;margin-left:auto}
 .exp-more{width:auto;height:30px;margin:0;padding:0 8px 0 12px;gap:4px;justify-content:center;border:1px solid #c7d2fe;
  border-radius:999px;font-size:.8rem;font-weight:650;color:var(--accent-d);background:var(--accent-soft)}
 .exp-more .chev{width:15px;height:15px}
 .findings tr.row.is-open{border-color:#c7d2fe}
 .findings tr.row.is-open>*{background:none!important}
 .dl-list{grid-template-columns:minmax(0,1fr)}
 .btn-big{min-width:0;width:100%}
 .cu-stats dd{font-size:1.1rem}
}
.hero-btns{display:flex;gap:10px;flex:none;flex-wrap:wrap;margin-top:2px}
.hero-btns .btn{padding:6px 11px;font-size:.82rem;gap:6px;border-radius:8px}
.hero-btns svg{width:15px;height:15px}
@media (max-width:640px){.hero-btns{margin-bottom:8px}.hero-btns .btn{margin:0}}
/* ---- v3: in-page filter dropdowns ---- */
.filters{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px 14px;padding:14px 16px}
@media (max-width:1000px){.filters{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:640px){.filters{grid-template-columns:repeat(2,minmax(0,1fr))}}
.dd{position:relative;display:flex;flex-direction:column;gap:6px;min-width:0}
.dd-btn{all:unset;box-sizing:border-box;cursor:pointer;display:flex;align-items:center;justify-content:space-between;gap:8px;
height:38px;padding:0 10px 0 12px;border:1px solid var(--line2);border-radius:9px;background:#fff;font-size:.9rem;color:var(--fg);
box-shadow:var(--shadow);transition:border-color .12s,box-shadow .12s}
.dd-btn:hover{border-color:#a5b4fc}
.dd-btn:focus-visible,.dd-btn[aria-expanded="true"]{border-color:var(--accent);box-shadow:0 0 0 3px rgba(79,70,229,.15)}
.dd.is-set .dd-btn{border-color:var(--accent);background:var(--accent-soft);color:var(--accent-d);font-weight:600}
.dd-val{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dd-chev{width:16px;height:16px;flex:none;color:var(--subtle);transition:transform .15s}
.dd-btn[aria-expanded="true"] .dd-chev{transform:rotate(180deg)}
.dd-menu{position:absolute;top:calc(100% + 6px);left:0;min-width:100%;width:max-content;max-width:min(320px,90vw);max-height:300px;overflow:auto;
margin:0;padding:6px;list-style:none;background:#fff;border:1px solid var(--line);border-radius:11px;z-index:30;
box-shadow:0 10px 30px rgba(15,23,42,.12),0 2px 6px rgba(15,23,42,.06)}
.dd-opt{display:grid;grid-template-columns:16px minmax(0,1fr) auto;align-items:center;gap:8px;padding:8px 10px;border-radius:7px;
font-size:.9rem;cursor:pointer;color:var(--fg);outline:none}
.dd-opt:hover,.dd-opt:focus{background:var(--soft)}
.dd-opt[aria-selected="true"]{background:var(--accent-soft);color:var(--accent-d);font-weight:600}
.dd-tick{width:15px;height:15px;visibility:hidden}
.dd-opt[aria-selected="true"] .dd-tick{visibility:visible}
.dd:last-child .dd-menu{left:auto;right:0}
.dd-opt .fc-n{justify-self:end}
.dd-opt:not(:has(.fc-n)) .dd-text{grid-column:2/4}
/* ---- v3: pager ---- */
.pager{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-top:14px}
.pager[hidden]{display:none}
.pg-info{font-size:.9rem;color:var(--muted)}
.pg-info strong{color:var(--fg)}
.pg-btns{display:flex;gap:6px;flex-wrap:wrap}
.pg-btn{all:unset;box-sizing:border-box;cursor:pointer;min-width:36px;height:36px;padding:0 12px;display:inline-flex;align-items:center;
justify-content:center;border:1px solid var(--line2);border-radius:9px;background:#fff;font-size:.88rem;font-weight:600;color:var(--fg)}
.pg-btn:hover:not([disabled]){border-color:var(--accent);color:var(--accent-d)}
.pg-btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.pg-btn[aria-current="page"]{background:var(--accent);border-color:var(--accent);color:#fff}
.pg-btn[disabled]{opacity:.4;cursor:default}
/* ---- v3: where a human was needed ---- */
.h-summary{display:flex;align-items:center;gap:20px 28px;flex-wrap:wrap;padding:22px 26px;border:1px solid #f5d0fe;
background:var(--human-soft);border-radius:14px;margin-bottom:36px}
.h-big{margin:0;font-size:3rem;font-weight:750;line-height:1;letter-spacing:-.03em;color:var(--human);font-variant-numeric:tabular-nums}
.h-big .of{font-size:.45em;color:var(--subtle);font-weight:600;letter-spacing:0}
.h-big-text{margin:0;max-width:56ch;color:var(--muted);font-size:1rem;line-height:1.6}
.h-big-text strong{display:block;color:var(--fg);font-size:1.1rem;margin-bottom:2px}
.h-sub{font-size:1.15rem;margin:0 0 14px}
.h-sub-note{font-weight:400;font-size:.9rem;color:var(--subtle);margin-left:6px}
.h-reasons{list-style:none;margin:0 0 40px;padding:0;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
.hr-card{display:flex;gap:16px;align-items:flex-start;padding:18px 20px;border:1px solid var(--line);border-radius:12px;background:#fff;box-shadow:var(--shadow)}
.hr-num{flex:none;min-width:52px;height:52px;padding:0 8px;border-radius:12px;display:grid;place-items:center;background:var(--human-soft);
color:var(--human);font-size:1.4rem;font-weight:750;font-variant-numeric:tabular-nums}
.hr-card h4,.h-steps h4{margin:2px 0 4px;font-size:1rem}
.hr-card p,.h-steps p{margin:0;font-size:.92rem;color:var(--muted);line-height:1.6}
.h-steps{list-style:none;margin:0 0 40px;padding:0;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}
.h-steps li{padding:18px 20px;border:1px solid var(--line);border-radius:12px;background:var(--soft)}
.h-steps{counter-reset:hs}.h-steps li{counter-increment:hs}.hs-n::before{content:counter(hs)}
.hs-n{display:grid;place-items:center;width:28px;height:28px;border-radius:50%;background:var(--fg);color:#fff;font-size:.82rem;font-weight:700;margin-bottom:10px}
.h-cats{list-style:none;margin:0;padding:20px 24px;border:1px solid var(--line);border-radius:12px;display:grid;
grid-template-columns:repeat(2,minmax(0,1fr));column-gap:48px;row-gap:12px}
.h-cats li{display:grid;grid-template-columns:minmax(0,1fr) 140px 1.6em;gap:12px;align-items:center;font-size:.92rem}
.hc-bar{height:8px;background:var(--soft);border:1px solid var(--line);border-radius:999px;overflow:hidden}
.hc-bar span{display:block;height:100%;background:var(--human);border-radius:999px}
.hc-num{text-align:right;font-weight:650;font-variant-numeric:tabular-nums}
@media (max-width:900px){.h-reasons{grid-template-columns:minmax(0,1fr)}.h-steps{grid-template-columns:repeat(2,minmax(0,1fr))}
.h-cats{grid-template-columns:minmax(0,1fr)}}
@media (max-width:640px){.h-steps{grid-template-columns:minmax(0,1fr)}.h-cats li{grid-template-columns:minmax(0,1fr) 80px 1.6em}
.h-cats{padding:16px}.h-summary{padding:18px}.filters{grid-template-columns:repeat(2,minmax(0,1fr))}
.pager{justify-content:center}.pg-info{width:100%;text-align:center}}
/* ---- v3: typography, easier reading ---- */
body{font-size:16.5px;line-height:1.65;-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
p,li{text-wrap:pretty}
h1,h2,h3{text-wrap:balance}
h2{font-size:1.75rem;letter-spacing:-.02em;margin-bottom:12px}
h3{line-height:1.35}
section{padding:72px 0 16px}
.section-lead{font-size:1.05rem;line-height:1.7;max-width:68ch;margin-bottom:32px}
.lead{line-height:1.7;max-width:68ch}
.subtitle{line-height:1.5}
.facts,.steps li,.takeaways li,.item-meta,.group-note,.eg,.og-blurb,.ho-note,.nx-head p{line-height:1.65}
.takeaways li{font-size:.95rem}
.facts{font-size:.95rem}
.facts li{margin-bottom:6px}
.item-meta,.group-note{font-size:.9rem}
.og-blurb{font-size:.92rem}
.ho-lead{line-height:1.6}
.ck{font-size:.88rem;padding:7px 10px}
.bc-cat,.oc-cat{font-size:.82rem}
.warn-note{font-size:.84rem;line-height:1.5;padding:6px 10px}
.findings{font-size:.88rem;line-height:1.5}
.findings td,.findings tbody th{padding:12px 10px}
.findings thead th{font-size:.72rem;padding:12px 10px}
.detail-grid{font-size:.9rem;line-height:1.6}
blockquote{font-size:.88rem;line-height:1.55}
details>div{line-height:1.65}
.panel p{line-height:1.65}
@media print{
 .top,.toolbar,.skip{display:none}
 .js tr.detail{display:none}
 details{break-inside:avoid}
 details>*{display:block}
 section{break-inside:auto}
 a{color:inherit}
}
"""

JS = r"""
(function(){
  function $(s,r){return (r||document).querySelector(s)}
  function $$(s,r){return [].slice.call((r||document).querySelectorAll(s))}
  function payload(id,json){var el=document.getElementById(id);if(!el)return null;
    try{return json?JSON.parse(el.textContent):el.textContent}catch(e){return null}}
  var rows=payload('dl-rows',true)||[],needs=payload('dl-needs',true)||{rows:[]};
  var flagged={};(needs.rows||[]).forEach(function(r){flagged[r.slug]=!!r.needs_human});
  var bySlug={};rows.forEach(function(r){bySlug[r.slug]={name:r.name,one_liner:r.one_liner,slug:r.slug,
    verdict:r.verdict,access:r.access,mcp_status:r.mcp_status,category:r.category,
    composio:(r.composio_toolkit||{}).status,needs_human:String(!!flagged[r.slug])}});

  /* ---- findings filters (in-page dropdowns) + pagination ---- */
  var q=$('#q'),shown=$('#shown'),empty=$('#empty'),clear=$('#clear'),pager=$('#pager');
  var trs=$$('tr.row'),dds=$$('.dd'),state={},PER=20,page=1,matched=[];
  function match(r){var t=(q&&q.value||'').trim().toLowerCase();
    if(t&&(r.name+' '+r.one_liner+' '+r.slug).toLowerCase().indexOf(t)<0)return false;
    for(var k in state){if(state[k]&&String(r[k])!==state[k])return false}return true}
  function render(){var pages=Math.max(1,Math.ceil(matched.length/PER));if(page>pages)page=pages;
    var lo=(page-1)*PER,hi=lo+PER,on={};matched.slice(lo,hi).forEach(function(tr){on[tr.getAttribute('data-slug')]=1});
    trs.forEach(function(tr){var slug=tr.getAttribute('data-slug'),v=!!on[slug];tr.hidden=!v;
      var d=document.getElementById('d-'+slug);if(d)d.hidden=!v});
    if(!pager)return;pager.hidden=!matched.length;if(!matched.length){pager.innerHTML='';return}
    var h='<p class="pg-info">Showing <strong>'+(lo+1)+'–'+Math.min(hi,matched.length)+'</strong> of <strong>'+matched.length+'</strong></p>';
    if(pages>1){h+='<div class="pg-btns"><button type="button" class="pg-btn" data-pg="'+(page-1)+'"'+(page<2?' disabled':'')+'>← Prev</button>';
      for(var i=1;i<=pages;i++)h+='<button type="button" class="pg-btn" data-pg="'+i+'"'+(i===page?' aria-current="page"':'')+
        ' aria-label="Page '+i+'">'+i+'</button>';
      h+='<button type="button" class="pg-btn" data-pg="'+(page+1)+'"'+(page>=pages?' disabled':'')+'>Next →</button></div>'}
    pager.innerHTML=h}
  if(pager)pager.addEventListener('click',function(e){var b=e.target.closest('[data-pg]');if(!b||b.disabled)return;
    page=+b.getAttribute('data-pg');render();var f=$('#findings .findings-wrap');
    if(f&&f.getBoundingClientRect().top<0)f.scrollIntoView({block:'start'})});
  function apply(keepPage){matched=trs.filter(function(tr){var r=bySlug[tr.getAttribute('data-slug')];return !r||match(r)});
    if(!keepPage)page=1;render();
    if(shown)shown.textContent=matched.length;if(empty)empty.hidden=matched.length>0;
    var active=(q&&q.value.trim())||Object.keys(state).some(function(k){return state[k]});
    if(clear)clear.disabled=!active}
  function showPageOf(slug){var i=matched.findIndex(function(tr){return tr.getAttribute('data-slug')===slug});
    if(i<0){clearAll();i=matched.findIndex(function(tr){return tr.getAttribute('data-slug')===slug})}
    if(i>=0){page=Math.floor(i/PER)+1;render()}}
  function closeAll(except){dds.forEach(function(d){if(d===except)return;var m=$('.dd-menu',d);if(m)m.hidden=true;
    $('.dd-btn',d).setAttribute('aria-expanded','false')})}
  function setDD(d,v){var k=d.getAttribute('data-filter');state[k]=v;var label='All';
    $$('.dd-opt',d).forEach(function(o){var on=o.getAttribute('data-value')===v;o.setAttribute('aria-selected',String(on));
      if(on)label=$('.dd-text',o).textContent});
    $('.dd-val',d).textContent=label;d.classList.toggle('is-set',!!v)}
  dds.forEach(function(d){var btn=$('.dd-btn',d),menu=$('.dd-menu',d),opts=$$('.dd-opt',d);
    function open(){closeAll(d);menu.hidden=false;btn.setAttribute('aria-expanded','true');
      var cur=opts.filter(function(o){return o.getAttribute('aria-selected')==='true'})[0]||opts[0];cur.focus()}
    function close(f){menu.hidden=true;btn.setAttribute('aria-expanded','false');if(f)btn.focus()}
    btn.addEventListener('click',function(e){e.stopPropagation();menu.hidden?open():close()});
    btn.addEventListener('keydown',function(e){if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();open()}});
    opts.forEach(function(o,i){o.addEventListener('click',function(e){e.stopPropagation();setDD(d,o.getAttribute('data-value'));close(true);apply()});
      o.addEventListener('keydown',function(e){var j=e.key==='ArrowDown'?i+1:e.key==='ArrowUp'?i-1:e.key==='Home'?0:e.key==='End'?opts.length-1:-9;
        if(opts[j]){e.preventDefault();opts[j].focus();return}
        if(e.key==='Enter'||e.key===' '){e.preventDefault();o.click()}
        else if(e.key==='Escape'){e.preventDefault();close(true)}
        else if(e.key==='Tab'){close(false)}})})});
  document.addEventListener('click',function(e){if(!e.target.closest('.dd'))closeAll()});
  function clearAll(){state={};if(q)q.value='';dds.forEach(function(d){setDD(d,'')});apply()}
  if(q){q.addEventListener('input',function(){apply()});q.addEventListener('keydown',function(e){if(e.key==='Escape'){q.value='';apply()}})}
  if(clear)clear.addEventListener('click',clearAll);
  $$('[data-clear]').forEach(function(b){b.addEventListener('click',clearAll)});
  apply();

  /* ---- row details ---- */
  function toggle(slug,open){var d=document.getElementById('d-'+slug);if(!d)return;
    var o=open===undefined?!d.classList.contains('open'):open;d.classList.toggle('open',o);
    var tr=document.getElementById('app-'+slug);if(tr)tr.classList.toggle('is-open',o);
    $$('button.exp[aria-controls="d-'+slug+'"]').forEach(function(b){b.setAttribute('aria-expanded',o?'true':'false')})}
  trs.forEach(function(tr){var slug=tr.getAttribute('data-slug');
    $$('button.exp',tr).forEach(function(b){b.addEventListener('click',function(e){e.stopPropagation();toggle(slug)})});
    tr.addEventListener('click',function(e){if(e.target.closest('a,button'))return;
      if(window.getSelection&&String(window.getSelection()).length)return;toggle(slug)})});
  function fromUrl(){var m=(location.hash.match(/expand=([a-z0-9-]+)/)||location.search.match(/expand=([a-z0-9-]+)/));
    if(!m)return;showPageOf(m[1]);toggle(m[1],true);var tr=document.getElementById('app-'+m[1]);
    if(tr)setTimeout(function(){tr.scrollIntoView({block:'start'})},0)}
  window.addEventListener('hashchange',fromUrl);fromUrl();

  /* ---- pipeline diagram: one stage panel at a time ---- */
  var nodes=$$('.flow2 .node'),panels=$$('.stage-panels .sp');
  function select(id,focusPanel){nodes.forEach(function(b){b.setAttribute('aria-expanded',String(b.getAttribute('data-panel')===id))});
    panels.forEach(function(p){p.classList.toggle('open',p.id===id)});
    if(focusPanel){var p=document.getElementById(id);if(p&&p.getBoundingClientRect().top>window.innerHeight-80)
      p.scrollIntoView({block:'nearest'})}}
  nodes.forEach(function(b,i){b.addEventListener('click',function(){select(b.getAttribute('data-panel'),true)});
    b.addEventListener('keydown',function(e){var j=e.key==='ArrowRight'||e.key==='ArrowDown'?i+1:
      e.key==='ArrowLeft'||e.key==='ArrowUp'?i-1:-9;if(nodes[j]){e.preventDefault();nodes[j].focus()}})});
  if(nodes.length)select(nodes[0].getAttribute('data-panel'));

  /* ---- downloads: rebuilt from the embedded data, so they work from this file alone ---- */
  var LIST=['auth_methods','api_types'],OBJ=['evidence','composio_toolkit'];
  function cell(k,v){if(v===null||v===undefined)return '';
    if(LIST.indexOf(k)>=0)return v.map(String).join(';');
    if(OBJ.indexOf(k)>=0||typeof v==='object')return JSON.stringify(v);
    if(typeof v==='boolean')return v?'true':'false';return String(v)}
  function q8(s){return /[",\r\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s}
  function csv(){var cols=payload('dl-cols',true)||[];var out=[cols.map(q8).join(',')];
    rows.forEach(function(r){out.push(cols.map(function(c){return q8(cell(c,r[c]))}).join(','))});
    return out.join('\r\n')+'\r\n'}
  var BUILD={'final.json':function(){return [JSON.stringify(rows,null,2)+'\n','application/json']},
    'final.csv':function(){return [csv(),'text/csv']},
    'needs_human.json':function(){return [JSON.stringify(needs,null,2)+'\n','application/json']},
    'patterns.json':function(){return [payload('dl-patterns'),'application/json']},
    'schema.md':function(){return [payload('dl-schema'),'text/markdown']},
    'llms.txt':function(){return [payload('dl-llms'),'text/plain']}};
  window.__caseStudyDownload=function(name){var f=BUILD[name];if(!f)return null;var r=f();return r[0]===null?null:r};
  $$('a[data-dl]').forEach(function(a){a.addEventListener('click',function(e){
    var r=window.__caseStudyDownload(a.getAttribute('data-dl'));if(!r||!window.Blob||!window.URL)return;
    e.preventDefault();var url=URL.createObjectURL(new Blob([r[0]],{type:r[1]+';charset=utf-8'}));
    var t=document.createElement('a');t.href=url;t.download=a.getAttribute('data-dl');t.style.display='none';
    document.body.appendChild(t);t.click();t.remove();setTimeout(function(){URL.revokeObjectURL(url)},4000)})});

  $$('a[data-resume]').forEach(function(a){a.addEventListener('click',function(e){
    var el=document.getElementById('dl-resume');if(!el||!window.Blob||!window.URL||!window.atob)return;
    e.preventDefault();var bin=atob(el.textContent.trim()),u=new Uint8Array(bin.length);
    for(var i=0;i<bin.length;i++)u[i]=bin.charCodeAt(i);
    var url=URL.createObjectURL(new Blob([u],{type:'application/pdf'}));
    var t=document.createElement('a');t.href=url;t.download='resume.pdf';t.style.display='none';
    document.body.appendChild(t);t.click();t.remove();setTimeout(function(){URL.revokeObjectURL(url)},4000)})});

  /* ---- sticky nav: highlight the section in view ---- */
  var links=$$('.top nav a');
  if('IntersectionObserver' in window){
    var map={};links.forEach(function(a){map[a.getAttribute('href').slice(1)]=a});
    var vis={};function mark(){
      var best=null,bv=0;Object.keys(vis).forEach(function(k){if(vis[k]>bv){bv=vis[k];best=k}});
      if(window.innerHeight+window.scrollY>=document.documentElement.scrollHeight-4)best=links.length?
        links[links.length-1].getAttribute('href').slice(1):best;
      if(best){links.forEach(function(a){a.classList.remove('active');a.removeAttribute('aria-current')});
        if(map[best]){map[best].classList.add('active');map[best].setAttribute('aria-current','true');
          var nav=map[best].closest('nav');if(nav){nav.scrollLeft=map[best].offsetLeft-nav.clientWidth/2+map[best].clientWidth/2}}}}
    var io=new IntersectionObserver(function(es){es.forEach(function(e){vis[e.target.id]=e.isIntersecting?e.intersectionRatio:0});mark()},
      {rootMargin:'-64px 0px -55% 0px',threshold:[0,0.01,0.1,0.25,0.5]});
    window.addEventListener('scroll',function(){if(window.innerHeight+window.scrollY>=document.documentElement.scrollHeight-4)mark()},{passive:true});
    $$('main section[id]').forEach(function(s){io.observe(s)})}
})();
"""


if __name__ == "__main__":
    raise SystemExit(main())
