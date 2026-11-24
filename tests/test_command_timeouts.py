"""Every CodeFoundry git/gh invocation must be bounded.

An unbounded subprocess can wedge the factory forever on a hung credential
prompt, stuck network push, or stalled local git index. These tests pin the
timeout at the boundary and require a domain error on expiry.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import codefoundry.reports as reports
import codefoundry.ship as ship


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    for args in (
        ["init"],
        ["config", "user.name", "Fixture"],
        ["config", "user.email", "fixture@example.invalid"],
        ["add", "-A"],
        ["commit", "-m", "fixture"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return repo


def test_ship_commands_have_positive_timeout_and_expiry_fails_closed(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    real_run = subprocess.run
    calls: list[tuple[list[str], int | None]] = []

    def guarded(cmd, *args, **kwargs):
        timeout = kwargs.get("timeout")
        calls.append((list(cmd), timeout))
        assert timeout is not None and timeout > 0, f"unbounded command: {cmd}"
        if list(cmd)[:3] == ["git", "config", "user.name"]:
            raise subprocess.TimeoutExpired(cmd, timeout)
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(ship.subprocess, "run", guarded)
    with pytest.raises(ship.ShipError, match="timed out"):
        ship.ensure_git_repo(repo)

    assert calls
    assert any(cmd[:2] == ["git", "config"] for cmd, _ in calls)


def test_report_commands_have_positive_timeout_and_cleanup_still_runs(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    real_run = subprocess.run
    calls: list[tuple[list[str], int | None]] = []

    def guarded(cmd, *args, **kwargs):
        timeout = kwargs.get("timeout")
        calls.append((list(cmd), timeout))
        assert timeout is not None and timeout > 0, f"unbounded command: {cmd}"
        if list(cmd)[:2] == ["git", "add"]:
            raise subprocess.TimeoutExpired(cmd, timeout)
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(reports.subprocess, "run", guarded)
    report = {
        "schema_version": 1,
        "batch_id": "b1",
        "source": "Owner/Repo",
        "status": "VERIFIED",
        "receipt": {"destination_sha": "d" * 40},
    }
    with pytest.raises(reports.ReportWriteError, match="timed out"):
        reports.write_report_to_branch(report, repo_dir=repo)

    assert any(cmd[:2] == ["git", "worktree"] and cmd[2] == "remove" for cmd, _ in calls)
