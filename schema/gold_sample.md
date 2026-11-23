# Gold sample (20 apps)

Two apps per category. One is a well-documented “easy” case; one is gated, obscure, or otherwise easy for an agent to get wrong. Answers are **not** filled here. Labels will be written by hand before pass-1 scoring, without looking at the agent’s rows for these apps.

| id | slug | name | category | difficulty | why this one |
|---:|---|---|---|---|---|
| 2 | hubspot | HubSpot | CRM and Sales | easy | Large, well-documented CRM API; typical self-serve OAuth / key case. |
| 10 | dealcloud | DealCloud | CRM and Sales | hard | Enterprise deal CRM; likely a sales/partner gate rather than a quick signup. |
| 11 | zendesk | Zendesk | Support and Helpdesk | easy | Classic support platform with long-standing public docs. |
| 15 | pylon | Pylon | Support and Helpdesk | hard | Newer product; thinner public footprint, easier to mis-label. |
| 21 | slack | Slack | Communications and Messaging | easy | Well-known OAuth app model; also tests the “workspace admin ≠ vendor gate” rule. |
| 28 | whatsapp-business | WhatsApp Business | Communications and Messaging | hard | Famous partner / solution-provider gate. “Gated, with evidence” should score correct. |
| 35 | mailchimp | Mailchimp | Marketing, Ads, Email and Social | easy | Mature email API, often used as a straightforward self-serve example. |
| 33 | linkedin-ads | LinkedIn Ads | Marketing, Ads, Email and Social | hard | Ads APIs often need developer/partner approval; easy to call “self-serve” by mistake. |
| 41 | shopify | Shopify | Ecommerce | easy | Large, well-documented commerce platform API. |
| 50 | fanbasis | fanbasis | Ecommerce | hard | Thin hint (just a marketing site). Tests whether the agent admits “no public API / gated” instead of inventing one. |
| 56 | firecrawl | Firecrawl | Data, SEO and Scraping | easy | Modern, documented developer API. |
| 60 | clay | Clay | Data, SEO and Scraping | hard | Product is well known; a public, self-serve API is not obviously documented. |
| 61 | github | GitHub | Developer, Infra and Data platforms | easy | Canonical REST + OAuth / token case. |
| 67 | snowflake | Snowflake | Developer, Infra and Data platforms | hard | Enterprise data platform; several auth styles and a heavier access path. |
| 71 | notion | Notion | Productivity and Project Management | easy | Clear public API and integration docs. |
| 79 | smartsheet | Smartsheet | Productivity and Project Management | hard | API access is often plan-gated; tests `self_serve_paid` vs `gated_sales`. |
| 81 | stripe | Stripe | Finance and Fintech | easy | Best-in-class API docs; should be a clean `buildable_now`. |
| 90 | pitchbook | PitchBook | Finance and Fintech | hard | Research data API is typically sales-gated. Another “gated, with evidence” test. |
| 95 | reducto | Reducto | AI, Research and Media-native | easy | Developer-focused document API with public docs. |
| 91 | notebooklm | NotebookLM | AI, Research and Media-native | hard | Easy to confuse the consumer product with a Gemini / Workspace enterprise API. |

## Held out of the gold sample on purpose

These are still in the 100, and the agent must research them. They are just not part of the frozen accuracy set:

- **Paygent Connect** and **iPayX** — obscure payment products; useful stress tests, but a human gold label may itself be too shaky to score against.
- **Mermaid CLI** and **Sherlock** — local/open-source tools that exercise the `cli_only` / `auth: none` path. They are better used as schema edge cases than as half of a category’s accuracy score.
- **Amazon Selling Partner** — a strong gated-access example, but WhatsApp, LinkedIn Ads, and PitchBook already cover that pattern in the 20.

If a listed gold app later proves unlabelable, withdraw it and disclose that in the write-up. Do not silently swap in a replacement after pass 1.

## Labels

Hand-written gold rows live in `gold_sample.jsonl` (one schema-valid object per line). They were filled from official vendor docs, without looking at agent output for these apps. Optional gold-only keys (`label_notes`, `acceptable`, `acceptable_alternatives`) record judgement calls and are stripped before schema validation. Score a run with:

```bash
.venv/bin/python scripts/score_gold.py --run-dir data/runs/pass1 --gold schema/gold_sample.jsonl --out-dir data/scores
.venv/bin/python scripts/score_gold.py --validate-gold
```

Reports are written to `data/scores/<run_id>.json` and `data/scores/<run_id>.md`.

