# Gold-sample score: pass1

- Run dir: `/home/box/work/gig/composio-takehome/data/runs/pass1`
- Gold file: `/home/box/work/gig/composio-gold/schema/gold_sample.jsonl`
- Scored at: 2026-11-23T13:50:41Z
- Gold apps: 20
- Row-correct: 9 / 20 (45.0%)
- Coverage-lookup accuracy: 20 / 20 (100.0%)

Row accuracy requires every critical unit to match. Minor mismatches are listed below but do not fail the row.
Evidence is scored on URL presence (http/https). Whether a URL actually supports the claim is a human check.

## Per-field accuracy

| Field | Kind | Matches | Applies | Accuracy |
|---|---|---:|---:|---:|
| auth_methods / primary_auth | critical | 14 | 20 | 70.0% |
| access | critical | 17 | 20 | 85.0% |
| api_types + has_public_docs | critical | 18 | 20 | 90.0% |
| mcp_status | critical | 19 | 20 | 95.0% |
| verdict | critical | 16 | 20 | 80.0% |
| blocker | critical | 16 | 20 | 80.0% |
| evidence (critical claims) | critical | 20 | 20 | 100.0% |
| one_liner | minor | 20 | 20 | 100.0% |
| api_breadth | minor | 13 | 20 | 65.0% |
| docs_url | minor | 19 | 20 | 95.0% |
| mcp_url | minor | 17 | 17 | 100.0% |
| requires_end_user_admin | minor | 14 | 20 | 70.0% |
| composio_toolkit (status + primary slug) | coverage | 20 | 20 | 100.0% |

## Apps

| App | Row-correct | Failed critical units |
|---|---|---|
| HubSpot (`hubspot`) | no | api_types + has_public_docs |
| DealCloud (`dealcloud`) | no | auth_methods / primary_auth |
| Zendesk (`zendesk`) | no | auth_methods / primary_auth |
| Pylon (`pylon`) | no | access, verdict, blocker |
| Slack (`slack`) | yes | — |
| WhatsApp Business (`whatsapp-business`) | no | auth_methods / primary_auth, access, mcp_status, verdict, blocker |
| Mailchimp (`mailchimp`) | no | auth_methods / primary_auth |
| LinkedIn Ads (`linkedin-ads`) | yes | — |
| Shopify (`shopify`) | yes | — |
| fanbasis (`fanbasis`) | yes | — |
| Firecrawl (`firecrawl`) | yes | — |
| Clay (`clay`) | no | auth_methods / primary_auth |
| GitHub (`github`) | no | auth_methods / primary_auth |
| Snowflake (`snowflake`) | yes | — |
| Notion (`notion`) | yes | — |
| Smartsheet (`smartsheet`) | no | access, verdict, blocker |
| Stripe (`stripe`) | yes | — |
| PitchBook (`pitchbook`) | no | api_types + has_public_docs |
| Reducto (`reducto`) | yes | — |
| NotebookLM (`notebooklm`) | no | verdict, blocker |

## Misses

| App | Field | Severity | Gold | Agent | Reason |
|---|---|---|---|---|---|
| HubSpot (`hubspot`) | api_types + has_public_docs | critical | {"api_types": ["rest", "graphql"], "has_public_docs": true} | {"api_types": ["rest"], "has_public_docs": true} | value mismatch |
| DealCloud (`dealcloud`) | auth_methods / primary_auth | critical | {"auth_methods": ["oauth2", "api_key"], "primary_auth": "oauth2"} | {"auth_methods": ["oauth2"], "primary_auth": "oauth2"} | value mismatch |
| Zendesk (`zendesk`) | auth_methods / primary_auth | critical | {"auth_methods": ["oauth2", "api_key"], "primary_auth": "oauth2"} | {"auth_methods": ["oauth2", "api_key", "basic"], "primary_auth": "oauth2"} | value mismatch |
| Pylon (`pylon`) | access | critical | gated_sales | self_serve_paid | value mismatch |
| Pylon (`pylon`) | verdict | critical | buildable_with_approval | buildable_now | value mismatch |
| Pylon (`pylon`) | blocker | critical | gated_access | none | value mismatch |
| Pylon (`pylon`) | api_breadth | minor | moderate | broad | value mismatch |
| Slack (`slack`) | requires_end_user_admin | minor | true | false | value mismatch |
| WhatsApp Business (`whatsapp-business`) | auth_methods / primary_auth | critical | {"auth_methods": ["oauth2", "api_key"], "primary_auth": "oauth2"} | {"auth_methods": ["api_key"], "primary_auth": "api_key"} | value mismatch |
| WhatsApp Business (`whatsapp-business`) | access | critical | gated_partner | self_serve | value mismatch |
| WhatsApp Business (`whatsapp-business`) | mcp_status | critical | none | official | value mismatch |
| WhatsApp Business (`whatsapp-business`) | verdict | critical | buildable_with_approval | buildable_now | value mismatch |
| WhatsApp Business (`whatsapp-business`) | blocker | critical | gated_access | none | value mismatch |
| WhatsApp Business (`whatsapp-business`) | api_breadth | minor | moderate | broad | value mismatch |
| WhatsApp Business (`whatsapp-business`) | requires_end_user_admin | minor | true | false | value mismatch |
| Mailchimp (`mailchimp`) | auth_methods / primary_auth | critical | {"auth_methods": ["oauth2", "api_key"], "primary_auth": "oauth2"} | {"auth_methods": ["oauth2", "api_key", "basic"], "primary_auth": "oauth2"} | value mismatch |
| LinkedIn Ads (`linkedin-ads`) | api_breadth | minor | moderate | broad | value mismatch |
| Shopify (`shopify`) | requires_end_user_admin | minor | true | false | value mismatch |
| Firecrawl (`firecrawl`) | api_breadth | minor | moderate | broad | value mismatch |
| Clay (`clay`) | auth_methods / primary_auth | critical | {"auth_methods": ["api_key"], "primary_auth": "api_key"} | {"auth_methods": ["api_key", "oauth2"], "primary_auth": "oauth2"} | value mismatch |
| GitHub (`github`) | auth_methods / primary_auth | critical | {"auth_methods": ["oauth2", "api_key", "jwt_service_account"], "primary_auth": "oauth2"} | {"auth_methods": ["oauth2", "api_key", "basic", "jwt_service_account"], "primary_auth": "oauth2"} | value mismatch |
| GitHub (`github`) | requires_end_user_admin | minor | true | false | value mismatch |
| Snowflake (`snowflake`) | requires_end_user_admin | minor | true | false | value mismatch |
| Notion (`notion`) | api_breadth | minor | moderate | broad | value mismatch |
| Notion (`notion`) | requires_end_user_admin | minor | true | false | value mismatch |
| Smartsheet (`smartsheet`) | access | critical | self_serve_paid | gated_partner | value mismatch |
| Smartsheet (`smartsheet`) | verdict | critical | buildable_now | buildable_with_approval | value mismatch |
| Smartsheet (`smartsheet`) | blocker | critical | none | gated_access | value mismatch |
| Smartsheet (`smartsheet`) | api_breadth | minor | moderate | broad | value mismatch |
| PitchBook (`pitchbook`) | api_types + has_public_docs | critical | {"api_types": ["rest"], "has_public_docs": true} | {"api_types": ["rest"], "has_public_docs": false} | value mismatch |
| PitchBook (`pitchbook`) | docs_url | minor | https://pitchbook.com/help/PitchBook-api | null | value mismatch |
| Reducto (`reducto`) | api_breadth | minor | moderate | broad | value mismatch |
| NotebookLM (`notebooklm`) | verdict | critical | not_buildable | buildable_now | value mismatch |
| NotebookLM (`notebooklm`) | blocker | critical | preview_or_unstable | none | value mismatch |

A human read of each critical miss, including gold-label review flags, is in `pass1-review.md`. Gold labels were not changed after scoring.
