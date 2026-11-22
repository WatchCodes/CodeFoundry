"""Branding wrapper: delegates to the pinned 100Ways engine.

Non-negotiable: no token rules or brand-scan logic are reimplemented here.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine_ref": self.engine_ref,
            "files_changed": self.files_changed,
            "replacements": self.replacements,
            "scan_passed": self.scan_passed,
            "scan_violations": self.scan_violations,
            "log": self.log,
        }


def _ensure_100ways_importable(engine_path: str | Path | None = None) -> Path:
    candidates = []
    if engine_path:
        candidates.append(Path(engine_path))
    env = os.environ.get("CODEFOUNDRY_100WAYS_PATH")
    if env:
        candidates.append(Path(env))
    candidates.append(Path("/home/nascode/work/100ways"))
    for c in candidates:
        if (c / "hundredways" / "rules.py").is_file():
            if str(c) not in sys.path:
                sys.path.insert(0, str(c))
            return c
    raise BrandError(
        "100Ways engine not found. Set CODEFOUNDRY_100WAYS_PATH or install 100ways."
    )


def _engine_ref(engine_root: Path) -> str:
    try:
        p = subprocess.run(
            ["git", "-C", str(engine_root), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=True,
        )
        return p.stdout.strip()
    except Exception:
        return "unknown"


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

    from hundredways import brandscan  # type: ignore
    from hundredways.rules import BrandingRules  # type: ignore

    rules = BrandingRules()

    changed_files = 0
    replacements = 0
    logs: list[str] = []

    # Path transforms: deepest first to avoid moving parents before children.
    all_paths = sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True)
    for p in all_paths:
        if ".git" in p.parts:
            continue
        rel = p.relative_to(root).as_posix()
        new_rel = rules.transform_path(rel)
        if new_rel != rel and not dry_run:
            target = root / new_rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if p.exists():
                p.rename(target)
                logs.append(f"rename: {rel} -> {new_rel}")

    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if ".git" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        new_text = rules.transform_text(text)
        if new_text != text:
            old_lines = text.splitlines()
            new_lines = new_text.splitlines()
            count = sum(1 for a, b in zip(old_lines, new_lines) if a != b) + abs(len(old_lines) - len(new_lines))
            count = max(1, count)
            changed_files += 1
            replacements += count
            logs.append(f"{path.relative_to(root)}: {count}")
            if not dry_run:
                path.write_text(new_text, encoding="utf-8")

    scan = brandscan.scan_brand(str(root))
    raw_violations = scan.violations() if callable(getattr(scan, "violations", None)) else getattr(scan, "violations", [])
    violations = [f"{getattr(v,'path','?')}:{getattr(v,'line','?')}: {getattr(v,'text',str(v))}" for v in raw_violations]
    clean_attr = getattr(scan, "clean", None)
    passed = clean_attr() if callable(clean_attr) else bool(clean_attr) if clean_attr is not None else len(violations) == 0
    return BrandResult(
        engine_ref=ref,
        files_changed=changed_files,
        replacements=replacements,
        scan_passed=passed,
        scan_violations=violations,
        log="\n".join(logs),
    )
