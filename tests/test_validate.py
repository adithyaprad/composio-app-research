import pytest
from conftest import IDENTITY

from agent.validate import validate_row


def test_valid_row_passes(row):
    assert validate_row(row, IDENTITY) == []


def test_missing_field_and_bad_enum(row):
    del row["one_liner"]
    row["access"] = "free"
    errs = validate_row(row, IDENTITY)
    assert any("'one_liner' is a required property" in e for e in errs)
    assert any(e.startswith("schema: access:") for e in errs)


def test_extra_property_rejected(row):
    row["snippets"] = {}
    assert any("Additional properties" in e for e in validate_row(row))


def test_bad_uri_format(row):
    row["docs_url"] = "not a url"
    assert any("docs_url" in e for e in validate_row(row))


def test_empty_required_evidence_fails(row):
    row["evidence"]["auth"] = []
    assert any("evidence/auth" in e for e in validate_row(row))


def test_blocker_none_iff_buildable_now(row):
    row["blocker"] = "gated_access"
    assert any(e.startswith("schema: blocker") for e in validate_row(row))


def test_docs_url_null_iff_no_docs(row):
    row["has_public_docs"] = False
    assert any(e.startswith("schema: docs_url") for e in validate_row(row))


def test_mcp_url_required_when_status_not_none(row):
    row["mcp_status"] = "official"
    assert any(e.startswith("schema: mcp_url") for e in validate_row(row))


def test_rule1_primary_auth_in_methods(row):
    row["auth_methods"] = ["api_key"]
    errs = validate_row(row)
    assert any(e.startswith("rule 1") for e in errs)


def test_oauth2_not_forced_as_primary(row):
    # e.g. OAuth exists but the API is mainly key-based: the preference is judged by prompt/rubric, not code
    row["primary_auth"] = "api_key"
    assert validate_row(row) == []


def test_unknown_auth_valid_alone(row):
    row.update(auth_methods=["unknown"], primary_auth="unknown", verdict="not_buildable", blocker="auth_undocumented",
               access="gated_sales")
    assert validate_row(row) == []


def test_unknown_auth_must_be_alone(row):
    row.update(auth_methods=["unknown", "api_key"], primary_auth="unknown")
    assert any(e.startswith("schema: auth_methods") for e in validate_row(row))


def test_unknown_primary_must_be_in_methods(row):
    row["primary_auth"] = "unknown"
    assert any(e.startswith("rule 1") for e in validate_row(row))


def test_rule7_no_public_api_pairing(row):
    row.update(access="no_public_api", verdict="buildable_with_approval", blocker="gated_access")
    errs = validate_row(row)
    assert any(e.startswith("rule 7") for e in errs)


def test_rule8_gated_cannot_be_buildable_now(row):
    row["access"] = "gated_partner"
    errs = validate_row(row)
    assert any(e.startswith("rule 8") for e in errs)
    assert any("buildable_now requires access" in e for e in errs)


def test_rule8_gated_pairing_valid(row):
    row.update(access="gated_sales", verdict="buildable_with_approval", blocker="gated_access")
    assert validate_row(row) == []


def test_identity_must_match_csv(row):
    row["name"] = "Hubspot Inc"
    assert any("rule identity: name" in e for e in validate_row(row, IDENTITY))


def test_composio_exists_requires_slug(row):
    row["composio_toolkit"]["toolkit_slug"] = None
    assert any("composio_toolkit" in e for e in validate_row(row))


# ---- sdk_only exclusivity ----------------------------------------------------------

@pytest.mark.parametrize("types", [["rest", "sdk_only"], ["graphql", "sdk_only"], ["soap", "sdk_only"]])
def test_sdk_only_with_raw_http_fails(row, types):
    row["api_types"] = types
    errs = validate_row(row, IDENTITY)
    assert any(e.startswith("rule api_types: sdk_only") and "drop sdk_only" in e for e in errs)


def test_sdk_only_alone_passes(row):
    row["api_types"] = ["sdk_only"]
    assert validate_row(row, IDENTITY) == []


def test_rest_and_graphql_pass(row):
    row["api_types"] = ["rest", "graphql"]
    assert validate_row(row, IDENTITY) == []


# ---- notes vs fields ---------------------------------------------------------------

# Paraphrased from pilot-10 rows whose notes contradicted auth_methods ["api_key", "oauth2"].
CONTRADICTING_OAUTH_NOTES = [
    "OAuth2 is documented for the MCP server only, not for the REST API, so it is not listed as an API auth "
    "method. A free trial is mentioned.",
    "Fixing auth_methods to api_key only is more accurate. A free trial is mentioned.",
    "API keys are created in Settings by a workspace user. MCP uses OAuth only for the MCP server.",
    "OAuth applies only to the dashboard login; API calls use keys.",
    "The OAuth flow is MCP-only.",
    "OAuth2 is not documented for the public API.",
    "We should drop oauth2 since it is for the hosted MCP.",
]


@pytest.mark.parametrize("notes", CONTRADICTING_OAUTH_NOTES)
def test_notes_contradict_oauth2_in_auth_methods(row, notes):
    row.update(auth_methods=["api_key", "oauth2"], primary_auth="api_key", notes=notes)
    errs = validate_row(row, IDENTITY)
    assert any(e.startswith("rule notes:") and "remove oauth2" in e for e in errs)


@pytest.mark.parametrize("notes", [
    "",
    "OAuth2 (authorization code with PKCE) is documented for third-party apps; API keys also work.",
    "Webhooks are available. The MCP server also uses OAuth, the same flow as the API.",
    "Rate limits are per workspace. SDKs exist for Node and Python.",
    "Public apps must use OAuth; private apps can use static access tokens only for their own account.",
])
def test_clean_notes_with_oauth2_pass(row, notes):
    row["notes"] = notes
    assert validate_row(row, IDENTITY) == []


def test_mcp_only_oauth_notes_with_api_key_only_pass(row):
    row.update(auth_methods=["api_key"], primary_auth="api_key",
               notes="OAuth2 is documented for the MCP server only, so it is not listed as an API auth method.")
    assert validate_row(row, IDENTITY) == []


def test_notes_no_official_mcp_contradicts_official_status(row):
    row.update(mcp_status="official", mcp_url="https://developers.hubspot.com/mcp",
               notes="No official MCP server; only a community one on GitHub.")
    assert any(e.startswith("rule notes:") and "mcp_status is official" in e for e in validate_row(row))
    row["mcp_status"] = "community"  # "no official" is consistent with community
    assert validate_row(row) == []
    row["notes"] = "No MCP server was found in the docs."
    assert any("mcp_status is community" in e for e in validate_row(row))
