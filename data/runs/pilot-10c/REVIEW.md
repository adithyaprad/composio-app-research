# pilot-10c hand review (direct Exa provider)

Same 10 apps as `data/runs/pilot-10/` and `data/runs/pilot-10b/`, scored against the same product truth in `data/runs/pilot-10/REVIEW.md`, including the Plain GraphQL OAuth clarification in the 10b review, plus one new evidence-backed clarification for Amazon below.

Run on `gig/exa-provider` at `751869c`. Search and fetch were the direct Exa API (`SEARCH_PROVIDER=exa`, `FETCH_PROVIDER=exa`), with ddgs+http as automatic fallback (used once for GoHighLevel and once for Paygent). All 10 rows schema-valid, 0 validation retries except Paygent (2 LLM calls after a consistency check). Wall time 78.8s at concurrency 3. LLM estimate $0.802 total ($0.080/app), computed at $3/$15 per MTok (the log's `est_cost_usd` field was null). Exa spend $0.367 (`costDollars` summed over 67 calls: 53 search, 14 contents), under the $5 cap. Instant spend $0.

## Amazon MCP clarification

The 10/10b truth listed Amazon Selling Partner MCP as `community` (a third-party SP-API wrapper). Official first-party docs now describe an Amazon Selling Partner Connector (MCP) at `https://sellingpartner-ai.amazon.com/mcp`, with a product page at `https://developer.amazonservices.com/amazon-selling-partner-plugin` and the Amazon repo `https://github.com/amzn/selling-partner-agentic-toolkit`. Under the rubric, `mcp_status` should be `official`. The 10c search results included those URLs, but the run fetched the Ads MCP blog and `llms.txt` instead, and set `mcp_status` to `none`.

## After scores (pilot-10c)

Row-correct under the rubric: **8/10** (twenty, plain, twilio, gohighlevel, sherlock, linear, paygent-connect, devin). Remaining misses: amazon-selling-partner (MCP) and datadog (auth notes vs methods). Headline holdout score is unchanged vs 10b (3/10 → 8/10 → 8/10), but the miss set changed: Paygent identity is fixed; a notes-vs-auth-methods contradiction came back on Datadog.

| slug | 10 | 10b | 10c | what changed vs 10b |
|---|---|---|---|---|
| twenty | borderline (primary_auth api_key) | correct | correct | unchanged |
| plain | miss (oauth2 listed as MCP-only) | correct | correct | unchanged; still cites official GraphQL OAuth docs |
| twilio | miss (`rest` + `sdk_only`) | correct | correct | improved: fetched the API security guide and now lists documented API OAuth2 alongside basic and api_key; `api_types` stays `rest` only |
| gohighlevel | miss (mcp none) | correct | correct | official MCP unchanged. `access` moved from `self_serve` to `self_serve_paid` (paid plan after a 14-day trial). That is a reasonable label and is not scored as a row fail; verdict stays `buildable_now` |
| amazon-selling-partner | miss (self_serve_paid / buildable_now) | miss (mcp none vs community) | miss (mcp none vs official) | access/verdict/blocker still `gated_partner` / `buildable_with_approval` / `gated_access`. MCP stayed `none`; official connector pages were in search results but not fetched |
| sherlock | correct | correct | correct | unchanged; still ignores same-name products |
| datadog | miss (notes vs auth_methods) | correct | miss (notes vs auth_methods) | `auth_methods` lists `oauth2` again, but auth evidence is only API/application keys, and the notes say the fetched pages show OAuth in the MCP context only. Official API OAuth pages (`oauth2_endpoints`, OAuth client scopes) appeared in search results and were not fetched. `access` also moved to `self_serve_paid`; a free-trial page was in the search results. The access label alone would be a soft miss; the notes-vs-methods contradiction is the row fail, same pattern as pilot-10 |
| linear | correct | correct | correct | unchanged |
| paygent-connect | miss (wrong product: paygent.to) | miss (found gopaygent.com, rejected it) | correct | identified the NMI-powered product at gopaygent.com. Fields are a fair reading of the official pages (Bearer API key on the homepage, REST advertised, "free plan / no credit card", no MCP), at low confidence because the docs page is thin. Japanese GitLab "paygent-connect" pages were correctly ignored |
| devin | correct | correct | correct | still correct; `mcp_url` now points at the English docs path |

## Per-field misses (critical fields only)

| field | 10 (of 10) | 10b (of 10) | 10c (of 10) |
|---|---|---|---|
| auth (methods + primary) | 3 (twenty primary, plain contradiction, datadog contradiction) | 0 | 1 (datadog notes vs methods; oauth2 listed without a fetched API OAuth page) |
| api_types | 1 (twilio sdk_only+rest) | 0 | 0 |
| mcp_status | 1 (gohighlevel) | 1 (amazon community MCP missed) | 1 (amazon official Selling Partner MCP missed) |
| access + verdict + blocker | 1 (amazon) | 0 | 0 |
| product identity | 1 (paygent) | 1 (paygent found gopaygent.com but rejected it) | 0 |
| **row-correct** | **3/10** (plus 1 borderline) | **8/10** | **8/10** |

## Cost and time

LLM $ computed at $3/$15 per MTok. Exa $ is the sum of `costDollars` reported by the Exa API (an estimate, not an invoice).

| slug | wall_s | llm_usd | exa_usd | exa_calls | llm_calls |
|---|---|---|---|---|---|
| twenty | 15.76 | 0.0854 | 0.035 | 5 | 1 |
| twilio | 16.93 | 0.0728 | 0.036 | 6 | 1 |
| plain | 18.66 | 0.0758 | 0.036 | 6 | 1 |
| gohighlevel | 20.59 | 0.0795 | 0.028 | 6 | 1 |
| sherlock | 14.94 | 0.0782 | 0.029 | 5 | 1 |
| datadog | 18.13 | 0.0830 | 0.044 | 8 | 1 |
| linear | 21.80 | 0.0675 | 0.037 | 8 | 1 |
| amazon-selling-partner | 25.39 | 0.0871 | 0.043 | 7 | 1 |
| paygent-connect | 36.11 | 0.0893 | 0.035 | 8 | 2 |
| devin | 19.99 | 0.0828 | 0.044 | 8 | 1 |
| TOTAL | 208.3s cpu / 78.8s wall | 0.8016 | 0.367 | 67 |  |

Compared with the hybrid free-first runs (170.6s / $0.897 LLM, then 185.8s / $0.947 LLM), the Exa run was much faster and a little cheaper on the model, plus $0.367 Exa, for about $1.17 all-in ($0.12/app).
