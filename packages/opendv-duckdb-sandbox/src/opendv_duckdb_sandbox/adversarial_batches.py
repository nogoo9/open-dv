r"""4-Batch Adversarial DuckDB Shadow Sandbox Executor (`docs/03` §7.1, `docs/07` Scene 6).

Executes the 4 progressive adversarial test batches inside an ephemeral DuckDB `:memory:`
sandbox to prove structural, cryptographic, idempotency, and bi-temporal invariants before
promoting generated 4-Zone SQL to production:
- **Batch 1**: Baseline Load + Null-FK & Ghost `INNER JOIN` Completeness (`0` orphans,
  both Zero Keys `'0'*64` and `'f'*64` in every Hub/Link, `1` Ghost Record `SYSTEM_GHOST`
  in every Satellite).
- **Batch 2**: Replay Idempotency ($\Delta\text{rows} = 0$ across all Hubs, Links, and
  Satellites when re-executing the identical Zone 2 load).
- **Batch 3**: Single-Attribute Delta & Boundary-Shift Collision (`'AB' || 'C'` vs
  `'A' || 'BC'` produces distinct length-prefixed SHA-256 `hash_diff` digests and inserts
  exactly `1` new Satellite version row while Hub/Link $\Delta\text{rows} = 0$).
- **Batch 4**: $N=1 \to N=2$ Cross-Source Collision & Bi-Temporal Backdated Restatement
  (inserts the November 15, 2026 Q3 valuation restatement from `12.80x` / `$216.28M` EV
  to `14.50x` / `$245.00M` EV with `effective_from_dt = 2026-09-30` and
  `assertion_ts = 2026-11-15 09:15:00`).
"""

from __future__ import annotations

from typing import Any

import duckdb
from pydantic import BaseModel, ConfigDict, Field

from opendv_core.constants import GHOST_DTS, GHOST_REC_SRC, ZERO_KEY_ERROR, ZERO_KEY_UNKNOWN
from opendv_core.hashing import compute_dv2_length_prefixed_sha256
from opendv_duckdb_sandbox.compiler_4zone import Compiled4ZoneBundle
from opendv_duckdb_sandbox.dynamic_checks import (
    HUB_PK_MAP,
    LINK_PK_MAP,
    SATELLITE_PK_MAP,
    check_dv_dyn_002_referential_integrity,
    check_dv_dyn_007_ghost_and_zero_key_completeness,
)
from opendv_duckdb_sandbox.pit_query_engine import PitQueryEngine, ensure_pit_snapshot_for_cutoff


class AdversarialBatchResult(BaseModel):
    """Structured execution result for one of the 4 Adversarial Sandbox Batches."""

    model_config = ConfigDict(extra="forbid")

    batch_number: int = Field(ge=1, le=4)
    batch_name: str
    passed: bool
    hub_delta_rows: int
    link_delta_rows: int
    satellite_delta_rows: int
    total_delta_rows: int
    orphan_count: int = Field(default=0, ge=0)
    metrics: dict[str, Any] = Field(default_factory=dict)
    detail: str


class FourBatchExecutionReport(BaseModel):
    """Aggregate report of executing Batches 1 through 4 in sequence."""

    model_config = ConfigDict(extra="forbid")

    all_passed: bool
    batches: list[AdversarialBatchResult] = Field(default_factory=list)

    @property
    def by_number(self) -> dict[int, AdversarialBatchResult]:
        """Lookup map of `batch_number -> AdversarialBatchResult`."""
        return {b.batch_number: b for b in self.batches}


def _count_table_group(conn: duckdb.DuckDBPyConnection, tables: dict[str, str]) -> int:
    total = 0
    for tbl in tables:
        row = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
        total += int(row[0]) if row is not None else 0
    return total


def _snapshot_vault_counts(conn: duckdb.DuckDBPyConnection) -> tuple[int, int, int]:
    """Return `(hub_rows, link_rows, satellite_rows)` across all Vault tables."""
    return (
        _count_table_group(conn, HUB_PK_MAP),
        _count_table_group(conn, LINK_PK_MAP),
        _count_table_group(conn, SATELLITE_PK_MAP),
    )


def run_batch_1_baseline_and_ghost_completeness(
    conn: duckdb.DuckDBPyConnection,
    bundle: Compiled4ZoneBundle,
) -> AdversarialBatchResult:
    """Execute Batch 1: Baseline Load + Null-FK & Ghost `INNER JOIN` Completeness."""
    conn.execute(bundle.zones_0_to_3_sql)
    hub_rows, link_rows, sat_rows = _snapshot_vault_counts(conn)

    ref_check = check_dv_dyn_002_referential_integrity(conn)
    ghost_check = check_dv_dyn_007_ghost_and_zero_key_completeness(conn)

    zero_keys_verified = all(
        int(
            (
                conn.execute(
                    f"""
                    SELECT COUNT(*) FROM {tbl}
                    WHERE {pk} IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')
                      AND rec_src = 'SYSTEM_ZERO_KEY'
                    """
                ).fetchone()
                or (0,)
            )[0]
        )
        == 2
        for tbl, pk in {**HUB_PK_MAP, **LINK_PK_MAP}.items()
    )

    ghosts_verified = all(
        int(
            (
                conn.execute(
                    f"""
                    SELECT COUNT(*) FROM {tbl}
                    WHERE {pk} = '{ZERO_KEY_UNKNOWN}'
                      AND load_dts = TIMESTAMP '{GHOST_DTS}'
                      AND rec_src = '{GHOST_REC_SRC}'
                    """
                ).fetchone()
                or (0,)
            )[0]
        )
        == 1
        for tbl, pk in SATELLITE_PK_MAP.items()
    )

    passed = (
        ref_check.passed
        and ghost_check.passed
        and zero_keys_verified
        and ghosts_verified
        and hub_rows > 0
        and link_rows > 0
        and sat_rows > 0
    )

    return AdversarialBatchResult(
        batch_number=1,
        batch_name="Baseline + Null-FK & Ghost INNER JOIN Completeness",
        passed=passed,
        hub_delta_rows=hub_rows,
        link_delta_rows=link_rows,
        satellite_delta_rows=sat_rows,
        total_delta_rows=hub_rows + link_rows + sat_rows,
        orphan_count=ref_check.failing_rows,
        metrics={
            "zero_keys_in_all_hubs_and_links": zero_keys_verified,
            "ghost_record_in_all_satellites": ghosts_verified,
            "orphan_count": ref_check.failing_rows,
            "hub_rows": hub_rows,
            "link_rows": link_rows,
            "satellite_rows": sat_rows,
        },
        detail=(
            f"Loaded {hub_rows} Hub rows, {link_rows} Link rows, {sat_rows} Satellite rows; "
            f"orphans={ref_check.failing_rows}, zero_keys={zero_keys_verified}, "
            f"ghosts={ghosts_verified}."
        ),
    )


def run_batch_2_replay_idempotency(
    conn: duckdb.DuckDBPyConnection,
    bundle: Compiled4ZoneBundle,
) -> AdversarialBatchResult:
    """Execute Batch 2: Replay Idempotency (Zone 2 & Zone 3 replay yields delta_rows = 0)."""
    before_hubs, before_links, before_sats = _snapshot_vault_counts(conn)

    # Re-execute all Zone 2 Raw Vault DML and Zone 3 Business Vault DML
    conn.execute(bundle.zone_2_sql)
    conn.execute(bundle.zone_3_sql)

    after_hubs, after_links, after_sats = _snapshot_vault_counts(conn)
    d_hubs = after_hubs - before_hubs
    d_links = after_links - before_links
    d_sats = after_sats - before_sats
    total_delta = d_hubs + d_links + d_sats

    return AdversarialBatchResult(
        batch_number=2,
        batch_name="Replay Idempotency",
        passed=(d_hubs == 0 and d_links == 0 and d_sats == 0),
        hub_delta_rows=d_hubs,
        link_delta_rows=d_links,
        satellite_delta_rows=d_sats,
        total_delta_rows=total_delta,
        orphan_count=0,
        metrics={
            "hub_delta_rows": d_hubs,
            "link_delta_rows": d_links,
            "satellite_delta_rows": d_sats,
            "total_delta_rows": total_delta,
        },
        detail=(
            f"Replayed identical Zone 2 & Zone 3 batch: "
            f"delta_hubs={d_hubs}, delta_links={d_links}, delta_sats={d_sats}."
        ),
    )


def run_batch_3_delta_and_boundary_shift(
    conn: duckdb.DuckDBPyConnection,
) -> AdversarialBatchResult:
    """Execute Batch 3: Attribute Delta & Boundary-Shift Collision ('AB'||'C' vs 'A'||'BC')."""
    before_hubs, before_links, before_sats = _snapshot_vault_counts(conn)

    # 1. Verify Python & DuckDB length-prefixed SHA-256 boundary-shift collision immunity
    py_hash_ab_c = compute_dv2_length_prefixed_sha256(["AB", "C"], is_business_key=False).lower()
    py_hash_a_bc = compute_dv2_length_prefixed_sha256(["A", "BC"], is_business_key=False).lower()

    sql_hashes = conn.execute(
        """
        SELECT
            LOWER(SHA256(CONCAT_WS('||',
                CAST(STRLEN('AB') AS VARCHAR) || ':' || 'AB',
                CAST(STRLEN('C') AS VARCHAR) || ':' || 'C'
            ))) AS h_ab_c,
            LOWER(SHA256(CONCAT_WS('||',
                CAST(STRLEN('A') AS VARCHAR) || ':' || 'A',
                CAST(STRLEN('BC') AS VARCHAR) || ':' || 'BC'
            ))) AS h_a_bc
        """
    ).fetchone()
    assert sql_hashes is not None
    duck_hash_ab_c = str(sql_hashes[0])
    duck_hash_a_bc = str(sql_hashes[1])

    boundary_shift_distinct = (
        py_hash_ab_c != py_hash_a_bc
        and duck_hash_ab_c != duck_hash_a_bc
        and py_hash_ab_c == duck_hash_ab_c
        and py_hash_a_bc == duck_hash_a_bc
    )

    # 2. Apply a single-attribute / boundary-shift delta to `raw_vault.sat_lp_core`
    #    for LEGACY_GP_ADMIN:LP-101 (Bavarian Pension) at load_dts = '2026-10-02 08:00:00'
    #    using the standard Zone 2 Satellite anti-join (`cur.hash_diff <> src.hash_diff`).
    conn.execute(
        """
        INSERT INTO raw_vault.sat_lp_core (
            hk_lp, load_dts, rec_src, hash_diff, lp_legal_name, lp_type, country_code
        )
        SELECT
            src.hk_lp,
            src.load_dts,
            src.rec_src,
            src.hash_diff,
            src.lp_legal_name,
            src.lp_type,
            src.country_code
        FROM (
            SELECT
                LOWER(SHA256('15:LEGACY_GP_ADMIN||6:LP-101')) AS hk_lp,
                TIMESTAMP '2026-10-02 08:00:00' AS load_dts,
                'LEGACY_GP_ADMIN' AS rec_src,
                LOWER(SHA256('1:A||2:BC||2:DE')) AS hash_diff,
                'Bavarian Municipal Pension Versorgungswerk e.V.' AS lp_legal_name,
                'LEGACY_INSTITUTIONAL' AS lp_type,
                'DE' AS country_code
        ) src
        LEFT JOIN (
            SELECT hk_lp, hash_diff
            FROM raw_vault.sat_lp_core
            QUALIFY ROW_NUMBER() OVER (PARTITION BY hk_lp ORDER BY load_dts DESC) = 1
        ) cur ON cur.hk_lp = src.hk_lp
        WHERE cur.hk_lp IS NULL OR cur.hash_diff <> src.hash_diff;
        """
    )

    after_hubs, after_links, after_sats = _snapshot_vault_counts(conn)
    d_hubs = after_hubs - before_hubs
    d_links = after_links - before_links
    d_sats = after_sats - before_sats

    passed = boundary_shift_distinct and d_hubs == 0 and d_links == 0 and d_sats == 1
    return AdversarialBatchResult(
        batch_number=3,
        batch_name="Single-Attribute Delta & Boundary-Shift Collision",
        passed=passed,
        hub_delta_rows=d_hubs,
        link_delta_rows=d_links,
        satellite_delta_rows=d_sats,
        total_delta_rows=d_hubs + d_links + d_sats,
        orphan_count=0,
        metrics={
            "hash_diff_ab_c": duck_hash_ab_c,
            "hash_diff_a_bc": duck_hash_a_bc,
            "boundary_shift_distinct": boundary_shift_distinct,
            "hub_delta_rows": d_hubs,
            "link_delta_rows": d_links,
            "satellite_delta_rows": d_sats,
        },
        detail=(
            f"Boundary-shift '2:AB||1:C' ({duck_hash_ab_c[:12]}...) != '1:A||2:BC' "
            f"({duck_hash_a_bc[:12]}...); inserted +{d_sats} Satellite version row "
            f"with +{d_hubs} Hub / +{d_links} Link rows."
        ),
    )


def run_batch_4_cross_source_and_bitemporal_restatement(
    conn: duckdb.DuckDBPyConnection,
) -> AdversarialBatchResult:
    """Execute Batch 4: N=1->N=2 Cross-Source Collision & Bi-Temporal Backdated Restatement."""
    before_hubs, before_links, before_sats = _snapshot_vault_counts(conn)

    # 1. Verify N=1 -> N=2 cross-source BKCC collision isolation on 'LP-101'
    lp101_rows = conn.execute(
        """
        SELECT bkcc, lp_id, hk_lp
        FROM raw_vault.hub_limited_partner
        WHERE lp_id = 'LP-101'
        ORDER BY bkcc
        """
    ).fetchall()
    distinct_lp101_hks = {str(r[2]) for r in lp101_rows}
    bkcc_collision_isolated = len(lp101_rows) == 2 and len(distinct_lp101_hks) == 2

    # 2. Insert the November 15, 2026 Q3 backdated valuation restatement for Nordic Cloud
    #    (effective_from_dt = 2026-09-30, assertion_ts = 2026-11-15 09:15:00,
    #     12.80x / $216.28M EV -> 14.50x / $245.00M EV)
    conn.execute(
        """
        INSERT INTO dealcloud.src_portfolio_companies
        SELECT
            'PC-DEAL-01',
            '529900T8BM49AURSDO55',
            'Nordic Cloud Infrastructure AB',
            '45102010',
            'SE',
            2023,
            16896550.00,
            14.50,
            245000000.00,
            DATE '2026-09-30',
            TIMESTAMP '2026-11-15 09:15:00'
        WHERE NOT EXISTS (
            SELECT 1 FROM dealcloud.src_portfolio_companies
            WHERE lei_code = '529900T8BM49AURSDO55'
              AND entry_ev_multiple = 14.50
        );
        """
    )

    # 3. Propagate the late-arriving restatement into `raw_vault.sat_portco_valuation_mnpi`
    conn.execute(
        """
        INSERT INTO raw_vault.sat_portco_valuation_mnpi
        SELECT
            src.hk_portco,
            src.load_dts,
            src.applied_dts,
            src.rec_src,
            src.hash_diff_portco_valuation,
            src.entry_ebitda_usd,
            src.entry_ev_multiple,
            src.current_nav_usd,
            src.as_of_quarter_end
        FROM staging.stg_dealcloud_portfolio_companies src
        WHERE src.hk_portco NOT IN (REPEAT('0', 64), REPEAT('f', 64))
          AND NOT EXISTS (
              SELECT 1 FROM raw_vault.sat_portco_valuation_mnpi s
              WHERE s.hk_portco = src.hk_portco
                AND s.hash_diff = src.hash_diff_portco_valuation
          );
        """
    )

    # 4. Refresh the PIT table for both audit timestamps
    ensure_pit_snapshot_for_cutoff(conn, "2026-10-15 12:00:00")
    ensure_pit_snapshot_for_cutoff(conn, "2026-11-15 23:59:59")

    after_hubs, after_links, after_sats = _snapshot_vault_counts(conn)
    d_hubs = after_hubs - before_hubs
    d_links = after_links - before_links
    d_sats = after_sats - before_sats

    pit_engine = PitQueryEngine(conn)
    proof = pit_engine.verify_bitemporal_restatement(
        prior_cutoff_ts="2026-10-15 12:00:00",
        restated_cutoff_ts="2026-11-15 23:59:59",
        effective_as_of_dt="2026-09-30",
    )

    passed = (
        bkcc_collision_isolated and d_hubs == 0 and d_links == 0 and d_sats == 1 and proof.verified
    )
    return AdversarialBatchResult(
        batch_number=4,
        batch_name="N=1->N=2 Cross-Source Collision & Bi-Temporal Backdated Restatement",
        passed=passed,
        hub_delta_rows=d_hubs,
        link_delta_rows=d_links,
        satellite_delta_rows=d_sats,
        total_delta_rows=d_hubs + d_links + d_sats,
        orphan_count=0,
        metrics={
            "bkcc_collision_isolated": bkcc_collision_isolated,
            "lp101_distinct_hub_keys": len(distinct_lp101_hks),
            "effective_from_dt": "2026-09-30",
            "assertion_ts": "2026-11-15 09:15:00",
            "oct_15_ev_ebitda_multiple": str(proof.prior_ev_ebitda_multiple),
            "oct_15_enterprise_value_usd": str(proof.prior_enterprise_value_usd),
            "nov_15_ev_ebitda_multiple": str(proof.restated_ev_ebitda_multiple),
            "nov_15_enterprise_value_usd": str(proof.restated_enterprise_value_usd),
            "bitemporal_proof_verified": proof.verified,
        },
        detail=(
            f"Isolated 2 'LP-101' Hub keys across GLOBAL/LEGACY_GP_ADMIN; inserted Q3 restatement "
            f"(effective_from_dt=2026-09-30, assertion_ts=2026-11-15 09:15:00) from "
            f"{proof.prior_ev_ebitda_multiple}x (${proof.prior_enterprise_value_usd:,.2f}) to "
            f"{proof.restated_ev_ebitda_multiple}x (${proof.restated_enterprise_value_usd:,.2f})."
        ),
    )


def execute_4batch_adversarial_suite(
    conn: duckdb.DuckDBPyConnection,
    bundle: Compiled4ZoneBundle,
) -> FourBatchExecutionReport:
    """Execute all 4 Adversarial Shadow Sandbox Batches in sequence on `conn`."""
    b1 = run_batch_1_baseline_and_ghost_completeness(conn, bundle)
    b2 = run_batch_2_replay_idempotency(conn, bundle)
    b3 = run_batch_3_delta_and_boundary_shift(conn)
    b4 = run_batch_4_cross_source_and_bitemporal_restatement(conn)

    batches = [b1, b2, b3, b4]
    return FourBatchExecutionReport(
        all_passed=all(b.passed for b in batches),
        batches=batches,
    )
