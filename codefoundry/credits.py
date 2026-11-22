"""Attribution artifacts: CREDITS.md and NOTICE handling.

Policy: LICENSE stays verbatim; one attribution line per upstream in CREDITS.md.
"""
from __future__ import annotations

from pathlib import Path


def ensure_credits(repo_dir: str | Path, *, source: str, sha: str, license_spdx: str) -> Path:
    root = Path(repo_dir)
    credits = root / "CREDITS.md"
    line = f"- Derived from {source} at {sha} ({license_spdx}).\n"
    header = "# Credits\n\nThis repository includes derived work from upstream sources.\n\n"
    if credits.exists():
        text = credits.read_text(encoding="utf-8", errors="replace")
        if line.strip() in text:
            return credits
        if not text.endswith("\n"):
            text += "\n"
        credits.write_text(text + line, encoding="utf-8")
        return credits
    credits.write_text(header + line, encoding="utf-8")
    return credits


def ensure_notice(repo_dir: str | Path, *, source: str, sha: str) -> Path:
    root = Path(repo_dir)
    notice = root / "NOTICE"
    banner = f"Derived from {source} at {sha}.\n"
    if notice.exists():
        text = notice.read_text(encoding="utf-8", errors="replace")
        if banner.strip() in text:
            return notice
        notice.write_text(banner + "\n" + text, encoding="utf-8")
        return notice
    notice.write_text(banner, encoding="utf-8")
    return notice
