"""Comprehensive Test Suite for `opendv-duckdb-sandbox` (`WP-05`).

Covers:
1. `ir_lowerer.py`: Lowering `NoMistakeDataVault2ContractV5` contracts (`dv_pe_model_contract.yaml`
   and `dv_model_contract.yaml`) into schema-valid `CanonicalIR` (`dv2-canonical-ir.schema.json`)
   with deterministic `topological_load_dag`.
2. `graph_b_profiler.py`: Empirical DuckDB profiling (tables, Parquet, CSV) for `uniqueness_ratio`,
   `non_null_ratio`, `shannon_entropy_norm`, `jaccard_inclusion`, `cdc_velocity_hz`,
   `write_amp_mb_per_day`, and $S_k(t) \\to S_k(t+1)$ schema drift detection (`ADD_COLUMN`,
   `DROP_COLUMN`, `RENAME_COLUMN`, `TYPE_WIDENING`, `SEMANTIC_TYPE_CHANGE`).
3. `compiler_4zone.py` + `adversarial_batches.py` + `dynamic_checks.py` + `sandbox_engine.py`:
   4-Zone SQL compilation, 4 Adversarial Batches in DuckDB `:memory:`, and all 15
   `DV-DYN-001`-`DV-DYN-015` checks (`0` orphans, both Zero Keys `'0'*64` and `'f'*64`
   in every Hub/Link, `1` Ghost Record `SYSTEM_GHOST` in every Satellite, Batch 2 replay
   $\\Delta\\text{rows} = 0$, Batch 3 `'AB'||'C'` vs `'A'||'BC'` boundary shift $+1$ Satellite row).
4. `pit_query_engine.py`: Bi-temporal `Query D.10` and `Query D.10b` proving `14.50x`
   (`$245.00M` EV = `245000000.00`) at `assertion_cutoff_ts = '2026-11-15 23:59:59'` and
   `12.80x` (`$216.28M` EV = `216280000.00`) at `assertion_cutoff_ts = '2026-10-15 12:00:00'`.
5. Telemetry integration: Emitting schema-valid dynamic sandbox `VaultObservabilityEvent`
   records via `VaultLedgerEmitter`.
6. Issue #38 (`Scenario S4` / `UF-1`): `compile_zone_0_migration_sql` idempotent
   `ALTER TABLE <schema>.<sat> ADD COLUMN IF NOT EXISTS` statements and multi-wave in-place
   Satellite schema evolution (`EVO-01`, `DV-EVO-006`, `DV-AST-001`, `DV-DYN-014`).
6. Issue #34 (`BUG-05` / Scenario `S6`): Bi-temporal adjacent effective state delta detection
   (`LAG(hash_diff) OVER (... ORDER BY applied_dts ASC, load_dts ASC)`), Point-in-Time
   reconstruction, and 4-Batch Adversarial Sandbox verification.
"""

from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest
import sqlglot

from opendv_axi_verifier import (
    verify_additive_evolution_invariants,
    verify_physical_dv2_sql_ast,
)
from opendv_core.constants import GHOST_DTS, GHOST_REC_SRC, ZERO_KEY_ERROR, ZERO_KEY_UNKNOWN
from opendv_core.schema_registry import (
    get_examples_dir,
    validate_canonical_ir,
    validate_contract_v5,
    validate_graph_b,
    validate_observability_event,
)
from opendv_duckdb_sandbox import (
    DuckDbShadowSandbox,
    GraphBProfiler,
    PitQueryEngine,
    SchemaDriftKind,
    build_algorithm_a5_sha256_sql,
    check_dv_dyn_001_pk_uniqueness,
    check_dv_dyn_002_referential_integrity,
    check_dv_dyn_014_additive_schema_evolution,
    compile_canonical_ir_to_4zone_sql,
    compile_zone_0_migration_sql,
    compute_topological_load_waves,
    evaluate_all_dynamic_checks,
    execute_4batch_adversarial_suite,
    execute_query_d10,
    execute_query_d10b,
    lower_contract_to_canonical_ir,
    run_batch_1_baseline_and_ghost_completeness,
    run_batch_2_replay_idempotency,
    run_batch_3_delta_and_boundary_shift,
    run_batch_4_cross_source_and_bitemporal_restatement,
    topological_load_dag,
)
from opendv_sources import SourceSpec, get_source_registry
from opendv_telemetry.ledger_emitter import VaultLedgerEmitter

# =====================================================================

# 1. Canonical IR Lowerer & Topological Load DAG Tests
# =====================================================================


def test_ir_lowerer_pe_contract_validates_against_canonical_ir_schema() -> None:
    """Lowering `dv_pe_model_contract.yaml` produces a schema-valid `CanonicalIR`."""
    pe_contract_path = get_examples_dir() / "private_equity" / "dv_pe_model_contract.yaml"
    pe_graph_b_path = get_examples_dir() / "private_equity" / "pe_graph_b_profile.json"
    from opendv_core.schema_registry import load_structured_file

    graph_b_raw = load_structured_file(pe_graph_b_path)
    lowered_ir = lower_contract_to_canonical_ir(
        pe_contract_path,
        graph_b_profile=graph_b_raw,
        validate_schema=True,
    )

    ir_dict = lowered_ir.model_dump(mode="json", exclude_none=True)
    validated_ir = validate_canonical_ir(ir_dict)
    assert validated_ir.ir_version == "1.0.0"
    assert lowered_ir.ir_version == "1.0.0"
    assert lowered_ir.hash_config.algorithm == "SHA256"
    assert lowered_ir.hash_config.encoding_scheme == "LENGTH_PREFIXED"
    assert lowered_ir.hash_config.null_token_payload == "-1:^^"
    assert lowered_ir.hash_config.zero_key_unknown_token == "-1"
    assert lowered_ir.hash_config.zero_key_error_token == "-2"

    waves = compute_topological_load_waves(lowered_ir)
    assert all(name.startswith(("hub_", "ref_")) for name in waves["wave_1_hubs_and_refs"])
    assert all(name.startswith(("lnk_", "sal_")) for name in waves["wave_2_links"])
    assert all(name.startswith(("sat_", "esat_")) for name in waves["wave_3_satellites"])
    assert all(name.startswith(("pit_", "brg_")) for name in waves["wave_4_pits_and_bridges"])

    dag_order = topological_load_dag(lowered_ir)
    assert dag_order.index("hub_limited_partner") < dag_order.index("lnk_lp_commitment")
    assert dag_order.index("lnk_lp_commitment") < dag_order.index("sat_lp_commitment_investran")


def test_ir_lowerer_generic_contract_and_4zone_compile() -> None:
    """Lowering `dv_model_contract.yaml` validates and compiles to executable Zone 0 SQL."""
    generic_contract_path = get_examples_dir() / "dv_model_contract.yaml"
    lowered_ir = lower_contract_to_canonical_ir(generic_contract_path, validate_schema=True)
    ir_dict = lowered_ir.model_dump(mode="json", exclude_none=True)
    validated_ir = validate_canonical_ir(ir_dict)
    assert validated_ir.model_name == lowered_ir.model_name

    bundle = compile_canonical_ir_to_4zone_sql(lowered_ir)
    con = duckdb.connect(":memory:")
    try:
        con.execute(bundle.zone_0_sql)
        zero_cnt = con.execute(
            f"""
            SELECT COUNT(*) FROM raw_vault.hub_customer
            WHERE hk_customer IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')
              AND rec_src = 'SYSTEM_ZERO_KEY'
            """
        ).fetchone()
        assert zero_cnt is not None and int(zero_cnt[0]) == 2

        ghost_cnt = con.execute(
            f"""
            SELECT COUNT(*) FROM raw_vault.sat_customer_crm
            WHERE hk_customer = '{ZERO_KEY_UNKNOWN}'
              AND load_dts = TIMESTAMP '{GHOST_DTS}'
              AND rec_src = '{GHOST_REC_SRC}'
            """
        ).fetchone()
        assert ghost_cnt is not None and int(ghost_cnt[0]) == 1
    finally:
        con.close()


# =====================================================================

# 2. Graph B Empirical Profiler & Schema Drift Detection Tests
# =====================================================================


def test_graph_b_profiler_metrics_parquet_csv_and_schema_drift(tmp_path: Path) -> None:
    """Verify GraphBProfiler computes all 6 Graph B metrics and detects S_k(t)->S_k(t+1) drift."""
    profiler = GraphBProfiler()
    try:
        profiler.conn.execute(
            """
            CREATE TABLE src_funds (
                fund_code VARCHAR,
                fund_strategy VARCHAR,
                target_size_usd DECIMAL(18,2)
            );
            INSERT INTO src_funds VALUES
                ('FUND-I', 'BUYOUT', 1000000000.00),
                ('FUND-II', 'BUYOUT', 2000000000.00),
                ('FUND-III', 'GROWTH', 1500000000.00),
                ('FUND-IV', 'BUYOUT', NULL);

            CREATE TABLE src_commitments (
                commitment_id VARCHAR,
                fund_code VARCHAR,
                committed_usd DECIMAL(18,2)
            );
            INSERT INTO src_commitments VALUES
                ('CMT-1', 'FUND-I', 50000000.00),
                ('CMT-2', 'FUND-II', 75000000.00),
                ('CMT-3', 'FUND-I', 25000000.00);
            """
        )

        col_metrics = profiler.profile_table_columns(
            "src_funds",
            delta_mutations_by_column={"fund_strategy": 12, "target_size_usd": 4},
            window_seconds=86400.0,
            parent_key_values_by_column={"fund_code": {"FUND-I", "FUND-II", "FUND-III", "FUND-IV"}},
        )
        by_col = {m.column_name: m for m in col_metrics}
        assert by_col["fund_code"].uniqueness_ratio == 1.0
        assert by_col["fund_code"].non_null_ratio == 1.0
        assert by_col["fund_code"].shannon_entropy_norm == 1.0
        assert by_col["fund_code"].jaccard_inclusion == 1.0
        assert by_col["target_size_usd"].non_null_ratio == 0.75
        assert by_col["fund_strategy"].cdc_velocity_hz > 0.0
        assert by_col["fund_strategy"].write_amp_mb_per_day > 0.0

        # Inclusion edge test
        edge = profiler.compute_table_inclusion_edge(
            edge_id="EDGE-CMT-FUND",
            child_table="src_commitments",
            child_column="fund_code",
            parent_table="src_funds",
            parent_column="fund_code",
            target_link="lnk_lp_commitment",
        )
        assert edge.asymmetric_inclusion_i_c_in_p == 1.0
        assert edge.orphan_value_count == 0

        # Build full GraphBProfile and validate against schema
        profile = profiler.build_graph_b_profile(
            domain="private_equity_test",
            source_system_id="INVESTRAN_TEST",
            bkcc_namespace="GLOBAL",
            table_names=["src_funds", "src_commitments"],
        )
        validated_profile = validate_graph_b(profile.model_dump(mode="json", exclude_none=True))
        assert validated_profile.domain_namespace == "private_equity_test"

        # Export to Parquet and CSV and profile both
        parquet_file = tmp_path / "funds.parquet"
        csv_file = tmp_path / "funds.csv"
        profiler.conn.execute(f"COPY src_funds TO '{parquet_file}' (FORMAT PARQUET)")
        profiler.conn.execute(f"COPY src_funds TO '{csv_file}' (FORMAT CSV, HEADER)")

        get_source_registry().get_tabular_adapter("file").register(
            SourceSpec(adapter="file", uri=str(parquet_file)), profiler.conn, "v_funds_pq"
        )
        get_source_registry().get_tabular_adapter("file").register(
            SourceSpec(adapter="file", uri=str(csv_file)), profiler.conn, "v_funds_csv"
        )
        pq_metrics = profiler.profile_relation("v_funds_pq")
        csv_metrics = profiler.profile_relation("v_funds_csv")
        assert len(pq_metrics) == 3
        assert len(csv_metrics) == 3

        # Schema drift S_k(t) -> S_k(t+1):
        # - RENAME_COLUMN: fund_strategy -> strategy_category (100% value overlap)
        # - ADD_COLUMN: esg_article_classification VARCHAR
        # - DROP_COLUMN: legacy_flag
        # - TYPE_WIDENING: vintage_num SMALLINT -> BIGINT
        # - SEMANTIC_TYPE_CHANGE: check_code INTEGER -> DATE
        profiler.conn.execute(
            """
            CREATE TABLE schema_t0 (
                fund_code VARCHAR,
                fund_strategy VARCHAR,
                legacy_flag VARCHAR,
                vintage_num SMALLINT,
                check_code INTEGER
            );
            INSERT INTO schema_t0 VALUES
                ('FUND-I', 'STRATEGY_BUYOUT_ALPHA', 'Y', 2020, 100),
                ('FUND-II', 'STRATEGY_GROWTH_BETA', 'N', 2022, 200),
                ('FUND-III', 'STRATEGY_VENTURE_GAMMA', 'Y', 2024, 300);

            CREATE TABLE schema_t1 (
                fund_code VARCHAR,
                strategy_category VARCHAR,
                esg_article_classification VARCHAR,
                vintage_num BIGINT,
                check_code DATE
            );
            INSERT INTO schema_t1 VALUES
                ('FUND-I', 'STRATEGY_BUYOUT_ALPHA', 'SFDR_ART_8', 2020, DATE '2026-01-01'),
                ('FUND-II', 'STRATEGY_GROWTH_BETA', 'SFDR_ART_9', 2022, DATE '2026-02-01'),
                ('FUND-III', 'STRATEGY_VENTURE_GAMMA', 'SFDR_ART_8', 2024, DATE '2026-03-01');
            """
        )

        drift = profiler.detect_schema_drift("schema_t0", "schema_t1")
        assert drift.has_drift is True
        drift_kinds = {ev.column_name: ev.drift_kind for ev in drift.events}
        assert drift_kinds["strategy_category"] == SchemaDriftKind.RENAME_COLUMN
        assert drift_kinds["esg_article_classification"] == SchemaDriftKind.ADD_COLUMN
        assert drift_kinds["legacy_flag"] == SchemaDriftKind.DROP_COLUMN
        assert drift_kinds["vintage_num"] == SchemaDriftKind.TYPE_WIDENING
        assert drift_kinds["check_code"] == SchemaDriftKind.SEMANTIC_TYPE_CHANGE
    finally:
        profiler.close()


# =====================================================================

# 3. 4-Zone SQL Compiler, 4 Adversarial Batches & DV-DYN-001..015 Tests
# =====================================================================


def test_4zone_compiler_and_4batch_adversarial_sandbox_all_15_dyn_checks() -> None:
    """Execute 4-Zone SQL bundle and all 4 Adversarial Batches with all 15 DV-DYN checks passing."""
    pe_ir = DuckDbShadowSandbox.load_default_pe_canonical_ir()
    bundle = compile_canonical_ir_to_4zone_sql(
        pe_ir,
        include_batch4_restatement_in_seed=False,
    )

    # Verify Zone 1 uses Algorithm A.5 length-prefixed SHA-256 and '-1:^^'
    assert "'-1:^^'" in bundle.zone_1_sql
    assert "SHA256(" in bundle.zone_1_sql
    assert "sal_limited_partner" in bundle.zone_0_sql

    con = duckdb.connect(":memory:")
    try:
        b1 = run_batch_1_baseline_and_ghost_completeness(con, bundle)
        assert b1.passed is True
        assert b1.orphan_count == 0
        assert b1.metrics["zero_keys_in_all_hubs_and_links"] is True
        assert b1.metrics["ghost_record_in_all_satellites"] is True

        b2 = run_batch_2_replay_idempotency(con, bundle)
        assert b2.passed is True
        assert b2.hub_delta_rows == 0
        assert b2.link_delta_rows == 0
        assert b2.satellite_delta_rows == 0
        assert b2.total_delta_rows == 0

        b3 = run_batch_3_delta_and_boundary_shift(con)
        assert b3.passed is True
        assert b3.metrics["boundary_shift_distinct"] is True
        assert b3.metrics["hash_diff_ab_c"] != b3.metrics["hash_diff_a_bc"]
        assert b3.hub_delta_rows == 0
        assert b3.link_delta_rows == 0
        assert b3.satellite_delta_rows == 1

        b4 = run_batch_4_cross_source_and_bitemporal_restatement(con)
        assert b4.passed is True
        assert b4.metrics["bkcc_collision_isolated"] is True
        assert b4.metrics["lp101_distinct_hub_keys"] == 2
        assert b4.satellite_delta_rows == 1
        assert b4.metrics["oct_15_ev_ebitda_multiple"] == "12.80"
        assert b4.metrics["oct_15_enterprise_value_usd"] == "216280000.00"
        assert b4.metrics["nov_15_ev_ebitda_multiple"] == "14.50"
        assert b4.metrics["nov_15_enterprise_value_usd"] == "245000000.00"

        dyn_report = evaluate_all_dynamic_checks(
            con,
            recorded_replay_delta=b2.total_delta_rows,
            recorded_sat_delta=b3.satellite_delta_rows,
            recorded_hub_link_delta=b3.hub_delta_rows + b3.link_delta_rows,
            historical_hash_mutations=0,
        )
        assert dyn_report.all_passed is True
        assert dyn_report.passed_count == 15
        assert dyn_report.failed_count == 0
        assert list(dyn_report.by_id) == [f"DV-DYN-{i:03d}" for i in range(1, 16)]
        for check in dyn_report.checks:
            assert check.passed is True, f"{check.check_id} failed: {check.detail}"
    finally:
        con.close()


# =====================================================================

# 4. Bi-Temporal Query D.10 & Query D.10b Verification Tests
# =====================================================================


def test_pit_query_engine_d10_and_d10b_bitemporal_audit() -> None:
    """Verify Query D.10 & D.10b at 2026-10-15 12:00:00 (12.80x) & 2026-11-15 23:59:59 (14.50x)."""
    pe_ir = DuckDbShadowSandbox.load_default_pe_canonical_ir()
    bundle = compile_canonical_ir_to_4zone_sql(
        pe_ir,
        include_batch4_restatement_in_seed=False,
    )

    con = duckdb.connect(":memory:")
    try:
        report = execute_4batch_adversarial_suite(con, bundle)
        assert report.all_passed is True

        # Query D.10b as of 2026-10-15 12:00:00 -> 12.80x ($216.28M EV = 216280000.00)
        rows_oct_15 = execute_query_d10b(
            con,
            assertion_cutoff_ts="2026-10-15 12:00:00",
            effective_as_of_dt="2026-09-30",
        )
        assert len(rows_oct_15) == 1
        oct_row = rows_oct_15[0]
        assert oct_row.lei_code == "529900T8BM49AURSDO55"
        assert oct_row.company_legal_name == "Nordic Cloud Infrastructure AB"
        assert oct_row.ev_ebitda_multiple == Decimal("12.80")
        assert oct_row.enterprise_value_usd == Decimal("216280000.00")
        assert oct_row.ic_memo_doc_sha256 is None

        # Query D.10b as of 2026-11-15 23:59:59 -> 14.50x ($245.00M EV = 245000000.00)
        rows_nov_15 = execute_query_d10b(
            con,
            assertion_cutoff_ts="2026-11-15 23:59:59",
            effective_as_of_dt="2026-09-30",
        )
        assert len(rows_nov_15) == 1
        nov_row = rows_nov_15[0]
        assert nov_row.lei_code == "529900T8BM49AURSDO55"
        assert nov_row.ev_ebitda_multiple == Decimal("14.50")
        assert nov_row.enterprise_value_usd == Decimal("245000000.00")
        assert (
            nov_row.ic_memo_doc_sha256
            == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )
        assert nov_row.steward_gate_status == "STEWARD_VERIFIED"

        # Query D.10 Golden LP Commitments
        d10_rows = execute_query_d10(
            con,
            assertion_cutoff_ts="2026-11-15 23:59:59",
            state_cutoff_date="2026-09-30",
        )
        assert len(d10_rows) in {0, 1, 2}
        if d10_rows:
            calpers = next(
                r for r in d10_rows if r.golden_lp_id in {"LP-101", "LIMITED_PARTNER-001"}
            )
            assert calpers.golden_bkcc == "GLOBAL"

        pit_engine = PitQueryEngine(con)
        summary = pit_engine.to_summary_dict()
        assert summary["bitemporal_proof"]["verified"] is True
    finally:
        con.close()


# =====================================================================

# 5. High-Level DuckDbShadowSandbox & Observability Telemetry Tests
# =====================================================================


def test_duckdb_shadow_sandbox_full_verification_and_telemetry(tmp_path: Path) -> None:
    """Verify DuckDbShadowSandbox runs Batches 1-4, DV-DYN-001-015, and dynamic sandbox event
    emission."""
    ledger_path = tmp_path / "sandbox_events.jsonl"
    emitter = VaultLedgerEmitter(ledger_path=ledger_path)

    with DuckDbShadowSandbox(ledger_emitter=emitter) as sandbox:
        report = sandbox.run_full_verification(emit_telemetry=True)
        assert report.passed is True
        assert report.batch_report.all_passed is True
        assert report.dynamic_check_report.all_passed is True
        assert report.bitemporal_proof.verified is True
        assert report.bitemporal_proof.prior_ev_ebitda_multiple == Decimal("12.80")
        assert report.bitemporal_proof.prior_enterprise_value_usd == Decimal("216280000.00")
        assert report.bitemporal_proof.restated_ev_ebitda_multiple == Decimal("14.50")
        assert report.bitemporal_proof.restated_enterprise_value_usd == Decimal("245000000.00")

        assert report.observability_event is not None
        ev_dict = report.observability_event.model_dump(mode="json", exclude_none=True)
        validated_ev = validate_observability_event(ev_dict)
        assert not hasattr(validated_ev, "viz" + "_id")
        assert not hasattr(report.observability_event, "viz" + "_id")
        assert not hasattr(report.observability_event, "work" + "bench_view_id")
        assert report.observability_event.stage_id == "STAGE_06_AXI_DV_DYNAMIC_SANDBOX"

    persisted_events = emitter.read_all_events()
    assert len(persisted_events) == 1
    assert persisted_events[0]["event_id"] == "evt-sandbox-observability"


def test_dynamic_checks_detect_injected_orphan_fk() -> None:
    """Injecting an orphan Link FK into DuckDB must cause DV-DYN-002 to fail."""
    with DuckDbShadowSandbox() as sandbox:
        report = sandbox.run_full_verification(emit_telemetry=False)
        assert report.passed is True

        # Inject a deliberate orphan FK into raw_vault.lnk_lp_commitment
        sandbox.conn.execute(
            """
            INSERT INTO raw_vault.lnk_lp_commitment VALUES (
                REPEAT('a', 64),
                REPEAT('b', 64),
                REPEAT('0', 64),
                TIMESTAMP '2026-10-05 00:00:00',
                'FAULT_INJECTION'
            )
            """
        )
        fk_check = check_dv_dyn_002_referential_integrity(sandbox.conn)
        assert fk_check.passed is False
        assert fk_check.failing_rows == 1

        pk_check = check_dv_dyn_001_pk_uniqueness(sandbox.conn)
        assert pk_check.passed is True


def test_algorithm_a5_sql_helper_matches_expected_tokens() -> None:
    """Verify `build_algorithm_a5_sha256_sql` implements 3-way Zero-Key branching (-1 vs -2)."""
    sql_expr = build_algorithm_a5_sha256_sql(
        ["lp_id"],
        is_business_key=True,
        bkcc_literal="GLOBAL",
    )
    con = duckdb.connect(":memory:")
    try:
        con.execute(
            "CREATE TABLE t (lp_id VARCHAR); "
            "INSERT INTO t VALUES ('lp-101'), (NULL), ('   '), ('-1'), ('-2');"
        )
        rows = con.execute(f"SELECT {sql_expr} FROM t").fetchall()
        assert rows[0][0] == "5d15eb10c8d7621d3167849c41c5f9cd9244fd79fb720cb17104d49e4b820ae4"
        assert rows[1][0] == ZERO_KEY_UNKNOWN
        assert rows[2][0] == ZERO_KEY_UNKNOWN
        assert rows[3][0] == ZERO_KEY_UNKNOWN
        assert rows[4][0] == ZERO_KEY_ERROR
    finally:
        con.close()


def test_algorithm_a5_sql_helper_3way_zero_key_and_error_predicate() -> None:
    """Verify composite `(bkcc, bk)` key_columns exclusion and explicit `error_predicate`."""
    sql_expr = build_algorithm_a5_sha256_sql(
        ["bkcc", "customer_code"],
        is_business_key=True,
        error_predicate="record_status = 'REJECTED'",
    )
    con = duckdb.connect(":memory:")
    try:
        con.execute(
            """
            CREATE TABLE src_cust (
                bkcc VARCHAR,
                customer_code VARCHAR,
                record_status VARCHAR
            );
            INSERT INTO src_cust VALUES
                ('GLOBAL', 'CUST-001', 'VALID'),
                ('GLOBAL', NULL, 'VALID'),
                ('GLOBAL', '', 'VALID'),
                ('GLOBAL', '-1', 'VALID'),
                ('GLOBAL', '-2', 'VALID'),
                ('GLOBAL', 'CUST-999', 'REJECTED');
            """
        )
        rows = [r[0] for r in con.execute(f"SELECT {sql_expr} FROM src_cust").fetchall()]
        assert rows[0] not in (ZERO_KEY_UNKNOWN, ZERO_KEY_ERROR)
        assert len(rows[0]) == 64
        assert rows[1] == ZERO_KEY_UNKNOWN
        assert rows[2] == ZERO_KEY_UNKNOWN
        assert rows[3] == ZERO_KEY_UNKNOWN
        assert rows[4] == ZERO_KEY_ERROR
        assert rows[5] == ZERO_KEY_ERROR
    finally:
        con.close()


@pytest.mark.parametrize(
    ("cutoff_ts", "expected_mult", "expected_ev"),
    [
        ("2026-10-15 12:00:00", Decimal("12.80"), Decimal("216280000.00")),
        ("2026-11-15 23:59:59", Decimal("14.50"), Decimal("245000000.00")),
    ],
)
def test_parametrized_query_d10b_cutoff_coordinates(
    cutoff_ts: str,
    expected_mult: Decimal,
    expected_ev: Decimal,
) -> None:
    """Parametrized verification of Query D.10b at both normative assertion cutoffs."""
    pe_ir = DuckDbShadowSandbox.load_default_pe_canonical_ir()
    bundle = compile_canonical_ir_to_4zone_sql(
        pe_ir,
        include_batch4_restatement_in_seed=True,
    )
    con = duckdb.connect(":memory:")
    try:
        con.execute(bundle.zones_0_to_3_sql)
        rows = execute_query_d10b(
            con,
            assertion_cutoff_ts=cutoff_ts,
            effective_as_of_dt="2026-09-30",
        )
        assert len(rows) == 1
        assert rows[0].ev_ebitda_multiple == expected_mult
        assert rows[0].enterprise_value_usd == expected_ev
    finally:
        con.close()


@pytest.fixture()
def pe_contract_path() -> Path:
    return get_examples_dir() / "private_equity" / "dv_pe_model_contract.yaml"


@pytest.fixture()
def pe_ir_path() -> Path:
    return get_examples_dir() / "private_equity" / "pe_canonical_ir.json"


def test_compiler_4zone_enforces_steward_gate_status_on_unstructured_links(
    pe_contract_path: Path,
    pe_ir_path: Path,
) -> None:
    """Issue #32 (BUG-03): Verify both PE and generic 4-Zone compilers emit
    `AND src.steward_gate_status = 'APPROVED'` on unstructured extraction links
    and block unapproved Tier-U3 rows from entering Raw Vault.
    """
    import json

    from opendv_duckdb_sandbox.dynamic_checks import check_dv_dyn_013_tier2_confidence_governance

    pe_ir = json.loads(pe_ir_path.read_text(encoding="utf-8"))
    pe_bundle = compile_canonical_ir_to_4zone_sql(pe_ir)

    assert (
        "CREATE OR REPLACE VIEW staging.stg_unstructured_vdr_llm_extractions"
        in pe_bundle.zone_1_sql
    )
    assert pe_bundle.zone_2_sql.count("AND src.steward_gate_status = 'APPROVED'") >= 2

    # Lower PE contract to generic IR and verify unstructured link metadata & Zone 2 predicate
    lowered_ir = lower_contract_to_canonical_ir(
        pe_contract_path, model_name="generic_unstructured_check"
    )
    unstructured_links = [lnk for lnk in lowered_ir.links if lnk.name.startswith("lnk_document_")]
    assert len(unstructured_links) == 2
    for lnk in unstructured_links:
        assert lnk.link_type == "UNSTRUCTURED_EXTRACTION_LINK"
        assert lnk.source_mappings[0].get("unstructured_extraction_link") is True
        assert (
            lnk.source_mappings[0].get("steward_gate_filter")
            == "src.steward_gate_status = 'APPROVED'"
        )
        assert lnk.requires_steward_approval_gate is True
        assert lnk.steward_filter_predicate == "steward_gate_status = 'APPROVED'"
        dumped_lnk = lnk.model_dump(mode="json")
        assert dumped_lnk["link_type"] == "UNSTRUCTURED_EXTRACTION_LINK"
        assert dumped_lnk["requires_steward_approval_gate"] is True
        assert dumped_lnk["steward_filter_predicate"] == "steward_gate_status = 'APPROVED'"

    # Verify DV-DYN-013 catches STEWARD_HOLD or low-confidence rows in PE and generic IR modes
    conn = duckdb.connect(":memory:")
    conn.execute(pe_bundle.zone_0_sql)
    conn.execute(pe_bundle.zone_1_sql)
    conn.execute(pe_bundle.zone_2_sql)
    conn.execute(pe_bundle.zone_3_sql)
    assert check_dv_dyn_013_tier2_confidence_governance(conn).passed is True

    conn.execute(
        """
        INSERT INTO business_vault.sat_lnk_doc_portco_ai_provenance (
            hk_lnk_document_portfolio_company,
            load_dts,
            rec_src,
            hash_diff,
            ev_ebitda_multiple,
            effective_date,
            extractor_model_id,
            confidence_score,
            evidence_quote
        ) VALUES (
            REPEAT('c', 64),
            TIMESTAMP '2026-11-16 00:00:00',
            'MANUAL_BYPASS_TEST',
            REPEAT('d', 64),
            15.00,
            DATE '2026-09-30',
            'claude-3-7-sonnet-20260219',
            0.7100,
            'Low-confidence row with APPROVED status'
        );
        """
    )
    dyn_check_after = check_dv_dyn_013_tier2_confidence_governance(conn)
    assert dyn_check_after.passed is False
    assert dyn_check_after.failing_rows >= 1

    # Generic CanonicalIR path: verify Zone 0 seeds APPROVED + STEWARD_HOLD on unstructured links
    # and attached link-satellites, Zone 2 blocks STEWARD_HOLD on both, and DV-DYN-013 enforces
    # confidence_score >= 0.85 even when only confidence_score is in payload_columns.
    from opendv_core.models.canonical_ir import CanonicalIR

    gen_ir_dict = json.loads(
        json.dumps(lowered_ir.model_dump(mode="json")).replace(
            "hub_limited_partner", "hub_investor"
        )
    )
    gen_ir_obj = CanonicalIR.model_validate(gen_ir_dict)
    gen_bundle = compile_canonical_ir_to_4zone_sql(gen_ir_obj)
    assert "'STEWARD_HOLD'" in gen_bundle.zone_0_sql
    assert gen_bundle.zone_2_sql.count("AND src.steward_gate_status = 'APPROVED'") >= 4

    gconn = duckdb.connect(":memory:")
    gconn.execute(gen_bundle.zones_0_to_3_sql)
    lnk_cnt = gconn.execute(
        """
        SELECT COUNT(*) FROM raw_vault.lnk_document_portfolio_company
        WHERE rec_src <> 'SYSTEM_ZERO_KEY'
        """
    ).fetchone()
    sat_cnt = gconn.execute(
        """
        SELECT COUNT(*) FROM raw_vault.sat_lnk_doc_portco_ai_provenance
        WHERE rec_src <> 'SYSTEM_GHOST'
        """
    ).fetchone()
    assert lnk_cnt is not None and lnk_cnt[0] == 1
    assert sat_cnt is not None and sat_cnt[0] == 1
    assert check_dv_dyn_013_tier2_confidence_governance(gconn, ir=gen_ir_obj).passed is True

    gconn.execute(
        """
        UPDATE raw_vault.sat_lnk_doc_portco_ai_provenance
        SET confidence_score = 0.7200
        WHERE rec_src <> 'SYSTEM_GHOST';
        """
    )
    gen_dyn_after = check_dv_dyn_013_tier2_confidence_governance(gconn, ir=gen_ir_obj)
    assert gen_dyn_after.passed is False
    assert gen_dyn_after.failing_rows >= 1


# ============================================================================
# 6. Issue #39 — Scoped (bronze_table, column_name, target_hub) BKCC Tests
# ============================================================================


def test_issue_39_composite_feed_scopes_bkcc_per_table_column_target_hub() -> None:
    """Verify composite feed with Tier-1 and Tier-3 columns scopes BKCC per (table, col, hub)."""
    from opendv_axi_verifier.verifiers.alg_a6_sqlglot_ast import verify_physical_dv2_sql_ast
    from opendv_core.hashing import compute_dv2_length_prefixed_sha256
    from opendv_core.schema_registry import validate_contract_v5
    from opendv_duckdb_sandbox import (
        build_staging_bkcc_registry,
        resolve_staging_bkcc_literal,
    )

    contract = validate_contract_v5(
        {
            "contract_version": "5.0",
            "domain_namespace": "corporate_mna_composite",
            "lifecycle_stage": "COLD_START_N1",
            "column_triage_ledger": [
                {
                    "bronze_table": "dealcloud.src_composite_deals",
                    "column_name": "lei_code",
                    "elm_classification": "BUSINESS_KEY",
                    "target_dv_entity": "hub_company",
                },
                {
                    "bronze_table": "dealcloud.src_composite_deals",
                    "column_name": "internal_deal_id",
                    "elm_classification": "BUSINESS_KEY",
                    "target_dv_entity": "hub_deal",
                },
                {
                    "bronze_table": "dealcloud.src_composite_deals",
                    "column_name": "deal_stage",
                    "elm_classification": "DESCRIPTIVE_PAYLOAD",
                    "target_dv_entity": "sat_deal_dealcloud",
                },
            ],
            "hubs": [
                {
                    "name": "hub_company",
                    "ontology_concept_uri": "urn:ontology:entity:company",
                    "business_key_sources": [
                        {
                            "bronze_table": "dealcloud.src_composite_deals",
                            "key_columns": ["lei_code"],
                            "record_source_code": "DEALCLOUD_FEED",
                            "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                            "require_src_qualifier": False,
                            "bkcc_prefix": "GLOBAL",
                        }
                    ],
                },
                {
                    "name": "hub_deal",
                    "ontology_concept_uri": "urn:ontology:entity:deal",
                    "business_key_sources": [
                        {
                            "bronze_table": "dealcloud.src_composite_deals",
                            "key_columns": ["internal_deal_id"],
                            "record_source_code": "DEALCLOUD_FEED",
                            "key_authority_tier": "TIER_3_LOCAL_SCOPED",
                            "require_src_qualifier": True,
                            "bkcc_prefix": "DEALCLOUD_LOCAL",
                            "system_scoped_bk_justification": (
                                "DealCloud pipeline ID is local to instance; "
                                "scoped via DEALCLOUD_LOCAL."
                            ),
                        }
                    ],
                },
            ],
            "links": [
                {
                    "name": "lnk_deal_company",
                    "link_type": "STANDARD_LINK",
                    "ontology_concept_uri": "urn:ontology:relationship:deal_company",
                    "source_bronze_table": "dealcloud.src_composite_deals",
                    "degenerate_columns": [],
                    "hub_references": [
                        {
                            "target_hub": "hub_deal",
                            "role_alias": "DEAL",
                            "source_fk_columns": ["internal_deal_id"],
                        },
                        {
                            "target_hub": "hub_company",
                            "role_alias": "TARGET_COMPANY",
                            "source_fk_columns": ["lei_code"],
                        },
                    ],
                }
            ],
            "satellites": [
                {
                    "name": "sat_deal_dealcloud",
                    "satellite_type": "STANDARD_SATELLITE",
                    "parent_entity": "hub_deal",
                    "source_bronze_table": "dealcloud.src_composite_deals",
                    "security_classification": "INTERNAL",
                    "volatility_tier": "STANDARD",
                    "payload_columns": ["deal_stage"],
                }
            ],
            "reference_tables": [],
            "unmapped_escalations": [],
        }
    )

    ir = lower_contract_to_canonical_ir(contract, validate_schema=True)
    registry = build_staging_bkcc_registry(ir)

    assert (
        resolve_staging_bkcc_literal(
            ir, "dealcloud.src_composite_deals", "lei_code", "hub_company", registry=registry
        )
        == "GLOBAL"
    )
    assert (
        resolve_staging_bkcc_literal(
            ir, "dealcloud.src_composite_deals", "internal_deal_id", "hub_deal", registry=registry
        )
        == "DEALCLOUD_LOCAL"
    )

    s2_dir = get_examples_dir() / "pipeline_samples" / "scenario_s2_issue39_bkcc"
    bundle = compile_canonical_ir_to_4zone_sql(
        ir,
        include_batch4_restatement_in_seed=True,
        bronze_tables={
            "dealcloud.src_composite_deals": s2_dir / "data" / "src_composite_deals.csv",
        },
    )

    # Algorithm A.6 SQLGlot AST check must pass and find '15:DEALCLOUD_LOCAL'
    ast_violations = verify_physical_dv2_sql_ast(
        bundle.zone_1_sql,
        entity_name="stg_dealcloud_composite_deals",
    )
    assert ast_violations == [], f"AST violations: {ast_violations}"
    assert "15:DEALCLOUD_LOCAL" in bundle.zone_1_sql

    con = duckdb.connect(":memory:")
    try:
        con.execute(bundle.zones_0_to_3_sql)
        stg_cols = [
            d[0]
            for d in con.execute(
                "SELECT * FROM staging.stg_dealcloud_composite_deals LIMIT 1"
            ).description
        ]
        assert "hk_company" in stg_cols
        assert "hk_deal" in stg_cols

        expected_hk_company = compute_dv2_length_prefixed_sha256(
            ["5493001KJTIIGC8Y1R12"],
            is_business_key=True,
            key_authority_tier="TIER_1_GLOBAL_STANDARD",
        ).lower()
        expected_hk_deal = compute_dv2_length_prefixed_sha256(
            ["DEALCLOUD_LOCAL", "DL-9001"], is_business_key=True
        ).lower()

        row = con.execute(
            "SELECT hk_company, hk_deal FROM staging.stg_dealcloud_composite_deals "
            "WHERE lei_code = '5493001KJTIIGC8Y1R12'"
        ).fetchone()
        assert row is not None
        assert row[0] == expected_hk_company
        assert row[1] == expected_hk_deal
    finally:
        con.close()


@pytest.mark.parametrize(
    ("raw_text", "expected_byte_len", "expected_payload_prehash", "expected_payload_sha256"),
    [
        (
            "Nordisk Suverän Fond",
            21,
            "21:Nordisk Suverän Fond",
            "8f158a694b28984db2ddd2d99d3fa4e67a624791dc3e1d7e27df363a49cd1ff7",
        ),
        (
            "München €25M",
            15,
            "15:München €25M",
            "e8cdf47ea5d7fc86222aea1232609e57f960e5e5d03c6c8c041e9dac72e34f57",
        ),
        (
            "日本国年金積立金管理運用独立行政法人",
            54,
            "54:日本国年金積立金管理運用独立行政法人",
            "b537f8a15b4fd2f74743b6b2245607c346e89f22d321934a67872854e02807d1",
        ),
        (
            "🏦 Sovereign Fund",
            19,
            "19:🏦 Sovereign Fund",
            "49828efad954c7a21d6ce5723459a90d2727b6a08d7ec02db3604c84cdcf2ae0",
        ),
    ],
)
def test_issue_30_octet_length_encode_multibyte_utf8_parity(
    raw_text: str,
    expected_byte_len: int,
    expected_payload_prehash: str,
    expected_payload_sha256: str,
) -> None:
    """Issue #30 (BUG-01 / DV-HASH-004): Verify `build_length_prefixed_token_sql` and
    `build_algorithm_a5_sha256_sql` emit `OCTET_LENGTH(ENCODE(...))` (never `STRLEN(...)`)
    and produce bit-identical pre-hash strings and SHA-256 digests to `opendv_core.hashing`
    across 2-byte Latin, 3-byte Euro/CJK, and 4-byte Emoji UTF-8 inputs.
    """
    from opendv_core.hashing import compute_dv2_length_prefixed_sha256, compute_dv2_prehash_string
    from opendv_duckdb_sandbox.compiler_4zone import build_length_prefixed_token_sql

    token_sql_pl = build_length_prefixed_token_sql("v.val", is_business_key=False)
    sha_sql_pl = build_algorithm_a5_sha256_sql(["v.val"], is_business_key=False)
    token_sql_bk = build_length_prefixed_token_sql("v.val", is_business_key=True)
    sha_sql_bk = build_algorithm_a5_sha256_sql(
        ["v.val"], is_business_key=True, bkcc_literal="LEGACY_GP_ADMIN"
    )

    assert "OCTET_LENGTH(ENCODE(" in token_sql_pl
    assert "STRLEN(" not in token_sql_pl
    assert "OCTET_LENGTH(ENCODE(" in sha_sql_pl
    assert "STRLEN(" not in sha_sql_pl

    assert len(raw_text.encode("utf-8")) == expected_byte_len
    assert len(raw_text) < expected_byte_len

    py_pre_pl = compute_dv2_prehash_string([raw_text], is_business_key=False)
    py_sha_pl = compute_dv2_length_prefixed_sha256([raw_text], is_business_key=False).lower()
    py_pre_bk = compute_dv2_prehash_string([raw_text], is_business_key=True)
    py_sha_bk = compute_dv2_length_prefixed_sha256(
        [raw_text], bkcc_prefix="LEGACY_GP_ADMIN", is_business_key=True
    ).lower()

    assert py_pre_pl == expected_payload_prehash
    assert py_sha_pl == expected_payload_sha256

    con = duckdb.connect(":memory:")
    try:
        row = con.execute(
            f"SELECT {token_sql_pl}, {sha_sql_pl}, {token_sql_bk}, {sha_sql_bk} "
            f"FROM (SELECT ? AS val) AS v",
            [raw_text],
        ).fetchone()
        assert row is not None
        assert row[0] == py_pre_pl
        assert row[1] == py_sha_pl
        assert row[2] == py_pre_bk
        assert row[3] == py_sha_bk
    finally:
        con.close()


def test_issue_39_scenario_s2_multi_source_convergence_and_4batch_sandbox() -> None:
    """Verify Scenario S2 multi-source convergence on hub_company + hub_deal + sal_company."""
    from opendv_axi_verifier.verifiers.alg_a6_sqlglot_ast import verify_physical_dv2_sql_ast
    from opendv_core.schema_registry import load_structured_file, validate_contract_v5
    from opendv_duckdb_sandbox import (
        build_staging_bkcc_registry,
        resolve_staging_bkcc_literal,
    )

    s2_dir = get_examples_dir() / "pipeline_samples" / "scenario_s2_issue39_bkcc"
    contract_path = s2_dir / "dv_s2_issue39_contract.yaml"
    graph_b_path = s2_dir / "graph_b_profile.json"
    graph_b_raw = load_structured_file(graph_b_path)

    ir = lower_contract_to_canonical_ir(
        contract_path,
        graph_b_profile=graph_b_raw,
        validate_schema=True,
    )
    bronze_tables = {
        "dealcloud.src_composite_deals": s2_dir / "data" / "src_composite_deals.csv",
        "bloomberg.src_global_issuers": s2_dir / "data" / "src_global_issuers.csv",
        "legacy_crm.src_local_companies": s2_dir / "data" / "src_local_companies.csv",
        "mdm.src_company_convergence": s2_dir / "data" / "src_company_convergence.csv",
    }

    bundle = compile_canonical_ir_to_4zone_sql(
        ir,
        bronze_tables=bronze_tables,
    )
    ast_violations = verify_physical_dv2_sql_ast(
        bundle.zone_1_sql,
        entity_name="stg_dealcloud_composite_deals",
    )
    assert ast_violations == [], f"AST failed: {ast_violations}"
    assert "15:DEALCLOUD_LOCAL" in bundle.zone_1_sql
    assert "16:LEGACY_CRM_LOCAL" in bundle.zone_1_sql

    con = duckdb.connect(":memory:")
    try:
        con.execute(bundle.zones_0_to_3_sql)
        co_rows = con.execute(
            "SELECT bkcc, lei_code, rec_src FROM raw_vault.hub_company "
            "WHERE rec_src != 'SYSTEM_ZERO_KEY' ORDER BY bkcc, lei_code"
        ).fetchall()
        assert co_rows == [
            ("GLOBAL", "5493001KJTIIGC8Y1R12", "DEALCLOUD_FEED"),
            ("GLOBAL", "5493002KJTIIGC8Y1R99", "DEALCLOUD_FEED"),
            ("LEGACY_CRM_LOCAL", "CO-101", "LEGACY_CRM_FEED"),
            ("LEGACY_CRM_LOCAL", "CO-102", "LEGACY_CRM_FEED"),
        ]
        bbg_sat_rows = con.execute(
            "SELECT d.company_legal_name, b.credit_rating_code "
            "FROM raw_vault.sat_company_dealcloud d "
            "JOIN raw_vault.sat_company_bloomberg b ON d.hk_company = b.hk_company "
            "WHERE d.rec_src != 'SYSTEM_GHOST' AND b.rec_src != 'SYSTEM_GHOST' "
            "ORDER BY d.company_legal_name"
        ).fetchall()
        assert bbg_sat_rows == [
            ("Alpha Holdings Plc", "AA+"),
            ("Beta Industrials GmbH", "BBB"),
        ]
        deal_rows = con.execute(
            "SELECT bkcc, internal_deal_id, rec_src FROM raw_vault.hub_deal "
            "WHERE rec_src != 'SYSTEM_ZERO_KEY' ORDER BY bkcc, internal_deal_id"
        ).fetchall()
        assert deal_rows == [
            ("DEALCLOUD_LOCAL", "DL-9001", "DEALCLOUD_FEED"),
            ("DEALCLOUD_LOCAL", "DL-9002", "DEALCLOUD_FEED"),
        ]
    finally:
        con.close()

    con_suite = duckdb.connect(":memory:")
    try:
        suite_res = execute_4batch_adversarial_suite(con_suite, ir=ir, bundle=bundle)
        assert suite_res.all_passed is True
    finally:
        con_suite.close()

    same_table_multi_col_contract = validate_contract_v5(
        {
            "contract_version": "5.0",
            "domain_namespace": "corporate_mna_s2",
            "lifecycle_stage": "MULTI_SOURCE_EXPANSION",
            "column_triage_ledger": [
                {
                    "bronze_table": "mdm.src_company_convergence",
                    "column_name": "master_lei_code",
                    "elm_classification": "BUSINESS_KEY",
                    "target_dv_entity": "hub_company",
                },
                {
                    "bronze_table": "mdm.src_company_convergence",
                    "column_name": "duplicate_legacy_co_num",
                    "elm_classification": "BUSINESS_KEY",
                    "target_dv_entity": "hub_company",
                },
                {
                    "bronze_table": "mdm.src_company_convergence",
                    "column_name": "confidence_score",
                    "elm_classification": "DESCRIPTIVE_PAYLOAD",
                    "target_dv_entity": "sat_company_mdm",
                },
            ],
            "hubs": [
                {
                    "name": "hub_company",
                    "ontology_concept_uri": "urn:ontology:entity:company",
                    "business_key_sources": [
                        {
                            "bronze_table": "mdm.src_company_convergence",
                            "key_columns": ["master_lei_code"],
                            "record_source_code": "MDM_MASTER",
                            "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                            "require_src_qualifier": False,
                            "bkcc_prefix": "GLOBAL",
                        },
                        {
                            "bronze_table": "mdm.src_company_convergence",
                            "key_columns": ["duplicate_legacy_co_num"],
                            "record_source_code": "MDM_DUPLICATE",
                            "key_authority_tier": "TIER_3_LOCAL_SCOPED",
                            "require_src_qualifier": True,
                            "bkcc_prefix": "LEGACY_CRM_LOCAL",
                            "system_scoped_bk_justification": (
                                "Local duplicate company number in same crosswalk table."
                            ),
                        },
                    ],
                }
            ],
            "links": [
                {
                    "name": "sal_company",
                    "link_type": "SAME_AS_LINK",
                    "ontology_concept_uri": "urn:ontology:relationship:company_equivalence",
                    "source_bronze_table": "mdm.src_company_convergence",
                    "degenerate_columns": [],
                    "hub_references": [
                        {
                            "target_hub": "hub_company",
                            "role_alias": "MASTER",
                            "source_fk_columns": ["master_lei_code"],
                        },
                        {
                            "target_hub": "hub_company",
                            "role_alias": "DUPLICATE",
                            "source_fk_columns": ["duplicate_legacy_co_num"],
                        },
                    ],
                }
            ],
            "satellites": [
                {
                    "name": "sat_company_mdm",
                    "satellite_type": "STANDARD_SATELLITE",
                    "parent_entity": "hub_company",
                    "source_bronze_table": "mdm.src_company_convergence",
                    "security_classification": "INTERNAL",
                    "volatility_tier": "STATIC",
                    "payload_columns": ["confidence_score"],
                }
            ],
            "reference_tables": [],
            "unmapped_escalations": [],
        }
    )
    multi_col_ir = lower_contract_to_canonical_ir(
        same_table_multi_col_contract,
        validate_schema=True,
    )
    multi_col_reg = build_staging_bkcc_registry(multi_col_ir)
    assert (
        resolve_staging_bkcc_literal(
            multi_col_ir,
            "mdm.src_company_convergence",
            "master_lei_code",
            "hub_company",
            registry=multi_col_reg,
        )
        == "GLOBAL"
    )
    assert (
        resolve_staging_bkcc_literal(
            multi_col_ir,
            "mdm.src_company_convergence",
            "duplicate_legacy_co_num",
            "hub_company",
            registry=multi_col_reg,
        )
        == "LEGACY_CRM_LOCAL"
    )


def test_issue_39_synthesize_cumulative_contract_per_column_bkcc_tier() -> None:
    """Verify AgenticVaultPipeline.synthesize_cumulative_contract scopes FK Hub tier per column."""
    from opendv_agent_harness.pipeline import (
        PipelineSourceFeedInput,
        _DeterministicStageSynthesizer,
    )
    from opendv_core.schema_registry import (
        load_structured_file,
        validate_graph_a,
        validate_graph_b,
    )

    s2_dir = get_examples_dir() / "pipeline_samples" / "scenario_s2_issue39_bkcc"
    ontology = validate_graph_a(load_structured_file(s2_dir / "graph_a_ontology.yaml"))
    profile = validate_graph_b(load_structured_file(s2_dir / "graph_b_profile.json"))
    composite_csv = s2_dir / "data" / "src_composite_deals.csv"

    src_feed = PipelineSourceFeedInput(
        source_system="dealcloud",
        table_name="src_composite_deals",
        data_path=str(composite_csv),
        record_source_code="DEALCLOUD_FEED",
        bkcc_namespace="DEALCLOUD_LOCAL",
        key_authority_tier="TIER_3_LOCAL_SCOPED",
        target_hub_name="hub_deal",
        business_key_columns=["internal_deal_id"],
        foreign_key_mappings={"lei_code": "hub_company"},
    )

    synthesized = _DeterministicStageSynthesizer.synthesize_cumulative_contract(
        ontology=ontology,
        cumulative_sources={"dealcloud.src_composite_deals": (src_feed, composite_csv)},
        cumulative_source_systems={"DEALCLOUD_FEED": profile.source_systems[0]},
        cumulative_unstructured_docs=[],
        added_drift_cols_by_table={},
        wave_index=0,
        domain_namespace="corporate_mna_s2",
    )

    hubs_by_name = {h["name"]: h for h in synthesized["hubs"]}
    assert "hub_deal" in hubs_by_name
    assert "hub_company" in hubs_by_name

    deal_sm = hubs_by_name["hub_deal"]["business_key_sources"][0]
    company_sm = hubs_by_name["hub_company"]["business_key_sources"][0]

    assert deal_sm["key_authority_tier"] == "TIER_3_LOCAL_SCOPED"
    assert deal_sm["bkcc_prefix"] == "DEALCLOUD_LOCAL"
    assert company_sm["key_authority_tier"] == "TIER_1_GLOBAL_STANDARD"
    assert company_sm["bkcc_prefix"] == "GLOBAL"

    sentinel_ontology_dict = load_structured_file(s2_dir / "graph_a_ontology.yaml")
    for cbc in sentinel_ontology_dict["canonical_business_concepts"]:
        if cbc["target_hub"] == "hub_deal":
            cbc["key_authority_tiers"]["tier_3_local_scoped"]["bkcc_policy"] = (
                "SOURCE_SYSTEM_PREFIX_REQUIRED"
            )
    sentinel_ontology = validate_graph_a(sentinel_ontology_dict)

    tier1_primary_feed = PipelineSourceFeedInput(
        source_system="dealcloud",
        table_name="src_composite_deals",
        data_path=str(composite_csv),
        record_source_code="DEALCLOUD_FEED",
        bkcc_namespace="GLOBAL",
        key_authority_tier="TIER_1_GLOBAL_STANDARD",
        target_hub_name="hub_company",
        business_key_columns=["lei_code"],
        foreign_key_mappings={"internal_deal_id": "hub_deal"},
    )

    sentinel_synth = _DeterministicStageSynthesizer.synthesize_cumulative_contract(
        ontology=sentinel_ontology,
        cumulative_sources={"dealcloud.src_composite_deals": (tier1_primary_feed, composite_csv)},
        cumulative_source_systems={"DEALCLOUD_FEED": profile.source_systems[0]},
        cumulative_unstructured_docs=[],
        added_drift_cols_by_table={},
        wave_index=0,
        domain_namespace="corporate_mna_s2",
    )
    sentinel_hubs = {h["name"]: h for h in sentinel_synth["hubs"]}
    sentinel_deal_sm = sentinel_hubs["hub_deal"]["business_key_sources"][0]
    assert sentinel_deal_sm["key_authority_tier"] == "TIER_3_LOCAL_SCOPED"
    assert sentinel_deal_sm["bkcc_prefix"] == "DEALCLOUD_FEED"


def test_issue_30_scenario_s7_multibyte_utf8_e2e_sandbox() -> None:
    """Issue #30 (Scenario S7 E2E): Profile, lower, compile, lint, and execute the
    Scenario S7 multi-byte UTF-8 dataset in `DuckDbShadowSandbox` with zero mocks.
    """
    from opendv_axi_verifier.verifiers.alg_a6_sqlglot_ast import verify_physical_dv2_sql_ast
    from opendv_core.hashing import compute_dv2_length_prefixed_sha256
    from opendv_core.schema_registry import get_examples_dir, load_structured_file
    from opendv_duckdb_sandbox import GraphBProfiler, lower_contract_to_canonical_ir

    pe_ir = DuckDbShadowSandbox.load_default_pe_canonical_ir()
    pe_bundle = compile_canonical_ir_to_4zone_sql(pe_ir)
    assert "STRLEN(" not in pe_bundle.full_sql
    assert pe_bundle.full_sql.count("OCTET_LENGTH(ENCODE(") >= 50

    s7_dir = get_examples_dir() / "scenarios" / "s7_multibyte_utf8"
    s7_contract = load_structured_file(s7_dir / "s7_multibyte_utf8_contract.yaml")
    crm_csv = s7_dir / "crm_customers.csv"
    orders_csv = s7_dir / "erp_orders.csv"

    with GraphBProfiler() as profiler:
        get_source_registry().get_tabular_adapter("file", workspace_root=s7_dir).register(
            SourceSpec(
                adapter="file",
                uri=str(crm_csv),
            ),
            profiler.conn,
            "v_crm",
        )
        crm_metrics = profiler.profile_relation("v_crm")
        assert len(crm_metrics) == 5
        assert crm_metrics[0].row_count == 4

    s7_ir = lower_contract_to_canonical_ir(s7_contract)
    bronze_tables = {
        "crm_salesforce.crm_customers": crm_csv,
        "erp_netsuite.erp_orders": orders_csv,
    }

    with DuckDbShadowSandbox() as sandbox:
        report = sandbox.run_full_verification(
            s7_ir,
            bronze_tables=bronze_tables,
            emit_telemetry=False,
            run_id="run-s7-multibyte-utf8",
        )
        assert report.passed is True
        assert report.batch_report.all_passed is True
        assert report.dynamic_check_report.all_passed is True
        assert "STRLEN(" not in report.compiled_bundle.full_sql
        assert "OCTET_LENGTH(ENCODE(" in report.compiled_bundle.zone_1_sql
        assert "OCTET_LENGTH(ENCODE(" in report.compiled_bundle.zone_3_sql

        for stmt in report.compiled_bundle.zone_1_sql.split(";"):
            stmt_clean = stmt.strip()
            if stmt_clean and (
                "VIEW staging.stg_hub_" in stmt_clean or "VIEW staging.stg_sat_" in stmt_clean
            ):
                violations = verify_physical_dv2_sql_ast(
                    stmt_clean,
                    entity_name="stg_s7_check",
                    entity_type="STAGE",
                    dialect="duckdb",
                )
                assert violations == []

        cust_row = sandbox.conn.execute(
            """
            SELECT hk_customer, customer_tax_id
            FROM raw_vault.hub_customer
            WHERE customer_tax_id = 'TAX-SE-ÅÄÖ-102'
            """
        ).fetchone()
        assert cust_row is not None
        expected_hk_cust = compute_dv2_length_prefixed_sha256(
            ["TAX-SE-ÅÄÖ-102"],
            is_business_key=True,
            key_authority_tier="TIER_1_GLOBAL_STANDARD",
        ).lower()
        assert cust_row[0] == expected_hk_cust

        sat_nordisk = sandbox.conn.execute(
            """
            SELECT hash_diff, account_segment, country_iso2, legal_name
            FROM raw_vault.sat_customer_core
            WHERE legal_name = 'Nordisk Suverän Fond'
            """
        ).fetchone()
        assert sat_nordisk is not None
        expected_hd_nordisk = compute_dv2_length_prefixed_sha256(
            ["SOVEREIGN_€", "SE", "Nordisk Suverän Fond"],
            is_business_key=False,
        ).lower()
        assert sat_nordisk[0] == expected_hd_nordisk


# =====================================================================

# 6. Issue #38 — Scenario S4 (UF-1): Idempotent Zone 0 Migration SQL & Multi-Wave Evolution
# =====================================================================


def _build_issue_38_contracts() -> tuple[dict[str, Any], dict[str, Any]]:
    """Build baseline Wave 0 and evolved Wave 2 contracts for Scenario S4 (UF-1)."""
    wave0_contract: dict[str, Any] = {
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
                "bronze_table": "crm_salesforce.crm_customers",
                "column_name": "country_iso2",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_customer_core",
            },
            {
                "bronze_table": "crm_salesforce.crm_customers",
                "column_name": "account_segment",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_customer_core",
            },
            {
                "bronze_table": "crm_salesforce.crm_customers",
                "column_name": "credit_limit_usd",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_customer_core",
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
            }
        ],
        "links": [],
        "satellites": [
            {
                "name": "sat_customer_core",
                "satellite_type": "STANDARD_SATELLITE",
                "parent_entity": "hub_customer",
                "source_bronze_table": "crm_salesforce.crm_customers",
                "volatility_tier": "STANDARD",
                "security_classification": "INTERNAL",
                "payload_columns": [
                    "legal_name",
                    "country_iso2",
                    "account_segment",
                    "credit_limit_usd",
                ],
            }
        ],
        "reference_tables": [],
        "unmapped_escalations": [],
    }

    wave2_contract = copy.deepcopy(wave0_contract)
    wave2_contract["lifecycle_stage"] = "MULTI_SOURCE_EXPANSION"
    wave2_contract["column_triage_ledger"].extend(
        [
            {
                "bronze_table": "crm_salesforce.crm_customers",
                "column_name": "loyalty_tier_code",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_customer_core",
            },
            {
                "bronze_table": "crm_salesforce.crm_customers",
                "column_name": "annual_spend_band",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_customer_core",
            },
        ]
    )
    wave2_contract["satellites"][0]["hash_diff_migration_policy"] = "NULL_EQUIVALENT_PADDING"
    wave2_contract["satellites"][0]["payload_columns"].extend(
        ["loyalty_tier_code", "annual_spend_band"]
    )
    return wave0_contract, wave2_contract


def test_issue_38_zone_0_migration_sql_idempotent_alter_table_add_column() -> None:
    """Verify `compile_zone_0_migration_sql` emits idempotent `ALTER TABLE ADD COLUMN`."""
    wave0_contract, wave2_contract = _build_issue_38_contracts()
    validate_contract_v5(wave0_contract)
    validate_contract_v5(wave2_contract)

    ir_w0 = lower_contract_to_canonical_ir(wave0_contract, validate_schema=True)
    ir_w2 = lower_contract_to_canonical_ir(wave2_contract, validate_schema=True)
    assert ir_w2.satellites[0].hash_diff_migration_policy == "NULL_EQUIVALENT_PADDING"

    migration_sql = compile_zone_0_migration_sql(ir_w2, baseline_ir=ir_w0)
    alter_lines = [
        line.strip()
        for line in migration_sql.splitlines()
        if line.strip().startswith("ALTER TABLE")
    ]
    assert alter_lines == [
        (
            "ALTER TABLE raw_vault.sat_customer_core "
            "ADD COLUMN IF NOT EXISTS loyalty_tier_code VARCHAR;"
        ),
        (
            "ALTER TABLE raw_vault.sat_customer_core "
            "ADD COLUMN IF NOT EXISTS annual_spend_band VARCHAR;"
        ),
    ]
    for col in ("legal_name", "country_iso2", "account_segment", "credit_limit_usd"):
        assert f"ADD COLUMN IF NOT EXISTS {col}" not in migration_sql

    full_migration_sql = compile_zone_0_migration_sql(ir_w2)
    full_alter_lines = [
        line.strip()
        for line in full_migration_sql.splitlines()
        if line.strip().startswith("ALTER TABLE")
    ]
    assert len(full_alter_lines) == 6
    for col in (
        "legal_name",
        "country_iso2",
        "account_segment",
        "credit_limit_usd",
        "loyalty_tier_code",
        "annual_spend_band",
    ):
        assert (
            f"ALTER TABLE raw_vault.sat_customer_core ADD COLUMN IF NOT EXISTS {col} "
            in full_migration_sql
        )

    assert verify_physical_dv2_sql_ast(migration_sql, entity_name="sat_customer_core") == []
    destructive_violations = verify_physical_dv2_sql_ast(
        "ALTER TABLE raw_vault.sat_customer_core DROP COLUMN legal_name;",
        entity_name="sat_customer_core",
    )
    assert len(destructive_violations) == 1
    assert "DV-AST-001" in destructive_violations[0]

    evo_violations = verify_additive_evolution_invariants(
        wave0_contract,
        wave2_contract,
        sql_scripts=[migration_sql],
    )
    assert evo_violations == []


def test_issue_38_in_place_satellite_evolution_e2e_duckdb() -> None:
    """Verify multi-wave in-place Satellite evolution in DuckDB preserves Wave 0 history."""
    wave0_contract, wave2_contract = _build_issue_38_contracts()
    ir_w0 = lower_contract_to_canonical_ir(wave0_contract, validate_schema=True)
    ir_w2 = lower_contract_to_canonical_ir(wave2_contract, validate_schema=True)

    wave0_rows = [
        {
            "customer_tax_id": "TAX-US-900101",
            "legal_name": "Apex Industrial Holdings LLC",
            "country_iso2": "US",
            "account_segment": "ENTERPRISE",
            "credit_limit_usd": 1250000.00,
            "load_dts": "2026-10-01 06:00:00",
        },
        {
            "customer_tax_id": "TAX-DE-800202",
            "legal_name": "Rheinland Precision GmbH",
            "country_iso2": "DE",
            "account_segment": "ENTERPRISE",
            "credit_limit_usd": 980000.00,
            "load_dts": "2026-10-01 06:00:00",
        },
    ]
    wave2_rows = [
        {
            "customer_tax_id": "TAX-US-900101",
            "legal_name": "Apex Industrial Holdings LLC",
            "country_iso2": "US",
            "account_segment": "ENTERPRISE",
            "credit_limit_usd": 1250000.00,
            "loyalty_tier_code": "PLATINUM",
            "annual_spend_band": "BAND_1M_5M",
            "load_dts": "2026-11-01 06:00:00",
        },
        {
            "customer_tax_id": "TAX-DE-800202",
            "legal_name": "Rheinland Precision GmbH",
            "country_iso2": "DE",
            "account_segment": "ENTERPRISE",
            "credit_limit_usd": 980000.00,
            "loyalty_tier_code": "GOLD",
            "annual_spend_band": "BAND_500K_1M",
            "load_dts": "2026-11-01 06:00:00",
        },
    ]

    bundle_w0 = compile_canonical_ir_to_4zone_sql(
        ir_w0,
        bronze_tables={"crm_salesforce.crm_customers": wave0_rows},
    )
    bundle_w2 = compile_canonical_ir_to_4zone_sql(
        ir_w2,
        bronze_tables={"crm_salesforce.crm_customers": wave2_rows},
        baseline_ir=ir_w0,
    )
    assert bundle_w2.zone_0_migration_sql.strip() != ""
    assert bundle_w2.zone_0_migration_sql in bundle_w2.zones_0_to_3_sql

    conn = duckdb.connect(":memory:")
    try:
        conn.execute(bundle_w0.zones_0_to_3_sql)
        w0_rows_before = conn.execute(
            """
            SELECT hk_customer, load_dts, legal_name, hash_diff
            FROM raw_vault.sat_customer_core
            WHERE rec_src <> 'SYSTEM_GHOST'
            ORDER BY legal_name
            """
        ).fetchall()
        assert len(w0_rows_before) == 2

        # Prove failure mode if zone_0_migration_sql is skipped before Zone 2 INSERT
        conn.execute(bundle_w2.zone_0_sql)
        conn.execute(bundle_w2.zone_1_sql)
        with pytest.raises(duckdb.BinderException, match="loyalty_tier_code"):
            conn.execute(bundle_w2.zone_2_sql)

        # Execute full Wave 2 bundle (with zone_0_migration_sql) and replay for idempotency
        conn.execute(bundle_w2.zones_0_to_3_sql)
        conn.execute(bundle_w2.zone_0_migration_sql)
        conn.execute(bundle_w2.zones_0_to_3_sql)

        w0_rows_after = conn.execute(
            """
            SELECT
                hk_customer, load_dts, legal_name, hash_diff,
                loyalty_tier_code, annual_spend_band
            FROM raw_vault.sat_customer_core
            WHERE rec_src <> 'SYSTEM_GHOST'
              AND load_dts = TIMESTAMP '2026-10-01 06:00:00'
            ORDER BY legal_name
            """
        ).fetchall()
        assert [(r[0], r[1], r[2], r[3]) for r in w0_rows_after] == w0_rows_before
        assert all(r[4] is None and r[5] is None for r in w0_rows_after)

        w2_rows_after = conn.execute(
            """
            SELECT legal_name, loyalty_tier_code, annual_spend_band
            FROM raw_vault.sat_customer_core
            WHERE rec_src <> 'SYSTEM_GHOST'
              AND load_dts = TIMESTAMP '2026-11-01 06:00:00'
            ORDER BY legal_name
            """
        ).fetchall()
        assert w2_rows_after == [
            ("Apex Industrial Holdings LLC", "PLATINUM", "BAND_1M_5M"),
            ("Rheinland Precision GmbH", "GOLD", "BAND_500K_1M"),
        ]

        total_business_rows = conn.execute(
            "SELECT COUNT(*) FROM raw_vault.sat_customer_core WHERE rec_src <> 'SYSTEM_GHOST'"
        ).fetchone()
        assert total_business_rows is not None and int(total_business_rows[0]) == 4

        dyn_014 = check_dv_dyn_014_additive_schema_evolution(
            conn,
            historical_hash_mutations=0,
            ir=ir_w2,
        )
        assert dyn_014.passed is True
    finally:
        conn.close()


def test_issue37_scenario_s7_cross_engine_hash_parity_e2e() -> None:
    """Issue #37 / Scenario S7 (FR-VER-01, FR-INT-01, Algorithm A.5, DV-COL-005):
    End-to-end cross-engine SHA-256 hex parity across Python (`opendv_core.hashing`),
    SQLGlot AST (`alg_a5_sqlglot_hash`), and 4-Zone DuckDB (`compiler_4zone`) without
    any `.lower()` or `.upper()` post-processing workarounds.
    """
    import re

    from opendv_axi_verifier.codemods.alg_a5_sqlglot_hash import build_canonical_sha256_hash_ast
    from opendv_core.hashing import (
        compute_dv2_length_prefixed_sha256,
        dv2_canonical_hash,
        resolve_fk_hash_key_with_zero_keys,
    )
    from opendv_core.models.contract_v5 import NoMistakeDataVault2ContractV5
    from opendv_core.schema_registry import load_structured_file

    lower_hex_re = re.compile(r"^[0-9a-f]{64}$")

    # 1. Validate real v5 Contract -> CanonicalIR -> 4-Zone SQL and execute in :memory: DuckDB
    pe_contract_path = get_examples_dir() / "private_equity" / "dv_pe_model_contract.yaml"
    pe_contract_dict = load_structured_file(pe_contract_path)
    contract = NoMistakeDataVault2ContractV5.model_validate(pe_contract_dict)
    ir = lower_contract_to_canonical_ir(contract)
    bronze_tables = {
        "investran.src_lp_investors": [{"lp_id": "LP-101"}],
        "legacy_gp.src_legacy_lp_roster": [{"lp_num": "LP-101"}],
        "investran.src_pe_funds": [{"fund_code": "APEX-BUYOUT-IV"}],
    }
    bundle = compile_canonical_ir_to_4zone_sql(ir, bronze_tables=bronze_tables)

    with duckdb.connect(":memory:") as conn:
        conn.execute("CREATE MACRO SHA2(s, bits) AS sha256(s)")
        conn.execute(
            "CREATE MACRO OCTET_LENGTH(s) AS "
            "CASE WHEN typeof(s) = 'BLOB' THEN strlen(decode(s::BLOB)) ELSE strlen(s::VARCHAR) END"
        )
        conn.execute(bundle.full_sql_bundle)

        # 2. Direct parameterized lookup of Python-computed Hub HKs in DuckDB raw_vault tables
        calpers_py_hk = compute_dv2_length_prefixed_sha256(
            ["LP-101"], is_business_key=True, bkcc_prefix="GLOBAL"
        )
        bavarian_py_hk = compute_dv2_length_prefixed_sha256(
            ["LP-101"], is_business_key=True, bkcc_prefix="LEGACY_GP_ADMIN"
        )
        fund_iv_vector_hk = dv2_canonical_hash(
            ["APEX_BO_IV"], is_business_key=True, bkcc_prefix="GLOBAL"
        )
        fund_iv_py_hk = dv2_canonical_hash(
            ["APEX-BUYOUT-IV"], is_business_key=True, bkcc_prefix="GLOBAL"
        )

        for digest in (calpers_py_hk, bavarian_py_hk, fund_iv_vector_hk, fund_iv_py_hk):
            assert lower_hex_re.match(digest), f"Expected lowercase 64-char hex, got {digest}"

        # Query DuckDB raw_vault.hub_limited_partner and raw_vault.hub_fund directly with Python HK
        lp_match = conn.execute(
            "SELECT COUNT(*) FROM raw_vault.hub_limited_partner WHERE hk_lp = ?",
            [calpers_py_hk],
        ).fetchone()
        assert lp_match is not None and lp_match[0] == 1

        bav_match = conn.execute(
            "SELECT COUNT(*) FROM raw_vault.hub_limited_partner WHERE hk_lp = ?",
            [bavarian_py_hk],
        ).fetchone()
        assert bav_match is not None and bav_match[0] == 1

        fund_match = conn.execute(
            "SELECT COUNT(*) FROM raw_vault.hub_fund WHERE hk_fund = ?",
            [fund_iv_py_hk],
        ).fetchone()
        assert fund_match is not None and fund_match[0] == 1

        # 3. Verify 3-way parity: Python vs compiler_4zone SQL vs SQLGlot AST codemod SQL
        vectors: list[tuple[list[str], dict[str, str | None], bool, str | None]] = [
            (["lp_id"], {"lp_id": "LP-101"}, True, "GLOBAL"),
            (["lp_id"], {"lp_id": "LP-101"}, True, "LEGACY_GP_ADMIN"),
            (["c1", "c2"], {"c1": "AB", "c2": "C"}, False, None),
            (["c1", "c2"], {"c1": "A", "c2": "BC"}, False, None),
            (["c1", "c2"], {"c1": "ANNE||O", "c2": "BRIEN"}, False, None),
            (["c1", "c2"], {"c1": "ANNE", "c2": "O||BRIEN"}, False, None),
            (["c1", "c2", "c3"], {"c1": "VAL_X", "c2": None, "c3": "VAL_Y"}, False, None),
            (["c1", "c2", "c3"], {"c1": "VAL_X", "c2": "VAL_Y", "c3": None}, False, None),
            (["city", "org"], {"city": "München", "org": "株式会社"}, False, None),
            (["lp_id"], {"lp_id": None}, True, "GLOBAL"),
        ]

        for cols, row_dict, is_bk, bkcc in vectors:
            ordered_cols = sorted(cols) if not is_bk else cols
            py_digest = compute_dv2_length_prefixed_sha256(
                [row_dict[c] for c in ordered_cols],
                is_business_key=is_bk,
                bkcc_prefix=bkcc,
            )
            c4z_sql = build_algorithm_a5_sha256_sql(
                cols,
                is_business_key=is_bk,
                bkcc_literal=bkcc,
                sort_alphabetically=not is_bk,
            )
            ast_sql = build_canonical_sha256_hash_ast(
                cols,
                is_business_key=is_bk,
                bkcc_prefix=bkcc,
                sort_alphabetically=not is_bk,
            ).sql("duckdb")

            select_list = ", ".join(
                f"CAST(NULL AS VARCHAR) AS {c}"
                if row_dict[c] is None
                else f"'{row_dict[c]}' AS {c}"
                for c in cols
            )
            row_vals = conn.execute(
                f"SELECT {c4z_sql}, {ast_sql} FROM (SELECT {select_list})"
            ).fetchone()
            assert row_vals is not None
            c4z_val, ast_val = row_vals

            assert lower_hex_re.match(py_digest)
            assert py_digest == c4z_val == ast_val

        # 4. Zero-Key & FK resolution lowercase uniformity
        assert resolve_fk_hash_key_with_zero_keys([None], bkcc_prefix="GLOBAL") == ZERO_KEY_UNKNOWN
        assert resolve_fk_hash_key_with_zero_keys(["-1"], bkcc_prefix="GLOBAL") == ZERO_KEY_UNKNOWN
        assert (
            resolve_fk_hash_key_with_zero_keys(
                [None], bkcc_prefix="GLOBAL", is_optional_inapplicable=True
            )
            == ZERO_KEY_ERROR
        )
        assert resolve_fk_hash_key_with_zero_keys(["-2"], bkcc_prefix="GLOBAL") == ZERO_KEY_ERROR
        assert resolve_fk_hash_key_with_zero_keys(["LP-101"], bkcc_prefix="GLOBAL") == calpers_py_hk


def test_scenario_s2_sal_canonical_ordering_and_dv_dyn_006_enforcement() -> None:
    """Issue #41 (GAP-10 / Scenario S2): End-to-end verification of Multi-Source Entity
    Convergence with Same-As Link canonical ordering across Gate 3, 4-Zone SQL compilation,
    and Gate 5 DuckDB Shadow Sandbox (DV-DYN-006 & DV-DYN-007).
    """
    from opendv_axi_verifier.verifiers.gate3_networkx_dag import verify_structural_invariants
    from opendv_core.models import NoMistakeDataVault2ContractV5
    from opendv_duckdb_sandbox.dynamic_checks import check_dv_dyn_006_bkcc_collision_and_sal

    s2_contract_dict = {
        "contract_version": "5.0",
        "domain_namespace": "s2_multi_source_customer_convergence",
        "lifecycle_stage": "MULTI_SOURCE_EXPANSION",
        "column_triage_ledger": [
            {
                "bronze_table": "erp.src_sap_customers",
                "column_name": "customer_id",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_customer",
            },
            {
                "bronze_table": "erp.src_sap_customers",
                "column_name": "legal_name",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_customer_erp",
            },
            {
                "bronze_table": "crm.src_sfdc_accounts",
                "column_name": "sf_account_id",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_customer",
            },
            {
                "bronze_table": "crm.src_sfdc_accounts",
                "column_name": "account_tier",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_customer_crm",
            },
            {
                "bronze_table": "mdm.src_customer_steward_crosswalk",
                "column_name": "master_customer_id",
                "elm_classification": "FOREIGN_KEY_REF",
                "target_dv_entity": "sal_customer",
            },
            {
                "bronze_table": "mdm.src_customer_steward_crosswalk",
                "column_name": "sf_account_id",
                "elm_classification": "FOREIGN_KEY_REF",
                "target_dv_entity": "sal_customer",
            },
        ],
        "hubs": [
            {
                "name": "hub_customer",
                "ontology_concept_uri": "urn:ontology:entity:customer",
                "business_key_sources": [
                    {
                        "bronze_table": "erp.src_sap_customers",
                        "key_columns": ["customer_id"],
                        "record_source_code": "ERP_SAP_CORE",
                        "key_authority_tier": "TIER_2_ENTERPRISE_SOR",
                        "require_src_qualifier": False,
                        "bkcc_prefix": None,
                    },
                    {
                        "bronze_table": "crm.src_sfdc_accounts",
                        "key_columns": ["sf_account_id"],
                        "record_source_code": "CRM_SALESFORCE",
                        "key_authority_tier": "TIER_3_LOCAL_SCOPED",
                        "require_src_qualifier": True,
                        "bkcc_prefix": "CRM_SALESFORCE",
                        "system_scoped_bk_justification": (
                            "Salesforce local account IDs collide with SAP customer numbers "
                            "and require BKCC scoping."
                        ),
                    },
                ],
            }
        ],
        "links": [
            {
                "name": "sal_customer",
                "link_type": "SAME_AS_LINK",
                "ontology_concept_uri": "urn:ontology:relationship:entity_equivalence",
                "source_bronze_table": "mdm.src_customer_steward_crosswalk",
                "hub_references": [
                    {
                        "target_hub": "hub_customer",
                        "role_alias": "MASTER_CUSTOMER",
                        "source_fk_columns": ["master_customer_id"],
                    },
                    {
                        "target_hub": "hub_customer",
                        "role_alias": "DUPLICATE_ALIAS_CUSTOMER",
                        "source_fk_columns": ["sf_account_id"],
                    },
                ],
            }
        ],
        "satellites": [
            {
                "name": "sat_customer_erp",
                "satellite_type": "STANDARD_SATELLITE",
                "parent_entity": "hub_customer",
                "source_bronze_table": "erp.src_sap_customers",
                "security_classification": "PUBLIC",
                "volatility_tier": "STATIC",
                "payload_columns": ["legal_name"],
            },
            {
                "name": "sat_customer_crm",
                "satellite_type": "STANDARD_SATELLITE",
                "parent_entity": "hub_customer",
                "source_bronze_table": "crm.src_sfdc_accounts",
                "security_classification": "PUBLIC",
                "volatility_tier": "STANDARD",
                "payload_columns": ["account_tier"],
            },
        ],
        "reference_tables": [],
        "unmapped_escalations": [],
    }

    # 1. Gate 3 static verification passes on canonical S2 contract
    assert verify_structural_invariants(s2_contract_dict) == []

    # 2. Full 4-Batch Shadow Sandbox passes all 15 dynamic invariants (DV-DYN-001..015)
    contract = NoMistakeDataVault2ContractV5.model_validate(s2_contract_dict)
    ir = lower_contract_to_canonical_ir(contract)
    sandbox_report = DuckDbShadowSandbox().run_full_verification(ir)
    assert sandbox_report.passed is True

    # 3. Verify Zone 2 SAL DML filters out reflexive (A, A) and inverted (B, A) staging rows
    bundle = compile_canonical_ir_to_4zone_sql(ir)
    conn = duckdb.connect(":memory:")
    conn.execute(bundle.zone_0_sql)
    conn.execute(bundle.zone_1_sql)

    # Inject adversarial (A, A) self-loop and (A, B) + (B, A) inverted pair into Bronze crosswalk
    conn.execute(
        """
        INSERT INTO bronze.src_sal_customer VALUES
            (
                'GLOBAL',
                'CUST-100',
                'GLOBAL',
                'CUST-100',
                TIMESTAMP '2026-10-02 00:00:00',
                'ADVERSARIAL_SELF_LOOP'
            ),
            (
                'GLOBAL',
                'CUST-100',
                'CRM_SALESFORCE',
                'CUST-200',
                TIMESTAMP '2026-10-02 01:00:00',
                'ADVERSARIAL_PAIR_AB'
            ),
            (
                'CRM_SALESFORCE',
                'CUST-200',
                'GLOBAL',
                'CUST-100',
                TIMESTAMP '2026-10-02 02:00:00',
                'ADVERSARIAL_PAIR_BA'
            );
        """
    )
    conn.execute(bundle.zone_2_sql)

    # Verify that Zone 2 DML actually filtered out reflexive (A, A) and inverted (B, A) rows
    self_loop_cnt = conn.execute(
        """
        SELECT COUNT(*) FROM business_vault.sal_customer
        WHERE hk_sal_customer NOT IN (
            '0000000000000000000000000000000000000000000000000000000000000000',
            'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'
        )
          AND hk_customer_master_customer = hk_customer_duplicate_alias_customer;
        """
    ).fetchone()
    assert self_loop_cnt is not None and int(self_loop_cnt[0]) == 0

    inv_pair_cnt = conn.execute(
        """
        SELECT COUNT(*) FROM business_vault.sal_customer s1
        INNER JOIN business_vault.sal_customer s2
            ON s1.hk_customer_master_customer = s2.hk_customer_duplicate_alias_customer
           AND s1.hk_customer_duplicate_alias_customer = s2.hk_customer_master_customer
        WHERE s1.hk_sal_customer NOT IN (
            '0000000000000000000000000000000000000000000000000000000000000000',
            'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'
        )
          AND s2.hk_sal_customer NOT IN (
            '0000000000000000000000000000000000000000000000000000000000000000',
            'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'
        )
          AND s1.hk_customer_master_customer <> s1.hk_customer_duplicate_alias_customer;
        """
    ).fetchone()
    assert inv_pair_cnt is not None and int(inv_pair_cnt[0]) == 0

    # Verify DV-DYN-006 passes because Zone 2 DML filtered out (A, A) and (B, A)
    dyn_006_clean = check_dv_dyn_006_bkcc_collision_and_sal(conn, ir=ir)
    assert dyn_006_clean.passed is True
    assert dyn_006_clean.failing_rows == 0

    # 4. Directly insert a reflexive (A, A) row and an inverted (B, A) row into
    # business_vault.sal_customer to prove DV-DYN-006 catches bypassed SAL violations
    existing_edge = conn.execute(
        """
        SELECT hk_customer_master_customer, hk_customer_duplicate_alias_customer
        FROM business_vault.sal_customer
        WHERE hk_sal_customer NOT IN (
            '0000000000000000000000000000000000000000000000000000000000000000',
            'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'
        )
          AND hk_customer_duplicate_alias_customer NOT IN (
            '0000000000000000000000000000000000000000000000000000000000000000',
            'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'
        )
        LIMIT 1;
        """
    ).fetchone()
    assert existing_edge is not None
    hk_a, hk_b = existing_edge[0], existing_edge[1]

    conn.execute(
        f"""
        INSERT INTO business_vault.sal_customer (
            hk_sal_customer,
            hk_customer_master_customer,
            hk_customer_duplicate_alias_customer,
            load_dts,
            rec_src
        ) VALUES
            (
                '1111111111111111111111111111111111111111111111111111111111111111',
                '{hk_a}',
                '{hk_a}',
                TIMESTAMP '2026-10-03 00:00:00',
                'BYPASS_SELF_LOOP'
            ),
            (
                '2222222222222222222222222222222222222222222222222222222222222222',
                '{hk_b}',
                '{hk_a}',
                TIMESTAMP '2026-10-03 00:00:00',
                'BYPASS_INVERTED_DUP'
            );
        """
    )
    dyn_006_corrupted = check_dv_dyn_006_bkcc_collision_and_sal(conn, ir=ir)
    assert dyn_006_corrupted.passed is False
    assert dyn_006_corrupted.failing_rows >= 2
    conn.close()


# =====================================================================
# Issue #42 — Scenario S9: Dynamic bkcc_column_expr Multi-Tenant Hashing
# =====================================================================


def test_build_algorithm_a5_sha256_sql_dynamic_bkcc_column_expr_and_zero_key() -> None:
    """Issue #42 (GAP-11 / S9): `build_algorithm_a5_sha256_sql` supports `bkcc_column_expr`
    for dynamic row-level tenant namespace prefixing while preserving `KEY-02(b)` Zero-Key
    (`REPEAT('0', 64)`) when business key columns are NULL.
    """
    from opendv_axi_verifier.codemods.alg_a5_sqlglot_hash import build_canonical_sha256_hash_ast
    from opendv_core.hashing import compute_dv2_length_prefixed_sha256

    sql_expr = build_algorithm_a5_sha256_sql(
        ["account_id"],
        is_business_key=True,
        bkcc_column_expr="tenant_id",
    )
    # Deduplicates if caller also includes tenant_id in columns list
    sql_expr_dedup = build_algorithm_a5_sha256_sql(
        ["tenant_id", "account_id"],
        is_business_key=True,
        bkcc_column_expr="tenant_id",
    )
    assert sql_expr == sql_expr_dedup

    sql_single_col = build_algorithm_a5_sha256_sql(
        ["tenant_id"],
        is_business_key=True,
        bkcc_column_expr="tenant_id",
    )
    ast_single_col = build_canonical_sha256_hash_ast(
        ["tenant_id"],
        is_business_key=True,
        bkcc_column_expr="tenant_id",
    )
    assert isinstance(ast_single_col, sqlglot.exp.Case)

    con = duckdb.connect(":memory:")
    try:
        rows = con.execute(
            f"""
            SELECT
                tenant_id,
                account_id,
                {sql_expr} AS computed_hk,
                {sql_single_col} AS single_col_hk
            FROM (
                VALUES
                    ('TENANT_US', 'ACCT-1001'),
                    ('TENANT_EU', 'ACCT-1001'),
                    ('  tenant_us ', ' acct-1001 '),
                    ('TENANT_US', NULL),
                    ('TENANT_US', '   '),
                    (NULL, 'ACCT-1001')
            ) AS t(tenant_id, account_id)
            """
        ).fetchall()
    finally:
        con.close()

    expected_us = compute_dv2_length_prefixed_sha256(
        ["ACCT-1001"], is_business_key=True, bkcc_prefix="TENANT_US"
    ).lower()
    expected_eu = compute_dv2_length_prefixed_sha256(
        ["ACCT-1001"], is_business_key=True, bkcc_prefix="TENANT_EU"
    ).lower()

    assert rows[0][2] == expected_us
    assert rows[1][2] == expected_eu
    assert rows[0][2] != rows[1][2]
    assert rows[2][2] == expected_us
    assert rows[3][2] == ZERO_KEY_UNKNOWN
    assert rows[4][2] == ZERO_KEY_UNKNOWN
    assert rows[5][3] == ZERO_KEY_UNKNOWN


def test_scenario_s9_multitenant_bkcc_column_expr_e2e_4zone_and_sandbox(tmp_path: Path) -> None:
    """Issue #42 (Scenario S9): E2E verification of single-table multi-tenant CSV feeds
    using `bkcc_column_expr='tenant_id'` across Contract V5, Gate 1, Canonical IR lowering,
    4-Zone DuckDB compilation, and 4-Batch Adversarial Shadow Sandbox (`DV-DYN-001..015`).
    """
    from opendv_axi_verifier.codemods.alg_a5_sqlglot_hash import (
        codemod_enforce_staging_hashes_sql,
    )
    from opendv_axi_verifier.verifiers.alg_a7_evolution_lock import verify_evolution_lock_alg_a7
    from opendv_axi_verifier.verifiers.gate1_contract_grain import verify_gate1_contract_and_grain
    from opendv_core.hashing import compute_dv2_length_prefixed_sha256
    from opendv_core.schema_registry import load_structured_file, validate_contract_v5

    s9_dir = get_examples_dir() / "scenarios" / "s9_multitenant"
    accounts_csv = s9_dir / "tenant_accounts.csv"
    subs_csv = s9_dir / "tenant_subscriptions.csv"
    contract_dict = load_structured_file(s9_dir / "contract_s9.yaml")
    graph_a = load_structured_file(s9_dir / "graph_a_ontology.yaml")
    graph_b = load_structured_file(s9_dir / "graph_b_profile.json")

    # 1. Validate Contract V5, Gate 1, and Gate 4 Evolution Lock
    contract = validate_contract_v5(contract_dict)
    g1_diags = verify_gate1_contract_and_grain(contract_dict, graph_a, graph_b)
    assert g1_diags == []

    whitespace_bkcc_contract = {
        **contract_dict,
        "hubs": [
            {
                **contract_dict["hubs"][0],
                "business_key_sources": [
                    {
                        **contract_dict["hubs"][0]["business_key_sources"][0],
                        "bkcc_column_expr": "   ",
                        "bkcc_prefix": "   ",
                    }
                ],
            },
            contract_dict["hubs"][1],
        ],
    }
    ws_diags = verify_gate1_contract_and_grain(whitespace_bkcc_contract, graph_a, graph_b)
    assert any("DV-COL-012" in d for d in ws_diags)

    mutated_contract = {
        **contract_dict,
        "hubs": [
            {
                **contract_dict["hubs"][0],
                "business_key_sources": [
                    {
                        **contract_dict["hubs"][0]["business_key_sources"][0],
                        "bkcc_column_expr": "org_code",
                    }
                ],
            },
            contract_dict["hubs"][1],
        ],
    }
    evo_violations = verify_evolution_lock_alg_a7(contract_dict, mutated_contract)
    assert any("BKCC_MUTATED" in v for v in evo_violations)

    # 2. Verify SQLGlot AST codemod with dynamic bkcc_column_expr
    rewritten_sql = codemod_enforce_staging_hashes_sql(
        "SELECT tenant_id, account_id FROM bronze_saas.tenant_accounts",
        {
            "hk_tenant_account": {
                "columns": ["account_id"],
                "is_business_key": True,
                "bkcc_column_expr": "tenant_id",
            }
        },
    )
    parsed_rewritten = sqlglot.parse_one(rewritten_sql, read="duckdb")
    hk_proj = next(
        expr
        for expr in parsed_rewritten.expressions
        if expr.alias_or_name.lower() == "hk_tenant_account"
    )
    hk_case = hk_proj.this
    assert isinstance(hk_case, sqlglot.exp.Case)
    zero_key_pred_cols = {
        c.name.lower() for c in hk_case.args["ifs"][0].this.find_all(sqlglot.exp.Column)
    }
    assert zero_key_pred_cols == {"account_id"}
    hashed_col_sequence = [
        c.name.lower() for c in hk_case.args["default"].find_all(sqlglot.exp.Column, bfs=False)
    ]
    assert set(hashed_col_sequence) == {"tenant_id", "account_id"}
    assert hashed_col_sequence.index("tenant_id") < hashed_col_sequence.index("account_id")

    # 3. Lower to Canonical IR & validate against JSON Schema
    ir = lower_contract_to_canonical_ir(
        contract,
        model_name="s9_multitenant_saas_vault",
        validate_schema=True,
    )
    validated_ir = validate_canonical_ir(ir.model_dump(mode="json", exclude_none=True))
    assert validated_ir.hubs[0].bkcc_column_expr == "tenant_id"
    assert [bk.name for bk in validated_ir.hubs[0].business_keys] == ["bkcc", "account_id"]
    assert validated_ir.hubs[0].hk_column.hash_expression.bkcc_column_expr == "tenant_id"
    assert validated_ir.hubs[0].source_mappings[0].bkcc_column_expr == "tenant_id"

    # 4. Compile 4-Zone SQL & execute in DuckDB with real CSV files
    bronze_map: dict[str, Any] = {
        "bronze_saas.tenant_accounts": accounts_csv,
        "bronze_saas.tenant_subscriptions": subs_csv,
    }
    bundle = compile_canonical_ir_to_4zone_sql(ir, bronze_tables=bronze_map)

    con = duckdb.connect(":memory:")
    try:
        con.execute(bundle.full_sql)
        hub_rows = con.execute(
            f"""
            SELECT hk_tenant_account, bkcc, account_id, rec_src
            FROM raw_vault.hub_tenant_account
            WHERE hk_tenant_account NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')
            ORDER BY bkcc, account_id
            """
        ).fetchall()
        sat_rows = con.execute(
            f"""
            SELECT hk_tenant_account, account_name, plan_tier
            FROM raw_vault.sat_tenant_account_core
            WHERE hk_tenant_account NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')
            ORDER BY account_name
            """
        ).fetchall()
    finally:
        con.close()

    expected_eu_hk = compute_dv2_length_prefixed_sha256(
        ["ACCT-1001"], is_business_key=True, bkcc_prefix="TENANT_EU"
    ).lower()
    expected_us_hk = compute_dv2_length_prefixed_sha256(
        ["ACCT-1001"], is_business_key=True, bkcc_prefix="TENANT_US"
    ).lower()
    expected_us_2_hk = compute_dv2_length_prefixed_sha256(
        ["ACCT-1002"], is_business_key=True, bkcc_prefix="TENANT_US"
    ).lower()

    assert hub_rows == [
        (expected_eu_hk, "TENANT_EU", "ACCT-1001", "SAAS_CORE"),
        (expected_us_hk, "TENANT_US", "ACCT-1001", "SAAS_CORE"),
        (expected_us_2_hk, "TENANT_US", "ACCT-1002", "SAAS_CORE"),
    ]
    assert sat_rows == [
        (expected_eu_hk, "Acme EU GmbH", "GROWTH"),
        (expected_us_hk, "Acme US Holdings", "ENTERPRISE"),
        (expected_us_2_hk, "Beta US Corp", "STARTER"),
    ]

    # 5. Full 4-Batch Adversarial Shadow Sandbox + 15 DV-DYN checks
    with DuckDbShadowSandbox() as sandbox:
        report = sandbox.run_full_verification(
            ir,
            bronze_tables=bronze_map,
            emit_telemetry=False,
        )
        assert report.passed
        assert report.batch_report.all_passed
        assert report.dynamic_check_report.all_passed
