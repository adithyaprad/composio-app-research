# Pass 1 gold-score review

Automated score: `pass1.json` / `pass1.md`. This note is a human read of each critical mismatch. Gold labels were not changed after seeing the agent rows. Items marked **flag for gold review** should be checked against official docs before any gold fix; a gold fix, if any, would be applied to both pass 1 and later scores.

Scored at 7:20 PM IST on 23 Nov 2026. Headline row accuracy is **9 / 20 (45.0%)**. Coverage lookup is **20 / 20 (100%)**. Required evidence URLs were present on every gold row.

## Critical mismatches

### HubSpot — `api_types`

- Gold: `["rest", "graphql"]`. Agent: `["rest"]`.
- Read: **agent miss.** Official GraphQL docs exist. The agent only cited the REST reference.

### DealCloud — `auth_methods`

- Gold: `["oauth2", "api_key"]`, primary `oauth2`. Agent: `["oauth2"]` only.
- Read: **flag for gold review, lean slightly toward the agent.** Official pages describe both an “API Key” in the user profile and an OAuth2 client-credentials token exchange that uses that key as `client_secret`. If the key is only the OAuth secret and cannot call the API on its own, gold may be over-counting `api_key`. Do not change the label until that is confirmed.

### Zendesk — `auth_methods`

- Gold: `["oauth2", "api_key"]`. Agent: `["oauth2", "api_key", "basic"]`.
- Read: **agent miss.** API tokens are already `api_key`. HTTP Basic is how those tokens are often sent, not a third credential type. Same pattern as Mailchimp and GitHub.

### Pylon — `access` / `verdict` / `blocker`

- Gold: `gated_sales` + `buildable_with_approval` + `gated_access`.
- Agent: `self_serve_paid` + `buildable_now` + `none`.
- Read: **agent miss.** The pricing page lists seat prices but checkout is “Book a demo” / “Talk to sales”, and the FAQ says free trials are set up by a sales representative. Published prices are not a self-serve checkout. The agent’s own notes mention demo/sales.

### WhatsApp Business — several critical units

- Auth: gold `["oauth2", "api_key"]` / `oauth2`. Agent `["api_key"]` / `api_key`.
  - Read: **judgement, leave gold.** The agent treated system-user and temporary bearer tokens as `api_key` and skipped OAuth because docs also say “OAuth (not 2.0)”. Gold includes OAuth because Embedded Signup’s documented code exchange is how third-party apps obtain customer credentials.
- Access / verdict / blocker: gold `gated_partner` + `buildable_with_approval` + `gated_access`. Agent `self_serve` + `buildable_now` + `none`.
  - Read: **agent miss on the toolkit path.** Official App Review docs say an app that other businesses will use needs Advanced access / App Review. Direct-developer test access is real, but that is not the multi-tenant path the rubric describes.
- MCP: gold `none`. Agent `official` (`https://mcp.facebook.com/whatsapp_business_tools`, documented at `https://developers.facebook.com/documentation/mcp`).
  - Read: **flag for gold review — gold looks wrong.** Meta publishes an official WhatsApp Business Tools MCP server. This was missed during labelling. Do not silently edit gold here; if the official page is accepted, update gold later and rescore both passes.

### Mailchimp — `auth_methods`

- Gold: `["oauth2", "api_key"]`. Agent: `["oauth2", "api_key", "basic"]`.
- Read: **agent miss.** Docs say an API key or OAuth token can be sent as HTTP Basic or Bearer. That is transport for the same credentials, not a separate method.

### Clay — `auth_methods` / `primary_auth`

- Gold: `["api_key"]` / `api_key`. Agent: `["api_key", "oauth2"]` / `oauth2`.
- Read: **agent miss, and self-contradictory.** Official Public API auth is the `clay-api-key` header only. The agent’s notes say the documented OAuth is for the MCP server, then still listed `oauth2` as API auth and made it primary. MCP-only OAuth should not appear in `auth_methods`.

### GitHub — `auth_methods`

- Gold: `["oauth2", "api_key", "jwt_service_account"]`. Agent also listed `basic`.
- Read: **agent miss.** PATs and app credentials are already covered. Basic is a way to send some of those secrets, not a fourth method.

### Smartsheet — `access` / `verdict` / `blocker`

- Gold: `self_serve_paid` + `buildable_now` + `none`.
- Agent: `gated_partner` + `buildable_with_approval` + `gated_access`.
- Read: **flag for gold review; both readings have official support.** Business plan can be bought online and includes personal API tokens (no vendor review), which is why gold is `self_serve_paid`. OAuth “Developer Tools” registration says Smartsheet activates it “upon accepting your request” and “if Smartsheet denies your request, consult with your Customer Success Manager.” A multi-tenant OAuth app may therefore be partner-gated. Leave gold unchanged until that call is reviewed.

### PitchBook — `has_public_docs` (and `docs_url`)

- Gold: `has_public_docs: true`, docs at the official help page. Agent: `false` / `null`, while still labelling a REST API.
- Read: **agent miss, borderline.** There is no public full reference, but official help and product pages describe the REST API at `api.pitchbook.com`. That is enough for `has_public_docs: true`. The access gate is already captured as `gated_sales`.

### NotebookLM — `verdict` / `blocker`

- Gold: `not_buildable` + `preview_or_unstable` (acceptable alternative: Enterprise path as `gated_sales` + `buildable_with_approval` + `gated_access`).
- Agent: `buildable_now` + `none`.
- Read: **agent miss.** Official docs call the Gemini Notebook API an Early Access Public Preview, and the agent’s own notes say the same. The rubric does not treat an unstable preview as `buildable_now`. The agent also did not hit the acceptable Enterprise alternative.

## Patterns (for later, not a pass-2 change)

The 11 row failures cluster into a few general issues, not one-off product bugs:

1. Treating HTTP Basic as a separate auth method when it only carries an API key or token (Zendesk, Mailchimp, GitHub).
2. Listing MCP-only OAuth as API auth (Clay), or missing a documented extra API style (HubSpot GraphQL).
3. Misreading a sales-led or review-gated path as self-serve (Pylon, WhatsApp access), or the reverse ambiguity on Smartsheet OAuth registration.
4. Ignoring an explicit preview label (NotebookLM).

Coverage lookup (`composio_toolkit` status + primary slug) was perfect on all 20 and is not part of the 45% headline number.
