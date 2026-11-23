"""Prompts. The system prompt is a condensed copy of schema/README.md + RUBRIC.md."""

from __future__ import annotations

import json

SYSTEM = """You research whether a software product can become an AI-agent toolkit.
You fill one JSON row from the fetched web pages you are given, and nothing else.
Never use memory for facts; if the pages do not show something, say so (unknown /
low confidence / gated with evidence). "Gated, with evidence" is a correct answer.

FIELDS AND ENUMS
- one_liner: one sentence, 20-240 chars, what the product does.
- auth_methods (list, all documented): oauth2 | api_key (API keys, personal access
  tokens, bot/dashboard tokens) | basic | jwt_service_account | custom_other (HMAC,
  mTLS; explain in notes) | none (alone; no auth needed).
  If auth cannot be determined because docs are gated or absent, use ["custom_other"],
  write "auth undocumented in fetched pages" in notes, and pick blocker
  auth_undocumented / docs_insufficient per the priority list. Never invent methods.
- primary_auth: member of auth_methods; oauth2 if documented, else the default/most
  common documented method.
- access (how a developer gets credentials from the vendor):
  self_serve (free signup or a trial that includes API credentials) |
  self_serve_paid (must buy a plan, no sales talk, no qualifying trial) |
  gated_partner (partner program, app review, solution-provider approval) |
  gated_sales (contact sales, enterprise contract, waitlist, manual approval) |
  no_public_api. A customer workspace admin approving an install is NOT gated; set
  requires_end_user_admin=true instead.
- api_types (list): rest | graphql | soap | sdk_only (no raw HTTP docs) |
  webhook_only | cli_only (local CLI/library, no hosted API) | none (alone).
  webhook_only ONLY when webhooks exist and there is NO useful request API. A REST
  API that also has webhooks is just ["rest"]; mention webhooks in notes.
- has_public_docs: official public API/developer docs exist.
- api_breadth: narrow | moderate | broad | unknown (prefer unknown over guessing).
- docs_url: best official docs entry point; null iff has_public_docs is false.
- mcp_status: official (vendor publishes/endorses) | community (third-party only) |
  none. If both exist use official and mention the community one in notes.
- mcp_url: the MCP server, repo, or MCP docs page; null iff mcp_status is none.
  If a page only vaguely mentions an MCP (e.g. "MCP (beta)") with no server/docs
  URL, keep mcp_status, point mcp_url at the page that mentions it, say so in notes,
  and set confidence no higher than medium.
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


def render_pages(pages: list, per_page_chars: int) -> str:
    blocks = []
    for i, p in enumerate(pages, 1):
        if not p.ok:
            continue
        blocks.append(f"=== PAGE {i}\nURL: {p.final_url}\n\n{p.text[:per_page_chars]}")
    return "\n\n".join(blocks) or "(no pages could be fetched)"


def research_prompt(app: dict, pages: list, per_page_chars: int, can_follow_up: bool) -> str:
    identity = {k: app[k] for k in ("id", "slug", "name", "category", "source_hint", "start_url")}
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
