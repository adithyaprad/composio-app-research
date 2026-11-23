# Holdout-sample score: holdout-pass2

- Run dir: `data/runs/pass2 (gig/pass2 @ c495108)`
- Gold file: `schema/holdout_sample.jsonl` (gig/holdout-labels @ 9136a08, PR #6)
- Scored at: 2026-11-23T14:34:57Z
- Gold apps: 10
- Row-correct: 3 / 10 (30.0%)
- Coverage-lookup accuracy: 10 / 10 (100.0%)

Row accuracy requires every critical unit to match. Minor mismatches are listed below but do not fail the row.
Evidence is scored on URL presence (http/https). Whether a URL actually supports the claim is a human check.

## Per-field accuracy

| Field | Kind | Matches | Applies | Accuracy |
|---|---|---:|---:|---:|
| auth_methods / primary_auth | critical | 6 | 10 | 60.0% |
| access | critical | 9 | 10 | 90.0% |
| api_types + has_public_docs | critical | 10 | 10 | 100.0% |
| mcp_status | critical | 8 | 10 | 80.0% |
| verdict | critical | 10 | 10 | 100.0% |
| blocker | critical | 10 | 10 | 100.0% |
| evidence (critical claims) | critical | 10 | 10 | 100.0% |
| one_liner | minor | 10 | 10 | 100.0% |
| api_breadth | minor | 10 | 10 | 100.0% |
| docs_url | minor | 10 | 10 | 100.0% |
| mcp_url | minor | 8 | 8 | 100.0% |
| requires_end_user_admin | minor | 1 | 10 | 10.0% |
| composio_toolkit (status + primary slug) | coverage | 10 | 10 | 100.0% |

## Apps

| App | Row-correct | Failed critical units |
|---|---|---|
| Zoho CRM (`zoho-crm`) | yes | — |
| Help Scout (`help-scout`) | no | access |
| Vonage (`vonage`) | no | auth_methods / primary_auth |
| Pinterest (`pinterest`) | no | mcp_status |
| Ecwid (`ecwid`) | no | auth_methods / primary_auth |
| Apify (`apify`) | yes | — |
| Vercel (`vercel`) | no | auth_methods / primary_auth |
| Harvest (`harvest`) | yes | — |
| Binance (`binance`) | no | auth_methods / primary_auth |
| Mermaid CLI (`mermaid-cli`) | no | mcp_status |

## Misses

| App | Field | Severity | Gold | Agent | Reason |
|---|---|---|---|---|---|
| Zoho CRM (`zoho-crm`) | requires_end_user_admin | minor | true | false | value mismatch |
| Help Scout (`help-scout`) | access | critical | self_serve | self_serve_paid | value mismatch |
| Help Scout (`help-scout`) | requires_end_user_admin | minor | true | false | value mismatch |
| Vonage (`vonage`) | auth_methods / primary_auth | critical | {"auth_methods": ["api_key", "jwt_service_account"], "primary_auth": "jwt_service_account"} | {"auth_methods": ["api_key", "jwt_service_account"], "primary_auth": "api_key"} | value mismatch |
| Vonage (`vonage`) | requires_end_user_admin | minor | true | false | value mismatch |
| Pinterest (`pinterest`) | mcp_status | critical | none | official | value mismatch |
| Pinterest (`pinterest`) | requires_end_user_admin | minor | true | false | value mismatch |
| Ecwid (`ecwid`) | auth_methods / primary_auth | critical | {"auth_methods": ["oauth2", "api_key"], "primary_auth": "oauth2"} | {"auth_methods": ["oauth2"], "primary_auth": "oauth2"} | value mismatch |
| Ecwid (`ecwid`) | requires_end_user_admin | minor | true | false | value mismatch |
| Apify (`apify`) | requires_end_user_admin | minor | true | false | value mismatch |
| Vercel (`vercel`) | auth_methods / primary_auth | critical | {"auth_methods": ["oauth2", "api_key"], "primary_auth": "oauth2"} | {"auth_methods": ["api_key"], "primary_auth": "api_key"} | value mismatch |
| Vercel (`vercel`) | requires_end_user_admin | minor | true | false | value mismatch |
| Harvest (`harvest`) | requires_end_user_admin | minor | true | false | value mismatch |
| Binance (`binance`) | auth_methods / primary_auth | critical | {"auth_methods": ["custom_other"], "primary_auth": "custom_other"} | {"auth_methods": ["api_key"], "primary_auth": "api_key"} | value mismatch |
| Binance (`binance`) | requires_end_user_admin | minor | true | false | value mismatch |
| Mermaid CLI (`mermaid-cli`) | mcp_status | critical | community | official | value mismatch |
