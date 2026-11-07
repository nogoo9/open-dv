"""CLI specification exporter and validator.

Validates generated cli-spec.json against cli-spec.schema.json via jsonschema.Draft202012Validator
and exports bit-for-bit identical files to:
1. packages/opendv-contracts/schemas/cli-spec.json (internal package asset)
2. schemas/v1/cli-spec.json (canonical public schema)
3. schemas/cli-spec.json (root alias)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[import-untyped]

from opendv_contracts.spec import load_cli_spec_schema
from opendv_service.cli_spec.generator import generate_cli_spec


def find_repo_root(start: Path | None = None) -> Path:
    """Locate the git repository root by looking for root marker files."""
    current = (start or Path(__file__)).resolve()
    for parent in [current, *current.parents]:
        has_root_marker = (
            (parent / ".git").exists()
            or (parent / "moon.yml").exists()
            or (parent / "packages").is_dir()
        )
        if has_root_marker and (parent / "packages" / "opendv-service").is_dir():
            return parent
    # Fallback: traverse up 4 levels from cli_spec/exporter.py
    return Path(__file__).resolve().parent.parent.parent.parent.parent


def validate_cli_spec_dict(spec: dict[str, Any]) -> None:
    """Validate in-memory cli-spec dictionary against Draft 2020-12 schema."""
    schema = load_cli_spec_schema()
    validator = jsonschema.Draft202012Validator(schema)
    validator.validate(spec)


def export_cli_spec(
    *,
    repo_root: Path | None = None,
    include_seams: bool = False,
) -> list[Path]:
    """Generate, validate, and write cli-spec.json to standard contract locations."""
    root = repo_root or find_repo_root()
    spec = generate_cli_spec(include_seams=include_seams)

    # Validate schema conformance before writing
    validate_cli_spec_dict(spec)

    content = json.dumps(spec, indent=2, ensure_ascii=False) + "\n"

    target_paths = [
        root / "packages" / "opendv-contracts" / "schemas" / "cli-spec.json",
        root / "schemas" / "v1" / "cli-spec.json",
        root / "schemas" / "cli-spec.json",
    ]

    written_paths: list[Path] = []
    for path in target_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        written_paths.append(path)

    return written_paths
