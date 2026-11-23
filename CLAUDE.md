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
- Concurrency uses a thread pool (the SDKs are sync). Writes to the jsonl files go through one lock. Resume means skipping slugs already in `rows.jsonl`.
- Do not run the agent on all 100 apps without being asked. It costs real LLM tokens.
- No person's real name anywhere. The repo owner is "adithyaprad".
