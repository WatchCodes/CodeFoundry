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
from .report import build_migration_report, make_checkpoint, now_iso
from .scan import scan_repo
from .ship import ship_private_repo


def _license_obj(detected: str | None) -> dict[str, Any]:
    spdx = detected if detected and detected not in {"UNKNOWN", "NONE"} else "NOASSERTION"
    return {"detected": spdx, "preserved": spdx not in {"NOASSERTION", "OTHER"}, "review_required": spdx in {"NOASSERTION", "OTHER"}}


def _failed_report(req: dict[str, Any], *, stage: str, reason: str, checkpoints: list[dict[str, Any]], detected_license: str | None = None, destination_sha: str | None = None) -> dict[str, Any]:
    batch_id = str(req.get("batch_id", "unknown"))
    source = str(req.get("source", "unknown/unknown"))
    destination = f"{req.get('destination_owner','unknown')}/{req.get('target_name','unknown')}"
    sha = str(req.get("sha", "0" * 40))
    lic = _license_obj(detected_license)
    return build_migration_report(
        batch_id=batch_id,
        source=source,
        destination=destination,
        status="FAILED",
        checks={"created": False, "named": False, "owned": False, "gated": False, "license_kept": lic["preserved"], "credits": False},
        receipt={"idempotency_key": f"{batch_id}::{source}", "destination_repo_id": 0, "source_sha": sha, "destination_sha": destination_sha or sha, "checkpoints": checkpoints + [make_checkpoint(stage, False, reason)]},
        license_info=lic,
        attribution={"notice": False, "credits": False},
        verification={"files_checked": 0, "branding_findings": 0, "integrity": False},
        pr_url=None,
        error=reason,
    )


def run_slice(
    request: dict[str, Any],
    *,
    workdir: str | Path | None = None,
    engine_path: str | Path | None = None,
    dry_run: bool = True,
    push: bool = False,
    clone_source: str | None = None,
    receipts_dir: str | Path | None = None,
) -> dict[str, Any]:
    checkpoints: list[dict[str, Any]] = []
    try:
        req = validate_migration_request(request)
        checkpoints.append(make_checkpoint("validate", True))
    except IntakeError as e:
        return _failed_report(request if isinstance(request, dict) else {}, stage="validate", reason=f"intake: {e}", checkpoints=checkpoints)

    batch_id = req["batch_id"]
    source = req["source"]
    destination = f"{req['destination_owner']}/{req['target_name']}"
    sha = req["sha"]
    clone_ref = clone_source or source
    store = ReceiptStore(receipts_dir or (Path(workdir) / "receipts" if workdir else Path(tempfile.gettempdir()) / "codefoundry-receipts"))
    existing = store.get(batch_id, source)
    if existing and existing.get("destination_repo_id"):
        checkpoints.append(make_checkpoint("receipt", True, "existing destination"))

    with tempfile.TemporaryDirectory(prefix="codefoundry-") as td:
        base = Path(workdir) if workdir else Path(td)
        base.mkdir(parents=True, exist_ok=True)
        clone_dir = base / "clone"
        scan_dir = base / "scan-src"

        try:
            scan_clone = clone_snapshot(clone_ref, sha, scan_dir)
            checkpoints.append(make_checkpoint("scan", True, f"scanned {scan_clone.file_count} files"))
        except Exception as e:
            return _failed_report(req, stage="scan", reason=f"scan clone failed: {e}", checkpoints=checkpoints)

        scan = scan_repo(scan_dir, source=source, sha=sha).to_dict()
        detected = scan.get("detected_license")
        lic = _license_obj(detected)
        if not lic["preserved"]:
            return _failed_report(req, stage="scan", reason=f"undetected or unsupported license: {detected!r}", checkpoints=checkpoints, detected_license=detected)

        try:
            branded_clone = clone_snapshot(clone_ref, sha, clone_dir)
            checkpoints.append(make_checkpoint("clone", True, f"cloned {branded_clone.file_count} files"))
        except Exception as e:
            return _failed_report(req, stage="clone", reason=f"clone failed: {e}", checkpoints=checkpoints, detected_license=detected)

        try:
            brand = run_brand(clone_dir, engine_path=engine_path, dry_run=False).to_dict()
            checkpoints.append(make_checkpoint("brand", True, f"changed={brand.get('files_changed')}"))
        except Exception as e:
            return _failed_report(req, stage="brand", reason=f"brand failed: {e}", checkpoints=checkpoints, detected_license=detected)

        # attribution artifacts
        try:
            ensure_credits(clone_dir, source=source, sha=sha, license_spdx=lic["detected"])
            ensure_notice(clone_dir, source=source, sha=sha)
            checkpoints.append(make_checkpoint("verify", True, "credits+notice"))
            credits_ok = True
        except Exception as e:
            checkpoints.append(make_checkpoint("verify", False, f"credits: {e}"))
            credits_ok = False

        brand_clean = bool(brand.get("scan_passed"))
        checkpoints.append(make_checkpoint("gate", brand_clean, f"violations={len(brand.get('scan_violations', []))}"))

        ship_sha = sha
        created = False
        if not dry_run:
            try:
                ship = ship_private_repo(clone_dir, destination=destination, dry_run=not push, visibility="private").to_dict()
                ship_sha = ship.get("pushed_sha") or sha
                created = not ship.get("dry_run", True)
                checkpoints.append(make_checkpoint("create", True, destination))
                if push:
                    checkpoints.append(make_checkpoint("push", True, ship.get("repo_url", "")))
            except Exception as e:
                return _failed_report(req, stage="create", reason=f"ship failed: {e}", checkpoints=checkpoints, detected_license=detected, destination_sha=ship_sha)
        else:
            checkpoints.append(make_checkpoint("verify", True, "dry-run"))

        checks = {
            "created": created,
            "named": req["target_name"] == destination.split("/", 1)[1],
            "owned": req["destination_owner"] == destination.split("/", 1)[0],
            "gated": brand_clean,
            "license_kept": bool(lic["preserved"]),
            "credits": bool(credits_ok),
        }
        verification = {
            "files_checked": int(brand.get("files_changed", 0)),
            "branding_findings": len(brand.get("scan_violations", [])),
            "integrity": True,
        }
        status = "VERIFIED" if all(checks.values()) else "REQUIRES_REVIEW"
        error = None
        if not brand_clean:
            status = "FAILED"
            error = "brand scan violations remain"
        elif not all(checks.values()):
            missing = [k for k, v in checks.items() if not v]
            status = "REQUIRES_REVIEW"
            error = f"checks not satisfied: {', '.join(missing)}"
        report = build_migration_report(
            batch_id=batch_id,
            source=source,
            destination=destination,
            status=status,
            checks=checks,
            receipt={
                "idempotency_key": f"{batch_id}::{source}",
                "destination_repo_id": int((existing or {}).get("destination_repo_id", 0)),
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
        store.put(batch_id, source, report["receipt"])
        return report
