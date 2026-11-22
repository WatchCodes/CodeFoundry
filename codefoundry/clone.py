"""Clone: byte-extract a pinned SHA snapshot into a clean workspace."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass, field
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
    submodules_detected: bool = False
    submodule_paths: list[str] = field(default_factory=list)
    lfs_detected: bool = False
    lfs_patterns: list[str] = field(default_factory=list)
    manifest_hash: str = ""
    manifest_file_count: int = 0
    file_manifest: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "sha": self.sha,
            "dest_dir": self.dest_dir,
            "file_count": self.file_count,
            "tree_hash": self.tree_hash,
            "submodules_detected": self.submodules_detected,
            "submodule_paths": list(self.submodule_paths),
            "lfs_detected": self.lfs_detected,
            "lfs_patterns": list(self.lfs_patterns),
            "manifest_hash": self.manifest_hash,
            "manifest_file_count": self.manifest_file_count,
            "file_manifest": [dict(item) for item in self.file_manifest] if self.file_manifest is not None else None,
        }


def _run(cmd: list[str], *, cwd: Path | None = None, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout if isinstance(e.stdout, str) else (e.stdout.decode("utf-8", errors="replace") if e.stdout else "")
        stderr = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode("utf-8", errors="replace") if e.stderr else "")
        raise CloneError(
            f"command timed out after {timeout}s: {' '.join(cmd)}\n{stdout}\n{stderr}"
        ) from e
    if p.returncode != 0:
        raise CloneError(f"command failed: {' '.join(cmd)}\n{p.stdout}\n{p.stderr}")
    return p


def _iter_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


def _hash_tree(root: Path) -> str:
    h = hashlib.sha256()
    for path in _iter_files(root):
        rel = path.relative_to(root).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _build_manifest(root: Path) -> tuple[list[dict[str, Any]], str]:
    entries: list[dict[str, Any]] = []
    h = hashlib.sha256()
    for path in _iter_files(root):
        rel = path.relative_to(root).as_posix()
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        entry = {"path": rel, "sha256": digest, "size": len(data)}
        entries.append(entry)
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(digest.encode("utf-8"))
        h.update(b"\0")
    return entries, h.hexdigest()


def _detect_submodules(root: Path) -> list[str]:
    gitmodules = root / ".gitmodules"
    if not gitmodules.is_file():
        return []
    paths: list[str] = []
    try:
        content = gitmodules.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip().lower() == "path":
            path = value.strip().replace("\\", "/").strip("/")
            if path:
                paths.append(path)
    # Preserve discovery order but remove duplicates.
    seen: set[str] = set()
    uniq: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def _detect_lfs(root: Path) -> list[str]:
    patterns: list[str] = []
    for attr_path in sorted(root.rglob(".gitattributes")):
        if not attr_path.is_file():
            continue
        try:
            content = attr_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel_attr = attr_path.relative_to(root).as_posix()
        for raw in content.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "filter=lfs" in line.lower():
                patterns.append(f"{rel_attr}: {line}")
    return patterns


def _cleanup_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _cleanup_partial_dest(dest: Path, created_dest: bool) -> None:
    if not dest.exists():
        return
    if created_dest:
        _cleanup_dir(dest)
        return
    for child in list(dest.iterdir()):
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                child.unlink()
            except OSError:
                pass


def clone_snapshot(
    source: str,
    sha: str,
    dest_dir: str | Path,
    *,
    timeout: int = 300,
    include_manifest: bool = False,
) -> CloneResult:
    """Clone source at pinned SHA into dest_dir, removing .git history.

    source is expected to be a GitHub owner/repo or a full git URL.
    The clone is non-recursive; submodules/LFS filters are detected and reported.
    """
    dest = Path(dest_dir)
    if dest.exists():
        if dest.is_file():
            raise CloneError(f"dest_dir is a file: {dest}")
        if any(dest.iterdir()):
            raise CloneError(f"dest_dir not empty: {dest}")
        created_dest = False
    else:
        dest.mkdir(parents=True)
        created_dest = True

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
    _cleanup_dir(tmp)
    try:
        # Try init+fetch for pinned SHA (works for forks/commit SHAs not on default ref tips too)
        tmp.mkdir(parents=True)
        _run(["git", "init"], cwd=tmp, timeout=timeout)
        _run(["git", "remote", "add", "origin", url], cwd=tmp, timeout=timeout)
        _run(["git", "fetch", "--depth", "1", "origin", sha], cwd=tmp, timeout=timeout)
        _run(["git", "checkout", "FETCH_HEAD"], cwd=tmp, timeout=timeout)
    except CloneError:
        # fallback: full clone then checkout
        _cleanup_dir(tmp)
        try:
            _run(["git", "clone", "--no-tags", url, str(tmp)], timeout=timeout)
            _run(["git", "checkout", sha], cwd=tmp, timeout=timeout)
        except Exception:
            _cleanup_dir(tmp)
            _cleanup_partial_dest(dest, created_dest)
            raise
    except Exception:
        _cleanup_dir(tmp)
        _cleanup_partial_dest(dest, created_dest)
        raise

    try:
        submodule_paths = _detect_submodules(tmp)
        lfs_patterns = _detect_lfs(tmp)

        # strip git history and copy files
        git_dir = tmp / ".git"
        if git_dir.exists():
            shutil.rmtree(git_dir)
        # move contents into dest
        for child in list(tmp.iterdir()):
            shutil.move(str(child), str(dest / child.name))
        _cleanup_dir(tmp)

        manifest_entries, manifest_hash = _build_manifest(dest)
        return CloneResult(
            source=source,
            sha=sha,
            dest_dir=str(dest),
            file_count=len(manifest_entries),
            tree_hash=_hash_tree(dest),
            submodules_detected=bool(submodule_paths),
            submodule_paths=submodule_paths,
            lfs_detected=bool(lfs_patterns),
            lfs_patterns=lfs_patterns,
            manifest_hash=manifest_hash,
            manifest_file_count=len(manifest_entries),
            file_manifest=manifest_entries if include_manifest else None,
        )
    except Exception:
        _cleanup_dir(tmp)
        _cleanup_partial_dest(dest, created_dest)
        raise
