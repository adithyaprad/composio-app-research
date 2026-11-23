# composio-app-research

Research whether 100 apps can become agent toolkits: auth, how a developer gets access, API surface, existing MCP, and a buildability verdict. Then cluster the patterns and show how accuracy was verified.

This is a take-home style research pipeline, not a production product.

## Repo layout

- `data/apps.csv` — the 100-app research set (id, slug, category, starting hint/URL)
- `schema/` — what one researched row looks like, how fields are defined, and how accuracy is scored
  - `app_row.schema.json` — machine-readable row shape
  - `README.md` — field and enum definitions
  - `RUBRIC.md` — scoring rules and gold-sample procedure
  - `gold_sample.md` — 20 apps reserved for a human accuracy check (answers not filled yet)
- `.agents/skills/composio/` — Composio skill used when building the research agent

## Status

Phase 1 is done: input list, schema, and rubric. The research agent, verification loop, pattern write-up, and case-study page come next.

## How to run the agent

Not available yet. This README will get a short run guide once the agent exists.

Until then, expected setup will be: copy `.env.example`, set an LLM provider (`bedrock` by default, or an API key for Gemini / Anthropic / OpenAI) plus a `COMPOSIO_API_KEY` if the agent uses Composio tools, then run a single command that writes JSON rows matching `schema/app_row.schema.json`.
