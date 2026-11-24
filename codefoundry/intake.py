"""Intake: validate and normalize a MigrationRequest. Fail-closed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import is_valid


class IntakeError(ValueError):
    pass


def idempotency_key(request: dict[str, Any]) -> str:
    batch = request.get("batch_id", "")
    source = request.get("source", "")
    return f"{batch}::{source}"


def validate_migration_request(request: dict[str, Any]) -> dict[str, Any]:
    ok, errors = is_valid("migration-request", request)
    if not ok:
        raise IntakeError("MigrationRequest schema validation failed:\n" + "\n".join(errors))

    action = request["action"]

    # The request license is advisory: RepoAtlas cannot detect it at discovery
    # time and emits NOASSERTION. The authoritative gate is the scan stage,
    # which detects the real license from the cloned repo (missing/unknown ->
    # FAILED, copyleft -> REQUIRES_REVIEW). Rejecting unknown values here
    # blocked every request without adding safety.
    if action == "create":
        if request.get("subtree_path"):
            raise IntakeError("create action must not include subtree_path")
    elif action == "import_subtree":
        if not request.get("subtree_path"):
            raise IntakeError("import_subtree action requires subtree_path")
    return dict(request)
