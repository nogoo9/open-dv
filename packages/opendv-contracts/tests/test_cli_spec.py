"""Tests validating cli-spec.json, error-codes.json, and baseline-digests.json parity."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from opendv_contracts.errors import ERROR_MAPPINGS, VALID_EXIT_CODES, ErrorCode
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

    # 2. Canonical repo root schemas/cli-spec.json (runtime baseline)
    repo_root = pkg_schema_path.parent.parent.parent.parent
    root_schema_path = repo_root / "schemas" / "cli-spec.json"
    assert root_schema_path.is_file(), f"Missing {root_schema_path}"
    root_content = root_schema_path.read_text(encoding="utf-8")
    assert root_content == expected_content, (
        "Committed schemas/cli-spec.json is stale! "
        "Run `python -m opendv_service.export_cli_spec` to synchronize."
    )

    # 3. Canonical forward studio schema schemas/v1/cli-spec.json (Feature 11)
    v1_schema_path = repo_root / "schemas" / "v1" / "cli-spec.json"
    assert v1_schema_path.is_file(), f"Missing {v1_schema_path}"
    v1_content = v1_schema_path.read_text(encoding="utf-8")
    assert v1_content == expected_content, (
        "Committed schemas/v1/cli-spec.json is stale! "
        "Run `python -m opendv_service.export_cli_spec` to synchronize."
    )
    v1_spec = json.loads(v1_content)
    validate_cli_spec(v1_spec)
    v1_model = CliSpec.model_validate(v1_spec)
    assert v1_model.spec_version == "1.0.0"
    assert v1_model.contract_version == "1.0.0"
    assert any(cmd.name == "ontology" for cmd in v1_model.commands)


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
        assert entry["exit_code"] in VALID_EXIT_CODES
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


def test_error_codes_normative_catalog_conformance() -> None:
    """Assert 100% set and field parity between ErrorCode / ERROR_MAPPINGS and error-codes.json."""
    pkg_root = Path(__file__).resolve().parent.parent
    catalog_path = pkg_root.parent.parent / "schemas" / "v1" / "error-codes.json"
    assert catalog_path.is_file(), f"Missing normative error code catalog: {catalog_path}"

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog_codes = catalog.get("error_codes", [])
    assert catalog.get("total_error_codes") == len(catalog_codes)

    catalog_code_set = {e["code"] for e in catalog_codes}
    enum_code_set = {c.value for c in ErrorCode}
    assert enum_code_set == catalog_code_set, (
        f"ErrorCode enum vs error-codes.json parity mismatch: "
        f"only_in_enum={sorted(enum_code_set - catalog_code_set)}, "
        f"only_in_json={sorted(catalog_code_set - enum_code_set)}"
    )

    for entry in catalog_codes:
        code_str = entry["code"]
        mapping = ERROR_MAPPINGS[ErrorCode[code_str]]
        assert mapping.exit_code == entry["exit_code"], (
            f"Exit code mismatch for {code_str}: "
            f"py={mapping.exit_code} vs catalog={entry['exit_code']}"
        )
        assert mapping.http_status == entry["http_status"], (
            f"HTTP status mismatch for {code_str}: "
            f"py={mapping.http_status} vs catalog={entry['http_status']}"
        )
        assert mapping.problem_title == entry["problem_title"], (
            f"Title mismatch for {code_str}: "
            f"py={mapping.problem_title} vs catalog={entry['problem_title']}"
        )
        assert mapping.problem_type_uri == entry["problem_type_uri"], (
            f"URI mismatch for {code_str}: "
            f"py={mapping.problem_type_uri} vs catalog={entry['problem_type_uri']}"
        )

    # Verify problem_type_uri uniqueness across ERROR_MAPPINGS
    uris = [m.problem_type_uri for m in ERROR_MAPPINGS.values()]
    assert len(uris) == len(set(uris)), "Duplicate problem_type_uri detected in ERROR_MAPPINGS!"


def test_baseline_digests_all_files_and_sha256_match_disk() -> None:
    """Assert every file path and SHA-256 in schemas/v1/baseline-digests.json matches disk."""
    pkg_root = Path(__file__).resolve().parent.parent
    repo_root = pkg_root.parent.parent
    baselines_path = repo_root / "schemas" / "v1" / "baseline-digests.json"
    assert baselines_path.is_file(), f"Missing baseline digests: {baselines_path}"

    baselines = json.loads(baselines_path.read_text(encoding="utf-8"))

    for key, ont_meta in baselines.get("certified_ontologies", {}).items():
        ont_file = repo_root / ont_meta["path"]
        assert ont_file.is_file(), f"Missing certified ontology '{key}': {ont_file}"

    genesis_vec = repo_root / baselines["genesis_draft"]["vector_path"]
    assert genesis_vec.is_file(), f"Missing genesis vector: {genesis_vec}"

    for matcher_id, m_meta in baselines.get("matchers", {}).items():
        assert (repo_root / m_meta["spec_path"]).is_file(), f"Missing {matcher_id} spec_path"
        assert (repo_root / m_meta["vector_path"]).is_file(), f"Missing {matcher_id} vector_path"

    for pack_id, p_meta in baselines.get("reference_packs", {}).items():
        pack_file = repo_root / p_meta["pack_path"]
        reg_file = repo_root / p_meta["registry_path"]
        assert pack_file.is_file(), f"Missing {pack_id} pack_path: {pack_file}"
        assert reg_file.is_file(), f"Missing {pack_id} registry_path: {reg_file}"
        actual_pack_sha = hashlib.sha256(pack_file.read_bytes()).hexdigest()
        assert actual_pack_sha == p_meta["canonical_sha256"], (
            f"FIBO pack SHA-256 mismatch for {pack_id}: "
            f"expected {p_meta['canonical_sha256']}, got {actual_pack_sha}"
        )

    for schema_rel, s_meta in baselines.get("schema_baselines", {}).items():
        schema_file = repo_root / schema_rel
        assert schema_file.is_file(), f"Missing baseline schema: {schema_file}"
        actual_sha = hashlib.sha256(schema_file.read_bytes()).hexdigest()
        assert actual_sha == s_meta["sha256"], (
            f"Schema baseline SHA-256 mismatch for {schema_rel}: "
            f"expected {s_meta['sha256']}, got {actual_sha}"
        )
