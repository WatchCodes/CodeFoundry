"""Minimal CLI for hand-driven CodeFoundry slice."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts import is_valid, load_schema
from .pipeline import run_slice


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="codefoundry")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="Validate a JSON document against a contract")
    v.add_argument("schema", choices=["discovery-manifest", "migration-request", "repository-mapping", "migration-report", "upstream-report", "batch-readiness-report"])
    v.add_argument("file")

    r = sub.add_parser("run", help="Run hand-driven migration slice")
    r.add_argument("request", help="MigrationRequest JSON file")
    r.add_argument("--workdir")
    r.add_argument("--engine-path")
    r.add_argument("--dry-run", action="store_true", default=True)
    r.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    r.add_argument("--push", action="store_true", default=False)
    r.add_argument("--clone-source", default=None)

    a = p.parse_args(argv)
    if a.cmd == "validate":
        doc = json.loads(Path(a.file).read_text(encoding="utf-8"))
        ok, errors = is_valid(a.schema, doc)
        if ok:
            print("OK")
            return 0
        for e in errors:
            print(e)
        return 1
    if a.cmd == "run":
        req = json.loads(Path(a.request).read_text(encoding="utf-8"))
        report = run_slice(req, workdir=a.workdir, engine_path=a.engine_path, dry_run=a.dry_run, push=a.push, clone_source=a.clone_source)
        print(json.dumps(report, indent=2))
        return 0 if report.get("status") == "VERIFIED" else 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
