"""Zone 4: Information Mart Current Views and Bi-Temporal PIT Audit Queries."""

from __future__ import annotations

from opendv_core.models.canonical_ir import CanonicalIR
from opendv_duckdb_sandbox.compiler_4zone.hashing import _validate_ident
from opendv_duckdb_sandbox.compiler_4zone.schema_utils import _effective_pit_tables
from opendv_duckdb_sandbox.compiler_4zone.zone_3 import _build_generic_pit_view_sql


def _compile_generic_zone_4_information_mart_views(ir_obj: CanonicalIR) -> tuple[str, str]:
    """Compile Zone 4 Information Mart current views and bi-temporal PIT audit queries."""
    hub_by_name = {h.name: h for h in ir_obj.hubs}
    sat_by_name = {s.name: s for s in ir_obj.satellites}
    effective_pits = _effective_pit_tables(ir_obj)

    if not effective_pits:
        first_hub = ir_obj.hubs[0]
        hub_tbl = _validate_ident(first_hub.name)
        q1 = f"SELECT * FROM raw_vault.{hub_tbl};"
        return q1, q1

    view_defs = [
        _build_generic_pit_view_sql(pit, hub_by_name, sat_by_name) for pit in effective_pits
    ]
    first_pit = effective_pits[0]
    first_pit_tbl = _validate_ident(first_pit.name)
    as_of_col = _validate_ident(first_pit.as_of_dts_column)

    q_d10 = "\n\n".join(
        [
            "-- ZONE 4: INFORMATION MART CURRENT VIEWS (INNER JOIN OVER PIT + GHOST)",
            *view_defs,
            f"SELECT * FROM information_mart.vw_{first_pit_tbl}_current;",
        ]
    )
    q_d10b = (
        f"-- ZONE 4: BI-TEMPORAL POINT-IN-TIME AUDIT QUERY\n"
        f"SELECT *\n"
        f"FROM information_mart.vw_{first_pit_tbl}_current\n"
        f"WHERE {as_of_col} = TIMESTAMP '2026-11-15 23:59:59';"
    )
    return q_d10, q_d10b


def build_query_d10_sql(
    *,
    assertion_cutoff_ts: str = "2026-11-15 23:59:59",
    state_cutoff_date: str = "2026-09-30",
    hub_primary_table: str = "raw_vault.hub_investor",
    hub_vehicle_table: str = "raw_vault.hub_investment_vehicle",
    lp_fk_col: str = "hk_limited_partner",
) -> str:
    """Build Zone 4 `Query D.10` with parameterized investor and fund hub tables."""
    return f"""-- =============================================================================
-- ZONE 4: BI-TEMPORAL MULTIMODAL POINT-IN-TIME AUDIT QUERIES
-- =============================================================================
-- Query D.10: Golden LP Commitment, Legacy Capital Account NAV & Side-Letter Provenance
WITH audit_params AS (
    SELECT
        TIMESTAMP '{assertion_cutoff_ts}' AS assertion_cutoff_ts,
        DATE '{state_cutoff_date}'          AS state_cutoff_date
),
active_sal AS (
    SELECT
        sal.hk_lp_master,
        sal.hk_lp_duplicate
    FROM business_vault.sal_limited_partner sal
    INNER JOIN (
        SELECT
            es.hk_sal_limited_partner,
            es.is_active_flag
        FROM business_vault.esat_sal_limited_partner es
        CROSS JOIN audit_params p
        WHERE es.load_dts <= p.assertion_cutoff_ts
          AND es.effective_from_dts
              <= CAST(p.state_cutoff_date AS TIMESTAMP) + INTERVAL '23 hours 59 minutes 59 seconds'
          AND (
              es.effective_to_dts IS NULL
              OR es.effective_to_dts > CAST(p.state_cutoff_date AS TIMESTAMP)
          )
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY es.hk_sal_limited_partner
            ORDER BY es.load_dts DESC
        ) = 1
    ) es_cur
      ON es_cur.hk_sal_limited_partner = sal.hk_sal_limited_partner
     AND es_cur.is_active_flag = TRUE
),
lp_golden_map AS (
    SELECT
        h.hk_lp AS hk_lp_raw,
        COALESCE(sal.hk_lp_master, h.hk_lp) AS hk_lp_golden
    FROM {hub_primary_table} h
    LEFT JOIN active_sal sal ON sal.hk_lp_duplicate = h.hk_lp
    WHERE h.hk_lp NOT IN (REPEAT('0', 64), REPEAT('f', 64))
),
lp_core_pit AS (
    SELECT s.*
    FROM raw_vault.sat_lp_core s
    CROSS JOIN audit_params p
    WHERE s.load_dts <= p.assertion_cutoff_ts
    QUALIFY ROW_NUMBER() OVER (PARTITION BY s.hk_lp ORDER BY s.load_dts DESC) = 1
),
commitment_terms_pit AS (
    SELECT s.*
    FROM raw_vault.sat_lnk_lp_commitment_terms s
    CROSS JOIN audit_params p
    WHERE s.load_dts <= p.assertion_cutoff_ts
      AND s.commitment_date <= p.state_cutoff_date
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY s.hk_lnk_lp_commitment
        ORDER BY s.commitment_date DESC, s.load_dts DESC
    ) = 1
),
cap_acct_nav_pit AS (
    SELECT
        gm.hk_lp_golden,
        l_ca.hk_fund,
        SUM(s_nav.ending_nav_amt)       AS total_ending_nav_usd,
        SUM(s_nav.unfunded_balance_amt) AS total_unfunded_usd
    FROM raw_vault.lnk_cap_acct_alignment l_ca
    INNER JOIN lp_golden_map gm ON gm.hk_lp_raw = l_ca.hk_lp
    INNER JOIN (
        SELECT s.*
        FROM raw_vault.sat_cap_acct_quarterly_nav s
        CROSS JOIN audit_params p
        WHERE s.load_dts <= p.assertion_cutoff_ts
          AND s.reporting_quarter <= p.state_cutoff_date
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY s.hk_cap_acct
            ORDER BY s.reporting_quarter DESC, s.load_dts DESC
        ) = 1
    ) s_nav ON s_nav.hk_cap_acct = l_ca.hk_cap_acct
    GROUP BY gm.hk_lp_golden, l_ca.hk_fund
),
side_letter_pit AS (
    SELECT
        l_sl.hk_lp,
        l_sl.hk_fund,
        d.doc_sha256,
        s_sl.mgmt_fee_discount_bps,
        s_sl.mfn_election_flag,
        s_sl.co_invest_rights_flag,
        s_sl.esg_exclusion_clause,
        s_sl.extractor_model_id,
        s_sl.extraction_confidence,
        s_sl.human_review_status,
        s_sl.source_page_number,
        s_sl.source_text_quote
    FROM raw_vault.lnk_doc_lp_side_letter l_sl
    INNER JOIN raw_vault.hub_unstructured_document d ON d.hk_doc = l_sl.hk_doc
    INNER JOIN (
        SELECT s.*
        FROM business_vault.sat_lp_side_letter_llm_ext s
        CROSS JOIN audit_params p
        WHERE s.load_dts <= p.assertion_cutoff_ts
          AND s.effective_from_date <= p.state_cutoff_date
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY s.hk_lnk_doc_lp_side_letter
            ORDER BY s.effective_from_date DESC, s.load_dts DESC
        ) = 1
    ) s_sl ON s_sl.hk_lnk_doc_lp_side_letter = l_sl.hk_lnk_doc_lp_side_letter
)
SELECT
    h_lp.bkcc                                  AS golden_bkcc,
    h_lp.lp_id                                 AS golden_lp_id,
    lp_c.lp_legal_name                         AS golden_lp_name,
    lp_c.country_code                          AS domicile_iso,
    h_f.fund_code                              AS fund_code,
    ct.committed_amount                        AS committed_amount,
    ct.commitment_currency                     AS commitment_ccy,
    COALESCE(nav.total_ending_nav_usd, 0.00)   AS reconciled_legacy_nav_usd,
    COALESCE(nav.total_unfunded_usd, 0.00)     AS reconciled_unfunded_usd,
    COALESCE(TRY_CAST(sl.mgmt_fee_discount_bps AS INTEGER), 0)      AS side_letter_fee_discount_bps,
    COALESCE(TRY_CAST(sl.mfn_election_flag AS BOOLEAN), FALSE)      AS has_mfn_protection,
    sl.doc_sha256                              AS side_letter_doc_sha256,
    sl.extraction_confidence                   AS llm_confidence,
    sl.human_review_status                     AS governance_verdict,
    sl.source_page_number                      AS pdf_page
FROM {hub_primary_table} h_lp
INNER JOIN lp_core_pit lp_c
    ON lp_c.hk_lp = h_lp.hk_lp
INNER JOIN raw_vault.lnk_lp_commitment l_c
    ON l_c.{lp_fk_col} = h_lp.hk_lp
INNER JOIN {hub_vehicle_table} h_f
    ON h_f.hk_fund = l_c.hk_fund
INNER JOIN commitment_terms_pit ct
    ON ct.hk_lnk_lp_commitment = l_c.hk_lnk_lp_commitment
LEFT JOIN cap_acct_nav_pit nav
    ON nav.hk_lp_golden = h_lp.hk_lp
   AND nav.hk_fund = h_f.hk_fund
LEFT JOIN side_letter_pit sl
    ON sl.hk_lp = h_lp.hk_lp
   AND sl.hk_fund = h_f.hk_fund
WHERE h_lp.hk_lp NOT IN (REPEAT('0', 64), REPEAT('f', 64))
ORDER BY h_lp.lp_id;"""


def build_query_d10b_sql(
    *,
    assertion_cutoff_ts: str = "2026-11-15 23:59:59",
    effective_as_of_dt: str = "2026-09-30",
) -> str:
    """Build Zone 4 `Query D.10b` (Bi-Temporal Portfolio Company Valuation Audit Query)."""
    return f"""-- Query D.10b: Latest Portfolio Company Valuation Visible at the Assertion Cutoff
WITH audit_params AS (
    SELECT
        TIMESTAMP '{assertion_cutoff_ts}' AS assertion_cutoff_ts,
        DATE '{effective_as_of_dt}'         AS state_cutoff_date
)
SELECT
    p.assertion_cutoff_ts,
    p.state_cutoff_date,
    h_pc.lei_code,
    sc.company_legal_name,
    rg.sub_industry_name                       AS gics_sub_industry,
    s_val.as_of_quarter_end                    AS effective_quarter,
    s_val.load_dts                             AS valuation_asserted_dts,
    s_val.entry_ebitda_usd,
    s_val.entry_ev_multiple                    AS ev_ebitda_multiple,
    s_val.current_nav_usd                      AS enterprise_value_usd,
    ROUND(s_val.entry_ebitda_usd * s_val.entry_ev_multiple, 2) AS implied_ev_from_ebitda_usd,
    s_val.current_nav_usd                      AS reported_nav_usd,
    ai.doc_sha256                              AS ic_memo_doc_sha256,
    ai.extraction_confidence                   AS ic_memo_ai_confidence,
    ai.tier3_cosine_sim                        AS tier3_semantic_cosine,
    ai.human_review_status                     AS steward_gate_status
FROM audit_params p
INNER JOIN information_mart.pit_portfolio_company pit
    ON pit.as_of_dts = p.assertion_cutoff_ts
INNER JOIN raw_vault.hub_portfolio_company h_pc
    ON h_pc.hk_portco = pit.hk_portco
INNER JOIN raw_vault.sat_portco_core sc
    ON sc.hk_portco = pit.sat_portco_core_hk
   AND sc.load_dts  = pit.sat_portco_core_load_dts
INNER JOIN raw_vault.ref_gics_sector rg
    ON rg.gics_sub_industry_code = sc.gics_sub_industry_code
INNER JOIN raw_vault.sat_portco_valuation_mnpi s_val
    ON s_val.hk_portco = pit.sat_portco_valuation_mnpi_hk
   AND s_val.load_dts  = pit.sat_portco_valuation_mnpi_load_dts
   AND s_val.as_of_quarter_end <= p.state_cutoff_date
LEFT JOIN (
    SELECT
        l_m.hk_portco,
        d.doc_sha256,
        sp.restated_ev_multiple,
        sp.extraction_confidence,
        sp.tier3_cosine_sim,
        sp.human_review_status,
        sp.load_dts
    FROM raw_vault.lnk_doc_portco_memo l_m
    INNER JOIN raw_vault.hub_unstructured_document d ON d.hk_doc = l_m.hk_doc
    INNER JOIN business_vault.sat_lnk_doc_portco_ai_provenance sp
        ON sp.hk_lnk_doc_portco_memo = l_m.hk_lnk_doc_portco_memo
) ai
    ON ai.hk_portco = h_pc.hk_portco
   AND ai.load_dts <= p.assertion_cutoff_ts
   AND ai.restated_ev_multiple = s_val.entry_ev_multiple;"""


__all__ = [
    "_compile_generic_zone_4_information_mart_views",
    "build_query_d10_sql",
    "build_query_d10b_sql",
]
