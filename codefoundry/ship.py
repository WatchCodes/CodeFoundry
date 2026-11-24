"""Ship: create a private destination repo and push verified snapshot."""

from __future__ import annotations

import re
import shutil
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
    repo_existed: bool = False
    remote_updated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "destination": self.destination,
            "repo_url": self.repo_url,
            "private": self.private,
            "pushed_sha": self.pushed_sha,
            "dry_run": self.dry_run,
            "repo_existed": self.repo_existed,
            "remote_updated": self.remote_updated,
        }


_DEST_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
DEFAULT_COMMAND_TIMEOUT = 300


def _require_git() -> None:
    if shutil.which("git") is None:
        raise ShipError(
            "preflight failed: 'git' is not available on PATH. Install git and retry."
        )


def _require_gh() -> None:
    if shutil.which("gh") is None:
        raise ShipError(
            "preflight failed: 'gh' (GitHub CLI) is not available on PATH. "
            "Install/auth gh or use dry_run=True."
        )


def _validate_destination(destination: str) -> str:
    if not isinstance(destination, str) or not destination.strip():
        raise ShipError("destination must be a non-empty 'owner/name' string")
    dest = destination.strip()
    if not _DEST_RE.match(dest) or dest.count("/") != 1:
        raise ShipError(
            f"destination must be 'owner/name' with safe characters [A-Za-z0-9_.-], got {destination!r}"
        )
    owner, name = dest.split("/", 1)
    if not owner or not name or owner in {".", ".."} or name in {".", ".."}:
        raise ShipError(f"destination has invalid owner/name parts: {destination!r}")
    return dest


def _run_raw(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = DEFAULT_COMMAND_TIMEOUT,
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Run one command with a hard deadline; never inherit an unbounded wait."""
    kwargs.pop("text", None)
    kwargs.pop("capture_output", None)
    try:
        return subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, text=True, capture_output=True, timeout=timeout,
            **kwargs,
        )
    except subprocess.TimeoutExpired as exc:
        raise ShipError(f"command timed out after {timeout}s: {' '.join(cmd)}") from exc
    except OSError as exc:
        raise ShipError(f"failed to execute {' '.join(cmd)}: {exc}") from exc


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = DEFAULT_COMMAND_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    p = _run_raw(cmd, cwd=cwd, timeout=timeout)
    if p.returncode != 0:
        raise ShipError(
            f"command failed (exit {p.returncode}): {' '.join(cmd)}\n"
            f"stdout:\n{p.stdout}\n"
            f"stderr:\n{p.stderr}"
        )
    return p


def ensure_git_repo(repo_dir: Path) -> str:
    """Ensure repo_dir is a git repo with at least one commit. Idempotent."""
    _require_git()
    root = Path(repo_dir)
    if not root.exists():
        raise ShipError(f"repo_dir does not exist: {root}")
    if not root.is_dir():
        raise ShipError(f"repo_dir is not a directory: {root}")
    if not (root / ".git").exists():
        _run(["git", "init"], cwd=root)
    # ensure identity is local-only to this repo
    _run(["git", "config", "user.name", "WatchCodes Bot"], cwd=root)
    _run(["git", "config", "user.email", "bot@watchcodes.local"], cwd=root)
    # commit if there is no HEAD or there are staged/unstaged changes
    head = _run_raw(["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True)
    status = _run_raw(["git", "status", "--porcelain"], cwd=root, text=True, capture_output=True)
    if head.returncode != 0 or status.stdout.strip():
        _run(["git", "add", "-A"], cwd=root)
        # avoid empty commit failure
        staged = _run_raw(["git", "diff", "--cached", "--quiet"], cwd=root)
        if staged.returncode != 0 or head.returncode != 0:
            msg = "Initial branded snapshot" if head.returncode != 0 else "Update branded snapshot"
            commit = _run_raw(["git", "commit", "-m", msg], cwd=root, text=True, capture_output=True)
            if commit.returncode != 0:
                # If nothing to commit after race, ignore; otherwise fail
                if "nothing to commit" not in (commit.stdout + commit.stderr).lower():
                    raise ShipError(
                        "git commit failed while ensuring repo snapshot.\n"
                        f"stdout:\n{commit.stdout}\n"
                        f"stderr:\n{commit.stderr}"
                    )
    return _run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()


def _repo_is_private(destination: str) -> bool | None:
    p = _run_raw(
        ["gh", "repo", "view", destination, "--json", "isPrivate", "-q", ".isPrivate"],
        text=True,
        capture_output=True,
    )
    if p.returncode != 0:
        return None
    out = p.stdout.strip().lower()
    if out == "true":
        return True
    if out == "false":
        return False
    return None


def ship_private_repo(
    repo_dir: str | Path,
    *,
    destination: str,
    dry_run: bool = True,
    visibility: str = "private",
) -> ShipResult:
    _require_git()
    if visibility != "private":
        raise ShipError(
            "private-first enforcement: visibility must be 'private'. "
            "Public or other visibility is refused; create as private first."
        )
    dest = _validate_destination(destination)
    root = Path(repo_dir)
    if not root.exists():
        raise ShipError(f"repo_dir does not exist: {root}")
    if not root.is_dir():
        raise ShipError(f"repo_dir is not a directory: {root}")

    head = ensure_git_repo(root)
    repo_url = f"https://github.com/{dest}"

    if dry_run:
        # Dry-run must perform zero external writes: no gh, no git push, no remote mutation.
        return ShipResult(
            destination=dest,
            repo_url=repo_url,
            private=True,
            pushed_sha=head,
            dry_run=True,
        )

    _require_gh()

    # create repo via gh; idempotent - if exists, continue
    view = _run_raw(["gh", "repo", "view", dest], text=True, capture_output=True)
    repo_existed = view.returncode == 0
    if repo_existed:
        is_private = _repo_is_private(dest)
        if is_private is not True:
            raise ShipError(
                "private-first enforcement: could not verify "
                f"existing repo {dest!r} is private. "
                "Refusing to ship to a destination with unverified privacy."
            )
    else:
        create = _run_raw(
            ["gh", "repo", "create", dest, "--private"],
            text=True,
            capture_output=True,
        )
        if create.returncode != 0:
            # race/retry: if it exists now, continue; else fail
            view2 = _run_raw(["gh", "repo", "view", dest], text=True, capture_output=True)
            if view2.returncode == 0:
                repo_existed = True
                is_private = _repo_is_private(dest)
                if is_private is not True:
                    raise ShipError(
                        "private-first enforcement: could not verify "
                        f"existing repo {dest!r} is private. "
                        "Refusing to ship to a destination with unverified privacy."
                    )
            else:
                raise ShipError(
                    "gh repo create failed; repository does not exist after retry check.\n"
                    f"command: gh repo create {dest} --private\n"
                    f"stdout:\n{create.stdout}\n"
                    f"stderr:\n{create.stderr}\n"
                    "Guidance: verify gh auth (`gh auth status`), confirm owner permissions, "
                    "and retry. Do not use destructive flags."
                )

    remote_url = f"https://github.com/{dest}.git"
    has_origin = (
        _run_raw(["git", "remote", "get-url", "origin"], cwd=root).returncode == 0
    )
    remote_updated = False
    if has_origin:
        current = _run_raw(
            ["git", "remote", "get-url", "origin"], cwd=root, text=True, capture_output=True
        )
        current_url = current.stdout.strip()
        if current_url != remote_url:
            _run(["git", "remote", "set-url", "origin", remote_url], cwd=root)
            remote_updated = True
    else:
        _run(["git", "remote", "add", "origin", remote_url], cwd=root)
        remote_updated = True

    push = _run_raw(
        ["git", "push", "-u", "origin", "HEAD:main"],
        cwd=root,
        text=True,
        capture_output=True,
    )
    if push.returncode != 0:
        combined = (push.stdout + "\n" + push.stderr).lower()
        guidance = [
            "Push rejected; no force-push was attempted and no deletion was performed.",
            "Guidance: do NOT use --force/--delete.",
        ]
        if "non-fast-forward" in combined or "fetch first" in combined or "rejected" in combined:
            guidance.append(
                "Remote has history not in local snapshot. Fetch/reconcile manually (e.g. inspect remote, "
                "merge/rebase on a separate branch) then retry ship."
            )
        if "authentication" in combined or "permission" in combined or "403" in combined or "denied" in combined:
            guidance.append("Check gh/git auth and repository permissions (`gh auth status`).")
        if "protected" in combined:
            guidance.append("Branch protection may block push; adjust policy or push to an allowed branch manually.")
        raise ShipError(
            "git push failed.\n"
            f"command: git push -u origin HEAD:main\n"
            f"stdout:\n{push.stdout}\n"
            f"stderr:\n{push.stderr}\n"
            + "\n".join(guidance)
        )
    return ShipResult(
        destination=dest,
        repo_url=repo_url,
        private=True,
        pushed_sha=head,
        dry_run=False,
        repo_existed=repo_existed,
        remote_updated=remote_updated,
    )
