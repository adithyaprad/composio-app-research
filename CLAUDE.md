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
- **Consistency rules** that JSON Schema can't express are in `validate.consistency_errors`: primary_auth in auth_methods, no_public_api pairing, gated cannot be buildable_now, gated_access needs gated access, and `sdk_only` never alongside rest/graphql/soap. The oauth2-first preference is deliberately NOT a code rule. It applies only when OAuth2 authenticates the product's own API, not an MCP server or a dashboard login, and code can't tell the difference. The prompt and rubric own it: a documented third-party-app API OAuth2 flow means oauth2 is primary even when keys exist.
- **Notes vs fields** (`validate.notes_contradictions`): sentence-level regexes flag only self-contradiction in one row. Notes saying OAuth is MCP-only / login-only / undocumented / should be dropped while `auth_methods` has oauth2. Notes saying "no official MCP" with `official`, or "no MCP server found" with official/community. Tuned for precision; the error feeds the single retry.
- **Access gate** (prompt + rubric only): any manual vendor review (developer-profile approval, partner program, mandatory app-store listing) is `gated_partner` + `buildable_with_approval` + `gated_access`, even if a paid account is bought online. Customer admin installs are not a vendor gate.
- **Auth `unknown`** (alone, in both auth fields) = auth could not be determined because docs are absent, gated, or omit auth. `custom_other` is only for documented custom schemes. `auth_methods` is developer-API auth only.
- **Vague MCP mentions are `none`**: "MCP (beta)" with no server URL, repo, or MCP docs page gives `mcp_status: none`, `mcp_url: null`, and the mention goes in notes.
- **Exa-first default** (`tools/exa.py`): with `EXA_API_KEY` set, `Settings.from_env` defaults both providers to `exa`; without it, to `ddgs` / `hybrid`. An explicit `exa` with no key logs a warning and uses ddgs / http, never crashes.
  - One `ExaClient` per run (`tools.make_exa`) is shared by search and fetch. `/search` asks for page text, cached by `normalize_url`, so fetching a searched URL makes no request (`method: exa_search`). Other URLs use `/contents` (`exa_text`); repeats are `exa_cache`.
  - Both are wrapped in `Fallback`. 401/402/403 or `MAX_FAILURES` 5xx/network errors in a row raise `ProviderUnavailable`, and the client stays off for the run, so the other wrapper does not send a request either. A single transient error or a page Exa can't read/was blocked from raises `UseFallback`: the free tool serves that call only. Other per-URL 4xx return a failed `Page` (`exa_error`).
  - `ExaBudget` (`EXA_BUDGET_USD`, default 5, 0 disables) subclasses `InstantBudget` but is tracked separately. It reserves the listed price ($0.007 search, $0.001 contents) and records `costDollars.total` when reported. Logged as `exa_*` fields, never `instant_*`. `est_cost_usd` stays the LLM token estimate.
- **Composio still fails in this project** with `400 No active connection found for toolkit(s) ...`. This is true even after Instant was toggled; `session.search` shows `instant_account: null`. That is why Exa is called directly. `composio_search` is retired (410). Composio paths stay but are opt-in (`hybrid`, `auto`, `composio_*`).
  - `HybridFetch` escalates a URL to Firecrawl only when the http page is thin (after all extraction fallbacks) or blocked (0/401/403/429/503). Other 4xx/5xx are not escalated.
  - `HybridSearch` uses Exa only when ddgs returns nothing.
  - The first ProviderUnavailable switches Composio off for the run, logged once. Explicit `composio_*` modes also fall back to free.
- **Instant budget** (`tools/budget.py`, `INSTANT_BUDGET_USD`, default 0.30, 0 disables): one `InstantBudget` per run is shared by search and fetch.
  - `ComposioTool.execute` reserves the listed price before calling. `BudgetExceeded` means nothing was sent, and the caller keeps the free result.
  - It records `instant_charge.amount` if reported, otherwise the listed price as an estimate. The reservation is released when the call fails unbilled.
  - Per-app attribution uses the `current_app` ContextVar set in `Researcher.research`. Per-app deltas go in `log.jsonl`, totals in the summary.
- **ddgs:** only `backend="yahoo"` returned results on the dev box. The others raise "No results found". Backends are tried in order (`DDGS_BACKENDS`).
- **Composio arg names** (`numResults`, `max_results`, `onlyMainContent`) are checked against the live tool schema (`tools.get_raw_composio_tool_by_slug`) when a session is created. They fall back to the defaults in `composio_tools.TOOLS`.
- **LLM SDKs are imported lazily** inside each adapter. Missing-key errors name the variable, never a value.
- **LLM output handling.** Every raw response (text, stop_reason, truncated, usage) is saved per attempt to `cache/<slug>.llm.json`. `extract_json` only parses the outermost object starting at the first `{`. The old fallback to later `{` returned a nested object when the outer one was malformed, which produced all-null rows (firecrawl in smoke-1). The row is accepted under `"row"`, at top level, or under one wrapper key. Truncation (`max_tokens` / `length` / `MAX_TOKENS`), unparseable JSON, and JSON with no row fields each give one explicit `output:` error for the single retry, never schema noise. `LLM_MAX_TOKENS` defaults to 8000.
- **Relevance filter before fetching** (`research.classify_result`): a result is kept if:
  - it is on the official domain, or a host label naming the app (`token_in_label`); or
  - it is on a doc/code host (`DOC_HOSTS`) whose URL path or title names the app; or
  - it is a third-party page naming the app in title/snippet (max 2).

  `start_url` is always kept, and official pages rank first. Short tokens (≤ 6 chars, e.g. slate/acme/front) never match inside another word. Host labels must equal the token, contain it as a hyphen segment, or be the token plus an allowlisted affix (get/use/app/hq/...). Text needs a whole-word match.
- **Search queries** (`research.search_queries`, 4–6 per app): auth, developer access, pricing, `<name> official MCP server`, plus `site:<official host> MCP` and `site:<parent domain> MCP` for subdomains. No site: query on shared doc hosts (`DOC_HOSTS`, e.g. github.com). The official host is the start_url host, else the first host-like token in `source_hint` (`official_host`). With a blank start_url, queries become `"<name>" <hint words> <category label words>`, minus name words and stopwords. The prompt shows `category_label` and tells the model not to fill a confident row for a same-named different product (unknown / not_buildable / docs_insufficient / low instead).
- **Gap-fill of leftover official MCP/auth hits** (`research.gap_fill_urls`): a general retrieval rule, not a product patch. After the initial/follow-up LLM row, up to 3 still-unfetched search results whose URL or title signals MCP (`mcp` / "model context protocol") or API auth (oauth/authentication/authorization; login/sign-in/sign-up pages without "oauth" are skipped) are fetched via `_fetch_new`, even when `max_pages` is full. They must be vendor-owned (`vendor_tier`): official domain and subdomains, the same registrable name on another TLD (`developers.acmepay.io` for `acmepay.com`), a vendor subdomain of a doc host, or a repo under the vendor's org on github/gitlab/bitbucket. Directories like mcp.so are never vendor-owned. MCP hits count only while MCP is unresolved (no row / `none`); auth hits always count. Order: official MCP, official auth, sibling/repo MCP, sibling/repo auth. It runs before the llms.txt MCP check, and both share one re-ask (`gap_fill` or `mcp_check`), made only if a gap-fill page is ok or an llms.txt page mentions MCP.
- **llms.txt before `mcp_status: none`**: after the initial (and follow-up) LLM row, if MCP is unresolved (no row or `none`) and no fetched URL contains "mcp", `<origin>/llms.txt` is fetched for the official docs_url and start_url origins (max 2, deduped against the thin-page llms.txt). Up to 2 MCP links in it are fetched. The LLM is asked again (`mcp_check`) only if a new page mentions MCP, so at most one extra call per app.
- **Pricing page**: one of the search queries targets pricing/plans/signup. `pick_urls` makes sure one official pricing/plans/signup URL is picked: it replaces the lowest-ranked pick if the list is full. If none was found, it synthesizes `<start_url origin>/pricing`, which may be max_pages + 1.
- **Dedupe**: `pick_urls` dedupes by `normalize_url`. `Researcher._fetch_new` also skips URLs whose normalized form matches any already-fetched page's requested *or final* URL, which catches redirects. `prompts.render_pages` dedupes by final URL, keeping the OK page with the longest text. URLs are deduped by their normalized form but fetched as written, since normalizing strips `www.`.
- **`Page.method` is never blank**: `http_error` for HTTP 4xx/5xx (text dropped) and request exceptions, `fetch_error` for crashes, `firecrawl_markdown`, `fake`, or one of the HttpFetch extraction methods.
- **Thin pages (< 1500 chars)** in `HttpFetch`: trafilatura is tried, then trafilatura with favor_recall, then the `<url>.md` twin (docs URLs only), then lxml visible text. The first candidate that is no longer thin wins, else the longest. `method` / `text_source_url` are recorded in the page meta. `<origin>/llms.txt` is fetched once per app as its own page when a docs page is thin.
- **Pass-2 reviewer** (`python -m agent review --from-run <id>`; `agent/review.py`, `agent/review_rules.py`): it does not redo the run. Per row it does three things.
  - It runs deterministic rule checks (stable ids in `review_rules.RULES`) plus `validate_row`, then makes one LLM reviewer call (`prompts.review_system`) that only lists rule ids, with quotes.
  - Rows where nothing fired are written through unchanged. Otherwise only the fired rules' fields are re-researched, plus coupled fields (`COUPLED`: access→verdict/blocker, has_public_docs↔docs_url, ...). This means field-specific searches, cited pages re-read, gap-fill, and llms.txt when MCP is in scope. Then one fill whose output is merged only into those fields, their evidence claims and notes (`merge_fill`).
  - Replaced claims are evidence-checked against pages fetched in the review. Frozen claims keep pass-1 evidence. There is one retry. If it still fails, the pass-1 values are kept with a note (`status: kept`).
  - Deterministic checks read only notes and quotes from vendor-owned pages and are tuned for precision. The LLM is the recall net. LLM hits whose rule cannot apply to the row (`PRECONDITIONS`) are ignored and logged.
  - The OAuth notes contradiction from `notes_contradictions` folds into `mcp_only_oauth`. Other validator errors are `validation_failed`.
  - `review.jsonl` holds per-row rules, findings, before/after, and spend. Resume skips slugs in the output `rows.jsonl`.
- Concurrency uses a thread pool (the SDKs are sync). Writes to the jsonl files go through one lock. Resume means skipping slugs already in `rows.jsonl`.
- Do not run the agent on all 100 apps without being asked. It costs real LLM tokens.
- No person's real name anywhere. The repo owner is "adithyaprad".
