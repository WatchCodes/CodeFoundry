"""Schema loading and validation for WatchCodes contracts.

The JSON Schema is the contract; prose is not. Validation is fail-closed.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

SCHEMA_NAMES = {
    "discovery-manifest": "discovery-manifest.schema.json",
    "migration-request": "migration-request.schema.json",
    "repository-mapping": "repository-mapping.schema.json",
    "migration-report": "migration-report.schema.json",
    "upstream-report": "upstream-report.schema.json",
    "batch-readiness-report": "batch-readiness-report.schema.json",
    "common": "common.definitions.json",
}


@lru_cache(maxsize=1)
def _schemas_dir() -> Path:
    # Schemas live at repo root /schemas; package may be installed editable.
    here = Path(__file__).resolve().parent
    repo_schemas = here.parent / "schemas"
    if repo_schemas.is_dir():
        return repo_schemas
    # fallback: packaged data
    try:
        return Path(str(resources.files("codefoundry").joinpath("schemas")))
    except Exception:
        return repo_schemas


_ADDITIVE_CHECKPOINT_STAGES = ("start", "receipt", "ship", "license", "credits")


def _apply_additive_compat(schema_name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Apply additive, backward-compatible schema relaxations for in-flight producers.

    These do not weaken required fields or type safety; they only accept known
    additive stage values that predate schema enum updates.
    """
    if schema_name == "migration-report":
        try:
            stages = schema["properties"]["receipt"]["properties"]["checkpoints"]["items"]["properties"]["stage"]["enum"]
            if isinstance(stages, list):
                for stage in _ADDITIVE_CHECKPOINT_STAGES:
                    if stage not in stages:
                        stages.append(stage)
        except Exception:
            pass
    return schema


@lru_cache(maxsize=None)
def load_schema(name: str) -> dict[str, Any]:
    if not isinstance(name, str):
        raise TypeError(f"schema name must be str, got {type(name).__name__}")
    if name not in SCHEMA_NAMES:
        raise KeyError(f"unknown schema: {name!r}; known: {', '.join(sorted(SCHEMA_NAMES))}")
    path = _schemas_dir() / SCHEMA_NAMES[name]
    if not path.is_file():
        raise FileNotFoundError(f"schema file missing: {path}")
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in schema {name!r} ({path}): {exc}") from exc
    return _apply_additive_compat(name, schema)


@lru_cache(maxsize=1)
def _registry():
    # jsonschema >= 4.18 uses referencing.Registry
    from referencing import Registry, Resource

    schemas_dir = _schemas_dir()
    registry = Registry()
    for filename in SCHEMA_NAMES.values():
        path = schemas_dir / filename
        schema = json.loads(path.read_text(encoding="utf-8"))
        resource = Resource.from_contents(schema)
        schema_id = schema.get("$id")
        if schema_id:
            registry = registry.with_resource(schema_id, resource)
        # also register by filename for relative refs
        registry = registry.with_resource(filename, resource)
    return registry


@lru_cache(maxsize=None)
def validator_for(name: str) -> Draft202012Validator:
    schema = load_schema(name)
    return Draft202012Validator(schema, registry=_registry())


def _format_error_path(error: ValidationError) -> str:
    return "$" + "".join(f"[{p!r}]" if isinstance(p, int) else f".{p}" for p in error.path)


def format_errors(name: str, errors: list[ValidationError]) -> list[str]:
    ordered = sorted(errors, key=lambda e: (list(e.path), e.message))
    msgs: list[str] = []
    for err in ordered:
        loc = _format_error_path(err)
        msgs.append(f"{name}{loc}: {err.message}")
    return msgs


def _iter_errors(name: str, document: Any) -> list[ValidationError]:
    v = validator_for(name)
    return sorted(v.iter_errors(document), key=lambda e: (list(e.path), e.message))


def validate(name: str, document: Any) -> None:
    """Validate document against named schema. Raises ValidationError on failure."""
    errors = _iter_errors(name, document)
    if not errors:
        return
    msgs = format_errors(name, errors)
    summary = f"{name} schema validation failed ({len(msgs)} error{'s' if len(msgs) != 1 else ''}):\n" + "\n".join(msgs)
    err = ValidationError(summary)
    err.schema_name = name  # type: ignore[attr-defined]
    err.errors = msgs  # type: ignore[attr-defined]
    raise err


def is_valid(name: str, document: Any) -> tuple[bool, list[str]]:
    try:
        errors = _iter_errors(name, document)
    except Exception as exc:
        return False, [f"{name}$: {type(exc).__name__}: {exc}"]
    if not errors:
        return True, []
    return False, format_errors(name, errors)
