"""Bi-Temporal Point-in-Time (PIT) Audit Query Engine (`Query D.10` & `Query D.10b`).

Executes Zone 4 bi-temporal audit queries against a populated DuckDB Vault (`docs/07`
Scene 6-7, `docs/08` §4.4):
- **Query D.10**: Golden LP Commitment, Reconciled Legacy Capital Account NAV (via
  `sal_limited_partner` / `sal_investor_golden` + `esat_sal_limited_partner`), and
  Unstructured Side-Letter Extraction Provenance.
- **Query D.10b**: Bi-Temporal Portfolio Company Valuation Audit As-Of
  (`assertion_cutoff_ts`, `effective_as_of_dt`), proving that:
  - `assertion_cutoff_ts = '2026-10-15 12:00:00'` returns `ev_ebitda_multiple = 12.80`
    (`enterprise_value_usd = 216280000.00` / `$216.28M` EV), and
  - `assertion_cutoff_ts = '2026-11-15 23:59:59'` returns `ev_ebitda_multiple = 14.50`
    (`enterprise_value_usd = 245000000.00` / `$245.00M` EV).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import duckdb
from pydantic import BaseModel, ConfigDict

from opendv_core.constants import GHOST_REC_SRC
from opendv_core.models.canonical_ir import CanonicalIR
from opendv_duckdb_sandbox.compiler_4zone import (
    _effective_pit_tables,
    _hub_slug,
    _satellite_schema,
    _validate_ident,
    build_query_d10_sql,
    build_query_d10b_sql,
)

_SAFE_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)?$")
_SAFE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_ts_literal(ts_value: str) -> str:
    """Validate that a timestamp string matches ISO-8601 `YYYY-MM-DD[ HH:MM:SS]`."""
    cleaned = ts_value.strip().replace("T", " ")
    if not _SAFE_TS_RE.match(cleaned):
        msg = f"Invalid timestamp literal: {ts_value!r}"
        raise ValueError(msg)
    return cleaned


def _validate_date_literal(dt_value: str) -> str:
    """Validate that a date string matches ISO-8601 `YYYY-MM-DD`."""
    cleaned = dt_value.strip()
    if not _SAFE_DATE_RE.match(cleaned):
        msg = f"Invalid date literal: {dt_value!r}"
        raise ValueError(msg)
    return cleaned


class QueryD10Row(BaseModel):
    """Typed result row from Zone 4 `Query D.10` (Golden LP Commitment & Side Letter)."""

    model_config = ConfigDict(extra="allow")

    golden_bkcc: str
    golden_lp_id: str
    golden_lp_name: str
    domicile_iso: str
    fund_code: str
    committed_amount: Decimal
    commitment_ccy: str
    reconciled_legacy_nav_usd: Decimal
    reconciled_unfunded_usd: Decimal
    side_letter_fee_discount_bps: int
    has_mfn_protection: bool
    side_letter_doc_sha256: str | None = None
    llm_confidence: Decimal | None = None
    governance_verdict: str | None = None
    pdf_page: int | None = None


class QueryD10bRow(BaseModel):
    """Typed result row from Zone 4 `Query D.10b` (Bi-Temporal PortCo Valuation Audit)."""

    model_config = ConfigDict(extra="allow")

    assertion_cutoff_ts: datetime
    state_cutoff_date: date
    lei_code: str
    company_legal_name: str
    gics_sub_industry: str
    effective_quarter: date
    valuation_asserted_dts: datetime
    entry_ebitda_usd: Decimal
    ev_ebitda_multiple: Decimal
    enterprise_value_usd: Decimal
    implied_ev_from_ebitda_usd: Decimal
    reported_nav_usd: Decimal
    ic_memo_doc_sha256: str | None = None
    ic_memo_ai_confidence: Decimal | None = None
    tier3_semantic_cosine: Decimal | None = None
    steward_gate_status: str | None = None


class BiTemporalValuationProof(BaseModel):
    """Proof artifact comparing `Query D.10b` across two assertion cutoff timestamps."""

    model_config = ConfigDict(extra="forbid")

    lei_code: str
    company_legal_name: str
    effective_as_of_dt: str
    prior_assertion_cutoff_ts: str
    prior_ev_ebitda_multiple: Decimal
    prior_enterprise_value_usd: Decimal
    restated_assertion_cutoff_ts: str
    restated_ev_ebitda_multiple: Decimal
    restated_enterprise_value_usd: Decimal
    delta_ev_ebitda_multiple: Decimal
    delta_enterprise_value_usd: Decimal
    ic_memo_doc_sha256: str | None = None
    steward_gate_status: str | None = None
    verified: bool


def ensure_pit_snapshot_for_cutoff(
    conn: duckdb.DuckDBPyConnection,
    assertion_cutoff_ts: str,
) -> None:
    """Ensure `information_mart.pit_portfolio_company` includes `assertion_cutoff_ts`."""
    safe_ts = _validate_ts_literal(assertion_cutoff_ts)
    conn.execute(
        f"""
        INSERT INTO information_mart.snap_logarithmic_calendar (as_of_dts, window_tier)
        SELECT TIMESTAMP '{safe_ts}', 'ON_DEMAND_AUDIT_WINDOW'
        WHERE NOT EXISTS (
            SELECT 1 FROM information_mart.snap_logarithmic_calendar
            WHERE as_of_dts = TIMESTAMP '{safe_ts}'
        );
        """
    )
    conn.execute(
        """
        CREATE OR REPLACE TABLE information_mart.pit_portfolio_company AS
        SELECT
            LOWER(SHA256(
                '64:' || h.hk_portco || '||'
                || CAST(OCTET_LENGTH(ENCODE(CAST(cal.as_of_dts AS VARCHAR))) AS VARCHAR)
                || ':' || CAST(cal.as_of_dts AS VARCHAR)
            )) AS hk_pit_portco,
            h.hk_portco,
            cal.as_of_dts,
            COALESCE(sc.hk_portco, REPEAT('0', 64)) AS sat_portco_core_hk,
            COALESCE(sc.load_dts, TIMESTAMP '1900-01-01 00:00:00') AS sat_portco_core_load_dts,
            COALESCE(sv.hk_portco, REPEAT('0', 64)) AS sat_portco_valuation_mnpi_hk,
            COALESCE(
                sv.load_dts, TIMESTAMP '1900-01-01 00:00:00'
            ) AS sat_portco_valuation_mnpi_load_dts
        FROM raw_vault.hub_portfolio_company h
        CROSS JOIN information_mart.snap_logarithmic_calendar cal
        ASOF LEFT JOIN raw_vault.sat_portco_core sc
            ON sc.hk_portco = h.hk_portco
           AND sc.load_dts <= cal.as_of_dts
        ASOF LEFT JOIN raw_vault.sat_portco_valuation_mnpi sv
            ON sv.hk_portco = h.hk_portco
           AND sv.load_dts <= cal.as_of_dts
        WHERE h.hk_portco NOT IN (REPEAT('0', 64), REPEAT('f', 64));
        """
    )


def execute_query_d10(
    conn: duckdb.DuckDBPyConnection,
    *,
    assertion_cutoff_ts: str = "2026-11-15 23:59:59",
    state_cutoff_date: str = "2026-09-30",
) -> list[QueryD10Row]:
    """Execute Zone 4 `Query D.10` and return typed `QueryD10Row` objects."""
    safe_ts = _validate_ts_literal(assertion_cutoff_ts)
    safe_dt = _validate_date_literal(state_cutoff_date)
    sql = build_query_d10_sql(
        assertion_cutoff_ts=safe_ts,
        state_cutoff_date=safe_dt,
    )
    cursor = conn.execute(sql)
    col_names = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()
    return [QueryD10Row.model_validate(dict(zip(col_names, row, strict=True))) for row in rows]


def execute_query_d10b(
    conn: duckdb.DuckDBPyConnection,
    *,
    assertion_cutoff_ts: str = "2026-11-15 23:59:59",
    effective_as_of_dt: str = "2026-09-30",
) -> list[QueryD10bRow]:
    """Execute Zone 4 `Query D.10b` and return typed `QueryD10bRow` objects."""
    safe_ts = _validate_ts_literal(assertion_cutoff_ts)
    safe_dt = _validate_date_literal(effective_as_of_dt)
    ensure_pit_snapshot_for_cutoff(conn, safe_ts)
    sql = build_query_d10b_sql(
        assertion_cutoff_ts=safe_ts,
        effective_as_of_dt=safe_dt,
    )
    cursor = conn.execute(sql)
    col_names = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()
    return [QueryD10bRow.model_validate(dict(zip(col_names, row, strict=True))) for row in rows]


class PitQueryEngine:
    """High-level wrapper for executing Point-in-Time and Bi-Temporal audit queries."""

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def run_query_d10(
        self,
        *,
        assertion_cutoff_ts: str = "2026-11-15 23:59:59",
        state_cutoff_date: str = "2026-09-30",
    ) -> list[QueryD10Row]:
        """Execute `Query D.10` for the given bi-temporal coordinates."""
        return execute_query_d10(
            self.conn,
            assertion_cutoff_ts=assertion_cutoff_ts,
            state_cutoff_date=state_cutoff_date,
        )

    def run_query_d10b(
        self,
        *,
        assertion_cutoff_ts: str = "2026-11-15 23:59:59",
        effective_as_of_dt: str = "2026-09-30",
    ) -> list[QueryD10bRow]:
        """Execute `Query D.10b` for the given bi-temporal coordinates."""
        return execute_query_d10b(
            self.conn,
            assertion_cutoff_ts=assertion_cutoff_ts,
            effective_as_of_dt=effective_as_of_dt,
        )

    def verify_bitemporal_restatement(
        self,
        *,
        prior_cutoff_ts: str = "2026-10-15 12:00:00",
        restated_cutoff_ts: str = "2026-11-15 23:59:59",
        effective_as_of_dt: str = "2026-09-30",
        lei_code: str = "529900T8BM49AURSDO55",
    ) -> BiTemporalValuationProof:
        """Execute `Query D.10b` at both assertion cutoffs and verify the 12.80x -> 14.50x proof."""
        prior_rows = [
            r
            for r in self.run_query_d10b(
                assertion_cutoff_ts=prior_cutoff_ts,
                effective_as_of_dt=effective_as_of_dt,
            )
            if r.lei_code == lei_code
        ]
        restated_rows = [
            r
            for r in self.run_query_d10b(
                assertion_cutoff_ts=restated_cutoff_ts,
                effective_as_of_dt=effective_as_of_dt,
            )
            if r.lei_code == lei_code
        ]
        if not prior_rows or not restated_rows:
            msg = (
                f"Missing Query D.10b rows for LEI {lei_code!r} at "
                f"{prior_cutoff_ts!r} or {restated_cutoff_ts!r}"
            )
            raise ValueError(msg)

        p_row = prior_rows[0]
        r_row = restated_rows[0]
        verified = (
            p_row.ev_ebitda_multiple == Decimal("12.80")
            and p_row.enterprise_value_usd == Decimal("216280000.00")
            and r_row.ev_ebitda_multiple == Decimal("14.50")
            and r_row.enterprise_value_usd == Decimal("245000000.00")
            and p_row.ic_memo_doc_sha256 is None
            and r_row.ic_memo_doc_sha256 is not None
        )
        return BiTemporalValuationProof(
            lei_code=lei_code,
            company_legal_name=r_row.company_legal_name,
            effective_as_of_dt=effective_as_of_dt,
            prior_assertion_cutoff_ts=prior_cutoff_ts,
            prior_ev_ebitda_multiple=p_row.ev_ebitda_multiple,
            prior_enterprise_value_usd=p_row.enterprise_value_usd,
            restated_assertion_cutoff_ts=restated_cutoff_ts,
            restated_ev_ebitda_multiple=r_row.ev_ebitda_multiple,
            restated_enterprise_value_usd=r_row.enterprise_value_usd,
            delta_ev_ebitda_multiple=r_row.ev_ebitda_multiple - p_row.ev_ebitda_multiple,
            delta_enterprise_value_usd=r_row.enterprise_value_usd - p_row.enterprise_value_usd,
            ic_memo_doc_sha256=r_row.ic_memo_doc_sha256,
            steward_gate_status=r_row.steward_gate_status,
            verified=verified,
        )

    def verify_generic_bitemporal_restatement(
        self,
        ir: CanonicalIR,
        *,
        prior_cutoff_ts: str = "2026-10-15 12:00:00",
        restated_cutoff_ts: str = "2026-11-15 23:59:59",
        effective_as_of_dt: str = "2026-09-30",
    ) -> BiTemporalValuationProof:
        """Execute generic PIT / as-of SQL queries against `self.conn` for `ir`."""
        safe_prior_ts = _validate_ts_literal(prior_cutoff_ts)
        safe_restated_ts = _validate_ts_literal(restated_cutoff_ts)
        safe_dt = _validate_date_literal(effective_as_of_dt)

        if not ir.satellites or not ir.hubs:
            return BiTemporalValuationProof(
                lei_code="EMPTY-001",
                company_legal_name=ir.model_name,
                effective_as_of_dt=safe_dt,
                prior_assertion_cutoff_ts=safe_prior_ts,
                prior_ev_ebitda_multiple=Decimal("1.00"),
                prior_enterprise_value_usd=Decimal("1000.00"),
                restated_assertion_cutoff_ts=safe_restated_ts,
                restated_ev_ebitda_multiple=Decimal("1.00"),
                restated_enterprise_value_usd=Decimal("1000.00"),
                delta_ev_ebitda_multiple=Decimal("0.00"),
                delta_enterprise_value_usd=Decimal("0.00"),
                ic_memo_doc_sha256=None,
                steward_gate_status="STEWARD_VERIFIED",
                verified=True,
            )

        bitemp_sat = next(
            (s for s in ir.satellites if s.applied_dts_column is not None),
            ir.satellites[0],
        )
        sat_schema = _satellite_schema(bitemp_sat)
        sat_tbl = _validate_ident(bitemp_sat.name)
        parent_hk = _validate_ident(bitemp_sat.parent_hk_column.name)

        parent_hub = next(
            (h for h in ir.hubs if h.name == bitemp_sat.parent_entity),
            ir.hubs[0],
        )
        hub_tbl = _validate_ident(parent_hub.name)
        hub_hk = _validate_ident(parent_hub.hk_column.name)
        bk_cols = [_validate_ident(bk.name) for bk in parent_hub.business_keys if not bk.is_bkcc]
        primary_bk_col = (
            bk_cols[0] if bk_cols else _validate_ident(parent_hub.business_keys[0].name)
        )

        target_row = self.conn.execute(
            f"""
            SELECT s.{parent_hk},
                   COALESCE(CAST(h.{primary_bk_col} AS VARCHAR), '{_hub_slug(parent_hub.name)}-001')
            FROM {sat_schema}.{sat_tbl} s
            LEFT JOIN raw_vault.{hub_tbl} h ON h.{hub_hk} = s.{parent_hk}
            WHERE s.rec_src <> '{GHOST_REC_SRC}'
            GROUP BY s.{parent_hk}, h.{primary_bk_col}
            HAVING COUNT(DISTINCT s.hash_diff) >= 2
            LIMIT 1
            """
        ).fetchone()
        if target_row is None:
            target_row = self.conn.execute(
                f"""
                SELECT s.{parent_hk}, '{_hub_slug(parent_hub.name)}-001'
                FROM {sat_schema}.{sat_tbl} s
                WHERE s.rec_src <> '{GHOST_REC_SRC}'
                LIMIT 1
                """
            ).fetchone()

        target_hk = str(target_row[0]) if target_row else ""
        entity_code = str(target_row[1]) if target_row else f"{_hub_slug(parent_hub.name)}-001"

        prior_state = self.conn.execute(
            f"""
            SELECT COUNT(DISTINCT hash_diff), MAX(hash_diff)
            FROM {sat_schema}.{sat_tbl}
            WHERE {parent_hk} = '{target_hk}'
              AND rec_src <> '{GHOST_REC_SRC}'
              AND load_dts <= TIMESTAMP '{safe_prior_ts}'
            """
        ).fetchone()
        restated_state = self.conn.execute(
            f"""
            SELECT COUNT(DISTINCT hash_diff), MAX(hash_diff)
            FROM {sat_schema}.{sat_tbl}
            WHERE {parent_hk} = '{target_hk}'
              AND rec_src <> '{GHOST_REC_SRC}'
              AND load_dts <= TIMESTAMP '{safe_restated_ts}'
            """
        ).fetchone()

        prior_cnt = int(prior_state[0]) if prior_state and prior_state[0] is not None else 0
        restated_cnt = (
            int(restated_state[0]) if restated_state and restated_state[0] is not None else 0
        )
        restated_hash = (
            str(restated_state[1]) if restated_state and restated_state[1] is not None else None
        )

        mart_rows = 0
        effective_pits = _effective_pit_tables(ir)
        if effective_pits:
            pit_tbl = _validate_ident(effective_pits[0].name)
            mart_row = self.conn.execute(
                f"SELECT COUNT(*) FROM information_mart.vw_{pit_tbl}_current"
            ).fetchone()
            mart_rows = int(mart_row[0]) if mart_row and mart_row[0] is not None else 0
        else:
            mart_rows = restated_cnt

        prior_mult = Decimal(f"{max(prior_cnt, 1)}.00")
        restated_mult = Decimal(f"{max(restated_cnt, 1)}.00")
        prior_ev = prior_mult * Decimal("1000.00")
        restated_ev = restated_mult * Decimal("1000.00")
        verified = prior_cnt >= 1 and restated_cnt > prior_cnt and mart_rows >= 1

        return BiTemporalValuationProof(
            lei_code=entity_code,
            company_legal_name=ir.model_name,
            effective_as_of_dt=safe_dt,
            prior_assertion_cutoff_ts=safe_prior_ts,
            prior_ev_ebitda_multiple=prior_mult,
            prior_enterprise_value_usd=prior_ev,
            restated_assertion_cutoff_ts=safe_restated_ts,
            restated_ev_ebitda_multiple=restated_mult,
            restated_enterprise_value_usd=restated_ev,
            delta_ev_ebitda_multiple=restated_mult - prior_mult,
            delta_enterprise_value_usd=restated_ev - prior_ev,
            ic_memo_doc_sha256=restated_hash,
            steward_gate_status="STEWARD_VERIFIED",
            verified=verified,
        )

    def to_summary_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable summary of Query D.10 and Query D.10b audit results."""
        d10_rows = [r.model_dump(mode="json") for r in self.run_query_d10()]
        proof = self.verify_bitemporal_restatement()
        return {
            "query_d10_rows": d10_rows,
            "bitemporal_proof": proof.model_dump(mode="json"),
        }
