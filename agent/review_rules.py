"""Pass-2 review checklist: stable rule ids, the fields each rule may change, and deterministic checks.

A deterministic check fires only when the row's own notes, or a quoted snippet from a vendor-owned page,
clearly shows the pattern (same spirit as validate.notes_contradictions). Keyword guesses that would
rewrite correct rows are worse than a miss; the LLM reviewer is the recall net.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from agent.research import vendor_tier
from agent.validate import SELF_SERVE, normalize_url, notes_say_oauth_not_api, validate_row

CRITICAL_CLAIMS = ("auth", "access", "api_surface", "mcp", "verdict")
# Fields a claim's evidence supports; re-researching a claim may change these.
CLAIM_FIELDS = {
    "auth": ("auth_methods", "primary_auth"),
    "access": ("access",),
    "api_surface": ("api_types", "has_public_docs", "docs_url"),
    "mcp": ("mcp_status", "mcp_url"),
    "verdict": ("verdict", "blocker"),
}
FIELD_CLAIM = {f: c for c, fs in CLAIM_FIELDS.items() for f in fs}
# Fields that consistency rules tie together: changing one may force the other.
COUPLED = {
    "auth_methods": ("primary_auth",), "primary_auth": ("auth_methods",),
    "access": ("verdict", "blocker"), "verdict": ("blocker",), "blocker": ("verdict",),
    "has_public_docs": ("docs_url",), "docs_url": ("has_public_docs",),
    "mcp_status": ("mcp_url",), "mcp_url": ("mcp_status",),
}

# id -> (checklist text for the LLM reviewer, fields the fix may change)
RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "basic_transport_not_method": (
        "auth_methods/primary_auth lists basic, but the pages only send an API key or token through the HTTP "
        "Basic scheme. basic is its own method only for a username + password (or equivalent user login). "
        "Fix: drop basic, keep api_key/oauth2.", ("auth_methods", "primary_auth")),
    "oauth_client_secret_not_api_key": (
        "auth_methods lists api_key, but official docs use that dashboard/profile secret only as the OAuth "
        "client_secret (or equivalent) in a token request, never as a standalone API credential. "
        "Fix: drop api_key, keep oauth2.", ("auth_methods", "primary_auth")),
    "mcp_only_oauth": (
        "auth_methods lists oauth2, but OAuth is documented only for the MCP server or a dashboard/SSO login, "
        "not for third-party apps calling the product's own API. Fix: drop oauth2 (and fix primary_auth).",
        ("auth_methods", "primary_auth")),
    "sales_or_review_gate": (
        "access is self_serve/self_serve_paid, but official pages show the only way to API access is a "
        "sales/demo process, or a vendor app review required to call the API as a third-party / multi-tenant "
        "app, AND there is no self-serve credential path. Exception: if a developer can get usable API "
        "credentials with no human vendor review (e.g. a personal token after buying a plan online), access "
        "stays self_serve/self_serve_paid even if a separate OAuth app registration is reviewed. "
        "Fix: gated_sales or gated_partner + buildable_with_approval + gated_access.",
        ("access", "verdict", "blocker")),
    "preview_not_buildable_now": (
        "verdict is buildable_now, but the product API itself (not merely an MCP server) is only a public "
        "preview, beta, or otherwise unstable. Fix: buildable_with_approval or not_buildable with blocker "
        "preview_or_unstable.", ("verdict", "blocker")),
    "official_graphql_omitted": (
        "official vendor docs describe a GraphQL API, but api_types lacks graphql. Fix: add graphql (keep rest "
        "if it is also documented).", ("api_types",)),
    "public_docs_marked_false": (
        "has_public_docs is false, but an official help, product, or developer page describes how to call the "
        "API. Fix: has_public_docs true and docs_url at that official page.", ("has_public_docs", "docs_url")),
    "missed_vendor_mcp": (
        "mcp_status is none, but a vendor-published or vendor-endorsed MCP server or MCP docs page exists "
        "(official docs, official repo, official announcement, or a vendor beta MCP with a real server URL). "
        "Third-party directories are not official. Fix: official (community only if clearly unofficial) and "
        "mcp_url.", ("mcp_status", "mcp_url")),
    "unsupported_evidence": (
        "a critical claim (auth, access, api_surface, mcp, verdict) has no well-formed evidence URL, or its "
        "quoted snippet does not support the claim. Name the claims. Fix: re-fetch official pages for that "
        "claim and replace the evidence.", ()),
}
VALIDATION_RULE = "validation_failed"  # existing validate_row errors, treated like a fired rule
RULE_ORDER = (*RULES, VALIDATION_RULE)
PRECONDITIONS = {  # a rule cannot apply when the row does not have the shape it corrects
    "basic_transport_not_method": lambda r: "basic" in [*(r.get("auth_methods") or []), r.get("primary_auth")],
    "oauth_client_secret_not_api_key": lambda r: "api_key" in (r.get("auth_methods") or []),
    "mcp_only_oauth": lambda r: "oauth2" in (r.get("auth_methods") or []),
    "sales_or_review_gate": lambda r: r.get("access") in SELF_SERVE,
    "preview_not_buildable_now": lambda r: r.get("verdict") == "buildable_now",
    "official_graphql_omitted": lambda r: "graphql" not in (r.get("api_types") or []),
    "public_docs_marked_false": lambda r: r.get("has_public_docs") is False,
    "missed_vendor_mcp": lambda r: r.get("mcp_status") == "none",
}


@dataclass
class Finding:
    rule: str
    source: str  # code | llm | validator
    reason: str
    fields: tuple[str, ...]
    quotes: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"rule": self.rule, "source": self.source, "reason": self.reason, "fields": list(self.fields),
                "quotes": self.quotes}


def applies(rule: str, row: dict) -> bool:
    check = PRECONDITIONS.get(rule)
    return check is None or bool(check(row))


def claim_fields(claims) -> tuple[str, ...]:
    return tuple(f for c in CRITICAL_CLAIMS if c in claims for f in CLAIM_FIELDS[c])


def scope(findings: list[Finding]) -> tuple[list[str], list[str]]:
    """(fields that may change, evidence claims to replace) for a set of findings, with coupled fields added."""
    fields: list[str] = []
    for f in findings:
        for name in f.fields:
            for x in (name, *COUPLED.get(name, ())):
                if x not in fields:
                    fields.append(x)
    claims = [c for c in CRITICAL_CLAIMS if any(FIELD_CLAIM.get(f) == c for f in fields)]
    return fields, claims


# ---------- text the checks read: the row's notes + quotes from vendor-owned pages ----------

_SENTENCE_SPLIT = re.compile(r"(?<=[.;!?])\s+|\n+")
_URL_RE = re.compile(r"https?://[^\s)\]>\"'<,]+")


def _sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_SPLIT.split(text.lower()) if s.strip()]


def _texts(app: dict, row: dict, snippets: dict, claims: tuple[str, ...] | None = None) -> list[str]:
    """Sentences from notes and from verified-or-not quotes on vendor-owned pages (optionally some claims only)."""
    out = _sentences(row.get("notes") or "")
    for claim, items in (snippets or {}).items():
        if claims is not None and claim not in claims:
            continue
        for s in items if isinstance(items, list) else []:
            if isinstance(s, dict) and s.get("quote") and vendor_tier(app, s.get("url") or "") is not None:
                out += _sentences(s["quote"])
    return out


def _any(pattern: re.Pattern, sentences: list[str], neg: re.Pattern | None = None) -> str | None:
    for s in sentences:
        if pattern.search(s) and not (neg and neg.search(s)):
            return s.strip()
    return None


_KEY = r"(?:api[\s_-]?keys?|api[\s_-]?tokens?|access[\s_-]?tokens?|secret[\s_-]?keys?|tokens?)"
BASIC_TRANSPORT = re.compile(
    _KEY + r"\b[^.]{0,60}?\bas\s+(?:the\s+)?(?:basic(?:[\s-]auth\w*)?\s+)?(?:username|user\s*name|password)\b"
    r"|\bbasic\s+auth\w*\s+(?:with|using|via)\s+(?:your\s+|an?\s+|the\s+)?" + _KEY + r"\b"
    r"|" + _KEY + r"\s+(?:is\s+|are\s+)?(?:sent|passed|supplied|provided|encoded)\s+(?:via|using|with|in|through|as)\s+"
    r"(?:the\s+|an?\s+)?(?:http\s+)?basic\b")
REAL_BASIC = re.compile(r"\b(?:username|user\s*name|e-?mail|login)\s+(?:and|&|/)\s+password\b|\buser\s+credentials\b")

CLIENT_SECRET = re.compile(
    r"\b(?:api[\s_-]?key|secret[\s_-]?key|api[\s_-]?secret|app(?:lication)?[\s_-]?secret|secret)\b[^.]{0,60}?"
    r"\b(?:as|is)\s+(?:the\s+|your\s+|an?\s+)?(?:oauth\s*2?(?:\.0)?\s+)?client[\s_-]?secret\b"
    r"|\bclient[\s_-]?secret\b[^.]{0,30}?\b(?:is|=|:)\s+(?:your|the)\s+(?:api[\s_-]?key|secret[\s_-]?key|api\s+secret)\b")
STANDALONE_KEY = re.compile(
    r"\b(?:api[\s_-]?key|secret[\s_-]?key)\b[^.]{0,40}?\b(?:in|via|as)\s+(?:the\s+|an?\s+)?"
    r"(?:x-api-key|bearer|query\s+param|request\s+header|api\s+header)")

GATE = re.compile(
    r"\b(?:contact|talk\s+to|speak\s+(?:with|to))\s+(?:our\s+)?(?:sales|account\s+(?:team|manager)|us)\b[^.]{0,60}?"
    r"\b(?:api|access|credentials?|keys?)\b"
    r"|\bapi\s+(?:access|keys?|credentials?)\b[^.]{0,40}?\b(?:requires?|only\s+(?:after|with|via|through)|"
    r"(?:is\s+)?available\s+only|granted\s+(?:only\s+)?after|upon)\b[^.]{0,40}?"
    r"\b(?:sales|demo|approval|review|partner|contract|enterprise)\b"
    r"|\b(?:book|request|schedule)\s+a\s+demo\b[^.]{0,60}?\bapi\b"
    r"|\b(?:only|must)\b[^.]{0,30}?\b(?:approved|partner\s+program|app\s+review)\b[^.]{0,40}?\b(?:api|credentials?)\b")
GATE_NEG = re.compile(r"\b(?:not\s+required|no\s+(?:sales|approval|review)|without\s+(?:sales|approval|review)"
                      r"|optional)\b")
SELF_SERVE_PATH = re.compile(
    r"\bpersonal\s+(?:access\s+)?tokens?\b|\b(?:generate|create|get|copy)\s+(?:an?\s+|your\s+)?"
    r"(?:api[\s_-]?key|api[\s_-]?token|access[\s_-]?token)\b|\bself[\s-]serve\b|\bsign\s*up\b[^.]{0,40}?\b(?:api|key|token)\b"
    r"|\bfrom\s+(?:your|the)\s+(?:dashboard|account\s+settings|settings)\b|\binstantly\b")

PREVIEW = re.compile(
    r"\bapi\s+(?:is|remains)\s+(?:still\s+|currently\s+|only\s+)?(?:in\s+)?(?:an?\s+)?"
    r"(?:public\s+|private\s+|closed\s+|open\s+|early\s+)?(?:beta|preview|alpha|experimental|unstable)\b"
    r"|\b(?:beta|preview|alpha|experimental)\s+(?:rest\s+|graphql\s+|public\s+|developer\s+)?api\b"
    r"(?!\s+(?:endpoints?|features?|fields?|methods?|versions?))"
    r"|\bapi\s*\((?:beta|preview|alpha)\)")
PREVIEW_NEG = re.compile(r"\bmcp\b|\b(?:out\s+of|no\s+longer|exited|left|graduated\s+from)\s+(?:beta|preview)"
                         r"|generally\s+available|\bga\b|\bstable\b")

GRAPHQL = re.compile(
    r"\bgraphql\s+(?:admin\s+|storefront\s+|public\s+|management\s+|partner\s+)?(?:api|endpoint|schema)\b"
    r"|\b(?:api|endpoint)\s+(?:is|uses)\s+(?:a\s+)?graphql\b"
    r"|\bgraphql\s+(?:is|are)\s+(?:also\s+)?(?:available|supported|documented)\b")
GRAPHQL_NEG = re.compile(r"\b(?:no|not|without|never|lacks?|deprecated|sunset|retired|removed|third[\s-]party|"
                         r"community|unofficial|isn't|doesn't)\b")

PUBLIC_DOCS = re.compile(
    r"\bcurl\s+(?:-\w+\s+)*['\"]?https?://|\b(?:get|post|put|patch|delete)\s+/\w"
    r"|\bapi\s+reference\b|\bbase\s+url\b"
    r"|\b(?:api|developer)\s+(?:docs|documentation)\b[^.]{0,40}?\b(?:at|available|published|public(?:ly)?|"
    r"describes?|explains?|covers?)\b")
PUBLIC_DOCS_NEG = re.compile(r"\b(?:no|not|without|lacks?|gated|private|behind|login|log\s+in|internal|"
                             r"partners?\s+only|customers?\s+only|requires?\s+(?:an?\s+)?(?:account|login))\b")

MCP_VAGUE = re.compile(r"coming\s+soon|\bplanned\b|\broadmap\b|\bwaitlist\b|\bvague\b|no\s+(?:server\s+)?url"
                       r"|announced\s+but|not\s+(?:yet\s+)?(?:released|available)")


def _finding(rule: str, reason: str, quote: str | None = None) -> Finding:
    return Finding(rule, "code", reason, RULES[rule][1], [{"quote": quote}] if quote else [])


def deterministic_findings(app: dict, row: dict, evidence: dict | None) -> list[Finding]:
    """Content rules that the row's notes or vendor quotes clearly show, plus unsupported_evidence."""
    snippets = (evidence or {}).get("snippets") or {}
    out: list[Finding] = []

    if applies("basic_transport_not_method", row):
        texts = _texts(app, row, snippets, ("auth",))
        hit = _any(BASIC_TRANSPORT, texts)
        if hit and not any(REAL_BASIC.search(s) for s in texts):
            out.append(_finding("basic_transport_not_method", "a key/token is sent via the Basic scheme; no "
                                "username + password login is described", hit))

    if applies("oauth_client_secret_not_api_key", row) and "oauth2" in (row.get("auth_methods") or []):
        texts = _texts(app, row, snippets, ("auth",))
        hit = _any(CLIENT_SECRET, texts)
        if hit and not any(STANDALONE_KEY.search(s) for s in texts):
            out.append(_finding("oauth_client_secret_not_api_key", "the listed secret is the OAuth client_secret, "
                                "not a standalone API key", hit))

    if applies("mcp_only_oauth", row) and notes_say_oauth_not_api(row):
        out.append(_finding("mcp_only_oauth", "notes say OAuth is MCP-only, login-only, undocumented for the API, "
                            "or should be dropped, while auth_methods lists oauth2"))

    if applies("sales_or_review_gate", row):
        texts = _texts(app, row, snippets, ("access", "verdict"))
        hit = _any(GATE, texts, GATE_NEG)
        if hit and not any(SELF_SERVE_PATH.search(s) for s in texts):
            out.append(_finding("sales_or_review_gate", "access is self-serve, but the only described path to API "
                                "credentials is sales or vendor review", hit))

    if applies("preview_not_buildable_now", row):
        hit = _any(PREVIEW, _texts(app, row, snippets, ("api_surface", "verdict", "access")), PREVIEW_NEG)
        if hit:
            out.append(_finding("preview_not_buildable_now", "the product API itself is described as preview/beta",
                                hit))

    if applies("official_graphql_omitted", row):
        hit = _any(GRAPHQL, _texts(app, row, snippets), GRAPHQL_NEG)
        if hit:
            out.append(_finding("official_graphql_omitted", "a GraphQL API is described but api_types lacks graphql",
                                hit))

    if applies("public_docs_marked_false", row):
        if row.get("docs_url"):
            out.append(_finding("public_docs_marked_false", "has_public_docs is false but docs_url is set"))
        else:
            hit = _any(PUBLIC_DOCS, _texts(app, row, snippets, ("api_surface", "auth")), PUBLIC_DOCS_NEG)
            if hit:
                out.append(_finding("public_docs_marked_false", "an official page describes how to call the API",
                                    hit))

    if applies("missed_vendor_mcp", row):
        urls = [i.get("url") or "" for i in (row.get("evidence") or {}).get("mcp") or [] if isinstance(i, dict)]
        urls += [s.get("url") or "" for s in snippets.get("mcp") or [] if isinstance(s, dict)]
        text = " ".join([row.get("notes") or ""] + [s.get("quote") or "" for items in snippets.values()
                                                     if isinstance(items, list) for s in items if isinstance(s, dict)])
        urls += _URL_RE.findall(text)
        vendor_mcp = next((u for u in urls if "mcp" in u.lower() and vendor_tier(app, u) is not None), None)
        if vendor_mcp and not MCP_VAGUE.search((row.get("notes") or "").lower()):
            out.append(_finding("missed_vendor_mcp", f"a vendor-owned MCP URL is cited or quoted: {vendor_mcp}"))

    out += evidence_findings(row, evidence)
    return out


def _well_formed(url: object) -> bool:
    if not isinstance(url, str):
        return False
    p = urlsplit(url)
    return p.scheme in ("http", "https") and bool(p.netloc) and " " not in url


def evidence_findings(row: dict, evidence: dict | None) -> list[Finding]:
    """unsupported_evidence per critical claim: no well-formed URL; or, when the pass-1 evidence file is
    available, no cited URL among its fetched pages, or no quote that was verified in the page text."""
    ev = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    fetched: set[str] | None = None
    snippets = None
    if evidence:
        fetched = set()
        for p in evidence.get("pages") or []:
            if not p.get("error") and 200 <= (p.get("status") or 0) < 400 and p.get("text_len", 1) > 0:
                fetched |= {normalize_url(p.get("requested_url") or ""), normalize_url(p.get("final_url") or "")}
        snippets = evidence.get("snippets") if isinstance(evidence.get("snippets"), dict) else None
    bad: dict[str, str] = {}
    for claim in CRITICAL_CLAIMS:
        urls = [i.get("url") for i in ev.get(claim) or [] if isinstance(i, dict)]
        good = [u for u in urls if _well_formed(u)]
        if not good:
            bad[claim] = "no well-formed evidence URL"
        elif fetched is not None and not any(normalize_url(u) in fetched for u in good):
            bad[claim] = "evidence URL was not a fetched page"
        elif snippets is not None and not any(isinstance(s, dict) and s.get("verified")
                                              for s in snippets.get(claim) or []):
            bad[claim] = "no quoted snippet was found in the cited page text"
    if not bad:
        return []
    reason = "; ".join(f"{c}: {why}" for c, why in bad.items())
    return [Finding("unsupported_evidence", "code", reason, claim_fields(bad))]


# ---------- existing validators as a fired rule ----------

_SCHEMA_PATH = re.compile(r"^schema: ([a-z_]+)(?:/([a-z_]+))?")
_REQUIRED = re.compile(r"'([a-z_]+)' is a required property")
ALL_CRITICAL = claim_fields(CRITICAL_CLAIMS)


def fields_for_error(err: str) -> tuple[str, ...]:
    """Which researched fields a validate_row error is about (all critical fields when unclear)."""
    if err.startswith("rule 1:"):
        return CLAIM_FIELDS["auth"]
    if err.startswith(("rule 7", "rule 8", "rule verdict")):
        return ("access", "verdict", "blocker")
    if err.startswith("rule 9"):
        return ("blocker",)
    if err.startswith("rule api_types"):
        return ("api_types",)
    if err.startswith("rule notes") and "mcp" in err.lower().split("but", 1)[-1]:
        return CLAIM_FIELDS["mcp"]
    m = _SCHEMA_PATH.match(err)
    if m:
        top, sub = m.groups()
        if top == "evidence" and sub in CLAIM_FIELDS:
            return CLAIM_FIELDS[sub]
        if top in FIELD_CLAIM or top in ("api_breadth", "one_liner", "confidence", "requires_end_user_admin"):
            return (top,)
    m = _REQUIRED.search(err)
    if m and m.group(1) in FIELD_CLAIM:
        return (m.group(1),)
    return ALL_CRITICAL


def validator_findings(row: dict, identity: dict) -> list[Finding]:
    """validate_row errors as findings. The OAuth notes contradiction folds into mcp_only_oauth."""
    out = []
    for err in validate_row(row, identity):
        if err.startswith("rule identity"):
            continue  # identity is re-copied from the CSV before review; never researched
        if err.startswith("rule notes") and "oauth" in err.lower().split("but", 1)[0]:
            out.append(Finding("mcp_only_oauth", "validator", err, RULES["mcp_only_oauth"][1]))
        else:
            out.append(Finding(VALIDATION_RULE, "validator", err, fields_for_error(err)))
    return out


def merge_findings(*groups: list[Finding]) -> list[Finding]:
    """All findings, ordered by RULE_ORDER, then validator / code / llm."""
    order = {r: i for i, r in enumerate(RULE_ORDER)}
    src = {"validator": 0, "code": 1, "llm": 2}
    return sorted((f for g in groups for f in g), key=lambda f: (order.get(f.rule, 99), src.get(f.source, 9)))


def rules_fired(findings: list[Finding]) -> list[str]:
    return list(dict.fromkeys(f.rule for f in findings))
