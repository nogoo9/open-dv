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
    ('GLOBAL', 'CUSTOMER-001', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE'),
    ('GLOBAL', 'CUSTOMER-002', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE');

CREATE OR REPLACE TABLE bronze.src_hub_order (
    bkcc VARCHAR,
    order_id VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_hub_order VALUES
    ('GLOBAL', 'ORDER-001', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE'),
    ('GLOBAL', 'ORDER-002', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE');

CREATE OR REPLACE TABLE bronze.src_hub_promotion (
    bkcc VARCHAR,
    promo_campaign_code VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_hub_promotion VALUES
    ('GLOBAL', 'PROMOTION-001', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE'),
    ('GLOBAL', 'PROMOTION-002', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE');

CREATE OR REPLACE TABLE bronze.src_hub_invoice (
    bkcc VARCHAR,
    invoice_number VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_hub_invoice VALUES
    ('STRIPE_EU', 'INVOICE-001', TIMESTAMP '2026-10-01 06:00:00', 'STRIPE_EU'),
    ('STRIPE_EU', 'INVOICE-002', TIMESTAMP '2026-10-01 06:00:00', 'STRIPE_EU');

CREATE OR REPLACE TABLE bronze.src_hub_document (
    bkcc VARCHAR,
    customer_tax_id VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_hub_document VALUES
    ('GLOBAL', 'DOCUMENT-001', TIMESTAMP '2026-10-01 06:00:00', 'LEGAL_DOC_VAULT'),
    ('GLOBAL', 'DOCUMENT-002', TIMESTAMP '2026-10-01 06:00:00', 'LEGAL_DOC_VAULT');

CREATE OR REPLACE TABLE bronze.crm_salesforce_crm_customers (
    customer_tax_id VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.crm_salesforce_crm_customers VALUES
    ('CUSTOMER-001', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE'),
    ('CUSTOMER-002', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE');

CREATE OR REPLACE TABLE bronze.erp_netsuite_erp_orders (
    order_id VARCHAR,
    promo_campaign_code VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.erp_netsuite_erp_orders VALUES
    ('ORDER-001', 'PROMOTION-001', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE'),
    ('ORDER-002', 'PROMOTION-002', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE');

CREATE OR REPLACE VIEW bronze.billing_stripe_stripe_invoices AS SELECT * FROM bronze.src_hub_invoice;

CREATE OR REPLACE TABLE bronze.src_lnk_order_customer (
    hk_order__bkcc VARCHAR,
    hk_order__order_id VARCHAR,
    hk_customer__bkcc VARCHAR,
    hk_customer__customer_tax_id VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_lnk_order_customer VALUES
    ('GLOBAL', 'ORDER-001', 'GLOBAL', 'CUSTOMER-001', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE'),
    ('GLOBAL', 'ORDER-002', 'GLOBAL', NULL, TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE'),
    ('GLOBAL', 'ORDER-001', 'GLOBAL', '-1', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE'),
    ('GLOBAL', 'ORDER-002', 'GLOBAL', '-2', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE');

CREATE OR REPLACE TABLE bronze.src_lnk_order_customer_promotion (
    hk_order__bkcc VARCHAR,
    hk_order__order_id VARCHAR,
    hk_promotion__bkcc VARCHAR,
    hk_promotion__promo_campaign_code VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_lnk_order_customer_promotion VALUES
    ('GLOBAL', 'ORDER-001', 'GLOBAL', 'PROMOTION-001', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE'),
    ('GLOBAL', 'ORDER-002', 'GLOBAL', NULL, TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE'),
    ('GLOBAL', 'ORDER-001', 'GLOBAL', '-1', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE'),
    ('GLOBAL', 'ORDER-002', 'GLOBAL', '-2', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE');

CREATE OR REPLACE TABLE bronze.src_lnk_invoice_customer_order (
    hk_invoice__bkcc VARCHAR,
    hk_invoice__invoice_number VARCHAR,
    hk_customer__bkcc VARCHAR,
    hk_customer__customer_tax_id VARCHAR,
    hk_order__bkcc VARCHAR,
    hk_order__order_id VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR
);
INSERT INTO bronze.src_lnk_invoice_customer_order VALUES
    ('STRIPE_EU', 'INVOICE-001', 'GLOBAL', 'CUSTOMER-001', 'GLOBAL', 'ORDER-001', TIMESTAMP '2026-10-01 06:00:00', 'STRIPE_EU'),
    ('STRIPE_EU', 'INVOICE-002', 'GLOBAL', NULL, 'GLOBAL', NULL, TIMESTAMP '2026-10-01 06:00:00', 'STRIPE_EU'),
    ('STRIPE_EU', 'INVOICE-001', 'GLOBAL', '-1', 'GLOBAL', '-1', TIMESTAMP '2026-10-01 06:00:00', 'STRIPE_EU'),
    ('STRIPE_EU', 'INVOICE-002', 'GLOBAL', '-2', 'GLOBAL', '-2', TIMESTAMP '2026-10-01 06:00:00', 'STRIPE_EU');

CREATE OR REPLACE TABLE bronze.src_lnk_document_customer (
    hk_document__bkcc VARCHAR,
    hk_document__customer_tax_id VARCHAR,
    hk_customer__bkcc VARCHAR,
    hk_customer__customer_tax_id VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    steward_gate_status VARCHAR
);
INSERT INTO bronze.src_lnk_document_customer VALUES
    ('GLOBAL', 'DOCUMENT-001', 'GLOBAL', 'CUSTOMER-001', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE', 'APPROVED'),
    ('GLOBAL', 'DOCUMENT-002', 'GLOBAL', NULL, TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE', 'STEWARD_HOLD'),
    ('GLOBAL', 'DOCUMENT-001', 'GLOBAL', '-1', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE', 'STEWARD_HOLD'),
    ('GLOBAL', 'DOCUMENT-002', 'GLOBAL', '-2', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE', 'STEWARD_HOLD');

CREATE OR REPLACE TABLE bronze.src_sat_customer_pii (
    parent_bk__bkcc VARCHAR,
    parent_bk__customer_tax_id VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    contact_email VARCHAR,
    tax_ssn_tin VARCHAR
);
INSERT INTO bronze.src_sat_customer_pii VALUES
    ('GLOBAL', 'CUSTOMER-001', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE', 'contact_email_val_1', 'tax_ssn_tin_val_1'),
    ('GLOBAL', 'CUSTOMER-002', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE', 'contact_email_val_2', 'tax_ssn_tin_val_2');

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
    ('GLOBAL', 'CUSTOMER-001', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE', 'account_segment_val_1', 'country_iso2_val_1', 1000.50, 'legal_name_val_1'),
    ('GLOBAL', 'CUSTOMER-002', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE', 'account_segment_val_2', 'country_iso2_val_2', 2000.50, 'legal_name_val_2');

CREATE OR REPLACE TABLE bronze.src_sat_customer_core_ext_v2 (
    parent_bk__bkcc VARCHAR,
    parent_bk__customer_tax_id VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    annual_spend_band VARCHAR,
    loyalty_tier_code VARCHAR
);
INSERT INTO bronze.src_sat_customer_core_ext_v2 VALUES
    ('GLOBAL', 'CUSTOMER-001', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE', 'annual_spend_band_val_1', 'loyalty_tier_code_val_1'),
    ('GLOBAL', 'CUSTOMER-002', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE', 'annual_spend_band_val_2', 'loyalty_tier_code_val_2');

CREATE OR REPLACE TABLE bronze.src_sat_promotion_ref (
    parent_bk__bkcc VARCHAR,
    parent_bk__promo_campaign_code VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    promo_campaign_code VARCHAR
);
INSERT INTO bronze.src_sat_promotion_ref VALUES
    ('GLOBAL', 'PROMOTION-001', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE', 'promo_campaign_code_val_1'),
    ('GLOBAL', 'PROMOTION-002', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE', 'promo_campaign_code_val_2');

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
    ('GLOBAL', 'ORDER-001', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE', 'currency_code_val_1', DATE '2026-09-30', 'order_status_val_1', 1000.50),
    ('GLOBAL', 'ORDER-002', TIMESTAMP '2026-10-01 06:00:00', 'ERP_NETSUITE', 'currency_code_val_2', DATE '2026-09-30', 'order_status_val_2', 2000.50);

CREATE OR REPLACE TABLE bronze.src_sat_invoice_core (
    parent_bk__bkcc VARCHAR,
    parent_bk__invoice_number VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    invoice_amount_usd DOUBLE,
    settlement_status VARCHAR
);
INSERT INTO bronze.src_sat_invoice_core VALUES
    ('STRIPE_EU', 'INVOICE-001', TIMESTAMP '2026-10-01 06:00:00', 'STRIPE_EU', 1000.50, 'settlement_status_val_1'),
    ('STRIPE_EU', 'INVOICE-002', TIMESTAMP '2026-10-01 06:00:00', 'STRIPE_EU', 2000.50, 'settlement_status_val_2');

CREATE OR REPLACE TABLE bronze.src_sat_document_customer_ai_provenance (
    parent_fk__hk_document VARCHAR,
    parent_fk__hk_customer VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    confidence_score DECIMAL(10,4),
    evidence_quote VARCHAR,
    extractor_model_id VARCHAR,
    steward_gate_status VARCHAR DEFAULT 'APPROVED'
);
INSERT INTO bronze.src_sat_document_customer_ai_provenance VALUES
    ('f3265a555edb0c65ba1ed27f627d904657e0a51d0821f403d7194d807e5a96c3', 'afff6ad9e40d04b38a44d1794a35619d8e11c172106674eea1e971f76ab9cb93', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE', 0.9500, 'Verified contractual clause quote #1 from source document.', 'gemini-2.5-pro', 'APPROVED'),
    ('89e2c58d513cd51f9bb12e0d6bce6a0732db3a1cf68dcd3c191e4871561c113d', '1681a07e1b50ae7ef341f131ca437d013556cad9402c95a613c880b40b6d483d', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE', 0.9200, 'Verified contractual clause quote #2 from source document.', 'gemini-2.5-pro', 'STEWARD_HOLD');

CREATE OR REPLACE TABLE bronze.src_mas_document_extractions (
    parent_bk__bkcc VARCHAR,
    parent_bk__customer_tax_id VARCHAR,
    load_dts TIMESTAMP,
    rec_src VARCHAR,
    extraction_seq INTEGER,
    extracted_business_key VARCHAR,
    verbatim_quote VARCHAR
);
INSERT INTO bronze.src_mas_document_extractions VALUES
    ('GLOBAL', 'DOCUMENT-001', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE', 100, 'extracted_business_key_val_1', 'Verified contractual clause quote #1 from source document.'),
    ('GLOBAL', 'DOCUMENT-002', TIMESTAMP '2026-10-01 06:00:00', 'CRM_SALESFORCE', 200, 'extracted_business_key_val_2', 'Verified contractual clause quote #2 from source document.');

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

CREATE TABLE IF NOT EXISTS raw_vault.hub_invoice (
    hk_invoice CHAR(64) NOT NULL PRIMARY KEY,
    bkcc VARCHAR NOT NULL,
    invoice_number VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.hub_invoice VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', 'SYSTEM', '-1', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'SYSTEM', '-2', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.hub_document (
    hk_document CHAR(64) NOT NULL PRIMARY KEY,
    bkcc VARCHAR NOT NULL,
    customer_tax_id VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.hub_document VALUES
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

CREATE TABLE IF NOT EXISTS raw_vault.lnk_invoice_customer_order (
    hk_lnk_invoice_customer_order CHAR(64) NOT NULL PRIMARY KEY,
    hk_invoice CHAR(64) NOT NULL,
    hk_customer CHAR(64) NOT NULL,
    hk_order CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.lnk_invoice_customer_order VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', '0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
    ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');

CREATE TABLE IF NOT EXISTS raw_vault.lnk_document_customer (
    hk_lnk_document_customer CHAR(64) NOT NULL PRIMARY KEY,
    hk_document CHAR(64) NOT NULL,
    hk_customer CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL
);
INSERT OR IGNORE INTO raw_vault.lnk_document_customer VALUES
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

CREATE TABLE IF NOT EXISTS raw_vault.sat_customer_core_ext_v2 (
    hk_customer CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    annual_spend_band VARCHAR,
    loyalty_tier_code VARCHAR,
    PRIMARY KEY (hk_customer, load_dts)
);
INSERT OR IGNORE INTO raw_vault.sat_customer_core_ext_v2 (hk_customer, load_dts, rec_src, hash_diff) VALUES
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

CREATE TABLE IF NOT EXISTS raw_vault.sat_invoice_core (
    hk_invoice CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    invoice_amount_usd DOUBLE,
    settlement_status VARCHAR,
    PRIMARY KEY (hk_invoice, load_dts)
);
INSERT OR IGNORE INTO raw_vault.sat_invoice_core (hk_invoice, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

CREATE TABLE IF NOT EXISTS raw_vault.sat_document_customer_ai_provenance (
    hk_lnk_document_customer CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    confidence_score DECIMAL(10,4),
    evidence_quote VARCHAR,
    extractor_model_id VARCHAR,
    PRIMARY KEY (hk_lnk_document_customer, load_dts)
);
INSERT OR IGNORE INTO raw_vault.sat_document_customer_ai_provenance (hk_lnk_document_customer, load_dts, rec_src, hash_diff) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000');

CREATE TABLE IF NOT EXISTS raw_vault.mas_document_extractions (
    hk_document CHAR(64) NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src VARCHAR NOT NULL,
    hash_diff CHAR(64) NOT NULL,
    extraction_seq INTEGER NOT NULL,
    extracted_business_key VARCHAR,
    verbatim_quote VARCHAR,
    PRIMARY KEY (hk_document, load_dts, extraction_seq)
);
INSERT OR IGNORE INTO raw_vault.mas_document_extractions (hk_document, load_dts, rec_src, hash_diff, extraction_seq) VALUES
    ('0000000000000000000000000000000000000000000000000000000000000000', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_GHOST', '0000000000000000000000000000000000000000000000000000000000000000', 0);

-- =============================================================================
-- ZONE 0 MIGRATION: IDEMPOTENT ADDITIVE SATELLITE SCHEMA EVOLUTION (EVO-01)
-- =============================================================================
ALTER TABLE raw_vault.sat_customer_pii ADD COLUMN IF NOT EXISTS contact_email VARCHAR;
ALTER TABLE raw_vault.sat_customer_pii ADD COLUMN IF NOT EXISTS tax_ssn_tin VARCHAR;
ALTER TABLE raw_vault.sat_customer_core ADD COLUMN IF NOT EXISTS account_segment VARCHAR;
ALTER TABLE raw_vault.sat_customer_core ADD COLUMN IF NOT EXISTS country_iso2 VARCHAR;
ALTER TABLE raw_vault.sat_customer_core ADD COLUMN IF NOT EXISTS credit_limit_usd DOUBLE;
ALTER TABLE raw_vault.sat_customer_core ADD COLUMN IF NOT EXISTS legal_name VARCHAR;
ALTER TABLE raw_vault.sat_customer_core_ext_v2 ADD COLUMN IF NOT EXISTS annual_spend_band VARCHAR;
ALTER TABLE raw_vault.sat_customer_core_ext_v2 ADD COLUMN IF NOT EXISTS loyalty_tier_code VARCHAR;
ALTER TABLE raw_vault.sat_promotion_ref ADD COLUMN IF NOT EXISTS promo_campaign_code VARCHAR;
ALTER TABLE raw_vault.sat_order_core ADD COLUMN IF NOT EXISTS currency_code VARCHAR;
ALTER TABLE raw_vault.sat_order_core ADD COLUMN IF NOT EXISTS order_date DATE;
ALTER TABLE raw_vault.sat_order_core ADD COLUMN IF NOT EXISTS order_status VARCHAR;
ALTER TABLE raw_vault.sat_order_core ADD COLUMN IF NOT EXISTS order_total_usd DOUBLE;
ALTER TABLE raw_vault.sat_invoice_core ADD COLUMN IF NOT EXISTS invoice_amount_usd DOUBLE;
ALTER TABLE raw_vault.sat_invoice_core ADD COLUMN IF NOT EXISTS settlement_status VARCHAR;
ALTER TABLE raw_vault.sat_document_customer_ai_provenance ADD COLUMN IF NOT EXISTS confidence_score DECIMAL(10,4);
ALTER TABLE raw_vault.sat_document_customer_ai_provenance ADD COLUMN IF NOT EXISTS evidence_quote VARCHAR;
ALTER TABLE raw_vault.sat_document_customer_ai_provenance ADD COLUMN IF NOT EXISTS extractor_model_id VARCHAR;
ALTER TABLE raw_vault.mas_document_extractions ADD COLUMN IF NOT EXISTS extracted_business_key VARCHAR;
ALTER TABLE raw_vault.mas_document_extractions ADD COLUMN IF NOT EXISTS verbatim_quote VARCHAR;

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

CREATE OR REPLACE VIEW staging.stg_hub_invoice AS
SELECT
    CASE WHEN TRIM(CAST(invoice_number AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(invoice_number AS VARCHAR)), '') IS NULL OR TRIM(CAST(invoice_number AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('9:STRIPE_EU' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(invoice_number AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(invoice_number AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(invoice_number AS VARCHAR))), '') END)) END AS hk_invoice,
    'STRIPE_EU' AS bkcc, invoice_number,
    load_dts,
    rec_src
FROM bronze.src_hub_invoice;

CREATE OR REPLACE VIEW staging.stg_hub_document AS
SELECT
    CASE WHEN TRIM(CAST(customer_tax_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(customer_tax_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(customer_tax_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('6:GLOBAL' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(customer_tax_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(customer_tax_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(customer_tax_id AS VARCHAR))), '') END)) END AS hk_document,
    'GLOBAL' AS bkcc, customer_tax_id,
    load_dts,
    rec_src
FROM bronze.src_hub_document;

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

CREATE OR REPLACE VIEW staging.stg_lnk_invoice_customer_order AS
WITH fk_resolved AS (
    SELECT
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_invoice__invoice_number AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_invoice__invoice_number AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_invoice__invoice_number AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_invoice__invoice_number AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_invoice__invoice_number AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_invoice__invoice_number AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('9:STRIPE_EU' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_invoice__invoice_number AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_invoice__invoice_number AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_invoice__invoice_number AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_invoice,
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('6:GLOBAL' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_customer,
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_order__order_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_order__order_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_order__order_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_order__order_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_order__order_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_order__order_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('6:GLOBAL' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_order__order_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_order__order_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_order__order_id AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_order,
        load_dts,
        rec_src
    FROM bronze.src_lnk_invoice_customer_order
)
SELECT
    CASE WHEN (hk_invoice = REPEAT('f', 64) AND hk_customer = REPEAT('f', 64) AND hk_order = REPEAT('f', 64)) OR (TRIM(CAST(hk_invoice AS VARCHAR)) = '-2' OR TRIM(CAST(hk_customer AS VARCHAR)) = '-2' OR TRIM(CAST(hk_order AS VARCHAR)) = '-2') THEN REPEAT('f', 64) WHEN (hk_invoice = REPEAT('0', 64) AND hk_customer = REPEAT('0', 64) AND hk_order = REPEAT('0', 64)) OR ((NULLIF(TRIM(CAST(hk_invoice AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_invoice AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(hk_customer AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_customer AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(hk_order AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_order AS VARCHAR)) = '-1')) THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_invoice AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_invoice AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_invoice AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_customer AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_customer AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_customer AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_order AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_order AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_order AS VARCHAR))), '') END)) END AS hk_lnk_invoice_customer_order,
    hk_invoice, hk_customer, hk_order,
    load_dts,
    rec_src
FROM fk_resolved;

CREATE OR REPLACE VIEW staging.stg_lnk_document_customer AS
WITH fk_resolved AS (
    SELECT
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_document__customer_tax_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_document__customer_tax_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_document__customer_tax_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_document__customer_tax_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_document__customer_tax_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_document__customer_tax_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('6:GLOBAL' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_document__customer_tax_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_document__customer_tax_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_document__customer_tax_id AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_document,
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('6:GLOBAL' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_customer,
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
    FROM bronze.src_lnk_document_customer
)
SELECT
    CASE WHEN (hk_document = REPEAT('f', 64) AND hk_customer = REPEAT('f', 64)) OR (TRIM(CAST(hk_document AS VARCHAR)) = '-2' OR TRIM(CAST(hk_customer AS VARCHAR)) = '-2') THEN REPEAT('f', 64) WHEN (hk_document = REPEAT('0', 64) AND hk_customer = REPEAT('0', 64)) OR ((NULLIF(TRIM(CAST(hk_document AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_document AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(hk_customer AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_customer AS VARCHAR)) = '-1')) THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_document AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_document AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_document AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_customer AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_customer AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_customer AS VARCHAR))), '') END)) END AS hk_lnk_document_customer,
    hk_document, hk_customer,
    load_dts,
    rec_src,
    steward_gate_status
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

CREATE OR REPLACE VIEW staging.stg_sat_customer_core_ext_v2 AS
SELECT
    CASE WHEN TRIM(CAST(parent_bk__customer_tax_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(parent_bk__customer_tax_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_bk__customer_tax_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('6:GLOBAL' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__customer_tax_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__customer_tax_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__customer_tax_id AS VARCHAR))), '') END)) END AS hk_customer,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(annual_spend_band AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(annual_spend_band AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(annual_spend_band AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(loyalty_tier_code AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(loyalty_tier_code AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(loyalty_tier_code AS VARCHAR)), '') END)) AS hash_diff,
    annual_spend_band, loyalty_tier_code
FROM bronze.src_sat_customer_core_ext_v2;

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

CREATE OR REPLACE VIEW staging.stg_sat_invoice_core AS
SELECT
    CASE WHEN TRIM(CAST(parent_bk__invoice_number AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(parent_bk__invoice_number AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_bk__invoice_number AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('9:STRIPE_EU' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__invoice_number AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__invoice_number AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__invoice_number AS VARCHAR))), '') END)) END AS hk_invoice,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(invoice_amount_usd AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(invoice_amount_usd AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(invoice_amount_usd AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(settlement_status AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(settlement_status AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(settlement_status AS VARCHAR)), '') END)) AS hash_diff,
    invoice_amount_usd, settlement_status
FROM bronze.src_sat_invoice_core;

CREATE OR REPLACE VIEW staging.stg_sat_document_customer_ai_provenance AS
SELECT
    CASE WHEN (parent_fk__hk_document = REPEAT('f', 64) AND parent_fk__hk_customer = REPEAT('f', 64)) OR (TRIM(CAST(parent_fk__hk_document AS VARCHAR)) = '-2' OR TRIM(CAST(parent_fk__hk_customer AS VARCHAR)) = '-2') THEN REPEAT('f', 64) WHEN (parent_fk__hk_document = REPEAT('0', 64) AND parent_fk__hk_customer = REPEAT('0', 64)) OR ((NULLIF(TRIM(CAST(parent_fk__hk_document AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_fk__hk_document AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(parent_fk__hk_customer AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_fk__hk_customer AS VARCHAR)) = '-1')) THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_fk__hk_document AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_fk__hk_document AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_fk__hk_document AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_fk__hk_customer AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_fk__hk_customer AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_fk__hk_customer AS VARCHAR))), '') END)) END AS hk_lnk_document_customer,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(confidence_score AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(confidence_score AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(confidence_score AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(evidence_quote AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(evidence_quote AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(evidence_quote AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(extractor_model_id AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(extractor_model_id AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(extractor_model_id AS VARCHAR)), '') END)) AS hash_diff,
    confidence_score, evidence_quote, extractor_model_id,
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
FROM bronze.src_sat_document_customer_ai_provenance;

CREATE OR REPLACE VIEW staging.stg_mas_document_extractions AS
SELECT
    CASE WHEN TRIM(CAST(parent_bk__customer_tax_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(parent_bk__customer_tax_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(parent_bk__customer_tax_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('6:GLOBAL' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(parent_bk__customer_tax_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(parent_bk__customer_tax_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(parent_bk__customer_tax_id AS VARCHAR))), '') END)) END AS hk_document,
    load_dts,
    rec_src,
    LOWER(SHA256(CASE WHEN NULLIF(TRIM(CAST(extraction_seq AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(extraction_seq AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(extraction_seq AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(extracted_business_key AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(extracted_business_key AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(extracted_business_key AS VARCHAR)), '') END || '||' || CASE WHEN NULLIF(TRIM(CAST(verbatim_quote AS VARCHAR)), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(TRIM(CAST(verbatim_quote AS VARCHAR)), ''))) AS VARCHAR) || ':' || NULLIF(TRIM(CAST(verbatim_quote AS VARCHAR)), '') END)) AS hash_diff,
    extraction_seq, extracted_business_key, verbatim_quote
FROM bronze.src_mas_document_extractions;

CREATE OR REPLACE VIEW staging.stg_crm_salesforce_crm_customers AS
SELECT
    CASE WHEN TRIM(CAST(customer_tax_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(customer_tax_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(customer_tax_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('6:GLOBAL' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(customer_tax_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(customer_tax_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(customer_tax_id AS VARCHAR))), '') END)) END AS hk_customer,
    'GLOBAL' AS bkcc__hub_customer,
    customer_tax_id,
    CASE WHEN TRIM(CAST(customer_tax_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(customer_tax_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(customer_tax_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('6:GLOBAL' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(customer_tax_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(customer_tax_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(customer_tax_id AS VARCHAR))), '') END)) END AS hk_document,
    'GLOBAL' AS bkcc__hub_document,
    load_dts,
    rec_src
FROM bronze.crm_salesforce_crm_customers;

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

CREATE OR REPLACE VIEW staging.stg_billing_stripe_stripe_invoices AS
SELECT
    CASE WHEN TRIM(CAST(invoice_number AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(invoice_number AS VARCHAR)), '') IS NULL OR TRIM(CAST(invoice_number AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('9:STRIPE_EU' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(invoice_number AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(invoice_number AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(invoice_number AS VARCHAR))), '') END)) END AS hk_invoice,
    'STRIPE_EU' AS bkcc, invoice_number,
    load_dts,
    rec_src
FROM bronze.src_hub_invoice;

CREATE OR REPLACE VIEW staging.stg_unstructured_vdr_llm_extractions AS
WITH fk_resolved AS (
    SELECT
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_document__customer_tax_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_document__customer_tax_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_document__customer_tax_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_document__customer_tax_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_document__customer_tax_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_document__customer_tax_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('6:GLOBAL' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_document__customer_tax_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_document__customer_tax_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_document__customer_tax_id AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_document,
        COALESCE(
            CASE
                WHEN TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64)
                WHEN (NULLIF(TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64)
                ELSE CASE WHEN TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR)) = '-2' THEN REPEAT('f', 64) WHEN (NULLIF(TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR)) = '-1') THEN REPEAT('0', 64) ELSE LOWER(SHA256('6:GLOBAL' || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_customer__customer_tax_id AS VARCHAR))), '') END)) END
            END,
            REPEAT('0', 64)
        ) AS hk_customer,
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
    FROM bronze.src_lnk_document_customer
)
SELECT
    CASE WHEN (hk_document = REPEAT('f', 64) AND hk_customer = REPEAT('f', 64)) OR (TRIM(CAST(hk_document AS VARCHAR)) = '-2' OR TRIM(CAST(hk_customer AS VARCHAR)) = '-2') THEN REPEAT('f', 64) WHEN (hk_document = REPEAT('0', 64) AND hk_customer = REPEAT('0', 64)) OR ((NULLIF(TRIM(CAST(hk_document AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_document AS VARCHAR)) = '-1') OR (NULLIF(TRIM(CAST(hk_customer AS VARCHAR)), '') IS NULL OR TRIM(CAST(hk_customer AS VARCHAR)) = '-1')) THEN REPEAT('0', 64) ELSE LOWER(SHA256(CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_document AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_document AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_document AS VARCHAR))), '') END || '||' || CASE WHEN NULLIF(UPPER(TRIM(CAST(hk_customer AS VARCHAR))), '') IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(hk_customer AS VARCHAR))), ''))) AS VARCHAR) || ':' || NULLIF(UPPER(TRIM(CAST(hk_customer AS VARCHAR))), '') END)) END AS hk_lnk_document_customer,
    hk_document, hk_customer,
    load_dts,
    rec_src,
    steward_gate_status
FROM fk_resolved;

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

INSERT INTO raw_vault.hub_invoice (hk_invoice, bkcc, invoice_number, load_dts, rec_src)
SELECT
    src.hk_invoice,
    src.bkcc, src.invoice_number,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_hub_invoice src
WHERE src.hk_invoice NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.hub_invoice h
      WHERE h.hk_invoice = src.hk_invoice
  )
GROUP BY src.hk_invoice, src.bkcc, src.invoice_number;

INSERT INTO raw_vault.hub_document (hk_document, bkcc, customer_tax_id, load_dts, rec_src)
SELECT
    src.hk_document,
    src.bkcc, src.customer_tax_id,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_hub_document src
WHERE src.hk_document NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.hub_document h
      WHERE h.hk_document = src.hk_document
  )
GROUP BY src.hk_document, src.bkcc, src.customer_tax_id;

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

INSERT INTO raw_vault.lnk_invoice_customer_order (hk_lnk_invoice_customer_order, hk_invoice, hk_customer, hk_order, load_dts, rec_src)
SELECT
    src.hk_lnk_invoice_customer_order,
    src.hk_invoice, src.hk_customer, src.hk_order,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_lnk_invoice_customer_order src
WHERE src.hk_lnk_invoice_customer_order NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.lnk_invoice_customer_order l
      WHERE l.hk_lnk_invoice_customer_order = src.hk_lnk_invoice_customer_order
  )
GROUP BY src.hk_lnk_invoice_customer_order, src.hk_invoice, src.hk_customer, src.hk_order;

INSERT INTO raw_vault.lnk_document_customer (hk_lnk_document_customer, hk_document, hk_customer, load_dts, rec_src)
SELECT
    src.hk_lnk_document_customer,
    src.hk_document, src.hk_customer,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_lnk_document_customer src
WHERE src.hk_lnk_document_customer NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND src.steward_gate_status = 'APPROVED'
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.lnk_document_customer l
      WHERE l.hk_lnk_document_customer = src.hk_lnk_document_customer
  )
GROUP BY src.hk_lnk_document_customer, src.hk_document, src.hk_customer;

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

INSERT INTO raw_vault.sat_customer_core_ext_v2 (
    hk_customer, load_dts, rec_src, hash_diff, annual_spend_band, loyalty_tier_code
)
SELECT
    src.hk_customer,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.annual_spend_band, src.loyalty_tier_code
FROM staging.stg_sat_customer_core_ext_v2 src
LEFT JOIN (
    SELECT hk_customer, load_dts, hash_diff
    FROM raw_vault.sat_customer_core_ext_v2
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

INSERT INTO raw_vault.sat_invoice_core (
    hk_invoice, load_dts, rec_src, hash_diff, invoice_amount_usd, settlement_status
)
SELECT
    src.hk_invoice,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.invoice_amount_usd, src.settlement_status
FROM staging.stg_sat_invoice_core src
LEFT JOIN (
    SELECT hk_invoice, load_dts, hash_diff
    FROM raw_vault.sat_invoice_core
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_invoice ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_invoice = src.hk_invoice
WHERE src.hk_invoice NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND (
      cur.hk_invoice IS NULL
      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)
  );

INSERT INTO raw_vault.sat_document_customer_ai_provenance (
    hk_lnk_document_customer, load_dts, rec_src, hash_diff, confidence_score, evidence_quote, extractor_model_id
)
SELECT
    src.hk_lnk_document_customer,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.confidence_score, src.evidence_quote, src.extractor_model_id
FROM staging.stg_sat_document_customer_ai_provenance src
LEFT JOIN (
    SELECT hk_lnk_document_customer, load_dts, hash_diff
    FROM raw_vault.sat_document_customer_ai_provenance
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_lnk_document_customer ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_lnk_document_customer = src.hk_lnk_document_customer
WHERE src.hk_lnk_document_customer NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND EXISTS (
      SELECT 1 FROM raw_vault.lnk_document_customer pl
      WHERE pl.hk_lnk_document_customer = src.hk_lnk_document_customer
  )
  AND src.steward_gate_status = 'APPROVED'
  AND (
      cur.hk_lnk_document_customer IS NULL
      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)
  );

INSERT INTO raw_vault.mas_document_extractions (
    hk_document, load_dts, rec_src, hash_diff, extraction_seq, extracted_business_key, verbatim_quote
)
SELECT
    src.hk_document,
    src.load_dts,
    src.rec_src,
    src.hash_diff,
    src.extraction_seq, src.extracted_business_key, src.verbatim_quote
FROM staging.stg_mas_document_extractions src
LEFT JOIN (
    SELECT hk_document, extraction_seq, load_dts, hash_diff
    FROM raw_vault.mas_document_extractions
    WHERE rec_src <> 'SYSTEM_GHOST'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY hk_document, extraction_seq ORDER BY load_dts DESC
    ) = 1
) cur ON cur.hk_document = src.hk_document AND cur.extraction_seq = src.extraction_seq
WHERE src.hk_document NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
  AND (
      cur.hk_document IS NULL
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
    sat_customer_core_load_dts TIMESTAMP NOT NULL,
    sat_customer_core_ext_v2_hk CHAR(64) NOT NULL,
    sat_customer_core_ext_v2_load_dts TIMESTAMP NOT NULL
);

INSERT INTO business_vault.pit_customer (hk_pit_customer, hk_customer, as_of_dts, sat_customer_pii_hk, sat_customer_pii_load_dts, sat_customer_core_hk, sat_customer_core_load_dts, sat_customer_core_ext_v2_hk, sat_customer_core_ext_v2_load_dts)
SELECT src.hk_pit_customer, src.hk_customer, src.as_of_dts, src.sat_customer_pii_hk, src.sat_customer_pii_load_dts, src.sat_customer_core_hk, src.sat_customer_core_load_dts, src.sat_customer_core_ext_v2_hk, src.sat_customer_core_ext_v2_load_dts
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
        COALESCE(s1.load_dts, TIMESTAMP '1900-01-01 00:00:00') AS sat_customer_core_load_dts,
        COALESCE(s2.hk_customer, '0000000000000000000000000000000000000000000000000000000000000000') AS sat_customer_core_ext_v2_hk,
        COALESCE(s2.load_dts, TIMESTAMP '1900-01-01 00:00:00') AS sat_customer_core_ext_v2_load_dts
    FROM raw_vault.hub_customer h
    CROSS JOIN business_vault.snap_logarithmic_calendar cal
    ASOF LEFT JOIN raw_vault.sat_customer_pii s0
        ON s0.hk_customer = h.hk_customer
       AND s0.load_dts <= cal.as_of_dts
    ASOF LEFT JOIN raw_vault.sat_customer_core s1
        ON s1.hk_customer = h.hk_customer
       AND s1.load_dts <= cal.as_of_dts
    ASOF LEFT JOIN raw_vault.sat_customer_core_ext_v2 s2
        ON s2.hk_customer = h.hk_customer
       AND s2.load_dts <= cal.as_of_dts
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
    s1.legal_name AS legal_name,
    s2.annual_spend_band AS annual_spend_band,
    s2.loyalty_tier_code AS loyalty_tier_code
FROM business_vault.pit_customer pit
INNER JOIN raw_vault.hub_customer h ON h.hk_customer = pit.hk_customer
INNER JOIN raw_vault.sat_customer_pii s0
    ON s0.hk_customer = pit.sat_customer_pii_hk
   AND s0.load_dts = pit.sat_customer_pii_load_dts
INNER JOIN raw_vault.sat_customer_core s1
    ON s1.hk_customer = pit.sat_customer_core_hk
   AND s1.load_dts = pit.sat_customer_core_load_dts
INNER JOIN raw_vault.sat_customer_core_ext_v2 s2
    ON s2.hk_customer = pit.sat_customer_core_ext_v2_hk
   AND s2.load_dts = pit.sat_customer_core_ext_v2_load_dts;

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
    s1.legal_name AS legal_name,
    s2.annual_spend_band AS annual_spend_band,
    s2.loyalty_tier_code AS loyalty_tier_code
FROM business_vault.pit_customer pit
INNER JOIN raw_vault.hub_customer h ON h.hk_customer = pit.hk_customer
INNER JOIN raw_vault.sat_customer_pii s0
    ON s0.hk_customer = pit.sat_customer_pii_hk
   AND s0.load_dts = pit.sat_customer_pii_load_dts
INNER JOIN raw_vault.sat_customer_core s1
    ON s1.hk_customer = pit.sat_customer_core_hk
   AND s1.load_dts = pit.sat_customer_core_load_dts
INNER JOIN raw_vault.sat_customer_core_ext_v2 s2
    ON s2.hk_customer = pit.sat_customer_core_ext_v2_hk
   AND s2.load_dts = pit.sat_customer_core_ext_v2_load_dts;

SELECT * FROM information_mart.vw_pit_customer_current;

-- ZONE 4: BI-TEMPORAL POINT-IN-TIME AUDIT QUERY
SELECT *
FROM information_mart.vw_pit_customer_current
WHERE as_of_dts = TIMESTAMP '2026-11-15 23:59:59';