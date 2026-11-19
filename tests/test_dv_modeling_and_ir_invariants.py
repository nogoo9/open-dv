"""Phase 2 TDD Tests: Portable IR, JCS Binding, 100% Triage, Anti-Silo Hubs & Surrogates (`FR-DVM-02..04`).

Covers:
1. `FR-DVM-02`: `CanonicalIR` + `NoMistakeDataVault2ContractV5` bound via `VaultBlueprint` RFC 8785 JCS SHA-256.
2. `FR-DVM-03`: 100% `column_triage_ledger` (`unaccounted_columns == 0`, `DV-G4-002` rejects missing columns,
   `QUARANTINED_SURROGATE` role support).
3. `FR-DVM-04`: Anti-Silo Hubs (`AP-11`, `ANTI_SILO_VIOLATION` on duplicate `ontology_concept_uri` Hubs,
   `PHANTOM_HUB_REJECTED` / `DV-G4-001`), surrogate quarantine (`SURROGATE_KEY_REJECTED`), and
   `TRIM(UPPER(CAST(...)))` Hub BK normalization.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from opendv_axi_verifier import verify_gate1_contract_and_grain
from opendv_core.models.contract_v5 import ColumnTriageEntry
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.models.vault_blueprint import (
    compute_contract_sha256,
    compute_ontology_sha256,
    create_vault_blueprint,
)
from opendv_core.schema_registry import validate_canonical_ir, validate_contract_v5
from opendv_duckdb_sandbox.compiler_4zone import compile_canonical_ir_to_4zone_sql
from opendv_duckdb_sandbox.ir_lowerer import lower_contract_to_canonical_ir

REPO_ROOT = Path(__file__).resolve().parents[1]
PE_DIR = REPO_ROOT / "examples" / "private_equity"


def _load_pe_fixtures() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    ont = dict(yaml.safe_load((PE_DIR / "pe_graph_a_ontology.yaml").read_text(encoding="utf-8")))
    gb = dict(json.loads((PE_DIR / "pe_graph_b_profile.json").read_text(encoding="utf-8")))
    contract = dict(yaml.safe_load((PE_DIR / "dv_pe_model_contract.yaml").read_text(encoding="utf-8")))
    return ont, gb, contract


def test_canonical_ir_and_contract_jcs_sha256_binding() -> None:
    """`CanonicalIR` and `NoMistakeDataVault2ContractV5` bind deterministically to Graph A JCS SHA-256."""
    ont_raw, gb_raw, contract_raw = _load_pe_fixtures()
    ont_model = GraphAOntology.model_validate(ont_raw)
    contract_model = validate_contract_v5(contract_raw)

    blueprint = create_vault_blueprint(
        domain_namespace=ont_model.domain_namespace,
        ontology=ont_model,
        contract=contract_model,
    )
    assert blueprint.lineage.ontology_sha256 == compute_ontology_sha256(ont_model)
    assert blueprint.lineage.contract_sha256 == compute_contract_sha256(contract_model)

    ir_model = lower_contract_to_canonical_ir(contract_model, graph_b_profile=gb_raw, validate_schema=True)
    validated_ir = validate_canonical_ir(ir_model.model_dump(mode="json", exclude_none=True))
    assert {h.name for h in validated_ir.hubs} == {h.name for h in contract_model.hubs}
    assert {l.name for l in validated_ir.links} == {l.name for l in contract_model.links}

    # Mutating contract changes JCS SHA-256 deterministically
    mutated = copy.deepcopy(contract_raw)
    mutated["domain_namespace"] = "mutated_domain"
    assert compute_contract_sha256(validate_contract_v5(mutated)) != blueprint.lineage.contract_sha256


def test_100_percent_column_triage_ledger_and_quarantined_surrogate_role() -> None:
    """`column_triage_ledger` must achieve 100% column coverage and accept `QUARANTINED_SURROGATE`."""
    ont_raw, gb_raw, contract_raw = _load_pe_fixtures()
    diags = verify_gate1_contract_and_grain(
        contract_raw,
        ontology_graph=ont_raw,
        bronze_profile=gb_raw,
        strict_schema=True,
        check_full_ledger_coverage=True,
    )
    assert diags == []

    # Removing one ledger entry must trigger DV-G4-002 unaccounted column violation
    missing_ledger_contract = copy.deepcopy(contract_raw)
    missing_ledger_contract["column_triage_ledger"].pop()
    missing_diags = verify_gate1_contract_and_grain(
        missing_ledger_contract,
        ontology_graph=ont_raw,
        bronze_profile=gb_raw,
        check_full_ledger_coverage=True,
    )
    assert any("DV-G4-002" in d for d in missing_diags)

    # Surrogate quarantine classification must be valid in ColumnTriageEntry
    entry = ColumnTriageEntry(
        bronze_table="bronze.src_orders",
        column_name="order_sk",
        elm_classification="SYSTEM_SURROGATE_DROPPED",
        target_dv_entity="QUARANTINE",
    )
    assert entry.elm_classification == "SYSTEM_SURROGATE_DROPPED"


def test_anti_silo_hub_uniqueness_and_phantom_hub_rejection() -> None:
    """Duplicate siloed Hubs sharing an ontology URI trigger ANTI_SILO_VIOLATION; phantom Hubs fail."""
    ont_raw, gb_raw, contract_raw = _load_pe_fixtures()

    siloed = copy.deepcopy(contract_raw)
    dup_hub = copy.deepcopy(siloed["hubs"][0])
    dup_hub["name"] = "hub_limited_partner_silo_copy"
    siloed["hubs"].append(dup_hub)
    silo_diags = verify_gate1_contract_and_grain(siloed, ontology_graph=ont_raw, bronze_profile=gb_raw)
    assert any("ANTI_SILO_VIOLATION" in d for d in silo_diags)

    phantom = copy.deepcopy(contract_raw)
    phantom["hubs"].append(
        {
            "name": "hub_ungrounded_phantom",
            "ontology_concept_uri": "urn:ontology:entity:ungrounded_phantom",
            "business_key_sources": [
                {
                    "bronze_table": "bronze.nonexistent",
                    "key_columns": ["ghost_id"],
                    "record_source_code": "GHOST",
                    "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                    "require_src_qualifier": False,
                    "bkcc_prefix": "GLOBAL",
                }
            ],
        }
    )
    phantom_diags = verify_gate1_contract_and_grain(phantom, ontology_graph=ont_raw, bronze_profile=gb_raw)
    assert any("DV-G4-001" in d for d in phantom_diags)
    assert any("PHANTOM_HUB_REJECTED" in d for d in phantom_diags)


def test_surrogate_key_quarantine_and_bk_trim_upper_normalization() -> None:
    """Technical surrogate keys in Hub BKs trigger SURROGATE_KEY_REJECTED; Zone 1 normalizes BKs."""
    ont_raw, gb_raw, contract_raw = _load_pe_fixtures()
    surrogate_contract = copy.deepcopy(contract_raw)
    surrogate_gb = copy.deepcopy(gb_raw)
    surrogate_gb["source_systems"][0]["tables"][0]["columns"].append(
        {
            "column_name": "lp_seq_sk",
            "data_type": "BIGINT",
            "pattern_class": "MONOTONIC_INT",
            "candidate_role": "SURROGATE_SEQUENCE",
        }
    )
    surrogate_contract["hubs"][0]["business_key_sources"][0]["key_columns"] = ["lp_seq_sk"]
    diags = verify_gate1_contract_and_grain(
        surrogate_contract,
        ontology_graph=ont_raw,
        bronze_profile=surrogate_gb,
    )
    assert any("SURROGATE_KEY_REJECTED" in d for d in diags)

    # Zone 1 SQL normalizes Hub BKs with UPPER(TRIM(CAST(... AS VARCHAR)))
    ir_model = lower_contract_to_canonical_ir(validate_contract_v5(contract_raw), graph_b_profile=gb_raw)
    bundle = compile_canonical_ir_to_4zone_sql(ir_model)
    assert "UPPER(TRIM(CAST(" in bundle.zone_1_sql
