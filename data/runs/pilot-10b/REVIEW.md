# pilot-10b hand review (after v2 fixes)

Same 10 apps as `data/runs/pilot-10/`, scored against the same product truth in that folder's REVIEW.md, with one evidence-backed clarification: Plain's official docs at `https://www.plain.com/docs/graphql/oauth.md` document an OAuth 2.0 authorization-code + PKCE flow for third-party apps calling the GraphQL API (machine users still use API keys). Under the rubric, `primary_auth` must be `oauth2`. The pilot-10 notes that treated Plain OAuth as MCP-only were wrong.

Run on `gig/agent-v2-fixes` at `a91709a`. All 10 rows schema-valid. Wall time 185.8s at concurrency 3. LLM estimate $0.947 total ($0.095/app), computed at $3/$15 per MTok (the log's `est_cost_usd` field was null on this run). Instant spend $0 (Firecrawl still "no active connection").

## After scores

Row-correct under the rubric: **8/10** (twenty, plain, twilio, gohighlevel, sherlock, datadog, linear, devin). Remaining misses: amazon-selling-partner (community MCP) and paygent-connect (identity).

| slug | before | after | what changed |
|---|---|---|---|
| twenty | borderline (primary_auth api_key) | correct | primary_auth now oauth2, citing docs.twenty.com/developers/extend/oauth |
| plain | miss (oauth2 listed as MCP-only, self-contradicting notes) | correct | notes no longer contradict; cites official GraphQL OAuth docs; primary_auth oauth2 |
| twilio | miss (`rest` + `sdk_only`) | correct | api_types is `rest` only. Auth is basic + api_key; no fetched page documented API OAuth2 |
| gohighlevel | miss (mcp none) | correct | official MCP at marketplace.gohighlevel.com/docs/other/mcp/index.html |
| amazon-selling-partner | miss (self_serve_paid / buildable_now) | miss (mcp only) | access/verdict/blocker now gated_partner / buildable_with_approval / gated_access. mcp_status dropped from community (KuudoAI SP-API MCP) to none |
| sherlock | correct | correct | unchanged |
| datadog | miss (notes said drop MCP-only OAuth, auth_methods still listed it) | correct | auth_methods is api_key only; notes correctly treat MCP OAuth as MCP-only. The Marketplace/API OAuth2 page was still not fetched |
| linear | correct | correct | unchanged |
| paygent-connect | miss (wrong product: paygent.to) | miss (identity) | did not invent a row for paygent.to; used unknown / not_buildable / docs_insufficient. But it fetched gopaygent.com and rejected it as a "different Paygent product", which is the NMI-powered target. access=gated_sales was noted as a placeholder |
| devin | correct | correct | still correct; mcp_url now a locale path `/es/` |

## Per-field misses (critical fields only)

| field | before (of 10) | after (of 10) |
|---|---|---|
| auth (methods + primary) | 3 (twenty primary, plain contradiction, datadog contradiction) | 0 |
| api_types | 1 (twilio sdk_only+rest) | 0 |
| mcp_status | 1 (gohighlevel) | 1 (amazon community MCP missed) |
| access + verdict + blocker | 1 (amazon) | 0 |
| product identity | 1 (paygent) | 1 (paygent found gopaygent.com but rejected it) |
| **row-correct** | **3/10** (plus 1 borderline) | **8/10** |

## Cost and time

| slug | wall_s | est_usd | instant_calls | llm_calls |
|---|---|---|---|---|
| twenty | 16.68 | 0.0732 | 0 | 1 |
| twilio | 30.30 | 0.0807 | 0 | 1 |
| plain | 50.15 | 0.1308 | 0 | 2 |
| gohighlevel | 61.62 | 0.1468 | 0 | 2 |
| sherlock | 36.97 | 0.0642 | 0 | 1 |
| datadog | 39.62 | 0.0719 | 0 | 1 |
| linear | 23.09 | 0.0600 | 0 | 1 |
| amazon-selling-partner | 98.11 | 0.1612 | 0 | 2 |
| paygent-connect | 54.17 | 0.0904 | 0 | 2 |
| devin | 66.57 | 0.0675 | 0 | 1 |
| TOTAL | 477.28s cpu / 185.8s wall | 0.9467 | 0 |  |
