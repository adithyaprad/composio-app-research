# pilot-10d hand review (leftover official MCP/auth fetch)

Same 10 apps as `data/runs/pilot-10/`, `pilot-10b/`, and `pilot-10c/`, scored against the same product truth in `data/runs/pilot-10/REVIEW.md`, including the Plain GraphQL OAuth clarification in the 10b review, the Amazon official Selling Partner Connector clarification in the 10c review, and one new Datadog clarification below.

Run on `gig/exa-provider` at `aaf3e99`. Direct Exa search and fetch, with the new gap-fill: up to 3 leftover vendor-owned official MCP or API-auth/OAuth search hits are fetched before the row is settled. All 10 rows schema-valid. Wall time 100.6s at concurrency 3. LLM estimate $1.150 total ($0.115/app), computed at $3/$15 per MTok (`est_cost_usd` was null). Exa spend $0.374 (67 calls: 53 search, 14 contents), under the $5 cap. Instant spend $0. Gap-fill re-asks ran on Plain, Amazon Selling Partner, and Datadog. Sherlock had a validation retry (still correct). Paygent had a follow-up round.

## Datadog auth clarification

The 10/10b truth listed `primary_auth` as `api_key`, with OAuth2 allowed in `auth_methods` only when a Marketplace/API OAuth page was cited. Official docs at `https://docs.datadoghq.com/extend/authorization/oauth2_endpoints.md` (and `.../api/latest/scopes.md`) document an OAuth2 authorization-code + PKCE flow against the Datadog API. Under the rubric (OAuth2 documented for third-party apps calling the API), `primary_auth` must be `oauth2` and `auth_methods` must include `oauth2` and `api_key`. The 10c notes that treated fetched OAuth as MCP-only, while still listing `oauth2`, were a contradiction miss. This run fetched those official API OAuth pages and cites them.

## After scores (pilot-10d)

Row-correct under the rubric: **9/10** (twenty, plain, twilio, gohighlevel, sherlock, datadog, linear, paygent-connect, devin). Remaining miss: amazon-selling-partner (MCP). Headline holdout score: 3/10 → 8/10 → 8/10 → 9/10.

| slug | 10 | 10b | 10c | 10d | what changed vs 10c |
|---|---|---|---|---|---|
| twenty | borderline | correct | correct | correct | unchanged |
| plain | miss | correct | correct | correct | still correct; a leftover auth page triggered a gap-fill re-ask; `docs_url` now points at the GraphQL authentication page |
| twilio | miss | correct | correct | correct | unchanged |
| gohighlevel | miss | correct | correct | correct | unchanged on critical fields (`access` stays `self_serve_paid`, still not scored as a row fail). Confidence dropped to medium |
| amazon-selling-partner | miss (access) | miss (mcp) | miss (mcp none vs official) | miss (mcp none vs official) | access/verdict/blocker still right. Gap-fill did run (12 pages, 2 LLM calls) but spent its slots on `docs.aws.amazon.com` / `aws.amazon.com` MCP overview pages (those hosts sit under the official `amazon.com` parent). The official Selling Partner plugin page (`developer.amazonservices.com/amazon-selling-partner-plugin`) was still in the search results; its URL/title does not contain "mcp", and `amazonservices.com` is not treated as the same vendor name as `amazon.com`. The `amzn` GitHub org also does not match the vendor name `amazon`. `mcp_status` stayed `none` |
| sherlock | correct | correct | correct | correct | one validation retry; still correct |
| datadog | miss (notes vs methods) | correct | miss (notes vs methods) | correct | fetched official API OAuth docs; `primary_auth` is now `oauth2` with cited evidence; `access` is `self_serve`; notes no longer contradict the fields |
| linear | correct | correct | correct | correct | unchanged |
| paygent-connect | miss (wrong product) | miss (rejected gopaygent.com) | correct | correct | still the NMI-powered product at gopaygent.com; confidence medium |
| devin | correct | correct | correct | correct | unchanged |

## Per-field misses (critical fields only)

| field | 10 | 10b | 10c | 10d |
|---|---|---|---|---|
| auth (methods + primary) | 3 | 0 | 1 (datadog notes vs methods) | 0 |
| api_types | 1 | 0 | 0 | 0 |
| mcp_status | 1 | 1 | 1 (amazon official MCP missed) | 1 (amazon official MCP still missed) |
| access + verdict + blocker | 1 | 0 | 0 | 0 |
| product identity | 1 | 1 | 0 | 0 |
| **row-correct** | **3/10** | **8/10** | **8/10** | **9/10** |

## Cost and time

LLM $ computed at $3/$15 per MTok. Exa $ is the sum of `costDollars` reported by the Exa API (an estimate, not an invoice).

| slug | wall_s | llm_usd | exa_usd | exa_calls | llm_calls |
|---|---|---|---|---|---|
| twenty | 15.88 | 0.0854 | 0.035 | 5 | 1 |
| twilio | 19.84 | 0.0743 | 0.036 | 6 | 1 |
| plain | 26.77 | 0.1528 | 0.036 | 6 | 2 |
| gohighlevel | 18.78 | 0.0822 | 0.035 | 6 | 1 |
| sherlock | 20.51 | 0.1492 | 0.029 | 5 | 2 |
| datadog | 25.11 | 0.1820 | 0.044 | 8 | 2 |
| linear | 20.65 | 0.0670 | 0.037 | 8 | 1 |
| amazon-selling-partner | 38.82 | 0.1829 | 0.043 | 7 | 2 |
| paygent-connect | 37.56 | 0.0913 | 0.035 | 8 | 2 |
| devin | 17.11 | 0.0822 | 0.044 | 8 | 1 |
| TOTAL | 241.0s cpu / 100.6s wall | 1.1495 | 0.374 | 67 |  |

Vs the previous Exa run (78.8s / $0.802 LLM / $0.367 Exa), this run is a little slower and about $0.35 more on the model because some apps now get a gap-fill re-ask. All-in about $1.52 ($0.15/app).
