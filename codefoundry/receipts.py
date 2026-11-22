"""Idempotency receipts for batch_id + source."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ReceiptStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, batch_id: str, source: str) -> Path:
        safe = source.replace("/", "__")
        return self.root / f"{batch_id}__{safe}.json"

    def get(self, batch_id: str, source: str) -> dict[str, Any] | None:
        p = self._path(batch_id, source)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def put(self, batch_id: str, source: str, receipt: dict[str, Any]) -> Path:
        p = self._path(batch_id, source)
        p.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return p
