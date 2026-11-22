"""Ship: create a private destination repo and push verified snapshot."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ShipError(RuntimeError):
    pass


@dataclass
class ShipResult:
    destination: str
    repo_url: str
    private: bool
    pushed_sha: str | None
    dry_run: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "destination": self.destination,
            "repo_url": self.repo_url,
            "private": self.private,
            "pushed_sha": self.pushed_sha,
            "dry_run": self.dry_run,
        }


def _run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True, capture_output=True)
    if p.returncode != 0:
        raise ShipError(f"command failed: {' '.join(cmd)}\n{p.stdout}\n{p.stderr}")
    return p


def ensure_git_repo(repo_dir: Path) -> None:
    if not (repo_dir / ".git").exists():
        _run(["git", "init"], cwd=repo_dir)
        _run(["git", "config", "user.name", "WatchCodes Bot"], cwd=repo_dir)
        _run(["git", "config", "user.email", "bot@watchcodes.local"], cwd=repo_dir)
        _run(["git", "add", "-A"], cwd=repo_dir)
        _run(["git", "commit", "-m", "Initial branded snapshot"], cwd=repo_dir)


def ship_private_repo(
    repo_dir: str | Path,
    *,
    destination: str,
    dry_run: bool = True,
    visibility: str = "private",
) -> ShipResult:
    if visibility != "private":
        raise ShipError("creation must be private-first; visibility must be 'private'")
    root = Path(repo_dir)
    ensure_git_repo(root)
    head = _run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()
    repo_url = f"https://github.com/{destination}"

    if dry_run:
        return ShipResult(destination=destination, repo_url=repo_url, private=True, pushed_sha=head, dry_run=True)

    owner, name = destination.split("/", 1)
    # create repo via gh; idempotent-ish: fail if exists
    view = subprocess.run(["gh", "repo", "view", destination], text=True, capture_output=True)
    if view.returncode != 0:
        _run(["gh", "repo", "create", destination, "--private", "--confirm"])
    _run(["git", "remote", "remove", "origin"], cwd=root) if subprocess.run(["git", "remote", "get-url", "origin"], cwd=root, capture_output=True).returncode == 0 else None
    _run(["git", "remote", "add", "origin", f"https://github.com/{destination}.git"], cwd=root)
    _run(["git", "push", "-u", "origin", "HEAD:main"], cwd=root)
    return ShipResult(destination=destination, repo_url=repo_url, private=True, pushed_sha=head, dry_run=False)
