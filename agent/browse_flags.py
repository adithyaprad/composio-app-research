"""Pass-3 flag selector: which pass-2 rows still need a real browser, with stable reason ids.

General rules only: they read the row, its evidence file (pages[] records, snippets, checks) and the
pass-2 review record. No product is named here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agent.validate import normalize_url

REASONS = ("confidence_not_high", "unsupported_or_invalid", "thin_or_blocked_page", "unresolved_deciding_fact")
# Claims a browser pass can settle; their cited pages are the ones worth re-opening.
DECIDING_CLAIMS = ("access", "auth", "mcp", "verdict")
# Which claims each reason puts in scope (the deciding-fact and page reasons narrow this per hit).
REASON_CLAIMS = {
    "confidence_not_high": DECIDING_CLAIMS,
    "unsupported_or_invalid": DECIDING_CLAIMS,
}

THIN_PAGE_CHARS = 400  # extracted text shorter than this is too thin to have decided anything
BLOCKED_STATUS = {0, 401, 403, 429, 503}
FAILED_METHODS = {"http_error", "fetch_error", "exa_error"}

# Notes / quotes that leave a deciding fact open: -> claims it decides.
DECIDING_PATTERNS: tuple[tuple[str, re.Pattern, tuple[str, ...]], ...] = (
    ("sales_checkout", re.compile(
        r"\b(?:book|request|schedule)\s+(?:a\s+)?demo\b|\b(?:talk|speak)\s+(?:to|with)\s+(?:our\s+)?sales\b"
        r"|\bcontact\s+(?:our\s+)?sales\b"), ("access", "verdict")),
    ("mcp_not_live", re.compile(
        r"\bmcp\b[^.]{0,60}?\b(?:coming\s+soon|beta|preview|early\s+access|waitlist|announced)\b"
        r"|\b(?:coming\s+soon|beta|preview|early\s+access|waitlist)\b[^.]{0,40}?\bmcp\b"), ("mcp",)),
    ("api_not_live", re.compile(
        r"\bapi\b[^.]{0,30}?\b(?:coming\s+soon|private\s+beta|closed\s+beta|early\s+access|waitlist)\b"
        r"|\b(?:coming\s+soon|private\s+beta|closed\s+beta|early\s+access|waitlist)\b[^.]{0,30}?\bapi\b"),
     ("access", "verdict")),
    ("page_unreadable", re.compile(
        r"\b(?:js|javascript)[\s-](?:only|rendered|heavy)\b|\brequires?\s+javascript\b|\benable\s+javascript\b"
        r"|\b(?:could\s+not|couldn't|cannot|can't|unable\s+to)\s+(?:be\s+)?(?:fetch|render|load|read|access)\w*\b"
        r"[^.]{0,40}?\b(?:page|docs|documentation|pricing|site)\b"
        r"|\b(?:page|docs|documentation|pricing|site)\b[^.]{0,40}?\b(?:was|were|is|are)\s+(?:blocked|"
        r"empty|not\s+rendered)\b|\bblocked\s+by\s+(?:a\s+)?(?:bot|captcha|firewall|waf)\b"),
     ("access", "auth", "mcp")),
)


@dataclass
class Flag:
    reason: str
    detail: str
    claims: tuple[str, ...]
    urls: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"reason": self.reason, "detail": self.detail, "claims": list(self.claims), "urls": list(self.urls)}


def _cited(row: dict, claims=DECIDING_CLAIMS) -> dict[str, list[str]]:
    """normalized URL -> claims citing it, for the given claims."""
    out: dict[str, list[str]] = {}
    ev = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    for c in claims:
        for item in ev.get(c) or []:
            if isinstance(item, dict) and isinstance(item.get("url"), str):
                out.setdefault(normalize_url(item["url"]), []).append(c)
    return out


def page_problem(meta: dict) -> str | None:
    """Why a fetched page record could not have decided anything (None when it looks fine)."""
    status = meta.get("status")
    if meta.get("error"):
        return f"error: {str(meta['error'])[:80]}"
    if status in BLOCKED_STATUS or (isinstance(status, int) and status >= 400):
        return f"http {status}"
    if meta.get("method") in FAILED_METHODS:
        return f"method {meta['method']}"
    if (meta.get("text_len") or 0) < THIN_PAGE_CHARS:
        return f"thin text ({meta.get('text_len') or 0} chars)"
    return None


def _texts(row: dict, evidence: dict | None) -> list[str]:
    out = [row.get("notes") or ""]
    for items in ((evidence or {}).get("snippets") or {}).values():
        for s in items if isinstance(items, list) else []:
            if isinstance(s, dict) and s.get("quote"):
                out.append(str(s["quote"]))
    return [t.lower() for t in out if t]


def flag_row(row: dict, evidence: dict | None = None, review: dict | None = None) -> list[Flag]:
    """Every reason that applies to a pass-2 row, in REASONS order (empty: copy through)."""
    flags: list[Flag] = []
    if row.get("confidence") in ("medium", "low"):
        flags.append(Flag("confidence_not_high", f"confidence is {row['confidence']}",
                          REASON_CLAIMS["confidence_not_high"]))

    bad: list[str] = []
    review = review or {}
    for rule in ("unsupported_evidence", "validation_failed"):
        if rule in (review.get("rules_fired") or []):
            bad.append(f"pass-2 review fired {rule}")
    if review.get("validation_errors"):
        bad.append(f"pass-2 validation errors: {len(review['validation_errors'])}")
    if ((evidence or {}).get("checks") or {}).get("validation_errors"):
        bad.append("evidence checks list validation errors")
    if bad:
        flags.append(Flag("unsupported_or_invalid", "; ".join(bad), REASON_CLAIMS["unsupported_or_invalid"]))

    cited = _cited(row)
    by_url: dict[str, list[dict]] = {}
    for meta in (evidence or {}).get("pages") or []:
        if not isinstance(meta, dict):
            continue
        for u in {normalize_url(meta.get("requested_url") or ""), normalize_url(meta.get("final_url") or "")}:
            if u in cited:
                by_url.setdefault(u, []).append(meta)
    thin_urls, thin_claims, details = [], [], []
    for u, metas in by_url.items():
        problems = [page_problem(m) for m in metas]
        if all(problems):  # no record of this cited page came back usable
            thin_urls.append(metas[0].get("requested_url") or u)
            thin_claims += cited[u]
            details.append(f"{u}: {problems[-1]}")
    if thin_urls:
        claims = tuple(c for c in DECIDING_CLAIMS if c in thin_claims)
        flags.append(Flag("thin_or_blocked_page", "; ".join(details)[:500], claims, tuple(thin_urls)))

    hits, claims = [], []
    for name, pattern, decides in DECIDING_PATTERNS:
        for t in _texts(row, evidence):
            m = pattern.search(t)
            if m:
                hits.append(f"{name}: {m.group(0)!r}")
                claims += decides
                break
    if hits:
        flags.append(Flag("unresolved_deciding_fact", "; ".join(hits)[:500],
                          tuple(c for c in DECIDING_CLAIMS if c in claims)))
    return flags


def flag_claims(flags: list[Flag]) -> list[str]:
    """Union of claims in scope for a row's flags, in DECIDING_CLAIMS order."""
    return [c for c in DECIDING_CLAIMS if any(c in f.claims for f in flags)]


def flag_urls(flags: list[Flag]) -> list[str]:
    return list(dict.fromkeys(u for f in flags for u in f.urls))
