"""Unit test suite for generic non-PE 4-Zone SQL compilation & 4-Batch DuckDB Sandbox (`WP-05`).

Verifies that:
1. `compile_canonical_ir_to_4zone_sql` compiles arbitrary non-PE `CanonicalIR` models
   (e.g., `global_commerce_vault` with `hub_customer`, `hub_sales_order`,
   `lnk_customer_order`, `sat_customer_core`, `pit_customer`) into executable Zones 0-4
   DuckDB SQL with synthetic baseline `bronze.src_*` seed rows, length-prefixed SHA-256
   `staging.stg_*` views, idempotent Zone 2 loads, Zone 3 PIT tables, and Zone 4 Mart views.
2. `build_vault_topology_maps(ir)` dynamically derives PK maps and FK edges from `CanonicalIR`.
3. `DuckDbShadowSandbox.run_full_verification(source=commerce_ir)` passes all 4 adversarial
   batches and all 15 `DV-DYN-001..015` dynamic checks on non-PE schemas.
"""

from __future__ import annotations

from pathlib import Path

from opendv_core.models.canonical_ir import (
    CanonicalIR,
    IRBusinessKeyColumn,
    IRColumnDef,
    IRHashColumn,
    IRHashConfig,
    IRHashExpression,
    IRHub,
    IRHubSourceMapping,
    IRLink,
    IRLinkHubReference,
    IRParentHkColumn,
    IRPitTable,
    IRSatellite,
    IRSourceMapping,
    IRSystemColumns,
)
from opendv_core.schema_registry import get_examples_dir, validate_observability_event
from opendv_duckdb_sandbox.compiler_4zone import compile_canonical_ir_to_4zone_sql
from opendv_duckdb_sandbox.dynamic_checks import (
    build_vault_topology_maps,
    check_dv_dyn_002_referential_integrity,
)
from opendv_duckdb_sandbox.sandbox_engine import DuckDbShadowSandbox
from opendv_telemetry.ledger_emitter import VaultLedgerEmitter


def _build_global_commerce_canonical_ir() -> CanonicalIR:
    """Construct a non-PE `global_commerce_vault` `CanonicalIR` model for testing."""
    return CanonicalIR(
        ir_version="1.0.0",
        model_name="global_commerce_vault",
        hash_config=IRHashConfig(
            algorithm="SHA256",
            storage_type="BINARY(32)",
            delimiter="||",
            null_token_bk="-1",
            null_token_payload="-1:^^",
            zero_key_unknown_token="-1",
            zero_key_error_token="-2",
            encoding_scheme="LENGTH_PREFIXED",
        ),
        system_columns=IRSystemColumns(
            load_dts_col="load_dts",
            load_dts_type="TIMESTAMP_NTZ(9)",
            rec_src_col="rec_src",
            rec_src_type="VARCHAR(255)",
            hash_diff_col="hash_diff",
            applied_dts_col="applied_dts",
        ),
        hubs=[
            IRHub(
                name="hub_customer",
                business_concept="Customer",
                verified_global_master_key=False,
                hk_column=IRHashColumn(
                    name="hk_customer",
                    data_type="BINARY(32)",
                    nullable=False,
                    hash_expression=IRHashExpression(
                        algorithm="SHA256",
                        input_columns=["bkcc", "customer_code"],
                        normalization="UPPER_TRIM",
                    ),
                ),
                business_keys=[
                    IRBusinessKeyColumn(
                        name="bkcc",
                        data_type="VARCHAR(64)",
                        nullable=False,
                        is_bkcc=True,
                    ),
                    IRBusinessKeyColumn(
                        name="customer_code",
                        data_type="VARCHAR(255)",
                        nullable=False,
                    ),
                ],
                load_dts_column=IRColumnDef(
                    name="load_dts", data_type="TIMESTAMP_NTZ(9)", nullable=False
                ),
                rec_src_column=IRColumnDef(
                    name="rec_src", data_type="VARCHAR(255)", nullable=False
                ),
                source_mappings=[
                    IRHubSourceMapping(
                        staging_table="stg_crm_customers",
                        bk_source_columns=["bkcc", "customer_code"],
                        rec_src_value="CRM_SFDC",
                    )
                ],
            ),
            IRHub(
                name="hub_sales_order",
                business_concept="Sales Order",
                verified_global_master_key=True,
                hk_column=IRHashColumn(
                    name="hk_sales_order",
                    data_type="BINARY(32)",
                    nullable=False,
                    hash_expression=IRHashExpression(
                        algorithm="SHA256",
                        input_columns=["order_number"],
                        normalization="UPPER_TRIM",
                    ),
                ),
                business_keys=[
                    IRBusinessKeyColumn(
                        name="order_number",
                        data_type="VARCHAR(255)",
                        nullable=False,
                    )
                ],
                load_dts_column=IRColumnDef(
                    name="load_dts", data_type="TIMESTAMP_NTZ(9)", nullable=False
                ),
                rec_src_column=IRColumnDef(
                    name="rec_src", data_type="VARCHAR(255)", nullable=False
                ),
                source_mappings=[
                    IRHubSourceMapping(
                        staging_table="stg_erp_orders",
                        bk_source_columns=["order_number"],
                        rec_src_value="ERP_NS",
                    )
                ],
            ),
        ],
        links=[
            IRLink(
                name="lnk_customer_order",
                link_type="STANDARD",
                semantic_role="Customer to Sales Order relationship",
                hk_column=IRHashColumn(
                    name="hk_lnk_customer_order",
                    data_type="BINARY(32)",
                    nullable=False,
                    hash_expression=IRHashExpression(
                        algorithm="SHA256",
                        input_columns=["hk_customer", "hk_sales_order"],
                        normalization="UPPER_TRIM",
                    ),
                ),
                hub_references=[
                    IRLinkHubReference(
                        fk_column_name="hk_customer",
                        target_hub="hub_customer",
                        target_hk_column="hk_customer",
                        data_type="BINARY(32)",
                        nullable=False,
                        role_name="customer",
                    ),
                    IRLinkHubReference(
                        fk_column_name="hk_sales_order",
                        target_hub="hub_sales_order",
                        target_hk_column="hk_sales_order",
                        data_type="BINARY(32)",
                        nullable=True,
                        role_name="sales_order",
                    ),
                ],
                degenerate_columns=[],
                immutable_measures=[],
                load_dts_column=IRColumnDef(
                    name="load_dts", data_type="TIMESTAMP_NTZ(9)", nullable=False
                ),
                rec_src_column=IRColumnDef(
                    name="rec_src", data_type="VARCHAR(255)", nullable=False
                ),
                source_mappings=[
                    {
                        "staging_table": "stg_erp_orders",
                        "fk_source_columns": {
                            "hk_customer": "hk_customer",
                            "hk_sales_order": "hk_sales_order",
                        },
                        "rec_src_value": "ERP_NS",
                    }
                ],
            )
        ],
        satellites=[
            IRSatellite(
                name="sat_customer_core",
                satellite_type="STANDARD",
                split_classification="CORE",
                parent_entity="hub_customer",
                parent_hk_column=IRParentHkColumn(
                    name="hk_customer",
                    target_column="hk_customer",
                    data_type="BINARY(32)",
                    nullable=False,
                ),
                load_dts_column=IRColumnDef(
                    name="load_dts", data_type="TIMESTAMP_NTZ(9)", nullable=False
                ),
                applied_dts_column=IRColumnDef(
                    name="applied_dts", data_type="TIMESTAMP_NTZ(9)", nullable=False
                ),
                rec_src_column=IRColumnDef(
                    name="rec_src", data_type="VARCHAR(255)", nullable=False
                ),
                hash_diff_column=IRHashColumn(
                    name="hash_diff",
                    data_type="BINARY(32)",
                    nullable=False,
                    hash_expression=IRHashExpression(
                        algorithm="SHA256",
                        input_columns=["customer_name", "credit_limit_usd", "is_vip_flag"],
                        normalization="EXACT_TRIM",
                    ),
                ),
                hash_diff_migration_policy="SPLIT_NEW_SATELLITE",
                payload_columns=[
                    IRColumnDef(name="customer_name", data_type="VARCHAR(255)", nullable=True),
                    IRColumnDef(name="credit_limit_usd", data_type="DECIMAL(18,2)", nullable=True),
                    IRColumnDef(name="is_vip_flag", data_type="BOOLEAN", nullable=True),
                ],
                source_mapping=IRSourceMapping(
                    staging_table="stg_crm_customers",
                    rec_src_value="CRM_SFDC",
                ),
            ),
            IRSatellite(
                name="sat_sales_order_metrics",
                satellite_type="STANDARD",
                split_classification="HIGH_VELOCITY",
                parent_entity="hub_sales_order",
                parent_hk_column=IRParentHkColumn(
                    name="hk_sales_order",
                    target_column="hk_sales_order",
                    data_type="BINARY(32)",
                    nullable=False,
                ),
                load_dts_column=IRColumnDef(
                    name="load_dts", data_type="TIMESTAMP_NTZ(9)", nullable=False
                ),
                rec_src_column=IRColumnDef(
                    name="rec_src", data_type="VARCHAR(255)", nullable=False
                ),
                hash_diff_column=IRHashColumn(
                    name="hash_diff",
                    data_type="BINARY(32)",
                    nullable=False,
                    hash_expression=IRHashExpression(
                        algorithm="SHA256",
                        input_columns=["order_total_usd", "fulfillment_status"],
                        normalization="EXACT_TRIM",
                    ),
                ),
                hash_diff_migration_policy="SPLIT_NEW_SATELLITE",
                payload_columns=[
                    IRColumnDef(name="order_total_usd", data_type="DECIMAL(18,2)", nullable=True),
                    IRColumnDef(name="fulfillment_status", data_type="VARCHAR(255)", nullable=True),
                ],
                source_mapping=IRSourceMapping(
                    staging_table="stg_erp_orders",
                    rec_src_value="ERP_NS",
                ),
            ),
        ],
        pit_tables=[
            IRPitTable(
                name="pit_customer",
                parent_entity="hub_customer",
                as_of_dts_column="as_of_dts",
                snapshot_window_policy="LOGARITHMIC",
                tracked_satellites=["sat_customer_core"],
            )
        ],
        bridge_tables=[],
        reference_tables=[],
    )


def test_global_commerce_vault_4zone_compiler_and_full_verification(tmp_path: Path) -> None:
    """Verify non-PE `global_commerce_vault` passes all 4 batches and all 15 DV-DYN checks."""
    commerce_ir = _build_global_commerce_canonical_ir()

    hub_map, link_map, sat_map, fk_edges = build_vault_topology_maps(commerce_ir)
    assert hub_map == {
        "raw_vault.hub_customer": "hk_customer",
        "raw_vault.hub_sales_order": "hk_sales_order",
    }
    assert link_map == {
        "raw_vault.lnk_customer_order": "hk_lnk_customer_order",
    }
    assert sat_map == {
        "raw_vault.sat_customer_core": "hk_customer",
        "raw_vault.sat_sales_order_metrics": "hk_sales_order",
    }
    assert len(fk_edges) == 4

    bundle = compile_canonical_ir_to_4zone_sql(commerce_ir)
    assert "CREATE SCHEMA IF NOT EXISTS bronze;" in bundle.zone_0_sql
    assert "CREATE OR REPLACE VIEW staging.stg_hub_customer" in bundle.zone_1_sql
    assert "COALESCE(" in bundle.zone_1_sql
    assert "INSERT INTO raw_vault.hub_customer" in bundle.zone_2_sql
    assert "CREATE TABLE IF NOT EXISTS business_vault.pit_customer" in bundle.zone_3_sql
    assert "CREATE OR REPLACE VIEW information_mart.vw_pit_customer_current" in bundle.zone_4_sql
    assert bundle.full_sql == bundle.full_sql_bundle

    ledger_path = tmp_path / "commerce_sandbox_events.jsonl"
    emitter = VaultLedgerEmitter(ledger_path=ledger_path)

    with DuckDbShadowSandbox(ledger_emitter=emitter) as sandbox:
        report = sandbox.run_full_verification(
            source=commerce_ir,
            emit_telemetry=True,
            run_id="run-commerce-20261102-001",
        )
        assert report.passed is True
        assert report.model_name == "global_commerce_vault"
        assert report.batch_report.all_passed is True
        assert len(report.batch_report.batches) == 4
        for batch in report.batch_report.batches:
            assert batch.passed is True, f"Batch {batch.batch_number} failed: {batch.detail}"

        # Batch 2 replay idempotency must have 0 delta rows
        assert report.batch_report.by_number[2].total_delta_rows == 0
        # Batch 3 single-attribute mutation must insert +1 Satellite row and +0 Hub/Link rows
        assert report.batch_report.by_number[3].satellite_delta_rows == 1
        assert report.batch_report.by_number[3].hub_delta_rows == 0
        assert report.batch_report.by_number[3].link_delta_rows == 0

        assert report.dynamic_check_report.all_passed is True
        assert report.dynamic_check_report.passed_count == 15
        assert report.dynamic_check_report.failed_count == 0
        for check in report.dynamic_check_report.checks:
            assert check.passed is True, f"{check.check_id} failed: {check.detail}"

        assert report.bitemporal_proof.verified is True
        assert report.observability_event is not None
        validated_ev = validate_observability_event(
            report.observability_event.model_dump(mode="json", exclude_none=True)
        )
        assert validated_ev.status == "PASS"

        # Verify Zone 4 Information Mart current view returns rows via pure INNER JOINs
        mart_rows = sandbox.conn.execute(
            "SELECT COUNT(*) FROM information_mart.vw_pit_customer_current"
        ).fetchone()
        assert mart_rows is not None and int(mart_rows[0]) > 0

        # Inject an orphan FK into raw_vault.lnk_customer_order and confirm DV-DYN-002 catches it
        sandbox.conn.execute(
            """
            INSERT INTO raw_vault.lnk_customer_order VALUES (
                REPEAT('a', 64),
                REPEAT('b', 64),
                REPEAT('0', 64),
                TIMESTAMP '2026-10-05 00:00:00',
                'ORPHAN_INJECTION'
            )
            """
        )
        fk_check = check_dv_dyn_002_referential_integrity(sandbox.conn, ir=commerce_ir)
        assert fk_check.passed is False
        assert fk_check.failing_rows == 1


def test_generic_contract_yaml_full_sandbox_verification(tmp_path: Path) -> None:
    """Verify `examples/dv_model_contract.yaml` lowers and passes full 4-batch verification."""
    generic_contract_path = get_examples_dir() / "dv_model_contract.yaml"
    with DuckDbShadowSandbox() as sandbox:
        report = sandbox.run_full_verification(
            source=generic_contract_path,
            emit_telemetry=False,
        )
        assert report.passed is True
        assert report.batch_report.all_passed is True
        assert report.dynamic_check_report.all_passed is True
        assert report.dynamic_check_report.passed_count == 15

    # Verify MULTI_ACTIVE satellite sub-sequence PK, bronze_tables CSV hydration, and Batch 4 parity
    csv_path = tmp_path / "crm_customers.csv"
    csv_path.write_text(
        "customer_code,customer_name,tier\n"
        "CUST-REAL-101,Acme Global,VIP\n"
        "CUST-REAL-202,Beta Corp,STD\n",
        encoding="utf-8",
    )
    base_ir = _build_global_commerce_canonical_ir()
    mas_sat = IRSatellite(
        name="mas_customer_tags",
        satellite_type="MULTI_ACTIVE",
        split_classification="CORE",
        parent_entity="hub_customer",
        parent_hk_column=IRParentHkColumn(
            name="hk_customer",
            target_column="hk_customer",
            data_type="BINARY(32)",
            nullable=False,
        ),
        child_dependent_keys=[
            IRColumnDef(name="tag_seq", data_type="INTEGER", nullable=False),
        ],
        load_dts_column=IRColumnDef(name="load_dts", data_type="TIMESTAMP_NTZ(9)", nullable=False),
        rec_src_column=IRColumnDef(name="rec_src", data_type="VARCHAR(255)", nullable=False),
        hash_diff_column=IRHashColumn(
            name="hash_diff",
            data_type="BINARY(32)",
            nullable=False,
            hash_expression=IRHashExpression(
                algorithm="SHA256",
                input_columns=["tag_seq", "tier"],
                normalization="EXACT_TRIM",
            ),
        ),
        hash_diff_migration_policy="SPLIT_NEW_SATELLITE",
        payload_columns=[
            IRColumnDef(name="tier", data_type="VARCHAR(255)", nullable=True),
        ],
        source_mapping=IRSourceMapping(
            staging_table="stg_crm_customers",
            rec_src_value="CRM_SFDC",
        ),
    )
    mas_ir = base_ir.model_copy(
        update={
            "model_name": "commerce_mas_vault",
            "satellites": [*base_ir.satellites, mas_sat],
        }
    )
    with DuckDbShadowSandbox() as mas_sbx:
        mas_rep = mas_sbx.run_full_verification(
            source=mas_ir,
            bronze_tables={"bronze.crm_customers": csv_path},
            emit_telemetry=True,
        )
        assert mas_rep.passed is True
        assert mas_rep.observability_event is not None
        assert mas_rep.observability_event.context_refs.canonical_ir_sha256 != "a" * 64
        cust_bks = {
            str(r[0])
            for r in mas_sbx.conn.execute(
                "SELECT customer_code FROM raw_vault.hub_customer "
                "WHERE rec_src <> 'SYSTEM_ZERO_KEY'"
            ).fetchall()
        }
        assert "CUST-REAL-101" in cust_bks
        assert "CUST-REAL-202" in cust_bks


def test_ir_lowerer_and_compiler_4zone_tier1_global_bkcc_standardization() -> None:
    """Verify lower_contract_to_canonical_ir and compile_canonical_ir_to_4zone_sql
    always include 'bkcc' and hash Tier-1 global keys with '6:GLOBAL||<len>:<bk>'
    across both Cold Start (S1) and Multi-Source Convergence (S2).
    """
    import copy

    import duckdb
    import yaml

    from opendv_core.hashing import compute_dv2_length_prefixed_sha256
    from opendv_core.models.contract_v5 import NoMistakeDataVault2ContractV5
    from opendv_duckdb_sandbox.ir_lowerer import lower_contract_to_canonical_ir

    raw_contract = yaml.safe_load(
        (get_examples_dir() / "dv_model_contract.yaml").read_text(encoding="utf-8")
    )
    s1_raw = copy.deepcopy(raw_contract)
    s1_raw["hubs"][0]["business_key_sources"] = [
        s
        for s in s1_raw["hubs"][0]["business_key_sources"]
        if s["key_authority_tier"] == "TIER_1_GLOBAL_STANDARD"
    ]

    s1_ir = lower_contract_to_canonical_ir(NoMistakeDataVault2ContractV5.model_validate(s1_raw))
    s2_ir = lower_contract_to_canonical_ir(
        NoMistakeDataVault2ContractV5.model_validate(raw_contract)
    )

    assert [b.name for b in s1_ir.hubs[0].business_keys] == ["bkcc", "company_uen"]
    assert [b.name for b in s2_ir.hubs[0].business_keys] == ["bkcc", "company_uen"]
    assert [sm.bkcc_prefix for sm in s1_ir.hubs[0].source_mappings] == ["GLOBAL", "GLOBAL"]
    assert [sm.bkcc_prefix for sm in s2_ir.hubs[0].source_mappings] == [
        "GLOBAL",
        "GLOBAL",
        "LEGACY_TICKET_DB",
    ]

    bronze_rows = {
        "bronze_crm.accounts": [{"company_uen": "202601234K", "legal_name": "Acme"}],
        "bronze_erp.debtors": [{"tax_reg_no": "202601234K", "credit_limit_sgd": 1000}],
        "bronze_legacy.tickets": [{"legacy_client_no": "1001"}],
    }
    s1_sql = compile_canonical_ir_to_4zone_sql(s1_ir, bronze_tables=bronze_rows).full_sql
    s2_sql = compile_canonical_ir_to_4zone_sql(s2_ir, bronze_tables=bronze_rows).full_sql

    expected_t1_hk = compute_dv2_length_prefixed_sha256(
        ["202601234K"], key_authority_tier="TIER_1_GLOBAL_STANDARD"
    ).lower()
    expected_t3_hk = compute_dv2_length_prefixed_sha256(
        ["1001"],
        bkcc_prefix="LEGACY_TICKET_DB",
        key_authority_tier="TIER_3_LOCAL_SCOPED",
    ).lower()

    with duckdb.connect(":memory:") as c1, duckdb.connect(":memory:") as c2:
        c1.execute(s1_sql)
        c2.execute(s2_sql)
        r1 = c1.execute(
            "SELECT hk_customer, bkcc, company_uen FROM raw_vault.hub_customer "
            "WHERE company_uen = '202601234K'"
        ).fetchone()
        r2 = c2.execute(
            "SELECT hk_customer, bkcc, company_uen FROM raw_vault.hub_customer "
            "WHERE company_uen = '202601234K'"
        ).fetchone()
        r2_t3 = c2.execute(
            "SELECT hk_customer, bkcc, company_uen FROM raw_vault.hub_customer "
            "WHERE company_uen = '1001'"
        ).fetchone()
        assert r1 == r2 == (expected_t1_hk, "GLOBAL", "202601234K")
        assert r2_t3 == (expected_t3_hk, "LEGACY_TICKET_DB", "1001")


def test_scenario_s10_dual_zero_key_3way_branching_e2e(tmp_path: Path) -> None:
    """Scenario S10 (Issue #43): Verify 3-way Zero-Key branching across Zone 1 -> 2 -> Gate 5."""
    import duckdb

    from opendv_core.constants import ZERO_KEY_ERROR, ZERO_KEY_UNKNOWN
    from opendv_duckdb_sandbox.dynamic_checks import (
        check_dv_dyn_002_referential_integrity,
        check_dv_dyn_007_ghost_and_zero_key_completeness,
    )

    commerce_ir = _build_global_commerce_canonical_ir()
    csv_path = tmp_path / "crm_customers_s10.csv"
    csv_path.write_text(
        "bkcc,customer_code,customer_name,credit_limit_usd,is_vip_flag\n"
        "GLOBAL,CUST-S10-101,Apex Industrial Holdings LLC,1250000.00,true\n"
        "GLOBAL,CUST-S10-202,Rheinland Precision GmbH,980000.00,false\n"
        "GLOBAL,-1,Unknown Customer Sentinel,0.00,false\n"
        "GLOBAL,-2,Error Customer Sentinel,0.00,false\n",
        encoding="utf-8",
    )

    bundle = compile_canonical_ir_to_4zone_sql(
        commerce_ir,
        bronze_tables={"bronze.crm_customers": csv_path},
    )

    con = duckdb.connect(":memory:")
    try:
        con.execute(bundle.zones_0_to_3_sql)
        # Inject explicit NULL, '-1', and '-2' rows into bronze Hub & Satellite sources
        con.execute(
            """
            INSERT INTO bronze.src_hub_customer VALUES
                ('GLOBAL', NULL, TIMESTAMP '2026-10-02 06:00:00', 'CRM_SFDC'),
                ('GLOBAL', '-1', TIMESTAMP '2026-10-02 06:00:00', 'CRM_SFDC'),
                ('GLOBAL', '-2', TIMESTAMP '2026-10-02 06:00:00', 'CRM_SFDC');
            INSERT INTO bronze.src_sat_customer_core VALUES
                ('GLOBAL', NULL, TIMESTAMP '2026-10-02 06:00:00', TIMESTAMP '2026-09-30 00:00:00',
                 'CRM_SFDC', 'Null Customer', 0.00, FALSE),
                ('GLOBAL', '-1', TIMESTAMP '2026-10-02 06:00:00', TIMESTAMP '2026-09-30 00:00:00',
                 'CRM_SFDC', 'Unknown Customer', 0.00, FALSE),
                ('GLOBAL', '-2', TIMESTAMP '2026-10-02 06:00:00', TIMESTAMP '2026-09-30 00:00:00',
                 'CRM_SFDC', 'Error Customer', 0.00, FALSE);
            """
        )
        # Re-run Zone 2 load to verify idempotency and Zero-Key filtering
        con.execute(bundle.zone_2_sql)

        # 1. Verify stg_hub_customer 3-way branching
        stg_hub_map = {
            r[0]: r[1]
            for r in con.execute(
                "SELECT customer_code, hk_customer FROM staging.stg_hub_customer"
            ).fetchall()
        }
        assert stg_hub_map["CUST-S10-101"] not in (ZERO_KEY_UNKNOWN, ZERO_KEY_ERROR)
        assert stg_hub_map["CUST-S10-202"] not in (ZERO_KEY_UNKNOWN, ZERO_KEY_ERROR)
        assert stg_hub_map[None] == ZERO_KEY_UNKNOWN
        assert stg_hub_map["-1"] == ZERO_KEY_UNKNOWN
        assert stg_hub_map["-2"] == ZERO_KEY_ERROR

        # 2. Verify stg_lnk_customer_order resolves NULL & '-1' -> UNKNOWN and '-2' -> ERROR
        stg_lnk_fks = [
            r[0]
            for r in con.execute(
                "SELECT hk_sales_order FROM staging.stg_lnk_customer_order"
            ).fetchall()
        ]
        assert ZERO_KEY_UNKNOWN in stg_lnk_fks
        assert ZERO_KEY_ERROR in stg_lnk_fks

        # 3. Verify DV-DYN-002 (0 orphans) and DV-DYN-007 (Ghost & Dual Zero-Key INNER JOIN)
        dyn_002 = check_dv_dyn_002_referential_integrity(con, ir=commerce_ir)
        assert dyn_002.passed is True
        assert dyn_002.failing_rows == 0

        dyn_007 = check_dv_dyn_007_ghost_and_zero_key_completeness(con, ir=commerce_ir)
        assert dyn_007.passed is True
        assert dyn_007.failing_rows == 0
    finally:
        con.close()


def test_scenario_s7_intrabatch_deduplication_ast_and_duckdb_e2e() -> None:
    """Scenario S7 (Issue #44): Compiled Zone 2 Hub/Link micro-batch deduplication SQL
    must pass Algorithm A.6 (`DV-AST-006` / `DV-G6-005`) and deduplicate duplicate
    natural keys within a single micro-batch in DuckDB using `MIN(load_dts)`.
    """
    import sqlglot
    from sqlglot import exp

    from opendv_axi_verifier import (
        run_axi_dv_verification_pipeline,
        verify_physical_dv2_sql_ast,
        verify_sql_models_batch,
    )
    from opendv_core.schema_registry import load_structured_file
    from opendv_duckdb_sandbox import (
        evaluate_all_dynamic_checks,
        lower_contract_to_canonical_ir,
    )

    commerce_ir = _build_global_commerce_canonical_ir()
    bundle = compile_canonical_ir_to_4zone_sql(commerce_ir)

    # 1. Parse compiled Zone 2 DML into per-entity SQL models and verify Gate 6 AST rules
    compiled_sql_models: dict[str, str] = {}
    for stmt in sqlglot.parse(bundle.zone_2_sql, read="duckdb"):
        if stmt is None:
            continue
        tbl = stmt.find(exp.Table)
        if tbl is not None and tbl.name:
            compiled_sql_models[tbl.name] = stmt.sql(dialect="duckdb")

    assert set(compiled_sql_models) == {
        "hub_customer",
        "hub_sales_order",
        "lnk_customer_order",
        "sat_customer_core",
        "sat_sales_order_metrics",
    }
    assert verify_sql_models_batch(compiled_sql_models) == []
    assert (
        verify_physical_dv2_sql_ast(
            compiled_sql_models["hub_customer"],
            entity_name="hub_customer",
            entity_type="HUB",
        )
        == []
    )
    assert (
        verify_physical_dv2_sql_ast(
            compiled_sql_models["lnk_customer_order"],
            entity_name="lnk_customer_order",
            entity_type="LINK",
        )
        == []
    )

    # 2. Verify full 6-Gate orchestrator (Algorithm A.8) with compiled Zone 2 SQL models
    generic_contract_path = get_examples_dir() / "dv_model_contract.yaml"
    generic_contract_dict = load_structured_file(generic_contract_path)
    generic_ir = lower_contract_to_canonical_ir(generic_contract_path, validate_schema=True)
    generic_bundle = compile_canonical_ir_to_4zone_sql(generic_ir)
    generic_sql_models = {
        tbl.name: stmt.sql(dialect="duckdb")
        for stmt in sqlglot.parse(generic_bundle.zone_2_sql, read="duckdb")
        if stmt is not None and (tbl := stmt.find(exp.Table)) is not None
    }
    axi_res = run_axi_dv_verification_pipeline(
        candidate_contract=generic_contract_dict,
        ontology_graph={},
        bronze_profile={},
        compiled_sql_models=generic_sql_models,
        apply_codemods=False,
    )
    assert axi_res.status == "PASS", f"Expected PASS, got {axi_res.status}: {axi_res.diagnostics}"

    # 3. Execute in-memory DuckDB sandbox with Scenario S7 intra-batch duplicate keys
    with DuckDbShadowSandbox() as sandbox:
        report = sandbox.run_full_verification(source=commerce_ir, emit_telemetry=False)
        assert report.passed is True

        # Inject 3 duplicate natural-key rows for ('CRM_SFDC', 'CUST-S7-DUP-777') and
        # 2 duplicate rows for 'ORD-S7-DUP-999' with unordered load_dts in one micro-batch
        sandbox.conn.execute(
            """
            INSERT INTO bronze.src_hub_customer (bkcc, customer_code, load_dts, rec_src) VALUES
                ('CRM_SFDC', 'CUST-S7-DUP-777', TIMESTAMP '2026-10-01 05:30:00', 'CRM_SFDC_LATE'),
                ('CRM_SFDC', 'CUST-S7-DUP-777', TIMESTAMP '2026-10-01 05:00:00', 'CRM_SFDC_EARLY'),
                ('CRM_SFDC', 'CUST-S7-DUP-777', TIMESTAMP '2026-10-01 05:45:00', 'CRM_SFDC_LATEST');

            INSERT INTO bronze.src_hub_sales_order (order_number, load_dts, rec_src) VALUES
                ('ORD-S7-DUP-999', TIMESTAMP '2026-10-01 05:20:00', 'ERP_NS_LATE'),
                ('ORD-S7-DUP-999', TIMESTAMP '2026-10-01 05:05:00', 'ERP_NS_EARLY');

            INSERT INTO bronze.src_lnk_customer_order (
                hk_customer__bkcc,
                hk_customer__customer_code,
                hk_sales_order__order_number,
                load_dts,
                rec_src
            ) VALUES
                (
                    'CRM_SFDC', 'CUST-S7-DUP-777', 'ORD-S7-DUP-999',
                    TIMESTAMP '2026-10-01 05:25:00', 'ERP_NS_LATE'
                ),
                (
                    'CRM_SFDC', 'CUST-S7-DUP-777', 'ORD-S7-DUP-999',
                    TIMESTAMP '2026-10-01 05:10:00', 'ERP_NS_EARLY'
                );
            """
        )
        sandbox.conn.execute(bundle.zone_2_sql)
        sandbox.conn.execute(bundle.zone_3_sql)

        hub_rows = sandbox.conn.execute(
            """
            SELECT bkcc, customer_code, CAST(load_dts AS VARCHAR)
            FROM raw_vault.hub_customer
            WHERE customer_code = 'CUST-S7-DUP-777'
            """
        ).fetchall()
        assert hub_rows == [("CRM_SFDC", "CUST-S7-DUP-777", "2026-10-01 05:00:00")]

        order_rows = sandbox.conn.execute(
            """
            SELECT order_number, CAST(load_dts AS VARCHAR)
            FROM raw_vault.hub_sales_order
            WHERE order_number = 'ORD-S7-DUP-999'
            """
        ).fetchall()
        assert order_rows == [("ORD-S7-DUP-999", "2026-10-01 05:05:00")]

        link_rows = sandbox.conn.execute(
            """
            SELECT COUNT(*), CAST(MIN(l.load_dts) AS VARCHAR)
            FROM raw_vault.lnk_customer_order l
            INNER JOIN raw_vault.hub_customer c ON c.hk_customer = l.hk_customer
            INNER JOIN raw_vault.hub_sales_order o ON o.hk_sales_order = l.hk_sales_order
            WHERE c.customer_code = 'CUST-S7-DUP-777'
              AND o.order_number = 'ORD-S7-DUP-999'
            """
        ).fetchall()
        assert link_rows == [(1, "2026-10-01 05:10:00")]

        dyn_report = evaluate_all_dynamic_checks(sandbox.conn, ir=commerce_ir)
        assert dyn_report.all_passed is True
        assert dyn_report.passed_count == 15
