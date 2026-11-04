-- =============================================================================

-- ZONE 0: TARGET SCHEMAS, BRONZE SOURCE SEEDS, VAULT DDL & GHOST/ZERO RECORDS

-- =============================================================================

CREATE SCHEMA IF NOT EXISTS bronze;

CREATE SCHEMA IF NOT EXISTS staging;

CREATE SCHEMA IF NOT EXISTS raw_vault;

CREATE SCHEMA IF NOT EXISTS business_vault;

CREATE SCHEMA IF NOT EXISTS information_mart;

CREATE OR REPLACE TABLE bronze.src_hub_customer (
    bkcc VARCHAR,
    customer_tax_id VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_hub_customer VALUES
    ('GLOBAL', 'TAX-US-900101', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE'),
    ('GLOBAL', 'TAX-DE-800202', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE'),
    ('GLOBAL', 'TAX-SG-700303', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE'),
    ('GLOBAL', 'TAX-GB-600404', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE'),
    ('GLOBAL', 'TAX-JP-500505', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE'),
    ('GLOBAL', 'TAX-CA-400606', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE');

CREATE OR REPLACE TABLE bronze.src_hub_order (
    bkcc VARCHAR,
    order_id VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_hub_order VALUES
    ('GLOBAL', 'ORD-2026-1001', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE'),
    ('GLOBAL', 'ORD-2026-1002', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE'),
    ('GLOBAL', 'ORD-2026-1003', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE'),
    ('GLOBAL', 'ORD-2026-1004', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE'),
    ('GLOBAL', 'ORD-2026-1005', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE'),
    ('GLOBAL', 'ORD-2026-1006', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE');

CREATE OR REPLACE TABLE bronze.src_hub_promotion (
    bkcc VARCHAR,
    promo_campaign_code VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_hub_promotion VALUES
    ('GLOBAL', 'PROMO-SPRING-2026', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE'),
    ('GLOBAL', 'PROMO-VIP-2026', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE');

CREATE OR REPLACE VIEW bronze.crm_salesforce_crm_customers AS SELECT * FROM bronze.src_hub_customer;

CREATE OR REPLACE TABLE bronze.erp_netsuite_erp_orders (
    order_id VARCHAR,
    promo_campaign_code VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.erp_netsuite_erp_orders VALUES
    ('ORD-2026-1001', 'PROMO-SPRING-2026', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE'),
    ('ORD-2026-1002', 'PROMO-VIP-2026', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE'),
    ('ORD-2026-1003', 'PROMO-SPRING-2026', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE'),
    ('ORD-2026-1004', 'PROMO-VIP-2026', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE');

CREATE OR REPLACE TABLE bronze.src_lnk_order_customer (
    hk_order__bkcc VARCHAR,
    hk_order__order_id VARCHAR,
    hk_customer__bkcc VARCHAR,
    hk_customer__customer_tax_id VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_lnk_order_customer VALUES
    ('GLOBAL', 'ORD-2026-1001', 'GLOBAL', 'TAX-US-900101', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE'),
    ('GLOBAL', 'ORD-2026-1002', 'GLOBAL', NULL, TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE'),
    ('GLOBAL', 'ORD-2026-1001', 'GLOBAL', '-1', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE'),
    ('GLOBAL', 'ORD-2026-1002', 'GLOBAL', '-2', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE');

CREATE OR REPLACE TABLE bronze.src_lnk_order_customer_promotion (
    hk_order__bkcc VARCHAR,
    hk_order__order_id VARCHAR,
    hk_promotion__bkcc VARCHAR,
    hk_promotion__promo_campaign_code VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_lnk_order_customer_promotion VALUES
    ('GLOBAL', 'ORD-2026-1001', 'GLOBAL', 'PROMO-SPRING-2026', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE'),
    ('GLOBAL', 'ORD-2026-1002', 'GLOBAL', NULL, TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE'),
    ('GLOBAL', 'ORD-2026-1001', 'GLOBAL', '-1', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE'),
    ('GLOBAL', 'ORD-2026-1002', 'GLOBAL', '-2', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE');

CREATE OR REPLACE TABLE bronze.src_sat_customer_pii (
    parent_bk__bkcc VARCHAR,
    parent_bk__customer_tax_id VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    contact_email VARCHAR,
    tax_ssn_tin VARCHAR
);
INSERT INTO bronze.src_sat_customer_pii VALUES
    ('GLOBAL', 'TAX-US-900101', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE', 'treasury@apex-industrial.example.com', '99-4102931'),
    ('GLOBAL', 'TAX-DE-800202', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE', 'finance@rheinland-precision.example.de', '44-8821044'),
    ('GLOBAL', 'TAX-SG-700303', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE', 'ap@merlion-logistics.example.sg', '77-1928374'),
    ('GLOBAL', 'TAX-GB-600404', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE', 'billing@thames-sovereign.example.co.uk', '22-5591823'),
    ('GLOBAL', 'TAX-JP-500505', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE', 'procurement@kanto-semi.example.jp', '66-3019284'),
    ('GLOBAL', 'TAX-CA-400606', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE', 'accounts@boreal-cloud.example.ca', '11-7482910');

CREATE OR REPLACE TABLE bronze.src_sat_customer_core (
    parent_bk__bkcc VARCHAR,
    parent_bk__customer_tax_id VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    account_segment VARCHAR,
    country_iso2 VARCHAR,
    credit_limit_usd DOUBLE,
    legal_name VARCHAR
);
INSERT INTO bronze.src_sat_customer_core VALUES
    ('GLOBAL', 'TAX-US-900101', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE', 'ENTERPRISE', 'US', 1250000.00, 'Apex Industrial Holdings LLC'),
    ('GLOBAL', 'TAX-DE-800202', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE', 'ENTERPRISE', 'DE', 980000.00, 'Rheinland Precision GmbH'),
    ('GLOBAL', 'TAX-SG-700303', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE', 'MID_MARKET', 'SG', 640000.00, 'Merlion Maritime Logistics Pte Ltd'),
    ('GLOBAL', 'TAX-GB-600404', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE', 'ENTERPRISE', 'GB', 1500000.00, 'Thames Sovereign Analytics Ltd'),
    ('GLOBAL', 'TAX-JP-500505', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE', 'STRATEGIC', 'JP', 2100000.00, 'Kanto Semiconductor Works KK'),
    ('GLOBAL', 'TAX-CA-400606', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE', 'MID_MARKET', 'CA', 520000.00, 'Boreal Cloud Infrastructure Inc');

CREATE OR REPLACE TABLE bronze.src_sat_promotion_ref (
    parent_bk__bkcc VARCHAR,
    parent_bk__promo_campaign_code VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    promo_campaign_code VARCHAR
);
INSERT INTO bronze.src_sat_promotion_ref VALUES
    ('GLOBAL', 'PROMO-SPRING-2026', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE', 'PROMO-SPRING-2026'),
    ('GLOBAL', 'PROMO-VIP-2026', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE', 'promo_campaign_code_val_2');

CREATE OR REPLACE TABLE bronze.src_sat_order_core (
    parent_bk__bkcc VARCHAR,
    parent_bk__order_id VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    currency_code VARCHAR,
    order_date DATE,
    order_status VARCHAR,
    order_total_usd DOUBLE
);
INSERT INTO bronze.src_sat_order_core VALUES
    ('GLOBAL', 'ORD-2026-1001', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE', 'USD', DATE '2026-01-15', 'FULFILLED', 185000.00),
    ('GLOBAL', 'ORD-2026-1002', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE', 'EUR', DATE '2026-01-18', 'FULFILLED', 92400.50),
    ('GLOBAL', 'ORD-2026-1003', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE', 'SGD', DATE '2026-01-22', 'SHIPPED', 64000.00),
    ('GLOBAL', 'ORD-2026-1004', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE', 'GBP', DATE '2026-02-01', 'FULFILLED', 310000.00),
    ('GLOBAL', 'ORD-2026-1005', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE', 'JPY', DATE '2026-02-09', 'PROCESSING', 420000.00),
    ('GLOBAL', 'ORD-2026-1006', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE', 'CAD', DATE '2026-02-14', 'FULFILLED', 48500.00);

CREATE TABLE IF NOT EXISTS raw_vault.hub_customer (
    hk_customer CHAR(64) NOT NULL PRIMARY KEY,
    bkcc VARCHAR NOT NULL,
    customer_tax_id VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.hub_customer VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', 'SYSTEM', '-1', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'SYSTEM', '-2', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.hub_order (
    hk_order CHAR(64) NOT NULL PRIMARY KEY,
    bkcc VARCHAR NOT NULL,
    order_id VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.hub_order VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', 'SYSTEM', '-1', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'SYSTEM', '-2', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.hub_promotion (
    hk_promotion CHAR(64) NOT NULL PRIMARY KEY,
    bkcc VARCHAR NOT NULL,
    promo_campaign_code VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.hub_promotion VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', 'SYSTEM', '-1', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'SYSTEM', '-2', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.lnk_order_customer (
    hk_lnk_order_customer CHAR(64) NOT NULL PRIMARY KEY,
    hk_order CHAR(64) NOT NULL,
    hk_customer CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.lnk_order_customer VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.lnk_order_customer_promotion (
    hk_lnk_order_customer_promotion CHAR(64) NOT NULL PRIMARY KEY,
    hk_order CHAR(64) NOT NULL,
    hk_promotion CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.lnk_order_customer_promotion VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.sat_customer_pii (
    hk_customer CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    contact_email VARCHAR,
    tax_ssn_tin VARCHAR,
    PRIMARY KEY (hk_customer, load_dts)
);
INSERT OR IGNORE INTO raw_vault.sat_customer_pii (hk_customer, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

CREATE TABLE IF NOT EXISTS raw_vault.sat_customer_core (
    hk_customer CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    account_segment VARCHAR,
    country_iso2 VARCHAR,
    credit_limit_usd DOUBLE,
    legal_name VARCHAR,
    PRIMARY KEY (hk_customer, load_dts)
);
INSERT OR IGNORE INTO raw_vault.sat_customer_core (hk_customer, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

CREATE TABLE IF NOT EXISTS raw_vault.sat_promotion_ref (
    hk_promotion CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    promo_campaign_code VARCHAR,
    PRIMARY KEY (hk_promotion, load_dts)
);
INSERT OR IGNORE INTO raw_vault.sat_promotion_ref (hk_promotion, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

CREATE TABLE IF NOT EXISTS raw_vault.sat_order_core (
    hk_order CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    currency_code VARCHAR,
    order_date DATE,
    order_status VARCHAR,
    order_total_usd DOUBLE,
    PRIMARY KEY (hk_order, load_dts)
);
INSERT OR IGNORE INTO raw_vault.sat_order_core (hk_order, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

-- =============================================================================
-- ZONE 0 MIGRATION: IDEMPOTENT ADDITIVE SATELLITE SCHEMA EVOLUTION (EVO-01)
-- =============================================================================
ALTER TABLE raw_vault.sat_customer_pii ADD COLUMN IF NOT EXISTS contact_email VARCHAR;
ALTER TABLE raw_vault.sat_customer_pii ADD COLUMN IF NOT EXISTS tax_ssn_tin VARCHAR;
ALTER TABLE raw_vault.sat_customer_core ADD COLUMN IF NOT EXISTS account_segment VARCHAR;
ALTER TABLE raw_vault.sat_customer_core ADD COLUMN IF NOT EXISTS country_iso2 VARCHAR;
ALTER TABLE raw_vault.sat_customer_core ADD COLUMN IF NOT EXISTS credit_limit_usd DOUBLE;
ALTER TABLE raw_vault.sat_customer_core ADD COLUMN IF NOT EXISTS legal_name VARCHAR;
ALTER TABLE raw_vault.sat_promotion_ref ADD COLUMN IF NOT EXISTS promo_campaign_code VARCHAR;
ALTER TABLE raw_vault.sat_order_core ADD COLUMN IF NOT EXISTS currency_code VARCHAR;
ALTER TABLE raw_vault.sat_order_core ADD COLUMN IF NOT EXISTS order_date DATE;
ALTER TABLE raw_vault.sat_order_core ADD COLUMN IF NOT EXISTS order_status VARCHAR;
ALTER TABLE raw_vault.sat_order_core ADD COLUMN IF NOT EXISTS order_total_usd DOUBLE;

-- =============================================================================

-- ZONE 1: HASHED STAGING VIEWS (ALGORITHM A.5 LENGTH-PREFIXED SHA-256)

-- =============================================================================

CREATE OR REPLACE VIEW staging.stg_hub_customer AS
SELECT
    CASE WHEN TRIM(CAST(customer_tax_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(customer_tax_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(customer_tax_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('6:GLOBAL' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(customer_tax_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(customer_tax_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(customer_tax_id AS VARCHAR))), '') END)) END AS hk_customer,
    'GLOBAL' AS bkcc, customer_tax_id,
    load_dts,
    rec_src
FROM bronze.src_hub_customer;

CREATE OR REPLACE VIEW staging.stg_hub_order AS
SELECT
    CASE WHEN TRIM(CAST(order_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(order_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(order_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('6:GLOBAL' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(order_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(order_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(order_id AS VARCHAR))), '') END)) END AS hk_order,
    'GLOBAL' AS bkcc, order_id,
    load_dts,
    rec_src
FROM bronze.src_hub_order;

CREATE OR REPLACE VIEW staging.stg_hub_promotion AS
SELECT
    CASE WHEN TRIM(CAST(promo_campaign_code AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(promo_campaign_code AS VARCHAR)), '') IS NULL OR TRIM(CAST(promo_campaign_code AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('6:GLOBAL' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(promo_campaign_code AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(promo_campaign_code AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(promo_campaign_code AS VARCHAR))), '') END)) END AS hk_promotion,
    'GLOBAL' AS bkcc, promo_campaign_code,
    load_dts,
    rec_src
FROM bronze.src_hub_promotion;

CREATE OR REPLACE VIEW staging.stg_lnk_order_customer AS
WITH fk_resolved AS (
    SELECT
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_order__order_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_order__order_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_order__order_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_order__order_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_order__order_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_order__order_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('6:GLOBAL' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_order__order_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_order__order_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_order__order_id AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_order,
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('6:GLOBAL' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_customer,
        load_dts,
        rec_src
    FROM bronze.src_lnk_order_customer
)
SELECT
    CASE WHEN (hk_order = REPEAT('f', 64) AND hk_customer = REPEAT('f', 64)) OR (TRIM(CAST(hk_order AS VARCHAR)) = '-2' OR TRIM(CAST(hk_customer AS VARCHAR)) = '-2') THEN REPEAT('f', 64) WHEN (hk_order = REPEAT('0', 64) AND hk_customer = REPEAT('0', 64)) OR ((NULLIF(TRIM(CAST(hk_order AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_order AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(hk_customer AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_customer AS VARCHAR)) = '-1')) THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_order AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_order AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_order AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_customer AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_customer AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_customer AS VARCHAR))), '') END)) END AS hk_lnk_order_customer,
    hk_order, hk_customer,
    load_dts,
    rec_src
FROM fk_resolved;

CREATE OR REPLACE VIEW staging.stg_lnk_order_customer_promotion AS
WITH fk_resolved AS (
    SELECT
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_order__order_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_order__order_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_order__order_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_order__order_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_order__order_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_order__order_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('6:GLOBAL' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_order__order_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_order__order_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_order__order_id AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_order,
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_promotion__promo_campaign_code AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_promotion__promo_campaign_code AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_promotion__promo_campaign_code AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_promotion__promo_campaign_code AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_promotion__promo_campaign_code AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_promotion__promo_campaign_code AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('6:GLOBAL' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_promotion__promo_campaign_code AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_promotion__promo_campaign_code AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_promotion__promo_campaign_code AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_promotion,
        load_dts,
        rec_src
    FROM bronze.src_lnk_order_customer_promotion
)
SELECT
    CASE WHEN (hk_order = REPEAT('f', 64) AND hk_promotion = REPEAT('f', 64)) OR (TRIM(CAST(hk_order AS VARCHAR)) = '-2' OR TRIM(CAST(hk_promotion AS VARCHAR)) = '-2') THEN REPEAT('f', 64) WHEN (hk_order = REPEAT('0', 64) AND hk_promotion = REPEAT('0', 64)) OR ((NULLIF(TRIM(CAST(hk_order AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_order AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(hk_promotion AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_promotion AS VARCHAR)) = '-1')) THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_order AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_order AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_order AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_promotion AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_promotion AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_promotion AS VARCHAR))), '') END)) END AS hk_lnk_order_customer_promotion,
    hk_order, hk_promotion,
    load_dts,
    rec_src
FROM fk_resolved;

CREATE OR REPLACE VIEW staging.stg_sat_customer_pii AS
SELECT
    CASE WHEN TRIM(CAST(parent_bk__customer_tax_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(parent_bk__customer_tax_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_bk__customer_tax_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('6:GLOBAL' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__customer_tax_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__customer_tax_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__customer_tax_id AS VARCHAR))), '') END)) END AS hk_customer,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(contact_email AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(contact_email AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(contact_email AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(tax_ssn_tin AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(tax_ssn_tin AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(tax_ssn_tin AS VARCHAR)), '') END)) AS hash_diff,
    contact_email, tax_ssn_tin
FROM bronze.src_sat_customer_pii;

CREATE OR REPLACE VIEW staging.stg_sat_customer_core AS
SELECT
    CASE WHEN TRIM(CAST(parent_bk__customer_tax_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(parent_bk__customer_tax_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_bk__customer_tax_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('6:GLOBAL' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__customer_tax_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__customer_tax_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__customer_tax_id AS VARCHAR))), '') END)) END AS hk_customer,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(account_segment AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(account_segment AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(account_segment AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(country_iso2 AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(country_iso2 AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(country_iso2 AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(credit_limit_usd AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(credit_limit_usd AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(credit_limit_usd AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(legal_name AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(legal_name AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(legal_name AS VARCHAR)), '') END)) AS hash_diff,
    account_segment, country_iso2, credit_limit_usd, legal_name
FROM bronze.src_sat_customer_core;

CREATE OR REPLACE VIEW staging.stg_sat_promotion_ref AS
SELECT
    CASE WHEN TRIM(CAST(parent_bk__promo_campaign_code AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(parent_bk__promo_campaign_code AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_bk__promo_campaign_code AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('6:GLOBAL' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__promo_campaign_code AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__promo_campaign_code AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__promo_campaign_code AS VARCHAR))), '') END)) END AS hk_promotion,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(promo_campaign_code AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(promo_campaign_code AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(promo_campaign_code AS VARCHAR)), '') END)) AS hash_diff,
    promo_campaign_code
FROM bronze.src_sat_promotion_ref;

CREATE OR REPLACE VIEW staging.stg_sat_order_core AS
SELECT
    CASE WHEN TRIM(CAST(parent_bk__order_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(parent_bk__order_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_bk__order_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('6:GLOBAL' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__order_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__order_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__order_id AS VARCHAR))), '') END)) END AS hk_order,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(currency_code AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(currency_code AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(currency_code AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(order_date AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(order_date AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(order_date AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(order_status AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(order_status AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(order_status AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(order_total_usd AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(order_total_usd AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(order_total_usd AS VARCHAR)), '') END)) AS hash_diff,
    currency_code, order_date, order_status, order_total_usd
FROM bronze.src_sat_order_core;

CREATE OR REPLACE VIEW staging.stg_crm_salesforce_crm_customers AS
SELECT
    CASE WHEN TRIM(CAST(customer_tax_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(customer_tax_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(customer_tax_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('6:GLOBAL' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(customer_tax_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(customer_tax_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(customer_tax_id AS VARCHAR))), '') END)) END AS hk_customer,
    'GLOBAL' AS bkcc, customer_tax_id,
    load_dts,
    rec_src
FROM bronze.src_hub_customer;

CREATE OR REPLACE VIEW staging.stg_erp_netsuite_erp_orders AS
SELECT
    CASE WHEN TRIM(CAST(order_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(order_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(order_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('6:GLOBAL' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(order_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(order_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(order_id AS VARCHAR))), '') END)) END AS hk_order,
    'GLOBAL' AS bkcc__hub_order,
    order_id,
    CASE WHEN TRIM(CAST(promo_campaign_code AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(promo_campaign_code AS VARCHAR)), '') IS NULL OR TRIM(CAST(promo_campaign_code AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('6:GLOBAL' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(promo_campaign_code AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(promo_campaign_code AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(promo_campaign_code AS VARCHAR))), '') END)) END AS hk_promotion,
    'GLOBAL' AS bkcc__hub_promotion,
    promo_campaign_code,
    load_dts,
    rec_src
FROM bronze.erp_netsuite_erp_orders;

-- =============================================================================

-- ZONE 2: IDEMPOTENT INSERT-ONLY RAW & BUSINESS VAULT LOADS

-- =============================================================================

INSERT INTO raw_vault.hub_customer (hk_customer, bkcc, customer_tax_id, load_dts, rec_src)
SELECT
    src.hk_customer,
    src.bkcc, src.customer_tax_id,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_hub_customer src
WHERE src.hk_customer NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.hub_customer h
      WHERE h.hk_customer = src.hk_customer
  )
GROUP BY src.hk_customer, src.bkcc, src.customer_tax_id;

INSERT INTO raw_vault.hub_order (hk_order, bkcc, order_id, load_dts, rec_src)
SELECT
    src.hk_order,
    src.bkcc, src.order_id,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_hub_order src
WHERE src.hk_order NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.hub_order h
      WHERE h.hk_order = src.hk_order
  )
GROUP BY src.hk_order, src.bkcc, src.order_id;

INSERT INTO raw_vault.hub_promotion (hk_promotion, bkcc, promo_campaign_code, load_dts, rec_src)
SELECT
    src.hk_promotion,
    src.bkcc, src.promo_campaign_code,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_hub_promotion src
WHERE src.hk_promotion NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.hub_promotion h
      WHERE h.hk_promotion = src.hk_promotion
  )
GROUP BY src.hk_promotion, src.bkcc, src.promo_campaign_code;

INSERT INTO raw_vault.lnk_order_customer (hk_lnk_order_customer, hk_order, hk_customer, load_dts, rec_src)
SELECT
    src.hk_lnk_order_customer,
    src.hk_order, src.hk_customer,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_lnk_order_customer src
WHERE src.hk_lnk_order_customer NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.lnk_order_customer l
      WHERE l.hk_lnk_order_customer = src.hk_lnk_order_customer
  )
GROUP BY src.hk_lnk_order_customer, src.hk_order, src.hk_customer;

INSERT INTO raw_vault.lnk_order_customer_promotion (hk_lnk_order_customer_promotion, hk_order, hk_promotion, load_dts, rec_src)
SELECT
    src.hk_lnk_order_customer_promotion,
    src.hk_order, src.hk_promotion,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_lnk_order_customer_promotion src
WHERE src.hk_lnk_order_customer_promotion NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.lnk_order_customer_promotion l
      WHERE l.hk_lnk_order_customer_promotion = src.hk_lnk_order_customer_promotion
  )
GROUP BY src.hk_lnk_order_customer_promotion, src.hk_order, src.hk_promotion;

INSERT INTO raw_vault.sat_customer_pii (
    hk_customer, load_dts, rec_src, hash_diff, contact_email, tax_ssn_tin
)
SELECT
    src.hk_customer,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.contact_email, src.tax_ssn_tin
FROM staging.stg_sat_customer_pii src
LEFT JOIN (
    SELECT hk_customer, load_dts, hash_diff
    FROM raw_vault.sat_customer_pii
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_customer ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_customer = src.hk_customer
WHERE src.hk_customer NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND (
      cur.hk_customer IS NULL
      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)
  );

INSERT INTO raw_vault.sat_customer_core (
    hk_customer, load_dts, rec_src, hash_diff, account_segment, country_iso2, credit_limit_usd, legal_name
)
SELECT
    src.hk_customer,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.account_segment, src.country_iso2, src.credit_limit_usd, src.legal_name
FROM staging.stg_sat_customer_core src
LEFT JOIN (
    SELECT hk_customer, load_dts, hash_diff
    FROM raw_vault.sat_customer_core
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_customer ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_customer = src.hk_customer
WHERE src.hk_customer NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND (
      cur.hk_customer IS NULL
      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)
  );

INSERT INTO raw_vault.sat_promotion_ref (
    hk_promotion, load_dts, rec_src, hash_diff, promo_campaign_code
)
SELECT
    src.hk_promotion,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.promo_campaign_code
FROM staging.stg_sat_promotion_ref src
LEFT JOIN (
    SELECT hk_promotion, load_dts, hash_diff
    FROM raw_vault.sat_promotion_ref
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_promotion ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_promotion = src.hk_promotion
WHERE src.hk_promotion NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND (
      cur.hk_promotion IS NULL
      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)
  );

INSERT INTO raw_vault.sat_order_core (
    hk_order, load_dts, rec_src, hash_diff, currency_code, order_date, order_status, order_total_usd
)
SELECT
    src.hk_order,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.currency_code, src.order_date, src.order_status, src.order_total_usd
FROM staging.stg_sat_order_core src
LEFT JOIN (
    SELECT hk_order, load_dts, hash_diff
    FROM raw_vault.sat_order_core
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_order ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_order = src.hk_order
WHERE src.hk_order NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND (
      cur.hk_order IS NULL
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

CREATE TABLE IF NOT EXISTS business_vault.pit_customer (
    hk_pit_customer CHAR(64) NOT NULL PRIMARY KEY,
    hk_customer CHAR(64) NOT NULL,
    as_of_dts TIMESTAMP NOT NULL,
    sat_customer_pii_hk CHAR(64) NOT NULL,
    sat_customer_pii_load_dts TIMESTAMP NOT NULL,
    sat_customer_core_hk CHAR(64) NOT NULL,
    sat_customer_core_load_dts TIMESTAMP NOT NULL
);

INSERT INTO business_vault.pit_customer (hk_pit_customer, hk_customer, as_of_dts, sat_customer_pii_hk, sat_customer_pii_load_dts, sat_customer_core_hk, sat_customer_core_load_dts)
SELECT src.hk_pit_customer, src.hk_customer, src.as_of_dts, src.sat_customer_pii_hk, src.sat_customer_pii_load_dts, src.sat_customer_core_hk, src.sat_customer_core_load_dts
FROM (
    SELECT
        LOWER(SHA256(
            '64:' || h.hk_customer || '||'
            || CAST(OCTET_LENGTH(ENCODE(CAST(cal.as_of_dts AS VARCHAR))) AS VARCHAR)
            || ':' || CAST(cal.as_of_dts AS VARCHAR)
        )) AS hk_pit_customer,
        h.hk_customer,
        cal.as_of_dts AS as_of_dts,
        COALESCE(s0.hk_customer, '0000000000000000000000000000000000000000000000000000000000000000') AS sat_customer_pii_hk,
        COALESCE(s0.load_dts, TIMESTAMP '1900-01-01 00:00:00') AS sat_customer_pii_load_dts,
        COALESCE(s1.hk_customer, '0000000000000000000000000000000000000000000000000000000000000000') AS sat_customer_core_hk,
        COALESCE(s1.load_dts, TIMESTAMP '1900-01-01 00:00:00') AS sat_customer_core_load_dts
    FROM raw_vault.hub_customer h
    CROSS JOIN business_vault.snap_logarithmic_calendar cal
    ASOF LEFT JOIN raw_vault.sat_customer_pii s0
        ON s0.hk_customer = h.hk_customer
       AND s0.load_dts <= cal.as_of_dts
    ASOF LEFT JOIN raw_vault.sat_customer_core s1
        ON s1.hk_customer = h.hk_customer
       AND s1.load_dts <= cal.as_of_dts
    WHERE h.hk_customer NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
) src
WHERE NOT EXISTS (
    SELECT 1 FROM business_vault.pit_customer p
    WHERE p.hk_pit_customer = src.hk_pit_customer
);

CREATE OR REPLACE VIEW information_mart.pit_customer AS
SELECT * FROM business_vault.pit_customer;

CREATE OR REPLACE VIEW information_mart.vw_pit_customer_current AS
SELECT
    pit.as_of_dts,
    h.hk_customer,
    h.bkcc,
    h.customer_tax_id,
    s0.contact_email AS contact_email,
    s0.tax_ssn_tin AS tax_ssn_tin,
    s1.account_segment AS account_segment,
    s1.country_iso2 AS country_iso2,
    s1.credit_limit_usd AS credit_limit_usd,
    s1.legal_name AS legal_name
FROM business_vault.pit_customer pit
INNER JOIN raw_vault.hub_customer h ON h.hk_customer = pit.hk_customer
INNER JOIN raw_vault.sat_customer_pii s0
    ON s0.hk_customer = pit.sat_customer_pii_hk
   AND s0.load_dts = pit.sat_customer_pii_load_dts
INNER JOIN raw_vault.sat_customer_core s1
    ON s1.hk_customer = pit.sat_customer_core_hk
   AND s1.load_dts = pit.sat_customer_core_load_dts;

-- ZONE 4: INFORMATION MART CURRENT VIEWS (INNER JOIN OVER PIT + GHOST)

CREATE OR REPLACE VIEW information_mart.vw_pit_customer_current AS
SELECT
    pit.as_of_dts,
    h.hk_customer,
    h.bkcc,
    h.customer_tax_id,
    s0.contact_email AS contact_email,
    s0.tax_ssn_tin AS tax_ssn_tin,
    s1.account_segment AS account_segment,
    s1.country_iso2 AS country_iso2,
    s1.credit_limit_usd AS credit_limit_usd,
    s1.legal_name AS legal_name
FROM business_vault.pit_customer pit
INNER JOIN raw_vault.hub_customer h ON h.hk_customer = pit.hk_customer
INNER JOIN raw_vault.sat_customer_pii s0
    ON s0.hk_customer = pit.sat_customer_pii_hk
   AND s0.load_dts = pit.sat_customer_pii_load_dts
INNER JOIN raw_vault.sat_customer_core s1
    ON s1.hk_customer = pit.sat_customer_core_hk
   AND s1.load_dts = pit.sat_customer_core_load_dts;

SELECT * FROM information_mart.vw_pit_customer_current;

-- ZONE 4: BI-TEMPORAL POINT-IN-TIME AUDIT QUERY
SELECT *
FROM information_mart.vw_pit_customer_current
WHERE as_of_dts = TIMESTAMP '2026-11-15 23:59:59';