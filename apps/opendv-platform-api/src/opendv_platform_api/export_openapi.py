"""Deterministic OpenAPI 3.1.0 specification exporter (`schemas/openapi-v1.json`)."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from opendv_platform_api.main import create_app
from opendv_service import OpenDVSettings

DRAFT_2020_12_URI = "https://json-schema.org/draft/2020-12/schema"


def generate_openapi_spec() -> dict[str, Any]:
    """Build the deterministic OpenAPI 3.1.0 document with Draft 2020-12 dialect metadata."""
    isolated_ws = Path(__file__).resolve().parent
    isolated_settings = OpenDVSettings(
        workspace_root=isolated_ws,
        state_dir=isolated_ws / ".opendv_openapi_tmp",
    )
    app = create_app(settings=isolated_settings, workspace_root=isolated_ws)
    raw_spec = copy.deepcopy(app.openapi())
    raw_spec["$schema"] = DRAFT_2020_12_URI
    raw_spec["$id"] = "https://open-dv.dev/schemas/openapi-v1.json"
    raw_spec["jsonSchemaDialect"] = DRAFT_2020_12_URI

    if "components" in raw_spec and "schemas" in raw_spec["components"]:
        raw_spec["components"]["schemas"]["OntologyDelta"] = {
            "$ref": "#/components/schemas/OntologyDelta-Output",
            "description": (
                "Ordered collection of additive and structural operations evolving Graph A to vN+1."
            ),
        }

    Draft202012Validator.check_schema(raw_spec)
    return raw_spec


def render_openapi_spec() -> str:
    """Serialize the deterministic OpenAPI 3.1.0 document to canonical JSON string."""
    spec = generate_openapi_spec()
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def export_openapi_to_file(out_path: Path | None = None) -> Path:
    """Write the deterministic OpenAPI 3.1.0 specification to `schemas/openapi-v1.json`."""
    target = (out_path or Path("schemas/openapi-v1.json")).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    rendered = render_openapi_spec()
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        f.write(rendered)
    v1_target = target.parent / "v1" / "openapi.json"
    if v1_target.parent.exists():
        with open(v1_target, "w", encoding="utf-8", newline="\n") as f:
            f.write(rendered)
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
        default=Path("schemas/openapi-v1.json"),
        help="Destination path for openapi-v1.json",
    )
    args = parser.parse_args(argv)
    out_file = export_openapi_to_file(args.out)
    print(f"Exported OpenAPI 3.1.0 specification to {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
