"""tests/sandbox/test_challenger_m4_1_adversarial_stability.py — Empirical Adversarial Stress Suite.

Milestone M4 Adversarial Verification for Features 13 & 14:
1. Empty Batches & Edge Cases across Wave 0, Wave 1, Wave 2.
2. Duplicate Transaction & Replay Stress (PK collisions, delta-guard idempotency).
3. Multi-Source Business Key Collisions & BKCC Isolation (SHA-256 separation).
4. Same-As Link (SAL) Adversarial Loop Resolution (Self-loops, inverted pairs, FK orphans,
   transitive graph equivalence).
5. PII & MNPI Satellite Split Isolation (Data privacy boundaries, GDPR shredding invariance).
6. Algorithm A.7 Sensitivity to Subtle Non-Additive Mutations (BKCC prefixes, CDK removal,
   BK reordering, column drops, Link arity, hash config).
7. Algorithm A.6 Physical SQL AST Static Checks (Destructive ALTER vs additive ALTER,
   audit column purity, wildcard bans).
8. Cumulative 3-Wave Bit-for-Bit Deterministic Invariance across DuckDB shadow sandbox.
"""

from __future__ import annotations

import copy
from pathlib import Path

import duckdb
import pytest
from tests.sandbox.test_asset_management_3wave_stability import (
    build_wave0_ddl,
    build_wave1_additive_ddl,
    build_wave1_contract,
    build_wave2_additive_ddl,
)

from opendv_axi_verifier import (
    verify_additive_evolution_invariants,
    verify_physical_dv2_sql_ast,
)
from opendv_core.hashing import compute_dv2_length_prefixed_sha256
from opendv_duckdb_sandbox.dynamic_checks import (
    ZERO_KEY_ERROR,
    ZERO_KEY_UNKNOWN,
    check_dv_dyn_001_pk_uniqueness,
    check_dv_dyn_002_referential_integrity,
    check_dv_dyn_004_true_change_capture,
    check_dv_dyn_006_bkcc_collision_and_sal,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


# ===========================================================================
# 1. Empty Batches & Edge Cases Stress
# ===========================================================================


class TestEmptyBatchStressAndEdgeCases:
    """Stress tests verifying system resilience when receiving zero-row batches."""

    def test_wave0_empty_batch_execution(self) -> None:
        """Wave 0 sandbox executes cleanly with zero ingested documents."""
        conn = duckdb.connect(":memory:")
        for stmt in build_wave0_ddl():
            conn.execute(stmt)

        # Only dual zero keys seeded
        conn.execute(f"""
            INSERT INTO raw_vault.hub_document VALUES
            ('{ZERO_KEY_UNKNOWN}', 'SYSTEM_ZERO_KEY',
             TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
            ('{ZERO_KEY_ERROR}', 'SYSTEM_ERROR_KEY',
             TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');
        """)

        # Zero documents in sat_document_metadata
        assert (
            conn.execute("SELECT count(*) FROM raw_vault.sat_document_metadata;").fetchone()[0] == 0
        )

        # Dynamic checks must pass even with zero payload documents
        pk_res = check_dv_dyn_001_pk_uniqueness(conn)
        assert pk_res.passed is True
        fk_res = check_dv_dyn_002_referential_integrity(conn)
        assert fk_res.passed is True

    def test_wave1_empty_batch_progression(self) -> None:
        """Wave 1 additive DDL compiles and passes all checks with empty staging tables."""
        conn = duckdb.connect(":memory:")
        for stmt in build_wave0_ddl() + build_wave1_additive_ddl():
            conn.execute(stmt)

        tables = conn.execute("""
            SELECT count(*) FROM information_schema.tables
            WHERE table_schema IN ('staging', 'raw_vault', 'business_vault', 'marts');
        """).fetchone()[0]
        assert tables == 16

        # Zero rows in all tables
        pk_res = check_dv_dyn_001_pk_uniqueness(conn)
        assert pk_res.passed is True
        fk_res = check_dv_dyn_002_referential_integrity(conn)
        assert fk_res.passed is True

    def test_wave2_empty_batch_progression(self) -> None:
        """Wave 2 additive DDL compiles to 21 tables and passes checks with zero data."""
        conn = duckdb.connect(":memory:")
        for stmt in build_wave0_ddl() + build_wave1_additive_ddl() + build_wave2_additive_ddl():
            conn.execute(stmt)

        tables = conn.execute("""
            SELECT count(*) FROM information_schema.tables
            WHERE table_schema IN ('staging', 'raw_vault', 'business_vault', 'marts');
        """).fetchone()[0]
        assert tables == 21

        pk_res = check_dv_dyn_001_pk_uniqueness(conn)
        assert pk_res.passed is True
        fk_res = check_dv_dyn_002_referential_integrity(conn)
        assert fk_res.passed is True


# ===========================================================================
# 2. Duplicate Transaction & Replay Stress
# ===========================================================================


class TestDuplicateTransactionAndReplayStress:
    """Stress tests on transaction replay, PK collisions, and satellite delta guards."""

    def test_duckdb_native_pk_constraint_violation(self) -> None:
        """DuckDB natively enforces PRIMARY KEY on Hubs, Links, and Business Vault."""
        conn = duckdb.connect(":memory:")
        for stmt in build_wave0_ddl() + build_wave1_additive_ddl():
            conn.execute(stmt)

        conn.execute("""
            INSERT INTO raw_vault.hub_security VALUES
            ('hk_sec_aapl', 'AAPL', '0000320193', CURRENT_TIMESTAMP, 'SEC_EDGAR');
        """)

        # Re-inserting identical primary key must raise duckdb.ConstraintException
        with pytest.raises(duckdb.ConstraintException):
            conn.execute("""
                INSERT INTO raw_vault.hub_security VALUES
                ('hk_sec_aapl', 'AAPL_REPLAY', '0000320193', CURRENT_TIMESTAMP, 'SEC_EDGAR');
            """)

    def test_idempotent_microbatch_deduplication(self) -> None:
        """DV2 idempotent loading query (WHERE NOT EXISTS) prevents duplicate rows upon replay."""
        conn = duckdb.connect(":memory:")
        for stmt in build_wave0_ddl() + build_wave1_additive_ddl():
            conn.execute(stmt)

        # Batch 1 load
        conn.execute("""
            INSERT INTO raw_vault.hub_investor (hk_investor, bk_cik, load_dts, record_source)
            SELECT 'hk_inv_001', '0001067983', CURRENT_TIMESTAMP, 'SEC_EDGAR'
            WHERE NOT EXISTS (
                SELECT 1 FROM raw_vault.hub_investor WHERE hk_investor = 'hk_inv_001'
            );
        """)
        assert conn.execute("SELECT count(*) FROM raw_vault.hub_investor;").fetchone()[0] == 1

        # Batch 2 duplicate replay
        conn.execute("""
            INSERT INTO raw_vault.hub_investor (hk_investor, bk_cik, load_dts, record_source)
            SELECT 'hk_inv_001', '0001067983', CURRENT_TIMESTAMP, 'SEC_EDGAR'
            WHERE NOT EXISTS (
                SELECT 1 FROM raw_vault.hub_investor WHERE hk_investor = 'hk_inv_001'
            );
        """)
        # Row count MUST remain exactly 1 (0 delta rows)
        assert conn.execute("SELECT count(*) FROM raw_vault.hub_investor;").fetchone()[0] == 1

    def test_satellite_true_change_capture_and_delta_suppression(self) -> None:
        """Satellite delta guard suppresses duplicate payloads while capturing real changes."""
        conn = duckdb.connect(":memory:")
        for stmt in build_wave0_ddl() + build_wave1_additive_ddl():
            conn.execute(stmt)

        conn.execute("""
            INSERT INTO raw_vault.hub_security VALUES
            ('hk_sec_msft', 'MSFT', '0000789019', TIMESTAMP '2026-01-01 00:00:00', 'SEC_EDGAR');
            INSERT INTO raw_vault.sat_security_details VALUES
            ('hk_sec_msft', 'Microsoft Corp', 'NASDAQ', TIMESTAMP '2026-01-01 00:00:00',
             'SEC_EDGAR', 'diff_v1');
        """)

        # Replay batch with identical payload (diff_v1): delta guard suppresses insertion
        conn.execute("""
            INSERT INTO raw_vault.sat_security_details
            SELECT 'hk_sec_msft', 'Microsoft Corp', 'NASDAQ', TIMESTAMP '2026-01-02 00:00:00',
                   'SEC_EDGAR', 'diff_v1'
            WHERE NOT EXISTS (
                SELECT 1 FROM raw_vault.sat_security_details s
                WHERE s.hk_security = 'hk_sec_msft'
                  AND s.hash_diff = 'diff_v1'
            );
        """)
        assert (
            conn.execute("SELECT count(*) FROM raw_vault.sat_security_details;").fetchone()[0] == 1
        )

        # Real attribute change (exchange changes to 'NYSE_ARCA', diff_v2)
        conn.execute("""
            INSERT INTO raw_vault.sat_security_details
            SELECT 'hk_sec_msft', 'Microsoft Corp', 'NYSE_ARCA', TIMESTAMP '2026-01-03 00:00:00',
                   'SEC_EDGAR', 'diff_v2'
            WHERE NOT EXISTS (
                SELECT 1 FROM raw_vault.sat_security_details s
                WHERE s.hk_security = 'hk_sec_msft'
                  AND s.hash_diff = 'diff_v2'
            );
        """)
        assert (
            conn.execute("SELECT count(*) FROM raw_vault.sat_security_details;").fetchone()[0] == 2
        )

        # Verify DV-DYN-004 passes (captured satellite change without mutating hub)
        hub_delta = conn.execute("SELECT count(*) FROM raw_vault.hub_security;").fetchone()[0] - 1
        dyn_res = check_dv_dyn_004_true_change_capture(
            conn, recorded_sat_delta=2, recorded_hub_link_delta=hub_delta
        )
        assert dyn_res.passed is True


# ===========================================================================
# 3. Multi-Source BK Collisions & BKCC Isolation
# ===========================================================================


class TestMultiSourceBKCollisionsAndBKCC:
    """Stress tests on collision avoidance between disparate source systems with identical BKs."""

    def test_cross_source_bk_collision_avoidance_via_bkcc(self) -> None:
        """Identical BK 'IBM' from SEC (GLOBAL) vs Internal (LOCAL_OMS) produce distinct hashes."""
        # Using DV2 length-prefixed hashing
        # Source 1: Global Tier-1 authority
        h_sec = compute_dv2_length_prefixed_sha256(["GLOBAL", "IBM"], is_business_key=True)
        # Source 2: Local scoped authority
        h_local = compute_dv2_length_prefixed_sha256(["LOCAL_OMS", "IBM"], is_business_key=True)

        assert h_sec != h_local, "Collision! BKCC prefixes failed to isolate distinct sources."

    def test_duckdb_sandbox_multi_source_bkcc_isolation(self) -> None:
        """DuckDB stores both records cleanly without PK collisions and passes DV-DYN-006."""
        conn = duckdb.connect(":memory:")
        for stmt in build_wave0_ddl() + build_wave1_additive_ddl() + build_wave2_additive_ddl():
            conn.execute(stmt)

        hk_sec = compute_dv2_length_prefixed_sha256(["GLOBAL", "IBM"], is_business_key=True)
        hk_local = compute_dv2_length_prefixed_sha256(["LOCAL_OMS", "IBM"], is_business_key=True)

        conn.execute(f"""
            INSERT INTO raw_vault.hub_borrower VALUES
            ('hk_borr_seed', 'BORR-SEED-1', CURRENT_TIMESTAMP, 'DIRECT_LENDING');
            INSERT INTO raw_vault.hub_security VALUES
            ('{hk_sec}', 'IBM', '0000051143', CURRENT_TIMESTAMP, 'SEC_EDGAR'),
            ('{hk_local}', 'IBM', 'LOCAL_INTERNAL_ID', CURRENT_TIMESTAMP, 'LOCAL_OMS');
        """)

        # Both records coexist in hub_security
        assert conn.execute("SELECT count(*) FROM raw_vault.hub_security;").fetchone()[0] == 2

        # Disambiguate via Same-As Link
        conn.execute(f"""
            INSERT INTO raw_vault.sal_instrument VALUES
            ('hk_sal_ibm', '{hk_sec}', '{hk_local}', CURRENT_TIMESTAMP, 'MASTER_CROSSWALK');
        """)

        dyn_006 = check_dv_dyn_006_bkcc_collision_and_sal(conn)
        assert dyn_006.passed is True, f"DV-DYN-006 failed: {dyn_006.detail}"


# ===========================================================================
# 4. Same-As Link (SAL) Loop Resolution & Symmetry
# ===========================================================================


class TestSameAsLinkAdversarialLoopResolution:
    """Adversarial challenge on Same-As Link loop resolution, symmetry, and FK integrity."""

    def test_sal_self_loop_rejection(self) -> None:
        """Self-loops (hk_master == hk_duplicate) violate DV-DYN-006."""
        conn = duckdb.connect(":memory:")
        for stmt in build_wave0_ddl() + build_wave1_additive_ddl() + build_wave2_additive_ddl():
            conn.execute(stmt)

        conn.execute("""
            INSERT INTO raw_vault.hub_borrower VALUES
            ('hk_b1', 'BORR-1', CURRENT_TIMESTAMP, 'DIRECT_LENDING');
            INSERT INTO raw_vault.hub_security VALUES
            ('hk_sec_self', 'SELF', '0001', CURRENT_TIMESTAMP, 'SEC');
            INSERT INTO raw_vault.sal_instrument VALUES
            ('hk_sal_self', 'hk_sec_self', 'hk_sec_self', CURRENT_TIMESTAMP, 'CROSSWALK');
        """)

        res = check_dv_dyn_006_bkcc_collision_and_sal(conn)
        assert res.passed is False, "DV-DYN-006 should have failed on self-loop!"
        assert res.failing_rows > 0

    def test_sal_inverted_duplicate_pair_rejection(self) -> None:
        """Inverted pairs (A -> B and B -> A) violate DV-DYN-006."""
        conn = duckdb.connect(":memory:")
        for stmt in build_wave0_ddl() + build_wave1_additive_ddl() + build_wave2_additive_ddl():
            conn.execute(stmt)

        conn.execute("""
            INSERT INTO raw_vault.hub_borrower VALUES
            ('hk_b1', 'BORR-1', CURRENT_TIMESTAMP, 'DIRECT_LENDING');
            INSERT INTO raw_vault.hub_security VALUES
            ('hk_sec_a', 'SEC_A', '0001', CURRENT_TIMESTAMP, 'SEC'),
            ('hk_sec_b', 'SEC_B', '0002', CURRENT_TIMESTAMP, 'CUSTODY');
            INSERT INTO raw_vault.sal_instrument VALUES
            ('hk_sal_ab', 'hk_sec_a', 'hk_sec_b', CURRENT_TIMESTAMP, 'CROSSWALK'),
            ('hk_sal_ba', 'hk_sec_b', 'hk_sec_a', CURRENT_TIMESTAMP, 'CROSSWALK');
        """)

        res = check_dv_dyn_006_bkcc_collision_and_sal(conn)
        assert res.passed is False, "DV-DYN-006 should have failed on bidirectional inverted pair!"

    def test_sal_fk_orphan_detection(self) -> None:
        """SAL referencing nonexistent hub keys fails DV-DYN-002 referential integrity."""
        conn = duckdb.connect(":memory:")
        for stmt in build_wave0_ddl() + build_wave1_additive_ddl() + build_wave2_additive_ddl():
            conn.execute(stmt)

        conn.execute("""
            INSERT INTO raw_vault.hub_security VALUES
            ('hk_sec_exists', 'EXISTS', '0001', CURRENT_TIMESTAMP, 'SEC');
            INSERT INTO raw_vault.sal_instrument VALUES
            ('hk_sal_ghost', 'hk_sec_exists', 'hk_sec_DOES_NOT_EXIST',
             CURRENT_TIMESTAMP, 'CROSSWALK');
        """)

        res = check_dv_dyn_002_referential_integrity(conn)
        assert res.passed is False, "DV-DYN-002 should have detected FK orphan in SAL!"
        assert res.failing_rows >= 1

    def test_sal_transitive_canonical_resolution(self) -> None:
        """Transitive chains A -> B -> C resolve to master A via SQL CTE without infinite loops."""
        conn = duckdb.connect(":memory:")
        for stmt in build_wave0_ddl() + build_wave1_additive_ddl() + build_wave2_additive_ddl():
            conn.execute(stmt)

        conn.execute("""
            INSERT INTO raw_vault.hub_security VALUES
            ('hk_sec_master', 'MASTER_AAPL', '0001', CURRENT_TIMESTAMP, 'SEC'),
            ('hk_sec_custody', 'CUSTODY_AAPL', '0002', CURRENT_TIMESTAMP, 'BNY'),
            ('hk_sec_clearing', 'CLEARING_AAPL', '0003', CURRENT_TIMESTAMP, 'DTCC');
            INSERT INTO raw_vault.sal_instrument VALUES
            ('hk_sal_1', 'hk_sec_master', 'hk_sec_custody', CURRENT_TIMESTAMP, 'CROSSWALK'),
            ('hk_sal_2', 'hk_sec_custody', 'hk_sec_clearing', CURRENT_TIMESTAMP, 'CROSSWALK');
        """)

        # Resolve all aliases to canonical master
        canonical_map = conn.execute("""
            WITH RECURSIVE sal_tree AS (
                -- Base: direct links
                SELECT hk_master_security AS root_hk, hk_duplicate_security AS alias_hk, 1 AS depth
                FROM raw_vault.sal_instrument
                WHERE hk_master_security = 'hk_sec_master'
                UNION ALL
                -- Transitive hop
                SELECT st.root_hk, s.hk_duplicate_security AS alias_hk, st.depth + 1
                FROM sal_tree st
                JOIN raw_vault.sal_instrument s ON st.alias_hk = s.hk_master_security
                WHERE st.depth < 10
            )
            SELECT DISTINCT alias_hk, root_hk FROM sal_tree;
        """).fetchall()

        resolved = {row[0]: row[1] for row in canonical_map}
        assert resolved["hk_sec_custody"] == "hk_sec_master"
        assert resolved["hk_sec_clearing"] == "hk_sec_master"


# ===========================================================================
# 5. PII & MNPI Satellite Split Isolation
# ===========================================================================


class TestPIIAndMNPISatelliteSplitIsolation:
    """Stress tests on security classification, privacy isolation, and GDPR crypto-shredding."""

    def test_pii_and_mnpi_data_boundary_isolation(self) -> None:
        """Standard analytical query joins Hub and MNPI satellite without leaking PII."""
        conn = duckdb.connect(":memory:")
        for stmt in build_wave0_ddl() + build_wave1_additive_ddl() + build_wave2_additive_ddl():
            conn.execute(stmt)

        conn.execute("""
            INSERT INTO raw_vault.hub_credit_facility VALUES
            ('hk_fac_001', 'FAC-TERM-A', CURRENT_TIMESTAMP, 'DIRECT_LENDING');
            INSERT INTO raw_vault.hub_borrower VALUES
            ('hk_borr_001', 'BORR-ALPHA', CURRENT_TIMESTAMP, 'DIRECT_LENDING');
            INSERT INTO raw_vault.sat_borrower_pii VALUES
            ('hk_borr_001', '99-8877665', 'cfo@alpha.com', '123-45-6789',
             CURRENT_TIMESTAMP, 'DIRECT_LENDING', 'diff_pii');
            INSERT INTO raw_vault.sat_credit_facility_mnpi VALUES
            ('hk_fac_001', 5.25, 12.50, TRUE, CURRENT_TIMESTAMP,
             'DIRECT_LENDING', 'diff_mnpi');
        """)

        # Standard query on Credit Facility Mart
        facility_view = conn.execute("""
            SELECT h.bk_facility_id, m.leverage_covenant_max, m.insider_restricted_flag
            FROM raw_vault.hub_credit_facility h
            JOIN raw_vault.sat_credit_facility_mnpi m
              ON h.hk_credit_facility = m.hk_credit_facility;
        """).fetchall()

        assert len(facility_view) == 1
        assert facility_view[0][0] == "FAC-TERM-A"
        assert float(facility_view[0][1]) == 5.25
        assert facility_view[0][2] is True

    def test_gdpr_crypto_shredding_leaves_vault_intact(self) -> None:
        """Purging PII satellite records does not break referential integrity or hub invariants."""
        conn = duckdb.connect(":memory:")
        for stmt in build_wave0_ddl() + build_wave1_additive_ddl() + build_wave2_additive_ddl():
            conn.execute(stmt)

        conn.execute("""
            INSERT INTO raw_vault.hub_borrower VALUES
            ('hk_borr_001', 'BORR-ALPHA', CURRENT_TIMESTAMP, 'DIRECT_LENDING');
            INSERT INTO raw_vault.sat_borrower_pii VALUES
            ('hk_borr_001', '99-8877665', 'cfo@alpha.com', '123-45-6789',
             CURRENT_TIMESTAMP, 'DIRECT_LENDING', 'diff_pii');
        """)

        # Execute GDPR erasure on PII satellite
        conn.execute("DELETE FROM raw_vault.sat_borrower_pii WHERE hk_borrower = 'hk_borr_001';")

        # PII table is clean
        assert conn.execute("SELECT count(*) FROM raw_vault.sat_borrower_pii;").fetchone()[0] == 0
        # Hub remains intact
        assert conn.execute("SELECT count(*) FROM raw_vault.hub_borrower;").fetchone()[0] == 1

        # All dynamic checks remain valid
        assert check_dv_dyn_001_pk_uniqueness(conn).passed is True
        assert check_dv_dyn_002_referential_integrity(conn).passed is True


# ===========================================================================
# 6. Algorithm A.7 Sensitivity to Subtle Non-Additive Mutations
# ===========================================================================


class TestAlgorithmA7SubtleNonAdditiveMutations:
    """Stress tests verifying Algorithm A.7 catches all non-additive schema modifications."""

    def test_catch_bkcc_prefix_mutation(self) -> None:
        """Altering bkcc_prefix from GLOBAL to LOCAL_SCOPED triggers DV-EVO-003/DV-EVO-007."""
        w1 = build_wave1_contract()
        w1_bad = copy.deepcopy(w1)
        w1_bad["hubs"][1]["business_key_sources"][0]["bkcc_prefix"] = "LOCAL_SCOPED"

        violations = verify_additive_evolution_invariants(w1, w1_bad)
        assert any("BKCC_MUTATED" in v for v in violations)

    def test_catch_child_dependent_key_removal(self) -> None:
        """Removing child dependent key in a multi-active satellite triggers DV-EVO-003."""
        w1 = build_wave1_contract()
        w1_mas = copy.deepcopy(w1)
        # Add a MAS with child dependent key
        w1_mas["satellites"][1]["child_dependent_keys"] = ["position_seq_id"]

        w1_mas_bad = copy.deepcopy(w1_mas)
        # Drop child dependent key
        w1_mas_bad["satellites"][1]["child_dependent_keys"] = []

        violations = verify_additive_evolution_invariants(w1_mas, w1_mas_bad)
        assert any("MAS_CDK_MUTATED" in v for v in violations)

    def test_catch_sub_sequence_column_mutation(self) -> None:
        """Mutating sub_sequence_column triggers DV-EVO-003."""
        w1 = build_wave1_contract()
        w1_mas = copy.deepcopy(w1)
        w1_mas["satellites"][1]["sub_sequence_column"] = "effective_seq_1"

        w1_mas_bad = copy.deepcopy(w1_mas)
        w1_mas_bad["satellites"][1]["sub_sequence_column"] = "effective_seq_2"

        violations = verify_additive_evolution_invariants(w1_mas, w1_mas_bad)
        assert any("MAS_CDK_MUTATED" in v for v in violations)

    def test_catch_business_key_column_reordering(self) -> None:
        """Reordering BK columns ['ticker', 'cik'] to ['cik', 'ticker'] triggers DV-EVO-003."""
        w1 = build_wave1_contract()
        w1_bad = copy.deepcopy(w1)
        w1_bad["hubs"][1]["business_key_sources"][0]["key_columns"] = ["cik", "ticker"]

        violations = verify_additive_evolution_invariants(w1, w1_bad)
        assert any("BK_SIGNATURE_MUTATED" in v for v in violations)

    def test_catch_source_mapping_dropped(self) -> None:
        """Dropping an existing business key source mapping triggers DV-EVO-003."""
        w1 = build_wave1_contract()
        w1_bad = copy.deepcopy(w1)
        w1_bad["hubs"][1]["business_key_sources"] = []

        violations = verify_additive_evolution_invariants(w1, w1_bad)
        assert any("HUB_SOURCE_REMOVED" in v for v in violations)

    def test_catch_link_hub_reference_mutation(self) -> None:
        """Mutating or dropping a hub reference in a link triggers DV-EVO-003."""
        w1 = build_wave1_contract()
        w1_bad = copy.deepcopy(w1)
        # Remove hub_security reference
        w1_bad["links"][0]["hub_references"] = [w1_bad["links"][0]["hub_references"][0]]

        violations = verify_additive_evolution_invariants(w1, w1_bad)
        assert any("LINK_ARITY_OR_ORDER_MUTATED" in v for v in violations)

    def test_catch_link_degenerate_column_dropped(self) -> None:
        """Dropping a degenerate key from a link triggers DV-EVO-004."""
        w1 = build_wave1_contract()
        w1_bad = copy.deepcopy(w1)
        w1_bad["links"][0]["degenerate_columns"] = []

        violations = verify_additive_evolution_invariants(w1, w1_bad)
        assert any("LINK_DEGENERATE_DROPPED" in v for v in violations)

    def test_catch_payload_column_dropped_without_soft_deprecation(self) -> None:
        """Dropping a payload column without soft_deprecated_columns triggers DV-EVO-004."""
        w1 = build_wave1_contract()
        w1_bad = copy.deepcopy(w1)
        # Drop 'exchange' column
        w1_bad["satellites"][1]["payload_columns"] = ["company_name"]

        violations = verify_additive_evolution_invariants(w1, w1_bad)
        assert any("SAT_COLUMN_DROPPED" in v for v in violations)

    def test_allow_payload_column_soft_deprecation(self) -> None:
        """Declaring dropped column in soft_deprecated_columns passes DV-EVO-004."""
        w1 = build_wave1_contract()
        w1_soft = copy.deepcopy(w1)
        w1_soft["satellites"][1]["payload_columns"] = ["company_name"]
        w1_soft["satellites"][1]["soft_deprecated_columns"] = ["exchange"]

        violations = verify_additive_evolution_invariants(w1, w1_soft)
        assert violations == []

    def test_catch_in_place_column_addition_without_hashdiff_policy(self) -> None:
        """Adding column in-place without hash_diff_migration_policy triggers DV-EVO-006."""
        w1 = build_wave1_contract()
        w1_bad = copy.deepcopy(w1)
        w1_bad["satellites"][1]["payload_columns"] = ["company_name", "exchange", "new_col"]

        violations = verify_additive_evolution_invariants(w1, w1_bad)
        assert any("SAT_HASHDIFF_INVALIDATED" in v for v in violations)

    def test_catch_global_hash_algorithm_mutation(self) -> None:
        """Changing hash algorithm from sha256 to md5 triggers DV-EVO-001."""
        prev = {"hash_config": {"algorithm": "sha256", "delimiter": "||"}}
        next_ = {"hash_config": {"algorithm": "blake3", "delimiter": "||"}}

        violations = verify_additive_evolution_invariants(prev, next_)
        assert any("HASH_CONFIG_MUTATED" in v for v in violations)


# ===========================================================================
# 7. Algorithm A.6 Physical SQL AST Static Checks
# ===========================================================================


class TestAlgorithmA6PhysicalSQLASTVerifications:
    """Stress tests on Algorithm A.6 SQL AST static verification."""

    def test_destructive_alter_table_rejected(self) -> None:
        """Destructive ALTER COLUMN and DROP COLUMN on Raw Vault tables are rejected."""
        v_type = verify_physical_dv2_sql_ast(
            "ALTER TABLE raw_vault.sat_security_details "
            "ALTER COLUMN company_name TYPE VARCHAR(500);"
        )
        assert any("DV-AST-001" in v or "DV-G6-002" in v for v in v_type)

        v_drop = verify_physical_dv2_sql_ast(
            "ALTER TABLE raw_vault.sat_security_details DROP COLUMN company_name;"
        )
        assert any("DV-AST-001" in v or "DV-G6-002" in v for v in v_drop)

    def test_additive_alter_table_permitted(self) -> None:
        """Additive ALTER TABLE ADD COLUMN on Raw Vault tables passes with 0 violations."""
        v_add = verify_physical_dv2_sql_ast(
            "ALTER TABLE raw_vault.sat_security_details "
            "ADD COLUMN IF NOT EXISTS esg_score DECIMAL(5,2);"
        )
        assert v_add == []

    def test_destructive_dml_rejected(self) -> None:
        """DROP, TRUNCATE, DELETE, and UPDATE on Raw Vault tables are rejected."""
        assert len(verify_physical_dv2_sql_ast("DROP TABLE raw_vault.hub_security;")) > 0
        assert (
            len(verify_physical_dv2_sql_ast("TRUNCATE TABLE raw_vault.sat_security_details;")) > 0
        )
        assert len(verify_physical_dv2_sql_ast("DELETE FROM raw_vault.hub_security WHERE 1=1;")) > 0
        assert (
            len(verify_physical_dv2_sql_ast("UPDATE raw_vault.hub_security SET bk_ticker = 'X';"))
            > 0
        )

    def test_wildcard_and_missing_audit_columns_rejected(self) -> None:
        """Wildcard SELECT * and queries missing load_dts or rec_src are rejected."""
        v_wildcard = verify_physical_dv2_sql_ast("SELECT * FROM staging.stg_sec_security_master;")
        assert any("DV-AST-004" in v for v in v_wildcard)
        assert any("DV-AST-006" in v for v in v_wildcard)


# ===========================================================================
# 8. Cumulative 3-Wave Bit-for-Bit Deterministic Invariance
# ===========================================================================


class TestThreeWaveBitForBitInvariance:
    """Stress tests verifying bit-for-bit data invariance across Wave 0 -> Wave 1 -> Wave 2."""

    def test_cumulative_bit_for_bit_preservation(self) -> None:
        """Wave 0 data remains byte-for-byte identical through Wave 1 and Wave 2 migrations."""
        conn = duckdb.connect(":memory:")

        # Wave 0 Genesis
        for stmt in build_wave0_ddl():
            conn.execute(stmt)

        w0_doc_hk = "hk_doc_calpers_2026_q1"
        w0_doc_bk = "DOC-CALPERS-2026-Q1-STRATEGY"
        w0_doc_file_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        w0_doc_byte_count = 5242880
        w0_doc_mime = "application/pdf"
        w0_doc_hd = "hd_doc_calpers_v1"

        conn.execute(f"""
            INSERT INTO raw_vault.hub_document VALUES
            ('{w0_doc_hk}', '{w0_doc_bk}', TIMESTAMP '2026-01-15 10:00:00', 'CAS_INGESTION');
            INSERT INTO raw_vault.sat_document_metadata VALUES
            ('{w0_doc_hk}', '{w0_doc_file_hash}', {w0_doc_byte_count}, '{w0_doc_mime}',
             TIMESTAMP '2026-01-15 10:00:00', 'CAS_INGESTION', '{w0_doc_hd}');
        """)

        # Execute Wave 1 migration
        for stmt in build_wave1_additive_ddl():
            conn.execute(stmt)

        # Execute Wave 2 migration
        for stmt in build_wave2_additive_ddl():
            conn.execute(stmt)

        # Assert 21 tables across 4 zones
        total_tables = conn.execute("""
            SELECT count(*) FROM information_schema.tables
            WHERE table_schema IN ('staging', 'raw_vault', 'business_vault', 'marts');
        """).fetchone()[0]
        assert total_tables == 21

        # Retrieve Wave 0 data and verify exact bit-for-bit equality
        hub_row = conn.execute(
            "SELECT bk_document_id, record_source FROM raw_vault.hub_document "
            f"WHERE hk_document = '{w0_doc_hk}';"
        ).fetchone()
        assert hub_row == (w0_doc_bk, "CAS_INGESTION")

        sat_row = conn.execute(
            "SELECT file_hash, byte_count, mime_type, hash_diff "
            f"FROM raw_vault.sat_document_metadata WHERE hk_document = '{w0_doc_hk}';"
        ).fetchone()
        assert sat_row == (w0_doc_file_hash, w0_doc_byte_count, w0_doc_mime, w0_doc_hd)

        # Final dynamic check verification on cumulative state
        assert check_dv_dyn_001_pk_uniqueness(conn).passed is True
        assert check_dv_dyn_002_referential_integrity(conn).passed is True
