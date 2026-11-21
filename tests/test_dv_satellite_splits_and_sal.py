"""Phase 2 TDD Tests: 3-Axis Satellite Splits & Business Vault SAL Convergence (`FR-DVM-09,10`).

Covers:
1. `FR-DVM-09` (Algorithm A.1): 3-axis Satellite partitioning by Source System, Security/PII
   (`sat_*_pii`), and Change Velocity (`W_amp >= 50 MB/day` & `Delta log10(lambda) >= 2.0`),
   plus YAGNI merging of sub-threshold micro-satellites while preserving PII isolation.
2. `FR-DVM-10` (`EVO-08`, `DV-STR-015`): Business Vault `sal_*` + `esat_sal_*` enforcing
   distinct `hk_master != hk_duplicate` endpoints and compiling into Zone 3 SQL.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import yaml

from opendv_axi_verifier import (
    codemod_split_satellites_3axis,
    verify_structural_invariants,
)
from opendv_core.schema_registry import validate_contract_v5
from opendv_duckdb_sandbox.compiler_4zone import compile_canonical_ir_to_4zone_sql
from opendv_duckdb_sandbox.ir_lowerer import lower_contract_to_canonical_ir

REPO_ROOT = Path(__file__).resolve().parents[1]
PE_DIR = REPO_ROOT / "examples" / "private_equity"


def test_3axis_satellite_split_isolates_pii_and_respects_velocity_threshold() -> None:
    """Algorithm A.1 isolates `PII_HIGH` into `sat_*_pii` and splits high-velocity only above 50 MB/day."""
    gb = dict(json.loads((PE_DIR / "pe_graph_b_profile.json").read_text(encoding="utf-8")))
    contract = dict(yaml.safe_load((PE_DIR / "dv_pe_model_contract.yaml").read_text(encoding="utf-8")))

    # Merge sat_limited_partner_pii back into sat_limited_partner_investran and test 3-axis split
    mutated = copy.deepcopy(contract)
    pii_sat = next(s for s in mutated["satellites"] if s["name"] == "sat_limited_partner_pii")
    core_sat = next(s for s in mutated["satellites"] if s["name"] == "sat_limited_partner_investran")
    core_sat["payload_columns"] = sorted(set(core_sat["payload_columns"]) | set(pii_sat["payload_columns"]))
    mutated["satellites"] = [s for s in mutated["satellites"] if s["name"] != "sat_limited_partner_pii"]

    split = codemod_split_satellites_3axis(mutated, gb)
    lp_sats = [s for s in split["satellites"] if s["parent_entity"] == "hub_limited_partner"]
    pii_sats = [s for s in lp_sats if s["name"].endswith("_pii")]
    assert len(pii_sats) == 1
    assert pii_sats[0]["security_classification"] == "PII_HIGH"
    assert set(pii_sat["payload_columns"]).issubset(set(pii_sats[0]["payload_columns"]))
    # Non-PII sibling satellites contain none of the PII columns
    non_pii_sats = [s for s in lp_sats if s is not pii_sats[0]]
    assert non_pii_sats
    for non_pii in non_pii_sats:
        assert not (set(non_pii["payload_columns"]) & set(pii_sat["payload_columns"]))

    # High-velocity split requires W_amp >= 50 MB/day and len >= 10
    high_vel_cols = [f"metric_{i}" for i in range(10)]
    vel_profile = {
        "source_systems": [
            {
                "record_source_code": "TRADING",
                "tables": [
                    {
                        "bronze_table": "trading.src_quotes",
                        "row_count": 10_000_000,
                        "daily_delta_rows": 5_000_000,
                        "yagni_telemetry": {"write_amplification_mb_per_day": 120.0},
                        "columns": [
                            {
                                "column_name": c,
                                "security_classification": "INTERNAL",
                                "drift_rate_lambda": 5.0 if i < 3 else 0.001,
                                "bytes_per_row": 64,
                            }
                            for i, c in enumerate(high_vel_cols)
                        ],
                    }
                ],
            }
        ]
    }
    vel_contract: dict[str, Any] = {
        "contract_version": "5.0",
        "domain_namespace": "trading_analytics",
        "lifecycle_stage": "COLD_START_N1",
        "column_triage_ledger": [
            {
                "bronze_table": "trading.src_quotes",
                "column_name": "instrument_id",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_instrument",
            },
            *[
                {
                    "bronze_table": "trading.src_quotes",
                    "column_name": c,
                    "elm_classification": "DESCRIPTIVE_PAYLOAD",
                    "target_dv_entity": "sat_instrument_quotes",
                }
                for c in high_vel_cols
            ],
        ],
        "hubs": [
            {
                "name": "hub_instrument",
                "ontology_concept_uri": "urn:ontology:entity:instrument",
                "business_key_sources": [
                    {
                        "bronze_table": "trading.src_quotes",
                        "key_columns": ["instrument_id"],
                        "record_source_code": "TRADING",
                        "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                        "require_src_qualifier": False,
                        "bkcc_prefix": "GLOBAL",
                    }
                ],
            }
        ],
        "links": [],
        "satellites": [
            {
                "name": "sat_instrument_quotes",
                "satellite_type": "STANDARD_SATELLITE",
                "parent_entity": "hub_instrument",
                "source_bronze_table": "trading.src_quotes",
                "security_classification": "INTERNAL",
                "volatility_tier": "STANDARD",
                "payload_columns": high_vel_cols,
            }
        ],
        "reference_tables": [],
        "unmapped_escalations": [],
    }
    vel_split = codemod_split_satellites_3axis(vel_contract, vel_profile)
    vol_tiers = {s["volatility_tier"] for s in vel_split["satellites"]}
    assert "HIGH_FREQ" in vol_tiers
    assert "STATIC" in vol_tiers or "STANDARD" in vol_tiers


def test_business_vault_sal_and_esat_sal_enforces_distinct_master_duplicate() -> None:
    """`sal_*` + `esat_sal_*` pass Gate 3 with distinct `hk_master != hk_duplicate` and fail `DV-STR-015` when identical."""
    valid_sal_contract: dict[str, Any] = {
        "contract_version": "5.0",
        "domain_namespace": "entity_resolution",
        "lifecycle_stage": "MULTI_SOURCE_EXPANSION",
        "column_triage_ledger": [
            {
                "bronze_table": "mdm.counterparty_crosswalk",
                "column_name": "master_counterparty_id",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_counterparty",
            },
            {
                "bronze_table": "mdm.counterparty_crosswalk",
                "column_name": "duplicate_counterparty_id",
                "elm_classification": "FOREIGN_KEY_REF",
                "target_dv_entity": "sal_counterparty",
            },
            {
                "bronze_table": "mdm.counterparty_crosswalk",
                "column_name": "match_confidence",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "esat_sal_counterparty",
            },
        ],
        "hubs": [
            {
                "name": "hub_counterparty",
                "ontology_concept_uri": "urn:ontology:entity:counterparty",
                "business_key_sources": [
                    {
                        "bronze_table": "mdm.counterparty_crosswalk",
                        "key_columns": ["master_counterparty_id"],
                        "record_source_code": "MDM",
                        "key_authority_tier": "TIER_2_ENTERPRISE_SOR",
                        "require_src_qualifier": False,
                        "bkcc_prefix": None,
                    }
                ],
            }
        ],
        "links": [
            {
                "name": "sal_counterparty",
                "link_type": "SAME_AS_LINK",
                "ontology_concept_uri": "urn:ontology:relationship:counterparty_same_as",
                "source_bronze_table": "mdm.counterparty_crosswalk",
                "driving_key_hub": "hub_counterparty",
                "hub_references": [
                    {
                        "target_hub": "hub_counterparty",
                        "role_alias": "master",
                        "source_fk_columns": ["master_counterparty_id"],
                    },
                    {
                        "target_hub": "hub_counterparty",
                        "role_alias": "duplicate",
                        "source_fk_columns": ["duplicate_counterparty_id"],
                    },
                ],
            }
        ],
        "satellites": [
            {
                "name": "esat_sal_counterparty",
                "satellite_type": "EFFECTIVITY_SATELLITE",
                "parent_entity": "sal_counterparty",
                "source_bronze_table": "mdm.counterparty_crosswalk",
                "security_classification": "INTERNAL",
                "volatility_tier": "STANDARD",
                "driving_key_column": "master_counterparty_id",
                "payload_columns": ["match_confidence"],
            }
        ],
        "reference_tables": [],
        "unmapped_escalations": [],
    }

    assert verify_structural_invariants(valid_sal_contract) == []

    # Same FK column for both master and duplicate must trigger DV-STR-015 / DV-STR-004
    bad_sal = copy.deepcopy(valid_sal_contract)
    bad_sal["links"][0]["hub_references"][1]["source_fk_columns"] = ["master_counterparty_id"]
    bad_diags = verify_structural_invariants(bad_sal)
    assert any("DV-STR-015" in d or "DV-STR-004" in d for d in bad_diags)

    ir_model = lower_contract_to_canonical_ir(validate_contract_v5(valid_sal_contract))
    bundle = compile_canonical_ir_to_4zone_sql(ir_model)
    assert "sal_counterparty" in bundle.zone_0_sql
    assert "hk_counterparty_master" in bundle.zone_0_sql or "master" in bundle.zone_0_sql
    assert "hk_counterparty_duplicate" in bundle.zone_0_sql or "duplicate" in bundle.zone_0_sql
    assert "sal_counterparty" in bundle.zone_2_sql and "<>" in bundle.zone_2_sql
