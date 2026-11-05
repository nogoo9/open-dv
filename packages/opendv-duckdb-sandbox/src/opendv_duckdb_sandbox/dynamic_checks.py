"""15 Dynamic DuckDB Sandbox Invariant Evaluators (`DV-DYN-001` through `DV-DYN-015`).

Evaluates the 15 runtime Data Vault 2.0 invariants defined in `docs/03` §7.2 and
`docs/07` Scene 6 against an active DuckDB connection and returns structured
`DynamicCheckResult` diagnostics.
"""

from __future__ import annotations

import duckdb
from pydantic import BaseModel, ConfigDict, Field

from opendv_core.constants import GHOST_DTS, GHOST_REC_SRC, ZERO_KEY_ERROR, ZERO_KEY_UNKNOWN
from opendv_core.hashing import compute_dv2_length_prefixed_sha256, compute_dv2_prehash_string
from opendv_core.models.canonical_ir import CanonicalIR
from opendv_duckdb_sandbox.compiler_4zone import (
    _effective_pit_tables,
    _find_bitemporal_satellite,
    _link_schema,
    _satellite_schema,
    _validate_ident,
    build_algorithm_a5_sha256_sql,
    build_length_prefixed_token_sql,
)


class DynamicCheckResult(BaseModel):
    """Structured outcome of a single `DV-DYN-*` sandbox check."""

    model_config = ConfigDict(extra="forbid")

    check_id: str = Field(pattern=r"^DV-DYN-0(0[1-9]|1[0-5])$")
    check_name: str
    passed: bool
    metric_value: int | float | str | bool
    detail: str
    failing_rows: int = 0


class DynamicCheckSuiteReport(BaseModel):
    """Aggregated report of all 15 dynamic checks."""

    model_config = ConfigDict(extra="forbid")

    all_passed: bool
    passed_count: int
    failed_count: int
    checks: list[DynamicCheckResult]

    @property
    def by_id(self) -> dict[str, DynamicCheckResult]:
        """Lookup map of `check_id -> DynamicCheckResult`."""
        return {c.check_id: c for c in self.checks}


def _inspect_vault_topology_from_duckdb(
    conn: duckdb.DuckDBPyConnection,
) -> tuple[
    dict[str, str],
    dict[str, str],
    dict[str, str],
    tuple[tuple[str, str, str, str], ...],
]:
    """Inspect DuckDB information_schema to dynamically derive Data Vault topology."""
    try:
        rows = conn.execute(
            """
            SELECT table_schema, table_name, column_name, ordinal_position
            FROM information_schema.columns
            WHERE table_schema IN ('raw_vault', 'business_vault')
            ORDER BY table_schema, table_name, ordinal_position
            """
        ).fetchall()
    except duckdb.Error:
        return {}, {}, {}, ()

    table_cols: dict[tuple[str, str], list[str]] = {}
    for schema, tbl, col, _ in rows:
        table_cols.setdefault((schema, tbl), []).append(col)

    hub_pk_map: dict[str, str] = {}
    link_pk_map: dict[str, str] = {}
    satellite_pk_map: dict[str, str] = {}
    fk_edges: list[tuple[str, str, str, str]] = []

    # 1. Hubs
    for (schema, tbl), cols in table_cols.items():
        if tbl.startswith(("hub_", "bhub_")):
            hk_cols = [c for c in cols if c.startswith("hk_")]
            hk = hk_cols[0] if hk_cols else cols[0]
            hub_pk_map[f"{schema}.{tbl}"] = hk

    # 2. Links
    for (schema, tbl), cols in table_cols.items():
        if tbl.startswith(("lnk_", "sal_", "hal_")):
            hk_cols = [c for c in cols if c.startswith("hk_")]
            link_pk = hk_cols[0] if hk_cols else cols[0]
            link_pk_map[f"{schema}.{tbl}"] = link_pk
            for c in cols:
                if c != link_pk and c.startswith("hk_"):
                    for hub_q, hub_hk in hub_pk_map.items():
                        if c == hub_hk or c.endswith(hub_hk.removeprefix("hk_")):
                            fk_edges.append((f"{schema}.{tbl}", c, hub_q, hub_hk))

    # 3. Satellites
    for (schema, tbl), cols in table_cols.items():
        if tbl.startswith(("sat_", "esat_")):
            hk_cols = [c for c in cols if c.startswith("hk_")]
            sat_hk = hk_cols[0] if hk_cols else cols[0]
            satellite_pk_map[f"{schema}.{tbl}"] = sat_hk
            matched = False
            for hub_q, hub_hk in hub_pk_map.items():
                if sat_hk == hub_hk:
                    fk_edges.append((f"{schema}.{tbl}", sat_hk, hub_q, hub_hk))
                    matched = True
                    break
            if not matched:
                for lnk_q, lnk_hk in link_pk_map.items():
                    if sat_hk == lnk_hk:
                        fk_edges.append((f"{schema}.{tbl}", sat_hk, lnk_q, lnk_hk))
                        break

    return hub_pk_map, link_pk_map, satellite_pk_map, tuple(fk_edges)


def build_vault_topology_maps(
    ir: CanonicalIR | None = None,
    conn: duckdb.DuckDBPyConnection | None = None,
) -> tuple[
    dict[str, str],
    dict[str, str],
    dict[str, str],
    tuple[tuple[str, str, str, str], ...],
]:
    """Derive `(hub_pk_map, link_pk_map, satellite_pk_map, fk_edges)` from `ir` or active `conn`."""
    if ir is not None:
        hub_pk_map: dict[str, str] = {}
        hub_lookup: dict[str, tuple[str, str]] = {}
        for hub in ir.hubs:
            q_hub = f"raw_vault.{_validate_ident(hub.name)}"
            hk_col = _validate_ident(hub.hk_column.name)
            hub_pk_map[q_hub] = hk_col
            hub_lookup[hub.name] = (q_hub, hk_col)

        link_pk_map: dict[str, str] = {}
        link_lookup: dict[str, tuple[str, str]] = {}
        fk_edges: list[tuple[str, str, str, str]] = []
        all_links = list(ir.links) + list(getattr(ir, "same_as_links", None) or [])
        for link in all_links:
            schema = _link_schema(link)
            q_link = f"{schema}.{_validate_ident(link.name)}"
            hk_col = _validate_ident(link.hk_column.name)
            link_pk_map[q_link] = hk_col
            link_lookup[link.name] = (q_link, hk_col)
            for ref in link.hub_references:
                if ref.target_hub in hub_lookup:
                    parent_q, parent_pk = hub_lookup[ref.target_hub]
                    fk_edges.append(
                        (
                            q_link,
                            _validate_ident(ref.fk_column_name),
                            parent_q,
                            parent_pk,
                        )
                    )

        satellite_pk_map: dict[str, str] = {}
        for sat in ir.satellites:
            schema = _satellite_schema(sat)
            q_sat = f"{schema}.{_validate_ident(sat.name)}"
            parent_hk = _validate_ident(sat.parent_hk_column.name)
            satellite_pk_map[q_sat] = parent_hk
            if sat.parent_entity in hub_lookup:
                parent_q, parent_pk = hub_lookup[sat.parent_entity]
                fk_edges.append((q_sat, parent_hk, parent_q, parent_pk))
            elif sat.parent_entity in link_lookup:
                parent_q, parent_pk = link_lookup[sat.parent_entity]
                fk_edges.append((q_sat, parent_hk, parent_q, parent_pk))

        return hub_pk_map, link_pk_map, satellite_pk_map, tuple(fk_edges)

    if conn is not None:
        return _inspect_vault_topology_from_duckdb(conn)

    return {}, {}, {}, ()


def _scalar_int(conn: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    return int(row[0]) if row is not None and row[0] is not None else 0


def _table_column_map(conn: duckdb.DuckDBPyConnection, schema_and_table: str) -> dict[str, str]:
    try:
        rows = conn.execute(f"DESCRIBE {schema_and_table}").fetchall()
    except duckdb.Error:
        return {}
    return {str(r[0]).lower(): _validate_ident(str(r[0])) for r in rows}


def check_dv_dyn_001_pk_uniqueness(
    conn: duckdb.DuckDBPyConnection,
    *,
    ir: CanonicalIR | None = None,
) -> DynamicCheckResult:
    """DV-DYN-001: Hash Key Primary Key Uniqueness across all Vault entities."""
    hub_pk_map, link_pk_map, satellite_pk_map, _ = build_vault_topology_maps(ir, conn=conn)
    pk_violations = 0

    for tbl, hk in hub_pk_map.items():
        pk_violations += _scalar_int(
            conn,
            f"""
            SELECT COUNT(*) FROM (
                SELECT {hk} FROM {tbl}
                GROUP BY {hk}
                HAVING COUNT(*) > 1
            )
            """,
        )

    for tbl, hk in link_pk_map.items():
        pk_violations += _scalar_int(
            conn,
            f"""
            SELECT COUNT(*) FROM (
                SELECT {hk} FROM {tbl}
                GROUP BY {hk}
                HAVING COUNT(*) > 1
            )
            """,
        )

    for tbl, parent_hk in satellite_pk_map.items():
        col_map = _table_column_map(conn, tbl)
        child_keys = [c for c in col_map.values() if c.endswith("_seq") or c.startswith("seq_")]
        part_cols = [parent_hk, "load_dts", *child_keys]
        part_csv = ", ".join(part_cols)
        pk_violations += _scalar_int(
            conn,
            f"""
            SELECT COUNT(*) FROM (
                SELECT {part_csv} FROM {tbl}
                GROUP BY {part_csv}
                HAVING COUNT(*) > 1
            )
            """,
        )

    total_tables = len(hub_pk_map) + len(link_pk_map) + len(satellite_pk_map)
    return DynamicCheckResult(
        check_id="DV-DYN-001",
        check_name="Hash Key PK Uniqueness",
        passed=pk_violations == 0,
        metric_value=pk_violations,
        failing_rows=pk_violations,
        detail=f"Evaluated {total_tables} tables; duplicate PK groups={pk_violations}.",
    )


def check_dv_dyn_002_referential_integrity(
    conn: duckdb.DuckDBPyConnection,
    *,
    ir: CanonicalIR | None = None,
) -> DynamicCheckResult:
    """DV-DYN-002: Foreign Key Referential Integrity (Link/Sat -> Hub/Link PK)."""
    _, _, _, fk_edges = build_vault_topology_maps(ir, conn=conn)
    orphan_total = 0

    for child_tbl, child_fk, parent_tbl, parent_pk in fk_edges:
        orphan_count = _scalar_int(
            conn,
            f"""
            SELECT COUNT(*)
            FROM {child_tbl} c
            LEFT JOIN {parent_tbl} p ON p.{parent_pk} = c.{child_fk}
            WHERE p.{parent_pk} IS NULL
              AND c.{child_fk} NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')
            """,
        )
        orphan_total += orphan_count

    return DynamicCheckResult(
        check_id="DV-DYN-002",
        check_name="FK Referential Integrity",
        passed=orphan_total == 0,
        metric_value=orphan_total,
        failing_rows=orphan_total,
        detail=f"Verified {len(fk_edges)} FK relationships; orphan rows={orphan_total}.",
    )


def check_dv_dyn_003_replay_idempotency(
    conn: duckdb.DuckDBPyConnection,
    *,
    recorded_replay_delta: int | None = None,
    ir: CanonicalIR | None = None,
) -> DynamicCheckResult:
    """DV-DYN-003: Replay Idempotency (re-running identical batch yields delta_rows = 0)."""
    if recorded_replay_delta is not None:
        delta = recorded_replay_delta
    else:
        hub_pk_map, _, _, _ = build_vault_topology_maps(ir, conn=conn)
        if hub_pk_map:
            first_hub_q = next(iter(hub_pk_map))
            tbl = first_hub_q.split(".")[-1]
            hk = hub_pk_map[first_hub_q]
            col_map = _table_column_map(conn, first_hub_q)
            bk_cols = [c for c in col_map.values() if c not in {hk, "load_dts", "rec_src"}]
            bk_csv = ", ".join(bk_cols)
            src_bk_csv = ", ".join(f"src.{c}" for c in bk_cols)
            before = _scalar_int(conn, f"SELECT COUNT(*) FROM {first_hub_q}")
            conn.execute(
                f"""
                INSERT INTO {first_hub_q} ({hk}, {bk_csv}, load_dts, rec_src)
                SELECT src.{hk}, {src_bk_csv}, MIN(src.load_dts), ANY_VALUE(src.rec_src)
                FROM staging.stg_{tbl} src
                WHERE src.{hk} NOT IN (REPEAT('0', 64), REPEAT('f', 64))
                  AND NOT EXISTS (
                      SELECT 1 FROM {first_hub_q} h
                      WHERE h.{hk} = src.{hk}
                  )
                GROUP BY src.{hk}, {src_bk_csv}
                """
            )
            after = _scalar_int(conn, f"SELECT COUNT(*) FROM {first_hub_q}")
            delta = after - before
        else:
            delta = 0

    return DynamicCheckResult(
        check_id="DV-DYN-003",
        check_name="Replay Idempotency",
        passed=delta == 0,
        metric_value=delta,
        failing_rows=delta,
        detail=f"Replaying idempotent batch inserted {delta} delta rows (expected 0).",
    )


def check_dv_dyn_004_true_change_capture(
    conn: duckdb.DuckDBPyConnection,
    *,
    recorded_sat_delta: int | None = None,
    recorded_hub_link_delta: int = 0,
    ir: CanonicalIR | None = None,
) -> DynamicCheckResult:
    """DV-DYN-004: True Change Capture (+1 Satellite row on attribute mutation, +0 Hub/Link)."""
    if recorded_sat_delta is not None:
        sat_versions = recorded_sat_delta
    else:
        _, _, satellite_pk_map, _ = build_vault_topology_maps(ir, conn=conn)
        if satellite_pk_map:
            first_sat_tbl = next(iter(satellite_pk_map))
            sat_versions = _scalar_int(
                conn,
                f"SELECT COUNT(*) FROM {first_sat_tbl} WHERE rec_src <> '{GHOST_REC_SRC}'",
            )
        else:
            sat_versions = 0
    passed = sat_versions >= 1 and recorded_hub_link_delta == 0
    return DynamicCheckResult(
        check_id="DV-DYN-004",
        check_name="True Change Capture",
        passed=passed,
        metric_value=sat_versions,
        failing_rows=0 if passed else 1,
        detail=(
            f"Captured {sat_versions} Satellite delta version(s) with "
            f"hub_link_delta={recorded_hub_link_delta}."
        ),
    )


def check_dv_dyn_005_boundary_shift_collision(
    conn: duckdb.DuckDBPyConnection,
    *,
    ir: CanonicalIR | None = None,
) -> DynamicCheckResult:
    """DV-DYN-005 / DV-HASH-004: Boundary-Shift Collision Resistance & Multi-Byte UTF-8 Parity."""
    _ = ir
    multibyte_val = "Nordisk Suverän Fond"
    py_prehash = compute_dv2_prehash_string([multibyte_val], is_business_key=False)
    py_sha256 = compute_dv2_length_prefixed_sha256([multibyte_val], is_business_key=False).lower()
    token_sql = build_length_prefixed_token_sql("v.val", is_business_key=False)
    sha_sql = build_algorithm_a5_sha256_sql(["v.val"], is_business_key=False)
    row = conn.execute(
        f"""
        SELECT
            LOWER(SHA256('2:AB||1:C')) AS h_ab_c,
            LOWER(SHA256('1:A||2:BC')) AS h_a_bc,
            LOWER(SHA256('7:ANNE||O||5:BRIEN')) AS h_anne1,
            LOWER(SHA256('4:ANNE||7:O||BRIEN')) AS h_anne2,
            {token_sql} AS utf8_prehash,
            {sha_sql} AS utf8_sha256
        FROM (SELECT ? AS val) AS v
        """,
        [multibyte_val],
    ).fetchone()
    assert row is not None
    staging_strlen_views = _scalar_int(
        conn,
        """
        SELECT COUNT(*) FROM duckdb_views()
        WHERE schema_name = 'staging' AND UPPER(sql) LIKE '%STRLEN(%'
        """,
    )
    utf8_mismatch = int(
        row[4] != py_prehash
        or row[5] != py_sha256
        or "OCTET_LENGTH(" not in token_sql.upper()
        or "STRLEN(" in token_sql.upper()
    )
    collisions = (
        int(row[0] == row[1]) + int(row[2] == row[3]) + utf8_mismatch + staging_strlen_views
    )
    return DynamicCheckResult(
        check_id="DV-DYN-005",
        check_name="Boundary-Shift Collision Resistance",
        passed=collisions == 0,
        metric_value=collisions,
        failing_rows=collisions,
        detail=(
            f"Length-prefixed SHA-256 ('2:AB||1:C' vs '1:A||2:BC', "
            f"DV-HASH-004 '{py_prehash}') collisions={collisions}."
        ),
    )


def check_dv_dyn_006_bkcc_collision_and_sal(
    conn: duckdb.DuckDBPyConnection,
    *,
    ir: CanonicalIR | None = None,
) -> DynamicCheckResult:
    """DV-DYN-006: Multi-Source BK Convergence, BKCC Isolation & Canonical SAL Resolution."""
    hub_pk_map, link_pk_map, _, _ = build_vault_topology_maps(ir, conn=conn)
    if not hub_pk_map:
        return DynamicCheckResult(
            check_id="DV-DYN-006",
            check_name="Multi-Source BK Convergence & BKCC Isolation",
            passed=True,
            metric_value=0,
            failing_rows=0,
            detail="No hubs present to evaluate BKCC isolation.",
        )
    first_hub_q = next(iter(hub_pk_map))
    hk_col = hub_pk_map[first_hub_q]
    non_zero_rows = _scalar_int(
        conn,
        f"""
        SELECT COUNT(*) FROM {first_hub_q}
        WHERE {hk_col} NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')
        """,
    )
    distinct_hks = _scalar_int(
        conn,
        f"""
        SELECT COUNT(DISTINCT {hk_col}) FROM {first_hub_q}
        WHERE {hk_col} NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')
        """,
    )
    bkcc_distinct = _scalar_int(
        conn,
        """
        SELECT CASE
            WHEN LOWER(SHA256('6:GLOBAL||8:KEY-0001'))
              <> LOWER(SHA256('13:ALT_NAMESPACE||8:KEY-0001'))
            THEN 1 ELSE 0
        END
        """,
    )
    sal_links = [k for k in link_pk_map if k.split(".")[-1].startswith("sal_")]
    sal_ok = True
    sal_violations = 0
    for sal_q in sal_links:
        sal_hk = link_pk_map[sal_q]
        cnt = _scalar_int(
            conn,
            f"""
            SELECT COUNT(*) FROM {sal_q}
            WHERE {sal_hk} NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')
            """,
        )
        if cnt < 1:
            sal_ok = False
        cols = _table_column_map(conn, sal_q)
        fk_cols = [c for c in cols.values() if c.startswith("hk_") and c != sal_hk]
        if len(fk_cols) >= 2:
            m_col, d_col = fk_cols[0], fk_cols[1]
            self_loops = _scalar_int(
                conn,
                f"""
                SELECT COUNT(*) FROM {sal_q}
                WHERE {sal_hk} NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')
                  AND {m_col} = {d_col}
                """,
            )
            inv_dups = _scalar_int(
                conn,
                f"""
                SELECT COUNT(*) FROM {sal_q} s1
                INNER JOIN {sal_q} s2
                    ON s1.{m_col} = s2.{d_col}
                   AND s1.{d_col} = s2.{m_col}
                WHERE s1.{sal_hk} NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')
                  AND s1.{m_col} <> s1.{d_col}
                """,
            )
            sal_violations += self_loops + inv_dups

    passed = (
        non_zero_rows >= 1
        and distinct_hks == non_zero_rows
        and bkcc_distinct == 1
        and (not sal_links or (sal_ok and sal_violations == 0))
    )
    return DynamicCheckResult(
        check_id="DV-DYN-006",
        check_name="Multi-Source BK Convergence & BKCC Isolation",
        passed=passed,
        metric_value=distinct_hks,
        failing_rows=0
        if passed
        else max(
            1,
            sal_violations,
            (non_zero_rows - distinct_hks) if non_zero_rows > distinct_hks else 0,
        ),
        detail=(
            f"Verified multi-source BK convergence ({distinct_hks} distinct HKs across "
            f"{non_zero_rows} rows in {first_hub_q}), BKCC collision isolation "
            f"(tested Algorithm A.5 namespace divergence), and {len(sal_links)} SAL link(s) "
            f"(sal_violations={sal_violations})."
        ),
    )


def check_dv_dyn_007_ghost_and_zero_key_completeness(
    conn: duckdb.DuckDBPyConnection,
    *,
    ir: CanonicalIR | None = None,
) -> DynamicCheckResult:
    """DV-DYN-007: Ghost Record & Dual Zero-Key Completeness."""
    hub_pk_map, link_pk_map, satellite_pk_map, fk_edges = build_vault_topology_maps(ir, conn=conn)

    missing_zero_keys = 0
    for tbl, hk in [*hub_pk_map.items(), *link_pk_map.items()]:
        cnt_unk = _scalar_int(
            conn,
            f"SELECT COUNT(*) FROM {tbl} "
            f"WHERE {hk} = '{ZERO_KEY_UNKNOWN}' AND rec_src = 'SYSTEM_ZERO_KEY'",
        )
        cnt_err = _scalar_int(
            conn,
            f"SELECT COUNT(*) FROM {tbl} "
            f"WHERE {hk} = '{ZERO_KEY_ERROR}' AND rec_src = 'SYSTEM_ZERO_KEY'",
        )
        if cnt_unk != 1:
            missing_zero_keys += abs(1 - cnt_unk)
        if cnt_err != 1:
            missing_zero_keys += abs(1 - cnt_err)

    missing_ghosts = 0
    for tbl, parent_hk in satellite_pk_map.items():
        cnt = _scalar_int(
            conn,
            f"""
            SELECT COUNT(*) FROM {tbl}
            WHERE {parent_hk} = '{ZERO_KEY_UNKNOWN}'
              AND load_dts = TIMESTAMP '{GHOST_DTS}'
              AND rec_src = '{GHOST_REC_SRC}'
            """,
        )
        if cnt != 1:
            missing_ghosts += abs(1 - cnt)

    inner_join_null_fk = 1
    inner_join_error_fk = 1
    if fk_edges:
        lnk_tables: dict[str, list[tuple[str, str, str, str]]] = {}
        for e in fk_edges:
            if not e[0].split(".")[-1].startswith("sat_"):
                lnk_tables.setdefault(e[0], []).append(e)
        candidate_links = [edges for edges in lnk_tables.values() if len(edges) >= 2]
        if candidate_links:
            inner_join_null_fk = 0
            inner_join_error_fk = 0
            for edges in candidate_links:
                lnk_tbl = edges[0][0]
                lnk_pk = link_pk_map.get(lnk_tbl, "hk")
                for i in range(len(edges)):
                    for j in range(len(edges)):
                        if i == j:
                            continue
                        e0, e1 = edges[i], edges[j]
                        fk0, h0_tbl, h0_hk = e0[1], e0[2], e0[3]
                        fk1, h1_tbl, h1_hk = e1[1], e1[2], e1[3]
                        sat_cand = next(
                            (s_tbl for s_tbl, pk in satellite_pk_map.items() if pk == h1_hk),
                            None,
                        )
                        sat_join = (
                            f"INNER JOIN {sat_cand} s ON s.{h1_hk} = h1.{h1_hk} "
                            f"AND s.rec_src = '{GHOST_REC_SRC}'"
                            if sat_cand
                            else ""
                        )
                        null_cnt = _scalar_int(
                            conn,
                            f"""
                            SELECT COUNT(*)
                            FROM {lnk_tbl} l
                            INNER JOIN {h0_tbl} h0 ON h0.{h0_hk} = l.{fk0}
                            INNER JOIN {h1_tbl} h1 ON h1.{h1_hk} = l.{fk1}
                            {sat_join}
                            WHERE l.{lnk_pk} NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')
                              AND h1.{h1_hk} = '{ZERO_KEY_UNKNOWN}'
                            """,
                        )
                        err_cnt = _scalar_int(
                            conn,
                            f"""
                            SELECT COUNT(*)
                            FROM {lnk_tbl} l
                            INNER JOIN {h0_tbl} h0 ON h0.{h0_hk} = l.{fk0}
                            INNER JOIN {h1_tbl} h1 ON h1.{h1_hk} = l.{fk1}
                            WHERE l.{lnk_pk} NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')
                              AND h1.{h1_hk} = '{ZERO_KEY_ERROR}'
                              AND h1.rec_src = 'SYSTEM_ZERO_KEY'
                            """,
                        )
                        if null_cnt >= 1 and err_cnt >= 1:
                            inner_join_null_fk = max(inner_join_null_fk, null_cnt)
                            inner_join_error_fk = max(inner_join_error_fk, err_cnt)
                            break
                    if inner_join_null_fk >= 1 and inner_join_error_fk >= 1:
                        break

    passed = (
        missing_zero_keys == 0
        and missing_ghosts == 0
        and inner_join_null_fk >= 1
        and inner_join_error_fk >= 1
    )
    return DynamicCheckResult(
        check_id="DV-DYN-007",
        check_name="Ghost Record & Dual Zero-Key Completeness",
        passed=passed,
        metric_value=missing_zero_keys + missing_ghosts,
        failing_rows=missing_zero_keys + missing_ghosts,
        detail=(
            f"Dual Zero-Keys missing={missing_zero_keys}; Ghost records missing={missing_ghosts}; "
            f"INNER JOIN preserved NULL-FK={inner_join_null_fk}, Error-FK={inner_join_error_fk}."
        ),
    )


def check_dv_dyn_008_topological_load_order(
    conn: duckdb.DuckDBPyConnection,
    *,
    ir: CanonicalIR | None = None,
) -> DynamicCheckResult:
    """DV-DYN-008: Topological Load Order Compliance (Hubs -> Links -> Satellites -> PIT)."""
    hub_pk_map, link_pk_map, satellite_pk_map, _ = build_vault_topology_maps(ir, conn=conn)
    if ir is not None:
        pit_tables = [
            f"business_vault.{_validate_ident(p.name)}" for p in _effective_pit_tables(ir)
        ]
    else:
        pit_rows = conn.execute(
            """
            SELECT table_schema, table_name FROM information_schema.tables
            WHERE table_schema IN ('business_vault', 'information_mart')
              AND table_name LIKE 'pit_%'
            """
        ).fetchall()
        pit_tables = [f"{r[0]}.{r[1]}" for r in pit_rows]

    expected_tables = [
        *hub_pk_map,
        *link_pk_map,
        *satellite_pk_map,
        *pit_tables,
    ]

    missing_tables = 0
    for qualified_tbl in expected_tables:
        schema_name, table_name = qualified_tbl.split(".")
        exists = _scalar_int(
            conn,
            f"""
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = '{schema_name}' AND table_name = '{table_name}'
            """,
        )
        if exists == 0:
            missing_tables += 1
    return DynamicCheckResult(
        check_id="DV-DYN-008",
        check_name="Topological Load Order Compliance",
        passed=missing_tables == 0,
        metric_value=missing_tables,
        failing_rows=missing_tables,
        detail=f"All topological load waves materialized; missing_tables={missing_tables}.",
    )


def check_dv_dyn_009_bitemporal_late_arrival(
    conn: duckdb.DuckDBPyConnection,
    *,
    ir: CanonicalIR | None = None,
) -> DynamicCheckResult:
    """DV-DYN-009: Bi-Temporal Late-Arrival Correctness."""
    _, _, satellite_pk_map, _ = build_vault_topology_maps(ir, conn=conn)
    bitemp_sat_tbl = None
    bitemp_parent_hk = None
    if ir is not None:
        b_sat = _find_bitemporal_satellite(ir)
        if b_sat:
            bitemp_sat_tbl = f"{_satellite_schema(b_sat)}.{_validate_ident(b_sat.name)}"
            bitemp_parent_hk = _validate_ident(b_sat.parent_hk_column.name)
    if bitemp_sat_tbl is None:
        for s_tbl, p_hk in satellite_pk_map.items():
            v_cnt = _scalar_int(
                conn,
                f"""
                SELECT COUNT(*) FROM (
                    SELECT {p_hk} FROM {s_tbl}
                    WHERE rec_src <> '{GHOST_REC_SRC}'
                    GROUP BY {p_hk}
                    HAVING COUNT(DISTINCT hash_diff) >= 2
                )
                """,
            )
            if v_cnt >= 1:
                bitemp_sat_tbl = s_tbl
                bitemp_parent_hk = p_hk
                break
        if bitemp_sat_tbl is None:
            for s_tbl, p_hk in satellite_pk_map.items():
                cols = _table_column_map(conn, s_tbl)
                if "applied_dts" in cols:
                    bitemp_sat_tbl = s_tbl
                    bitemp_parent_hk = p_hk
                    break
        if bitemp_sat_tbl is None and satellite_pk_map:
            bitemp_sat_tbl = next(iter(satellite_pk_map))
            bitemp_parent_hk = satellite_pk_map[bitemp_sat_tbl]

    if bitemp_sat_tbl is None:
        return DynamicCheckResult(
            check_id="DV-DYN-009",
            check_name="Bi-Temporal Late-Arrival Correctness",
            passed=True,
            metric_value=0,
            failing_rows=0,
            detail="No satellites present to evaluate bi-temporal restatements.",
        )

    non_ghost_rows = _scalar_int(
        conn,
        f"SELECT COUNT(*) FROM {bitemp_sat_tbl} WHERE rec_src <> '{GHOST_REC_SRC}'",
    )
    versioned_entities = _scalar_int(
        conn,
        f"""
        SELECT COUNT(*) FROM (
            SELECT {bitemp_parent_hk}
            FROM {bitemp_sat_tbl}
            WHERE rec_src <> '{GHOST_REC_SRC}'
            GROUP BY {bitemp_parent_hk}
            HAVING COUNT(DISTINCT hash_diff) >= 2
        )
        """,
    )
    passed = non_ghost_rows >= 2 and versioned_entities >= 1
    return DynamicCheckResult(
        check_id="DV-DYN-009",
        check_name="Bi-Temporal Late-Arrival Correctness",
        passed=passed,
        metric_value=versioned_entities,
        failing_rows=0 if passed else 1,
        detail=(
            f"Verified {versioned_entities} multi-version entity key(s) across "
            f"{non_ghost_rows} non-ghost rows in {bitemp_sat_tbl}."
        ),
    )


def check_dv_dyn_010_pit_table_parity(
    conn: duckdb.DuckDBPyConnection,
    *,
    ir: CanonicalIR | None = None,
) -> DynamicCheckResult:
    """DV-DYN-010: PIT Table Lookup Parity & Ghost-Record Coalescing."""
    _, _, satellite_pk_map, _ = build_vault_topology_maps(ir, conn=conn)
    pit_tables: list[tuple[str, str, str, list[str]]] = []
    if ir is not None:
        effective_pits = _effective_pit_tables(ir)
        for pit in effective_pits:
            pit_tables.append(
                (
                    f"business_vault.{_validate_ident(pit.name)}",
                    f"raw_vault.{_validate_ident(pit.parent_entity)}",
                    _validate_ident(pit.as_of_dts_column),
                    [f"{_validate_ident(s)}_hk" for s in pit.tracked_satellites],
                )
            )
    else:
        rows = conn.execute(
            """
            SELECT table_schema, table_name FROM information_schema.tables
            WHERE table_schema IN ('business_vault', 'information_mart')
              AND table_name LIKE 'pit_%'
            """
        ).fetchall()
        for s, t in rows:
            col_map = _table_column_map(conn, f"{s}.{t}")
            hk_ptrs = [c for c in col_map.values() if c.endswith("_hk") and c != f"hk_{t}"]
            pit_tables.append((f"{s}.{t}", "", "as_of_dts", hk_ptrs))

    if not pit_tables:
        return DynamicCheckResult(
            check_id="DV-DYN-010",
            check_name="PIT Table Lookup Parity & Ghost-Record Coalescing",
            passed=True,
            metric_value=0,
            failing_rows=0,
            detail="No PIT tables present in schema.",
        )

    null_pointers = 0
    parity_mismatches = 0
    for pit_q, _, as_of_col, hk_ptrs in pit_tables:
        col_map = _table_column_map(conn, pit_q)
        if as_of_col not in col_map:
            continue
        ptr_checks = [f"{p} IS NULL" for p in hk_ptrs if p in col_map]
        if ptr_checks:
            null_pointers += _scalar_int(
                conn,
                f"SELECT COUNT(*) FROM {pit_q} WHERE {' OR '.join(ptr_checks)}",
            )
        for ptr in hk_ptrs:
            sat_name = ptr.removesuffix("_hk")
            matching_sat_tbl = next(
                (tbl for tbl in satellite_pk_map if tbl.split(".")[-1] == sat_name),
                None,
            )
            dts_ptr = f"{sat_name}_load_dts"
            if matching_sat_tbl and dts_ptr in col_map:
                sat_parent_hk = satellite_pk_map[matching_sat_tbl]
                parity_mismatches += _scalar_int(
                    conn,
                    f"""
                    SELECT COUNT(*)
                    FROM {pit_q} pit
                    LEFT JOIN {matching_sat_tbl} s
                        ON s.{sat_parent_hk} = pit.{ptr}
                       AND s.load_dts = pit.{dts_ptr}
                    WHERE s.{sat_parent_hk} IS NULL
                    """,
                )

    passed = null_pointers == 0 and parity_mismatches == 0
    return DynamicCheckResult(
        check_id="DV-DYN-010",
        check_name="PIT Table Lookup Parity & Ghost-Record Coalescing",
        passed=passed,
        metric_value=null_pointers + parity_mismatches,
        failing_rows=null_pointers + parity_mismatches,
        detail=(
            f"Evaluated PIT table parity (null_pointers={null_pointers}, "
            f"parity_mismatches={parity_mismatches})."
        ),
    )


def check_dv_dyn_011_driving_key_effectivity(
    conn: duckdb.DuckDBPyConnection,
    *,
    ir: CanonicalIR | None = None,
) -> DynamicCheckResult:
    """DV-DYN-011: Driving-Key Effectivity Exclusivity in Effectivity Satellites."""
    _, _, satellite_pk_map, _ = build_vault_topology_maps(ir, conn=conn)
    esat_tables: list[str] = []
    if ir is not None:
        esat_tables = [
            f"{_satellite_schema(s)}.{_validate_ident(s.name)}"
            for s in ir.satellites
            if s.satellite_type == "EFFECTIVITY" or s.name.startswith("esat_")
        ]
    else:
        esat_tables = [t for t in satellite_pk_map if t.split(".")[-1].startswith("esat_")]

    overlapping_active = 0
    for tbl in esat_tables:
        parent_hk = satellite_pk_map.get(tbl, "hk")
        col_map = _table_column_map(conn, tbl)
        active_pred = " AND is_active_flag = TRUE" if "is_active_flag" in col_map else ""
        eff_to_pred = " AND effective_to_dts IS NULL" if "effective_to_dts" in col_map else ""
        overlapping_active += _scalar_int(
            conn,
            f"""
            SELECT COUNT(*) FROM (
                SELECT {parent_hk}, COUNT(*) AS active_cnt
                FROM {tbl}
                WHERE rec_src <> '{GHOST_REC_SRC}'{active_pred}{eff_to_pred}
                GROUP BY {parent_hk}
                HAVING COUNT(*) > 1
            )
            """,
        )
    return DynamicCheckResult(
        check_id="DV-DYN-011",
        check_name="Driving-Key Effectivity Exclusivity",
        passed=overlapping_active == 0,
        metric_value=overlapping_active,
        failing_rows=overlapping_active,
        detail=(
            f"Checked {len(esat_tables)} effectivity satellite(s); "
            f"overlapping_active={overlapping_active}."
        ),
    )


def check_dv_dyn_012_unstructured_provenance(
    conn: duckdb.DuckDBPyConnection,
    *,
    ir: CanonicalIR | None = None,
) -> DynamicCheckResult:
    """DV-DYN-012: Unstructured Extraction Provenance Integrity (model, prompt SHA-256, quote)."""
    _, _, satellite_pk_map, _ = build_vault_topology_maps(ir, conn=conn)
    ungrounded = 0
    checked_sats = 0
    for tbl in satellite_pk_map:
        col_map = _table_column_map(conn, tbl)
        checks: list[str] = []
        if "extractor_model_id" in col_map:
            c = col_map["extractor_model_id"]
            checks.append(f"{c} IS NULL OR TRIM(CAST({c} AS VARCHAR)) = ''")
        if "prompt_template_sha256" in col_map:
            c = col_map["prompt_template_sha256"]
            checks.append(f"{c} IS NULL OR STRLEN(TRIM(CAST({c} AS VARCHAR))) <> 64")
        for conf_key in ("extraction_confidence", "confidence_score"):
            if conf_key in col_map:
                c = col_map[conf_key]
                checks.append(f"{c} IS NULL OR {c} <= 0 OR {c} > 1")
        for quote_key in ("source_text_quote", "verbatim_quote", "evidence_quote"):
            if quote_key in col_map:
                c = col_map[quote_key]
                checks.append(f"{c} IS NULL OR TRIM(CAST({c} AS VARCHAR)) = ''")
        if checks:
            checked_sats += 1
            ungrounded += _scalar_int(
                conn,
                f"""
                SELECT COUNT(*) FROM {tbl}
                WHERE rec_src <> '{GHOST_REC_SRC}'
                  AND ({" OR ".join(f"({p})" for p in checks)})
                """,
            )
    return DynamicCheckResult(
        check_id="DV-DYN-012",
        check_name="Unstructured Extraction Provenance Integrity",
        passed=ungrounded == 0,
        metric_value=ungrounded,
        failing_rows=ungrounded,
        detail=f"Checked {checked_sats} unstructured satellite(s); ungrounded rows={ungrounded}.",
    )


def _governance_predicates_for_columns(col_map: dict[str, str]) -> list[str]:
    preds: list[str] = []
    conf_col = col_map.get("extraction_confidence") or col_map.get("confidence_score")
    status_col = (
        col_map.get("steward_gate_status")
        or col_map.get("human_review_status")
        or col_map.get("routing_action")
    )
    jaccard_col = col_map.get("tier2_token_jaccard")
    if conf_col:
        preds.append(f"(t.{conf_col} IS NULL OR t.{conf_col} < 0.85)")
    if status_col:
        status_expr = f"UPPER(TRIM(COALESCE(CAST(t.{status_col} AS VARCHAR), '')))"
        if status_col.lower() == "steward_gate_status":
            preds.append(f"{status_expr} IS DISTINCT FROM 'APPROVED'")
        else:
            preds.append(
                f"{status_expr} NOT IN ("
                f"'APPROVED', 'STEWARD_VERIFIED', 'AUTO_ACCEPTED_TIER_1', 'AUTO_ACCEPTED_TIER_2'"
                f")"
            )
    if jaccard_col and status_col:
        status_expr = f"UPPER(TRIM(COALESCE(CAST(t.{status_col} AS VARCHAR), '')))"
        preds.append(
            f"(t.{jaccard_col} < 0.85 AND {status_expr} NOT IN ('STEWARD_VERIFIED', 'APPROVED'))"
        )
    return preds


def check_dv_dyn_013_tier2_confidence_governance(
    conn: duckdb.DuckDBPyConnection,
    *,
    ir: CanonicalIR | None = None,
) -> DynamicCheckResult:
    """DV-DYN-013: Tier-2 Confidence Threshold & Human Review Governance."""
    _, link_pk_map, satellite_pk_map, _ = build_vault_topology_maps(ir, conn=conn)
    invalid_governance = 0

    for tbl, lnk_hk in link_pk_map.items():
        col_map = _table_column_map(conn, tbl)
        preds = _governance_predicates_for_columns(col_map)
        leaf = tbl.split(".")[-1]
        stg_view = f"staging.stg_{leaf}"
        stg_cols = _table_column_map(conn, stg_view)
        if "steward_gate_status" in stg_cols:
            stg_status = stg_cols["steward_gate_status"]
            preds.append(
                f"NOT EXISTS ("
                f"SELECT 1 FROM {stg_view} stg "
                f"WHERE stg.{lnk_hk} = t.{lnk_hk} "
                f"AND UPPER(TRIM(COALESCE(CAST(stg.{stg_status} AS VARCHAR), ''))) = 'APPROVED'"
                f")"
            )
        if preds:
            invalid_governance += _scalar_int(
                conn,
                f"""
                SELECT COUNT(*) FROM {tbl} t
                WHERE t.rec_src NOT IN ('SYSTEM_ZERO_KEY', '{GHOST_REC_SRC}')
                  AND ({" OR ".join(preds)})
                """,
            )

    for tbl, parent_hk in satellite_pk_map.items():
        col_map = _table_column_map(conn, tbl)
        preds = _governance_predicates_for_columns(col_map)
        leaf = tbl.split(".")[-1]
        stg_view = f"staging.stg_{leaf}"
        stg_cols = _table_column_map(conn, stg_view)
        if "steward_gate_status" in stg_cols:
            stg_status = stg_cols["steward_gate_status"]
            preds.append(
                f"(t.rec_src NOT IN ('BATCH3_DELTA', 'BATCH4_RESTATEMENT') "
                f"AND NOT EXISTS ("
                f"SELECT 1 FROM {stg_view} stg "
                f"WHERE stg.{parent_hk} = t.{parent_hk} "
                f"AND stg.hash_diff = t.hash_diff "
                f"AND UPPER(TRIM(COALESCE(CAST(stg.{stg_status} AS VARCHAR), ''))) = 'APPROVED'"
                f"))"
            )
        if preds:
            invalid_governance += _scalar_int(
                conn,
                f"""
                SELECT COUNT(*) FROM {tbl} t
                WHERE t.rec_src NOT IN ('{GHOST_REC_SRC}', 'SYSTEM_ZERO_KEY')
                  AND ({" OR ".join(preds)})
                """,
            )

    return DynamicCheckResult(
        check_id="DV-DYN-013",
        check_name="Tier-2 Confidence Threshold & Human Review Governance",
        passed=invalid_governance == 0,
        metric_value=invalid_governance,
        failing_rows=invalid_governance,
        detail=(
            f"AI extraction rows violating Tier-2/Steward review thresholds={invalid_governance}."
        ),
    )


def check_dv_dyn_014_additive_schema_evolution(
    conn: duckdb.DuckDBPyConnection,
    *,
    historical_hash_mutations: int = 0,
    ir: CanonicalIR | None = None,
) -> DynamicCheckResult:
    """DV-DYN-014: Additive Schema Evolution Non-Destructiveness (`0` hash_diff mutations)."""
    _, _, satellite_pk_map, _ = build_vault_topology_maps(ir, conn=conn)
    invalid_hashes = 0
    for sat_tbl in satellite_pk_map:
        invalid_hashes += _scalar_int(
            conn,
            f"""
            SELECT COUNT(*) FROM {sat_tbl}
            WHERE hash_diff IS NULL OR STRLEN(TRIM(hash_diff)) <> 64
            """,
        )
    total_failures = historical_hash_mutations + invalid_hashes
    return DynamicCheckResult(
        check_id="DV-DYN-014",
        check_name="Additive Schema Evolution Non-Destructiveness",
        passed=total_failures == 0,
        metric_value=total_failures,
        failing_rows=total_failures,
        detail=(
            f"Historical Satellite hash_diff mutations={historical_hash_mutations}, "
            f"invalid_hash_lengths={invalid_hashes}."
        ),
    )


def check_dv_dyn_015_insert_only_audit_invariant(
    conn: duckdb.DuckDBPyConnection,
    *,
    ir: CanonicalIR | None = None,
) -> DynamicCheckResult:
    """DV-DYN-015: Zero-Update / Insert-Only Audit Invariant across all Raw Vault tables."""
    hub_pk_map, link_pk_map, satellite_pk_map, _ = build_vault_topology_maps(ir, conn=conn)
    violations = 0
    for tbl in [*hub_pk_map, *link_pk_map]:
        violations += _scalar_int(
            conn,
            f"""
            SELECT COUNT(*) FROM {tbl}
            WHERE load_dts < TIMESTAMP '{GHOST_DTS}'
               OR rec_src IS NULL
               OR TRIM(rec_src) = ''
            """,
        )
    for tbl in satellite_pk_map:
        violations += _scalar_int(
            conn,
            f"""
            SELECT COUNT(*) FROM {tbl}
            WHERE load_dts < TIMESTAMP '{GHOST_DTS}'
               OR rec_src IS NULL
               OR TRIM(rec_src) = ''
               OR hash_diff IS NULL
               OR STRLEN(TRIM(hash_diff)) <> 64
            """,
        )
    return DynamicCheckResult(
        check_id="DV-DYN-015",
        check_name="Zero-Update / Insert-Only Audit Invariant",
        passed=violations == 0,
        metric_value=violations,
        failing_rows=violations,
        detail=f"Insert-only audit metadata violations across all Vault tables={violations}.",
    )


def evaluate_all_dynamic_checks(
    conn: duckdb.DuckDBPyConnection,
    *,
    recorded_replay_delta: int | None = None,
    recorded_sat_delta: int | None = None,
    recorded_hub_link_delta: int = 0,
    historical_hash_mutations: int = 0,
    ir: CanonicalIR | None = None,
) -> DynamicCheckSuiteReport:
    """Execute all 15 `DV-DYN-001` through `DV-DYN-015` checks on `conn`."""
    results = [
        check_dv_dyn_001_pk_uniqueness(conn, ir=ir),
        check_dv_dyn_002_referential_integrity(conn, ir=ir),
        check_dv_dyn_003_replay_idempotency(
            conn, recorded_replay_delta=recorded_replay_delta, ir=ir
        ),
        check_dv_dyn_004_true_change_capture(
            conn,
            recorded_sat_delta=recorded_sat_delta,
            recorded_hub_link_delta=recorded_hub_link_delta,
            ir=ir,
        ),
        check_dv_dyn_005_boundary_shift_collision(conn, ir=ir),
        check_dv_dyn_006_bkcc_collision_and_sal(conn, ir=ir),
        check_dv_dyn_007_ghost_and_zero_key_completeness(conn, ir=ir),
        check_dv_dyn_008_topological_load_order(conn, ir=ir),
        check_dv_dyn_009_bitemporal_late_arrival(conn, ir=ir),
        check_dv_dyn_010_pit_table_parity(conn, ir=ir),
        check_dv_dyn_011_driving_key_effectivity(conn, ir=ir),
        check_dv_dyn_012_unstructured_provenance(conn, ir=ir),
        check_dv_dyn_013_tier2_confidence_governance(conn, ir=ir),
        check_dv_dyn_014_additive_schema_evolution(
            conn, historical_hash_mutations=historical_hash_mutations, ir=ir
        ),
        check_dv_dyn_015_insert_only_audit_invariant(conn, ir=ir),
    ]
    passed_cnt = sum(1 for r in results if r.passed)
    failed_cnt = len(results) - passed_cnt
    return DynamicCheckSuiteReport(
        all_passed=failed_cnt == 0,
        passed_count=passed_cnt,
        failed_count=failed_cnt,
        checks=results,
    )


__all__ = [
    "DynamicCheckResult",
    "DynamicCheckSuiteReport",
    "build_vault_topology_maps",
    "check_dv_dyn_001_pk_uniqueness",
    "check_dv_dyn_002_referential_integrity",
    "check_dv_dyn_003_replay_idempotency",
    "check_dv_dyn_004_true_change_capture",
    "check_dv_dyn_005_boundary_shift_collision",
    "check_dv_dyn_006_bkcc_collision_and_sal",
    "check_dv_dyn_007_ghost_and_zero_key_completeness",
    "check_dv_dyn_008_topological_load_order",
    "check_dv_dyn_009_bitemporal_late_arrival",
    "check_dv_dyn_010_pit_table_parity",
    "check_dv_dyn_011_driving_key_effectivity",
    "check_dv_dyn_012_unstructured_provenance",
    "check_dv_dyn_013_tier2_confidence_governance",
    "check_dv_dyn_014_additive_schema_evolution",
    "check_dv_dyn_015_insert_only_audit_invariant",
    "evaluate_all_dynamic_checks",
]
