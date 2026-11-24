"""An unverifiable privacy answer must never authorize a real push.

"Private first" is non-negotiable. If gh cannot prove an existing destination
is private, the only safe outcome is refusal -- not a push followed by a
``private=True`` claim in the ship result.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

import codefoundry.ship as ship
from codefoundry.ship import ShipError, ship_private_repo
from tests.test_ship_hardening import make_repo

DESTINATION = "WatchCodes/privacy-unknown"


def _install_fake_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    privacy_result: subprocess.CompletedProcess[str],
    create_result: subprocess.CompletedProcess[str] | None = None,
) -> list[list[str]]:
    real_run = subprocess.run
    calls: list[list[str]] = []

    def fake(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        cmd_list = list(cmd)
        calls.append(cmd_list)
        if cmd_list[:3] == ["gh", "repo", "view"]:
            if "--json" in cmd_list:
                return privacy_result
            return subprocess.CompletedProcess(cmd_list, 0, stdout="ok\n", stderr="")
        if cmd_list[:3] == ["gh", "repo", "create"]:
            if create_result is not None:
                return create_result
            raise AssertionError("must not create when repo already exists")
        if cmd_list[:2] == ["git", "push"]:
            raise AssertionError("must not push when privacy is unverified")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(ship.subprocess, "run", fake)
    monkeypatch.setattr(ship.shutil, "which", lambda c: "/usr/bin/" + c)
    return calls


@pytest.mark.parametrize(
    "privacy_result",
    [
        subprocess.CompletedProcess([], 1, stdout="", stderr="gh: rate limited"),
        subprocess.CompletedProcess([], 0, stdout="null\n", stderr=""),
        subprocess.CompletedProcess([], 0, stdout="unknown\n", stderr=""),
    ],
    ids=["query-failed", "null-answer", "unrecognized-answer"],
)
def test_unknown_privacy_refuses_push(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    privacy_result: subprocess.CompletedProcess[str],
) -> None:
    repo = make_repo(tmp_path, "src", {"README.md": "hi\n"})
    calls = _install_fake_run(monkeypatch, privacy_result=privacy_result)

    with pytest.raises(ShipError) as excinfo:
        ship_private_repo(repo, destination=DESTINATION, dry_run=False)

    message = str(excinfo.value).lower()
    assert "private" in message
    assert not any(c[:2] == ["git", "push"] for c in calls)


def test_unknown_privacy_after_create_race_refuses_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path, "src", {"README.md": "hi\n"})
    calls = _install_fake_run(
        monkeypatch,
        privacy_result=subprocess.CompletedProcess([], 1, stdout="", stderr="gh: forbidden"),
        create_result=subprocess.CompletedProcess([], 1, stdout="", stderr="already exists"),
    )

    with pytest.raises(ShipError) as excinfo:
        ship_private_repo(repo, destination=DESTINATION, dry_run=False)

    assert "private" in str(excinfo.value).lower()
    assert not any(c[:2] == ["git", "push"] for c in calls)
