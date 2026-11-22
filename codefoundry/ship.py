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


def ensure_git_repo(repo_dir: Path) -> str:
    """Ensure repo_dir is a git repo with at least one commit. Idempotent."""
    if not (repo_dir / ".git").exists():
        _run(["git", "init"], cwd=repo_dir)
    # ensure identity is local-only to this repo
    _run(["git", "config", "user.name", "WatchCodes Bot"], cwd=repo_dir)
    _run(["git", "config", "user.email", "bot@watchcodes.local"], cwd=repo_dir)
    # commit if there is no HEAD or there are staged/unstaged changes
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, text=True, capture_output=True)
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo_dir, text=True, capture_output=True)
    if head.returncode != 0 or status.stdout.strip():
        _run(["git", "add", "-A"], cwd=repo_dir)
        # avoid empty commit failure
        staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo_dir)
        if staged.returncode != 0 or head.returncode != 0:
            msg = "Initial branded snapshot" if head.returncode != 0 else "Update branded snapshot"
            commit = subprocess.run(["git", "commit", "-m", msg], cwd=repo_dir, text=True, capture_output=True)
            if commit.returncode != 0:
                # If nothing to commit after race, ignore; otherwise fail
                if "nothing to commit" not in (commit.stdout + commit.stderr).lower():
                    raise ShipError(f"git commit failed:\n{commit.stdout}\n{commit.stderr}")
    return _run(["git", "rev-parse", "HEAD"], cwd=repo_dir).stdout.strip()


def ship_private_repo(
    repo_dir: str | Path,
    *,
    destination: str,
    dry_run: bool = True,
    visibility: str = "private",
) -> ShipResult:
    if visibility != "private":
        raise ShipError("creation must be private-first; visibility must be 'private'")
    if "/" not in destination:
        raise ShipError(f"destination must be owner/name, got {destination!r}")
    root = Path(repo_dir)
    head = ensure_git_repo(root)
    repo_url = f"https://github.com/{destination}"

    if dry_run:
        return ShipResult(destination=destination, repo_url=repo_url, private=True, pushed_sha=head, dry_run=True)

    # create repo via gh; idempotent - if exists, continue
    view = subprocess.run(["gh", "repo", "view", destination], text=True, capture_output=True)
    if view.returncode != 0:
        create = subprocess.run(
            ["gh", "repo", "create", destination, "--private"],
            text=True,
            capture_output=True,
        )
        if create.returncode != 0:
            # race/retry: if it exists now, continue; else fail
            view2 = subprocess.run(["gh", "repo", "view", destination], text=True, capture_output=True)
            if view2.returncode != 0:
                raise ShipError(f"gh repo create failed: {create.stdout}\n{create.stderr}")

    remote_url = f"https://github.com/{destination}.git"
    has_origin = subprocess.run(["git", "remote", "get-url", "origin"], cwd=root, capture_output=True).returncode == 0
    if has_origin:
        _run(["git", "remote", "set-url", "origin", remote_url], cwd=root)
    else:
        _run(["git", "remote", "add", "origin", remote_url], cwd=root)

    push = subprocess.run(["git", "push", "-u", "origin", "HEAD:main"], cwd=root, text=True, capture_output=True)
    if push.returncode != 0:
        raise ShipError(f"git push failed: {push.stdout}\n{push.stderr}")
    return ShipResult(destination=destination, repo_url=repo_url, private=True, pushed_sha=head, dry_run=False)
