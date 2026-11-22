"""Scan: collect licence + structure evidence for a source repo."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LICENSE_FILENAMES = [
    "LICENSE",
    "LICENSE.md",
    "LICENSE.txt",
    "LICENSE.rst",
    "LICENCE",
    "LICENCE.md",
    "LICENCE.txt",
    "LICENCE.rst",
    "COPYING",
    "COPYING.md",
    "COPYING.txt",
    "COPYING.rst",
    "UNLICENSE",
    "UNLICENSE.md",
    "UNLICENSE.txt",
]

SPDX_HINTS = {
    "MIT License": "MIT",
    "Apache License, Version 2.0": "Apache-2.0",
    "GNU GENERAL PUBLIC LICENSE\nVersion 3": "GPL-3.0-only",
    "GNU GENERAL PUBLIC LICENSE\nVersion 2": "GPL-2.0-only",
    "GNU AFFERO GENERAL PUBLIC LICENSE": "AGPL-3.0-only",
    "GNU LESSER GENERAL PUBLIC LICENSE\nVersion 3": "LGPL-3.0-only",
    "GNU LESSER GENERAL PUBLIC LICENSE\nVersion 2.1": "LGPL-2.1-only",
    "BSD 3-Clause": "BSD-3-Clause",
    "BSD 2-Clause": "BSD-2-Clause",
    "Mozilla Public License Version 2.0": "MPL-2.0",
    "ISC License": "ISC",
    "The Unlicense": "Unlicense",
    "Boost Software License": "BSL-1.0",
}

_LARGE_FILE_THRESHOLD = 1024 * 1024
_SPDX_ID_RE = re.compile(r"SPDX-License-Identifier:\s*(.+)", re.IGNORECASE)


@dataclass
class ScanResult:
    source: str
    sha: str
    detected_license: str | None
    license_file: str | None
    file_count: int
    top_level: list[str]
    has_readme: bool
    has_dotgit: bool
    evidence: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "sha": self.sha,
            "detected_license": self.detected_license,
            "license_file": self.license_file,
            "file_count": self.file_count,
            "top_level": self.top_level,
            "has_readme": self.has_readme,
            "has_dotgit": self.has_dotgit,
            "evidence": self.evidence,
            "warnings": self.warnings,
        }


def _normalize_spdx(value: str) -> str:
    cleaned = value.strip().strip("*").strip()
    # strip trailing comment closers or punctuation noise
    cleaned = re.sub(r"[>\]*/]+$", "", cleaned).strip()
    return cleaned


def _is_dual_spdx_expression(spdx: str) -> bool:
    upper = spdx.upper()
    if " OR " in f" {upper} " or " AND " in f" {upper} ":
        return True
    return False


def _detect_license_in_text(text: str) -> tuple[str | None, str | None, bool]:
    """Return (spdx, detection_kind, dual_signal)."""
    dual_signal = bool(re.search(r"\bdual[- ]licen", text, re.IGNORECASE))

    for line in text.splitlines():
        m = _SPDX_ID_RE.search(line)
        if m:
            spdx = _normalize_spdx(m.group(1))
            if not spdx:
                continue
            upper = spdx.upper()
            if upper == "NOASSERTION":
                return "NOASSERTION", "spdx", dual_signal
            if upper == "NONE":
                return "NOASSERTION", "spdx", dual_signal
            if _is_dual_spdx_expression(spdx):
                return "NOASSERTION", "spdx-expression", True
            return spdx, "spdx", dual_signal

    for hint, spdx in SPDX_HINTS.items():
        if hint.lower() in text.lower():
            return spdx, "hint", dual_signal

    return "UNKNOWN", "unknown", dual_signal


def _detect_license(repo_dir: Path) -> tuple[str | None, str | None]:
    detected, license_file, _, _, _ = _detect_licenses(repo_dir)
    return detected, license_file


def _detect_licenses(repo_dir: Path) -> tuple[str | None, str | None, list[str], list[str], dict[str, Any]]:
    warnings: list[str] = []
    evidence: dict[str, Any] = {}
    found_files: list[str] = []
    detections: list[dict[str, Any]] = []

    for name in LICENSE_FILENAMES:
        p = repo_dir / name
        if p.is_file():
            found_files.append(name)
            text = p.read_text(encoding="utf-8", errors="replace")
            spdx, kind, dual_signal = _detect_license_in_text(text)
            detections.append(
                {
                    "file": name,
                    "detected": spdx,
                    "kind": kind,
                    "dual_signal": dual_signal,
                }
            )

    evidence["license_files"] = list(found_files)
    evidence["license_detections"] = detections

    if not detections:
        return None, None, warnings, [], evidence

    dual_signal = any(d.get("dual_signal") for d in detections)
    spdx_values = [d.get("detected") for d in detections if d.get("detected")]

    if len(found_files) > 1:
        warnings.append(f"multiple license files detected: {', '.join(found_files)}")

    distinct = {v for v in spdx_values if v not in {None}}
    distinct_non_unknown = {v for v in distinct if v not in {"UNKNOWN", "NOASSERTION"}}

    if dual_signal:
        evidence["dual_license_signal"] = True
        warnings.append("dual-license signal detected")

    # Fail closed on ambiguity: multiple different concrete licenses or dual signal.
    if len(found_files) > 1 and len(distinct_non_unknown) > 1:
        evidence["dual_license_signal"] = True
        warnings.append("conflicting license detections across files")
        return "NOASSERTION", found_files[0], warnings, found_files, evidence

    if dual_signal and any(_is_dual_spdx_expression(str(d.get("detected"))) or d.get("kind") == "spdx-expression" for d in detections):
        return "NOASSERTION", found_files[0], warnings, found_files, evidence

    # Single-file dual signal with a concrete license is still ambiguous.
    if dual_signal and len(distinct_non_unknown) == 1 and any(d.get("dual_signal") for d in detections if d.get("detected") in distinct_non_unknown):
        return "NOASSERTION", found_files[0], warnings, found_files, evidence

    # If we have multiple files but same detection, keep it.
    first = detections[0]
    detected = first.get("detected")
    license_file = first.get("file")

    # If first is UNKNOWN but another is concrete, prefer concrete unless conflicting.
    if detected == "UNKNOWN":
        concrete = [d for d in detections if d.get("detected") not in {None, "UNKNOWN", "NOASSERTION"}]
        if len({d.get("detected") for d in concrete}) == 1:
            detected = concrete[0].get("detected")
            license_file = concrete[0].get("file")

    # If any NOASSERTION explicitly present, keep fail-closed.
    if any(d.get("detected") == "NOASSERTION" for d in detections):
        return "NOASSERTION", license_file, warnings, found_files, evidence

    if detected not in {None, "UNKNOWN", "NOASSERTION"} and dual_signal:
        # explicit dual text in an otherwise concrete file - fail closed
        return "NOASSERTION", license_file, warnings, found_files, evidence

    return detected, license_file, warnings, found_files, evidence


def _is_binary_file(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            chunk = f.read(4096)
    except OSError:
        return False
    if not chunk:
        return False
    if b"\x00" in chunk:
        # text files may contain null rarely; treat as binary signal
        # but avoid classifying obvious LFS pointers as binary? they are text
        if chunk.startswith(b"version https://git-lfs.github.com/spec/v1"):
            return False
        return True
    return False


def _is_lfs_pointer(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            chunk = f.read(256)
    except OSError:
        return False
    return chunk.startswith(b"version https://git-lfs.github.com/spec/v1")


def _collect_structure_evidence(root: Path) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    gitmodules = root / ".gitmodules"
    has_gitmodules = gitmodules.is_file()
    evidence["has_gitmodules"] = has_gitmodules
    submodule_count = 0
    if has_gitmodules:
        try:
            text = gitmodules.read_text(encoding="utf-8", errors="replace")
            submodule_count = len(re.findall(r"^\s*\[submodule\s+\"[^\"]+\"\]", text, re.MULTILINE))
        except OSError:
            submodule_count = 0
    evidence["submodule_count"] = submodule_count

    symlink_paths: list[str] = []
    lfs_pointer_files: list[str] = []
    binary_files: list[str] = []
    large_files: list[dict[str, Any]] = []
    file_count = 0

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # prune .git
        dirnames[:] = [d for d in dirnames if d != ".git"]
        rel_dir = Path(dirpath).relative_to(root)

        # detect symlinked dirs (reported but not walked)
        for d in list(dirnames):
            full = Path(dirpath) / d
            if full.is_symlink():
                symlink_paths.append((rel_dir / d).as_posix())
                dirnames.remove(d)

        for name in filenames:
            full = Path(dirpath) / name
            rel = (rel_dir / name).as_posix()
            if full.is_symlink():
                symlink_paths.append(rel)
                # preserve file_count semantics: symlinks to files counted as files
                if full.is_file():
                    file_count += 1
                continue
            if not full.is_file():
                continue
            file_count += 1
            try:
                size = full.stat().st_size
            except OSError:
                size = 0
            if size > _LARGE_FILE_THRESHOLD:
                large_files.append({"path": rel, "size": size})
            if _is_lfs_pointer(full):
                lfs_pointer_files.append(rel)
                continue
            if _is_binary_file(full):
                binary_files.append(rel)

    evidence["symlink_count"] = len(symlink_paths)
    evidence["symlink_paths"] = sorted(symlink_paths)
    evidence["lfs_pointer_count"] = len(lfs_pointer_files)
    evidence["lfs_pointer_files"] = sorted(lfs_pointer_files)
    evidence["binary_file_count"] = len(binary_files)
    evidence["binary_files"] = sorted(binary_files)
    evidence["large_file_count"] = len(large_files)
    evidence["large_files"] = sorted(large_files, key=lambda x: x["path"])
    evidence["scanned_file_count"] = file_count
    return evidence


def scan_repo(repo_dir: str | Path, *, source: str, sha: str) -> ScanResult:
    root = Path(repo_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"repo dir not found: {root}")

    top_level = []
    for child in sorted(root.iterdir()):
        if child.name == ".git":
            continue
        top_level.append(child.name)

    structure_evidence = _collect_structure_evidence(root)
    file_count = int(structure_evidence.get("scanned_file_count", 0))

    detected_license, license_file, license_warnings, _, license_evidence = _detect_licenses(root)
    has_readme = any((root / n).is_file() for n in ("README.md", "README.rst", "README.txt", "README"))
    has_dotgit = (root / ".git").exists()

    evidence: dict[str, Any] = {"scanner": "codefoundry.scan.v1"}
    evidence.update(structure_evidence)
    evidence.update(license_evidence)

    return ScanResult(
        source=source,
        sha=sha,
        detected_license=detected_license,
        license_file=license_file,
        file_count=file_count,
        top_level=top_level,
        has_readme=has_readme,
        has_dotgit=has_dotgit,
        evidence=evidence,
        warnings=license_warnings,
    )
