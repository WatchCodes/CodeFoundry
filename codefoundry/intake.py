"""Intake: validate and normalize a MigrationRequest. Fail-closed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import is_valid


class IntakeError(ValueError):
    pass


_CREATE_LICENSES_REJECTED = {None, "", "NOASSERTION", "NONE", "UNKNOWN"}


def idempotency_key(request: dict[str, Any]) -> str:
    batch = request.get("batch_id", "")
    source = request.get("source", "")
    return f"{batch}::{source}"


def validate_migration_request(request: dict[str, Any]) -> dict[str, Any]:
    ok, errors = is_valid("migration-request", request)
    if not ok:
        raise IntakeError("MigrationRequest schema validation failed:\n" + "\n".join(errors))

    action = request["action"]
    license_value = request.get("license")

    if action == "create":
        if license_value in _CREATE_LICENSES_REJECTED:
            raise IntakeError(
                f"create action requires a detectable license, got {license_value!r}"
            )
        if request.get("subtree_path"):
            raise IntakeError("create action must not include subtree_path")
    elif action == "import_subtree":
        if not request.get("subtree_path"):
            raise IntakeError("import_subtree action requires subtree_path")
        if license_value in _CREATE_LICENSES_REJECTED:
            raise IntakeError(
                f"import_subtree action requires a detectable license, got {license_value!r}"
            )
    return dict(request)
