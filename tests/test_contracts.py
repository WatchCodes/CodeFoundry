from codefoundry.contracts import is_valid


def test_migration_request_schema_accepts_valid():
    doc = {
        "schema_version": 1,
        "batch_id": "b1",
        "source": "Owner/Repo",
        "destination_owner": "WatchCodes",
        "target_name": "nastech-repo",
        "sha": "a" * 40,
        "license": "MIT",
        "action": "create",
        "provenance": {
            "origin": "hermes",
            "discovered_in": "nastechresearch/hermes-agent",
            "manifest_batch_id": "b1",
        },
    }
    ok, errs = is_valid("migration-request", doc)
    assert ok, errs


def test_migration_report_requires_status():
    doc = {
        "schema_version": 1,
        "batch_id": "b1",
        "source": "Owner/Repo",
        "destination": "WatchCodes/nastech-repo",
        "sha": "a" * 40,
        "generated_at": "2026-11-22T00:00:00Z",
    }
    ok, errs = is_valid("migration-report", doc)
    assert not ok
