"""Where a person is still needed after pass 3: per-row `needs_human` flags, a fixed-seed spot-check list,
and the final JSON / CSV export.

General rules only (field values, browse status, note wording). Nothing here names a product.
"""

from __future__ import annotations

import csv
import json
import random
import re
from collections import Counter
from pathlib import Path

from agent.config import SCHEMA_PATH

# Stable reason ids, in the order they are reported.
REASONS = {
    "sales_or_partner_gate": "access needs vendor outreach (sales demo or manual partner review) a bot cannot pass",
    "confidence_not_high": "confidence is still not high after pass 3",
    "unknown_deciding_field": "a deciding field (access, primary_auth or mcp_status) is unknown",
    "browse_unresolved": "the browser pass could not settle the flagged fields",
    "conflicting_or_caveated_docs": "notes report conflicting docs, a login wall, a deprecation or migration, "
                                    "or a claim the browser pass left unverified",
}

# Sentence-level patterns on notes. Tuned for precision: a bare "not shown in the fetched pages" is not enough.
_SENT = re.compile(r"(?<=[.!?;])\s+")
CAVEATS = {
    "conflicting_docs": re.compile(r"\b(conflict\w*|contradict\w*|inconsistent|disagree\w*)\b", re.I),
    "login_wall": re.compile(r"\b(log-?in|sign-?in) (wall|required to view)\b|\bbehind (a |the )?(log-?in|sign-?in|"
                             r"account|auth\w*)\b|\bgated docs\b", re.I),
    "deprecated_or_migrating": re.compile(r"\b(deprecat\w*|sunset\w*|end[- ]of[- ]life|being (removed|retired|"
                                          r"phased out)|migrat(e|es|ing|ion) (to|from|away))\b", re.I),
}
_BROWSER = re.compile(r"\b(browser|rendered)\b", re.I)
_UNVERIFIED = re.compile(r"\bunverified\b|\bnot (re-?)?(checked|confirmed|verified)\b|"
                         r"\bcould not (be )?(confirm|verif)\w*", re.I)


def _caveats(notes: str) -> list[str]:
    """Caveat ids found in the notes, each with the sentence that triggered it."""
    out = []
    for sent in _SENT.split(notes or ""):
        for cid, rx in CAVEATS.items():
            if rx.search(sent):
                out.append(f"{cid}: {sent.strip()[:200]!r}")
        if _BROWSER.search(sent) and _UNVERIFIED.search(sent):
            out.append(f"unverified_after_browser: {sent.strip()[:200]!r}")
    return out


def evidence_urls(row: dict) -> list[str]:
    seen: list[str] = []
    for items in (row.get("evidence") or {}).values():
        for e in items or []:
            if e.get("url") and e["url"] not in seen:
                seen.append(e["url"])
    return seen


def classify_needs_human(row: dict, evidence: dict | None = None, browse: dict | None = None,
                         review: dict | None = None) -> dict:
    """One row's needs_human record. `browse` is its browse.jsonl line (or the evidence file's browse block);
    `review` its review.jsonl line (only used for a pass-2 failure that pass 3 did not fix)."""
    reasons: list[dict] = []

    def add(rid: str, detail: str) -> None:
        reasons.append({"id": rid, "detail": detail})

    access, verdict = row.get("access"), row.get("verdict")
    if access in ("gated_sales", "gated_partner") or verdict == "buildable_with_approval":
        add("sales_or_partner_gate", f"access={access}, verdict={verdict}")
    if row.get("confidence") in ("medium", "low"):
        add("confidence_not_high", f"confidence is {row['confidence']}")
    unknown = [k for k in ("access", "primary_auth", "mcp_status") if row.get(k) == "unknown"]
    if unknown:
        add("unknown_deciding_field", ", ".join(f"{k}=unknown" for k in unknown))

    b = browse if browse is not None else (evidence or {}).get("browse")
    if b and b.get("flagged"):
        usable = sum(bool(p.get("usable")) for p in b.get("pages") or [])
        status = b.get("status")
        if status in ("kept", "failed"):
            add("browse_unresolved", f"browse status {status} ({usable} usable rendered page(s))")
        elif not usable:
            add("browse_unresolved", "flagged but no usable rendered page")
    if review and review.get("status") == "failed" and not (b and b.get("flagged")):
        add("browse_unresolved", "pass-2 review failed and the row was not re-checked in the browser")

    caveats = _caveats(row.get("notes") or "")
    if caveats:
        add("conflicting_or_caveated_docs", "; ".join(caveats))

    return {
        "slug": row.get("slug"), "name": row.get("name"), "needs_human": bool(reasons),
        "reason": "; ".join(REASONS[r] for r in dict.fromkeys(x["id"] for x in reasons)) or None,
        "reasons": reasons, "confidence": row.get("confidence"), "access": access, "verdict": verdict,
        "evidence_urls": evidence_urls(row),
    }


# ---------- run-folder helpers ----------

def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _by_slug(path: Path) -> dict[str, dict]:
    return {r["slug"]: r for r in read_jsonl(path) if r.get("slug")}  # last line per slug wins


def _evidence(run_dir: Path, slug: str) -> dict | None:
    path = run_dir / "evidence" / f"{slug}.json"
    try:
        return json.loads(path.read_text()) if path.is_file() else None
    except json.JSONDecodeError:
        return None


def write_needs_human(run_dir: Path) -> dict:
    """Classify every row of run_dir/rows.jsonl; write needs_human.jsonl and needs_human.json."""
    rows = read_jsonl(run_dir / "rows.jsonl")
    browse, review = _by_slug(run_dir / "browse.jsonl"), _by_slug(run_dir / "review.jsonl")
    out = [classify_needs_human(r, _evidence(run_dir, r["slug"]), browse.get(r["slug"]), review.get(r["slug"]))
           for r in rows]
    with (run_dir / "needs_human.jsonl").open("w") as f:
        for rec in out:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    by_reason = Counter(rid for rec in out for rid in dict.fromkeys(x["id"] for x in rec["reasons"]))
    summary = {"total": len(out), "needs_human_count": sum(r["needs_human"] for r in out),
               "by_reason": {k: by_reason[k] for k in REASONS if by_reason[k]}, "rows": out}
    (run_dir / "needs_human.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    return summary


# ---------- spot check ----------

def _slugs(path: Path) -> set[str]:
    return {r["slug"] for r in read_jsonl(path)}


def _claims(row: dict, ev: dict | None) -> list[dict]:
    """Per claim: cited URLs with their quoted snippets and screenshots from the evidence file."""
    snippets = (ev or {}).get("snippets") or {}
    out = []
    for claim, items in (row.get("evidence") or {}).items():
        for e in items or []:
            quotes = [s for s in snippets.get(claim) or [] if s.get("url") == e.get("url")]
            out.append({"claim": claim, "url": e.get("url"), "source_type": e.get("source_type"),
                        "note": e.get("note"), "quotes": [s.get("quote") for s in quotes if s.get("quote")],
                        "screenshots": sorted({s["screenshot"] for s in quotes if s.get("screenshot")})})
    return out


def write_spot_check(run_dir: Path, gold_path: Path, holdout_path: Path, n: int = 10, seed: int = 42) -> Path:
    """Fixed-seed random sample of n rows outside the gold and holdout sets, for a person to check.
    Writes spot_check_<n>.json and .md; returns the .md path. Nothing here is graded by the agent."""
    rows = {r["slug"]: r for r in read_jsonl(run_dir / "rows.jsonl")}
    excluded = _slugs(gold_path) | _slugs(holdout_path)
    candidates = sorted(set(rows) - excluded)
    picked = random.Random(seed).sample(candidates, min(n, len(candidates)))
    nh = {r["slug"]: r for r in read_jsonl(run_dir / "needs_human.jsonl")}
    browse = _by_slug(run_dir / "browse.jsonl")

    items = []
    for slug in picked:
        row, ev = rows[slug], _evidence(run_dir, slug)
        flag = nh.get(slug) or classify_needs_human(row, ev, browse.get(slug))
        shots = sorted({p["screenshot"] for p in ((ev or {}).get("browse") or {}).get("pages") or []
                        if p.get("screenshot")})
        items.append({
            "slug": slug, "name": row.get("name"), "category": row.get("category"),
            **{k: row.get(k) for k in ("auth_methods", "primary_auth", "access", "api_types", "has_public_docs",
                                       "mcp_status", "mcp_url", "verdict", "blocker", "confidence", "notes")},
            "needs_human": flag["needs_human"], "needs_human_reason": flag["reason"],
            "evidence": _claims(row, ev), "screenshots": shots,
            "human_check": {"confirmed": None, "needs_correction": None, "notes": ""},
        })
    excluded_count = len(set(rows) & excluded)
    stem = f"spot_check_{n}"
    (run_dir / f"{stem}.json").write_text(json.dumps(
        {"status": "pending_human_check", "seed": seed, "n": len(items), "population": len(rows),
         "excluded_count": excluded_count, "excluded_sets": [str(gold_path), str(holdout_path)], "items": items},
        indent=2, ensure_ascii=False) + "\n")

    md = [f"# Spot check: {len(items)} random rows (pending human check)", "",
          f"A fixed-seed (`{seed}`) random sample of {len(items)} rows from the final {len(rows)}, excluding the "
          f"{len(_slugs(gold_path))} gold and {len(_slugs(holdout_path))} holdout apps ({excluded_count} rows "
          f"excluded). Candidates are sorted by slug and drawn with `random.Random({seed}).sample`, so the list "
          "is the same on every run.", "",
          "**Status: pending human verification. These rows are not self-graded.** A person checks each row "
          "against the vendor's official docs and ticks one box. Screenshot paths are relative to this run folder.",
          ""]
    for i, it in enumerate(items, 1):
        md += [f"## {i}. {it['name']} (`{it['slug']}`)", "",
               f"- **category:** {it['category']}",
               f"- **auth_methods / primary_auth:** {_fmt(it['auth_methods'])} / {_fmt(it['primary_auth'])}",
               f"- **access:** {_fmt(it['access'])}",
               f"- **api_types / has_public_docs:** {_fmt(it['api_types'])} / {_fmt(it['has_public_docs'])}",
               f"- **mcp_status / mcp_url:** {_fmt(it['mcp_status'])} / {_fmt(it['mcp_url'])}",
               f"- **verdict / blocker:** {_fmt(it['verdict'])} / {_fmt(it['blocker'])}",
               f"- **confidence:** {_fmt(it['confidence'])}",
               f"- **needs_human:** {_fmt(it['needs_human'])}"
               + (f" ({it['needs_human_reason']})" if it["needs_human_reason"] else ""),
               f"- **notes:** {it['notes'] or '(none)'}", "", "Evidence:", ""]
        for c in it["evidence"]:
            md.append(f"- `{c['claim']}` {c['url']}" + (f" ({c['source_type']})" if c["source_type"] else ""))
            md += [f"  > {_one_line(q)}" for q in c["quotes"]]
        if it["screenshots"]:
            md += ["", "Screenshots: " + ", ".join(f"`{s}`" for s in it["screenshots"])]
        md += ["", "- [ ] Confirmed against official docs", "- [ ] Needs correction (notes: ___)", ""]
    path = run_dir / f"{stem}.md"
    path.write_text("\n".join(md))
    return path


def _fmt(v) -> str:
    if isinstance(v, list):
        return "[" + ", ".join(map(str, v)) + "]" if v else "[]"
    return "null" if v is None else f"`{v}`" if isinstance(v, str) else str(v).lower()


def _one_line(s: str) -> str:
    return " ".join(str(s).split())


# ---------- final export ----------

LIST_FIELDS = ("auth_methods", "api_types")  # `;`-joined in final.csv
OBJECT_FIELDS = ("evidence", "composio_toolkit")  # compact JSON in final.csv


def schema_columns() -> list[str]:
    return list(json.loads(SCHEMA_PATH.read_text())["properties"])


def export_final(rows: list[dict], out_dir: Path) -> tuple[Path, Path]:
    """final.json (pretty array of the rows, sorted by id) and final.csv (schema columns in schema order)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: (r.get("id") is None, r.get("id") or 0, r.get("slug") or ""))
    jpath, cpath = out_dir / "final.json", out_dir / "final.csv"
    jpath.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
    cols = schema_columns()
    with cpath.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: _cell(k, r.get(k)) for k in cols})
    return jpath, cpath


def _cell(key: str, v) -> str:
    if v is None:
        return ""
    if key in LIST_FIELDS:
        return ";".join(map(str, v))
    if key in OBJECT_FIELDS or isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)
