import os
import json
import subprocess
from pathlib import Path

import pytest

import codefoundry.pipeline as pipeline
from codefoundry.pipeline import run_slice


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_repo(tmp: Path, *, name: str = "src", files: dict[str, str] | None = None) -> tuple[str, str]:
    src = tmp / name
    src.mkdir(parents=True, exist_ok=True)
    for p, content in (files or {}).items():
        fp = src / p
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
    _git(src, "init")
    _git(src, "config", "user.name", "t")
    _git(src, "config", "user.email", "t@t")
    _git(src, "add", "-A")
    _git(src, "commit", "-m", "init")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, check=True, capture_output=True, text=True).stdout.strip()
    return str(src), sha


def base_req(sha: str, **overrides):
    r = {
        "schema_version": 1,
        "batch_id": "b-hard",
        "source": "Test/Repo",
        "destination_owner": "WatchCodes",
        "target_name": "nastech-test",
        "sha": sha,
        "license": "MIT",
        "action": "create",
        "provenance": {
            "origin": "allowlisted",
            "discovered_in": "test/repo",
            "manifest_batch_id": "b-hard",
        },
    }
    r.update(overrides)
    return r


ENGINE = os.environ.get("CODEFOUNDRY_100WAYS_PATH", "/home/nascode/work/100ways")
GOOD_FILES = {"README.md": "Hello\n", "LICENSE": "MIT License\n\nPermission is hereby granted...\n"}


def _run_ok(tmp_path: Path, **kwargs):
    src, sha = make_repo(tmp_path, name=kwargs.pop("name", "src"), files=kwargs.pop("files", GOOD_FILES))
    req = base_req(sha, **kwargs.pop("req_overrides", {}))
    return run_slice(req, dry_run=kwargs.pop("dry_run", True), engine_path=ENGINE, clone_source=src, **kwargs), src, sha


def test_explicit_writes_only_no_dirs(tmp_path: Path):
    src, sha = make_repo(tmp_path, name="s1", files=GOOD_FILES)
    before = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()}
    report = run_slice(base_req(sha), dry_run=True, engine_path=ENGINE, clone_source=src)
    after = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()}
    new_files = after - before
    assert report["status"] in {"VERIFIED", "FAILED", "REQUIRES_REVIEW"}
    assert new_files == set()


def test_explicit_writes_only_reports_and_receipts(tmp_path: Path):
    src, sha = make_repo(tmp_path, name="s2", files=GOOD_FILES)
    reports = tmp_path / "reports"
    receipts = tmp_path / "receipts"
    before = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()}
    run_slice(base_req(sha), dry_run=True, engine_path=ENGINE, clone_source=src, reports_dir=reports, receipts_dir=receipts)
    after = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()}
    new_files = after - before
    assert new_files
    assert all(f.startswith("reports/") or f.startswith("receipts/") or f.startswith("s2/") for f in new_files)


def test_receipt_recorded_on_success(tmp_path: Path):
    src, sha = make_repo(tmp_path, name="s3", files=GOOD_FILES)
    receipts = tmp_path / "receipts"
    report = run_slice(base_req(sha), dry_run=True, engine_path=ENGINE, clone_source=src, receipts_dir=receipts)
    files = list(receipts.glob("*.json"))
    assert len(files) == 1
    stored = json.loads(files[0].read_text(encoding="utf-8"))
    # v2 wrapper or raw receipt; either way receipt info present
    receipt = stored.get("receipt", stored)
    assert receipt.get("idempotency_key") == f"{report['batch_id']}::{report['source']}"
    assert stored.get("status", report["status"]) == report["status"]


def test_receipt_recorded_on_failure(tmp_path: Path):
    receipts = tmp_path / "receipts"
    report = run_slice({"schema_version": 1, "batch_id": "b-bad"}, dry_run=True, receipts_dir=receipts)
    assert report["status"] == "FAILED"
    files = list(receipts.glob("*.json"))
    assert len(files) == 1
    stored = json.loads(files[0].read_text(encoding="utf-8"))
    assert stored.get("status") == "FAILED" or stored.get("receipt")


def test_no_duplicate_reports_same_logical_run(tmp_path: Path):
    src, sha = make_repo(tmp_path, name="s4", files=GOOD_FILES)
    reports = tmp_path / "reports"
    receipts = tmp_path / "receipts"
    r1 = run_slice(base_req(sha), dry_run=True, engine_path=ENGINE, clone_source=src, reports_dir=reports, receipts_dir=receipts)
    r2 = run_slice(base_req(sha), dry_run=True, engine_path=ENGINE, clone_source=src, reports_dir=reports, receipts_dir=receipts)
    assert r1["status"] == r2["status"]
    assert len(list(reports.glob("*.json"))) == 1


def test_idempotent_resume_returns_terminal_report(tmp_path: Path):
    src, sha = make_repo(tmp_path, name="s5", files=GOOD_FILES)
    receipts = tmp_path / "receipts"
    reports = tmp_path / "reports"
    r1 = run_slice(base_req(sha), dry_run=True, engine_path=ENGINE, clone_source=src, reports_dir=reports, receipts_dir=receipts)
    r2 = run_slice(base_req(sha), dry_run=True, engine_path=ENGINE, clone_source=src, reports_dir=reports, receipts_dir=receipts)
    assert r2["status"] == r1["status"]
    assert r2["receipt"]["idempotency_key"] == r1["receipt"]["idempotency_key"]
    assert r2["receipt"]["source_sha"] == r1["receipt"]["source_sha"]
    assert len(list(reports.glob("*.json"))) == 1


def test_checkpoints_cover_stages(tmp_path: Path):
    src, sha = make_repo(tmp_path, name="s6", files=GOOD_FILES)
    report = run_slice(base_req(sha), dry_run=True, engine_path=ENGINE, clone_source=src)
    stages = [cp.get("stage") for cp in report["receipt"]["checkpoints"]]
    for required in ["start", "validate", "scan", "license", "clone", "brand", "credits", "gate", "ship", "report"]:
        assert required in stages, f"missing stage {required} in {stages}"
    assert all("at" in cp and "ok" in cp and "stage" in cp for cp in report["receipt"]["checkpoints"])


def test_missing_license_fails_closed(tmp_path: Path):
    src, sha = make_repo(tmp_path, name="s7", files={"README.md": "Hello\n"})
    report = run_slice(base_req(sha, license="MIT"), dry_run=True, engine_path=ENGINE, clone_source=src)
    assert report["status"] == "FAILED"
    assert report["checks"]["license_kept"] is False
    assert "license" in (report.get("error") or "").lower() or "scan" in (report.get("error") or "").lower()


def test_brand_violation_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    src, sha = make_repo(tmp_path, name="s8", files=GOOD_FILES)

    class FakeBrand:
        def to_dict(self):
            return {
                "engine_ref": "fake",
                "files_changed": 1,
                "replacements": 1,
                "scan_passed": False,
                "scan_violations": ["README.md:1: hermes"],
                "log": "fake",
            }

    monkeypatch.setattr(pipeline, "run_brand", lambda *args, **kwargs: FakeBrand())
    report = run_slice(base_req(sha), dry_run=True, engine_path=ENGINE, clone_source=src)
    assert report["status"] == "FAILED"
    assert report["checks"]["gated"] is False
    assert "brand" in (report.get("error") or "").lower() or "gate" in (report.get("error") or "").lower()


def test_dry_run_never_calls_ship(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    src, sha = make_repo(tmp_path, name="s9", files=GOOD_FILES)

    def boom(*args, **kwargs):
        raise AssertionError("ship_private_repo should not be called in dry-run")

    monkeypatch.setattr(pipeline, "ship_private_repo", boom)
    report = run_slice(base_req(sha), dry_run=True, engine_path=ENGINE, clone_source=src)
    assert report["status"] in {"VERIFIED", "REQUIRES_REVIEW", "FAILED"}


def test_recovery_error_details_include_stage_and_type(tmp_path: Path):
    report = run_slice(base_req("not-a-sha"), dry_run=True, engine_path=ENGINE, clone_source=str(tmp_path / "missing"))
    assert report["status"] == "FAILED"
    err = report.get("error") or ""
    assert "intake" in err.lower() or "scan" in err.lower() or "validate" in err.lower() or "sha" in err.lower()
    # recovery-friendly: should have stage/context clues
    assert ":" in err


def test_receipt_recorded_for_scan_failure(tmp_path: Path):
    receipts = tmp_path / "receipts"
    req = base_req("a" * 40)
    report = run_slice(req, dry_run=True, engine_path=ENGINE, clone_source=str(tmp_path / "nope"), receipts_dir=receipts)
    assert report["status"] == "FAILED"
    assert list(receipts.glob("*.json"))


def test_failed_report_has_checkpoint_details(tmp_path: Path):
    receipts = tmp_path / "receipts"
    report = run_slice({"bad": "request"}, dry_run=True, receipts_dir=receipts)
    assert report["status"] == "FAILED"
    cps = report["receipt"]["checkpoints"]
    assert any(cp.get("stage") == "validate" and cp.get("ok") is False for cp in cps)
    failed = [cp for cp in cps if cp.get("ok") is False]
    assert any(cp.get("detail") for cp in failed)
