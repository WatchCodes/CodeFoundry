"""Build a MigrationReport conforming to the contract."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .contracts import validate

TERMINAL_STATUSES = {"VERIFIED", "FAILED", "REQUIRES_REVIEW"}
# Mirrors the canonical migration-report.schema.json stage enum for fast local
# validation. tests/test_report_contracts_hardening.py asserts exact parity with
# the raw on-disk schema so this mirror cannot drift away from the contract.
_ADDITIVE_CHECKPOINT_STAGES = {"start", "receipt", "ship", "license", "credits"}
CHECKPOINT_STAGES = {
    "validate",
    "scan",
    "clone",
    "create",
    "push",
    "brand",
    "gate",
    "verify",
    "report",
} | _ADDITIVE_CHECKPOINT_STAGES
REQUIRED_CHECK_KEYS = {"created", "named", "owned", "gated", "license_kept", "credits"}
REQUIRED_RECEIPT_KEYS = {
    "idempotency_key",
    "destination_repo_id",
    "source_sha",
    "destination_sha",
    "checkpoints",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_checkpoint(checkpoint: Any, *, index: int | None = None) -> dict[str, Any]:
    prefix = f"checkpoint[{index}]" if index is not None else "checkpoint"
    _require(isinstance(checkpoint, dict), f"{prefix} must be an object")
    stage = checkpoint.get("stage")
    ok = checkpoint.get("ok")
    at = checkpoint.get("at")
    _require(isinstance(stage, str) and stage in CHECKPOINT_STAGES, f"{prefix}.stage must be one of {sorted(CHECKPOINT_STAGES)}, got {stage!r}")
    _require(isinstance(ok, bool), f"{prefix}.ok must be bool, got {type(ok).__name__}")
    _require(isinstance(at, str) and at.strip(), f"{prefix}.at must be a non-empty ISO date-time string")
    if "detail" in checkpoint:
        detail = checkpoint["detail"]
        _require(isinstance(detail, str), f"{prefix}.detail must be str, got {type(detail).__name__}")
    return checkpoint


def validate_receipt(receipt: Any) -> dict[str, Any]:
    _require(isinstance(receipt, dict), f"receipt must be an object, got {type(receipt).__name__}")
    missing = REQUIRED_RECEIPT_KEYS - set(receipt.keys())
    _require(not missing, f"receipt missing required keys: {sorted(missing)}")
    _require(_is_non_empty_string(receipt.get("idempotency_key")), "receipt.idempotency_key must be a non-empty string")
    _require(isinstance(receipt.get("destination_repo_id"), int) and not isinstance(receipt.get("destination_repo_id"), bool), "receipt.destination_repo_id must be int")
    _require(_is_non_empty_string(receipt.get("source_sha")), "receipt.source_sha must be a non-empty string")
    _require(_is_non_empty_string(receipt.get("destination_sha")), "receipt.destination_sha must be a non-empty string")
    checkpoints = receipt.get("checkpoints")
    _require(isinstance(checkpoints, list), f"receipt.checkpoints must be a list, got {type(checkpoints).__name__}")
    for i, cp in enumerate(checkpoints):
        validate_checkpoint(cp, index=i)
    return receipt


def validate_checks(checks: Any) -> dict[str, Any]:
    _require(isinstance(checks, dict), f"checks must be an object, got {type(checks).__name__}")
    missing = REQUIRED_CHECK_KEYS - set(checks.keys())
    _require(not missing, f"checks missing required keys: {sorted(missing)}")
    for key in REQUIRED_CHECK_KEYS:
        value = checks.get(key)
        _require(isinstance(value, bool), f"checks.{key} must be bool, got {type(value).__name__}")
    return checks


def validate_license_info(license_info: Any) -> dict[str, Any]:
    _require(isinstance(license_info, dict), f"license_info must be an object, got {type(license_info).__name__}")
    for key in ("detected", "preserved"):
        _require(key in license_info, f"license_info missing required key: {key}")
    _require(isinstance(license_info.get("preserved"), bool), "license_info.preserved must be bool")
    if "review_required" in license_info:
        _require(isinstance(license_info.get("review_required"), bool), "license_info.review_required must be bool")
    return license_info


def validate_attribution(attribution: Any) -> dict[str, Any]:
    _require(isinstance(attribution, dict), f"attribution must be an object, got {type(attribution).__name__}")
    for key in ("notice", "credits"):
        _require(key in attribution, f"attribution missing required key: {key}")
        _require(isinstance(attribution.get(key), bool), f"attribution.{key} must be bool, got {type(attribution.get(key)).__name__}")
    return attribution


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
    _require(isinstance(status, str), f"status must be str, got {type(status).__name__}")
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"status must be terminal ({', '.join(sorted(TERMINAL_STATUSES))}), got {status!r}")
    _require(_is_non_empty_string(batch_id), "batch_id must be a non-empty string")
    _require(_is_non_empty_string(source), "source must be a non-empty string")
    _require(_is_non_empty_string(destination), "destination must be a non-empty string")
    _require(error is None or isinstance(error, str), f"error must be str or None, got {type(error).__name__}")
    _require(pr_url is None or isinstance(pr_url, str), f"pr_url must be str or None, got {type(pr_url).__name__}")

    verification_obj = {} if verification is None else verification
    _require(isinstance(verification_obj, dict), f"verification must be an object, got {type(verification_obj).__name__}")

    validate_checks(checks)
    validate_receipt(receipt)
    validate_license_info(license_info)
    validate_attribution(attribution)

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
        "verification": verification_obj,
        "pr_url": pr_url,
        "error": error,
    }
    validate("migration-report", report)
    return report


def make_checkpoint(stage: str, ok: bool, detail: str | None = None) -> dict[str, Any]:
    _require(isinstance(stage, str) and stage in CHECKPOINT_STAGES, f"stage must be one of {sorted(CHECKPOINT_STAGES)}, got {stage!r}")
    _require(isinstance(ok, bool), f"ok must be bool, got {type(ok).__name__}")
    if detail is not None:
        _require(isinstance(detail, str), f"detail must be str or None, got {type(detail).__name__}")
    cp: dict[str, Any] = {"stage": stage, "at": now_iso(), "ok": ok}
    if detail:
        cp["detail"] = detail
    return cp
