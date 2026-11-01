"""Comprehensive unit tests for Work Package WP-04: `opendv-axi-verifier`.

Covers:
  - Algorithm A.1: 3-Axis Satellite Split & Gate 4 YAGNI Cost Guard (`W_amp >= 50 MB/day`)
  - Algorithm A.2: Low-Cardinality Enum Hub-to-Ref Demotion
  - Algorithm A.3: Link Effectivity Satellite (`esat_*`) Auto-Injection
  - Algorithm A.4: 3-Tier BKCC Authority & Nullable-FK Link Splitter (`U_nn >= 0.95`)
  - Algorithm A.5: SQLGlot AST Canonical Length-Prefixed SHA-256 Hash Recipe Builder
  - Algorithm A.6: SQLGlot AST Static Verifier (`DV-AST-001`-`006`)
  - Gate 1 & Gate 2: Contract Schema, Grain Statement (`>= 8` words), 100% Column Triage Ledger
  - Gate 3: NetworkX Bipartite DAG Structural Verifier (`DV-STR-001`-`014` & `DV-COL-001`-`012`)
  - Algorithm A.7: `N -> N+1` Additive Evolution Lock Verifier (`DV-EVO-001`-`006`)
  - Algorithm A.9a: Intra-Source Schema Drift Detector & Codemod (5 categories, Levenshtein >= 0.85)
  - Algorithm A.9b: Unstructured Document Grounding, Span Substring & 3-Tier ER Routing
  - `@toon_v1` Codec: Serializer & Parser Round-Trip
  - Algorithm A.8: Orchestrator `run_axi_dv_verification_pipeline` & Private Equity Demo
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from opendv_axi_verifier import (
    ToonTrace,
    ToonViolation,
    build_canonical_sha256_hash_ast,
    build_toon_trace_from_diagnostics,
    classify_er_resolution_tier,
    codemod_demote_enum_hubs_to_ref,
    codemod_enforce_cold_start_extensibility,
    codemod_enforce_staging_hashes_sql,
    codemod_extract_hub_mutable_attributes,
    codemod_inject_link_effectivity,
    codemod_intra_source_schema_drift,
    codemod_split_satellites_3axis,
    detect_intra_source_schema_drift,
    escalate_classification,
    evaluate_unstructured_er_routing,
    is_lossless_type_widening,
    levenshtein_similarity,
    parse_toon_v1,
    run_axi_dv_verification_pipeline,
    serialize_toon_v1,
    verify_additive_evolution_invariants,
    verify_column_invariants,
    verify_gate1_contract_and_grain,
    verify_gate5_unstructured_invariants,
    verify_grain_statement_word_count,
    verify_physical_dv2_sql_ast,
    verify_structural_invariants,
)
from opendv_telemetry import ContentAddressableStore, VaultLedgerEmitter

REPO_ROOT = Path(__file__).resolve().parents[3]
PE_EXAMPLES_DIR = REPO_ROOT / "examples" / "private_equity"


@pytest.fixture()
def pe_ontology() -> dict[str, Any]:
    path = PE_EXAMPLES_DIR / "pe_graph_a_ontology.yaml"
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")))


@pytest.fixture()
def pe_bronze_profile() -> dict[str, Any]:
    path = PE_EXAMPLES_DIR / "pe_graph_b_profile.json"
    return dict(json.loads(path.read_text(encoding="utf-8")))


@pytest.fixture()
def pe_golden_contract() -> dict[str, Any]:
    path = PE_EXAMPLES_DIR / "dv_pe_model_contract.yaml"
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")))


# ==============================================================================
# 1. Algorithm A.1: 3-Axis Satellite Split & Gate 4 YAGNI Cost Guard
# ==============================================================================


def test_alg_a1_merges_pe_fx_micro_satellite_and_preserves_pii(
    pe_golden_contract: dict[str, Any],
    pe_bronze_profile: dict[str, Any],
) -> None:
    """Verify Algorithm A.1 merges `sat_lp_commitment_fx` (at W_amp = 4.20 MB/day < 50 MB/day)
    into `sat_lp_commitment_investran` while preserving `sat_limited_partner_pii`.
    """
    mutated = copy.deepcopy(pe_golden_contract)

    # Split FX columns out into premature micro-satellite `sat_lp_commitment_fx`
    fx_cols = ["eur_usd_rate", "gbp_usd_rate", "daily_interim_nav_usd"]
    for sat in mutated["satellites"]:
        if sat["name"] == "sat_lp_commitment_investran":
            sat["payload_columns"] = [c for c in sat["payload_columns"] if c not in fx_cols]

    mutated["satellites"].append(
        {
            "name": "sat_lp_commitment_fx",
            "parent_entity": "lnk_lp_commitment",
            "satellite_type": "STANDARD_SATELLITE",
            "source_bronze_table": "investran.src_lp_commitments_and_calls",
            "security_classification": "CONFIDENTIAL",
            "volatility_tier": "HIGH_FREQ",
            "payload_columns": fx_cols,
        }
    )
    for entry in mutated["column_triage_ledger"]:
        if (
            entry["bronze_table"] == "investran.src_lp_commitments_and_calls"
            and entry["column_name"] in fx_cols
        ):
            entry["target_dv_entity"] = "sat_lp_commitment_fx"

    repaired = codemod_split_satellites_3axis(mutated, pe_bronze_profile)
    sat_names = {s["name"] for s in repaired["satellites"]}

    assert "sat_lp_commitment_fx" not in sat_names
    assert "sat_lp_commitment_investran" in sat_names
    assert "sat_limited_partner_pii" in sat_names

    comm_sat = next(s for s in repaired["satellites"] if s["name"] == "sat_lp_commitment_investran")
    for col in fx_cols:
        assert col in comm_sat["payload_columns"]

    pii_sat = next(s for s in repaired["satellites"] if s["name"] == "sat_limited_partner_pii")
    assert pii_sat["security_classification"] == "PII_HIGH"
    assert set(pii_sat["payload_columns"]) == {"tax_id", "wire_iban"}

    for entry in repaired["column_triage_ledger"]:
        assert entry["target_dv_entity"] != "sat_lp_commitment_fx"


def test_alg_a1_splits_when_w_amp_exceeds_50mb_per_day() -> None:
    """Verify Algorithm A.1 splits PII and cadence tiers when W_amp >= 50 MB/day."""
    assert escalate_classification(["PUBLIC", "CONFIDENTIAL", "PII_HIGH"]) == "PII_HIGH"
    assert escalate_classification(["PUBLIC", "PCI"]) == "PCI"

    cols = ["ssn", "tax_id"] + [f"static_{i}" for i in range(6)] + [f"hf_{i}" for i in range(4)]
    contract = {
        "satellites": [
            {
                "name": "sat_investor",
                "parent_entity": "hub_investor",
                "satellite_type": "STANDARD_SATELLITE",
                "source_bronze_table": "crm.investors",
                "security_classification": "PUBLIC",
                "payload_columns": cols,
            }
        ],
        "column_triage_ledger": [
            {
                "source_table": "crm.investors",
                "column_name": c,
                "dv_target_role": "SATELLITE_PAYLOAD",
                "target_entity": "sat_investor",
            }
            for c in cols
        ],
    }
    profile: dict[str, dict[str, Any]] = {"crm.investors": {}}
    for c in ("ssn", "tax_id"):
        profile["crm.investors"][c] = {
            "security_classification": "PII_HIGH",
            "change_freq": 0.0001,
            "avg_width_bytes": 64,
            "active_rows": 2_000_000,
        }
    for i in range(6):
        profile["crm.investors"][f"static_{i}"] = {
            "security_classification": "CONFIDENTIAL",
            "change_freq": 0.0001,
            "avg_width_bytes": 128,
            "active_rows": 2_000_000,
        }
    for i in range(4):
        profile["crm.investors"][f"hf_{i}"] = {
            "security_classification": "PUBLIC",
            "change_freq": 0.5,
            "avg_width_bytes": 64,
            "active_rows": 2_000_000,
        }

    res = codemod_split_satellites_3axis(contract, profile)
    names = {s["name"] for s in res["satellites"]}
    assert names == {"sat_investor_pii", "sat_investor_static", "sat_investor_high_freq"}


# ==============================================================================
# 2. Algorithm A.2: Low-Cardinality Enum Hub-to-Ref Demotion
# ==============================================================================


def test_alg_a2_enum_demotion_rewires_links_and_satellites(
    pe_ontology: dict[str, Any],
    pe_bronze_profile: dict[str, Any],
) -> None:
    """Verify Algorithm A.2 demotes an enum Hub (`hub_currency`) to `ref_currency`,
    folds its Satellite into `description_columns`, and dissolves a 2-way Link to 1 Hub.
    """
    contract = {
        "hubs": [
            {
                "name": "hub_fund",
                "ontology_concept_uri": "urn:ontology:pe:fund",
                "business_key_sources": [
                    {
                        "bronze_table": "investran.src_funds",
                        "record_source_code": "INVESTRAN_GP_CORE",
                        "key_columns": ["fund_code"],
                    }
                ],
            },
            {
                "name": "hub_currency",
                "ontology_concept_uri": "urn:ontology:enum:iso_currency",
                "business_key_sources": [
                    {
                        "bronze_table": "investran.src_funds",
                        "record_source_code": "INVESTRAN_GP_CORE",
                        "key_columns": ["base_currency_iso"],
                    }
                ],
            },
        ],
        "links": [
            {
                "name": "lnk_fund_currency",
                "link_type": "STANDARD_LINK",
                "source_bronze_table": "investran.src_funds",
                "hub_references": [
                    {
                        "target_hub": "hub_fund",
                        "role_alias": "FUND",
                        "source_fk_columns": ["fund_code"],
                    },
                    {
                        "target_hub": "hub_currency",
                        "role_alias": "CURRENCY",
                        "source_fk_columns": ["base_currency_iso"],
                    },
                ],
                "degenerate_columns": [],
            }
        ],
        "satellites": [
            {
                "name": "sat_fund_investran",
                "parent_entity": "hub_fund",
                "satellite_type": "STANDARD_SATELLITE",
                "source_bronze_table": "investran.src_funds",
                "payload_columns": ["fund_legal_name"],
            },
            {
                "name": "sat_currency_desc",
                "parent_entity": "hub_currency",
                "satellite_type": "STANDARD_SATELLITE",
                "source_bronze_table": "investran.src_funds",
                "payload_columns": ["currency_name"],
            },
        ],
        "reference_tables": [],
        "column_triage_ledger": [
            {
                "source_table": "investran.src_funds",
                "column_name": "base_currency_iso",
                "dv_target_role": "HUB_BUSINESS_KEY",
                "target_entity": "hub_currency",
            }
        ],
    }

    res = codemod_demote_enum_hubs_to_ref(contract, pe_ontology, pe_bronze_profile)
    assert [h["name"] for h in res["hubs"]] == ["hub_fund"]
    assert len(res["reference_tables"]) == 1
    assert res["reference_tables"][0]["name"] == "ref_currency"
    assert res["reference_tables"][0]["description_columns"] == ["currency_name"]
    assert res["links"] == []
    fund_sat = res["satellites"][0]
    assert "base_currency_iso" in fund_sat["payload_columns"]
    assert res["column_triage_ledger"][0]["dv_target_role"] == "REFERENCE_CODE"
    assert res["column_triage_ledger"][0]["target_entity"] == "ref_currency"


# ==============================================================================
# 3. Algorithm A.3: Link Effectivity Satellite (`esat_*`) Auto-Injection
# ==============================================================================


def test_alg_a3_injects_effectivity_satellite() -> None:
    """Verify Algorithm A.3 injects `esat_*` when CDC FK swap rate > 0."""
    contract = {
        "links": [
            {
                "name": "lnk_deal_lead_partner",
                "link_type": "STANDARD_LINK",
                "source_bronze_table": "dealcloud.deals",
                "hub_references": [
                    {
                        "target_hub": "hub_portfolio_company",
                        "role_alias": "PORTCO",
                        "source_fk_columns": ["portco_lei"],
                    },
                    {
                        "target_hub": "hub_partner",
                        "role_alias": "LEAD_PARTNER",
                        "source_fk_columns": ["lead_partner_id"],
                    },
                ],
            }
        ],
        "satellites": [],
    }
    cdc_profile = {"dealcloud.deals": {"portco_lei": 0.04, "lead_partner_id": 0.0}}
    res = codemod_inject_link_effectivity(contract, cdc_profile)

    assert res["links"][0]["driving_key_hub"] == "hub_portfolio_company"
    assert len(res["satellites"]) == 1
    esat = res["satellites"][0]
    assert esat["name"] == "esat_deal_lead_partner"
    assert esat["satellite_type"] == "EFFECTIVITY_SATELLITE"
    assert esat["driving_key_column"] == "portco_lei"
    assert esat["payload_columns"] == ["START_DTS", "END_DTS", "IS_CURRENT_FLAG"]


# ==============================================================================
# 4. Algorithm A.4: 3-Tier BKCC Authority & Nullable-FK Link Splitter
# ==============================================================================


def test_alg_a4_cold_start_splits_co_invest_and_injects_bkcc(
    pe_golden_contract: dict[str, Any],
    pe_ontology: dict[str, Any],
    pe_bronze_profile: dict[str, Any],
) -> None:
    """Verify Algorithm A.4 injects `BKCC = 'LEGACY_GP_ADMIN'` on Tier-3 `hub_limited_partner`
    and splits `co_invest_portco_lei` (`U_nn = 0.286 < 0.95`) into `lnk_lp_commitment_co_invest`.
    """
    naive = copy.deepcopy(pe_golden_contract)

    # Corrupt Tier-3 BKCC on hub_limited_partner
    for hub in naive["hubs"]:
        if hub["name"] == "hub_limited_partner":
            for src in hub["business_key_sources"]:
                if src["record_source_code"] == "LEGACY_GP_ADMIN":
                    src["key_authority_tier"] = "TIER_2_ENTERPRISE_SOR"
                    src["require_src_qualifier"] = False
                    src["bkcc_prefix"] = None
                    src["system_scoped_bk_justification"] = None

    # Merge co_invest_portco_lei back into lnk_lp_commitment as a 3-way Link
    naive["links"] = [lnk for lnk in naive["links"] if lnk["name"] != "lnk_lp_commitment_co_invest"]
    for lnk in naive["links"]:
        if lnk["name"] == "lnk_lp_commitment":
            lnk["hub_references"].append(
                {
                    "target_hub": "hub_portfolio_company",
                    "role_alias": "CO_INVEST_ASSET",
                    "source_fk_columns": ["co_invest_portco_lei"],
                }
            )
    for entry in naive["column_triage_ledger"]:
        if entry["column_name"] == "co_invest_portco_lei":
            entry["target_dv_entity"] = "lnk_lp_commitment"

    repaired = codemod_enforce_cold_start_extensibility(naive, pe_ontology, pe_bronze_profile)

    # Check Tier-3 BKCC restoration
    lp_hub = next(h for h in repaired["hubs"] if h["name"] == "hub_limited_partner")
    legacy_src = next(
        s for s in lp_hub["business_key_sources"] if s["record_source_code"] == "LEGACY_GP_ADMIN"
    )
    assert legacy_src["key_authority_tier"] == "TIER_3_LOCAL_SCOPED"
    assert legacy_src["require_src_qualifier"] is True
    assert legacy_src["bkcc_prefix"] == "LEGACY_GP_ADMIN"
    assert legacy_src["system_scoped_bk_justification"] is not None

    # Check Link split
    link_names = {lnk["name"] for lnk in repaired["links"]}
    assert "lnk_lp_commitment" in link_names
    assert "lnk_lp_commitment_co_invest" in link_names

    base_lnk = next(lnk for lnk in repaired["links"] if lnk["name"] == "lnk_lp_commitment")
    assert len(base_lnk["hub_references"]) == 2

    co_lnk = next(lnk for lnk in repaired["links"] if lnk["name"] == "lnk_lp_commitment_co_invest")
    assert co_lnk["where_filter_condition"] == "co_invest_portco_lei IS NOT NULL"
    assert len(co_lnk["hub_references"]) == 3


def test_alg_a4_extract_hub_mutable_attributes() -> None:
    """Verify Codemod 1 evicts mutable attributes from Hub `key_columns` into a Satellite."""
    contract = {
        "hubs": [
            {
                "name": "hub_fund",
                "business_key_sources": [
                    {
                        "bronze_table": "investran.src_funds",
                        "record_source_code": "INVESTRAN",
                        "key_columns": ["fund_code", "target_size_usd"],
                    }
                ],
            }
        ],
        "satellites": [],
        "column_triage_ledger": [
            {
                "source_table": "investran.src_funds",
                "column_name": "target_size_usd",
                "dv_target_role": "HUB_BUSINESS_KEY",
                "target_entity": "hub_fund",
            }
        ],
    }
    repaired = codemod_extract_hub_mutable_attributes(
        contract, {"investran.src_funds": {"target_size_usd"}}
    )
    assert repaired["hubs"][0]["business_key_sources"][0]["key_columns"] == ["fund_code"]
    assert len(repaired["satellites"]) == 1
    assert repaired["satellites"][0]["payload_columns"] == ["target_size_usd"]
    assert repaired["column_triage_ledger"][0]["dv_target_role"] == "SATELLITE_PAYLOAD"


# ==============================================================================
# 5. Algorithm A.5 & A.6: SQLGlot Hash Recipe Builder & Physical AST Verifier
# ==============================================================================


def test_alg_a5_and_a6_sqlglot_hash_recipe_and_linter() -> None:
    """Verify Algorithm A.5 synthesizes canonical length-prefixed SHA-256 SQL that passes
    Algorithm A.6 (`DV-AST-001`-`006`), while malformed SQL triggers `DV-AST-001`-`006`.
    """
    ast_node = build_canonical_sha256_hash_ast(
        ["lp_number"],
        is_business_key=True,
        bkcc_prefix="LEGACY_GP_ADMIN",
    )
    sql_expr = ast_node.sql("duckdb")
    assert "15:LEGACY_GP_ADMIN" in sql_expr
    assert "-1:^^" in sql_expr
    assert "OCTET_LENGTH" in sql_expr

    raw_staging_sql = (
        "SELECT fund_code, fund_legal_name, CURRENT_TIMESTAMP AS load_dts, "
        "'INVESTRAN' AS rec_src FROM src_funds"
    )
    rewritten_sql = codemod_enforce_staging_hashes_sql(
        raw_staging_sql,
        {
            "hk_hub_fund": {
                "columns": ["fund_code"],
                "is_business_key": True,
                "bkcc_prefix": None,
                "sort_alphabetically": False,
            },
            "hash_diff": {
                "columns": ["target_size_usd", "fund_legal_name"],
                "is_business_key": False,
                "bkcc_prefix": None,
                "sort_alphabetically": True,
            },
        },
    )

    stage_violations = verify_physical_dv2_sql_ast(
        rewritten_sql, entity_name="stg_funds", entity_type="STAGE"
    )
    assert stage_violations == []

    # Test DV-AST-001..006 negative cases
    bad_sql = """
    DELETE FROM hub_fund WHERE fund_code IS NULL;
    UPDATE sat_fund SET target_size_usd = 0;
    INSERT INTO sat_fund
    SELECT *, SHA256(a || b || RANDOM()) AS hk_hub_fund, SHA256(c) AS hash_diff
    FROM stg_fund
    GROUP BY a, b, c;
    """
    violations = verify_physical_dv2_sql_ast(
        bad_sql, entity_name="sat_fund", entity_type="STANDARD_SATELLITE"
    )
    joined = "\n".join(violations)
    assert "DV-AST-001" in joined
    assert "DV-AST-002" in joined
    assert "DV-AST-003" in joined
    assert "DV-AST-004" in joined
    assert "DV-AST-005" in joined
    assert "DV-AST-006" in joined


# ==============================================================================
# 6. Gate 1 & Gate 3: Contract Grain & NetworkX Structural Verifiers
# ==============================================================================


def test_gate1_grain_statement_and_contract_checks(
    pe_golden_contract: dict[str, Any],
    pe_ontology: dict[str, Any],
    pe_bronze_profile: dict[str, Any],
) -> None:
    """Verify Gate 1 validates the Private Equity golden contract and rejects short
    grain statements (< 8 words), phantom Hubs, and unaccounted Bronze columns.
    """
    assert verify_grain_statement_word_count(
        "One record per unique investor commitment contract to a specific fund."
    )
    assert not verify_grain_statement_word_count("Too short grain statement.")

    diags = verify_gate1_contract_and_grain(
        pe_golden_contract,
        pe_ontology,
        pe_bronze_profile,
        strict_schema=True,
        check_full_ledger_coverage=True,
    )
    assert diags == []

    bad = copy.deepcopy(pe_golden_contract)
    bad["grain_statement"] = "Too short"
    bad["column_triage_ledger"].pop()
    bad_diags = verify_gate1_contract_and_grain(
        bad,
        pe_ontology,
        pe_bronze_profile,
        check_full_ledger_coverage=True,
    )
    assert any("DV-G1-GRAIN" in d for d in bad_diags)
    assert any("DV-G4-002" in d for d in bad_diags)


def test_gate3_all_14_dv_str_rules_and_column_invariants(
    pe_golden_contract: dict[str, Any],
) -> None:
    """Verify Gate 3 passes the golden Private Equity contract and catches violations
    across `DV-STR-001` through `DV-STR-014` and `DV-COL-001` through `DV-COL-012`.
    """
    assert verify_structural_invariants(pe_golden_contract) == []

    bad_topology: dict[str, Any] = {
        "hubs": [
            {
                "name": "hub_a",
                "foreign_keys": [{"target_hub": "hub_b"}],  # DV-STR-001
            },
            {"name": "hub_b"},
            {"name": "hub_isolated"},  # DV-STR-013
        ],
        "links": [
            {
                "name": "lnk_unary",
                "link_type": "STANDARD_LINK",
                "source_bronze_table": "t",
                "hub_references": [
                    {
                        "target_hub": "hub_a",
                        "role_alias": "A",
                        "source_fk_columns": ["a_id"],
                    }
                ],  # DV-STR-002
            },
            {
                "name": "lnk_bad_target",
                "link_type": "HIERARCHICAL_LINK",
                "source_bronze_table": "t",
                "max_recursion_depth": 0,  # DV-STR-014
                "cycle_prevention_predicate": False,
                "hub_references": [
                    {
                        "target_hub": "hub_a",
                        "role_alias": "SAME",
                        "source_fk_columns": ["a_id"],
                    },
                    {
                        "target_hub": "hub_a",
                        "role_alias": "SAME",  # DV-STR-004
                        "source_fk_columns": ["a_id"],
                    },
                    {
                        "target_hub": "lnk_unary",  # DV-STR-003 / DV-G3-004
                        "role_alias": "LNK",
                        "source_fk_columns": ["u_id"],
                    },
                ],
                "grain_statement": "",  # DV-STR-011
            },
            {
                "name": "lnk_nhl_dup1",
                "link_type": "NON_HISTORIZED_LINK",
                "source_bronze_table": "t",
                "hub_references": [
                    {
                        "target_hub": "hub_a",
                        "role_alias": "A",
                        "source_fk_columns": ["a_id"],
                    },
                    {
                        "target_hub": "hub_b",
                        "role_alias": "B",
                        "source_fk_columns": ["b_id"],
                    },
                ],
            },
            {
                "name": "lnk_nhl_dup2",  # DV-STR-010 duplicate signature
                "link_type": "NON_HISTORIZED_LINK",
                "source_bronze_table": "t",
                "hub_references": [
                    {
                        "target_hub": "hub_a",
                        "role_alias": "A",
                        "source_fk_columns": ["a_id"],
                    },
                    {
                        "target_hub": "hub_b",
                        "role_alias": "B",
                        "source_fk_columns": ["b_id"],
                    },
                ],
            },
        ],
        "satellites": [
            {
                "name": "sat_on_nhl",
                "parent_entity": "lnk_nhl_dup1",  # DV-STR-009
                "satellite_type": "MULTI_ACTIVE_SATELLITE",
                "sub_sequence_column": None,  # DV-STR-008
                "source_mappings": ["s1", "s2"],  # DV-STR-012
                "payload_columns": ["val"],
            },
            {
                "name": "sat_chained",
                "parent_entity": "sat_on_nhl",  # DV-STR-006
                "satellite_type": "STANDARD_SATELLITE",
                "source_bronze_table": "t",
                "payload_columns": ["val2"],
            },
            {
                "name": "sat_multi_parent",
                "parent_entity": "hub_ghost",  # DV-STR-005
                "additional_parents": ["hub_b"],
                "satellite_type": "EFFECTIVITY_SATELLITE",  # DV-STR-007
                "source_bronze_table": "t",
                "driving_key_column": "invalid_fk",
                "payload_columns": ["START_DTS"],
            },
            {
                "name": "esat_bad_subset",
                "parent_entity": "lnk_nhl_dup1",
                "satellite_type": "EFFECTIVITY_SATELLITE",
                "source_bronze_table": "t",
                "driving_key_column": "not_in_link_fk",  # DV-STR-007
                "payload_columns": ["START_DTS"],
            },
        ],
    }

    v = verify_structural_invariants(bad_topology, check_isolated_hubs=True)
    joined_v = "\n".join(v)
    for code in (
        "DV-STR-001",
        "DV-STR-002",
        "DV-STR-003",
        "DV-STR-004",
        "DV-STR-005",
        "DV-STR-006",
        "DV-STR-007",
        "DV-STR-008",
        "DV-STR-009",
        "DV-STR-010",
        "DV-STR-011",
        "DV-STR-012",
        "DV-STR-013",
        "DV-STR-014",
    ):
        assert code in joined_v, f"Expected {code} in violations"

    col_v = verify_column_invariants(
        {
            "hubs": [
                {
                    "entity_id": "hub_bad",
                    "hk_digest_bits": 128,
                    "business_key_columns": [],
                    "payload_columns": ["illegal_attr"],
                    "has_cross_source_bk_collision": True,
                }
            ]
        }
    )
    assert any("DV-COL-001" in x for x in col_v)
    assert any("DV-COL-007" in x for x in col_v)
    assert any("DV-COL-012" in x for x in col_v)


# ==============================================================================
# 7. Algorithm A.7 & A.9a: Evolution Lock & 5-Category Intra-Source Schema Drift
# ==============================================================================


def test_alg_a9a_5_drift_categories_and_alg_a7_evolution_lock(
    pe_golden_contract: dict[str, Any],
) -> None:
    """Verify Algorithm A.9a detects all 5 schema drift categories (Additive,
    Soft-Deprecated, Renamed with Levenshtein >= 0.85, Type Widened, Key Drift)
    and produces an evolved contract that passes Algorithm A.7 (`DV-EVO-001`-`006`).
    """
    assert levenshtein_similarity("commitment_amount_usd", "commitment_amount_usds") >= 0.85
    assert is_lossless_type_widening("VARCHAR(64)", "VARCHAR(255)")
    assert is_lossless_type_widening("INT", "BIGINT")
    assert is_lossless_type_widening("NUMERIC(12,2)", "NUMERIC(18,2)")
    assert not is_lossless_type_widening("VARCHAR(255)", "VARCHAR(32)")

    findings = detect_intra_source_schema_drift(
        deployed_columns={
            "fund_code",
            "fund_legal_name",
            "commitment_amount_usd",
            "legacy_tax_code",
            "vintage_year",
        },
        candidate_columns={
            "fund_code",
            "fund_legal_name",
            "commitment_amount_usds",  # RENAMED (similarity ~ 0.95 >= 0.85)
            "vintage_year",
            "esg_article_classification",  # ADDITIVE
        },
        source_table="investran.src_funds",
        target_entity="sat_fund_investran",
        key_columns={"fund_code"},
        old_types={
            "fund_code": "VARCHAR(64)",
            "fund_legal_name": "VARCHAR(128)",
            "vintage_year": "VARCHAR(255)",
        },
        new_types={
            "fund_code": "VARCHAR(64)",
            "fund_legal_name": "VARCHAR(255)",  # TYPE_WIDENED
            "vintage_year": "INT",  # KEY_DRIFT / incompatible type change
        },
    )
    categories = {f.category for f in findings}
    assert categories == {
        "ADDITIVE",
        "SOFT_DEPRECATED",
        "RENAMED",
        "TYPE_WIDENED",
        "KEY_DRIFT",
    }

    # Test codemod_intra_source_schema_drift + Algorithm A.7 Evolution Lock
    baseline_v1 = copy.deepcopy(pe_golden_contract)
    baseline_v1["satellites"] = [
        s for s in baseline_v1["satellites"] if s["name"] != "sat_fund_investran_esg"
    ]
    candidate_v2 = copy.deepcopy(baseline_v1)
    for sat in candidate_v2["satellites"]:
        if sat["name"] == "sat_fund_investran":
            # Drop mgmt_fee_bps, rename target_size_usd -> target_size_usds, add 2 ESG cols
            sat["payload_columns"] = [
                "deprecated_fax_no",
                "fund_name",
                "isin",
                "target_size_usds",
                "vintage_year",
                "esg_article_classification",
                "esg_carbon_intensity_target",
            ]

    evolved_v2 = codemod_intra_source_schema_drift(
        baseline_v1,
        candidate_v2,
        schema_epoch_tag="esg",
        companion_name_override="sat_fund_investran_esg",
    )

    evo_violations = verify_additive_evolution_invariants(baseline_v1, evolved_v2)
    assert evo_violations == []

    esg_sat = next(s for s in evolved_v2["satellites"] if s["name"] == "sat_fund_investran_esg")
    assert esg_sat["volatility_tier"] == "ADDITIVE_DRIFT_COMPANION"
    assert esg_sat["payload_columns"] == [
        "esg_article_classification",
        "esg_carbon_intensity_target",
    ]

    orig_sat = next(s for s in evolved_v2["satellites"] if s["name"] == "sat_fund_investran")
    assert "mgmt_fee_bps" in orig_sat["soft_deprecated_columns"]
    assert orig_sat["staging_column_aliases"] == {"target_size_usd": "target_size_usds"}


# ==============================================================================
# 8. Algorithm A.9b: Unstructured Grounding & 3-Tier ER Confidence Margin Routing
# ==============================================================================


def test_alg_a9b_unstructured_spans_and_3tier_er_routing(
    pe_golden_contract: dict[str, Any],
    pe_bronze_profile: dict[str, Any],
) -> None:
    """Verify Algorithm A.9b validates unstructured spans and routes Tier-U1, Tier-U2,
    and Tier-U3 (`ASK-USER` on `Horizon Sovereign Co-Invest Vehicle` with margin 0.02 < 0.15).
    """
    assert classify_er_resolution_tier(1.00, 1.00) == ("TIER_U1_DETERMINISTIC", "PASS")
    assert classify_er_resolution_tier(0.91, 0.24) == ("TIER_U2_HYBRID_EXACT", "PASS")
    assert classify_er_resolution_tier(0.81, 0.02) == (
        "TIER_U3_CAPTAIN_HOLD",
        "ASK-USER",
    )

    decisions = evaluate_unstructured_er_routing(pe_bronze_profile)
    assert len(decisions) >= 3
    by_field = {d["field"]: d for d in decisions}

    assert by_field["investor_lei"]["computed_tier"] == "TIER_U1_DETERMINISTIC"
    assert by_field["investor_lei"]["action"] == "PASS"

    assert by_field["investor_name"]["computed_tier"] == "TIER_U2_HYBRID_EXACT"
    assert by_field["investor_name"]["action"] == "PASS"

    assert by_field["ambiguous_vehicle"]["computed_tier"] == "TIER_U3_CAPTAIN_HOLD"
    assert by_field["ambiguous_vehicle"]["action"] == "ASK-USER"
    assert by_field["ambiguous_vehicle"]["candidate_name"] == (
        "Horizon Sovereign Co-Invest Vehicle"
    )
    assert by_field["ambiguous_vehicle"]["span_errors"] == []

    # Verify Gate 5 unstructured invariants on PE golden contract
    v = verify_gate5_unstructured_invariants(
        pe_golden_contract,
        bronze_profile=pe_bronze_profile,
        enforce_er_holds_as_diagnostics=False,
    )
    assert v == []

    v_with_holds = verify_gate5_unstructured_invariants(
        pe_golden_contract,
        bronze_profile=pe_bronze_profile,
        enforce_er_holds_as_diagnostics=True,
    )
    assert len(v_with_holds) == 1
    assert "TIER_U3_CAPTAIN_HOLD" in v_with_holds[0]
    assert "Horizon Sovereign Co-Invest Vehicle" in v_with_holds[0]


# ==============================================================================
# 9. @toon_v1 Serializer & Parser Round-Trip
# ==============================================================================


def test_toon_v1_round_trip_serialization() -> None:
    """Verify `@toon_v1` serializer & parser round-trip matches Scene 5 (`WB-08`)."""
    trace = ToonTrace(
        run_id="run-pe-apex-horizon-20261030",
        attempt="1/3",
        status="REJECT-WITH-TRACE",
        violations=[
            ToonViolation(
                gate="G1",
                rule="DV-COL-012",
                action="REJECT",
                target="hub_limited_partner",
                fix="Set require_src_qualifier=true & bkcc_prefix='LEGACY_GP_ADMIN' on lp_num",
            ),
            ToonViolation(
                gate="G4",
                rule="ALG-A1",
                action="AUTO-FIX",
                target="sat_lp_comm_fx",
                fix="Merged 3 cols into sat_lp_commitment_inv (W_amp=4.20 MB/d < 50.0 MB/d)",
            ),
        ],
    )
    serialized = serialize_toon_v1(trace)
    assert serialized.startswith("@toon_v1\nrun_id: run-pe-apex-horizon-20261030\n")
    assert "violations[2]{gate,rule,action,target,fix}:" in serialized

    parsed = parse_toon_v1(serialized)
    assert parsed == trace

    from_diags = build_toon_trace_from_diagnostics(
        ["SEMANTIC_ERROR [Gate 1 - DV-COL-012]: Hub 'hub_limited_partner' lacks bkcc_prefix."],
        run_id="run-test-01",
    )
    assert from_diags.status == "REJECT-WITH-TRACE"
    assert parse_toon_v1(serialize_toon_v1(from_diags)) == from_diags


# ==============================================================================
# 10. Algorithm A.8 End-to-End Orchestrator & Telemetry Emission
# ==============================================================================


def test_alg_a8_orchestrator_end_to_end_pe_verification_and_telemetry(
    pe_golden_contract: dict[str, Any],
    pe_ontology: dict[str, Any],
    pe_bronze_profile: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Verify `run_axi_dv_verification_pipeline` repairs cold-start traps on the
    Private Equity contract, passes all 6 gates, generates `@toon_v1`, and emits
    schema-valid `opendv-telemetry` events.
    """
    candidate = copy.deepcopy(pe_golden_contract)
    # Introduce Trap 3 (missing Tier-3 BKCC) and Trap 4 (premature sat_lp_commitment_fx)
    for hub in candidate["hubs"]:
        if hub["name"] == "hub_limited_partner":
            for src in hub["business_key_sources"]:
                if src["record_source_code"] == "LEGACY_GP_ADMIN":
                    src["key_authority_tier"] = "TIER_2_ENTERPRISE_SOR"
                    src["require_src_qualifier"] = False
                    src["bkcc_prefix"] = None

    fx_cols = ["eur_usd_rate", "gbp_usd_rate", "daily_interim_nav_usd"]
    for sat in candidate["satellites"]:
        if sat["name"] == "sat_lp_commitment_investran":
            sat["payload_columns"] = [c for c in sat["payload_columns"] if c not in fx_cols]
    candidate["satellites"].append(
        {
            "name": "sat_lp_commitment_fx",
            "parent_entity": "lnk_lp_commitment",
            "satellite_type": "STANDARD_SATELLITE",
            "source_bronze_table": "investran.src_lp_commitments_and_calls",
            "security_classification": "CONFIDENTIAL",
            "volatility_tier": "HIGH_FREQ",
            "payload_columns": fx_cols,
        }
    )

    emitter = VaultLedgerEmitter(
        ledger_path=tmp_path / "vault_events.jsonl",
        cas_store=ContentAddressableStore(tmp_path / "cas"),
    )

    result = run_axi_dv_verification_pipeline(
        candidate_contract=candidate,
        ontology_graph=pe_ontology,
        bronze_profile=pe_bronze_profile,
        strict_schema=True,
        check_full_ledger_coverage=True,
        check_isolated_hubs=True,
        run_id="run-pe-apex-horizon-20261030",
        ledger_emitter=emitter,
    )

    status, transformed, diags = result
    assert status == "PASS"
    assert diags == ["ALL_GATES_GREEN: Contract verified and cold-start codemods applied."]
    assert "ALG_A4_COLD_START_EXTENSIBILITY" in result.applied_codemods
    assert "ALG_A1_3AXIS_SATELLITE_SPLIT" in result.applied_codemods
    assert result.toon_trace.status == "PASS"
    assert len(result.emitted_events) == 1
    assert len(result.er_routing_decisions) >= 3
    assert "sat_lp_commitment_fx" not in {s["name"] for s in transformed["satellites"]}
