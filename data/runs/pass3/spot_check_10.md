# Spot check: 10 random rows (pending human check)

A fixed-seed (`42`) random sample of 10 rows from the final 100, excluding the 20 gold and 10 holdout apps (30 rows excluded). Candidates are sorted by slug and drawn with `random.Random(42).sample`, so the list is the same on every run.

**Status: pending human verification. These rows are not self-graded.** A person checks each row against the vendor's official docs and ticks one box. Screenshot paths are relative to this run folder.

## 1. Copper (`copper`)

- **category:** crm_and_sales
- **auth_methods / primary_auth:** [oauth2, api_key] / `oauth2`
- **access:** `self_serve`
- **api_types / has_public_docs:** [rest] / true
- **mcp_status / mcp_url:** `none` / null
- **verdict / blocker:** `buildable_now` / `none`
- **confidence:** `medium`
- **needs_human:** true (confidence is still not high after pass 3; notes report conflicting docs, a login wall, a deprecation or migration, or a claim the browser pass left unverified)
- **notes:** Rendered pages confirm API keys (self-generated in System settings) and OAuth2.0 (preferred for partner integrations). The earlier note that OAuth client registration goes through partners@copper.com was not re-checked in the rendered pages. The official Copper MCP is not live: copper.com/mcp says the beta launches in November 2026, read-only, with a waitlist. Copper GPT (ChatGPT) exists separately. Which plans include API access is unclear. Copper works only with Gmail and Google Workspace.

Evidence:

- `auth` https://developer.copper.com/introduction/authentication.html (official_docs)
  > There are two ways to access the Copper Developer API: API keys and OAuth2.0.
  > OAuth2.0 is the preferred approach for partner integrations.
- `access` https://developer.copper.com/introduction/authentication.html (official_docs)
  > To generate an API token, in the Copper web app go to System settings > API Keys and click the 'GENERATE API KEY' button.
- `access` https://www.copper.com/pricing (official_other)
  > Start free, no credit card required.
- `api_surface` https://developer.copper.com/introduction/oauth/flow.html (official_docs)
  > https://api.copper.com/developer_api/v1/account
- `mcp` https://www.copper.com/mcp (official_other)
  > Copper is launching an official MCP server, built and hosted by Copper. The beta launches in November 2026.
  > Request early access to the beta, or join the waitlist for updates as we get closer to launch.
- `verdict` https://developer.copper.com/introduction/authentication.html (official_docs)
  > Users can hold multiple valid tokens at the same time.
- `verdict` https://www.copper.com/pricing (official_other)
  > Instant activation, no credit card required.

Screenshots: `screenshots/copper/01-76027c04a9.jpg`, `screenshots/copper/02-90b1656d72.jpg`, `screenshots/copper/03-20cc1cb39d.jpg`, `screenshots/copper/04-8c5719c51a.jpg`

- [ ] Confirmed against official docs
- [ ] Needs correction (notes: ___)

## 2. Amazon Selling Partner (`amazon-selling-partner`)

- **category:** ecommerce
- **auth_methods / primary_auth:** [oauth2] / `oauth2`
- **access:** `gated_partner`
- **api_types / has_public_docs:** [rest] / true
- **mcp_status / mcp_url:** `none` / null
- **verdict / blocker:** `buildable_with_approval` / `gated_access`
- **confidence:** `medium`
- **needs_human:** true (access needs vendor outreach (sales demo or manual partner review) a bot cannot pass; confidence is still not high after pass 3)
- **notes:** Browser pass confirmed OAuth 2.0 (Login with Amazon) and the review gates for public developers. The Selling Partner plugin (beta, US) for AWS Quick and Claude is a blog announcement only, with no server URL, repo or MCP docs, so mcp_status stays none. The Amazon Ads MCP Server belongs to Amazon Ads, a different product. A private-developer self-authorization path exists (self-authorization, step 2C) but its approval requirements were not shown, so access stays gated_partner. Medium confidence.

Evidence:

- `auth` https://developer.amazonservices.com/start/getting-started-for-public-developers (official_docs)
  > The authorization model for the Selling Partner API is based on Login with Amazon, Amazon’s implementation of OAuth 2.0.
- `access` https://developer.amazonservices.com/start/getting-started-for-public-developers (official_docs)
  > public developers must list their app in the Amazon Selling Partner Appstore
  > you must request and qualify for a particular role, or you will not be able to access the operations and resources grouped under that role.
- `access` https://sell.amazon.com/pricing (official_other)
- `api_surface` https://developer-docs.amazon.com/sp-api/ (official_docs)
- `api_surface` https://developer-docs.amazon.com/amazon-shipping/docs/what-is-the-selling-partner-api (official_docs)
  > The Selling Partner API (SP-API) is a REST-based API that helps Amazon selling partners programmatically access their data on orders, shipments, payments, and much more.
- `mcp` https://sell.amazon.com/blog/announcements/amazons-selling-partner-plugin-ads-mcp (official_other)
  > the Selling Partner plugin is currently in beta for US stores.
- `mcp` https://developer-docs.amazon/sp-api (official_docs)
  > Welcome to Selling Partner API Documentation. Learn how to get started and build applications with Amazon's REST-based SP-API.
- `verdict` https://developer.amazonservices.com/start/getting-started-for-public-developers (official_docs)
  > must go through an architecture review with the SP-API Solutions Architecture team.

Screenshots: `screenshots/amazon-selling-partner/01-05162b962c.jpg`, `screenshots/amazon-selling-partner/02-719a3642e7.jpg`, `screenshots/amazon-selling-partner/03-61ea7f35b7.jpg`, `screenshots/amazon-selling-partner/04-70a7c17549.jpg`

- [ ] Confirmed against official docs
- [ ] Needs correction (notes: ___)

## 3. LiveAgent (`liveagent`)

- **category:** support_and_helpdesk
- **auth_methods / primary_auth:** [api_key] / `api_key`
- **access:** `self_serve`
- **api_types / has_public_docs:** [rest] / true
- **mcp_status / mcp_url:** `official` / `https://support.liveagent.com/647358-MCP-Integration-for-agents`
- **verdict / blocker:** `buildable_now` / `none`
- **confidence:** `high`
- **needs_human:** true (notes report conflicting docs, a login wall, a deprecation or migration, or a claim the browser pass left unverified)
- **notes:** Browser pass confirmed self-serve trial signup, API key auth (v3 keys under Configuration > System > API, v1 deprecated) and a live official MCP server. MCP OAuth 2.1 applies to the MCP server only, so it is excluded from auth_methods. The MCP server is only on Business and Enterprise plans. The Swagger API reference was not rendered, so API breadth stays unknown.

Evidence:

- `auth` https://faq.liveagent.com/535002-LiveAgent-API-v1-vs-v3-Authentication-Differences-API-Key-Management-and-Resolving-403-Errors (official_docs)
  > Both versions can be used simultaneously, but they require separate API keys.
  > Navigate to Configuration > System > API.
- `access` https://www.liveagent.com/trial/ (official_other)
  > Your LiveAgent is ready in about a minute
  > The trial ends and nothing is charged: we have no card.
- `access` https://www.liveagent.com/pricing/ (official_other)
  > 30-day free trial no credit card
- `api_surface` https://faq.liveagent.com/916372-API-v1-vs-API-v3-in-LiveAgent-Key-Differences-Authentication-and-Resolving-ApiKey-Errors (official_docs)
  > POST /api/conversations/{conversationId}/messages
- `mcp` https://support.liveagent.com/647358-MCP-Integration-for-agents (official_docs)
  > LiveAgent exposes a built-in MCP server (Model Context Protocol)
  > MCP server URL https://<your-liveagent-domain>/api/mcp
- `mcp` https://www.liveagent.com/pricing/ (official_other)
- `verdict` https://faq.liveagent.com/535002-LiveAgent-API-v1-vs-v3-Authentication-Differences-API-Key-Management-and-Resolving-403-Errors (official_docs)
  > Generate or manage your API v3 key here.
- `verdict` https://www.liveagent.com/trial/ (official_other)
  > Start free trial
- `one_liner` https://www.liveagent.com/ (official_other)
  > LiveAgent is cloud help desk software for small and growing support teams

Screenshots: `screenshots/liveagent/01-c33b8e0bf5.jpg`, `screenshots/liveagent/02-aa7bf6b127.jpg`, `screenshots/liveagent/03-0cfc810319.jpg`, `screenshots/liveagent/04-e0d114bad8.jpg`

- [ ] Confirmed against official docs
- [ ] Needs correction (notes: ___)

## 4. Jira (`jira`)

- **category:** productivity_and_project_management
- **auth_methods / primary_auth:** [oauth2, api_key, jwt_service_account] / `oauth2`
- **access:** `self_serve`
- **api_types / has_public_docs:** [rest] / true
- **mcp_status / mcp_url:** `official` / `https://developer.atlassian.com/cloud/rovo-mcp/`
- **verdict / blocker:** `buildable_now` / `none`
- **confidence:** `high`
- **needs_human:** false
- **notes:** Browser pass confirmed self-serve access: Free plan and trials need no sales contact, and OAuth apps are created in the developer console. Basic is dropped as a separate method because the Basic header only carries email + API token (api_key). JWT applies to Connect apps. Atlassian policy says apps that collect API tokens or ask customers to make their own 3LO apps violate its policy; distributable apps should use one 3LO app. Rovo MCP API-token auth needs org admin enablement, and some Rovo tools use credits. Pricing page notes usage-based billing starting Dec 3, 2026. Data Center is not covered. Admin requirement for app install not shown.

Evidence:

- `auth` https://developer.atlassian.com/cloud/jira/platform/oauth-2-3lo-apps/ (official_docs)
  > OAuth 2.0 (3LO) allows external applications and services to access Atlassian product APIs on a user's behalf.
- `auth` https://developer.atlassian.com/cloud/jira/platform/basic-auth-for-rest-apis/ (official_docs)
  > basic authentication with an Atlassian account email address and API token. Authentication using passwords has been deprecated.
  > Forge apps and Connect apps use more secure methods for authentication: OAuth 2.0 and JWT respectively.
- `access` https://www.atlassian.com/software/jira/pricing (official_other)
  > Free forever for 10 users
  > Start free trial
- `access` https://developer.atlassian.com/cloud/jira/platform/oauth-2-3lo-apps/ (official_docs)
  > OAuth 2.0 (3LO) apps are created and managed in the developer console.
- `api_surface` https://developer.atlassian.com/cloud/jira/platform/basic-auth-for-rest-apis/ (official_docs)
  > https://your-domain.atlassian.net/rest/api/2/issue/createmeta
- `mcp` https://developer.atlassian.com/cloud/rovo-mcp/ (official_docs)
  > The Atlassian Rovo MCP Server is a cloud-hosted Model Context Protocol (MCP) server that connects AI-powered clients to your Atlassian Cloud apps.
- `verdict` https://developer.atlassian.com/cloud/jira/platform/basic-auth-for-rest-apis/ (official_docs)
  > You generate an API token for your Atlassian account and use it to authenticate anywhere where you would have used a password.

Screenshots: `screenshots/jira/01-0663cd8aff.jpg`, `screenshots/jira/02-29e487b114.jpg`, `screenshots/jira/03-f06af199ca.jpg`

- [ ] Confirmed against official docs
- [ ] Needs correction (notes: ___)

## 5. higgsfield (`higgsfield`)

- **category:** ai_research_and_media
- **auth_methods / primary_auth:** [api_key] / `api_key`
- **access:** `self_serve_paid`
- **api_types / has_public_docs:** [rest] / true
- **mcp_status / mcp_url:** `official` / `https://mcp.higgsfield.ai/mcp`
- **verdict / blocker:** `buildable_now` / `none`
- **confidence:** `medium`
- **needs_human:** true (confidence is still not high after pass 3; notes report conflicting docs, a login wall, a deprecation or migration, or a claim the browser pass left unverified)
- **notes:** 'Contact sales' on the pricing page is for the Enterprise tier only. Individual plans are online, and API credentials come from Higgsfield Console. The browser pass could not confirm whether the API requires a paid plan, because the billing page was not rendered. It also could not confirm the MCP server URL, so the earlier help-center evidence stands. MCP and CLI use OAuth or browser sign-in, which is not developer API auth, so oauth2 is excluded. MCP and CLI deduct credits, and unlimited or free generations do not apply there.

Evidence:

- `auth` https://docs.higgsfield.ai/docs/authentication (official_docs)
  > Authorization: Key YOUR_KEY_ID:YOUR_KEY_SECRET
  > Create and manage API credentials in Higgsfield Console. Each credential consists of a key ID and a secret.
- `access` https://docs.higgsfield.ai/docs (official_docs)
  > Create and manage server-side credentials in Higgsfield Console.
- `access` https://higgsfield.ai/pricing.md (official_other)
  > Enterprise: custom pricing, tailored to the organization. Contact sales at https://higgsfield.ai/enterprise.
- `api_surface` https://docs.higgsfield.ai/docs (official_docs)
  > Submit a request to a model endpoint, then poll for the result or receive a webhook when processing finishes.
- `mcp` https://higgsfield.ai/pricing.md (official_other)
  > Unlimited access and free generations are not available in MCP/CLI, Canvas, or Supercomputer.
- `verdict` https://docs.higgsfield.ai/docs (official_docs)
  > Generate images and videos through one authenticated, asynchronous API.
- `verdict` https://docs.higgsfield.ai/docs/authentication (official_docs)
  > Authenticate server-side API requests with your Higgsfield key and secret.

Screenshots: `screenshots/higgsfield/01-449f62accb.jpg`, `screenshots/higgsfield/02-95f5a77cb3.jpg`, `screenshots/higgsfield/03-bc88d11469.jpg`, `screenshots/higgsfield/04-671af07ffc.jpg`

- [ ] Confirmed against official docs
- [ ] Needs correction (notes: ___)

## 6. Devin (`devin`)

- **category:** ai_research_and_media
- **auth_methods / primary_auth:** [api_key] / `api_key`
- **access:** `self_serve_paid`
- **api_types / has_public_docs:** [rest] / true
- **mcp_status / mcp_url:** `official` / `https://docs.devin.ai/work-with-devin/devin-mcp`
- **verdict / blocker:** `buildable_now` / `none`
- **confidence:** `medium`
- **needs_human:** true (confidence is still not high after pass 3; notes report conflicting docs, a login wall, a deprecation or migration, or a claim the browser pass left unverified)
- **notes:** Browser pass confirmed official MCP server and cog_ Bearer token auth (service user keys, PATs). The pricing page lists a Devin API row but the rendered text does not show which plans include it. Service user keys are provisioned in org/enterprise settings, likely by an admin. Legacy v1/v2 apk_ keys deprecated. Confidence stays medium.

Evidence:

- `auth` https://docs.devin.ai/api-reference/authentication (official_docs)
  > All API credentials use the cog_ prefix format. Include your token in the Authorization header of every request
  > Service User (non-human) Service User API Key For automated integrations and CI/CD pipelines
- `access` https://devin.ai/pricing (official_other)
  > Pro POPULAR $20 PER MONTH Select plan
- `access` https://docs.devin.ai/get-started/devin-intro (official_docs)
  > To access Devin, sign up at app.devin.ai. Individual and Teams plans are available.
- `api_surface` https://docs.devin.ai/api-reference/authentication (official_docs)
  > curl -X GET "https://api.devin.ai/v3/organizations/$DEVIN_ORG_ID/sessions"
- `api_surface` https://docs.devin.ai/llms.txt (official_docs)
  > API (315 pages)
- `mcp` https://docs.devin.ai/work-with-devin/devin-mcp (official_docs)
  > Base Server URL: https://mcp.devin.ai/
  > Set up the official Devin MCP server so external AI tools can manage sessions, playbooks, knowledge, and repository docs.
- `verdict` https://docs.devin.ai/api-reference/authentication (official_docs)
  > curl -X GET "https://api.devin.ai/v3/organizations/$DEVIN_ORG_ID/sessions"
- `verdict` https://devin.ai/pricing (official_other)
  > Devin API

Screenshots: `screenshots/devin/01-abdab1b3ff.jpg`, `screenshots/devin/02-afe65daee7.jpg`, `screenshots/devin/03-3cdfab2a58.jpg`, `screenshots/devin/04-7ceea0f5a5.jpg`

- [ ] Confirmed against official docs
- [ ] Needs correction (notes: ___)

## 7. Consensus (`consensus`)

- **category:** ai_research_and_media
- **auth_methods / primary_auth:** [api_key] / `api_key`
- **access:** `self_serve`
- **api_types / has_public_docs:** [rest] / true
- **mcp_status / mcp_url:** `official` / `https://docs.consensus.app/consensus-mcp`
- **verdict / blocker:** `buildable_now` / `none`
- **confidence:** `high`
- **needs_human:** true (notes report conflicting docs, a login wall, a deprecation or migration, or a claim the browser pass left unverified)
- **notes:** The MCP server uses OAuth 2.0 (with Dynamic Client Registration) for sign-in. The docs do not describe OAuth for the REST API, so it is excluded from auth_methods. The MCP also accepts API keys as Bearer tokens. M365 Copilot needs a tenant admin to add the connector. Legacy /v1/quick_search is deprecated. Research Agent Threads are available through the API.

Evidence:

- `auth` https://docs.consensus.app/api-get-started (official_docs)
  > `query` is the only required parameter, and the key goes in the `x-api-key` header.
  > Click Create API key, name the key, and click Copy API key.
- `access` https://docs.consensus.app/api-get-started (official_docs)
  > Every plan includes API access, and the Free plan includes 30 calls a month.
- `access` https://docs.consensus.app/core-features/mcp (official_docs)
  > It's self-serve and open to everyone.
- `api_surface` https://docs.consensus.app/api-reference/search (official_docs)
  > The supported endpoint for searching academic papers.
- `mcp` https://docs.consensus.app/consensus-mcp (official_docs)
  > https://mcp.consensus.app/mcp
  > One hosted server covers every field of science
- `verdict` https://docs.consensus.app/mcp-plans-and-access (official_docs)
  > Any Consensus API key works on the MCP server.

- [ ] Confirmed against official docs
- [ ] Needs correction (notes: ___)

## 8. Otter AI (`otter-ai`)

- **category:** ai_research_and_media
- **auth_methods / primary_auth:** [api_key] / `api_key`
- **access:** `gated_sales`
- **api_types / has_public_docs:** [rest] / true
- **mcp_status / mcp_url:** `official` / `https://help.otter.ai/hc/en-us/articles/35287607569687-Otter-MCP-Server`
- **verdict / blocker:** `buildable_with_approval` / `gated_access`
- **confidence:** `high`
- **needs_human:** true (access needs vendor outreach (sales demo or manual partner review) a bot cannot pass)
- **notes:** MCP uses OAuth, which applies to the MCP server only and is kept out of auth_methods. Zapier docs say Pro, Business and Enterprise can create API keys under Developer, but the Public API is Enterprise-only. Super Admin endpoints need a super admin. MCP is on all plans, including Basic.

Evidence:

- `auth` https://help.otter.ai/hc/en-us/articles/36130822688279-Otter-ai-Public-API (official_docs)
  > All API requests require authentication using Bearer token authentication.
- `access` https://help.otter.ai/hc/en-us/articles/36130822688279-Otter-ai-Public-API (official_docs)
  > Otter's Public API is available for all Enterprise workspaces. If you do not see this feature for your workspace, contact your Otter account manager.
- `access` https://otter.ai/pricing (official_other)
  > Custom pricing
- `api_surface` https://help.otter.ai/hc/en-us/articles/36130822688279-Otter-ai-Public-API (official_docs)
  > programmatic access to retrieve channels, conversations, transcripts, audio, action items, insights, outlines, and workspace details from your Otter account
- `mcp` https://help.otter.ai/hc/en-us/articles/35287607569687-Otter-MCP-Server (official_docs)
  > "url": "https://mcp.otter.ai/mcp"
- `verdict` https://help.otter.ai/hc/en-us/articles/36130822688279-Otter-ai-Public-API (official_docs)
  > Otter's Public API is available for all Enterprise workspaces.

- [ ] Confirmed against official docs
- [ ] Needs correction (notes: ___)

## 9. Plain (`plain`)

- **category:** support_and_helpdesk
- **auth_methods / primary_auth:** [oauth2, api_key] / `oauth2`
- **access:** `self_serve`
- **api_types / has_public_docs:** [graphql] / true
- **mcp_status / mcp_url:** `official` / `https://www.plain.com/docs/integrations/mcp-server`
- **verdict / blocker:** `buildable_now` / `none`
- **confidence:** `high`
- **needs_human:** false
- **notes:** OAuth apps work only in the workspace where they were created. The MCP server is OAuth-only (not in auth_methods since it is MCP-specific). Webhooks also exist. Some changes (API keys, billing, webhooks) are app-only. Admin requirements for creating OAuth apps or machine users are not stated.

Evidence:

- `auth` https://www.plain.com/docs/graphql/oauth (official_docs)
  > Your app gets an access token with the OAuth 2.0 authorization code flow
- `auth` https://www.plain.com/docs/graphql/authentication (official_docs)
  > pass it on any API call as a header: Authorization: Bearer plainApiKey_xxx
- `access` https://www.plain.com/pricing (official_other)
  > 7 day free trial No credit card required
- `access` https://www.plain.com/docs/graphql/authentication (official_docs)
  > Go to Settings → Machine users & agents and click Add machine user.
- `api_surface` https://www.plain.com/docs/graphql/authentication (official_docs)
  > Each API key carries its own fine-grained permissions.
- `api_surface` https://www.plain.com/blog/mcp-customer-support-2026 (official_other)
  > For Plain that's the GraphQL API
- `mcp` https://www.plain.com/docs/integrations/mcp-server (official_docs)
  > The Plain MCP server at `https://mcp.plain.com/mcp` lets tools like Claude, ChatGPT, and Cursor work with your support data directly.
- `mcp` https://help.plain.com/article/does-the-plain-mcp-server-support-api-key-authentication-h7d83qtfdzxo4h49x4oyklwx (official_docs)
  > The Plain Model Context Protocol (MCP) server at `https://mcp.plain.com/mcp` supports OAuth only.
- `verdict` https://www.plain.com/pricing (official_other)
  > Developer platform API, Webhooks, Events, MCP, Custom channels
- `verdict` https://www.plain.com/docs/graphql/oauth (official_docs)
- `one_liner` https://plain.com (official_other)
  > Plain is the agentic platform to orchestrate customer work.

- [ ] Confirmed against official docs
- [ ] Needs correction (notes: ___)

## 10. Sherlock (`sherlock`)

- **category:** data_seo_and_scraping
- **auth_methods / primary_auth:** [none] / `none`
- **access:** `self_serve`
- **api_types / has_public_docs:** [cli_only] / true
- **mcp_status / mcp_url:** `community` / `https://github.com/Burnsedia/sherlock-mcp`
- **verdict / blocker:** `buildable_now` / `none`
- **confidence:** `high`
- **needs_human:** false
- **notes:** Rendered pages confirm the sherlock-project OSINT CLI is a local tool with no hosted API and no auth. The only MCP is the community Burnsedia/sherlock-mcp wrapper; the sherlock-project README shows no official MCP. Checks scrape third-party sites, so their terms and rate limits may apply. sherlockproject.xyz was not fetched.

Evidence:

- `auth` https://github.com/sherlock-project/sherlock (official_other)
  > pipx install sherlock-project
  > Make requests over a proxy. e.g. socks5://127.0.0.1:1080
- `access` https://github.com/sherlock-project/sherlock (official_other)
  > MIT © Sherlock Project
  > docker run -it --rm sherlock/sherlock
- `api_surface` https://github.com/sherlock-project/sherlock (official_other)
  > Accounts found will be stored in an individual text file with the corresponding username
- `mcp` https://github.com/Burnsedia/sherlock-mcp (community)
  > FastMCP server integration for the Sherlock OSINT tool
  > Tool: search_username
- `verdict` https://github.com/sherlock-project/sherlock (official_other)
  > Hunt down social media accounts by username across 400+ social networks
  > Create Comma-Separated Values (CSV) File.

Screenshots: `screenshots/sherlock/01-eb8e266816.jpg`, `screenshots/sherlock/02-0fad000af5.jpg`

- [ ] Confirmed against official docs
- [ ] Needs correction (notes: ___)
