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


def test_oauth2_preferred(row):
    row["primary_auth"] = "api_key"
    assert any("oauth2 is documented" in e for e in validate_row(row))


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
