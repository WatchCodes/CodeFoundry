import json
import subprocess
from pathlib import Path

from codefoundry.pipeline import run_slice
from codefoundry.reports import write_report, write_report_to_branch
from codefoundry.ship import ensure_git_repo, ship_private_repo


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_repo(tmp: Path, name="src", files=None):
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
        "schema_version": 1, "batch_id": "b-h", "source": "T/R",
        "destination_owner": "WatchCodes", "target_name": "nastech-test",
        "sha": sha, "license": "MIT", "action": "create",
        "provenance": {"origin": "allowlisted", "discovered_in": "t/r", "manifest_batch_id": "b-h"},
    }


ENGINE = "/home/nascode/work/100ways"


def test_ensure_git_repo_idempotent(tmp_path):
    src, sha = make_repo(tmp_path, "r1", {"README.md": "hi\n"})
    h1 = ensure_git_repo(tmp_path / "r1")
    h2 = ensure_git_repo(tmp_path / "r1")
    assert h1 == h2
    assert len(h1) == 40


def test_ship_dry_run_no_external_write(tmp_path):
    src, sha = make_repo(tmp_path, "r2", {"README.md": "hi\n"})
    res = ship_private_repo(tmp_path / "r2", destination="WatchCodes/test-repo", dry_run=True)
    assert res.dry_run is True
    assert res.private is True
    assert res.pushed_sha == sha
    # no origin remote required
    rem = subprocess.run(["git", "remote"], cwd=tmp_path / "r2", capture_output=True, text=True)
    assert "origin" not in rem.stdout.split()


def test_report_append_only_different_content(tmp_path):
    r1 = {"batch_id": "b1", "source": "A/B", "status": "FAILED", "receipt": {"destination_sha": "a"*40}}
    r2 = {"batch_id": "b1", "source": "A/B", "status": "VERIFIED", "receipt": {"destination_sha": "a"*40}}
    p1 = write_report(r1, tmp_path)
    p2 = write_report(r2, tmp_path)
    assert p1 != p2
    assert json.loads(p1.read_text())["status"] == "FAILED"
    assert json.loads(p2.read_text())["status"] == "VERIFIED"


def test_write_report_to_branch_local(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@t")
    (repo / "README.md").write_text("hi\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    report = {"batch_id": "b1", "source": "A/B", "status": "VERIFIED", "receipt": {"destination_sha": "b"*40}}
    p = write_report_to_branch(report, repo_dir=repo, branch="reports/test", push=False)
    assert p.name.endswith(".json")
    # branch exists
    br = subprocess.run(["git", "branch", "--list", "reports/test"], cwd=repo, capture_output=True, text=True)
    assert "reports/test" in br.stdout


def test_pipeline_reports_dir_idempotent(tmp_path):
    src, sha = make_repo(tmp_path, "s", {"README.md": "Hi\n", "LICENSE": "MIT License\n\nPermission is hereby granted...\n"})
    reports = tmp_path / "reports"
    req = base_req(sha)
    r1 = run_slice(req, dry_run=True, engine_path=ENGINE, clone_source=src, reports_dir=reports)
    r2 = run_slice(req, dry_run=True, engine_path=ENGINE, clone_source=src, reports_dir=reports)
    assert r1["status"] == r2["status"]
    assert len(list(reports.glob("*.json"))) == 1


def test_pipeline_no_reports_dir_no_write(tmp_path):
    src, sha = make_repo(tmp_path, "s2", {"README.md": "Hi\n", "LICENSE": "MIT License\n\nPermission is hereby granted...\n"})
    req = base_req(sha)
    # no reports_dir, no receipts_dir - should not write reports anywhere under tmp_path except clone temp? clone is in tempdir
    before = {p for p in tmp_path.rglob("*.json")}
    run_slice(req, dry_run=True, engine_path=ENGINE, clone_source=src)
    after = {p for p in tmp_path.rglob("*.json")}
    assert before == after
