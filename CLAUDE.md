# CLAUDE.md

Research agent that fills one `schema/app_row.schema.json` row per app in `data/apps.csv`. Field meanings, enums and consistency rules: `schema/README.md`. Scoring and evidence rules: `schema/RUBRIC.md`.

## Checks (run before committing)

```bash
.venv/bin/ruff check .
.venv/bin/pytest -q
.venv/bin/python -m agent run --dry-run --apps hubspot   # offline end-to-end, writes data/runs/dry-run-*/ (gitignored)
```

Tests are offline: LLM, Composio and ddgs are mocked, and httpx uses `MockTransport`. Never add tests that call real APIs.

## Non-obvious decisions

- **Identity and `composio_toolkit` are never LLM output.** `research._assemble` copies id/slug/name/category from the CSV and `composio_toolkit` from `data/composio_coverage.csv`. Empty CSV cells become `null` / `[]`.
- **Evidence integrity.** Evidence URLs must be pages fetched (status < 400, non-empty text) in the same run. Matching uses the requested or final URL, normalized. Others are dropped in `validate.check_evidence`. An empty required list then fails the schema and triggers the single retry. Snippets live in `evidence/<slug>.json`, not in the row, because the row has `additionalProperties: false`.
- **Consistency rules** that JSON Schema can't express are in `validate.consistency_errors`: primary_auth in auth_methods, oauth2 preferred, no_public_api pairing, gated cannot be buildable_now, gated_access needs gated access.
- **Composio tools fail in this project** with "No active connection found for toolkit(s) 'exa'". Instant Toolkits are off and no provider key is connected. `composio_search` is retired (410). `auto` providers fall back to `ddgs` / `httpx`+`trafilatura` on the first ProviderUnavailable and do not retry Composio for the rest of the run (`tools.Fallback`).
- **ddgs:** only `backend="yahoo"` returned results on the dev box. The others raise "No results found". Backends are tried in order (`DDGS_BACKENDS`).
- **Composio arg names** (`numResults`, `max_results`, `onlyMainContent`) are checked against the live tool schema (`tools.get_raw_composio_tool_by_slug`) when a session is created. They fall back to the defaults in `composio_tools.TOOLS`.
- **LLM SDKs are imported lazily** inside each adapter. Missing-key errors name the variable, never a value.
- **LLM output handling.** Every raw response (text, stop_reason, truncated, usage) is saved per attempt to `cache/<slug>.llm.json`. `extract_json` only parses the outermost object starting at the first `{`. The old fallback to later `{` returned a nested object when the outer one was malformed, which produced all-null rows (firecrawl in smoke-1). The row is accepted under `"row"`, at top level, or under one wrapper key. Truncation (`max_tokens` / `length` / `MAX_TOKENS`), unparseable JSON, and JSON with no row fields each give one explicit `output:` error for the single retry, never schema noise. `LLM_MAX_TOKENS` defaults to 8000.
- **Relevance filter before fetching** (`research.classify_result`): a result is kept if it is on the official domain or a host containing the app's name token; or on a doc/code host (`DOC_HOSTS`) whose URL or title names the app; or third party naming the app in title/snippet, max 2 of these. `start_url` is always kept. Official pages rank first.
- **Thin pages (< 1500 chars)** in `HttpFetch`: trafilatura is tried, then trafilatura with favor_recall, then the `<url>.md` twin (docs URLs only), then lxml visible text. The first candidate that is no longer thin wins, else the longest. `method` / `text_source_url` are recorded in the page meta. `<origin>/llms.txt` is fetched once per app as its own page when a docs page is thin.
- Concurrency uses a thread pool (the SDKs are sync). Writes to the jsonl files go through one lock. Resume means skipping slugs already in `rows.jsonl`.
- Do not run the agent on all 100 apps without being asked. It costs real LLM tokens.
- No person's real name anywhere. The repo owner is "adithyaprad".
