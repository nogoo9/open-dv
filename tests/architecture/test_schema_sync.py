"""Architecture Test: V2 Schema Synchronization & Zero Drift Verification (`schemas/v2/`)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from opendv.cli.export_contracts import (
    build_cli_spec,
    check_contracts,
    generate_cli_reference_markdown,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMAS_V2_DIR = REPO_ROOT / "schemas" / "v2"
SCHEMAS_V2_CLI_SPEC = SCHEMAS_V2_DIR / "cli-spec.json"
SCHEMAS_V2_OPENAPI = SCHEMAS_V2_DIR / "openapi.json"
DOCS_16_CLI_REF = REPO_ROOT / "docs" / "16-cli-reference.md"
WEB_OPENAPI_TYPES = REPO_ROOT / "web" / "src" / "api" / "types.generated.ts"


@pytest.mark.architecture
def test_v2_contracts_match_exporters() -> None:
    """Assert checked-in `schemas/v2/*` artifacts match live exporters with zero drift."""
    drifts = check_contracts(REPO_ROOT)
    assert not drifts, (
        "V2 contract drift detected (`uv run python -m opendv.cli.export_contracts`):\n"
        + "\n".join(drifts)
    )

    spec = json.loads(SCHEMAS_V2_CLI_SPEC.read_text(encoding="utf-8"))
    live_spec = build_cli_spec()
    assert spec == live_spec
    cmd_paths = {" ".join(cmd["path"]) for cmd in spec["subcommands"]}
    assert {
        "project init",
        "source attach",
        "ontology construct",
        "vault build",
        "storage compile",
        "mcp serve",
        "doctor",
        "serve",
    }.issubset(cmd_paths)


@pytest.mark.architecture
def test_cli_reference_docs_strict_sync() -> None:
    """Assert `docs/16-cli-reference.md` is strictly up to date with `generate_cli_reference_markdown()`."""
    assert DOCS_16_CLI_REF.is_file(), f"Missing {DOCS_16_CLI_REF}"
    committed = DOCS_16_CLI_REF.read_text(encoding="utf-8").replace("\r\n", "\n")
    generated = generate_cli_reference_markdown().replace("\r\n", "\n")

    assert committed == generated, (
        "Documentation drift detected in docs/16-cli-reference.md! "
        "Run 'uv run python scripts/generate_cli_reference.py' to synchronize."
    )
    assert "### `opendv project init`" in committed
    assert "### `opendv vault build`" in committed
    assert "### `opendv storage compile`" in committed
    assert "### `opendv mcp serve`" in committed


@pytest.mark.architecture
def test_typescript_web_types_sync_with_v2_openapi() -> None:
    """Assert `web/src/api/types.generated.ts` covers all OpenAPI paths, HTTP methods & schemas."""
    assert WEB_OPENAPI_TYPES.is_file(), f"Missing {WEB_OPENAPI_TYPES}"
    assert SCHEMAS_V2_OPENAPI.is_file(), f"Missing {SCHEMAS_V2_OPENAPI}"
    ts_content = WEB_OPENAPI_TYPES.read_text(encoding="utf-8")
    openapi_doc = json.loads(SCHEMAS_V2_OPENAPI.read_text(encoding="utf-8"))

    http_methods = {"get", "post", "put", "delete", "patch", "options", "head", "trace"}
    missing_paths: list[str] = []
    missing_methods: list[str] = []
    for api_path, path_item in openapi_doc.get("paths", {}).items():
        path_marker = f'"{api_path}":'
        idx = ts_content.find(path_marker)
        if idx == -1:
            missing_paths.append(api_path)
            continue
        next_path_idx = ts_content.find('\n    "/', idx + len(path_marker))
        paths_end_idx = ts_content.find("\n}", idx + len(path_marker))
        candidates = [pos for pos in (next_path_idx, paths_end_idx) if pos != -1]
        block_end = min(candidates) if candidates else len(ts_content)
        path_block = ts_content[idx:block_end]
        for method in path_item:
            m_lower = method.lower()
            if m_lower in http_methods and f"{m_lower}: operations[" not in path_block:
                missing_methods.append(f"{m_lower.upper()} {api_path}")

    assert not missing_paths, f"Paths missing from web/src/api/types.generated.ts: {missing_paths}"
    assert not missing_methods, (
        f"HTTP methods missing from web/src/api/types.generated.ts: {missing_methods}"
    )

    schemas_dict = openapi_doc.get("components", {}).get("schemas", {})
    missing_schemas = [
        schema_name
        for schema_name in schemas_dict
        if not re.search(rf'(?:^|\s)"?{re.escape(schema_name)}"?\s*:', ts_content, re.MULTILINE)
    ]
    assert not missing_schemas, (
        f"Component schemas missing from web/src/api/types.generated.ts: {missing_schemas}"
    )
