# Scoring rubric

This rubric is how we decide whether a researched row is right. It is also how we will report **pass 1 vs final accuracy** on the gold sample.

The assignment is explicit on one point we take as law: **“gated, with evidence” is a correct answer**, not a miss. We do not need paid or partner accounts. If the agent correctly says an app is gated or has no public API, and backs it with a URL, that counts.

Gold-sample answers are not filled in this phase. The 20 apps are listed in `gold_sample.md` and frozen before the agent runs, so we cannot quietly change the test after seeing results.

## What gets scored

Identity fields (`id`, `slug`, `name`, `category`) are copied from `data/apps.csv`. A mismatch there is a pipeline bug, not a research error. If it happens, the row is invalid and scores 0, but it should not happen.

Researched fields are split into **critical** and **minor**.

### Critical fields

These decide whether a third party could actually start building a toolkit.

| Field | How a match is judged |
|---|---|
| `auth_methods` / `primary_auth` | See auth rules below. Treated as one critical unit. |
| `access` | Exact enum match, with the access notes below. |
| `api_types` + `has_public_docs` | See API rules below. Treated as one critical unit. |
| `mcp_status` | Exact enum match. |
| `verdict` | Exact enum match, except the allowed pairs listed under verdict rules. |
| `blocker` | Exact enum match when verdict is not `buildable_now`. Must be `none` when verdict is `buildable_now`. |
| Evidence on the critical claims | Each of `auth`, `access`, `api_surface`, `mcp`, `verdict` has at least one URL. The URL must actually support the claim (see evidence rules). |

If any critical unit is wrong, or required evidence is missing or does not support the claim, the row is **not correct**.

### Minor fields

These are reported, and they affect per-field accuracy, but they do **not** by themselves fail the row.

| Field | How a match is judged |
|---|---|
| `one_liner` | Semantically equivalent is enough. It must be about the right product and not misleading. Wording need not match. |
| `api_breadth` | Exact enum match. `unknown` is accepted when official docs are too thin to judge, even if a reviewer would have guessed. |
| `docs_url` | Must point at official docs for the same product/API when `has_public_docs` is true. A different official entry point (guides vs reference) is fine. A blog post, wrong product, or 404 is not. |
| `mcp_url` | Required when `mcp_status` is not `none`. Must point at that official or community server. A different path to the same server is fine. |
| `confidence` | Not scored for correctness. It is metadata about certainty. |
| `requires_end_user_admin` | Exact boolean match when the gold label is confident; ignored if gold marks it unknown/unspecified in notes. |
| `notes` | Not scored, except they must be non-empty when `blocker` is `other`. |

### Coverage lookup (scored separately)

`composio_toolkit` is **not** a critical research field and is **not** part of the headline row-accuracy number. It is a fact lookup against a frozen catalog snapshot (`data/composio_toolkits.json`) and the alias map in `data/composio_aliases.json`.

The gold label for this field is the row in `data/composio_coverage.csv`. A match is an exact `status` match, and when status is `exists`, an exact `toolkit_slug` match. Missing or extra `sibling_slugs` is a minor miss on this lookup only.

If we report a coverage-lookup accuracy, it is:

```
coverage_accuracy = (gold apps whose composio_toolkit status and primary slug match) / (gold apps)
```

A later catalog refresh can change labels. We score against the snapshot that was frozen for that pass, and we say which snapshot `checked_at` was used.

## Field rules in more detail

### Auth

- The gold set is the list of documented methods. Extra or missing values are a miss, with two exceptions:
  - If the agent finds a real additional official method that the gold pass missed, gold is updated and the agent is marked correct. That is a gold fix, not extra credit, and it is logged.
  - Close variants that we deliberately collapsed do not count as extra methods: personal access tokens, bot tokens, and dashboard API keys are all `api_key`.
- Auth is scored for the product's **developer API** only. OAuth that only applies to an MCP server, and website / dashboard / SSO login, are not API auth methods. Listing them in `auth_methods` is an extra value, and a miss.
- `primary_auth` is correct if it is a documented API method and follows the preference rule: `oauth2` when OAuth2 is a documented way to call the product's API itself; otherwise the method official docs present as the default or most common. Validation only checks that `primary_auth` is in `auth_methods`. The OAuth preference is judged here, not in code.
  - If a fetched official page documents OAuth2 for *third-party / external apps* calling the product's own API (authorization code, PKCE, or client credentials against that API), then `auth_methods` must include `oauth2` and `primary_auth` must be `oauth2`, even when API keys also exist and are simpler. Example: a CRM whose developer docs have an "OAuth for apps" page next to API keys.
  - OAuth that only authenticates an MCP server, or only a dashboard / SSO login, is neither in `auth_methods` nor `primary_auth`.
  - `notes` must not contradict the auth fields. Notes that say OAuth is MCP-only, login-only, undocumented for the API, or should be dropped, while `auth_methods` still lists `oauth2`, make the auth unit wrong. Validation flags this self-contradiction and the agent retries once.
- If official docs only describe one method, `auth_methods` is that one value and it is also `primary_auth`.
- `unknown` means the auth method could not be determined because developer docs are absent, gated, or omit auth. It is used alone, for both `auth_methods` and `primary_auth`. It matches a gold `unknown`. It is not interchangeable with `custom_other`, which is for a documented vendor-specific scheme (HMAC, mTLS, proprietary handshake).

### Access

Exact enum match, plus:

- Free signup or a trial that already includes API credentials → `self_serve`.
- Developer can buy a plan or sign up online and get API credentials with **no human vendor review**, and there is no qualifying trial → `self_serve_paid`.
- Getting usable credentials, or being allowed to call the API as a third-party or multi-tenant app, needs **manual vendor review**: app review, a developer-profile approval, a partner or solution-provider program, or a mandatory app-store listing → `gated_partner`, with verdict `buildable_with_approval` and blocker `gated_access`. A paid seller or developer account does **not** make this `self_serve_paid` when that review is also required.
  - Example: Apple App Store Connect or Google Play Console. A paid developer account can be bought online, but an app still has to pass the vendor's review before it ships, so the label is `gated_partner` + `buildable_with_approval` + `gated_access`, not `self_serve_paid`.
- Contact sales, enterprise contract, waitlist, or manual vendor approval → `gated_sales`.
- No documented public API → `no_public_api`.
- A workspace admin having to install an app is **not** gated access. Customer-side admin approval is `requires_end_user_admin`, not a vendor gate.
- “Gated, with evidence” is correct even if we never obtain credentials.

### API surface

- `has_public_docs` must match gold.
- `api_types` must include every documented style in gold (`rest`, `graphql`, `soap`, etc.). Missing a documented style is a miss. An extra style is a miss unless gold is updated as above.
- `sdk_only` is only used when raw HTTP is not publicly documented, so it is never combined with `rest`, `graphql`, or `soap`. A REST API that also ships SDKs is `["rest"]`. Validation rejects the combination.
- `cli_only` is for a local CLI or library with no hosted public HTTP API.
- `none` cannot be combined with other types.

### MCP

- `official` means the vendor publishes or endorses it (docs, official repo, or official announcement).
- `community` means third-party only.
- If both exist, the correct status is `official`.
- A vague mention is `none`, with `mcp_url: null` and the mention recorded in `notes`. Examples: "MCP (beta)", or "coming soon" on a marketing page, with no actual server URL, installable repo, or dedicated MCP docs page. Labelling such a mention `official` or `community` is a miss.
- A real MCP docs page or server published by the vendor is `official`, including one on a sibling subdomain (`marketplace.`, `developers.`, `docs.`) or linked from the vendor's `llms.txt`.
- `none` is correct when no usable server is found after checking official docs (including `llms.txt` on the docs origin), the vendor’s GitHub org if any, and a reasonable web/MCP-directory search. Evidence for `none` is the official docs or org URL that was checked, with a note that no official MCP was found. We do not require a page that literally says “we have no MCP”.

### Verdict and blocker

Allowed pairings:

| access (typical) | verdict | blocker |
|---|---|---|
| `self_serve` or `self_serve_paid` and a usable documented interface | `buildable_now` | `none` |
| `gated_partner` or `gated_sales` and a documented interface | `buildable_with_approval` | `gated_access` |
| `no_public_api` | `not_buildable` | `no_public_api` |

Exceptions that are still correct:

- Terms forbid this kind of automation → `not_buildable` + `terms_restrict_automation`, even if an API exists.
- Docs or auth are too thin to implement → `not_buildable` + `auth_undocumented` or `docs_insufficient`.
- Interface is real but not useful as a toolkit → `not_buildable` + `api_too_narrow`.
- Only an unstable preview exists → `not_buildable` or `buildable_with_approval` + `preview_or_unstable`, matching gold.
- A local CLI that can be wrapped as a skill, with no hosted API → `buildable_now` + `none`, and `api_types` includes `cli_only`.

If two blockers apply, gold and the agent must agree on the *main* one, using this priority: `no_public_api` > `terms_restrict_automation` > `auth_undocumented` / `docs_insufficient` > `gated_access` > `api_too_narrow` / `preview_or_unstable` > `other`.

### Evidence quality

Preference order: `official_docs` > `official_other` > `community` / `news` / `other`.

- If official docs exist for a claim, the agent should cite them. Citing only a blog or a third-party page when official docs are easy to find is a **minor** evidence miss (does not fail the row) unless the cited page is also wrong.
- A URL that does not support the claim (wrong product, marketing fluff used as proof of auth, 404, or a page that contradicts the label) is a **critical** miss for that field.
- Pricing pages and official blogs are acceptable for `access` when the docs do not spell out plan gates.
- Community sources are acceptable when official sources are absent, and should usually come with `confidence: low`.
- Wrong product: pages about a different product or industry that shares the app's name are not evidence. A confident row for the wrong product is a critical miss on every field it touches. When the CSV has no `start_url`, `source_hint` and the category identify the product. If the official product cannot be identified, the correct row is `auth_methods: ["unknown"]`, `verdict: not_buildable`, `blocker: docs_insufficient`, `confidence: low`, with notes saying the official product was not found.

## How accuracy is computed

All percentages use the frozen gold sample (20 apps unless a listed app is later withdrawn as unlabelable, which would be disclosed).

### Per-field accuracy

For each scored field or critical unit:

```
field_accuracy = (number of gold apps where that field matches) / (number of gold apps where the field applies)
```

`mcp_url` applies only when gold `mcp_status` is not `none`. `blocker` always applies. `confidence` and `notes` are not included in per-field accuracy. `composio_toolkit` is reported as coverage-lookup accuracy, not as a research per-field score and not in the headline row-accuracy number.

We report each critical unit and each minor field separately.

### Per-row accuracy (the headline number)

A gold app is a **row-correct** if:

1. Every critical unit matches, and
2. Required evidence is present and actually supports those critical claims.

Minor mismatches do not fail the row. They still show up in per-field scores.

```
row_accuracy = (number of row-correct gold apps) / (number of gold apps)
```

This is the number we compare across passes: **pass 1 row accuracy vs final row accuracy**.

### Pass 1 vs final

1. Freeze the gold list (already done in `gold_sample.md`).
2. Fill gold labels by hand *before looking at agent output for those apps* (see procedure below).
3. Run the agent on all 100 apps (pass 1). Score the 20 gold apps against the frozen labels. Record per-field and per-row accuracy, plus every miss.
4. Run verification loops (checker agent, browser pass on flagged rows, rubric consistency checks). The agent may revise any of the 100 rows, including gold ones.
5. Score the *same* 20 apps again, with the *same* gold labels, unless a gold label is proven wrong by official docs. Gold fixes are logged and applied to both pass 1 and final scoring so we do not inflate the gain.
6. Report both passes, the misses that were fixed, and the misses that remained.

## Gold-sample procedure

### Selection

20 apps, 2 per category, mixing well-documented “easy” cases with gated, obscure, or awkward “hard” cases. The list and the reasons are in `gold_sample.md`. Do not swap apps after pass 1 unless an app is withdrawn as unlabelable; if that happens, disclose it and do not silently replace it with an easier one.

### How gold labels are produced

1. A human opens official docs (or the best available official page) for each gold app, using the CSV `start_url` / `source_hint` only as a starting point, not as the answer.
2. The human fills a complete row that validates against `app_row.schema.json`, including evidence URLs.
3. Hard apps are expected. If the honest label is `gated_sales`, `gated_partner`, or `no_public_api`, write that with evidence and move on.
4. Gold answers live in a file that is added later (for example `schema/gold_sample.jsonl`). They are not in this phase.

### Disagreements and ambiguity

- Official docs win over blogs, memory, or the agent.
- If two reviewers (or a reviewer and a later check) disagree, re-read the official docs and try to agree on one label.
- If the official docs are genuinely ambiguous, record an **acceptable set** (e.g. `access` may be `self_serve_paid` or `gated_sales` when pricing is unclear). The agent is correct if it hits any label in that set *and* its evidence is valid. The ambiguity is noted on the case-study page.
- If an app cannot be labeled at all (product unclear, official presence missing, assignment hint unresolvable), withdraw it from scoring, disclose it, and do not replace it after seeing agent results.

### Independence

Gold labels for an app must be written before looking at the agent’s row for that app. After pass 1, gold changes only when official docs prove the gold label wrong.
