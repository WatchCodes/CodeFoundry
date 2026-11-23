import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from codefoundry.cli import main


def run_cli(args, cwd=None, input_text=None):
    cmd = [sys.executable, "-m", "codefoundry.cli", *args]
    env = dict(**__import__("os").environ)
    root = str(Path(__file__).resolve().parents[1])
    pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root + (":" + pp if pp else "")
    return subprocess.run(cmd, cwd=cwd, input=input_text, text=True, capture_output=True, env=env)


def make_source_repo(tmp: Path) -> tuple[str, str]:
    src = tmp / "src"
    src.mkdir()
    (src / "README.md").write_text("Hello\n", encoding="utf-8")
    (src / "LICENSE").write_text("MIT License\n\nPermission is hereby granted...\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=src, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=src, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=src, check=True)
    subprocess.run(["git", "add", "-A"], cwd=src, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=src, check=True, capture_output=True, text=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, check=True, capture_output=True, text=True).stdout.strip()
    return str(src), sha


def make_request(src_sha: str, batch="b-cli") -> dict:
    return {
        "schema_version": 1,
        "batch_id": batch,
        "source": "Test/Repo",
        "destination_owner": "WatchCodes",
        "target_name": "nastech-test",
        "sha": src_sha,
        "license": "MIT",
        "action": "create",
        "provenance": {"origin": "allowlisted", "discovered_in": "test/repo", "manifest_batch_id": batch},
    }


# --- exit codes ---

def test_validate_ok_exit_0(tmp_path):
    doc = {"schema_version": 1, "batch_id": "b", "source": "A/B", "destination_owner": "WatchCodes", "target_name": "nastech-r", "sha": "a" * 40, "license": "MIT", "action": "create", "provenance": {"origin": "allowlisted", "discovered_in": "A/B", "manifest_batch_id": "b"}}
    f = tmp_path / "req.json"
    f.write_text(json.dumps(doc), encoding="utf-8")
    r = run_cli(["validate", "migration-request", str(f)])
    assert r.returncode == 0


def test_validate_bad_schema_exit_1(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("{}", encoding="utf-8")
    r = run_cli(["validate", "migration-request", str(f)])
    assert r.returncode == 1


def test_validate_missing_file_exit_1(tmp_path):
    r = run_cli(["validate", "migration-request", str(tmp_path / "nope.json")])
    assert r.returncode == 1
    assert r.stderr.strip() or r.stdout.strip()


def test_usage_error_exit_1():
    r = run_cli(["run"])
    assert r.returncode == 1


def test_unknown_command_exit_1():
    r = run_cli(["bogus"])
    assert r.returncode == 1


# --- JSON output ---

def test_validate_ok_json_output(tmp_path):
    doc = {"schema_version": 1, "batch_id": "b", "source": "A/B", "destination_owner": "WatchCodes", "target_name": "nastech-r", "sha": "a" * 40, "license": "MIT", "action": "create", "provenance": {"origin": "allowlisted", "discovered_in": "A/B", "manifest_batch_id": "b"}}
    f = tmp_path / "req.json"
    f.write_text(json.dumps(doc), encoding="utf-8")
    r = run_cli(["validate", "migration-request", str(f), "--json"])
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["ok"] is True


def test_validate_bad_json_output(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("{}", encoding="utf-8")
    r = run_cli(["validate", "migration-request", str(f), "--json"])
    assert r.returncode == 1
    payload = json.loads(r.stdout)
    assert payload["ok"] is False
    assert payload["errors"]


def test_run_json_output(tmp_path):
    src, sha = make_source_repo(tmp_path)
    req = make_request(sha)
    req_file = tmp_path / "req.json"
    req_file.write_text(json.dumps(req), encoding="utf-8")
    r = run_cli(["run", str(req_file), "--clone-source", src, "--engine-path", os.environ.get("CODEFOUNDRY_100WAYS_PATH", "/home/nascode/work/100ways")])
    assert r.returncode in (0, 2)
    report = json.loads(r.stdout)
    assert report["schema_version"] == 1
    assert report["batch_id"] == "b-cli"


# --- run behavior ---

def test_run_dry_run_default_true(tmp_path):
    src, sha = make_source_repo(tmp_path)
    req = make_request(sha)
    req_file = tmp_path / "req.json"
    req_file.write_text(json.dumps(req), encoding="utf-8")
    r = run_cli(["run", str(req_file), "--clone-source", src, "--engine-path", os.environ.get("CODEFOUNDRY_100WAYS_PATH", "/home/nascode/work/100ways")])
    report = json.loads(r.stdout)
    # dry-run should not create/push external repo; checks.created false in dry-run path
    assert report["checks"]["created"] is False


def test_run_missing_request_exit_1(tmp_path):
    r = run_cli(["run", str(tmp_path / "missing.json")])
    assert r.returncode == 1


def test_run_invalid_json_exit_1(tmp_path):
    f = tmp_path / "req.json"
    f.write_text("{not json", encoding="utf-8")
    r = run_cli(["run", str(f)])
    assert r.returncode == 1


def test_run_exit_2_on_not_verified(tmp_path, monkeypatch):
    # build a request that will fail intake validation in run_slice -> FAILED report
    req = {"schema_version": 1}
    req_file = tmp_path / "req.json"
    req_file.write_text(json.dumps(req), encoding="utf-8")
    r = run_cli(["run", str(req_file)])
    assert r.returncode == 2
    report = json.loads(r.stdout)
    assert report["status"] in {"FAILED", "REQUIRES_REVIEW"}


# --- output file ---

def test_run_output_file(tmp_path):
    src, sha = make_source_repo(tmp_path)
    req = make_request(sha)
    req_file = tmp_path / "req.json"
    req_file.write_text(json.dumps(req), encoding="utf-8")
    out = tmp_path / "out" / "report.json"
    r = run_cli(["run", str(req_file), "--clone-source", src, "--engine-path", os.environ.get("CODEFOUNDRY_100WAYS_PATH", "/home/nascode/work/100ways"), "--output", str(out)])
    assert r.returncode in (0, 2)
    assert out.exists()
    file_report = json.loads(out.read_text(encoding="utf-8"))
    stdout_report = json.loads(r.stdout)
    assert file_report["batch_id"] == stdout_report["batch_id"]
    assert file_report["status"] == stdout_report["status"]


# --- arg validation ---

def test_reports_branch_push_requires_repo(tmp_path):
    src, sha = make_source_repo(tmp_path)
    req = make_request(sha)
    req_file = tmp_path / "req.json"
    req_file.write_text(json.dumps(req), encoding="utf-8")
    r = run_cli(["run", str(req_file), "--clone-source", src, "--reports-branch-push"])
    assert r.returncode == 1


def test_dry_run_push_no_external_write(tmp_path):
    # dry-run default true; reports-branch-push must not push
    src, sha = make_source_repo(tmp_path)
    repo = tmp_path / "reports-repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True, text=True)

    req = make_request(sha)
    req_file = tmp_path / "req.json"
    req_file.write_text(json.dumps(req), encoding="utf-8")
    r = run_cli([
        "run", str(req_file), "--clone-source", src, "--engine-path", os.environ.get("CODEFOUNDRY_100WAYS_PATH", "/home/nascode/work/100ways"),
        "--reports-branch-repo", str(repo), "--reports-branch", "reports/test", "--reports-branch-push",
    ])
    assert r.returncode in (0, 2)
    # branch may exist locally, but no remote configured/push happened; ensure no origin push side effects
    rem = subprocess.run(["git", "remote"], cwd=repo, capture_output=True, text=True)
    assert rem.stdout.strip() == ""


def test_reports_dir_and_receipts_dir_accepted(tmp_path):
    src, sha = make_source_repo(tmp_path)
    req = make_request(sha)
    req_file = tmp_path / "req.json"
    req_file.write_text(json.dumps(req), encoding="utf-8")
    reports = tmp_path / "reports"
    receipts = tmp_path / "receipts"
    r = run_cli([
        "run", str(req_file), "--clone-source", src, "--engine-path", os.environ.get("CODEFOUNDRY_100WAYS_PATH", "/home/nascode/work/100ways"),
        "--reports-dir", str(reports), "--receipts-dir", str(receipts),
    ])
    assert r.returncode in (0, 2)
    report = json.loads(r.stdout)
    assert report["batch_id"] == "b-cli"


def test_help_exit_0():
    r = run_cli(["--help"])
    assert r.returncode == 0
    assert "codefoundry" in r.stdout


def test_run_help_exit_0():
    r = run_cli(["run", "--help"])
    assert r.returncode == 0
    assert "--output" in r.stdout
