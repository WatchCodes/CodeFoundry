import subprocess
from pathlib import Path

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
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, check=True, capture_output=True, text=True).stdout.strip()
    return str(src), sha


def base_req(sha):
    return {
        "schema_version": 1,
        "batch_id": "b-scenario",
        "source": "Test/Repo",
        "destination_owner": "WatchCodes",
        "target_name": "nastech-test",
        "sha": sha,
        "license": "MIT",
        "action": "create",
        "provenance": {"origin": "allowlisted", "discovered_in": "test/repo", "manifest_batch_id": "b-scenario"},
    }


def test_no_license_fails(tmp_path: Path):
    src, sha = make_repo(tmp_path, files={"README.md": "Hello\n"})
    report = run_slice(base_req(sha), dry_run=True, engine_path="/home/nascode/work/100ways", clone_source=src)
    assert report["status"] == "FAILED"
    assert report["checks"]["license_kept"] is False


def test_brand_remnant_in_filename_is_cleaned(tmp_path: Path):
    src, sha = make_repo(tmp_path, name="src2", files={"README.md": "ok\n", "LICENSE": "MIT License\n\nPermission is hereby granted...\n", "hermes.txt": "data\n"})
    report = run_slice(base_req(sha), dry_run=True, engine_path="/home/nascode/work/100ways", clone_source=src)
    # branding engine should rename/clean; gate should be clean
    assert report["checks"]["gated"] is True


def test_invalid_request_fails_closed(tmp_path: Path):
    report = run_slice({"schema_version": 1, "batch_id": "b"}, dry_run=True)
    assert report["status"] == "FAILED"
    assert "intake" in (report.get("error") or "")


def test_dry_run_does_not_create(tmp_path: Path):
    src, sha = make_repo(tmp_path, name="src3", files={"README.md": "Hello\n", "LICENSE": "MIT License\n\nPermission is hereby granted...\n"})
    report = run_slice(base_req(sha), dry_run=True, engine_path="/home/nascode/work/100ways", clone_source=src)
    assert report["checks"]["created"] is False
    # no destination_repo_id assigned in dry-run
    assert report["receipt"]["destination_repo_id"] == 0


def test_idempotent_receipt_written(tmp_path: Path):
    src, sha = make_repo(tmp_path, name="src4", files={"README.md": "Hello\n", "LICENSE": "MIT License\n\nPermission is hereby granted...\n"})
    receipts = tmp_path / "receipts"
    r1 = run_slice(base_req(sha), dry_run=True, engine_path="/home/nascode/work/100ways", clone_source=src, receipts_dir=receipts)
    r2 = run_slice(base_req(sha), dry_run=True, engine_path="/home/nascode/work/100ways", clone_source=src, receipts_dir=receipts)
    assert r1["receipt"]["idempotency_key"] == r2["receipt"]["idempotency_key"]
    assert any(receipts.iterdir())
