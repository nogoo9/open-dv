"""Architecture Test: Schema Synchronization & Zero Drift Verification (Task 6D.2 / Issue #236).

Enforces:
1. schemas/v1/cli-spec.json strictly matches declarative command definitions in
   opendv_service.cli_spec.definitions (including STUDIO_COMMAND with 14 subcommand groups).
2. docs/16-cli-reference.md is strictly synchronized with cli-spec.json via docs_generator.
3. schemas/v1/openapi.json and schemas/openapi-v1.json match live FastAPI routes in
   opendv_platform_api.
4. packages/opendv-protocol-ts/src/types/openapi.ts is synchronized with schemas/openapi-v1.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from opendv_service.cli_spec.definitions import CORE_COMMANDS, OptionDef
from opendv_service.cli_spec.docs_generator import generate_cli_reference_markdown

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMAS_V1_CLI_SPEC = REPO_ROOT / "schemas" / "v1" / "cli-spec.json"
SCHEMAS_OPENAPI_V1 = REPO_ROOT / "schemas" / "openapi-v1.json"
SCHEMAS_V1_OPENAPI = REPO_ROOT / "schemas" / "v1" / "openapi.json"
DOCS_16_CLI_REF = REPO_ROOT / "docs" / "16-cli-reference.md"
TS_OPENAPI_TYPES = REPO_ROOT / "packages" / "opendv-protocol-ts" / "src" / "types" / "openapi.ts"


@pytest.mark.architecture
def test_cli_spec_matches_definitions() -> None:
    """Assert schemas/v1/cli-spec.json command hierarchy strictly matches definitions.py."""
    assert SCHEMAS_V1_CLI_SPEC.is_file(), f"Missing {SCHEMAS_V1_CLI_SPEC}"
    spec = json.loads(SCHEMAS_V1_CLI_SPEC.read_text(encoding="utf-8"))

    spec_commands = {cmd["name"]: cmd for cmd in spec.get("commands", [])}
    def_commands = {cmd.name: cmd for cmd in CORE_COMMANDS}

    # 1. Top-level commands must match
    assert set(spec_commands.keys()) == set(def_commands.keys()), (
        f"Top-level command mismatch: {set(spec_commands.keys()) ^ set(def_commands.keys())}"
    )

    # 2. Check each command recursively
    def _assert_command_matches(spec_cmd: dict[str, Any], def_cmd: Any, path: str) -> None:
        cmd_path = f"{path}/{def_cmd.name}"
        assert spec_cmd["name"] == def_cmd.name, f"{cmd_path}: name mismatch"
        assert spec_cmd["description"].strip() == def_cmd.description.strip(), (
            f"{cmd_path}: description mismatch"
        )
        assert spec_cmd["help"].strip() == def_cmd.help.strip(), f"{cmd_path}: help mismatch"
        assert spec_cmd.get("availability", "both") == def_cmd.availability, (
            f"{cmd_path}: availability mismatch"
        )

        # Check options
        spec_opts = {opt["name"]: opt for opt in spec_cmd.get("options", [])}
        def_opts = {opt.name: opt for opt in def_cmd.options if isinstance(opt, OptionDef)}
        assert set(spec_opts.keys()) == set(def_opts.keys()), (
            f"{cmd_path}: options mismatch: {set(spec_opts.keys()) ^ set(def_opts.keys())}"
        )

        for opt_name, def_opt in def_opts.items():
            spec_opt = spec_opts[opt_name]
            assert spec_opt["flag"] == def_opt.flag, f"{cmd_path} opt {opt_name}: flag mismatch"
            assert spec_opt["type"] == def_opt.type, f"{cmd_path} opt {opt_name}: type mismatch"
            assert spec_opt.get("required", False) == def_opt.required, (
                f"{cmd_path} opt {opt_name}: required mismatch"
            )
            assert spec_opt.get("default") == def_opt.default, (
                f"{cmd_path} opt {opt_name}: default mismatch"
            )

        # Check subcommands
        spec_subs = {sub["name"]: sub for sub in spec_cmd.get("subcommands", [])}
        def_subs = {sub.name: sub for sub in def_cmd.subcommands}
        assert set(spec_subs.keys()) == set(def_subs.keys()), (
            f"{cmd_path}: subcommands mismatch: {set(spec_subs.keys()) ^ set(def_subs.keys())}"
        )

        for sub_name, def_sub in def_subs.items():
            _assert_command_matches(spec_subs[sub_name], def_sub, cmd_path)

    for cmd_name, def_cmd in def_commands.items():
        _assert_command_matches(spec_commands[cmd_name], def_cmd, "")

    # 3. Explicit check for all 14 studio subcommand groups
    studio_cmd = spec_commands["ontology"]
    studio_subs = next(s for s in studio_cmd.get("subcommands", []) if s["name"] == "studio")
    expected_studio_groups = {
        "init",
        "create",
        "delete",
        "list",
        "status",
        "upload",
        "parse",
        "clarify",
        "answer",
        "construct",
        "delta",
        "proposal",
        "export",
        "promote",
        "bundle",
    }
    actual_studio_groups = {s["name"] for s in studio_subs.get("subcommands", [])}
    assert actual_studio_groups == expected_studio_groups, (
        f"Studio subcommand groups mismatch: {actual_studio_groups ^ expected_studio_groups}"
    )


@pytest.mark.architecture
def test_cli_reference_docs_strict_sync() -> None:
    """Assert docs/16-cli-reference.md is strictly up to date with generator."""
    assert DOCS_16_CLI_REF.is_file(), f"Missing {DOCS_16_CLI_REF}"
    committed = DOCS_16_CLI_REF.read_text(encoding="utf-8").replace("\r\n", "\n")
    generated = generate_cli_reference_markdown().replace("\r\n", "\n")

    assert committed == generated, (
        "Documentation drift detected in docs/16-cli-reference.md! "
        "Run 'python scripts/generate_cli_reference.py' to synchronize."
    )

    # Verify Studio subcommands are deeply documented
    assert "###### `ontology studio init`" in committed
    assert "###### `ontology studio create`" in committed
    assert "###### `ontology studio proposal accept`" in committed
    assert "###### `ontology studio bundle export`" in committed
    assert "###### `ontology studio delta apply`" in committed


@pytest.mark.architecture
def test_openapi_specs_sync_with_fastapi_app() -> None:
    """Assert schemas/v1/openapi.json matches live FastAPI routes and openapi-v1.json."""
    from opendv_platform_api.main import create_app

    assert SCHEMAS_V1_OPENAPI.is_file(), f"Missing {SCHEMAS_V1_OPENAPI}"
    assert SCHEMAS_OPENAPI_V1.is_file(), f"Missing {SCHEMAS_OPENAPI_V1}"

    v1_openapi = json.loads(SCHEMAS_V1_OPENAPI.read_text(encoding="utf-8"))
    root_openapi = json.loads(SCHEMAS_OPENAPI_V1.read_text(encoding="utf-8"))

    # Both openapi schema files must match in paths
    assert set(v1_openapi["paths"].keys()) == set(root_openapi["paths"].keys()), (
        "schemas/v1/openapi.json and schemas/openapi-v1.json paths mismatch!"
    )

    # Test live FastAPI app contains all paths
    app = create_app(workspace_root=REPO_ROOT)
    live_openapi = app.openapi()

    for path in live_openapi["paths"]:
        assert path in v1_openapi["paths"], (
            f"Live FastAPI path {path} missing from schemas/v1/openapi.json!"
        )

    # Specifically assert presence of Studio endpoints
    studio_paths = [p for p in v1_openapi["paths"] if p.startswith("/api/v1/ontology/studio")]
    assert len(studio_paths) >= 12, (
        f"Expected at least 12 Studio endpoints, found {len(studio_paths)}: {studio_paths}"
    )
    assert "/api/v1/ontology/studio/sessions" in v1_openapi["paths"]
    assert "/api/v1/ontology/studio/init" in v1_openapi["paths"]
    assert "/api/v1/ontology/studio/jobs/{job_id}" in v1_openapi["paths"]


@pytest.mark.architecture
def test_typescript_protocol_types_sync() -> None:
    """Assert packages/opendv-protocol-ts/src/types/openapi.ts is up to date."""
    assert TS_OPENAPI_TYPES.is_file(), f"Missing {TS_OPENAPI_TYPES}"
    ts_content = TS_OPENAPI_TYPES.read_text(encoding="utf-8")

    # Verify crucial Studio paths and schemas exist in generated TS types
    assert '"/api/v1/ontology/studio/sessions":' in ts_content
    assert '"/api/v1/ontology/studio/init":' in ts_content
    assert '"/api/v1/ontology/studio/jobs/{job_id}":' in ts_content
    assert "StudioSessionDTO:" in ts_content
    assert "CreateSessionRequest:" in ts_content
    assert 'OntologyDelta: components["schemas"]["OntologyDelta-Output"];' in ts_content
    assert "OntologyDelta: unknown;" not in ts_content
