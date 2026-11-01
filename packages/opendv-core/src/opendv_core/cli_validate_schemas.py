"""CLI entrypoint for `moon run opendv-core:validate-schemas`.

Validates all Draft 2020-12 JSON schemas in `schemas/*.json` and all example
artifacts in `examples/**` against both JSON Schema and strict Pydantic v2 models.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from opendv_core.schema_registry import (
    SchemaRegistry,
    find_workspace_root,
    validate_all_examples,
)


def main(argv: Sequence[str] | None = None) -> int:
    """Validate all normative JSON schemas and example artifacts."""
    _ = argv
    workspace_root: Path = find_workspace_root()
    registry = SchemaRegistry(schemas_dir=workspace_root / "schemas")

    checked_schemas = registry.check_all_schemas()
    validated_examples = validate_all_examples(workspace_root=workspace_root)

    print(
        f"Validated {len(checked_schemas)} Draft 2020-12 JSON schemas "
        f"({', '.join(sorted(checked_schemas.keys()))}) and "
        f"{len(validated_examples)} example artifacts "
        f"({', '.join(sorted(validated_examples.keys()))})."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
