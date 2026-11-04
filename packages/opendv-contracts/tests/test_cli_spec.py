"""Tests validating cli-spec.json and cli-spec.schema.json."""

from __future__ import annotations

from opendv_contracts.errors import ERROR_MAPPINGS
from opendv_contracts.spec import (
    load_cli_spec,
    validate_cli_spec,
)


def test_cli_spec_schema_conformance() -> None:
    """Validate cli-spec.json against Draft 2020-12 cli-spec.schema.json."""
    validate_cli_spec()


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

    # Verify run has subcommands list, get, create, abort, events
    run_subcmds = {sub["name"] for sub in commands["run"].get("subcommands", [])}
    assert {"list", "get", "create", "abort", "events"}.issubset(run_subcmds)

    # Verify hold has subcommands list, approve, reject, answer
    hold_subcmds = {sub["name"] for sub in commands["hold"].get("subcommands", [])}
    assert {"list", "approve", "reject", "answer"}.issubset(hold_subcmds)


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
