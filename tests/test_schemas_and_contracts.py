"""Gate 1 Schema & Contract Verification Suite (`tests/test_schemas_and_contracts.py`).

Validates:
1. JSON Schema Draft 2020-12 meta-schema compliance for all `schemas/*.json`.
2. Positive contract & Canonical IR validation for:
   - `examples/dv_model_contract.yaml`
   - `examples/private_equity/dv_pe_model_contract.yaml`
   - `examples/private_equity/pe_canonical_ir.json`
3. Parametrized negative/adversarial schema & Gate 1 contract tests:
   - Missing required top-level sections (Contract v5.0 & Canonical IR v1.0.0)
   - Invalid naming regexes (`HUB_`, `LNK_`, `SAT_` prefix and case rules)
   - Hub containing mutable descriptive attributes (`DV-G1-001` / `AP-01`)
   - Link with `< 2` parent Hub references (`DV-G3-002`)
   - Satellite attached to multiple parent tables (`DV-G3-003` / `DV-STR-005`)
   - Invalid `hash_function` / cryptographic digest algorithm
   - Missing `zero_key_hex` / zero-key sentinels (`-1`, `-2`) or `ghost_record_policy`
"""

from __future__ import annotations

import copy
import json
import pathlib
import re
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator
import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMAS_DIR = REPO_ROOT / "schemas"
EXAMPLES_DIR = REPO_ROOT / "examples"

CONTRACT_SCHEMA_PATH = SCHEMAS_DIR / "no-mistake-dv2-contract-v5.schema.json"
IR_SCHEMA_PATH = SCHEMAS_DIR / "dv2-canonical-ir.schema.json"
OBSERVABILITY_SCHEMA_PATH = SCHEMAS_DIR / "vault-observability-event.schema.json"

GENERIC_CONTRACT_PATH = EXAMPLES_DIR / "dv_model_contract.yaml"
PE_CONTRACT_PATH = EXAMPLES_DIR / "private_equity" / "dv_pe_model_contract.yaml"
PE_IR_PATH = EXAMPLES_DIR / "private_equity" / "pe_canonical_ir.json"

ZERO_KEY_UNKNOWN_HEX = "0" * 64
ZERO_KEY_ERROR_HEX = "f" * 64


# ============================================================================
# Reference Gate 1 Semantic Contract & Policy Validator
# ============================================================================


def validate_dv2_contract_and_ir_policy(
    doc: dict[str, Any],
    schema: dict[str, Any],
    *,
    require_strict_sha256: bool = True,
    require_ghost_and_zero_key_policy: bool = False,
) -> list[str]:
    """Run JSON Schema Draft 2020-12 validation plus Gate 1 semantic policy checks.

    Returns a list of deterministic violation codes (empty list if valid).
    """
    violations: list[str] = []

    validator = Draft202012Validator(schema)
    schema_target = (
        {k: v for k, v in doc.items() if k not in {"zero_key_hex", "ghost_record_policy"}}
        if require_ghost_and_zero_key_policy
        else doc
    )
    for err in sorted(validator.iter_errors(schema_target), key=lambda e: list(e.path)):
        path_str = ".".join(str(p) for p in err.path) or "<root>"
        violations.append(f"SCHEMA_ERROR:{path_str}:{err.validator}")

    # Semantic checks applicable to both Contract v5 and Canonical IR
    for hub in doc.get("hubs", []):
        hub_name = hub.get("name", "")
        if not re.match(r"^(hub|HUB)_[a-zA-Z0-9_]+$", hub_name):
            violations.append(f"DV-G1-NAME-HUB:{hub_name}")
        # Check for mutable descriptive attributes leaked into Hub (DV-G1-001 / AP-01)
        extra_cols = hub.get("extra_columns", [])
        mutable_attrs = hub.get("descriptive_attributes", []) or hub.get("payload_columns", [])
        if extra_cols or mutable_attrs:
            violations.append(f"DV-G1-001:AP-01:Hub '{hub_name}' contains mutable descriptive attributes")
        for bk in hub.get("business_keys", []):
            if isinstance(bk, dict) and bk.get("column_role") in {"MUTABLE_STATUS", "DESCRIPTIVE_PAYLOAD"}:
                violations.append(f"DV-G1-001:AP-01:Hub '{hub_name}' business_key '{bk.get('name')}' is mutable")

    for link in doc.get("links", []):
        link_name = link.get("name", "")
        if not re.match(r"^(lnk|t_lnk|nh_lnk|sal|hal|x_lnk|LNK|T_LNK|NH_LNK|SAL|HAL|X_LNK)_[a-zA-Z0-9_]+$", link_name):
            violations.append(f"DV-G1-NAME-LNK:{link_name}")
        hub_refs = link.get("hub_references") if "hub_references" in link else link.get("parent_hubs", [])
        if not isinstance(hub_refs, list) or len(hub_refs) < 2:
            violations.append(
                f"DV-G3-002:Link '{link_name}' has < 2 parent Hub references ({len(hub_refs) if isinstance(hub_refs, list) else 0})"
            )

    for sat in doc.get("satellites", []):
        sat_name = sat.get("name", "")
        if not re.match(r"^(sat|bsat|mas|ma_sat|esat|sts|rts|SAT|BSAT|MAS|MA_SAT|ESAT|STS|RTS)_[a-zA-Z0-9_]+$", sat_name):
            violations.append(f"DV-G1-NAME-SAT:{sat_name}")
        parent = sat.get("parent_entity") if "parent_entity" in sat else sat.get("parent_table")
        additional_parents = sat.get("additional_parents", [])
        if isinstance(parent, list) or not isinstance(parent, str) or not parent.strip() or additional_parents:
            violations.append(f"DV-G3-003:Satellite '{sat_name}' must attach to exactly 1 parent table")

    # Hash function & Zero Key / Ghost Record policy validation
    hash_cfg = doc.get("hash_config", {})
    hash_fn = doc.get("hash_function") or hash_cfg.get("algorithm")
    if hash_fn is not None:
        allowed = {"SHA256", "SHA-256"} if require_strict_sha256 else {"SHA256", "SHA-256", "MD5"}
        if hash_fn not in allowed:
            violations.append(f"DV-G1-HASH-FN:Invalid hash_function '{hash_fn}'")

    if "hash_config" in doc or require_ghost_and_zero_key_policy:
        zk_unknown = hash_cfg.get("zero_key_unknown_token")
        zk_error = hash_cfg.get("zero_key_error_token")
        if zk_unknown != "-1" or zk_error != "-2":
            violations.append("DV-G1-ZERO-KEY:Missing or invalid zero_key_unknown_token ('-1') / zero_key_error_token ('-2')")

    if require_ghost_and_zero_key_policy:
        zk_hex = doc.get("zero_key_hex")
        ghost_policy = doc.get("ghost_record_policy")
        if zk_hex not in {ZERO_KEY_UNKNOWN_HEX, f"0x{ZERO_KEY_UNKNOWN_HEX}"}:
            violations.append(f"DV-G1-ZERO-KEY-HEX:Missing or invalid zero_key_hex '{zk_hex}'")
        if not ghost_policy or not isinstance(ghost_policy, (str, dict)):
            violations.append("DV-G1-GHOST-POLICY:Missing or invalid ghost_record_policy")

    return violations


# ============================================================================
# 1. Draft 2020-12 Meta-Schema Validation for All `schemas/*.json`
# ============================================================================


@pytest.mark.parametrize(
    "schema_path",
    sorted(SCHEMAS_DIR.glob("*.json")),
    ids=lambda p: p.name,
)
def test_all_schemas_valid_draft_2020_12(schema_path: pathlib.Path) -> None:
    """Every JSON Schema in `schemas/*.json` must be valid Draft 2020-12."""
    schema_doc = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema_doc.get("$schema") == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema_doc)


def test_expected_schema_inventory_present() -> None:
    """Verify the three canonical schema files exist in `schemas/`."""
    found_names = {p.name for p in SCHEMAS_DIR.glob("*.json")}
    expected = {
        "no-mistake-dv2-contract-v5.schema.json",
        "dv2-canonical-ir.schema.json",
        "vault-observability-event.schema.json",
    }
    assert expected.issubset(found_names)


# ============================================================================
# 2. Positive Validation of Example Contracts & Canonical IR
# ============================================================================


@pytest.mark.parametrize(
    "contract_path",
    [GENERIC_CONTRACT_PATH, PE_CONTRACT_PATH],
    ids=["examples/dv_model_contract.yaml", "examples/private_equity/dv_pe_model_contract.yaml"],
)
def test_positive_model_contracts_validate(
    contract_path: pathlib.Path,
    contract_schema: dict[str, Any],
) -> None:
    """Both reference YAML contracts must pass Draft 2020-12 schema and Gate 1 policy checks."""
    contract_doc = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    Draft202012Validator(contract_schema).validate(contract_doc)
    violations = validate_dv2_contract_and_ir_policy(contract_doc, contract_schema)
    assert violations == []


def test_positive_pe_canonical_ir_validates(
    pe_ir: dict[str, Any],
    ir_schema: dict[str, Any],
) -> None:
    """`examples/private_equity/pe_canonical_ir.json` must pass schema and Gate 1 policy checks."""
    Draft202012Validator(ir_schema).validate(pe_ir)
    violations = validate_dv2_contract_and_ir_policy(pe_ir, ir_schema)
    assert violations == []
    assert pe_ir["ir_version"] == "1.0.0"
    assert pe_ir["hash_config"]["algorithm"] == "SHA256"
    assert pe_ir["hash_config"]["encoding_scheme"] == "LENGTH_PREFIXED"
    assert pe_ir["hash_config"]["zero_key_unknown_token"] == "-1"
    assert pe_ir["hash_config"]["zero_key_error_token"] == "-2"


# ============================================================================
# 3. Parametrized Negative/Adversarial Tests: Missing Top-Level Sections
# ============================================================================


@pytest.mark.parametrize(
    "missing_section",
    [
        "contract_version",
        "domain_namespace",
        "lifecycle_stage",
        "column_triage_ledger",
        "hubs",
        "links",
        "satellites",
        "reference_tables",
        "unmapped_escalations",
    ],
)
def test_negative_contract_missing_required_top_level_section(
    pe_contract: dict[str, Any],
    contract_schema: dict[str, Any],
    missing_section: str,
) -> None:
    """Removing any required top-level key from a v5.0 contract must fail schema validation."""
    mutated = copy.deepcopy(pe_contract)
    del mutated[missing_section]
    with pytest.raises(jsonschema.ValidationError) as exc_info:
        Draft202012Validator(contract_schema).validate(mutated)
    assert missing_section in str(exc_info.value)


@pytest.mark.parametrize(
    "missing_section",
    [
        "ir_version",
        "model_name",
        "hash_config",
        "system_columns",
        "hubs",
        "links",
        "satellites",
    ],
)
def test_negative_canonical_ir_missing_required_top_level_section(
    pe_ir: dict[str, Any],
    ir_schema: dict[str, Any],
    missing_section: str,
) -> None:
    """Removing any required top-level key from Canonical IR must fail schema validation."""
    mutated = copy.deepcopy(pe_ir)
    del mutated[missing_section]
    with pytest.raises(jsonschema.ValidationError) as exc_info:
        Draft202012Validator(ir_schema).validate(mutated)
    assert missing_section in str(exc_info.value)


# ============================================================================
# 4. Parametrized Negative/Adversarial Tests: Invalid Naming Regexes
# ============================================================================


@pytest.mark.parametrize(
    ("entity_collection", "bad_name"),
    [
        ("hubs", "HUB_FUND"),             # Uppercase rejected by ^hub_[a-z0-9_]+$
        ("hubs", "dim_fund"),             # Dimensional prefix instead of hub_
        ("hubs", "hub-fund"),             # Hyphen instead of underscore
        ("hubs", "hub_"),                 # Missing concept suffix
        ("links", "LNK_LP_COMMITMENT"),   # Uppercase rejected by ^(lnk|...)_[a-z0-9_]+$
        ("links", "fact_lp_commitment"),  # Fact prefix instead of lnk_
        ("links", "bridge_lp_fund"),      # Bridge prefix in links section
        ("satellites", "SAT_FUND_CORE"),  # Uppercase rejected by ^(sat|...)_[a-z0-9_]+$
        ("satellites", "tbl_fund_attrs"), # Wrong prefix
        ("satellites", "sat-fund-core"),  # Hyphens disallowed
    ],
)
def test_negative_invalid_entity_naming_regexes(
    pe_contract: dict[str, Any],
    contract_schema: dict[str, Any],
    entity_collection: str,
    bad_name: str,
) -> None:
    """Invalid Hub (`HUB_`), Link (`LNK_`), and Satellite (`SAT_`) table names must be rejected."""
    mutated = copy.deepcopy(pe_contract)
    mutated[entity_collection][0]["name"] = bad_name
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(contract_schema).validate(mutated)


# ============================================================================
# 5. Negative/Adversarial Tests: Hub Mutable Descriptive Attributes (DV-G1-001 / AP-01)
# ============================================================================


@pytest.mark.parametrize(
    ("injected_field", "injected_value"),
    [
        ("descriptive_attributes", ["deal_stage", "company_name"]),
        ("payload_columns", ["deal_stage"]),
        ("mutable_status_column", "deal_stage"),
    ],
)
def test_negative_hub_with_mutable_descriptive_attributes_in_contract(
    pe_contract: dict[str, Any],
    contract_schema: dict[str, Any],
    injected_field: str,
    injected_value: Any,
) -> None:
    """Placing mutable descriptive attributes inside a Hub violates DV-G1-001 / AP-01."""
    mutated = copy.deepcopy(pe_contract)
    mutated["hubs"][2][injected_field] = injected_value  # hub_portfolio_company
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(contract_schema).validate(mutated)
    violations = validate_dv2_contract_and_ir_policy(mutated, contract_schema)
    assert any("DV-G1-001" in v or "SCHEMA_ERROR" in v for v in violations)


def test_negative_hub_with_mutable_extra_columns_in_ir_triggers_dv_g1_001(
    pe_ir: dict[str, Any],
    ir_schema: dict[str, Any],
) -> None:
    """In Canonical IR, injecting `deal_stage` into Hub `extra_columns` triggers `DV-G1-001` (`AP-01`)."""
    mutated = copy.deepcopy(pe_ir)
    mutated["hubs"][2]["extra_columns"] = [
        {"name": "deal_stage", "data_type": "VARCHAR(64)", "nullable": True}
    ]
    violations = validate_dv2_contract_and_ir_policy(mutated, ir_schema)
    assert any("DV-G1-001" in v and "AP-01" in v for v in violations)


# ============================================================================
# 6. Negative/Adversarial Tests: Link With < 2 Parent Hub References (DV-G3-002)
# ============================================================================


@pytest.mark.parametrize("num_hub_refs", [0, 1])
def test_negative_link_with_fewer_than_two_parent_hubs(
    pe_contract: dict[str, Any],
    contract_schema: dict[str, Any],
    num_hub_refs: int,
) -> None:
    """A Link with < 2 parent Hub references must fail schema validation and trigger DV-G3-002."""
    mutated = copy.deepcopy(pe_contract)
    mutated["links"][0]["hub_references"] = mutated["links"][0]["hub_references"][:num_hub_refs]
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(contract_schema).validate(mutated)
    violations = validate_dv2_contract_and_ir_policy(mutated, contract_schema)
    assert any("DV-G3-002" in v for v in violations)


# ============================================================================
# 7. Negative/Adversarial Tests: Satellite Attached to Multiple Parent Tables
# ============================================================================


@pytest.mark.parametrize(
    "invalid_parent_spec",
    [
        {"parent_entity": ["hub_fund", "hub_limited_partner"]},
        {"parent_entity": "hub_fund,hub_limited_partner"},
        {"parent_entity": "hub_fund", "additional_parents": ["hub_limited_partner"]},
    ],
)
def test_negative_satellite_attached_to_multiple_parents(
    pe_contract: dict[str, Any],
    contract_schema: dict[str, Any],
    invalid_parent_spec: dict[str, Any],
) -> None:
    """A Satellite attached to multiple parent tables must fail schema and DV-G3-003 checks."""
    mutated = copy.deepcopy(pe_contract)
    mutated["satellites"][0].update(invalid_parent_spec)
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(contract_schema).validate(mutated)
    violations = validate_dv2_contract_and_ir_policy(mutated, contract_schema)
    assert any("DV-G3-003" in v or "SCHEMA_ERROR" in v for v in violations)


def test_negative_ir_satellite_with_additional_parents_rejected(
    pe_ir: dict[str, Any],
    ir_schema: dict[str, Any],
) -> None:
    """In Canonical IR, `additional_parents` on a Satellite must trigger `DV-G3-003`."""
    mutated = copy.deepcopy(pe_ir)
    mutated["satellites"][0]["additional_parents"] = ["hub_limited_partner"]
    violations = validate_dv2_contract_and_ir_policy(mutated, ir_schema)
    assert any("DV-G3-003" in v for v in violations)


# ============================================================================
# 8. Negative/Adversarial Tests: Invalid `hash_function` & Missing Zero-Key / Ghost Policy
# ============================================================================


@pytest.mark.parametrize("bad_hash_fn", ["MD5", "SHA1", "CRC32", "XXHASH64", "NONE", ""])
def test_negative_invalid_hash_function(
    pe_ir: dict[str, Any],
    ir_schema: dict[str, Any],
    bad_hash_fn: str,
) -> None:
    """Non-SHA256 hash functions must be rejected by strict Gate 1 policy (and schema for non-enum values)."""
    mutated = copy.deepcopy(pe_ir)
    mutated["hash_config"]["algorithm"] = bad_hash_fn
    if bad_hash_fn not in {"MD5"}:
        with pytest.raises(jsonschema.ValidationError):
            Draft202012Validator(ir_schema).validate(mutated)
    violations = validate_dv2_contract_and_ir_policy(
        mutated, ir_schema, require_strict_sha256=True
    )
    assert any("DV-G1-HASH-FN" in v or "SCHEMA_ERROR" in v for v in violations)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda d: d["hash_config"].pop("zero_key_unknown_token", None),
        lambda d: d["hash_config"].pop("zero_key_error_token", None),
        lambda d: d["hash_config"].update({"zero_key_unknown_token": "0"}),
        lambda d: d["hash_config"].update({"zero_key_error_token": "-99"}),
        lambda d: d.pop("zero_key_hex", None),
        lambda d: d.update({"zero_key_hex": "deadbeef"}),
        lambda d: d.pop("ghost_record_policy", None),
        lambda d: d.update({"ghost_record_policy": ""}),
    ],
    ids=[
        "missing_zero_key_unknown_token",
        "missing_zero_key_error_token",
        "wrong_zero_key_unknown_token",
        "wrong_zero_key_error_token",
        "missing_zero_key_hex",
        "invalid_zero_key_hex",
        "missing_ghost_record_policy",
        "empty_ghost_record_policy",
    ],
)
def test_negative_missing_zero_key_hex_or_ghost_record_policy(
    pe_ir: dict[str, Any],
    ir_schema: dict[str, Any],
    mutator: Any,
) -> None:
    """Missing or corrupted `zero_key_hex`, zero-key tokens, or `ghost_record_policy` must fail."""
    baseline = copy.deepcopy(pe_ir)
    baseline["zero_key_hex"] = ZERO_KEY_UNKNOWN_HEX
    baseline["ghost_record_policy"] = "SEED_SYSTEM_GHOST_ROW_ON_ALL_SATELLITES"

    # Baseline with policy fields present passes
    assert (
        validate_dv2_contract_and_ir_policy(
            baseline, ir_schema, require_ghost_and_zero_key_policy=True
        )
        == []
    )

    mutated = copy.deepcopy(baseline)
    mutator(mutated)
    violations = validate_dv2_contract_and_ir_policy(
        mutated, ir_schema, require_ghost_and_zero_key_policy=True
    )
    assert len(violations) >= 1
    assert any(
        code in " ".join(violations)
        for code in ("DV-G1-ZERO-KEY", "DV-G1-ZERO-KEY-HEX", "DV-G1-GHOST-POLICY", "SCHEMA_ERROR")
    )
