import os
import subprocess
from pathlib import Path

from codefoundry.pipeline import run_slice


def make_source_repo(tmp: Path) -> tuple[str, str]:
    src = tmp / "src"
    src.mkdir()
    (src / "README.md").write_text("Hello Hermes\n", encoding="utf-8")
    (src / "LICENSE").write_text("MIT License\n\nPermission is hereby granted...\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=src, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=src, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=src, check=True)
    subprocess.run(["git", "add", "-A"], cwd=src, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=src, check=True, capture_output=True, text=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, check=True, capture_output=True, text=True).stdout.strip()
    return str(src), sha


def test_dry_run_slice_local_repo(tmp_path: Path):
    src, sha = make_source_repo(tmp_path)
    req = {
        "schema_version": 1,
        "batch_id": "b-test",
        "source": "Test/Repo",
        "destination_owner": "WatchCodes",
        "target_name": "nastech-test",
        "sha": sha,
        "license": "MIT",
        "action": "create",
        "provenance": {
            "origin": "allowlisted",
            "discovered_in": "test/repo",
            "manifest_batch_id": "b-test",
        },
    }
    report = run_slice(req, dry_run=True, engine_path=os.environ.get("CODEFOUNDRY_100WAYS_PATH", "/home/nascode/work/100ways"), clone_source=src)
    assert report["status"] in {"VERIFIED", "REQUIRES_REVIEW", "FAILED"}
    assert report["batch_id"] == "b-test"
    assert report["receipt"]["source_sha"] == sha
    assert report["checks"]["gated"] is True
    assert report["checks"]["credits"] is True
    assert report["checks"]["license_kept"] is True
    assert report["verification"]["branding_findings"] == 0
    assert report["attribution"]["credits"] is True
