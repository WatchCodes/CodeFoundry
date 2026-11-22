"""Clone: byte-extract a pinned SHA snapshot into a clean workspace."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CloneError(RuntimeError):
    pass


@dataclass
class CloneResult:
    source: str
    sha: str
    dest_dir: str
    file_count: int
    tree_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "sha": self.sha,
            "dest_dir": self.dest_dir,
            "file_count": self.file_count,
            "tree_hash": self.tree_hash,
        }


def _run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True, capture_output=True)
    if p.returncode != 0:
        raise CloneError(f"command failed: {' '.join(cmd)}\n{p.stdout}\n{p.stderr}")
    return p


def _hash_tree(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def clone_snapshot(source: str, sha: str, dest_dir: str | Path, *, timeout: int = 300) -> CloneResult:
    """Clone source at pinned SHA into dest_dir, removing .git history.

    source is expected to be a GitHub owner/repo or a full git URL.
    """
    dest = Path(dest_dir)
    if dest.exists() and any(dest.iterdir()):
        raise CloneError(f"dest_dir not empty: {dest}")
    dest.mkdir(parents=True, exist_ok=True)

    src_path = Path(source)
    if source.startswith("http://") or source.startswith("https://") or source.startswith("git@"):
        url = source
    elif source.startswith("file://"):
        url = source
    elif src_path.exists():
        url = str(src_path.resolve())
    else:
        url = f"https://github.com/{source}.git"

    tmp = dest.parent / (dest.name + ".tmp-clone")
    if tmp.exists():
        shutil.rmtree(tmp)
    try:
        # Try init+fetch for pinned SHA (works for forks/commit SHAs not on default ref tips too)
        tmp.mkdir(parents=True)
        _run(["git", "init"], cwd=tmp)
        _run(["git", "remote", "add", "origin", url], cwd=tmp)
        _run(["git", "fetch", "--depth", "1", "origin", sha], cwd=tmp)
        _run(["git", "checkout", "FETCH_HEAD"], cwd=tmp)
    except CloneError:
        # fallback: full clone then checkout
        shutil.rmtree(tmp, ignore_errors=True)
        _run(["git", "clone", "--no-tags", url, str(tmp)])
        _run(["git", "checkout", sha], cwd=tmp)

    # strip git history and copy files
    git_dir = tmp / ".git"
    if git_dir.exists():
        shutil.rmtree(git_dir)
    # move contents into dest
    for child in tmp.iterdir():
        shutil.move(str(child), str(dest / child.name))
    shutil.rmtree(tmp, ignore_errors=True)

    file_count = sum(1 for p in dest.rglob("*") if p.is_file())
    return CloneResult(
        source=source,
        sha=sha,
        dest_dir=str(dest),
        file_count=file_count,
        tree_hash=_hash_tree(dest),
    )
