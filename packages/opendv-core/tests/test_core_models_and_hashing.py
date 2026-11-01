"""Comprehensive unit tests for `opendv-core` (WP-02).

Covers:
- Normative Data Vault 2.0 constants (Zero Keys, Ghost Record sentinels, NULL_TOKEN)
- Algorithm A.5 6-stage length-prefixed SHA-256 hashing & Private Equity test vectors
- Boundary-shift collision resistance & positional NULL preservation
- Zero Key resolution for mandatory vs optional inapplicable foreign keys
- ELM information-theoretic and temporal calculus (including PE 4.20 MB/day W_amp)
- Draft 2020-12 JSON Schema registry & validation of all `schemas/*.json` and `examples/**`
- Strict Pydantic v2 models for Contract v5.0, Canonical IR, Graph A, Graph B, and Observability
"""

from __future__ import annotations

import math
from typing import Any

import pytest
from pydantic import ValidationError

from opendv_core import (
    CANONICAL_IR_SCHEMA_FILENAME,
    CONTRACT_V5_SCHEMA_FILENAME,
    GHOST_DTS,
    GHOST_REC_SRC,
    NULL_TOKEN,
    OBSERVABILITY_EVENT_SCHEMA_FILENAME,
    ZERO_KEY_ERROR,
    ZERO_KEY_UNKNOWN,
    CanonicalIR,
    GraphAOntology,
    GraphBProfile,
    NoMistakeDataVault2ContractV5,
    SchemaRegistry,
    VaultObservabilityEvent,
    compute_asymmetric_containment,
    compute_cdc_velocity,
    compute_dv2_length_prefixed_sha256,
    compute_dv2_prehash_string,
    compute_jaccard_inclusion,
    compute_jaccard_similarity,
    compute_log_volatility,
    compute_non_null_ratio,
    compute_normalized_shannon_entropy,
    compute_null_rate,
    compute_shannon_entropy,
    compute_u_c,
    compute_u_nn,
    compute_uniqueness_ratio,
    compute_write_amplification_bytes_per_day,
    compute_write_amplification_mb_per_day,
    dv2_canonical_hash,
    encode_dv2_token,
    evaluate_yagni_satellite_split,
    find_workspace_root,
    resolve_fk_hash_key_with_zero_keys,
    validate_all_examples,
    validate_canonical_ir,
    validate_contract_v5,
    validate_graph_a,
    validate_graph_b,
    validate_observability_event,
)
from opendv_core.cli_validate_schemas import main as cli_validate_schemas_main


def test_normative_constants() -> None:
    """Verify sentinel constants match docs/01-core-modelling-spec.md."""
    assert ZERO_KEY_UNKNOWN == "0" * 64
    assert len(ZERO_KEY_UNKNOWN) == 64
    assert ZERO_KEY_ERROR == "f" * 64
    assert len(ZERO_KEY_ERROR) == 64
    assert GHOST_DTS == "1900-01-01 00:00:00"
    assert NULL_TOKEN == "-1:^^"
    assert GHOST_REC_SRC == "SYSTEM_GHOST"


def test_algorithm_a5_private_equity_vectors() -> None:
    """Verify Algorithm A.5 Private Equity benchmark vectors (docs/06 §2.4, docs/07)."""
    # 1. CalPERS: ('GLOBAL', 'LP-101') -> '6:GLOBAL||6:LP-101' -> 5D15..0AE4
    calpers_prehash = compute_dv2_prehash_string(("GLOBAL", "LP-101"))
    assert calpers_prehash == "6:GLOBAL||6:LP-101"
    calpers_hk = compute_dv2_length_prefixed_sha256(("GLOBAL", "LP-101"))
    assert calpers_hk == "5D15EB10C8D7621D3167849C41C5F9CD9244FD79FB720CB17104D49E4B820AE4"
    assert calpers_hk.startswith("5D15")
    assert calpers_hk.endswith("0AE4")
    assert dv2_canonical_hash(["LP-101"], bkcc_prefix="GLOBAL") == calpers_hk

    # 2. Apex Buyout Fund IV: ('GLOBAL', 'APEX_BO_IV') -> '6:GLOBAL||10:APEX_BO_IV' -> 94A6..8759
    fund_iv_prehash = compute_dv2_prehash_string(("GLOBAL", "APEX_BO_IV"))
    assert fund_iv_prehash == "6:GLOBAL||10:APEX_BO_IV"
    fund_iv_hk = compute_dv2_length_prefixed_sha256(("GLOBAL", "APEX_BO_IV"))
    assert fund_iv_hk == "94A60FCDA278C5AEC284C595413935793D5CEC113C511F20690046061D388759"
    assert fund_iv_hk.startswith("94A6")
    assert fund_iv_hk.endswith("8759")

    # 3. Bavarian Pension (Legacy GP Admin): ('LEGACY_GP_ADMIN', 'LP-101') -> 6B7C..D8B9
    bavarian_prehash = compute_dv2_prehash_string(("LEGACY_GP_ADMIN", "LP-101"))
    assert bavarian_prehash == "15:LEGACY_GP_ADMIN||6:LP-101"
    bavarian_hk = compute_dv2_length_prefixed_sha256(("LEGACY_GP_ADMIN", "LP-101"))
    assert bavarian_hk == "6B7C7180B62164F31F14EEBCD16CC5FD2B0CE58143DE66A4B67521113EC5D8B9"
    assert bavarian_hk.startswith("6B7C")
    assert bavarian_hk.endswith("D8B9")

    # Cross-source homonym collision immunity proof
    assert calpers_hk != bavarian_hk


def test_algorithm_a5_boundary_shift_and_positional_nulls() -> None:
    """Verify boundary-shift collision resistance and positional NULL preservation."""
    # Boundary-shift resistance with embedded delimiter '||'
    pre_left = compute_dv2_prehash_string(("ANNE||O", "BRIEN"))
    pre_right = compute_dv2_prehash_string(("ANNE", "O||BRIEN"))
    assert pre_left == "7:ANNE||O||5:BRIEN"
    assert pre_right == "4:ANNE||8:O||BRIEN"
    assert pre_left != pre_right
    assert compute_dv2_length_prefixed_sha256(
        ("ANNE||O", "BRIEN")
    ) != compute_dv2_length_prefixed_sha256(("ANNE", "O||BRIEN"))

    # Standard boundary shift ('AB', 'C') vs ('A', 'BC')
    assert compute_dv2_length_prefixed_sha256(("AB", "C")) != compute_dv2_length_prefixed_sha256(
        ("A", "BC")
    )

    # Positional NULL preservation
    null_mid_pre = compute_dv2_prehash_string(("VAL_X", None, "VAL_Y"))
    null_end_pre = compute_dv2_prehash_string(("VAL_X", "VAL_Y", None))
    assert null_mid_pre == "5:VAL_X||-1:^^||5:VAL_Y"
    assert null_end_pre == "5:VAL_X||5:VAL_Y||-1:^^"
    assert compute_dv2_length_prefixed_sha256(
        ("VAL_X", None, "VAL_Y")
    ) != compute_dv2_length_prefixed_sha256(("VAL_X", "VAL_Y", None))

    # Literal string '-1:^^' cannot collide with actual None because of byte-length prefix '5:'
    assert encode_dv2_token(None) == "-1:^^"
    assert encode_dv2_token("-1:^^") == "5:-1:^^"
    assert compute_dv2_length_prefixed_sha256(
        ("VAL_X", None), is_business_key=False
    ) != compute_dv2_length_prefixed_sha256(("VAL_X", "-1:^^"), is_business_key=False)


def test_algorithm_a5_case_folding_and_utf8_byte_length() -> None:
    """Verify BK case folding vs Satellite hash_diff case preservation and UTF-8 byte length."""
    # Business keys fold case and strip whitespace
    assert compute_dv2_length_prefixed_sha256(
        ("  global ", " lp-101  "), is_business_key=True
    ) == compute_dv2_length_prefixed_sha256(("GLOBAL", "LP-101"), is_business_key=True)

    # Satellite hash_diff preserves case sensitivity
    lower_diff = compute_dv2_length_prefixed_sha256(("CalPERS", "Active"), is_business_key=False)
    upper_diff = compute_dv2_length_prefixed_sha256(("CALPERS", "ACTIVE"), is_business_key=False)
    assert lower_diff != upper_diff

    # Mapping input for hash_diff sorts columns alphabetically by column name
    dict_diff_a = compute_dv2_length_prefixed_sha256(
        {"z_col": "100", "a_col": "200"}, is_business_key=False
    )
    dict_diff_b = compute_dv2_length_prefixed_sha256(
        {"a_col": "200", "z_col": "100"}, is_business_key=False
    )
    assert dict_diff_a == dict_diff_b
    assert (
        compute_dv2_prehash_string({"z_col": "100", "a_col": "200"}, is_business_key=False)
        == "3:200||3:100"
    )

    # Multi-byte UTF-8 byte length prefixing ('München' -> 'MÜNCHEN' has 7 chars, 8 UTF-8 bytes)
    assert encode_dv2_token("München", is_business_key=True) == "8:MÜNCHEN"

    # Bare string rejected to prevent accidental char-by-char hashing
    with pytest.raises(TypeError):
        compute_dv2_prehash_string("LP-101")  # type: ignore[arg-type]


def test_zero_key_resolution() -> None:
    """Verify foreign key resolution to ZERO_KEY_UNKNOWN and ZERO_KEY_ERROR."""
    # All-null business key resolves to ZERO_KEY_UNKNOWN
    assert compute_dv2_length_prefixed_sha256([None, "  "], is_business_key=True) == (
        ZERO_KEY_UNKNOWN
    )

    # Mandatory missing FK -> ZERO_KEY_UNKNOWN ('0'*64)
    assert resolve_fk_hash_key_with_zero_keys([None]) == ZERO_KEY_UNKNOWN
    assert resolve_fk_hash_key_with_zero_keys(["   "]) == ZERO_KEY_UNKNOWN
    assert resolve_fk_hash_key_with_zero_keys(["-1"]) == ZERO_KEY_UNKNOWN
    assert resolve_fk_hash_key_with_zero_keys(None) == ZERO_KEY_UNKNOWN

    # Optional inapplicable FK or '-2' error sentinel -> ZERO_KEY_ERROR ('f'*64)
    assert (
        resolve_fk_hash_key_with_zero_keys([None], is_optional_inapplicable=True) == ZERO_KEY_ERROR
    )
    assert resolve_fk_hash_key_with_zero_keys(["-2"]) == ZERO_KEY_ERROR

    # Valid FK resolves to exact Hub Hash Key
    expected_calpers = compute_dv2_length_prefixed_sha256(("GLOBAL", "LP-101"))
    assert resolve_fk_hash_key_with_zero_keys(["LP-101"], bkcc_prefix="GLOBAL") == expected_calpers
    assert resolve_fk_hash_key_with_zero_keys(["GLOBAL", "LP-101"]) == expected_calpers


def test_elm_calculus_metrics() -> None:
    """Verify ELM information-theoretic and temporal calculus formulas (docs/05 §1)."""
    # Candidate primary key column: 4 distinct non-null values
    pk_vals = ["LP-101", "LP-102", "LP-103", "LP-104"]
    assert compute_null_rate(pk_vals) == 0.0
    assert compute_non_null_ratio(pk_vals) == 1.0
    assert compute_uniqueness_ratio(pk_vals) == 1.0
    assert compute_u_c(pk_vals) == 1.0
    assert compute_u_nn(pk_vals) == 1.0
    assert math.isclose(compute_shannon_entropy(pk_vals), 2.0, rel_tol=1e-6)
    assert math.isclose(compute_normalized_shannon_entropy(pk_vals), 1.0, rel_tol=1e-6)

    # Partially nullable column with duplicates
    mixed_vals = ["BUYOUT", "BUYOUT", "GROWTH", None]
    assert math.isclose(compute_null_rate(mixed_vals), 0.25, rel_tol=1e-6)
    assert math.isclose(compute_non_null_ratio(mixed_vals), 0.75, rel_tol=1e-6)
    assert math.isclose(compute_u_c(mixed_vals), 2 / 4, rel_tol=1e-6)
    assert math.isclose(compute_u_nn(mixed_vals), 2 / 3, rel_tol=1e-6)
    assert 0.0 < compute_normalized_shannon_entropy(mixed_vals) < 1.0

    # Jaccard similarity, directional FK inclusion, and asymmetric containment
    parent_pks = ["APEX_BO_IV", "APEX_GE_II", "APEX_CI_I"]
    child_fks = ["APEX_BO_IV", "APEX_BO_IV", "APEX_GE_II", None]
    assert compute_jaccard_inclusion(child_fks, parent_pks) == 1.0
    assert compute_asymmetric_containment(child_fks, parent_pks) == 1.0
    assert math.isclose(compute_jaccard_similarity(child_fks, parent_pks), 2 / 3, rel_tol=1e-6)

    # CDC velocity & log volatility
    lam = compute_cdc_velocity(cdc_update_count=850, entity_count=1000, delta_t_days=1.0)
    assert math.isclose(lam, 0.85, rel_tol=1e-6)
    assert compute_log_volatility(0.0) == math.log10(1e-6)

    # Private Equity demo Write Amplification W_amp:
    # 157,000 daily rows * 0.85 change probability * 33 bytes = 4,403,850 bytes/day = 4.20 MiB/day
    w_bytes = compute_write_amplification_bytes_per_day(
        daily_rows_changed=157_000,
        slow_payload_width_bytes=33,
        high_freq_change_probability=0.85,
    )
    assert w_bytes == 4_403_850.0
    w_mb = compute_write_amplification_mb_per_day(
        daily_rows_changed=157_000,
        slow_payload_width_bytes=33,
        high_freq_change_probability=0.85,
        precision=2,
    )
    assert w_mb == 4.20

    # Gate 4 YAGNI split evaluation
    assert evaluate_yagni_satellite_split(4.20, min_w_amp_mb_per_day=1.0) is True
    assert evaluate_yagni_satellite_split(4.20, min_w_amp_mb_per_day=50.0) is False


def test_schema_registry_and_all_examples() -> None:
    """Verify Draft 2020-12 JSON schemas and all `examples/**` artifacts."""
    root = find_workspace_root()
    registry = SchemaRegistry(schemas_dir=root / "schemas")

    schemas = registry.check_all_schemas()
    assert CONTRACT_V5_SCHEMA_FILENAME in schemas
    assert CANONICAL_IR_SCHEMA_FILENAME in schemas
    assert OBSERVABILITY_EVENT_SCHEMA_FILENAME in schemas

    examples = validate_all_examples(workspace_root=root)
    assert isinstance(
        examples["examples/dv_model_contract.yaml"],
        NoMistakeDataVault2ContractV5,
    )
    pe_contract = examples["examples/private_equity/dv_pe_model_contract.yaml"]
    assert isinstance(pe_contract, NoMistakeDataVault2ContractV5)
    assert pe_contract.domain_namespace == "pe_asset_management"
    assert len(pe_contract.hubs) == 4
    assert len(pe_contract.links) == 5
    assert len(pe_contract.satellites) == 11

    pe_ir = examples["examples/private_equity/pe_canonical_ir.json"]
    assert isinstance(pe_ir, CanonicalIR)
    assert pe_ir.model_name == "pe_asset_management"
    assert len(pe_ir.hubs) == 4
    assert len(pe_ir.links) == 5
    assert len(pe_ir.satellites) == 11

    pe_graph_a = examples["examples/private_equity/pe_graph_a_ontology.yaml"]
    assert isinstance(pe_graph_a, GraphAOntology)
    assert len(pe_graph_a.canonical_business_concepts) == 5

    pe_graph_b = examples["examples/private_equity/pe_graph_b_profile.json"]
    assert isinstance(pe_graph_b, GraphBProfile)
    assert len(pe_graph_b.source_systems) == 5

    # Also test convenience validators and CLI entrypoint
    assert validate_contract_v5(root / "examples" / "dv_model_contract.yaml").contract_version == (
        "5.0"
    )
    assert (
        validate_canonical_ir(
            root / "examples" / "private_equity" / "pe_canonical_ir.json"
        ).ir_version
        == "1.0.0"
    )
    assert (
        validate_graph_a(
            root / "examples" / "private_equity" / "pe_graph_a_ontology.yaml"
        ).ontology_version
        == "1.0.0"
    )
    assert (
        validate_graph_b(
            root / "examples" / "private_equity" / "pe_graph_b_profile.json"
        ).profile_version
        == "1.0.0"
    )
    assert cli_validate_schemas_main([]) == 0


def test_observability_event_validation_and_invariants() -> None:
    """Verify `VaultObservabilityEvent` JSON Schema + Pydantic v2 `allOf` invariants."""
    calpers_hk = compute_dv2_length_prefixed_sha256(("GLOBAL", "LP-101"))
    valid_p6_event: dict[str, Any] = {
        "event_id": "evt-0196ed70-0001",
        "run_id": "run-pe-demo-001",
        "trace_id": "0af7651916cd43dd8448eb211c80319c",
        "span_id": "b7ad6b7169203331",
        "parent_span_id": None,
        "timestamp_utc": "2026-10-31T12:00:00Z",
        "stage_id": "STAGE_05_AXI_DV_STATIC_GATES",
        "phase_id": "P6_VERIFICATION",
        "gate_id": "G2",
        "rule_code": "KEY-01",
        "contract_version": "6.0",
        "confidence": 1.0,
        "workbench_view_id": "WB-04",
        "viz_id": "VIZ-GATE-02",
        "agent_persona": "axi-dv",
        "status": "AUTO_REPAIRED",
        "remediation": {
            "codemod_type": "INJECT_BKCC_NAMESPACE",
            "target_entity": "hub_limited_partner",
            "patch_diff": "+ bkcc_prefix: LEGACY_GP_ADMIN",
            "action_description": "Scoped legacy LP-101 under LEGACY_GP_ADMIN BKCC",
        },
        "rule_ids_evaluated": ["KEY-01", "ALG-A5"],
        "data_classification": "INTERNAL",
        "layer1_story_banner": {
            "headline": "Gate 2 Homonym Collision Prevented via BKCC",
            "business_impact": "Prevented CalPERS and Bavarian Pension from merging on LP-101.",
            "verdict_pill": "AUTO_REPAIRED",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [
                {
                    "pill_label": (
                        f"[HK: {calpers_hk[:4].lower()}..{calpers_hk[-4:].lower()} "
                        "| GLOBAL:LP-101 CalPERS]"
                    ),
                    "full_sha256_hex": calpers_hk,
                    "bkcc_prefix": "GLOBAL",
                    "business_key": "LP-101",
                    "entity_label": "CalPERS",
                }
            ],
            "summary_metrics": {
                "collisions_detected": 1,
                "auto_repaired": True,
            },
        },
        "layer3_formal_proof": {
            "algorithm_ref": "Algorithm A.5",
            "formula_latex": (
                r"H(\text{6:GLOBAL||6:LP-101}) \neq "
                r"H(\text{15:LEGACY\_GP\_ADMIN||6:LP-101})"
            ),
        },
    }

    parsed = validate_observability_event(valid_p6_event)
    assert isinstance(parsed, VaultObservabilityEvent)
    assert parsed.gate_id == "G2"

    # Negative case 1: P6_VERIFICATION without gate_id must fail
    missing_gate = {**valid_p6_event, "gate_id": None}
    with pytest.raises(ValidationError):
        VaultObservabilityEvent.model_validate(missing_gate)

    # Negative case 2: AUTO_REPAIRED without remediation must fail
    missing_remediation = {**valid_p6_event, "remediation": None}
    with pytest.raises(ValidationError):
        VaultObservabilityEvent.model_validate(missing_remediation)

    # Negative case 3: Non-P6 phase with gate_id set must fail
    invalid_non_p6 = {
        **valid_p6_event,
        "phase_id": "P2_PROFILING",
        "gate_id": "G2",
    }
    with pytest.raises(ValidationError):
        VaultObservabilityEvent.model_validate(invalid_non_p6)
