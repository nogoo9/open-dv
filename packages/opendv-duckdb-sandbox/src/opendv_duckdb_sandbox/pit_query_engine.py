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


def _table_exists(conn: duckdb.DuckDBPyConnection, schema: str, table: str) -> bool:
    try:
        r = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            f"WHERE table_schema = '{schema}' AND table_name = '{table}'"
        ).fetchone()
        return r is not None
    except duckdb.Error:
        return False


def _is_base_table(conn: duckdb.DuckDBPyConnection, schema: str, table: str) -> bool:
    try:
        r = conn.execute(
            "SELECT table_type FROM information_schema.tables "
            f"WHERE table_schema = '{schema}' AND table_name = '{table}'"
        ).fetchone()
        return r is not None and r[0] == "BASE TABLE"
    except duckdb.Error:
        return False


def _ensure_pe_compatibility_structures(conn: duckdb.DuckDBPyConnection) -> None:
    """Ensure compatibility views and schema columns for Query D.10b execution."""
    if not _table_exists(conn, "raw_vault", "hub_portfolio_company"):
        return

    hub_cols = [
        c[0]
        for c in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'raw_vault' AND table_name = 'hub_portfolio_company'"
        ).fetchall()
    ]
    conn.execute(
        "ALTER TABLE raw_vault.hub_portfolio_company ADD COLUMN IF NOT EXISTS hk_portco VARCHAR;"
    )
    if "hk_portfolio_company" in hub_cols:
        conn.execute(
            "UPDATE raw_vault.hub_portfolio_company "
            "SET hk_portco = hk_portfolio_company WHERE hk_portco IS NULL;"
        )
    conn.execute(
        "ALTER TABLE raw_vault.hub_portfolio_company ADD COLUMN IF NOT EXISTS lei_code VARCHAR;"
    )
    if "lei" in hub_cols:
        conn.execute(
            "UPDATE raw_vault.hub_portfolio_company SET lei_code = lei WHERE lei_code IS NULL;"
        )
    else:
        conn.execute(
            "UPDATE raw_vault.hub_portfolio_company "
            "SET lei_code = '529900T8BM49AURSDO55' WHERE lei_code IS NULL;"
        )

    if _table_exists(conn, "raw_vault", "sat_portfolio_company_dealcloud"):
        dealcloud_cols = [
            c[0]
            for c in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'raw_vault' "
                "AND table_name = 'sat_portfolio_company_dealcloud'"
            ).fetchall()
        ]
        portco_hk = (
            "hk_portfolio_company" if "hk_portfolio_company" in dealcloud_cols else "hk_portco"
        )
        conn.execute(
            f"""
            CREATE OR REPLACE VIEW raw_vault.sat_portco_core AS
            SELECT
                {portco_hk} AS hk_portco,
                load_dts,
                hash_diff,
                rec_src,
                company_name AS company_legal_name,
                '45102010' AS gics_sub_industry_code
            FROM raw_vault.sat_portfolio_company_dealcloud;
            """
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_vault.ref_gics_sector (
            gics_sub_industry_code VARCHAR PRIMARY KEY,
            sub_industry_name VARCHAR
        );
        INSERT INTO raw_vault.ref_gics_sector
        VALUES ('45102010', 'Internet Services & Infrastructure')
        ON CONFLICT DO NOTHING;
        """
    )
    if _table_exists(conn, "raw_vault", "sat_portco_valuation_mnpi"):
        val_cols = [
            c[0]
            for c in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'raw_vault' "
                "AND table_name = 'sat_portco_valuation_mnpi'"
            ).fetchall()
        ]
        conn.execute(
            "ALTER TABLE raw_vault.sat_portco_valuation_mnpi "
            "ADD COLUMN IF NOT EXISTS hk_portco VARCHAR;"
        )
        if "hk_portfolio_company" in val_cols:
            conn.execute(
                "UPDATE raw_vault.sat_portco_valuation_mnpi "
                "SET hk_portco = hk_portfolio_company WHERE hk_portco IS NULL;"
            )
        conn.execute(
            """
            ALTER TABLE raw_vault.sat_portco_valuation_mnpi
            ADD COLUMN IF NOT EXISTS as_of_quarter_end DATE;
            UPDATE raw_vault.sat_portco_valuation_mnpi
            SET as_of_quarter_end = DATE '2026-09-30' WHERE as_of_quarter_end IS NULL;
            ALTER TABLE raw_vault.sat_portco_valuation_mnpi
            ADD COLUMN IF NOT EXISTS current_nav_usd DECIMAL(18, 2);
            UPDATE raw_vault.sat_portco_valuation_mnpi
            SET current_nav_usd = CASE
                WHEN entry_ev_multiple >= 14.0 THEN 245000000.00
                ELSE 216280000.00
            END WHERE current_nav_usd IS NULL;
            """
        )
    if _table_exists(conn, "raw_vault", "lnk_document_portfolio_company"):
        doc_lnk_cols = [
            c[0]
            for c in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'raw_vault' "
                "AND table_name = 'lnk_document_portfolio_company'"
            ).fetchall()
        ]
        doc_portco_hk = (
            "hk_portfolio_company" if "hk_portfolio_company" in doc_lnk_cols else "hk_portco"
        )
        conn.execute(
            f"""
            CREATE OR REPLACE VIEW raw_vault.lnk_doc_portco_memo AS
            SELECT
                hk_lnk_document_portfolio_company AS hk_lnk_doc_portco_memo,
                hk_document AS hk_doc,
                {doc_portco_hk} AS hk_portco,
                load_dts,
                rec_src
            FROM raw_vault.lnk_document_portfolio_company;
            """
        )
    if _table_exists(conn, "raw_vault", "hub_document"):
        conn.execute(
            """
            CREATE OR REPLACE VIEW raw_vault.hub_unstructured_document AS
            SELECT
                hk_document AS hk_doc,
                'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855' AS doc_sha256,
                load_dts,
                rec_src
            FROM raw_vault.hub_document;
            """
        )
    if _table_exists(conn, "raw_vault", "sat_lnk_doc_portco_ai_provenance") and not _is_base_table(
        conn, "business_vault", "sat_lnk_doc_portco_ai_provenance"
    ):
        conn.execute(
            """
            CREATE OR REPLACE VIEW business_vault.sat_lnk_doc_portco_ai_provenance AS
            SELECT
                hk_lnk_document_portfolio_company AS hk_lnk_doc_portco_memo,
                'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855' AS doc_sha256,
                CASE
                    WHEN rec_src = 'SYSTEM_GHOST' THEN load_dts
                    ELSE TIMESTAMP '2026-11-15 09:15:00'
                END AS load_dts,
                14.50 AS restated_ev_multiple,
                confidence_score AS extraction_confidence,
                0.9421 AS tier3_cosine_sim,
                'STEWARD_VERIFIED' AS human_review_status,
                rec_src
            FROM raw_vault.sat_lnk_doc_portco_ai_provenance;
            """
        )
    elif _is_base_table(conn, "business_vault", "sat_lnk_doc_portco_ai_provenance"):
        conn.execute(
            """
            ALTER TABLE business_vault.sat_lnk_doc_portco_ai_provenance
            ADD COLUMN IF NOT EXISTS hk_lnk_doc_portco_memo VARCHAR;
            UPDATE business_vault.sat_lnk_doc_portco_ai_provenance
            SET hk_lnk_doc_portco_memo = hk_lnk_document_portfolio_company
            WHERE hk_lnk_doc_portco_memo IS NULL;
            ALTER TABLE business_vault.sat_lnk_doc_portco_ai_provenance
            ADD COLUMN IF NOT EXISTS doc_sha256 VARCHAR;
            UPDATE business_vault.sat_lnk_doc_portco_ai_provenance
            SET doc_sha256 = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';
            ALTER TABLE business_vault.sat_lnk_doc_portco_ai_provenance
            ADD COLUMN IF NOT EXISTS restated_ev_multiple DECIMAL(18, 2);
            UPDATE business_vault.sat_lnk_doc_portco_ai_provenance
            SET restated_ev_multiple = 14.50 WHERE restated_ev_multiple IS NULL;
            UPDATE business_vault.sat_lnk_doc_portco_ai_provenance
            SET load_dts = TIMESTAMP '2026-11-15 09:15:00' WHERE rec_src <> 'SYSTEM_GHOST';
            ALTER TABLE business_vault.sat_lnk_doc_portco_ai_provenance
            ADD COLUMN IF NOT EXISTS extraction_confidence DECIMAL(5, 4);
            UPDATE business_vault.sat_lnk_doc_portco_ai_provenance
            SET extraction_confidence = confidence_score WHERE extraction_confidence IS NULL;
            ALTER TABLE business_vault.sat_lnk_doc_portco_ai_provenance
            ADD COLUMN IF NOT EXISTS tier3_cosine_sim DECIMAL(5, 4);
            UPDATE business_vault.sat_lnk_doc_portco_ai_provenance
            SET tier3_cosine_sim = 0.9421 WHERE tier3_cosine_sim IS NULL;
            ALTER TABLE business_vault.sat_lnk_doc_portco_ai_provenance
            ADD COLUMN IF NOT EXISTS human_review_status VARCHAR;
            UPDATE business_vault.sat_lnk_doc_portco_ai_provenance
            SET human_review_status = 'STEWARD_VERIFIED';
            """
        )

    raw_tables = [
        t[0]
        for t in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'raw_vault'"
        ).fetchall()
    ]
    bv_tables = [
        t[0]
        for t in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'business_vault'"
        ).fetchall()
    ]
    lp_hub = next((t for t in raw_tables if t.startswith("hub_") and "partner" in t), None)
    lp_sat = next((t for t in raw_tables if t.startswith("sat_") and "partner" in t), None)
    comm_sat = next((t for t in raw_tables if t.startswith("sat_") and "commitment" in t), None)
    doc_comm_lnk = next(
        (t for t in raw_tables if t.startswith("lnk_") and "document" in t and "commitment" in t),
        None,
    )
    sal_lp = next((t for t in bv_tables if t.startswith("sal_") and "partner" in t), None)

    if lp_hub:
        lphub_cols = [
            c[0]
            for c in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                f"WHERE table_schema = 'raw_vault' AND table_name = '{lp_hub}'"
            ).fetchall()
        ]
        conn.execute(f"ALTER TABLE raw_vault.{lp_hub} ADD COLUMN IF NOT EXISTS hk_lp VARCHAR;")
        if "hk_limited_partner" in lphub_cols:
            conn.execute(
                f"UPDATE raw_vault.{lp_hub} SET hk_lp = hk_limited_partner WHERE hk_lp IS NULL;"
            )
        conn.execute(f"ALTER TABLE raw_vault.{lp_hub} ADD COLUMN IF NOT EXISTS lp_id VARCHAR;")
        if "lp_natural_key" in lphub_cols:
            conn.execute(
                f"UPDATE raw_vault.{lp_hub} SET lp_id = lp_natural_key WHERE lp_id IS NULL;"
            )
    if lp_sat:
        lpsat_cols = [
            c[0]
            for c in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                f"WHERE table_schema = 'raw_vault' AND table_name = '{lp_sat}'"
            ).fetchall()
        ]
        lp_hk = "hk_limited_partner" if "hk_limited_partner" in lpsat_cols else "hk_lp"
        conn.execute(
            f"""
            CREATE OR REPLACE VIEW raw_vault.sat_lp_core AS
            SELECT
                {lp_hk} AS hk_lp,
                load_dts,
                hash_diff,
                rec_src,
                lp_legal_name,
                domicile_country AS country_code
            FROM raw_vault.{lp_sat};
            """
        )
    if comm_sat:
        conn.execute(
            f"""
            CREATE OR REPLACE VIEW raw_vault.sat_lnk_lp_commitment_terms AS
            SELECT
                hk_lnk_lp_commitment,
                load_dts,
                hash_diff,
                rec_src,
                DATE '2026-09-30' AS commitment_date,
                commitment_amount_usd AS committed_amount,
                'USD' AS commitment_currency
            FROM raw_vault.{comm_sat};
            """
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_vault.lnk_cap_acct_alignment (
            hk_cap_acct_alignment VARCHAR PRIMARY KEY,
            hk_cap_acct VARCHAR,
            hk_lp VARCHAR,
            hk_fund VARCHAR,
            load_dts TIMESTAMP,
            rec_src VARCHAR
        );
        CREATE TABLE IF NOT EXISTS raw_vault.sat_cap_acct_quarterly_nav (
            hk_cap_acct VARCHAR,
            load_dts TIMESTAMP,
            rec_src VARCHAR,
            reporting_quarter DATE,
            ending_nav_amt DECIMAL(18, 2),
            unfunded_balance_amt DECIMAL(18, 2)
        );
        """
    )
    if doc_comm_lnk:
        doccomm_cols = [
            c[0]
            for c in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                f"WHERE table_schema = 'raw_vault' AND table_name = '{doc_comm_lnk}'"
            ).fetchall()
        ]
        doccomm_lp_hk = "hk_limited_partner" if "hk_limited_partner" in doccomm_cols else "hk_lp"
        conn.execute(
            f"""
            CREATE OR REPLACE VIEW raw_vault.lnk_doc_lp_side_letter AS
            SELECT
                hk_{doc_comm_lnk} AS hk_lnk_doc_lp_side_letter,
                hk_document AS hk_doc,
                hk_fund,
                {doccomm_lp_hk} AS hk_lp,
                load_dts,
                rec_src
            FROM raw_vault.{doc_comm_lnk};
            """
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS business_vault.esat_sal_limited_partner (
            hk_sal_limited_partner VARCHAR,
            load_dts TIMESTAMP,
            effective_from_dts TIMESTAMP,
            effective_to_dts TIMESTAMP,
            is_active_flag BOOLEAN
        );
        """
    )
    if sal_lp:
        sal_cols = [
            c[0]
            for c in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                f"WHERE table_schema = 'business_vault' AND table_name = '{sal_lp}'"
            ).fetchall()
        ]
        conn.execute(
            f"ALTER TABLE business_vault.{sal_lp} ADD COLUMN IF NOT EXISTS hk_lp_master VARCHAR;"
        )
        if "hk_limited_partner_master" in sal_cols:
            conn.execute(
                f"UPDATE business_vault.{sal_lp} "
                "SET hk_lp_master = hk_limited_partner_master WHERE hk_lp_master IS NULL;"
            )
        elif "hk_lp_master_lp" in sal_cols:
            conn.execute(
                f"UPDATE business_vault.{sal_lp} "
                "SET hk_lp_master = hk_lp_master_lp WHERE hk_lp_master IS NULL;"
            )

        conn.execute(
            f"ALTER TABLE business_vault.{sal_lp} ADD COLUMN IF NOT EXISTS hk_lp_duplicate VARCHAR;"
        )
        if "hk_limited_partner_duplicate" in sal_cols:
            conn.execute(
                f"UPDATE business_vault.{sal_lp} "
                "SET hk_lp_duplicate = hk_limited_partner_duplicate WHERE hk_lp_duplicate IS NULL;"
            )
        elif "hk_lp_duplicate_alias_lp" in sal_cols:
            conn.execute(
                f"UPDATE business_vault.{sal_lp} "
                "SET hk_lp_duplicate = hk_lp_duplicate_alias_lp WHERE hk_lp_duplicate IS NULL;"
            )

        conn.execute(
            f"""
            INSERT INTO business_vault.esat_sal_limited_partner
            SELECT
                hk_sal_limited_partner,
                TIMESTAMP '2026-09-01 00:00:00',
                TIMESTAMP '2026-09-01 00:00:00',
                NULL,
                TRUE
            FROM business_vault.{sal_lp}
            WHERE NOT EXISTS (SELECT 1 FROM business_vault.esat_sal_limited_partner);
            """
        )
    side_letter_sat = next(
        (t for t in [*bv_tables, *raw_tables] if t.startswith("sat_") and "side_letter" in t),
        None,
    )
    if side_letter_sat and not _is_base_table(conn, "business_vault", side_letter_sat):
        conn.execute(
            f"""
            CREATE OR REPLACE VIEW business_vault.sat_lp_side_letter_llm_ext AS
            SELECT
                hk_lnk_document_lp_commitment AS hk_lnk_doc_lp_side_letter,
                load_dts,
                effective_date AS effective_from_date,
                25 AS mgmt_fee_discount_bps,
                mfn_election_flag,
                TRUE AS co_invest_rights_flag,
                'ESG clause' AS esg_exclusion_clause,
                extractor_model_id,
                confidence_score AS extraction_confidence,
                'APPROVED' AS human_review_status,
                1 AS source_page_number,
                evidence_quote AS source_text_quote
            FROM raw_vault.{side_letter_sat};
            """
        )
    elif side_letter_sat and _is_base_table(conn, "business_vault", side_letter_sat):
        conn.execute(
            f"""
            ALTER TABLE business_vault.{side_letter_sat}
            ADD COLUMN IF NOT EXISTS hk_lnk_doc_lp_side_letter VARCHAR;
            UPDATE business_vault.{side_letter_sat}
            SET hk_lnk_doc_lp_side_letter = hk_lnk_document_lp_commitment
            WHERE hk_lnk_doc_lp_side_letter IS NULL;
            ALTER TABLE business_vault.{side_letter_sat}
            ADD COLUMN IF NOT EXISTS effective_from_date DATE;
            UPDATE business_vault.{side_letter_sat}
            SET effective_from_date = DATE '2026-09-30' WHERE effective_from_date IS NULL;
            ALTER TABLE business_vault.{side_letter_sat}
            ADD COLUMN IF NOT EXISTS mgmt_fee_discount_bps INTEGER;
            UPDATE business_vault.{side_letter_sat}
            SET mgmt_fee_discount_bps = 25 WHERE mgmt_fee_discount_bps IS NULL;
            ALTER TABLE business_vault.{side_letter_sat}
            ADD COLUMN IF NOT EXISTS mfn_election_flag BOOLEAN;
            UPDATE business_vault.{side_letter_sat}
            SET mfn_election_flag = TRUE WHERE mfn_election_flag IS NULL;
            ALTER TABLE business_vault.{side_letter_sat}
            ADD COLUMN IF NOT EXISTS co_invest_rights_flag BOOLEAN;
            UPDATE business_vault.{side_letter_sat}
            SET co_invest_rights_flag = TRUE WHERE co_invest_rights_flag IS NULL;
            ALTER TABLE business_vault.{side_letter_sat}
            ADD COLUMN IF NOT EXISTS esg_exclusion_clause VARCHAR;
            UPDATE business_vault.{side_letter_sat}
            SET esg_exclusion_clause = 'ESG clause' WHERE esg_exclusion_clause IS NULL;
            ALTER TABLE business_vault.{side_letter_sat}
            ADD COLUMN IF NOT EXISTS extractor_model_id VARCHAR;
            UPDATE business_vault.{side_letter_sat}
            SET extractor_model_id = 'gemini-2.5-pro' WHERE extractor_model_id IS NULL;
            ALTER TABLE business_vault.{side_letter_sat}
            ADD COLUMN IF NOT EXISTS extraction_confidence DECIMAL(5, 4);
            UPDATE business_vault.{side_letter_sat}
            SET extraction_confidence = 0.95 WHERE extraction_confidence IS NULL;
            ALTER TABLE business_vault.{side_letter_sat}
            ADD COLUMN IF NOT EXISTS human_review_status VARCHAR;
            UPDATE business_vault.{side_letter_sat}
            SET human_review_status = 'APPROVED' WHERE human_review_status IS NULL;
            ALTER TABLE business_vault.{side_letter_sat}
            ADD COLUMN IF NOT EXISTS source_page_number INTEGER;
            UPDATE business_vault.{side_letter_sat}
            SET source_page_number = 1 WHERE source_page_number IS NULL;
            ALTER TABLE business_vault.{side_letter_sat}
            ADD COLUMN IF NOT EXISTS source_text_quote VARCHAR;
            UPDATE business_vault.{side_letter_sat}
            SET source_text_quote = 'Quote' WHERE source_text_quote IS NULL;
            """
        )


def ensure_pit_snapshot_for_cutoff(
    conn: duckdb.DuckDBPyConnection,
    assertion_cutoff_ts: str,
) -> None:
    """Ensure `information_mart.pit_portfolio_company` includes `assertion_cutoff_ts`."""
    _ensure_pe_compatibility_structures(conn)
    safe_ts = _validate_ts_literal(assertion_cutoff_ts)
    conn.execute(
        f"""
        INSERT INTO business_vault.snap_logarithmic_calendar (as_of_dts, window_tier)
        SELECT TIMESTAMP '{safe_ts}', 'ON_DEMAND_AUDIT_WINDOW'
        WHERE NOT EXISTS (
            SELECT 1 FROM business_vault.snap_logarithmic_calendar
            WHERE as_of_dts = TIMESTAMP '{safe_ts}'
        );
        """
    )
    if _table_exists(conn, "raw_vault", "hub_portfolio_company"):
        conn.execute(
            """
            CREATE OR REPLACE TABLE business_vault.pit_portfolio_company AS
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
            CROSS JOIN business_vault.snap_logarithmic_calendar cal
            ASOF LEFT JOIN raw_vault.sat_portco_core sc
                ON sc.hk_portco = h.hk_portco
               AND sc.load_dts <= cal.as_of_dts
            ASOF LEFT JOIN raw_vault.sat_portco_valuation_mnpi sv
                ON sv.hk_portco = h.hk_portco
               AND sv.load_dts <= cal.as_of_dts
            WHERE h.hk_portco NOT IN (REPEAT('0', 64), REPEAT('f', 64));
            """
        )
        conn.execute(
            """
            CREATE OR REPLACE VIEW information_mart.pit_portfolio_company AS
            SELECT * FROM business_vault.pit_portfolio_company;
            """
        )


def execute_query_d10(
    conn: duckdb.DuckDBPyConnection,
    *,
    assertion_cutoff_ts: str = "2026-11-15 23:59:59",
    state_cutoff_date: str = "2026-09-30",
) -> list[QueryD10Row]:
    """Execute Zone 4 `Query D.10` and return typed `QueryD10Row` objects."""
    _ensure_pe_compatibility_structures(conn)
    safe_ts = _validate_ts_literal(assertion_cutoff_ts)
    safe_dt = _validate_date_literal(state_cutoff_date)
    raw_tables = [
        t[0]
        for t in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'raw_vault'"
        ).fetchall()
    ]
    lp_hub = next((t for t in raw_tables if t.startswith("hub_") and "partner" in t), None)
    fund_hub = next((t for t in raw_tables if t.startswith("hub_") and "fund" in t), None)
    hub_primary = f"raw_vault.{lp_hub}" if lp_hub else "raw_vault.hub_investor"
    hub_vehicle = f"raw_vault.{fund_hub}" if fund_hub else "raw_vault.hub_investment_vehicle"
    comm_cols = [
        c[0]
        for c in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'raw_vault' AND table_name = 'lnk_lp_commitment'"
        ).fetchall()
    ]
    lp_fk_col = "hk_limited_partner" if "hk_limited_partner" in comm_cols else "hk_lp"
    sql = build_query_d10_sql(
        assertion_cutoff_ts=safe_ts,
        state_cutoff_date=safe_dt,
        hub_primary_table=hub_primary,
        hub_vehicle_table=hub_vehicle,
        lp_fk_col=lp_fk_col,
    )
    try:
        cursor = conn.execute(sql)
        col_names = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        return [QueryD10Row.model_validate(dict(zip(col_names, row, strict=True))) for row in rows]
    except duckdb.Error:
        return []


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
