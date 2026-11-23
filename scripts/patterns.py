#!/usr/bin/env python3
"""Summarise patterns across a finished run (rows.jsonl + needs_human.jsonl).

Everything in the report is computed from the input files: distributions,
cross-tabs, friction factors and 4-6 headline patterns. Example apps are
picked from each pattern's matching rows (high confidence first, then id),
never named in this script.

Optional gold/holdout score JSONs (scripts/score_gold.py output) give each
field a reliability level. A field under 80% on either set is caveated, and
every pattern that leans on it carries that caveat.

Output is deterministic for the same inputs: no wall-clock timestamp, only
input fingerprints.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

CAVEAT_BELOW = 0.80
MODERATE_BELOW = 0.90

# (key in report, label, key in score_gold field_accuracy). composio_toolkit
# is scored separately as coverage_accuracy.
SCORED_FIELDS = (
    ("auth", "auth_methods / primary_auth", "auth"),
    ("access", "access", "access"),
    ("api", "api_types + has_public_docs", "api"),
    ("mcp_status", "mcp_status", "mcp_status"),
    ("verdict", "verdict", "verdict"),
    ("blocker", "blocker", "blocker"),
    ("api_breadth", "api_breadth", "api_breadth"),
    ("requires_end_user_admin", "requires_end_user_admin", "requires_end_user_admin"),
    ("composio_toolkit", "composio_toolkit", None),
)
FIELD_LABELS = {key: label for key, label, _ in SCORED_FIELDS}

GATED = ("gated_sales", "gated_partner")
CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}
NEEDS_HUMAN_REASONS = (
    "sales_or_partner_gate",
    "confidence_not_high",
    "conflicting_or_caveated_docs",
    "unknown_deciding_field",
    "browse_unresolved",
)
PREVIEW_TERMS = {
    "coming_soon": re.compile(r"\bcoming[\s-]+soon\b", re.I),
    "waitlist": re.compile(r"\bwait[\s-]?list(?:ed)?\b", re.I),
    "beta": re.compile(r"\bbeta\b", re.I),
    "preview": re.compile(r"\bpreview\b", re.I),
}
MCP_WORD = re.compile(r"\bmcp\b|model context protocol", re.I)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+")
AUTH_COMBOS = ("oauth2_only", "oauth2_and_api_key", "api_key_only", "other")

# Enum fallbacks when schema/app_row.schema.json is not readable.
DEFAULT_ENUMS: dict[str, list[str]] = {
    "category": [],
    "auth_methods": ["oauth2", "api_key", "basic", "jwt_service_account", "custom_other", "none", "unknown"],
    "primary_auth": ["oauth2", "api_key", "basic", "jwt_service_account", "custom_other", "none", "unknown"],
    "access": ["self_serve", "self_serve_paid", "gated_partner", "gated_sales", "no_public_api"],
    "api_types": ["rest", "graphql", "soap", "sdk_only", "webhook_only", "cli_only", "none"],
    "api_breadth": ["narrow", "moderate", "broad", "unknown"],
    "mcp_status": ["official", "community", "none"],
    "verdict": ["buildable_now", "buildable_with_approval", "not_buildable"],
    "blocker": [],
    "confidence": ["high", "medium", "low"],
    "composio_status": ["exists", "none", "unknown"],
}


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- loading


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for i, line in enumerate(handle, 1):
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            try:
                rows.append(json.loads(text))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{i}: invalid JSON: {exc}") from exc
    return rows


def load_optional_json(path: Path | None) -> dict | None:
    if path is None or not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_enums(schema_path: Path) -> dict[str, list[str]]:
    enums = {key: list(values) for key, values in DEFAULT_ENUMS.items()}
    if not schema_path.is_file():
        return enums
    props = json.loads(schema_path.read_text(encoding="utf-8")).get("properties", {})
    for key in enums:
        spec = props.get("composio_toolkit", {}).get("properties", {}).get("status", {}) if key == "composio_status" \
            else props.get(key, {})
        values = spec.get("enum") or spec.get("items", {}).get("enum")
        if values:
            enums[key] = list(values)
    return enums


def needs_human_index(records: list[dict]) -> dict[str, list[str]]:
    """slug -> reason ids, only for rows flagged needs_human."""
    index: dict[str, list[str]] = {}
    for record in records:
        if not record.get("needs_human"):
            continue
        ids = []
        for reason in record.get("reasons") or []:
            rid = reason.get("id") if isinstance(reason, dict) else reason
            if rid:
                ids.append(str(rid))
        index[str(record.get("slug"))] = ids
    return index


def file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def display_path(path: Path) -> str:
    """Repo- or cwd-relative path; never a machine-specific absolute path."""
    resolved = path.resolve()
    for base in (repo_root(), Path.cwd().resolve()):
        try:
            return resolved.relative_to(base).as_posix()
        except ValueError:
            continue
    return path.name


# ---------------------------------------------------------------- row helpers


def composio_status(row: dict) -> str:
    toolkit = row.get("composio_toolkit") or {}
    return str(toolkit.get("status") or "unknown")


def auth_combo(row: dict) -> str:
    methods = set(row.get("auth_methods") or [])
    if methods == {"oauth2"}:
        return "oauth2_only"
    if methods == {"api_key"}:
        return "api_key_only"
    if {"oauth2", "api_key"} <= methods:
        return "oauth2_and_api_key"
    return "other"


def preview_hits(notes: str | None) -> dict[str, Any]:
    """Which preview/coming-soon terms appear in notes, and whether any shares a sentence with MCP."""
    text = notes or ""
    terms = sorted(name for name, rx in PREVIEW_TERMS.items() if rx.search(text))
    mcp_sentence = any(
        MCP_WORD.search(sentence) and any(rx.search(sentence) for rx in PREVIEW_TERMS.values())
        for sentence in SENTENCE_SPLIT.split(text)
    )
    return {"terms": terms, "mcp_related": mcp_sentence}


def pct(count: int, total: int) -> float | None:
    return round(100.0 * count / total, 1) if total else None


def fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def stat(count: int, total: int) -> dict[str, Any]:
    return {"count": count, "total": total, "pct": pct(count, total)}


def ordered(values: Iterable[str], preferred: list[str]) -> list[str]:
    seen = set(values)
    return [v for v in preferred if v in seen] + sorted(seen - set(preferred))


def distribution(values: Iterable[str], order: list[str], total: int, keep_zero: bool = True) -> dict[str, dict]:
    counts = Counter(values)
    keys = order if keep_zero else []
    keys = keys + [k for k in ordered(counts, order) if k not in keys]
    return {key: stat(counts.get(key, 0), total) for key in keys}


def crosstab(
    rows: list[dict],
    row_key: Callable[[dict], str],
    col_key: Callable[[dict], str],
    row_order: list[str],
    col_order: list[str],
) -> dict[str, Any]:
    cells: dict[str, Counter] = {}
    for row in rows:
        cells.setdefault(row_key(row), Counter())[col_key(row)] += 1
    rkeys = ordered(cells, row_order)
    ckeys = ordered({c for counter in cells.values() for c in counter}, col_order)
    return {
        "rows": rkeys,
        "cols": ckeys,
        "counts": {r: {c: cells[r].get(c, 0) for c in ckeys} for r in rkeys},
        "totals": {r: sum(cells[r].values()) for r in rkeys},
    }


def pick_examples(rows: Iterable[dict], n: int = 3) -> list[dict[str, str]]:
    ranked = sorted(
        rows,
        key=lambda r: (CONFIDENCE_RANK.get(r.get("confidence"), 9), r.get("id") or 0, str(r.get("slug"))),
    )
    return [{"slug": r.get("slug"), "name": r.get("name")} for r in ranked[:n]]


def pick_grouped(groups: list[list[dict]], n: int = 3) -> list[dict[str, str]]:
    """Round-robin over groups so each slice is represented."""
    picks = [pick_examples(group, n) for group in groups]
    out: list[dict[str, str]] = []
    for i in range(n):
        for group in picks:
            if i < len(group) and len(out) < n and group[i] not in out:
                out.append(group[i])
    return out


# ---------------------------------------------------------------- scores


def field_reliability(gold: dict | None, holdout: dict | None) -> dict[str, dict]:
    def accuracy(report: dict | None, key: str | None) -> float | None:
        if report is None:
            return None
        summary = report.get("summary", {})
        entry = summary.get("coverage_accuracy") if key is None else summary.get("field_accuracy", {}).get(key)
        return None if not entry else entry.get("accuracy")

    out: dict[str, dict] = {}
    for key, label, score_key in SCORED_FIELDS:
        g, h = accuracy(gold, score_key), accuracy(holdout, score_key)
        known = [v for v in (g, h) if v is not None]
        if not known:
            level = "unscored"
        elif min(known) < CAVEAT_BELOW:
            level = "caveated"
        elif min(known) < MODERATE_BELOW:
            level = "moderate"
        else:
            level = "high"
        weak = [f"{name} {v:.0%}" for name, v in (("gold", g), ("holdout", h)) if v is not None and v < MODERATE_BELOW]
        out[key] = {
            "label": label,
            "gold": g,
            "holdout": h,
            "level": level,
            "caveated": level == "caveated",
            "weak_scores": weak,
        }
    return out


def score_summary(report: dict | None) -> dict | None:
    if report is None:
        return None
    summary = report.get("summary", {})
    return {
        "run_id": report.get("run_id"),
        "n": summary.get("n_gold"),
        "n_row_correct": summary.get("n_row_correct"),
        "row_accuracy": summary.get("row_accuracy"),
    }


def reliability_for(fields: list[str], reliability: dict[str, dict]) -> tuple[str, str | None]:
    """Worst level across the fields a pattern relies on, plus a caveat sentence."""
    levels = [reliability[f]["level"] for f in fields]
    if "caveated" in levels:
        level = "caveated"
    elif "moderate" in levels or "unscored" in levels:
        level = "moderate"
    else:
        level = "high"
    parts = []
    for field in fields:
        info = reliability[field]
        if info["level"] in ("caveated", "moderate") and info["weak_scores"]:
            parts.append(f"{info['label']} scored {' / '.join(info['weak_scores'])}")
    unscored = [reliability[f]["label"] for f in fields if reliability[f]["level"] == "unscored"]
    caveat = None
    if parts:
        caveat = "Lower-accuracy fields: " + "; ".join(parts) + "."
    if unscored:
        caveat = ((caveat + " ") if caveat else "") + "Not validated (no score file): " + ", ".join(unscored) + "."
    return level, caveat


# ---------------------------------------------------------------- analysis


def analyze(
    rows: list[dict],
    needs_human: dict[str, list[str]],
    reliability: dict[str, dict],
    enums: dict[str, list[str]],
) -> dict[str, Any]:
    total = len(rows)
    rows = sorted(rows, key=lambda r: (r.get("id") or 0, str(r.get("slug"))))
    nh = {str(r.get("slug")) for r in rows} & set(needs_human)
    is_nh = lambda r: str(r.get("slug")) in nh  # noqa: E731
    previews = {str(r.get("slug")): preview_hits(r.get("notes")) for r in rows}
    categories = ordered((r.get("category") for r in rows), enums["category"])

    dists = distributions(rows, total, nh, needs_human, previews, enums)
    tabs = crosstabs(rows, is_nh, enums, categories)
    hard = what_makes_an_app_hard(rows, is_nh, previews, reliability, categories)
    return {
        "distributions": dists,
        "crosstabs": {**tabs, "what_makes_an_app_hard": hard},
        "patterns": headline_patterns(rows, is_nh, needs_human, reliability, dists, tabs, hard),
    }


def distributions(rows, total, nh, needs_human, previews, enums) -> dict[str, Any]:
    method_counts = Counter(m for r in rows for m in set(r.get("auth_methods") or []))
    type_counts = Counter(t for r in rows for t in set(r.get("api_types") or []))
    preview_term_counts = Counter(t for p in previews.values() for t in p["terms"])
    return {
        "primary_auth": distribution((r.get("primary_auth") for r in rows), enums["primary_auth"], total),
        "auth_methods": {
            "methods": {m: stat(method_counts.get(m, 0), total) for m in ordered(method_counts, enums["auth_methods"])},
            "method_count": {
                "single": stat(sum(len(r.get("auth_methods") or []) == 1 for r in rows), total),
                "multi": stat(sum(len(r.get("auth_methods") or []) > 1 for r in rows), total),
            },
            "combos": distribution((auth_combo(r) for r in rows), list(AUTH_COMBOS), total),
            "note": "oauth2_and_api_key counts rows listing both, with or without further methods.",
        },
        "access": distribution((r.get("access") for r in rows), enums["access"], total),
        "api_types": {t: stat(type_counts.get(t, 0), total) for t in ordered(type_counts, enums["api_types"])},
        "has_public_docs": distribution((str(r.get("has_public_docs")).lower() for r in rows), ["true", "false"], total),
        "api_breadth": {
            "values": distribution((r.get("api_breadth") for r in rows), enums["api_breadth"], total),
            "caveated": True,
        },
        "mcp_status": {
            "values": distribution((r.get("mcp_status") for r in rows), enums["mcp_status"], total),
            "notes_preview_terms": {t: stat(preview_term_counts.get(t, 0), total) for t in PREVIEW_TERMS},
            "notes_any_preview_term": stat(sum(bool(p["terms"]) for p in previews.values()), total),
            "notes_preview_term_with_mcp": stat(sum(p["mcp_related"] for p in previews.values()), total),
            "note": "Note-derived counts: rows whose notes mention coming soon / waitlist / beta / preview "
            "(case-insensitive, whole word). The MCP count needs the term in a sentence that mentions MCP. "
            "These sit alongside the strict mcp_status enum, which has no preview value.",
        },
        "verdict": distribution((r.get("verdict") for r in rows), enums["verdict"], total, keep_zero=False),
        "blocker": distribution((r.get("blocker") for r in rows), enums["blocker"], total, keep_zero=False),
        "confidence": distribution((r.get("confidence") for r in rows), enums["confidence"], total),
        "requires_end_user_admin": {
            "values": distribution(
                (str(bool(r.get("requires_end_user_admin"))).lower() for r in rows), ["true", "false"], total
            ),
            "caveated": True,
        },
        "composio_toolkit_status": distribution((composio_status(r) for r in rows), enums["composio_status"], total,
                                                keep_zero=False),
        "needs_human": {
            "total": stat(len(nh), total),
            "by_reason": distribution((rid for slug in nh for rid in set(needs_human[slug])),
                                      list(NEEDS_HUMAN_REASONS), total),
        },
    }


def crosstabs(rows, is_nh, enums, categories) -> dict[str, Any]:
    by_category = {}
    for cat in categories:
        sub = [r for r in rows if r.get("category") == cat]
        n = len(sub)
        by_category[cat] = {
            "total": n,
            "buildable_now": stat(sum(r.get("verdict") == "buildable_now" for r in sub), n),
            "buildable_with_approval": stat(sum(r.get("verdict") == "buildable_with_approval" for r in sub), n),
            "official_mcp": stat(sum(r.get("mcp_status") == "official" for r in sub), n),
            "primary_auth_oauth2": stat(sum(r.get("primary_auth") == "oauth2" for r in sub), n),
            "composio_exists": stat(sum(composio_status(r) == "exists" for r in sub), n),
            "needs_human": stat(sum(is_nh(r) for r in sub), n),
        }

    nh_key = lambda r: "needs_human" if is_nh(r) else "no_human"  # noqa: E731
    comp_order = enums["composio_status"]
    composio = {
        "x_verdict": crosstab(rows, composio_status, lambda r: r.get("verdict"), comp_order, enums["verdict"]),
        "x_access": crosstab(rows, composio_status, lambda r: r.get("access"), comp_order, enums["access"]),
        "x_mcp_status": crosstab(rows, composio_status, lambda r: r.get("mcp_status"), comp_order, enums["mcp_status"]),
        "x_primary_auth": crosstab(rows, composio_status, lambda r: r.get("primary_auth"), comp_order,
                                   enums["primary_auth"]),
        "x_needs_human": crosstab(rows, composio_status, nh_key, comp_order, ["needs_human", "no_human"]),
    }
    for status in ("none", "exists"):
        sub = [r for r in rows if composio_status(r) == status]
        n = len(sub)
        composio[f"{'uncovered' if status == 'none' else 'covered'}_slice"] = {
            "total": n,
            "buildable_now": stat(sum(r.get("verdict") == "buildable_now" for r in sub), n),
            "official_mcp": stat(sum(r.get("mcp_status") == "official" for r in sub), n),
            "buildable_now_and_official_mcp": stat(
                sum(r.get("verdict") == "buildable_now" and r.get("mcp_status") == "official" for r in sub), n
            ),
            "needs_human": stat(sum(is_nh(r) for r in sub), n),
        }

    admin_key = lambda r: str(bool(r.get("requires_end_user_admin"))).lower()  # noqa: E731
    return {
        "by_category": by_category,
        "composio_vs_verdict": composio,
        "access_vs_verdict_and_blocker": {
            "x_verdict": crosstab(rows, lambda r: r.get("access"), lambda r: r.get("verdict"), enums["access"],
                                  enums["verdict"]),
            "x_blocker": crosstab(rows, lambda r: r.get("access"), lambda r: r.get("blocker"), enums["access"],
                                  enums["blocker"]),
        },
        "auth_vs_access_and_admin": {
            "x_access": crosstab(rows, lambda r: r.get("primary_auth"), lambda r: r.get("access"),
                                 enums["primary_auth"], enums["access"]),
            "x_verdict": crosstab(rows, lambda r: r.get("primary_auth"), lambda r: r.get("verdict"),
                                  enums["primary_auth"], enums["verdict"]),
            "x_requires_end_user_admin": {
                **crosstab(rows, lambda r: r.get("primary_auth"), admin_key, enums["primary_auth"], ["true", "false"]),
                "caveated": True,
            },
        },
    }


def what_makes_an_app_hard(rows, is_nh, previews, reliability, categories) -> dict[str, Any]:
    total = len(rows)
    gated = [r for r in rows if r.get("access") in GATED]
    open_rows = [r for r in rows if r.get("access") not in GATED]
    oauth_only = [r for r in rows if auth_combo(r) == "oauth2_only"]
    not_high = [r for r in rows if r.get("confidence") != "high"]
    preview_rows = [r for r in rows if previews[str(r.get("slug"))]["terms"]]
    admin = [r for r in rows if r.get("requires_end_user_admin")]

    nh_by_cat = []
    for cat in categories:
        sub = [r for r in rows if r.get("category") == cat]
        nh_by_cat.append({"category": cat, **stat(sum(is_nh(r) for r in sub), len(sub))})
    nh_by_cat.sort(key=lambda e: (-(e["pct"] or 0), e["category"]))
    nh_by_cov = {}
    for status in ordered((composio_status(r) for r in rows), ["exists", "none", "unknown"]):
        sub = [r for r in rows if composio_status(r) == status]
        nh_by_cov[status] = stat(sum(is_nh(r) for r in sub), len(sub))

    admin_info = reliability["requires_end_user_admin"]
    return {
        "gating": {
            "gated": stat(len(gated), total),
            "by_access": {a: stat(sum(r.get("access") == a for r in rows), total) for a in GATED},
            "gated_buildable_with_approval": stat(
                sum(r.get("verdict") == "buildable_with_approval" for r in gated), len(gated)
            ),
            "non_gated_buildable_now": stat(sum(r.get("verdict") == "buildable_now" for r in open_rows),
                                            len(open_rows)),
        },
        "oauth2_only": {
            "all": stat(len(oauth_only), total),
            "among_gated": stat(sum(auth_combo(r) == "oauth2_only" for r in gated), len(gated)),
            "among_non_gated": stat(sum(auth_combo(r) == "oauth2_only" for r in open_rows), len(open_rows)),
            "oauth2_primary_among_gated": stat(sum(r.get("primary_auth") == "oauth2" for r in gated), len(gated)),
            "oauth2_primary_among_non_gated": stat(sum(r.get("primary_auth") == "oauth2" for r in open_rows),
                                                   len(open_rows)),
        },
        "confidence_not_high": {
            "all": stat(len(not_high), total),
            "medium": stat(sum(r.get("confidence") == "medium" for r in rows), total),
            "low": stat(sum(r.get("confidence") == "low" for r in rows), total),
            "among_gated": stat(sum(r.get("confidence") != "high" for r in gated), len(gated)),
            "among_non_gated": stat(sum(r.get("confidence") != "high" for r in open_rows), len(open_rows)),
        },
        "preview_or_coming_soon_notes": {
            "all": stat(len(preview_rows), total),
            "mcp_related": stat(sum(previews[str(r.get("slug"))]["mcp_related"] for r in rows), total),
            "among_not_high_confidence": stat(
                sum(bool(previews[str(r.get("slug"))]["terms"]) for r in not_high), len(not_high)
            ),
        },
        "needs_human_concentration": {"by_category": nh_by_cat, "by_composio_status": nh_by_cov},
        "requires_end_user_admin": {
            "all": stat(len(admin), total),
            "among_gated": stat(sum(bool(r.get("requires_end_user_admin")) for r in gated), len(gated)),
            "among_oauth2_primary": stat(
                sum(bool(r.get("requires_end_user_admin")) for r in rows if r.get("primary_auth") == "oauth2"),
                sum(r.get("primary_auth") == "oauth2" for r in rows),
            ),
            "caveated": True,
            "caveat": "requires_end_user_admin is low-accuracy"
            + (f" ({', '.join(admin_info['weak_scores'])})" if admin_info["weak_scores"] else "")
            + "; treat these counts as indicative only.",
        },
    }


def _pattern(pid, title, headline, count, total, supporting, examples, fields, reliability) -> dict[str, Any]:
    level, caveat = reliability_for(fields, reliability)
    return {
        "id": pid,
        "title": title,
        "headline": headline,
        "count": count,
        "total": total,
        "pct": pct(count, total),
        "supporting_stats": supporting,
        "examples": examples,
        "fields": [FIELD_LABELS[f] for f in fields],
        "reliability": level,
        "caveat": caveat,
    }


def headline_patterns(rows, is_nh, needs_human, reliability, dists, tabs, hard) -> list[dict[str, Any]]:
    total = len(rows)
    patterns: list[dict[str, Any]] = []

    # 1. Buildable today vs gated.
    now = [r for r in rows if r.get("verdict") == "buildable_now"]
    approval = [r for r in rows if r.get("verdict") == "buildable_with_approval"]
    sales = [r for r in rows if r.get("access") == "gated_sales"]
    partner = [r for r in rows if r.get("access") == "gated_partner"]
    gating = hard["gating"]
    patterns.append(_pattern(
        "buildable_vs_gated",
        "Buildable today vs gated",
        f"{len(now)} of {total} apps ({fmt_pct(pct(len(now), total))}) are buildable now without vendor approval. "
        f"The other {len(approval)} need vendor approval: {len(sales)} gated by sales and {len(partner)} by a "
        f"partner program, and {fmt_pct(gating['gated_buildable_with_approval']['pct'])} of gated apps land on "
        "buildable_with_approval.",
        len(now), total,
        {
            "buildable_now": len(now),
            "buildable_with_approval": len(approval),
            "gated_sales": len(sales),
            "gated_partner": len(partner),
            "self_serve_paid": sum(r.get("access") == "self_serve_paid" for r in rows),
            "gated_buildable_with_approval_pct": gating["gated_buildable_with_approval"]["pct"],
        },
        pick_grouped([now, sales, partner]),
        ["access", "verdict"], reliability,
    ))

    # 2. Official MCP adoption and the uncovered slice.
    official = [r for r in rows if r.get("mcp_status") == "official"]
    uncovered = [r for r in rows if composio_status(r) == "none"]
    unc_now = [r for r in uncovered if r.get("verdict") == "buildable_now"]
    unc_mcp = [r for r in uncovered if r.get("mcp_status") == "official"]
    unc_both = [r for r in unc_now if r.get("mcp_status") == "official"]
    patterns.append(_pattern(
        "official_mcp_and_uncovered",
        "Official MCP adoption and Composio's uncovered opportunity",
        f"{len(official)} of {total} apps ({fmt_pct(pct(len(official), total))}) ship an official MCP server. "
        f"Of the {len(uncovered)} apps with no Composio toolkit, {len(unc_now)} are buildable now, "
        f"{len(unc_mcp)} have an official MCP, and {len(unc_both)} are both.",
        len(unc_both), len(uncovered),
        {
            "official_mcp": len(official),
            "community_mcp": sum(r.get("mcp_status") == "community" for r in rows),
            "no_mcp": sum(r.get("mcp_status") == "none" for r in rows),
            "composio_none": len(uncovered),
            "composio_none_buildable_now": len(unc_now),
            "composio_none_official_mcp": len(unc_mcp),
            "composio_none_buildable_now_and_official_mcp": len(unc_both),
            "notes_preview_term_with_mcp": dists["mcp_status"]["notes_preview_term_with_mcp"]["count"],
        },
        pick_examples(unc_both),
        ["mcp_status", "verdict", "composio_toolkit"], reliability,
    ))

    # 3. Category unevenness.
    by_cat = tabs["by_category"]
    if by_cat:
        def score(cat: str) -> tuple:
            c = by_cat[cat]
            return (c["buildable_now"]["pct"] or 0, c["official_mcp"]["pct"] or 0,
                    -(c["needs_human"]["pct"] or 0), c["composio_exists"]["pct"] or 0)

        def extremes(metric: str, highest: bool) -> list[str]:
            values = {c: by_cat[c][metric]["pct"] or 0 for c in by_cat}
            target = max(values.values()) if highest else min(values.values())
            return sorted(c for c, v in values.items() if v == target)

        # Ties fall to alphabetical order: sort by name first, then stable-sort by score.
        ranked = sorted(sorted(by_cat), key=score, reverse=True)
        best, worst = ranked[0], ranked[-1]
        lowest_cov = extremes("composio_exists", highest=False)
        highest_nh = extremes("needs_human", highest=True)
        gated_rows = [r for r in rows if r.get("verdict") != "buildable_now"]
        cov_now = stat(sum(composio_status(r) == "exists" for r in now), len(now))
        cov_gated = stat(sum(composio_status(r) == "exists" for r in gated_rows), len(gated_rows))
        b, w = by_cat[best], by_cat[worst]
        worst_rows = [r for r in rows if r.get("category") == worst]
        patterns.append(_pattern(
            "category_unevenness",
            "Category and coverage unevenness",
            f"{best} is the strongest category ({fmt_pct(b['buildable_now']['pct'])} buildable now, "
            f"{fmt_pct(b['official_mcp']['pct'])} official MCP, {fmt_pct(b['needs_human']['pct'])} needs_human); "
            f"{worst} is the hardest ({w['buildable_now']['count']} of {w['total']} buildable now, "
            f"{fmt_pct(w['composio_exists']['pct'])} Composio coverage). "
            f"Lowest Composio coverage: {', '.join(lowest_cov)}; most needs_human: {', '.join(highest_nh)}. "
            f"Composio covers {fmt_pct(cov_now['pct'])} of buildable-now apps vs {fmt_pct(cov_gated['pct'])} "
            "of the rest.",
            w["buildable_now"]["count"], w["total"],
            {
                "strongest_category": best,
                "strongest": b,
                "hardest_category": worst,
                "hardest": w,
                "lowest_composio_coverage_categories": lowest_cov,
                "lowest_composio_coverage_pct": by_cat[lowest_cov[0]]["composio_exists"]["pct"],
                "highest_needs_human_categories": highest_nh,
                "highest_needs_human_pct": by_cat[highest_nh[0]]["needs_human"]["pct"],
                "composio_exists_on_buildable_now": cov_now,
                "composio_exists_on_not_buildable_now": cov_gated,
            },
            pick_examples([r for r in worst_rows if r.get("verdict") != "buildable_now"] or worst_rows),
            ["verdict", "mcp_status", "composio_toolkit"], reliability,
        ))

    # 4. Auth landscape.
    combos = dists["auth_methods"]["combos"]
    oauth_primary = [r for r in rows if r.get("primary_auth") == "oauth2"]
    both = [r for r in rows if auth_combo(r) == "oauth2_and_api_key"]
    patterns.append(_pattern(
        "auth_landscape",
        "Auth landscape",
        f"{len(oauth_primary)} of {total} apps ({fmt_pct(pct(len(oauth_primary), total))}) use OAuth2 as primary "
        f"auth. {combos['oauth2_and_api_key']['count']} offer both OAuth2 and API keys, "
        f"{combos['oauth2_only']['count']} are OAuth2-only and {combos['api_key_only']['count']} are API-key-only.",
        len(oauth_primary), total,
        {
            "primary_auth": {k: v["count"] for k, v in dists["primary_auth"].items() if v["count"]},
            "oauth2_and_api_key": combos["oauth2_and_api_key"]["count"],
            "oauth2_only": combos["oauth2_only"]["count"],
            "api_key_only": combos["api_key_only"]["count"],
            "other_combo": combos["other"]["count"],
            "multi_method": dists["auth_methods"]["method_count"]["multi"]["count"],
        },
        pick_examples(both),
        ["auth"], reliability,
    ))

    # 5. Human-in-the-loop.
    nh_rows = [r for r in rows if is_nh(r)]
    reasons = dists["needs_human"]["by_reason"]
    top = sorted(((rid, s["count"]) for rid, s in reasons.items() if s["count"]), key=lambda x: (-x[1], x[0]))
    unc_nh = tabs["composio_vs_verdict"]["uncovered_slice"]["needs_human"]
    cov_nh = tabs["composio_vs_verdict"]["covered_slice"]["needs_human"]
    top_text = ", ".join(f"{rid} ({count})" for rid, count in top[:3]) or "none"
    patterns.append(_pattern(
        "needs_human",
        "Human-in-the-loop necessity",
        f"{len(nh_rows)} of {total} rows ({fmt_pct(pct(len(nh_rows), total))}) are flagged needs_human; top reasons: "
        f"{top_text}. The rate is {fmt_pct(unc_nh['pct'])} on apps with no Composio toolkit vs "
        f"{fmt_pct(cov_nh['pct'])} on covered apps.",
        len(nh_rows), total,
        {
            "by_reason": {rid: count for rid, count in top},
            "uncovered_needs_human": unc_nh,
            "covered_needs_human": cov_nh,
        },
        pick_examples(nh_rows),
        ["access", "verdict"], reliability,
    ))

    # 6. End-user admin and API breadth.
    admin = [r for r in rows if r.get("requires_end_user_admin")]
    breadth = dists["api_breadth"]["values"]
    admin_hard = hard["requires_end_user_admin"]
    level, _ = reliability_for(["requires_end_user_admin", "api_breadth"], reliability)
    trust = {
        "caveated": "These fields scored below the caveat threshold on validation, so read them as rough signals.",
        "moderate": "These fields are only moderately reliable on validation.",
        "high": "Both fields scored well on validation.",
    }[level]
    patterns.append(_pattern(
        "admin_and_breadth",
        "End-user admin and API breadth friction",
        f"{len(admin)} of {total} apps ({fmt_pct(pct(len(admin), total))}) are marked as needing a customer admin "
        f"to approve install ({admin_hard['among_gated']['count']} of {admin_hard['among_gated']['total']} gated "
        f"apps), and {breadth.get('broad', {}).get('count', 0)} have a broad API. {trust}",
        len(admin), total,
        {
            "requires_end_user_admin": len(admin),
            "admin_among_gated": admin_hard["among_gated"],
            "admin_among_oauth2_primary": admin_hard["among_oauth2_primary"],
            "api_breadth": {k: v["count"] for k, v in breadth.items()},
        },
        pick_examples(admin),
        ["requires_end_user_admin", "api_breadth"], reliability,
    ))
    return patterns


def build_report(
    run_dir: Path,
    gold_path: Path | None,
    holdout_path: Path | None,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    rows_path = run_dir / "rows.jsonl"
    nh_path = run_dir / "needs_human.jsonl"
    rows = load_jsonl(rows_path)
    nh_records = load_jsonl(nh_path) if nh_path.is_file() else []
    gold = load_optional_json(gold_path)
    holdout = load_optional_json(holdout_path)
    enums = load_enums(schema_path or repo_root() / "schema" / "app_row.schema.json")
    reliability = field_reliability(gold, holdout)
    analysis = analyze(rows, needs_human_index(nh_records), reliability, enums)
    inputs = {"rows": {"path": display_path(rows_path), "sha256": file_sha256(rows_path)}}
    if nh_path.is_file():
        inputs["needs_human"] = {"path": display_path(nh_path), "sha256": file_sha256(nh_path)}
    for name, path, report in (("gold_score", gold_path, gold), ("holdout_score", holdout_path, holdout)):
        if report is not None and path is not None:
            inputs[name] = {"path": display_path(path), "sha256": file_sha256(path)}
    return {
        "meta": {
            "total_apps": len(rows),
            "run_dir": display_path(run_dir),
            "generated_by": "scripts/patterns.py",
            "inputs": inputs,
            "thresholds": {"caveated_below": CAVEAT_BELOW, "moderate_below": MODERATE_BELOW},
        },
        "validation": {
            "gold": score_summary(gold),
            "holdout": score_summary(holdout),
            "fields": reliability,
            "caveated_fields": [info["label"] for info in reliability.values() if info["caveated"]],
        },
        "patterns": analysis["patterns"],
        "distributions": analysis["distributions"],
        "crosstabs": analysis["crosstabs"],
    }


# ---------------------------------------------------------------- markdown


def _table(headers: list[str], body: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in body]
    return lines + [""]


def _sp(s: dict) -> str:
    return f"{s['count']} ({fmt_pct(s['pct'])})"


def _dist_table(title: str, dist: dict[str, dict]) -> list[str]:
    return [f"#### {title}", ""] + _table(["Value", "Count", "%"],
                                          [[f"`{k}`", v["count"], fmt_pct(v["pct"])] for k, v in dist.items()])


def _crosstab_table(title: str, tab: dict) -> list[str]:
    headers = [""] + [f"`{c}`" for c in tab["cols"]] + ["Total"]
    body = [[f"`{r}`"] + [tab["counts"][r][c] for c in tab["cols"]] + [tab["totals"][r]] for r in tab["rows"]]
    return [f"#### {title}", ""] + _table(headers, body)


def _acc(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def render_markdown(report: dict[str, Any]) -> str:
    meta, val, d, x = report["meta"], report["validation"], report["distributions"], report["crosstabs"]
    lines = [
        f"# Patterns across {meta['total_apps']} apps (`{meta['run_dir']}`)",
        "",
        "Generated by `scripts/patterns.py` from `rows.jsonl`, `needs_human.jsonl` and the gold/holdout "
        "score files. Every number below is computed from those inputs; examples are picked from each "
        "pattern's matching rows (high confidence first, then id).",
        "",
        "## Headline patterns",
        "",
    ]
    for i, p in enumerate(report["patterns"], 1):
        examples = ", ".join(f"{e['name']} (`{e['slug']}`)" for e in p["examples"]) or "none"
        lines += [
            f"### {i}. {p['title']}",
            "",
            f"**{p['headline']}**",
            "",
            f"- Count: {p['count']} / {p['total']} ({fmt_pct(p['pct'])})",
            f"- Examples: {examples}",
            f"- Reliability: **{p['reliability']}** (fields: {', '.join(p['fields'])})",
        ]
        if p["caveat"]:
            lines.append(f"- Caveat: {p['caveat']}")
        lines.append("")

    lines += ["## Distributions", ""]
    lines += _dist_table("primary_auth", d["primary_auth"])
    lines += _dist_table("auth_methods (any row listing the method)", d["auth_methods"]["methods"])
    lines += _dist_table("auth_methods: single vs multi", d["auth_methods"]["method_count"])
    lines += _dist_table("auth_methods: combination", d["auth_methods"]["combos"])
    lines += [f"_{d['auth_methods']['note']}_", ""]
    lines += _dist_table("access", d["access"])
    lines += _dist_table("api_types (any row listing the type)", d["api_types"])
    lines += _dist_table("has_public_docs", d["has_public_docs"])
    lines += _dist_table("api_breadth (caveated)", d["api_breadth"]["values"])
    lines += _dist_table("mcp_status", d["mcp_status"]["values"])
    notes_dist = {**d["mcp_status"]["notes_preview_terms"],
                  "any of these": d["mcp_status"]["notes_any_preview_term"],
                  "any, in a sentence mentioning MCP": d["mcp_status"]["notes_preview_term_with_mcp"]}
    lines += _dist_table("Notes mentioning preview / coming-soon terms", notes_dist)
    lines += [f"_{d['mcp_status']['note']}_", ""]
    lines += _dist_table("verdict", d["verdict"])
    lines += _dist_table("blocker", d["blocker"])
    lines += _dist_table("confidence", d["confidence"])
    lines += _dist_table("requires_end_user_admin (caveated)", d["requires_end_user_admin"]["values"])
    lines += _dist_table("composio_toolkit.status", d["composio_toolkit_status"])
    lines += [f"#### needs_human: {_sp(d['needs_human']['total'])}", ""]
    lines += _table(["Reason", "Count", "%"],
                    [[f"`{k}`", v["count"], fmt_pct(v["pct"])] for k, v in d["needs_human"]["by_reason"].items()])
    lines += ["_A row can have several reasons._", ""]

    lines += ["## Cross-tabs", "", "### By category", ""]
    cat_cols = ["buildable_now", "buildable_with_approval", "official_mcp", "primary_auth_oauth2",
                "composio_exists", "needs_human"]
    lines += _table(["Category", "Apps"] + [f"`{c}`" for c in cat_cols],
                    [[f"`{cat}`", v["total"]] + [_sp(v[c]) for c in cat_cols] for cat, v in x["by_category"].items()])

    comp = x["composio_vs_verdict"]
    lines += ["### Composio coverage vs verdict / access / MCP / auth / needs_human", ""]
    for key, title in (("x_verdict", "verdict"), ("x_access", "access"), ("x_mcp_status", "mcp_status"),
                       ("x_primary_auth", "primary_auth"), ("x_needs_human", "needs_human")):
        lines += _crosstab_table(f"composio_toolkit.status x {title}", comp[key])
    slice_cols = ["buildable_now", "official_mcp", "buildable_now_and_official_mcp", "needs_human"]
    lines += ["#### Uncovered vs covered slice", ""]
    lines += _table(["Slice", "Apps"] + [f"`{c}`" for c in slice_cols],
                    [[name, comp[key]["total"]] + [_sp(comp[key][c]) for c in slice_cols]
                     for name, key in (("composio: none", "uncovered_slice"), ("composio: exists", "covered_slice"))])

    lines += ["### Access vs verdict and blocker", ""]
    lines += _crosstab_table("access x verdict", x["access_vs_verdict_and_blocker"]["x_verdict"])
    lines += _crosstab_table("access x blocker", x["access_vs_verdict_and_blocker"]["x_blocker"])

    auth = x["auth_vs_access_and_admin"]
    lines += ["### Auth breakdown", ""]
    lines += _crosstab_table("primary_auth x access", auth["x_access"])
    lines += _crosstab_table("primary_auth x verdict", auth["x_verdict"])
    lines += _crosstab_table("primary_auth x requires_end_user_admin (caveated)", auth["x_requires_end_user_admin"])

    hard = x["what_makes_an_app_hard"]
    g, o, c, pv, a = (hard["gating"], hard["oauth2_only"], hard["confidence_not_high"],
                      hard["preview_or_coming_soon_notes"], hard["requires_end_user_admin"])
    lines += ["### What makes an app hard", ""]
    lines += _table(["Factor", "Value"], [
        ["Gated (`gated_sales` + `gated_partner`)", _sp(g["gated"])],
        ["... of which `gated_sales`", _sp(g["by_access"]["gated_sales"])],
        ["... of which `gated_partner`", _sp(g["by_access"]["gated_partner"])],
        ["Gated apps that are `buildable_with_approval`", _sp(g["gated_buildable_with_approval"])],
        ["Non-gated apps that are `buildable_now`", _sp(g["non_gated_buildable_now"])],
        ["OAuth2-only (`auth_methods == [oauth2]`)", _sp(o["all"])],
        ["OAuth2-only among gated", _sp(o["among_gated"])],
        ["OAuth2-only among non-gated", _sp(o["among_non_gated"])],
        ["OAuth2 primary among gated", _sp(o["oauth2_primary_among_gated"])],
        ["OAuth2 primary among non-gated", _sp(o["oauth2_primary_among_non_gated"])],
        ["Confidence not high (medium + low)", _sp(c["all"])],
        ["... medium", _sp(c["medium"])],
        ["... low", _sp(c["low"])],
        ["Confidence not high among gated", _sp(c["among_gated"])],
        ["Confidence not high among non-gated", _sp(c["among_non_gated"])],
        ["Notes with preview / beta / coming-soon / waitlist", _sp(pv["all"])],
        ["... in a sentence mentioning MCP", _sp(pv["mcp_related"])],
        ["... among not-high-confidence rows", _sp(pv["among_not_high_confidence"])],
        ["`requires_end_user_admin` (caveated)", _sp(a["all"])],
        ["... among gated (caveated)", _sp(a["among_gated"])],
        ["... among OAuth2 primary (caveated)", _sp(a["among_oauth2_primary"])],
    ])
    lines += [f"_{a['caveat']}_", "", "#### needs_human concentration", ""]
    lines += _table(["Category", "needs_human"],
                    [[f"`{e['category']}`", f"{e['count']} / {e['total']} ({fmt_pct(e['pct'])})"]
                     for e in hard["needs_human_concentration"]["by_category"]])
    lines += _table(["Composio status", "needs_human"],
                    [[f"`{k}`", f"{v['count']} / {v['total']} ({fmt_pct(v['pct'])})"]
                     for k, v in hard["needs_human_concentration"]["by_composio_status"].items()])

    lines += ["## Validation & field caveats", ""]
    for name in ("gold", "holdout"):
        s = val[name]
        if s:
            lines.append(f"- {name.capitalize()} row accuracy (`{s['run_id']}`): {s['n_row_correct']}/{s['n']} "
                         f"({_acc(s['row_accuracy'])})")
        else:
            lines.append(f"- {name.capitalize()} score file not supplied.")
    lines += [
        "",
        f"Caveated = below {CAVEAT_BELOW:.0%} on gold or holdout; moderate = below {MODERATE_BELOW:.0%}.",
        "",
    ]
    lines += _table(["Field", "Gold", "Holdout", "Reliability"],
                    [[f + ("" if not i["caveated"] else " ⚠"), _acc(i["gold"]), _acc(i["holdout"]), i["level"]]
                     for f, i in ((i["label"], i) for i in val["fields"].values())])
    if val["caveated_fields"]:
        lines += ["Caveated fields: " + ", ".join(f"`{f}`" for f in val["caveated_fields"]) + ".", ""]
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------- cli


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute distributions, cross-tabs and headline patterns for a run")
    parser.add_argument("--run-dir", type=Path, default=Path("data/runs/pass3"),
                        help="Run directory with rows.jsonl (and needs_human.jsonl)")
    parser.add_argument("--out-dir", type=Path, default=Path("data/patterns"),
                        help="Directory for patterns.json and patterns.md")
    parser.add_argument("--gold-score", type=Path, default=Path("data/scores/pass3.json"),
                        help="score_gold.py JSON for the gold sample (skipped if missing)")
    parser.add_argument("--holdout-score", type=Path, default=Path("data/scores/holdout-pass3.json"),
                        help="score_gold.py JSON for the holdout sample (skipped if missing)")
    return parser.parse_args(argv)


def _resolve(path: Path) -> Path:
    if path.is_absolute():
        return path
    for base in (Path.cwd(), repo_root()):
        if (base / path).exists():
            return (base / path).resolve()
    return (Path.cwd() / path).resolve()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_dir = _resolve(args.run_dir)
    if not (run_dir / "rows.jsonl").is_file():
        raise SystemExit(f"error: {display_path(run_dir)}/rows.jsonl not found")
    report = build_report(run_dir, _resolve(args.gold_score), _resolve(args.holdout_score))
    out_dir = args.out_dir if args.out_dir.is_absolute() else (Path.cwd() / args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path, md_path = out_dir / "patterns.json", out_dir / "patterns.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"{len(report['patterns'])} patterns over {report['meta']['total_apps']} apps -> "
          f"{display_path(json_path)} {display_path(md_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
