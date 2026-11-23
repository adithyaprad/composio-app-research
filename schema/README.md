# App row schema

This folder defines **exactly what the research agent must output for each app**, and (in `RUBRIC.md`) **how we judge whether that output is correct**.

A schema is a contract. Before any agent code exists, we lock the fields, the allowed values, and what each value means, so two people — or a person and an agent — would label the same app the same way. The agent writes one JSON object per app that must validate against `app_row.schema.json`. The 100 apps themselves live in `../data/apps.csv`; the agent does not invent names, ids, slugs, or categories.

Identity fields (`id`, `slug`, `name`, `category`) are copied from the CSV. Everything else is researched.

## Fields

### Identity (from the CSV, not researched)

- **id** — stable number 1–100 from the assignment list.
- **slug** — kebab-case id used in filenames and joins (e.g. `whatsapp-business`).
- **name** — app name as listed.
- **category** — one of the 10 assignment categories (see enums below).

### What it does

- **one_liner** — one sentence on what the product does (20–240 characters).

### Auth

- **auth_methods** — every documented way a developer or app can authenticate. List all that apply.
- **primary_auth** — the method a multi-tenant agent toolkit would implement first. If OAuth2 is documented, prefer it; otherwise use the most common documented method. Must be one of `auth_methods`.

### Access (how a developer gets credentials)

- **access** — vendor path to credentials, not “can an end-user log in”.

### API surface

- **api_types** — documented interface styles. List all that apply; use `none` alone if there is no callable public interface.
- **has_public_docs** — true if official public API or developer docs exist.
- **api_breadth** — rough useful surface: `narrow`, `moderate`, `broad`, or `unknown`. This is a sketch, not an endpoint count.
- **docs_url** — best official docs entry point; `null` only when `has_public_docs` is false.

### Existing MCP

- **mcp_status** — `official`, `community`, or `none`. If both official and community exist, label `official` and mention the community server in `notes`.
- **mcp_url** — server, repo, or official announcement. Required when status is not `none`; `null` when `none`.

### Buildability

- **verdict** — could this be an agent toolkit today?
- **blocker** — the single main reason it is not `buildable_now`. Must be `none` if and only if the verdict is `buildable_now`. Secondary issues go in `notes`.

### Evidence, confidence, flags

- **evidence** — one or more URLs for each of `auth`, `access`, `api_surface`, `mcp`, and `verdict`. `one_liner` evidence is optional (a homepage is enough). Each item has `url`, `source_type`, and an optional `note`.
- **confidence** — overall confidence in the row, driven by evidence quality.
- **requires_end_user_admin** — true when a *customer* workspace/org admin must approve an install (e.g. Slack). This is **not** a vendor gate and does not by itself make `access` gated.
- **notes** — caveats, ambiguity, secondary blockers. Must be non-empty when `blocker` is `other`.

## Enums

### category

| Value | Label in the research set |
|---|---|
| `crm_and_sales` | CRM and Sales |
| `support_and_helpdesk` | Support and Helpdesk |
| `communications_and_messaging` | Communications and Messaging |
| `marketing_ads_email_social` | Marketing, Ads, Email and Social |
| `ecommerce` | Ecommerce |
| `data_seo_and_scraping` | Data, SEO and Scraping |
| `developer_infra_and_data` | Developer, Infra and Data platforms |
| `productivity_and_project_management` | Productivity and Project Management |
| `finance_and_fintech` | Finance and Fintech |
| `ai_research_and_media` | AI, Research and Media-native |

### auth_methods / primary_auth

| Value | Meaning |
|---|---|
| `oauth2` | OAuth 2.0 (authorization code, client credentials, or similar). |
| `api_key` | A developer-generated secret: API keys, personal access tokens, and dashboard tokens. These are lumped together so labelers do not argue “key vs token”. |
| `basic` | HTTP Basic (username and password, or username and API key sent as Basic). |
| `jwt_service_account` | Signed JWT, service-account JSON key, or similar machine credential that is not a static API key. |
| `custom_other` | A vendor-specific scheme that does not fit the above (HMAC signatures, mutual TLS, proprietary handshakes). Explain in `notes`. |
| `none` | No auth required (public or local tool). Use this value alone. |

### access

| Value | Meaning |
|---|---|
| `self_serve` | A developer can create an account and get credentials alone, for free or on a trial, with no sales conversation. |
| `self_serve_paid` | Same, but API access requires a paid plan the developer can buy without talking to sales. A free trial that already includes API access is `self_serve`, not this. |
| `gated_partner` | Credentials require a partner program, app-store review, or vendor partnership (e.g. WhatsApp Business solution-provider approval). |
| `gated_sales` | Credentials require contacting sales, an enterprise contract, a waitlist, or manual vendor approval that is not a self-serve checkout. |
| `no_public_api` | No documented public API a third-party developer can call. |

Workspace-admin install approval is `requires_end_user_admin: true`, not a gated `access` value.

### api_types

| Value | Meaning |
|---|---|
| `rest` | Documented HTTP REST (or REST-like) API. |
| `graphql` | Documented GraphQL API. |
| `soap` | Documented SOAP / XML API. |
| `sdk_only` | Official SDK or client library is the only supported interface; no public raw HTTP docs. |
| `webhook_only` | Outbound webhooks exist, but there is no useful request API. |
| `cli_only` | The usable interface is a local CLI or library, not a hosted HTTP API. |
| `none` | No callable public interface found. Use this value alone. |

### api_breadth

| Value | Meaning |
|---|---|
| `narrow` | A small, focused surface (roughly a handful of useful resources). |
| `moderate` | A typical product API: core objects are covered, not a platform-scale catalog. |
| `broad` | A large platform surface (many resources, or several distinct API families). |
| `unknown` | Not enough official detail to judge. Prefer this over guessing. |

### mcp_status

| Value | Meaning |
|---|---|
| `official` | Published or endorsed by the vendor (listed in their docs, official repo, or official announcement). |
| `community` | A third-party server only (GitHub, directories, blogs). |
| `none` | No usable MCP server found. |

### verdict

| Value | Meaning |
|---|---|
| `buildable_now` | A documented callable interface and a self-serve path (`self_serve` or `self_serve_paid`) exist, so a toolkit or skill could be built today. Paid-plan API access is not an engineering blocker. Local CLIs that can be wrapped as a skill also count. |
| `buildable_with_approval` | A documented interface exists, but credentials or listing require partner, sales, or vendor approval first. |
| `not_buildable` | No usable public interface, terms forbid this kind of automation, or docs/auth are too incomplete to implement against. |

### blocker

| Value | Meaning |
|---|---|
| `none` | No blocker. Required when verdict is `buildable_now`. |
| `no_public_api` | No documented public interface. |
| `gated_access` | Partner/sales/approval gate is the main thing in the way. Typical for `buildable_with_approval`. |
| `auth_undocumented` | An API may exist, but auth is not documented well enough to implement. |
| `docs_insufficient` | Auth may be known, but the API is not documented well enough to implement. |
| `api_too_narrow` | The interface exists but is too thin to be a useful toolkit (e.g. webhooks only, or one or two unrelated endpoints). |
| `terms_restrict_automation` | Terms of use or policy forbid bots, scraping, or third-party agent access. |
| `preview_or_unstable` | Only an alpha/beta or unstable preview exists. |
| `other` | Anything else; `notes` must explain. |

### source_type

| Value | Meaning |
|---|---|
| `official_docs` | Vendor developer docs, API reference, or official help article. Preferred. |
| `official_other` | Vendor blog, changelog, pricing page, or official GitHub org. |
| `community` | Third-party docs, unofficial repos, forums, directories. |
| `news` | Press or independent articles. |
| `other` | Anything else; add a `note`. |

### confidence

| Value | Meaning |
|---|---|
| `high` | Official docs (or official MCP announcement) directly support the critical claims. |
| `medium` | Official but incomplete pages, or a solid official blog/pricing page, with a small amount of inference. |
| `low` | Community/news only, or an absence claim (e.g. no MCP found) without a strong official page to hang it on. |

## Consistency rules

These are also encoded in the rubric and should be treated as part of a valid row:

1. `primary_auth` is a member of `auth_methods`.
2. If `auth_methods` contains `none`, it is the only value.
3. If `api_types` contains `none`, it is the only value.
4. `docs_url` is `null` if and only if `has_public_docs` is false.
5. `mcp_url` is `null` if and only if `mcp_status` is `none`.
6. `blocker` is `none` if and only if `verdict` is `buildable_now`.
7. `access: no_public_api` goes with `verdict: not_buildable` and `blocker: no_public_api` (unless a narrow official CLI/skill path is documented — then explain in `notes` and do not use `no_public_api`).
8. `access: gated_partner` or `gated_sales` is normally `verdict: buildable_with_approval` and `blocker: gated_access`, unless a separate blocker is worse (no docs, terms, etc.).
9. When `blocker` is `other`, `notes` is non-empty.

## Example (illustrative only, not a scored gold answer)

```json
{
  "id": 21,
  "slug": "slack",
  "name": "Slack",
  "category": "communications_and_messaging",
  "one_liner": "Team messaging platform with channels, DMs, and an app ecosystem.",
  "auth_methods": ["oauth2", "api_key"],
  "primary_auth": "oauth2",
  "access": "self_serve",
  "api_types": ["rest"],
  "has_public_docs": true,
  "api_breadth": "broad",
  "docs_url": "https://api.slack.com/",
  "mcp_status": "official",
  "mcp_url": "https://docs.slack.dev/",
  "verdict": "buildable_now",
  "blocker": "none",
  "evidence": {
    "auth": [{"url": "https://api.slack.com/authentication", "source_type": "official_docs"}],
    "access": [{"url": "https://api.slack.com/apps", "source_type": "official_docs"}],
    "api_surface": [{"url": "https://api.slack.com/methods", "source_type": "official_docs"}],
    "mcp": [{"url": "https://docs.slack.dev/", "source_type": "official_docs"}],
    "verdict": [{"url": "https://api.slack.com/methods", "source_type": "official_docs"}]
  },
  "confidence": "high",
  "requires_end_user_admin": true,
  "notes": "Workspace admin approval is an install step, not a vendor credential gate. Bot tokens are listed under api_key."
}
```

The Slack URLs above are placeholders for the format. Gold-sample answers are filled only during the human check, not in this phase.
