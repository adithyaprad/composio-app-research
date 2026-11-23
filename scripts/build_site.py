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
import csv
import filecmp
import html
import json
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
from agent.human import export_final  # noqa: E402
from scripts.patterns import CAVEAT_BELOW, auth_combo, pick_examples  # noqa: E402
from scripts.patterns import MODERATE_BELOW as PATTERNS_MODERATE_BELOW  # noqa: E402

REPO_URL = "https://github.com/adithyaprad/composio-app-research"
SITE_DIR = ROOT / "site"
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
        "gold": read_jsonl(p(P_GOLD)),
        "holdout": read_jsonl(p(P_HOLDOUT)),
        "audit": p(P_AUDIT).read_text(encoding="utf-8"),
        "schema": read_json(p(P_SCHEMA)),
        "schema_readme": p(P_SCHEMA_README).read_text(encoding="utf-8"),
        "readme": p("README.md").read_text(encoding="utf-8"),
        "scores": {kind: {n: read_json(p(score_path(kind, n))) for n in PASSES} for kind in SETS},
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
        <p class="big-label">easy wins: buildable now, official MCP, not on Composio yet</p>
        <p class="eg">e.g. {names(pick_examples(easy, 3))}</p></div>
      <div><p class="big warn">{n(gated_total, rows_src('verdict=buildable_with_approval'))}</p>
        <p class="big-label">need outreach: a sales or partner gate first</p>
        <p class="eg">e.g. {names(pick_examples(out['gated_sales'], 1) + pick_examples(out['gated_partner'], 2))}</p></div>
    </div>
    <p class="more"><a href="#next">See both lists</a></p>
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
  <p class="subtitle">Which apps an agent toolkit can be built for today, which need outreach first, and how far to trust the answers.</p>
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
                cls = "weak" if v < WEAK_BELOW else "mid" if v < MODERATE_BELOW else ""
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
  <p class="section-lead">Field accuracy against hand-labelled answers, after each loop: a gold set of {ng} apps and a sealed holdout of {nh} apps the rules never saw. Amber is under {n(MODERATE_BELOW, f"{P_PATTERNS}:meta.thresholds.moderate_below", "pct")}, red under {n(WEAK_BELOW, f"{P_PATTERNS}:meta.thresholds.caveated_below", "pct")}.</p>
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
  <p class="more"><a href="#verification">Full verification, every miss, and the spot-check</a></p>
</section>"""


def _scored_note(m: dict, slug: str) -> str:
    notes = []
    for kind, u in m["miss_by_slug"].get(slug, []):
        notes.append(f"{kind} label disagrees on {FIELD_LABEL[u['unit']].lower()}: {u['reason']}")
    return "".join(f'<span class="warn-note">{esc(t)}</span>' for t in notes)


def section_next(src: dict, m: dict) -> str:
    easy = m["easy"]
    out = m["outreach"]
    easy_f = "verdict=buildable_now&mcp_status=official&composio_toolkit.status=none"
    li = []
    for r in easy:
        url = r.get("mcp_url") or first_url(r, "mcp")
        li.append(f'<li><div class="item-head"><span class="item-name" data-text>{esc(r["name"])}</span>'
                  f'{chip(AUTH.get(r["primary_auth"], r["primary_auth"]))}</div>'
                  f'<div class="item-meta">{esc(m["labels"][r["category"]])} · {link(url, "MCP server")}'
                  f' · {link(first_url(r, "verdict"), "verdict evidence")}</div>{_scored_note(m, r["slug"])}</li>')
    groups = []
    for access, title, blurb in (("gated_sales", "Sales gate", "a contract, demo or sales-provisioned account"),
                                 ("gated_partner", "Partner program", "a vendor app review or partner approval")):
        items = []
        for r in out[access]:
            has = composio_status(r) == "exists"
            items.append(f'<li><div class="item-head"><span class="item-name" data-text>{esc(r["name"])}</span>'
                         f'{chip("on Composio" if has else "not on Composio", "ok" if has else "")}</div>'
                         f'<div class="item-meta">{esc(m["labels"][r["category"]])} · '
                         f'{link(first_url(r, "access"), "access evidence")}</div>{_scored_note(m, r["slug"])}</li>')
        covered = sum(composio_status(r) == "exists" for r in out[access])
        groups.append(f'<h4>{title} <span class="count">{n(len(out[access]), rows_src(f"verdict=buildable_with_approval&access={access}"))}</span></h4>'
                      f'<p class="group-note">Needs {blurb}. {n(covered, rows_src(f"verdict=buildable_with_approval&access={access}&composio_toolkit.status=exists"))} already have a Composio toolkit.</p>'
                      f'<ul class="item-list">{"".join(items)}</ul>')
    gated_total = len(out["gated_sales"]) + len(out["gated_partner"])
    return f"""
<section id="next" aria-labelledby="h-next">
  <h2 id="h-next">What Composio should do next</h2>
  <p class="section-lead">Two lists straight from the final rows. Gated-with-evidence is a correct finding, not a failure: each gated app links the vendor page that shows the gate.</p>
  <div class="two-col">
    <div class="panel panel-good">
      <h3>Build now <span class="count">{n(len(easy), rows_src(easy_f))}</span></h3>
      <p class="group-note">Buildable today, an official MCP server exists, and Composio has no toolkit yet. Where a hand label disagrees, it says so.</p>
      <ul class="item-list">{''.join(li)}</ul>
    </div>
    <div class="panel panel-warn">
      <h3>Needs outreach <span class="count">{n(gated_total, rows_src('verdict=buildable_with_approval'))}</span></h3>
      {''.join(groups)}
    </div>
  </div>
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


def section_findings(src: dict, m: dict) -> str:
    rows = m["rows"]
    body = []
    table_data = []
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
        acls = "a-self" if r["access"] in SELF_SERVE else "a-gated"
        access_html = link(first_url(r, "access"), ACCESS.get(r["access"], r["access"]), acls)
        has_tk = composio_status(r) == "exists"
        flag_html = (f'<span class="flag" title="{esc("; ".join(reasons))}">check<span class="sr-only">: '
                     f'{esc(", ".join(reasons))}</span></span>' if flagged else '<span class="muted">—</span>')
        if r["blocker"] != "none":  # blocker shares the verdict cell; it is "none" exactly when buildable now
            verdict_html += f'<span class="sub blk">blocker: {esc(BLOCKER.get(r["blocker"], r["blocker"]).lower())}</span>'
        body.append(
            f'<tr class="row" id="app-{esc(slug)}" data-slug="{esc(slug)}">'
            f'<th scope="row" class="c-app" data-label="App"><button type="button" class="exp" aria-expanded="false" '
            f'aria-controls="d-{esc(slug)}"><span class="caret" aria-hidden="true"></span>{esc(r["name"])}</button></th>'
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
            f'</tr>'
            f'<tr class="detail" id="d-{esc(slug)}" data-slug="{esc(slug)}"><td colspan="11">{_detail(src, m, r)}</td></tr>')
        table_data.append({
            "slug": slug, "name": r["name"], "category": r["category"], "one_liner": r["one_liner"],
            "primary_auth": r["primary_auth"], "auth_methods": r["auth_methods"], "access": r["access"],
            "api_types": r["api_types"], "api_breadth": r["api_breadth"], "mcp_status": r["mcp_status"],
            "mcp_url": r.get("mcp_url"), "verdict": r["verdict"], "blocker": r["blocker"],
            "composio": composio_status(r), "needs_human": flagged, "needs_human_reasons": reasons,
            "browser_pass": (m["browse"].get(slug) or {}).get("status"), "confidence": r["confidence"],
        })

    def options(name: str, values: list[tuple[str, str]], label: str) -> str:
        opts = "".join(f'<option value="{esc(v)}">{esc(t)}</option>' for v, t in values)
        return (f'<label class="f"><span>{label}</span><select data-filter="{name}" aria-label="Filter by {label.lower()}">'
                f'<option value="">All</option>{opts}</select></label>')

    present = lambda field, labels: [(k, v) for k, v in labels.items() if any(r[field] == k for r in rows)]  # noqa: E731
    filters = "".join([
        options("verdict", present("verdict", VERDICT), "Verdict"),
        options("access", present("access", ACCESS), "Access"),
        options("mcp_status", present("mcp_status", MCP), "MCP"),
        options("category", [(c, m["labels"][c]) for c in m["categories"]], "Category"),
        options("needs_human", [("true", "Flagged"), ("false", "Not flagged")], "Human check"),
        options("composio", [("exists", "Covered"), ("none", "Not yet")], "Composio"),
    ])
    payload = json.dumps(table_data, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/").replace("<!--", "<\\!--")
    total = m["total"]
    return f"""
<section id="findings" aria-labelledby="h-findings">
  <h2 id="h-findings">Findings: all {n(total, rows_src(''))} apps</h2>
  <p class="section-lead">One row per app. Each field links to its first evidence page; open a row for the quoted snippets, notes, the browser-pass result and any human-check reasons. API breadth is a rough sketch (it scored lowest of the fields).</p>
  <div class="toolbar">
    <label class="search"><span class="sr-only">Search apps</span><input type="search" id="q" placeholder="Search name or description" aria-label="Search by name or description" autocomplete="off"></label>
    <div class="filters">{filters}</div>
    <div class="tb-foot">
      <p class="count-line" aria-live="polite">Showing <strong id="shown" data-src="{esc(rows_src(''))}" data-fmt="int">{total}</strong> of {n(total, rows_src(''))}</p>
      <p class="dl">Download: <a href="data/final.json" download>final.json</a> · <a href="data/final.csv" download>final.csv</a> · <a href="data/schema.md">schema</a></p>
    </div>
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
    <p class="empty" id="empty" hidden>No apps match these filters.</p>
  </div>
  <script type="application/json" id="rows-data">{payload}</script>
</section>"""


def section_agent(src: dict, m: dict) -> str:
    log1 = src["log1"]
    review = src["review"]
    browse = src["browse"]
    flags = src["flags"]
    model = Counter(x.get("llm_model") for x in log1).most_common(1)[0][0]
    provider = Counter(x.get("llm_provider") for x in log1).most_common(1)[0][0]
    total = len(src["apps"])
    changed2 = sum(r["status"] == "changed" for r in review)
    unchanged2 = sum(r["status"] == "unchanged" for r in review)
    rule_counts = Counter(rule for r in review for rule in r.get("rules_fired") or [])
    flagged = sum(f["flagged"] for f in flags)
    reason_counts = Counter(x for f in flags for x in f.get("reasons") or [])
    st = Counter(b["status"] for b in browse)
    pl = sum(b.get("pages_local", 0) for b in browse)
    pc = sum(b.get("pages_cloud", 0) for b in browse)
    sessions = sum(b.get("cloud_sessions", 0) for b in browse)
    s = m["scores"]
    nh_total = src["needs"]["needs_human_count"]
    spot_n = src["spot"]["result"]["rows_checked"]
    reasons_li = "".join(f"<li>{n(reason_counts[k], f'{P_FLAGS}:count(reasons@={k})')} {esc(SELECTOR_TEXT.get(k, k))}</li>"
                         for k, _ in reason_counts.most_common())
    p1_pages = sum(x.get("fetch_calls", 0) for x in log1)
    p1_search = sum(x.get("search_calls", 0) for x in log1)
    stages = [
        ("Input", f"{n(total, f'{P_APPS}:count()')} apps + website hints", "The assignment list, as a CSV.", ""),
        ("Pass 1 · Research agent",
         f"LLM on {esc(provider.title())} + Exa search and fetch",
         f"{n(p1_search, f'{P_LOG1}:sum(search_calls)')} searches, {n(p1_pages, f'{P_LOG1}:sum(fetch_calls)')} page fetches; every claim quoted; validators",
         "Searches official docs, fills one schema row per app from fetched text only, and quotes the sentence behind each claim. "
         f"Evidence URLs must be pages it actually fetched; quotes must appear in the page text; consistency validators catch contradictions. Model: <code>{esc(model)}</code>."),
        ("Pass 2 · Rule checker",
         f"{n(len(rule_counts), f'{P_REVIEW}:distinct(rules_fired)')} general rules fired",
         f"{n(changed2, f'{P_REVIEW}:count(status=changed)')} rows changed, {n(unchanged2, f'{P_REVIEW}:count(status=unchanged)')} left byte-identical",
         "Re-checks every row against general rules (no app names). Code fires a rule only when notes or a vendor quote show the pattern; one LLM reviewer call per row can name more. Only the fields behind a fired rule are re-researched."),
        ("Selector",
         f"{n(flagged, f'{P_FLAGS}:count(flagged=true)')} unsure rows flagged",
         "a row can have several reasons",
         f"Code picks rows that still look shaky. Reasons:<ul>{reasons_li}</ul>"),
        ("Pass 3 · Browser Use",
         f"{n(pl, f'{P_BROWSE}:sum(pages_local)')} pages in local Chromium, {n(pc, f'{P_BROWSE}:sum(pages_cloud)')} on Browser Use Cloud",
         f"{n(st['confirmed'], f'{P_BROWSE}:count(status=confirmed)')} confirmed, {n(st['changed'], f'{P_BROWSE}:count(status=changed)')} changed",
         f"Renders each flagged row's pricing, signup, auth and MCP pages in a real browser and saves a screenshot. Only a blocked, captcha or empty page is retried on a cloud browser ({n(sessions, f'{P_BROWSE}:sum(cloud_sessions)')} cloud sessions). The same LLM reads the rendered text and may confirm or fix only the flagged fields, with quotes found on the page. It reads pages; it does not click or navigate."),
        ("Scoring",
         f"vs gold {set_size_n(s, 'gold')} + sealed holdout {set_size_n(s, 'holdout')}",
         "per field and whole row",
         "<code>scripts/score_gold.py</code> compares each pass to locked hand labels. A row counts only if every critical field is right and evidence is present."),
        ("Patterns + this page",
         "counts, cross-tabs, caveats",
         "generated, never hand-typed",
         "<code>scripts/patterns.py</code> clusters the final rows; <code>scripts/build_site.py</code> renders this page and <code>scripts/check_site.py</code> re-checks every number."),
    ]
    human_in = {"Scoring": "human answer keys", "Pass 3 · Browser Use": "then human review + spot-check"}
    flow = "".join(f'<li class="stage"><span class="st-title">{t}</span><span class="st-main">{a}</span>'
                   f'<span class="st-sub">{b}</span>'
                   f'{"<span class=st-human>" + human_in[t] + "</span>" if t in human_in else ""}</li>'
                   for t, a, b, _ in stages)
    details = "".join(f'<details class="stage-d"><summary>{t}</summary><div>{d}</div></details>'
                      for t, _, _, d in stages if d)
    humans = [
        ("Gold labels + audit", f"{set_size_n(s, 'gold')} apps labelled blind from official docs, then audited",
         "feeds Scoring"),
        ("Sealed holdout", f"{set_size_n(s, 'holdout')} apps labelled and locked before the pass-2 rules",
         "feeds Scoring"),
        ("Needs-human review", f"{n(nh_total, f'{P_NEEDS}:needs_human_count')} final rows flagged for a person to look at", "after Pass 3"),
        ("Spot-check", f"{n(spot_n, f'{P_SPOT}:result.rows_checked')} random rows checked by hand against the evidence", "after Pass 3"),
    ]
    hl = "".join(f'<li class="hstage"><span class="human-tag">human</span><span class="st-title">{t}</span>'
                 f'<span class="st-sub">{b}</span><span class="feeds">{f}</span></li>' for t, b, f in humans)
    outputs = [Path(v).name for v in src["proof"]["outputs"].values()]
    return f"""
<section id="agent" aria-labelledby="h-agent">
  <h2 id="h-agent">How the agent works</h2>
  <p class="section-lead">A pipeline of loops, not a single run. The agent fills every row; a rule checker and a browser pass re-check it; people write the answer keys and look at what the agent flags.</p>
  <figure class="flow-fig" aria-label="Pipeline diagram">
    <ol class="flow">{flow}</ol>
    <p class="lane-label">Where a person made the call</p>
    <ol class="hlane">{hl}</ol>
  </figure>
  <h3 class="sub-h">Run it with one command</h3>
  <pre class="code"><code>python -m agent all --input apps.csv --run-id my-run</code></pre>
  <p class="code-note">Runs research → rule check → browser check into <code>data/runs/my-run/pass1..3</code>, then writes <code>{'</code>, <code>'.join(esc(o) for o in outputs)}</code>. <code>--dry-run</code> runs offline with fakes.</p>
  <h3 class="sub-h">What each stage does</h3>
  <div class="stage-list">{details}</div>
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


def section_composio(src: dict, m: dict) -> str:
    cov = src["coverage"]
    covered = sum(r["status"] == "exists" for r in cov)
    return f"""
<section id="composio" aria-labelledby="h-composio">
  <h2 id="h-composio">How Composio was used</h2>
  <p class="section-lead">Honestly: the catalog lookup and the agent skill did real work; tool execution through Composio was wired in but never connected for this project.</p>
  <div class="use-grid">
    <div class="use"><p class="use-st">{chip("used", "ok")}</p><h3>Catalog coverage check</h3>
      <p>A snapshot of the toolkit catalog ({n(src['toolkits_total'], f'{P_TOOLKITS}:total')} toolkits) was matched to the apps: <strong>{n(covered, f'{P_COVERAGE}:count(status=exists)')} of {n(len(cov), f'{P_COVERAGE}:count()')}</strong> already have a toolkit. This field is a lookup, never LLM output.</p></div>
    <div class="use"><p class="use-st">{chip("used", "ok")}</p><h3>Composio agent skill</h3>
      <p>The Composio agent skill, kept in the repo under .agents/skills/composio, was used while building the research agent.</p></div>
    <div class="use"><p class="use-st">{chip("not connected", "bad")}</p><h3>Instant tool execution</h3>
      <p>{readme_instant_tools(src)} were wired to run through a Composio session. Every call came back "No active connection found", so research uses the Exa API directly, with free search and fetch as fallback.</p></div>
    <div class="use"><p class="use-st">{chip("not connected", "bad")}</p><h3>Browser Use toolkit</h3>
      <p>A read-only probe found no active connection, so pass 3 uses the open-source Browser Use library and Browser Use Cloud directly.</p></div>
  </div>
</section>"""


def _acc_table(m: dict, kind: str) -> str:
    s = m["scores"][kind]
    keys = list(CRITICAL) + list(MINOR) + ["coverage"]
    body = []
    for key in keys:
        vals = [field_acc(s[p], key) for p in PASSES]
        cells = "".join(
            f'<td class="acc {"weak" if v < WEAK_BELOW else "mid" if v < MODERATE_BELOW else ""}">'
            f'{n(v, score_src(kind, p, key), "pct")}</td>' for p, v in zip(PASSES, vals, strict=True))
        crit = "critical" if key in CRITICAL else "minor"
        body.append(f'<tr><th scope="row">{FIELD_LABEL[key]} <span class="muted small">{crit}</span></th>{cells}</tr>')
    rows = "".join(f'<td>{n(s[p]["summary"]["n_row_correct"], f"{score_path(kind, p)}:summary.n_row_correct")}</td>'
                   for p in PASSES)
    return (f'<table class="mini"><thead><tr><th scope="col">Field</th><th scope="col">Pass 1</th><th scope="col">Pass 2</th>'
            f'<th scope="col">Pass 3</th></tr></thead><tbody>{"".join(body)}'
            f'<tr class="total"><th scope="row">Whole rows right</th>{rows}</tr></tbody></table>')


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


def section_verification(src: dict, m: dict) -> str:
    s = m["scores"]
    review = src["review"]
    browse = src["browse"]
    rule_counts = Counter(rule for r in review for rule in r.get("rules_fired") or [])
    fields2 = Counter(f for r in review if r["status"] == "changed" for f in r.get("fields_changed") or []
                      if f not in ("evidence", "notes"))
    rules_li = "".join(f'<tr><td><code>{esc(k)}</code></td><td>{esc(RULE_TEXT.get(k, ""))}</td>'
                       f'<td class="num">{n(v, f"{P_REVIEW}:count(rules_fired@={k})")}</td></tr>'
                       for k, v in sorted(rule_counts.items(), key=lambda kv: (-kv[1], kv[0])))
    fields2_txt = ", ".join(f"{esc(k)} {n(v, f'{P_REVIEW}:count(status=changed&fields_changed@={k})')}"
                            for k, v in sorted(fields2.items(), key=lambda kv: (-kv[1], kv[0])))
    st = Counter(b["status"] for b in browse)
    why = Counter(p.get("why_cloud") for b in browse for p in b.get("pages") or [] if p.get("browser") == "cloud")
    why_txt = ", ".join(f"{esc(k)} {n(v, f'{P_BROWSE}:count_pages(browser=cloud&why_cloud={k})')}"
                        for k, v in sorted(why.items(), key=lambda kv: (-kv[1], str(kv[0]))))
    cost3 = sum(b.get("est_cost_usd") or 0 for b in browse)
    wall3 = sum(b.get("wall_s") or 0 for b in browse)
    changed3 = Counter(f for b in browse if b["status"] == "changed" for f in b.get("fields_changed") or []
                       if f not in ("evidence", "notes"))
    changed3_txt = ", ".join(f"{esc(k)} {n(v, f'{P_BROWSE}:count(status=changed&fields_changed@={k})')}"
                             for k, v in sorted(changed3.items(), key=lambda kv: (-kv[1], kv[0])))

    # hits and misses
    rows_by = {r["slug"]: r for r in m["rows"]}
    miss_rows = []
    for kind in SETS:
        regress = set(m["regressions"] if kind == "gold" else m["holdout_regressions"])
        for app in m["misses"][kind]:
            # The generated reason adds detail only for list fields; for single values it would repeat the line.
            fields = "".join(
                f'<li><strong>{FIELD_LABEL[u["unit"]]}</strong>: agent <span class="val-bad">{esc(_val(u["unit"], u["agent"]))}</span>, '
                f'gold <span class="val-good">{esc(_val(u["unit"], u["gold"]))}</span>'
                f'{"<span class=why>" + esc(u["reason"]) + "</span>" if u["unit"] in ("auth", "api") else ""}</li>'
                for u in app["units"])
            tag = ""
            if app["slug"] in regress:
                b = m["browse"].get(app["slug"]) or {}
                changed = [f for f in b.get("fields_changed") or [] if f not in ("evidence", "notes")]
                evid = [it.get("note") for it in (rows_by.get(app["slug"], {}).get("evidence") or {}).get("verdict", [])]
                tag = ('<span class="tag tag-rough">pass-3 regression</span> Right in pass 2, wrong in pass 3: the '
                       f'browser pass changed {esc(", ".join(changed))}. Its verdict evidence: '
                       f'<q>{esc(RENDERED_NOTE.sub("", evid[0] or "").strip().rstrip(".")) if evid else ""}</q>. '
                       'Hand label note: ')
            miss_rows.append(f'<tr><th scope="row"><span data-text>{esc(app["name"])}</span><span class="muted small"> {kind}</span></th>'
                             f'<td><ul class="miss-list">{fields}</ul></td>'
                             f'<td class="label-note" data-text>{tag}{esc(app["label_notes"])}</td></tr>')
    hits = {kind: [a for a in s[kind][3]["apps"] if a.get("row_correct")] for kind in SETS}
    hits_html = "".join(
        f'<p><strong>{kind.title()} rows fully right in pass 3 ({n(len(hits[kind]), f"{score_path(kind, 3)}:summary.n_row_correct")}):</strong> '
        f'<span data-text>{", ".join(esc(a["name"]) for a in hits[kind])}</span>.</p>' for kind in SETS)
    n_miss_apps = {kind: len(m["misses"][kind]) for kind in SETS}

    # pass-3 fixes outside the scored sets
    fixes = changed_rows(src["rows2"], m["rows"], ("mcp_status", "mcp_url"), m["gold_slugs"] | m["holdout_slugs"])
    fix_items = []
    for f in fixes:
        b = m["browse"].get(f["slug"]) or {}
        def shown(v: Any) -> str:
            if v is None:
                return "null"
            return short_url(v) if str(v).startswith("http") else str(v)

        changes = "".join(f'<li><code>{esc(k)}</code>: <span class="val-bad">{esc(shown(f["before"][k]))}</span> → '
                          f'<span class="val-good">{esc(shown(f["after"][k]))}</span></li>' for k in f["fields"])
        pages = "".join(f'<li>{link(p.get("final_url") or p.get("url"), p.get("title") or p.get("url"))} '
                        f'<span class="muted">({esc(p.get("browser"))}{", " + esc(p.get("why_cloud")) if p.get("why_cloud") else ""})</span>'
                        f'{" · " + link(SCREENSHOT_BASE + p["screenshot"], "screenshot") if p.get("screenshot") else ""}</li>'
                        for p in b.get("pages") or [])
        quotes = [x for x in src["evidence"][f["slug"]]["snippets"].get("mcp", []) if x.get("quote")]
        q = "".join(f'<blockquote>{esc(_clip(x["quote"]))}<cite>{link(x["url"], short_url(x["url"]))}'
                    f'{" · " + esc(x["browser"]) + " browser" if x.get("browser") else ""}</cite></blockquote>'
                    for x in quotes[:2])
        fix_items.append(f'<div class="fix" data-text><h5>{esc(f["name"])}</h5><ul class="changes">{changes}</ul>'
                         f'{q}<p class="small"><strong>Pages rendered:</strong></p><ul class="pages">{pages}</ul>'
                         f'<p class="small muted">{esc(rows_by[f["slug"]].get("notes"))}</p></div>')

    # spot-check
    spot = src["spot"]
    spot_items = "".join(
        f'<li><span class="item-name" data-text>{esc(it["name"])}</span> '
        f'<span class="muted">{esc(m["labels"].get(it["category"], it["category"]))}</span> <span class="chip ok">'
        f'{n(sum(v == "right" for v in it["human_check"]["claims"].values()), f"{P_SPOT}:count_values(items.{i}.human_check.claims=right)")}/'
        f'{n(len(it["human_check"]["claims"]), f"{P_SPOT}:len(items.{i}.human_check.claims)")} claims right</span></li>'
        for i, it in enumerate(spot["items"]))
    corrections = gold_audit_corrections(src["audit"])
    ng = s["gold"][3]["summary"]["n_gold"]
    nho = s["holdout"][3]["summary"]["n_gold"]
    per_cat = Counter(g["category"] for g in src["gold"])
    ensure(len(set(per_cat.values())) == 1, "gold has the same number of apps per category")
    return f"""
<section id="verification" aria-labelledby="h-verification">
  <h2 id="h-verification">Verification in full</h2>
  <p class="section-lead">How the numbers above were produced, every remaining miss, and what the browser pass actually fixed. A gated app with evidence of the gate counts as a correct finding, not a failure.</p>

  <details class="v" id="v-misses" open>
    <summary>Hits and misses on the hand-labelled sets (pass 3)</summary>
    <div class="v-body">
      <p>{n(n_miss_apps['gold'], f"{score_path('gold', 3)}:apps_with_critical_miss()")} of {n(ng, f"{score_path('gold', 3)}:summary.n_gold")} gold apps and {n(n_miss_apps['holdout'], f"{score_path('holdout', 3)}:apps_with_critical_miss()")} of {n(nho, f"{score_path('holdout', 3)}:summary.n_gold")} holdout apps still have a critical miss. Every one is listed, with the hand label's own note.</p>
      <div class="table-wrap"><table class="misses">
        <thead><tr><th scope="col">App</th><th scope="col">What the agent got wrong</th><th scope="col">Hand label note</th></tr></thead>
        <tbody>{''.join(miss_rows)}</tbody>
      </table></div>
      {hits_html}
    </div>
  </details>

  <details class="v" id="v-fields">
    <summary>Per-field accuracy, all fields</summary>
    <div class="v-body two-col">
      <div><h4>Gold ({n(ng, f"{score_path('gold', 3)}:summary.n_gold")} apps)</h4>{_acc_table(m, 'gold')}</div>
      <div><h4>Holdout ({n(nho, f"{score_path('holdout', 3)}:summary.n_gold")} apps)</h4>{_acc_table(m, 'holdout')}</div>
      <p class="small muted full">Critical fields decide whether a whole row is right; minor fields are reported but never fail a row. Evidence scoring checks that each claim has an evidence URL; whether the page really supports the claim was checked by people on samples. Red is under {n(WEAK_BELOW, f"{P_PATTERNS}:meta.thresholds.caveated_below", "pct")}, amber under {n(MODERATE_BELOW, f"{P_PATTERNS}:meta.thresholds.moderate_below", "pct")}.</p>
    </div>
  </details>

  <details class="v" id="v-loops">
    <summary>What each loop changed</summary>
    <div class="v-body">
      <h4>Pass 2 · rule checker</h4>
      <p>{n(sum(r['status'] == 'changed' for r in review), f'{P_REVIEW}:count(status=changed)')} of {n(len(review), f'{P_REVIEW}:count()')} rows changed. Fields changed (besides evidence and notes): {fields2_txt}.</p>
      <div class="table-wrap"><table class="mini rules"><thead><tr><th scope="col">Rule</th><th scope="col">What it catches</th><th scope="col" class="num">Rows</th></tr></thead><tbody>{rules_li}</tbody></table></div>
      <h4>Pass 3 · Browser Use</h4>
      <p>{n(sum(b.get('flagged', False) for b in browse), f'{P_BROWSE}:count(flagged=true)')} rows flagged: {n(st['confirmed'], f'{P_BROWSE}:count(status=confirmed)')} confirmed, {n(st['changed'], f'{P_BROWSE}:count(status=changed)')} changed, {n(st['copied'], f'{P_BROWSE}:count(status=copied)')} unflagged rows copied through unchanged.
      Fields changed (besides evidence and notes): {changed3_txt}.
      Pages: {n(sum(b.get('pages_local', 0) for b in browse), f'{P_BROWSE}:sum(pages_local)')} in local headless Chromium, {n(sum(b.get('pages_cloud', 0) for b in browse), f'{P_BROWSE}:sum(pages_cloud)')} retried on Browser Use Cloud ({why_txt}) across {n(sum(b.get('cloud_sessions', 0) for b in browse), f'{P_BROWSE}:sum(cloud_sessions)')} sessions.
      LLM cost about {n(cost3, f'{P_BROWSE}:sum(est_cost_usd)', 'usd')} (token estimate) and {n(wall3, f'{P_BROWSE}:sum(wall_s)', 'min')} minutes of summed row time.</p>
    </div>
  </details>

  <details class="v" id="v-fixes">
    <summary>Pass 3 fixes outside the scored sets</summary>
    <div class="v-body">
      <p>{n(len(fixes), f'{P_ROWS2}:changed({P_ROWS};mcp_status,mcp_url;exclude_scored)')} rows outside gold and holdout had their MCP answer changed by the browser pass. Neither is scored, so these do not show up in the accuracy numbers.</p>
      <div class="fixes">{''.join(fix_items)}</div>
    </div>
  </details>

  <details class="v" id="v-spot">
    <summary>Human spot-check of {n(spot['result']['rows_checked'], f'{P_SPOT}:result.rows_checked')} random rows</summary>
    <div class="v-body">
      <p><strong>Result:</strong> {n(spot['result']['rows_correct'], f'{P_SPOT}:result.rows_correct')}/{n(spot['result']['rows_checked'], f'{P_SPOT}:result.rows_checked')} rows and {n(spot['result']['claims_correct'], f'{P_SPOT}:result.claims_correct')}/{n(spot['result']['claims_checked'], f'{P_SPOT}:result.claims_checked')} claims (auth, access, MCP, verdict) confirmed.
      <strong>Method:</strong> {esc(spot['method'])}. Sample: fixed seed {n(spot['seed'], f'{P_SPOT}:seed')}, drawn from the {n(spot['population'], f'{P_SPOT}:population')} final rows minus the {n(spot['excluded_count'], f'{P_SPOT}:excluded_count')} gold and holdout apps.</p>
      <p class="scope-note"><strong>Scope:</strong> <span data-text>{esc(spot['scope_note'])}</span></p>
      <ul class="spot">{spot_items}</ul>
    </div>
  </details>

  <details class="v" id="v-keys">
    <summary>How the answer keys were built</summary>
    <div class="v-body">
      <ul class="plain-list">
        <li><strong>Gold ({n(ng, f"{score_path('gold', 3)}:summary.n_gold")} apps, {n(max(per_cat.values()), f'{P_GOLD}:max_per(category)')} per category).</strong> Labelled by hand from official docs without looking at the agent's rows, then audited field by field against current vendor docs. The audit corrected {n(len(corrections), f'{P_AUDIT}:corrected_apps()')} apps' scored labels and clarified the rubric (<code>schema/GOLD_AUDIT.md</code>).</li>
        <li><strong>Sealed holdout ({n(nho, f"{score_path('holdout', 3)}:summary.n_gold")} apps).</strong> A seeded random draw, one per category, excluding gold and pilot apps. Labelled and locked before the pass-2 rules were written, scored once per frozen pass, and never used to write a rule.</li>
        <li><strong>Gated counts as correct.</strong> Where an app is behind a sales or partner gate, saying so with the vendor page as evidence is the right answer and is scored that way.</li>
        <li><strong>Strict row score.</strong> A row is right only if auth, access, API, MCP, verdict and blocker all match and every claim has evidence.</li>
      </ul>
    </div>
  </details>
</section>"""


def section_humans(src: dict, m: dict) -> str:
    needs = src["needs"]
    total = needs["needs_human_count"]
    by_reason = needs["by_reason"]
    reason_sum = sum(by_reason.values())
    ensure(reason_sum > total, "needs-human reasons overlap (they sum to more than the flagged rows)")
    mx = max(by_reason.values())
    reasons = "".join(
        f'<li><span class="hb-label">{esc(NEEDS_HUMAN_TEXT.get(k, "").capitalize())}<code>{esc(k)}</code></span>'
        f'<span class="hb-bar" aria-hidden="true"><span style="width:{100 * v / mx:.0f}%"></span></span>'
        f'<span class="hb-num">{n(v, f"{P_NEEDS}:by_reason.{k}")}</span></li>'
        for k, v in sorted(by_reason.items(), key=lambda kv: -kv[1]))
    rows = m["rows"]
    flagged = {s for s, r in m["nh"].items() if r.get("needs_human")}
    by_cat = sorted(((c, sum(1 for r in rows if r["category"] == c and r["slug"] in flagged),
                      sum(1 for r in rows if r["category"] == c)) for c in m["categories"]),
                    key=lambda t: (-t[1], m["categories"].index(t[0])))
    cats = "".join(
        f'<li><span class="hb-label">{esc(m["labels"][c])}</span>'
        f'<span class="hb-bar" aria-hidden="true"><span style="width:{100 * k / max(t, 1):.0f}%"></span></span>'
        f'<span class="hb-num">{n(k, f"{P_PATTERNS}:crosstabs.by_category.{c}.needs_human.count")}</span></li>'
        for c, k, t in by_cat)
    s = m["scores"]
    return f"""
<section id="humans" aria-labelledby="h-humans">
  <h2 id="h-humans">Where a human was needed</h2>
  <p class="section-lead">People wrote the answer keys and are the referee for rows the agent is unsure about. A flag means "a person should look", not "the agent failed".</p>
  <div class="two-col">
    <div class="panel">
      <h3>{n(total, f'{P_NEEDS}:needs_human_count')} of {n(needs['total'], f'{P_NEEDS}:total')} rows flagged for a human check</h3>
      <p class="group-note">A row can have several reasons, so these add up to {n(reason_sum, f'{P_NEEDS}:sum(by_reason)')}, more than {n(total, f'{P_NEEDS}:needs_human_count')}.</p>
      <ul class="hbars">{reasons}</ul>
    </div>
    <div class="panel panel-human">
      <h3><span class="human-tag">human</span> The four human steps</h3>
      <ol class="steps">
        <li><strong>Gold labels + audit</strong>: {n(s['gold'][3]['summary']['n_gold'], f"{score_path('gold', 3)}:summary.n_gold")} apps labelled blind, then every field re-checked against official docs.</li>
        <li><strong>Sealed holdout</strong>: {n(s['holdout'][3]['summary']['n_gold'], f"{score_path('holdout', 3)}:summary.n_gold")} apps labelled and locked before any checker rule existed.</li>
        <li><strong>Needs-human review</strong>: {n(total, f'{P_NEEDS}:needs_human_count')} flagged rows listed in <a href="data/needs_human.json">needs_human.json</a> with reasons and evidence URLs.</li>
        <li><strong>Spot-check</strong>: {n(src['spot']['result']['rows_checked'], f'{P_SPOT}:result.rows_checked')} random rows outside gold and holdout, every deciding claim opened and compared by hand.</li>
      </ol>
      <p class="small muted">Every sales or partner gate is flagged on purpose: a bot cannot sit through a demo or pass a partner review, so a person decides whether to start that outreach.</p>
    </div>
  </div>
  <div class="panel panel-flat">
    <h3>Flagged rows by category <span class="muted small">out of {cat_size_n(m)} apps each</span></h3>
    <ul class="hbars cat">{cats}</ul>
  </div>
</section>"""


def section_proof(src: dict, m: dict) -> str:
    proof = src["proof"]
    names_ = f'<span data-text>{", ".join(esc(r["name"]) for r in src["proof_rows"])}</span>'
    t = proof["totals"]
    flagged = sum(f.get("flagged", False) for f in src["proof_flags"])
    outputs = [Path(v).name for v in proof["outputs"].values()]
    flagged_txt = (f"The selector flagged {n(flagged, f'{P_PROOF_FLAGS}:count(flagged=true)')} rows, so no browser pages were needed."
                   if flagged == 0 else
                   f"The selector flagged {n(flagged, f'{P_PROOF_FLAGS}:count(flagged=true)')} rows for the browser pass.")
    return f"""
<section id="proof" aria-labelledby="h-proof">
  <h2 id="h-proof">Proof: run it yourself</h2>
  <div class="two-col">
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
    <div class="panel">
      <h3>A live end-to-end run on {n(proof['apps_requested'], f'{P_PROOF}:apps_requested')} apps outside the {n(len(m['rows']), rows_src(''))}</h3>
      <p>{names_}: research, rule check and browser check, then the final export.</p>
      <dl class="mini-stats">
        <div><dt>rows out</dt><dd>{n(proof['rows_final'], f'{P_PROOF}:rows_final')}</dd></div>
        <div><dt>seconds, all stages</dt><dd>{n(t['wall_s'], f'{P_PROOF}:totals.wall_s', 's')}</dd></div>
        <div><dt>LLM + Exa cost</dt><dd>{n(t['est_cost_usd'] + t['exa_cost_usd'], f'{P_PROOF}:add(totals.est_cost_usd,totals.exa_cost_usd)', 'usd')}</dd></div>
        <div><dt>flagged for a human check</dt><dd>{n(proof['needs_human_count'], f'{P_PROOF}:needs_human_count')}</dd></div>
      </dl>
      <p class="small">{flagged_txt} Outputs in <code>data/runs/proof-e2e/</code>: <code>{'</code>, <code>'.join(esc(o) for o in outputs)}</code>.</p>
    </div>
  </div>
  <div class="downloads">
    <h3>Downloads</h3>
    <ul class="dl-list">
      <li><a href="data/final.json" download>final.json</a><span>all {n(len(m['rows']), rows_src(''))} rows with evidence</span></li>
      <li><a href="data/final.csv" download>final.csv</a><span>same rows, one line each</span></li>
      <li><a href="data/schema.md">schema.md</a><span>what each field means</span></li>
      <li><a href="data/patterns.json" download>patterns.json</a><span>counts and cross-tabs</span></li>
      <li><a href="data/needs_human.json" download>needs_human.json</a><span>rows flagged for a person</span></li>
      <li><a href="llms.txt">llms.txt</a><span>entry point for agents</span></li>
    </ul>
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


NAV = (("patterns", "Patterns"), ("accuracy", "Accuracy"), ("next", "Next steps"), ("findings", "Findings"),
       ("agent", "Agent"), ("composio", "Composio"), ("verification", "Verification"), ("humans", "Humans"),
       ("proof", "Proof"), ("limits", "Limits"))


def render_page(src: dict) -> str:
    m = build_model(src)
    nav = "".join(f'<li><a href="#{k}">{t}</a></li>' for k, t in NAV)
    body = "".join(fn(src, m) for fn in (section_hero, section_accuracy, section_next, section_findings, section_agent,
                                         section_composio, section_verification, section_humans, section_proof,
                                         section_limits))
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
  </div>
</header>
<main>{body}</main>
<footer class="foot">
  <p>Data as of {as_of_span(m)} · {link(REPO_URL, 'Source repo')} · Generated by <code>scripts/build_site.py</code> · <a href="llms.txt">llms.txt</a></p>
</footer>
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

- [Case study page](index.html): patterns, accuracy, next steps, the findings table, how the agent works, verification and limits

## Data

- [data/final.json](data/final.json): all {total} final rows (JSON array, sorted by id)
- [data/final.csv](data/final.csv): the same rows as CSV (`;`-joined lists, JSON for evidence and composio_toolkit)
- [data/schema.md](data/schema.md): field names, allowed values and meanings
- [data/patterns.json](data/patterns.json): distributions, cross-tabs and headline patterns with accuracy caveats
- [data/needs_human.json](data/needs_human.json): rows flagged for a human check, with reasons and evidence URLs

## Source

- [Repository]({REPO_URL}): research agent, rule checker, browser pass, scorer and this page's generator (`scripts/build_site.py`)
"""


def write_site(root: Path, out: Path) -> None:
    src = load_sources(root)
    if out.exists():
        shutil.rmtree(out)
    (out / "data").mkdir(parents=True)
    (out / "index.html").write_text(render_page(src), encoding="utf-8")
    export_final(src["rows"], out / "data")
    shutil.copyfile(root / P_NEEDS, out / "data" / "needs_human.json")
    shutil.copyfile(root / P_PATTERNS, out / "data" / "patterns.json")
    (out / "data" / "schema.md").write_text(schema_md(src), encoding="utf-8")
    (out / "llms.txt").write_text(llms_txt(src), encoding="utf-8")
    leaks = [p for p in out.rglob("*") if p.is_file() and re.search(r"/home/|/tmp/|/Users/",
                                                                    p.read_text(encoding="utf-8"))]
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
.use-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}
.use{border:1px solid var(--line);border-radius:var(--r);padding:16px 18px;background:#fff;box-shadow:var(--shadow);font-size:.9rem}
.use h3{font-size:1rem}
.use p{color:var(--muted)}
.use p strong{color:var(--fg)}
.use-st{margin-bottom:8px}
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
.takeaways li{border-left:3px solid var(--accent);padding:4px 0 4px 14px;font-size:.93rem;color:var(--muted)}
.takeaways strong{color:var(--fg);display:block;margin-bottom:2px}
.two-col{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px;align-items:start}
.two-col>*,.cards>*,.use-grid>*,.fixes>*,.acc-layout>*{min-width:0}
.panel{border:1px solid var(--line);border-radius:var(--r);padding:20px 22px;background:#fff;box-shadow:var(--shadow)}
.panel-good{border-top:3px solid #16a34a}.panel-warn{border-top:3px solid #f59e0b}.panel-human{border-top:3px solid var(--human)}
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
.search input{width:100%;max-width:420px;font:inherit;padding:9px 12px;border:1px solid var(--line2);border-radius:8px}
.filters{display:flex;flex-wrap:wrap;gap:8px 12px}
.f{display:flex;flex-direction:column;font-size:.74rem;color:var(--subtle);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.f select{font:inherit;font-size:.875rem;text-transform:none;letter-spacing:0;color:var(--fg);padding:6px 8px;border:1px solid var(--line2);
border-radius:8px;background:#fff;margin-top:3px;max-width:240px}
.tb-foot{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}
.count-line{margin:0;font-size:.9rem;color:var(--muted)}
.dl{margin:0;font-size:.9rem}
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
.exp{all:unset;cursor:pointer;font-weight:650;color:var(--fg);display:inline-flex;align-items:baseline;gap:6px}
.exp:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}
.caret{width:0;height:0;border-left:5px solid var(--subtle);border-top:4px solid transparent;border-bottom:4px solid transparent;
transition:transform .15s;flex:none;transform:translateY(-1px)}
.exp[aria-expanded="true"] .caret{transform:rotate(90deg)}
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
.d-human{border-left:3px solid var(--human);padding-left:10px}
.d-human ul{margin:4px 0 0;padding-left:16px}
.evidence{display:grid;gap:10px}
.ev-claim ul{list-style:none;margin:0;padding:0}
.ev-claim li{margin-bottom:6px}
.ev-url{overflow-wrap:anywhere;font-size:.82rem}
.ev-note{display:block;color:var(--muted)}
blockquote{margin:4px 0 6px;padding:4px 10px;border-left:3px solid var(--line2);color:var(--fg);background:#fff;border-radius:0 6px 6px 0;
font-size:.84rem}
blockquote cite{display:block;font-style:normal;font-size:.76rem;color:var(--subtle);overflow-wrap:anywhere}
.empty{padding:24px;text-align:center;color:var(--muted)}
.flow-fig{margin:0 0 24px}
.flow{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:22px;counter-reset:st}
.stage,.hstage{position:relative;border:1px solid var(--line);border-radius:var(--r);padding:12px 12px;background:#fff;
box-shadow:var(--shadow);display:flex;flex-direction:column;gap:4px;font-size:.82rem}
.stage{border-top:3px solid var(--accent)}
.stage:not(:last-child)::after{content:"";position:absolute;right:-17px;top:50%;width:12px;height:12px;
border-top:2px solid var(--line2);border-right:2px solid var(--line2);transform:translateY(-50%) rotate(45deg)}
.st-title{font-weight:700;font-size:.86rem;color:var(--fg)}
.st-main{color:var(--fg)}
.st-main span[data-src]{font-weight:700}
.st-sub{color:var(--muted);font-size:.78rem}
.lane-label{margin:20px 0 8px;font-size:.78rem;font-weight:700;color:var(--human);text-transform:uppercase;letter-spacing:.06em}
.st-human{margin-top:auto;padding-top:8px;font-size:.76rem;font-weight:600;color:var(--human);line-height:1.35}
.st-human::before{content:"";display:inline-block;width:8px;height:8px;border-radius:2px;background:var(--human);
margin-right:5px;vertical-align:0}
.hlane{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
.hstage{border-color:#f0abfc;background:var(--human-soft);border-top:3px solid var(--human)}
.hstage .human-tag{align-self:flex-start}
.feeds{font-size:.74rem;color:var(--human);font-weight:600;margin-top:auto}
.sub-h{margin-top:24px}
pre.code{background:#0f172a;color:#e2e8f0;border-radius:var(--r);padding:14px 16px;overflow-x:auto;font:0.86rem/1.6 var(--mono);margin:0 0 8px}
pre.code code{background:none;border:0;padding:0;color:inherit;overflow-wrap:normal;white-space:pre}
.code-note{font-size:.9rem;color:var(--muted)}
.stage-list{display:grid;grid-template-columns:1fr 1fr;gap:8px 16px}
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
.scope-note{background:var(--human-soft);border-left:3px solid var(--human);padding:8px 12px;border-radius:0 6px 6px 0}
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
.dl-list li{border:1px solid var(--line);border-radius:8px;padding:10px 14px;display:flex;flex-direction:column}
.dl-list a{font-weight:650;font-family:var(--mono);font-size:.88rem}
.dl-list span{font-size:.82rem;color:var(--muted)}
.limits{padding-left:18px;max-width:90ch}
.limits li{margin-bottom:8px}
.foot{max-width:1160px;margin:48px auto 0;padding:0 24px 32px;color:var(--muted);font-size:.875rem}
.foot p{border-top:1px solid var(--line);padding-top:24px}
@media (max-width:1080px){
 .flow{grid-template-columns:repeat(4,minmax(0,1fr))}
 .stage:nth-child(4)::after{display:none}
 .acc-layout{grid-template-columns:1fr}
 .acc-side{grid-template-columns:1fr 1fr}
 .use-grid{grid-template-columns:1fr 1fr}
}
@media (max-width:860px){
 .cards,.two-col,.takeaways,.fixes,.stage-list{grid-template-columns:1fr}
 .card-wide{grid-column:auto}
 .card-wide .cat-bars,.hbars.cat{grid-template-columns:1fr;grid-auto-flow:row;grid-template-rows:none}
 .stats{grid-template-columns:1fr 1fr}
 .stats>div:nth-child(3){border-left:0}
 .stats>div:nth-child(n+3){border-top:1px solid var(--line)}
 .hlane{grid-template-columns:1fr 1fr}
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
 .flow{grid-template-columns:1fr;gap:18px}
 .stage:not(:last-child)::after{display:block;right:auto;left:50%;top:auto;bottom:-14px;transform:translateX(-50%) rotate(135deg)}
 .card,.panel{padding:16px}
 .acc-table th,.acc-table td{padding:6px 4px;font-size:.8rem}
 .acc-table td.acc{min-width:0}
 .acc-table td.acc .bar{max-width:40px}
 .acc-table thead tr:first-child th{font-size:.74rem}
 .acc-table tbody th{font-size:.78rem;white-space:normal}
 .up,.down{margin-left:1px}
 .hlane{grid-template-columns:1fr}
 .acc-side{grid-template-columns:1fr}
 .spot,.dl-list,.mini-stats{grid-template-columns:1fr 1fr}
 .use-grid{grid-template-columns:1fr}
 .split{grid-template-columns:1fr 1fr;gap:12px}
 .cat-bars li{grid-template-columns:minmax(0,1fr) 72px 3em}
 .hbars.cat li,.hbars li{grid-template-columns:minmax(0,1fr) 72px 2.2em}
 .filters{display:grid;grid-template-columns:1fr 1fr}
 .f select{max-width:none;width:100%}
 .search input{max-width:none}
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
  var data=[];try{data=JSON.parse(document.getElementById('rows-data').textContent)}catch(e){}
  var bySlug={};data.forEach(function(r){bySlug[r.slug]=r});
  var q=document.getElementById('q'),sel=[].slice.call(document.querySelectorAll('select[data-filter]'));
  var shown=document.getElementById('shown'),empty=document.getElementById('empty');
  var rows=[].slice.call(document.querySelectorAll('tr.row'));
  function match(r){
    var t=(q&&q.value||'').trim().toLowerCase();
    if(t&&(r.name+' '+r.one_liner+' '+r.slug).toLowerCase().indexOf(t)<0)return false;
    for(var i=0;i<sel.length;i++){var k=sel[i].getAttribute('data-filter'),v=sel[i].value;
      if(v&&String(r[k])!==v)return false}
    return true}
  function apply(){var n=0;rows.forEach(function(tr){var r=bySlug[tr.getAttribute('data-slug')];
      var ok=!r||match(r);tr.hidden=!ok;var d=document.getElementById('d-'+tr.getAttribute('data-slug'));
      if(d)d.hidden=!ok;if(ok)n++});
    if(shown)shown.textContent=n;if(empty)empty.hidden=n>0}
  if(q)q.addEventListener('input',apply);sel.forEach(function(s){s.addEventListener('change',apply)});
  function toggle(slug,open){var tr=document.getElementById('app-'+slug),d=document.getElementById('d-'+slug);
    if(!tr||!d)return;var b=tr.querySelector('button.exp');var o=open===undefined?!d.classList.contains('open'):open;
    d.classList.toggle('open',o);if(b)b.setAttribute('aria-expanded',o?'true':'false')}
  rows.forEach(function(tr){var slug=tr.getAttribute('data-slug');
    var b=tr.querySelector('button.exp');if(b)b.addEventListener('click',function(e){e.stopPropagation();toggle(slug)});
    tr.addEventListener('click',function(e){if(e.target.closest('a,button,select,input'))return;toggle(slug)})});
  function fromUrl(){var m=(location.hash.match(/expand=([a-z0-9-]+)/)||location.search.match(/expand=([a-z0-9-]+)/));
    if(!m)return;toggle(m[1],true);var tr=document.getElementById('app-'+m[1]);
    if(tr)setTimeout(function(){tr.scrollIntoView({block:'start'})},0)}
  window.addEventListener('hashchange',fromUrl);fromUrl();
  var links=[].slice.call(document.querySelectorAll('.top nav a'));
  if('IntersectionObserver' in window){
    var map={};links.forEach(function(a){map[a.getAttribute('href').slice(1)]=a});
    var vis={};var io=new IntersectionObserver(function(es){es.forEach(function(e){vis[e.target.id]=e.isIntersecting?e.intersectionRatio:0});
      var best=null,bv=0;Object.keys(vis).forEach(function(k){if(vis[k]>bv){bv=vis[k];best=k}});
      if(best){links.forEach(function(a){a.classList.remove('active');a.removeAttribute('aria-current')});
        if(map[best]){map[best].classList.add('active');map[best].setAttribute('aria-current','true');
          var nav=map[best].closest('nav');if(nav){var l=map[best].offsetLeft-nav.clientWidth/2+map[best].clientWidth/2;nav.scrollLeft=l}}}},
      {rootMargin:'-64px 0px -55% 0px',threshold:[0,0.01,0.1,0.25,0.5]});
    document.querySelectorAll('main section[id]').forEach(function(s){io.observe(s)})}
})();
"""


if __name__ == "__main__":
    raise SystemExit(main())
