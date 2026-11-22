"""Minimal CLI for hand-driven CodeFoundry slice."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


class CLIArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that uses exit code 1 for usage/validation errors."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def _emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _load_json_file(path_str: str, *, label: str) -> dict[str, Any]:
    path = Path(path_str)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise ValueError(f"{label} not found: {path}") from e
    except OSError as e:
        raise ValueError(f"{label} cannot be read: {path}: {e}") from e
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{label} is not valid JSON: {path}: {e}") from e
    if not isinstance(doc, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return doc


def _write_output(path_str: str, report: dict[str, Any]) -> None:
    path = Path(path_str)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as e:
        raise ValueError(f"output cannot be written: {path}: {e}") from e


def _run_validate(args: argparse.Namespace) -> int:
    from .contracts import is_valid

    try:
        doc = _load_json_file(args.file, label="file")
    except ValueError as e:
        if args.json:
            _emit_json({"ok": False, "schema": args.schema, "file": args.file, "errors": [str(e)]})
        else:
            print(f"error: {e}", file=sys.stderr)
        return 1

    ok, errors = is_valid(args.schema, doc)
    if args.json:
        payload: dict[str, Any] = {"ok": ok, "schema": args.schema, "file": args.file}
        if not ok:
            payload["errors"] = errors
        _emit_json(payload)
    else:
        if ok:
            print("OK")
        else:
            for e in errors:
                print(e, file=sys.stderr)
    return 0 if ok else 1


def _run_run(args: argparse.Namespace) -> int:
    from .pipeline import run_slice

    try:
        req = _load_json_file(args.request, label="request")
    except ValueError as e:
        if args.json:
            _emit_json({"ok": False, "error": str(e), "status": "INVALID"})
        else:
            print(f"error: {e}", file=sys.stderr)
        return 1

    if args.reports_branch_push and not args.reports_branch_repo:
        msg = "--reports-branch-push requires --reports-branch-repo"
        if args.json:
            _emit_json({"ok": False, "error": msg, "status": "INVALID"})
        else:
            print(f"error: {msg}", file=sys.stderr)
        return 1

    try:
        report = run_slice(
            req,
            workdir=args.workdir,
            engine_path=args.engine_path,
            dry_run=args.dry_run,
            push=args.push,
            clone_source=args.clone_source,
            receipts_dir=args.receipts_dir,
            reports_dir=args.reports_dir,
        )
    except Exception as e:
        msg = f"run failed: {e}"
        if args.json:
            _emit_json({"ok": False, "error": msg, "status": "FAILED"})
        else:
            print(f"error: {msg}", file=sys.stderr)
        return 2

    if args.reports_branch_repo:
        try:
            from .reports import write_report_to_branch

            write_report_to_branch(
                report,
                repo_dir=args.reports_branch_repo,
                branch=args.reports_branch,
                push=args.reports_branch_push and not args.dry_run,
            )
        except Exception as e:
            msg = f"reports branch write failed: {e}"
            if args.json:
                _emit_json({"ok": False, "error": msg, "status": "FAILED", "report": report})
            else:
                print(f"error: {msg}", file=sys.stderr)
            return 2

    if args.output:
        try:
            _write_output(args.output, report)
        except ValueError as e:
            if args.json:
                _emit_json({"ok": False, "error": str(e), "status": "FAILED", "report": report})
            else:
                print(f"error: {e}", file=sys.stderr)
            return 2

    print(json.dumps(report, indent=2, sort_keys=True))
    status = report.get("status")
    return 0 if status == "VERIFIED" else 2


def build_parser() -> CLIArgumentParser:
    p = CLIArgumentParser(prog="codefoundry")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="Validate a JSON document against a contract")
    v.add_argument(
        "schema",
        choices=[
            "discovery-manifest",
            "migration-request",
            "repository-mapping",
            "migration-report",
            "upstream-report",
            "batch-readiness-report",
        ],
    )
    v.add_argument("file")
    v.add_argument("--json", action="store_true", default=False, help="Emit JSON output")
    v.set_defaults(func=_run_validate)

    r = sub.add_parser("run", help="Run hand-driven migration slice")
    r.add_argument("request", help="MigrationRequest JSON file")
    r.add_argument("--workdir")
    r.add_argument("--engine-path")
    r.add_argument("--dry-run", action="store_true", default=True)
    r.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    r.add_argument("--push", action="store_true", default=False)
    r.add_argument("--clone-source", default=None)
    r.add_argument("--receipts-dir", default=None)
    r.add_argument("--reports-dir", default=None)
    r.add_argument("--reports-branch-repo", default=None, help="Git repo for append-only reports branch")
    r.add_argument("--reports-branch", default="codefoundry/reports")
    r.add_argument("--reports-branch-push", action="store_true", default=False)
    r.add_argument("--output", default=None, help="Write report JSON to file")
    r.add_argument("--json", action="store_true", default=False, help="Emit JSON errors")
    r.set_defaults(func=_run_run)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        raise SystemExit(code) from e
    func = getattr(args, "func", None)
    if func is None:
        parser.error("command required")
    return int(func(args))


if __name__ == "__main__":
    raise SystemExit(main())
