"""Scan: collect licence + structure evidence for a source repo."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LICENSE_FILENAMES = [
    "LICENSE",
    "LICENSE.md",
    "LICENSE.txt",
    "LICENCE",
    "LICENCE.md",
    "LICENCE.txt",
    "COPYING",
    "COPYING.md",
    "COPYING.txt",
]

SPDX_HINTS = {
    "MIT License": "MIT",
    "Apache License, Version 2.0": "Apache-2.0",
    "GNU GENERAL PUBLIC LICENSE\nVersion 3": "GPL-3.0-only",
    "GNU GENERAL PUBLIC LICENSE\nVersion 2": "GPL-2.0-only",
    "GNU AFFERO GENERAL PUBLIC LICENSE": "AGPL-3.0-only",
    "BSD 3-Clause": "BSD-3-Clause",
    "BSD 2-Clause": "BSD-2-Clause",
    "Mozilla Public License Version 2.0": "MPL-2.0",
}


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
        }


def _detect_license(repo_dir: Path) -> tuple[str | None, str | None]:
    for name in LICENSE_FILENAMES:
        p = repo_dir / name
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace")
            for hint, spdx in SPDX_HINTS.items():
                if hint.lower() in text.lower():
                    return spdx, name
            # fallback: first non-empty line sometimes has SPDX
            for line in text.splitlines():
                line = line.strip()
                if line.upper().startswith("SPDX-License-Identifier:"):
                    return line.split(":", 1)[1].strip(), name
            return "UNKNOWN", name
    return None, None


def scan_repo(repo_dir: str | Path, *, source: str, sha: str) -> ScanResult:
    root = Path(repo_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"repo dir not found: {root}")

    file_count = 0
    top_level = []
    for child in sorted(root.iterdir()):
        if child.name == ".git":
            continue
        top_level.append(child.name)
    for _p in root.rglob("*"):
        if _p.is_file() and ".git" not in _p.parts:
            file_count += 1

    detected_license, license_file = _detect_license(root)
    has_readme = any((root / n).is_file() for n in ("README.md", "README.rst", "README.txt", "README"))
    has_dotgit = (root / ".git").exists()

    return ScanResult(
        source=source,
        sha=sha,
        detected_license=detected_license,
        license_file=license_file,
        file_count=file_count,
        top_level=top_level,
        has_readme=has_readme,
        has_dotgit=has_dotgit,
        evidence={"scanner": "codefoundry.scan.v1"},
    )
