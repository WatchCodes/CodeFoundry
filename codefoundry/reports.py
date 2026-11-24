"""Append-only report storage helpers.

Reports are immutable records. Writing the same report is idempotent. Writing a
different report for the same batch/source creates a new append-only file keyed by
destination SHA; existing files are never overwritten.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


class ReportWriteError(RuntimeError):
    pass


DEFAULT_COMMAND_TIMEOUT = 180


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = DEFAULT_COMMAND_TIMEOUT,
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Run one git command with a hard deadline and deterministic text output."""
    kwargs.pop("text", None)
    kwargs.pop("capture_output", None)
    try:
        return subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, text=True, capture_output=True, timeout=timeout,
            **kwargs,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReportWriteError(f"command timed out after {timeout}s: {' '.join(cmd)}") from exc
    except OSError as exc:
        raise ReportWriteError(f"failed to execute {' '.join(cmd)}: {exc}") from exc


def _normalize_for_compare(report: dict[str, Any]) -> dict[str, Any]:
    """Normalize volatile fields for idempotent comparison."""
    import copy

    r = copy.deepcopy(report)
    receipt = r.get("receipt")
    if isinstance(receipt, dict):
        cps = receipt.get("checkpoints")
        if isinstance(cps, list):
            for cp in cps:
                if isinstance(cp, dict):
                    cp.pop("at", None)
    return r


def _same_logical_report(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return _normalize_for_compare(a) == _normalize_for_compare(b)


def _report_path(reports_dir: Path, report: dict[str, Any]) -> Path:
    batch = str(report.get("batch_id", "unknown-batch"))
    source = str(report.get("source", "unknown")).replace("/", "__")
    base = reports_dir / f"{batch}__{source}.json"
    if not base.exists():
        return base
    existing = json.loads(base.read_text(encoding="utf-8"))
    if _same_logical_report(existing, report):
        return base
    dest_sha = str(report.get("receipt", {}).get("destination_sha", "new"))
    status = str(report.get("status", "unknown")).lower()
    return reports_dir / f"{batch}__{source}__{dest_sha}__{status}.json"


def write_report(report: dict[str, Any], reports_dir: str | Path) -> Path:
    """Write report as an append-only JSON record. Idempotent for logically identical content."""
    root = Path(reports_dir)
    root.mkdir(parents=True, exist_ok=True)
    p = _report_path(root, report)
    if p.exists():
        existing = json.loads(p.read_text(encoding="utf-8"))
        if _same_logical_report(existing, report):
            return p
        # extremely unlikely hash/status collision: add numeric suffix
        i = 2
        while True:
            candidate = p.with_name(p.stem + f"__{i}" + p.suffix)
            if not candidate.exists():
                p = candidate
                break
            if _same_logical_report(json.loads(candidate.read_text(encoding="utf-8")), report):
                return candidate
            i += 1
    p.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return p


def write_report_to_branch(
    report: dict[str, Any],
    *,
    repo_dir: str | Path,
    branch: str = "codefoundry/reports",
    reports_subdir: str = "reports",
    remote: str = "origin",
    push: bool = False,
) -> Path:
    """Append a report file in a git worktree branch.

    This is append-only: it never rewrites existing report files with different content.
    When push=False, it commits locally only (no external write).
    """
    root = Path(repo_dir)
    if not (root / ".git").exists():
        raise ReportWriteError(f"not a git repo: {root}")

    # ensure local branch exists
    branch_exists = _run(["git", "rev-parse", "--verify", branch], cwd=root, text=True, capture_output=True).returncode == 0
    if not branch_exists:
        # create orphan-ish branch from current HEAD without changing files; use branch from current
        _run(["git", "branch", branch], cwd=root, text=True, capture_output=True)

    # use a temporary worktree to avoid disturbing current checkout
    import tempfile

    with tempfile.TemporaryDirectory(prefix="cf-reports-") as td:
        wt = Path(td) / "wt"
        add = _run(["git", "worktree", "add", str(wt), branch], cwd=root, text=True, capture_output=True)
        if add.returncode != 0:
            raise ReportWriteError(f"git worktree add failed: {add.stdout}\n{add.stderr}")
        try:
            out_dir = wt / reports_subdir
            p = write_report(report, out_dir)
            rel = p.relative_to(wt).as_posix()
            _run(["git", "add", rel], cwd=wt, check=True, capture_output=True, text=True)
            diff = _run(["git", "diff", "--cached", "--quiet"], cwd=wt)
            if diff.returncode != 0:
                batch = report.get("batch_id", "unknown")
                source = str(report.get("source", "unknown")).replace("/", "__")
                status = report.get("status", "unknown")
                msg = f"report: {batch} {source} {status}"
                _run(["git", "commit", "-m", msg], cwd=wt, check=True, capture_output=True, text=True)
                if push:
                    _run(["git", "push", remote, f"{branch}:{branch}"], cwd=wt, check=True, capture_output=True, text=True)
            return root / reports_subdir / p.name
        finally:
            _run(["git", "worktree", "remove", "--force", str(wt)], cwd=root, text=True, capture_output=True)

