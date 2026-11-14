-- Open-DV Asset Management 4-Zone Data Vault 2.0 Schema
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS raw_vault;
CREATE SCHEMA IF NOT EXISTS business_vault;
CREATE SCHEMA IF NOT EXISTS marts;

CREATE TABLE IF NOT EXISTS staging.stg_sec_security_master (
    ticker VARCHAR NOT NULL,
    cik VARCHAR NOT NULL,
    company_name VARCHAR,
    exchange VARCHAR,
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS staging.stg_sec_13f_holdings (
    holding_id VARCHAR NOT NULL,
    cik VARCHAR NOT NULL,
    cusip VARCHAR,
    ticker VARCHAR,
    issuer_name VARCHAR,
    shares BIGINT,
    market_value_usd DECIMAL(18,2),
    investment_discretion VARCHAR,
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS staging.stg_fred_treasury_rates (
    observation_date DATE NOT NULL,
    series_id VARCHAR NOT NULL,
    yield_pct DECIMAL(6,4),
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_vault.hub_security (
    hk_security VARCHAR PRIMARY KEY,
    bk_ticker VARCHAR NOT NULL,
    bk_cik VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_vault.hub_investor (
    hk_investor VARCHAR PRIMARY KEY,
    bk_cik VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_vault.hub_rate_index (
    hk_rate_index VARCHAR PRIMARY KEY,
    bk_series_id VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_vault.hub_document (
    hk_document VARCHAR PRIMARY KEY,
    bk_document_id VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_vault.lnk_investor_holding (
    hk_investor_holding VARCHAR PRIMARY KEY,
    hk_investor VARCHAR NOT NULL,
    hk_security VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_vault.sat_security_details (
    hk_security VARCHAR NOT NULL,
    company_name VARCHAR,
    exchange VARCHAR,
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL,
    hash_diff VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_vault.sat_holding_position (
    hk_investor_holding VARCHAR NOT NULL,
    holding_id VARCHAR,
    shares BIGINT,
    market_value_usd DECIMAL(18,2),
    investment_discretion VARCHAR,
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL,
    hash_diff VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_vault.sat_rate_observation (
    hk_rate_index VARCHAR NOT NULL,
    observation_date DATE NOT NULL,
    yield_pct DECIMAL(6,4),
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL,
    hash_diff VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_vault.sat_document_metadata (
    hk_document VARCHAR NOT NULL,
    file_hash VARCHAR NOT NULL,
    byte_count BIGINT,
    mime_type VARCHAR,
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL,
    hash_diff VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS business_vault.pit_security (
    hk_security VARCHAR NOT NULL,
    snapshot_dts TIMESTAMP NOT NULL,
    sat_security_details_load_dts TIMESTAMP,
    PRIMARY KEY (hk_security, snapshot_dts)
);

CREATE TABLE IF NOT EXISTS business_vault.bridge_portfolio_holdings (
    hk_investor_holding VARCHAR NOT NULL,
    hk_investor VARCHAR NOT NULL,
    hk_security VARCHAR NOT NULL,
    PRIMARY KEY (hk_investor_holding)
);

CREATE TABLE IF NOT EXISTS marts.dim_security (
    security_key VARCHAR PRIMARY KEY,
    ticker VARCHAR NOT NULL,
    cik VARCHAR NOT NULL,
    company_name VARCHAR,
    exchange VARCHAR
);

CREATE TABLE IF NOT EXISTS marts.fct_institutional_holding (
    holding_key VARCHAR PRIMARY KEY,
    investor_cik VARCHAR NOT NULL,
    security_ticker VARCHAR NOT NULL,
    shares BIGINT,
    market_value_usd DECIMAL(18,2),
    observation_date DATE
);