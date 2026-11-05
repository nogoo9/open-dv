-- =============================================================================

-- ZONE 0: TARGET SCHEMAS, BRONZE SOURCE SEEDS, VAULT DDL & GHOST/ZERO RECORDS

-- =============================================================================

CREATE SCHEMA IF NOT EXISTS bronze;

CREATE SCHEMA IF NOT EXISTS staging;

CREATE SCHEMA IF NOT EXISTS raw_vault;

CREATE SCHEMA IF NOT EXISTS business_vault;

CREATE SCHEMA IF NOT EXISTS information_mart;

CREATE OR REPLACE TABLE bronze.src_hub_fund (
    bkcc VARCHAR,
    fund_code VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_hub_fund VALUES
    ('GLOBAL', 'APEX-BUYOUT-IV', TIMESTAMP '2026-10-01 06:00:00', 'INVESTRAN_FUND_ADMIN'),
    ('GLOBAL', 'FUND-002', TIMESTAMP '2026-10-01 06:00:00', 'INVESTRAN_FUND_ADMIN');

CREATE OR REPLACE TABLE bronze.src_hub_limited_partner (
    bkcc VARCHAR,
    lp_natural_key VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_hub_limited_partner VALUES
    ('GLOBAL', 'LP-101', TIMESTAMP '2026-10-01 06:00:00', 'INVESTRAN_FUND_ADMIN'),
    ('GLOBAL', 'LP-102', TIMESTAMP '2026-10-01 06:00:00', 'INVESTRAN_FUND_ADMIN');

CREATE OR REPLACE TABLE bronze.src_hub_portfolio_company (
    bkcc VARCHAR,
    portco_natural_key VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_hub_portfolio_company VALUES
    ('GLOBAL', '529900T8BM49AURSDO55', TIMESTAMP '2026-10-01 06:00:00', 'DEALCLOUD_PE_CRM');

CREATE OR REPLACE TABLE bronze.src_hub_document (
    bkcc VARCHAR,
    doc_uri VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_hub_document VALUES
    ('GLOBAL', 'DOCUMENT-001', TIMESTAMP '2026-10-01 06:00:00', 'OBJECT_STORE_S3'),
    ('GLOBAL', 'DOCUMENT-002', TIMESTAMP '2026-10-01 06:00:00', 'OBJECT_STORE_S3');

CREATE OR REPLACE VIEW bronze.investran_src_pe_funds AS SELECT * FROM bronze.src_hub_fund;

CREATE OR REPLACE VIEW bronze.investran_src_lp_investors AS SELECT * FROM bronze.src_hub_limited_partner;

CREATE OR REPLACE VIEW bronze.legacy_gp_src_legacy_lp_roster AS SELECT * FROM bronze.src_hub_limited_partner;

CREATE OR REPLACE VIEW bronze.dealcloud_src_portfolio_companies AS SELECT * FROM bronze.src_hub_portfolio_company;

CREATE OR REPLACE VIEW bronze.dms_documents AS SELECT * FROM bronze.src_hub_document;

CREATE OR REPLACE TABLE bronze.src_lnk_lp_commitment (
    hk_fund__bkcc VARCHAR,
    hk_fund__fund_code VARCHAR,
    hk_limited_partner__bkcc VARCHAR,
    hk_limited_partner__lp_natural_key VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_lnk_lp_commitment VALUES
    ('GLOBAL', 'APEX-BUYOUT-IV', 'GLOBAL', 'LP-101', TIMESTAMP '2026-10-01 06:00:00', 'INVESTRAN_FUND_ADMIN'),
    ('GLOBAL', 'FUND-002', 'GLOBAL', NULL, TIMESTAMP '2026-10-01 06:00:00', 'INVESTRAN_FUND_ADMIN'),
    ('GLOBAL', 'APEX-BUYOUT-IV', 'GLOBAL', '-1', TIMESTAMP '2026-10-01 06:00:00', 'INVESTRAN_FUND_ADMIN'),
    ('GLOBAL', 'FUND-002', 'GLOBAL', '-2', TIMESTAMP '2026-10-01 06:00:00', 'INVESTRAN_FUND_ADMIN');

CREATE OR REPLACE TABLE bronze.src_lnk_lp_commitment_co_invest (
    hk_fund__bkcc VARCHAR,
    hk_fund__fund_code VARCHAR,
    hk_limited_partner__bkcc VARCHAR,
    hk_limited_partner__lp_natural_key VARCHAR,
    hk_portfolio_company__bkcc VARCHAR,
    hk_portfolio_company__portco_natural_key VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_lnk_lp_commitment_co_invest VALUES
    ('GLOBAL', 'APEX-BUYOUT-IV', 'GLOBAL', 'LP-101', 'GLOBAL', '529900T8BM49AURSDO55', TIMESTAMP '2026-10-01 06:00:00', 'INVESTRAN_FUND_ADMIN'),
    ('GLOBAL', 'FUND-002', 'GLOBAL', NULL, 'GLOBAL', NULL, TIMESTAMP '2026-10-01 06:00:00', 'INVESTRAN_FUND_ADMIN'),
    ('GLOBAL', 'APEX-BUYOUT-IV', 'GLOBAL', '-1', 'GLOBAL', '-1', TIMESTAMP '2026-10-01 06:00:00', 'INVESTRAN_FUND_ADMIN'),
    ('GLOBAL', 'FUND-002', 'GLOBAL', '-2', 'GLOBAL', '-2', TIMESTAMP '2026-10-01 06:00:00', 'INVESTRAN_FUND_ADMIN');

CREATE OR REPLACE TABLE bronze.src_sal_limited_partner (
    hk_limited_partner_master__bkcc VARCHAR,
    hk_limited_partner_master__lp_natural_key VARCHAR,
    hk_limited_partner_duplicate__bkcc VARCHAR,
    hk_limited_partner_duplicate__lp_natural_key VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_sal_limited_partner VALUES
    ('GLOBAL', 'LP-101', 'GLOBAL', 'LP-102', TIMESTAMP '2026-10-01 06:00:00', 'MDM_STEWARD_RESOLUTION'),
    ('GLOBAL', 'LP-102', 'GLOBAL', NULL, TIMESTAMP '2026-10-01 06:00:00', 'MDM_STEWARD_RESOLUTION'),
    ('GLOBAL', 'LP-101', 'GLOBAL', '-1', TIMESTAMP '2026-10-01 06:00:00', 'MDM_STEWARD_RESOLUTION'),
    ('GLOBAL', 'LP-102', 'GLOBAL', '-2', TIMESTAMP '2026-10-01 06:00:00', 'MDM_STEWARD_RESOLUTION');

CREATE OR REPLACE TABLE bronze.src_lnk_document_lp_commitment (
    hk_document__bkcc VARCHAR,
    hk_document__doc_uri VARCHAR,
    hk_fund__bkcc VARCHAR,
    hk_fund__fund_code VARCHAR,
    hk_limited_partner__bkcc VARCHAR,
    hk_limited_partner__lp_natural_key VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    steward_gate_status VARCHAR
);
INSERT INTO bronze.src_lnk_document_lp_commitment VALUES
    ('GLOBAL', 'DOCUMENT-001', 'GLOBAL', 'APEX-BUYOUT-IV', 'GLOBAL', 'LP-101', TIMESTAMP '2026-10-01 06:00:00', 'OBJECT_STORE_S3', 'APPROVED'),
    ('GLOBAL', 'DOCUMENT-002', 'GLOBAL', NULL, 'GLOBAL', NULL, TIMESTAMP '2026-10-01 06:00:00', 'OBJECT_STORE_S3', 'STEWARD_HOLD'),
    ('GLOBAL', 'DOCUMENT-001', 'GLOBAL', '-1', 'GLOBAL', '-1', TIMESTAMP '2026-10-01 06:00:00', 'OBJECT_STORE_S3', 'STEWARD_HOLD'),
    ('GLOBAL', 'DOCUMENT-002', 'GLOBAL', '-2', 'GLOBAL', '-2', TIMESTAMP '2026-10-01 06:00:00', 'OBJECT_STORE_S3', 'STEWARD_HOLD');

CREATE OR REPLACE TABLE bronze.src_lnk_document_portfolio_company (
    hk_document__bkcc VARCHAR,
    hk_document__doc_uri VARCHAR,
    hk_portfolio_company__bkcc VARCHAR,
    hk_portfolio_company__portco_natural_key VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    steward_gate_status VARCHAR
);
INSERT INTO bronze.src_lnk_document_portfolio_company VALUES
    ('GLOBAL', 'DOCUMENT-001', 'GLOBAL', '529900T8BM49AURSDO55', TIMESTAMP '2026-10-01 06:00:00', 'OBJECT_STORE_S3', 'APPROVED'),
    ('GLOBAL', 'DOCUMENT-002', 'GLOBAL', NULL, TIMESTAMP '2026-10-01 06:00:00', 'OBJECT_STORE_S3', 'STEWARD_HOLD'),
    ('GLOBAL', 'DOCUMENT-001', 'GLOBAL', '-1', TIMESTAMP '2026-10-01 06:00:00', 'OBJECT_STORE_S3', 'STEWARD_HOLD'),
    ('GLOBAL', 'DOCUMENT-002', 'GLOBAL', '-2', TIMESTAMP '2026-10-01 06:00:00', 'OBJECT_STORE_S3', 'STEWARD_HOLD');

CREATE OR REPLACE TABLE bronze.src_sat_fund_investran (
    parent_bk__bkcc VARCHAR,
    parent_bk__fund_code VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    isin VARCHAR,
    fund_name VARCHAR,
    vintage_year INTEGER,
    target_size_usd NUMERIC(18,2),
    mgmt_fee_bps NUMERIC(8,4),
    deprecated_fax_no VARCHAR
);
INSERT INTO bronze.src_sat_fund_investran VALUES
    ('GLOBAL', 'APEX-BUYOUT-IV', TIMESTAMP '2026-10-01 06:00:00', 'INVESTRAN_FUND_ADMIN', 'isin_val_1', 'fund_name_val_1', 100, 1000.50, 1000.50, 'deprecated_fax_no_val_1'),
    ('GLOBAL', 'FUND-002', TIMESTAMP '2026-10-01 06:00:00', 'INVESTRAN_FUND_ADMIN', 'isin_val_2', 'fund_name_val_2', 200, 2000.50, 2000.50, 'deprecated_fax_no_val_2');

CREATE OR REPLACE TABLE bronze.src_sat_limited_partner_pii (
    parent_bk__bkcc VARCHAR,
    parent_bk__lp_natural_key VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    wire_iban VARCHAR,
    tax_id VARCHAR
);
INSERT INTO bronze.src_sat_limited_partner_pii VALUES
    ('GLOBAL', 'LP-101', TIMESTAMP '2026-10-01 06:00:00', 'INVESTRAN_FUND_ADMIN', 'wire_iban_val_1', 'tax_id_val_1'),
    ('GLOBAL', 'LP-102', TIMESTAMP '2026-10-01 06:00:00', 'INVESTRAN_FUND_ADMIN', 'wire_iban_val_2', 'tax_id_val_2');

CREATE OR REPLACE TABLE bronze.src_sat_lp_commitment_investran (
    parent_fk__hk_fund VARCHAR,
    parent_fk__hk_limited_partner VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    commitment_amount_usd NUMERIC(18,2),
    called_usd NUMERIC(18,2),
    distribution_recallable_flag VARCHAR,
    eur_usd_rate NUMERIC(12,6),
    gbp_usd_rate NUMERIC(12,6),
    daily_interim_nav_usd NUMERIC(18,2)
);
INSERT INTO bronze.src_sat_lp_commitment_investran VALUES
    ('69f7a0694d918dca897a19a3d0c06b037ce62fd19467bbe281ca64263998c85b', '5d15eb10c8d7621d3167849c41c5f9cd9244fd79fb720cb17104d49e4b820ae4', TIMESTAMP '2026-10-01 06:00:00', 'INVESTRAN_FUND_ADMIN', 150000000.00, 1000.50, 'distribution_recallable_flag_val_1', 1000.50, 1000.50, 1000.50),
    ('d99f83b63aff1bc84833a6122963f71597a5e3137139ef5e115dd0208bb43ad2', 'a2c5399f2645fab22420647d07277f4c3921842419b15c2619d60487ace24b0b', TIMESTAMP '2026-10-01 06:00:00', 'INVESTRAN_FUND_ADMIN', 95000000.00, 2000.50, 'distribution_recallable_flag_val_2', 2000.50, 2000.50, 2000.50);

CREATE OR REPLACE TABLE bronze.src_sat_portfolio_company_dealcloud (
    parent_bk__bkcc VARCHAR,
    parent_bk__portco_natural_key VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    deal_code VARCHAR,
    company_name VARCHAR,
    gp_deal_partner VARCHAR
);
INSERT INTO bronze.src_sat_portfolio_company_dealcloud VALUES
    ('GLOBAL', '529900T8BM49AURSDO55', TIMESTAMP '2026-10-01 06:00:00', 'DEALCLOUD_PE_CRM', 'deal_code_val_1', 'Nordic Cloud Infrastructure AB', 'gp_deal_partner_val_1');

CREATE OR REPLACE TABLE bronze.src_sat_portco_valuation_mnpi (
    parent_bk__bkcc VARCHAR,
    parent_bk__portco_natural_key VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    entry_ebitda_usd NUMERIC(18,2),
    entry_ev_multiple NUMERIC(6,2)
);
INSERT INTO bronze.src_sat_portco_valuation_mnpi VALUES
    ('GLOBAL', '529900T8BM49AURSDO55', TIMESTAMP '2026-10-01 06:00:00', 'DEALCLOUD_PE_CRM', 16896550.00, 12.80);

CREATE OR REPLACE TABLE bronze.src_sat_fund_investran_esg (
    parent_bk__bkcc VARCHAR,
    parent_bk__fund_code VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    ilpa_esg_rating VARCHAR
);
INSERT INTO bronze.src_sat_fund_investran_esg VALUES
    ('GLOBAL', 'APEX-BUYOUT-IV', TIMESTAMP '2026-10-01 06:00:00', 'INVESTRAN_FUND_ADMIN', 'ilpa_esg_rating_val_1'),
    ('GLOBAL', 'FUND-002', TIMESTAMP '2026-10-01 06:00:00', 'INVESTRAN_FUND_ADMIN', 'ilpa_esg_rating_val_2');

CREATE OR REPLACE TABLE bronze.src_sat_limited_partner_investran (
    parent_bk__bkcc VARCHAR,
    parent_bk__lp_natural_key VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    lei VARCHAR,
    lp_legal_name VARCHAR,
    domicile_country VARCHAR,
    tier_status VARCHAR
);
INSERT INTO bronze.src_sat_limited_partner_investran VALUES
    ('GLOBAL', 'LP-101', TIMESTAMP '2026-10-01 06:00:00', 'INVESTRAN_FUND_ADMIN', '529900T8BM49AURSDO55', 'lp_legal_name_val_1', 'domicile_country_val_1', 'tier_status_val_1'),
    ('GLOBAL', 'LP-102', TIMESTAMP '2026-10-01 06:00:00', 'INVESTRAN_FUND_ADMIN', 'lei_val_2', 'lp_legal_name_val_2', 'domicile_country_val_2', 'tier_status_val_2');

CREATE OR REPLACE TABLE bronze.src_sat_limited_partner_legacy (
    parent_bk__bkcc VARCHAR,
    parent_bk__lp_natural_key VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    legacy_investor_name VARCHAR,
    contact_person VARCHAR,
    phone_number VARCHAR
);
INSERT INTO bronze.src_sat_limited_partner_legacy VALUES
    ('GLOBAL', 'LP-101', TIMESTAMP '2026-10-01 06:00:00', 'LEGACY_GP_ADMIN', 'legacy_investor_name_val_1', 'contact_person_val_1', 'phone_number_val_1'),
    ('GLOBAL', 'LP-102', TIMESTAMP '2026-10-01 06:00:00', 'LEGACY_GP_ADMIN', 'legacy_investor_name_val_2', 'contact_person_val_2', 'phone_number_val_2');

CREATE OR REPLACE TABLE bronze.src_sat_document_manifest (
    parent_bk__bkcc VARCHAR,
    parent_bk__doc_uri VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    content_sha256 CHAR(64),
    mime_type VARCHAR,
    byte_size BIGINT
);
INSERT INTO bronze.src_sat_document_manifest VALUES
    ('GLOBAL', 'DOCUMENT-001', TIMESTAMP '2026-10-01 06:00:00', 'OBJECT_STORE_S3', '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08', 'mime_type_val_1', 100),
    ('GLOBAL', 'DOCUMENT-002', TIMESTAMP '2026-10-01 06:00:00', 'OBJECT_STORE_S3', '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08', 'mime_type_val_2', 200);

CREATE OR REPLACE TABLE bronze.src_sat_lp_side_letter_llm_ext (
    parent_fk__hk_document VARCHAR,
    parent_fk__hk_fund VARCHAR,
    parent_fk__hk_limited_partner VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    fee_cap_bps NUMERIC(8,4),
    headline_fee_bps NUMERIC(8,4),
    mfn_election_flag VARCHAR,
    co_invest_rofr VARCHAR,
    effective_date DATE,
    extractor_model_id VARCHAR,
    confidence_score NUMERIC(4,3),
    chunk_id VARCHAR,
    evidence_quote VARCHAR,
    steward_gate_status VARCHAR DEFAULT 'APPROVED'
);
INSERT INTO bronze.src_sat_lp_side_letter_llm_ext VALUES
    ('f3265a555edb0c65ba1ed27f627d904657e0a51d0821f403d7194d807e5a96c3', '69f7a0694d918dca897a19a3d0c06b037ce62fd19467bbe281ca64263998c85b', '5d15eb10c8d7621d3167849c41c5f9cd9244fd79fb720cb17104d49e4b820ae4', TIMESTAMP '2026-10-01 06:00:00', 'OBJECT_STORE_S3', 1000.50, 1000.50, TRUE, 'co_invest_rofr_val_1', DATE '2026-09-30', 'gemini-2.5-pro', 0.9500, 'chunk_id_val_1', 'Verified contractual clause quote #1 from source document.', 'APPROVED'),
    ('89e2c58d513cd51f9bb12e0d6bce6a0732db3a1cf68dcd3c191e4871561c113d', 'd99f83b63aff1bc84833a6122963f71597a5e3137139ef5e115dd0208bb43ad2', 'a2c5399f2645fab22420647d07277f4c3921842419b15c2619d60487ace24b0b', TIMESTAMP '2026-10-01 06:00:00', 'OBJECT_STORE_S3', 2000.50, 2000.50, TRUE, 'co_invest_rofr_val_2', DATE '2026-09-30', 'gemini-2.5-pro', 0.9200, 'chunk_id_val_2', 'Verified contractual clause quote #2 from source document.', 'STEWARD_HOLD');

CREATE OR REPLACE TABLE bronze.src_sat_lnk_doc_portco_ai_provenance (
    parent_fk__hk_document VARCHAR,
    parent_fk__hk_portfolio_company VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    asc_820_level VARCHAR,
    ev_ebitda_multiple NUMERIC(6,2),
    implied_ev_usd NUMERIC(18,2),
    fair_value_usd NUMERIC(18,2),
    effective_date DATE,
    extractor_model_id VARCHAR,
    confidence_score NUMERIC(4,3),
    chunk_id VARCHAR,
    evidence_quote VARCHAR,
    steward_gate_status VARCHAR DEFAULT 'APPROVED'
);
INSERT INTO bronze.src_sat_lnk_doc_portco_ai_provenance VALUES
    ('f3265a555edb0c65ba1ed27f627d904657e0a51d0821f403d7194d807e5a96c3', '0c1850cac2e686a1f9233fa223f3986dc0949963de1a32dffdb25e1139b0454e', TIMESTAMP '2026-10-01 06:00:00', 'OBJECT_STORE_S3', 'asc_820_level_val_1', 12.80, 1000.50, 1000.50, DATE '2026-09-30', 'gemini-2.5-pro', 0.9500, 'chunk_id_val_1', 'Verified contractual clause quote #1 from source document.', 'APPROVED'),
    ('89e2c58d513cd51f9bb12e0d6bce6a0732db3a1cf68dcd3c191e4871561c113d', '0c1850cac2e686a1f9233fa223f3986dc0949963de1a32dffdb25e1139b0454e', TIMESTAMP '2026-10-01 06:00:00', 'OBJECT_STORE_S3', 'asc_820_level_val_2', 14.50, 2000.50, 2000.50, DATE '2026-09-30', 'gemini-2.5-pro', 0.9200, 'chunk_id_val_2', 'Verified contractual clause quote #2 from source document.', 'STEWARD_HOLD');

CREATE TABLE IF NOT EXISTS raw_vault.hub_fund (
    hk_fund CHAR(64) NOT NULL PRIMARY KEY,
    bkcc VARCHAR NOT NULL,
    fund_code VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.hub_fund VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', 'SYSTEM', '-1', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'SYSTEM', '-2', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.hub_limited_partner (
    hk_limited_partner CHAR(64) NOT NULL PRIMARY KEY,
    bkcc VARCHAR NOT NULL,
    lp_natural_key VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.hub_limited_partner VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', 'SYSTEM', '-1', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'SYSTEM', '-2', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.hub_portfolio_company (
    hk_portfolio_company CHAR(64) NOT NULL PRIMARY KEY,
    bkcc VARCHAR NOT NULL,
    portco_natural_key VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.hub_portfolio_company VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', 'SYSTEM', '-1', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'SYSTEM', '-2', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.hub_document (
    hk_document CHAR(64) NOT NULL PRIMARY KEY,
    bkcc VARCHAR NOT NULL,
    doc_uri VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.hub_document VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', 'SYSTEM', '-1', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'SYSTEM', '-2', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.ref_fund_strategy (
    strategy VARCHAR NOT NULL PRIMARY KEY,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.ref_fund_strategy VALUES
    ('REF-001', TIMESTAMP '2026-10-01 06:00:00', 'SYSTEM_SEED'),
    ('REF-002', TIMESTAMP '2026-10-01 06:00:00', 'SYSTEM_SEED');

CREATE TABLE IF NOT EXISTS raw_vault.ref_industry_sector (
    sector VARCHAR NOT NULL PRIMARY KEY,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.ref_industry_sector VALUES
    ('REF-001', TIMESTAMP '2026-10-01 06:00:00', 'SYSTEM_SEED'),
    ('REF-002', TIMESTAMP '2026-10-01 06:00:00', 'SYSTEM_SEED');

CREATE TABLE IF NOT EXISTS raw_vault.lnk_lp_commitment (
    hk_lnk_lp_commitment CHAR(64) NOT NULL PRIMARY KEY,
    hk_fund CHAR(64) NOT NULL,
    hk_limited_partner CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.lnk_lp_commitment VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.lnk_lp_commitment_co_invest (
    hk_lnk_lp_commitment_co_invest CHAR(64) NOT NULL PRIMARY KEY,
    hk_fund CHAR(64) NOT NULL,
    hk_limited_partner CHAR(64) NOT NULL,
    hk_portfolio_company CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.lnk_lp_commitment_co_invest VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS business_vault.sal_limited_partner (
    hk_sal_limited_partner CHAR(64) NOT NULL PRIMARY KEY,
    hk_limited_partner_master CHAR(64) NOT NULL,
    hk_limited_partner_duplicate CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO business_vault.sal_limited_partner VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.lnk_document_lp_commitment (
    hk_lnk_document_lp_commitment CHAR(64) NOT NULL PRIMARY KEY,
    hk_document CHAR(64) NOT NULL,
    hk_fund CHAR(64) NOT NULL,
    hk_limited_partner CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.lnk_document_lp_commitment VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.lnk_document_portfolio_company (
    hk_lnk_document_portfolio_company CHAR(64) NOT NULL PRIMARY KEY,
    hk_document CHAR(64) NOT NULL,
    hk_portfolio_company CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.lnk_document_portfolio_company VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.sat_fund_investran (
    hk_fund CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    isin VARCHAR,
    fund_name VARCHAR,
    vintage_year INTEGER,
    target_size_usd NUMERIC(18,2),
    mgmt_fee_bps NUMERIC(8,4),
    deprecated_fax_no VARCHAR,
    PRIMARY KEY (hk_fund, load_dts)
);
INSERT OR IGNORE INTO raw_vault.sat_fund_investran (hk_fund, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

CREATE TABLE IF NOT EXISTS raw_vault.sat_limited_partner_pii (
    hk_limited_partner CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    wire_iban VARCHAR,
    tax_id VARCHAR,
    PRIMARY KEY (hk_limited_partner, load_dts)
);
INSERT OR IGNORE INTO raw_vault.sat_limited_partner_pii (hk_limited_partner, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

CREATE TABLE IF NOT EXISTS raw_vault.sat_lp_commitment_investran (
    hk_lnk_lp_commitment CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    commitment_amount_usd NUMERIC(18,2),
    called_usd NUMERIC(18,2),
    distribution_recallable_flag VARCHAR,
    eur_usd_rate NUMERIC(12,6),
    gbp_usd_rate NUMERIC(12,6),
    daily_interim_nav_usd NUMERIC(18,2),
    PRIMARY KEY (hk_lnk_lp_commitment, load_dts)
);
INSERT OR IGNORE INTO raw_vault.sat_lp_commitment_investran (hk_lnk_lp_commitment, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

CREATE TABLE IF NOT EXISTS raw_vault.sat_portfolio_company_dealcloud (
    hk_portfolio_company CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    deal_code VARCHAR,
    company_name VARCHAR,
    gp_deal_partner VARCHAR,
    PRIMARY KEY (hk_portfolio_company, load_dts)
);
INSERT OR IGNORE INTO raw_vault.sat_portfolio_company_dealcloud (hk_portfolio_company, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

CREATE TABLE IF NOT EXISTS raw_vault.sat_portco_valuation_mnpi (
    hk_portfolio_company CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    entry_ebitda_usd NUMERIC(18,2),
    entry_ev_multiple NUMERIC(6,2),
    PRIMARY KEY (hk_portfolio_company, load_dts)
);
INSERT OR IGNORE INTO raw_vault.sat_portco_valuation_mnpi (hk_portfolio_company, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

CREATE TABLE IF NOT EXISTS raw_vault.sat_fund_investran_esg (
    hk_fund CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    ilpa_esg_rating VARCHAR,
    PRIMARY KEY (hk_fund, load_dts)
);
INSERT OR IGNORE INTO raw_vault.sat_fund_investran_esg (hk_fund, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

CREATE TABLE IF NOT EXISTS raw_vault.sat_limited_partner_investran (
    hk_limited_partner CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    lei VARCHAR,
    lp_legal_name VARCHAR,
    domicile_country VARCHAR,
    tier_status VARCHAR,
    PRIMARY KEY (hk_limited_partner, load_dts)
);
INSERT OR IGNORE INTO raw_vault.sat_limited_partner_investran (hk_limited_partner, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

CREATE TABLE IF NOT EXISTS raw_vault.sat_limited_partner_legacy (
    hk_limited_partner CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    legacy_investor_name VARCHAR,
    contact_person VARCHAR,
    phone_number VARCHAR,
    PRIMARY KEY (hk_limited_partner, load_dts)
);
INSERT OR IGNORE INTO raw_vault.sat_limited_partner_legacy (hk_limited_partner, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

CREATE TABLE IF NOT EXISTS raw_vault.sat_document_manifest (
    hk_document CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    content_sha256 CHAR(64),
    mime_type VARCHAR,
    byte_size BIGINT,
    PRIMARY KEY (hk_document, load_dts)
);
INSERT OR IGNORE INTO raw_vault.sat_document_manifest (hk_document, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

CREATE TABLE IF NOT EXISTS business_vault.sat_lp_side_letter_llm_ext (
    hk_lnk_document_lp_commitment CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    fee_cap_bps NUMERIC(8,4),
    headline_fee_bps NUMERIC(8,4),
    mfn_election_flag VARCHAR,
    co_invest_rofr VARCHAR,
    effective_date DATE,
    extractor_model_id VARCHAR,
    confidence_score NUMERIC(4,3),
    chunk_id VARCHAR,
    evidence_quote VARCHAR,
    PRIMARY KEY (hk_lnk_document_lp_commitment, load_dts)
);
INSERT OR IGNORE INTO business_vault.sat_lp_side_letter_llm_ext (hk_lnk_document_lp_commitment, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

CREATE TABLE IF NOT EXISTS business_vault.sat_lnk_doc_portco_ai_provenance (
    hk_lnk_document_portfolio_company CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    asc_820_level VARCHAR,
    ev_ebitda_multiple NUMERIC(6,2),
    implied_ev_usd NUMERIC(18,2),
    fair_value_usd NUMERIC(18,2),
    effective_date DATE,
    extractor_model_id VARCHAR,
    confidence_score NUMERIC(4,3),
    chunk_id VARCHAR,
    evidence_quote VARCHAR,
    PRIMARY KEY (hk_lnk_document_portfolio_company, load_dts)
);
INSERT OR IGNORE INTO business_vault.sat_lnk_doc_portco_ai_provenance (hk_lnk_document_portfolio_company, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

-- =============================================================================
-- ZONE 0 MIGRATION: IDEMPOTENT ADDITIVE SATELLITE SCHEMA EVOLUTION (EVO-01)
-- =============================================================================
ALTER TABLE raw_vault.sat_fund_investran ADD COLUMN IF NOT EXISTS isin VARCHAR;
ALTER TABLE raw_vault.sat_fund_investran ADD COLUMN IF NOT EXISTS fund_name VARCHAR;
ALTER TABLE raw_vault.sat_fund_investran ADD COLUMN IF NOT EXISTS vintage_year INTEGER;
ALTER TABLE raw_vault.sat_fund_investran ADD COLUMN IF NOT EXISTS target_size_usd NUMERIC(18,2);
ALTER TABLE raw_vault.sat_fund_investran ADD COLUMN IF NOT EXISTS mgmt_fee_bps NUMERIC(8,4);
ALTER TABLE raw_vault.sat_fund_investran ADD COLUMN IF NOT EXISTS deprecated_fax_no VARCHAR;
ALTER TABLE raw_vault.sat_limited_partner_pii ADD COLUMN IF NOT EXISTS wire_iban VARCHAR;
ALTER TABLE raw_vault.sat_limited_partner_pii ADD COLUMN IF NOT EXISTS tax_id VARCHAR;
ALTER TABLE raw_vault.sat_lp_commitment_investran ADD COLUMN IF NOT EXISTS commitment_amount_usd NUMERIC(18,2);
ALTER TABLE raw_vault.sat_lp_commitment_investran ADD COLUMN IF NOT EXISTS called_usd NUMERIC(18,2);
ALTER TABLE raw_vault.sat_lp_commitment_investran ADD COLUMN IF NOT EXISTS distribution_recallable_flag VARCHAR;
ALTER TABLE raw_vault.sat_lp_commitment_investran ADD COLUMN IF NOT EXISTS eur_usd_rate NUMERIC(12,6);
ALTER TABLE raw_vault.sat_lp_commitment_investran ADD COLUMN IF NOT EXISTS gbp_usd_rate NUMERIC(12,6);
ALTER TABLE raw_vault.sat_lp_commitment_investran ADD COLUMN IF NOT EXISTS daily_interim_nav_usd NUMERIC(18,2);
ALTER TABLE raw_vault.sat_portfolio_company_dealcloud ADD COLUMN IF NOT EXISTS deal_code VARCHAR;
ALTER TABLE raw_vault.sat_portfolio_company_dealcloud ADD COLUMN IF NOT EXISTS company_name VARCHAR;
ALTER TABLE raw_vault.sat_portfolio_company_dealcloud ADD COLUMN IF NOT EXISTS gp_deal_partner VARCHAR;
ALTER TABLE raw_vault.sat_portco_valuation_mnpi ADD COLUMN IF NOT EXISTS entry_ebitda_usd NUMERIC(18,2);
ALTER TABLE raw_vault.sat_portco_valuation_mnpi ADD COLUMN IF NOT EXISTS entry_ev_multiple NUMERIC(6,2);
ALTER TABLE raw_vault.sat_fund_investran_esg ADD COLUMN IF NOT EXISTS ilpa_esg_rating VARCHAR;
ALTER TABLE raw_vault.sat_limited_partner_investran ADD COLUMN IF NOT EXISTS lei VARCHAR;
ALTER TABLE raw_vault.sat_limited_partner_investran ADD COLUMN IF NOT EXISTS lp_legal_name VARCHAR;
ALTER TABLE raw_vault.sat_limited_partner_investran ADD COLUMN IF NOT EXISTS domicile_country VARCHAR;
ALTER TABLE raw_vault.sat_limited_partner_investran ADD COLUMN IF NOT EXISTS tier_status VARCHAR;
ALTER TABLE raw_vault.sat_limited_partner_legacy ADD COLUMN IF NOT EXISTS legacy_investor_name VARCHAR;
ALTER TABLE raw_vault.sat_limited_partner_legacy ADD COLUMN IF NOT EXISTS contact_person VARCHAR;
ALTER TABLE raw_vault.sat_limited_partner_legacy ADD COLUMN IF NOT EXISTS phone_number VARCHAR;
ALTER TABLE raw_vault.sat_document_manifest ADD COLUMN IF NOT EXISTS content_sha256 CHAR(64);
ALTER TABLE raw_vault.sat_document_manifest ADD COLUMN IF NOT EXISTS mime_type VARCHAR;
ALTER TABLE raw_vault.sat_document_manifest ADD COLUMN IF NOT EXISTS byte_size BIGINT;
ALTER TABLE business_vault.sat_lp_side_letter_llm_ext ADD COLUMN IF NOT EXISTS fee_cap_bps NUMERIC(8,4);
ALTER TABLE business_vault.sat_lp_side_letter_llm_ext ADD COLUMN IF NOT EXISTS headline_fee_bps NUMERIC(8,4);
ALTER TABLE business_vault.sat_lp_side_letter_llm_ext ADD COLUMN IF NOT EXISTS mfn_election_flag VARCHAR;
ALTER TABLE business_vault.sat_lp_side_letter_llm_ext ADD COLUMN IF NOT EXISTS co_invest_rofr VARCHAR;
ALTER TABLE business_vault.sat_lp_side_letter_llm_ext ADD COLUMN IF NOT EXISTS effective_date DATE;
ALTER TABLE business_vault.sat_lp_side_letter_llm_ext ADD COLUMN IF NOT EXISTS extractor_model_id VARCHAR;
ALTER TABLE business_vault.sat_lp_side_letter_llm_ext ADD COLUMN IF NOT EXISTS confidence_score NUMERIC(4,3);
ALTER TABLE business_vault.sat_lp_side_letter_llm_ext ADD COLUMN IF NOT EXISTS chunk_id VARCHAR;
ALTER TABLE business_vault.sat_lp_side_letter_llm_ext ADD COLUMN IF NOT EXISTS evidence_quote VARCHAR;
ALTER TABLE business_vault.sat_lnk_doc_portco_ai_provenance ADD COLUMN IF NOT EXISTS asc_820_level VARCHAR;
ALTER TABLE business_vault.sat_lnk_doc_portco_ai_provenance ADD COLUMN IF NOT EXISTS ev_ebitda_multiple NUMERIC(6,2);
ALTER TABLE business_vault.sat_lnk_doc_portco_ai_provenance ADD COLUMN IF NOT EXISTS implied_ev_usd NUMERIC(18,2);
ALTER TABLE business_vault.sat_lnk_doc_portco_ai_provenance ADD COLUMN IF NOT EXISTS fair_value_usd NUMERIC(18,2);
ALTER TABLE business_vault.sat_lnk_doc_portco_ai_provenance ADD COLUMN IF NOT EXISTS effective_date DATE;
ALTER TABLE business_vault.sat_lnk_doc_portco_ai_provenance ADD COLUMN IF NOT EXISTS extractor_model_id VARCHAR;
ALTER TABLE business_vault.sat_lnk_doc_portco_ai_provenance ADD COLUMN IF NOT EXISTS confidence_score NUMERIC(4,3);
ALTER TABLE business_vault.sat_lnk_doc_portco_ai_provenance ADD COLUMN IF NOT EXISTS chunk_id VARCHAR;
ALTER TABLE business_vault.sat_lnk_doc_portco_ai_provenance ADD COLUMN IF NOT EXISTS evidence_quote VARCHAR;

-- =============================================================================

-- ZONE 1: HASHED STAGING VIEWS (ALGORITHM A.5 LENGTH-PREFIXED SHA-256)

-- =============================================================================

CREATE OR REPLACE VIEW staging.stg_hub_fund AS
SELECT
    CASE WHEN TRIM(CAST(fund_code AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(fund_code AS VARCHAR)), '') IS NULL OR TRIM(CAST(fund_code AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(fund_code AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(fund_code AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(fund_code AS VARCHAR))), '') END)) END AS hk_fund,
    bkcc, fund_code,
    load_dts,
    rec_src
FROM bronze.src_hub_fund;

CREATE OR REPLACE VIEW staging.stg_hub_limited_partner AS
SELECT
    CASE WHEN TRIM(CAST(lp_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(lp_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(lp_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(lp_natural_key AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(lp_natural_key AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(lp_natural_key AS VARCHAR))), '') END)) END AS hk_limited_partner,
    bkcc, lp_natural_key,
    load_dts,
    rec_src
FROM bronze.src_hub_limited_partner;

CREATE OR REPLACE VIEW staging.stg_hub_portfolio_company AS
SELECT
    CASE WHEN TRIM(CAST(portco_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(portco_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(portco_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(portco_natural_key AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(portco_natural_key AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(portco_natural_key AS VARCHAR))), '') END)) END AS hk_portfolio_company,
    bkcc, portco_natural_key,
    load_dts,
    rec_src
FROM bronze.src_hub_portfolio_company;

CREATE OR REPLACE VIEW staging.stg_hub_document AS
SELECT
    CASE WHEN TRIM(CAST(doc_uri AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(doc_uri AS VARCHAR)), '') IS NULL OR TRIM(CAST(doc_uri AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(doc_uri AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(doc_uri AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(doc_uri AS VARCHAR))), '') END)) END AS hk_document,
    bkcc, doc_uri,
    load_dts,
    rec_src
FROM bronze.src_hub_document;

CREATE OR REPLACE VIEW staging.stg_lnk_lp_commitment AS
WITH fk_resolved AS (
    SELECT
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_fund__fund_code AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_fund__fund_code AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_fund__fund_code AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_fund__fund_code AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_fund__fund_code AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_fund__fund_code AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_fund__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_fund__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_fund__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_fund__fund_code AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_fund__fund_code AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_fund__fund_code AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_fund,
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_limited_partner,
        load_dts,
        rec_src
    FROM bronze.src_lnk_lp_commitment
)
SELECT
    CASE WHEN (hk_fund = REPEAT('f', 64) AND hk_limited_partner = REPEAT('f', 64)) OR (TRIM(CAST(hk_fund AS VARCHAR)) = '-2' OR TRIM(CAST(hk_limited_partner AS VARCHAR)) = '-2') THEN REPEAT('f', 64) WHEN (hk_fund = REPEAT('0', 64) AND hk_limited_partner = REPEAT('0', 64)) OR ((NULLIF(TRIM(CAST(hk_fund AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_fund AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(hk_limited_partner AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner AS VARCHAR)) = '-1')) THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_fund AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_fund AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_fund AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner AS VARCHAR))), '') END)) END AS hk_lnk_lp_commitment,
    hk_fund, hk_limited_partner,
    load_dts,
    rec_src
FROM fk_resolved;

CREATE OR REPLACE VIEW staging.stg_lnk_lp_commitment_co_invest AS
WITH fk_resolved AS (
    SELECT
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_fund__fund_code AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_fund__fund_code AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_fund__fund_code AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_fund__fund_code AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_fund__fund_code AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_fund__fund_code AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_fund__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_fund__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_fund__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_fund__fund_code AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_fund__fund_code AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_fund__fund_code AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_fund,
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_limited_partner,
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_portfolio_company__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_portfolio_company__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_portfolio_company__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_portfolio_company,
        load_dts,
        rec_src
    FROM bronze.src_lnk_lp_commitment_co_invest
)
SELECT
    CASE WHEN (hk_fund = REPEAT('f', 64) AND hk_limited_partner = REPEAT('f', 64) AND hk_portfolio_company = REPEAT('f', 64)) OR (TRIM(CAST(hk_fund AS VARCHAR)) = '-2' OR TRIM(CAST(hk_limited_partner AS VARCHAR)) = '-2' OR TRIM(CAST(hk_portfolio_company AS VARCHAR)) = '-2') THEN REPEAT('f', 64) WHEN (hk_fund = REPEAT('0', 64) AND hk_limited_partner = REPEAT('0', 64) AND hk_portfolio_company = REPEAT('0', 64)) OR ((NULLIF(TRIM(CAST(hk_fund AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_fund AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(hk_limited_partner AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(hk_portfolio_company AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_portfolio_company AS VARCHAR)) = '-1')) THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_fund AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_fund AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_fund AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_portfolio_company AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_portfolio_company AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_portfolio_company AS VARCHAR))), '') END)) END AS hk_lnk_lp_commitment_co_invest,
    hk_fund, hk_limited_partner, hk_portfolio_company,
    load_dts,
    rec_src
FROM fk_resolved;

CREATE OR REPLACE VIEW staging.stg_sal_limited_partner AS
WITH fk_resolved AS (
    SELECT
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_limited_partner_master__lp_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_limited_partner_master__lp_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner_master__lp_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_limited_partner_master__lp_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_limited_partner_master__lp_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner_master__lp_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner_master__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner_master__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner_master__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner_master__lp_natural_key AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner_master__lp_natural_key AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner_master__lp_natural_key AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_limited_partner_master,
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_limited_partner_duplicate__lp_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_limited_partner_duplicate__lp_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner_duplicate__lp_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_limited_partner_duplicate__lp_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_limited_partner_duplicate__lp_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner_duplicate__lp_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner_duplicate__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner_duplicate__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner_duplicate__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner_duplicate__lp_natural_key AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner_duplicate__lp_natural_key AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner_duplicate__lp_natural_key AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_limited_partner_duplicate,
        load_dts,
        rec_src
    FROM bronze.src_sal_limited_partner
)
SELECT
    CASE WHEN (hk_limited_partner_master = REPEAT('f', 64) AND hk_limited_partner_duplicate = REPEAT('f', 64)) OR (TRIM(CAST(hk_limited_partner_master AS VARCHAR)) = '-2' OR TRIM(CAST(hk_limited_partner_duplicate AS VARCHAR)) = '-2') THEN REPEAT('f', 64) WHEN (hk_limited_partner_master = REPEAT('0', 64) AND hk_limited_partner_duplicate = REPEAT('0', 64)) OR ((NULLIF(TRIM(CAST(hk_limited_partner_master AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner_master AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(hk_limited_partner_duplicate AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner_duplicate AS VARCHAR)) = '-1')) THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner_master AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner_master AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner_master AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner_duplicate AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner_duplicate AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner_duplicate AS VARCHAR))), '') END)) END AS hk_sal_limited_partner,
    hk_limited_partner_master, hk_limited_partner_duplicate,
    load_dts,
    rec_src
FROM fk_resolved;

CREATE OR REPLACE VIEW staging.stg_lnk_document_lp_commitment AS
WITH fk_resolved AS (
    SELECT
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_document__doc_uri AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_document__doc_uri AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_document__doc_uri AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_document__doc_uri AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_document__doc_uri AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_document__doc_uri AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_document__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_document__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_document__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_document__doc_uri AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_document__doc_uri AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_document__doc_uri AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_document,
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_fund__fund_code AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_fund__fund_code AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_fund__fund_code AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_fund__fund_code AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_fund__fund_code AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_fund__fund_code AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_fund__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_fund__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_fund__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_fund__fund_code AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_fund__fund_code AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_fund__fund_code AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_fund,
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_limited_partner,
        load_dts,
        rec_src,
        CASE
            WHEN UPPER(TRIM(COALESCE(steward_gate_status, ''))) IN (
                'APPROVED',
                'STEWARD_VERIFIED',
                'AUTO_ACCEPTED_TIER_1',
                'AUTO_ACCEPTED_TIER_2'
            ) THEN 'APPROVED'
            ELSE COALESCE(
                NULLIF(TRIM(steward_gate_status), ''), 'STEWARD_HOLD'
            )
        END AS steward_gate_status
    FROM bronze.src_lnk_document_lp_commitment
)
SELECT
    CASE WHEN (hk_document = REPEAT('f', 64) AND hk_fund = REPEAT('f', 64) AND hk_limited_partner = REPEAT('f', 64)) OR (TRIM(CAST(hk_document AS VARCHAR)) = '-2' OR TRIM(CAST(hk_fund AS VARCHAR)) = '-2' OR TRIM(CAST(hk_limited_partner AS VARCHAR)) = '-2') THEN REPEAT('f', 64) WHEN (hk_document = REPEAT('0', 64) AND hk_fund = REPEAT('0', 64) AND hk_limited_partner = REPEAT('0', 64)) OR ((NULLIF(TRIM(CAST(hk_document AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_document AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(hk_fund AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_fund AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(hk_limited_partner AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner AS VARCHAR)) = '-1')) THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_document AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_document AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_document AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_fund AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_fund AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_fund AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner AS VARCHAR))), '') END)) END AS hk_lnk_document_lp_commitment,
    hk_document, hk_fund, hk_limited_partner,
    load_dts,
    rec_src,
    steward_gate_status
FROM fk_resolved;

CREATE OR REPLACE VIEW staging.stg_lnk_document_portfolio_company AS
WITH fk_resolved AS (
    SELECT
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_document__doc_uri AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_document__doc_uri AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_document__doc_uri AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_document__doc_uri AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_document__doc_uri AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_document__doc_uri AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_document__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_document__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_document__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_document__doc_uri AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_document__doc_uri AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_document__doc_uri AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_document,
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_portfolio_company__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_portfolio_company__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_portfolio_company__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_portfolio_company,
        load_dts,
        rec_src,
        CASE
            WHEN UPPER(TRIM(COALESCE(steward_gate_status, ''))) IN (
                'APPROVED',
                'STEWARD_VERIFIED',
                'AUTO_ACCEPTED_TIER_1',
                'AUTO_ACCEPTED_TIER_2'
            ) THEN 'APPROVED'
            ELSE COALESCE(
                NULLIF(TRIM(steward_gate_status), ''), 'STEWARD_HOLD'
            )
        END AS steward_gate_status
    FROM bronze.src_lnk_document_portfolio_company
)
SELECT
    CASE WHEN (hk_document = REPEAT('f', 64) AND hk_portfolio_company = REPEAT('f', 64)) OR (TRIM(CAST(hk_document AS VARCHAR)) = '-2' OR TRIM(CAST(hk_portfolio_company AS VARCHAR)) = '-2') THEN REPEAT('f', 64) WHEN (hk_document = REPEAT('0', 64) AND hk_portfolio_company = REPEAT('0', 64)) OR ((NULLIF(TRIM(CAST(hk_document AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_document AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(hk_portfolio_company AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_portfolio_company AS VARCHAR)) = '-1')) THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_document AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_document AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_document AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_portfolio_company AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_portfolio_company AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_portfolio_company AS VARCHAR))), '') END)) END AS hk_lnk_document_portfolio_company,
    hk_document, hk_portfolio_company,
    load_dts,
    rec_src,
    steward_gate_status
FROM fk_resolved;

CREATE OR REPLACE VIEW staging.stg_sat_fund_investran AS
SELECT
    CASE WHEN TRIM(CAST(parent_bk__fund_code AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(parent_bk__fund_code AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_bk__fund_code AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__fund_code AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__fund_code AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__fund_code AS VARCHAR))), '') END)) END AS hk_fund,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(isin AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(isin AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(isin AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(fund_name AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(fund_name AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(fund_name AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(vintage_year AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(vintage_year AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(vintage_year AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(target_size_usd AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(target_size_usd AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(target_size_usd AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(mgmt_fee_bps AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(mgmt_fee_bps AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(mgmt_fee_bps AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(deprecated_fax_no AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(deprecated_fax_no AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(deprecated_fax_no AS VARCHAR)), '') END)) AS hash_diff,
    isin, fund_name, vintage_year, target_size_usd, mgmt_fee_bps, deprecated_fax_no
FROM bronze.src_sat_fund_investran;

CREATE OR REPLACE VIEW staging.stg_sat_limited_partner_pii AS
SELECT
    CASE WHEN TRIM(CAST(parent_bk__lp_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(parent_bk__lp_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_bk__lp_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__lp_natural_key AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__lp_natural_key AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__lp_natural_key AS VARCHAR))), '') END)) END AS hk_limited_partner,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(wire_iban AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(wire_iban AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(wire_iban AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(tax_id AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(tax_id AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(tax_id AS VARCHAR)), '') END)) AS hash_diff,
    wire_iban, tax_id
FROM bronze.src_sat_limited_partner_pii;

CREATE OR REPLACE VIEW staging.stg_sat_lp_commitment_investran AS
SELECT
    CASE WHEN (parent_fk__hk_fund = REPEAT('f', 64) AND parent_fk__hk_limited_partner = REPEAT('f', 64)) OR (TRIM(CAST(parent_fk__hk_fund AS VARCHAR)) = '-2' OR TRIM(CAST(parent_fk__hk_limited_partner AS VARCHAR)) = '-2') THEN REPEAT('f', 64) WHEN (parent_fk__hk_fund = REPEAT('0', 64) AND parent_fk__hk_limited_partner = REPEAT('0', 64)) OR ((NULLIF(TRIM(CAST(parent_fk__hk_fund AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_fk__hk_fund AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(parent_fk__hk_limited_partner AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_fk__hk_limited_partner AS VARCHAR)) = '-1')) THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_fk__hk_fund AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_fk__hk_fund AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_fk__hk_fund AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_fk__hk_limited_partner AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_fk__hk_limited_partner AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_fk__hk_limited_partner AS VARCHAR))), '') END)) END AS hk_lnk_lp_commitment,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(commitment_amount_usd AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(commitment_amount_usd AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(commitment_amount_usd AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(called_usd AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(called_usd AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(called_usd AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(distribution_recallable_flag AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(distribution_recallable_flag AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(distribution_recallable_flag AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(eur_usd_rate AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(eur_usd_rate AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(eur_usd_rate AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(gbp_usd_rate AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(gbp_usd_rate AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(gbp_usd_rate AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(daily_interim_nav_usd AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(daily_interim_nav_usd AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(daily_interim_nav_usd AS VARCHAR)), '') END)) AS hash_diff,
    commitment_amount_usd, called_usd, distribution_recallable_flag, eur_usd_rate, gbp_usd_rate, daily_interim_nav_usd
FROM bronze.src_sat_lp_commitment_investran;

CREATE OR REPLACE VIEW staging.stg_sat_portfolio_company_dealcloud AS
SELECT
    CASE WHEN TRIM(CAST(parent_bk__portco_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(parent_bk__portco_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_bk__portco_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__portco_natural_key AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__portco_natural_key AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__portco_natural_key AS VARCHAR))), '') END)) END AS hk_portfolio_company,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(deal_code AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(deal_code AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(deal_code AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(company_name AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(company_name AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(company_name AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(gp_deal_partner AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(gp_deal_partner AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(gp_deal_partner AS VARCHAR)), '') END)) AS hash_diff,
    deal_code, company_name, gp_deal_partner
FROM bronze.src_sat_portfolio_company_dealcloud;

CREATE OR REPLACE VIEW staging.stg_sat_portco_valuation_mnpi AS
SELECT
    CASE WHEN TRIM(CAST(parent_bk__portco_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(parent_bk__portco_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_bk__portco_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__portco_natural_key AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__portco_natural_key AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__portco_natural_key AS VARCHAR))), '') END)) END AS hk_portfolio_company,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(entry_ebitda_usd AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(entry_ebitda_usd AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(entry_ebitda_usd AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(entry_ev_multiple AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(entry_ev_multiple AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(entry_ev_multiple AS VARCHAR)), '') END)) AS hash_diff,
    entry_ebitda_usd, entry_ev_multiple
FROM bronze.src_sat_portco_valuation_mnpi;

CREATE OR REPLACE VIEW staging.stg_sat_fund_investran_esg AS
SELECT
    CASE WHEN TRIM(CAST(parent_bk__fund_code AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(parent_bk__fund_code AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_bk__fund_code AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__fund_code AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__fund_code AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__fund_code AS VARCHAR))), '') END)) END AS hk_fund,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(ilpa_esg_rating AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(ilpa_esg_rating AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(ilpa_esg_rating AS VARCHAR)), '') END)) AS hash_diff,
    ilpa_esg_rating
FROM bronze.src_sat_fund_investran_esg;

CREATE OR REPLACE VIEW staging.stg_sat_limited_partner_investran AS
SELECT
    CASE WHEN TRIM(CAST(parent_bk__lp_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(parent_bk__lp_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_bk__lp_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__lp_natural_key AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__lp_natural_key AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__lp_natural_key AS VARCHAR))), '') END)) END AS hk_limited_partner,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(lei AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(lei AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(lei AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(lp_legal_name AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(lp_legal_name AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(lp_legal_name AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(domicile_country AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(domicile_country AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(domicile_country AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(tier_status AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(tier_status AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(tier_status AS VARCHAR)), '') END)) AS hash_diff,
    lei, lp_legal_name, domicile_country, tier_status
FROM bronze.src_sat_limited_partner_investran;

CREATE OR REPLACE VIEW staging.stg_sat_limited_partner_legacy AS
SELECT
    CASE WHEN TRIM(CAST(parent_bk__lp_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(parent_bk__lp_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_bk__lp_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__lp_natural_key AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__lp_natural_key AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__lp_natural_key AS VARCHAR))), '') END)) END AS hk_limited_partner,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(legacy_investor_name AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(legacy_investor_name AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(legacy_investor_name AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(contact_person AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(contact_person AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(contact_person AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(phone_number AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(phone_number AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(phone_number AS VARCHAR)), '') END)) AS hash_diff,
    legacy_investor_name, contact_person, phone_number
FROM bronze.src_sat_limited_partner_legacy;

CREATE OR REPLACE VIEW staging.stg_sat_document_manifest AS
SELECT
    CASE WHEN TRIM(CAST(parent_bk__doc_uri AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(parent_bk__doc_uri AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_bk__doc_uri AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__doc_uri AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__doc_uri AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__doc_uri AS VARCHAR))), '') END)) END AS hk_document,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(content_sha256 AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(content_sha256 AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(content_sha256 AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(mime_type AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(mime_type AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(mime_type AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(byte_size AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(byte_size AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(byte_size AS VARCHAR)), '') END)) AS hash_diff,
    content_sha256, mime_type, byte_size
FROM bronze.src_sat_document_manifest;

CREATE OR REPLACE VIEW staging.stg_sat_lp_side_letter_llm_ext AS
SELECT
    CASE WHEN (parent_fk__hk_document = REPEAT('f', 64) AND parent_fk__hk_fund = REPEAT('f', 64) AND parent_fk__hk_limited_partner = REPEAT('f', 64)) OR (TRIM(CAST(parent_fk__hk_document AS VARCHAR)) = '-2' OR TRIM(CAST(parent_fk__hk_fund AS VARCHAR)) = '-2' OR TRIM(CAST(parent_fk__hk_limited_partner AS VARCHAR)) = '-2') THEN REPEAT('f', 64) WHEN (parent_fk__hk_document = REPEAT('0', 64) AND parent_fk__hk_fund = REPEAT('0', 64) AND parent_fk__hk_limited_partner = REPEAT('0', 64)) OR ((NULLIF(TRIM(CAST(parent_fk__hk_document AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_fk__hk_document AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(parent_fk__hk_fund AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_fk__hk_fund AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(parent_fk__hk_limited_partner AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_fk__hk_limited_partner AS VARCHAR)) = '-1')) THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_fk__hk_document AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_fk__hk_document AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_fk__hk_document AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_fk__hk_fund AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_fk__hk_fund AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_fk__hk_fund AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_fk__hk_limited_partner AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_fk__hk_limited_partner AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_fk__hk_limited_partner AS VARCHAR))), '') END)) END AS hk_lnk_document_lp_commitment,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(fee_cap_bps AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(fee_cap_bps AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(fee_cap_bps AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(headline_fee_bps AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(headline_fee_bps AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(headline_fee_bps AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(mfn_election_flag AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(mfn_election_flag AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(mfn_election_flag AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(co_invest_rofr AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(co_invest_rofr AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(co_invest_rofr AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(effective_date AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(effective_date AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(effective_date AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(extractor_model_id AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(extractor_model_id AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(extractor_model_id AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(confidence_score AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(confidence_score AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(confidence_score AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(chunk_id AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(chunk_id AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(chunk_id AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(evidence_quote AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(evidence_quote AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(evidence_quote AS VARCHAR)), '') END)) AS hash_diff,
    fee_cap_bps, headline_fee_bps, mfn_election_flag, co_invest_rofr, effective_date, extractor_model_id, confidence_score, chunk_id, evidence_quote,
    CASE
        WHEN UPPER(TRIM(COALESCE(steward_gate_status, ''))) IN (
            'APPROVED',
            'STEWARD_VERIFIED',
            'AUTO_ACCEPTED_TIER_1',
            'AUTO_ACCEPTED_TIER_2'
        ) THEN 'APPROVED'
        ELSE COALESCE(
            NULLIF(TRIM(steward_gate_status), ''), 'STEWARD_HOLD'
        )
    END AS steward_gate_status
FROM bronze.src_sat_lp_side_letter_llm_ext;

CREATE OR REPLACE VIEW staging.stg_sat_lnk_doc_portco_ai_provenance AS
SELECT
    CASE WHEN (parent_fk__hk_document = REPEAT('f', 64) AND parent_fk__hk_portfolio_company = REPEAT('f', 64)) OR (TRIM(CAST(parent_fk__hk_document AS VARCHAR)) = '-2' OR TRIM(CAST(parent_fk__hk_portfolio_company AS VARCHAR)) = '-2') THEN REPEAT('f', 64) WHEN (parent_fk__hk_document = REPEAT('0', 64) AND parent_fk__hk_portfolio_company = REPEAT('0', 64)) OR ((NULLIF(TRIM(CAST(parent_fk__hk_document AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_fk__hk_document AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(parent_fk__hk_portfolio_company AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_fk__hk_portfolio_company AS VARCHAR)) = '-1')) THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_fk__hk_document AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_fk__hk_document AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_fk__hk_document AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_fk__hk_portfolio_company AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_fk__hk_portfolio_company AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_fk__hk_portfolio_company AS VARCHAR))), '') END)) END AS hk_lnk_document_portfolio_company,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(asc_820_level AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(asc_820_level AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(asc_820_level AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(ev_ebitda_multiple AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(ev_ebitda_multiple AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(ev_ebitda_multiple AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(implied_ev_usd AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(implied_ev_usd AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(implied_ev_usd AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(fair_value_usd AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(fair_value_usd AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(fair_value_usd AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(effective_date AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(effective_date AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(effective_date AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(extractor_model_id AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(extractor_model_id AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(extractor_model_id AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(confidence_score AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(confidence_score AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(confidence_score AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(chunk_id AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(chunk_id AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(chunk_id AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(evidence_quote AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(evidence_quote AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(evidence_quote AS VARCHAR)), '') END)) AS hash_diff,
    asc_820_level, ev_ebitda_multiple, implied_ev_usd, fair_value_usd, effective_date, extractor_model_id, confidence_score, chunk_id, evidence_quote,
    CASE
        WHEN UPPER(TRIM(COALESCE(steward_gate_status, ''))) IN (
            'APPROVED',
            'STEWARD_VERIFIED',
            'AUTO_ACCEPTED_TIER_1',
            'AUTO_ACCEPTED_TIER_2'
        ) THEN 'APPROVED'
        ELSE COALESCE(
            NULLIF(TRIM(steward_gate_status), ''), 'STEWARD_HOLD'
        )
    END AS steward_gate_status
FROM bronze.src_sat_lnk_doc_portco_ai_provenance;

CREATE OR REPLACE VIEW staging.stg_investran_src_pe_funds AS
SELECT
    CASE WHEN TRIM(CAST(fund_code AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(fund_code AS VARCHAR)), '') IS NULL OR TRIM(CAST(fund_code AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(fund_code AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(fund_code AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(fund_code AS VARCHAR))), '') END)) END AS hk_fund,
    bkcc, fund_code,
    load_dts,
    rec_src
FROM bronze.src_hub_fund;

CREATE OR REPLACE VIEW staging.stg_investran_src_lp_investors AS
SELECT
    CASE WHEN TRIM(CAST(lp_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(lp_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(lp_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(lp_natural_key AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(lp_natural_key AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(lp_natural_key AS VARCHAR))), '') END)) END AS hk_limited_partner,
    bkcc, lp_natural_key,
    load_dts,
    rec_src
FROM bronze.src_hub_limited_partner;

CREATE OR REPLACE VIEW staging.stg_legacy_gp_src_legacy_lp_roster AS
SELECT
    CASE WHEN TRIM(CAST(lp_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(lp_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(lp_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(lp_natural_key AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(lp_natural_key AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(lp_natural_key AS VARCHAR))), '') END)) END AS hk_limited_partner,
    bkcc, lp_natural_key,
    load_dts,
    rec_src
FROM bronze.src_hub_limited_partner;

CREATE OR REPLACE VIEW staging.stg_dealcloud_src_portfolio_companies AS
SELECT
    CASE WHEN TRIM(CAST(portco_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(portco_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(portco_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(portco_natural_key AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(portco_natural_key AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(portco_natural_key AS VARCHAR))), '') END)) END AS hk_portfolio_company,
    bkcc, portco_natural_key,
    load_dts,
    rec_src
FROM bronze.src_hub_portfolio_company;

CREATE OR REPLACE VIEW staging.stg_dms_documents AS
SELECT
    CASE WHEN TRIM(CAST(doc_uri AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(doc_uri AS VARCHAR)), '') IS NULL OR TRIM(CAST(doc_uri AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(doc_uri AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(doc_uri AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(doc_uri AS VARCHAR))), '') END)) END AS hk_document,
    bkcc, doc_uri,
    load_dts,
    rec_src
FROM bronze.src_hub_document;

CREATE OR REPLACE VIEW staging.stg_investran_src_lp_commitments_and_calls AS
WITH fk_resolved AS (
    SELECT
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_fund__fund_code AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_fund__fund_code AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_fund__fund_code AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_fund__fund_code AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_fund__fund_code AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_fund__fund_code AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_fund__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_fund__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_fund__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_fund__fund_code AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_fund__fund_code AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_fund__fund_code AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_fund,
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_limited_partner,
        load_dts,
        rec_src
    FROM bronze.src_lnk_lp_commitment
)
SELECT
    CASE WHEN (hk_fund = REPEAT('f', 64) AND hk_limited_partner = REPEAT('f', 64)) OR (TRIM(CAST(hk_fund AS VARCHAR)) = '-2' OR TRIM(CAST(hk_limited_partner AS VARCHAR)) = '-2') THEN REPEAT('f', 64) WHEN (hk_fund = REPEAT('0', 64) AND hk_limited_partner = REPEAT('0', 64)) OR ((NULLIF(TRIM(CAST(hk_fund AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_fund AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(hk_limited_partner AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner AS VARCHAR)) = '-1')) THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_fund AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_fund AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_fund AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner AS VARCHAR))), '') END)) END AS hk_lnk_lp_commitment,
    hk_fund, hk_limited_partner,
    load_dts,
    rec_src
FROM fk_resolved;

CREATE OR REPLACE VIEW staging.stg_mdm_src_lp_steward_crosswalk AS
WITH fk_resolved AS (
    SELECT
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_limited_partner_master__lp_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_limited_partner_master__lp_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner_master__lp_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_limited_partner_master__lp_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_limited_partner_master__lp_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner_master__lp_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner_master__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner_master__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner_master__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner_master__lp_natural_key AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner_master__lp_natural_key AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner_master__lp_natural_key AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_limited_partner_master,
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_limited_partner_duplicate__lp_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_limited_partner_duplicate__lp_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner_duplicate__lp_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_limited_partner_duplicate__lp_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_limited_partner_duplicate__lp_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner_duplicate__lp_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner_duplicate__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner_duplicate__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner_duplicate__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner_duplicate__lp_natural_key AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner_duplicate__lp_natural_key AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner_duplicate__lp_natural_key AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_limited_partner_duplicate,
        load_dts,
        rec_src
    FROM bronze.src_sal_limited_partner
)
SELECT
    CASE WHEN (hk_limited_partner_master = REPEAT('f', 64) AND hk_limited_partner_duplicate = REPEAT('f', 64)) OR (TRIM(CAST(hk_limited_partner_master AS VARCHAR)) = '-2' OR TRIM(CAST(hk_limited_partner_duplicate AS VARCHAR)) = '-2') THEN REPEAT('f', 64) WHEN (hk_limited_partner_master = REPEAT('0', 64) AND hk_limited_partner_duplicate = REPEAT('0', 64)) OR ((NULLIF(TRIM(CAST(hk_limited_partner_master AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner_master AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(hk_limited_partner_duplicate AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner_duplicate AS VARCHAR)) = '-1')) THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner_master AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner_master AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner_master AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner_duplicate AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner_duplicate AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner_duplicate AS VARCHAR))), '') END)) END AS hk_sal_limited_partner,
    hk_limited_partner_master, hk_limited_partner_duplicate,
    load_dts,
    rec_src
FROM fk_resolved;

CREATE OR REPLACE VIEW staging.stg_unstructured_vdr_llm_extractions AS
WITH fk_resolved AS (
    SELECT
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_document__doc_uri AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_document__doc_uri AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_document__doc_uri AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_document__doc_uri AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_document__doc_uri AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_document__doc_uri AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_document__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_document__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_document__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_document__doc_uri AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_document__doc_uri AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_document__doc_uri AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_document,
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_fund__fund_code AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_fund__fund_code AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_fund__fund_code AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_fund__fund_code AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_fund__fund_code AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_fund__fund_code AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_fund__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_fund__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_fund__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_fund__fund_code AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_fund__fund_code AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_fund__fund_code AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_fund,
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_limited_partner,
        load_dts,
        rec_src,
        CASE
            WHEN UPPER(TRIM(COALESCE(steward_gate_status, ''))) IN (
                'APPROVED',
                'STEWARD_VERIFIED',
                'AUTO_ACCEPTED_TIER_1',
                'AUTO_ACCEPTED_TIER_2'
            ) THEN 'APPROVED'
            ELSE COALESCE(
                NULLIF(TRIM(steward_gate_status), ''), 'STEWARD_HOLD'
            )
        END AS steward_gate_status
    FROM bronze.src_lnk_document_lp_commitment
)
SELECT
    CASE WHEN (hk_document = REPEAT('f', 64) AND hk_fund = REPEAT('f', 64) AND hk_limited_partner = REPEAT('f', 64)) OR (TRIM(CAST(hk_document AS VARCHAR)) = '-2' OR TRIM(CAST(hk_fund AS VARCHAR)) = '-2' OR TRIM(CAST(hk_limited_partner AS VARCHAR)) = '-2') THEN REPEAT('f', 64) WHEN (hk_document = REPEAT('0', 64) AND hk_fund = REPEAT('0', 64) AND hk_limited_partner = REPEAT('0', 64)) OR ((NULLIF(TRIM(CAST(hk_document AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_document AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(hk_fund AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_fund AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(hk_limited_partner AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner AS VARCHAR)) = '-1')) THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_document AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_document AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_document AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_fund AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_fund AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_fund AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner AS VARCHAR))), '') END)) END AS hk_lnk_document_lp_commitment,
    hk_document, hk_fund, hk_limited_partner,
    load_dts,
    rec_src,
    steward_gate_status
FROM fk_resolved;

CREATE OR REPLACE VIEW staging.stg_dms_src_side_letter_extractions AS
WITH fk_resolved AS (
    SELECT
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_document__doc_uri AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_document__doc_uri AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_document__doc_uri AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_document__doc_uri AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_document__doc_uri AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_document__doc_uri AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_document__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_document__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_document__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_document__doc_uri AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_document__doc_uri AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_document__doc_uri AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_document,
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_fund__fund_code AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_fund__fund_code AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_fund__fund_code AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_fund__fund_code AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_fund__fund_code AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_fund__fund_code AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_fund__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_fund__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_fund__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_fund__fund_code AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_fund__fund_code AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_fund__fund_code AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_fund,
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner__lp_natural_key AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_limited_partner,
        load_dts,
        rec_src,
        CASE
            WHEN UPPER(TRIM(COALESCE(steward_gate_status, ''))) IN (
                'APPROVED',
                'STEWARD_VERIFIED',
                'AUTO_ACCEPTED_TIER_1',
                'AUTO_ACCEPTED_TIER_2'
            ) THEN 'APPROVED'
            ELSE COALESCE(
                NULLIF(TRIM(steward_gate_status), ''), 'STEWARD_HOLD'
            )
        END AS steward_gate_status
    FROM bronze.src_lnk_document_lp_commitment
)
SELECT
    CASE WHEN (hk_document = REPEAT('f', 64) AND hk_fund = REPEAT('f', 64) AND hk_limited_partner = REPEAT('f', 64)) OR (TRIM(CAST(hk_document AS VARCHAR)) = '-2' OR TRIM(CAST(hk_fund AS VARCHAR)) = '-2' OR TRIM(CAST(hk_limited_partner AS VARCHAR)) = '-2') THEN REPEAT('f', 64) WHEN (hk_document = REPEAT('0', 64) AND hk_fund = REPEAT('0', 64) AND hk_limited_partner = REPEAT('0', 64)) OR ((NULLIF(TRIM(CAST(hk_document AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_document AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(hk_fund AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_fund AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(hk_limited_partner AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_limited_partner AS VARCHAR)) = '-1')) THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_document AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_document AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_document AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_fund AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_fund AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_fund AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_limited_partner AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_limited_partner AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_limited_partner AS VARCHAR))), '') END)) END AS hk_lnk_document_lp_commitment,
    hk_document, hk_fund, hk_limited_partner,
    load_dts,
    rec_src,
    steward_gate_status
FROM fk_resolved;

CREATE OR REPLACE VIEW staging.stg_dms_src_ic_memo_extractions AS
WITH fk_resolved AS (
    SELECT
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_document__doc_uri AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_document__doc_uri AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_document__doc_uri AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_document__doc_uri AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_document__doc_uri AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_document__doc_uri AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_document__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_document__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_document__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_document__doc_uri AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_document__doc_uri AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_document__doc_uri AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_document,
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_portfolio_company__bkcc AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_portfolio_company__bkcc AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_portfolio_company__bkcc AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_portfolio_company__portco_natural_key AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_portfolio_company,
        load_dts,
        rec_src,
        CASE
            WHEN UPPER(TRIM(COALESCE(steward_gate_status, ''))) IN (
                'APPROVED',
                'STEWARD_VERIFIED',
                'AUTO_ACCEPTED_TIER_1',
                'AUTO_ACCEPTED_TIER_2'
            ) THEN 'APPROVED'
            ELSE COALESCE(
                NULLIF(TRIM(steward_gate_status), ''), 'STEWARD_HOLD'
            )
        END AS steward_gate_status
    FROM bronze.src_lnk_document_portfolio_company
)
SELECT
    CASE WHEN (hk_document = REPEAT('f', 64) AND hk_portfolio_company = REPEAT('f', 64)) OR (TRIM(CAST(hk_document AS VARCHAR)) = '-2' OR TRIM(CAST(hk_portfolio_company AS VARCHAR)) = '-2') THEN REPEAT('f', 64) WHEN (hk_document = REPEAT('0', 64) AND hk_portfolio_company = REPEAT('0', 64)) OR ((NULLIF(TRIM(CAST(hk_document AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_document AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(hk_portfolio_company AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_portfolio_company AS VARCHAR)) = '-1')) THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_document AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_document AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_document AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_portfolio_company AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_portfolio_company AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_portfolio_company AS VARCHAR))), '') END)) END AS hk_lnk_document_portfolio_company,
    hk_document, hk_portfolio_company,
    load_dts,
    rec_src,
    steward_gate_status
FROM fk_resolved;

-- =============================================================================

-- ZONE 2: IDEMPOTENT INSERT-ONLY RAW & BUSINESS VAULT LOADS

-- =============================================================================

INSERT INTO raw_vault.hub_fund (hk_fund, bkcc, fund_code, load_dts, rec_src)
SELECT
    src.hk_fund,
    src.bkcc, src.fund_code,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_hub_fund src
WHERE src.hk_fund NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.hub_fund h
      WHERE h.hk_fund = src.hk_fund
  )
GROUP BY src.hk_fund, src.bkcc, src.fund_code;

INSERT INTO raw_vault.hub_limited_partner (hk_limited_partner, bkcc, lp_natural_key, load_dts, rec_src)
SELECT
    src.hk_limited_partner,
    src.bkcc, src.lp_natural_key,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_hub_limited_partner src
WHERE src.hk_limited_partner NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.hub_limited_partner h
      WHERE h.hk_limited_partner = src.hk_limited_partner
  )
GROUP BY src.hk_limited_partner, src.bkcc, src.lp_natural_key;

INSERT INTO raw_vault.hub_portfolio_company (hk_portfolio_company, bkcc, portco_natural_key, load_dts, rec_src)
SELECT
    src.hk_portfolio_company,
    src.bkcc, src.portco_natural_key,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_hub_portfolio_company src
WHERE src.hk_portfolio_company NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.hub_portfolio_company h
      WHERE h.hk_portfolio_company = src.hk_portfolio_company
  )
GROUP BY src.hk_portfolio_company, src.bkcc, src.portco_natural_key;

INSERT INTO raw_vault.hub_document (hk_document, bkcc, doc_uri, load_dts, rec_src)
SELECT
    src.hk_document,
    src.bkcc, src.doc_uri,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_hub_document src
WHERE src.hk_document NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.hub_document h
      WHERE h.hk_document = src.hk_document
  )
GROUP BY src.hk_document, src.bkcc, src.doc_uri;

INSERT INTO raw_vault.lnk_lp_commitment (hk_lnk_lp_commitment, hk_fund, hk_limited_partner, load_dts, rec_src)
SELECT
    src.hk_lnk_lp_commitment,
    src.hk_fund, src.hk_limited_partner,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_lnk_lp_commitment src
WHERE src.hk_lnk_lp_commitment NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.lnk_lp_commitment l
      WHERE l.hk_lnk_lp_commitment = src.hk_lnk_lp_commitment
  )
GROUP BY src.hk_lnk_lp_commitment, src.hk_fund, src.hk_limited_partner;

INSERT INTO raw_vault.lnk_lp_commitment_co_invest (hk_lnk_lp_commitment_co_invest, hk_fund, hk_limited_partner, hk_portfolio_company, load_dts, rec_src)
SELECT
    src.hk_lnk_lp_commitment_co_invest,
    src.hk_fund, src.hk_limited_partner, src.hk_portfolio_company,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_lnk_lp_commitment_co_invest src
WHERE src.hk_lnk_lp_commitment_co_invest NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.lnk_lp_commitment_co_invest l
      WHERE l.hk_lnk_lp_commitment_co_invest = src.hk_lnk_lp_commitment_co_invest
  )
GROUP BY src.hk_lnk_lp_commitment_co_invest, src.hk_fund, src.hk_limited_partner, src.hk_portfolio_company;

INSERT INTO business_vault.sal_limited_partner (hk_sal_limited_partner, hk_limited_partner_master, hk_limited_partner_duplicate, load_dts, rec_src)
SELECT
    src.hk_sal_limited_partner,
    src.hk_limited_partner_master, src.hk_limited_partner_duplicate,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_sal_limited_partner src
WHERE src.hk_sal_limited_partner NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND src.hk_limited_partner_master <> src.hk_limited_partner_duplicate
  AND NOT EXISTS (
      SELECT 1 FROM business_vault.sal_limited_partner l_inv
      WHERE l_inv.hk_limited_partner_master = src.hk_limited_partner_duplicate
        AND l_inv.hk_limited_partner_duplicate = src.hk_limited_partner_master
  )
  AND NOT EXISTS (
      SELECT 1 FROM staging.stg_sal_limited_partner src_inv
      WHERE src_inv.hk_limited_partner_master = src.hk_limited_partner_duplicate
        AND src_inv.hk_limited_partner_duplicate = src.hk_limited_partner_master
        AND src_inv.hk_limited_partner_master <> src_inv.hk_limited_partner_duplicate
        AND (
            src_inv.load_dts < src.load_dts
            OR (
                src_inv.load_dts = src.load_dts
                AND src_inv.hk_limited_partner_master < src.hk_limited_partner_master
            )
        )
  )
  AND NOT EXISTS (
      SELECT 1 FROM business_vault.sal_limited_partner l
      WHERE l.hk_sal_limited_partner = src.hk_sal_limited_partner
  )
GROUP BY src.hk_sal_limited_partner, src.hk_limited_partner_master, src.hk_limited_partner_duplicate;

INSERT INTO raw_vault.lnk_document_lp_commitment (hk_lnk_document_lp_commitment, hk_document, hk_fund, hk_limited_partner, load_dts, rec_src)
SELECT
    src.hk_lnk_document_lp_commitment,
    src.hk_document, src.hk_fund, src.hk_limited_partner,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_lnk_document_lp_commitment src
WHERE src.hk_lnk_document_lp_commitment NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND src.steward_gate_status = 'APPROVED'
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.lnk_document_lp_commitment l
      WHERE l.hk_lnk_document_lp_commitment = src.hk_lnk_document_lp_commitment
  )
GROUP BY src.hk_lnk_document_lp_commitment, src.hk_document, src.hk_fund, src.hk_limited_partner;

INSERT INTO raw_vault.lnk_document_portfolio_company (hk_lnk_document_portfolio_company, hk_document, hk_portfolio_company, load_dts, rec_src)
SELECT
    src.hk_lnk_document_portfolio_company,
    src.hk_document, src.hk_portfolio_company,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_lnk_document_portfolio_company src
WHERE src.hk_lnk_document_portfolio_company NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND src.steward_gate_status = 'APPROVED'
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.lnk_document_portfolio_company l
      WHERE l.hk_lnk_document_portfolio_company = src.hk_lnk_document_portfolio_company
  )
GROUP BY src.hk_lnk_document_portfolio_company, src.hk_document, src.hk_portfolio_company;

INSERT INTO raw_vault.sat_fund_investran (
    hk_fund, load_dts, rec_src, hash_diff, isin, fund_name, vintage_year, target_size_usd, mgmt_fee_bps, deprecated_fax_no
)
SELECT
    src.hk_fund,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.isin, src.fund_name, src.vintage_year, src.target_size_usd, src.mgmt_fee_bps, src.deprecated_fax_no
FROM staging.stg_sat_fund_investran src
LEFT JOIN (
    SELECT hk_fund, load_dts, hash_diff
    FROM raw_vault.sat_fund_investran
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_fund ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_fund = src.hk_fund
WHERE src.hk_fund NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND (
      cur.hk_fund IS NULL
      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)
  );

INSERT INTO raw_vault.sat_limited_partner_pii (
    hk_limited_partner, load_dts, rec_src, hash_diff, wire_iban, tax_id
)
SELECT
    src.hk_limited_partner,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.wire_iban, src.tax_id
FROM staging.stg_sat_limited_partner_pii src
LEFT JOIN (
    SELECT hk_limited_partner, load_dts, hash_diff
    FROM raw_vault.sat_limited_partner_pii
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_limited_partner ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_limited_partner = src.hk_limited_partner
WHERE src.hk_limited_partner NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND (
      cur.hk_limited_partner IS NULL
      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)
  );

INSERT INTO raw_vault.sat_lp_commitment_investran (
    hk_lnk_lp_commitment, load_dts, rec_src, hash_diff, commitment_amount_usd, called_usd, distribution_recallable_flag, eur_usd_rate, gbp_usd_rate, daily_interim_nav_usd
)
SELECT
    src.hk_lnk_lp_commitment,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.commitment_amount_usd, src.called_usd, src.distribution_recallable_flag, src.eur_usd_rate, src.gbp_usd_rate, src.daily_interim_nav_usd
FROM staging.stg_sat_lp_commitment_investran src
LEFT JOIN (
    SELECT hk_lnk_lp_commitment, load_dts, hash_diff
    FROM raw_vault.sat_lp_commitment_investran
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_lnk_lp_commitment ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_lnk_lp_commitment = src.hk_lnk_lp_commitment
WHERE src.hk_lnk_lp_commitment NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND EXISTS (
      SELECT 1 FROM raw_vault.lnk_lp_commitment pl
      WHERE pl.hk_lnk_lp_commitment = src.hk_lnk_lp_commitment
  )
  AND (
      cur.hk_lnk_lp_commitment IS NULL
      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)
  );

INSERT INTO raw_vault.sat_portfolio_company_dealcloud (
    hk_portfolio_company, load_dts, rec_src, hash_diff, deal_code, company_name, gp_deal_partner
)
SELECT
    src.hk_portfolio_company,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.deal_code, src.company_name, src.gp_deal_partner
FROM staging.stg_sat_portfolio_company_dealcloud src
LEFT JOIN (
    SELECT hk_portfolio_company, load_dts, hash_diff
    FROM raw_vault.sat_portfolio_company_dealcloud
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_portfolio_company ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_portfolio_company = src.hk_portfolio_company
WHERE src.hk_portfolio_company NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND (
      cur.hk_portfolio_company IS NULL
      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)
  );

INSERT INTO raw_vault.sat_portco_valuation_mnpi (
    hk_portfolio_company, load_dts, rec_src, hash_diff, entry_ebitda_usd, entry_ev_multiple
)
SELECT
    src.hk_portfolio_company,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.entry_ebitda_usd, src.entry_ev_multiple
FROM staging.stg_sat_portco_valuation_mnpi src
LEFT JOIN (
    SELECT hk_portfolio_company, load_dts, hash_diff
    FROM raw_vault.sat_portco_valuation_mnpi
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_portfolio_company ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_portfolio_company = src.hk_portfolio_company
WHERE src.hk_portfolio_company NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND (
      cur.hk_portfolio_company IS NULL
      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)
  );

INSERT INTO raw_vault.sat_fund_investran_esg (
    hk_fund, load_dts, rec_src, hash_diff, ilpa_esg_rating
)
SELECT
    src.hk_fund,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.ilpa_esg_rating
FROM staging.stg_sat_fund_investran_esg src
LEFT JOIN (
    SELECT hk_fund, load_dts, hash_diff
    FROM raw_vault.sat_fund_investran_esg
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_fund ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_fund = src.hk_fund
WHERE src.hk_fund NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND (
      cur.hk_fund IS NULL
      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)
  );

INSERT INTO raw_vault.sat_limited_partner_investran (
    hk_limited_partner, load_dts, rec_src, hash_diff, lei, lp_legal_name, domicile_country, tier_status
)
SELECT
    src.hk_limited_partner,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.lei, src.lp_legal_name, src.domicile_country, src.tier_status
FROM staging.stg_sat_limited_partner_investran src
LEFT JOIN (
    SELECT hk_limited_partner, load_dts, hash_diff
    FROM raw_vault.sat_limited_partner_investran
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_limited_partner ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_limited_partner = src.hk_limited_partner
WHERE src.hk_limited_partner NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND (
      cur.hk_limited_partner IS NULL
      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)
  );

INSERT INTO raw_vault.sat_limited_partner_legacy (
    hk_limited_partner, load_dts, rec_src, hash_diff, legacy_investor_name, contact_person, phone_number
)
SELECT
    src.hk_limited_partner,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.legacy_investor_name, src.contact_person, src.phone_number
FROM staging.stg_sat_limited_partner_legacy src
LEFT JOIN (
    SELECT hk_limited_partner, load_dts, hash_diff
    FROM raw_vault.sat_limited_partner_legacy
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_limited_partner ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_limited_partner = src.hk_limited_partner
WHERE src.hk_limited_partner NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND (
      cur.hk_limited_partner IS NULL
      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)
  );

INSERT INTO raw_vault.sat_document_manifest (
    hk_document, load_dts, rec_src, hash_diff, content_sha256, mime_type, byte_size
)
SELECT
    src.hk_document,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.content_sha256, src.mime_type, src.byte_size
FROM staging.stg_sat_document_manifest src
LEFT JOIN (
    SELECT hk_document, load_dts, hash_diff
    FROM raw_vault.sat_document_manifest
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_document ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_document = src.hk_document
WHERE src.hk_document NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND (
      cur.hk_document IS NULL
      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)
  );

INSERT INTO business_vault.sat_lp_side_letter_llm_ext (
    hk_lnk_document_lp_commitment, load_dts, rec_src, hash_diff, fee_cap_bps, headline_fee_bps, mfn_election_flag, co_invest_rofr, effective_date, extractor_model_id, confidence_score, chunk_id, evidence_quote
)
SELECT
    src.hk_lnk_document_lp_commitment,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.fee_cap_bps, src.headline_fee_bps, src.mfn_election_flag, src.co_invest_rofr, src.effective_date, src.extractor_model_id, src.confidence_score, src.chunk_id, src.evidence_quote
FROM staging.stg_sat_lp_side_letter_llm_ext src
LEFT JOIN (
    SELECT hk_lnk_document_lp_commitment, load_dts, hash_diff
    FROM business_vault.sat_lp_side_letter_llm_ext
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_lnk_document_lp_commitment ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_lnk_document_lp_commitment = src.hk_lnk_document_lp_commitment
WHERE src.hk_lnk_document_lp_commitment NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND EXISTS (
      SELECT 1 FROM raw_vault.lnk_document_lp_commitment pl
      WHERE pl.hk_lnk_document_lp_commitment = src.hk_lnk_document_lp_commitment
  )
  AND src.steward_gate_status = 'APPROVED'
  AND (
      cur.hk_lnk_document_lp_commitment IS NULL
      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)
  );

INSERT INTO business_vault.sat_lnk_doc_portco_ai_provenance (
    hk_lnk_document_portfolio_company, load_dts, rec_src, hash_diff, asc_820_level, ev_ebitda_multiple, implied_ev_usd, fair_value_usd, effective_date, extractor_model_id, confidence_score, chunk_id, evidence_quote
)
SELECT
    src.hk_lnk_document_portfolio_company,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.asc_820_level, src.ev_ebitda_multiple, src.implied_ev_usd, src.fair_value_usd, src.effective_date, src.extractor_model_id, src.confidence_score, src.chunk_id, src.evidence_quote
FROM staging.stg_sat_lnk_doc_portco_ai_provenance src
LEFT JOIN (
    SELECT hk_lnk_document_portfolio_company, load_dts, hash_diff
    FROM business_vault.sat_lnk_doc_portco_ai_provenance
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_lnk_document_portfolio_company ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_lnk_document_portfolio_company = src.hk_lnk_document_portfolio_company
WHERE src.hk_lnk_document_portfolio_company NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND EXISTS (
      SELECT 1 FROM raw_vault.lnk_document_portfolio_company pl
      WHERE pl.hk_lnk_document_portfolio_company = src.hk_lnk_document_portfolio_company
  )
  AND src.steward_gate_status = 'APPROVED'
  AND (
      cur.hk_lnk_document_portfolio_company IS NULL
      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)
  );

-- =============================================================================

-- ZONE 3: BUSINESS VAULT & POINT-IN-TIME (PIT) HELPER TABLES

-- =============================================================================

CREATE TABLE IF NOT EXISTS business_vault.snap_logarithmic_calendar (
    as_of_dts TIMESTAMP NOT NULL PRIMARY KEY,
    window_tier VARCHAR NOT NULL
);

INSERT INTO business_vault.snap_logarithmic_calendar
SELECT * FROM (
    VALUES
        (TIMESTAMP '2026-10-01 12:00:00', 'DAILY_ROLLING_30D'),
        (TIMESTAMP '2026-10-15 12:00:00', 'DAILY_ROLLING_30D'),
        (TIMESTAMP '2026-11-15 23:59:59', 'QUARTER_END_PERMANENT')
) v(as_of_dts, window_tier)
WHERE NOT EXISTS (
    SELECT 1 FROM business_vault.snap_logarithmic_calendar c
    WHERE c.as_of_dts = v.as_of_dts
);

CREATE OR REPLACE VIEW information_mart.snap_logarithmic_calendar AS
SELECT * FROM business_vault.snap_logarithmic_calendar;

CREATE TABLE IF NOT EXISTS business_vault.pit_portfolio_company (
    hk_pit_portfolio_company CHAR(64) NOT NULL PRIMARY KEY,
    hk_portfolio_company CHAR(64) NOT NULL,
    as_of_dts TIMESTAMP NOT NULL,
    sat_portfolio_company_dealcloud_hk CHAR(64) NOT NULL,
    sat_portfolio_company_dealcloud_load_dts TIMESTAMP NOT NULL,
    sat_portco_valuation_mnpi_hk CHAR(64) NOT NULL,
    sat_portco_valuation_mnpi_load_dts TIMESTAMP NOT NULL
);

INSERT INTO business_vault.pit_portfolio_company (hk_pit_portfolio_company, hk_portfolio_company, as_of_dts, sat_portfolio_company_dealcloud_hk, sat_portfolio_company_dealcloud_load_dts, sat_portco_valuation_mnpi_hk, sat_portco_valuation_mnpi_load_dts)
SELECT src.hk_pit_portfolio_company, src.hk_portfolio_company, src.as_of_dts, src.sat_portfolio_company_dealcloud_hk, src.sat_portfolio_company_dealcloud_load_dts, src.sat_portco_valuation_mnpi_hk, src.sat_portco_valuation_mnpi_load_dts
FROM (
    SELECT
        LOWER(SHA256(
            '64:' || h.hk_portfolio_company || '||'
            || CAST(OCTET_LENGTH(ENCODE(CAST(cal.as_of_dts AS VARCHAR))) AS VARCHAR)
            || ':' || CAST(cal.as_of_dts AS VARCHAR)
        )) AS hk_pit_portfolio_company,
        h.hk_portfolio_company,
        cal.as_of_dts AS as_of_dts,
        COALESCE(s0.hk_portfolio_company, '0000000000000000000000000000000000000000000000000000000000000000') AS sat_portfolio_company_dealcloud_hk,
        COALESCE(s0.load_dts, TIMESTAMP '1900-01-01 00:00:00') AS sat_portfolio_company_dealcloud_load_dts,
        COALESCE(s1.hk_portfolio_company, '0000000000000000000000000000000000000000000000000000000000000000') AS sat_portco_valuation_mnpi_hk,
        COALESCE(s1.load_dts, TIMESTAMP '1900-01-01 00:00:00') AS sat_portco_valuation_mnpi_load_dts
    FROM raw_vault.hub_portfolio_company h
    CROSS JOIN business_vault.snap_logarithmic_calendar cal
    ASOF LEFT JOIN raw_vault.sat_portfolio_company_dealcloud s0
        ON s0.hk_portfolio_company = h.hk_portfolio_company
       AND s0.load_dts <= cal.as_of_dts
    ASOF LEFT JOIN raw_vault.sat_portco_valuation_mnpi s1
        ON s1.hk_portfolio_company = h.hk_portfolio_company
       AND s1.load_dts <= cal.as_of_dts
    WHERE h.hk_portfolio_company NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
) src
WHERE NOT EXISTS (
    SELECT 1 FROM business_vault.pit_portfolio_company p
    WHERE p.hk_pit_portfolio_company = src.hk_pit_portfolio_company
);

CREATE OR REPLACE VIEW information_mart.pit_portfolio_company AS
SELECT * FROM business_vault.pit_portfolio_company;

CREATE OR REPLACE VIEW information_mart.vw_pit_portfolio_company_current AS
SELECT
    pit.as_of_dts,
    h.hk_portfolio_company,
    h.bkcc,
    h.portco_natural_key,
    s0.deal_code AS deal_code,
    s0.company_name AS company_name,
    s0.gp_deal_partner AS gp_deal_partner,
    s1.entry_ebitda_usd AS entry_ebitda_usd,
    s1.entry_ev_multiple AS entry_ev_multiple
FROM business_vault.pit_portfolio_company pit
INNER JOIN raw_vault.hub_portfolio_company h ON h.hk_portfolio_company = pit.hk_portfolio_company
INNER JOIN raw_vault.sat_portfolio_company_dealcloud s0
    ON s0.hk_portfolio_company = pit.sat_portfolio_company_dealcloud_hk
   AND s0.load_dts = pit.sat_portfolio_company_dealcloud_load_dts
INNER JOIN raw_vault.sat_portco_valuation_mnpi s1
    ON s1.hk_portfolio_company = pit.sat_portco_valuation_mnpi_hk
   AND s1.load_dts = pit.sat_portco_valuation_mnpi_load_dts;

-- ZONE 4: INFORMATION MART CURRENT VIEWS (INNER JOIN OVER PIT + GHOST)

CREATE OR REPLACE VIEW information_mart.vw_pit_portfolio_company_current AS
SELECT
    pit.as_of_dts,
    h.hk_portfolio_company,
    h.bkcc,
    h.portco_natural_key,
    s0.deal_code AS deal_code,
    s0.company_name AS company_name,
    s0.gp_deal_partner AS gp_deal_partner,
    s1.entry_ebitda_usd AS entry_ebitda_usd,
    s1.entry_ev_multiple AS entry_ev_multiple
FROM business_vault.pit_portfolio_company pit
INNER JOIN raw_vault.hub_portfolio_company h ON h.hk_portfolio_company = pit.hk_portfolio_company
INNER JOIN raw_vault.sat_portfolio_company_dealcloud s0
    ON s0.hk_portfolio_company = pit.sat_portfolio_company_dealcloud_hk
   AND s0.load_dts = pit.sat_portfolio_company_dealcloud_load_dts
INNER JOIN raw_vault.sat_portco_valuation_mnpi s1
    ON s1.hk_portfolio_company = pit.sat_portco_valuation_mnpi_hk
   AND s1.load_dts = pit.sat_portco_valuation_mnpi_load_dts;

SELECT * FROM information_mart.vw_pit_portfolio_company_current;

-- ZONE 4: BI-TEMPORAL POINT-IN-TIME AUDIT QUERY
SELECT *
FROM information_mart.vw_pit_portfolio_company_current
WHERE as_of_dts = TIMESTAMP '2026-11-15 23:59:59';
