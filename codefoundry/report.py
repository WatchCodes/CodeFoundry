"""Build a MigrationReport conforming to the contract."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .contracts import validate

TERMINAL_STATUSES = {"VERIFIED", "FAILED", "REQUIRES_REVIEW"}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_migration_report(
    *,
    batch_id: str,
    source: str,
    destination: str,
    status: str,
    checks: dict[str, bool],
    receipt: dict[str, Any],
    license_info: dict[str, Any],
    attribution: dict[str, bool],
    verification: dict[str, Any] | None = None,
    pr_url: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"status must be terminal, got {status}")
    report: dict[str, Any] = {
        "schema_version": 1,
        "batch_id": batch_id,
        "source": source,
        "destination": destination,
        "status": status,
        "checks": checks,
        "receipt": receipt,
        "license": license_info,
        "attribution": attribution,
        "verification": verification or {},
        "pr_url": pr_url,
        "error": error,
    }
    validate("migration-report", report)
    return report


def make_checkpoint(stage: str, ok: bool, detail: str | None = None) -> dict[str, Any]:
    cp: dict[str, Any] = {"stage": stage, "at": now_iso(), "ok": bool(ok)}
    if detail:
        cp["detail"] = detail
    return cp
