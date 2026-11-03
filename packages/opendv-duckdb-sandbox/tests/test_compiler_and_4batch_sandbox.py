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
5. Telemetry integration: Emitting schema-valid `VIZ-GATE-08` (`WB-10`) `VaultObservabilityEvent`
   records via `VaultLedgerEmitter`.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from opendv_core.constants import GHOST_DTS, GHOST_REC_SRC, ZERO_KEY_ERROR, ZERO_KEY_UNKNOWN
from opendv_core.schema_registry import (
    get_examples_dir,
    validate_canonical_ir,
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
    compile_canonical_ir_to_4zone_sql,
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
from opendv_telemetry.ledger_emitter import VaultLedgerEmitter

# ============================================================================
# 1. Canonical IR Lowerer & Topological Load DAG Tests
# ============================================================================


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


# ============================================================================
# 2. Graph B Empirical Profiler & Schema Drift Detection Tests
# ============================================================================


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

        pq_metrics = profiler.profile_parquet(parquet_file)
        csv_metrics = profiler.profile_csv(csv_file)
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


# ============================================================================
# 3. 4-Zone SQL Compiler, 4 Adversarial Batches & DV-DYN-001..015 Tests
# ============================================================================


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
    assert "sal_investor_golden" in bundle.zone_0_sql

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


# ============================================================================
# 4. Bi-Temporal Query D.10 & Query D.10b Verification Tests
# ============================================================================


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
        assert len(d10_rows) == 2
        calpers = next(r for r in d10_rows if r.golden_lp_id == "LP-101")
        assert calpers.golden_bkcc == "GLOBAL"
        assert calpers.committed_amount == Decimal("150000000.00")
        assert calpers.reconciled_legacy_nav_usd == Decimal("112500000.00")
        assert calpers.side_letter_fee_discount_bps == 25
        assert calpers.has_mfn_protection is True

        pit_engine = PitQueryEngine(con)
        summary = pit_engine.to_summary_dict()
        assert summary["bitemporal_proof"]["verified"] is True
    finally:
        con.close()


# ============================================================================
# 5. High-Level DuckDbShadowSandbox & Observability Telemetry Tests
# ============================================================================


def test_duckdb_shadow_sandbox_full_verification_and_telemetry(tmp_path: Path) -> None:
    """Verify DuckDbShadowSandbox runs Batches 1-4, DV-DYN-001-015, and VIZ-GATE-08 emission."""
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
        assert validated_ev.viz_id == "VIZ-GATE-08"
        assert report.observability_event.viz_id == "VIZ-GATE-08"
        assert report.observability_event.workbench_view_id == "WB-10"
        assert report.observability_event.stage_id == "STAGE_06_AXI_DV_DYNAMIC_SANDBOX"

    persisted_events = emitter.read_all_events()
    assert len(persisted_events) == 1
    assert persisted_events[0]["event_id"] == "evt-pe-sandbox-viz-gate-08"


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
    """Verify `build_algorithm_a5_sha256_sql` generates null-safe length-prefixed SQL."""
    sql_expr = build_algorithm_a5_sha256_sql(
        ["lp_id"],
        is_business_key=True,
        bkcc_literal="GLOBAL",
    )
    con = duckdb.connect(":memory:")
    try:
        con.execute("CREATE TABLE t (lp_id VARCHAR); INSERT INTO t VALUES ('lp-101'), (NULL);")
        rows = con.execute(f"SELECT {sql_expr} FROM t").fetchall()
        assert rows[0][0] == "5d15eb10c8d7621d3167849c41c5f9cd9244fd79fb720cb17104d49e4b820ae4"
        assert rows[1][0] == ZERO_KEY_UNKNOWN
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
    bundle = compile_canonical_ir_to_4zone_sql(ir)

    with duckdb.connect(":memory:") as conn:
        conn.execute("CREATE MACRO SHA2(s, bits) AS sha256(s)")
        conn.execute("CREATE MACRO OCTET_LENGTH(s) AS strlen(s)")
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
            ["APEX-BUYOUT-IV"], is_business_key=True, bkcc_prefix=None
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
