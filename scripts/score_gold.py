#!/usr/bin/env python3
"""Score an agent run against frozen gold labels (schema/RUBRIC.md).

Headline metric: row accuracy on the gold sample (all critical units right).
Minor fields are reported in per-field accuracy but do not fail a row.
composio_toolkit is a separate coverage-lookup score, not part of row accuracy.

Gold files may include gold-only keys that are stripped before schema checks:
label_notes, acceptable (per-field alternate values), acceptable_alternatives
(full-field overlays for coupled judgements).

This script does not fetch pages and must not be pointed at a live research
cache for labelling. Evidence scoring checks that each critical claim has at
least one well-formed URL. Whether that URL actually supports the claim is
left for human review and called out in the report.

"Gated, with evidence" is a correct answer: a matching gated_* / no_public_api
access value plus required evidence URLs counts, even if credentials were
never obtained.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

GOLD_ONLY_KEYS = ("label_notes", "acceptable", "acceptable_alternatives")
CRITICAL_EVIDENCE = ("auth", "access", "api_surface", "mcp", "verdict")
CRITICAL_UNITS = ("auth", "access", "api", "mcp_status", "verdict", "blocker", "evidence")
MINOR_FIELDS = ("one_liner", "api_breadth", "docs_url", "mcp_url", "requires_end_user_admin")
IDENTITY_FIELDS = ("id", "slug", "name", "category")

# How each unit/field is named in reports.
UNIT_LABELS = {
    "auth": "auth_methods / primary_auth",
    "access": "access",
    "api": "api_types + has_public_docs",
    "mcp_status": "mcp_status",
    "verdict": "verdict",
    "blocker": "blocker",
    "evidence": "evidence (critical claims)",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def strip_gold_only(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in GOLD_ONLY_KEYS}


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


def load_gold(path: Path) -> dict[str, dict]:
    by_slug: dict[str, dict] = {}
    for row in load_jsonl(path):
        slug = row.get("slug")
        if not slug:
            raise SystemExit(f"{path}: a gold row is missing slug")
        if slug in by_slug:
            raise SystemExit(f"{path}: duplicate gold slug {slug}")
        by_slug[slug] = row
    return by_slug


def load_run_rows(run_dir: Path) -> dict[str, dict]:
    rows_path = run_dir / "rows.jsonl"
    if not rows_path.is_file():
        raise SystemExit(f"run dir {run_dir} has no rows.jsonl")
    by_slug: dict[str, dict] = {}
    for row in load_jsonl(rows_path):
        slug = row.get("slug")
        if slug:
            by_slug[str(slug)] = row
    return by_slug


def as_set(value: Any) -> set[Any] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return set(value)
    return {value}


def _tokens(text: str) -> set[str]:
    return {t for t in re.sub(r"[^a-z0-9]+", " ", text.lower()).split() if t}


def is_http_url(url: Any) -> bool:
    if not isinstance(url, str) or " " in url:
        return False
    parts = urlsplit(url)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def candidates_for(field: str, gold: dict) -> list[Any]:
    """Gold value plus any per-field acceptable alternates."""
    values = [gold.get(field)]
    extra = (gold.get("acceptable") or {}).get(field)
    if extra is None:
        return values
    extras = extra if isinstance(extra, list) else [extra]
    for item in extras:
        if item not in values:
            values.append(item)
    return values


def values_match(field: str, agent_val: Any, gold: dict) -> bool:
    options = candidates_for(field, gold)
    if field in ("auth_methods", "api_types", "composio_toolkit.sibling_slugs"):
        agent_set = as_set(agent_val)
        return any(agent_set == as_set(option) for option in options)
    return any(agent_val == option for option in options)


def one_liner_match(agent_val: Any, gold: dict) -> bool:
    """Wording need not match; it must be about the right product and long enough."""
    if not isinstance(agent_val, str) or len(agent_val.strip()) < 20:
        return False
    agent_tokens = _tokens(agent_val)
    product = _tokens(str(gold.get("name") or "")) | {t for t in str(gold.get("slug") or "").split("-") if len(t) > 2}
    if product and agent_tokens & product:
        return True
    gold_tokens = _tokens(str(gold.get("one_liner") or ""))
    return len(agent_tokens & gold_tokens) >= 3


def docs_url_match(agent_val: Any, gold: dict) -> bool:
    if not gold.get("has_public_docs"):
        return agent_val in (None, "")
    # When gold has public docs, any official-looking http(s) entry point is fine.
    return is_http_url(agent_val)


def mcp_url_applies(gold: dict) -> bool:
    return gold.get("mcp_status") != "none"


def mcp_url_match(agent_val: Any, gold: dict) -> bool:
    if not mcp_url_applies(gold):
        return True
    return is_http_url(agent_val)


def admin_flag_scored(gold: dict) -> bool:
    """Score the flag unless gold notes mark it unknown/unspecified."""
    notes = f"{gold.get('notes') or ''} {gold.get('label_notes') or ''}".lower()
    marked = "requires_end_user_admin" in notes or "end_user_admin" in notes
    unsure = "unknown" in notes or "unspecified" in notes
    return not (marked and unsure)


def requires_admin_match(agent_val: Any, gold: dict) -> bool:
    if not admin_flag_scored(gold):
        return True
    return values_match("requires_end_user_admin", agent_val, gold)


def evidence_problems(row: dict | None) -> list[str]:
    if not row:
        return ["row missing"]
    evidence = row.get("evidence") or {}
    problems: list[str] = []
    for key in CRITICAL_EVIDENCE:
        items = evidence.get(key) or []
        urls = [item.get("url") for item in items if isinstance(item, dict)]
        if not any(is_http_url(url) for url in urls):
            problems.append(f"{key}: no http(s) URL")
    return problems


def gold_views(gold: dict) -> list[dict]:
    views = [gold]
    for overlay in gold.get("acceptable_alternatives") or []:
        if not isinstance(overlay, dict):
            continue
        merged = dict(gold)
        merged.update(overlay)
        # Keep acceptable from overlay if provided, else inherit.
        views.append(merged)
    return views


def unit_match(unit: str, agent: dict, gold: dict) -> tuple[bool, dict[str, Any]]:
    if unit == "auth":
        methods_ok = values_match("auth_methods", agent.get("auth_methods"), gold)
        primary_ok = values_match("primary_auth", agent.get("primary_auth"), gold)
        ok = methods_ok and primary_ok
        return ok, {
            "gold": {"auth_methods": gold.get("auth_methods"), "primary_auth": gold.get("primary_auth")},
            "agent": {"auth_methods": agent.get("auth_methods"), "primary_auth": agent.get("primary_auth")},
        }
    if unit == "api":
        types_ok = values_match("api_types", agent.get("api_types"), gold)
        docs_ok = values_match("has_public_docs", agent.get("has_public_docs"), gold)
        ok = types_ok and docs_ok
        return ok, {
            "gold": {"api_types": gold.get("api_types"), "has_public_docs": gold.get("has_public_docs")},
            "agent": {"api_types": agent.get("api_types"), "has_public_docs": agent.get("has_public_docs")},
        }
    if unit == "evidence":
        problems = evidence_problems(agent)
        return not problems, {
            "gold": "each of auth, access, api_surface, mcp, verdict has at least one http(s) URL",
            "agent": problems or "required evidence URLs present",
        }
    ok = values_match(unit, agent.get(unit), gold)
    return ok, {"gold": gold.get(unit), "agent": agent.get(unit)}


def minor_match(field: str, agent: dict, gold: dict) -> tuple[bool, bool, dict[str, Any]]:
    """Returns (applies, match, detail)."""
    if field == "one_liner":
        return (
            True,
            one_liner_match(agent.get("one_liner"), gold),
            {
                "gold": gold.get("one_liner"),
                "agent": agent.get("one_liner"),
            },
        )
    if field == "docs_url":
        return (
            True,
            docs_url_match(agent.get("docs_url"), gold),
            {
                "gold": gold.get("docs_url"),
                "agent": agent.get("docs_url"),
            },
        )
    if field == "mcp_url":
        applies = mcp_url_applies(gold)
        if not applies:
            return False, True, {"gold": gold.get("mcp_url"), "agent": agent.get("mcp_url")}
        return (
            True,
            mcp_url_match(agent.get("mcp_url"), gold),
            {
                "gold": gold.get("mcp_url"),
                "agent": agent.get("mcp_url"),
            },
        )
    if field == "requires_end_user_admin":
        applies = admin_flag_scored(gold)
        if not applies:
            return (
                False,
                True,
                {"gold": gold.get("requires_end_user_admin"), "agent": agent.get("requires_end_user_admin")},
            )
        return (
            True,
            requires_admin_match(agent.get("requires_end_user_admin"), gold),
            {
                "gold": gold.get("requires_end_user_admin"),
                "agent": agent.get("requires_end_user_admin"),
            },
        )
    return (
        True,
        values_match(field, agent.get(field), gold),
        {
            "gold": gold.get(field),
            "agent": agent.get(field),
        },
    )


def coverage_match(agent: dict | None, gold: dict) -> dict[str, Any]:
    gold_tk = gold.get("composio_toolkit") or {}
    agent_tk = (agent or {}).get("composio_toolkit") or {}
    status_ok = agent_tk.get("status") == gold_tk.get("status")
    slug_ok = True
    if gold_tk.get("status") == "exists":
        slug_ok = agent_tk.get("toolkit_slug") == gold_tk.get("toolkit_slug")
    siblings_ok = as_set(agent_tk.get("sibling_slugs") or []) == as_set(gold_tk.get("sibling_slugs") or [])
    headline = status_ok and slug_ok
    return {
        "match": headline,
        "sibling_match": siblings_ok,
        "gold": {
            "status": gold_tk.get("status"),
            "toolkit_slug": gold_tk.get("toolkit_slug"),
            "sibling_slugs": gold_tk.get("sibling_slugs") or [],
            "checked_at": gold_tk.get("checked_at"),
        },
        "agent": {
            "status": agent_tk.get("status"),
            "toolkit_slug": agent_tk.get("toolkit_slug"),
            "sibling_slugs": agent_tk.get("sibling_slugs") or [],
        },
    }


def identity_ok(agent: dict, gold: dict) -> bool:
    return all(agent.get(field) == gold.get(field) for field in IDENTITY_FIELDS)


def score_against_view(agent: dict, gold: dict) -> dict[str, Any]:
    units: dict[str, Any] = {}
    all_critical = True
    for unit in CRITICAL_UNITS:
        ok, detail = unit_match(unit, agent, gold)
        units[unit] = {"match": ok, **detail}
        if not ok:
            all_critical = False
    minors: dict[str, Any] = {}
    for field in MINOR_FIELDS:
        applies, ok, detail = minor_match(field, agent, gold)
        minors[field] = {"applies": applies, "match": ok, **detail}
    return {"row_correct": all_critical, "units": units, "minors": minors}


def pick_best_view(agent: dict, gold: dict) -> tuple[dict, dict[str, Any]]:
    best_view = gold
    best_result = score_against_view(agent, gold)
    best_hits = sum(1 for unit in CRITICAL_UNITS if best_result["units"][unit]["match"])
    for view in gold_views(gold)[1:]:
        result = score_against_view(agent, view)
        hits = sum(1 for unit in CRITICAL_UNITS if result["units"][unit]["match"])
        if result["row_correct"] or hits > best_hits:
            best_view, best_result, best_hits = view, result, hits
            if result["row_correct"]:
                break
    return best_view, best_result


def score_app(agent: dict | None, gold: dict) -> dict[str, Any]:
    slug = gold["slug"]
    coverage = coverage_match(agent, gold)
    base = {
        "id": gold.get("id"),
        "slug": slug,
        "name": gold.get("name"),
        "present": agent is not None,
        "identity_ok": bool(agent) and identity_ok(agent, gold),
        "coverage": coverage,
        "label_notes": gold.get("label_notes") or "",
    }
    if agent is None:
        units = {
            unit: {
                "match": False,
                "gold": gold.get(unit, UNIT_LABELS.get(unit)),
                "agent": None,
            }
            for unit in CRITICAL_UNITS
        }
        units["auth"] = {
            "match": False,
            "gold": {"auth_methods": gold.get("auth_methods"), "primary_auth": gold.get("primary_auth")},
            "agent": None,
        }
        units["api"] = {
            "match": False,
            "gold": {"api_types": gold.get("api_types"), "has_public_docs": gold.get("has_public_docs")},
            "agent": None,
        }
        units["evidence"] = {"match": False, "gold": "required evidence URLs", "agent": "row missing"}
        minors = {
            field: {
                "applies": field != "mcp_url" or mcp_url_applies(gold),
                "match": False,
                "gold": gold.get(field),
                "agent": None,
            }
            for field in MINOR_FIELDS
        }
        if not mcp_url_applies(gold):
            minors["mcp_url"]["applies"] = False
            minors["mcp_url"]["match"] = True
        return {
            **base,
            "row_correct": False,
            "missing": True,
            "invalid": False,
            "units": units,
            "minors": minors,
        }
    if not identity_ok(agent, gold):
        result = score_against_view(agent, gold)
        # Identity mismatch is a pipeline bug: the row scores 0.
        for unit in CRITICAL_UNITS:
            result["units"][unit]["match"] = False
        for field in MINOR_FIELDS:
            if result["minors"][field]["applies"]:
                result["minors"][field]["match"] = False
        result["row_correct"] = False
        return {**base, "missing": False, "invalid": True, **result}

    _view, result = pick_best_view(agent, gold)
    return {**base, "missing": False, "invalid": False, **result}


def aggregate(app_results: list[dict]) -> dict[str, Any]:
    n = len(app_results)
    row_correct = sum(1 for item in app_results if item["row_correct"])
    field_stats: dict[str, dict[str, int]] = {}

    def add(name: str, applies: bool, match: bool) -> None:
        stats = field_stats.setdefault(name, {"applies": 0, "matches": 0})
        if applies:
            stats["applies"] += 1
            if match:
                stats["matches"] += 1

    for item in app_results:
        for unit in CRITICAL_UNITS:
            add(unit, True, item["units"][unit]["match"])
        for field in MINOR_FIELDS:
            minor = item["minors"][field]
            add(field, minor["applies"], minor["match"])

    def pct(name: str) -> float | None:
        stats = field_stats[name]
        if not stats["applies"]:
            return None
        return stats["matches"] / stats["applies"]

    coverage_matches = sum(1 for item in app_results if item["coverage"]["match"])
    sibling_misses = sum(
        1 for item in app_results if item["coverage"]["match"] and not item["coverage"]["sibling_match"]
    )

    misses: list[dict[str, Any]] = []
    for item in app_results:
        for unit in CRITICAL_UNITS:
            if not item["units"][unit]["match"]:
                detail = item["units"][unit]
                misses.append(
                    {
                        "slug": item["slug"],
                        "name": item["name"],
                        "field": UNIT_LABELS[unit],
                        "unit": unit,
                        "severity": "critical",
                        "gold": detail.get("gold"),
                        "agent": detail.get("agent"),
                        "evidence": (item.get("units") or {}).get("evidence", {}).get("agent"),
                        "reason": "missing row"
                        if item.get("missing")
                        else "identity mismatch"
                        if item.get("invalid")
                        else "value mismatch",
                    }
                )
        for field in MINOR_FIELDS:
            minor = item["minors"][field]
            if minor["applies"] and not minor["match"]:
                misses.append(
                    {
                        "slug": item["slug"],
                        "name": item["name"],
                        "field": field,
                        "unit": field,
                        "severity": "minor",
                        "gold": minor.get("gold"),
                        "agent": minor.get("agent"),
                        "evidence": None,
                        "reason": "missing row"
                        if item.get("missing")
                        else "identity mismatch"
                        if item.get("invalid")
                        else "value mismatch",
                    }
                )
        if not item["coverage"]["match"]:
            misses.append(
                {
                    "slug": item["slug"],
                    "name": item["name"],
                    "field": "composio_toolkit",
                    "unit": "composio_toolkit",
                    "severity": "coverage",
                    "gold": item["coverage"]["gold"],
                    "agent": item["coverage"]["agent"],
                    "evidence": None,
                    "reason": "coverage lookup mismatch",
                }
            )

    return {
        "n_gold": n,
        "n_row_correct": row_correct,
        "row_accuracy": (row_correct / n) if n else None,
        "field_accuracy": {
            name: {
                "matches": field_stats[name]["matches"],
                "applies": field_stats[name]["applies"],
                "accuracy": pct(name),
            }
            for name in [*CRITICAL_UNITS, *MINOR_FIELDS]
        },
        "coverage_accuracy": {
            "matches": coverage_matches,
            "applies": n,
            "accuracy": (coverage_matches / n) if n else None,
            "sibling_misses_only": sibling_misses,
        },
        "misses": misses,
    }


def render_markdown(report: dict) -> str:
    summary = report["summary"]
    row_pct = _pct(summary["row_accuracy"])
    cov_pct = _pct(summary["coverage_accuracy"]["accuracy"])
    lines = [
        f"# Gold-sample score: {report['run_id']}",
        "",
        f"- Run dir: `{report['run_dir']}`",
        f"- Gold file: `{report['gold_path']}`",
        f"- Scored at: {report['scored_at']}",
        f"- Gold apps: {summary['n_gold']}",
        f"- Row-correct: {summary['n_row_correct']} / {summary['n_gold']} ({row_pct})",
        f"- Coverage-lookup accuracy: {summary['coverage_accuracy']['matches']} / {summary['n_gold']} ({cov_pct})",
        "",
        "Row accuracy requires every critical unit to match. Minor mismatches are listed below but do not fail the row.",
        "Evidence is scored on URL presence (http/https). Whether a URL actually supports the claim is a human check.",
        "",
        "## Per-field accuracy",
        "",
        "| Field | Kind | Matches | Applies | Accuracy |",
        "|---|---|---:|---:|---:|",
    ]
    for unit in CRITICAL_UNITS:
        stats = summary["field_accuracy"][unit]
        lines.append(
            f"| {UNIT_LABELS[unit]} | critical | {stats['matches']} | {stats['applies']} | {_pct(stats['accuracy'])} |"
        )
    for field in MINOR_FIELDS:
        stats = summary["field_accuracy"][field]
        lines.append(f"| {field} | minor | {stats['matches']} | {stats['applies']} | {_pct(stats['accuracy'])} |")
    cov = summary["coverage_accuracy"]
    lines.append(
        f"| composio_toolkit (status + primary slug) | coverage | {cov['matches']} | {cov['applies']} | {_pct(cov['accuracy'])} |"
    )
    lines += ["", "## Apps", "", "| App | Row-correct | Failed critical units |", "|---|---|---|"]
    for item in report["apps"]:
        failed = ", ".join(UNIT_LABELS[u] for u in CRITICAL_UNITS if not item["units"][u]["match"]) or "—"
        mark = "yes" if item["row_correct"] else "no"
        lines.append(f"| {item['name']} (`{item['slug']}`) | {mark} | {failed} |")

    misses = summary["misses"]
    lines += ["", "## Misses", ""]
    if not misses:
        lines.append("None.")
    else:
        lines += [
            "| App | Field | Severity | Gold | Agent | Reason |",
            "|---|---|---|---|---|---|",
        ]
        for miss in misses:
            lines.append(
                "| {name} (`{slug}`) | {field} | {severity} | {gold} | {agent} | {reason} |".format(
                    name=miss["name"],
                    slug=miss["slug"],
                    field=miss["field"],
                    severity=miss["severity"],
                    gold=_cell(miss.get("gold")),
                    agent=_cell(miss.get("agent")),
                    reason=miss["reason"],
                )
            )
    lines.append("")
    return "\n".join(lines)


def _pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1%}"


def _cell(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text.replace("|", "\\|").replace("\n", " ")


def validate_gold_file(path: Path) -> list[str]:
    """Schema + consistency checks on each gold row (gold-only keys stripped)."""
    # Import lazily so --help works without the package on PYTHONPATH issues in odd setups.
    sys.path.insert(0, str(repo_root()))
    from agent.validate import validate_row  # noqa: WPS433

    errors: list[str] = []
    gold = load_gold(path)
    for slug, row in gold.items():
        research = strip_gold_only(row)
        identity = {field: research.get(field) for field in IDENTITY_FIELDS}
        for message in validate_row(research, identity):
            errors.append(f"{slug}: {message}")
    return errors


def score_run(run_dir: Path, gold_path: Path) -> dict[str, Any]:
    gold = load_gold(gold_path)
    agent_rows = load_run_rows(run_dir)
    apps = [score_app(agent_rows.get(slug), row) for slug, row in gold.items()]
    summary = aggregate(apps)
    run_id = run_dir.name
    report = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "gold_path": str(gold_path),
        "scored_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": summary,
        "apps": apps,
        "notes": [
            "Evidence scoring checks that required claim URLs are present and well-formed.",
            "Semantic support of those URLs is a human review step.",
            "composio_toolkit is reported as coverage-lookup accuracy, not in row accuracy.",
        ],
    }
    return report


def default_out_dir() -> Path:
    return repo_root() / "data" / "scores"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score an agent run against schema/gold_sample.jsonl")
    parser.add_argument("--run-dir", type=Path, help="Run directory containing rows.jsonl, e.g. data/runs/pass1")
    parser.add_argument(
        "--gold", type=Path, default=repo_root() / "schema" / "gold_sample.jsonl", help="Gold JSONL path"
    )
    parser.add_argument(
        "--out-dir", type=Path, default=default_out_dir(), help="Directory for <run_id>.json and <run_id>.md"
    )
    parser.add_argument(
        "--validate-gold", action="store_true", help="Validate the gold file against the schema and exit"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    gold_path = args.gold if args.gold.is_absolute() else (Path.cwd() / args.gold).resolve()
    if args.validate_gold or args.run_dir is None:
        if args.validate_gold or args.run_dir is None and "--validate-gold" in (argv or sys.argv):
            errors = validate_gold_file(gold_path)
            if errors:
                print(f"gold validation failed ({len(errors)} errors)", file=sys.stderr)
                for message in errors:
                    print(message, file=sys.stderr)
                return 1
            print(f"gold ok: {len(load_gold(gold_path))} rows in {gold_path}")
            if args.run_dir is None:
                return 0
        elif args.run_dir is None:
            print("error: --run-dir is required unless --validate-gold is set", file=sys.stderr)
            return 2

    run_dir = args.run_dir if args.run_dir.is_absolute() else (Path.cwd() / args.run_dir).resolve()
    report = score_run(run_dir, gold_path)
    out_dir = args.out_dir if args.out_dir.is_absolute() else (Path.cwd() / args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{report['run_id']}.json"
    md_path = out_dir / f"{report['run_id']}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    summary = report["summary"]
    print(
        f"row accuracy {summary['n_row_correct']}/{summary['n_gold']} "
        f"({_pct(summary['row_accuracy'])}) -> {json_path} {md_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
