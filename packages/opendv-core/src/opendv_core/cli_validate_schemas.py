"""CLI entrypoint for `moon run opendv-core:validate-schemas`.

Validates all Draft 2020-12 JSON schemas in `schemas/*.json` and all example
artifacts in `examples/**` against both JSON Schema and strict Pydantic v2 models.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from opendv_core.schema_registry import (
    SchemaRegistry,
    validate_all_examples,
)


def main(argv: Sequence[str] | None = None) -> int:
    """Validate all normative JSON schemas and example artifacts."""
    parser = argparse.ArgumentParser(
        prog="opendv-core:validate-schemas",
        description="Validate all normative JSON schemas and example artifacts.",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Root directory containing schemas/ and examples/.",
    )
    args = parser.parse_args(argv)

    ws_arg = args.workspace_root
    if ws_arg is not None:
        workspace_root = ws_arg.resolve()
    elif "OPENDV_WORKSPACE_ROOT" in os.environ:
        workspace_root = Path(os.environ["OPENDV_WORKSPACE_ROOT"]).resolve()
    else:
        workspace_root = Path.cwd().resolve()

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
