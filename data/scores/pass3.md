# Gold-sample score: pass3

- Run dir: `/home/box/work/gig/composio-browser/data/runs/pass3`
- Gold file: `/home/box/work/gig/composio-browser/schema/gold_sample.jsonl`
- Scored at: 2026-11-23T15:29:43Z
- Gold apps: 20
- Row-correct: 15 / 20 (75.0%)
- Coverage-lookup accuracy: 20 / 20 (100.0%)

Row accuracy requires every critical unit to match. Minor mismatches are listed below but do not fail the row.
Evidence is scored on URL presence (http/https). Whether a URL actually supports the claim is a human check.

## Per-field accuracy

| Field | Kind | Matches | Applies | Accuracy |
|---|---|---:|---:|---:|
| auth_methods / primary_auth | critical | 19 | 20 | 95.0% |
| access | critical | 18 | 20 | 90.0% |
| api_types + has_public_docs | critical | 19 | 20 | 95.0% |
| mcp_status | critical | 19 | 20 | 95.0% |
| verdict | critical | 17 | 20 | 85.0% |
| blocker | critical | 17 | 20 | 85.0% |
| evidence (critical claims) | critical | 20 | 20 | 100.0% |
| one_liner | minor | 20 | 20 | 100.0% |
| api_breadth | minor | 13 | 20 | 65.0% |
| docs_url | minor | 20 | 20 | 100.0% |
| mcp_url | minor | 18 | 19 | 94.7% |
| requires_end_user_admin | minor | 14 | 20 | 70.0% |
| composio_toolkit (status + primary slug) | coverage | 20 | 20 | 100.0% |

## Apps

| App | Row-correct | Failed critical units |
|---|---|---|
| HubSpot (`hubspot`) | no | api_types + has_public_docs |
| DealCloud (`dealcloud`) | yes | — |
| Zendesk (`zendesk`) | yes | — |
| Pylon (`pylon`) | yes | — |
| Slack (`slack`) | yes | — |
| WhatsApp Business (`whatsapp-business`) | no | auth_methods / primary_auth, access, verdict, blocker |
| Mailchimp (`mailchimp`) | yes | — |
| LinkedIn Ads (`linkedin-ads`) | yes | — |
| Shopify (`shopify`) | yes | — |
| fanbasis (`fanbasis`) | no | mcp_status |
| Firecrawl (`firecrawl`) | yes | — |
| Clay (`clay`) | yes | — |
| GitHub (`github`) | yes | — |
| Snowflake (`snowflake`) | yes | — |
| Notion (`notion`) | yes | — |
| Smartsheet (`smartsheet`) | no | access, verdict, blocker |
| Stripe (`stripe`) | yes | — |
| PitchBook (`pitchbook`) | yes | — |
| Reducto (`reducto`) | yes | — |
| NotebookLM (`notebooklm`) | no | verdict, blocker |

## Misses

| App | Field | Severity | Gold | Agent | Reason |
|---|---|---|---|---|---|
| HubSpot (`hubspot`) | api_types + has_public_docs | critical | {"api_types": ["rest", "graphql"], "has_public_docs": true} | {"api_types": ["rest"], "has_public_docs": true} | value mismatch |
| Pylon (`pylon`) | api_breadth | minor | moderate | broad | value mismatch |
| Slack (`slack`) | requires_end_user_admin | minor | true | false | value mismatch |
| WhatsApp Business (`whatsapp-business`) | auth_methods / primary_auth | critical | {"auth_methods": ["oauth2", "api_key"], "primary_auth": "oauth2"} | {"auth_methods": ["api_key"], "primary_auth": "api_key"} | value mismatch |
| WhatsApp Business (`whatsapp-business`) | access | critical | gated_partner | self_serve | value mismatch |
| WhatsApp Business (`whatsapp-business`) | verdict | critical | buildable_with_approval | buildable_now | value mismatch |
| WhatsApp Business (`whatsapp-business`) | blocker | critical | gated_access | none | value mismatch |
| WhatsApp Business (`whatsapp-business`) | api_breadth | minor | moderate | broad | value mismatch |
| WhatsApp Business (`whatsapp-business`) | requires_end_user_admin | minor | true | false | value mismatch |
| LinkedIn Ads (`linkedin-ads`) | api_breadth | minor | moderate | broad | value mismatch |
| Shopify (`shopify`) | requires_end_user_admin | minor | true | false | value mismatch |
| fanbasis (`fanbasis`) | mcp_status | critical | official | none | value mismatch |
| fanbasis (`fanbasis`) | mcp_url | minor | https://commasdocs.com/#ai-agent | null | value mismatch |
| Firecrawl (`firecrawl`) | api_breadth | minor | moderate | broad | value mismatch |
| GitHub (`github`) | requires_end_user_admin | minor | true | false | value mismatch |
| Snowflake (`snowflake`) | requires_end_user_admin | minor | true | false | value mismatch |
| Notion (`notion`) | api_breadth | minor | moderate | broad | value mismatch |
| Notion (`notion`) | requires_end_user_admin | minor | true | false | value mismatch |
| Smartsheet (`smartsheet`) | access | critical | self_serve_paid | gated_partner | value mismatch |
| Smartsheet (`smartsheet`) | verdict | critical | buildable_now | buildable_with_approval | value mismatch |
| Smartsheet (`smartsheet`) | blocker | critical | none | gated_access | value mismatch |
| Smartsheet (`smartsheet`) | api_breadth | minor | moderate | broad | value mismatch |
| Reducto (`reducto`) | api_breadth | minor | moderate | broad | value mismatch |
| NotebookLM (`notebooklm`) | verdict | critical | not_buildable | buildable_now | value mismatch |
| NotebookLM (`notebooklm`) | blocker | critical | preview_or_unstable | none | value mismatch |
