# composio-app-research

Research whether 100 apps can become agent toolkits: auth, how a developer gets access, API surface, existing MCP, whether Composio already covers the app, and a buildability verdict. Then cluster the patterns and show how accuracy was verified.

This is a take-home style research pipeline, not a production product.

## Repo layout

- `data/apps.csv` — the 100-app research set (id, slug, category, starting hint/URL)
- `data/composio_toolkits.json` — cached Composio catalog snapshot
- `data/composio_aliases.json` — manual slug map for product names the automatic matcher would get wrong
- `data/composio_coverage.csv` — which of the 100 already have a Composio toolkit
- `scripts/composio_coverage.py` — refresh the catalog and rebuild the coverage table
- `schema/` — what one researched row looks like, how fields are defined, and how accuracy is scored
  - `app_row.schema.json` — machine-readable row shape
  - `README.md` — field and enum definitions
  - `RUBRIC.md` — scoring rules and gold-sample procedure
  - `gold_sample.md` — 20 apps reserved for a human accuracy check (answers not filled yet)
- `agent/` — research agent CLI (`python -m agent run`): `llm/` provider adapters, `tools/` search/fetch providers, `research.py` loop, `validate.py` schema + consistency + evidence checks, `prompts.py`
- `tests/` — offline unit tests (LLM and tools mocked)
- `data/runs/<run_id>/` — agent outputs
- `.agents/skills/composio/` — Composio skill used when building the research agent

## Status

Phase 1 is done: input list, schema, rubric, and a Composio catalog coverage snapshot. The research agent (v1, `agent/`) now exists. The verification loop, pattern write-up, and case-study page come next.

## Refresh Composio coverage

Needs a `COMPOSIO_API_KEY` (already in `.env.example`). From the repo root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
set -a && . /path/to/local.env && set +a
python scripts/composio_coverage.py --refresh
```

Without `--refresh`, the script reuses `data/composio_toolkits.json` and only rebuilds the coverage table. It never prints secret values.

## Run the research agent

For each app in `data/apps.csv` the agent runs a few targeted web searches (API auth, access/pricing, API reference, MCP server), drops results that don't look like the app (it keeps the official domain, doc/code hosts that name the app, and at most 2 third-party pages that name it), fetches up to `--max-pages` pages (always including `start_url`, official domain first), and asks an LLM to fill the row **from the fetched page text only**. The LLM may ask for one follow-up round (max 3 searches / 3 extra pages). Then:

- Every evidence URL is checked against the pages actually fetched in that run. Unfetched URLs are dropped and logged in `checks`.
- Each claim's quoted snippet is checked against the page text.
- The row is validated against `schema/app_row.schema.json` and the consistency rules in `schema/README.md`. If it fails, the errors go back to the LLM for one retry. If it still fails, the row goes to `failures.jsonl` and the run continues.
- `composio_toolkit` is copied from `data/composio_coverage.csv`. The LLM never researches it.

### Install

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pip install -r requirements-dev.txt   # tests + ruff
```

`requirements.txt` installs `boto3` for the default Bedrock provider. For another provider, install its SDK too: `anthropic`, `openai`, or `google-genai`.

### Configure

```bash
cp .env.example .env   # then fill in one LLM provider
```

| `LLM_PROVIDER` | Needs | Default `LLM_MODEL` |
|---|---|---|
| `bedrock` (default) | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (or `AWS_PROFILE`), `AWS_REGION` (default `ap-south-1`) | `global.anthropic.claude-sonnet-5-5` |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-5-5` |
| `openai` | `OPENAI_API_KEY` | `gpt-5.1` |
| `gemini` | `GEMINI_API_KEY` | `gemini-2.5-pro` |

`LLM_MODEL` overrides the default for any provider. Set `LLM_PRICE_IN_PER_MTOK` / `LLM_PRICE_OUT_PER_MTOK` to get `est_cost_usd` in the log. Variables already in the shell win over `.env`. Use `ENV_FILE=/path/to/file` to load a different file. Secrets are never printed or logged.

### Run

```bash
.venv/bin/python -m agent run --dry-run --apps hubspot          # offline: fake LLM + fake tools, no keys
.venv/bin/python -m agent run --apps hubspot,firecrawl --run-id smoke
.venv/bin/python -m agent run --limit 10 --concurrency 3 --max-pages 8 --run-id pass1
.venv/bin/python -m agent run --run-id pass1                    # resume: skips apps already in rows.jsonl
```

Output goes to `data/runs/<run_id>/`:

- `rows.jsonl`: one schema-valid row per app
- `failures.jsonl`: rows that stayed invalid after the retry, with their errors, plus crashes
- `evidence/<slug>.json`: searches run, fetched pages (requested and final URL, status, fetched_at, sha256, text_len, extraction `method`), a quoted snippet per claim, and `checks` (dropped evidence, unverified snippets, unfetched docs/MCP URLs, warnings, validation errors)
- `log.jsonl`: per app wall time, search/fetch/LLM call counts, tokens, est. cost, and which providers served each call
- `cache/`: raw fetched text, plus `<slug>.llm.json` with every raw LLM response per attempt (text, stop reason, usage). Gitignored.

Pages that extract to under ~1500 chars (typical for JS-rendered docs) go through fallbacks: trafilatura with favor_recall, then the page's `.md` twin, then plain visible text. For a thin docs page, `<origin>/llms.txt` is also fetched once per app. A truncated LLM answer (`LLM_MAX_TOKENS`, default 8000) gets one retry asking for a compact row.

Failed apps are not written to `rows.jsonl`, so re-running the same `--run-id` retries them.

### Search and fetch providers

The defaults are free first. Composio is a paid escalation, capped per run:

| Variable | Default | Values |
|---|---|---|
| `SEARCH_PROVIDER` | `ddgs` | `ddgs` (free only); `hybrid` (ddgs, then Composio Exa only when ddgs returns 0 results); `auto` / `composio_exa` / `composio_tavily` (Composio first, ddgs fallback). `USE_COMPOSIO_EXA=true` is the same as `auto`. |
| `FETCH_PROVIDER` | `hybrid` | `hybrid` (http first; a URL goes to Composio `FIRECRAWL_SCRAPE` only when the page is still thin after all extraction fallbacks, or blocked: status 401/403/429/503 or a network error); `auto` / `composio_firecrawl` (Firecrawl first, http fallback); `http` (free only). |
| `INSTANT_BUDGET_USD` | `0.30` | Hard cap on Composio Instant spend per run. `0` disables Instant calls. |

Composio is only tried when `COMPOSIO_API_KEY` is set. The first `No active connection`, 400/401/403 or 410 error switches Composio off for the rest of the run, logged once, and the free result is kept.

Every Composio call first reserves its listed price: `FIRECRAWL_SCRAPE` $0.003, `EXA_SEARCH` $0.007, `TAVILY_SEARCH` $0.008, anything else $0.01. You can override these with `INSTANT_PRICES`. If a call would push spend past the cap, it is skipped (logged once) and the free result is used. Sessions are created with `instant={"return_instant_charge": True}`. When Composio reports `instant_charge.amount`, that is counted as actual spend; otherwise the listed price is counted as an estimate.

`log.jsonl` (per app) and the end-of-run summary report `instant_calls`, `instant_calls_by_tool`, `instant_est_cost_usd` and `instant_charged_usd`.

`ddgs` tries backends in the order set by `DDGS_BACKENDS` (default `yahoo,duckduckgo,bing,brave,google`). When this was written, only `yahoo` returned results from this machine.

**What Composio unlocks:** Exa, Tavily and Firecrawl run through a Composio session (`composio.sessions.create(...)` then `session.execute(...)`). In this project they currently fail with `400 No active connection found for toolkit(s) ...`. This is still true after Instant was toggled in the dashboard: `session.search` reports `instant_account: null`. The free path keeps working, so nothing needs to change in the agent.

Composio becomes active once Instant Toolkits work for the project key (prepaid balance) or an Exa / Firecrawl key is connected. From then on, `hybrid` fetch starts escalating thin or blocked pages to Firecrawl automatically, within `INSTANT_BUDGET_USD`. Set `SEARCH_PROVIDER=hybrid` or `auto` to also use Exa. The old `composio_search` toolkit is retired (HTTP 410) and is not used.
