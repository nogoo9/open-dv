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

import asyncio
import copy
import inspect
import json
import pathlib
import re
import sys
import tomllib
import types
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ValidationError
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


def _extract_fenced_blocks(doc_rel_path: str, lang: str) -> list[str]:
    text = (REPO_ROOT / doc_rel_path).read_text(encoding="utf-8")
    return re.findall(rf"```{lang}\n(.*?)```", text, flags=re.DOTALL)


def _exec_doc_python_blocks(doc_rel_path: str) -> dict[str, Any]:
    blocks = _extract_fenced_blocks(doc_rel_path, "python")
    assert blocks, f"Expected executable Python blocks in {doc_rel_path}"
    mod_name = "doc_ref_" + re.sub(r"[^a-zA-Z0-9_]", "_", doc_rel_path)
    mod = types.ModuleType(mod_name)
    sys.modules[mod_name] = mod
    for idx, block in enumerate(blocks, 1):
        exec(compile(block, f"{doc_rel_path}-block-{idx}.py", "exec"), mod.__dict__)
    return mod.__dict__


def test_doc08_monorepo_configs_rbac_redaction_and_sse_wire_vectors() -> None:
    toml_blocks = [
        tomllib.loads(block) for block in _extract_fenced_blocks("docs/08-platform-mvp-monorepo-and-api-spec.md", "toml")
    ]
    assert len(toml_blocks) == 2
    prototools, pyproject = toml_blocks
    assert set(prototools) >= {"python", "uv", "bun", "moon"}
    assert set(pyproject["tool"]["uv"]["workspace"]["members"]) == {
        "packages/opendv-core",
        "packages/opendv-telemetry",
        "packages/opendv-axi-verifier",
        "packages/opendv-duckdb-sandbox",
        "packages/opendv-agent-harness",
        "apps/opendv-platform-api",
        "apps/opendv-cli",
    }
    banned_api = pyproject["tool"]["ruff"]["lint"]["flake8-tidy-imports"]["banned-api"]
    assert "antigravity" in banned_api

    yaml_blocks = [
        yaml.safe_load(block) for block in _extract_fenced_blocks("docs/08-platform-mvp-monorepo-and-api-spec.md", "yaml")
    ]
    assert len(yaml_blocks) == 4
    workspace_cfg, toolchains_cfg, py_tasks_cfg, ts_tasks_cfg = yaml_blocks
    assert workspace_cfg["projects"]["globs"] == ["packages/*", "apps/*"]
    assert workspace_cfg["vcs"]["defaultBranch"] == "main"
    assert workspace_cfg["hasher"]["walkStrategy"] == "vcs"
    assert toolchains_cfg["unstable_python"]["packageManager"] == "uv"
    assert toolchains_cfg["javascript"]["packageManager"] == "bun"
    assert toolchains_cfg["typescript"]["syncProjectReferences"] is True
    assert set(py_tasks_cfg["tasks"]) >= {"lint", "typecheck", "test"}
    assert set(ts_tasks_cfg["tasks"]) >= {"lint", "typecheck", "test", "build"}

    ns08 = _exec_doc_python_blocks("docs/08-platform-mvp-monorepo-and-api-spec.md")
    RbacRole = ns08["RbacRole"]
    CreateRunRequest = ns08["CreateRunRequest"]
    redact_event_for_role = ns08["redact_event_for_role"]

    run_req = CreateRunRequest(project_id="proj-pe-01", harness_provider="claude", model_tier_override="argon")
    assert run_req.harness_provider == "claude"
    with pytest.raises(ValidationError):
        CreateRunRequest(project_id="proj-pe-01", harness_provider="replay")

    sample_event: dict[str, Any] = {
        "event_id": "evt-test-01",
        "data_classification": "RESTRICTED_PII",
        "layer1_story_banner": {
            "headline": "Extracted SSN 999-00-1234 and CalPERS $150M side-letter fee cap",
            "business_impact": "Exposes confidential LP fee terms and tax IDs",
        },
        "layer2_visual_payload": {
            "viz_id": "VIZ-STEP-04",
            "summary_metrics": {"extracted_ssn_count": 3, "lp_commitment_usd": 150000000},
            "ner_spans": [{"text": "999-00-1234", "span": [10, 21]}],
            "semantic_hash_pills": [
                {
                    "pill_label": "[HK: 5D15..0AE4 | GLOBAL:LP-101 CalPERS]",
                    "full_sha256_hex": "5D15EB10C8D7621D3167849C41C5F9CD9244FD79FB720CB17104D49E4B820AE4",
                    "bkcc_prefix": "GLOBAL",
                    "business_key": "LP-101",
                    "entity_label": "CalPERS",
                }
            ],
        },
        "layer3_formal_proof": {
            "contract_yaml_diff": "+ tax_id: 999-00-1234",
            "toon_diagnostic_payload": "@toon_v1 secret",
            "formula_latex": "x = 999",
            "sqlglot_ast_summary": "SELECT ssn FROM raw_lp",
        },
    }

    redacted_auditor = redact_event_for_role(sample_event, RbacRole.AUDITOR_FINANCE)
    assert "999-00-1234" not in redacted_auditor["layer1_story_banner"]["headline"]
    assert "CalPERS" not in redacted_auditor["layer1_story_banner"]["headline"]
    assert "tax IDs" not in redacted_auditor["layer1_story_banner"]["business_impact"]
    assert redacted_auditor["layer2_visual_payload"]["summary_metrics"] == {
        "redacted": True,
        "required_classification": "RESTRICTED_PII",
    }
    assert "ner_spans" not in redacted_auditor["layer2_visual_payload"]
    assert redacted_auditor["layer2_visual_payload"]["semantic_hash_pills"][0]["entity_label"] == "[REDACTED]"
    assert (
        redacted_auditor["layer3_formal_proof"]["sqlglot_ast_summary"]
        == "[REDACTED RESTRICTED_PII]"
    )
    assert (
        redacted_auditor["layer3_formal_proof"]["formula_latex"]
        == "[REDACTED RESTRICTED_PII]"
    )

    unredacted_steward = redact_event_for_role(sample_event, RbacRole.RESTRICTED_PII_CUSTODIAN)
    assert unredacted_steward == sample_event

    ns10 = _exec_doc_python_blocks("docs/10-decoupled-ui-workbench-and-theme-spec.md")
    verify_hash = ns10["verify_ts_python_hash_pill_parity"]
    obs_schema = json.loads(
        (REPO_ROOT / "schemas/vault-observability-event.schema.json").read_text(encoding="utf-8")
    )
    sse_blocks = _extract_fenced_blocks("docs/08-platform-mvp-monorepo-and-api-spec.md", "text")
    sse_events: list[dict[str, Any]] = []
    for block in sse_blocks:
        for line in block.splitlines():
            if line.startswith("data: "):
                payload = json.loads(line.removeprefix("data: "))
                if "event_id" in payload:
                    sse_events.append(payload)
    assert len(sse_events) == 2
    for ev in sse_events:
        Draft202012Validator(obs_schema).validate(ev)
        for pill in ev["layer2_visual_payload"].get("semantic_hash_pills", []):
            expected_hex = verify_hash([pill["bkcc_prefix"], pill["business_key"]])
            assert pill["full_sha256_hex"] == expected_hex


def test_doc09_agent_harness_protocol_bridges_and_adapters(tmp_path: pathlib.Path) -> None:
    ns09 = _exec_doc_python_blocks("docs/09-agnostic-agent-harness-and-sdk-spec.md")
    AgentPersona = ns09["AgentPersona"]
    ModelTier = ns09["ModelTier"]
    HarnessConfig = ns09["HarnessConfig"]
    AgentToolDefinition = ns09["AgentToolDefinition"]
    AgentTaskSpec = ns09["AgentTaskSpec"]
    BaseVaultAgentHarness = ns09["BaseVaultAgentHarness"]
    HarnessEventKind = ns09["HarnessEventKind"]
    HarnessStopReason = ns09["HarnessStopReason"]
    AntigravityHarnessAdapter = ns09["AntigravityHarnessAdapter"]
    ClaudeAgentHarnessAdapter = ns09["ClaudeAgentHarnessAdapter"]
    to_antigravity_tool_callable = ns09["to_antigravity_tool_callable"]
    build_claude_sdk_mcp_bundle = ns09["build_claude_sdk_mcp_bundle"]

    assert [t.value for t in ModelTier] == ["argon", "flash"]
    assert issubclass(AntigravityHarnessAdapter, BaseVaultAgentHarness)
    assert issubclass(ClaudeAgentHarnessAdapter, BaseVaultAgentHarness)

    class SampleInput(BaseModel):
        source_table: str

    class SampleOutput(BaseModel):
        profiled_columns: int

    async def _sample_tool(inp: SampleInput, ctx: Any) -> SampleOutput:
        ctx.emit_observability_event({"event_id": "evt-01", "status": "PASS", "table": inp.source_table})
        ctx.emit_observability_event({"event_id": "evt-02", "status": "ASK-USER", "table": inp.source_table})
        return SampleOutput(profiled_columns=44)

    tool_def = AgentToolDefinition(
        name="profile_source_feed",
        description="Profile Bronze source feed.",
        input_model=SampleInput,
        output_model=SampleOutput,
        allowed_personas=frozenset({AgentPersona.SCOUT_SOURCE}),
        handler=_sample_tool,
    )

    cfg = HarnessConfig(
        provider="antigravity",
        workspace_dir=tmp_path,
        state_dir=tmp_path / "state",
        app_data_dir=tmp_path / "appdata",
        save_dir=tmp_path / "savedir",
    )
    spec = AgentTaskSpec(
        task_id="task-01",
        run_id="run-01",
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        persona=AgentPersona.SCOUT_SOURCE,
        model_tier=ModelTier.FAST_TIER,
        stage_id="STAGE_01_SCOUT_SOURCE",
        workbench_view_id="WB-02",
        viz_id="VIZ-STEP-02",
        system_instructions="Profile Bronze tables.",
        prompt="Profile raw_lp_commitments.",
        tools=(tool_def,),
        output_schema=SampleOutput,
    )

    agy_callable = to_antigravity_tool_callable(tool_def, None)
    sig = inspect.signature(agy_callable)
    assert list(sig.parameters) == ["source_table"]

    async def _failing_handler(inp: SampleInput, ctx: Any) -> SampleOutput:
        raise RuntimeError(f"failed on {inp.source_table}")

    failing_tool = AgentToolDefinition(
        name="failing_tool",
        description="Always raises.",
        input_model=SampleInput,
        output_model=SampleOutput,
        allowed_personas=frozenset({AgentPersona.SCOUT_SOURCE}),
        handler=_failing_handler,
    )

    async def _run_harness_checks() -> None:
        dummy_adapter = AntigravityHarnessAdapter(cfg)
        ctx_err = dummy_adapter.build_tool_context(spec)
        mcp_servers, qual_names, _ = build_claude_sdk_mcp_bundle([tool_def, failing_tool], ctx_err)
        assert qual_names == ["mcp__opendv__profile_source_feed", "mcp__opendv__failing_tool"]
        err_resp = await mcp_servers["opendv"]["handlers"]["mcp__opendv__failing_tool"](
            {"source_table": "bad_tbl"}
        )
        assert err_resp["is_error"] is True
        assert json.loads(err_resp["content"][0]["text"])["error"] == "RuntimeError"

        post_hooks: list[Any] = []

        class FakeAgySdk:
            class types:
                class AgentBehavior:
                    AUTONOMOUS = "AUTONOMOUS"

                class SubagentCapabilities:
                    def __init__(self, **kw: Any) -> None:
                        self.kw = kw

                class SubagentConfig:
                    def __init__(self, **kw: Any) -> None:
                        self.kw = kw

                class TemplatedSystemInstructions:
                    def __init__(self, **kw: Any) -> None:
                        self.kw = kw

                class CapabilitiesConfig:
                    def __init__(self, **kw: Any) -> None:
                        self.kw = kw

            class hooks:
                class hooks:
                    @staticmethod
                    def post_tool_call(fn: Any) -> Any:
                        post_hooks.append(fn)
                        return fn

                    @staticmethod
                    def on_tool_error(fn: Any) -> Any:
                        return fn

                class policy:
                    @staticmethod
                    def deny_all() -> str:
                        return "deny_all"

                    @staticmethod
                    def workspace_only(paths: list[str]) -> tuple[str, list[str]]:
                        return ("workspace_only", paths)

                    @staticmethod
                    def allow(name: str) -> tuple[str, str]:
                        return ("allow", name)

                    @staticmethod
                    def deny(name: str, **kw: Any) -> tuple[str, str]:
                        return ("deny", name)

            class LocalAgentConfig:
                def __init__(self, **kw: Any) -> None:
                    self.kw = kw

            class Agent:
                def __init__(self, agent_cfg: Any) -> None:
                    self.agent_cfg = agent_cfg
                    self.conversation_id = "conv-agy-01"

                async def __aenter__(self) -> Any:
                    return self

                async def __aexit__(self, *args: Any) -> None:
                    return None

                async def chat(self, prompt: str) -> Any:
                    bridged = self.agent_cfg.kw["tools"][0]
                    tool_out = await bridged(source_table="raw_lp_commitments")
                    for hk in post_hooks:
                        await hk({"output": tool_out})

                    class _Resp:
                        async def cancel(self) -> None:
                            return None

                        @property
                        def thoughts(self) -> Any:
                            async def _gen() -> Any:
                                yield "thinking..."

                            return _gen()

                        @property
                        def tool_calls(self) -> Any:
                            async def _gen() -> Any:
                                yield types.SimpleNamespace(
                                    name="profile_source_feed",
                                    args={"source_table": "raw_lp_commitments"},
                                )

                            return _gen()

                        def __aiter__(self) -> Any:
                            async def _gen() -> Any:
                                yield "Profiled 44 columns."

                            return _gen()

                        async def structured_output(self) -> dict[str, Any]:
                            return {"profiled_columns": 44}

                    return _Resp()

        agy_adapter = AntigravityHarnessAdapter(cfg, sdk_module=FakeAgySdk)
        agy_result = await agy_adapter.run_task(spec)
        assert agy_result.stop_reason == HarnessStopReason.STEWARD_HOLD
        assert len(agy_result.emitted_events) == 2
        assert agy_result.structured_output == SampleOutput(profiled_columns=44)

        class ThinkingBlock:
            thinking = "analyzing..."

        class ToolUseBlock:
            name = "mcp__opendv__profile_source_feed"
            input = {"source_table": "raw_lp_commitments"}

        class TextBlock:
            text = "Done."

        class AssistantMessage:
            content = [ThinkingBlock(), ToolUseBlock(), TextBlock()]

        class ResultMessage:
            session_id = "sess-claude-01"
            structured_output = {"profiled_columns": 44}

        class FakeClaudeSdk:
            class AgentDefinition:
                def __init__(self, **kw: Any) -> None:
                    self.kw = kw

            class ClaudeAgentOptions:
                def __init__(self, **kw: Any) -> None:
                    self.kw = kw

            class ClaudeSDKClient:
                def __init__(self, options: Any) -> None:
                    self.options = options

                async def __aenter__(self) -> Any:
                    return self

                async def __aexit__(self, *args: Any) -> None:
                    return None

                async def interrupt(self) -> None:
                    return None

                async def query(self, prompt: str) -> None:
                    handler = self.options.kw["mcp_servers"]["opendv"]["handlers"][
                        "mcp__opendv__profile_source_feed"
                    ]
                    await handler({"source_table": "raw_lp_commitments"})

                async def receive_response(self) -> Any:
                    yield AssistantMessage()
                    yield ResultMessage()

        claude_cfg = HarnessConfig(
            provider="claude",
            workspace_dir=tmp_path,
            state_dir=tmp_path / "state",
            app_data_dir=tmp_path / "appdata",
            save_dir=tmp_path / "savedir",
        )
        claude_adapter = ClaudeAgentHarnessAdapter(claude_cfg, sdk_module=FakeClaudeSdk)
        streamed_obs = [
            ev.observability_event
            async for ev in claude_adapter.stream_task(spec)
            if ev.kind == HarnessEventKind.OBSERVABILITY_EVENT
        ]
        assert [e["event_id"] for e in streamed_obs if e] == ["evt-01", "evt-02"]
        claude_result = await claude_adapter.run_task(spec)
        assert claude_result.stop_reason == HarnessStopReason.STEWARD_HOLD
        assert len(claude_result.emitted_events) == 2

    asyncio.run(_run_harness_checks())


def test_doc10_hash_pill_parity_and_workbench_view_map() -> None:
    ns10 = _exec_doc_python_blocks("docs/10-decoupled-ui-workbench-and-theme-spec.md")
    verify_hash = ns10["verify_ts_python_hash_pill_parity"]
    view_map = ns10["WORKBENCH_VIEW_STAGE_MAP"]

    assert list(view_map) == [f"WB-{i:02d}" for i in range(12)]
    assert verify_hash(["ANNE||O", "BRIEN"]) != verify_hash(["ANNE", "O||BRIEN"])
    assert verify_hash(["VAL_X", None, "VAL_Y"]) != verify_hash(["VAL_X", "VAL_Y", None])
    assert (
        verify_hash(["GLOBAL", "LP-101"])
        == "5D15EB10C8D7621D3167849C41C5F9CD9244FD79FB720CB17104D49E4B820AE4"
    )
    assert (
        verify_hash(["lp-101"], bkcc_prefix="global")
        == "5D15EB10C8D7621D3167849C41C5F9CD9244FD79FB720CB17104D49E4B820AE4"
    )
    assert verify_hash([None, "   "]) == "0" * 64
    assert verify_hash([None, ""], bkcc_prefix="GLOBAL") == "0" * 64
    assert verify_hash(["CalPERS", "Active"], is_business_key=False) != verify_hash(
        ["CALPERS", "ACTIVE"], is_business_key=False
    )
    assert verify_hash([None, None], is_business_key=False) != "0" * 64


def test_doc11_work_package_manifest_waves_and_disjoint_ownership() -> None:
    ns11 = _exec_doc_python_blocks("docs/11-agent-worker-implementation-plan.md")
    WorkPackageSpec = ns11["WorkPackageSpec"]
    manifest = ns11["WORK_PACKAGE_MANIFEST"]
    verify_disjoint = ns11["verify_disjoint_owned_paths"]
    compute_waves = ns11["compute_dispatch_waves"]

    assert [wp.wp_id for wp in manifest] == [f"WP-{i:02d}" for i in range(1, 13)]
    verify_disjoint(manifest)
    waves = compute_waves(manifest)
    assert waves == [
        ["WP-01"],
        ["WP-02", "WP-09"],
        ["WP-03", "WP-10"],
        ["WP-04", "WP-06", "WP-11"],
        ["WP-05", "WP-07"],
        ["WP-08"],
        ["WP-12"],
    ]

    with pytest.raises(ValueError, match="Path prefix collision"):
        verify_disjoint(
            (
                WorkPackageSpec("WP-A", "b-a", "flash", (), ("apps/api/",), (), "true"),
                WorkPackageSpec("WP-B", "b-b", "flash", (), ("apps/api/tests/test_e2e.py",), (), "true"),
            )
        )
