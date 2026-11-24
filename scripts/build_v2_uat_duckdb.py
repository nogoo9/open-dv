#!/usr/bin/env python3
"""DuckDB 12-trap fixture builder (`ds_cegar_12trap.duckdb`) for build_v2_uat_fixtures.py."""

from __future__ import annotations

import os
from pathlib import Path

import duckdb


def build_ds_cegar_12trap_duckdb(out_path: Path) -> Path:
    """Create 6-table ds_cegar_12trap.duckdb triggering TRAP-01..12."""
    tmp_path = out_path.with_suffix(".duckdb.tmp")
    tmp_path.unlink(missing_ok=True)
    conn = duckdb.connect(str(tmp_path))
    try:
        # 1. trap_crm_accounts: TRAP-01, TRAP-09 (26 cols + 3 PII), TRAP-10
        conn.execute(
            """
            CREATE TABLE trap_crm_accounts (
                account_id VARCHAR PRIMARY KEY,
                delimiter_part_a VARCHAR,
                delimiter_part_b VARCHAR,
                beneficial_owner_tax_id VARCHAR,
                stewardship_officer_email VARCHAR,
                client_phone VARCHAR,
                account_tier VARCHAR,
                onboarding_date DATE,
                country_code VARCHAR,
                kyc_status VARCHAR,
                risk_rating VARCHAR,
                advisor_code VARCHAR,
                office_code VARCHAR,
                mandate_family VARCHAR,
                fee_schedule_code VARCHAR,
                billing_cycle VARCHAR,
                reporting_ccy VARCHAR,
                benchmark_code VARCHAR,
                daily_nav_usd DECIMAL(18,2),
                intraday_cash_usd DECIMAL(18,2),
                intraday_margin_usd DECIMAL(18,2),
                daily_turnover_pct DOUBLE,
                daily_var_95 DOUBLE,
                daily_tracking_err DOUBLE,
                intraday_exposure_usd DECIMAL(18,2),
                daily_accrual_usd DECIMAL(18,2)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO trap_crm_accounts VALUES
            ('1001', 'A|B', 'C', '123-45-6789', 'steward.one@am-example.com', '212-555-0101',
             'INST', DATE '2024-01-15', 'US', 'VERIFIED', 'LOW', 'ADV-01', 'NYC', 'CORE',
             'FEE-A', 'QUARTERLY', 'USD', 'SP500', 1250000.00, 50000.00, 10000.00, 0.04, 0.02,
             0.005, 1100000.00, 420.50),
            ('1002', 'A', 'B|C', '987-65-4321', 'steward.two@am-example.com', '212-555-0102',
             'INST', DATE '2024-02-20', 'GB', 'VERIFIED', 'MED', 'ADV-02', 'LON', 'ESG',
             'FEE-B', 'MONTHLY', 'GBP', 'FTSE', 980000.00, 25000.00, 5000.00, 0.06, 0.03,
             0.008, 850000.00, 310.25),
            ('1003', NULL, 'ONLY_B', '555-44-3333', 'steward.three@am-example.com',
             '212-555-0103', 'SOV', DATE '2024-03-10', 'NO', 'VERIFIED', 'LOW', 'ADV-03', 'OSL',
             'GLOBAL', 'FEE-C', 'QUARTERLY', 'EUR', 'MSCI', 5400000.00, 120000.00, 0.00, 0.01,
             0.01, 0.002, 5200000.00, 1100.00),
            ('1004', 'ONLY_A', NULL, '444-33-2222', 'steward.four@am-example.com',
             '212-555-0104', 'ENDOW', DATE '2024-04-05', 'US', 'PENDING', 'HIGH', 'ADV-04',
             'BOS', 'CREDIT', 'FEE-D', 'ANNUAL', 'USD', 'AGG', 760000.00, 15000.00, 2500.00,
             0.03, 0.02, 0.004, 710000.00, 190.00)
            """
        )

        # 2. trap_ibor_accounts: TRAP-01, TRAP-02, TRAP-07
        conn.execute(
            """
            CREATE TABLE trap_ibor_accounts (
                account_id VARCHAR PRIMARY KEY,
                issuer_cik VARCHAR,
                issuer_legal_name VARCHAR,
                executing_broker_lei VARCHAR,
                prime_custodian_bic VARCHAR
            )
            """
        )
        conn.execute(
            """
            INSERT INTO trap_ibor_accounts VALUES
            ('1001', '0000909100', 'Nordisk Suverän Fond',
             '5493001KJTIIGC8Y1R12', 'CITIUS33XXX'),
            ('1002', '0000320193', 'Apple Institutional',
             '5493002KJTIIGC8Y1R13', NULL),
            ('1003', '0000789019', 'Microsoft Global Tr', NULL, NULL),
            ('1004', '0001045810', 'NVIDIA Growth Fund', NULL, NULL)
            """
        )

        # 3. trap_ballots_mas: TRAP-04 & TRAP-06
        conn.execute(
            """
            CREATE TABLE trap_ballots_mas (
                ballot_id VARCHAR,
                proposal_seq INTEGER,
                issuer_ticker VARCHAR,
                iso_currency_code VARCHAR,
                vote_cast_code VARCHAR,
                shares_voted BIGINT,
                PRIMARY KEY (ballot_id, proposal_seq)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO trap_ballots_mas VALUES
            ('PRX_NVDA_2024', 1, 'NVDA', 'USD', 'FOR', 120000),
            ('PRX_NVDA_2024', 2, 'NVDA', 'EUR', 'AGAINST', 120000),
            ('PRX_NVDA_2024', 3, 'NVDA', 'GBP', 'ABSTAIN', 120000),
            ('PRX_AAPL_2024', 1, 'AAPL', 'JPY', 'FOR', 85000),
            ('PRX_MSFT_2024', 1, 'MSFT', 'CHF', 'AGAINST', 95000)
            """
        )

        # 4. trap_snapshots_churn: TRAP-05
        conn.execute(
            """
            CREATE TABLE trap_snapshots_churn (
                position_id VARCHAR PRIMARY KEY,
                account_id VARCHAR,
                ticker VARCHAR,
                quantity DECIMAL(18,4),
                etl_snapshot_ts TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO trap_snapshots_churn VALUES
            ('POS-01', '1001', 'NVDA', 1500.0000, TIMESTAMP '2026-11-20 01:00:00'),
            ('POS-02', '1002', 'AAPL', 2400.0000, TIMESTAMP '2026-11-20 01:00:00'),
            ('POS-03', '1003', 'MSFT', 1800.0000, TIMESTAMP '2026-11-20 01:00:00')
            """
        )

        # 5. trap_manager_eff: TRAP-08
        conn.execute(
            """
            CREATE TABLE trap_manager_eff (
                assignment_id VARCHAR PRIMARY KEY,
                account_id VARCHAR,
                portfolio_manager_id VARCHAR,
                effective_from TIMESTAMP,
                effective_to TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO trap_manager_eff VALUES
            ('ASG-1', 'ACCT-1001', 'PM-A',
             TIMESTAMP '2024-01-01 00:00:00', TIMESTAMP '2025-01-01 00:00:00'),
            ('ASG-2', 'ACCT-1001', 'PM-B',
             TIMESTAMP '2025-01-01 00:00:00', TIMESTAMP '2026-01-01 00:00:00'),
            ('ASG-3', 'ACCT-1001', 'PM-A',
             TIMESTAMP '2026-01-01 00:00:00', TIMESTAMP '9999-12-31 00:00:00')
            """
        )

        # 6. trap_early_trades: TRAP-11
        conn.execute(
            """
            CREATE TABLE trap_early_trades (
                trade_id VARCHAR PRIMARY KEY,
                security_isin VARCHAR,
                as_of_date DATE,
                applied_dts TIMESTAMP,
                nav_per_share DECIMAL(18,4)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO trap_early_trades VALUES
            ('TRD-STUB-01', 'US9999999999', DATE '2026-10-20',
             TIMESTAMP '2026-10-20 18:00:00', 216.2800),
            ('TRD-RESTATE-01', 'US0378331005', DATE '2026-10-20',
             TIMESTAMP '2026-11-20 09:30:00', 245.0000)
            """
        )
    finally:
        conn.close()

    os.replace(tmp_path, out_path)
    return out_path
