"""Attribution artifacts: CREDITS.md and NOTICE handling.

Policy: LICENSE stays verbatim; attribution lines are idempotent and append-only.
"""
from __future__ import annotations

from pathlib import Path


def _normalize_line(line: str) -> str:
    return line.strip()


def _append_line(path: Path, line: str, *, header: str | None = None) -> Path:
    """Append a single line idempotently, preserving existing content."""
    normalized = _normalize_line(line)
    if path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        if normalized in text:
            return path
        if text and not text.endswith("\n"):
            text += "\n"
        path.write_text(text + line, encoding="utf-8")
        return path
    content = (header or "") + line
    path.write_text(content, encoding="utf-8")
    return path


def ensure_credits(repo_dir: str | Path, *, source: str, sha: str, license_spdx: str) -> Path:
    root = Path(repo_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"repo_dir not found: {root}")
    credits = root / "CREDITS.md"
    line = f"- Derived from {source} at {sha} ({license_spdx}).\n"
    header = "# Credits\n\nThis repository includes derived work from upstream sources.\n\n"
    return _append_line(credits, line, header=header)


def ensure_notice(repo_dir: str | Path, *, source: str, sha: str) -> Path:
    root = Path(repo_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"repo_dir not found: {root}")
    notice = root / "NOTICE"
    banner = f"Derived from {source} at {sha}.\n"
    # Append-only: never prepend or rewrite existing NOTICE content, never touch LICENSE.
    return _append_line(notice, banner)
