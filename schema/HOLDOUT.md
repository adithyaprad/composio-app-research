# Held-out test set (10 apps)

This is an **untouched generalisation set**. It is not the gold sample, and it is not used to invent or revise pass-2 rules.

The 10 apps were labelled from official vendor docs **before pass-2 rules were frozen**. After those rules are frozen, this set is scored **once**. There are no further label or rule fixes from its results. The score is the honest "does this still work on apps we never studied?" number.

It is not part of the headline pass-1 vs final gold accuracy on the 20. Report it separately.

## Why it exists

Pass-2 rules are general checks written after reviewing pass-1 mistakes on the gold 20. Scoring the same 20 again measures improvement, but an evaluator can fairly ask whether the rules only help on the apps that inspired them. This set answers that question: same schema and rubric, 10 apps that were not in the gold 20 and not in the pilot, labelled independently, scored once.

## How the apps were chosen

Selection is scripted and seeded. Nothing was hand-picked.

- Script: `scripts/select_holdout.py`
- Seed: `20261123`
- Method: stratified random sample, **one app per category**. Categories stay in `data/apps.csv` order. Inside a category, candidates are sorted by slug, then `random.Random(20261123).choice` picks one.
- Exclusions (frozen in the script, so the draw does not depend on reading run-row contents):
  - The gold sample of 20, from `schema/gold_sample.md`.
  - The pilot-10 apps. Slugs were taken from `data/runs/pilot-10*` cache directory names and `*.llm.json` filenames only (pilot-10, pilot-10b, pilot-10c, and pilot-10d all used the same 10 apps). Row contents were not read.

Eligible pool: 70 apps (100 minus 20 gold minus 10 pilot).

## Labels

Labels follow the same schema and rubric as the gold sample (`schema/app_row.schema.json`, `schema/RUBRIC.md`, `schema/README.md`), including the general rubric clarifications recorded on `gig/gold-labels` at commit `eefb4c9`:

1. A dashboard secret used only as an OAuth `client_secret` is not a separate `api_key` method.
2. `basic` is its own method only for a username and password (or equivalent user login). A key or token sent via the HTTP Basic scheme stays `api_key` or `oauth2`.
3. If a self-serve path to usable API credentials already exists (for example a personal or custom token after buying a plan online), `access` stays `self_serve` or `self_serve_paid` even when a separate OAuth app-registration path may be reviewed.

Each row was filled from official vendor documentation (or the official source repo for a local tool), with URL and quote evidence. Agent run rows, caches, and scores were not read for these apps. `composio_toolkit` is the frozen catalog snapshot in `data/composio_coverage.csv` (`checked_at` 2026-11-23T10:41:38Z).

The label file is `schema/holdout_sample.jsonl`. It uses the same gold-label format (`label_notes` is gold/holdout-only and is stripped before schema checks). Validate with:

```bash
python scripts/score_gold.py --validate-gold --gold schema/holdout_sample.jsonl
```

That command requires `scripts/score_gold.py` (shipped on `gig/gold-labels`). The file is a drop-in gold JSONL as far as that flag is concerned.

## App list

| id | slug | name | category |
|---:|---|---|---|
| 7 | zoho-crm | Zoho CRM | CRM and Sales |
| 18 | help-scout | Help Scout | Support and Helpdesk |
| 30 | vonage | Vonage | Communications and Messaging |
| 38 | pinterest | Pinterest | Marketing, Ads, Email and Social |
| 47 | ecwid | Ecwid | Ecommerce |
| 55 | apify | Apify | Data, SEO and Scraping |
| 62 | vercel | Vercel | Developer, Infra and Data platforms |
| 80 | harvest | Harvest | Productivity and Project Management |
| 83 | binance | Binance | Finance and Fintech |
| 98 | mermaid-cli | Mermaid CLI | AI, Research and Media-native |

Do not swap apps after this set is published. If an app later proves unlabelable, withdraw it and disclose that; do not silently replace it after seeing agent results.
