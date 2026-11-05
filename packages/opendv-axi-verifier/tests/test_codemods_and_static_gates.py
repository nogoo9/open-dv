"""Comprehensive unit tests for Work Package WP-04: `opendv-axi-verifier`.

Covers:
  - Algorithm A.1: 3-Axis Satellite Split & Gate 4 YAGNI Cost Guard (`W_amp >= 50 MB/day`)
  - Algorithm A.2: Low-Cardinality Enum Hub-to-Ref Demotion
  - Algorithm A.3: Link Effectivity Satellite (`esat_*`) Auto-Injection
  - Algorithm A.4: 3-Tier BKCC Authority & Nullable-FK Link Splitter (`U_nn >= 0.95`)
  - Algorithm A.5: SQLGlot AST Canonical Length-Prefixed SHA-256 Hash Recipe Builder
  - Algorithm A.6: SQLGlot AST Static Verifier (`DV-AST-001`-`007`)
  - Gate 1 & Gate 2: Contract Schema, Grain Statement (`>= 8` words), 100% Column Triage Ledger
  - Gate 3: NetworkX Bipartite DAG Structural Verifier (`DV-STR-001`-`015` & `DV-COL-001`-`012`)
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
    ZERO_KEY,
    ToonTrace,
    ToonViolation,
    build_canonical_sha256_hash_ast,
    build_dv2_networkx_digraph,
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
    diagnostic_string_to_toon_violation,
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
from opendv_core.schema_registry import validate_contract_v5
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
    for s in res["satellites"]:
        assert s["volatility_tier"] in {"STATIC", "STANDARD", "HIGH_FREQ"}


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
    assert res["reference_tables"][0]["ontology_concept_uri"] == "urn:ontology:enum:iso_currency"
    assert res["reference_tables"][0]["code_column"] == "base_currency_iso"
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
            entry["elm_classification"] = "FOREIGN_KEY_REF"

    repaired = codemod_enforce_cold_start_extensibility(naive, pe_ontology, pe_bronze_profile)
    validate_contract_v5(repaired)

    co_invest_ledger = next(
        e for e in repaired["column_triage_ledger"] if e["column_name"] == "co_invest_portco_lei"
    )
    assert co_invest_ledger["target_dv_entity"] == "lnk_lp_commitment_co_invest"
    assert co_invest_ledger["elm_classification"] == "OPTIONAL_FOREIGN_KEY_REF"

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

    # Verify idempotency: running A.4 a second time does not re-split filtered Links
    second_pass = codemod_enforce_cold_start_extensibility(repaired, pe_ontology, pe_bronze_profile)
    assert second_pass == repaired


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
        bkcc_prefix="  legacy_gp_admin  ",
    )
    sql_expr = ast_node.sql("duckdb")
    assert "15:LEGACY_GP_ADMIN" in sql_expr
    assert "-1:^^" in sql_expr
    assert "OCTET_LENGTH" in sql_expr

    import duckdb

    with duckdb.connect(":memory:") as con:
        con.execute("CREATE MACRO SHA2(s, bits) AS sha256(s)")
        con.execute("CREATE MACRO OCTET_LENGTH(s) AS strlen(s)")
        for empty_val in ("NULL", "''", "'   '"):
            row = con.execute(
                f"SELECT {sql_expr} FROM (SELECT CAST({empty_val} AS VARCHAR) AS lp_number)"
            ).fetchone()
            assert row is not None
            assert row[0] == ZERO_KEY

        from opendv_core.hashing import compute_dv2_length_prefixed_sha256

        non_null_row = con.execute(
            f"SELECT {sql_expr} FROM (SELECT 'LP-101' AS lp_number)"
        ).fetchone()
        assert non_null_row is not None
        assert non_null_row[0] == compute_dv2_length_prefixed_sha256(
            ["LP-101"],
            is_business_key=True,
            bkcc_prefix="LEGACY_GP_ADMIN",
        )
        assert non_null_row[0] == "6b7c7180b62164f31f14eebcd16cc5fd2b0ce58143de66a4b67521113ec5d8b9"

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


def test_alg_a6_dv_ast_006_whitelists_idempotent_hub_and_link_dedup_and_blocks_kpis() -> None:
    """Issue #44 / Scenario S7: `DV-AST-006` (`DV-G6-005`) must whitelist idempotent
    Hub and Link micro-batch key deduplication (`GROUP BY <hk>, <bk_or_fk>` with
    `MIN(load_dts)` and `ANY_VALUE(rec_src)` / `MIN(rec_src)`) while rejecting
    business KPI aggregations (`SUM`, `AVG`, `COUNT`, `MAX`), `HAVING`, non-key
    grouping (`load_dts`, `rec_src`), and Satellite `GROUP BY`.
    """
    # 1. Positive: Generic compiler Zone 2 Hub micro-batch deduplication
    hub_dedup_sql = """
    INSERT INTO raw_vault.hub_customer (hk_customer, bkcc, customer_code, load_dts, rec_src)
    SELECT
        src.hk_customer,
        src.bkcc,
        src.customer_code,
        MIN(src.load_dts) AS load_dts,
        ANY_VALUE(src.rec_src) AS rec_src
    FROM staging.stg_hub_customer src
    WHERE NOT EXISTS (
        SELECT 1 FROM raw_vault.hub_customer h WHERE h.hk_customer = src.hk_customer
    )
    GROUP BY src.hk_customer, src.bkcc, src.customer_code;
    """
    assert (
        verify_physical_dv2_sql_ast(hub_dedup_sql, entity_name="hub_customer", entity_type="HUB")
        == []
    )
    assert verify_physical_dv2_sql_ast(hub_dedup_sql, entity_name="raw_vault.hub_customer") == []

    # 2. Positive: Generic compiler Zone 2 Link micro-batch deduplication
    link_dedup_sql = """
    INSERT INTO raw_vault.lnk_customer_order (
        hk_lnk_customer_order, hk_customer, hk_sales_order, load_dts, rec_src
    )
    SELECT
        src.hk_lnk_customer_order,
        src.hk_customer,
        src.hk_sales_order,
        MIN(src.load_dts) AS load_dts,
        ANY_VALUE(src.rec_src) AS rec_src
    FROM staging.stg_lnk_customer_order src
    WHERE NOT EXISTS (
        SELECT 1 FROM raw_vault.lnk_customer_order l
        WHERE l.hk_lnk_customer_order = src.hk_lnk_customer_order
    )
    GROUP BY src.hk_lnk_customer_order, src.hk_customer, src.hk_sales_order;
    """
    assert (
        verify_physical_dv2_sql_ast(
            link_dedup_sql, entity_name="lnk_customer_order", entity_type="LINK"
        )
        == []
    )

    # 3. Positive: PE compiler Zone 2 Hub with unaliased MIN(load_dts) and ANY_VALUE(rec_src)
    # matched against INSERT target columns
    pe_hub_dedup_sql = """
    INSERT INTO raw_vault.hub_fund (hk_fund, fund_code, load_dts, rec_src)
    SELECT hk_fund, fund_code, MIN(load_dts), ANY_VALUE(rec_src)
    FROM staging.stg_pe_funds src
    WHERE NOT EXISTS (SELECT 1 FROM raw_vault.hub_fund h WHERE h.hk_fund = src.hk_fund)
    GROUP BY hk_fund, fund_code;
    """
    assert verify_physical_dv2_sql_ast(pe_hub_dedup_sql, entity_name="hub_fund") == []

    # 4. Positive: ANSI fallback MIN(src.rec_src) AS rec_src alongside MIN(src.load_dts) AS load_dts
    ansi_hub_dedup_sql = """
    INSERT INTO raw_vault.hub_customer (hk_customer, customer_code, load_dts, rec_src)
    SELECT
        src.hk_customer,
        src.customer_code,
        MIN(src.load_dts) AS load_dts,
        MIN(src.rec_src) AS rec_src
    FROM staging.stg_hub_customer src
    GROUP BY src.hk_customer, src.customer_code;
    """
    assert verify_physical_dv2_sql_ast(ansi_hub_dedup_sql, entity_name="hub_customer") == []

    # 5. Negative: Hub GROUP BY with business KPI aggregations (SUM / AVG / COUNT)
    kpi_exprs = (
        "SUM(src.amount) AS total_amt",
        "AVG(src.amount) AS avg_amt",
        "COUNT(*) AS cnt",
    )
    for kpi_expr in kpi_exprs:
        bad_kpi_sql = f"""
        INSERT INTO raw_vault.hub_customer (
            hk_customer, customer_code, load_dts, rec_src, metric_val
        )
        SELECT
            src.hk_customer,
            src.customer_code,
            MIN(src.load_dts) AS load_dts,
            ANY_VALUE(src.rec_src) AS rec_src,
            {kpi_expr}
        FROM staging.stg_hub_customer src
        GROUP BY src.hk_customer, src.customer_code;
        """
        v_kpi = verify_physical_dv2_sql_ast(bad_kpi_sql, entity_name="hub_customer")
        assert any("DV-AST-006" in v for v in v_kpi), (
            f"Expected DV-AST-006 for {kpi_expr}, got {v_kpi}"
        )

    # 6. Negative: Hub GROUP BY with MAX(src.load_dts) instead of MIN(src.load_dts)
    bad_max_sql = """
    INSERT INTO raw_vault.hub_customer (hk_customer, customer_code, load_dts, rec_src)
    SELECT
        src.hk_customer,
        src.customer_code,
        MAX(src.load_dts) AS load_dts,
        ANY_VALUE(src.rec_src) AS rec_src
    FROM staging.stg_hub_customer src
    GROUP BY src.hk_customer, src.customer_code;
    """
    assert any(
        "DV-AST-006" in v
        for v in verify_physical_dv2_sql_ast(bad_max_sql, entity_name="hub_customer")
    )

    # 7. Negative: Hub GROUP BY that includes rec_src or load_dts in GROUP BY
    for non_key_col in ("src.rec_src", "src.load_dts"):
        bad_group_col_sql = f"""
        INSERT INTO raw_vault.hub_customer (hk_customer, customer_code, load_dts, rec_src)
        SELECT
            src.hk_customer,
            src.customer_code,
            MIN(src.load_dts) AS load_dts,
            ANY_VALUE(src.rec_src) AS rec_src
        FROM staging.stg_hub_customer src
        GROUP BY src.hk_customer, src.customer_code, {non_key_col};
        """
        assert any(
            "DV-AST-006" in v
            for v in verify_physical_dv2_sql_ast(bad_group_col_sql, entity_name="hub_customer")
        )

    # 8. Negative: Hub GROUP BY with HAVING clause
    bad_having_sql = """
    INSERT INTO raw_vault.hub_customer (hk_customer, customer_code, load_dts, rec_src)
    SELECT
        src.hk_customer,
        src.customer_code,
        MIN(src.load_dts) AS load_dts,
        ANY_VALUE(src.rec_src) AS rec_src
    FROM staging.stg_hub_customer src
    GROUP BY src.hk_customer, src.customer_code
    HAVING COUNT(*) > 1;
    """
    assert any(
        "DV-AST-006" in v
        for v in verify_physical_dv2_sql_ast(bad_having_sql, entity_name="hub_customer")
    )

    # 9. Negative: Satellite (sat_*, mas_*, esat_*) INSERT containing GROUP BY
    for sat_name in ("sat_customer_core", "mas_customer_tags", "esat_customer_link"):
        bad_sat_sql = f"""
        INSERT INTO raw_vault.{sat_name} (hk_customer, load_dts, rec_src, hash_diff)
        SELECT
            src.hk_customer,
            MIN(src.load_dts) AS load_dts,
            ANY_VALUE(src.rec_src) AS rec_src,
            src.hash_diff
        FROM staging.stg_customer src
        WHERE src.hash_diff <> ''
        GROUP BY src.hk_customer, src.hash_diff;
        """
        assert any(
            "DV-AST-006" in v
            for v in verify_physical_dv2_sql_ast(bad_sat_sql, entity_name=sat_name)
        )


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
    pe_graph = build_dv2_networkx_digraph(pe_golden_contract)
    assert pe_graph.out_degree("sal_limited_partner") == 2

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
    assert "target_size_usd" in orig_sat["payload_columns"]
    validate_contract_v5(evolved_v2)

    # Verify KEY_DRIFT findings populate unmapped_escalations instead of being discarded
    drift_with_key_err = copy.deepcopy(baseline_v1)
    for hub in drift_with_key_err["hubs"]:
        if hub["name"] == "hub_fund":
            hub["business_key_sources"][0]["key_columns"] = ["new_incompatible_fund_key"]
    escalated_v2 = codemod_intra_source_schema_drift(baseline_v1, drift_with_key_err)
    assert len(escalated_v2["unmapped_escalations"]) == 1
    assert escalated_v2["unmapped_escalations"][0]["escalation_reason"] == "COMPETING_NATURAL_KEYS"


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


def _load_pe_pdf_corpus_lf() -> dict[str, str]:
    """Load verbatim LF unstructured PDF texts from docs/07 §1.3."""
    import re

    doc07_path = REPO_ROOT / "docs" / "07-end-to-end-private-equity-demo.md"
    doc07_text = doc07_path.read_text(encoding="utf-8")
    text_blocks = re.findall(r"```text\n(.*?)\n```", doc07_text, flags=re.DOTALL)
    assert len(text_blocks) >= 2
    side_letter_text = text_blocks[0]
    ic_memo_text = text_blocks[1]
    side_letter_uri = (
        "s3://apex-pe-vault-bronze/legal/side-letters/calpers_side_letter_fund_iv_2026.pdf"
    )
    ic_memo_uri = "s3://apex-pe-vault-bronze/ic-memos/2026-q3/ic_q3_valuation_nordic_cloud.pdf"
    return {
        side_letter_uri: side_letter_text,
        "calpers_side_letter_fund_iv_2026.pdf": side_letter_text,
        ic_memo_uri: ic_memo_text,
        "ic_q3_valuation_nordic_cloud.pdf": ic_memo_text,
    }


def test_issue_33_alg_a9b_crlf_and_cr_line_ending_normalization(
    pe_golden_contract: dict[str, Any],
    pe_ontology: dict[str, Any],
    pe_bronze_profile: dict[str, Any],
) -> None:
    """Issue #33 (BUG-04 / FR-DOM-05 / Rule U2 / Scenario S8 / UF-5):
    Verifies that Windows CRLF (`\\r\\n`) and legacy CR (`\\r`) line endings in unstructured
    document bodies and extracted verbatim quotes are normalized to LF (`\\n`) before
    character-offset span validation in Algorithm A.9b (`evaluate_unstructured_er_routing`,
    `verify_gate5_unstructured_invariants`, and `run_axi_dv_verification_pipeline`).
    """
    lf_corpus = _load_pe_pdf_corpus_lf()
    doc_001_uri = (
        "s3://apex-pe-vault-bronze/legal/side-letters/calpers_side_letter_fund_iv_2026.pdf"
    )
    doc_001_lf = lf_corpus[doc_001_uri]

    crlf_doc_samples = {k: v.replace("\n", "\r\n") for k, v in lf_corpus.items()}
    cr_only_doc_samples = {k: v.replace("\n", "\r") for k, v in lf_corpus.items()}

    # 1. evaluate_unstructured_er_routing must produce zero span_errors on CRLF & CR bodies
    decisions_crlf = evaluate_unstructured_er_routing(
        pe_bronze_profile,
        parsed_doc_samples=crlf_doc_samples,
    )
    assert len(decisions_crlf) == 7
    for dec in decisions_crlf:
        assert dec["span_errors"] == []

    decisions_cr = evaluate_unstructured_er_routing(
        pe_bronze_profile,
        parsed_doc_samples=cr_only_doc_samples,
    )
    assert len(decisions_cr) == 7
    for dec in decisions_cr:
        assert dec["span_errors"] == []

    # 2. Multi-line sample_extractions with CRLF quotes and LF-indexed spans
    contract_with_samples = copy.deepcopy(pe_golden_contract)
    multiline_lf_quote = doc_001_lf[0:98]
    assert "\n" in multiline_lf_quote
    multiline_crlf_quote = multiline_lf_quote.replace("\n", "\r\n")

    for sat in contract_with_samples["satellites"]:
        if sat["name"] == "sat_lp_side_letter_llm_ext":
            sat["sample_extractions"] = [
                {
                    "doc_id": doc_001_uri,
                    "evidence_quote": multiline_crlf_quote,
                    "char_start": 0,
                    "char_end": 98,
                    "confidence_score": 0.96,
                    "top2_margin": 0.30,
                },
            ]

    violations_crlf = verify_gate5_unstructured_invariants(
        contract_with_samples,
        parsed_doc_samples=crlf_doc_samples,
        bronze_profile=pe_bronze_profile,
    )
    assert violations_crlf == []

    # 3. Full 6-Gate run_axi_dv_verification_pipeline with CRLF parsed_doc_samples
    pipeline_report = run_axi_dv_verification_pipeline(
        pe_golden_contract,
        pe_ontology,
        pe_bronze_profile,
        parsed_doc_samples=crlf_doc_samples,
    )
    assert pipeline_report.status == "PASS"
    assert not any("SPAN_HALLUCINATION" in d for d in pipeline_report.diagnostics)


def test_issue_33_gate5_deduplicates_span_errors_across_decisions(
    pe_golden_contract: dict[str, Any],
    pe_bronze_profile: dict[str, Any],
) -> None:
    """Issue #33 Negative Invariant Test (Rule U2 / Scenario S8 / UF-5):
    Ensures that genuine fabricated quotes and off-by-one/shifted character offsets on
    CRLF-encoded documents are still strictly rejected with `SPAN_HALLUCINATION` and that
    document-level span errors are not duplicated across multiple ER spans (`decisions`).
    """
    lf_corpus = _load_pe_pdf_corpus_lf()
    crlf_samples = {k: v.replace("\n", "\r\n") for k, v in lf_corpus.items()}

    # Mutate one span in bronze_profile to have a genuine hallucinated quote
    bad_profile = copy.deepcopy(pe_bronze_profile)
    s3_sys = next(
        s for s in bad_profile["source_systems"] if s["source_system_code"] == "OBJECT_STORE_S3"
    )
    s3_sys["unstructured_documents"][0]["extracted_spans"][0] = {
        "field": "hallucinated_fee_cap",
        "span_start": 499,
        "span_end": 506,
        "verbatim_quote": "999 bps",
        "resolved_bk": None,
        "er_tier": "EXTRACTED_ATTRIBUTE",
        "confidence": 0.99,
    }
    violations = verify_gate5_unstructured_invariants(
        pe_golden_contract,
        parsed_doc_samples=crlf_samples,
        bronze_profile=bad_profile,
    )
    # Exactly 1 violation (not duplicated 4x across the 4 er_spans of doc_001)
    assert len(violations) == 1
    assert "SPAN_HALLUCINATION" in violations[0]
    assert "hallucinated_fee_cap" in violations[0]


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
        [
            "SEMANTIC_ERROR [Gate 1 - DV-COL-012]: Hub 'hub_limited_partner' lacks bkcc_prefix.",
            (
                "SCHEMA_ERROR [Gate 1 - DV-G1-001]: "
                "Multi-line error\nwith newline\rand carriage return."
            ),
            "BREAKING_EVOLUTION [Gate 0 - DV-EVO-002]: Hub 'hub_fund' was deleted.",
            "DV-COL-009: Satellite 'sat_fund' has empty payload_columns.",
        ],
        run_id="run-test-01",
    )
    assert from_diags.status == "REJECT-WITH-TRACE"
    assert parse_toon_v1(serialize_toon_v1(from_diags)) == from_diags
    assert diagnostic_string_to_toon_violation("Gate 0: DV-EVO-001 failed").gate == "G3"
    assert diagnostic_string_to_toon_violation("DV-COL-009: empty payload").gate == "G2"


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

    # Verify ASK_USER escalation from unmapped_escalations emits ASK-USER telemetry status
    ask_contract = copy.deepcopy(pe_golden_contract)
    ask_contract["unmapped_escalations"] = [
        {
            "bronze_table": "legacy_gp.src_legacy_lp_roster",
            "columns": ["lp_num"],
            "escalation_reason": "DISJOINT_MULTI_SOURCE_BKS",
            "architect_question": "Confirm whether lp_num requires a SAME_AS_LINK crosswalk.",
        }
    ]
    ask_res = run_axi_dv_verification_pipeline(
        candidate_contract=ask_contract,
        ontology_graph=pe_ontology,
        bronze_profile=pe_bronze_profile,
        run_id="run-pe-ask-user-01",
        ledger_emitter=emitter,
    )
    assert ask_res.status == "ASK_USER"
    assert ask_res.toon_trace.status == "ASK-USER"
    assert ask_res.emitted_events[0]["status"] == "ASK-USER"
    assert ask_res.emitted_events[0]["layer1_story_banner"]["verdict_pill"] == "ASK-USER"


def test_alg_a6_dv_ast_007_unstructured_link_steward_approval_filter() -> None:
    """Issue #32 (DV-AST-007 / Rule U4): Verify Algorithm A.6 rejects unstructured extraction
    Link INSERT statements that omit `AND src.steward_gate_status = 'APPROVED'` or wrap it in OR,
    and passes compliant Zone 2 unstructured Link INSERT statements.
    """
    from opendv_axi_verifier.verifiers.alg_a6_sqlglot_ast import (
        verify_physical_dv2_sql_ast,
        verify_sql_models_batch,
    )

    missing_filter_sql = """
    INSERT INTO raw_vault.lnk_doc_portco_memo (
        hk_lnk_doc_portco_memo, hk_doc, hk_portco, load_dts, rec_src
    )
    SELECT
        src.hk_lnk_doc_portco_memo,
        src.hk_doc,
        src.hk_portco,
        src.load_dts,
        src.rec_src
    FROM staging.stg_unstructured_vdr_llm_extractions src
    WHERE src.target_domain = 'PORTCO_VALUATION_RESTATEMENT'
      AND NOT EXISTS (
          SELECT 1 FROM raw_vault.lnk_doc_portco_memo l
          WHERE l.hk_lnk_doc_portco_memo = src.hk_lnk_doc_portco_memo
      );
    """
    v_missing = verify_physical_dv2_sql_ast(missing_filter_sql, entity_name="lnk_doc_portco_memo")
    assert any("DV-AST-007" in v for v in v_missing)

    or_bypass_sql = """
    INSERT INTO raw_vault.lnk_document_customer (
        hk_lnk_document_customer, hk_document, hk_customer, load_dts, rec_src
    )
    SELECT
        src.hk_lnk_document_customer,
        src.hk_document,
        src.hk_customer,
        src.load_dts,
        src.rec_src
    FROM staging.stg_lnk_document_customer src
    WHERE src.steward_gate_status = 'APPROVED' OR src.hk_lnk_document_customer IS NOT NULL;
    """
    v_or_bypass = verify_physical_dv2_sql_ast(or_bypass_sql, entity_name="lnk_document_customer")
    assert any("DV-AST-007" in v for v in v_or_bypass)

    compliant_sql = """
    INSERT INTO raw_vault.lnk_doc_portco_memo (
        hk_lnk_doc_portco_memo, hk_doc, hk_portco, load_dts, rec_src
    )
    SELECT
        src.hk_lnk_doc_portco_memo,
        src.hk_doc,
        src.hk_portco,
        src.load_dts,
        src.rec_src
    FROM staging.stg_unstructured_vdr_llm_extractions src
    WHERE src.target_domain = 'PORTCO_VALUATION_RESTATEMENT'
      AND src.steward_gate_status = 'APPROVED'
      AND NOT EXISTS (
          SELECT 1 FROM raw_vault.lnk_doc_portco_memo l
          WHERE l.hk_lnk_doc_portco_memo = src.hk_lnk_doc_portco_memo
      );
    """
    v_compliant = verify_physical_dv2_sql_ast(compliant_sql, entity_name="lnk_doc_portco_memo")
    assert v_compliant == []

    batch_violations = verify_sql_models_batch(
        {"lnk_custom_unstructured": missing_filter_sql},
        unstructured_link_names={"lnk_custom_unstructured"},
    )
    assert any("DV-AST-007" in v for v in batch_violations)


def test_issue_32_alg_a6_dv_ast_007_requires_steward_gate() -> None:
    """Issue #32 (DV-AST-007): Verify `requires_steward_gate=True` on
    `verify_physical_dv2_sql_ast` enforces `steward_gate_status = 'APPROVED'`
    on arbitrary unstructured extraction link names.
    """
    from opendv_axi_verifier.verifiers.alg_a6_sqlglot_ast import verify_physical_dv2_sql_ast

    custom_unfiltered = """
    INSERT INTO raw_vault.lnk_vdr_clause_party (
        hk_lnk_vdr_clause_party, hk_clause, hk_party, load_dts, rec_src
    )
    SELECT
        src.hk_lnk_vdr_clause_party,
        src.hk_clause,
        src.hk_party,
        src.load_dts,
        src.rec_src
    FROM staging.stg_lnk_vdr_clause_party src
    WHERE src.hk_lnk_vdr_clause_party IS NOT NULL;
    """
    violations = verify_physical_dv2_sql_ast(
        custom_unfiltered,
        entity_name="lnk_vdr_clause_party",
        requires_steward_gate=True,
    )
    assert any("DV-AST-007" in v for v in violations)

    custom_filtered = """
    INSERT INTO raw_vault.lnk_vdr_clause_party (
        hk_lnk_vdr_clause_party, hk_clause, hk_party, load_dts, rec_src
    )
    SELECT
        src.hk_lnk_vdr_clause_party,
        src.hk_clause,
        src.hk_party,
        src.load_dts,
        src.rec_src
    FROM staging.stg_lnk_vdr_clause_party src
    WHERE src.hk_lnk_vdr_clause_party IS NOT NULL
      AND src.steward_gate_status = 'APPROVED';
    """
    assert (
        verify_physical_dv2_sql_ast(
            custom_filtered,
            entity_name="lnk_vdr_clause_party",
            requires_steward_gate=True,
        )
        == []
    )

    subquery_only_sql = """
    INSERT INTO raw_vault.lnk_doc_portco_memo (
        hk_lnk_doc_portco_memo, hk_doc, hk_portco, load_dts, rec_src
    )
    SELECT
        src.hk_lnk_doc_portco_memo,
        src.hk_doc,
        src.hk_portco,
        src.load_dts,
        src.rec_src
    FROM (
        SELECT
            hk_lnk_doc_portco_memo,
            hk_doc,
            hk_portco,
            load_dts,
            rec_src
        FROM staging.stg_unstructured_vdr_llm_extractions
        WHERE steward_gate_status = 'APPROVED'
    ) src;
    """
    v_subquery = verify_physical_dv2_sql_ast(
        subquery_only_sql,
        entity_name="lnk_doc_portco_memo",
        requires_steward_gate=True,
    )
    assert any("DV-AST-007" in v for v in v_subquery)

    negated_sql = """
    INSERT INTO raw_vault.lnk_doc_portco_memo (
        hk_lnk_doc_portco_memo, hk_doc, hk_portco, load_dts, rec_src
    )
    SELECT
        src.hk_lnk_doc_portco_memo,
        src.hk_doc,
        src.hk_portco,
        src.load_dts,
        src.rec_src
    FROM staging.stg_unstructured_vdr_llm_extractions src
    WHERE NOT (src.steward_gate_status = 'APPROVED');
    """
    v_negated = verify_physical_dv2_sql_ast(
        negated_sql,
        entity_name="lnk_doc_portco_memo",
        requires_steward_gate=True,
    )
    assert any("DV-AST-007" in v for v in v_negated)

    multi_stmt_sql = """
    INSERT INTO raw_vault.hub_fund (hk_fund, fund_code, load_dts, rec_src)
    SELECT src.hk_fund, src.fund_code, src.load_dts, src.rec_src
    FROM staging.stg_hub_fund src
    WHERE src.hk_fund IS NOT NULL;

    INSERT INTO raw_vault.lnk_lp_commitment (
        hk_lnk_lp_commitment, hk_lp, hk_fund, load_dts, rec_src
    )
    SELECT src.hk_lnk_lp_commitment, src.hk_lp, src.hk_fund, src.load_dts, src.rec_src
    FROM staging.stg_lnk_lp_commitment src
    WHERE src.hk_lnk_lp_commitment IS NOT NULL;

    INSERT INTO raw_vault.lnk_doc_portco_memo (
        hk_lnk_doc_portco_memo, hk_doc, hk_portco, load_dts, rec_src
    )
    SELECT
        src.hk_lnk_doc_portco_memo,
        src.hk_doc,
        src.hk_portco,
        src.load_dts,
        src.rec_src
    FROM staging.stg_unstructured_vdr_llm_extractions src
    WHERE src.steward_gate_status = 'APPROVED';
    """
    v_multi = verify_physical_dv2_sql_ast(
        multi_stmt_sql,
        entity_name="lnk_doc_portco_memo",
        requires_steward_gate=True,
    )
    assert not any("DV-AST-007" in v for v in v_multi)


def test_dv_col_013_tier1_global_bkcc_invariant_and_alg_a4_normalization(
    pe_golden_contract: dict[str, Any],
    pe_ontology: dict[str, Any],
    pe_bronze_profile: dict[str, Any],
) -> None:
    """Verify Gate 1 and Gate 3 enforce DV-COL-013 on TIER_1_GLOBAL_STANDARD sources
    and Algorithm A.4 normalizes Tier-1 bkcc_prefix to 'GLOBAL' without breaking A.7.
    """
    corrupted = copy.deepcopy(pe_golden_contract)
    portco_hub = next(h for h in corrupted["hubs"] if h["name"] == "hub_portfolio_company")
    portco_hub["business_key_sources"][0]["require_src_qualifier"] = True
    portco_hub["business_key_sources"][0]["bkcc_prefix"] = "DEALCLOUD_LOCAL_SILO"

    g1_diags = verify_gate1_contract_and_grain(
        corrupted, pe_ontology, pe_bronze_profile, check_full_ledger_coverage=True
    )
    g3_diags = verify_column_invariants(corrupted)
    assert any("DV-COL-013" in d and "hub_portfolio_company" in d for d in g1_diags)
    assert any("DV-COL-013" in d and "hub_portfolio_company" in d for d in g3_diags)

    # Also verify bkcc_prefix=None on TIER_1_GLOBAL_STANDARD triggers DV-COL-013 prior to A.4
    null_bkcc_contract = copy.deepcopy(pe_golden_contract)
    next(h for h in null_bkcc_contract["hubs"] if h["name"] == "hub_portfolio_company")[
        "business_key_sources"
    ][0]["bkcc_prefix"] = None
    assert any(
        "DV-COL-013" in d
        for d in verify_gate1_contract_and_grain(null_bkcc_contract, pe_ontology, pe_bronze_profile)
    )

    # Algorithm A.4 repairs both corruptions to require_src_qualifier=False, bkcc_prefix='GLOBAL'
    repaired = codemod_enforce_cold_start_extensibility(corrupted, pe_ontology, pe_bronze_profile)
    repaired_src = next(h for h in repaired["hubs"] if h["name"] == "hub_portfolio_company")[
        "business_key_sources"
    ][0]
    assert repaired_src["key_authority_tier"] == "TIER_1_GLOBAL_STANDARD"
    assert repaired_src["require_src_qualifier"] is False
    assert repaired_src["bkcc_prefix"] == "GLOBAL"
    assert verify_gate1_contract_and_grain(repaired, pe_ontology, pe_bronze_profile) == []
    assert verify_column_invariants(repaired) == []
    assert verify_additive_evolution_invariants(null_bkcc_contract, repaired) == []


def test_issue_30_dv_ast_002_rejects_strlen_and_length_in_hashes() -> None:
    """Issue #30 (BUG-01 / DV-AST-002): Verify Algorithm A.6 rejects `STRLEN(...)` and
    `LENGTH(...)` in hash projections while accepting `OCTET_LENGTH(ENCODE(...))`.
    """
    bad_strlen_sql = """
    SELECT
        LOWER(SHA256(
            CASE WHEN lp_legal_name IS NULL THEN '-1:^^'
                 ELSE CAST(STRLEN(lp_legal_name) AS VARCHAR) || ':' || lp_legal_name END
        )) AS hash_diff,
        lp_legal_name,
        load_dts,
        rec_src
    FROM bronze.src_lp
    """
    strlen_violations = verify_physical_dv2_sql_ast(
        bad_strlen_sql, entity_name="stg_lp", entity_type="STAGE", dialect="duckdb"
    )
    assert any("DV-AST-002" in v for v in strlen_violations)

    bad_length_sql = bad_strlen_sql.replace("STRLEN(", "LENGTH(")
    length_violations = verify_physical_dv2_sql_ast(
        bad_length_sql, entity_name="stg_lp", entity_type="STAGE", dialect="duckdb"
    )
    assert any("DV-AST-002" in v for v in length_violations)

    good_octet_sql = bad_strlen_sql.replace(
        "STRLEN(lp_legal_name)", "OCTET_LENGTH(ENCODE(lp_legal_name))"
    )
    octet_violations = verify_physical_dv2_sql_ast(
        good_octet_sql, entity_name="stg_lp", entity_type="STAGE", dialect="duckdb"
    )
    assert octet_violations == []


@pytest.fixture()
def pe_contract_dict(pe_golden_contract: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(pe_golden_contract)


def test_gate3_dv_str_015_canonical_sal_edge_ordering(
    pe_contract_dict: dict[str, Any],
) -> None:
    """Issue #41 (GAP-10 / Scenario S2): Verify DV-STR-015 enforces canonical
    (hk_master, hk_duplicate) ordering, 3-Tier BKCC authority hierarchy,
    asymmetry (no (A, A) self-loops), and no duplicate inverted (B, A) SAL pairs.
    """
    # 0. Baseline valid PE contract passes with 0 violations
    assert verify_structural_invariants(pe_contract_dict) == []

    # 1. Inverted role ordering: [DUPLICATE_ALIAS_LP, MASTER_LP] -> DV-STR-015
    inverted_roles_contract = copy.deepcopy(pe_contract_dict)
    for lnk in inverted_roles_contract["links"]:
        if lnk["name"] == "sal_limited_partner":
            lnk["hub_references"][0]["role_alias"] = "DUPLICATE_ALIAS_LP"
            lnk["hub_references"][1]["role_alias"] = "MASTER_LP"
    v_roles = verify_structural_invariants(inverted_roles_contract)
    assert any("DV-STR-015" in v and "sal_limited_partner" in v for v in v_roles)

    # 2. Inverted 3-Tier BKCC authority hierarchy: Tier-3 lp_num as MASTER_LP -> DV-STR-015
    inverted_tier_contract = copy.deepcopy(pe_contract_dict)
    for lnk in inverted_tier_contract["links"]:
        if lnk["name"] == "sal_limited_partner":
            lnk["hub_references"] = [
                {
                    "target_hub": "hub_limited_partner",
                    "role_alias": "MASTER_LP",
                    "source_fk_columns": ["lp_num"],
                },
                {
                    "target_hub": "hub_limited_partner",
                    "role_alias": "DUPLICATE_ALIAS_LP",
                    "source_fk_columns": ["lp_id"],
                },
            ]
    v_tiers = verify_structural_invariants(inverted_tier_contract)
    assert any("DV-STR-015" in v and "authority" in v for v in v_tiers)

    # 3. Reflexive self-loop (A, A) on source_fk_columns -> DV-STR-015 (and DV-STR-004)
    self_loop_contract = copy.deepcopy(pe_contract_dict)
    for lnk in self_loop_contract["links"]:
        if lnk["name"] == "sal_limited_partner":
            lnk["hub_references"][0]["source_fk_columns"] = ["lp_id"]
            lnk["hub_references"][1]["source_fk_columns"] = ["lp_id"]
    v_loop = verify_structural_invariants(self_loop_contract)
    assert any("DV-STR-015" in v and "self-loop" in v for v in v_loop)

    # 4. Reflexive self-loop via edge_pairs [(A, A)] and inverted pair [(A, B), (B, A)]
    pair_violation_contract = copy.deepcopy(pe_contract_dict)
    for lnk in pair_violation_contract["links"]:
        if lnk["name"] == "sal_limited_partner":
            lnk["edge_pairs"] = [
                ("LP-100", "LP-100"),
                ("LP-100", "LP-900"),
                ("LP-900", "LP-100"),
            ]
    v_pairs = verify_structural_invariants(pair_violation_contract)
    assert any("DV-STR-015" in v and "self-loop" in v for v in v_pairs)
    assert any("DV-STR-015" in v and "inverted (B, A)" in v for v in v_pairs)

    # 5. Cross-link duplicate inverted SAL definition on the same Hub -> DV-STR-015
    dup_sal_contract = copy.deepcopy(pe_contract_dict)
    dup_sal_contract["links"].append(
        {
            "name": "sal_limited_partner_inverted",
            "link_type": "SAME_AS_LINK",
            "ontology_concept_uri": "urn:ontology:relationship:entity_equivalence",
            "source_bronze_table": "mdm.src_lp_steward_crosswalk",
            "hub_references": [
                {
                    "target_hub": "hub_limited_partner",
                    "role_alias": "MASTER_LP",
                    "source_fk_columns": ["lp_num"],
                },
                {
                    "target_hub": "hub_limited_partner",
                    "role_alias": "DUPLICATE_ALIAS_LP",
                    "source_fk_columns": ["master_lp_id"],
                },
            ],
        }
    )
    v_dup_sal = verify_structural_invariants(dup_sal_contract)
    assert any("DV-STR-015" in v and "sal_limited_partner_inverted" in v for v in v_dup_sal)


def test_issue_31_alg_a4_and_gate2_sparse_binary_link(tmp_path: Path) -> None:
    """Issue #31 (BUG-02 / Scenario S5): Binary links (arity = 2) with sparse optional FK
    (U_nn < 0.95 or null_rate > 0.05) must be rejected by Gate 2 (DV-G2-001 / DV-G2-001b)
    when unfiltered, and auto-repaired in-place by Algorithm A.4 with
    where_filter_condition = '<fk> IS NOT NULL' and OPTIONAL_FOREIGN_KEY_REF.
    """
    import duckdb

    from opendv_core.constants import ZERO_KEY_UNKNOWN
    from opendv_core.models.contract_v5 import NoMistakeDataVault2ContractV5
    from opendv_duckdb_sandbox.compiler_4zone import build_algorithm_a5_sha256_sql
    from opendv_duckdb_sandbox.graph_b_profiler import GraphBProfiler
    from opendv_sources import SourceSpec, get_source_registry

    customers_csv = tmp_path / "crm_customers.csv"
    customers_csv.write_text(
        "customer_tax_id,legal_name\n"
        "TAX-US-900101,Acme Industrial Holdings LLC\n"
        "TAX-DE-800202,Krauss Mittelstand GmbH\n",
        encoding="utf-8",
    )
    orders_csv = tmp_path / "erp_orders.csv"
    orders_csv.write_text(
        "order_id,customer_tax_id,order_status\n"
        "ORD-2026-1001,TAX-US-900101,FULFILLED\n"
        "ORD-2026-1002,,DRAFT\n"
        "ORD-2026-1003,TAX-DE-800202,SHIPPED\n"
        "ORD-2026-1004,,QUOTE_PENDING\n",
        encoding="utf-8",
    )

    # Profile real CSV files with GraphBProfiler in DuckDB (no mocks)
    profiler = GraphBProfiler()
    reg = get_source_registry()
    reg.get_tabular_adapter("file").register(
        SourceSpec(adapter="file", uri=str(customers_csv)), profiler.conn, "v_customers"
    )
    reg.get_tabular_adapter("file").register(
        SourceSpec(adapter="file", uri=str(orders_csv)), profiler.conn, "v_orders"
    )
    cust_metrics = profiler.profile_relation("v_customers")
    order_metrics = profiler.profile_relation("v_orders")

    bronze_profile = {
        "profile_version": "1.0.0",
        "domain_namespace": "global_commerce",
        "profiled_at_utc": "2026-10-30T00:00:00Z",
        "source_systems": [
            {
                "source_system_code": "CRM_SALESFORCE",
                "description": "CRM Customers",
                "tables": [
                    {
                        "bronze_table": "crm_salesforce.crm_customers",
                        "row_count": 2,
                        "daily_delta_rows": 2,
                        "columns": [
                            m.to_profiled_column().model_dump(mode="json", exclude_none=True)
                            for m in cust_metrics
                        ],
                    }
                ],
            },
            {
                "source_system_code": "ERP_NETSUITE",
                "description": "ERP Orders with 50% NULL customer_tax_id",
                "tables": [
                    {
                        "bronze_table": "erp_netsuite.erp_orders",
                        "row_count": 4,
                        "daily_delta_rows": 4,
                        "columns": [
                            m.to_profiled_column().model_dump(mode="json", exclude_none=True)
                            for m in order_metrics
                        ],
                    }
                ],
            },
        ],
    }

    ontology = {
        "ontology_version": "1.0.0",
        "domain_namespace": "global_commerce",
        "canonical_business_concepts": [
            {
                "concept_uri": "urn:ontology:entity:customer",
                "concept_name": "Customer",
                "expected_hub": "hub_customer",
                "description": "Enterprise customer entity",
                "key_authority_tiers": {
                    "tier_1_global_standard": {
                        "column_patterns": ["customer_tax_id"],
                        "bkcc_policy": "GLOBAL",
                    }
                },
            },
            {
                "concept_uri": "urn:ontology:entity:order",
                "concept_name": "SalesOrder",
                "expected_hub": "hub_order",
                "description": "Sales order header entity",
                "key_authority_tiers": {
                    "tier_1_global_standard": {
                        "column_patterns": ["order_id"],
                        "bkcc_policy": "GLOBAL",
                    }
                },
            },
        ],
        "canonical_relationships": [
            {
                "relationship_uri": "urn:ontology:relationship:order_customer",
                "relationship_name": "OrderCustomer",
                "expected_link": "lnk_order_customer",
                "involved_hubs": ["hub_order", "hub_customer"],
            }
        ],
    }

    unfiltered_binary_contract = {
        "contract_version": "5.0",
        "domain_namespace": "global_commerce",
        "lifecycle_stage": "COLD_START_N1",
        "column_triage_ledger": [
            {
                "bronze_table": "crm_salesforce.crm_customers",
                "column_name": "customer_tax_id",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_customer",
            },
            {
                "bronze_table": "crm_salesforce.crm_customers",
                "column_name": "legal_name",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_customer_core",
            },
            {
                "bronze_table": "erp_netsuite.erp_orders",
                "column_name": "order_id",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_order",
            },
            {
                "bronze_table": "erp_netsuite.erp_orders",
                "column_name": "customer_tax_id",
                "elm_classification": "FOREIGN_KEY_REF",
                "target_dv_entity": "lnk_order_customer",
            },
            {
                "bronze_table": "erp_netsuite.erp_orders",
                "column_name": "order_status",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_order_core",
            },
        ],
        "hubs": [
            {
                "name": "hub_customer",
                "ontology_concept_uri": "urn:ontology:entity:customer",
                "business_key_sources": [
                    {
                        "bronze_table": "crm_salesforce.crm_customers",
                        "key_columns": ["customer_tax_id"],
                        "record_source_code": "CRM_SALESFORCE",
                        "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                        "require_src_qualifier": False,
                        "bkcc_prefix": "GLOBAL",
                    }
                ],
            },
            {
                "name": "hub_order",
                "ontology_concept_uri": "urn:ontology:entity:order",
                "business_key_sources": [
                    {
                        "bronze_table": "erp_netsuite.erp_orders",
                        "key_columns": ["order_id"],
                        "record_source_code": "ERP_NETSUITE",
                        "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                        "require_src_qualifier": False,
                        "bkcc_prefix": "GLOBAL",
                    }
                ],
            },
        ],
        "links": [
            {
                "name": "lnk_order_customer",
                "link_type": "STANDARD_LINK",
                "ontology_concept_uri": "urn:ontology:relationship:order_customer",
                "source_bronze_table": "erp_netsuite.erp_orders",
                "hub_references": [
                    {
                        "target_hub": "hub_order",
                        "role_alias": "order",
                        "source_fk_columns": ["order_id"],
                    },
                    {
                        "target_hub": "hub_customer",
                        "role_alias": "customer",
                        "source_fk_columns": ["customer_tax_id"],
                    },
                ],
            }
        ],
        "satellites": [
            {
                "name": "sat_customer_core",
                "satellite_type": "STANDARD_SATELLITE",
                "parent_entity": "hub_customer",
                "source_bronze_table": "crm_salesforce.crm_customers",
                "security_classification": "INTERNAL",
                "volatility_tier": "STANDARD",
                "payload_columns": ["legal_name"],
                "soft_deprecated_columns": [],
            },
            {
                "name": "sat_order_core",
                "satellite_type": "STANDARD_SATELLITE",
                "parent_entity": "hub_order",
                "source_bronze_table": "erp_netsuite.erp_orders",
                "security_classification": "INTERNAL",
                "volatility_tier": "STANDARD",
                "payload_columns": ["order_status"],
                "soft_deprecated_columns": [],
            },
        ],
        "reference_tables": [],
        "unmapped_escalations": [],
    }

    # 1. Gate 2 must flag the unfiltered sparse binary Link when codemods are disabled
    diags = verify_gate1_contract_and_grain(
        unfiltered_binary_contract,
        ontology,
        bronze_profile,
    )
    assert any("DV-G2-001" in d and "lnk_order_customer" in d for d in diags)
    assert any("DV-G2-001b" in d and "customer_tax_id IS NOT NULL" in d for d in diags)

    no_codemod_res = run_axi_dv_verification_pipeline(
        unfiltered_binary_contract,
        ontology,
        bronze_profile,
        apply_codemods=False,
    )
    assert no_codemod_res.status == "REJECT_WITH_TRACE"
    assert any(
        v.gate == "G2" and v.rule == "DV-G2-001" and v.target == "lnk_order_customer"
        for v in no_codemod_res.toon_trace.violations
    )

    # 2. Algorithm A.4 must inject where_filter_condition in-place and promote ledger role
    repaired = codemod_enforce_cold_start_extensibility(
        unfiltered_binary_contract,
        ontology,
        bronze_profile,
    )
    validate_contract_v5(repaired)
    NoMistakeDataVault2ContractV5.model_validate(repaired)
    assert len(repaired["links"]) == 1
    lnk = repaired["links"][0]
    assert lnk["name"] == "lnk_order_customer"
    assert lnk["where_filter_condition"] == "customer_tax_id IS NOT NULL"

    fk_ledger = [
        e
        for e in repaired["column_triage_ledger"]
        if e["bronze_table"] == "erp_netsuite.erp_orders" and e["column_name"] == "customer_tax_id"
    ]
    assert len(fk_ledger) == 1
    assert fk_ledger[0]["target_dv_entity"] == "lnk_order_customer"
    assert fk_ledger[0]["elm_classification"] == "OPTIONAL_FOREIGN_KEY_REF"

    # 3. Full 6-Gate orchestrator with apply_codemods=True must converge to PASS
    with_codemod_res = run_axi_dv_verification_pipeline(
        unfiltered_binary_contract,
        ontology,
        bronze_profile,
        apply_codemods=True,
    )
    assert with_codemod_res.status == "PASS"
    assert "ALG_A4_COLD_START_EXTENSIBILITY" in with_codemod_res.applied_codemods

    # 4. Execute repaired where_filter_condition in real DuckDB to verify 0 Zero-Key rows
    conn = duckdb.connect(":memory:")
    try:
        hk_order_sql = build_algorithm_a5_sha256_sql(["order_id"], is_business_key=True)
        hk_cust_sql = build_algorithm_a5_sha256_sql(["customer_tax_id"], is_business_key=True)
        where_clause = lnk["where_filter_condition"]
        rows = conn.execute(
            f"""
            WITH raw_orders AS (
                SELECT * FROM read_csv_auto('{orders_csv.as_posix()}')
            ),
            staged AS (
                SELECT
                    order_id,
                    customer_tax_id,
                    {hk_order_sql} AS hk_order,
                    COALESCE(
                        CASE WHEN customer_tax_id IS NULL THEN NULL ELSE {hk_cust_sql} END,
                        '{ZERO_KEY_UNKNOWN}'
                    ) AS hk_customer
                FROM raw_orders
                WHERE {where_clause}
            )
            SELECT order_id, hk_customer FROM staged ORDER BY order_id
            """
        ).fetchall()
        assert len(rows) == 2
        assert [r[0] for r in rows] == ["ORD-2026-1001", "ORD-2026-1003"]
        assert all(r[1] != ZERO_KEY_UNKNOWN for r in rows)
    finally:
        conn.close()
