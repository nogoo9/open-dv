-- =============================================================================

-- ZONE 0: TARGET SCHEMAS, BRONZE SOURCE SEEDS, VAULT DDL & GHOST/ZERO RECORDS

-- =============================================================================

CREATE SCHEMA IF NOT EXISTS bronze;

CREATE SCHEMA IF NOT EXISTS staging;

CREATE SCHEMA IF NOT EXISTS raw_vault;

CREATE SCHEMA IF NOT EXISTS business_vault;

CREATE SCHEMA IF NOT EXISTS information_mart;

CREATE OR REPLACE TABLE bronze.src_hub_holding (
    bkcc VARCHAR,
    holding_id VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_hub_holding VALUES
    ('AM_SEC_TREASURY_IBOR_HOLDINGS_13F', 'HOLDING-001', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F'),
    ('AM_SEC_TREASURY_IBOR_HOLDINGS_13F', 'HOLDING-002', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F');

CREATE OR REPLACE TABLE bronze.src_hub_filer (
    bkcc VARCHAR,
    filer_cik VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_hub_filer VALUES
    ('AM_SEC_TREASURY_IBOR_HOLDINGS_13F', 'FILER-001', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F'),
    ('AM_SEC_TREASURY_IBOR_INSTITUTIONAL_FILERS', 'FILER-002', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_INSTITUTIONAL_FILERS'),
    ('AM_SEC_TREASURY_IBOR_HOLDINGS_13F', 'FILER-002', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F'),
    ('AM_SEC_TREASURY_IBOR_INSTITUTIONAL_FILERS', 'FILER-001', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_INSTITUTIONAL_FILERS');

CREATE OR REPLACE TABLE bronze.src_hub_securities (
    bkcc VARCHAR,
    ticker VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_hub_securities VALUES
    ('AM_SEC_TREASURY_IBOR_HOLDINGS_13F', 'SECURITIES-001', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F'),
    ('AM_SEC_TREASURY_IBOR_SECURITIES', 'SECURITIES-002', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_SECURITIES'),
    ('AM_SEC_TREASURY_IBOR_HOLDINGS_13F', 'SECURITIES-002', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F'),
    ('AM_SEC_TREASURY_IBOR_SECURITIES', 'SECURITIES-001', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_SECURITIES');

CREATE OR REPLACE TABLE bronze.src_hub_issuer (
    bkcc VARCHAR,
    cik VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_hub_issuer VALUES
    ('AM_SEC_TREASURY_IBOR_ISSUERS', 'ISSUER-001', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_ISSUERS'),
    ('AM_SEC_TREASURY_IBOR_ISSUERS', 'ISSUER-002', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_ISSUERS');

CREATE OR REPLACE TABLE bronze.src_hub_rate (
    bkcc VARCHAR,
    observation_date VARCHAR,
    series_id VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_hub_rate VALUES
    ('AM_SEC_TREASURY_IBOR_TREASURY_RATES', 'RATE-001', 'RATE-001', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_TREASURY_RATES'),
    ('AM_SEC_TREASURY_IBOR_TREASURY_RATES', 'RATE-002', 'RATE-002', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_TREASURY_RATES');

CREATE OR REPLACE TABLE bronze.src_hub_yield_curve (
    bkcc VARCHAR,
    curve_id VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_hub_yield_curve VALUES
    ('AM_SEC_TREASURY_IBOR_YIELD_CURVES', 'YIELD_CURVE-001', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_YIELD_CURVES'),
    ('AM_SEC_TREASURY_IBOR_YIELD_CURVES', 'YIELD_CURVE-002', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_YIELD_CURVES');

CREATE OR REPLACE TABLE bronze.src_hub_custody_account (
    bkcc VARCHAR,
    holding_id VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_hub_custody_account VALUES
    ('DATABRICKS_CUSTODY_HOLDINGS', 'CUSTODY_ACCOUNT-001', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_CUSTODY_HOLDINGS'),
    ('DATABRICKS_CUSTODY_HOLDINGS', 'CUSTODY_ACCOUNT-002', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_CUSTODY_HOLDINGS');

CREATE OR REPLACE TABLE bronze.src_hub_security (
    bkcc VARCHAR,
    cusip VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_hub_security VALUES
    ('DATABRICKS_CUSTODY_HOLDINGS', 'SECURITY-001', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_CUSTODY_HOLDINGS'),
    ('DATABRICKS_SECURITY_MASTER', 'SECURITY-002', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_SECURITY_MASTER'),
    ('DATABRICKS_CUSTODY_HOLDINGS', 'SECURITY-002', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_CUSTODY_HOLDINGS'),
    ('DATABRICKS_SECURITY_MASTER', 'SECURITY-001', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_SECURITY_MASTER');

CREATE OR REPLACE TABLE bronze.src_hub_position (
    bkcc VARCHAR,
    position_id VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_hub_position VALUES
    ('DATABRICKS_POSITIONS', 'POSITION-001', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_POSITIONS'),
    ('DATABRICKS_POSITIONS_SNAPSHOT', 'POSITION-002', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_POSITIONS_SNAPSHOT'),
    ('DATABRICKS_POSITIONS', 'POSITION-002', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_POSITIONS'),
    ('DATABRICKS_POSITIONS_SNAPSHOT', 'POSITION-001', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_POSITIONS_SNAPSHOT');

CREATE OR REPLACE TABLE bronze.am_sec_treasury_ibor_holdings_13f (
    holding_id VARCHAR,
    filer_cik VARCHAR,
    ticker VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.am_sec_treasury_ibor_holdings_13f VALUES
    ('HOLDING-001', 'FILER-001', 'SECURITIES-001', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F'),
    ('HOLDING-002', 'FILER-002', 'SECURITIES-002', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F'),
    ('HOLDING-001', 'FILER-001', 'SECURITIES-001', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F'),
    ('HOLDING-002', 'FILER-002', 'SECURITIES-002', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F');

CREATE OR REPLACE VIEW bronze.am_sec_treasury_ibor_institutional_filers AS SELECT * FROM bronze.src_hub_filer;

CREATE OR REPLACE VIEW bronze.am_sec_treasury_ibor_securities AS SELECT * FROM bronze.src_hub_securities;

CREATE OR REPLACE VIEW bronze.am_sec_treasury_ibor_issuers AS SELECT * FROM bronze.src_hub_issuer;

CREATE OR REPLACE VIEW bronze.am_sec_treasury_ibor_treasury_rates AS SELECT * FROM bronze.src_hub_rate;

CREATE OR REPLACE VIEW bronze.am_sec_treasury_ibor_yield_curves AS SELECT * FROM bronze.src_hub_yield_curve;

CREATE OR REPLACE TABLE bronze.databricks_custody_holdings (
    holding_id VARCHAR,
    cusip VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.databricks_custody_holdings VALUES
    ('CUSTODY_ACCOUNT-001', 'SECURITY-001', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_CUSTODY_HOLDINGS'),
    ('CUSTODY_ACCOUNT-002', 'SECURITY-002', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_CUSTODY_HOLDINGS'),
    ('CUSTODY_ACCOUNT-001', 'SECURITY-001', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_CUSTODY_HOLDINGS'),
    ('CUSTODY_ACCOUNT-002', 'SECURITY-002', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_CUSTODY_HOLDINGS');

CREATE OR REPLACE VIEW bronze.databricks_security_master AS SELECT * FROM bronze.src_hub_security;

CREATE OR REPLACE VIEW bronze.databricks_positions AS SELECT * FROM bronze.src_hub_position;

CREATE OR REPLACE VIEW bronze.databricks_positions_snapshot AS SELECT * FROM bronze.src_hub_position;

CREATE OR REPLACE TABLE bronze.src_lnk_holding_filer (
    hk_holding__bkcc VARCHAR,
    hk_holding__holding_id VARCHAR,
    hk_filer__bkcc VARCHAR,
    hk_filer__filer_cik VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_lnk_holding_filer VALUES
    ('AM_SEC_TREASURY_IBOR_HOLDINGS_13F', 'HOLDING-001', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F', 'FILER-001', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F'),
    ('AM_SEC_TREASURY_IBOR_HOLDINGS_13F', 'HOLDING-002', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F', NULL, TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F'),
    ('AM_SEC_TREASURY_IBOR_HOLDINGS_13F', 'HOLDING-001', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F', '-1', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F'),
    ('AM_SEC_TREASURY_IBOR_HOLDINGS_13F', 'HOLDING-002', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F', '-2', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F');

CREATE OR REPLACE TABLE bronze.src_lnk_holding_securities (
    hk_holding__bkcc VARCHAR,
    hk_holding__holding_id VARCHAR,
    hk_securities__bkcc VARCHAR,
    hk_securities__ticker VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_lnk_holding_securities VALUES
    ('AM_SEC_TREASURY_IBOR_HOLDINGS_13F', 'HOLDING-001', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F', 'SECURITIES-001', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F'),
    ('AM_SEC_TREASURY_IBOR_HOLDINGS_13F', 'HOLDING-002', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F', NULL, TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F'),
    ('AM_SEC_TREASURY_IBOR_HOLDINGS_13F', 'HOLDING-001', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F', '-1', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F'),
    ('AM_SEC_TREASURY_IBOR_HOLDINGS_13F', 'HOLDING-002', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F', '-2', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F');

CREATE OR REPLACE TABLE bronze.src_lnk_securities_issuer (
    hk_securities__bkcc VARCHAR,
    hk_securities__ticker VARCHAR,
    hk_issuer__bkcc VARCHAR,
    hk_issuer__cik VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_lnk_securities_issuer VALUES
    ('AM_SEC_TREASURY_IBOR_SECURITIES', 'SECURITIES-002', 'AM_SEC_TREASURY_IBOR_ISSUERS', 'ISSUER-001', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_SECURITIES'),
    ('AM_SEC_TREASURY_IBOR_SECURITIES', 'SECURITIES-001', 'AM_SEC_TREASURY_IBOR_ISSUERS', NULL, TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_SECURITIES'),
    ('AM_SEC_TREASURY_IBOR_SECURITIES', 'SECURITIES-002', 'AM_SEC_TREASURY_IBOR_ISSUERS', '-1', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_SECURITIES'),
    ('AM_SEC_TREASURY_IBOR_SECURITIES', 'SECURITIES-001', 'AM_SEC_TREASURY_IBOR_ISSUERS', '-2', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_SECURITIES');

CREATE OR REPLACE TABLE bronze.src_lnk_custody_account_security (
    hk_custody_account__bkcc VARCHAR,
    hk_custody_account__holding_id VARCHAR,
    hk_security__bkcc VARCHAR,
    hk_security__cusip VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_lnk_custody_account_security VALUES
    ('DATABRICKS_CUSTODY_HOLDINGS', 'CUSTODY_ACCOUNT-001', 'DATABRICKS_CUSTODY_HOLDINGS', 'SECURITY-001', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_CUSTODY_HOLDINGS'),
    ('DATABRICKS_CUSTODY_HOLDINGS', 'CUSTODY_ACCOUNT-002', 'DATABRICKS_CUSTODY_HOLDINGS', NULL, TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_CUSTODY_HOLDINGS'),
    ('DATABRICKS_CUSTODY_HOLDINGS', 'CUSTODY_ACCOUNT-001', 'DATABRICKS_CUSTODY_HOLDINGS', '-1', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_CUSTODY_HOLDINGS'),
    ('DATABRICKS_CUSTODY_HOLDINGS', 'CUSTODY_ACCOUNT-002', 'DATABRICKS_CUSTODY_HOLDINGS', '-2', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_CUSTODY_HOLDINGS');

CREATE OR REPLACE TABLE bronze.src_lnk_position_security (
    hk_position__bkcc VARCHAR,
    hk_position__position_id VARCHAR,
    hk_security__bkcc VARCHAR,
    hk_security__cusip VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_lnk_position_security VALUES
    ('DATABRICKS_POSITIONS', 'POSITION-001', 'DATABRICKS_SECURITY_MASTER', 'SECURITY-002', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_POSITIONS'),
    ('DATABRICKS_POSITIONS', 'POSITION-002', 'DATABRICKS_SECURITY_MASTER', NULL, TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_POSITIONS'),
    ('DATABRICKS_POSITIONS', 'POSITION-001', 'DATABRICKS_SECURITY_MASTER', '-1', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_POSITIONS'),
    ('DATABRICKS_POSITIONS', 'POSITION-002', 'DATABRICKS_SECURITY_MASTER', '-2', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_POSITIONS');

CREATE OR REPLACE TABLE bronze.src_sat_positions (
    parent_bk__bkcc VARCHAR,
    parent_bk__position_id VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    account_id VARCHAR,
    as_of_date DATE,
    market_value VARCHAR,
    quantity VARCHAR,
    synthetic_tax_lot_id VARCHAR
);
INSERT INTO bronze.src_sat_positions VALUES
    ('DATABRICKS_POSITIONS', 'POSITION-001', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_POSITIONS', 'account_id_val_1', DATE '2026-09-30', 'market_value_val_1', 'quantity_val_1', 'synthetic_tax_lot_id_val_1'),
    ('DATABRICKS_POSITIONS', 'POSITION-002', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_POSITIONS', 'account_id_val_2', DATE '2026-09-30', 'market_value_val_2', 'quantity_val_2', 'synthetic_tax_lot_id_val_2');

CREATE OR REPLACE TABLE bronze.src_sat_positions_pii (
    parent_bk__bkcc VARCHAR,
    parent_bk__position_id VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    custodian_account_no VARCHAR
);
INSERT INTO bronze.src_sat_positions_pii VALUES
    ('DATABRICKS_POSITIONS', 'POSITION-001', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_POSITIONS', 'custodian_account_no_val_1'),
    ('DATABRICKS_POSITIONS', 'POSITION-002', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_POSITIONS', 'custodian_account_no_val_2');

CREATE OR REPLACE TABLE bronze.src_sat_positions_snapshot (
    parent_bk__bkcc VARCHAR,
    parent_bk__position_id VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    unrealized_pnl VARCHAR
);
INSERT INTO bronze.src_sat_positions_snapshot VALUES
    ('DATABRICKS_POSITIONS_SNAPSHOT', 'POSITION-002', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_POSITIONS_SNAPSHOT', 'unrealized_pnl_val_1'),
    ('DATABRICKS_POSITIONS_SNAPSHOT', 'POSITION-001', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_POSITIONS_SNAPSHOT', 'unrealized_pnl_val_2');

CREATE OR REPLACE TABLE bronze.src_sat_holdings_13f (
    parent_bk__bkcc VARCHAR,
    parent_bk__holding_id VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    cusip VARCHAR,
    investment_discretion VARCHAR,
    issuer_name VARCHAR,
    market_value_usd DECIMAL(18,2),
    shares VARCHAR
);
INSERT INTO bronze.src_sat_holdings_13f VALUES
    ('AM_SEC_TREASURY_IBOR_HOLDINGS_13F', 'HOLDING-001', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F', 'cusip_val_1', 'investment_discretion_val_1', 'issuer_name_val_1', 1000.50, 'shares_val_1'),
    ('AM_SEC_TREASURY_IBOR_HOLDINGS_13F', 'HOLDING-002', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F', 'cusip_val_2', 'investment_discretion_val_2', 'issuer_name_val_2', 2000.50, 'shares_val_2');

CREATE OR REPLACE TABLE bronze.src_sat_institutional_filers (
    parent_bk__bkcc VARCHAR,
    parent_bk__filer_cik VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    filer_name VARCHAR
);
INSERT INTO bronze.src_sat_institutional_filers VALUES
    ('AM_SEC_TREASURY_IBOR_INSTITUTIONAL_FILERS', 'FILER-002', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_INSTITUTIONAL_FILERS', 'filer_name_val_1'),
    ('AM_SEC_TREASURY_IBOR_INSTITUTIONAL_FILERS', 'FILER-001', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_INSTITUTIONAL_FILERS', 'filer_name_val_2');

CREATE OR REPLACE TABLE bronze.src_sat_issuers (
    parent_bk__bkcc VARCHAR,
    parent_bk__cik VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    company_name VARCHAR,
    exchange VARCHAR,
    ticker VARCHAR
);
INSERT INTO bronze.src_sat_issuers VALUES
    ('AM_SEC_TREASURY_IBOR_ISSUERS', 'ISSUER-001', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_ISSUERS', 'Nordic Cloud Infrastructure AB', 'exchange_val_1', 'ticker_val_1'),
    ('AM_SEC_TREASURY_IBOR_ISSUERS', 'ISSUER-002', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_ISSUERS', 'company_name_val_2', 'exchange_val_2', 'ticker_val_2');

CREATE OR REPLACE TABLE bronze.src_sat_securities (
    parent_bk__bkcc VARCHAR,
    parent_bk__ticker VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    cusip VARCHAR,
    security_type VARCHAR
);
INSERT INTO bronze.src_sat_securities VALUES
    ('AM_SEC_TREASURY_IBOR_SECURITIES', 'SECURITIES-002', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_SECURITIES', 'cusip_val_1', 'security_type_val_1'),
    ('AM_SEC_TREASURY_IBOR_SECURITIES', 'SECURITIES-001', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_SECURITIES', 'cusip_val_2', 'security_type_val_2');

CREATE OR REPLACE TABLE bronze.src_sat_security_master (
    parent_bk__bkcc VARCHAR,
    parent_bk__cusip VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    asset_class VARCHAR,
    currency VARCHAR,
    cusip VARCHAR,
    isin VARCHAR,
    security_name VARCHAR,
    sedol VARCHAR,
    ticker VARCHAR
);
INSERT INTO bronze.src_sat_security_master VALUES
    ('DATABRICKS_SECURITY_MASTER', 'SECURITY-002', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_SECURITY_MASTER', 'asset_class_val_1', 'currency_val_1', 'cusip_val_1', 'isin_val_1', 'security_name_val_1', 'sedol_val_1', 'ticker_val_1'),
    ('DATABRICKS_SECURITY_MASTER', 'SECURITY-001', TIMESTAMP '2026-10-01 06:00:00', 'DATABRICKS_SECURITY_MASTER', 'asset_class_val_2', 'currency_val_2', 'cusip_val_2', 'isin_val_2', 'security_name_val_2', 'sedol_val_2', 'ticker_val_2');

CREATE OR REPLACE TABLE bronze.src_sat_treasury_rates (
    parent_bk__bkcc VARCHAR,
    parent_bk__observation_date VARCHAR,
    parent_bk__series_id VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    yield_pct DECIMAL(10,4)
);
INSERT INTO bronze.src_sat_treasury_rates VALUES
    ('AM_SEC_TREASURY_IBOR_TREASURY_RATES', 'RATE-001', 'RATE-001', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_TREASURY_RATES', 1000.50),
    ('AM_SEC_TREASURY_IBOR_TREASURY_RATES', 'RATE-002', 'RATE-002', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_TREASURY_RATES', 2000.50);

CREATE OR REPLACE TABLE bronze.src_sat_yield_curves (
    parent_bk__bkcc VARCHAR,
    parent_bk__curve_id VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    observation_date DATE,
    rate_10y VARCHAR,
    rate_2y VARCHAR,
    rate_sofr VARCHAR,
    spread_10y_2y VARCHAR
);
INSERT INTO bronze.src_sat_yield_curves VALUES
    ('AM_SEC_TREASURY_IBOR_YIELD_CURVES', 'YIELD_CURVE-001', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_YIELD_CURVES', DATE '2026-09-30', 'rate_10y_val_1', 'rate_2y_val_1', 'rate_sofr_val_1', 'spread_10y_2y_val_1'),
    ('AM_SEC_TREASURY_IBOR_YIELD_CURVES', 'YIELD_CURVE-002', TIMESTAMP '2026-10-01 06:00:00', 'AM_SEC_TREASURY_IBOR_YIELD_CURVES', DATE '2026-09-30', 'rate_10y_val_2', 'rate_2y_val_2', 'rate_sofr_val_2', 'spread_10y_2y_val_2');

CREATE TABLE IF NOT EXISTS raw_vault.hub_holding (
    hk_holding CHAR(64) NOT NULL PRIMARY KEY,
    bkcc VARCHAR NOT NULL,
    holding_id VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.hub_holding VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', 'SYSTEM', '-1', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'SYSTEM', '-2', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.hub_filer (
    hk_filer CHAR(64) NOT NULL PRIMARY KEY,
    bkcc VARCHAR NOT NULL,
    filer_cik VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.hub_filer VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', 'SYSTEM', '-1', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'SYSTEM', '-2', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.hub_securities (
    hk_securities CHAR(64) NOT NULL PRIMARY KEY,
    bkcc VARCHAR NOT NULL,
    ticker VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.hub_securities VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', 'SYSTEM', '-1', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'SYSTEM', '-2', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.hub_issuer (
    hk_issuer CHAR(64) NOT NULL PRIMARY KEY,
    bkcc VARCHAR NOT NULL,
    cik VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.hub_issuer VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', 'SYSTEM', '-1', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'SYSTEM', '-2', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.hub_rate (
    hk_rate CHAR(64) NOT NULL PRIMARY KEY,
    bkcc VARCHAR NOT NULL,
    observation_date VARCHAR NOT NULL,
    series_id VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.hub_rate VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', 'SYSTEM', '-1', '-1', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'SYSTEM', '-2', '-2', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.hub_yield_curve (
    hk_yield_curve CHAR(64) NOT NULL PRIMARY KEY,
    bkcc VARCHAR NOT NULL,
    curve_id VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.hub_yield_curve VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', 'SYSTEM', '-1', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'SYSTEM', '-2', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.hub_custody_account (
    hk_custody_account CHAR(64) NOT NULL PRIMARY KEY,
    bkcc VARCHAR NOT NULL,
    holding_id VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.hub_custody_account VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', 'SYSTEM', '-1', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'SYSTEM', '-2', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.hub_security (
    hk_security CHAR(64) NOT NULL PRIMARY KEY,
    bkcc VARCHAR NOT NULL,
    cusip VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.hub_security VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', 'SYSTEM', '-1', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'SYSTEM', '-2', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.hub_position (
    hk_position CHAR(64) NOT NULL PRIMARY KEY,
    bkcc VARCHAR NOT NULL,
    position_id VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.hub_position VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', 'SYSTEM', '-1', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'SYSTEM', '-2', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.lnk_holding_filer (
    hk_lnk_holding_filer CHAR(64) NOT NULL PRIMARY KEY,
    hk_holding CHAR(64) NOT NULL,
    hk_filer CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.lnk_holding_filer VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.lnk_holding_securities (
    hk_lnk_holding_securities CHAR(64) NOT NULL PRIMARY KEY,
    hk_holding CHAR(64) NOT NULL,
    hk_securities CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.lnk_holding_securities VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.lnk_securities_issuer (
    hk_lnk_securities_issuer CHAR(64) NOT NULL PRIMARY KEY,
    hk_securities CHAR(64) NOT NULL,
    hk_issuer CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.lnk_securities_issuer VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.lnk_custody_account_security (
    hk_lnk_custody_account_security CHAR(64) NOT NULL PRIMARY KEY,
    hk_custody_account CHAR(64) NOT NULL,
    hk_security CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.lnk_custody_account_security VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.lnk_position_security (
    hk_lnk_position_security CHAR(64) NOT NULL PRIMARY KEY,
    hk_position CHAR(64) NOT NULL,
    hk_security CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.lnk_position_security VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.sat_positions (
    hk_position CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    account_id VARCHAR,
    as_of_date DATE,
    market_value VARCHAR,
    quantity VARCHAR,
    synthetic_tax_lot_id VARCHAR,
    PRIMARY KEY (hk_position, load_dts)
);
INSERT OR IGNORE INTO raw_vault.sat_positions (hk_position, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

CREATE TABLE IF NOT EXISTS raw_vault.sat_positions_pii (
    hk_position CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    custodian_account_no VARCHAR,
    PRIMARY KEY (hk_position, load_dts)
);
INSERT OR IGNORE INTO raw_vault.sat_positions_pii (hk_position, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

CREATE TABLE IF NOT EXISTS raw_vault.sat_positions_snapshot (
    hk_position CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    unrealized_pnl VARCHAR,
    PRIMARY KEY (hk_position, load_dts)
);
INSERT OR IGNORE INTO raw_vault.sat_positions_snapshot (hk_position, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

CREATE TABLE IF NOT EXISTS raw_vault.sat_holdings_13f (
    hk_holding CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    cusip VARCHAR,
    investment_discretion VARCHAR,
    issuer_name VARCHAR,
    market_value_usd DECIMAL(18,2),
    shares VARCHAR,
    PRIMARY KEY (hk_holding, load_dts)
);
INSERT OR IGNORE INTO raw_vault.sat_holdings_13f (hk_holding, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

CREATE TABLE IF NOT EXISTS raw_vault.sat_institutional_filers (
    hk_filer CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    filer_name VARCHAR,
    PRIMARY KEY (hk_filer, load_dts)
);
INSERT OR IGNORE INTO raw_vault.sat_institutional_filers (hk_filer, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

CREATE TABLE IF NOT EXISTS raw_vault.sat_issuers (
    hk_issuer CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    company_name VARCHAR,
    exchange VARCHAR,
    ticker VARCHAR,
    PRIMARY KEY (hk_issuer, load_dts)
);
INSERT OR IGNORE INTO raw_vault.sat_issuers (hk_issuer, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

CREATE TABLE IF NOT EXISTS raw_vault.sat_securities (
    hk_securities CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    cusip VARCHAR,
    security_type VARCHAR,
    PRIMARY KEY (hk_securities, load_dts)
);
INSERT OR IGNORE INTO raw_vault.sat_securities (hk_securities, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

CREATE TABLE IF NOT EXISTS raw_vault.sat_security_master (
    hk_security CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    asset_class VARCHAR,
    currency VARCHAR,
    cusip VARCHAR,
    isin VARCHAR,
    security_name VARCHAR,
    sedol VARCHAR,
    ticker VARCHAR,
    PRIMARY KEY (hk_security, load_dts)
);
INSERT OR IGNORE INTO raw_vault.sat_security_master (hk_security, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

CREATE TABLE IF NOT EXISTS raw_vault.sat_treasury_rates (
    hk_rate CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    yield_pct DECIMAL(10,4),
    PRIMARY KEY (hk_rate, load_dts)
);
INSERT OR IGNORE INTO raw_vault.sat_treasury_rates (hk_rate, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

CREATE TABLE IF NOT EXISTS raw_vault.sat_yield_curves (
    hk_yield_curve CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    observation_date DATE,
    rate_10y VARCHAR,
    rate_2y VARCHAR,
    rate_sofr VARCHAR,
    spread_10y_2y VARCHAR,
    PRIMARY KEY (hk_yield_curve, load_dts)
);
INSERT OR IGNORE INTO raw_vault.sat_yield_curves (hk_yield_curve, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

-- =============================================================================

-- ZONE 1: HASHED STAGING VIEWS (ALGORITHM A.5 LENGTH-PREFIXED SHA-256)

-- =============================================================================

CREATE OR REPLACE VIEW staging.stg_hub_holding AS
SELECT
    CASE WHEN TRIM(CAST(holding_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(holding_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(holding_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('33:AM_SEC_TREASURY_IBOR_HOLDINGS_13F' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(holding_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(holding_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(holding_id AS VARCHAR))), '') END)) END AS hk_holding,
    'AM_SEC_TREASURY_IBOR_HOLDINGS_13F' AS bkcc, holding_id,
    load_dts,
    rec_src
FROM bronze.src_hub_holding;

CREATE OR REPLACE VIEW staging.stg_hub_filer AS
SELECT
    CASE WHEN TRIM(CAST(filer_cik AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(filer_cik AS VARCHAR)), '') IS NULL OR TRIM(CAST(filer_cik AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('33:AM_SEC_TREASURY_IBOR_HOLDINGS_13F' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(filer_cik AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(filer_cik AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(filer_cik AS VARCHAR))), '') END)) END AS hk_filer,
    'AM_SEC_TREASURY_IBOR_HOLDINGS_13F' AS bkcc, filer_cik,
    load_dts,
    rec_src
FROM bronze.src_hub_filer
WHERE UPPER(TRIM(CAST(bkcc AS VARCHAR))) = 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F'
UNION ALL
SELECT
    CASE WHEN TRIM(CAST(filer_cik AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(filer_cik AS VARCHAR)), '') IS NULL OR TRIM(CAST(filer_cik AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('41:AM_SEC_TREASURY_IBOR_INSTITUTIONAL_FILERS' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(filer_cik AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(filer_cik AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(filer_cik AS VARCHAR))), '') END)) END AS hk_filer,
    'AM_SEC_TREASURY_IBOR_INSTITUTIONAL_FILERS' AS bkcc, filer_cik,
    load_dts,
    rec_src
FROM bronze.src_hub_filer
WHERE UPPER(TRIM(CAST(bkcc AS VARCHAR))) = 'AM_SEC_TREASURY_IBOR_INSTITUTIONAL_FILERS';

CREATE OR REPLACE VIEW staging.stg_hub_securities AS
SELECT
    CASE WHEN TRIM(CAST(ticker AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(ticker AS VARCHAR)), '') IS NULL OR TRIM(CAST(ticker AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('33:AM_SEC_TREASURY_IBOR_HOLDINGS_13F' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(ticker AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(ticker AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(ticker AS VARCHAR))), '') END)) END AS hk_securities,
    'AM_SEC_TREASURY_IBOR_HOLDINGS_13F' AS bkcc, ticker,
    load_dts,
    rec_src
FROM bronze.src_hub_securities
WHERE UPPER(TRIM(CAST(bkcc AS VARCHAR))) = 'AM_SEC_TREASURY_IBOR_HOLDINGS_13F'
UNION ALL
SELECT
    CASE WHEN TRIM(CAST(ticker AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(ticker AS VARCHAR)), '') IS NULL OR TRIM(CAST(ticker AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('31:AM_SEC_TREASURY_IBOR_SECURITIES' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(ticker AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(ticker AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(ticker AS VARCHAR))), '') END)) END AS hk_securities,
    'AM_SEC_TREASURY_IBOR_SECURITIES' AS bkcc, ticker,
    load_dts,
    rec_src
FROM bronze.src_hub_securities
WHERE UPPER(TRIM(CAST(bkcc AS VARCHAR))) = 'AM_SEC_TREASURY_IBOR_SECURITIES';

CREATE OR REPLACE VIEW staging.stg_hub_issuer AS
SELECT
    CASE WHEN TRIM(CAST(cik AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(cik AS VARCHAR)), '') IS NULL OR TRIM(CAST(cik AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('28:AM_SEC_TREASURY_IBOR_ISSUERS' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(cik AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(cik AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(cik AS VARCHAR))), '') END)) END AS hk_issuer,
    'AM_SEC_TREASURY_IBOR_ISSUERS' AS bkcc, cik,
    load_dts,
    rec_src
FROM bronze.src_hub_issuer;

CREATE OR REPLACE VIEW staging.stg_hub_rate AS
SELECT
    CASE WHEN TRIM(CAST(observation_date AS VARCHAR)) = '-2' OR TRIM(CAST(series_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(observation_date AS VARCHAR)), '') IS NULL OR TRIM(CAST(observation_date AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(series_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(series_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('35:AM_SEC_TREASURY_IBOR_TREASURY_RATES' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(observation_date AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(observation_date AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(observation_date AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(series_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(series_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(series_id AS VARCHAR))), '') END)) END AS hk_rate,
    'AM_SEC_TREASURY_IBOR_TREASURY_RATES' AS bkcc, observation_date, series_id,
    load_dts,
    rec_src
FROM bronze.src_hub_rate;

CREATE OR REPLACE VIEW staging.stg_hub_yield_curve AS
SELECT
    CASE WHEN TRIM(CAST(curve_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(curve_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(curve_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('33:AM_SEC_TREASURY_IBOR_YIELD_CURVES' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(curve_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(curve_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(curve_id AS VARCHAR))), '') END)) END AS hk_yield_curve,
    'AM_SEC_TREASURY_IBOR_YIELD_CURVES' AS bkcc, curve_id,
    load_dts,
    rec_src
FROM bronze.src_hub_yield_curve;

CREATE OR REPLACE VIEW staging.stg_hub_custody_account AS
SELECT
    CASE WHEN TRIM(CAST(holding_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(holding_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(holding_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('27:DATABRICKS_CUSTODY_HOLDINGS' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(holding_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(holding_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(holding_id AS VARCHAR))), '') END)) END AS hk_custody_account,
    'DATABRICKS_CUSTODY_HOLDINGS' AS bkcc, holding_id,
    load_dts,
    rec_src
FROM bronze.src_hub_custody_account;

CREATE OR REPLACE VIEW staging.stg_hub_security AS
SELECT
    CASE WHEN TRIM(CAST(cusip AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(cusip AS VARCHAR)), '') IS NULL OR TRIM(CAST(cusip AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('27:DATABRICKS_CUSTODY_HOLDINGS' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(cusip AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(cusip AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(cusip AS VARCHAR))), '') END)) END AS hk_security,
    'DATABRICKS_CUSTODY_HOLDINGS' AS bkcc, cusip,
    load_dts,
    rec_src
FROM bronze.src_hub_security
WHERE UPPER(TRIM(CAST(bkcc AS VARCHAR))) = 'DATABRICKS_CUSTODY_HOLDINGS'
UNION ALL
SELECT
    CASE WHEN TRIM(CAST(cusip AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(cusip AS VARCHAR)), '') IS NULL OR TRIM(CAST(cusip AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('26:DATABRICKS_SECURITY_MASTER' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(cusip AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(cusip AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(cusip AS VARCHAR))), '') END)) END AS hk_security,
    'DATABRICKS_SECURITY_MASTER' AS bkcc, cusip,
    load_dts,
    rec_src
FROM bronze.src_hub_security
WHERE UPPER(TRIM(CAST(bkcc AS VARCHAR))) = 'DATABRICKS_SECURITY_MASTER';

CREATE OR REPLACE VIEW staging.stg_hub_position AS
SELECT
    CASE WHEN TRIM(CAST(position_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(position_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(position_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('20:DATABRICKS_POSITIONS' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(position_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(position_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(position_id AS VARCHAR))), '') END)) END AS hk_position,
    'DATABRICKS_POSITIONS' AS bkcc, position_id,
    load_dts,
    rec_src
FROM bronze.src_hub_position
WHERE UPPER(TRIM(CAST(bkcc AS VARCHAR))) = 'DATABRICKS_POSITIONS'
UNION ALL
SELECT
    CASE WHEN TRIM(CAST(position_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(position_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(position_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('29:DATABRICKS_POSITIONS_SNAPSHOT' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(position_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(position_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(position_id AS VARCHAR))), '') END)) END AS hk_position,
    'DATABRICKS_POSITIONS_SNAPSHOT' AS bkcc, position_id,
    load_dts,
    rec_src
FROM bronze.src_hub_position
WHERE UPPER(TRIM(CAST(bkcc AS VARCHAR))) = 'DATABRICKS_POSITIONS_SNAPSHOT';

CREATE OR REPLACE VIEW staging.stg_lnk_holding_filer AS
WITH fk_resolved AS (
    SELECT
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_holding__holding_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_holding__holding_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_holding__holding_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_holding__holding_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_holding__holding_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_holding__holding_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('33:AM_SEC_TREASURY_IBOR_HOLDINGS_13F' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_holding__holding_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_holding__holding_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_holding__holding_id AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_holding,
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_filer__filer_cik AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_filer__filer_cik AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_filer__filer_cik AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_filer__filer_cik AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_filer__filer_cik AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_filer__filer_cik AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('33:AM_SEC_TREASURY_IBOR_HOLDINGS_13F' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_filer__filer_cik AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_filer__filer_cik AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_filer__filer_cik AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_filer,
        load_dts,
        rec_src
    FROM bronze.src_lnk_holding_filer
)
SELECT
    CASE WHEN (hk_holding = REPEAT('f', 64) AND hk_filer = REPEAT('f', 64)) OR (TRIM(CAST(hk_holding AS VARCHAR)) = '-2' OR TRIM(CAST(hk_filer AS VARCHAR)) = '-2') THEN REPEAT('f', 64) WHEN (hk_holding = REPEAT('0', 64) AND hk_filer = REPEAT('0', 64)) OR ((NULLIF(TRIM(CAST(hk_holding AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_holding AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(hk_filer AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_filer AS VARCHAR)) = '-1')) THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_holding AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_holding AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_holding AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_filer AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_filer AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_filer AS VARCHAR))), '') END)) END AS hk_lnk_holding_filer,
    hk_holding, hk_filer,
    load_dts,
    rec_src
FROM fk_resolved;

CREATE OR REPLACE VIEW staging.stg_lnk_holding_securities AS
WITH fk_resolved AS (
    SELECT
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_holding__holding_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_holding__holding_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_holding__holding_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_holding__holding_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_holding__holding_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_holding__holding_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('33:AM_SEC_TREASURY_IBOR_HOLDINGS_13F' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_holding__holding_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_holding__holding_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_holding__holding_id AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_holding,
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_securities__ticker AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_securities__ticker AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_securities__ticker AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_securities__ticker AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_securities__ticker AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_securities__ticker AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('33:AM_SEC_TREASURY_IBOR_HOLDINGS_13F' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_securities__ticker AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_securities__ticker AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_securities__ticker AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_securities,
        load_dts,
        rec_src
    FROM bronze.src_lnk_holding_securities
)
SELECT
    CASE WHEN (hk_holding = REPEAT('f', 64) AND hk_securities = REPEAT('f', 64)) OR (TRIM(CAST(hk_holding AS VARCHAR)) = '-2' OR TRIM(CAST(hk_securities AS VARCHAR)) = '-2') THEN REPEAT('f', 64) WHEN (hk_holding = REPEAT('0', 64) AND hk_securities = REPEAT('0', 64)) OR ((NULLIF(TRIM(CAST(hk_holding AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_holding AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(hk_securities AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_securities AS VARCHAR)) = '-1')) THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_holding AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_holding AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_holding AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_securities AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_securities AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_securities AS VARCHAR))), '') END)) END AS hk_lnk_holding_securities,
    hk_holding, hk_securities,
    load_dts,
    rec_src
FROM fk_resolved;

CREATE OR REPLACE VIEW staging.stg_lnk_securities_issuer AS
WITH fk_resolved AS (
    SELECT
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_securities__ticker AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_securities__ticker AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_securities__ticker AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_securities__ticker AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_securities__ticker AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_securities__ticker AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('31:AM_SEC_TREASURY_IBOR_SECURITIES' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_securities__ticker AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_securities__ticker AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_securities__ticker AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_securities,
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_issuer__cik AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_issuer__cik AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_issuer__cik AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_issuer__cik AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_issuer__cik AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_issuer__cik AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('28:AM_SEC_TREASURY_IBOR_ISSUERS' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_issuer__cik AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_issuer__cik AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_issuer__cik AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_issuer,
        load_dts,
        rec_src
    FROM bronze.src_lnk_securities_issuer
)
SELECT
    CASE WHEN (hk_securities = REPEAT('f', 64) AND hk_issuer = REPEAT('f', 64)) OR (TRIM(CAST(hk_securities AS VARCHAR)) = '-2' OR TRIM(CAST(hk_issuer AS VARCHAR)) = '-2') THEN REPEAT('f', 64) WHEN (hk_securities = REPEAT('0', 64) AND hk_issuer = REPEAT('0', 64)) OR ((NULLIF(TRIM(CAST(hk_securities AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_securities AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(hk_issuer AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_issuer AS VARCHAR)) = '-1')) THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_securities AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_securities AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_securities AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_issuer AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_issuer AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_issuer AS VARCHAR))), '') END)) END AS hk_lnk_securities_issuer,
    hk_securities, hk_issuer,
    load_dts,
    rec_src
FROM fk_resolved;

CREATE OR REPLACE VIEW staging.stg_lnk_custody_account_security AS
WITH fk_resolved AS (
    SELECT
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_custody_account__holding_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_custody_account__holding_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_custody_account__holding_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_custody_account__holding_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_custody_account__holding_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_custody_account__holding_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('27:DATABRICKS_CUSTODY_HOLDINGS' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_custody_account__holding_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_custody_account__holding_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_custody_account__holding_id AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_custody_account,
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_security__cusip AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_security__cusip AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_security__cusip AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_security__cusip AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_security__cusip AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_security__cusip AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('27:DATABRICKS_CUSTODY_HOLDINGS' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_security__cusip AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_security__cusip AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_security__cusip AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_security,
        load_dts,
        rec_src
    FROM bronze.src_lnk_custody_account_security
)
SELECT
    CASE WHEN (hk_custody_account = REPEAT('f', 64) AND hk_security = REPEAT('f', 64)) OR (TRIM(CAST(hk_custody_account AS VARCHAR)) = '-2' OR TRIM(CAST(hk_security AS VARCHAR)) = '-2') THEN REPEAT('f', 64) WHEN (hk_custody_account = REPEAT('0', 64) AND hk_security = REPEAT('0', 64)) OR ((NULLIF(TRIM(CAST(hk_custody_account AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_custody_account AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(hk_security AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_security AS VARCHAR)) = '-1')) THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_custody_account AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_custody_account AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_custody_account AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_security AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_security AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_security AS VARCHAR))), '') END)) END AS hk_lnk_custody_account_security,
    hk_custody_account, hk_security,
    load_dts,
    rec_src
FROM fk_resolved;

CREATE OR REPLACE VIEW staging.stg_lnk_position_security AS
WITH fk_resolved AS (
    SELECT
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_position__position_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_position__position_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_position__position_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_position__position_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_position__position_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_position__position_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('20:DATABRICKS_POSITIONS' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_position__position_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_position__position_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_position__position_id AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_position,
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_security__cusip AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_security__cusip AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_security__cusip AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_security__cusip AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_security__cusip AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_security__cusip AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('26:DATABRICKS_SECURITY_MASTER' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_security__cusip AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_security__cusip AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_security__cusip AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_security,
        load_dts,
        rec_src
    FROM bronze.src_lnk_position_security
)
SELECT
    CASE WHEN (hk_position = REPEAT('f', 64) AND hk_security = REPEAT('f', 64)) OR (TRIM(CAST(hk_position AS VARCHAR)) = '-2' OR TRIM(CAST(hk_security AS VARCHAR)) = '-2') THEN REPEAT('f', 64) WHEN (hk_position = REPEAT('0', 64) AND hk_security = REPEAT('0', 64)) OR ((NULLIF(TRIM(CAST(hk_position AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_position AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(hk_security AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_security AS VARCHAR)) = '-1')) THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_position AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_position AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_position AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_security AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_security AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_security AS VARCHAR))), '') END)) END AS hk_lnk_position_security,
    hk_position, hk_security,
    load_dts,
    rec_src
FROM fk_resolved;

CREATE OR REPLACE VIEW staging.stg_sat_positions AS
SELECT
    CASE WHEN TRIM(CAST(parent_bk__position_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(parent_bk__position_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_bk__position_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('20:DATABRICKS_POSITIONS' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__position_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__position_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__position_id AS VARCHAR))), '') END)) END AS hk_position,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(account_id AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(account_id AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(account_id AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(as_of_date AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(as_of_date AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(as_of_date AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(market_value AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(market_value AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(market_value AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(quantity AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(quantity AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(quantity AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(synthetic_tax_lot_id AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(synthetic_tax_lot_id AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(synthetic_tax_lot_id AS VARCHAR)), '') END)) AS hash_diff,
    account_id, as_of_date, market_value, quantity, synthetic_tax_lot_id
FROM bronze.src_sat_positions;

CREATE OR REPLACE VIEW staging.stg_sat_positions_pii AS
SELECT
    CASE WHEN TRIM(CAST(parent_bk__position_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(parent_bk__position_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_bk__position_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('20:DATABRICKS_POSITIONS' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__position_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__position_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__position_id AS VARCHAR))), '') END)) END AS hk_position,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(custodian_account_no AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(custodian_account_no AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(custodian_account_no AS VARCHAR)), '') END)) AS hash_diff,
    custodian_account_no
FROM bronze.src_sat_positions_pii;

CREATE OR REPLACE VIEW staging.stg_sat_positions_snapshot AS
SELECT
    CASE WHEN TRIM(CAST(parent_bk__position_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(parent_bk__position_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_bk__position_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('29:DATABRICKS_POSITIONS_SNAPSHOT' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__position_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__position_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__position_id AS VARCHAR))), '') END)) END AS hk_position,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(unrealized_pnl AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(unrealized_pnl AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(unrealized_pnl AS VARCHAR)), '') END)) AS hash_diff,
    unrealized_pnl
FROM bronze.src_sat_positions_snapshot;

CREATE OR REPLACE VIEW staging.stg_sat_holdings_13f AS
SELECT
    CASE WHEN TRIM(CAST(parent_bk__holding_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(parent_bk__holding_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_bk__holding_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('33:AM_SEC_TREASURY_IBOR_HOLDINGS_13F' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__holding_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__holding_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__holding_id AS VARCHAR))), '') END)) END AS hk_holding,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(cusip AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(cusip AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(cusip AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(investment_discretion AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(investment_discretion AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(investment_discretion AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(issuer_name AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(issuer_name AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(issuer_name AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(market_value_usd AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(market_value_usd AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(market_value_usd AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(shares AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(shares AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(shares AS VARCHAR)), '') END)) AS hash_diff,
    cusip, investment_discretion, issuer_name, market_value_usd, shares
FROM bronze.src_sat_holdings_13f;

CREATE OR REPLACE VIEW staging.stg_sat_institutional_filers AS
SELECT
    CASE WHEN TRIM(CAST(parent_bk__filer_cik AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(parent_bk__filer_cik AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_bk__filer_cik AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('41:AM_SEC_TREASURY_IBOR_INSTITUTIONAL_FILERS' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__filer_cik AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__filer_cik AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__filer_cik AS VARCHAR))), '') END)) END AS hk_filer,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(filer_name AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(filer_name AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(filer_name AS VARCHAR)), '') END)) AS hash_diff,
    filer_name
FROM bronze.src_sat_institutional_filers;

CREATE OR REPLACE VIEW staging.stg_sat_issuers AS
SELECT
    CASE WHEN TRIM(CAST(parent_bk__cik AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(parent_bk__cik AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_bk__cik AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('28:AM_SEC_TREASURY_IBOR_ISSUERS' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__cik AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__cik AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__cik AS VARCHAR))), '') END)) END AS hk_issuer,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(company_name AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(company_name AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(company_name AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(exchange AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(exchange AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(exchange AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(ticker AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(ticker AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(ticker AS VARCHAR)), '') END)) AS hash_diff,
    company_name, exchange, ticker
FROM bronze.src_sat_issuers;

CREATE OR REPLACE VIEW staging.stg_sat_securities AS
SELECT
    CASE WHEN TRIM(CAST(parent_bk__ticker AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(parent_bk__ticker AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_bk__ticker AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('31:AM_SEC_TREASURY_IBOR_SECURITIES' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__ticker AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__ticker AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__ticker AS VARCHAR))), '') END)) END AS hk_securities,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(cusip AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(cusip AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(cusip AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(security_type AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(security_type AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(security_type AS VARCHAR)), '') END)) AS hash_diff,
    cusip, security_type
FROM bronze.src_sat_securities;

CREATE OR REPLACE VIEW staging.stg_sat_security_master AS
SELECT
    CASE WHEN TRIM(CAST(parent_bk__cusip AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(parent_bk__cusip AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_bk__cusip AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('26:DATABRICKS_SECURITY_MASTER' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__cusip AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__cusip AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__cusip AS VARCHAR))), '') END)) END AS hk_security,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(asset_class AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(asset_class AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(asset_class AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(currency AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(currency AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(currency AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(cusip AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(cusip AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(cusip AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(isin AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(isin AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(isin AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(security_name AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(security_name AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(security_name AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(sedol AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(sedol AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(sedol AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(ticker AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(ticker AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(ticker AS VARCHAR)), '') END)) AS hash_diff,
    asset_class, currency, cusip, isin, security_name, sedol, ticker
FROM bronze.src_sat_security_master;

CREATE OR REPLACE VIEW staging.stg_sat_treasury_rates AS
SELECT
    CASE WHEN TRIM(CAST(parent_bk__observation_date AS VARCHAR)) = '-2' OR TRIM(CAST(parent_bk__series_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(parent_bk__observation_date AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_bk__observation_date AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(parent_bk__series_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_bk__series_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('35:AM_SEC_TREASURY_IBOR_TREASURY_RATES' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__observation_date AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__observation_date AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__observation_date AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__series_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__series_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__series_id AS VARCHAR))), '') END)) END AS hk_rate,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(yield_pct AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(yield_pct AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(yield_pct AS VARCHAR)), '') END)) AS hash_diff,
    yield_pct
FROM bronze.src_sat_treasury_rates;

CREATE OR REPLACE VIEW staging.stg_sat_yield_curves AS
SELECT
    CASE WHEN TRIM(CAST(parent_bk__curve_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(parent_bk__curve_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_bk__curve_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('33:AM_SEC_TREASURY_IBOR_YIELD_CURVES' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__curve_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__curve_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__curve_id AS VARCHAR))), '') END)) END AS hk_yield_curve,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(observation_date AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(observation_date AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(observation_date AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(rate_10y AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(rate_10y AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(rate_10y AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(rate_2y AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(rate_2y AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(rate_2y AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(rate_sofr AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(rate_sofr AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(rate_sofr AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(spread_10y_2y AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(spread_10y_2y AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(spread_10y_2y AS VARCHAR)), '') END)) AS hash_diff,
    observation_date, rate_10y, rate_2y, rate_sofr, spread_10y_2y
FROM bronze.src_sat_yield_curves;

CREATE OR REPLACE VIEW staging.stg_am_sec_treasury_ibor_holdings_13f AS
SELECT
    CASE WHEN TRIM(CAST(holding_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(holding_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(holding_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('33:AM_SEC_TREASURY_IBOR_HOLDINGS_13F' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(holding_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(holding_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(holding_id AS VARCHAR))), '') END)) END AS hk_holding,
    'AM_SEC_TREASURY_IBOR_HOLDINGS_13F' AS bkcc__hub_holding,
    holding_id,
    CASE WHEN TRIM(CAST(filer_cik AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(filer_cik AS VARCHAR)), '') IS NULL OR TRIM(CAST(filer_cik AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('33:AM_SEC_TREASURY_IBOR_HOLDINGS_13F' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(filer_cik AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(filer_cik AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(filer_cik AS VARCHAR))), '') END)) END AS hk_filer,
    'AM_SEC_TREASURY_IBOR_HOLDINGS_13F' AS bkcc__hub_filer,
    filer_cik,
    CASE WHEN TRIM(CAST(ticker AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(ticker AS VARCHAR)), '') IS NULL OR TRIM(CAST(ticker AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('33:AM_SEC_TREASURY_IBOR_HOLDINGS_13F' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(ticker AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(ticker AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(ticker AS VARCHAR))), '') END)) END AS hk_securities,
    'AM_SEC_TREASURY_IBOR_HOLDINGS_13F' AS bkcc__hub_securities,
    ticker,
    load_dts,
    rec_src
FROM bronze.am_sec_treasury_ibor_holdings_13f;

CREATE OR REPLACE VIEW staging.stg_am_sec_treasury_ibor_institutional_filers AS
SELECT
    CASE WHEN TRIM(CAST(filer_cik AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(filer_cik AS VARCHAR)), '') IS NULL OR TRIM(CAST(filer_cik AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('41:AM_SEC_TREASURY_IBOR_INSTITUTIONAL_FILERS' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(filer_cik AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(filer_cik AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(filer_cik AS VARCHAR))), '') END)) END AS hk_filer,
    'AM_SEC_TREASURY_IBOR_INSTITUTIONAL_FILERS' AS bkcc, filer_cik,
    load_dts,
    rec_src
FROM bronze.src_hub_filer
WHERE UPPER(TRIM(CAST(bkcc AS VARCHAR))) = 'AM_SEC_TREASURY_IBOR_INSTITUTIONAL_FILERS';

CREATE OR REPLACE VIEW staging.stg_am_sec_treasury_ibor_securities AS
SELECT
    CASE WHEN TRIM(CAST(ticker AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(ticker AS VARCHAR)), '') IS NULL OR TRIM(CAST(ticker AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('31:AM_SEC_TREASURY_IBOR_SECURITIES' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(ticker AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(ticker AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(ticker AS VARCHAR))), '') END)) END AS hk_securities,
    'AM_SEC_TREASURY_IBOR_SECURITIES' AS bkcc, ticker,
    load_dts,
    rec_src
FROM bronze.src_hub_securities
WHERE UPPER(TRIM(CAST(bkcc AS VARCHAR))) = 'AM_SEC_TREASURY_IBOR_SECURITIES';

CREATE OR REPLACE VIEW staging.stg_am_sec_treasury_ibor_issuers AS
SELECT
    CASE WHEN TRIM(CAST(cik AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(cik AS VARCHAR)), '') IS NULL OR TRIM(CAST(cik AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('28:AM_SEC_TREASURY_IBOR_ISSUERS' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(cik AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(cik AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(cik AS VARCHAR))), '') END)) END AS hk_issuer,
    'AM_SEC_TREASURY_IBOR_ISSUERS' AS bkcc, cik,
    load_dts,
    rec_src
FROM bronze.src_hub_issuer;

CREATE OR REPLACE VIEW staging.stg_am_sec_treasury_ibor_treasury_rates AS
SELECT
    CASE WHEN TRIM(CAST(observation_date AS VARCHAR)) = '-2' OR TRIM(CAST(series_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(observation_date AS VARCHAR)), '') IS NULL OR TRIM(CAST(observation_date AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(series_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(series_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('35:AM_SEC_TREASURY_IBOR_TREASURY_RATES' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(observation_date AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(observation_date AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(observation_date AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(series_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(series_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(series_id AS VARCHAR))), '') END)) END AS hk_rate,
    'AM_SEC_TREASURY_IBOR_TREASURY_RATES' AS bkcc, observation_date, series_id,
    load_dts,
    rec_src
FROM bronze.src_hub_rate;

CREATE OR REPLACE VIEW staging.stg_am_sec_treasury_ibor_yield_curves AS
SELECT
    CASE WHEN TRIM(CAST(curve_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(curve_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(curve_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('33:AM_SEC_TREASURY_IBOR_YIELD_CURVES' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(curve_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(curve_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(curve_id AS VARCHAR))), '') END)) END AS hk_yield_curve,
    'AM_SEC_TREASURY_IBOR_YIELD_CURVES' AS bkcc, curve_id,
    load_dts,
    rec_src
FROM bronze.src_hub_yield_curve;

CREATE OR REPLACE VIEW staging.stg_databricks_custody_holdings AS
SELECT
    CASE WHEN TRIM(CAST(holding_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(holding_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(holding_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('27:DATABRICKS_CUSTODY_HOLDINGS' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(holding_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(holding_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(holding_id AS VARCHAR))), '') END)) END AS hk_custody_account,
    'DATABRICKS_CUSTODY_HOLDINGS' AS bkcc__hub_custody_account,
    holding_id,
    CASE WHEN TRIM(CAST(cusip AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(cusip AS VARCHAR)), '') IS NULL OR TRIM(CAST(cusip AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('27:DATABRICKS_CUSTODY_HOLDINGS' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(cusip AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(cusip AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(cusip AS VARCHAR))), '') END)) END AS hk_security,
    'DATABRICKS_CUSTODY_HOLDINGS' AS bkcc__hub_security,
    cusip,
    load_dts,
    rec_src
FROM bronze.databricks_custody_holdings;

CREATE OR REPLACE VIEW staging.stg_databricks_security_master AS
SELECT
    CASE WHEN TRIM(CAST(cusip AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(cusip AS VARCHAR)), '') IS NULL OR TRIM(CAST(cusip AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('26:DATABRICKS_SECURITY_MASTER' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(cusip AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(cusip AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(cusip AS VARCHAR))), '') END)) END AS hk_security,
    'DATABRICKS_SECURITY_MASTER' AS bkcc, cusip,
    load_dts,
    rec_src
FROM bronze.src_hub_security
WHERE UPPER(TRIM(CAST(bkcc AS VARCHAR))) = 'DATABRICKS_SECURITY_MASTER';

CREATE OR REPLACE VIEW staging.stg_databricks_positions AS
SELECT
    CASE WHEN TRIM(CAST(position_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(position_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(position_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('20:DATABRICKS_POSITIONS' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(position_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(position_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(position_id AS VARCHAR))), '') END)) END AS hk_position,
    'DATABRICKS_POSITIONS' AS bkcc, position_id,
    load_dts,
    rec_src
FROM bronze.src_hub_position
WHERE UPPER(TRIM(CAST(bkcc AS VARCHAR))) = 'DATABRICKS_POSITIONS';

CREATE OR REPLACE VIEW staging.stg_databricks_positions_snapshot AS
SELECT
    CASE WHEN TRIM(CAST(position_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(position_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(position_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('29:DATABRICKS_POSITIONS_SNAPSHOT' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(position_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(position_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(position_id AS VARCHAR))), '') END)) END AS hk_position,
    'DATABRICKS_POSITIONS_SNAPSHOT' AS bkcc, position_id,
    load_dts,
    rec_src
FROM bronze.src_hub_position
WHERE UPPER(TRIM(CAST(bkcc AS VARCHAR))) = 'DATABRICKS_POSITIONS_SNAPSHOT';

-- =============================================================================

-- ZONE 2: IDEMPOTENT INSERT-ONLY RAW & BUSINESS VAULT LOADS

-- =============================================================================

INSERT INTO raw_vault.hub_holding (hk_holding, bkcc, holding_id, load_dts, rec_src)
SELECT
    src.hk_holding,
    src.bkcc, src.holding_id,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_hub_holding src
WHERE src.hk_holding NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.hub_holding h
      WHERE h.hk_holding = src.hk_holding
  )
GROUP BY src.hk_holding, src.bkcc, src.holding_id;

INSERT INTO raw_vault.hub_filer (hk_filer, bkcc, filer_cik, load_dts, rec_src)
SELECT
    src.hk_filer,
    src.bkcc, src.filer_cik,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_hub_filer src
WHERE src.hk_filer NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.hub_filer h
      WHERE h.hk_filer = src.hk_filer
  )
GROUP BY src.hk_filer, src.bkcc, src.filer_cik;

INSERT INTO raw_vault.hub_securities (hk_securities, bkcc, ticker, load_dts, rec_src)
SELECT
    src.hk_securities,
    src.bkcc, src.ticker,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_hub_securities src
WHERE src.hk_securities NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.hub_securities h
      WHERE h.hk_securities = src.hk_securities
  )
GROUP BY src.hk_securities, src.bkcc, src.ticker;

INSERT INTO raw_vault.hub_issuer (hk_issuer, bkcc, cik, load_dts, rec_src)
SELECT
    src.hk_issuer,
    src.bkcc, src.cik,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_hub_issuer src
WHERE src.hk_issuer NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.hub_issuer h
      WHERE h.hk_issuer = src.hk_issuer
  )
GROUP BY src.hk_issuer, src.bkcc, src.cik;

INSERT INTO raw_vault.hub_rate (hk_rate, bkcc, observation_date, series_id, load_dts, rec_src)
SELECT
    src.hk_rate,
    src.bkcc, src.observation_date, src.series_id,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_hub_rate src
WHERE src.hk_rate NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.hub_rate h
      WHERE h.hk_rate = src.hk_rate
  )
GROUP BY src.hk_rate, src.bkcc, src.observation_date, src.series_id;

INSERT INTO raw_vault.hub_yield_curve (hk_yield_curve, bkcc, curve_id, load_dts, rec_src)
SELECT
    src.hk_yield_curve,
    src.bkcc, src.curve_id,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_hub_yield_curve src
WHERE src.hk_yield_curve NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.hub_yield_curve h
      WHERE h.hk_yield_curve = src.hk_yield_curve
  )
GROUP BY src.hk_yield_curve, src.bkcc, src.curve_id;

INSERT INTO raw_vault.hub_custody_account (hk_custody_account, bkcc, holding_id, load_dts, rec_src)
SELECT
    src.hk_custody_account,
    src.bkcc, src.holding_id,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_hub_custody_account src
WHERE src.hk_custody_account NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.hub_custody_account h
      WHERE h.hk_custody_account = src.hk_custody_account
  )
GROUP BY src.hk_custody_account, src.bkcc, src.holding_id;

INSERT INTO raw_vault.hub_security (hk_security, bkcc, cusip, load_dts, rec_src)
SELECT
    src.hk_security,
    src.bkcc, src.cusip,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_hub_security src
WHERE src.hk_security NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.hub_security h
      WHERE h.hk_security = src.hk_security
  )
GROUP BY src.hk_security, src.bkcc, src.cusip;

INSERT INTO raw_vault.hub_position (hk_position, bkcc, position_id, load_dts, rec_src)
SELECT
    src.hk_position,
    src.bkcc, src.position_id,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_hub_position src
WHERE src.hk_position NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.hub_position h
      WHERE h.hk_position = src.hk_position
  )
GROUP BY src.hk_position, src.bkcc, src.position_id;

INSERT INTO raw_vault.lnk_holding_filer (hk_lnk_holding_filer, hk_holding, hk_filer, load_dts, rec_src)
SELECT
    src.hk_lnk_holding_filer,
    src.hk_holding, src.hk_filer,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_lnk_holding_filer src
WHERE src.hk_lnk_holding_filer NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.lnk_holding_filer l
      WHERE l.hk_lnk_holding_filer = src.hk_lnk_holding_filer
  )
GROUP BY src.hk_lnk_holding_filer, src.hk_holding, src.hk_filer;

INSERT INTO raw_vault.lnk_holding_securities (hk_lnk_holding_securities, hk_holding, hk_securities, load_dts, rec_src)
SELECT
    src.hk_lnk_holding_securities,
    src.hk_holding, src.hk_securities,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_lnk_holding_securities src
WHERE src.hk_lnk_holding_securities NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.lnk_holding_securities l
      WHERE l.hk_lnk_holding_securities = src.hk_lnk_holding_securities
  )
GROUP BY src.hk_lnk_holding_securities, src.hk_holding, src.hk_securities;

INSERT INTO raw_vault.lnk_securities_issuer (hk_lnk_securities_issuer, hk_securities, hk_issuer, load_dts, rec_src)
SELECT
    src.hk_lnk_securities_issuer,
    src.hk_securities, src.hk_issuer,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_lnk_securities_issuer src
WHERE src.hk_lnk_securities_issuer NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.lnk_securities_issuer l
      WHERE l.hk_lnk_securities_issuer = src.hk_lnk_securities_issuer
  )
GROUP BY src.hk_lnk_securities_issuer, src.hk_securities, src.hk_issuer;

INSERT INTO raw_vault.lnk_custody_account_security (hk_lnk_custody_account_security, hk_custody_account, hk_security, load_dts, rec_src)
SELECT
    src.hk_lnk_custody_account_security,
    src.hk_custody_account, src.hk_security,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_lnk_custody_account_security src
WHERE src.hk_lnk_custody_account_security NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.lnk_custody_account_security l
      WHERE l.hk_lnk_custody_account_security = src.hk_lnk_custody_account_security
  )
GROUP BY src.hk_lnk_custody_account_security, src.hk_custody_account, src.hk_security;

INSERT INTO raw_vault.lnk_position_security (hk_lnk_position_security, hk_position, hk_security, load_dts, rec_src)
SELECT
    src.hk_lnk_position_security,
    src.hk_position, src.hk_security,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_lnk_position_security src
WHERE src.hk_lnk_position_security NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.lnk_position_security l
      WHERE l.hk_lnk_position_security = src.hk_lnk_position_security
  )
GROUP BY src.hk_lnk_position_security, src.hk_position, src.hk_security;

INSERT INTO raw_vault.sat_positions (
    hk_position, load_dts, rec_src, hash_diff, account_id, as_of_date, market_value, quantity, synthetic_tax_lot_id
)
SELECT
    src.hk_position,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.account_id, src.as_of_date, src.market_value, src.quantity, src.synthetic_tax_lot_id
FROM staging.stg_sat_positions src
LEFT JOIN (
    SELECT hk_position, load_dts, hash_diff
    FROM raw_vault.sat_positions
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_position ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_position = src.hk_position
WHERE src.hk_position NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND (
      cur.hk_position IS NULL
      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)
  );

INSERT INTO raw_vault.sat_positions_pii (
    hk_position, load_dts, rec_src, hash_diff, custodian_account_no
)
SELECT
    src.hk_position,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.custodian_account_no
FROM staging.stg_sat_positions_pii src
LEFT JOIN (
    SELECT hk_position, load_dts, hash_diff
    FROM raw_vault.sat_positions_pii
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_position ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_position = src.hk_position
WHERE src.hk_position NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND (
      cur.hk_position IS NULL
      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)
  );

INSERT INTO raw_vault.sat_positions_snapshot (
    hk_position, load_dts, rec_src, hash_diff, unrealized_pnl
)
SELECT
    src.hk_position,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.unrealized_pnl
FROM staging.stg_sat_positions_snapshot src
LEFT JOIN (
    SELECT hk_position, load_dts, hash_diff
    FROM raw_vault.sat_positions_snapshot
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_position ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_position = src.hk_position
WHERE src.hk_position NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND (
      cur.hk_position IS NULL
      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)
  );

INSERT INTO raw_vault.sat_holdings_13f (
    hk_holding, load_dts, rec_src, hash_diff, cusip, investment_discretion, issuer_name, market_value_usd, shares
)
SELECT
    src.hk_holding,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.cusip, src.investment_discretion, src.issuer_name, src.market_value_usd, src.shares
FROM staging.stg_sat_holdings_13f src
LEFT JOIN (
    SELECT hk_holding, load_dts, hash_diff
    FROM raw_vault.sat_holdings_13f
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_holding ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_holding = src.hk_holding
WHERE src.hk_holding NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND (
      cur.hk_holding IS NULL
      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)
  );

INSERT INTO raw_vault.sat_institutional_filers (
    hk_filer, load_dts, rec_src, hash_diff, filer_name
)
SELECT
    src.hk_filer,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.filer_name
FROM staging.stg_sat_institutional_filers src
LEFT JOIN (
    SELECT hk_filer, load_dts, hash_diff
    FROM raw_vault.sat_institutional_filers
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_filer ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_filer = src.hk_filer
WHERE src.hk_filer NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND (
      cur.hk_filer IS NULL
      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)
  );

INSERT INTO raw_vault.sat_issuers (
    hk_issuer, load_dts, rec_src, hash_diff, company_name, exchange, ticker
)
SELECT
    src.hk_issuer,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.company_name, src.exchange, src.ticker
FROM staging.stg_sat_issuers src
LEFT JOIN (
    SELECT hk_issuer, load_dts, hash_diff
    FROM raw_vault.sat_issuers
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_issuer ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_issuer = src.hk_issuer
WHERE src.hk_issuer NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND (
      cur.hk_issuer IS NULL
      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)
  );

INSERT INTO raw_vault.sat_securities (
    hk_securities, load_dts, rec_src, hash_diff, cusip, security_type
)
SELECT
    src.hk_securities,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.cusip, src.security_type
FROM staging.stg_sat_securities src
LEFT JOIN (
    SELECT hk_securities, load_dts, hash_diff
    FROM raw_vault.sat_securities
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_securities ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_securities = src.hk_securities
WHERE src.hk_securities NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND (
      cur.hk_securities IS NULL
      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)
  );

INSERT INTO raw_vault.sat_security_master (
    hk_security, load_dts, rec_src, hash_diff, asset_class, currency, cusip, isin, security_name, sedol, ticker
)
SELECT
    src.hk_security,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.asset_class, src.currency, src.cusip, src.isin, src.security_name, src.sedol, src.ticker
FROM staging.stg_sat_security_master src
LEFT JOIN (
    SELECT hk_security, load_dts, hash_diff
    FROM raw_vault.sat_security_master
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_security ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_security = src.hk_security
WHERE src.hk_security NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND (
      cur.hk_security IS NULL
      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)
  );

INSERT INTO raw_vault.sat_treasury_rates (
    hk_rate, load_dts, rec_src, hash_diff, yield_pct
)
SELECT
    src.hk_rate,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.yield_pct
FROM staging.stg_sat_treasury_rates src
LEFT JOIN (
    SELECT hk_rate, load_dts, hash_diff
    FROM raw_vault.sat_treasury_rates
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_rate ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_rate = src.hk_rate
WHERE src.hk_rate NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND (
      cur.hk_rate IS NULL
      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)
  );

INSERT INTO raw_vault.sat_yield_curves (
    hk_yield_curve, load_dts, rec_src, hash_diff, observation_date, rate_10y, rate_2y, rate_sofr, spread_10y_2y
)
SELECT
    src.hk_yield_curve,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.observation_date, src.rate_10y, src.rate_2y, src.rate_sofr, src.spread_10y_2y
FROM staging.stg_sat_yield_curves src
LEFT JOIN (
    SELECT hk_yield_curve, load_dts, hash_diff
    FROM raw_vault.sat_yield_curves
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_yield_curve ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_yield_curve = src.hk_yield_curve
WHERE src.hk_yield_curve NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND (
      cur.hk_yield_curve IS NULL
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

CREATE TABLE IF NOT EXISTS business_vault.pit_position (
    hk_pit_position CHAR(64) NOT NULL PRIMARY KEY,
    hk_position CHAR(64) NOT NULL,
    as_of_dts TIMESTAMP NOT NULL,
    sat_positions_hk CHAR(64) NOT NULL,
    sat_positions_load_dts TIMESTAMP NOT NULL,
    sat_positions_pii_hk CHAR(64) NOT NULL,
    sat_positions_pii_load_dts TIMESTAMP NOT NULL,
    sat_positions_snapshot_hk CHAR(64) NOT NULL,
    sat_positions_snapshot_load_dts TIMESTAMP NOT NULL
);

INSERT INTO business_vault.pit_position (hk_pit_position, hk_position, as_of_dts, sat_positions_hk, sat_positions_load_dts, sat_positions_pii_hk, sat_positions_pii_load_dts, sat_positions_snapshot_hk, sat_positions_snapshot_load_dts)
SELECT src.hk_pit_position, src.hk_position, src.as_of_dts, src.sat_positions_hk, src.sat_positions_load_dts, src.sat_positions_pii_hk, src.sat_positions_pii_load_dts, src.sat_positions_snapshot_hk, src.sat_positions_snapshot_load_dts
FROM (
    SELECT
        LOWER(SHA256(
            '64:' || h.hk_position || '||'
            || CAST(OCTET_LENGTH(ENCODE(CAST(cal.as_of_dts AS VARCHAR))) AS VARCHAR)
            || ':' || CAST(cal.as_of_dts AS VARCHAR)
        )) AS hk_pit_position,
        h.hk_position,
        cal.as_of_dts AS as_of_dts,
        COALESCE(s0.hk_position, '0000000000000000000000000000000000000000000000000000000000000000') AS sat_positions_hk,
        COALESCE(s0.load_dts, TIMESTAMP '1900-01-01 00:00:00') AS sat_positions_load_dts,
        COALESCE(s1.hk_position, '0000000000000000000000000000000000000000000000000000000000000000') AS sat_positions_pii_hk,
        COALESCE(s1.load_dts, TIMESTAMP '1900-01-01 00:00:00') AS sat_positions_pii_load_dts,
        COALESCE(s2.hk_position, '0000000000000000000000000000000000000000000000000000000000000000') AS sat_positions_snapshot_hk,
        COALESCE(s2.load_dts, TIMESTAMP '1900-01-01 00:00:00') AS sat_positions_snapshot_load_dts
    FROM raw_vault.hub_position h
    CROSS JOIN business_vault.snap_logarithmic_calendar cal
    ASOF LEFT JOIN raw_vault.sat_positions s0
        ON s0.hk_position = h.hk_position
       AND s0.load_dts <= cal.as_of_dts
    ASOF LEFT JOIN raw_vault.sat_positions_pii s1
        ON s1.hk_position = h.hk_position
       AND s1.load_dts <= cal.as_of_dts
    ASOF LEFT JOIN raw_vault.sat_positions_snapshot s2
        ON s2.hk_position = h.hk_position
       AND s2.load_dts <= cal.as_of_dts
    WHERE h.hk_position NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
) src
WHERE NOT EXISTS (
    SELECT 1 FROM business_vault.pit_position p
    WHERE p.hk_pit_position = src.hk_pit_position
);

CREATE OR REPLACE VIEW information_mart.pit_position AS
SELECT * FROM business_vault.pit_position;

CREATE OR REPLACE VIEW information_mart.vw_pit_position_current AS
SELECT
    pit.as_of_dts,
    h.hk_position,
    h.bkcc,
    h.position_id,
    s0.account_id AS account_id,
    s0.as_of_date AS as_of_date,
    s0.market_value AS market_value,
    s0.quantity AS quantity,
    s0.synthetic_tax_lot_id AS synthetic_tax_lot_id,
    s1.custodian_account_no AS custodian_account_no,
    s2.unrealized_pnl AS unrealized_pnl
FROM business_vault.pit_position pit
INNER JOIN raw_vault.hub_position h ON h.hk_position = pit.hk_position
INNER JOIN raw_vault.sat_positions s0
    ON s0.hk_position = pit.sat_positions_hk
   AND s0.load_dts = pit.sat_positions_load_dts
INNER JOIN raw_vault.sat_positions_pii s1
    ON s1.hk_position = pit.sat_positions_pii_hk
   AND s1.load_dts = pit.sat_positions_pii_load_dts
INNER JOIN raw_vault.sat_positions_snapshot s2
    ON s2.hk_position = pit.sat_positions_snapshot_hk
   AND s2.load_dts = pit.sat_positions_snapshot_load_dts;