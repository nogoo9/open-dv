"""Deterministic OpenAPI 3.1.0 specification exporter (`schemas/openapi-v1.json`)."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from opendv_platform_api.deps import REPO_ROOT
from opendv_platform_api.main import create_app

DRAFT_2020_12_URI = "https://json-schema.org/draft/2020-12/schema"


def generate_openapi_spec() -> dict[str, Any]:
    """Build the deterministic OpenAPI 3.1.0 document with Draft 2020-12 dialect metadata."""
    app = create_app()
    raw_spec = copy.deepcopy(app.openapi())
    raw_spec["$schema"] = DRAFT_2020_12_URI
    raw_spec["$id"] = "https://open-dv.dev/schemas/openapi-v1.json"
    raw_spec["jsonSchemaDialect"] = DRAFT_2020_12_URI
    Draft202012Validator.check_schema(raw_spec)
    return raw_spec


def export_openapi_to_file(out_path: Path | None = None) -> Path:
    """Write the deterministic OpenAPI 3.1.0 specification to `schemas/openapi-v1.json`."""
    target = (out_path or (REPO_ROOT / "schemas" / "openapi-v1.json")).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    spec = generate_openapi_spec()
    rendered = json.dumps(spec, indent=2, sort_keys=True) + "\n"
    target.write_text(rendered, encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for `python -m opendv_platform_api.export_openapi`."""
    parser = argparse.ArgumentParser(
        prog="opendv_platform_api.export_openapi",
        description="Export deterministic OpenAPI 3.1 specification to schemas/openapi-v1.json.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "schemas" / "openapi-v1.json",
        help="Destination path for openapi-v1.json",
    )
    args = parser.parse_args(argv)
    out_file = export_openapi_to_file(args.out)
    print(f"Exported OpenAPI 3.1.0 specification to {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
