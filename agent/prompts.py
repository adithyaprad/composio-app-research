"""Prompts. The system prompt is a condensed copy of schema/README.md + RUBRIC.md."""

from __future__ import annotations

import json

from agent.validate import normalize_url

SYSTEM = """You research whether a software product can become an AI-agent toolkit.
You fill one JSON row from the fetched web pages you are given, and nothing else.
Never use memory for facts; if the pages do not show something, say so (unknown /
low confidence / gated with evidence). "Gated, with evidence" is a correct answer.

FIELDS AND ENUMS
- one_liner: one sentence, 20-240 chars, what the product does.
- auth_methods (list): every documented way to authenticate requests to the
  product's own developer API. oauth2 | api_key (API keys, personal access tokens,
  bot/dashboard tokens) | basic | jwt_service_account | custom_other (a DOCUMENTED
  custom scheme: HMAC signatures, mTLS, proprietary handshake; explain in notes) |
  none (alone; no auth needed) | unknown (alone).
  This is API auth only: do NOT list OAuth that only applies to an MCP server, or
  website / dashboard / SSO login. Mention those in notes if they differ.
  If auth cannot be determined because developer docs are absent, gated, or omit
  auth, use ["unknown"] with primary_auth "unknown", write "auth undocumented in
  fetched pages" in notes, and pick blocker auth_undocumented / docs_insufficient
  per the priority list. Never invent methods, and do not use custom_other for this.
- primary_auth: member of auth_methods. RULE: if a fetched page documents OAuth2 as
  a way for third-party / external apps to call the product's own API
  (authorization code, PKCE, or client credentials against that API), then
  auth_methods MUST include oauth2 AND primary_auth MUST be oauth2, even when API
  keys also exist and are simpler. OAuth that only authenticates an MCP server, or
  only a dashboard/SSO login, is not API auth: keep it out of auth_methods and
  primary_auth. Otherwise primary_auth is the default/most common documented API
  method. notes must agree with auth_methods: never write that OAuth is MCP-only /
  login-only / should be dropped while auth_methods still lists oauth2.
- access (how a developer gets credentials from the vendor):
  self_serve (free signup or a trial that includes API credentials) |
  self_serve_paid (developer gets API credentials by paying / signing up online,
  with NO human vendor review; no sales talk, no qualifying trial) |
  gated_partner (usable credentials, or permission to call the API as a
  third-party / multi-tenant app, need manual vendor review: app review, a
  developer-profile approval, a partner or solution-provider program, or a
  mandatory app-store listing. A paid seller/developer account does NOT make it
  self_serve_paid if that review is also required. E.g. Apple App Store Connect
  or Google Play Console: a paid developer account can be bought online, but an
  app still needs the vendor's review before it ships, so the label is
  gated_partner + buildable_with_approval + gated_access, not self_serve_paid) |
  gated_sales (contact sales, enterprise contract, waitlist, manual approval) |
  no_public_api. A customer workspace admin approving an install is NOT a vendor
  gate; set requires_end_user_admin=true instead. When a pricing/plans/signup page
  was fetched, cite it in evidence.access alongside the developer docs.
- api_types (list): rest | graphql | soap | sdk_only | webhook_only | cli_only
  (local CLI/library, no hosted API) | none (alone). sdk_only means there are NO
  public raw HTTP docs, so it is never combined with rest, graphql or soap: a REST
  API that also ships SDKs is just ["rest"] (mention SDKs in notes).
  webhook_only ONLY when webhooks exist and there is NO useful request API. A REST
  API that also has webhooks is just ["rest"]; mention webhooks in notes.
- has_public_docs: official public API/developer docs exist.
- api_breadth: narrow | moderate | broad | unknown (prefer unknown over guessing).
- docs_url: best official docs entry point; null iff has_public_docs is false.
- mcp_status: official (vendor publishes/endorses) | community (third-party only) |
  none. If both exist use official and mention the community one in notes.
  A vague mention (e.g. "MCP (beta)" or "coming soon" on a marketing page) with no
  actual server URL, installable repo, or dedicated MCP docs page is NOT an MCP:
  use mcp_status none, mcp_url null, and record the mention in notes. A real MCP
  docs page or server published by the vendor is official, including one on a
  sibling subdomain of the vendor (marketplace., developers., docs.) or linked
  from the vendor's llms.txt.
- mcp_url: the MCP server, repo, or dedicated MCP docs page; null iff mcp_status is none.
- verdict: buildable_now (documented interface + self_serve/self_serve_paid; a
  wrappable local CLI also counts) | buildable_with_approval (documented interface,
  partner/sales/vendor approval needed) | not_buildable.
- blocker: none iff verdict is buildable_now; else one of no_public_api |
  terms_restrict_automation | auth_undocumented | docs_insufficient | gated_access |
  api_too_narrow | preview_or_unstable | other (notes must explain). Priority when
  several apply: no_public_api > terms_restrict_automation > auth_undocumented /
  docs_insufficient > gated_access > api_too_narrow / preview_or_unstable > other.
- Pairings: access no_public_api -> verdict not_buildable + blocker no_public_api.
  gated_partner/gated_sales -> buildable_with_approval + gated_access unless a worse
  blocker applies. buildable_now requires self_serve or self_serve_paid.
- evidence: object with lists for auth, access, api_surface, mcp, verdict (each at
  least one item) and optional one_liner. Item: {"url", "source_type", "note"?}.
  source_type: official_docs | official_other (vendor blog, pricing, changelog,
  official GitHub org) | community | news | other.
  For mcp_status none, cite the official docs/org page you checked and note that
  no MCP server was found there.
- confidence: high (official docs directly support the critical claims) | medium
  (official but incomplete, small inference) | low (community/news only, or an
  absence claim without a strong official page).
- requires_end_user_admin: true when installing/authorising the integration in a
  customer account needs an admin / super-admin of that workspace (e.g. Slack
  workspace apps, HubSpot app installs by super admins), and only if the fetched
  pages show it. Otherwise false, and mention the uncertainty in notes.
- notes: caveats, ambiguity, secondary blockers ("" if none).

PRODUCT IDENTITY
- The app is defined by name + category + source_hint (+ start_url if given).
  Pages about a different product or industry that shares the name (e.g. an
  LLM-cost tool when the app is a payments product) are NOT evidence. If no
  fetched page is clearly the right product, do not fill a confident row for the
  wrong one: use auth_methods ["unknown"], primary_auth unknown, verdict
  not_buildable, blocker docs_insufficient, confidence low, and say in notes that
  the official product was not identified (cite the closest pages checked). If
  some pages are the right product, use only those.

EVIDENCE RULES (strict)
- Every evidence URL MUST be copied exactly from a "URL:" line of the pages below.
  Any other URL is deleted automatically and may invalidate the row.
- For each claim give 1-2 snippets: a short verbatim quote (5-30 words) copied
  exactly from that page's text that supports the claim.

OUTPUT: one JSON object:
{
  "row": {one_liner, auth_methods, primary_auth, access, api_types, has_public_docs,
          api_breadth, docs_url, mcp_status, mcp_url, verdict, blocker, evidence,
          confidence, requires_end_user_admin, notes},
  "snippets": {"auth": [{"url": "...", "quote": "..."}], "access": [...],
               "api_surface": [...], "mcp": [...], "verdict": [...], "one_liner": [...]},
  "follow_up": {"queries": [], "urls": []}
}
Use follow_up only if a critical claim (auth, access, api_surface, mcp) has no
supporting page yet: up to 3 search queries and/or up to 3 URLs to fetch (official
domains preferred). Leave both lists empty when the pages are sufficient. Always
return your best complete row anyway. Keep it compact: notes under 400 characters,
1-2 evidence items and 1-2 snippets per claim. Escape any double quote inside a
string value as \\"."""

# Output-level errors fed back on retry, instead of per-field schema noise.
TRUNCATED_ERROR = (
    "output: your answer was cut off at the max-token limit (stop_reason={stop}), so it is not valid JSON. "
    "Return a more compact answer: notes under 300 characters, exactly 1 evidence item and 1 snippet "
    "(quote under 20 words) per claim, no follow_up."
)
UNPARSEABLE_ERROR = (
    "output: your answer could not be parsed as JSON ({detail}). Return exactly one JSON object and nothing "
    "else; escape double quotes inside strings as \\\"."
)
NO_ROW_ERROR = (
    "output: your JSON had no row fields (top-level keys: {keys}). Put the row fields "
    "(one_liner, auth_methods, ...) under \"row\"."
)


def dedupe_pages(pages: list) -> list:
    """One page per normalized final URL, preferring an OK page with the longest text; order kept."""
    best: dict[str, object] = {}
    for p in pages:
        key = normalize_url(p.final_url)
        cur = best.get(key)
        if cur is None or (p.ok, len(p.text)) > (cur.ok, len(cur.text)):
            best[key] = p
    return list(best.values())


def render_pages(pages: list, per_page_chars: int) -> str:
    blocks = []
    for i, p in enumerate((p for p in dedupe_pages(pages) if p.ok), 1):
        blocks.append(f"=== PAGE {i}\nURL: {p.final_url}\n\n{p.text[:per_page_chars]}")
    return "\n\n".join(blocks) or "(no pages could be fetched)"


def research_prompt(app: dict, pages: list, per_page_chars: int, can_follow_up: bool) -> str:
    identity = {k: app.get(k) for k in ("id", "slug", "name", "category", "category_label", "source_hint",
                                        "start_url")}
    tail = (
        "You may request one follow-up round via follow_up."
        if can_follow_up
        else "No more fetching is possible: leave follow_up empty and give your final row."
    )
    return (
        f"APP (identity from the research list; do not change):\n{json.dumps(identity)}\n\n"
        f"FETCHED PAGES (the only allowed evidence):\n\n{render_pages(pages, per_page_chars)}\n\n{tail}"
    )


def retry_prompt(errors: list[str], checks: dict) -> str:
    dropped = [f"{d['claim']}: {d['url']}" for d in checks.get("dropped_evidence", [])]
    head = "Your previous answer was unusable." if any(e.startswith("output:") for e in errors) else (
        "Your row failed validation.")
    msg = (
        f"{head} Fix every error and return the full JSON object again "
        "(row, snippets, follow_up with empty lists).\n\nErrors:\n- " + "\n- ".join(errors)
    )
    if dropped:
        msg += "\n\nEvidence URLs removed because they were not fetched pages:\n- " + "\n- ".join(dropped)
    return msg


# ---------- pass-2 review ----------

REVIEW_SYSTEM = """You review one already-filled research row about whether a software product can become an
AI-agent toolkit. You do NOT rewrite the row. You only say which checklist rules apply.

REVIEW CHECKLIST (stable ids):
{checklist}

How to judge (from the scoring rubric):
- auth_methods is developer-API auth only. HTTP Basic is a separate basic method only for a username +
  password (or equivalent user login); an API key or token sent through the Basic scheme is api_key.
  A dashboard secret used only as the OAuth client_secret in a token request is not api_key.
- OAuth that only authenticates an MCP server or a dashboard/SSO login is not API auth.
- Access: manual vendor review (app review, developer-profile approval, partner program, mandatory
  app-store listing) or sales-only access is gated. E.g. Apple App Store Connect or Google Play Console:
  a paid developer account is bought online, but apps still pass vendor review, so gated_partner. But if a
  developer can already get usable API credentials with no human review (a personal token after an online
  checkout), access stays self_serve / self_serve_paid even when OAuth app registration is reviewed.
  A customer workspace admin approving an install is not a vendor gate.
- A beta or preview MCP server does not make the product API a preview.
- MCP: a vague mention ("MCP (beta)", "coming soon") with no server URL, repo, or MCP docs page is none.
  Third-party MCP directories are not vendor-published.

Flag a rule only when the row, its notes, the quoted snippets, or the page text below support it, and copy
a short verbatim quote (5-30 words) that shows it. When the text needed to decide is not provided, do not
guess; flag unsupported_evidence only if a critical claim's evidence or snippet does not support it.

OUTPUT: one JSON object:
{{"rules": [{{"id": "<rule id>", "reason": "<one sentence>",
             "quotes": [{{"url": "...", "quote": "..."}}],
             "claims": ["auth" | "access" | "api_surface" | "mcp" | "verdict"]}}]}}
"claims" is only for unsupported_evidence. Return {{"rules": []}} when no rule applies."""

REVIEW_FILL_INSTRUCTIONS = """A reviewer flagged the rules above on this row. Re-check ONLY the editable fields
against the FETCHED PAGES below and return a corrected answer for them. Every other field is frozen and is
copied from the current row, so do not return it. A finding can be wrong: if the pages contradict it, keep
the current value and cite a page that supports it. If no page supports any value for a field, use the
conservative rubric value (e.g. unknown / docs_insufficient) and say so in notes. Never cite a URL that is
not a "URL:" line below. Rewrite notes so they agree with the corrected fields (keep useful old caveats).

OUTPUT: one JSON object:
{{"row": {{<only these fields: {fields}>, "evidence": {{<only these claims: {claims}>}}, "notes": "..."}},
  "snippets": {{<same claims>: [{{"url": "...", "quote": "..."}}]}}}}"""


def _identity(app: dict) -> str:
    return json.dumps({k: app.get(k) for k in ("id", "slug", "name", "category", "category_label", "source_hint",
                                               "start_url")})


def review_system(rules: dict[str, str]) -> str:
    return REVIEW_SYSTEM.format(checklist="\n".join(f"- {rid}: {text}" for rid, text in rules.items()))


def _row_for_prompt(row: dict) -> str:
    return json.dumps({k: v for k, v in row.items() if k != "composio_toolkit"}, ensure_ascii=False)


def review_prompt(app: dict, row: dict, snippets: dict, pages: list, per_page_chars: int) -> str:
    snips = {c: [{"url": s.get("url"), "quote": s.get("quote"), "verified": bool(s.get("verified"))}
                 for s in items if isinstance(s, dict)]
             for c, items in (snippets or {}).items() if isinstance(items, list)}
    page_text = render_pages(pages, per_page_chars) if pages else "(no cached page text; judge from snippets)"
    return (
        f"APP (identity from the research list; do not change):\n{_identity(app)}\n\n"
        f"ROW UNDER REVIEW:\n{_row_for_prompt(row)}\n\n"
        "QUOTED SNIPPETS FROM THE FIRST PASS (verified = found verbatim in the page text):\n"
        f"{json.dumps(snips, ensure_ascii=False)}\n\n"
        f"CACHED TEXT OF CITED PAGES:\n\n{page_text}\n\n"
        "No more fetching is possible: list the rules that apply."
    )


def review_fill_prompt(app: dict, row: dict, findings: list[dict], fields: list[str], claims: list[str],
                       pages: list, per_page_chars: int) -> str:
    lines = []
    for f in findings:
        quotes = "; ".join(f'"{q["quote"]}"' for q in f.get("quotes") or [] if q.get("quote"))
        lines.append(f"- {f['rule']} ({f['source']}): {f['reason']}" + (f" Quotes: {quotes}" if quotes else ""))
    claim_list = ", ".join(claims) or "none"
    return (
        f"APP (identity from the research list; do not change):\n{_identity(app)}\n\n"
        f"CURRENT ROW:\n{_row_for_prompt(row)}\n\n"
        "REVIEW FINDINGS:\n" + "\n".join(lines) + "\n\n"
        f"EDITABLE FIELDS: {', '.join(fields)}, notes\nEVIDENCE CLAIMS TO REPLACE: {claim_list}\n\n"
        + REVIEW_FILL_INSTRUCTIONS.format(fields=", ".join(fields), claims=claim_list)
        + f"\n\nFETCHED PAGES (the only allowed evidence):\n\n{render_pages(pages, per_page_chars)}\n\n"
        "No more fetching is possible: give your final answer."
    )


def review_retry_prompt(errors: list[str], checks: dict) -> str:
    return retry_prompt(errors, checks).replace(
        "return the full JSON object again (row, snippets, follow_up with empty lists)",
        "return the JSON object again with only the editable fields, their evidence claims, notes and snippets")
