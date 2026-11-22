"""Branding wrapper: delegates to the pinned 100Ways engine.

Non-negotiable: no token rules or brand-scan logic are reimplemented here.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class BrandError(RuntimeError):
    pass


@dataclass
class BrandResult:
    engine_ref: str
    files_changed: int
    replacements: int
    scan_passed: bool
    scan_violations: list[str]
    log: str
    changed_paths: list[str] = field(default_factory=list)
    engine_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine_ref": self.engine_ref,
            "engine_path": self.engine_path,
            "files_changed": self.files_changed,
            "changed_paths": self.changed_paths,
            "changed_files": self.changed_paths,
            "replacements": self.replacements,
            "scan_passed": self.scan_passed,
            "scan_violations": self.scan_violations,
            "violations": self.scan_violations,
            "log": self.log,
        }


def _ensure_100ways_importable(engine_path: str | Path | None = None) -> Path:
    candidates: list[Path] = []
    if engine_path:
        candidates.append(Path(engine_path))
    env = os.environ.get("CODEFOUNDRY_100WAYS_PATH")
    if env:
        candidates.append(Path(env))
    candidates.append(Path("/home/nascode/work/100ways"))
    seen: set[str] = set()
    for c in candidates:
        key = str(c)
        if key in seen:
            continue
        seen.add(key)
        if (c / "hundredways" / "rules.py").is_file():
            resolved = c.resolve()
            if str(resolved) not in sys.path:
                sys.path.insert(0, str(resolved))
            return resolved
    raise BrandError(
        "100Ways engine not found. Set CODEFOUNDRY_100WAYS_PATH or pass engine_path "
        "pointing to a checkout containing hundredways/rules.py."
    )


def _engine_ref(engine_root: Path) -> str:
    try:
        p = subprocess.run(
            ["git", "-C", str(engine_root), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=True,
            timeout=10,
        )
        ref = p.stdout.strip()
        return ref or "unknown"
    except Exception as e:
        return f"unknown ({type(e).__name__})"


def _is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            chunk = fh.read(8192)
    except OSError:
        return True
    return b"\x00" in chunk


def _read_text_safe(path: Path) -> str | None:
    """Return UTF-8 text for a text file, or None for binary/unreadable files."""
    if _is_binary(path):
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def _format_violation(v: Any) -> str:
    path = getattr(v, "path", "?")
    line = getattr(v, "line", "?")
    kind = getattr(v, "kind", None)
    word = getattr(v, "word", None)
    run = getattr(v, "run", None)
    sample = getattr(v, "sample", None)
    if word is not None or run is not None:
        parts = [f"{path}:{line}"]
        if kind:
            parts.append(f"[{kind}]")
        if word is not None:
            parts.append(f"word={word!r}")
        if run is not None:
            parts.append(f"run={run!r}")
        if sample:
            parts.append(f"sample={sample!r}")
        return " ".join(parts)
    text = getattr(v, "text", None)
    if text is not None:
        return f"{path}:{line}: {text}"
    return f"{path}:{line}: {v!s}"


def _scan_violations(scan: Any) -> list[str]:
    raw = getattr(scan, "violations", [])
    if callable(raw):
        raw = raw()
    if raw is None:
        return []
    try:
        items = list(raw)
    except TypeError:
        return [f"<unreadable violations: {type(raw).__name__}>"]
    return [_format_violation(v) for v in items]


def _scan_passed(scan: Any, violations: list[str]) -> bool:
    clean_attr = getattr(scan, "clean", None)
    if callable(clean_attr):
        try:
            return bool(clean_attr())
        except Exception:
            pass
    elif clean_attr is not None:
        try:
            return bool(clean_attr)
        except Exception:
            pass
    is_clean = getattr(scan, "is_clean", None)
    if callable(is_clean):
        try:
            return bool(is_clean())
        except Exception:
            pass
    return len(violations) == 0


def run_brand(
    repo_dir: str | Path,
    *,
    engine_path: str | Path | None = None,
    dry_run: bool = False,
) -> BrandResult:
    root = Path(repo_dir)
    if not root.is_dir():
        raise BrandError(f"repo_dir not found: {root}")

    engine_root = _ensure_100ways_importable(engine_path)
    ref = _engine_ref(engine_root)

    try:
        from hundredways import brandscan  # type: ignore
        from hundredways.rules import BrandingRules  # type: ignore
    except Exception as e:
        raise BrandError(f"failed to import 100Ways engine from {engine_root}: {e}") from e

    try:
        rules = BrandingRules()
    except Exception as e:
        raise BrandError(f"failed to initialize BrandingRules: {e}") from e

    changed_paths: list[str] = []
    replacements = 0
    logs: list[str] = []

    # Path transforms: deepest first to avoid moving parents before children.
    # In dry_run, compute and record intended renames without mutating the filesystem.
    all_paths = sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True)
    planned_renames: list[tuple[Path, Path, str, str]] = []
    for p in all_paths:
        if ".git" in p.parts:
            continue
        try:
            rel = p.relative_to(root).as_posix()
        except Exception:
            continue
        try:
            new_rel = rules.transform_path(rel)
        except Exception as e:
            logs.append(f"rename-error: {rel}: {e}")
            continue
        if new_rel != rel:
            target = root / new_rel
            if dry_run:
                logs.append(f"rename(dry-run): {rel} -> {new_rel}")
                planned_renames.append((p, target, rel, new_rel))
            else:
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if p.exists():
                        p.rename(target)
                        logs.append(f"rename: {rel} -> {new_rel}")
                        planned_renames.append((p, target, rel, new_rel))
                except Exception as e:
                    raise BrandError(f"failed to rename {rel} -> {new_rel}: {e}") from e

    # Build a lookup for renamed paths so content pass can report intended paths in dry_run.
    rename_map = {src: dst for src, dst, _, _ in planned_renames}

    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if ".git" in path.parts:
            continue
        rel_path = path
        try:
            rel = rel_path.relative_to(root).as_posix()
        except Exception:
            continue

        text = _read_text_safe(path)
        if text is None:
            continue

        try:
            new_text = rules.transform_text(text)
        except Exception as e:
            logs.append(f"transform-error: {rel}: {e}")
            continue

        if new_text != text:
            old_lines = text.splitlines()
            new_lines = new_text.splitlines()
            count = sum(1 for a, b in zip(old_lines, new_lines) if a != b) + abs(
                len(old_lines) - len(new_lines)
            )
            count = max(1, count)
            changed_paths.append(rel)
            replacements += count
            logs.append(f"{rel}: {count}")
            if not dry_run:
                try:
                    path.write_text(new_text, encoding="utf-8")
                except Exception as e:
                    raise BrandError(f"failed to write transformed file {rel}: {e}") from e

    # In dry_run, also report files whose path would change but content may not.
    # They are rename evidence; don't double-count as content changes.
    for _, _, old_rel, new_rel in planned_renames:
        if old_rel not in changed_paths:
            changed_paths.append(old_rel)
            logs.append(f"path-change: {old_rel} -> {new_rel}")

    try:
        scan = brandscan.scan_brand(str(root))
    except Exception as e:
        raise BrandError(f"brand scan failed: {e}") from e

    violations = _scan_violations(scan)
    passed = _scan_passed(scan, violations)

    # De-duplicate changed paths while preserving order.
    seen_paths: set[str] = set()
    deduped_paths: list[str] = []
    for pth in changed_paths:
        if pth not in seen_paths:
            seen_paths.add(pth)
            deduped_paths.append(pth)

    return BrandResult(
        engine_ref=ref,
        engine_path=str(engine_root),
        files_changed=len(deduped_paths),
        replacements=replacements,
        scan_passed=passed,
        scan_violations=violations,
        log="\n".join(logs),
        changed_paths=deduped_paths,
    )
