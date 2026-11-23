# Gold-sample audit

This audit re-checked every field of the 20 gold rows against current official vendor docs (developer docs, pricing or signup pages, official MCP pages, and app-review docs) and the written rubric. It was done **after pass-1 scoring**, because scoring had raised possible label issues. The audit itself used official docs and the rubric only. It did **not** read agent rows in `data/runs/` or prior score reports in `data/scores/`.

Where official docs proved a gold label wrong, the label was corrected and logged below. Where the rubric was genuinely ambiguous, a general (no app names) clarification was added to `RUBRIC.md` and `schema/README.md`, then applied consistently.

## Rubric clarifications added

1. **OAuth client secret vs `api_key`.** A dashboard or profile secret is `api_key` only if it can authenticate API requests by itself. If official docs use that secret only as the `client_secret` (or equivalent) in an OAuth token request, it is not a separate `api_key` method.
2. **HTTP Basic as transport.** HTTP Basic is a separate `basic` method only when the credentials are a username and password (or equivalent user login). Sending an API key or token via the HTTP Basic scheme is still `api_key` (or `oauth2` for an OAuth access token), not an extra `basic` value.
3. **Self-serve credentials vs optional OAuth registration review.** If a developer can already get usable API credentials with no human vendor review (for example a personal token after buying a plan online), `access` is `self_serve` or `self_serve_paid` even when a separate OAuth app-registration path may be reviewed. Use `gated_partner` when calling the API as a third-party or multi-tenant app requires vendor review *and* there is no self-serve credential path that already lets you call the API.

## Fields changed

| App | Field | Old | New | Official URL | Quote | Rubric rule |
|---|---|---|---|---|---|---|
| WhatsApp Business | `mcp_status` | `none` | `official` | https://developers.facebook.com/documentation/mcp/whatsapp-business-tools-mcp | “For developers building on the WhatsApp Business Platform (Cloud API) — the WhatsApp Business Tools MCP server… Endpoint: `https://mcp.facebook.com/whatsapp_business_tools`.” | `official` means the vendor publishes or endorses the server (docs, official repo, or official announcement). A real vendor MCP docs page and server URL is `official`, including beta. |
| WhatsApp Business | `mcp_url` | `null` | `https://developers.facebook.com/documentation/mcp/whatsapp-business-tools-mcp` | https://developers.facebook.com/documentation/mcp | “Meta runs official remote Model Context Protocol (MCP) servers… WhatsApp Business Tools MCP: `https://mcp.facebook.com/whatsapp_business_tools`.” | `mcp_url` is required when `mcp_status` is not `none`. A docs page for the same official server is fine. |
| WhatsApp Business | `evidence.mcp`, `notes` | Evidence said official Cloud API docs were checked and no official MCP was found | Evidence and notes now cite Meta’s MCP docs and server URL | https://developers.facebook.com/documentation/mcp/whatsapp-business-tools-mcp | Same as above. Status is documented as beta and rolling out gradually; it is still a real vendor-published server. | Evidence must support the claim. Access, auth, verdict, and blocker were already `gated_partner` / `buildable_with_approval` / `gated_access` for App Review and were left unchanged. |
| DealCloud | `auth_methods` | `["oauth2", "api_key"]` | `["oauth2"]` | https://api.docs.dealcloud.com/docs/token | “DealCloud uses the OAuth2 Client Credentials flow… providing its `client_id`, `apiKey`, and the required scope(s).” The example POST uses `grant_type=client_credentials`, `client_id`, and `client_secret`. | Auth is scored for the developer API only. A profile “API key” used only as the OAuth `client_secret` is not a separate `api_key` method (clarification 1). `primary_auth` stays `oauth2`. |
| DealCloud | `notes`, auth evidence notes | Notes treated the Profile API key as a second method | Notes and evidence now state that the Profile API key is the OAuth client secret | https://api.docs.dealcloud.com/docs/apikeys | “Scroll down to the API Key section and click Enable. Then click Copy API Key to Clipboard to copy your API key for use in integrations or API requests.” That page does not document a non-OAuth request header. | Same as above. Access (`gated_sales`), MCP (`official`), and verdict were confirmed and left unchanged. |
| fanbasis (Commas) | `mcp_status` | `none` | `official` | https://commasdocs.com/#ai-agent | Official API reference section “Connect an AI Agent”: “Connect any MCP-compatible AI agent — Claude, ChatGPT, or Grok — directly to your account.” ChatGPT setup: “Paste the server URL: `https://www.fanbasis.com/mcp`.” | A real MCP docs section and server URL published by the vendor is `official`. |
| fanbasis (Commas) | `mcp_url` | `null` | `https://commasdocs.com/#ai-agent` | https://commasdocs.com/#ai-agent | Server URL documented as `https://www.fanbasis.com/mcp`. Changelog (February 2026) also announces “AI Agent MCP Integration.” | `mcp_url` may be the official docs page for that server. REST auth stays `api_key` only: MCP OAuth, where present, authenticates the MCP server, not the Public API. |
| fanbasis (Commas) | `evidence.mcp`, `notes` | Evidence said no official MCP was found | Evidence and notes now cite the official MCP section and server | https://commasdocs.com/#ai-agent | Same as above. | Evidence must support the claim. Auth, access, and verdict were confirmed (`api_key`, `self_serve`, `buildable_now`). |
| Smartsheet | `evidence.verdict` note; `notes` | Verdict evidence said a developer can “create API tokens or an OAuth app, with no human vendor review” | Verdict evidence now rests on the Business-plan personal token path only; notes record that OAuth Developer Tools registration may be accepted or denied | https://developers.smartsheet.com/register and https://developers.smartsheet.com/api/smartsheet/guides/advanced-topics/oauth | Register: “If your application interacts with Smartsheet directly and does not require user authorization, you do not need to register for Developer Tools.” OAuth guide: “Upon accepting your request… If Smartsheet denies your request, consult with your Customer Success Manager.” Pricing: Business has “Start now”; “API calls” are included on Business and above, not Pro. Getting started: “Required plan: Business, Enterprise, or Advanced Work Management” and personal tokens are generated in the UI. | Clarification 3: usable personal tokens after a self-serve paid checkout keep `access` as `self_serve_paid` and `verdict` as `buildable_now`. Those scored fields were confirmed, not changed. |
| Mailchimp | `evidence.mcp`; `notes` | Notes described only the docs-search Marketing MCP | Notes and evidence also cite the official Transactional Messaging MCP | https://mailchimp.com/developer/transactional/guides/how-to-use-mailchimps-transactional-messaging-mcp | “The MCP server URL is `https://mandrillapp.com/mcp`.” The Marketing docs MCP remains: “This searches documentation, not your Mailchimp account… `https://mailchimp.com/developer/_mcp/server`.” | If official and community exist, label official; two official servers stay `official`. `mcp_status` and `mcp_url` were already official and were left pointing at the Marketing docs MCP page. HTTP Basic-as-transport stays out of `auth_methods` (clarification 2). |

## Rows confirmed unchanged

The scored fields below were checked against current official docs and left as they were. Minor wording in `label_notes` was not added unless a change or a tight call is recorded above.

| App | Confirmed critical labels | Notes from the audit |
|---|---|---|
| HubSpot | `oauth2` + `api_key`, `self_serve`, `rest` + `graphql`, official MCP, `buildable_now` | OAuth required for multiple accounts; service keys for a single account; GraphQL is documented; MCP at `https://mcp.hubspot.com`. |
| DealCloud | access `gated_sales`, official MCP, `buildable_with_approval` / `gated_access` | Tenant and CSM path confirmed. Only auth_methods changed (see above). |
| Zendesk | `oauth2` + `api_key`, `self_serve`, `rest`, official MCP, `buildable_now` | API tokens remain documented until 30 Apr 2027. Official MCP is in an EAP but has a real server URL (`https://example.zendesk.com/mcp`). HTTP Basic transport of tokens is not a separate `basic` method. |
| Pylon | `api_key`, `gated_sales`, `rest`, official MCP, `buildable_with_approval` | Pricing is “Book a demo” / “Talk to sales”; FAQ: trials are set up by a sales representative. MCP OAuth is MCP-only, correctly omitted from `auth_methods`. |
| Slack | `oauth2` + `api_key`, `self_serve`, official MCP, `buildable_now`, `requires_end_user_admin: true` | Workspace admin approval is not a vendor gate. Official MCP docs are on docs.slack.dev. |
| WhatsApp Business | `oauth2` + `api_key`, `gated_partner`, `rest`, `buildable_with_approval` / `gated_access` | App Review / Advanced access is still required to call the API as a third-party app. Only MCP changed (see above). |
| Mailchimp | `oauth2` + `api_key`, `self_serve`, `rest`, official MCP, `buildable_now` | Marketing API documents API keys and OAuth 2. Basic is transport. MCP extras are in the change table. |
| LinkedIn Ads | `oauth2`, `gated_partner`, `rest`, `mcp_status: none`, `buildable_with_approval` | Advertising API access requires LinkedIn’s application review; all apps start in Development tier. Official Microsoft Learn and developer pages do not document an MCP server (see residual uncertainty). |
| Shopify | `oauth2` + `api_key`, `self_serve`, `rest` + `graphql`, official MCP, `buildable_now` | Storefront Catalog MCP is a real official server. A separate Admin “Shopify MCP” page still says “Coming soon” / “isn’t publicly available yet,” which would be `none` on its own. App Store listing is optional for credentials. |
| fanbasis | `api_key`, `self_serve`, `rest`, `buildable_now` | Public Commas docs and dashboard API keys confirmed. Only MCP changed (see above). |
| Firecrawl | `oauth2` + `api_key` (primary `oauth2`), `self_serve`, official MCP, `buildable_now` | “OAuth for apps” issues `fco_` tokens that work on the REST API, so `oauth2` is correctly included and preferred as primary. |
| Clay | `api_key`, `self_serve`, official MCP, `buildable_now` | Public API uses `clay-api-key`. MCP at `https://api.clay.com/v3/mcp` uses OAuth / Dynamic Client Registration, correctly omitted from API `auth_methods`. Free plan / trial is self-serve. |
| GitHub | `oauth2` + `api_key` + `jwt_service_account`, `self_serve`, `rest` + `graphql`, official MCP, `buildable_now` | PATs, OAuth, and GitHub App JWTs are all documented API auth. Official MCP repo is `github/github-mcp-server`. |
| Snowflake | `oauth2` + `api_key` + `jwt_service_account`, `self_serve`, official MCP, `buildable_now` | Trial accounts are self-serve (email only, no contract). REST auth includes key-pair JWTs, OAuth, and PATs. Official Cortex MCP docs exist. |
| Notion | `oauth2` + `api_key`, `self_serve`, official MCP, `buildable_now` | Public integrations use OAuth; internal tokens are `api_key`. Official MCP at `https://mcp.notion.com/mcp`. |
| Smartsheet | `oauth2` + `api_key` (primary `oauth2`), `self_serve_paid`, official MCP, `buildable_now` | Scored fields confirmed; evidence/notes tightened (see change table). |
| Stripe | `oauth2` + `api_key` (primary `oauth2`), `self_serve`, official MCP, `buildable_now` | Secret keys and Connect OAuth are both documented for the Stripe API. Basic is transport for keys. Official MCP at `https://mcp.stripe.com`. |
| PitchBook | `api_key`, `gated_sales`, `rest`, official MCP, `buildable_with_approval` | Official help URL states the API is a separate offering that requires a standalone contract / Direct Data request, and is a REST API. Official press announcement describes PitchBook Data MCP as live and available to customers on request. Live HTML fetches of those pages returned 403 from this audit environment; the URLs and indexed official wording were still used. |
| Reducto | `api_key`, `self_serve`, official MCP, `buildable_now` | Self-serve signup and API keys; official hosted MCP at `https://mcp.reducto.ai/mcp`. |
| NotebookLM | `oauth2`, `self_serve`, `rest`, official MCP, `not_buildable` / `preview_or_unstable` | Gemini Notebook API is an early-access Public Preview (pre-GA) for personal Google Accounts; OAuth only; official MCP at `https://notebook.googleapis.com/mcp`. The Enterprise alternative (license-gated, also Preview / Pre-GA, `v1alpha`) remains recorded in `acceptable_alternatives`. |

`composio_toolkit` for all 20 rows still matches the frozen catalog snapshot in `data/composio_coverage.csv`.

## Residual uncertainty

These points were investigated and did not change a gold label, but they should stay visible:

- **LinkedIn Ads MCP.** `https://mcp.linkedin.com/marketing/mcp` responds `401` with OAuth `WWW-Authenticate` metadata (`resource_metadata` on `mcp.linkedin.com`, scopes such as `r_ads`). Official LinkedIn Marketing docs on Microsoft Learn, the Advertising API product page, and `llms.txt` probes do not publish or announce that server. Third-party writeups mention it. Per the rubric, `official` requires vendor publication or endorsement (docs, official repo, or official announcement), so the gold label stays `none`. If LinkedIn later publishes the server, gold should be updated.
- **PitchBook live pages.** Official URLs could not be re-fetched as HTML in this environment (HTTP 403 / timeout). Labels rely on the official URLs plus indexed official wording from those same pages, which still matches the existing gold claims. Confidence on PitchBook is slightly lower than on rows whose pages were read in full this turn, but not low enough to withdraw the row.
- **WhatsApp Business Tools MCP availability.** Meta documents the server as beta and “rolling out gradually,” so it may not be enabled for every developer account yet. It is still a vendor-published official server, so `official` is the correct status.
- **Smartsheet OAuth registration.** Developer Tools registration may be denied. That does not change `access` or `verdict` under clarification 3, because personal tokens on a bought Business plan already call the API. A multi-tenant OAuth-only reading is a possible policy debate; it is not the written rule after the clarification.

## Pass-1 scores before and after this audit

Pass 1 was re-scored with `scripts/score_gold.py` on the run artifacts from `origin/gig/pass1-run` (exported; that branch was not merged). Reports were written to `data/scores/pass1.json` and `data/scores/pass1.md` (overwritten). Scored at 2026-11-23, 7:38 PM IST.

- **Before (pre-audit gold):** 9 / 20 rows fully correct (**45.0%**). Per-field: auth 70%, access 85%, API types 90%, MCP 95%, verdict 80%, blocker 80%. Coverage lookup 20 / 20. Pre-audit reports remain in git at `4ab0878`.

- **After (audited gold):** 9 / 20 rows fully correct (**45.0%**). Per-field: auth **75%** (15 / 20), access 85%, API types 90%, MCP 95% (19 / 20), verdict 80%, blocker 80%. Coverage lookup 20 / 20. `mcp_url` is 18 / 19 (94.7%) because one more gold row now has an official MCP.

Headline row accuracy did not change. Two row-level effects cancelled out:

- DealCloud became row-correct: gold `auth_methods` is now `["oauth2"]`, which matches the pass-1 row (the previous miss was gold listing a separate `api_key`).
- fanbasis became a row miss: gold `mcp_status` is now `official`, and the pass-1 row has `none`.
- WhatsApp Business MCP now matches (`official`), but that row still fails on auth, access, verdict, and blocker, so it stays incorrect.

The same audited gold will be used for both pass-1 and final scoring.
