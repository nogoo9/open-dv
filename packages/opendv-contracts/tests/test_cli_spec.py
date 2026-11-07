"""Tests validating cli-spec.json and cli-spec.schema.json."""

from __future__ import annotations

import json
from pathlib import Path

from opendv_contracts.errors import ERROR_MAPPINGS
from opendv_contracts.spec import (
    CliSpec,
    load_cli_spec,
    load_cli_spec_model,
    validate_cli_spec,
)
from opendv_service.cli_spec.generator import generate_cli_spec


def test_cli_spec_schema_conformance() -> None:
    """Validate cli-spec.json against Draft 2020-12 cli-spec.schema.json."""
    validate_cli_spec()


def test_cli_spec_freshness_against_generator() -> None:
    """Assert committed cli-spec.json matches generator output bit-for-bit."""
    spec_from_generator = generate_cli_spec()
    expected_content = json.dumps(spec_from_generator, indent=2, ensure_ascii=False) + "\n"

    # 1. Internal contracts package schema
    pkg_schema_path = Path(__file__).resolve().parent.parent / "schemas" / "cli-spec.json"
    assert pkg_schema_path.is_file(), f"Missing {pkg_schema_path}"
    committed_content = pkg_schema_path.read_text(encoding="utf-8")
    assert committed_content == expected_content, (
        "Committed packages/opendv-contracts/schemas/cli-spec.json is stale! "
        "Run `python -m opendv_service.export_cli_spec` to synchronize."
    )

    # 2. Canonical repo root schemas/v1/cli-spec.json
    repo_root = pkg_schema_path.parent.parent.parent.parent
    v1_schema_path = repo_root / "schemas" / "v1" / "cli-spec.json"
    if v1_schema_path.is_file():
        v1_content = v1_schema_path.read_text(encoding="utf-8")
        assert v1_content == expected_content, (
            "Committed schemas/v1/cli-spec.json is stale! "
            "Run `python -m opendv_service.export_cli_spec` to synchronize."
        )


def test_cli_spec_typed_model_validation() -> None:
    """Assert that cli-spec.json parses cleanly into typed Pydantic CliSpec model."""
    spec_model = load_cli_spec_model()
    assert isinstance(spec_model, CliSpec)
    assert spec_model.spec_version == "1.0.0"
    assert spec_model.contract_version == "1.0.0"
    assert len(spec_model.commands) >= 7
    assert len(spec_model.global_options) >= 5
    assert len(spec_model.error_map) == len(ERROR_MAPPINGS)


def test_cli_spec_commands_inventory() -> None:
    """Assert all 7 command groups exist and have required properties."""
    spec = load_cli_spec()
    commands = {cmd["name"]: cmd for cmd in spec["commands"]}

    required_commands = ["profile", "compile", "verify", "sandbox", "pipeline", "run", "hold"]
    for name in required_commands:
        assert name in commands, f"Command '{name}' missing from cli-spec.json"

    # Verify 'verify' has 'axi-dv' alias
    assert "axi-dv" in commands["verify"]["aliases"]

    # Verify pipeline has subcommands validate, run, report
    pipeline_subcmds = {sub["name"] for sub in commands["pipeline"].get("subcommands", [])}
    assert {"validate", "run", "report"}.issubset(pipeline_subcmds)

    # Verify run has subcommands list, get, create, abort, events, explorer, artifact
    run_subcmds = {sub["name"] for sub in commands["run"].get("subcommands", [])}
    expected_run = {"list", "get", "create", "abort", "events", "explorer", "artifact"}
    assert expected_run.issubset(run_subcmds)

    # Verify hold has subcommands decide, list, approve, reject, answer
    hold_subcmds = {sub["name"] for sub in commands["hold"].get("subcommands", [])}
    assert {"decide", "list", "approve", "reject", "answer"}.issubset(hold_subcmds)


def test_cli_spec_error_map_completeness() -> None:
    """Assert all ErrorCodes from Python ERROR_MAPPINGS are registered in cli-spec.json."""
    spec = load_cli_spec()
    spec_error_codes = {entry["error_code"] for entry in spec["error_map"]}
    py_error_codes = {code.value for code in ERROR_MAPPINGS}

    assert spec_error_codes == py_error_codes, (
        f"Mismatch between cli-spec error_map and Python ERROR_MAPPINGS: "
        f"diff={spec_error_codes ^ py_error_codes}"
    )

    for entry in spec["error_map"]:
        assert entry["exit_code"] in [0, 1, 2]
        assert 200 <= entry["http_status"] <= 599
        assert entry["problem_title"]
        assert entry["problem_type_uri"].startswith("urn:opendv:")


def test_cli_spec_option_flags_validity() -> None:
    """Assert all option flags adhere to standard GNU long-flag conventions."""
    spec = load_cli_spec()

    def check_options(options: list[dict]) -> None:
        for opt in options:
            flag = opt["flag"]
            assert flag.startswith("--"), f"Option flag {flag} must start with '--'"
            assert flag == flag.lower(), f"Option flag {flag} must be lowercase"
            if opt["short"]:
                assert opt["short"].startswith("-"), (
                    f"Short flag {opt['short']} must start with '-'"
                )
                assert len(opt["short"]) == 2, f"Short flag {opt['short']} must be 2 chars"

    check_options(spec.get("global_options", []))

    for cmd in spec["commands"]:
        check_options(cmd.get("options", []))
        for subcmd in cmd.get("subcommands", []):
            check_options(subcmd.get("options", []))
