"""4-Zone Deterministic DuckDB SQL Compiler (`CanonicalIR` -> Zones 0-4 SQL).

Compiles verified `CanonicalIR` models (including the Private Equity reference vault) into
an executable 4-Zone DuckDB SQL bundle (`docs/03` §7, `docs/05` §3.5, `docs/07` Scene 6):
- **Zone 0**: Target schemas, Bronze staging seed tables, Raw/Business Vault DDL
  (`CREATE TABLE IF NOT EXISTS`), mandatory Ghost Records (`SYSTEM_GHOST` at
  `1900-01-01 00:00:00`) & Dual Zero Keys (`'0'*64` Unknown and `'f'*64` Error with
  `rec_src = 'SYSTEM_ZERO_KEY'`), plus idempotent `zone_0_migration_sql`
  (`ALTER TABLE <schema>.<sat> ADD COLUMN IF NOT EXISTS`) for additive Satellite evolution.
- **Zone 1**: Hashed Staging Views (`staging.stg_*`) using Algorithm A.5 length-prefixed
  SHA-256 (`<len>:<val>` and `'-1:^^'` null token), `BKCC` namespace injection, and
  3-way Zero-Key sentinel branching (`'-2'` -> `'f'*64`, `NULL`/`'-1'` -> `'0'*64`).
- **Zone 2**: Insert-Only Raw Vault DML for Hubs, Reference tables, Links, and Satellites
  using idempotent `WHERE NOT EXISTS`, lexicographic `(applied_dts ASC, load_dts ASC)`
  `LAG(hash_diff)` delta detection for bi-temporal Satellites (`DV-DML-003`), and
  `LEFT JOIN ... WHERE cur.hk IS NULL OR cur.hash_diff <> src.hash_diff` anti-joins for
  uni-temporal Satellites.
- **Zone 3**: Business Vault (`sal_limited_partner`, `sal_investor_golden`,
  `esat_sal_limited_partner`, AI provenance satellites, and `pit_portfolio_company`).
- **Zone 4**: Bi-temporal Point-in-Time audit queries (`Query D.10` and `Query D.10b`).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from opendv_core.constants import (
    DEFAULT_DELIMITER,
    GHOST_DTS,
    GHOST_REC_SRC,
    GLOBAL_BKCC,
    NULL_TOKEN,
    ZERO_KEY_ERROR,
    ZERO_KEY_ERROR_TOKEN,
    ZERO_KEY_REC_SRC,
    ZERO_KEY_UNKNOWN,
    ZERO_KEY_UNKNOWN_TOKEN,
)
from opendv_core.hashing import compute_dv2_length_prefixed_sha256
from opendv_core.models.canonical_ir import (
    CanonicalIR,
    IRColumnDef,
    IRHub,
    IRLink,
    IRPitTable,
    IRReferenceTable,
    IRSatellite,
)
from opendv_duckdb_sandbox.ir_lowerer import topological_load_dag

_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_ident(name: str) -> str:
    """Validate that a SQL identifier matches `^[A-Za-z_][A-Za-z0-9_]*$`."""
    if not _SAFE_IDENT_RE.match(name):
        msg = f"Unsafe SQL identifier: {name!r}"
        raise ValueError(msg)
    return name


class Compiled4ZoneBundle(BaseModel):
    """Structured output of the 4-Zone DuckDB SQL compiler."""

    model_config = ConfigDict(extra="forbid")

    model_name: str
    ir_version: str = "1.0.0"
    topological_load_order: list[str] = Field(default_factory=list)
    zone_0_sql: str
    zone_0_migration_sql: str = ""
    zone_1_sql: str
    zone_2_sql: str
    zone_3_sql: str
    zone_4_query_d10_sql: str
    zone_4_query_d10b_sql: str

    @property
    def zone_4_sql(self) -> str:
        """Combined Zone 4 SQL containing both Query D.10 and Query D.10b."""
        return f"{self.zone_4_query_d10_sql}\n\n{self.zone_4_query_d10b_sql}"

    @property
    def zones_0_to_3_sql(self) -> str:
        """Executable DDL + DML bundle for Zones 0 through 3."""
        sections = [self.zone_0_sql]
        if self.zone_0_migration_sql.strip():
            sections.append(self.zone_0_migration_sql)
        sections.extend(
            [
                self.zone_1_sql,
                self.zone_2_sql,
                self.zone_3_sql,
            ]
        )
        return "\n\n".join(sections)

    @property
    def full_sql_bundle(self) -> str:
        """Complete 4-Zone SQL script (Zones 0-4)."""
        return f"{self.zones_0_to_3_sql}\n\n{self.zone_4_sql}"

    @property
    def full_sql(self) -> str:
        """Alias for `full_sql_bundle`."""
        return self.full_sql_bundle


def build_length_prefixed_token_sql(
    col_expr: str,
    *,
    is_business_key: bool = False,
    null_token: str = NULL_TOKEN,
) -> str:
    """Build a DuckDB SQL expression encoding `col_expr` as `<len>:<val>` or `'-1:^^'`."""
    base_cast = f"TRIM(CAST({col_expr} AS VARCHAR))"
    norm_expr = f"UPPER({base_cast})" if is_business_key else base_cast
    nullif_expr = f"NULLIF({norm_expr}, '')"
    return (
        f"CASE WHEN {nullif_expr} IS NULL THEN '{null_token}' "
        f"ELSE CAST(OCTET_LENGTH(ENCODE({nullif_expr})) AS VARCHAR) || ':' || {nullif_expr} END"
    )


def build_algorithm_a5_sha256_sql(
    columns: list[str],
    *,
    is_business_key: bool = False,
    bkcc_literal: str | None = None,
    bkcc_column_expr: str | None = None,
    sort_alphabetically: bool = False,
    delimiter: str = DEFAULT_DELIMITER,
    null_token: str = NULL_TOKEN,
    error_predicate: str | None = None,
    unknown_predicate: str | None = None,
    key_columns: list[str] | None = None,
) -> str:
    """Build a DuckDB Algorithm A.5 length-prefixed SHA-256 SQL expression.

    When `is_business_key=True`, emits a 3-way `CASE` expression routing `'-2'` /
    `error_predicate` to `REPEAT('f', 64)` (`ZERO_KEY_ERROR`) and `NULL` / `''` / `'-1'` /
    `unknown_predicate` on `key_columns` (excluding constant `bkcc` / `*__bkcc` columns by
    default) to `REPEAT('0', 64)` (`ZERO_KEY_UNKNOWN`).
    """
    ordered = sorted(columns) if sort_alphabetically else list(columns)
    tokens: list[str] = []
    if is_business_key and bkcc_column_expr and bkcc_column_expr.strip():
        clean_bkcc_col = bkcc_column_expr.strip()
        ordered = [c for c in ordered if c.strip() != clean_bkcc_col] or ordered
        tokens.append(
            build_length_prefixed_token_sql(
                clean_bkcc_col,
                is_business_key=True,
                null_token=null_token,
            )
        )
    elif is_business_key and bkcc_literal and bkcc_literal.strip():
        clean_bkcc = bkcc_literal.strip().upper()
        tokens.append(f"'{len(clean_bkcc.encode('utf-8'))}:{clean_bkcc}'")
    for col in ordered:
        tokens.append(
            build_length_prefixed_token_sql(
                col,
                is_business_key=is_business_key,
                null_token=null_token,
            )
        )
    concat_sql = f" || '{delimiter}' || ".join(tokens)
    sha_expr = f"LOWER(SHA256({concat_sql}))"
    if is_business_key and ordered:
        if key_columns is not None:
            pred_cols = list(key_columns)
        else:
            non_bkcc = [
                c for c in ordered if c.lower() != "bkcc" and not c.lower().endswith("__bkcc")
            ]
            pred_cols = non_bkcc or ordered
        default_err_pred = " OR ".join(
            f"TRIM(CAST({c} AS VARCHAR)) = '{ZERO_KEY_ERROR_TOKEN}'" for c in pred_cols
        )
        err_branch_pred = (
            f"({error_predicate}) OR ({default_err_pred})" if error_predicate else default_err_pred
        )
        default_unk_pred = " OR ".join(
            f"(NULLIF(TRIM(CAST({c} AS VARCHAR)), '') IS NULL "
            f"OR TRIM(CAST({c} AS VARCHAR)) = '{ZERO_KEY_UNKNOWN_TOKEN}')"
            for c in pred_cols
        )
        unk_branch_pred = (
            f"({unknown_predicate}) OR ({default_unk_pred})"
            if unknown_predicate
            else default_unk_pred
        )
        return (
            f"CASE WHEN {err_branch_pred} THEN REPEAT('f', 64) "
            f"WHEN {unk_branch_pred} THEN REPEAT('0', 64) "
            f"ELSE {sha_expr} END"
        )
    return sha_expr


# ============================================================================
# Normative Private Equity 4-Zone SQL Templates
# ============================================================================


def _build_pe_zone_0_sql(*, include_batch4_restatement_in_seed: bool = False) -> str:
    """Generate Zone 0 DDL, Ghost/Zero records, and Bronze seed data for the PE vault."""
    restatement_seed_portco = (
        """,
    -- Late-Arriving Backdated Q3 Valuation Restatement (2026-11-15 09:15:00 for 2026-09-30)
    ('PC-DEAL-01', '529900T8BM49AURSDO55', 'Nordic Cloud Infrastructure AB', '45102010', 'SE', 2023,
     16896550.00, 14.50, 245000000.00, DATE '2026-09-30', TIMESTAMP '2026-11-15 09:15:00')"""
        if include_batch4_restatement_in_seed
        else ""
    )

    return f"""-- =============================================================================
-- ZONE 0: TARGET SCHEMAS, BRONZE SOURCE SEEDS & GHOST / ZERO RECORDS
-- =============================================================================
CREATE SCHEMA IF NOT EXISTS investran;
CREATE SCHEMA IF NOT EXISTS dealcloud;
CREATE SCHEMA IF NOT EXISTS legacy_gp;
CREATE SCHEMA IF NOT EXISTS unstructured_vdr;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS raw_vault;
CREATE SCHEMA IF NOT EXISTS business_vault;
CREATE SCHEMA IF NOT EXISTS information_mart;

-- -----------------------------------------------------------------------------
-- 0.1 Bronze Source Tables & Seed Rows (Investran, DealCloud, Legacy GP, VDR)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE investran.src_lp_commitments (
    commitment_row_seq_id INTEGER,
    lp_id                 VARCHAR,
    lp_legal_name         VARCHAR,
    lp_type               VARCHAR,
    country_code          VARCHAR,
    tax_id_ein            VARCHAR,
    kyc_aml_risk_tier     VARCHAR,
    fund_code             VARCHAR,
    fund_legal_name       VARCHAR,
    vintage_year          INTEGER,
    fund_strategy         VARCHAR,
    base_currency_iso     VARCHAR,
    target_size_usd       DECIMAL(18,2),
    committed_amount      DECIMAL(18,2),
    commitment_currency   VARCHAR,
    closing_round         VARCHAR,
    commitment_date       DATE,
    extract_dts           TIMESTAMP
);

INSERT INTO investran.src_lp_commitments VALUES
    (1, 'LP-101', 'California Public Employees Retirement System', 'PUBLIC_PENSION',
     'US', '94-6000521', 'LOW',
     'APEX-BUYOUT-IV', 'Apex Buyout Fund IV LP', 2026, 'BUYOUT', 'USD', 2500000000.00,
     150000000.00, 'USD', 'FIRST_CLOSE', DATE '2026-03-31', TIMESTAMP '2026-10-01 06:00:00'),
    (2, 'LP-102', 'Stichting Pensioenfonds Zorg en Welzijn', 'PUBLIC_PENSION',
     'NL', 'NL-800120491', 'LOW',
     'APEX-BUYOUT-IV', 'Apex Buyout Fund IV LP', 2026, 'BUYOUT', 'USD', 2500000000.00,
     95000000.00, 'EUR', 'FIRST_CLOSE', DATE '2026-03-31', TIMESTAMP '2026-10-01 06:00:00');

CREATE OR REPLACE TABLE dealcloud.src_portfolio_companies (
    deal_id               VARCHAR,
    lei_code              VARCHAR,
    company_legal_name    VARCHAR,
    gics_sub_industry_code VARCHAR,
    hq_country_iso        VARCHAR,
    acquisition_year      INTEGER,
    entry_ebitda_usd      DECIMAL(18,2),
    entry_ev_multiple     DECIMAL(10,2),
    current_nav_usd       DECIMAL(18,2),
    as_of_quarter_end     DATE,
    extract_dts           TIMESTAMP
);

INSERT INTO dealcloud.src_portfolio_companies VALUES
    ('PC-DEAL-01', '529900T8BM49AURSDO55', 'Nordic Cloud Infrastructure AB', '45102010', 'SE', 2023,
     16896550.00, 12.80, 216280000.00, DATE '2026-09-30', TIMESTAMP '2026-10-01 07:30:00'),
    ('PC-NULL-FK', NULL, 'Unassigned Pipeline Target', '45103010', 'US', 2026,
     5000000.00, 10.00, 50000000.00, DATE '2026-09-30',
     TIMESTAMP '2026-10-01 07:30:00'),
    ('PC-ERR-FK', '-2', 'Inapplicable Pipeline Target', '45103010', 'US', 2026,
     5000000.00, 10.00, 50000000.00, DATE '2026-09-30',
     TIMESTAMP '2026-10-01 07:30:00'){restatement_seed_portco};

CREATE OR REPLACE TABLE legacy_gp.src_legacy_investors (
    investor_num          VARCHAR,
    investor_full_name    VARCHAR,
    tax_identifier        VARCHAR,
    domicile_iso2         VARCHAR,
    cap_acct_code         VARCHAR,
    fund_short_code       VARCHAR,
    ending_nav_amt        DECIMAL(18,2),
    unfunded_balance_amt  DECIMAL(18,2),
    reporting_quarter     DATE,
    extract_dts           TIMESTAMP
);

INSERT INTO legacy_gp.src_legacy_investors VALUES
    ('LP-101', 'Bavarian Municipal Pension Versorgungswerk', 'DE-129481022', 'DE',
     'CAP-IV-BAV-001', 'APEX-BUYOUT-IV', 42000000.00, 18000000.00,
     DATE '2026-09-30', TIMESTAMP '2026-10-01 08:00:00'),
    ('LP-909', 'CalPERS - California Public Employees Ret Sys', '94-6000521', 'US',
     'CAP-IV-CAL-101', 'APEX-BUYOUT-IV', 112500000.00, 37500000.00,
     DATE '2026-09-30', TIMESTAMP '2026-10-01 08:00:00');

CREATE OR REPLACE TABLE unstructured_vdr.src_document_manifest (
    doc_sha256            CHAR(64),
    doc_uri               VARCHAR,
    doc_type              VARCHAR,
    doc_title             VARCHAR,
    page_count            INTEGER,
    mime_type             VARCHAR,
    ingested_dts          TIMESTAMP
);

INSERT INTO unstructured_vdr.src_document_manifest VALUES
    ('b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9',
     's3://apex-pe-vault-bronze/legal/side-letters/calpers_side_letter_fund_iv_2026.pdf',
     'LP_SIDE_LETTER_PDF',
     'Side Letter Agreement - CalPERS & Apex Buyout Fund IV LP',
     14, 'application/pdf', TIMESTAMP '2026-10-01 09:00:00'),
    ('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
     's3://apex-pe-vault-bronze/ic-memos/2026-q3/ic_q3_valuation_nordic_cloud.pdf',
     'IC_VALUATION_MEMO_PDF',
     'Q3 2026 IC Valuation Restatement Memo - Nordic Cloud Infrastructure AB',
     9, 'application/pdf', TIMESTAMP '2026-11-14 16:30:00');

CREATE OR REPLACE TABLE unstructured_vdr.src_llm_extractions (
    extraction_id          VARCHAR,
    doc_sha256             CHAR(64),
    target_domain          VARCHAR,
    resolved_lp_id         VARCHAR,
    resolved_lp_bkcc       VARCHAR,
    resolved_fund_code     VARCHAR,
    resolved_lei_code      VARCHAR,
    mgmt_fee_discount_bps  INTEGER,
    mfn_election_flag      BOOLEAN,
    co_invest_rights_flag  BOOLEAN,
    esg_exclusion_clause   VARCHAR,
    restated_ev_multiple   DECIMAL(10,2),
    effective_from_date    DATE,
    extractor_model_id     VARCHAR,
    prompt_template_sha256 CHAR(64),
    extraction_confidence  DECIMAL(5,4),
    tier2_token_jaccard    DECIMAL(5,4),
    tier3_cosine_sim       DECIMAL(5,4),
    human_review_status    VARCHAR,
    source_page_number     INTEGER,
    source_char_start      INTEGER,
    source_char_end        INTEGER,
    source_text_quote      VARCHAR,
    extracted_dts          TIMESTAMP
);

INSERT INTO unstructured_vdr.src_llm_extractions VALUES
    ('EXT-SL-001',
     'b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9',
     'SIDE_LETTER_TERMS',
     'LP-101', 'GLOBAL', 'APEX-BUYOUT-IV', NULL,
     25, TRUE, TRUE, 'Thermal coal & controversial weapons exclusion per CalPERS ESG Policy §4.2',
     NULL, DATE '2026-03-31',
     'claude-3-7-sonnet-20260219',
     '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08',
     0.9720, 0.9410, NULL, 'AUTO_ACCEPTED_TIER_2',
     4, 118, 289,
     'The General Partner hereby grants Investor (CalPERS, Tax ID 94-6000521) '
     || 'a Management Fee Discount of 25 bps and Most Favored Nation (MFN) election rights.',
     TIMESTAMP '2026-10-01 09:05:00'),
    ('EXT-IC-002',
     'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
     'PORTCO_VALUATION_RESTATEMENT',
     NULL, NULL, 'APEX-BUYOUT-IV', '529900T8BM49AURSDO55',
     NULL, NULL, NULL, NULL,
     14.50, DATE '2026-09-30',
     'claude-3-7-sonnet-20260219',
     '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08',
     0.9140, 0.7200, 0.9480, 'STEWARD_VERIFIED',
     2, 64, 241,
     'Effective as of September 30, 2026 (Q3 Valuation Date), the Valuation Committee '
     || 'restates Nordic Cloud Infrastructure AB from 12.80x to 14.50x EV/EBITDA ($245.00M EV).',
     TIMESTAMP '2026-11-14 16:30:00');

-- -----------------------------------------------------------------------------
-- 0.2 Raw Vault & Business Vault Physical DDL
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE raw_vault.hub_fund (
    hk_fund       CHAR(64) NOT NULL PRIMARY KEY,
    fund_code     VARCHAR  NOT NULL,
    load_dts      TIMESTAMP NOT NULL,
    rec_src       VARCHAR  NOT NULL
);

CREATE OR REPLACE TABLE raw_vault.hub_limited_partner (
    hk_lp         CHAR(64) NOT NULL PRIMARY KEY,
    bkcc          VARCHAR  NOT NULL,
    lp_id         VARCHAR  NOT NULL,
    load_dts      TIMESTAMP NOT NULL,
    rec_src       VARCHAR  NOT NULL
);

CREATE OR REPLACE TABLE raw_vault.hub_portfolio_company (
    hk_portco     CHAR(64) NOT NULL PRIMARY KEY,
    lei_code      VARCHAR  NOT NULL,
    load_dts      TIMESTAMP NOT NULL,
    rec_src       VARCHAR  NOT NULL
);

CREATE OR REPLACE TABLE raw_vault.hub_capital_account (
    hk_cap_acct   CHAR(64) NOT NULL PRIMARY KEY,
    bkcc          VARCHAR  NOT NULL,
    cap_acct_code VARCHAR  NOT NULL,
    load_dts      TIMESTAMP NOT NULL,
    rec_src       VARCHAR  NOT NULL
);

CREATE OR REPLACE TABLE raw_vault.hub_unstructured_document (
    hk_doc        CHAR(64) NOT NULL PRIMARY KEY,
    doc_sha256    CHAR(64) NOT NULL,
    load_dts      TIMESTAMP NOT NULL,
    rec_src       VARCHAR  NOT NULL
);

CREATE OR REPLACE TABLE raw_vault.ref_iso_currency (
    currency_iso_code CHAR(3) NOT NULL PRIMARY KEY,
    currency_name     VARCHAR NOT NULL,
    numeric_code      INTEGER NOT NULL,
    minor_unit_scale  INTEGER NOT NULL,
    load_dts          TIMESTAMP NOT NULL,
    rec_src           VARCHAR NOT NULL
);

CREATE OR REPLACE TABLE raw_vault.ref_gics_sector (
    gics_sub_industry_code VARCHAR NOT NULL PRIMARY KEY,
    sector_name            VARCHAR NOT NULL,
    industry_group_name    VARCHAR NOT NULL,
    sub_industry_name      VARCHAR NOT NULL,
    load_dts               TIMESTAMP NOT NULL,
    rec_src                VARCHAR NOT NULL
);

CREATE OR REPLACE TABLE raw_vault.lnk_lp_commitment (
    hk_lnk_lp_commitment CHAR(64) NOT NULL PRIMARY KEY,
    hk_lp                CHAR(64) NOT NULL,
    hk_fund              CHAR(64) NOT NULL,
    load_dts             TIMESTAMP NOT NULL,
    rec_src              VARCHAR  NOT NULL
);

CREATE OR REPLACE TABLE raw_vault.lnk_fund_portco_investment (
    hk_lnk_fund_portco_investment CHAR(64) NOT NULL PRIMARY KEY,
    hk_fund                       CHAR(64) NOT NULL,
    hk_portco                     CHAR(64) NOT NULL,
    load_dts                      TIMESTAMP NOT NULL,
    rec_src                       VARCHAR  NOT NULL
);

CREATE OR REPLACE TABLE raw_vault.lnk_cap_acct_alignment (
    hk_lnk_cap_acct_alignment CHAR(64) NOT NULL PRIMARY KEY,
    hk_cap_acct               CHAR(64) NOT NULL,
    hk_lp                     CHAR(64) NOT NULL,
    hk_fund                   CHAR(64) NOT NULL,
    load_dts                  TIMESTAMP NOT NULL,
    rec_src                   VARCHAR  NOT NULL
);

CREATE OR REPLACE TABLE raw_vault.lnk_doc_lp_side_letter (
    hk_lnk_doc_lp_side_letter CHAR(64) NOT NULL PRIMARY KEY,
    hk_doc                    CHAR(64) NOT NULL,
    hk_lp                     CHAR(64) NOT NULL,
    hk_fund                   CHAR(64) NOT NULL,
    load_dts                  TIMESTAMP NOT NULL,
    rec_src                   VARCHAR  NOT NULL
);

CREATE OR REPLACE TABLE raw_vault.lnk_doc_portco_memo (
    hk_lnk_doc_portco_memo CHAR(64) NOT NULL PRIMARY KEY,
    hk_doc                 CHAR(64) NOT NULL,
    hk_portco              CHAR(64) NOT NULL,
    load_dts               TIMESTAMP NOT NULL,
    rec_src                VARCHAR  NOT NULL
);

CREATE OR REPLACE TABLE business_vault.sal_limited_partner (
    hk_sal_limited_partner CHAR(64) NOT NULL PRIMARY KEY,
    hk_lp_master           CHAR(64) NOT NULL,
    hk_lp_duplicate        CHAR(64) NOT NULL,
    load_dts               TIMESTAMP NOT NULL,
    rec_src                VARCHAR  NOT NULL
);

CREATE OR REPLACE VIEW business_vault.sal_investor_golden AS
SELECT * FROM business_vault.sal_limited_partner;

CREATE OR REPLACE TABLE raw_vault.sat_fund_core (
    hk_fund            CHAR(64)      NOT NULL,
    load_dts           TIMESTAMP     NOT NULL,
    rec_src            VARCHAR       NOT NULL,
    hash_diff          CHAR(64)      NOT NULL,
    fund_legal_name    VARCHAR,
    vintage_year       INTEGER,
    fund_strategy      VARCHAR,
    base_currency_iso  VARCHAR,
    target_size_usd    DECIMAL(18,2),
    PRIMARY KEY (hk_fund, load_dts)
);

CREATE OR REPLACE TABLE raw_vault.sat_lp_core (
    hk_lp              CHAR(64)  NOT NULL,
    load_dts           TIMESTAMP NOT NULL,
    rec_src            VARCHAR   NOT NULL,
    hash_diff          CHAR(64)  NOT NULL,
    lp_legal_name      VARCHAR,
    lp_type            VARCHAR,
    country_code       VARCHAR,
    PRIMARY KEY (hk_lp, load_dts)
);

CREATE OR REPLACE TABLE raw_vault.sat_lp_kyc_pii (
    hk_lp              CHAR(64)  NOT NULL,
    load_dts           TIMESTAMP NOT NULL,
    rec_src            VARCHAR   NOT NULL,
    hash_diff          CHAR(64)  NOT NULL,
    tax_id_ein         VARCHAR,
    kyc_aml_risk_tier  VARCHAR,
    PRIMARY KEY (hk_lp, load_dts)
);

CREATE OR REPLACE TABLE raw_vault.sat_portco_core (
    hk_portco              CHAR(64)  NOT NULL,
    load_dts               TIMESTAMP NOT NULL,
    rec_src                VARCHAR   NOT NULL,
    hash_diff              CHAR(64)  NOT NULL,
    company_legal_name     VARCHAR,
    gics_sub_industry_code VARCHAR,
    hq_country_iso         VARCHAR,
    acquisition_year       INTEGER,
    PRIMARY KEY (hk_portco, load_dts)
);

CREATE OR REPLACE TABLE raw_vault.sat_portco_valuation_mnpi (
    hk_portco          CHAR(64)      NOT NULL,
    load_dts           TIMESTAMP     NOT NULL,
    applied_dts        TIMESTAMP     NOT NULL,
    rec_src            VARCHAR       NOT NULL,
    hash_diff          CHAR(64)      NOT NULL,
    entry_ebitda_usd   DECIMAL(18,2),
    entry_ev_multiple  DECIMAL(10,2),
    current_nav_usd    DECIMAL(18,2),
    as_of_quarter_end  DATE,
    PRIMARY KEY (hk_portco, load_dts)
);

CREATE OR REPLACE TABLE raw_vault.sat_cap_acct_quarterly_nav (
    hk_cap_acct          CHAR(64)      NOT NULL,
    load_dts             TIMESTAMP     NOT NULL,
    applied_dts          TIMESTAMP     NOT NULL,
    rec_src              VARCHAR       NOT NULL,
    hash_diff            CHAR(64)      NOT NULL,
    ending_nav_amt       DECIMAL(18,2),
    unfunded_balance_amt DECIMAL(18,2),
    reporting_quarter    DATE,
    PRIMARY KEY (hk_cap_acct, load_dts)
);

CREATE OR REPLACE TABLE raw_vault.sat_doc_metadata (
    hk_doc      CHAR(64)  NOT NULL,
    load_dts    TIMESTAMP NOT NULL,
    rec_src     VARCHAR   NOT NULL,
    hash_diff   CHAR(64)  NOT NULL,
    doc_uri     VARCHAR,
    doc_type    VARCHAR,
    doc_title   VARCHAR,
    page_count  INTEGER,
    mime_type   VARCHAR,
    PRIMARY KEY (hk_doc, load_dts)
);

CREATE OR REPLACE TABLE raw_vault.sat_lnk_lp_commitment_terms (
    hk_lnk_lp_commitment CHAR(64)      NOT NULL,
    load_dts             TIMESTAMP     NOT NULL,
    applied_dts          TIMESTAMP     NOT NULL,
    rec_src              VARCHAR       NOT NULL,
    hash_diff            CHAR(64)      NOT NULL,
    committed_amount     DECIMAL(18,2),
    commitment_currency  VARCHAR,
    closing_round        VARCHAR,
    commitment_date      DATE,
    PRIMARY KEY (hk_lnk_lp_commitment, load_dts)
);

CREATE OR REPLACE TABLE raw_vault.sat_lnk_fund_portco_deal (
    hk_lnk_fund_portco_investment CHAR(64)  NOT NULL,
    load_dts                      TIMESTAMP NOT NULL,
    rec_src                       VARCHAR   NOT NULL,
    hash_diff                     CHAR(64)  NOT NULL,
    deal_id                       VARCHAR,
    PRIMARY KEY (hk_lnk_fund_portco_investment, load_dts)
);

CREATE OR REPLACE TABLE business_vault.esat_sal_limited_partner (
    hk_sal_limited_partner  CHAR(64)      NOT NULL,
    load_dts                TIMESTAMP     NOT NULL,
    rec_src                 VARCHAR       NOT NULL,
    hash_diff               CHAR(64)      NOT NULL,
    effective_from_dts      TIMESTAMP,
    effective_to_dts        TIMESTAMP,
    is_active_flag          BOOLEAN,
    resolution_method       VARCHAR,
    match_confidence_score  DECIMAL(5,4),
    steward_approved_by     VARCHAR,
    PRIMARY KEY (hk_sal_limited_partner, load_dts)
);

CREATE OR REPLACE TABLE business_vault.sat_lp_side_letter_llm_ext (
    hk_lnk_doc_lp_side_letter CHAR(64)     NOT NULL,
    load_dts                  TIMESTAMP    NOT NULL,
    applied_dts               TIMESTAMP    NOT NULL,
    rec_src                   VARCHAR      NOT NULL,
    hash_diff                 CHAR(64)     NOT NULL,
    mgmt_fee_discount_bps     INTEGER,
    mfn_election_flag         BOOLEAN,
    co_invest_rights_flag     BOOLEAN,
    esg_exclusion_clause      VARCHAR,
    effective_from_date       DATE,
    extractor_model_id        VARCHAR,
    prompt_template_sha256    CHAR(64),
    extraction_confidence     DECIMAL(5,4),
    tier2_token_jaccard       DECIMAL(5,4),
    human_review_status       VARCHAR,
    source_page_number        INTEGER,
    source_char_start         INTEGER,
    source_char_end           INTEGER,
    source_text_quote         VARCHAR,
    PRIMARY KEY (hk_lnk_doc_lp_side_letter, load_dts)
);

CREATE OR REPLACE TABLE business_vault.sat_lnk_doc_portco_ai_provenance (
    hk_lnk_doc_portco_memo CHAR(64)      NOT NULL,
    load_dts               TIMESTAMP     NOT NULL,
    applied_dts            TIMESTAMP     NOT NULL,
    rec_src                VARCHAR       NOT NULL,
    hash_diff              CHAR(64)      NOT NULL,
    restated_ev_multiple   DECIMAL(10,2),
    effective_from_date    DATE,
    extractor_model_id     VARCHAR,
    prompt_template_sha256 CHAR(64),
    extraction_confidence  DECIMAL(5,4),
    tier2_token_jaccard    DECIMAL(5,4),
    tier3_cosine_sim       DECIMAL(5,4),
    human_review_status    VARCHAR,
    source_page_number     INTEGER,
    source_char_start      INTEGER,
    source_char_end        INTEGER,
    source_text_quote      VARCHAR,
    PRIMARY KEY (hk_lnk_doc_portco_memo, load_dts)
);

-- -----------------------------------------------------------------------------
-- 0.3 Mandatory Dual Zero-Key Sentinel Rows ('0'*64 Unknown & 'f'*64 Error)
--     and Ghost Records ('0'*64 at '1900-01-01 00:00:00', 'SYSTEM_GHOST')
-- -----------------------------------------------------------------------------
INSERT INTO raw_vault.hub_fund VALUES
    (REPEAT('0', 64), '-1', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    (REPEAT('f', 64), '-2', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

INSERT INTO raw_vault.hub_limited_partner VALUES
    (REPEAT('0', 64), 'SYSTEM', '-1', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    (REPEAT('f', 64), 'SYSTEM', '-2', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

INSERT INTO raw_vault.hub_portfolio_company VALUES
    (REPEAT('0', 64), '-1', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    (REPEAT('f', 64), '-2', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

INSERT INTO raw_vault.hub_capital_account VALUES
    (REPEAT('0', 64), 'SYSTEM', '-1', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    (REPEAT('f', 64), 'SYSTEM', '-2', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

INSERT INTO raw_vault.hub_unstructured_document VALUES
    (REPEAT('0', 64), REPEAT('0', 64), TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    (REPEAT('f', 64), REPEAT('f', 64), TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

INSERT INTO raw_vault.lnk_lp_commitment VALUES
    (REPEAT('0', 64), REPEAT('0', 64), REPEAT('0', 64),
     TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    (REPEAT('f', 64), REPEAT('f', 64), REPEAT('f', 64),
     TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

INSERT INTO raw_vault.lnk_fund_portco_investment VALUES
    (REPEAT('0', 64), REPEAT('0', 64), REPEAT('0', 64),
     TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    (REPEAT('f', 64), REPEAT('f', 64), REPEAT('f', 64),
     TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

INSERT INTO raw_vault.lnk_cap_acct_alignment VALUES
    (REPEAT('0', 64), REPEAT('0', 64), REPEAT('0', 64), REPEAT('0', 64),
     TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    (REPEAT('f', 64), REPEAT('f', 64), REPEAT('f', 64), REPEAT('f', 64),
     TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

INSERT INTO raw_vault.lnk_doc_lp_side_letter VALUES
    (REPEAT('0', 64), REPEAT('0', 64), REPEAT('0', 64), REPEAT('0', 64),
     TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    (REPEAT('f', 64), REPEAT('f', 64), REPEAT('f', 64), REPEAT('f', 64),
     TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

INSERT INTO raw_vault.lnk_doc_portco_memo VALUES
    (REPEAT('0', 64), REPEAT('0', 64), REPEAT('0', 64),
     TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    (REPEAT('f', 64), REPEAT('f', 64), REPEAT('f', 64),
     TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

INSERT INTO business_vault.sal_limited_partner VALUES
    (REPEAT('0', 64), REPEAT('0', 64), REPEAT('0', 64),
     TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    (REPEAT('f', 64), REPEAT('f', 64), REPEAT('f', 64),
     TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

INSERT INTO raw_vault.sat_fund_core VALUES
    (REPEAT('0', 64), TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', REPEAT('0', 64),
     NULL, NULL, NULL, NULL, NULL);

INSERT INTO raw_vault.sat_lp_core VALUES
    (REPEAT('0', 64), TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', REPEAT('0', 64),
     NULL, NULL, NULL);

INSERT INTO raw_vault.sat_lp_kyc_pii VALUES
    (REPEAT('0', 64), TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', REPEAT('0', 64), NULL, NULL);

INSERT INTO raw_vault.sat_portco_core VALUES
    (REPEAT('0', 64), TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', REPEAT('0', 64),
     NULL, NULL, NULL, NULL);

INSERT INTO raw_vault.sat_portco_valuation_mnpi VALUES
    (REPEAT('0', 64), TIMESTAMP '1900-01-01 00:00:00', TIMESTAMP '1900-01-01 00:00:00',
     'SYSTEM_GHOST', REPEAT('0', 64), NULL, NULL, NULL, NULL);

INSERT INTO raw_vault.sat_cap_acct_quarterly_nav VALUES
    (REPEAT('0', 64), TIMESTAMP '1900-01-01 00:00:00', TIMESTAMP '1900-01-01 00:00:00',
     'SYSTEM_GHOST', REPEAT('0', 64), NULL, NULL, NULL);

INSERT INTO raw_vault.sat_doc_metadata VALUES
    (REPEAT('0', 64), TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', REPEAT('0', 64),
     NULL, NULL, NULL, NULL, NULL);

INSERT INTO raw_vault.sat_lnk_lp_commitment_terms VALUES
    (REPEAT('0', 64), TIMESTAMP '1900-01-01 00:00:00', TIMESTAMP '1900-01-01 00:00:00',
     'SYSTEM_GHOST', REPEAT('0', 64), NULL, NULL, NULL, NULL);

INSERT INTO raw_vault.sat_lnk_fund_portco_deal VALUES
    (REPEAT('0', 64), TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', REPEAT('0', 64), NULL);

INSERT INTO business_vault.esat_sal_limited_partner VALUES
    (REPEAT('0', 64), TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', REPEAT('0', 64),
     TIMESTAMP '1900-01-01 00:00:00', NULL, FALSE, 'GHOST', 0.0000, 'SYSTEM');

INSERT INTO business_vault.sat_lp_side_letter_llm_ext VALUES
    (REPEAT('0', 64), TIMESTAMP '1900-01-01 00:00:00', TIMESTAMP '1900-01-01 00:00:00',
     'SYSTEM_GHOST', REPEAT('0', 64), NULL, NULL, NULL, NULL, NULL,
     'SYSTEM_GHOST', REPEAT('0', 64), 1.0000, 1.0000, 'GHOST', 0, 0, 0, 'SYSTEM_GHOST');

INSERT INTO business_vault.sat_lnk_doc_portco_ai_provenance VALUES
    (REPEAT('0', 64), TIMESTAMP '1900-01-01 00:00:00', TIMESTAMP '1900-01-01 00:00:00',
     'SYSTEM_GHOST', REPEAT('0', 64), NULL, NULL, 'SYSTEM_GHOST', REPEAT('0', 64),
     1.0000, 1.0000, 1.0000, 'GHOST', 0, 0, 0, 'SYSTEM_GHOST');"""


def _build_pe_zone_1_sql() -> str:
    """Generate Zone 1 Hashed Staging Views using Algorithm A.5 (`<len>:<val>` & `'-1:^^'`)."""
    return """-- =============================================================================
-- ZONE 1: HASHED STAGING VIEWS (ALGORITHM A.5 LENGTH-PREFIXED SHA-256 & '-1:^^')
-- =============================================================================
CREATE OR REPLACE VIEW staging.stg_investran_lp_commitments AS
WITH norm AS (
    SELECT
        'GLOBAL' AS bkcc,
        UPPER(TRIM(CAST(lp_id AS VARCHAR))) AS lp_id_norm,
        UPPER(TRIM(CAST(fund_code AS VARCHAR))) AS fund_code_norm,
        TRIM(CAST(lp_legal_name AS VARCHAR)) AS lp_legal_name,
        TRIM(CAST(lp_type AS VARCHAR)) AS lp_type,
        UPPER(TRIM(CAST(country_code AS VARCHAR))) AS country_code,
        TRIM(CAST(tax_id_ein AS VARCHAR)) AS tax_id_ein,
        UPPER(TRIM(CAST(kyc_aml_risk_tier AS VARCHAR))) AS kyc_aml_risk_tier,
        TRIM(CAST(fund_legal_name AS VARCHAR)) AS fund_legal_name,
        vintage_year,
        UPPER(TRIM(CAST(fund_strategy AS VARCHAR))) AS fund_strategy,
        UPPER(TRIM(CAST(base_currency_iso AS VARCHAR))) AS base_currency_iso,
        target_size_usd,
        committed_amount,
        UPPER(TRIM(CAST(commitment_currency AS VARCHAR))) AS commitment_currency,
        UPPER(TRIM(CAST(closing_round AS VARCHAR))) AS closing_round,
        commitment_date,
        CAST(commitment_date AS TIMESTAMP) AS applied_dts,
        extract_dts AS load_dts,
        'INVESTRAN_ADMIN' AS rec_src
    FROM investran.src_lp_commitments
),
keys AS (
    SELECT
        *,
        CASE
            WHEN lp_id_norm = '-2' THEN REPEAT('f', 64)
            WHEN lp_id_norm IS NULL OR lp_id_norm = '' OR lp_id_norm = '-1' THEN REPEAT('0', 64)
            ELSE LOWER(SHA256(
                '6:GLOBAL||'
                || CAST(OCTET_LENGTH(ENCODE(lp_id_norm)) AS VARCHAR)
                || ':' || lp_id_norm
            ))
        END AS hk_lp,
        CASE
            WHEN fund_code_norm = '-2' THEN REPEAT('f', 64)
            WHEN fund_code_norm IS NULL OR fund_code_norm = '' OR fund_code_norm = '-1'
                THEN REPEAT('0', 64)
            ELSE LOWER(SHA256(
                CAST(OCTET_LENGTH(ENCODE(fund_code_norm)) AS VARCHAR) || ':' || fund_code_norm
            ))
        END AS hk_fund
    FROM norm
)
SELECT
    hk_lp,
    hk_fund,
    LOWER(SHA256('64:' || hk_lp || '||64:' || hk_fund)) AS hk_lnk_lp_commitment,
    bkcc,
    lp_id_norm AS lp_id,
    fund_code_norm AS fund_code,
    lp_legal_name,
    lp_type,
    country_code,
    tax_id_ein,
    kyc_aml_risk_tier,
    fund_legal_name,
    vintage_year,
    fund_strategy,
    base_currency_iso,
    target_size_usd,
    committed_amount,
    commitment_currency,
    closing_round,
    commitment_date,
    applied_dts,
    load_dts,
    rec_src,
    LOWER(SHA256(CONCAT_WS('||',
        CASE WHEN lp_legal_name IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(lp_legal_name)) AS VARCHAR) || ':' || lp_legal_name END,
        CASE WHEN lp_type IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(lp_type)) AS VARCHAR) || ':' || lp_type END,
        CASE WHEN country_code IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(country_code)) AS VARCHAR) || ':' || country_code END
    ))) AS hash_diff_lp_core,
    LOWER(SHA256(CONCAT_WS('||',
        CASE WHEN tax_id_ein IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(tax_id_ein)) AS VARCHAR) || ':' || tax_id_ein END,
        CASE WHEN kyc_aml_risk_tier IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(kyc_aml_risk_tier)) AS VARCHAR)
                  || ':' || kyc_aml_risk_tier END
    ))) AS hash_diff_lp_kyc_pii,
    LOWER(SHA256(CONCAT_WS('||',
        CASE WHEN fund_legal_name IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(fund_legal_name)) AS VARCHAR)
                  || ':' || fund_legal_name END,
        CASE WHEN vintage_year IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(CAST(vintage_year AS VARCHAR))) AS VARCHAR)
                  || ':' || CAST(vintage_year AS VARCHAR) END,
        CASE WHEN fund_strategy IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(fund_strategy)) AS VARCHAR) || ':' || fund_strategy END,
        CASE WHEN base_currency_iso IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(base_currency_iso)) AS VARCHAR)
                  || ':' || base_currency_iso END,
        CASE WHEN target_size_usd IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(CAST(target_size_usd AS VARCHAR))) AS VARCHAR)
                  || ':' || CAST(target_size_usd AS VARCHAR) END
    ))) AS hash_diff_fund_core,
    LOWER(SHA256(CONCAT_WS('||',
        CASE WHEN committed_amount IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(CAST(committed_amount AS VARCHAR))) AS VARCHAR)
                  || ':' || CAST(committed_amount AS VARCHAR) END,
        CASE WHEN commitment_currency IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(commitment_currency)) AS VARCHAR)
                  || ':' || commitment_currency END,
        CASE WHEN closing_round IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(closing_round)) AS VARCHAR) || ':' || closing_round END,
        CASE WHEN commitment_date IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(CAST(commitment_date AS VARCHAR))) AS VARCHAR)
                  || ':' || CAST(commitment_date AS VARCHAR) END
    ))) AS hash_diff_commitment_terms
FROM keys;

CREATE OR REPLACE VIEW staging.stg_dealcloud_portfolio_companies AS
WITH norm AS (
    SELECT
        TRIM(CAST(deal_id AS VARCHAR)) AS deal_id,
        UPPER(TRIM(CAST(lei_code AS VARCHAR))) AS lei_code_norm,
        'APEX-BUYOUT-IV' AS fund_code_norm,
        TRIM(CAST(company_legal_name AS VARCHAR)) AS company_legal_name,
        TRIM(CAST(gics_sub_industry_code AS VARCHAR)) AS gics_sub_industry_code,
        UPPER(TRIM(CAST(hq_country_iso AS VARCHAR))) AS hq_country_iso,
        acquisition_year,
        entry_ebitda_usd,
        entry_ev_multiple,
        current_nav_usd,
        as_of_quarter_end,
        CAST(as_of_quarter_end AS TIMESTAMP)
            + INTERVAL '23 hours 59 minutes 59 seconds' AS applied_dts,
        extract_dts AS load_dts,
        'DEALCLOUD_CRM' AS rec_src
    FROM dealcloud.src_portfolio_companies
),
keys AS (
    SELECT
        *,
        CASE
            WHEN lei_code_norm = '-2' THEN REPEAT('f', 64)
            WHEN lei_code_norm IS NULL OR lei_code_norm = '' OR lei_code_norm = '-1'
                THEN REPEAT('0', 64)
            ELSE LOWER(SHA256(
                CAST(OCTET_LENGTH(ENCODE(lei_code_norm)) AS VARCHAR) || ':' || lei_code_norm
            ))
        END AS hk_portco,
        LOWER(SHA256('14:APEX-BUYOUT-IV')) AS hk_fund
    FROM norm
)
SELECT
    hk_portco,
    hk_fund,
    LOWER(SHA256('64:' || hk_fund || '||64:' || hk_portco)) AS hk_lnk_fund_portco_investment,
    COALESCE(lei_code_norm, '-1') AS lei_code,
    deal_id,
    company_legal_name,
    gics_sub_industry_code,
    hq_country_iso,
    acquisition_year,
    entry_ebitda_usd,
    entry_ev_multiple,
    current_nav_usd,
    as_of_quarter_end,
    applied_dts,
    load_dts,
    rec_src,
    LOWER(SHA256(CONCAT_WS('||',
        CASE WHEN company_legal_name IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(company_legal_name)) AS VARCHAR)
                  || ':' || company_legal_name END,
        CASE WHEN gics_sub_industry_code IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(gics_sub_industry_code)) AS VARCHAR)
                  || ':' || gics_sub_industry_code END,
        CASE WHEN hq_country_iso IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(hq_country_iso)) AS VARCHAR)
                  || ':' || hq_country_iso END,
        CASE WHEN acquisition_year IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(CAST(acquisition_year AS VARCHAR))) AS VARCHAR)
                  || ':' || CAST(acquisition_year AS VARCHAR) END
    ))) AS hash_diff_portco_core,
    LOWER(SHA256(CONCAT_WS('||',
        CASE WHEN entry_ebitda_usd IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(CAST(entry_ebitda_usd AS VARCHAR))) AS VARCHAR)
                  || ':' || CAST(entry_ebitda_usd AS VARCHAR) END,
        CASE WHEN entry_ev_multiple IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(CAST(entry_ev_multiple AS VARCHAR))) AS VARCHAR)
                  || ':' || CAST(entry_ev_multiple AS VARCHAR) END,
        CASE WHEN current_nav_usd IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(CAST(current_nav_usd AS VARCHAR))) AS VARCHAR)
                  || ':' || CAST(current_nav_usd AS VARCHAR) END,
        CASE WHEN as_of_quarter_end IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(CAST(as_of_quarter_end AS VARCHAR))) AS VARCHAR)
                  || ':' || CAST(as_of_quarter_end AS VARCHAR) END
    ))) AS hash_diff_portco_valuation,
    LOWER(SHA256(
        CASE WHEN deal_id IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(deal_id)) AS VARCHAR) || ':' || deal_id END
    )) AS hash_diff_deal
FROM keys;

CREATE OR REPLACE VIEW staging.stg_legacy_gp_investors AS
WITH norm AS (
    SELECT
        'LEGACY_GP_ADMIN' AS bkcc,
        UPPER(TRIM(CAST(investor_num AS VARCHAR))) AS lp_id_norm,
        UPPER(TRIM(CAST(cap_acct_code AS VARCHAR))) AS cap_acct_code_norm,
        UPPER(TRIM(CAST(fund_short_code AS VARCHAR))) AS fund_code_norm,
        TRIM(CAST(investor_full_name AS VARCHAR)) AS lp_legal_name,
        'LEGACY_INSTITUTIONAL' AS lp_type,
        UPPER(TRIM(CAST(domicile_iso2 AS VARCHAR))) AS country_code,
        TRIM(CAST(tax_identifier AS VARCHAR)) AS tax_id_ein,
        'STANDARD' AS kyc_aml_risk_tier,
        ending_nav_amt,
        unfunded_balance_amt,
        reporting_quarter,
        CAST(reporting_quarter AS TIMESTAMP)
            + INTERVAL '23 hours 59 minutes 59 seconds' AS applied_dts,
        extract_dts AS load_dts,
        'LEGACY_GP_ADMIN' AS rec_src
    FROM legacy_gp.src_legacy_investors
),
keys AS (
    SELECT
        *,
        CASE
            WHEN lp_id_norm = '-2' THEN REPEAT('f', 64)
            WHEN lp_id_norm IS NULL OR lp_id_norm = '' OR lp_id_norm = '-1' THEN REPEAT('0', 64)
            ELSE LOWER(SHA256(
                '15:LEGACY_GP_ADMIN||'
                || CAST(OCTET_LENGTH(ENCODE(lp_id_norm)) AS VARCHAR)
                || ':' || lp_id_norm
            ))
        END AS hk_lp,
        CASE
            WHEN cap_acct_code_norm = '-2' THEN REPEAT('f', 64)
            WHEN cap_acct_code_norm IS NULL OR cap_acct_code_norm = '' OR cap_acct_code_norm = '-1'
                THEN REPEAT('0', 64)
            ELSE LOWER(SHA256(
                '15:LEGACY_GP_ADMIN||'
                || CAST(OCTET_LENGTH(ENCODE(cap_acct_code_norm)) AS VARCHAR)
                || ':' || cap_acct_code_norm
            ))
        END AS hk_cap_acct,
        CASE
            WHEN fund_code_norm = '-2' THEN REPEAT('f', 64)
            WHEN fund_code_norm IS NULL OR fund_code_norm = '' OR fund_code_norm = '-1'
                THEN REPEAT('0', 64)
            ELSE LOWER(SHA256(
                CAST(OCTET_LENGTH(ENCODE(fund_code_norm)) AS VARCHAR) || ':' || fund_code_norm
            ))
        END AS hk_fund
    FROM norm
)
SELECT
    hk_lp,
    hk_cap_acct,
    hk_fund,
    LOWER(SHA256(
        '64:' || hk_cap_acct || '||64:' || hk_lp || '||64:' || hk_fund
    )) AS hk_lnk_cap_acct_alignment,
    bkcc,
    lp_id_norm AS lp_id,
    cap_acct_code_norm AS cap_acct_code,
    fund_code_norm AS fund_code,
    lp_legal_name,
    lp_type,
    country_code,
    tax_id_ein,
    kyc_aml_risk_tier,
    ending_nav_amt,
    unfunded_balance_amt,
    reporting_quarter,
    applied_dts,
    load_dts,
    rec_src,
    LOWER(SHA256(CONCAT_WS('||',
        CASE WHEN lp_legal_name IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(lp_legal_name)) AS VARCHAR) || ':' || lp_legal_name END,
        CASE WHEN lp_type IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(lp_type)) AS VARCHAR) || ':' || lp_type END,
        CASE WHEN country_code IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(country_code)) AS VARCHAR) || ':' || country_code END
    ))) AS hash_diff_lp_core,
    LOWER(SHA256(CONCAT_WS('||',
        CASE WHEN tax_id_ein IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(tax_id_ein)) AS VARCHAR) || ':' || tax_id_ein END,
        CASE WHEN kyc_aml_risk_tier IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(kyc_aml_risk_tier)) AS VARCHAR)
                  || ':' || kyc_aml_risk_tier END
    ))) AS hash_diff_lp_kyc_pii,
    LOWER(SHA256(CONCAT_WS('||',
        CASE WHEN ending_nav_amt IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(CAST(ending_nav_amt AS VARCHAR))) AS VARCHAR)
                  || ':' || CAST(ending_nav_amt AS VARCHAR) END,
        CASE WHEN unfunded_balance_amt IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(CAST(unfunded_balance_amt AS VARCHAR))) AS VARCHAR)
                  || ':' || CAST(unfunded_balance_amt AS VARCHAR) END,
        CASE WHEN reporting_quarter IS NULL THEN '-1:^^'
             ELSE CAST(OCTET_LENGTH(ENCODE(CAST(reporting_quarter AS VARCHAR))) AS VARCHAR)
                  || ':' || CAST(reporting_quarter AS VARCHAR) END
    ))) AS hash_diff_cap_acct_nav
FROM keys;

CREATE OR REPLACE VIEW staging.stg_vdr_document_manifest AS
SELECT
    LOWER(SHA256('64:' || LOWER(TRIM(doc_sha256)))) AS hk_doc,
    LOWER(TRIM(doc_sha256)) AS doc_sha256,
    doc_uri,
    doc_type,
    doc_title,
    page_count,
    mime_type,
    ingested_dts AS load_dts,
    'UNSTRUCTURED_VDR' AS rec_src,
    LOWER(SHA256(CONCAT_WS('||',
        CAST(OCTET_LENGTH(ENCODE(doc_uri)) AS VARCHAR) || ':' || doc_uri,
        CAST(OCTET_LENGTH(ENCODE(doc_type)) AS VARCHAR) || ':' || doc_type,
        CAST(OCTET_LENGTH(ENCODE(doc_title)) AS VARCHAR) || ':' || doc_title,
        CAST(OCTET_LENGTH(ENCODE(CAST(page_count AS VARCHAR))) AS VARCHAR)
            || ':' || CAST(page_count AS VARCHAR),
        CAST(OCTET_LENGTH(ENCODE(mime_type)) AS VARCHAR) || ':' || mime_type
    ))) AS hash_diff_doc_metadata
FROM unstructured_vdr.src_document_manifest;"""


def _build_pe_zone_2_sql() -> str:
    """Generate Zone 2 Insert-Only Raw Vault DML for Hubs, Ref tables, Links, and Satellites."""
    return """-- =============================================================================
-- ZONE 2: INSERT-ONLY RAW VAULT LOADS (HUBS, REFS, LINKS, SATELLITES)
-- =============================================================================
INSERT INTO raw_vault.ref_iso_currency
SELECT * FROM (
    VALUES
        ('USD', 'United States Dollar', 840, 2, TIMESTAMP '2026-01-01 00:00:00', 'ISO_4217'),
        ('EUR', 'Euro',                 978, 2, TIMESTAMP '2026-01-01 00:00:00', 'ISO_4217'),
        ('SEK', 'Swedish Krona',        752, 2, TIMESTAMP '2026-01-01 00:00:00', 'ISO_4217')
) AS v(currency_iso_code, currency_name, numeric_code, minor_unit_scale, load_dts, rec_src)
WHERE NOT EXISTS (
    SELECT 1 FROM raw_vault.ref_iso_currency r WHERE r.currency_iso_code = v.currency_iso_code
);

INSERT INTO raw_vault.ref_gics_sector
SELECT * FROM (
    VALUES
        ('45102010', 'Information Technology', 'Software & Services',
         'IT Consulting & Other Services', TIMESTAMP '2026-01-01 00:00:00', 'MSCI_GICS_2026'),
        ('45103010', 'Information Technology', 'Software & Services',
         'Application Software', TIMESTAMP '2026-01-01 00:00:00', 'MSCI_GICS_2026')
) AS v(
    gics_sub_industry_code, sector_name, industry_group_name, sub_industry_name, load_dts, rec_src
)
WHERE NOT EXISTS (
    SELECT 1 FROM raw_vault.ref_gics_sector r
    WHERE r.gics_sub_industry_code = v.gics_sub_industry_code
);

INSERT INTO raw_vault.hub_fund (hk_fund, fund_code, load_dts, rec_src)
SELECT hk_fund, fund_code, MIN(load_dts), ANY_VALUE(rec_src)
FROM staging.stg_investran_lp_commitments
WHERE hk_fund NOT IN (REPEAT('0', 64), REPEAT('f', 64))
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.hub_fund h
      WHERE h.hk_fund = stg_investran_lp_commitments.hk_fund
  )
GROUP BY hk_fund, fund_code;

INSERT INTO raw_vault.hub_limited_partner (hk_lp, bkcc, lp_id, load_dts, rec_src)
SELECT hk_lp, bkcc, lp_id, MIN(load_dts), ANY_VALUE(rec_src)
FROM (
    SELECT hk_lp, bkcc, lp_id, load_dts, rec_src FROM staging.stg_investran_lp_commitments
    UNION ALL
    SELECT hk_lp, bkcc, lp_id, load_dts, rec_src FROM staging.stg_legacy_gp_investors
) src
WHERE src.hk_lp NOT IN (REPEAT('0', 64), REPEAT('f', 64))
  AND NOT EXISTS (SELECT 1 FROM raw_vault.hub_limited_partner h WHERE h.hk_lp = src.hk_lp)
GROUP BY hk_lp, bkcc, lp_id;

INSERT INTO raw_vault.hub_portfolio_company (hk_portco, lei_code, load_dts, rec_src)
SELECT hk_portco, lei_code, MIN(load_dts), ANY_VALUE(rec_src)
FROM staging.stg_dealcloud_portfolio_companies
WHERE hk_portco NOT IN (REPEAT('0', 64), REPEAT('f', 64))
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.hub_portfolio_company h
      WHERE h.hk_portco = stg_dealcloud_portfolio_companies.hk_portco
  )
GROUP BY hk_portco, lei_code;

INSERT INTO raw_vault.hub_capital_account (hk_cap_acct, bkcc, cap_acct_code, load_dts, rec_src)
SELECT hk_cap_acct, bkcc, cap_acct_code, MIN(load_dts), ANY_VALUE(rec_src)
FROM staging.stg_legacy_gp_investors
WHERE hk_cap_acct NOT IN (REPEAT('0', 64), REPEAT('f', 64))
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.hub_capital_account h
      WHERE h.hk_cap_acct = stg_legacy_gp_investors.hk_cap_acct
  )
GROUP BY hk_cap_acct, bkcc, cap_acct_code;

INSERT INTO raw_vault.hub_unstructured_document (hk_doc, doc_sha256, load_dts, rec_src)
SELECT hk_doc, doc_sha256, MIN(load_dts), ANY_VALUE(rec_src)
FROM staging.stg_vdr_document_manifest
WHERE hk_doc NOT IN (REPEAT('0', 64), REPEAT('f', 64))
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.hub_unstructured_document h
      WHERE h.hk_doc = stg_vdr_document_manifest.hk_doc
  )
GROUP BY hk_doc, doc_sha256;

INSERT INTO raw_vault.lnk_lp_commitment (hk_lnk_lp_commitment, hk_lp, hk_fund, load_dts, rec_src)
SELECT hk_lnk_lp_commitment, hk_lp, hk_fund, MIN(load_dts), ANY_VALUE(rec_src)
FROM staging.stg_investran_lp_commitments
WHERE NOT EXISTS (
    SELECT 1 FROM raw_vault.lnk_lp_commitment l
    WHERE l.hk_lnk_lp_commitment = stg_investran_lp_commitments.hk_lnk_lp_commitment
)
GROUP BY hk_lnk_lp_commitment, hk_lp, hk_fund;

INSERT INTO raw_vault.lnk_fund_portco_investment (
    hk_lnk_fund_portco_investment, hk_fund, hk_portco, load_dts, rec_src
)
SELECT hk_lnk_fund_portco_investment, hk_fund, hk_portco, MIN(load_dts), ANY_VALUE(rec_src)
FROM staging.stg_dealcloud_portfolio_companies
WHERE NOT EXISTS (
    SELECT 1 FROM raw_vault.lnk_fund_portco_investment l
    WHERE l.hk_lnk_fund_portco_investment
        = stg_dealcloud_portfolio_companies.hk_lnk_fund_portco_investment
)
GROUP BY hk_lnk_fund_portco_investment, hk_fund, hk_portco;

INSERT INTO raw_vault.lnk_cap_acct_alignment (
    hk_lnk_cap_acct_alignment, hk_cap_acct, hk_lp, hk_fund, load_dts, rec_src
)
SELECT hk_lnk_cap_acct_alignment, hk_cap_acct, hk_lp, hk_fund, MIN(load_dts), ANY_VALUE(rec_src)
FROM staging.stg_legacy_gp_investors
WHERE NOT EXISTS (
    SELECT 1 FROM raw_vault.lnk_cap_acct_alignment l
    WHERE l.hk_lnk_cap_acct_alignment = stg_legacy_gp_investors.hk_lnk_cap_acct_alignment
)
GROUP BY hk_lnk_cap_acct_alignment, hk_cap_acct, hk_lp, hk_fund;

INSERT INTO raw_vault.lnk_doc_lp_side_letter (
    hk_lnk_doc_lp_side_letter, hk_doc, hk_lp, hk_fund, load_dts, rec_src
)
SELECT
    LOWER(SHA256(
        '64:' || d.hk_doc || '||64:' || lp.hk_lp || '||64:' || f.hk_fund
    )) AS hk_lnk_doc_lp_side_letter,
    d.hk_doc,
    lp.hk_lp,
    f.hk_fund,
    e.extracted_dts AS load_dts,
    'LLM_EXT_PIPELINE' AS rec_src
FROM unstructured_vdr.src_llm_extractions e
INNER JOIN raw_vault.hub_unstructured_document d ON d.doc_sha256 = e.doc_sha256
INNER JOIN raw_vault.hub_limited_partner lp
    ON lp.bkcc = e.resolved_lp_bkcc AND lp.lp_id = e.resolved_lp_id
INNER JOIN raw_vault.hub_fund f                  ON f.fund_code = e.resolved_fund_code
WHERE e.target_domain = 'SIDE_LETTER_TERMS'
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.lnk_doc_lp_side_letter l
      WHERE l.hk_lnk_doc_lp_side_letter = LOWER(SHA256(
          '64:' || d.hk_doc || '||64:' || lp.hk_lp || '||64:' || f.hk_fund
      ))
  );

INSERT INTO raw_vault.lnk_doc_portco_memo (
    hk_lnk_doc_portco_memo, hk_doc, hk_portco, load_dts, rec_src
)
SELECT
    LOWER(SHA256('64:' || d.hk_doc || '||64:' || p.hk_portco)) AS hk_lnk_doc_portco_memo,
    d.hk_doc,
    p.hk_portco,
    e.extracted_dts AS load_dts,
    'LLM_EXT_PIPELINE' AS rec_src
FROM unstructured_vdr.src_llm_extractions e
INNER JOIN raw_vault.hub_unstructured_document d ON d.doc_sha256 = e.doc_sha256
INNER JOIN raw_vault.hub_portfolio_company p     ON p.lei_code = e.resolved_lei_code
WHERE e.target_domain = 'PORTCO_VALUATION_RESTATEMENT'
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.lnk_doc_portco_memo l
      WHERE l.hk_lnk_doc_portco_memo = LOWER(SHA256('64:' || d.hk_doc || '||64:' || p.hk_portco))
  );

INSERT INTO raw_vault.sat_fund_core
SELECT DISTINCT
    src.hk_fund, src.load_dts, src.rec_src, src.hash_diff_fund_core,
    src.fund_legal_name, src.vintage_year, src.fund_strategy,
    src.base_currency_iso, src.target_size_usd
FROM (
    SELECT * FROM staging.stg_investran_lp_commitments
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_fund, hash_diff_fund_core ORDER BY load_dts ASC
    ) = 1
) src
LEFT JOIN (
    SELECT hk_fund, hash_diff
    FROM raw_vault.sat_fund_core
    QUALIFY ROW_NUMBER() OVER (PARTITION BY hk_fund ORDER BY load_dts DESC) = 1
) cur ON cur.hk_fund = src.hk_fund
WHERE cur.hk_fund IS NULL OR cur.hash_diff <> src.hash_diff_fund_core;

INSERT INTO raw_vault.sat_lp_core
SELECT
    src.hk_lp, src.load_dts, src.rec_src, src.hash_diff_lp_core,
    src.lp_legal_name, src.lp_type, src.country_code
FROM (
    SELECT hk_lp, load_dts, rec_src, hash_diff_lp_core, lp_legal_name, lp_type, country_code
    FROM staging.stg_investran_lp_commitments
    UNION ALL
    SELECT hk_lp, load_dts, rec_src, hash_diff_lp_core, lp_legal_name, lp_type, country_code
    FROM staging.stg_legacy_gp_investors
) src
LEFT JOIN (
    SELECT hk_lp, hash_diff
    FROM raw_vault.sat_lp_core
    QUALIFY ROW_NUMBER() OVER (PARTITION BY hk_lp ORDER BY load_dts DESC) = 1
) cur ON cur.hk_lp = src.hk_lp
WHERE cur.hk_lp IS NULL OR cur.hash_diff <> src.hash_diff_lp_core;

INSERT INTO raw_vault.sat_lp_kyc_pii
SELECT
    src.hk_lp, src.load_dts, src.rec_src, src.hash_diff_lp_kyc_pii,
    src.tax_id_ein, src.kyc_aml_risk_tier
FROM (
    SELECT hk_lp, load_dts, rec_src, hash_diff_lp_kyc_pii, tax_id_ein, kyc_aml_risk_tier
    FROM staging.stg_investran_lp_commitments
    UNION ALL
    SELECT hk_lp, load_dts, rec_src, hash_diff_lp_kyc_pii, tax_id_ein, kyc_aml_risk_tier
    FROM staging.stg_legacy_gp_investors
) src
LEFT JOIN (
    SELECT hk_lp, hash_diff
    FROM raw_vault.sat_lp_kyc_pii
    QUALIFY ROW_NUMBER() OVER (PARTITION BY hk_lp ORDER BY load_dts DESC) = 1
) cur ON cur.hk_lp = src.hk_lp
WHERE cur.hk_lp IS NULL OR cur.hash_diff <> src.hash_diff_lp_kyc_pii;

INSERT INTO raw_vault.sat_portco_core
SELECT
    src.hk_portco, src.load_dts, src.rec_src, src.hash_diff_portco_core,
    src.company_legal_name, src.gics_sub_industry_code, src.hq_country_iso, src.acquisition_year
FROM (
    SELECT *
    FROM staging.stg_dealcloud_portfolio_companies
    WHERE hk_portco NOT IN (REPEAT('0', 64), REPEAT('f', 64))
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_portco, hash_diff_portco_core ORDER BY load_dts ASC
    ) = 1
) src
LEFT JOIN (
    SELECT hk_portco, hash_diff
    FROM raw_vault.sat_portco_core
    QUALIFY ROW_NUMBER() OVER (PARTITION BY hk_portco ORDER BY load_dts DESC) = 1
) cur ON cur.hk_portco = src.hk_portco
WHERE cur.hk_portco IS NULL OR cur.hash_diff <> src.hash_diff_portco_core;

INSERT INTO raw_vault.sat_portco_valuation_mnpi
SELECT
    src.hk_portco, src.load_dts, src.applied_dts, src.rec_src, src.hash_diff_portco_valuation,
    src.entry_ebitda_usd, src.entry_ev_multiple, src.current_nav_usd, src.as_of_quarter_end
FROM staging.stg_dealcloud_portfolio_companies src
WHERE src.hk_portco NOT IN (REPEAT('0', 64), REPEAT('f', 64))
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.sat_portco_valuation_mnpi s
      WHERE s.hk_portco = src.hk_portco
        AND s.hash_diff = src.hash_diff_portco_valuation
  );

INSERT INTO raw_vault.sat_cap_acct_quarterly_nav
SELECT
    src.hk_cap_acct, src.load_dts, src.applied_dts, src.rec_src, src.hash_diff_cap_acct_nav,
    src.ending_nav_amt, src.unfunded_balance_amt, src.reporting_quarter
FROM staging.stg_legacy_gp_investors src
LEFT JOIN (
    SELECT hk_cap_acct, hash_diff
    FROM raw_vault.sat_cap_acct_quarterly_nav
    QUALIFY ROW_NUMBER() OVER (PARTITION BY hk_cap_acct ORDER BY load_dts DESC) = 1
) cur ON cur.hk_cap_acct = src.hk_cap_acct
WHERE cur.hk_cap_acct IS NULL OR cur.hash_diff <> src.hash_diff_cap_acct_nav;

INSERT INTO raw_vault.sat_doc_metadata
SELECT
    src.hk_doc, src.load_dts, src.rec_src, src.hash_diff_doc_metadata,
    src.doc_uri, src.doc_type, src.doc_title, src.page_count, src.mime_type
FROM staging.stg_vdr_document_manifest src
LEFT JOIN (
    SELECT hk_doc, hash_diff
    FROM raw_vault.sat_doc_metadata
    QUALIFY ROW_NUMBER() OVER (PARTITION BY hk_doc ORDER BY load_dts DESC) = 1
) cur ON cur.hk_doc = src.hk_doc
WHERE cur.hk_doc IS NULL OR cur.hash_diff <> src.hash_diff_doc_metadata;

INSERT INTO raw_vault.sat_lnk_lp_commitment_terms
SELECT
    src.hk_lnk_lp_commitment, src.load_dts, src.applied_dts,
    src.rec_src, src.hash_diff_commitment_terms,
    src.committed_amount, src.commitment_currency, src.closing_round, src.commitment_date
FROM staging.stg_investran_lp_commitments src
LEFT JOIN (
    SELECT hk_lnk_lp_commitment, hash_diff
    FROM raw_vault.sat_lnk_lp_commitment_terms
    QUALIFY ROW_NUMBER() OVER (PARTITION BY hk_lnk_lp_commitment ORDER BY load_dts DESC) = 1
) cur ON cur.hk_lnk_lp_commitment = src.hk_lnk_lp_commitment
WHERE cur.hk_lnk_lp_commitment IS NULL OR cur.hash_diff <> src.hash_diff_commitment_terms;

INSERT INTO raw_vault.sat_lnk_fund_portco_deal
SELECT DISTINCT
    src.hk_lnk_fund_portco_investment, src.load_dts, src.rec_src, src.hash_diff_deal, src.deal_id
FROM (
    SELECT *
    FROM staging.stg_dealcloud_portfolio_companies
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_lnk_fund_portco_investment, hash_diff_deal ORDER BY load_dts ASC
    ) = 1
) src
LEFT JOIN (
    SELECT hk_lnk_fund_portco_investment, hash_diff
    FROM raw_vault.sat_lnk_fund_portco_deal
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_lnk_fund_portco_investment ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_lnk_fund_portco_investment = src.hk_lnk_fund_portco_investment
WHERE cur.hk_lnk_fund_portco_investment IS NULL OR cur.hash_diff <> src.hash_diff_deal;"""


def _build_pe_zone_3_sql() -> str:
    """Generate Zone 3 Business Vault (`sal_*`, `esat_*`, AI provenance satellites, and `pit_*`)."""
    return """-- =============================================================================
-- ZONE 3: BUSINESS VAULT (SAME-AS LINK, EFFECTIVITY SATELLITE, AI PROVENANCE, PIT)
-- =============================================================================
INSERT INTO business_vault.sal_limited_partner (
    hk_sal_limited_partner, hk_lp_master, hk_lp_duplicate, load_dts, rec_src
)
SELECT
    LOWER(SHA256('64:' || g.hk_lp || '||64:' || d.hk_lp)) AS hk_sal_limited_partner,
    g.hk_lp AS hk_lp_master,
    d.hk_lp AS hk_lp_duplicate,
    TIMESTAMP '2026-10-01 08:30:00' AS load_dts,
    'AXI_ENTITY_RESOLUTION_ENGINE' AS rec_src
FROM raw_vault.hub_limited_partner g
INNER JOIN raw_vault.sat_lp_kyc_pii g_pii
    ON g_pii.hk_lp = g.hk_lp AND g_pii.rec_src <> 'SYSTEM_GHOST'
INNER JOIN raw_vault.hub_limited_partner d ON d.bkcc = 'LEGACY_GP_ADMIN'
INNER JOIN raw_vault.sat_lp_kyc_pii d_pii
    ON d_pii.hk_lp = d.hk_lp AND d_pii.rec_src <> 'SYSTEM_GHOST'
WHERE g.bkcc = 'GLOBAL'
  AND g_pii.tax_id_ein = d_pii.tax_id_ein
  AND NOT EXISTS (
      SELECT 1 FROM business_vault.sal_limited_partner s
      WHERE s.hk_sal_limited_partner = LOWER(SHA256('64:' || g.hk_lp || '||64:' || d.hk_lp))
  );

INSERT INTO business_vault.esat_sal_limited_partner
SELECT
    s.hk_sal_limited_partner,
    s.load_dts,
    s.rec_src,
    LOWER(SHA256('28:TIER_1_EXACT_TAX_ID_PLUS_MFN||6:0.9950||4:TRUE')) AS hash_diff,
    TIMESTAMP '2026-03-31 00:00:00' AS effective_from_dts,
    NULL AS effective_to_dts,
    TRUE AS is_active_flag,
    'TIER_1_EXACT_TAX_ID_PLUS_MFN' AS resolution_method,
    0.9950 AS match_confidence_score,
    'AUTO_TIER_1_VERIFIED' AS steward_approved_by
FROM business_vault.sal_limited_partner s
WHERE s.hk_sal_limited_partner NOT IN (REPEAT('0', 64), REPEAT('f', 64))
  AND NOT EXISTS (
      SELECT 1 FROM business_vault.esat_sal_limited_partner es
      WHERE es.hk_sal_limited_partner = s.hk_sal_limited_partner
        AND es.load_dts = s.load_dts
  );

INSERT INTO business_vault.sat_lp_side_letter_llm_ext
SELECT
    l.hk_lnk_doc_lp_side_letter,
    e.extracted_dts AS load_dts,
    CAST(e.effective_from_date AS TIMESTAMP) AS applied_dts,
    'LLM_EXT_PIPELINE' AS rec_src,
    LOWER(SHA256(CONCAT_WS('||',
        CAST(OCTET_LENGTH(ENCODE(CAST(e.mgmt_fee_discount_bps AS VARCHAR))) AS VARCHAR)
            || ':' || CAST(e.mgmt_fee_discount_bps AS VARCHAR),
        CAST(OCTET_LENGTH(ENCODE(CAST(e.mfn_election_flag AS VARCHAR))) AS VARCHAR)
            || ':' || CAST(e.mfn_election_flag AS VARCHAR),
        CAST(OCTET_LENGTH(ENCODE(CAST(e.co_invest_rights_flag AS VARCHAR))) AS VARCHAR)
            || ':' || CAST(e.co_invest_rights_flag AS VARCHAR),
        CAST(OCTET_LENGTH(ENCODE(e.esg_exclusion_clause)) AS VARCHAR)
            || ':' || e.esg_exclusion_clause
    ))) AS hash_diff,
    e.mgmt_fee_discount_bps,
    e.mfn_election_flag,
    e.co_invest_rights_flag,
    e.esg_exclusion_clause,
    e.effective_from_date,
    e.extractor_model_id,
    e.prompt_template_sha256,
    e.extraction_confidence,
    e.tier2_token_jaccard,
    e.human_review_status,
    e.source_page_number,
    e.source_char_start,
    e.source_char_end,
    e.source_text_quote
FROM unstructured_vdr.src_llm_extractions e
INNER JOIN raw_vault.hub_unstructured_document d ON d.doc_sha256 = e.doc_sha256
INNER JOIN raw_vault.hub_limited_partner lp
    ON lp.bkcc = e.resolved_lp_bkcc AND lp.lp_id = e.resolved_lp_id
INNER JOIN raw_vault.hub_fund f                  ON f.fund_code = e.resolved_fund_code
INNER JOIN raw_vault.lnk_doc_lp_side_letter l
    ON l.hk_doc = d.hk_doc AND l.hk_lp = lp.hk_lp AND l.hk_fund = f.hk_fund
WHERE e.target_domain = 'SIDE_LETTER_TERMS'
  AND NOT EXISTS (
      SELECT 1 FROM business_vault.sat_lp_side_letter_llm_ext s
      WHERE s.hk_lnk_doc_lp_side_letter = l.hk_lnk_doc_lp_side_letter
        AND s.load_dts = e.extracted_dts
  );

INSERT INTO business_vault.sat_lnk_doc_portco_ai_provenance
SELECT
    l.hk_lnk_doc_portco_memo,
    e.extracted_dts AS load_dts,
    CAST(e.effective_from_date AS TIMESTAMP)
        + INTERVAL '23 hours 59 minutes 59 seconds' AS applied_dts,
    'LLM_EXT_PIPELINE' AS rec_src,
    LOWER(SHA256(CONCAT_WS('||',
        CAST(OCTET_LENGTH(ENCODE(CAST(e.restated_ev_multiple AS VARCHAR))) AS VARCHAR)
            || ':' || CAST(e.restated_ev_multiple AS VARCHAR),
        CAST(OCTET_LENGTH(ENCODE(CAST(e.effective_from_date AS VARCHAR))) AS VARCHAR)
            || ':' || CAST(e.effective_from_date AS VARCHAR),
        CAST(OCTET_LENGTH(ENCODE(e.source_text_quote)) AS VARCHAR) || ':' || e.source_text_quote
    ))) AS hash_diff,
    e.restated_ev_multiple,
    e.effective_from_date,
    e.extractor_model_id,
    e.prompt_template_sha256,
    e.extraction_confidence,
    e.tier2_token_jaccard,
    e.tier3_cosine_sim,
    e.human_review_status,
    e.source_page_number,
    e.source_char_start,
    e.source_char_end,
    e.source_text_quote
FROM unstructured_vdr.src_llm_extractions e
INNER JOIN raw_vault.hub_unstructured_document d ON d.doc_sha256 = e.doc_sha256
INNER JOIN raw_vault.hub_portfolio_company p     ON p.lei_code = e.resolved_lei_code
INNER JOIN raw_vault.lnk_doc_portco_memo l
    ON l.hk_doc = d.hk_doc AND l.hk_portco = p.hk_portco
WHERE e.target_domain = 'PORTCO_VALUATION_RESTATEMENT'
  AND NOT EXISTS (
      SELECT 1 FROM business_vault.sat_lnk_doc_portco_ai_provenance s
      WHERE s.hk_lnk_doc_portco_memo = l.hk_lnk_doc_portco_memo
        AND s.load_dts = e.extracted_dts
  );

CREATE OR REPLACE TABLE information_mart.snap_logarithmic_calendar (
    as_of_dts      TIMESTAMP NOT NULL PRIMARY KEY,
    window_tier    VARCHAR   NOT NULL
);

INSERT INTO information_mart.snap_logarithmic_calendar VALUES
    (TIMESTAMP '2026-10-15 12:00:00', 'DAILY_RECENT_WINDOW'),
    (TIMESTAMP '2026-11-15 23:59:59', 'DAILY_RECENT_WINDOW');

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
    COALESCE(sv.load_dts, TIMESTAMP '1900-01-01 00:00:00') AS sat_portco_valuation_mnpi_load_dts
FROM raw_vault.hub_portfolio_company h
CROSS JOIN information_mart.snap_logarithmic_calendar cal
ASOF LEFT JOIN raw_vault.sat_portco_core sc
    ON sc.hk_portco = h.hk_portco
   AND sc.load_dts <= cal.as_of_dts
ASOF LEFT JOIN raw_vault.sat_portco_valuation_mnpi sv
    ON sv.hk_portco = h.hk_portco
   AND sv.load_dts <= cal.as_of_dts
WHERE h.hk_portco NOT IN (REPEAT('0', 64), REPEAT('f', 64));"""


def build_query_d10_sql(
    *,
    assertion_cutoff_ts: str = "2026-11-15 23:59:59",
    state_cutoff_date: str = "2026-09-30",
) -> str:
    """Build Zone 4 `Query D.10` (Golden LP Commitment, Legacy NAV & Side-Letter Provenance)."""
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
    FROM raw_vault.hub_limited_partner h
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
    COALESCE(sl.mgmt_fee_discount_bps, 0)      AS side_letter_fee_discount_bps,
    COALESCE(sl.mfn_election_flag, FALSE)      AS has_mfn_protection,
    sl.doc_sha256                              AS side_letter_doc_sha256,
    sl.extraction_confidence                   AS llm_confidence,
    sl.human_review_status                     AS governance_verdict,
    sl.source_page_number                      AS pdf_page
FROM raw_vault.hub_limited_partner h_lp
INNER JOIN lp_core_pit lp_c
    ON lp_c.hk_lp = h_lp.hk_lp
INNER JOIN raw_vault.lnk_lp_commitment l_c
    ON l_c.hk_lp = h_lp.hk_lp
INNER JOIN raw_vault.hub_fund h_f
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


# ============================================================================
# Generic CanonicalIR -> 4-Zone SQL Compiler
# ============================================================================


def is_pe_domain(ir: CanonicalIR | None) -> bool:
    """Return True if `ir` is None or represents the normative Private Equity domain."""
    if ir is None:
        return True
    hub_names = {h.name for h in ir.hubs}
    return {"hub_fund", "hub_limited_partner", "hub_portfolio_company"}.issubset(hub_names)


def _hub_slug(hub_name: str) -> str:
    """Derive an uppercase business key token prefix from a Hub table name."""
    return hub_name.removeprefix("hub_").removeprefix("bhub_").upper()


def _effective_hub_bkcc_column_expr(hub: IRHub) -> str | None:
    """Return the dynamic BKCC column expression configured on `hub`, if any."""
    for candidate in (
        hub.bkcc_column_expr,
        hub.hk_column.hash_expression.bkcc_column_expr,
        *(sm.bkcc_column_expr for sm in hub.source_mappings),
    ):
        if candidate and candidate.strip():
            return candidate.strip()
    return None


def _effective_hub_bkcc_literal(hub: IRHub) -> str | None:
    """Return the static BKCC literal configured on `hub`, if any."""
    for candidate in (
        hub.bkcc_literal,
        hub.hk_column.hash_expression.bkcc_literal,
        *(sm.bkcc_literal for sm in hub.source_mappings),
    ):
        if candidate and candidate.strip():
            return candidate.strip()
    return None


def _hub_seed_bk_values(
    hub: IRHub,
    row_idx: int,
    *,
    bkcc_override: str | None = None,
) -> list[str]:
    """Return deterministic business key token values for `row_idx` of `hub`."""
    slug = _hub_slug(hub.name)
    bkcc_col_expr = _effective_hub_bkcc_column_expr(hub)
    bkcc_lit = _effective_hub_bkcc_literal(hub)
    values: list[str] = []
    for bk in hub.business_keys:
        if bk.is_bkcc:
            if bkcc_override:
                values.append(bkcc_override)
            elif bkcc_col_expr or bk.is_tenant_id:
                values.append(f"TENANT_{row_idx:03d}")
            elif bkcc_lit:
                values.append(bkcc_lit)
            else:
                values.append("GLOBAL")
        else:
            values.append(f"{slug}-{row_idx:03d}")
    return values


def _hub_seed_hk(
    hub: IRHub,
    row_idx: int,
    *,
    bkcc_override: str | None = None,
) -> str:
    """Compute the deterministic lowercase 64-char SHA-256 hash key for `row_idx` of `hub`."""
    tokens = _hub_seed_bk_values(hub, row_idx, bkcc_override=bkcc_override)
    has_bkcc_col = any(bool(bk.is_bkcc) for bk in hub.business_keys)
    return compute_dv2_length_prefixed_sha256(
        tokens,
        is_business_key=True,
        bkcc_prefix=None
        if has_bkcc_col
        else (bkcc_override or _effective_hub_bkcc_literal(hub) or GLOBAL_BKCC),
    ).lower()


def _normalize_duckdb_type(data_type: str) -> str:
    """Normalize an `IRColumnDef.data_type` into a DuckDB-compatible SQL column type."""
    upper = data_type.strip().upper()
    if upper == "CHAR(64)":
        return "CHAR(64)"
    if upper.startswith(("VARCHAR", "TEXT", "STRING", "CHAR", "BINARY", "VARBINARY")):
        return "VARCHAR"
    if upper.startswith("TIMESTAMP"):
        return "TIMESTAMP"
    if upper == "DATE":
        return "DATE"
    if upper.startswith(("DECIMAL", "NUMERIC")):
        return upper
    if upper.startswith(("BIGINT", "INT8")):
        return "BIGINT"
    if upper.startswith(("SMALLINT", "TINYINT", "INT", "INTEGER")):
        return "INTEGER"
    if upper.startswith("BOOL"):
        return "BOOLEAN"
    if upper.startswith(("DOUBLE", "FLOAT", "REAL")):
        return "DOUBLE"
    return "VARCHAR"


def _satellite_seed_sql_literal(col: IRColumnDef, row_idx: int) -> str:
    """Return a deterministic SQL literal for a Satellite payload column at `row_idx`."""
    col_lower = col.name.lower()
    norm_type = _normalize_duckdb_type(col.data_type)

    # Governance & provenance columns (DV-DYN-011, DV-DYN-012, DV-DYN-013)
    if col_lower == "is_active_flag":
        return "TRUE"
    if col_lower in {"effective_to_dts", "effective_to_date"}:
        return "NULL"
    if col_lower == "effective_from_dts":
        return "TIMESTAMP '2026-10-01 06:00:00'"
    if col_lower in {
        "effective_from_date",
        "effective_from_dt",
        "as_of_quarter_end",
        "reporting_quarter",
    }:
        return "DATE '2026-09-30'"
    if col_lower == "extractor_model_id":
        return "'gemini-2.5-pro'"
    if col_lower.endswith("_sha256") or norm_type == "CHAR(64)":
        return "'9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08'"
    if col_lower in {
        "extraction_confidence",
        "confidence_score",
        "tier2_token_jaccard",
        "tier3_cosine_sim",
    }:
        return "0.9500" if row_idx == 1 else ("0.9200" if row_idx == 2 else "0.9800")
    if col_lower in {"human_review_status", "routing_action"}:
        return "'AUTO_ACCEPTED_TIER_2'"
    if col_lower.endswith("_quote") or col_lower in {"source_text_quote", "verbatim_quote"}:
        return f"'Verified contractual clause quote #{row_idx} from source document.'"
    if col_lower in {"source_page_number", "page_number"}:
        return str(row_idx)
    if col_lower in {"source_char_start", "char_start"}:
        return str(10 * row_idx)
    if col_lower in {"source_char_end", "char_end"}:
        return str(10 * row_idx + 45)

    if norm_type == "BOOLEAN":
        return "TRUE" if row_idx % 2 == 1 else "FALSE"
    if norm_type in {"INTEGER", "BIGINT"}:
        return str(100 * row_idx)
    if norm_type.startswith(("DECIMAL", "NUMERIC", "DOUBLE")):
        return f"{1000 * row_idx + 0.50:.2f}"
    if norm_type == "DATE":
        return "DATE '2026-09-30'"
    if norm_type == "TIMESTAMP":
        return "TIMESTAMP '2026-10-01 06:00:00'"
    return f"'{col.name}_val_{row_idx}'"


def _link_schema(link: IRLink) -> str:
    """Return the target Vault schema (`business_vault` or `raw_vault`) for `link`."""
    if link.name.startswith(("sal_", "hal_")) or link.link_type == "SAME_AS":
        return "business_vault"
    return "raw_vault"


def _satellite_schema(sat: IRSatellite) -> str:
    """Return the target Vault schema (`business_vault` or `raw_vault`) for `sat`."""
    if sat.satellite_type in {"BUSINESS", "EFFECTIVITY"} or sat.name.startswith("esat_"):
        return "business_vault"
    return "raw_vault"


def _satellite_child_key_names(sat: IRSatellite) -> list[str]:
    """Return the sub-sequence column names (`child_dependent_keys`) for `sat`."""
    return [c.name if hasattr(c, "name") else str(c) for c in (sat.child_dependent_keys or [])]


def _satellite_all_data_columns(sat: IRSatellite) -> list[IRColumnDef]:
    """Return sub-sequence and payload columns without duplicates or duplicate `applied_dts`."""
    child_defs = list(sat.child_dependent_keys or [])
    sub_seq_set = {c.name if hasattr(c, "name") else str(c) for c in child_defs}
    applied_col_name = sat.applied_dts_column.name if sat.applied_dts_column is not None else None
    existing_names = {c.name for c in sat.payload_columns}
    cols: list[IRColumnDef] = []
    for c_def in child_defs:
        seq_name = c_def.name if hasattr(c_def, "name") else str(c_def)
        if seq_name not in existing_names and seq_name != applied_col_name:
            if isinstance(c_def, IRColumnDef):
                cols.append(c_def.model_copy(update={"nullable": False}))
            else:
                cols.append(
                    IRColumnDef(
                        name=seq_name,
                        data_type="INTEGER" if seq_name.endswith("_seq") else "VARCHAR",
                        nullable=False,
                    )
                )
    for col in sat.payload_columns:
        if col.name == applied_col_name:
            continue
        if col.name in sub_seq_set and col.nullable:
            cols.append(col.model_copy(update={"nullable": False}))
        else:
            cols.append(col)
    return cols


def _ghost_literal_for_sat_column(col: IRColumnDef, sub_seq_set: set[str]) -> str:
    """Return the Ghost Record SQL literal for a Satellite column (`NOT NULL` for PK sub-seq)."""
    if col.name not in sub_seq_set:
        return "NULL"
    norm = _normalize_duckdb_type(col.data_type)
    if norm in {"INTEGER", "BIGINT"} or norm.startswith(("DECIMAL", "NUMERIC", "DOUBLE")):
        return "0"
    if norm == "BOOLEAN":
        return "FALSE"
    if norm == "DATE":
        return "DATE '1900-01-01'"
    if norm == "TIMESTAMP":
        return f"TIMESTAMP '{GHOST_DTS}'"
    return "'_GHOST_'"


def _effective_pit_tables(ir_obj: CanonicalIR) -> list[IRPitTable]:
    """Return configured `IRPitTable` list or synthesize a default PIT table if omitted."""
    hub_names = {h.name for h in ir_obj.hubs}
    sat_by_name = {s.name: s for s in ir_obj.satellites}
    if ir_obj.pit_tables:
        valid_pits: list[IRPitTable] = []
        for pit in ir_obj.pit_tables:
            if pit.parent_entity in hub_names:
                tracked = [
                    s
                    for s in pit.tracked_satellites
                    if s in sat_by_name
                    and sat_by_name[s].parent_entity == pit.parent_entity
                    and sat_by_name[s].satellite_type != "MULTI_ACTIVE"
                ]
                if not tracked:
                    tracked = [
                        s
                        for s in pit.tracked_satellites
                        if s in sat_by_name and sat_by_name[s].parent_entity == pit.parent_entity
                    ]
                if tracked:
                    valid_pits.append(
                        IRPitTable(
                            name=pit.name,
                            parent_entity=pit.parent_entity,
                            as_of_dts_column=pit.as_of_dts_column,
                            snapshot_window_policy=pit.snapshot_window_policy or "LOGARITHMIC",
                            tracked_satellites=tracked,
                        )
                    )
        if valid_pits:
            return valid_pits

    for hub in ir_obj.hubs:
        attached = [
            s.name
            for s in ir_obj.satellites
            if s.parent_entity == hub.name and s.satellite_type != "MULTI_ACTIVE"
        ]
        if not attached:
            attached = [s.name for s in ir_obj.satellites if s.parent_entity == hub.name]
        if attached:
            slug = hub.name.removeprefix("hub_").removeprefix("bhub_")
            return [
                IRPitTable(
                    name=f"pit_{slug}",
                    parent_entity=hub.name,
                    as_of_dts_column="as_of_dts",
                    snapshot_window_policy="LOGARITHMIC",
                    tracked_satellites=attached,
                )
            ]
    return []


def _compile_generic_hub_ddl_and_zero_keys(hub: IRHub) -> str:
    """Compile DDL and Dual Zero-Key (`'0'*64` and `'f'*64`) inserts for an `IRHub`."""
    tbl = _validate_ident(hub.name)
    hk = _validate_ident(hub.hk_column.name)
    bk_defs = [f"    {_validate_ident(bk.name)} VARCHAR NOT NULL," for bk in hub.business_keys]
    ddl = "\n".join(
        [
            f"CREATE TABLE IF NOT EXISTS raw_vault.{tbl} (",
            f"    {hk} CHAR(64) NOT NULL PRIMARY KEY,",
            *bk_defs,
            "    load_dts TIMESTAMP NOT NULL,",
            "    rec_src VARCHAR NOT NULL",
            ");",
        ]
    )
    unknown_bks = ", ".join("'SYSTEM'" if bk.is_bkcc else "'-1'" for bk in hub.business_keys)
    error_bks = ", ".join("'SYSTEM'" if bk.is_bkcc else "'-2'" for bk in hub.business_keys)
    zero_insert = (
        f"INSERT OR IGNORE INTO raw_vault.{tbl} VALUES\n"
        f"    ('{ZERO_KEY_UNKNOWN}', {unknown_bks}, "
        f"TIMESTAMP '{GHOST_DTS}', '{ZERO_KEY_REC_SRC}'),\n"
        f"    ('{ZERO_KEY_ERROR}', {error_bks}, "
        f"TIMESTAMP '{GHOST_DTS}', '{ZERO_KEY_REC_SRC}');"
    )
    return f"{ddl}\n{zero_insert}"


def _compile_generic_reference_ddl_and_seed(ref: IRReferenceTable) -> str:
    """Compile DDL and baseline seed rows for an `IRReferenceTable`."""
    tbl = _validate_ident(ref.name)
    code_col = _validate_ident(ref.natural_code_column)
    return (
        f"CREATE TABLE IF NOT EXISTS raw_vault.{tbl} (\n"
        f"    {code_col} VARCHAR NOT NULL PRIMARY KEY,\n"
        f"    load_dts TIMESTAMP NOT NULL,\n"
        f"    rec_src VARCHAR NOT NULL\n"
        f");\n"
        f"INSERT OR IGNORE INTO raw_vault.{tbl} VALUES\n"
        f"    ('REF-001', TIMESTAMP '2026-10-01 06:00:00', 'SYSTEM_SEED'),\n"
        f"    ('REF-002', TIMESTAMP '2026-10-01 06:00:00', 'SYSTEM_SEED');"
    )


def _compile_generic_link_ddl_and_zero_keys(link: IRLink) -> str:
    """Compile DDL and Dual Zero-Key (`'0'*64` and `'f'*64`) inserts for an `IRLink`."""
    schema = _link_schema(link)
    tbl = _validate_ident(link.name)
    hk = _validate_ident(link.hk_column.name)
    fk_defs = [
        f"    {_validate_ident(ref.fk_column_name)} CHAR(64) NOT NULL,"
        for ref in link.hub_references
    ]
    ddl = "\n".join(
        [
            f"CREATE TABLE IF NOT EXISTS {schema}.{tbl} (",
            f"    {hk} CHAR(64) NOT NULL PRIMARY KEY,",
            *fk_defs,
            "    load_dts TIMESTAMP NOT NULL,",
            "    rec_src VARCHAR NOT NULL",
            ");",
        ]
    )
    unknown_fks = ", ".join(f"'{ZERO_KEY_UNKNOWN}'" for _ in link.hub_references)
    error_fks = ", ".join(f"'{ZERO_KEY_ERROR}'" for _ in link.hub_references)
    zero_insert = (
        f"INSERT OR IGNORE INTO {schema}.{tbl} VALUES\n"
        f"    ('{ZERO_KEY_UNKNOWN}', {unknown_fks}, "
        f"TIMESTAMP '{GHOST_DTS}', '{ZERO_KEY_REC_SRC}'),\n"
        f"    ('{ZERO_KEY_ERROR}', {error_fks}, "
        f"TIMESTAMP '{GHOST_DTS}', '{ZERO_KEY_REC_SRC}');"
    )
    return f"{ddl}\n{zero_insert}"


def _compile_generic_satellite_ddl_and_ghost(sat: IRSatellite) -> str:
    """Compile DDL and `SYSTEM_GHOST` record insert for an `IRSatellite`."""
    schema = _satellite_schema(sat)
    tbl = _validate_ident(sat.name)
    parent_hk = _validate_ident(sat.parent_hk_column.name)
    has_applied = sat.applied_dts_column is not None
    data_cols = _satellite_all_data_columns(sat)
    child_keys = _satellite_child_key_names(sat)
    sub_seq_set = set(child_keys)

    col_lines = [
        f"    {parent_hk} CHAR(64) NOT NULL,",
        "    load_dts TIMESTAMP NOT NULL,",
    ]
    if has_applied:
        col_lines.append("    applied_dts TIMESTAMP NOT NULL,")
    col_lines.extend(
        [
            "    rec_src VARCHAR NOT NULL,",
            "    hash_diff CHAR(64) NOT NULL,",
        ]
    )
    for col in data_cols:
        c_name = _validate_ident(col.name)
        c_type = _normalize_duckdb_type(col.data_type)
        not_null = " NOT NULL" if col.name in sub_seq_set else ""
        col_lines.append(f"    {c_name} {c_type}{not_null},")
    pk_cols = [parent_hk, "load_dts", *(_validate_ident(c) for c in child_keys)]
    col_lines.append(f"    PRIMARY KEY ({', '.join(pk_cols)})")

    ddl = f"CREATE TABLE IF NOT EXISTS {schema}.{tbl} (\n" + "\n".join(col_lines) + "\n);"
    ghost_cols = [parent_hk, "load_dts"]
    ghost_vals = [f"'{ZERO_KEY_UNKNOWN}'", f"TIMESTAMP '{GHOST_DTS}'"]
    if has_applied:
        ghost_cols.append("applied_dts")
        ghost_vals.append(f"TIMESTAMP '{GHOST_DTS}'")
    ghost_cols.extend(["rec_src", "hash_diff"])
    ghost_vals.extend([f"'{GHOST_REC_SRC}'", f"'{ZERO_KEY_UNKNOWN}'"])
    for col in data_cols:
        if col.name in sub_seq_set:
            ghost_cols.append(_validate_ident(col.name))
            ghost_vals.append(_ghost_literal_for_sat_column(col, sub_seq_set))
    ghost_insert = (
        f"INSERT OR IGNORE INTO {schema}.{tbl} ({', '.join(ghost_cols)}) VALUES\n"
        f"    ({', '.join(ghost_vals)});"
    )
    return f"{ddl}\n{ghost_insert}"


def _load_bronze_table_rows(
    bronze_tables: Mapping[str, list[dict[str, Any]] | Any] | None,
) -> dict[str, list[dict[str, Any]]]:
    """Load rows from `bronze_tables` (CSV/Parquet paths or in-memory row lists)."""
    if not bronze_tables:
        return {}
    import csv
    from pathlib import Path

    import duckdb

    loaded: dict[str, list[dict[str, Any]]] = {}
    for raw_key, val in bronze_tables.items():
        rows: list[dict[str, Any]] = []
        if isinstance(val, list):
            rows = [dict(r) for r in val if isinstance(r, dict)]
        elif isinstance(val, (Path, str)):
            p = Path(val)
            if p.is_file():
                suffix = p.suffix.lower()
                if suffix == ".csv":
                    with p.open("r", encoding="utf-8", errors="replace") as fh:
                        rows = [dict(r) for r in csv.DictReader(fh)]
                elif suffix == ".parquet":
                    con = duckdb.connect(":memory:")
                    try:
                        esc = str(p).replace("'", "''")
                        cur = con.execute(f"SELECT * FROM read_parquet('{esc}')")
                        cols = [d[0] for d in cur.description]
                        rows = [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]
                    finally:
                        con.close()
        if rows:
            loaded[raw_key] = rows
            loaded[raw_key.lower()] = rows
            short_key = raw_key.split(".")[-1]
            clean_leaf = short_key
            for pfx in ("src_", "raw_", "bronze_"):
                if clean_leaf.lower().startswith(pfx) and len(clean_leaf) > len(pfx):
                    clean_leaf = clean_leaf[len(pfx) :]
                    break
            stripped_schema_key = raw_key.lower().removeprefix("bronze_").replace(".", "_")
            loaded.setdefault(short_key, rows)
            loaded.setdefault(clean_leaf, rows)
            loaded.setdefault(f"bronze.{short_key}", rows)
            loaded.setdefault(f"bronze.{clean_leaf}", rows)
            loaded.setdefault(f"stg_{raw_key.replace('.', '_')}", rows)
            loaded.setdefault(f"stg_{stripped_schema_key}", rows)
            loaded.setdefault(f"stg_{short_key}", rows)
            loaded.setdefault(f"stg_{clean_leaf}", rows)
            if "." in raw_key:
                sys_pfx = raw_key.split(".")[0].lower().removeprefix("bronze_")
                if not clean_leaf.lower().startswith(sys_pfx):
                    loaded.setdefault(f"stg_{sys_pfx}_{clean_leaf.lower()}", rows)
    return loaded


def _sql_lit(val: str) -> str:
    """Escape single quotes in a SQL string literal."""
    return str(val).replace("'", "''")


def _table_lookup_keys(table_name: str) -> list[str]:
    """Return normalized lookup keys for a Bronze or staging table identifier."""
    raw = table_name.strip().lower()
    if not raw:
        return []
    keys: list[str] = [raw]
    leaf = raw.split(".")[-1]
    if leaf not in keys:
        keys.append(leaf)
    clean_leaf = leaf
    for pfx in ("stg_", "src_", "raw_", "bronze_"):
        if clean_leaf.startswith(pfx) and len(clean_leaf) > len(pfx):
            clean_leaf = clean_leaf[len(pfx) :]
    if clean_leaf not in keys:
        keys.append(clean_leaf)
    if f"stg_{clean_leaf}" not in keys:
        keys.append(f"stg_{clean_leaf}")
    if "." in raw:
        sys_pfx = raw.split(".")[0].removeprefix("bronze_")
        if not clean_leaf.startswith(sys_pfx):
            comp = f"{sys_pfx}_{clean_leaf}"
            if comp not in keys:
                keys.append(comp)
            if f"stg_{comp}" not in keys:
                keys.append(f"stg_{comp}")
    return keys


def _effective_hub_sm_bkcc_literal(hub: IRHub, sm: Any) -> str | None:
    """Resolve the effective BKCC literal for a single `(hub, source_mapping)` pair."""
    if getattr(sm, "bkcc_column_expr", None) or _effective_hub_bkcc_column_expr(hub):
        return None
    for attr in ("bkcc_literal", "bkcc_prefix"):
        explicit_lit = str(getattr(sm, attr, None) or "").strip().upper()
        if explicit_lit and explicit_lit not in {"NONE", "SOURCE_SYSTEM_PREFIX_REQUIRED"}:
            return explicit_lit
    tier = str(getattr(sm, "key_authority_tier", "") or "").strip().upper()
    if tier in {"TIER_1_GLOBAL_STANDARD", "TIER_2_ENTERPRISE_SOR"}:
        return GLOBAL_BKCC
    if tier == "TIER_3_LOCAL_SCOPED":
        rec = str(getattr(sm, "rec_src_value", "") or GLOBAL_BKCC).strip().upper()
        return rec or GLOBAL_BKCC
    hub_lit = _effective_hub_bkcc_literal(hub)
    if hub_lit:
        return hub_lit.strip().upper()
    if any(bool(bk.is_bkcc) for bk in hub.business_keys):
        return None
    return GLOBAL_BKCC


def build_staging_bkcc_registry(
    ir_obj: CanonicalIR,
) -> dict[tuple[str, str, str], str | None]:
    """Build a `(bronze_table, column_name, target_hub) -> bkcc_literal` lookup registry."""
    registry: dict[tuple[str, str, str], str | None] = {}
    for hub in ir_obj.hubs:
        non_bkcc_hub_cols = [bk.name.lower() for bk in hub.business_keys if not bk.is_bkcc]
        for sm in hub.source_mappings:
            eff_lit = _effective_hub_sm_bkcc_literal(hub, sm)
            col_map = getattr(sm, "column_bkcc_map", None) or {}
            src_cols = [
                c.lower() for c in getattr(sm, "bk_source_columns", []) if c.lower() != "bkcc"
            ]
            tbl_keys = _table_lookup_keys(getattr(sm, "staging_table", ""))
            bronze_tbl = getattr(sm, "bronze_table", None)
            if bronze_tbl:
                for k in _table_lookup_keys(str(bronze_tbl)):
                    if k not in tbl_keys:
                        tbl_keys.append(k)
            for t_key in tbl_keys:
                registry.setdefault((t_key, "*", hub.name), eff_lit)
                for c_name in src_cols:
                    registry[(t_key, c_name, hub.name)] = eff_lit
                for c_name in non_bkcc_hub_cols:
                    registry.setdefault((t_key, c_name, hub.name), eff_lit)
                for c_name, c_lit in col_map.items():
                    norm_lit = str(c_lit).strip().upper() if c_lit else eff_lit
                    registry[(t_key, str(c_name).lower(), hub.name)] = norm_lit

    for link in ir_obj.links:
        for sm_dict in link.source_mappings:
            if not isinstance(sm_dict, dict):
                continue
            stg_tbl = str(sm_dict.get("staging_table", ""))
            brz_tbl = str(sm_dict.get("bronze_table", ""))
            fk_src_map = sm_dict.get("fk_source_columns") or {}
            fk_bkcc_map = sm_dict.get("fk_bkcc_literals") or {}
            tbl_keys = _table_lookup_keys(stg_tbl)
            for k in _table_lookup_keys(brz_tbl):
                if k not in tbl_keys:
                    tbl_keys.append(k)
            for ref in link.hub_references:
                fk_col = ref.fk_column_name
                if fk_col in fk_bkcc_map:
                    fk_lit = fk_bkcc_map[fk_col]
                    norm_fk_lit = str(fk_lit).strip().upper() if fk_lit else None
                    src_col = str(fk_src_map.get(fk_col, fk_col)).lower()
                    for t_key in tbl_keys:
                        registry[(t_key, fk_col.lower(), ref.target_hub)] = norm_fk_lit
                        registry[(t_key, src_col, ref.target_hub)] = norm_fk_lit

    return registry


def resolve_staging_bkcc_literal(
    ir_obj: CanonicalIR,
    bronze_or_staging_table: str,
    column_name: str,
    target_hub: str,
    *,
    role_name: str | None = None,
    registry: dict[tuple[str, str, str], str | None] | None = None,
) -> str | None:
    """Resolve the scoped `bkcc_literal` for `(bronze_table, column_name, target_hub)`."""
    reg = registry if registry is not None else build_staging_bkcc_registry(ir_obj)
    col_low = column_name.strip().lower()
    tbl_keys = _table_lookup_keys(bronze_or_staging_table)

    # 1. Exact (table, column, target_hub) match
    for t_key in tbl_keys:
        if (t_key, col_low, target_hub) in reg:
            return reg[(t_key, col_low, target_hub)]

    # 2. Table-scoped wildcard match on target_hub when column is '*' or unambiguous
    if col_low == "*":
        for t_key in tbl_keys:
            if (t_key, "*", target_hub) in reg:
                return reg[(t_key, "*", target_hub)]

    hub_obj = next((h for h in ir_obj.hubs if h.name == target_hub), None)
    if hub_obj is None:
        return None

    # 3. Match against target_hub's source_mappings by column name or stripped role prefix
    stripped_col = col_low
    for prefix in ("master_", "duplicate_", "primary_", "secondary_", "parent_", "child_", "fk_"):
        if stripped_col.startswith(prefix) and len(stripped_col) > len(prefix):
            stripped_col = stripped_col[len(prefix) :]
            break

    if col_low != "*":
        for sm in hub_obj.source_mappings:
            src_cols = [c.lower() for c in sm.bk_source_columns if c.lower() != "bkcc"]
            if col_low in src_cols or stripped_col in src_cols:
                return _effective_hub_sm_bkcc_literal(hub_obj, sm)
            if any(
                c.endswith(f"_{stripped_col}") or stripped_col.endswith(f"_{c}") for c in src_cols
            ):
                return _effective_hub_sm_bkcc_literal(hub_obj, sm)

    # 4. Role-based disambiguation for Same-As / Hierarchical links on multi-source Hubs
    if role_name:
        r_low = role_name.strip().lower()
        if (
            r_low in {"duplicate", "secondary", "legacy", "alias"}
            and len(hub_obj.source_mappings) > 1
        ):
            for sm in reversed(hub_obj.source_mappings):
                if getattr(sm, "key_authority_tier", None) == "TIER_3_LOCAL_SCOPED":
                    return _effective_hub_sm_bkcc_literal(hub_obj, sm)
            return _effective_hub_sm_bkcc_literal(hub_obj, hub_obj.source_mappings[-1])
        if r_low in {"master", "primary", "canonical", "golden"}:
            for sm in hub_obj.source_mappings:
                if getattr(sm, "key_authority_tier", None) in {
                    "TIER_1_GLOBAL_STANDARD",
                    "TIER_2_ENTERPRISE_SOR",
                }:
                    return _effective_hub_sm_bkcc_literal(hub_obj, sm)

    # 5. Table wildcard fallback or primary source_mapping fallback
    for t_key in tbl_keys:
        if (t_key, "*", target_hub) in reg:
            return reg[(t_key, "*", target_hub)]

    if hub_obj.source_mappings:
        return _effective_hub_sm_bkcc_literal(hub_obj, hub_obj.source_mappings[0])
    return None


def _resolve_hub_bk_rows_from_bronze(
    hub: IRHub,
    loaded_bronze: dict[str, list[dict[str, Any]]],
    *,
    ir_obj: CanonicalIR | None = None,
    bkcc_registry: dict[tuple[str, str, str], str | None] | None = None,
) -> list[list[str]]:
    """Resolve distinct business key token tuples for `hub` from `loaded_bronze` or defaults."""
    distinct_sm_bkccs: list[str | None] = []
    for sm in hub.source_mappings:
        eff_lit = _effective_hub_sm_bkcc_literal(hub, sm)
        if eff_lit not in distinct_sm_bkccs:
            distinct_sm_bkccs.append(eff_lit)
    primary_bkcc = distinct_sm_bkccs[0] if distinct_sm_bkccs else None
    secondary_bkcc = distinct_sm_bkccs[1] if len(distinct_sm_bkccs) > 1 else primary_bkcc
    default_r1 = _hub_seed_bk_values(hub, 1, bkcc_override=primary_bkcc)
    default_r2 = _hub_seed_bk_values(hub, 2, bkcc_override=secondary_bkcc)
    if not loaded_bronze:
        if len(distinct_sm_bkccs) > 1:
            extra_rows: list[list[str]] = [default_r1, default_r2]
            for extra_bkcc in distinct_sm_bkccs:
                r_a = _hub_seed_bk_values(hub, 1, bkcc_override=extra_bkcc)
                r_b = _hub_seed_bk_values(hub, 2, bkcc_override=extra_bkcc)
                if r_a not in extra_rows:
                    extra_rows.append(r_a)
                if r_b not in extra_rows:
                    extra_rows.append(r_b)
            return extra_rows
        return [default_r1, default_r2]

    extracted: list[list[str]] = []
    seen_tuples: set[tuple[str, ...]] = set()
    hub_bkcc_col_expr = _effective_hub_bkcc_column_expr(hub)
    hub_bkcc_lit = _effective_hub_bkcc_literal(hub)

    for sm in hub.source_mappings:
        stg_tbl = sm.staging_table
        sm_rows: list[dict[str, Any]] = []
        lookup_candidates = list(_table_lookup_keys(stg_tbl))
        if getattr(sm, "bronze_table", None):
            for k in _table_lookup_keys(str(sm.bronze_table)):
                if k not in lookup_candidates:
                    lookup_candidates.append(k)
        for extra_k in (
            f"bronze.{stg_tbl.removeprefix('stg_')}",
            hub.name,
            f"src_{hub.name}",
        ):
            if extra_k not in lookup_candidates:
                lookup_candidates.append(extra_k)
        for key in lookup_candidates:
            if key in loaded_bronze:
                sm_rows = loaded_bronze[key]
                break
        if not sm_rows:
            continue

        sm_bkcc_col = getattr(sm, "bkcc_column_expr", None) or hub_bkcc_col_expr
        excluded_bkcc_names = {"bkcc"}
        if sm_bkcc_col:
            excluded_bkcc_names.add(sm_bkcc_col.lower())
        raw_src_bk_cols = list(sm.bk_source_columns)
        src_bk_cols = [
            c for c in raw_src_bk_cols if c.lower() not in excluded_bkcc_names
        ] or raw_src_bk_cols
        primary_src_col = src_bk_cols[0] if src_bk_cols else "*"
        if ir_obj is not None and not sm_bkcc_col:
            scoped_bkcc = resolve_staging_bkcc_literal(
                ir_obj,
                getattr(sm, "bronze_table", None) or sm.staging_table,
                primary_src_col,
                hub.name,
                registry=bkcc_registry,
            )
        else:
            scoped_bkcc = _effective_hub_sm_bkcc_literal(hub, sm)
        fallback_bkcc = (
            GLOBAL_BKCC if hub.verified_global_master_key else (sm.rec_src_value or GLOBAL_BKCC)
        )
        sm_bkcc = (
            scoped_bkcc
            or getattr(sm, "bkcc_prefix", None)
            or getattr(sm, "bkcc_literal", None)
            or hub_bkcc_lit
            or fallback_bkcc
        ).strip() or GLOBAL_BKCC

        for row in sm_rows:
            tokens: list[str] = []
            valid_row = True
            non_bkcc_idx = 0
            for bk in hub.business_keys:
                if bk.is_bkcc:
                    if sm_bkcc_col:
                        raw_bkcc = (
                            row.get(sm_bkcc_col) or row.get(bk.name) or row.get("bkcc") or sm_bkcc
                        )
                    else:
                        raw_bkcc = scoped_bkcc or row.get(bk.name) or row.get("bkcc") or sm_bkcc
                    bkcc_val = str(raw_bkcc).strip()
                    tokens.append(bkcc_val or GLOBAL_BKCC)
                else:
                    src_col = (
                        src_bk_cols[non_bkcc_idx] if non_bkcc_idx < len(src_bk_cols) else bk.name
                    )
                    non_bkcc_idx += 1
                    raw_val = row.get(src_col, row.get(bk.name))
                    if raw_val is None or str(raw_val).strip() in {
                        "",
                        ZERO_KEY_UNKNOWN_TOKEN,
                        ZERO_KEY_ERROR_TOKEN,
                    }:
                        valid_row = False
                        break
                    tokens.append(str(raw_val).strip())
            if valid_row and len(tokens) == len(hub.business_keys):
                tup = tuple(tokens)
                if tup not in seen_tuples:
                    seen_tuples.add(tup)
                    extracted.append(tokens)

    if len(extracted) >= 2:
        return extracted
    if len(extracted) == 1:
        return [extracted[0], default_r2]
    return [default_r1, default_r2]


def _format_bronze_payload_sql_literal(
    col: IRColumnDef,
    row: dict[str, Any] | None,
    row_idx: int,
) -> str:
    """Format a column value from a `loaded_bronze` row as a SQL literal, or fall back to seed."""
    if row is None or col.name not in row or row[col.name] is None:
        return _satellite_seed_sql_literal(col, row_idx)
    s_val = str(row[col.name]).strip()
    if not s_val:
        return _satellite_seed_sql_literal(col, row_idx)

    col_lower = col.name.lower()
    if col_lower in {
        "prompt_template_sha256",
        "extraction_confidence",
        "confidence_score",
        "human_review_status",
        "routing_action",
    }:
        return _satellite_seed_sql_literal(col, row_idx)

    norm_type = _normalize_duckdb_type(col.data_type)
    if norm_type == "BOOLEAN":
        return "TRUE" if s_val.lower() in {"true", "1", "t", "yes", "y"} else "FALSE"
    if norm_type in {"INTEGER", "BIGINT"}:
        try:
            return str(int(float(s_val)))
        except ValueError:
            return _satellite_seed_sql_literal(col, row_idx)
    if norm_type.startswith(("DECIMAL", "NUMERIC", "DOUBLE")):
        try:
            return f"{float(s_val):.2f}"
        except ValueError:
            return _satellite_seed_sql_literal(col, row_idx)
    if norm_type == "DATE":
        if len(s_val) >= 10 and s_val[4:5] == "-":
            esc_d = s_val[:10].replace("'", "''")
            return f"DATE '{esc_d}'"
        return _satellite_seed_sql_literal(col, row_idx)
    if norm_type == "TIMESTAMP":
        if len(s_val) >= 10 and s_val[4:5] == "-":
            esc_ts = s_val.replace("'", "''")
            return f"TIMESTAMP '{esc_ts}'"
        return _satellite_seed_sql_literal(col, row_idx)
    esc = s_val.replace("'", "''")
    return f"'{esc}'"


def _filter_hub_rows_for_bkcc(
    hub: IRHub,
    rows: list[list[str]],
    bkcc_lit: str | None,
) -> list[list[str]]:
    """Filter `rows` for `hub` to those matching `bkcc_lit` when `hub` has an `is_bkcc` column."""
    if not rows or bkcc_lit is None:
        return rows
    bkcc_idx = next((i for i, bk in enumerate(hub.business_keys) if bk.is_bkcc), None)
    if bkcc_idx is None:
        return rows
    matched = [r for r in rows if len(r) > bkcc_idx and r[bkcc_idx].upper() == bkcc_lit.upper()]
    return matched if matched else rows


def _compile_generic_zone_0_sql(
    ir_obj: CanonicalIR,
    *,
    include_batch4_restatement_in_seed: bool = False,
    bronze_tables: Mapping[str, list[dict[str, Any]] | Any] | None = None,
) -> str:
    """Compile Zone 0 schemas, Bronze source tables, Vault DDL, Zero Keys, and Ghosts."""
    hub_by_name = {h.name: h for h in ir_obj.hubs}
    link_by_name = {lnk.name: lnk for lnk in ir_obj.links}
    bitemp_sat_name = next(
        (s.name for s in ir_obj.satellites if s.applied_dts_column is not None),
        ir_obj.satellites[0].name if ir_obj.satellites else None,
    )
    loaded_bronze = _load_bronze_table_rows(bronze_tables)
    bkcc_registry = build_staging_bkcc_registry(ir_obj)
    hub_bk_rows: dict[str, list[list[str]]] = {
        hub.name: _resolve_hub_bk_rows_from_bronze(
            hub,
            loaded_bronze,
            ir_obj=ir_obj,
            bkcc_registry=bkcc_registry,
        )
        for hub in ir_obj.hubs
    }

    parts = [
        "-- =============================================================================",
        "-- ZONE 0: TARGET SCHEMAS, BRONZE SOURCE SEEDS, VAULT DDL & GHOST/ZERO RECORDS",
        "-- =============================================================================",
        "CREATE SCHEMA IF NOT EXISTS bronze;",
        "CREATE SCHEMA IF NOT EXISTS staging;",
        "CREATE SCHEMA IF NOT EXISTS raw_vault;",
        "CREATE SCHEMA IF NOT EXISTS business_vault;",
        "CREATE SCHEMA IF NOT EXISTS information_mart;",
    ]

    # 1. Bronze source tables & seed rows for each Hub (hydrated from bronze_tables when provided)
    raw_bronze_hub_pairs: dict[str, list[tuple[IRHub, Any]]] = {}
    for hub in ir_obj.hubs:
        tbl = _validate_ident(hub.name)
        default_rec_src = (
            hub.source_mappings[0].rec_src_value if hub.source_mappings else "SYSTEM_SEED"
        )
        bkcc_to_rec_src: dict[str, str] = {}
        for sm in hub.source_mappings:
            eff_lit = _effective_hub_sm_bkcc_literal(hub, sm)
            if eff_lit:
                bkcc_to_rec_src.setdefault(eff_lit.upper(), sm.rec_src_value)
            raw_short = _validate_ident(sm.staging_table.removeprefix("stg_").split(".")[-1])
            if raw_short != f"src_{tbl}":
                raw_bronze_hub_pairs.setdefault(raw_short, []).append((hub, sm))

        bkcc_col_expr = _effective_hub_bkcc_column_expr(hub)
        bk_names = {bk.name for bk in hub.business_keys}
        extra_bkcc_col = (
            _validate_ident(bkcc_col_expr)
            if bkcc_col_expr and bkcc_col_expr not in bk_names
            else None
        )
        bk_defs = [f"    {_validate_ident(bk.name)} VARCHAR," for bk in hub.business_keys]
        if extra_bkcc_col:
            bk_defs.append(f"    {extra_bkcc_col} VARCHAR,")
        bk_rows = hub_bk_rows[hub.name]
        bkcc_idx = next((i for i, bk in enumerate(hub.business_keys) if bk.is_bkcc), None)
        hub_insert_rows = []
        for row_bks in bk_rows:
            row_rec_src = default_rec_src
            if bkcc_idx is not None and len(row_bks) > bkcc_idx:
                row_rec_src = bkcc_to_rec_src.get(row_bks[bkcc_idx].upper(), default_rec_src)
            row_vals = list(row_bks)
            if extra_bkcc_col:
                eff_idx = bkcc_idx if bkcc_idx is not None else 0
                row_vals.append(row_bks[eff_idx] if eff_idx < len(row_bks) else "GLOBAL")
            lit_vals = ", ".join(
                chr(39) + v.replace(chr(39), chr(39) * 2) + chr(39) for v in row_vals
            )
            hub_insert_rows.append(
                f"    ({lit_vals}, TIMESTAMP '2026-10-01 06:00:00', '{_sql_lit(row_rec_src)}')"
            )
        parts.append(
            "\n".join(
                [
                    f"CREATE OR REPLACE TABLE bronze.src_{tbl} (",
                    *bk_defs,
                    "    load_dts TIMESTAMP,",
                    "    rec_src VARCHAR",
                    ");",
                    f"INSERT INTO bronze.src_{tbl} VALUES",
                    ",\n".join(hub_insert_rows) + ";",
                ]
            )
        )

    for raw_short, pairs in raw_bronze_hub_pairs.items():
        if len(pairs) == 1:
            only_hub, _ = pairs[0]
            parts.append(
                f"CREATE OR REPLACE VIEW bronze.{raw_short} AS "
                f"SELECT * FROM bronze.src_{_validate_ident(only_hub.name)};"
            )
        else:
            # Composite Bronze feed mapping to multiple Hubs
            comp_cols: list[str] = []
            for _h, sm in pairs:
                for c in sm.bk_source_columns:
                    if c.lower() != "bkcc":
                        c_ident = _validate_ident(c)
                        if c_ident not in comp_cols:
                            comp_cols.append(c_ident)
            comp_rec_src = pairs[0][1].rec_src_value or "SYSTEM_SEED"
            col_defs = [f"    {c} VARCHAR," for c in comp_cols]
            row_count = max(
                (len(hub_bk_rows[h.name]) for h, _ in pairs),
                default=2,
            )
            comp_insert_rows: list[str] = []
            for r_idx in range(min(max(row_count, 2), 4)):
                row_vals: list[str] = []
                for c_ident in comp_cols:
                    val_found = f"'VAL-{r_idx + 1:03d}'"
                    for h, sm in pairs:
                        src_non_bkcc = [
                            _validate_ident(c) for c in sm.bk_source_columns if c.lower() != "bkcc"
                        ]
                        if c_ident in src_non_bkcc:
                            pos = src_non_bkcc.index(c_ident)
                            scoped_lit = _effective_hub_sm_bkcc_literal(h, sm)
                            h_cand = _filter_hub_rows_for_bkcc(h, hub_bk_rows[h.name], scoped_lit)
                            chosen_row = h_cand[r_idx % len(h_cand)]
                            non_bkcc_vals = [
                                v
                                for bk, v in zip(h.business_keys, chosen_row, strict=True)
                                if not bk.is_bkcc
                            ]
                            if pos < len(non_bkcc_vals):
                                esc_v = non_bkcc_vals[pos].replace("'", "''")
                                val_found = f"'{esc_v}'"
                            break
                    row_vals.append(val_found)
                comp_insert_rows.append(
                    f"    ({', '.join(row_vals)}, TIMESTAMP '2026-10-01 06:00:00', "
                    f"'{_sql_lit(comp_rec_src)}')"
                )
            parts.append(
                "\n".join(
                    [
                        f"CREATE OR REPLACE TABLE bronze.{raw_short} (",
                        *col_defs,
                        "    load_dts TIMESTAMP,",
                        "    rec_src VARCHAR",
                        ");",
                        f"INSERT INTO bronze.{raw_short} VALUES",
                        ",\n".join(comp_insert_rows) + ";",
                    ]
                )
            )

    # 2. Bronze source tables & seed rows for each Link
    for link in ir_obj.links:
        tbl = _validate_ident(link.name)
        sm0_dict = link.source_mappings[0] if link.source_mappings else {}
        rec_src = (
            str(sm0_dict.get("rec_src_value", "SYSTEM_SEED"))
            if isinstance(sm0_dict, dict)
            else "SYSTEM_SEED"
        )
        link_stg_tbl = (
            str(sm0_dict.get("staging_table", f"stg_{tbl}"))
            if isinstance(sm0_dict, dict)
            else f"stg_{tbl}"
        )
        fk_src_map = sm0_dict.get("fk_source_columns") or {} if isinstance(sm0_dict, dict) else {}
        col_defs = []
        row1_tokens: list[str] = []
        row2_tokens: list[str] = []
        row3_tokens: list[str] = []
        row4_tokens: list[str] = []
        target_counts: dict[str, int] = {}
        for ref_idx, ref in enumerate(link.hub_references):
            fk_col = _validate_ident(ref.fk_column_name)
            t_hub = hub_by_name[ref.target_hub]
            occ = target_counts.get(t_hub.name, 0)
            target_counts[t_hub.name] = occ + 1
            fk_src_col = str(fk_src_map.get(ref.fk_column_name, ref.fk_column_name))
            ref_bkcc_lit = resolve_staging_bkcc_literal(
                ir_obj,
                link_stg_tbl,
                fk_src_col,
                ref.target_hub,
                role_name=ref.role_name,
                registry=bkcc_registry,
            )
            scoped_rows = _filter_hub_rows_for_bkcc(
                t_hub,
                hub_bk_rows[t_hub.name],
                ref_bkcc_lit,
            )
            h_r1 = scoped_rows[0]
            h_r2 = scoped_rows[1] if len(scoped_rows) > 1 else scoped_rows[0]
            r1_bks = h_r1 if (occ == 0 or scoped_rows != hub_bk_rows[t_hub.name]) else h_r2
            r2_bks = h_r2
            for bk, val1, val2 in zip(t_hub.business_keys, r1_bks, r2_bks, strict=True):
                raw_col = _validate_ident(f"{fk_col}__{bk.name}")
                col_defs.append(f"    {raw_col} VARCHAR,")
                eff_v1 = (ref_bkcc_lit or val1) if bk.is_bkcc else val1
                eff_v2 = (ref_bkcc_lit or val2) if bk.is_bkcc else val2
                esc1 = eff_v1.replace("'", "''")
                esc2 = eff_v2.replace("'", "''")
                row1_tokens.append(f"'{esc1}'")
                if ref_idx == 0:
                    row2_tokens.append(f"'{esc2}'")
                    row3_tokens.append(f"'{esc1}'")
                    row4_tokens.append(f"'{esc2}'")
                else:
                    row2_tokens.append(f"'{esc2}'" if bk.is_bkcc else "NULL")
                    row3_tokens.append(f"'{esc1}'" if bk.is_bkcc else f"'{ZERO_KEY_UNKNOWN_TOKEN}'")
                    row4_tokens.append(f"'{esc2}'" if bk.is_bkcc else f"'{ZERO_KEY_ERROR_TOKEN}'")
            t_bkcc_col_expr = _effective_hub_bkcc_column_expr(t_hub)
            if t_bkcc_col_expr and t_bkcc_col_expr not in {bk.name for bk in t_hub.business_keys}:
                t_bkcc_idx = next(
                    (idx for idx, bk in enumerate(t_hub.business_keys) if bk.is_bkcc), 0
                )
                extra_fk_bkcc_col = _validate_ident(f"{fk_col}__{t_bkcc_col_expr}")
                col_defs.append(f"    {extra_fk_bkcc_col} VARCHAR,")
                v1_bkcc = r1_bks[t_bkcc_idx].replace("'", "''")
                v2_bkcc = r2_bks[t_bkcc_idx].replace("'", "''")
                row1_tokens.append(f"'{v1_bkcc}'")
                row2_tokens.append(f"'{v2_bkcc}'")
                row3_tokens.append(f"'{v1_bkcc}'")
                row4_tokens.append(f"'{v2_bkcc}'")

        r1_sql = ", ".join(row1_tokens)
        r2_sql = ", ".join(row2_tokens)
        r3_sql = ", ".join(row3_tokens)
        r4_sql = ", ".join(row4_tokens)
        parts.append(
            "\n".join(
                [
                    f"CREATE OR REPLACE TABLE bronze.src_{tbl} (",
                    *col_defs,
                    "    load_dts TIMESTAMP,",
                    "    rec_src VARCHAR",
                    ");",
                    f"INSERT INTO bronze.src_{tbl} VALUES",
                    f"    ({r1_sql}, TIMESTAMP '2026-10-01 06:00:00', '{rec_src}'),",
                    f"    ({r2_sql}, TIMESTAMP '2026-10-01 06:00:00', '{rec_src}'),",
                    f"    ({r3_sql}, TIMESTAMP '2026-10-01 06:00:00', '{rec_src}'),",
                    f"    ({r4_sql}, TIMESTAMP '2026-10-01 06:00:00', '{rec_src}');",
                ]
            )
        )

    # 3. Bronze source tables & seed rows for each Satellite
    for sat in ir_obj.satellites:
        tbl = _validate_ident(sat.name)
        rec_src = sat.source_mapping.rec_src_value or "SYSTEM_SEED"
        has_applied = sat.applied_dts_column is not None
        data_cols = _satellite_all_data_columns(sat)
        col_defs = []
        parent_val_rows: list[list[str]] = []
        sat_source_rows: list[dict[str, Any]] = []
        sat_stg = sat.source_mapping.staging_table
        for key in _table_lookup_keys(sat_stg):
            if key in loaded_bronze:
                sat_source_rows = loaded_bronze[key]
                break

        if sat.parent_entity in hub_by_name:
            p_hub = hub_by_name[sat.parent_entity]
            p_bkcc_col_expr = _effective_hub_bkcc_column_expr(p_hub)
            p_bk_names = {bk.name for bk in p_hub.business_keys}
            p_extra_bkcc = (
                _validate_ident(p_bkcc_col_expr)
                if p_bkcc_col_expr and p_bkcc_col_expr not in p_bk_names
                else None
            )
            p_bkcc_idx = next((idx for idx, bk in enumerate(p_hub.business_keys) if bk.is_bkcc), 0)
            sat_bkcc_lit = (
                resolve_staging_bkcc_literal(
                    ir_obj,
                    sat_stg,
                    "*",
                    p_hub.name,
                    registry=bkcc_registry,
                )
                if not p_bkcc_col_expr
                else None
            )
            for bk in p_hub.business_keys:
                col_defs.append(f"    parent_bk__{_validate_ident(bk.name)} VARCHAR,")
            if p_extra_bkcc:
                col_defs.append(f"    parent_bk__{p_extra_bkcc} VARCHAR,")
            scoped_hub_rows = _filter_hub_rows_for_bkcc(
                p_hub,
                hub_bk_rows[p_hub.name],
                sat_bkcc_lit,
            )
            for row_bks in scoped_hub_rows:
                p_vals = [f"'{v.replace(chr(39), chr(39) * 2)}'" for v in row_bks]
                if p_extra_bkcc:
                    bkcc_v = row_bks[p_bkcc_idx] if p_bkcc_idx < len(row_bks) else "GLOBAL"
                    p_vals.append(f"'{bkcc_v.replace(chr(39), chr(39) * 2)}'")
                parent_val_rows.append(p_vals)
            if not sat_source_rows:
                for sm in p_hub.source_mappings:
                    for key in _table_lookup_keys(sm.staging_table):
                        if key in loaded_bronze:
                            sat_source_rows = loaded_bronze[key]
                            break
                    if sat_source_rows:
                        break
        elif sat.parent_entity in link_by_name:
            p_lnk = link_by_name[sat.parent_entity]
            lnk_sm0 = p_lnk.source_mappings[0] if p_lnk.source_mappings else {}
            lnk_stg = (
                str(lnk_sm0.get("staging_table", f"stg_{p_lnk.name}"))
                if isinstance(lnk_sm0, dict)
                else f"stg_{p_lnk.name}"
            )
            lnk_fk_src_map = (
                lnk_sm0.get("fk_source_columns") or {} if isinstance(lnk_sm0, dict) else {}
            )
            target_counts = {}
            r1_parent_vals: list[str] = []
            r2_parent_vals: list[str] = []
            for ref_idx, ref in enumerate(p_lnk.hub_references):
                fk_col = _validate_ident(ref.fk_column_name)
                col_defs.append(f"    parent_fk__{fk_col} VARCHAR,")
                t_hub = hub_by_name[ref.target_hub]
                occ = target_counts.get(t_hub.name, 0)
                target_counts[t_hub.name] = occ + 1
                fk_src_col = str(lnk_fk_src_map.get(ref.fk_column_name, ref.fk_column_name))
                ref_bkcc_lit = resolve_staging_bkcc_literal(
                    ir_obj,
                    lnk_stg,
                    fk_src_col,
                    ref.target_hub,
                    role_name=ref.role_name,
                    registry=bkcc_registry,
                )
                scoped_rows = _filter_hub_rows_for_bkcc(
                    t_hub,
                    hub_bk_rows[t_hub.name],
                    ref_bkcc_lit,
                )
                h_r1 = scoped_rows[0]
                h_r2 = scoped_rows[1] if len(scoped_rows) > 1 else scoped_rows[0]
                r1_tokens = h_r1 if (occ == 0 or scoped_rows != hub_bk_rows[t_hub.name]) else h_r2
                t_has_bkcc = any(bool(bk.is_bkcc) for bk in t_hub.business_keys)
                t_bkcc_lit = ref_bkcc_lit or _effective_hub_bkcc_literal(t_hub) or GLOBAL_BKCC
                r1_hk = compute_dv2_length_prefixed_sha256(
                    r1_tokens,
                    is_business_key=True,
                    bkcc_prefix=None if t_has_bkcc else t_bkcc_lit,
                ).lower()
                r2_hk = compute_dv2_length_prefixed_sha256(
                    h_r2,
                    is_business_key=True,
                    bkcc_prefix=None if t_has_bkcc else t_bkcc_lit,
                ).lower()
                r1_parent_vals.append(f"'{r1_hk}'")
                if ref_idx == 0:
                    r2_parent_vals.append(f"'{r2_hk}'")
                else:
                    r2_parent_vals.append(f"'{ZERO_KEY_UNKNOWN}'")
            parent_val_rows = [r1_parent_vals, r2_parent_vals]

        col_defs.append("    load_dts TIMESTAMP,")
        if has_applied:
            col_defs.append("    applied_dts TIMESTAMP,")
        col_defs.append("    rec_src VARCHAR" + ("," if data_cols else ""))

        for idx_c, col in enumerate(data_cols):
            c_name = _validate_ident(col.name)
            c_type = _normalize_duckdb_type(col.data_type)
            comma = "," if idx_c < len(data_cols) - 1 else ""
            col_defs.append(f"    {c_name} {c_type}{comma}")

        app_lit = "TIMESTAMP '2026-09-30 00:00:00', " if has_applied else ""
        insert_rows: list[str] = []
        for idx_r, p_vals in enumerate(parent_val_rows):
            src_row = sat_source_rows[idx_r] if idx_r < len(sat_source_rows) else None
            row_load_dts = (
                str(src_row.get("load_dts") or "2026-10-01 06:00:00").replace("'", "''")
                if src_row is not None
                else "2026-10-01 06:00:00"
            )
            row_app_dts = (
                str(src_row.get("applied_dts") or "2026-09-30 00:00:00").replace("'", "''")
                if src_row is not None
                else "2026-09-30 00:00:00"
            )
            row_app_lit = f"TIMESTAMP '{row_app_dts}', " if has_applied else ""
            r_payload = [
                _format_bronze_payload_sql_literal(c, src_row, idx_r + 1) for c in data_cols
            ]
            r_pay_sql = f", {', '.join(r_payload)}" if r_payload else ""
            insert_rows.append(
                f"    ({', '.join(p_vals)}, TIMESTAMP '{row_load_dts}', "
                f"{row_app_lit}'{rec_src}'{r_pay_sql})"
            )

        if include_batch4_restatement_in_seed and sat.name == bitemp_sat_name and parent_val_rows:
            r3_payload = [
                _satellite_seed_sql_literal(c, len(parent_val_rows) + 1) for c in data_cols
            ]
            r3_pay_sql = f", {', '.join(r3_payload)}" if r3_payload else ""
            insert_rows.append(
                f"    ({', '.join(parent_val_rows[0])}, TIMESTAMP '2026-11-15 09:15:00', "
                f"{app_lit}'{rec_src}'{r3_pay_sql})"
            )

        parts.append(
            "\n".join(
                [
                    f"CREATE OR REPLACE TABLE bronze.src_{tbl} (",
                    *col_defs,
                    ");",
                    f"INSERT INTO bronze.src_{tbl} VALUES",
                    ",\n".join(insert_rows) + ";",
                ]
            )
        )

    # 4. Target Vault DDL + Dual Zero Keys + Ghost Records
    for hub in ir_obj.hubs:
        parts.append(_compile_generic_hub_ddl_and_zero_keys(hub))
    for ref_tbl in ir_obj.reference_tables or []:
        parts.append(_compile_generic_reference_ddl_and_seed(ref_tbl))
    for link in ir_obj.links:
        parts.append(_compile_generic_link_ddl_and_zero_keys(link))
    for sat in ir_obj.satellites:
        parts.append(_compile_generic_satellite_ddl_and_ghost(sat))

    return "\n\n".join(parts)


def _compile_generic_zone_1_staging_views(ir_obj: CanonicalIR) -> str:
    """Compile Zone 1 Hashed Staging Views (`staging.stg_*`) with Algorithm A.5 SHA-256."""
    hub_by_name = {h.name: h for h in ir_obj.hubs}
    link_by_name = {lnk.name: lnk for lnk in ir_obj.links}
    bkcc_registry = build_staging_bkcc_registry(ir_obj)
    parts = [
        "-- =============================================================================",
        "-- ZONE 1: HASHED STAGING VIEWS (ALGORITHM A.5 LENGTH-PREFIXED SHA-256)",
        "-- =============================================================================",
    ]
    created_views: set[str] = set()
    alias_targets: list[tuple[str, str]] = []
    stg_to_hub_mappings: dict[str, list[tuple[IRHub, Any]]] = {}
    view_body_by_name: dict[str, str] = {}

    for hub in ir_obj.hubs:
        tbl = _validate_ident(hub.name)
        view_name = f"stg_{tbl}"
        created_views.add(view_name)
        hk_col = _validate_ident(hub.hk_column.name)
        bk_cols = [_validate_ident(bk.name) for bk in hub.business_keys]
        non_bkcc_cols = [
            _validate_ident(bk.name) for bk in hub.business_keys if not bk.is_bkcc
        ] or bk_cols
        bkcc_col_expr = _effective_hub_bkcc_column_expr(hub)
        bkcc_lit = _effective_hub_bkcc_literal(hub)

        distinct_sm_bkccs: list[str | None] = []
        for sm in hub.source_mappings:
            stg_alias = _validate_ident(sm.staging_table)
            stg_to_hub_mappings.setdefault(stg_alias, []).append((hub, sm))
            alias_targets.append((stg_alias, view_name))
            primary_src_col = next(
                (c for c in sm.bk_source_columns if c.lower() != "bkcc"),
                non_bkcc_cols[0] if non_bkcc_cols else "*",
            )
            sm_bkcc = resolve_staging_bkcc_literal(
                ir_obj,
                getattr(sm, "bronze_table", None) or sm.staging_table,
                primary_src_col,
                hub.name,
                registry=bkcc_registry,
            )
            if sm_bkcc not in distinct_sm_bkccs:
                distinct_sm_bkccs.append(sm_bkcc)

        if bkcc_col_expr:
            hk_expr = build_algorithm_a5_sha256_sql(
                non_bkcc_cols,
                is_business_key=True,
                bkcc_column_expr=_validate_ident(bkcc_col_expr),
                key_columns=non_bkcc_cols,
            )
            bk_csv = ", ".join(bk_cols)
            parts.append(
                f"CREATE OR REPLACE VIEW staging.{view_name} AS\n"
                f"SELECT\n"
                f"    {hk_expr} AS {hk_col},\n"
                f"    {bk_csv},\n"
                f"    load_dts,\n"
                f"    rec_src\n"
                f"FROM bronze.src_{tbl};"
            )
        elif len(distinct_sm_bkccs) <= 1:
            has_bkcc_col = any(bool(bk.is_bkcc) for bk in hub.business_keys)
            scoped_bkcc = distinct_sm_bkccs[0] if distinct_sm_bkccs else bkcc_lit
            if scoped_bkcc is None and not has_bkcc_col:
                scoped_bkcc = GLOBAL_BKCC
            if scoped_bkcc is None and has_bkcc_col:
                hk_expr = build_algorithm_a5_sha256_sql(
                    bk_cols,
                    is_business_key=True,
                    key_columns=non_bkcc_cols,
                )
            else:
                hk_expr = build_algorithm_a5_sha256_sql(
                    non_bkcc_cols,
                    bkcc_literal=scoped_bkcc,
                    is_business_key=True,
                    key_columns=non_bkcc_cols,
                )
            bk_select_exprs = [
                f"'{_sql_lit(scoped_bkcc)}' AS {_validate_ident(bk.name)}"
                if bk.is_bkcc and scoped_bkcc is not None
                else _validate_ident(bk.name)
                for bk in hub.business_keys
            ]
            bk_csv = ", ".join(bk_select_exprs)
            parts.append(
                f"CREATE OR REPLACE VIEW staging.{view_name} AS\n"
                f"SELECT\n"
                f"    {hk_expr} AS {hk_col},\n"
                f"    {bk_csv},\n"
                f"    load_dts,\n"
                f"    rec_src\n"
                f"FROM bronze.src_{tbl};"
            )
        else:
            union_branches: list[str] = []
            for sm_bkcc in distinct_sm_bkccs:
                eff_sm_bkcc = sm_bkcc or bkcc_lit or GLOBAL_BKCC
                hk_expr = build_algorithm_a5_sha256_sql(
                    non_bkcc_cols,
                    bkcc_literal=eff_sm_bkcc,
                    is_business_key=True,
                    key_columns=non_bkcc_cols,
                )
                bk_select_exprs = [
                    f"'{_sql_lit(eff_sm_bkcc)}' AS {_validate_ident(bk.name)}"
                    if bk.is_bkcc and eff_sm_bkcc is not None
                    else _validate_ident(bk.name)
                    for bk in hub.business_keys
                ]
                bk_csv = ", ".join(bk_select_exprs)
                where_clause = (
                    f"\nWHERE UPPER(TRIM(CAST(bkcc AS VARCHAR))) = '{_sql_lit(eff_sm_bkcc)}'"
                    if eff_sm_bkcc is not None and any(bk.is_bkcc for bk in hub.business_keys)
                    else ""
                )
                union_branches.append(
                    f"SELECT\n"
                    f"    {hk_expr} AS {hk_col},\n"
                    f"    {bk_csv},\n"
                    f"    load_dts,\n"
                    f"    rec_src\n"
                    f"FROM bronze.src_{tbl}{where_clause}"
                )
            parts.append(
                f"CREATE OR REPLACE VIEW staging.{view_name} AS\n"
                + "\nUNION ALL\n".join(union_branches)
                + ";"
            )

    for link in ir_obj.links:
        tbl = _validate_ident(link.name)
        view_name = f"stg_{tbl}"
        created_views.add(view_name)
        hk_col = _validate_ident(link.hk_column.name)
        sm0_dict = link.source_mappings[0] if link.source_mappings else {}
        link_stg_tbl = (
            str(sm0_dict.get("staging_table", f"stg_{tbl}"))
            if isinstance(sm0_dict, dict)
            else f"stg_{tbl}"
        )
        fk_src_map = sm0_dict.get("fk_source_columns") or {} if isinstance(sm0_dict, dict) else {}
        fk_cols: list[str] = []
        fk_cte_exprs: list[str] = []
        for ref in link.hub_references:
            fk_col = _validate_ident(ref.fk_column_name)
            fk_cols.append(fk_col)
            t_hub = hub_by_name[ref.target_hub]
            fk_src_col = str(fk_src_map.get(ref.fk_column_name, ref.fk_column_name))
            ref_bkcc_lit = resolve_staging_bkcc_literal(
                ir_obj,
                link_stg_tbl,
                fk_src_col,
                ref.target_hub,
                role_name=ref.role_name,
                registry=bkcc_registry,
            )
            raw_bk_cols = [_validate_ident(f"{fk_col}__{bk.name}") for bk in t_hub.business_keys]
            non_bkcc_cols = [
                _validate_ident(f"{fk_col}__{bk.name}")
                for bk in t_hub.business_keys
                if not bk.is_bkcc
            ] or raw_bk_cols
            error_pred = " OR ".join(
                f"TRIM(CAST({c} AS VARCHAR)) = '{ZERO_KEY_ERROR_TOKEN}'" for c in non_bkcc_cols
            )
            unknown_pred = " OR ".join(
                f"(NULLIF(TRIM(CAST({c} AS VARCHAR)), '') IS NULL "
                f"OR TRIM(CAST({c} AS VARCHAR)) = '{ZERO_KEY_UNKNOWN_TOKEN}')"
                for c in non_bkcc_cols
            )
            t_bkcc_col_expr = _effective_hub_bkcc_column_expr(t_hub)
            t_bkcc_lit = _effective_hub_bkcc_literal(t_hub)
            t_has_bkcc_col = any(bool(bk.is_bkcc) for bk in t_hub.business_keys)
            eff_fk_bkcc_lit = ref_bkcc_lit or t_bkcc_lit
            if t_bkcc_col_expr:
                raw_hash = build_algorithm_a5_sha256_sql(
                    non_bkcc_cols,
                    is_business_key=True,
                    bkcc_column_expr=_validate_ident(f"{fk_col}__{t_bkcc_col_expr}"),
                    key_columns=non_bkcc_cols,
                )
            elif eff_fk_bkcc_lit is None and t_has_bkcc_col:
                raw_hash = build_algorithm_a5_sha256_sql(
                    raw_bk_cols,
                    is_business_key=True,
                    key_columns=non_bkcc_cols,
                )
            else:
                raw_hash = build_algorithm_a5_sha256_sql(
                    non_bkcc_cols,
                    bkcc_literal=eff_fk_bkcc_lit or GLOBAL_BKCC,
                    is_business_key=True,
                    key_columns=non_bkcc_cols,
                )
            fk_cte_exprs.append(
                f"        COALESCE(\n"
                f"            CASE\n"
                f"                WHEN {error_pred} THEN REPEAT('f', 64)\n"
                f"                WHEN {unknown_pred} THEN REPEAT('0', 64)\n"
                f"                ELSE {raw_hash}\n"
                f"            END,\n"
                f"            REPEAT('0', 64)\n"
                f"        ) AS {fk_col}"
            )
        all_fk_err_pred = " AND ".join(f"{c} = REPEAT('f', 64)" for c in fk_cols)
        all_fk_unk_pred = " AND ".join(f"{c} = REPEAT('0', 64)" for c in fk_cols)
        link_hk_expr = build_algorithm_a5_sha256_sql(
            fk_cols,
            is_business_key=True,
            error_predicate=all_fk_err_pred,
            unknown_predicate=all_fk_unk_pred,
        )
        fk_csv = ", ".join(fk_cols)
        fk_cte_body = ",\n".join(fk_cte_exprs)
        link_body = (
            f"WITH fk_resolved AS (\n"
            f"    SELECT\n"
            f"{fk_cte_body},\n"
            f"        load_dts,\n"
            f"        rec_src\n"
            f"    FROM bronze.src_{tbl}\n"
            f")\n"
            f"SELECT\n"
            f"    {link_hk_expr} AS {hk_col},\n"
            f"    {fk_csv},\n"
            f"    load_dts,\n"
            f"    rec_src\n"
            f"FROM fk_resolved;"
        )
        view_body_by_name[view_name] = link_body
        parts.append(f"CREATE OR REPLACE VIEW staging.{view_name} AS\n{link_body}")
        for sm_dict in link.source_mappings:
            if "staging_table" in sm_dict:
                alias_targets.append((_validate_ident(str(sm_dict["staging_table"])), view_name))

    for sat in ir_obj.satellites:
        tbl = _validate_ident(sat.name)
        view_name = f"stg_{tbl}"
        created_views.add(view_name)
        parent_hk = _validate_ident(sat.parent_hk_column.name)
        has_applied = sat.applied_dts_column is not None
        data_cols = _satellite_all_data_columns(sat)

        if sat.parent_entity in hub_by_name:
            p_hub = hub_by_name[sat.parent_entity]
            p_cols = [f"parent_bk__{_validate_ident(bk.name)}" for bk in p_hub.business_keys]
            non_bkcc_p_cols = [
                f"parent_bk__{_validate_ident(bk.name)}"
                for bk in p_hub.business_keys
                if not bk.is_bkcc
            ] or p_cols
            p_bkcc_col_expr = _effective_hub_bkcc_column_expr(p_hub)
            p_bkcc_lit = _effective_hub_bkcc_literal(p_hub)
            p_has_bkcc_col = any(bool(bk.is_bkcc) for bk in p_hub.business_keys)
            if p_bkcc_col_expr:
                parent_hk_expr = build_algorithm_a5_sha256_sql(
                    non_bkcc_p_cols,
                    is_business_key=True,
                    bkcc_column_expr=f"parent_bk__{_validate_ident(p_bkcc_col_expr)}",
                    key_columns=non_bkcc_p_cols,
                )
            else:
                sat_bkcc_lit = resolve_staging_bkcc_literal(
                    ir_obj,
                    sat.source_mapping.staging_table,
                    "*",
                    p_hub.name,
                    registry=bkcc_registry,
                )
                eff_sat_bkcc_lit = sat_bkcc_lit or p_bkcc_lit
                if eff_sat_bkcc_lit is None and p_has_bkcc_col:
                    parent_hk_expr = build_algorithm_a5_sha256_sql(
                        p_cols,
                        is_business_key=True,
                        key_columns=non_bkcc_p_cols,
                    )
                else:
                    parent_hk_expr = build_algorithm_a5_sha256_sql(
                        non_bkcc_p_cols,
                        bkcc_literal=eff_sat_bkcc_lit or GLOBAL_BKCC,
                        is_business_key=True,
                        key_columns=non_bkcc_p_cols,
                    )
        else:
            p_lnk = link_by_name[sat.parent_entity]
            p_cols = [
                f"parent_fk__{_validate_ident(ref.fk_column_name)}" for ref in p_lnk.hub_references
            ]
            all_p_err_pred = " AND ".join(f"{c} = REPEAT('f', 64)" for c in p_cols)
            all_p_unk_pred = " AND ".join(f"{c} = REPEAT('0', 64)" for c in p_cols)
            parent_hk_expr = build_algorithm_a5_sha256_sql(
                p_cols,
                is_business_key=True,
                error_predicate=all_p_err_pred,
                unknown_predicate=all_p_unk_pred,
            )

        payload_names = [_validate_ident(c.name) for c in data_cols]
        hash_diff_expr = build_algorithm_a5_sha256_sql(payload_names, is_business_key=False)
        payload_csv = ", ".join(payload_names)
        applied_sel = "    applied_dts,\n" if has_applied else ""
        payload_sel = f",\n    {payload_csv}" if payload_csv else ""
        sat_body = (
            f"SELECT\n"
            f"    {parent_hk_expr} AS {parent_hk},\n"
            f"    load_dts,\n"
            f"{applied_sel}"
            f"    rec_src,\n"
            f"    {hash_diff_expr} AS hash_diff"
            f"{payload_sel}\n"
            f"FROM bronze.src_{tbl};"
        )
        view_body_by_name[view_name] = sat_body
        parts.append(f"CREATE OR REPLACE VIEW staging.{view_name} AS\n{sat_body}")
        alias_targets.append((_validate_ident(sat.source_mapping.staging_table), view_name))

    for alias_name, target_view in alias_targets:
        if alias_name in created_views:
            continue
        created_views.add(alias_name)
        hub_pairs = stg_to_hub_mappings.get(alias_name, [])
        if len(hub_pairs) > 1:
            raw_short = _validate_ident(alias_name.removeprefix("stg_").split(".")[-1])
            select_exprs: list[str] = []
            seen_cols: set[str] = set()
            for hub, sm in hub_pairs:
                hk_col = _validate_ident(hub.hk_column.name)
                src_non_bkcc_cols = [
                    _validate_ident(c) for c in sm.bk_source_columns if c.lower() != "bkcc"
                ]
                primary_col = src_non_bkcc_cols[0] if src_non_bkcc_cols else "*"
                sm_bkcc = resolve_staging_bkcc_literal(
                    ir_obj,
                    getattr(sm, "bronze_table", None) or sm.staging_table,
                    primary_col,
                    hub.name,
                    registry=bkcc_registry,
                )
                hk_expr = build_algorithm_a5_sha256_sql(
                    src_non_bkcc_cols,
                    bkcc_literal=sm_bkcc,
                    is_business_key=True,
                )
                if hk_col not in seen_cols:
                    seen_cols.add(hk_col)
                    select_exprs.append(f"    {hk_expr} AS {hk_col}")
                if sm_bkcc is not None:
                    bkcc_alias = f"bkcc__{_validate_ident(hub.name)}"
                    if bkcc_alias not in seen_cols:
                        seen_cols.add(bkcc_alias)
                        select_exprs.append(f"    '{_sql_lit(sm_bkcc)}' AS {bkcc_alias}")
                for c_ident in src_non_bkcc_cols:
                    if c_ident not in seen_cols:
                        seen_cols.add(c_ident)
                        select_exprs.append(f"    {c_ident}")
            select_exprs.extend(["    load_dts", "    rec_src"])
            parts.append(
                f"CREATE OR REPLACE VIEW staging.{alias_name} AS\n"
                f"SELECT\n" + ",\n".join(select_exprs) + f"\nFROM bronze.{raw_short};"
            )
        elif len(hub_pairs) == 1:
            only_hub, sm = hub_pairs[0]
            tbl = _validate_ident(only_hub.name)
            hk_col = _validate_ident(only_hub.hk_column.name)
            non_bkcc_cols = [
                _validate_ident(bk.name) for bk in only_hub.business_keys if not bk.is_bkcc
            ]
            primary_col = next(
                (c for c in sm.bk_source_columns if c.lower() != "bkcc"),
                non_bkcc_cols[0] if non_bkcc_cols else "*",
            )
            sm_bkcc = resolve_staging_bkcc_literal(
                ir_obj,
                getattr(sm, "bronze_table", None) or sm.staging_table,
                primary_col,
                only_hub.name,
                registry=bkcc_registry,
            )
            hk_expr = build_algorithm_a5_sha256_sql(
                non_bkcc_cols,
                bkcc_literal=sm_bkcc,
                is_business_key=True,
            )
            bk_select_exprs = [
                f"'{_sql_lit(sm_bkcc)}' AS {_validate_ident(bk.name)}"
                if bk.is_bkcc and sm_bkcc is not None
                else _validate_ident(bk.name)
                for bk in only_hub.business_keys
            ]
            where_clause = (
                f"\nWHERE UPPER(TRIM(CAST(bkcc AS VARCHAR))) = '{_sql_lit(sm_bkcc)}'"
                if sm_bkcc is not None
                and len(only_hub.source_mappings) > 1
                and any(bk.is_bkcc for bk in only_hub.business_keys)
                else ""
            )
            parts.append(
                f"CREATE OR REPLACE VIEW staging.{alias_name} AS\n"
                f"SELECT\n"
                f"    {hk_expr} AS {hk_col},\n"
                f"    {', '.join(bk_select_exprs)},\n"
                f"    load_dts,\n"
                f"    rec_src\n"
                f"FROM bronze.src_{tbl}{where_clause};"
            )
        else:
            body_sql = view_body_by_name.get(
                target_view,
                f"SELECT load_dts, rec_src FROM staging.{target_view};",
            )
            parts.append(f"CREATE OR REPLACE VIEW staging.{alias_name} AS\n{body_sql}")

    return "\n\n".join(parts)


def _compile_generic_zone_2_raw_vault_dml(ir_obj: CanonicalIR) -> str:
    """Compile Zone 2 idempotent INSERT-only Raw/Business Vault load statements."""
    parts = [
        "-- =============================================================================",
        "-- ZONE 2: IDEMPOTENT INSERT-ONLY RAW & BUSINESS VAULT LOADS",
        "-- =============================================================================",
    ]

    for hub in ir_obj.hubs:
        tbl = _validate_ident(hub.name)
        hk = _validate_ident(hub.hk_column.name)
        bk_cols = [_validate_ident(bk.name) for bk in hub.business_keys]
        bk_csv = ", ".join(bk_cols)
        src_bk_csv = ", ".join(f"src.{c}" for c in bk_cols)
        parts.append(
            f"INSERT INTO raw_vault.{tbl} ({hk}, {bk_csv}, load_dts, rec_src)\n"
            f"SELECT\n"
            f"    src.{hk},\n"
            f"    {src_bk_csv},\n"
            f"    MIN(src.load_dts) AS load_dts,\n"
            f"    ANY_VALUE(src.rec_src) AS rec_src\n"
            f"FROM staging.stg_{tbl} src\n"
            f"WHERE src.{hk} NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')\n"
            f"  AND NOT EXISTS (\n"
            f"      SELECT 1 FROM raw_vault.{tbl} h\n"
            f"      WHERE h.{hk} = src.{hk}\n"
            f"  )\n"
            f"GROUP BY src.{hk}, {src_bk_csv};"
        )

    for link in ir_obj.links:
        schema = _link_schema(link)
        tbl = _validate_ident(link.name)
        hk = _validate_ident(link.hk_column.name)
        fk_cols = [_validate_ident(ref.fk_column_name) for ref in link.hub_references]
        fk_csv = ", ".join(fk_cols)
        src_fk_csv = ", ".join(f"src.{c}" for c in fk_cols)
        is_sal_link = (link.name.startswith("sal_") or link.link_type == "SAME_AS") and len(
            fk_cols
        ) == 2
        sal_where_clause = ""
        if is_sal_link:
            m_fk, d_fk = fk_cols[0], fk_cols[1]
            sal_where_clause = (
                f"  AND src.{m_fk} <> src.{d_fk}\n"
                f"  AND NOT EXISTS (\n"
                f"      SELECT 1 FROM {schema}.{tbl} l_inv\n"
                f"      WHERE l_inv.{m_fk} = src.{d_fk}\n"
                f"        AND l_inv.{d_fk} = src.{m_fk}\n"
                f"  )\n"
                f"  AND NOT EXISTS (\n"
                f"      SELECT 1 FROM staging.stg_{tbl} src_inv\n"
                f"      WHERE src_inv.{m_fk} = src.{d_fk}\n"
                f"        AND src_inv.{d_fk} = src.{m_fk}\n"
                f"        AND src_inv.{m_fk} <> src_inv.{d_fk}\n"
                f"        AND (\n"
                f"            src_inv.load_dts < src.load_dts\n"
                f"            OR (\n"
                f"                src_inv.load_dts = src.load_dts\n"
                f"                AND src_inv.{m_fk} < src.{m_fk}\n"
                f"            )\n"
                f"        )\n"
                f"  )\n"
            )
        parts.append(
            f"INSERT INTO {schema}.{tbl} ({hk}, {fk_csv}, load_dts, rec_src)\n"
            f"SELECT\n"
            f"    src.{hk},\n"
            f"    {src_fk_csv},\n"
            f"    MIN(src.load_dts) AS load_dts,\n"
            f"    ANY_VALUE(src.rec_src) AS rec_src\n"
            f"FROM staging.stg_{tbl} src\n"
            f"WHERE src.{hk} NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')\n"
            f"{sal_where_clause}"
            f"  AND NOT EXISTS (\n"
            f"      SELECT 1 FROM {schema}.{tbl} l\n"
            f"      WHERE l.{hk} = src.{hk}\n"
            f"  )\n"
            f"GROUP BY src.{hk}, {src_fk_csv};"
        )

    for sat in ir_obj.satellites:
        schema = _satellite_schema(sat)
        tbl = _validate_ident(sat.name)
        parent_hk = _validate_ident(sat.parent_hk_column.name)
        has_applied = sat.applied_dts_column is not None
        data_cols = _satellite_all_data_columns(sat)
        payload_cols = [_validate_ident(c.name) for c in data_cols]
        payload_csv = ", ".join(payload_cols)
        src_payload_csv = ", ".join(f"src.{c}" for c in payload_cols)
        app_col = "applied_dts, " if has_applied else ""
        src_app_col = "src.applied_dts, " if has_applied else ""
        part_cols = [parent_hk, *(_validate_ident(c) for c in _satellite_child_key_names(sat))]
        part_csv = ", ".join(part_cols)
        join_on = " AND ".join(f"cur.{c} = src.{c}" for c in part_cols)
        col_tail = f", {payload_csv}" if payload_csv else ""
        sel_tail = f",\n    {src_payload_csv}" if src_payload_csv else ""
        if has_applied:
            assert sat.applied_dts_column is not None
            applied_ident = _validate_ident(sat.applied_dts_column.name)
            v_payload_csv = ", ".join(f"v.{c}" for c in payload_cols)
            s_payload_csv = ", ".join(f"s.{c}" for c in payload_cols)
            comb_payload_csv = ", ".join(f"combined.{c}" for c in payload_cols)
            cand_payload_csv = ", ".join(f"cand.{c}" for c in payload_cols)
            v_sel_tail = f", {v_payload_csv}" if v_payload_csv else ""
            s_sel_tail = f", {s_payload_csv}" if s_payload_csv else ""
            comb_sel_tail = f",\n        {comb_payload_csv}" if comb_payload_csv else ""
            cand_sel_tail = f",\n    {cand_payload_csv}" if cand_payload_csv else ""
            comb_part_csv = ", ".join(f"combined.{c}" for c in part_cols)
            existing_on = " AND ".join(f"existing.{c} = s.{c}" for c in part_cols)
            parts.append(
                f"INSERT INTO {schema}.{tbl} (\n"
                f"    {parent_hk}, load_dts, {applied_ident}, rec_src, hash_diff{col_tail}\n"
                f")\n"
                f"SELECT\n"
                f"    cand.{parent_hk},\n"
                f"    cand.load_dts,\n"
                f"    cand.{applied_ident},\n"
                f"    cand.rec_src,\n"
                f"    cand.hash_diff{cand_sel_tail}\n"
                f"FROM (\n"
                f"    SELECT\n"
                f"        combined.{parent_hk},\n"
                f"        combined.load_dts,\n"
                f"        combined.{applied_ident},\n"
                f"        combined.rec_src,\n"
                f"        combined.hash_diff{comb_sel_tail},\n"
                f"        combined.is_new,\n"
                f"        LAG(combined.hash_diff) OVER (\n"
                f"            PARTITION BY {comb_part_csv}\n"
                f"            ORDER BY combined.{applied_ident} ASC, combined.load_dts ASC\n"
                f"        ) AS prev_hash_diff\n"
                f"    FROM (\n"
                f"        SELECT\n"
                f"            v.{parent_hk}, v.load_dts, v.{applied_ident}, "
                f"v.rec_src, v.hash_diff{v_sel_tail},\n"
                f"            0 AS is_new\n"
                f"        FROM {schema}.{tbl} v\n"
                f"        WHERE v.rec_src <> '{GHOST_REC_SRC}'\n"
                f"        UNION ALL\n"
                f"        SELECT\n"
                f"            s.{parent_hk}, s.load_dts, s.{applied_ident}, "
                f"s.rec_src, s.hash_diff{s_sel_tail},\n"
                f"            1 AS is_new\n"
                f"        FROM (\n"
                f"            SELECT {parent_hk}, load_dts, {applied_ident}, "
                f"rec_src, hash_diff{col_tail}\n"
                f"            FROM staging.stg_{tbl}\n"
                f"            WHERE {parent_hk} NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')\n"
                f"            QUALIFY ROW_NUMBER() OVER (\n"
                f"                PARTITION BY {part_csv}, load_dts\n"
                f"                ORDER BY {applied_ident} DESC\n"
                f"            ) = 1\n"
                f"        ) s\n"
                f"        WHERE NOT EXISTS (\n"
                f"            SELECT 1 FROM {schema}.{tbl} existing\n"
                f"            WHERE {existing_on}\n"
                f"              AND existing.load_dts = s.load_dts\n"
                f"        )\n"
                f"    ) combined\n"
                f") cand\n"
                f"WHERE cand.is_new = 1\n"
                f"  AND (\n"
                f"      cand.prev_hash_diff IS NULL\n"
                f"      OR cand.prev_hash_diff <> cand.hash_diff\n"
                f"  );"
            )
        else:
            parts.append(
                f"INSERT INTO {schema}.{tbl} (\n"
                f"    {parent_hk}, load_dts, {app_col}rec_src, hash_diff{col_tail}\n"
                f")\n"
                f"SELECT\n"
                f"    src.{parent_hk},\n"
                f"    src.load_dts,\n"
                f"    {src_app_col}src.rec_src,\n"
                f"    src.hash_diff{sel_tail}\n"
                f"FROM staging.stg_{tbl} src\n"
                f"LEFT JOIN (\n"
                f"    SELECT {part_csv}, load_dts, hash_diff\n"
                f"    FROM {schema}.{tbl}\n"
                f"    WHERE rec_src <> '{GHOST_REC_SRC}'\n"
                f"    QUALIFY ROW_NUMBER() OVER (\n"
                f"        PARTITION BY {part_csv} ORDER BY load_dts DESC\n"
                f"    ) = 1\n"
                f") cur ON {join_on}\n"
                f"WHERE src.{parent_hk} NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')\n"
                f"  AND (\n"
                f"      cur.{parent_hk} IS NULL\n"
                f"      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)\n"
                f"  );"
            )

    return "\n\n".join(parts)


def _build_generic_pit_view_sql(
    pit: IRPitTable,
    hub_by_name: dict[str, IRHub],
    sat_by_name: dict[str, IRSatellite],
) -> str:
    """Build `CREATE OR REPLACE VIEW information_mart.vw_<pit_name>_current` SQL."""
    pit_tbl = _validate_ident(pit.name)
    parent_hub = hub_by_name[pit.parent_entity]
    hub_tbl = _validate_ident(parent_hub.name)
    hub_hk = _validate_ident(parent_hub.hk_column.name)
    as_of_col = _validate_ident(pit.as_of_dts_column)

    select_cols = [
        f"    pit.{as_of_col}",
        f"    h.{hub_hk}",
        *[f"    h.{_validate_ident(bk.name)}" for bk in parent_hub.business_keys],
    ]
    join_clauses = [
        f"FROM business_vault.{pit_tbl} pit",
        f"INNER JOIN raw_vault.{hub_tbl} h ON h.{hub_hk} = pit.{hub_hk}",
    ]
    seen_col_names = {as_of_col, hub_hk, *(bk.name for bk in parent_hub.business_keys)}

    for idx_s, sat_name in enumerate(pit.tracked_satellites):
        sat = sat_by_name[sat_name]
        sat_tbl = _validate_ident(sat.name)
        sat_schema = _satellite_schema(sat)
        sat_parent_hk = _validate_ident(sat.parent_hk_column.name)
        alias = f"s{idx_s}"
        join_clauses.append(
            f"INNER JOIN {sat_schema}.{sat_tbl} {alias}\n"
            f"    ON {alias}.{sat_parent_hk} = pit.{sat_tbl}_hk\n"
            f"   AND {alias}.load_dts = pit.{sat_tbl}_load_dts"
        )
        for col in sat.payload_columns:
            c_ident = _validate_ident(col.name)
            out_alias = f"{sat_tbl}__{c_ident}" if c_ident in seen_col_names else c_ident
            seen_col_names.add(out_alias)
            select_cols.append(f"    {alias}.{c_ident} AS {out_alias}")

    select_body = ",\n".join(select_cols)
    join_body = "\n".join(join_clauses)
    return (
        f"CREATE OR REPLACE VIEW information_mart.vw_{pit_tbl}_current AS\n"
        f"SELECT\n"
        f"{select_body}\n"
        f"{join_body};"
    )


def _compile_generic_zone_3_business_vault_sql(ir_obj: CanonicalIR) -> str:
    """Compile Zone 3 Business Vault PIT tables and logarithmic snapshot calendar."""
    hub_by_name = {h.name: h for h in ir_obj.hubs}
    sat_by_name = {s.name: s for s in ir_obj.satellites}
    effective_pits = _effective_pit_tables(ir_obj)

    parts = [
        "-- =============================================================================",
        "-- ZONE 3: BUSINESS VAULT & POINT-IN-TIME (PIT) HELPER TABLES",
        "-- =============================================================================",
        "CREATE TABLE IF NOT EXISTS business_vault.snap_logarithmic_calendar (\n"
        "    as_of_dts TIMESTAMP NOT NULL PRIMARY KEY,\n"
        "    window_tier VARCHAR NOT NULL\n"
        ");",
        "INSERT INTO business_vault.snap_logarithmic_calendar\n"
        "SELECT * FROM (\n"
        "    VALUES\n"
        "        (TIMESTAMP '2026-10-01 12:00:00', 'DAILY_ROLLING_30D'),\n"
        "        (TIMESTAMP '2026-10-15 12:00:00', 'DAILY_ROLLING_30D'),\n"
        "        (TIMESTAMP '2026-11-15 23:59:59', 'QUARTER_END_PERMANENT')\n"
        ") v(as_of_dts, window_tier)\n"
        "WHERE NOT EXISTS (\n"
        "    SELECT 1 FROM business_vault.snap_logarithmic_calendar c\n"
        "    WHERE c.as_of_dts = v.as_of_dts\n"
        ");",
        "CREATE OR REPLACE VIEW information_mart.snap_logarithmic_calendar AS\n"
        "SELECT * FROM business_vault.snap_logarithmic_calendar;",
    ]

    for pit in effective_pits:
        if not pit.tracked_satellites:
            continue
        pit_tbl = _validate_ident(pit.name)
        pit_hk = f"hk_{pit_tbl}"
        parent_hub = hub_by_name[pit.parent_entity]
        hub_tbl = _validate_ident(parent_hub.name)
        hub_hk = _validate_ident(parent_hub.hk_column.name)
        as_of_col = _validate_ident(pit.as_of_dts_column)

        ddl_cols = [
            f"    {pit_hk} CHAR(64) NOT NULL PRIMARY KEY,",
            f"    {hub_hk} CHAR(64) NOT NULL,",
            f"    {as_of_col} TIMESTAMP NOT NULL,",
        ]
        ptr_col_names: list[str] = []
        subquery_selects: list[str] = [
            f"        LOWER(SHA256(\n"
            f"            '64:' || h.{hub_hk} || '||'\n"
            f"            || CAST(OCTET_LENGTH(ENCODE(CAST(cal.as_of_dts AS VARCHAR)))"
            f" AS VARCHAR)\n"
            f"            || ':' || CAST(cal.as_of_dts AS VARCHAR)\n"
            f"        )) AS {pit_hk}",
            f"        h.{hub_hk}",
            f"        cal.as_of_dts AS {as_of_col}",
        ]
        asof_joins: list[str] = []

        for idx_s, sat_name in enumerate(pit.tracked_satellites):
            sat = sat_by_name[sat_name]
            sat_tbl = _validate_ident(sat.name)
            sat_schema = _satellite_schema(sat)
            sat_parent_hk = _validate_ident(sat.parent_hk_column.name)
            alias = f"s{idx_s}"
            hk_ptr = f"{sat_tbl}_hk"
            dts_ptr = f"{sat_tbl}_load_dts"
            ptr_col_names.extend([hk_ptr, dts_ptr])
            comma = "," if idx_s < len(pit.tracked_satellites) - 1 else ""
            ddl_cols.append(f"    {hk_ptr} CHAR(64) NOT NULL,")
            ddl_cols.append(f"    {dts_ptr} TIMESTAMP NOT NULL{comma}")
            subquery_selects.append(
                f"        COALESCE({alias}.{sat_parent_hk}, '{ZERO_KEY_UNKNOWN}') AS {hk_ptr}"
            )
            subquery_selects.append(
                f"        COALESCE({alias}.load_dts, TIMESTAMP '{GHOST_DTS}') AS {dts_ptr}"
            )
            asof_joins.append(
                f"    ASOF LEFT JOIN {sat_schema}.{sat_tbl} {alias}\n"
                f"        ON {alias}.{sat_parent_hk} = h.{hub_hk}\n"
                f"       AND {alias}.load_dts <= cal.as_of_dts"
            )

        all_cols = [pit_hk, hub_hk, as_of_col, *ptr_col_names]
        cols_csv = ", ".join(all_cols)
        src_cols_csv = ", ".join(f"src.{c}" for c in all_cols)
        subquery_sel_str = ",\n".join(subquery_selects)
        asof_join_str = "\n".join(asof_joins)

        parts.append(
            f"CREATE TABLE IF NOT EXISTS business_vault.{pit_tbl} (\n"
            + "\n".join(ddl_cols)
            + "\n);"
        )
        parts.append(
            f"INSERT INTO business_vault.{pit_tbl} ({cols_csv})\n"
            f"SELECT {src_cols_csv}\n"
            f"FROM (\n"
            f"    SELECT\n"
            f"{subquery_sel_str}\n"
            f"    FROM raw_vault.{hub_tbl} h\n"
            f"    CROSS JOIN business_vault.snap_logarithmic_calendar cal\n"
            f"{asof_join_str}\n"
            f"    WHERE h.{hub_hk} NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')\n"
            f") src\n"
            f"WHERE NOT EXISTS (\n"
            f"    SELECT 1 FROM business_vault.{pit_tbl} p\n"
            f"    WHERE p.{pit_hk} = src.{pit_hk}\n"
            f");"
        )
        parts.append(
            f"CREATE OR REPLACE VIEW information_mart.{pit_tbl} AS\n"
            f"SELECT * FROM business_vault.{pit_tbl};"
        )
        parts.append(_build_generic_pit_view_sql(pit, hub_by_name, sat_by_name))

    return "\n\n".join(parts)


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


def compile_zone_0_migration_sql(
    ir: CanonicalIR | dict[str, Any],
    *,
    baseline_ir: CanonicalIR | dict[str, Any] | None = None,
) -> str:
    """Compile idempotent `ALTER TABLE <schema>.<sat> ADD COLUMN IF NOT EXISTS` statements.

    When `baseline_ir` is provided, emits `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
    statements only for columns added in-place to existing Satellites (`EVO-01` / `DV-EVO-006`).
    When `baseline_ir` is omitted, emits idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
    statements for all Satellite payload columns so any pre-existing physical Satellite table
    is safely migrated before Zone 1 and Zone 2 execute.
    """
    ir_obj = CanonicalIR.model_validate(ir) if isinstance(ir, dict) else ir
    base_obj = (
        CanonicalIR.model_validate(baseline_ir) if isinstance(baseline_ir, dict) else baseline_ir
    )
    prev_sats = {s.name: s for s in base_obj.satellites} if base_obj is not None else None

    stmts: list[str] = []
    for sat in ir_obj.satellites:
        schema = _satellite_schema(sat)
        tbl = _validate_ident(sat.name)
        child_keys = set(_satellite_child_key_names(sat))
        data_cols = [c for c in _satellite_all_data_columns(sat) if c.name not in child_keys]
        if prev_sats is not None:
            if sat.name not in prev_sats:
                continue
            prev_col_names = {
                c.name.lower() for c in _satellite_all_data_columns(prev_sats[sat.name])
            }
            target_cols = [c for c in data_cols if c.name.lower() not in prev_col_names]
        else:
            target_cols = data_cols

        for col in target_cols:
            c_name = _validate_ident(col.name)
            c_type = _normalize_duckdb_type(col.data_type)
            stmts.append(f"ALTER TABLE {schema}.{tbl} ADD COLUMN IF NOT EXISTS {c_name} {c_type};")

    if not stmts:
        return ""
    return "\n".join(
        [
            "-- =============================================================================",
            "-- ZONE 0 MIGRATION: IDEMPOTENT ADDITIVE SATELLITE SCHEMA EVOLUTION (EVO-01)",
            "-- =============================================================================",
            *stmts,
        ]
    )


def compile_canonical_ir_to_4zone_sql(
    ir: CanonicalIR | dict[str, Any],
    *,
    include_batch4_restatement_in_seed: bool = False,
    bronze_tables: Mapping[str, list[dict[str, Any]] | Any] | None = None,
    baseline_ir: CanonicalIR | dict[str, Any] | None = None,
) -> Compiled4ZoneBundle:
    """Compile a `CanonicalIR` model into a `Compiled4ZoneBundle`.

    For the Private Equity reference domain (`apex_private_equity_vault` or models containing
    `hub_limited_partner` and `hub_portfolio_company`), emits the full normative 4-Zone SQL
    bundle with Bronze seed tables, Hashed Staging Views, Raw Vault, Business Vault, and
    Bi-Temporal Queries D.10 / D.10b. For arbitrary custom `CanonicalIR` models, synthesizes
    the complete schema/DDL/Ghost/Zero-Key/Staging/Load/PIT/Mart bundle directly from the IR.
    """
    ir_obj = CanonicalIR.model_validate(ir) if isinstance(ir, dict) else ir
    load_order = topological_load_dag(ir_obj)

    if is_pe_domain(ir_obj):
        pe_migration_sql = (
            compile_zone_0_migration_sql(ir_obj, baseline_ir=baseline_ir)
            if baseline_ir is not None
            else ""
        )
        return Compiled4ZoneBundle(
            model_name=ir_obj.model_name,
            ir_version=ir_obj.ir_version,
            topological_load_order=load_order,
            zone_0_sql=_build_pe_zone_0_sql(
                include_batch4_restatement_in_seed=include_batch4_restatement_in_seed
            ),
            zone_0_migration_sql=pe_migration_sql,
            zone_1_sql=_build_pe_zone_1_sql(),
            zone_2_sql=_build_pe_zone_2_sql(),
            zone_3_sql=_build_pe_zone_3_sql(),
            zone_4_query_d10_sql=build_query_d10_sql(),
            zone_4_query_d10b_sql=build_query_d10b_sql(),
        )

    zone_0_sql = _compile_generic_zone_0_sql(
        ir_obj,
        include_batch4_restatement_in_seed=include_batch4_restatement_in_seed,
        bronze_tables=bronze_tables,
    )
    zone_0_migration_sql = compile_zone_0_migration_sql(ir_obj, baseline_ir=baseline_ir)
    zone_1_sql = _compile_generic_zone_1_staging_views(ir_obj)
    zone_2_sql = _compile_generic_zone_2_raw_vault_dml(ir_obj)
    zone_3_sql = _compile_generic_zone_3_business_vault_sql(ir_obj)
    zone_4_d10, zone_4_d10b = _compile_generic_zone_4_information_mart_views(ir_obj)

    return Compiled4ZoneBundle(
        model_name=ir_obj.model_name,
        ir_version=ir_obj.ir_version,
        topological_load_order=load_order,
        zone_0_sql=zone_0_sql,
        zone_0_migration_sql=zone_0_migration_sql,
        zone_1_sql=zone_1_sql,
        zone_2_sql=zone_2_sql,
        zone_3_sql=zone_3_sql,
        zone_4_query_d10_sql=zone_4_d10,
        zone_4_query_d10b_sql=zone_4_d10b,
    )
