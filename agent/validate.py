"""Row validation: JSON Schema (Draft 2020-12) plus the consistency rules in
schema/README.md that the schema cannot express, plus evidence integrity."""

from __future__ import annotations

import json
import re
from datetime import datetime
from functools import cache
from urllib.parse import urlsplit, urlunsplit

from jsonschema import Draft202012Validator, FormatChecker

from agent.config import SCHEMA_PATH

EVIDENCE_KEYS = ("one_liner", "auth", "access", "api_surface", "mcp", "verdict")
GATED = {"gated_partner", "gated_sales"}
SELF_SERVE = {"self_serve", "self_serve_paid"}
RAW_HTTP = {"rest", "graphql", "soap"}

_format_checker = FormatChecker()


@_format_checker.checks("uri", raises=ValueError)
def _is_uri(value: object) -> bool:
    if not isinstance(value, str):
        return True
    parts = urlsplit(value)
    return parts.scheme in ("http", "https") and bool(parts.netloc) and " " not in value


@_format_checker.checks("date-time", raises=ValueError)
def _is_datetime(value: object) -> bool:
    if not isinstance(value, str):
        return True
    datetime.fromisoformat(value)
    return "T" in value


@cache
def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text())
    return Draft202012Validator(schema, format_checker=_format_checker)


def schema_errors(row: dict) -> list[str]:
    errors = []
    for e in sorted(_validator().iter_errors(row), key=lambda e: list(e.absolute_path)):
        path = "/".join(str(p) for p in e.absolute_path) or "(root)"
        errors.append(f"schema: {path}: {e.message}")
    return errors


def consistency_errors(row: dict, identity: dict | None = None) -> list[str]:
    """Rules from schema/README.md 'Consistency rules' not fully covered by the schema."""
    errs: list[str] = []
    g = row.get
    if identity:
        for k in ("id", "slug", "name", "category"):
            if g(k) != identity[k]:
                errs.append(f"rule identity: {k} must be {identity[k]!r} from the input CSV")
    if g("primary_auth") not in (g("auth_methods") or []):
        errs.append("rule 1: primary_auth must be one of auth_methods")
    # The oauth2-first preference is not enforced here: it only applies when OAuth2 authenticates the
    # product's API itself (not an MCP server or dashboard login), which code cannot tell. Prompt + rubric own it.
    access, verdict, blocker = g("access"), g("verdict"), g("blocker")
    if access == "no_public_api" and (verdict != "not_buildable" or blocker != "no_public_api"):
        errs.append("rule 7: access no_public_api requires verdict not_buildable and blocker no_public_api")
    if blocker == "no_public_api" and access != "no_public_api":
        errs.append("rule 7: blocker no_public_api requires access no_public_api")
    if access in GATED and verdict == "buildable_now":
        errs.append("rule 8: gated access cannot be verdict buildable_now (use buildable_with_approval)")
    if blocker == "gated_access" and access not in GATED:
        errs.append("rule 8: blocker gated_access requires access gated_partner or gated_sales")
    if verdict == "buildable_now" and access not in SELF_SERVE:
        errs.append("rule verdict: buildable_now requires access self_serve or self_serve_paid")
    if verdict == "buildable_now" and g("api_types") == ["none"]:
        errs.append("rule verdict: buildable_now requires a callable interface (api_types is none)")
    if blocker == "other" and not (g("notes") or "").strip():
        errs.append("rule 9: blocker other requires non-empty notes")
    api_types = set(g("api_types") or [])
    if "sdk_only" in api_types and api_types & RAW_HTTP:
        raw = ", ".join(sorted(api_types & RAW_HTTP))
        errs.append(f"rule api_types: sdk_only means no public raw HTTP docs, so it cannot be combined with rest, "
                    f"graphql, or soap. The row lists {raw}, so drop sdk_only and keep {raw} (mention SDKs in notes)")
    return errs + notes_contradictions(row)


# ---------- notes vs structured fields ----------
# Only self-contradiction inside one row is flagged; precision over recall. Code still cannot tell API OAuth
# from MCP-only OAuth, so oauth2-as-primary stays a prompt/rubric rule.

_SENTENCE_SPLIT = re.compile(r"(?<=[.;!?])\s+|\n+")
_NON_API_SCOPE = r"(?:(?:hosted\s+|remote\s+)?mcp(?:\s+server)?|dashboard|website|web\s*app|sso|log-?in|sign-?in)"
_OAUTH_NOT_API = [
    # "OAuth only for the MCP server", "applies only to the dashboard login"
    re.compile(r"\b(?:only|solely|exclusively)\s+(?:applies\s+to|used\s+(?:for|by)|for|by|to|on|with|in)\s+"
               r"(?:the\s+|its\s+|a\s+)?" + _NON_API_SCOPE + r"\b"),
    # "MCP server only", "MCP-only", "dashboard login only"
    re.compile(r"\b" + _NON_API_SCOPE + r"(?:[\s-]+(?:login|server|auth))?[\s-]+only\b"),
    # "OAuth2 is undocumented / not documented for the API"
    re.compile(r"\boauth\s*2?(?:\.0)?\s+(?:\w+\s+){0,3}?(?:undocumented|not\s+documented)\b"),
    # "so it is not listed as an API auth method", "not an API auth method"
    re.compile(r"\bnot\s+(?:(?:listed|included)\s+(?:as|in|among)\b|an?\s+(?:developer\s+)?api\s+auth)"),
    re.compile(r"\b(?:drop|remov|exclud|omit)\w*\s+(?:the\s+)?oauth"),
    re.compile(r"\b(?:without|excluding)\s+oauth"),
]
# "Fixing auth_methods to api_key only is more accurate" (no "oauth" in that sentence)
_AUTH_MORE_ACCURATE = re.compile(r"\bauth\w*\b[^.]*\bmore\s+accurate\b|\bmore\s+accurate\b[^.]*\bauth\w*\b")
_NO_OFFICIAL_MCP = re.compile(r"\bno\s+official\s+mcp\b")
_NO_MCP_AT_ALL = re.compile(r"\bno\s+mcp\s+(?:server\s+)?(?:was\s+|is\s+)?(?:found|exists|available|published)\b"
                            r"|\bonly\s+(?:a\s+)?vague\s+(?:mcp\s+)?mention")


def notes_say_oauth_not_api(row: dict) -> bool:
    """Notes say OAuth is MCP-only, login-only, undocumented for the API, or should be dropped."""
    sentences = [s for s in _SENTENCE_SPLIT.split((row.get("notes") or "").lower()) if s.strip()]
    return any(("oauth" in s and any(p.search(s) for p in _OAUTH_NOT_API)) or _AUTH_MORE_ACCURATE.search(s)
               for s in sentences)


def notes_contradictions(row: dict) -> list[str]:
    """Flag notes that contradict auth_methods / mcp_status in the same row."""
    notes = (row.get("notes") or "").lower()
    if not notes:
        return []
    errs = []
    if "oauth2" in (row.get("auth_methods") or []):
        if notes_say_oauth_not_api(row):
            errs.append(
                "rule notes: notes say OAuth is MCP-only, dashboard/login-only, undocumented for the API, or should "
                "be dropped, but auth_methods still lists oauth2. Either remove oauth2 from auth_methods (and set "
                "primary_auth to another listed method), or, if a fetched page documents OAuth2 for third-party apps "
                "calling the product's own API, cite it in evidence.auth and rewrite notes to match.")
    status = row.get("mcp_status")
    if (status == "official" and _NO_OFFICIAL_MCP.search(notes)) or (
            status in ("official", "community") and _NO_MCP_AT_ALL.search(notes)):
        errs.append(
            f"rule notes: notes say there is no {'official ' if status == 'official' else ''}MCP server (or only a "
            f"vague mention), but mcp_status is {status}. Either set mcp_status to match the notes (none with mcp_url "
            "null, or community), or rewrite notes to match the cited MCP server / docs page.")
    return errs


def validate_row(row: dict, identity: dict | None = None) -> list[str]:
    return schema_errors(row) + consistency_errors(row, identity)


def normalize_url(url: str) -> str:
    """Compare URLs loosely: lowercase host, drop fragment, default port, trailing slash, www."""
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    host = (p.hostname or "").lower().removeprefix("www.")
    scheme = "https" if p.scheme.lower() in ("http", "https") else p.scheme.lower()
    return urlunsplit((scheme, host, p.path.rstrip("/"), p.query, ""))


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def check_evidence(row: dict, snippets: dict, pages: list) -> tuple[dict, dict]:
    """Drop evidence URLs that were not fetched (OK) in this run; verify quoted snippets.

    `pages` are tools.base.Page objects. Returns (row, checks); the row is modified
    in place. Required evidence lists that end up empty make the row fail schema
    validation, which triggers the retry path.
    """
    ok_pages = [p for p in pages if p.ok]
    fetched: dict[str, str] = {}
    for p in ok_pages:
        fetched[normalize_url(p.requested_url)] = p.text
        fetched[normalize_url(p.final_url)] = p.text
    checks: dict = {"dropped_evidence": [], "unverified_snippets": [], "unfetched_urls": [], "warnings": []}

    evidence = row.get("evidence") or {}
    for key in EVIDENCE_KEYS:
        items = evidence.get(key)
        if not isinstance(items, list):
            continue
        kept = []
        for item in items:
            url = item.get("url", "") if isinstance(item, dict) else ""
            if normalize_url(url) in fetched:
                kept.append(item)
            else:
                checks["dropped_evidence"].append({"claim": key, "url": url, "reason": "not fetched in this run"})
        evidence[key] = kept
        if key == "one_liner" and not kept:
            evidence.pop(key)

    for field in ("docs_url", "mcp_url"):
        url = row.get(field)
        if url and normalize_url(url) not in fetched:
            checks["unfetched_urls"].append({"field": field, "url": url})

    for claim, items in (snippets or {}).items():
        for s in items if isinstance(items, list) else []:
            url, quote = s.get("url", ""), s.get("quote", "")
            text = fetched.get(normalize_url(url))
            if text is None:
                checks["unverified_snippets"].append({"claim": claim, "url": url, "reason": "url not fetched"})
            elif not quote or _norm_text(quote) not in _norm_text(text):
                checks["unverified_snippets"].append({"claim": claim, "url": url, "reason": "quote not in page text"})
            else:
                s["verified"] = True

    for claim in ("auth", "access", "api_surface", "mcp", "verdict"):
        if not any(s.get("verified") for s in (snippets or {}).get(claim, []) or []):
            checks["warnings"].append(f"no verified snippet for {claim}")
    return row, checks
