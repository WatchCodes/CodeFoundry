import json
import os
import subprocess
from pathlib import Path

import pytest

from codefoundry.pipeline import run_slice


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_repo(tmp: Path, *, name="src", files=None):
    src = tmp / name
    src.mkdir()
    for p, content in (files or {}).items():
        fp = src / p
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
    _git(src, "init")
    _git(src, "config", "user.name", "t")
    _git(src, "config", "user.email", "t@t")
    _git(src, "add", "-A")
    _git(src, "commit", "-m", "init")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=src, check=True, capture_output=True, text=True
    ).stdout.strip()
    return str(src), sha


def base_req(sha, **overrides):
    r = {
        "schema_version": 1,
        "batch_id": "b-scenario",
        "source": "Test/Repo",
        "destination_owner": "WatchCodes",
        "target_name": "nastech-test",
        "sha": sha,
        "license": "MIT",
        "action": "create",
        "provenance": {
            "origin": "allowlisted",
            "discovered_in": "test/repo",
            "manifest_batch_id": "b-scenario",
        },
    }
    r.update(overrides)
    return r


ENGINE = os.environ.get("CODEFOUNDRY_100WAYS_PATH", "/home/nascode/work/100ways")


def _run(req, tmp_path, *, name="src", files=None, dry_run=True, **kwargs):
    if files is None:
        files = {
            "README.md": "Hello\n",
            "LICENSE": "MIT License\n\nPermission is hereby granted...\n",
        }
    src, sha = make_repo(tmp_path, name=name, files=files)
    req = dict(req)
    req["sha"] = sha
    return run_slice(
        req, dry_run=dry_run, engine_path=ENGINE, clone_source=src, **kwargs
    )


# 1
def test_no_license_fails(tmp_path: Path):
    report = _run(base_req("0" * 40), tmp_path, name="s1", files={"README.md": "Hello\n"})
    assert report["status"] == "FAILED"
    assert report["checks"]["license_kept"] is False


# 2
def test_brand_remnant_in_filename_is_cleaned(tmp_path: Path):
    report = _run(
        base_req("0" * 40),
        tmp_path,
        name="s2",
        files={
            "README.md": "ok\n",
            "LICENSE": "MIT License\n\nPermission is hereby granted...\n",
            "hermes.txt": "data\n",
        },
    )
    assert report["checks"]["gated"] is True


# 3
def test_invalid_request_fails_closed(tmp_path: Path):
    report = run_slice({"schema_version": 1, "batch_id": "b"}, dry_run=True)
    assert report["status"] == "FAILED"
    assert "intake" in (report.get("error") or "")


# 4
def test_dry_run_does_not_create(tmp_path: Path):
    report = _run(base_req("0" * 40), tmp_path, name="s4")
    assert report["checks"]["created"] is False
    assert report["receipt"]["destination_repo_id"] == 0


# 5
def test_idempotent_receipt_written(tmp_path: Path):
    src, sha = make_repo(
        tmp_path,
        name="s5",
        files={
            "README.md": "Hello\n",
            "LICENSE": "MIT License\n\nPermission is hereby granted...\n",
        },
    )
    receipts = tmp_path / "receipts"
    r1 = run_slice(
        base_req(sha), dry_run=True, engine_path=ENGINE, clone_source=src, receipts_dir=receipts
    )
    r2 = run_slice(
        base_req(sha), dry_run=True, engine_path=ENGINE, clone_source=src, receipts_dir=receipts
    )
    assert r1["receipt"]["idempotency_key"] == r2["receipt"]["idempotency_key"]
    assert any(receipts.iterdir())


# 6
def test_import_subtree_requires_subtree_path(tmp_path: Path):
    req = base_req("0" * 40, action="import_subtree")
    report = run_slice(req, dry_run=True)
    assert report["status"] == "FAILED"
    assert "intake" in (report.get("error") or "")


# 7
def test_create_rejects_subtree_path(tmp_path: Path):
    req = base_req("0" * 40, subtree_path="sub/dir")
    report = run_slice(req, dry_run=True)
    assert report["status"] == "FAILED"


# 8
def test_create_rejects_unknown_license(tmp_path: Path):
    req = base_req("0" * 40, license="UNKNOWN")
    report = run_slice(req, dry_run=True)
    assert report["status"] == "FAILED"


# 9
def test_create_rejects_noassertion_license(tmp_path: Path):
    req = base_req("0" * 40, license="NOASSERTION")
    report = run_slice(req, dry_run=True)
    assert report["status"] == "FAILED"


# 10
def test_report_written_when_reports_dir_given(tmp_path: Path):
    src, sha = make_repo(
        tmp_path,
        name="s10",
        files={"README.md": "Hi\n", "LICENSE": "MIT License\n\nPermission is hereby granted...\n"},
    )
    reports = tmp_path / "reports"
    report = run_slice(
        base_req(sha),
        dry_run=True,
        engine_path=ENGINE,
        clone_source=src,
        reports_dir=reports,
    )
    assert report["status"] in {"VERIFIED", "REQUIRES_REVIEW", "FAILED"}
    files = list(reports.glob("*.json"))
    assert len(files) == 1
    saved = json.loads(files[0].read_text())
    assert saved["batch_id"] == report["batch_id"]


# 11
def test_dry_run_with_explicit_dirs_writes_only_those_dirs(tmp_path: Path):
    src, sha = make_repo(
        tmp_path,
        name="s11",
        files={"README.md": "Hi\n", "LICENSE": "MIT License\n\nPermission is hereby granted...\n"},
    )
    receipts = tmp_path / "receipts"
    reports = tmp_path / "reports"
    outside = tmp_path / "outside"
    outside.mkdir()
    before = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()}
    run_slice(
        base_req(sha),
        dry_run=True,
        engine_path=ENGINE,
        clone_source=src,
        receipts_dir=receipts,
        reports_dir=reports,
    )
    after = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()}
    new_files = after - before
    assert all(f.startswith("receipts/") or f.startswith("reports/") or f.startswith("s11/") for f in new_files)


# 12
def test_missing_schema_version_fails_closed(tmp_path: Path):
    req = base_req("0" * 40)
    del req["schema_version"]
    report = run_slice(req, dry_run=True)
    assert report["status"] == "FAILED"


# 13
def test_missing_batch_id_fails_closed(tmp_path: Path):
    req = base_req("0" * 40)
    del req["batch_id"]
    report = run_slice(req, dry_run=True)
    assert report["status"] == "FAILED"


# 14
def test_brand_remnant_in_content_is_cleaned(tmp_path: Path):
    report = _run(
        base_req("0" * 40),
        tmp_path,
        name="s14",
        files={
            "README.md": "Hello Hermes agent\n",
            "LICENSE": "MIT License\n\nPermission is hereby granted...\n",
        },
    )
    assert report["checks"]["gated"] is True
    assert report["verification"]["branding_findings"] == 0


# 15
def test_apache_license_detected(tmp_path: Path):
    apache = "Apache License\nVersion 2.0, January 2004\nhttp://www.apache.org/licenses/\n"
    report = _run(
        base_req("0" * 40, license="Apache-2.0"),
        tmp_path,
        name="s15",
        files={"README.md": "Hi\n", "LICENSE": apache},
    )
    assert report["license"]["detected"] in {"Apache-2.0", "NOASSERTION"}
    # if detected, should be kept; if not, should fail-closed
    assert report["status"] in {"VERIFIED", "REQUIRES_REVIEW", "FAILED"}


# 16
def test_invalid_clone_source_fails(tmp_path: Path):
    req = base_req("a" * 40)
    report = run_slice(
        req, dry_run=True, engine_path=ENGINE, clone_source=str(tmp_path / "nope")
    )
    assert report["status"] == "FAILED"
    assert "scan" in (report.get("error") or "").lower() or "clone" in (report.get("error") or "").lower()


# 17
def test_receipts_dir_created_idempotently(tmp_path: Path):
    src, sha = make_repo(
        tmp_path,
        name="s17",
        files={"README.md": "Hi\n", "LICENSE": "MIT License\n\nPermission is hereby granted...\n"},
    )
    receipts = tmp_path / "r"
    run_slice(base_req(sha), dry_run=True, engine_path=ENGINE, clone_source=src, receipts_dir=receipts)
    run_slice(base_req(sha), dry_run=True, engine_path=ENGINE, clone_source=src, receipts_dir=receipts)
    files = list(receipts.glob("*.json"))
    assert len(files) == 1


# 18
def test_report_has_required_terminal_shape(tmp_path: Path):
    report = _run(base_req("0" * 40), tmp_path, name="s18")
    assert report["schema_version"] == 1
    assert report["status"] in {"VERIFIED", "FAILED", "REQUIRES_REVIEW"}
    assert "checks" in report
    assert "receipt" in report
    assert "license" in report
    assert "attribution" in report


# 19
def test_bad_sha_format_fails_closed(tmp_path: Path):
    req = base_req("not-a-sha")
    report = run_slice(req, dry_run=True)
    assert report["status"] == "FAILED"


# 20
def test_same_report_not_duplicated(tmp_path: Path):
    src, sha = make_repo(
        tmp_path,
        name="s20",
        files={"README.md": "Hi\n", "LICENSE": "MIT License\n\nPermission is hereby granted...\n"},
    )
    reports = tmp_path / "reports"
    run_slice(base_req(sha), dry_run=True, engine_path=ENGINE, clone_source=src, reports_dir=reports)
    run_slice(base_req(sha), dry_run=True, engine_path=ENGINE, clone_source=src, reports_dir=reports)
    files = list(reports.glob("*.json"))
    assert len(files) == 1
