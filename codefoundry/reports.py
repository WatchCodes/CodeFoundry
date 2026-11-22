"""Append-only report storage helpers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_report(report: dict[str, Any], reports_dir: str | Path) -> Path:
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    batch = report.get("batch_id", "unknown-batch")
    source = str(report.get("source", "unknown")).replace("/", "__")
    p = reports_dir / f"{batch}__{source}.json"
    # append-only: never overwrite an existing terminal report with different content silently
    if p.exists():
        existing = json.loads(p.read_text(encoding="utf-8"))
        if existing == report:
            return p
        p = reports_dir / f"{batch}__{source}__{report.get('receipt',{}).get('destination_sha','new')}.json"
    p.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return p
