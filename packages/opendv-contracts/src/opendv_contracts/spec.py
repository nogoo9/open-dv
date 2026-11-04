"""CLI specification loader and validator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema


def _get_schemas_dir() -> Path:
    """Return the schemas directory inside opendv-contracts package."""
    # From opendv_contracts/spec.py: parent is src/opendv_contracts,
    # parent.parent is src, parent.parent.parent is package root
    pkg_root = Path(__file__).resolve().parent.parent.parent
    schemas_dir = pkg_root / "schemas"
    if schemas_dir.is_dir():
        return schemas_dir
    # Fallback to co-located schemas directory if installed as wheel with package data
    colocated = Path(__file__).resolve().parent / "schemas"
    if colocated.is_dir():
        return colocated
    raise FileNotFoundError(
        f"Could not locate schemas directory in {pkg_root} or {Path(__file__).parent}"
    )


def load_cli_spec() -> dict[str, Any]:
    """Load the parsed cli-spec.json dictionary."""
    path = _get_schemas_dir() / "cli-spec.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_cli_spec_schema() -> dict[str, Any]:
    """Load the Draft 2020-12 cli-spec.schema.json dictionary."""
    path = _get_schemas_dir() / "cli-spec.schema.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def validate_cli_spec() -> None:
    """Validate cli-spec.json against cli-spec.schema.json using Draft202012Validator."""
    schema = load_cli_spec_schema()
    spec = load_cli_spec()
    validator = jsonschema.Draft202012Validator(schema)
    validator.validate(spec)
