"""Hand-driven vertical slice: MigrationRequest -> MigrationReport.

Phases: intake -> scan -> clone -> brand -> verify -> report.
Dry-run mode performs no external writes and no destination repo creation.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from .brand import run_brand
from .clone import clone_snapshot
from .credits import ensure_credits, ensure_notice
from .intake import IntakeError, validate_migration_request
from .receipts import ReceiptStore
from .report import build_migration_report, make_checkpoint
from .reports import write_report
from .scan import scan_repo
from .ship import ship_private_repo

TERMINAL_STATUSES = {"VERIFIED", "FAILED", "REQUIRES_REVIEW"}


def _license_obj(detected: str | None) -> dict[str, Any]:
    spdx = detected if detected and detected not in {"UNKNOWN", "NONE"} else "NOASSERTION"
    return {"detected": spdx, "preserved": spdx not in {"NOASSERTION", "OTHER"}, "review_required": spdx in {"NOASSERTION", "OTHER"}}


def _safe_sha(value: Any) -> str:
    s = str(value) if value is not None else ""
    if len(s) == 40 and all(c in "0123456789abcdef" for c in s.lower()):
        return s.lower()
    return "0" * 40


def _error_detail(stage: str, exc: Exception | str, *, context: dict[str, Any] | None = None) -> str:
    if isinstance(exc, Exception):
        msg = f"{stage}: {type(exc).__name__}: {exc}"
    else:
        msg = f"{stage}: {exc}"
    if context:
        ctx = ", ".join(f"{k}={v!r}" for k, v in sorted(context.items()) if v is not None)
        if ctx:
            msg = f"{msg} [{ctx}]"
    return msg


def _receipt_payload(report: dict[str, Any], *, dry_run: bool, push: bool) -> dict[str, Any]:
    receipt = report.get("receipt", {})
    payload: dict[str, Any] = {}
    if isinstance(receipt, dict):
        payload.update(receipt)
    payload.update(
        {
            "_format": "cf-receipt-v2",
            "status": report.get("status"),
            "error": report.get("error"),
            "dry_run": bool(dry_run),
            "push": bool(push),
            "receipt": receipt if isinstance(receipt, dict) else {},
            "report": report,
        }
    )
    return payload


def _unwrap_receipt(obj: dict[str, Any] | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    if not obj:
        return None, None, {}
    if obj.get("_format") == "cf-receipt-v2" and isinstance(obj.get("receipt"), dict):
        report = obj.get("report") if isinstance(obj.get("report"), dict) else None
        meta = {k: obj.get(k) for k in ("status", "error", "dry_run", "push", "_format") if k in obj}
        return obj["receipt"], report, meta
    # backward compat: raw receipt dict
    if "idempotency_key" in obj or "destination_repo_id" in obj:
        return obj, None, {}
    if isinstance(obj.get("receipt"), dict):
        return obj.get("receipt"), obj.get("report") if isinstance(obj.get("report"), dict) else None, {}
    return obj, None, {}


def _failed_report(req: dict[str, Any], *, stage: str, reason: str, checkpoints: list[dict[str, Any]], detected_license: str | None = None, destination_sha: str | None = None) -> dict[str, Any]:
    batch_id = str(req.get("batch_id", "unknown"))
    source = str(req.get("source", "unknown/unknown"))
    destination = f"{req.get('destination_owner','unknown')}/{req.get('target_name','unknown')}"
    sha = _safe_sha(req.get("sha"))
    dest_sha = _safe_sha(destination_sha) if destination_sha else sha
    lic = _license_obj(detected_license)
    cp = list(checkpoints)
    cp.append(make_checkpoint(stage, False, reason))
    return build_migration_report(
        batch_id=batch_id,
        source=source,
        destination=destination,
        status="FAILED",
        checks={"created": False, "named": False, "owned": False, "gated": False, "license_kept": lic["preserved"], "credits": False},
        receipt={"idempotency_key": f"{batch_id}::{source}", "destination_repo_id": 0, "source_sha": sha, "destination_sha": dest_sha, "checkpoints": cp},
        license_info=lic,
        attribution={"notice": False, "credits": False},
        verification={"files_checked": 0, "branding_findings": 0, "integrity": False},
        pr_url=None,
        error=reason,
    )


def _persist(report: dict[str, Any], *, reports_dir: str | Path | None, store: ReceiptStore | None, batch_id: str, source: str, dry_run: bool, push: bool) -> dict[str, Any]:
    if store is not None:
        store.put(batch_id, source, _receipt_payload(report, dry_run=dry_run, push=push))
    if reports_dir:
        write_report(report, reports_dir)
    return report


def run_slice(
    request: dict[str, Any],
    *,
    workdir: str | Path | None = None,
    engine_path: str | Path | None = None,
    dry_run: bool = True,
    push: bool = False,
    clone_source: str | None = None,
    receipts_dir: str | Path | None = None,
    reports_dir: str | Path | None = None,
) -> dict[str, Any]:
    checkpoints: list[dict[str, Any]] = []
    req_raw: dict[str, Any] = request if isinstance(request, dict) else {}
    store = ReceiptStore(receipts_dir) if receipts_dir is not None else None

    def _emit(report: dict[str, Any]) -> dict[str, Any]:
        bid = str(report.get("batch_id", req_raw.get("batch_id", "unknown")))
        src = str(report.get("source", req_raw.get("source", "unknown/unknown")))
        return _persist(report, reports_dir=reports_dir, store=store, batch_id=bid, source=src, dry_run=dry_run, push=push)

    checkpoints.append(make_checkpoint("start", True, "run_slice"))

    try:
        req = validate_migration_request(request)
        checkpoints.append(make_checkpoint("validate", True, "request valid"))
    except IntakeError as e:
        reason = _error_detail("intake", e, context={"batch_id": req_raw.get("batch_id"), "source": req_raw.get("source")})
        return _emit(_failed_report(req_raw, stage="validate", reason=reason, checkpoints=checkpoints))
    except Exception as e:
        reason = _error_detail("intake", e, context={"batch_id": req_raw.get("batch_id"), "source": req_raw.get("source")})
        return _emit(_failed_report(req_raw, stage="validate", reason=reason, checkpoints=checkpoints))

    batch_id = req["batch_id"]
    source = req["source"]
    destination = f"{req['destination_owner']}/{req['target_name']}"
    sha = req["sha"]
    clone_ref = clone_source or source

    existing_receipt: dict[str, Any] | None = None
    existing_report: dict[str, Any] | None = None
    existing_meta: dict[str, Any] = {}
    if store is not None:
        existing_receipt, existing_report, existing_meta = _unwrap_receipt(store.get(batch_id, source))
        if existing_receipt:
            checkpoints.append(make_checkpoint("receipt", True, "existing receipt loaded"))
            prior_dry = existing_meta.get("dry_run")
            compatible_mode = prior_dry is None or bool(prior_dry) == bool(dry_run)
            if compatible_mode and isinstance(existing_report, dict) and existing_report.get("status") in TERMINAL_STATUSES:
                # Idempotent/resumable: same logical request -> return prior terminal report.
                # Ensure explicit report persistence if requested.
                return _emit(existing_report)
        else:
            checkpoints.append(make_checkpoint("receipt", True, "no existing receipt"))
    else:
        checkpoints.append(make_checkpoint("receipt", True, "receipt persistence disabled"))

    with tempfile.TemporaryDirectory(prefix="codefoundry-") as td:
        base = Path(workdir) if workdir else Path(td)
        base.mkdir(parents=True, exist_ok=True)
        clone_dir = base / "clone"
        scan_dir = base / "scan-src"

        try:
            scan_clone = clone_snapshot(clone_ref, sha, scan_dir)
            checkpoints.append(make_checkpoint("scan", True, f"scanned {scan_clone.file_count} files"))
        except Exception as e:
            reason = _error_detail("scan", e, context={"source": clone_ref, "sha": sha})
            return _emit(_failed_report(req, stage="scan", reason=reason, checkpoints=checkpoints))

        try:
            scan = scan_repo(scan_dir, source=source, sha=sha).to_dict()
        except Exception as e:
            reason = _error_detail("scan", e, context={"source": source, "sha": sha})
            return _emit(_failed_report(req, stage="scan", reason=reason, checkpoints=checkpoints))

        detected = scan.get("detected_license")
        lic = _license_obj(detected)
        checkpoints.append(make_checkpoint("license", bool(lic["preserved"]), f"detected={detected!r} spdx={lic['detected']!r}"))
        if not lic["preserved"]:
            reason = _error_detail("scan", f"undetected or unsupported license: {detected!r}", context={"source": source, "sha": sha})
            return _emit(_failed_report(req, stage="scan", reason=reason, checkpoints=checkpoints, detected_license=detected))

        try:
            branded_clone = clone_snapshot(clone_ref, sha, clone_dir)
            checkpoints.append(make_checkpoint("clone", True, f"cloned {branded_clone.file_count} files"))
        except Exception as e:
            reason = _error_detail("clone", e, context={"source": clone_ref, "sha": sha})
            return _emit(_failed_report(req, stage="clone", reason=reason, checkpoints=checkpoints, detected_license=detected))

        try:
            brand = run_brand(clone_dir, engine_path=engine_path, dry_run=False).to_dict()
            checkpoints.append(make_checkpoint("brand", True, f"changed={brand.get('files_changed')} replacements={brand.get('replacements')}"))
        except Exception as e:
            reason = _error_detail("brand", e, context={"source": source, "sha": sha})
            return _emit(_failed_report(req, stage="brand", reason=reason, checkpoints=checkpoints, detected_license=detected))

        credits_ok = False
        try:
            ensure_credits(clone_dir, source=source, sha=sha, license_spdx=lic["detected"])
            ensure_notice(clone_dir, source=source, sha=sha)
            credits_ok = True
            checkpoints.append(make_checkpoint("credits", True, "credits and notice ensured"))
        except Exception as e:
            reason = _error_detail("credits", e, context={"source": source, "sha": sha})
            checkpoints.append(make_checkpoint("credits", False, reason))
            credits_ok = False

        brand_clean = bool(brand.get("scan_passed"))
        violations = brand.get("scan_violations", [])
        checkpoints.append(make_checkpoint("gate", brand_clean, f"violations={len(violations) if isinstance(violations, list) else 'unknown'}"))
        if not brand_clean:
            reason = _error_detail("gate", "brand scan violations remain", context={"violations": len(violations) if isinstance(violations, list) else None})
            return _emit(_failed_report(req, stage="gate", reason=reason, checkpoints=checkpoints, detected_license=detected, destination_sha=sha))

        ship_sha = sha
        created = False
        if not dry_run:
            try:
                ship = ship_private_repo(clone_dir, destination=destination, dry_run=not push, visibility="private").to_dict()
                ship_sha = ship.get("pushed_sha") or sha
                created = not bool(ship.get("dry_run", True))
                checkpoints.append(make_checkpoint("ship", True, f"destination={destination} dry_run={bool(ship.get('dry_run', True))}"))
                if push:
                    checkpoints.append(make_checkpoint("push", True, ship.get("repo_url", "")))
            except Exception as e:
                reason = _error_detail("ship", e, context={"destination": destination, "push": push})
                return _emit(_failed_report(req, stage="ship", reason=reason, checkpoints=checkpoints, detected_license=detected, destination_sha=ship_sha))
        else:
            checkpoints.append(make_checkpoint("ship", True, "dry-run: no external write"))

        checks = {
            "created": created,
            "named": req["target_name"] == destination.split("/", 1)[1],
            "owned": req["destination_owner"] == destination.split("/", 1)[0],
            "gated": brand_clean,
            "license_kept": bool(lic["preserved"]),
            "credits": bool(credits_ok),
        }
        verification = {
            "files_checked": int(brand.get("files_changed", 0) or 0),
            "branding_findings": len(violations) if isinstance(violations, list) else 0,
            "integrity": True,
        }
        status = "VERIFIED" if all(checks.values()) else "REQUIRES_REVIEW"
        error = None
        if not all(checks.values()):
            missing = [k for k, v in checks.items() if not v]
            status = "REQUIRES_REVIEW"
            error = _error_detail("verify", f"checks not satisfied: {', '.join(missing)}", context={"missing": missing})

        checkpoints.append(make_checkpoint("report", True, f"status={status}"))
        report = build_migration_report(
            batch_id=batch_id,
            source=source,
            destination=destination,
            status=status,
            checks=checks,
            receipt={
                "idempotency_key": f"{batch_id}::{source}",
                "destination_repo_id": int(((existing_receipt or {}).get("destination_repo_id", 0)) or 0),
                "source_sha": sha,
                "destination_sha": ship_sha,
                "checkpoints": checkpoints,
            },
            license_info=lic,
            attribution={"notice": bool(credits_ok), "credits": bool(credits_ok)},
            verification=verification,
            pr_url=None,
            error=error,
        )
        return _emit(report)
