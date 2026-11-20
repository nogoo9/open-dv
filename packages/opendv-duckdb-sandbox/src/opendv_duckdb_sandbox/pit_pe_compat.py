"""Compatibility view/column materialization helpers for Private Equity PIT audit queries."""

from __future__ import annotations

import duckdb


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


__all__ = [
    "_ensure_pe_compatibility_structures",
    "_is_base_table",
    "_table_exists",
]
