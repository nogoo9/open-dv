CREATE SCHEMA IF NOT EXISTS staging;

CREATE SCHEMA IF NOT EXISTS raw_vault;

CREATE SCHEMA IF NOT EXISTS business_vault;

CREATE TABLE IF NOT EXISTS raw_vault.hub_holding (
    hk_holding STRING NOT NULL,
    bkcc STRING NOT NULL,
    holding_id STRING NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src STRING NOT NULL
) USING DELTA;

MERGE INTO raw_vault.hub_holding AS target
USING (
    SELECT '0000000000000000000000000000000000000000000000000000000000000000' AS hk_holding, 'SYSTEM' AS bkcc, '-1' AS holding_id, TIMESTAMP '1900-01-01 00:00:00' AS load_dts, 'SYSTEM_ZERO_KEY' AS rec_src
    UNION ALL
    SELECT 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff' AS hk_holding, 'SYSTEM' AS bkcc, '-2' AS holding_id, TIMESTAMP '1900-01-01 00:00:00' AS load_dts, 'SYSTEM_ZERO_KEY' AS rec_src
) AS source
ON target.hk_holding = source.hk_holding
WHEN NOT MATCHED THEN
  INSERT (hk_holding, bkcc, holding_id, load_dts, rec_src)
  VALUES (source.hk_holding, source.bkcc, source.holding_id, source.load_dts, source.rec_src);

CREATE TABLE IF NOT EXISTS raw_vault.hub_filer (
    hk_filer STRING NOT NULL,
    bkcc STRING NOT NULL,
    filer_cik STRING NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src STRING NOT NULL
) USING DELTA;

MERGE INTO raw_vault.hub_filer AS target
USING (
    SELECT '0000000000000000000000000000000000000000000000000000000000000000' AS hk_filer, 'SYSTEM' AS bkcc, '-1' AS filer_cik, TIMESTAMP '1900-01-01 00:00:00' AS load_dts, 'SYSTEM_ZERO_KEY' AS rec_src
    UNION ALL
    SELECT 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff' AS hk_filer, 'SYSTEM' AS bkcc, '-2' AS filer_cik, TIMESTAMP '1900-01-01 00:00:00' AS load_dts, 'SYSTEM_ZERO_KEY' AS rec_src
) AS source
ON target.hk_filer = source.hk_filer
WHEN NOT MATCHED THEN
  INSERT (hk_filer, bkcc, filer_cik, load_dts, rec_src)
  VALUES (source.hk_filer, source.bkcc, source.filer_cik, source.load_dts, source.rec_src);

CREATE TABLE IF NOT EXISTS raw_vault.hub_securities (
    hk_securities STRING NOT NULL,
    bkcc STRING NOT NULL,
    ticker STRING NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src STRING NOT NULL
) USING DELTA;

MERGE INTO raw_vault.hub_securities AS target
USING (
    SELECT '0000000000000000000000000000000000000000000000000000000000000000' AS hk_securities, 'SYSTEM' AS bkcc, '-1' AS ticker, TIMESTAMP '1900-01-01 00:00:00' AS load_dts, 'SYSTEM_ZERO_KEY' AS rec_src
    UNION ALL
    SELECT 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff' AS hk_securities, 'SYSTEM' AS bkcc, '-2' AS ticker, TIMESTAMP '1900-01-01 00:00:00' AS load_dts, 'SYSTEM_ZERO_KEY' AS rec_src
) AS source
ON target.hk_securities = source.hk_securities
WHEN NOT MATCHED THEN
  INSERT (hk_securities, bkcc, ticker, load_dts, rec_src)
  VALUES (source.hk_securities, source.bkcc, source.ticker, source.load_dts, source.rec_src);

CREATE TABLE IF NOT EXISTS raw_vault.hub_issuer (
    hk_issuer STRING NOT NULL,
    bkcc STRING NOT NULL,
    cik STRING NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src STRING NOT NULL
) USING DELTA;

MERGE INTO raw_vault.hub_issuer AS target
USING (
    SELECT '0000000000000000000000000000000000000000000000000000000000000000' AS hk_issuer, 'SYSTEM' AS bkcc, '-1' AS cik, TIMESTAMP '1900-01-01 00:00:00' AS load_dts, 'SYSTEM_ZERO_KEY' AS rec_src
    UNION ALL
    SELECT 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff' AS hk_issuer, 'SYSTEM' AS bkcc, '-2' AS cik, TIMESTAMP '1900-01-01 00:00:00' AS load_dts, 'SYSTEM_ZERO_KEY' AS rec_src
) AS source
ON target.hk_issuer = source.hk_issuer
WHEN NOT MATCHED THEN
  INSERT (hk_issuer, bkcc, cik, load_dts, rec_src)
  VALUES (source.hk_issuer, source.bkcc, source.cik, source.load_dts, source.rec_src);

CREATE TABLE IF NOT EXISTS raw_vault.hub_rate (
    hk_rate STRING NOT NULL,
    bkcc STRING NOT NULL,
    observation_date STRING NOT NULL,
    series_id STRING NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src STRING NOT NULL
) USING DELTA;

MERGE INTO raw_vault.hub_rate AS target
USING (
    SELECT '0000000000000000000000000000000000000000000000000000000000000000' AS hk_rate, 'SYSTEM' AS bkcc, '-1' AS observation_date, '-1' AS series_id, TIMESTAMP '1900-01-01 00:00:00' AS load_dts, 'SYSTEM_ZERO_KEY' AS rec_src
    UNION ALL
    SELECT 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff' AS hk_rate, 'SYSTEM' AS bkcc, '-2' AS observation_date, '-2' AS series_id, TIMESTAMP '1900-01-01 00:00:00' AS load_dts, 'SYSTEM_ZERO_KEY' AS rec_src
) AS source
ON target.hk_rate = source.hk_rate
WHEN NOT MATCHED THEN
  INSERT (hk_rate, bkcc, observation_date, series_id, load_dts, rec_src)
  VALUES (source.hk_rate, source.bkcc, source.observation_date, source.series_id, source.load_dts, source.rec_src);

CREATE TABLE IF NOT EXISTS raw_vault.hub_yield_curve (
    hk_yield_curve STRING NOT NULL,
    bkcc STRING NOT NULL,
    curve_id STRING NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src STRING NOT NULL
) USING DELTA;

MERGE INTO raw_vault.hub_yield_curve AS target
USING (
    SELECT '0000000000000000000000000000000000000000000000000000000000000000' AS hk_yield_curve, 'SYSTEM' AS bkcc, '-1' AS curve_id, TIMESTAMP '1900-01-01 00:00:00' AS load_dts, 'SYSTEM_ZERO_KEY' AS rec_src
    UNION ALL
    SELECT 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff' AS hk_yield_curve, 'SYSTEM' AS bkcc, '-2' AS curve_id, TIMESTAMP '1900-01-01 00:00:00' AS load_dts, 'SYSTEM_ZERO_KEY' AS rec_src
) AS source
ON target.hk_yield_curve = source.hk_yield_curve
WHEN NOT MATCHED THEN
  INSERT (hk_yield_curve, bkcc, curve_id, load_dts, rec_src)
  VALUES (source.hk_yield_curve, source.bkcc, source.curve_id, source.load_dts, source.rec_src);

CREATE TABLE IF NOT EXISTS raw_vault.hub_custody_account (
    hk_custody_account STRING NOT NULL,
    bkcc STRING NOT NULL,
    holding_id STRING NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src STRING NOT NULL
) USING DELTA;

MERGE INTO raw_vault.hub_custody_account AS target
USING (
    SELECT '0000000000000000000000000000000000000000000000000000000000000000' AS hk_custody_account, 'SYSTEM' AS bkcc, '-1' AS holding_id, TIMESTAMP '1900-01-01 00:00:00' AS load_dts, 'SYSTEM_ZERO_KEY' AS rec_src
    UNION ALL
    SELECT 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff' AS hk_custody_account, 'SYSTEM' AS bkcc, '-2' AS holding_id, TIMESTAMP '1900-01-01 00:00:00' AS load_dts, 'SYSTEM_ZERO_KEY' AS rec_src
) AS source
ON target.hk_custody_account = source.hk_custody_account
WHEN NOT MATCHED THEN
  INSERT (hk_custody_account, bkcc, holding_id, load_dts, rec_src)
  VALUES (source.hk_custody_account, source.bkcc, source.holding_id, source.load_dts, source.rec_src);

CREATE TABLE IF NOT EXISTS raw_vault.hub_security (
    hk_security STRING NOT NULL,
    bkcc STRING NOT NULL,
    cusip STRING NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src STRING NOT NULL
) USING DELTA;

MERGE INTO raw_vault.hub_security AS target
USING (
    SELECT '0000000000000000000000000000000000000000000000000000000000000000' AS hk_security, 'SYSTEM' AS bkcc, '-1' AS cusip, TIMESTAMP '1900-01-01 00:00:00' AS load_dts, 'SYSTEM_ZERO_KEY' AS rec_src
    UNION ALL
    SELECT 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff' AS hk_security, 'SYSTEM' AS bkcc, '-2' AS cusip, TIMESTAMP '1900-01-01 00:00:00' AS load_dts, 'SYSTEM_ZERO_KEY' AS rec_src
) AS source
ON target.hk_security = source.hk_security
WHEN NOT MATCHED THEN
  INSERT (hk_security, bkcc, cusip, load_dts, rec_src)
  VALUES (source.hk_security, source.bkcc, source.cusip, source.load_dts, source.rec_src);

CREATE TABLE IF NOT EXISTS raw_vault.hub_position (
    hk_position STRING NOT NULL,
    bkcc STRING NOT NULL,
    position_id STRING NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src STRING NOT NULL
) USING DELTA;

MERGE INTO raw_vault.hub_position AS target
USING (
    SELECT '0000000000000000000000000000000000000000000000000000000000000000' AS hk_position, 'SYSTEM' AS bkcc, '-1' AS position_id, TIMESTAMP '1900-01-01 00:00:00' AS load_dts, 'SYSTEM_ZERO_KEY' AS rec_src
    UNION ALL
    SELECT 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff' AS hk_position, 'SYSTEM' AS bkcc, '-2' AS position_id, TIMESTAMP '1900-01-01 00:00:00' AS load_dts, 'SYSTEM_ZERO_KEY' AS rec_src
) AS source
ON target.hk_position = source.hk_position
WHEN NOT MATCHED THEN
  INSERT (hk_position, bkcc, position_id, load_dts, rec_src)
  VALUES (source.hk_position, source.bkcc, source.position_id, source.load_dts, source.rec_src);

CREATE TABLE IF NOT EXISTS raw_vault.lnk_holding_filer (
    hk_lnk_holding_filer STRING NOT NULL,
    hk_holding STRING NOT NULL,
    hk_filer STRING NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS raw_vault.lnk_holding_securities (
    hk_lnk_holding_securities STRING NOT NULL,
    hk_holding STRING NOT NULL,
    hk_securities STRING NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS raw_vault.lnk_securities_issuer (
    hk_lnk_securities_issuer STRING NOT NULL,
    hk_securities STRING NOT NULL,
    hk_issuer STRING NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS raw_vault.lnk_custody_account_security (
    hk_lnk_custody_account_security STRING NOT NULL,
    hk_custody_account STRING NOT NULL,
    hk_security STRING NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS raw_vault.lnk_position_security (
    hk_lnk_position_security STRING NOT NULL,
    hk_position STRING NOT NULL,
    hk_security STRING NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    rec_src STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS raw_vault.sat_positions (
    hk_position STRING NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    hash_diff STRING NOT NULL,
    account_id STRING ,
    as_of_date DATE ,
    market_value STRING ,
    quantity STRING ,
    synthetic_tax_lot_id STRING ,
    rec_src STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS raw_vault.sat_positions_pii (
    hk_position STRING NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    hash_diff STRING NOT NULL,
    custodian_account_no STRING ,
    rec_src STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS raw_vault.sat_positions_snapshot (
    hk_position STRING NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    hash_diff STRING NOT NULL,
    unrealized_pnl STRING ,
    rec_src STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS raw_vault.sat_holdings_13f (
    hk_holding STRING NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    hash_diff STRING NOT NULL,
    cusip STRING ,
    investment_discretion STRING ,
    issuer_name STRING ,
    market_value_usd DECIMAL(18,2) ,
    shares STRING ,
    rec_src STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS raw_vault.sat_institutional_filers (
    hk_filer STRING NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    hash_diff STRING NOT NULL,
    filer_name STRING ,
    rec_src STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS raw_vault.sat_issuers (
    hk_issuer STRING NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    hash_diff STRING NOT NULL,
    company_name STRING ,
    exchange STRING ,
    ticker STRING ,
    rec_src STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS raw_vault.sat_securities (
    hk_securities STRING NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    hash_diff STRING NOT NULL,
    cusip STRING ,
    security_type STRING ,
    rec_src STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS raw_vault.sat_security_master (
    hk_security STRING NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    hash_diff STRING NOT NULL,
    asset_class STRING ,
    currency STRING ,
    cusip STRING ,
    isin STRING ,
    security_name STRING ,
    sedol STRING ,
    ticker STRING ,
    rec_src STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS raw_vault.sat_treasury_rates (
    hk_rate STRING NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    hash_diff STRING NOT NULL,
    yield_pct DECIMAL(10,4) ,
    rec_src STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS raw_vault.sat_yield_curves (
    hk_yield_curve STRING NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    hash_diff STRING NOT NULL,
    observation_date DATE ,
    rate_10y STRING ,
    rate_2y STRING ,
    rate_sofr STRING ,
    spread_10y_2y STRING ,
    rec_src STRING NOT NULL
) USING DELTA;

-- =============================================================================

-- ZONE 2: IDEMPOTENT DELTA MERGE INTO RAW VAULT LOADS (DATABRICKS)

-- =============================================================================

MERGE INTO raw_vault.hub_holding AS target
USING (
    SELECT src.hk_holding, src.bkcc, src.holding_id, MIN(src.load_dts) AS load_dts, ANY_VALUE(src.rec_src) AS rec_src
    FROM staging.stg_hub_holding AS src
    WHERE src.hk_holding NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
    GROUP BY src.hk_holding, src.bkcc, src.holding_id
) AS source
ON target.hk_holding = source.hk_holding
WHEN NOT MATCHED THEN
  INSERT (hk_holding, bkcc, holding_id, load_dts, rec_src)
  VALUES (source.hk_holding, source.bkcc, source.holding_id, source.load_dts, source.rec_src);

MERGE INTO raw_vault.hub_filer AS target
USING (
    SELECT src.hk_filer, src.bkcc, src.filer_cik, MIN(src.load_dts) AS load_dts, ANY_VALUE(src.rec_src) AS rec_src
    FROM staging.stg_hub_filer AS src
    WHERE src.hk_filer NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
    GROUP BY src.hk_filer, src.bkcc, src.filer_cik
) AS source
ON target.hk_filer = source.hk_filer
WHEN NOT MATCHED THEN
  INSERT (hk_filer, bkcc, filer_cik, load_dts, rec_src)
  VALUES (source.hk_filer, source.bkcc, source.filer_cik, source.load_dts, source.rec_src);

MERGE INTO raw_vault.hub_securities AS target
USING (
    SELECT src.hk_securities, src.bkcc, src.ticker, MIN(src.load_dts) AS load_dts, ANY_VALUE(src.rec_src) AS rec_src
    FROM staging.stg_hub_securities AS src
    WHERE src.hk_securities NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
    GROUP BY src.hk_securities, src.bkcc, src.ticker
) AS source
ON target.hk_securities = source.hk_securities
WHEN NOT MATCHED THEN
  INSERT (hk_securities, bkcc, ticker, load_dts, rec_src)
  VALUES (source.hk_securities, source.bkcc, source.ticker, source.load_dts, source.rec_src);

MERGE INTO raw_vault.hub_issuer AS target
USING (
    SELECT src.hk_issuer, src.bkcc, src.cik, MIN(src.load_dts) AS load_dts, ANY_VALUE(src.rec_src) AS rec_src
    FROM staging.stg_hub_issuer AS src
    WHERE src.hk_issuer NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
    GROUP BY src.hk_issuer, src.bkcc, src.cik
) AS source
ON target.hk_issuer = source.hk_issuer
WHEN NOT MATCHED THEN
  INSERT (hk_issuer, bkcc, cik, load_dts, rec_src)
  VALUES (source.hk_issuer, source.bkcc, source.cik, source.load_dts, source.rec_src);

MERGE INTO raw_vault.hub_rate AS target
USING (
    SELECT src.hk_rate, src.bkcc, src.observation_date, src.series_id, MIN(src.load_dts) AS load_dts, ANY_VALUE(src.rec_src) AS rec_src
    FROM staging.stg_hub_rate AS src
    WHERE src.hk_rate NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
    GROUP BY src.hk_rate, src.bkcc, src.observation_date, src.series_id
) AS source
ON target.hk_rate = source.hk_rate
WHEN NOT MATCHED THEN
  INSERT (hk_rate, bkcc, observation_date, series_id, load_dts, rec_src)
  VALUES (source.hk_rate, source.bkcc, source.observation_date, source.series_id, source.load_dts, source.rec_src);

MERGE INTO raw_vault.hub_yield_curve AS target
USING (
    SELECT src.hk_yield_curve, src.bkcc, src.curve_id, MIN(src.load_dts) AS load_dts, ANY_VALUE(src.rec_src) AS rec_src
    FROM staging.stg_hub_yield_curve AS src
    WHERE src.hk_yield_curve NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
    GROUP BY src.hk_yield_curve, src.bkcc, src.curve_id
) AS source
ON target.hk_yield_curve = source.hk_yield_curve
WHEN NOT MATCHED THEN
  INSERT (hk_yield_curve, bkcc, curve_id, load_dts, rec_src)
  VALUES (source.hk_yield_curve, source.bkcc, source.curve_id, source.load_dts, source.rec_src);

MERGE INTO raw_vault.hub_custody_account AS target
USING (
    SELECT src.hk_custody_account, src.bkcc, src.holding_id, MIN(src.load_dts) AS load_dts, ANY_VALUE(src.rec_src) AS rec_src
    FROM staging.stg_hub_custody_account AS src
    WHERE src.hk_custody_account NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
    GROUP BY src.hk_custody_account, src.bkcc, src.holding_id
) AS source
ON target.hk_custody_account = source.hk_custody_account
WHEN NOT MATCHED THEN
  INSERT (hk_custody_account, bkcc, holding_id, load_dts, rec_src)
  VALUES (source.hk_custody_account, source.bkcc, source.holding_id, source.load_dts, source.rec_src);

MERGE INTO raw_vault.hub_security AS target
USING (
    SELECT src.hk_security, src.bkcc, src.cusip, MIN(src.load_dts) AS load_dts, ANY_VALUE(src.rec_src) AS rec_src
    FROM staging.stg_hub_security AS src
    WHERE src.hk_security NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
    GROUP BY src.hk_security, src.bkcc, src.cusip
) AS source
ON target.hk_security = source.hk_security
WHEN NOT MATCHED THEN
  INSERT (hk_security, bkcc, cusip, load_dts, rec_src)
  VALUES (source.hk_security, source.bkcc, source.cusip, source.load_dts, source.rec_src);

MERGE INTO raw_vault.hub_position AS target
USING (
    SELECT src.hk_position, src.bkcc, src.position_id, MIN(src.load_dts) AS load_dts, ANY_VALUE(src.rec_src) AS rec_src
    FROM staging.stg_hub_position AS src
    WHERE src.hk_position NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
    GROUP BY src.hk_position, src.bkcc, src.position_id
) AS source
ON target.hk_position = source.hk_position
WHEN NOT MATCHED THEN
  INSERT (hk_position, bkcc, position_id, load_dts, rec_src)
  VALUES (source.hk_position, source.bkcc, source.position_id, source.load_dts, source.rec_src);

MERGE INTO raw_vault.lnk_holding_filer AS target
USING (
    SELECT src.hk_lnk_holding_filer, src.hk_holding, src.hk_filer, MIN(src.load_dts) AS load_dts, ANY_VALUE(src.rec_src) AS rec_src
    FROM staging.stg_lnk_holding_filer AS src
    WHERE src.hk_lnk_holding_filer NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
    GROUP BY src.hk_lnk_holding_filer, src.hk_holding, src.hk_filer
) AS source
ON target.hk_lnk_holding_filer = source.hk_lnk_holding_filer
WHEN NOT MATCHED THEN
  INSERT (hk_lnk_holding_filer, hk_holding, hk_filer, load_dts, rec_src)
  VALUES (source.hk_lnk_holding_filer, source.hk_holding, source.hk_filer, source.load_dts, source.rec_src);

MERGE INTO raw_vault.lnk_holding_securities AS target
USING (
    SELECT src.hk_lnk_holding_securities, src.hk_holding, src.hk_securities, MIN(src.load_dts) AS load_dts, ANY_VALUE(src.rec_src) AS rec_src
    FROM staging.stg_lnk_holding_securities AS src
    WHERE src.hk_lnk_holding_securities NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
    GROUP BY src.hk_lnk_holding_securities, src.hk_holding, src.hk_securities
) AS source
ON target.hk_lnk_holding_securities = source.hk_lnk_holding_securities
WHEN NOT MATCHED THEN
  INSERT (hk_lnk_holding_securities, hk_holding, hk_securities, load_dts, rec_src)
  VALUES (source.hk_lnk_holding_securities, source.hk_holding, source.hk_securities, source.load_dts, source.rec_src);

MERGE INTO raw_vault.lnk_securities_issuer AS target
USING (
    SELECT src.hk_lnk_securities_issuer, src.hk_securities, src.hk_issuer, MIN(src.load_dts) AS load_dts, ANY_VALUE(src.rec_src) AS rec_src
    FROM staging.stg_lnk_securities_issuer AS src
    WHERE src.hk_lnk_securities_issuer NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
    GROUP BY src.hk_lnk_securities_issuer, src.hk_securities, src.hk_issuer
) AS source
ON target.hk_lnk_securities_issuer = source.hk_lnk_securities_issuer
WHEN NOT MATCHED THEN
  INSERT (hk_lnk_securities_issuer, hk_securities, hk_issuer, load_dts, rec_src)
  VALUES (source.hk_lnk_securities_issuer, source.hk_securities, source.hk_issuer, source.load_dts, source.rec_src);

MERGE INTO raw_vault.lnk_custody_account_security AS target
USING (
    SELECT src.hk_lnk_custody_account_security, src.hk_custody_account, src.hk_security, MIN(src.load_dts) AS load_dts, ANY_VALUE(src.rec_src) AS rec_src
    FROM staging.stg_lnk_custody_account_security AS src
    WHERE src.hk_lnk_custody_account_security NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
    GROUP BY src.hk_lnk_custody_account_security, src.hk_custody_account, src.hk_security
) AS source
ON target.hk_lnk_custody_account_security = source.hk_lnk_custody_account_security
WHEN NOT MATCHED THEN
  INSERT (hk_lnk_custody_account_security, hk_custody_account, hk_security, load_dts, rec_src)
  VALUES (source.hk_lnk_custody_account_security, source.hk_custody_account, source.hk_security, source.load_dts, source.rec_src);

MERGE INTO raw_vault.lnk_position_security AS target
USING (
    SELECT src.hk_lnk_position_security, src.hk_position, src.hk_security, MIN(src.load_dts) AS load_dts, ANY_VALUE(src.rec_src) AS rec_src
    FROM staging.stg_lnk_position_security AS src
    WHERE src.hk_lnk_position_security NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
    GROUP BY src.hk_lnk_position_security, src.hk_position, src.hk_security
) AS source
ON target.hk_lnk_position_security = source.hk_lnk_position_security
WHEN NOT MATCHED THEN
  INSERT (hk_lnk_position_security, hk_position, hk_security, load_dts, rec_src)
  VALUES (source.hk_lnk_position_security, source.hk_position, source.hk_security, source.load_dts, source.rec_src);

MERGE INTO raw_vault.sat_positions AS target
USING (
    SELECT src.hk_position, src.load_dts, src.hash_diff, src.account_id, src.as_of_date, src.market_value, src.quantity, src.synthetic_tax_lot_id, src.rec_src FROM staging.stg_sat_positions AS src
    WHERE src.hk_position NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
) AS source
ON target.hk_position = source.hk_position AND target.load_dts = source.load_dts
WHEN NOT MATCHED THEN
  INSERT (hk_position, load_dts, hash_diff, account_id, as_of_date, market_value, quantity, synthetic_tax_lot_id, rec_src)
  VALUES (source.hk_position, source.load_dts, source.hash_diff, source.account_id, source.as_of_date, source.market_value, source.quantity, source.synthetic_tax_lot_id, source.rec_src);

MERGE INTO raw_vault.sat_positions_pii AS target
USING (
    SELECT src.hk_position, src.load_dts, src.hash_diff, src.custodian_account_no, src.rec_src FROM staging.stg_sat_positions_pii AS src
    WHERE src.hk_position NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
) AS source
ON target.hk_position = source.hk_position AND target.load_dts = source.load_dts
WHEN NOT MATCHED THEN
  INSERT (hk_position, load_dts, hash_diff, custodian_account_no, rec_src)
  VALUES (source.hk_position, source.load_dts, source.hash_diff, source.custodian_account_no, source.rec_src);

MERGE INTO raw_vault.sat_positions_snapshot AS target
USING (
    SELECT src.hk_position, src.load_dts, src.hash_diff, src.unrealized_pnl, src.rec_src FROM staging.stg_sat_positions_snapshot AS src
    WHERE src.hk_position NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
) AS source
ON target.hk_position = source.hk_position AND target.load_dts = source.load_dts
WHEN NOT MATCHED THEN
  INSERT (hk_position, load_dts, hash_diff, unrealized_pnl, rec_src)
  VALUES (source.hk_position, source.load_dts, source.hash_diff, source.unrealized_pnl, source.rec_src);

MERGE INTO raw_vault.sat_holdings_13f AS target
USING (
    SELECT src.hk_holding, src.load_dts, src.hash_diff, src.cusip, src.investment_discretion, src.issuer_name, src.market_value_usd, src.shares, src.rec_src FROM staging.stg_sat_holdings_13f AS src
    WHERE src.hk_holding NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
) AS source
ON target.hk_holding = source.hk_holding AND target.load_dts = source.load_dts
WHEN NOT MATCHED THEN
  INSERT (hk_holding, load_dts, hash_diff, cusip, investment_discretion, issuer_name, market_value_usd, shares, rec_src)
  VALUES (source.hk_holding, source.load_dts, source.hash_diff, source.cusip, source.investment_discretion, source.issuer_name, source.market_value_usd, source.shares, source.rec_src);

MERGE INTO raw_vault.sat_institutional_filers AS target
USING (
    SELECT src.hk_filer, src.load_dts, src.hash_diff, src.filer_name, src.rec_src FROM staging.stg_sat_institutional_filers AS src
    WHERE src.hk_filer NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
) AS source
ON target.hk_filer = source.hk_filer AND target.load_dts = source.load_dts
WHEN NOT MATCHED THEN
  INSERT (hk_filer, load_dts, hash_diff, filer_name, rec_src)
  VALUES (source.hk_filer, source.load_dts, source.hash_diff, source.filer_name, source.rec_src);

MERGE INTO raw_vault.sat_issuers AS target
USING (
    SELECT src.hk_issuer, src.load_dts, src.hash_diff, src.company_name, src.exchange, src.ticker, src.rec_src FROM staging.stg_sat_issuers AS src
    WHERE src.hk_issuer NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
) AS source
ON target.hk_issuer = source.hk_issuer AND target.load_dts = source.load_dts
WHEN NOT MATCHED THEN
  INSERT (hk_issuer, load_dts, hash_diff, company_name, exchange, ticker, rec_src)
  VALUES (source.hk_issuer, source.load_dts, source.hash_diff, source.company_name, source.exchange, source.ticker, source.rec_src);

MERGE INTO raw_vault.sat_securities AS target
USING (
    SELECT src.hk_securities, src.load_dts, src.hash_diff, src.cusip, src.security_type, src.rec_src FROM staging.stg_sat_securities AS src
    WHERE src.hk_securities NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
) AS source
ON target.hk_securities = source.hk_securities AND target.load_dts = source.load_dts
WHEN NOT MATCHED THEN
  INSERT (hk_securities, load_dts, hash_diff, cusip, security_type, rec_src)
  VALUES (source.hk_securities, source.load_dts, source.hash_diff, source.cusip, source.security_type, source.rec_src);

MERGE INTO raw_vault.sat_security_master AS target
USING (
    SELECT src.hk_security, src.load_dts, src.hash_diff, src.asset_class, src.currency, src.cusip, src.isin, src.security_name, src.sedol, src.ticker, src.rec_src FROM staging.stg_sat_security_master AS src
    WHERE src.hk_security NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
) AS source
ON target.hk_security = source.hk_security AND target.load_dts = source.load_dts
WHEN NOT MATCHED THEN
  INSERT (hk_security, load_dts, hash_diff, asset_class, currency, cusip, isin, security_name, sedol, ticker, rec_src)
  VALUES (source.hk_security, source.load_dts, source.hash_diff, source.asset_class, source.currency, source.cusip, source.isin, source.security_name, source.sedol, source.ticker, source.rec_src);

MERGE INTO raw_vault.sat_treasury_rates AS target
USING (
    SELECT src.hk_rate, src.load_dts, src.hash_diff, src.yield_pct, src.rec_src FROM staging.stg_sat_treasury_rates AS src
    WHERE src.hk_rate NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
) AS source
ON target.hk_rate = source.hk_rate AND target.load_dts = source.load_dts
WHEN NOT MATCHED THEN
  INSERT (hk_rate, load_dts, hash_diff, yield_pct, rec_src)
  VALUES (source.hk_rate, source.load_dts, source.hash_diff, source.yield_pct, source.rec_src);

MERGE INTO raw_vault.sat_yield_curves AS target
USING (
    SELECT src.hk_yield_curve, src.load_dts, src.hash_diff, src.observation_date, src.rate_10y, src.rate_2y, src.rate_sofr, src.spread_10y_2y, src.rec_src FROM staging.stg_sat_yield_curves AS src
    WHERE src.hk_yield_curve NOT IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')
) AS source
ON target.hk_yield_curve = source.hk_yield_curve AND target.load_dts = source.load_dts
WHEN NOT MATCHED THEN
  INSERT (hk_yield_curve, load_dts, hash_diff, observation_date, rate_10y, rate_2y, rate_sofr, spread_10y_2y, rec_src)
  VALUES (source.hk_yield_curve, source.load_dts, source.hash_diff, source.observation_date, source.rate_10y, source.rate_2y, source.rate_sofr, source.spread_10y_2y, source.rec_src);