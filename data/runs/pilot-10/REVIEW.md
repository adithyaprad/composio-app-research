# pilot-10 hand review (before baseline)

Run on `main` at `402449a` (hybrid free-first tools, Instant inactive so $0 Composio spend).
10 apps, one per category, none from the gold sample in `schema/gold_sample.md`.
All 10 rows were schema-valid. Wall time 170.6s at concurrency 3. LLM estimate $0.897 total ($0.090/app).

This file freezes the product truth used to score the run, and the before scores.
Do not treat these 10 slugs as special cases in agent code; they are a regression sample only.

## Product truth (critical fields)

| slug | primary_auth (methods) | access | api_types | mcp_status | verdict (blocker) | notes on truth |
|---|---|---|---|---|---|---|
| twenty | oauth2 (api_key, oauth2) | self_serve | rest, graphql | official | buildable_now (none) | docs.twenty.com/developers/extend/oauth documents API OAuth2 for third-party apps (auth code + PKCE + client credentials), so primary_auth must be oauth2. |
| plain | api_key (api_key only) | self_serve | graphql | official | buildable_now (none) | OAuth2 exists only for the MCP server, not the product API, so it must not appear in auth_methods. |
| twilio | api_key or oauth2 (api_key, basic, and oauth2 if documented API OAuth2 is cited) | self_serve | rest (must not include sdk_only) | official | buildable_now (none) | `sdk_only` is invalid alongside `rest`. Twilio also documents API OAuth2; missing it is a field miss only if a fetched page states it. |
| gohighlevel | oauth2 (oauth2, api_key) | self_serve | rest | official | buildable_now (none) | Official MCP is documented at marketplace.gohighlevel.com/docs/other/mcp/ (server at services.leadconnectorhq.com/mcp/...). |
| amazon-selling-partner | oauth2 (oauth2) | gated_partner | rest | community | buildable_with_approval (gated_access) | Public developers need Amazon Developer Profile approval (1-2 week review) and Selling Partner Appstore listing. That is a vendor review gate, not self_serve_paid. |
| sherlock | none (none) | self_serve | cli_only | community | buildable_now (none) | Local CLI tool. |
| datadog | api_key (api_key, oauth2) | self_serve | rest | official | buildable_now (none) | API keys are the common path; Datadog also documents OAuth2 PKCE for Marketplace / API integrations (docs.datadoghq.com/extend/authorization/oauth2_in_datadog.md). oauth2 may appear in auth_methods when that page (or equivalent) is cited. Notes must not claim OAuth is MCP-only while auth_methods still lists oauth2. |
| linear | oauth2 (oauth2, api_key) | self_serve | graphql | official | buildable_now (none) | mcp_url may be the protocol endpoint mcp.linear.app/mcp; evidence should still cite the docs page. |
| paygent-connect | (must be the NMI-powered Paygent Connect product at gopaygent.com, not paygent.to) | (from that product's docs) | (from that product's docs) | (from that product's docs) | (from that product's docs) | CSV start_url is blank; source_hint is "paygent (NMI-powered)". Researching paygent.to (an unrelated LLM cost-metering tool) is a wrong-product miss. |
| devin | api_key (api_key) | self_serve_paid | rest | official | buildable_now (none) | Teams plan is self-serve paid (org `cog_` keys). |

## Before scores (pilot-10)

Row-correct under the rubric (critical fields + evidence support): **3/10 clean** (sherlock, linear, devin). twenty is borderline (primary_auth should be oauth2). 6 rows have real errors.

Per-field misses (a row can miss more than one field):

- auth: twenty (primary_auth api_key instead of oauth2); plain (oauth2 included though MCP-only); datadog (notes said OAuth is MCP-only and even recommended dropping it, but auth_methods still listed oauth2). twilio also missed documented API OAuth2 because that page was not fetched — counted as a soft miss, not a row fail by itself.
- api_types: twilio (`rest` + `sdk_only` together).
- mcp_status: gohighlevel (`none` instead of official).
- access + verdict + blocker: amazon-selling-partner (`self_serve_paid` / `buildable_now` / `none` instead of `gated_partner` / `buildable_with_approval` / `gated_access`).
- identity: paygent-connect researched paygent.to instead of Paygent Connect (gopaygent.com).

## Cost and time

| slug | wall_s | est_usd | instant_calls |
|---|---|---|---|
| twenty | 13.20 | 0.0656 | 0 |
| twilio | 22.82 | 0.0653 | 0 |
| plain | 42.54 | 0.0545 | 0 |
| gohighlevel | 51.39 | 0.1308 | 0 |
| sherlock | 25.85 | 0.0601 | 0 |
| datadog | 19.24 | 0.0657 | 0 |
| linear | 39.48 | 0.0655 | 0 |
| amazon-selling-partner | 91.30 | 0.1935 | 0 |
| devin | 17.79 | 0.0587 | 0 |
| paygent-connect | 85.37 | 0.1370 | 0 |
| TOTAL | 408.98s cpu / 170.6s wall | 0.8968 | 0 |
