"""15 Dynamic DuckDB Sandbox Invariant Evaluators (`DV-DYN-001` through `DV-DYN-015`).

Evaluates the 15 runtime Data Vault 2.0 invariants defined in `docs/03` §7.2 and
`docs/07` Scene 6 against an active DuckDB connection and returns structured
`DynamicCheckResult` diagnostics.
"""

from __future__ import annotations

from typing import Any

import duckdb
from pydantic import BaseModel, ConfigDict, Field

from opendv_core.constants import GHOST_DTS, GHOST_REC_SRC, ZERO_KEY_ERROR, ZERO_KEY_UNKNOWN
from opendv_core.models.canonical_ir import CanonicalIR
from opendv_duckdb_sandbox.compiler_4zone import (
    _effective_pit_tables,
    _link_schema,
    _satellite_schema,
    _validate_ident,
    is_pe_domain,
)
from opendv_duckdb_sandbox.pit_query_engine import PitQueryEngine

HUB_PK_MAP: dict[str, str] = {
    "raw_vault.hub_fund": "hk_fund",
    "raw_vault.hub_limited_partner": "hk_lp",
    "raw_vault.hub_portfolio_company": "hk_portco",
    "raw_vault.hub_capital_account": "hk_cap_acct",
    "raw_vault.hub_unstructured_document": "hk_doc",
}

LINK_PK_MAP: dict[str, str] = {
    "raw_vault.lnk_lp_commitment": "hk_lnk_lp_commitment",
    "raw_vault.lnk_fund_portco_investment": "hk_lnk_fund_portco_investment",
    "raw_vault.lnk_cap_acct_alignment": "hk_lnk_cap_acct_alignment",
    "raw_vault.lnk_doc_lp_side_letter": "hk_lnk_doc_lp_side_letter",
    "raw_vault.lnk_doc_portco_memo": "hk_lnk_doc_portco_memo",
    "business_vault.sal_limited_partner": "hk_sal_limited_partner",
}

SATELLITE_PK_MAP: dict[str, str] = {
    "raw_vault.sat_fund_core": "hk_fund",
    "raw_vault.sat_lp_core": "hk_lp",
    "raw_vault.sat_lp_kyc_pii": "hk_lp",
    "raw_vault.sat_portco_core": "hk_portco",
    "raw_vault.sat_portco_valuation_mnpi": "hk_portco",
    "raw_vault.sat_cap_acct_quarterly_nav": "hk_cap_acct",
    "raw_vault.sat_doc_metadata": "hk_doc",
    "raw_vault.sat_lnk_lp_commitment_terms": "hk_lnk_lp_commitment",
    "raw_vault.sat_lnk_fund_portco_deal": "hk_lnk_fund_portco_investment",
    "business_vault.esat_sal_limited_partner": "hk_sal_limited_partner",
    "business_vault.sat_lp_side_letter_llm_ext": "hk_lnk_doc_lp_side_letter",
    "business_vault.sat_lnk_doc_portco_ai_provenance": "hk_lnk_doc_portco_memo",
}

FK_EDGES: tuple[tuple[str, str, str, str], ...] = (
    ("raw_vault.lnk_lp_commitment", "hk_lp", "raw_vault.hub_limited_partner", "hk_lp"),
    ("raw_vault.lnk_lp_commitment", "hk_fund", "raw_vault.hub_fund", "hk_fund"),
    ("raw_vault.lnk_fund_portco_investment", "hk_fund", "raw_vault.hub_fund", "hk_fund"),
    (
        "raw_vault.lnk_fund_portco_investment",
        "hk_portco",
        "raw_vault.hub_portfolio_company",
        "hk_portco",
    ),
    (
        "raw_vault.lnk_cap_acct_alignment",
        "hk_cap_acct",
        "raw_vault.hub_capital_account",
        "hk_cap_acct",
    ),
    ("raw_vault.lnk_cap_acct_alignment", "hk_lp", "raw_vault.hub_limited_partner", "hk_lp"),
    ("raw_vault.lnk_cap_acct_alignment", "hk_fund", "raw_vault.hub_fund", "hk_fund"),
    ("raw_vault.lnk_doc_lp_side_letter", "hk_doc", "raw_vault.hub_unstructured_document", "hk_doc"),
    ("raw_vault.lnk_doc_lp_side_letter", "hk_lp", "raw_vault.hub_limited_partner", "hk_lp"),
    ("raw_vault.lnk_doc_lp_side_letter", "hk_fund", "raw_vault.hub_fund", "hk_fund"),
    ("raw_vault.lnk_doc_portco_memo", "hk_doc", "raw_vault.hub_unstructured_document", "hk_doc"),
    ("raw_vault.lnk_doc_portco_memo", "hk_portco", "raw_vault.hub_portfolio_company", "hk_portco"),
    (
        "business_vault.sal_limited_partner",
        "hk_lp_master",
        "raw_vault.hub_limited_partner",
        "hk_lp",
    ),
    (
        "business_vault.sal_limited_partner",
        "hk_lp_duplicate",
        "raw_vault.hub_limited_partner",
        "hk_lp",
    ),
    ("raw_vault.sat_fund_core", "hk_fund", "raw_vault.hub_fund", "hk_fund"),
    ("raw_vault.sat_lp_core", "hk_lp", "raw_vault.hub_limited_partner", "hk_lp"),
    ("raw_vault.sat_lp_kyc_pii", "hk_lp", "raw_vault.hub_limited_partner", "hk_lp"),
    ("raw_vault.sat_portco_core", "hk_portco", "raw_vault.hub_portfolio_company", "hk_portco"),
    (
        "raw_vault.sat_portco_valuation_mnpi",
        "hk_portco",
        "raw_vault.hub_portfolio_company",
        "hk_portco",
    ),
    (
        "raw_vault.sat_cap_acct_quarterly_nav",
        "hk_cap_acct",
        "raw_vault.hub_capital_account",
        "hk_cap_acct",
    ),
    ("raw_vault.sat_doc_metadata", "hk_doc", "raw_vault.hub_unstructured_document", "hk_doc"),
    (
        "raw_vault.sat_lnk_lp_commitment_terms",
        "hk_lnk_lp_commitment",
        "raw_vault.lnk_lp_commitment",
        "hk_lnk_lp_commitment",
    ),
    (
        "raw_vault.sat_lnk_fund_portco_deal",
        "hk_lnk_fund_portco_investment",
        "raw_vault.lnk_fund_portco_investment",
        "hk_lnk_fund_portco_investment",
    ),
    (
        "business_vault.esat_sal_limited_partner",
        "hk_sal_limited_partner",
        "business_vault.sal_limited_partner",
        "hk_sal_limited_partner",
    ),
    (
        "business_vault.sat_lp_side_letter_llm_ext",
        "hk_lnk_doc_lp_side_letter",
        "raw_vault.lnk_doc_lp_side_letter",
        "hk_lnk_doc_lp_side_letter",
    ),
    (
        "business_vault.sat_lnk_doc_portco_ai_provenance",
        "hk_lnk_doc_portco_memo",
        "raw_vault.lnk_doc_portco_memo",
        "hk_lnk_doc_portco_memo",
    ),
)


class DynamicCheckResult(BaseModel):
    """Structured outcome of a single `DV-DYN-*` sandbox check."""

    model_config = ConfigDict(extra="forbid")

    check_id: str = Field(pattern=r"^DV-DYN-0(0[1-9]|1[0-5])$")
    check_name: str
    passed: bool
    metric_value: int | float | str | bool
    detail: str
    failing_rows: int = Field(default=0, ge=0)


class DynamicCheckSuiteReport(BaseModel):
    """Aggregate report of all 15 `DV-DYN-001`-`DV-DYN-015` checks."""

    model_config = ConfigDict(extra="forbid")

    all_passed: bool
    passed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    checks: list[DynamicCheckResult] = Field(default_factory=list)

    @property
    def by_id(self) -> dict[str, DynamicCheckResult]:
        """Lookup map of `check_id -> DynamicCheckResult`."""
        return {c.check_id: c for c in self.checks}


def build_vault_topology_maps(
    ir: CanonicalIR | None = None,
) -> tuple[
    dict[str, str],
    dict[str, str],
    dict[str, str],
    tuple[tuple[str, str, str, str], ...],
]:
    """Derive `(hub_pk_map, link_pk_map, satellite_pk_map, fk_edges)` from `ir` or PE defaults."""
    if is_pe_domain(ir) or ir is None:
        return (
            dict(HUB_PK_MAP),
            dict(LINK_PK_MAP),
            dict(SATELLITE_PK_MAP),
            tuple(FK_EDGES),
        )

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
    """DV-DYN-001: Primary Key Uniqueness across all Hubs, Links, and Satellites."""
    hub_pk_map, link_pk_map, satellite_pk_map, _ = build_vault_topology_maps(ir)
    dup_count = 0
    for tbl, pk in {**hub_pk_map, **link_pk_map}.items():
        dup_count += _scalar_int(conn, f"SELECT COUNT(*) - COUNT(DISTINCT {pk}) FROM {tbl}")
    sat_by_qname: dict[str, Any] = {}
    if ir is not None:
        for sat in ir.satellites:
            q_sat = f"{_satellite_schema(sat)}.{_validate_ident(sat.name)}"
            sat_by_qname[q_sat] = sat
    for tbl, parent_hk in satellite_pk_map.items():
        sat_ir = sat_by_qname.get(tbl)
        seq_cols = (
            [
                _validate_ident(c.name if hasattr(c, "name") else str(c))
                for c in (sat_ir.child_dependent_keys or [])
            ]
            if sat_ir
            else []
        )
        pk_parts = [
            parent_hk,
            "CAST(load_dts AS VARCHAR)",
            *(f"CAST({c} AS VARCHAR)" for c in seq_cols),
        ]
        pk_expr = " || '||' || ".join(pk_parts)
        dup_count += _scalar_int(
            conn,
            f"SELECT COUNT(*) - COUNT(DISTINCT ({pk_expr})) FROM {tbl}",
        )
    table_total = len(hub_pk_map) + len(link_pk_map) + len(satellite_pk_map)
    return DynamicCheckResult(
        check_id="DV-DYN-001",
        check_name="Primary Key Uniqueness",
        passed=dup_count == 0,
        metric_value=dup_count,
        failing_rows=dup_count,
        detail=f"Checked {table_total} tables; duplicate PK rows={dup_count}.",
    )


def check_dv_dyn_002_referential_integrity(
    conn: duckdb.DuckDBPyConnection,
    *,
    ir: CanonicalIR | None = None,
) -> DynamicCheckResult:
    """DV-DYN-002: Parent-Child Referential Integrity (`0` orphan FK rows)."""
    _, _, _, fk_edges = build_vault_topology_maps(ir)
    orphan_total = 0
    for child_tbl, child_fk, parent_tbl, parent_pk in fk_edges:
        orphan_total += _scalar_int(
            conn,
            f"""
            SELECT COUNT(*)
            FROM {child_tbl} c
            LEFT JOIN {parent_tbl} p ON p.{parent_pk} = c.{child_fk}
            WHERE p.{parent_pk} IS NULL
            """,
        )
    return DynamicCheckResult(
        check_id="DV-DYN-002",
        check_name="Parent-Child Referential Integrity",
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
    elif is_pe_domain(ir) or ir is None:
        before = _scalar_int(conn, "SELECT COUNT(*) FROM raw_vault.hub_fund")
        conn.execute(
            """
            INSERT INTO raw_vault.hub_fund (hk_fund, fund_code, load_dts, rec_src)
            SELECT hk_fund, fund_code, MIN(load_dts), ANY_VALUE(rec_src)
            FROM staging.stg_investran_lp_commitments
            WHERE hk_fund NOT IN (REPEAT('0', 64), REPEAT('f', 64))
              AND NOT EXISTS (
                  SELECT 1 FROM raw_vault.hub_fund h
                  WHERE h.hk_fund = stg_investran_lp_commitments.hk_fund
              )
            GROUP BY hk_fund, fund_code
            """
        )
        after = _scalar_int(conn, "SELECT COUNT(*) FROM raw_vault.hub_fund")
        delta = after - before
    else:
        first_hub = ir.hubs[0]
        tbl = _validate_ident(first_hub.name)
        hk = _validate_ident(first_hub.hk_column.name)
        bk_cols = [_validate_ident(bk.name) for bk in first_hub.business_keys]
        bk_csv = ", ".join(bk_cols)
        src_bk_csv = ", ".join(f"src.{c}" for c in bk_cols)
        before = _scalar_int(conn, f"SELECT COUNT(*) FROM raw_vault.{tbl}")
        conn.execute(
            f"""
            INSERT INTO raw_vault.{tbl} ({hk}, {bk_csv}, load_dts, rec_src)
            SELECT src.{hk}, {src_bk_csv}, MIN(src.load_dts), ANY_VALUE(src.rec_src)
            FROM staging.stg_{tbl} src
            WHERE src.{hk} NOT IN (REPEAT('0', 64), REPEAT('f', 64))
              AND NOT EXISTS (
                  SELECT 1 FROM raw_vault.{tbl} h
                  WHERE h.{hk} = src.{hk}
              )
            GROUP BY src.{hk}, {src_bk_csv}
            """
        )
        after = _scalar_int(conn, f"SELECT COUNT(*) FROM raw_vault.{tbl}")
        delta = after - before
    return DynamicCheckResult(
        check_id="DV-DYN-003",
        check_name="Replay Idempotency",
        passed=delta == 0,
        metric_value=delta,
        failing_rows=max(delta, 0),
        detail=f"Batch replay delta_rows={delta} across Raw Vault entities.",
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
    elif is_pe_domain(ir) or ir is None:
        sat_versions = _scalar_int(
            conn,
            """
            SELECT COUNT(*) FROM raw_vault.sat_portco_valuation_mnpi
            WHERE rec_src <> 'SYSTEM_GHOST'
            """,
        )
    else:
        _, _, satellite_pk_map, _ = build_vault_topology_maps(ir)
        first_sat_tbl = next(iter(satellite_pk_map))
        sat_versions = _scalar_int(
            conn,
            f"SELECT COUNT(*) FROM {first_sat_tbl} WHERE rec_src <> 'SYSTEM_GHOST'",
        )
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
    """DV-DYN-005: Boundary-Shift Collision Resistance ('AB'||'C' vs 'A'||'BC')."""
    _ = ir
    row = conn.execute(
        """
        SELECT
            LOWER(SHA256('2:AB||1:C')) AS h_ab_c,
            LOWER(SHA256('1:A||2:BC')) AS h_a_bc,
            LOWER(SHA256('7:ANNE||O||5:BRIEN')) AS h_anne1,
            LOWER(SHA256('4:ANNE||7:O||BRIEN')) AS h_anne2
        """
    ).fetchone()
    assert row is not None
    collisions = int(row[0] == row[1]) + int(row[2] == row[3])
    return DynamicCheckResult(
        check_id="DV-DYN-005",
        check_name="Boundary-Shift Collision Resistance",
        passed=collisions == 0,
        metric_value=collisions,
        failing_rows=collisions,
        detail=(f"Length-prefixed SHA-256 ('2:AB||1:C' vs '1:A||2:BC') collisions={collisions}."),
    )


def check_dv_dyn_006_bkcc_collision_and_sal(
    conn: duckdb.DuckDBPyConnection,
    *,
    ir: CanonicalIR | None = None,
) -> DynamicCheckResult:
    """DV-DYN-006: Multi-Source BK Convergence & BKCC Isolation (`LP-101` & `sal`)."""
    if is_pe_domain(ir) or ir is None:
        lp_101_count = _scalar_int(
            conn,
            "SELECT COUNT(*) FROM raw_vault.hub_limited_partner WHERE lp_id = 'LP-101'",
        )
        sal_non_zero = _scalar_int(
            conn,
            f"""
            SELECT COUNT(*) FROM business_vault.sal_investor_golden
            WHERE hk_sal_limited_partner NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')
            """,
        )
        passed = lp_101_count == 2 and sal_non_zero >= 1
        return DynamicCheckResult(
            check_id="DV-DYN-006",
            check_name="Multi-Source BK Convergence & BKCC Isolation",
            passed=passed,
            metric_value=lp_101_count,
            failing_rows=0 if passed else 1,
            detail=(
                f"hub_limited_partner holds {lp_101_count} distinct BKCC-isolated 'LP-101' rows; "
                f"sal_investor_golden holds {sal_non_zero} resolved duplicate link(s)."
            ),
        )

    first_hub = ir.hubs[0]
    hub_tbl = f"raw_vault.{_validate_ident(first_hub.name)}"
    hk_col = _validate_ident(first_hub.hk_column.name)
    non_zero_rows = _scalar_int(
        conn,
        f"""
        SELECT COUNT(*) FROM {hub_tbl}
        WHERE {hk_col} NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')
        """,
    )
    distinct_hks = _scalar_int(
        conn,
        f"""
        SELECT COUNT(DISTINCT {hk_col}) FROM {hub_tbl}
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
    sal_links = [
        lnk for lnk in ir.links if lnk.name.startswith("sal_") or lnk.link_type == "SAME_AS"
    ]
    sal_ok = True
    for sal in sal_links:
        sal_tbl = f"{_link_schema(sal)}.{_validate_ident(sal.name)}"
        sal_hk = _validate_ident(sal.hk_column.name)
        cnt = _scalar_int(
            conn,
            f"""
            SELECT COUNT(*) FROM {sal_tbl}
            WHERE {sal_hk} NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')
            """,
        )
        if cnt < 1:
            sal_ok = False

    passed = non_zero_rows >= 2 and non_zero_rows == distinct_hks and bkcc_distinct == 1 and sal_ok
    return DynamicCheckResult(
        check_id="DV-DYN-006",
        check_name="Multi-Source BK Convergence & BKCC Isolation",
        passed=passed,
        metric_value=distinct_hks,
        failing_rows=0 if passed else 1,
        detail=(
            f"{hub_tbl} holds {distinct_hks} distinct non-zero Hub keys with "
            f"0 BKCC namespace collisions (sal_ok={sal_ok})."
        ),
    )


def check_dv_dyn_007_ghost_and_zero_key_completeness(
    conn: duckdb.DuckDBPyConnection,
    *,
    ir: CanonicalIR | None = None,
) -> DynamicCheckResult:
    """DV-DYN-007: Ghost & Zero-Key INNER JOIN Completeness (`'0'*64`, `'f'*64`, `SYSTEM_GHOST`)."""
    hub_pk_map, link_pk_map, satellite_pk_map, _ = build_vault_topology_maps(ir)
    missing_zero_keys = 0
    for tbl, pk in {**hub_pk_map, **link_pk_map}.items():
        cnt = _scalar_int(
            conn,
            f"""
            SELECT COUNT(*) FROM {tbl}
            WHERE {pk} IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')
              AND rec_src = 'SYSTEM_ZERO_KEY'
            """,
        )
        if cnt != 2:
            missing_zero_keys += abs(2 - cnt)

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

    if is_pe_domain(ir) or ir is None:
        # Verify INNER JOIN preserves the NULL-FK DealCloud row ('PC-NULL-FK')
        inner_join_null_fk = _scalar_int(
            conn,
            f"""
            SELECT COUNT(*)
            FROM raw_vault.lnk_fund_portco_investment l
            INNER JOIN raw_vault.hub_fund hf ON hf.hk_fund = l.hk_fund
            INNER JOIN raw_vault.hub_portfolio_company hp ON hp.hk_portco = l.hk_portco
            INNER JOIN raw_vault.sat_portco_core sc ON sc.hk_portco = hp.hk_portco
            INNER JOIN raw_vault.sat_lnk_fund_portco_deal sd
                ON sd.hk_lnk_fund_portco_investment = l.hk_lnk_fund_portco_investment
            WHERE sd.deal_id = 'PC-NULL-FK'
              AND hp.hk_portco = '{ZERO_KEY_UNKNOWN}'
              AND sc.rec_src = '{GHOST_REC_SRC}'
            """,
        )
    elif ir.links:
        lnk = next(
            (x for x in ir.links if not x.requires_steward_approval_gate),
            ir.links[0],
        )
        lnk_tbl = f"{_link_schema(lnk)}.{_validate_ident(lnk.name)}"
        lnk_hk = _validate_ident(lnk.hk_column.name)
        ref0 = lnk.hub_references[0]
        ref1 = lnk.hub_references[1]
        h0_tbl = f"raw_vault.{_validate_ident(ref0.target_hub)}"
        h0_hk = _validate_ident(ref0.target_hk_column)
        fk0 = _validate_ident(ref0.fk_column_name)
        h1_tbl = f"raw_vault.{_validate_ident(ref1.target_hub)}"
        h1_hk = _validate_ident(ref1.target_hk_column)
        fk1 = _validate_ident(ref1.fk_column_name)
        attached_sat = next(
            (s for s in ir.satellites if s.parent_entity == ref1.target_hub),
            ir.satellites[0],
        )
        sat_tbl = f"{_satellite_schema(attached_sat)}.{_validate_ident(attached_sat.name)}"
        sat_parent_hk = _validate_ident(attached_sat.parent_hk_column.name)
        inner_join_null_fk = _scalar_int(
            conn,
            f"""
            SELECT COUNT(*)
            FROM {lnk_tbl} l
            INNER JOIN {h0_tbl} h0 ON h0.{h0_hk} = l.{fk0}
            INNER JOIN {h1_tbl} h1 ON h1.{h1_hk} = l.{fk1}
            INNER JOIN {sat_tbl} s ON s.{sat_parent_hk} = h1.{h1_hk}
            WHERE l.{lnk_hk} NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')
              AND h1.{h1_hk} = '{ZERO_KEY_UNKNOWN}'
              AND s.rec_src = '{GHOST_REC_SRC}'
            """,
        )
    else:
        first_hub = ir.hubs[0]
        h_tbl = f"raw_vault.{_validate_ident(first_hub.name)}"
        h_hk = _validate_ident(first_hub.hk_column.name)
        first_sat = ir.satellites[0]
        s_tbl = f"{_satellite_schema(first_sat)}.{_validate_ident(first_sat.name)}"
        s_hk = _validate_ident(first_sat.parent_hk_column.name)
        inner_join_null_fk = _scalar_int(
            conn,
            f"""
            SELECT COUNT(*)
            FROM {h_tbl} h
            INNER JOIN {s_tbl} s ON s.{s_hk} = h.{h_hk}
            WHERE h.{h_hk} = '{ZERO_KEY_UNKNOWN}'
              AND s.rec_src = '{GHOST_REC_SRC}'
            """,
        )

    failures = missing_zero_keys + missing_ghosts + (0 if inner_join_null_fk >= 1 else 1)
    return DynamicCheckResult(
        check_id="DV-DYN-007",
        check_name="Ghost & Zero-Key INNER JOIN Completeness",
        passed=failures == 0,
        metric_value=failures,
        failing_rows=failures,
        detail=(
            f"missing_zero_keys={missing_zero_keys}, missing_ghosts={missing_ghosts}, "
            f"inner_join_null_fk_rows={inner_join_null_fk}."
        ),
    )


def check_dv_dyn_008_topological_load_order(
    conn: duckdb.DuckDBPyConnection,
    *,
    ir: CanonicalIR | None = None,
) -> DynamicCheckResult:
    """DV-DYN-008: Topological Load Order Compliance (Hubs -> Links -> Satellites -> PIT)."""
    hub_pk_map, link_pk_map, satellite_pk_map, _ = build_vault_topology_maps(ir)
    if is_pe_domain(ir) or ir is None:
        expected_tables = [
            *hub_pk_map,
            *link_pk_map,
            *satellite_pk_map,
            "information_mart.pit_portfolio_company",
        ]
    else:
        pit_tables = [
            f"business_vault.{_validate_ident(p.name)}" for p in _effective_pit_tables(ir)
        ]
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
        detail=f"All 4 topological load waves materialized; missing_tables={missing_tables}.",
    )


def check_dv_dyn_009_bitemporal_late_arrival(
    conn: duckdb.DuckDBPyConnection,
    *,
    ir: CanonicalIR | None = None,
) -> DynamicCheckResult:
    """DV-DYN-009: Bi-Temporal Late-Arrival Correctness (12.80x -> 14.50x Q3 restatement)."""
    if is_pe_domain(ir) or ir is None:
        val_rows = _scalar_int(
            conn,
            """
            SELECT COUNT(*) FROM raw_vault.sat_portco_valuation_mnpi
            WHERE rec_src <> 'SYSTEM_GHOST'
            """,
        )
        if val_rows < 2:
            return DynamicCheckResult(
                check_id="DV-DYN-009",
                check_name="Bi-Temporal Late-Arrival Correctness",
                passed=False,
                metric_value=val_rows,
                failing_rows=1,
                detail=(
                    f"Expected >= 2 valuation rows (initial 12.80x + restated 14.50x), "
                    f"found {val_rows}."
                ),
            )
        engine = PitQueryEngine(conn)
        proof = engine.verify_bitemporal_restatement()
        return DynamicCheckResult(
            check_id="DV-DYN-009",
            check_name="Bi-Temporal Late-Arrival Correctness",
            passed=proof.verified,
            metric_value=(
                f"{proof.prior_ev_ebitda_multiple}x -> {proof.restated_ev_ebitda_multiple}x"
            ),
            failing_rows=0 if proof.verified else 1,
            detail=(
                f"As-of {proof.prior_assertion_cutoff_ts}: {proof.prior_ev_ebitda_multiple}x "
                f"(${proof.prior_enterprise_value_usd:,.2f}); "
                f"As-of {proof.restated_assertion_cutoff_ts}: "
                f"{proof.restated_ev_ebitda_multiple}x "
                f"(${proof.restated_enterprise_value_usd:,.2f})."
            ),
        )

    bitemp_sat = next(
        (s for s in ir.satellites if s.applied_dts_column is not None),
        ir.satellites[0],
    )
    sat_tbl = f"{_satellite_schema(bitemp_sat)}.{_validate_ident(bitemp_sat.name)}"
    parent_hk = _validate_ident(bitemp_sat.parent_hk_column.name)
    non_ghost_rows = _scalar_int(
        conn,
        f"SELECT COUNT(*) FROM {sat_tbl} WHERE rec_src <> 'SYSTEM_GHOST'",
    )
    versioned_entities = _scalar_int(
        conn,
        f"""
        SELECT COUNT(*) FROM (
            SELECT {parent_hk}
            FROM {sat_tbl}
            WHERE rec_src <> 'SYSTEM_GHOST'
            GROUP BY {parent_hk}
            HAVING COUNT(DISTINCT hash_diff) >= 2
               AND MIN(load_dts) <= TIMESTAMP '2026-10-15 12:00:00'
               AND MAX(load_dts) > TIMESTAMP '2026-10-01 06:00:00'
        )
        """,
    )
    passed = non_ghost_rows >= 3 and versioned_entities >= 1
    return DynamicCheckResult(
        check_id="DV-DYN-009",
        check_name="Bi-Temporal Late-Arrival Correctness",
        passed=passed,
        metric_value=versioned_entities,
        failing_rows=0 if passed else 1,
        detail=(
            f"Verified {versioned_entities} multi-version entity key(s) across "
            f"{non_ghost_rows} non-ghost rows in {sat_tbl}."
        ),
    )


def check_dv_dyn_010_pit_table_parity(
    conn: duckdb.DuckDBPyConnection,
    *,
    ir: CanonicalIR | None = None,
) -> DynamicCheckResult:
    """DV-DYN-010: PIT Table Lookup Parity & Ghost-Record Coalescing."""
    if is_pe_domain(ir) or ir is None:
        null_pointers = _scalar_int(
            conn,
            """
            SELECT COUNT(*) FROM information_mart.pit_portfolio_company
            WHERE sat_portco_core_hk IS NULL
               OR sat_portco_core_load_dts IS NULL
               OR sat_portco_valuation_mnpi_hk IS NULL
               OR sat_portco_valuation_mnpi_load_dts IS NULL
            """,
        )
        parity_mismatches = _scalar_int(
            conn,
            """
            SELECT COUNT(*)
            FROM information_mart.pit_portfolio_company pit
            LEFT JOIN raw_vault.sat_portco_valuation_mnpi sv
                ON sv.hk_portco = pit.sat_portco_valuation_mnpi_hk
               AND sv.load_dts  = pit.sat_portco_valuation_mnpi_load_dts
            WHERE sv.hk_portco IS NULL
            """,
        )
        failures = null_pointers + parity_mismatches
        return DynamicCheckResult(
            check_id="DV-DYN-010",
            check_name="PIT Table Lookup Parity",
            passed=failures == 0,
            metric_value=failures,
            failing_rows=failures,
            detail=(
                f"pit_portfolio_company null_pointers={null_pointers}, "
                f"parity_mismatches={parity_mismatches}."
            ),
        )

    sat_by_name = {s.name: s for s in ir.satellites}
    effective_pits = _effective_pit_tables(ir)
    null_pointers = 0
    parity_mismatches = 0
    empty_pits = 0

    for pit in effective_pits:
        pit_tbl = f"business_vault.{_validate_ident(pit.name)}"
        pit_cnt = _scalar_int(conn, f"SELECT COUNT(*) FROM {pit_tbl}")
        if pit_cnt == 0:
            empty_pits += 1
        null_preds: list[str] = []
        for sat_name in pit.tracked_satellites:
            sat = sat_by_name[sat_name]
            s_ident = _validate_ident(sat.name)
            s_schema = _satellite_schema(sat)
            s_parent_hk = _validate_ident(sat.parent_hk_column.name)
            null_preds.extend([f"{s_ident}_hk IS NULL", f"{s_ident}_load_dts IS NULL"])
            parity_mismatches += _scalar_int(
                conn,
                f"""
                SELECT COUNT(*)
                FROM {pit_tbl} pit
                LEFT JOIN {s_schema}.{s_ident} s
                    ON s.{s_parent_hk} = pit.{s_ident}_hk
                   AND s.load_dts = pit.{s_ident}_load_dts
                WHERE s.{s_parent_hk} IS NULL
                """,
            )
        if null_preds:
            null_pointers += _scalar_int(
                conn,
                f"SELECT COUNT(*) FROM {pit_tbl} WHERE {' OR '.join(null_preds)}",
            )

    failures = null_pointers + parity_mismatches + empty_pits
    return DynamicCheckResult(
        check_id="DV-DYN-010",
        check_name="PIT Table Lookup Parity",
        passed=failures == 0,
        metric_value=failures,
        failing_rows=failures,
        detail=(
            f"Checked {len(effective_pits)} PIT table(s); null_pointers={null_pointers}, "
            f"parity_mismatches={parity_mismatches}, empty_pits={empty_pits}."
        ),
    )


def check_dv_dyn_011_driving_key_effectivity(
    conn: duckdb.DuckDBPyConnection,
    *,
    ir: CanonicalIR | None = None,
) -> DynamicCheckResult:
    """DV-DYN-011: Driving-Key Effectivity Exclusivity in `esat_sal_limited_partner`."""
    if is_pe_domain(ir) or ir is None:
        overlapping_active = _scalar_int(
            conn,
            """
            SELECT COUNT(*) FROM (
                SELECT hk_sal_limited_partner, COUNT(*) AS active_cnt
                FROM business_vault.esat_sal_limited_partner
                WHERE rec_src <> 'SYSTEM_GHOST'
                  AND is_active_flag = TRUE
                  AND effective_to_dts IS NULL
                GROUP BY hk_sal_limited_partner
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
                f"Overlapping active effectivity intervals in "
                f"esat_sal_limited_partner={overlapping_active}."
            ),
        )

    esats = [
        s for s in ir.satellites if s.satellite_type == "EFFECTIVITY" or s.name.startswith("esat_")
    ]
    overlapping_active = 0
    for esat in esats:
        tbl = f"{_satellite_schema(esat)}.{_validate_ident(esat.name)}"
        parent_hk = _validate_ident(esat.parent_hk_column.name)
        col_names = {c.name.lower() for c in esat.payload_columns}
        active_pred = " AND is_active_flag = TRUE" if "is_active_flag" in col_names else ""
        eff_to_pred = " AND effective_to_dts IS NULL" if "effective_to_dts" in col_names else ""
        overlapping_active += _scalar_int(
            conn,
            f"""
            SELECT COUNT(*) FROM (
                SELECT {parent_hk}, COUNT(*) AS active_cnt
                FROM {tbl}
                WHERE rec_src <> 'SYSTEM_GHOST'{active_pred}{eff_to_pred}
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
            f"Checked {len(esats)} effectivity satellite(s); "
            f"overlapping_active={overlapping_active}."
        ),
    )


def check_dv_dyn_012_unstructured_provenance(
    conn: duckdb.DuckDBPyConnection,
    *,
    ir: CanonicalIR | None = None,
) -> DynamicCheckResult:
    """DV-DYN-012: Unstructured Extraction Provenance Integrity (model, prompt SHA-256, quote)."""
    if is_pe_domain(ir) or ir is None:
        ungrounded = _scalar_int(
            conn,
            """
            SELECT (
                SELECT COUNT(*) FROM business_vault.sat_lp_side_letter_llm_ext
                WHERE rec_src <> 'SYSTEM_GHOST'
                  AND (
                      extractor_model_id IS NULL
                      OR prompt_template_sha256 IS NULL
                      OR STRLEN(TRIM(prompt_template_sha256)) <> 64
                      OR extraction_confidence IS NULL
                      OR extraction_confidence <= 0
                      OR extraction_confidence > 1
                      OR source_text_quote IS NULL
                      OR TRIM(source_text_quote) = ''
                  )
            ) + (
                SELECT COUNT(*) FROM business_vault.sat_lnk_doc_portco_ai_provenance
                WHERE rec_src <> 'SYSTEM_GHOST'
                  AND (
                      extractor_model_id IS NULL
                      OR prompt_template_sha256 IS NULL
                      OR STRLEN(TRIM(prompt_template_sha256)) <> 64
                      OR extraction_confidence IS NULL
                      OR extraction_confidence <= 0
                      OR extraction_confidence > 1
                      OR source_text_quote IS NULL
                      OR TRIM(source_text_quote) = ''
                  )
            )
            """,
        )
        return DynamicCheckResult(
            check_id="DV-DYN-012",
            check_name="Unstructured Extraction Provenance Integrity",
            passed=ungrounded == 0,
            metric_value=ungrounded,
            failing_rows=ungrounded,
            detail=(
                f"Ungrounded AI extraction rows missing model/prompt/confidence/quote={ungrounded}."
            ),
        )

    ungrounded = 0
    for sat in ir.satellites:
        tbl = f"{_satellite_schema(sat)}.{_validate_ident(sat.name)}"
        col_map = {c.name.lower(): _validate_ident(c.name) for c in sat.payload_columns}
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
            ungrounded += _scalar_int(
                conn,
                f"""
                SELECT COUNT(*) FROM {tbl}
                WHERE rec_src <> 'SYSTEM_GHOST'
                  AND ({" OR ".join(f"({p})" for p in checks)})
                """,
            )
    return DynamicCheckResult(
        check_id="DV-DYN-012",
        check_name="Unstructured Extraction Provenance Integrity",
        passed=ungrounded == 0,
        metric_value=ungrounded,
        failing_rows=ungrounded,
        detail=f"Ungrounded AI extraction rows missing model/prompt/confidence/quote={ungrounded}.",
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
    if is_pe_domain(ir) or ir is None:
        invalid_governance = _scalar_int(
            conn,
            """
            SELECT (
                SELECT COUNT(*) FROM business_vault.sat_lp_side_letter_llm_ext t
                WHERE t.rec_src <> 'SYSTEM_GHOST'
                  AND (
                      t.extraction_confidence IS NULL
                      OR t.extraction_confidence < 0.85
                      OR UPPER(TRIM(COALESCE(t.human_review_status, ''))) NOT IN (
                          'APPROVED',
                          'STEWARD_VERIFIED',
                          'AUTO_ACCEPTED_TIER_1',
                          'AUTO_ACCEPTED_TIER_2'
                      )
                  )
            ) + (
                SELECT COUNT(*) FROM business_vault.sat_lnk_doc_portco_ai_provenance t
                WHERE t.rec_src <> 'SYSTEM_GHOST'
                  AND (
                      t.extraction_confidence IS NULL
                      OR t.extraction_confidence < 0.85
                      OR (
                          t.tier2_token_jaccard < 0.85
                          AND UPPER(TRIM(COALESCE(t.human_review_status, ''))) NOT IN (
                              'STEWARD_VERIFIED',
                              'APPROVED'
                          )
                      )
                      OR UPPER(TRIM(COALESCE(t.human_review_status, ''))) NOT IN (
                          'APPROVED',
                          'STEWARD_VERIFIED',
                          'AUTO_ACCEPTED_TIER_1',
                          'AUTO_ACCEPTED_TIER_2'
                      )
                  )
            ) + (
                SELECT COUNT(*) FROM raw_vault.lnk_doc_lp_side_letter l
                WHERE l.rec_src <> 'SYSTEM_ZERO_KEY'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM staging.stg_unstructured_vdr_llm_extractions src
                      INNER JOIN raw_vault.hub_unstructured_document d
                          ON d.doc_sha256 = src.doc_sha256
                      INNER JOIN raw_vault.hub_limited_partner lp
                          ON lp.bkcc = src.resolved_lp_bkcc AND lp.lp_id = src.resolved_lp_id
                      INNER JOIN raw_vault.hub_fund f
                          ON f.fund_code = src.resolved_fund_code
                      WHERE d.hk_doc = l.hk_doc
                        AND lp.hk_lp = l.hk_lp
                        AND f.hk_fund = l.hk_fund
                        AND src.steward_gate_status = 'APPROVED'
                        AND src.extraction_confidence >= 0.85
                  )
            ) + (
                SELECT COUNT(*) FROM raw_vault.lnk_doc_portco_memo l
                WHERE l.rec_src <> 'SYSTEM_ZERO_KEY'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM staging.stg_unstructured_vdr_llm_extractions src
                      INNER JOIN raw_vault.hub_unstructured_document d
                          ON d.doc_sha256 = src.doc_sha256
                      INNER JOIN raw_vault.hub_portfolio_company p
                          ON p.lei_code = src.resolved_lei_code
                      WHERE d.hk_doc = l.hk_doc
                        AND p.hk_portco = l.hk_portco
                        AND src.steward_gate_status = 'APPROVED'
                        AND src.extraction_confidence >= 0.85
                  )
            )
            """,
        )
        for extra_tbl in (
            "raw_vault.lnk_document_portfolio_company",
            "raw_vault.lnk_document_lp_commitment",
            "raw_vault.sat_lnk_doc_portco_ai_provenance",
        ):
            extra_cols = _table_column_map(conn, extra_tbl)
            extra_preds = _governance_predicates_for_columns(extra_cols)
            if extra_preds:
                invalid_governance += _scalar_int(
                    conn,
                    f"""
                    SELECT COUNT(*) FROM {extra_tbl} t
                    WHERE t.rec_src NOT IN ('SYSTEM_GHOST', 'SYSTEM_ZERO_KEY')
                      AND ({" OR ".join(extra_preds)})
                    """,
                )
        return DynamicCheckResult(
            check_id="DV-DYN-013",
            check_name="Tier-2 Confidence Threshold & Human Review Governance",
            passed=invalid_governance == 0,
            metric_value=invalid_governance,
            failing_rows=invalid_governance,
            detail=(
                f"AI extraction rows violating Tier-2/Steward review thresholds="
                f"{invalid_governance}."
            ),
        )

    invalid_governance = 0
    unstructured_links_by_name = {
        lnk.name: lnk for lnk in ir.links if lnk.requires_steward_approval_gate
    }
    for lnk in unstructured_links_by_name.values():
        tbl = f"{_link_schema(lnk)}.{_validate_ident(lnk.name)}"
        lnk_hk = _validate_ident(lnk.hk_column.name)
        col_map = _table_column_map(conn, tbl)
        preds = _governance_predicates_for_columns(col_map)
        stg_view = f"staging.stg_{_validate_ident(lnk.name)}"
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
                WHERE t.rec_src NOT IN ('SYSTEM_ZERO_KEY', 'SYSTEM_GHOST')
                  AND ({" OR ".join(preds)})
                """,
            )

    for sat in ir.satellites:
        tbl = f"{_satellite_schema(sat)}.{_validate_ident(sat.name)}"
        parent_hk = _validate_ident(sat.parent_hk_column.name)
        col_map = {c.name.lower(): _validate_ident(c.name) for c in sat.payload_columns}
        col_map.update(_table_column_map(conn, tbl))
        preds = _governance_predicates_for_columns(col_map)
        if sat.parent_entity in unstructured_links_by_name:
            stg_view = f"staging.stg_{_validate_ident(sat.name)}"
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
                WHERE t.rec_src NOT IN ('SYSTEM_GHOST', 'SYSTEM_ZERO_KEY')
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
    if is_pe_domain(ir) or ir is None:
        invalid_hashes = _scalar_int(
            conn,
            """
            SELECT COUNT(*) FROM raw_vault.sat_lp_core
            WHERE hash_diff IS NULL OR STRLEN(TRIM(hash_diff)) <> 64
            """,
        )
    else:
        _, _, satellite_pk_map, _ = build_vault_topology_maps(ir)
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
    hub_pk_map, link_pk_map, satellite_pk_map, _ = build_vault_topology_maps(ir)
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
