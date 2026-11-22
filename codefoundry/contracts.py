"""Schema loading and validation for WatchCodes contracts.

The JSON Schema is the contract; prose is not. Validation is fail-closed.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_NAMES = {
    "discovery-manifest": "discovery-manifest.schema.json",
    "migration-request": "migration-request.schema.json",
    "repository-mapping": "repository-mapping.schema.json",
    "migration-report": "migration-report.schema.json",
    "upstream-report": "upstream-report.schema.json",
    "batch-readiness-report": "batch-readiness-report.schema.json",
    "common": "common.definitions.json",
}


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


def load_schema(name: str) -> dict[str, Any]:
    if name not in SCHEMA_NAMES:
        raise KeyError(f"unknown schema: {name}")
    path = _schemas_dir() / SCHEMA_NAMES[name]
    return json.loads(path.read_text(encoding="utf-8"))


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


def validator_for(name: str) -> Draft202012Validator:
    schema = load_schema(name)
    return Draft202012Validator(schema, registry=_registry())


def validate(name: str, document: Any) -> None:
    """Validate document against named schema. Raises ValidationError on failure."""
    validator_for(name).validate(document)


def is_valid(name: str, document: Any) -> tuple[bool, list[str]]:
    v = validator_for(name)
    errors = sorted(v.iter_errors(document), key=lambda e: list(e.path))
    if not errors:
        return True, []
    msgs = []
    for e in errors:
        loc = "$" + "".join(f"[{p!r}]" if isinstance(p, int) else f".{p}" for p in e.path)
        msgs.append(f"{loc}: {e.message}")
    return False, msgs
