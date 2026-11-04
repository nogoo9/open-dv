-- =============================================================================
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
     TIMESTAMP '2026-10-01 07:30:00');

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
     1.0000, 1.0000, 1.0000, 'GHOST', 0, 0, 0, 'SYSTEM_GHOST');

-- =============================================================================
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
FROM unstructured_vdr.src_document_manifest;

CREATE OR REPLACE VIEW staging.stg_unstructured_vdr_llm_extractions AS
SELECT
    e.*,
    CASE
        WHEN UPPER(TRIM(COALESCE(e.human_review_status, ''))) IN (
            'APPROVED',
            'STEWARD_VERIFIED',
            'AUTO_ACCEPTED_TIER_1',
            'AUTO_ACCEPTED_TIER_2'
        ) THEN 'APPROVED'
        ELSE COALESCE(NULLIF(TRIM(e.human_review_status), ''), 'STEWARD_HOLD')
    END AS steward_gate_status
FROM unstructured_vdr.src_llm_extractions e;

-- =============================================================================
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
    src.extracted_dts AS load_dts,
    'LLM_EXT_PIPELINE' AS rec_src
FROM staging.stg_unstructured_vdr_llm_extractions src
INNER JOIN raw_vault.hub_unstructured_document d ON d.doc_sha256 = src.doc_sha256
INNER JOIN raw_vault.hub_limited_partner lp
    ON lp.bkcc = src.resolved_lp_bkcc AND lp.lp_id = src.resolved_lp_id
INNER JOIN raw_vault.hub_fund f                  ON f.fund_code = src.resolved_fund_code
WHERE src.target_domain = 'SIDE_LETTER_TERMS'
  AND src.steward_gate_status = 'APPROVED'
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
    src.extracted_dts AS load_dts,
    'LLM_EXT_PIPELINE' AS rec_src
FROM staging.stg_unstructured_vdr_llm_extractions src
INNER JOIN raw_vault.hub_unstructured_document d ON d.doc_sha256 = src.doc_sha256
INNER JOIN raw_vault.hub_portfolio_company p     ON p.lei_code = src.resolved_lei_code
WHERE src.target_domain = 'PORTCO_VALUATION_RESTATEMENT'
  AND src.steward_gate_status = 'APPROVED'
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
WHERE cur.hk_lnk_fund_portco_investment IS NULL OR cur.hash_diff <> src.hash_diff_deal;

-- =============================================================================
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
FROM staging.stg_unstructured_vdr_llm_extractions e
INNER JOIN raw_vault.hub_unstructured_document d ON d.doc_sha256 = e.doc_sha256
INNER JOIN raw_vault.hub_limited_partner lp
    ON lp.bkcc = e.resolved_lp_bkcc AND lp.lp_id = e.resolved_lp_id
INNER JOIN raw_vault.hub_fund f                  ON f.fund_code = e.resolved_fund_code
INNER JOIN raw_vault.lnk_doc_lp_side_letter l
    ON l.hk_doc = d.hk_doc AND l.hk_lp = lp.hk_lp AND l.hk_fund = f.hk_fund
WHERE e.target_domain = 'SIDE_LETTER_TERMS'
  AND e.steward_gate_status = 'APPROVED'
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
FROM staging.stg_unstructured_vdr_llm_extractions e
INNER JOIN raw_vault.hub_unstructured_document d ON d.doc_sha256 = e.doc_sha256
INNER JOIN raw_vault.hub_portfolio_company p     ON p.lei_code = e.resolved_lei_code
INNER JOIN raw_vault.lnk_doc_portco_memo l
    ON l.hk_doc = d.hk_doc AND l.hk_portco = p.hk_portco
WHERE e.target_domain = 'PORTCO_VALUATION_RESTATEMENT'
  AND e.steward_gate_status = 'APPROVED'
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
WHERE h.hk_portco NOT IN (REPEAT('0', 64), REPEAT('f', 64));

-- =============================================================================
-- ZONE 4: BI-TEMPORAL MULTIMODAL POINT-IN-TIME AUDIT QUERIES
-- =============================================================================
-- Query D.10: Golden LP Commitment, Legacy Capital Account NAV & Side-Letter Provenance
WITH audit_params AS (
    SELECT
        TIMESTAMP '2026-11-15 23:59:59' AS assertion_cutoff_ts,
        DATE '2026-09-30'          AS state_cutoff_date
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
ORDER BY h_lp.lp_id;

-- Query D.10b: Latest Portfolio Company Valuation Visible at the Assertion Cutoff
WITH audit_params AS (
    SELECT
        TIMESTAMP '2026-11-15 23:59:59' AS assertion_cutoff_ts,
        DATE '2026-09-30'         AS state_cutoff_date
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
   AND ai.restated_ev_multiple = s_val.entry_ev_multiple;
