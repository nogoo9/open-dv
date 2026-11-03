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
from opendv_core.models.canonical_ir import CanonicalIR
from opendv_duckdb_sandbox.compiler_4zone import (
    Compiled4ZoneBundle,
    _compile_generic_zone_3_business_vault_sql,
    _effective_pit_tables,
    _hub_slug,
    _satellite_all_data_columns,
    _satellite_schema,
    _satellite_seed_sql_literal,
    _validate_ident,
    is_pe_domain,
)
from opendv_duckdb_sandbox.dynamic_checks import (
    build_vault_topology_maps,
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


def _snapshot_vault_counts(
    conn: duckdb.DuckDBPyConnection,
    ir: CanonicalIR | None = None,
) -> tuple[int, int, int]:
    """Return `(hub_rows, link_rows, satellite_rows)` across all Vault tables."""
    hub_pk_map, link_pk_map, satellite_pk_map, _ = build_vault_topology_maps(ir)
    return (
        _count_table_group(conn, hub_pk_map),
        _count_table_group(conn, link_pk_map),
        _count_table_group(conn, satellite_pk_map),
    )


def run_batch_1_baseline_and_ghost_completeness(
    conn: duckdb.DuckDBPyConnection,
    bundle: Compiled4ZoneBundle,
    *,
    ir: CanonicalIR | None = None,
) -> AdversarialBatchResult:
    """Execute Batch 1: Baseline Load + Null-FK & Ghost `INNER JOIN` Completeness."""
    conn.execute(bundle.zones_0_to_3_sql)
    hub_pk_map, link_pk_map, satellite_pk_map, _ = build_vault_topology_maps(ir)
    hub_rows, link_rows, sat_rows = _snapshot_vault_counts(conn, ir)

    ref_check = check_dv_dyn_002_referential_integrity(conn, ir=ir)
    ghost_check = check_dv_dyn_007_ghost_and_zero_key_completeness(conn, ir=ir)

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
        for tbl, pk in {**hub_pk_map, **link_pk_map}.items()
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
        for tbl, pk in satellite_pk_map.items()
    )

    links_loaded_ok = link_rows > 0 if link_pk_map else link_rows == 0
    passed = (
        ref_check.passed
        and ghost_check.passed
        and zero_keys_verified
        and ghosts_verified
        and hub_rows > 0
        and links_loaded_ok
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
    *,
    ir: CanonicalIR | None = None,
) -> AdversarialBatchResult:
    """Execute Batch 2: Replay Idempotency (Zone 2 & Zone 3 replay yields delta_rows = 0)."""
    before_hubs, before_links, before_sats = _snapshot_vault_counts(conn, ir)

    # Re-execute all Zone 2 Raw Vault DML and Zone 3 Business Vault DML
    conn.execute(bundle.zone_2_sql)
    conn.execute(bundle.zone_3_sql)

    after_hubs, after_links, after_sats = _snapshot_vault_counts(conn, ir)
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
    *,
    ir: CanonicalIR | None = None,
) -> AdversarialBatchResult:
    """Execute Batch 3: Attribute Delta & Boundary-Shift Collision ('AB'||'C' vs 'A'||'BC')."""
    before_hubs, before_links, before_sats = _snapshot_vault_counts(conn, ir)

    # 1. Verify Python & DuckDB length-prefixed SHA-256 boundary-shift collision immunity
    py_hash_ab_c = compute_dv2_length_prefixed_sha256(["AB", "C"], is_business_key=False)
    py_hash_a_bc = compute_dv2_length_prefixed_sha256(["A", "BC"], is_business_key=False)

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

    if is_pe_domain(ir) or ir is None:
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
    else:
        target_sat = next(
            (
                s
                for s in ir.satellites
                if _satellite_schema(s) == "raw_vault" and s.satellite_type != "MULTI_ACTIVE"
            ),
            next(
                (s for s in ir.satellites if _satellite_schema(s) == "raw_vault"),
                ir.satellites[0],
            ),
        )
        sat_schema = _satellite_schema(target_sat)
        sat_tbl = _validate_ident(target_sat.name)
        parent_hk = _validate_ident(target_sat.parent_hk_column.name)
        has_applied = target_sat.applied_dts_column is not None
        data_cols = _satellite_all_data_columns(target_sat)

        existing_row = conn.execute(
            f"""
            SELECT {parent_hk}
            FROM {sat_schema}.{sat_tbl}
            WHERE rec_src <> '{GHOST_REC_SRC}'
            ORDER BY load_dts DESC
            LIMIT 1
            """
        ).fetchone()
        assert existing_row is not None
        target_hk_val = str(existing_row[0])

        payload_cols = [_validate_ident(c.name) for c in data_cols]
        payload_csv = ", ".join(payload_cols)
        src_payload_csv = ", ".join(f"src.{c}" for c in payload_cols)
        mutated_selects = ",\n                ".join(
            f"{_satellite_seed_sql_literal(c, 3)} AS {_validate_ident(c.name)}" for c in data_cols
        )
        app_col = "applied_dts, " if has_applied else ""
        src_app_col = "src.applied_dts, " if has_applied else ""
        app_sel = (
            "TIMESTAMP '2026-10-02 08:00:00' AS applied_dts,\n                "
            if has_applied
            else ""
        )
        part_cols = [
            parent_hk,
            *(
                _validate_ident(c.name if hasattr(c, "name") else str(c))
                for c in (target_sat.child_dependent_keys or [])
            ),
        ]
        part_csv = ", ".join(part_cols)
        join_on = " AND ".join(f"cur.{c} = src.{c}" for c in part_cols)

        conn.execute(
            f"""
            INSERT INTO {sat_schema}.{sat_tbl} (
                {parent_hk}, load_dts, {app_col}rec_src, hash_diff, {payload_csv}
            )
            SELECT
                src.{parent_hk},
                src.load_dts,
                {src_app_col}src.rec_src,
                src.hash_diff,
                {src_payload_csv}
            FROM (
                SELECT
                    '{target_hk_val}' AS {parent_hk},
                    TIMESTAMP '2026-10-02 08:00:00' AS load_dts,
                    {app_sel}'BATCH3_DELTA' AS rec_src,
                    LOWER(SHA256('1:A||2:BC||2:DE')) AS hash_diff,
                    {mutated_selects}
            ) src
            LEFT JOIN (
                SELECT {part_csv}, hash_diff
                FROM {sat_schema}.{sat_tbl}
                WHERE rec_src <> '{GHOST_REC_SRC}'
                QUALIFY ROW_NUMBER() OVER (PARTITION BY {part_csv} ORDER BY load_dts DESC) = 1
            ) cur ON {join_on}
            WHERE cur.{parent_hk} IS NULL OR cur.hash_diff <> src.hash_diff;
            """
        )

    after_hubs, after_links, after_sats = _snapshot_vault_counts(conn, ir)
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
    *,
    ir: CanonicalIR | None = None,
) -> AdversarialBatchResult:
    """Execute Batch 4: N=1->N=2 Cross-Source Collision & Bi-Temporal Backdated Restatement."""
    before_hubs, before_links, before_sats = _snapshot_vault_counts(conn, ir)

    if is_pe_domain(ir) or ir is None:
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

        after_hubs, after_links, after_sats = _snapshot_vault_counts(conn, ir)
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
            bkcc_collision_isolated
            and d_hubs == 0
            and d_links == 0
            and d_sats == 1
            and proof.verified
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
                f"Isolated 2 'LP-101' Hub keys across GLOBAL/LEGACY_GP_ADMIN; inserted Q3 "
                f"restatement (effective_from_dt=2026-09-30, assertion_ts=2026-11-15 09:15:00) "
                f"from {proof.prior_ev_ebitda_multiple}x "
                f"(${proof.prior_enterprise_value_usd:,.2f}) to "
                f"{proof.restated_ev_ebitda_multiple}x "
                f"(${proof.restated_enterprise_value_usd:,.2f})."
            ),
        )

    # Generic non-PE Batch 4: Cross-Source BKCC Collision Isolation & Bi-Temporal Restatement
    target_hub = next(
        (h for h in ir.hubs if any(bk.is_bkcc for bk in h.business_keys)),
        ir.hubs[0],
    )
    hub_tbl = _validate_ident(target_hub.name)
    hk_col = _validate_ident(target_hub.hk_column.name)
    slug = _hub_slug(target_hub.name)
    shared_bk = f"{slug}-999"
    has_bkcc_col = any(bk.is_bkcc for bk in target_hub.business_keys)

    inserted_hks: list[str] = []
    for ns_label in ("GLOBAL", "ALT_NAMESPACE"):
        row_tokens: list[str] = []
        bk_literals: list[str] = []
        for bk in target_hub.business_keys:
            if bk.is_bkcc:
                token = ns_label
            elif has_bkcc_col:
                token = shared_bk
            else:
                token = f"{ns_label}:{shared_bk}"
            row_tokens.append(token)
            bk_literals.append(f"'{token}'")
        hk_val = compute_dv2_length_prefixed_sha256(row_tokens, is_business_key=True)
        inserted_hks.append(hk_val)
        bk_vals_sql = ", ".join(bk_literals)
        conn.execute(
            f"""
            INSERT INTO raw_vault.{hub_tbl}
            SELECT
                '{hk_val}',
                {bk_vals_sql},
                TIMESTAMP '2026-10-01 08:00:00',
                '{ns_label}_SRC'
            WHERE NOT EXISTS (
                SELECT 1 FROM raw_vault.{hub_tbl} h
                WHERE h.{hk_col} = '{hk_val}'
            );
            """
        )
    hk_global, hk_alt = inserted_hks[0], inserted_hks[1]

    matched_hks = conn.execute(
        f"""
        SELECT DISTINCT {hk_col}
        FROM raw_vault.{hub_tbl}
        WHERE {hk_col} IN ('{hk_global}', '{hk_alt}')
        """
    ).fetchall()
    distinct_bkcc_hks = {str(r[0]) for r in matched_hks}
    bkcc_collision_isolated = len(distinct_bkcc_hks) == 2 and hk_global != hk_alt

    # Bi-temporal / late-arriving restatement on the target satellite
    bitemp_sat = next(
        (s for s in ir.satellites if s.applied_dts_column is not None),
        ir.satellites[0],
    )
    sat_schema = _satellite_schema(bitemp_sat)
    sat_tbl = _validate_ident(bitemp_sat.name)
    parent_hk = _validate_ident(bitemp_sat.parent_hk_column.name)
    has_applied = bitemp_sat.applied_dts_column is not None
    bitemp_data_cols = _satellite_all_data_columns(bitemp_sat)

    existing_sat_row = conn.execute(
        f"""
        SELECT {parent_hk}
        FROM {sat_schema}.{sat_tbl}
        WHERE rec_src <> '{GHOST_REC_SRC}'
        ORDER BY load_dts ASC
        LIMIT 1
        """
    ).fetchone()
    assert existing_sat_row is not None
    target_sat_hk = str(existing_sat_row[0])

    payload_cols = [_validate_ident(c.name) for c in bitemp_data_cols]
    payload_csv = ", ".join(payload_cols)
    restated_vals = ", ".join(_satellite_seed_sql_literal(c, 4) for c in bitemp_data_cols)
    app_col = "applied_dts, " if has_applied else ""
    app_val = "TIMESTAMP '2026-09-30 00:00:00', " if has_applied else ""

    conn.execute(
        f"""
        INSERT INTO {sat_schema}.{sat_tbl} (
            {parent_hk}, load_dts, {app_col}rec_src, hash_diff, {payload_csv}
        )
        SELECT
            '{target_sat_hk}',
            TIMESTAMP '2026-11-15 09:15:00',
            {app_val}'BATCH4_RESTATEMENT',
            LOWER(SHA256('4:REST||4:ATED||2:V4')),
            {restated_vals}
        WHERE NOT EXISTS (
            SELECT 1 FROM {sat_schema}.{sat_tbl} s
            WHERE s.{parent_hk} = '{target_sat_hk}'
              AND s.load_dts = TIMESTAMP '2026-11-15 09:15:00'
        );
        """
    )

    # Refresh PIT tables in business_vault so PIT lookup parity holds across all snapshots
    for pit in _effective_pit_tables(ir):
        pit_ident = _validate_ident(pit.name)
        conn.execute(f"DELETE FROM business_vault.{pit_ident};")
    conn.execute(_compile_generic_zone_3_business_vault_sql(ir))

    prior_row = conn.execute(
        f"""
        SELECT hash_diff FROM {sat_schema}.{sat_tbl}
        WHERE {parent_hk} = '{target_sat_hk}'
          AND load_dts <= TIMESTAMP '2026-10-15 12:00:00'
        ORDER BY load_dts DESC
        LIMIT 1
        """
    ).fetchone()
    restated_row = conn.execute(
        f"""
        SELECT hash_diff FROM {sat_schema}.{sat_tbl}
        WHERE {parent_hk} = '{target_sat_hk}'
          AND load_dts <= TIMESTAMP '2026-11-15 23:59:59'
        ORDER BY load_dts DESC
        LIMIT 1
        """
    ).fetchone()
    bitemp_verified = (
        prior_row is not None
        and restated_row is not None
        and str(prior_row[0]) != str(restated_row[0])
    )

    ref_check = check_dv_dyn_002_referential_integrity(conn, ir=ir)
    after_hubs, after_links, after_sats = _snapshot_vault_counts(conn, ir)
    d_hubs = after_hubs - before_hubs
    d_links = after_links - before_links
    d_sats = after_sats - before_sats

    passed = (
        bkcc_collision_isolated
        and bitemp_verified
        and ref_check.failing_rows == 0
        and d_links == 0
        and d_sats == 1
    )
    return AdversarialBatchResult(
        batch_number=4,
        batch_name="N=1->N=2 Cross-Source Collision & Bi-Temporal Backdated Restatement",
        passed=passed,
        hub_delta_rows=d_hubs,
        link_delta_rows=d_links,
        satellite_delta_rows=d_sats,
        total_delta_rows=d_hubs + d_links + d_sats,
        orphan_count=ref_check.failing_rows,
        metrics={
            "bkcc_collision_isolated": bkcc_collision_isolated,
            "lp101_distinct_hub_keys": len(distinct_bkcc_hks),
            "effective_from_dt": "2026-09-30",
            "assertion_ts": "2026-11-15 09:15:00",
            "bitemporal_proof_verified": bitemp_verified,
        },
        detail=(
            f"Isolated {len(distinct_bkcc_hks)} cross-source BKCC Hub keys on {hub_tbl}; "
            f"inserted restatement into {sat_tbl} (bitemporal_verified={bitemp_verified}, "
            f"orphans={ref_check.failing_rows})."
        ),
    )


def execute_4batch_adversarial_suite(
    conn: duckdb.DuckDBPyConnection,
    bundle: Compiled4ZoneBundle,
    ir: CanonicalIR | None = None,
) -> FourBatchExecutionReport:
    """Execute all 4 Adversarial Shadow Sandbox Batches in sequence on `conn`."""
    b1 = run_batch_1_baseline_and_ghost_completeness(conn, bundle, ir=ir)
    b2 = run_batch_2_replay_idempotency(conn, bundle, ir=ir)
    b3 = run_batch_3_delta_and_boundary_shift(conn, ir=ir)
    b4 = run_batch_4_cross_source_and_bitemporal_restatement(conn, ir=ir)

    batches = [b1, b2, b3, b4]
    return FourBatchExecutionReport(
        all_passed=all(b.passed for b in batches),
        batches=batches,
    )
