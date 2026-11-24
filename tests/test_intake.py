import pytest

from codefoundry.intake import IntakeError, validate_migration_request


def valid_request(**overrides):
    base = {
        "schema_version": 1,
        "batch_id": "batch-001",
        "source": "NousResearch/hermes-agent",
        "destination_owner": "WatchCodes",
        "target_name": "nastech-agent",
        "sha": "a" * 40,
        "license": "MIT",
        "action": "create",
        "provenance": {
            "origin": "hermes",
            "discovered_in": "nastechresearch/hermes-agent",
            "manifest_batch_id": "batch-001",
        },
    }
    base.update(overrides)
    return base


def test_valid_request_passes():
    doc = validate_migration_request(valid_request())
    assert doc["source"] == "NousResearch/hermes-agent"
    assert doc["target_name"] == "nastech-agent"


def test_missing_required_field_fails_closed():
    req = valid_request()
    del req["sha"]
    with pytest.raises(IntakeError) as e:
        validate_migration_request(req)
    assert "sha" in str(e.value).lower() or "$.sha" in str(e.value)


def test_invalid_sha_fails():
    with pytest.raises(IntakeError):
        validate_migration_request(valid_request(sha="not-a-sha"))


def test_unknown_field_fails_closed():
    req = valid_request()
    req["surprise"] = True
    with pytest.raises(IntakeError):
        validate_migration_request(req)


def test_noassertion_license_accepted_for_create():
    # RepoAtlas cannot know the license at discovery time and emits
    # NOASSERTION; CodeFoundry detects and gates on the real license in scan.
    doc = validate_migration_request(valid_request(license="NOASSERTION"))
    assert doc["license"] == "NOASSERTION"


def test_null_license_accepted_for_create():
    doc = validate_migration_request(valid_request(license=None))
    assert doc["license"] is None


def test_create_forbids_subtree_path():
    req = valid_request()
    req["subtree_path"] = "sub/dir"
    with pytest.raises(IntakeError):
        validate_migration_request(req)


def test_import_subtree_requires_subtree_path():
    req = valid_request(action="import_subtree", license="MIT")
    req["subtree_path"] = "packages/foo"
    doc = validate_migration_request(req)
    assert doc["action"] == "import_subtree"


def test_import_subtree_missing_path_fails():
    req = valid_request(action="import_subtree", license="MIT")
    with pytest.raises(IntakeError):
        validate_migration_request(req)


def test_idempotency_key():
    from codefoundry.intake import idempotency_key

    assert idempotency_key(valid_request()) == "batch-001::NousResearch/hermes-agent"
