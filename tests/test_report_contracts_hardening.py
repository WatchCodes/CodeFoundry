import json

import pytest
from jsonschema.exceptions import ValidationError

from codefoundry.contracts import (
    format_errors,
    is_valid,
    load_schema,
    validate,
    validator_for,
)
from codefoundry.report import (
    CHECKPOINT_STAGES,
    TERMINAL_STATUSES,
    build_migration_report,
    make_checkpoint,
    validate_checkpoint,
    validate_receipt,
)


def valid_report_kwargs():
    return {
        "batch_id": "b-1",
        "source": "Owner/Repo",
        "destination": "WatchCodes/nastech-repo",
        "status": "VERIFIED",
        "checks": {
            "created": True,
            "named": True,
            "owned": True,
            "gated": True,
            "license_kept": True,
            "credits": True,
        },
        "receipt": {
            "idempotency_key": "b-1::Owner/Repo",
            "destination_repo_id": 123,
            "source_sha": "a" * 40,
            "destination_sha": "b" * 40,
            "checkpoints": [make_checkpoint("validate", True, "ok")],
        },
        "license_info": {"detected": "MIT", "preserved": True, "review_required": False},
        "attribution": {"notice": True, "credits": True},
        "verification": {"files_checked": 1, "branding_findings": 0, "integrity": True},
        "pr_url": None,
        "error": None,
    }


def test_build_migration_report_validates_schema_and_returns_report():
    report = build_migration_report(**valid_report_kwargs())
    assert report["schema_version"] == 1
    assert report["status"] == "VERIFIED"
    ok, errs = is_valid("migration-report", report)
    assert ok, errs


def test_build_migration_report_rejects_non_terminal_status():
    kwargs = valid_report_kwargs()
    kwargs["status"] = "CREATED"
    with pytest.raises(ValueError) as excinfo:
        build_migration_report(**kwargs)
    assert "terminal" in str(excinfo.value)


def test_build_migration_report_rejects_bad_status_type():
    kwargs = valid_report_kwargs()
    kwargs["status"] = None
    with pytest.raises(ValueError):
        build_migration_report(**kwargs)


def test_build_migration_report_rejects_missing_check_keys():
    kwargs = valid_report_kwargs()
    kwargs["checks"] = {"created": True}
    with pytest.raises(ValueError) as excinfo:
        build_migration_report(**kwargs)
    assert "checks missing required keys" in str(excinfo.value)


def test_build_migration_report_rejects_non_bool_check():
    kwargs = valid_report_kwargs()
    kwargs["checks"]["gated"] = "yes"
    with pytest.raises(ValueError) as excinfo:
        build_migration_report(**kwargs)
    assert "checks.gated must be bool" in str(excinfo.value)


def test_build_migration_report_rejects_missing_receipt_keys():
    kwargs = valid_report_kwargs()
    kwargs["receipt"] = {"idempotency_key": "b-1::Owner/Repo"}
    with pytest.raises(ValueError) as excinfo:
        build_migration_report(**kwargs)
    assert "receipt missing required keys" in str(excinfo.value)


def test_build_migration_report_rejects_bad_checkpoint_shape():
    kwargs = valid_report_kwargs()
    kwargs["receipt"]["checkpoints"] = [{"stage": "validate", "ok": True}]
    with pytest.raises(ValueError) as excinfo:
        build_migration_report(**kwargs)
    assert "at" in str(excinfo.value) or "checkpoint" in str(excinfo.value)


def test_make_checkpoint_rejects_unknown_stage():
    with pytest.raises(ValueError) as excinfo:
        make_checkpoint("unknown", True)
    assert "stage must be one of" in str(excinfo.value)


def test_make_checkpoint_rejects_non_bool_ok():
    with pytest.raises(ValueError) as excinfo:
        make_checkpoint("validate", 1)  # type: ignore[arg-type]
    assert "ok must be bool" in str(excinfo.value)


def test_make_checkpoint_rejects_non_string_detail():
    with pytest.raises(ValueError) as excinfo:
        make_checkpoint("validate", True, detail=123)  # type: ignore[arg-type]
    assert "detail must be str or None" in str(excinfo.value)


def test_validate_checkpoint_rejects_bad_shape():
    with pytest.raises(ValueError):
        validate_checkpoint({"stage": "validate", "at": "2026-11-22T00:00:00Z", "ok": "true"}, index=0)


def test_validate_receipt_rejects_non_list_checkpoints():
    receipt = {
        "idempotency_key": "b-1::Owner/Repo",
        "destination_repo_id": 1,
        "source_sha": "a" * 40,
        "destination_sha": "b" * 40,
        "checkpoints": "nope",
    }
    with pytest.raises(ValueError) as excinfo:
        validate_receipt(receipt)
    assert "checkpoints must be a list" in str(excinfo.value)


def test_contracts_validate_raises_clear_validation_error():
    bad = {
        "schema_version": 1,
        "batch_id": "b1",
        "source": "Owner/Repo",
        "destination": "WatchCodes/nastech-repo",
        "status": "VERIFIED",
        "checks": {
            "created": True,
            "named": True,
            "owned": True,
            "gated": True,
            "license_kept": True,
            "credits": True,
        },
        "receipt": {
            "idempotency_key": "b1::Owner/Repo",
            "destination_repo_id": 0,
            "source_sha": "a" * 40,
            "destination_sha": "b" * 40,
            "checkpoints": [],
        },
        "license": {"detected": "MIT", "preserved": True},
        "attribution": {"notice": True, "credits": True},
        "error": None,
        "unexpected": True,
    }
    with pytest.raises(ValidationError) as excinfo:
        validate("migration-report", bad)
    msg = str(excinfo.value)
    assert "migration-report" in msg
    assert "unexpected" in msg or "Additional properties" in msg


def test_contracts_is_valid_returns_clear_errors():
    ok, errs = is_valid("migration-report", {"schema_version": 1})
    assert not ok
    assert errs
    assert any("migration-report" in e for e in errs)


def test_contracts_is_valid_unknown_schema_is_robust():
    ok, errs = is_valid("nope-schema", {})
    assert not ok
    assert errs
    assert any("unknown schema" in e or "KeyError" in e for e in errs)


def test_contracts_load_schema_caches():
    a = load_schema("migration-report")
    b = load_schema("migration-report")
    assert a == b
    assert a is b


def test_contracts_validator_for_caches():
    a = validator_for("migration-report")
    b = validator_for("migration-report")
    assert a is b


def test_contracts_format_errors_is_stable():
    ok, errs = is_valid("migration-report", {})
    assert not ok
    # format_errors via is_valid should include schema prefix and locations
    assert all(e.startswith("migration-report$") or e.startswith("migration-report:") for e in errs)


def test_terminal_statuses_are_strict():
    assert TERMINAL_STATUSES == {"VERIFIED", "FAILED", "REQUIRES_REVIEW"}


def test_checkpoint_stages_match_schema_contract():
    assert "report" in CHECKPOINT_STAGES
    assert "validate" in CHECKPOINT_STAGES


def test_checkpoint_stage_source_of_truth_is_the_canonical_schema():
    # Read the raw on-disk schema, not load_schema(): an in-memory compat shim
    # must never mask a drift between the shared contract and emitted reports.
    from codefoundry.contracts import _schemas_dir

    raw = json.loads((_schemas_dir() / "migration-report.schema.json").read_text(encoding="utf-8"))
    enum = set(
        raw["properties"]["receipt"]["properties"]["checkpoints"]["items"]["properties"]["stage"]["enum"]
    )
    # Stages emitted by codefoundry.pipeline.run_slice must be declared in the
    # shared schema, otherwise downstream consumers reject real reports.
    emitted = {
        "start", "receipt", "validate", "scan", "license", "clone",
        "brand", "credits", "gate", "ship", "report",
    }
    assert emitted <= enum
    assert CHECKPOINT_STAGES == enum


def test_unknown_checkpoint_stage_is_still_rejected():
    report = build_migration_report(**valid_report_kwargs())
    bad = {"stage": "not_a_real_stage", "at": "2026-11-24T00:00:00Z", "ok": True}
    report["receipt"]["checkpoints"] = [bad]
    ok, errors = is_valid("migration-report", report)
    assert ok is False
    assert any("not_a_real_stage" in e for e in errors)
    with pytest.raises(ValueError, match="not_a_real_stage"):
        validate_checkpoint(bad)
