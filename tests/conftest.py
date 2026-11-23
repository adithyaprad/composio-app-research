import copy

import pytest

DOCS = "https://developers.hubspot.com/docs/api/overview"

VALID_ROW = {
    "id": 2,
    "slug": "hubspot",
    "name": "HubSpot",
    "category": "crm_and_sales",
    "one_liner": "CRM platform with marketing, sales, and service hubs.",
    "auth_methods": ["oauth2", "api_key"],
    "primary_auth": "oauth2",
    "access": "self_serve",
    "api_types": ["rest"],
    "has_public_docs": True,
    "api_breadth": "broad",
    "docs_url": DOCS,
    "mcp_status": "none",
    "mcp_url": None,
    "verdict": "buildable_now",
    "blocker": "none",
    "evidence": {k: [{"url": DOCS, "source_type": "official_docs"}]
                 for k in ("auth", "access", "api_surface", "mcp", "verdict")},
    "confidence": "high",
    "requires_end_user_admin": False,
    "composio_toolkit": {
        "status": "exists",
        "toolkit_slug": "hubspot",
        "sibling_slugs": [],
        "checked_at": "2026-11-23T10:41:38Z",
        "evidence_url": "https://docs.composio.dev/toolkits/hubspot",
    },
    "notes": "",
}

IDENTITY = {"id": 2, "slug": "hubspot", "name": "HubSpot", "category": "crm_and_sales"}


@pytest.fixture
def row():
    return copy.deepcopy(VALID_ROW)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Tests never see the developer's real keys or .env."""
    for k in ("LLM_PROVIDER", "LLM_MODEL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
              "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_PROFILE", "COMPOSIO_API_KEY",
              "SEARCH_PROVIDER", "FETCH_PROVIDER", "ENV_FILE", "EXA_API_KEY", "EXA_BUDGET_USD",
              "USE_COMPOSIO_EXA"):
        monkeypatch.delenv(k, raising=False)
