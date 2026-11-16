"""Fixture seeding helpers for DuckDB Unity Catalog emulator."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opendv_testkit.uc_emulator import DuckDBUnityCatalogEmulator


def seed_walkthrough_tables(
    emulator: DuckDBUnityCatalogEmulator,
    catalog: str = "am_prod",
    schema: str = "ibor",
) -> None:
    """Seed UI walkthrough tables (positions_snapshot and custody_holdings)."""
    emulator.register_table(
        catalog=catalog,
        schema=schema,
        table="positions_snapshot",
        columns=[
            ("account_id", "VARCHAR", False, "Account ID"),
            ("unrealized_pnl", "DECIMAL(18,2)", True, "Unrealized PnL"),
        ],
        rows=[
            ("ALPHA", 125000.50),
            ("BETA", -45000.00),
        ],
        primary_key=["account_id"],
        column_tags={"unrealized_pnl": {"mnpi": "sensitive"}},
        table_tags={"domain": "ibor"},
        comment="Positions Snapshot",
        version=1,
    )

    emulator.register_table(
        catalog=catalog,
        schema=schema,
        table="custody_holdings",
        columns=[
            ("holding_id", "VARCHAR", False, "Holding ID"),
            ("cusip", "VARCHAR", True, "CUSIP"),
        ],
        rows=[
            ("HOLD_01", "037833100"),
            ("HOLD_02", "594918104"),
        ],
        primary_key=["holding_id"],
        foreign_keys=[
            {
                "constraint_name": "fk_custody_security",
                "from_column": "cusip",
                "target_table": "security_master",
                "target_column": "cusip",
            }
        ],
        table_tags={"domain": "custody"},
        comment="Custody Holdings",
        version=1,
    )


def seed_asset_management_ibor(
    emulator: DuckDBUnityCatalogEmulator,
    include_walkthrough: bool = False,
) -> None:
    """Seed canonical Asset Management IBOR fixtures into am_prod.ibor."""
    cat, sch = "am_prod", "ibor"

    emulator.register_table(
        catalog=cat,
        schema=sch,
        table="security_master",
        columns=[
            ("instrument_id", "VARCHAR", False, "Primary ID"),
            ("isin", "VARCHAR", False, "ISIN"),
            ("cusip", "VARCHAR", True, "CUSIP"),
            ("sedol", "VARCHAR", True, "SEDOL"),
            ("ticker", "VARCHAR", True, "Ticker"),
            ("security_name", "VARCHAR", False, "Security Name"),
            ("asset_class", "VARCHAR", False, "Asset Class"),
            ("currency", "VARCHAR", False, "Currency"),
        ],
        rows=[
            ("INST_001", "US0378331005", "037833100", "2046251", "AAPL", "Apple", "EQ", "USD"),
            ("INST_002", "US5949181045", "594918104", "2588173", "MSFT", "MSFT", "EQ", "USD"),
            ("INST_003", "US02079K3059", "02079K305", "BYVY8G0", "GOOGL", "GOOGL", "EQ", "USD"),
            ("INST_004", "US0231351067", "023135106", "2000019", "AMZN", "Amazon", "EQ", "USD"),
            ("INST_005", "US67066G1040", "67066G104", "2379504", "NVDA", "NVIDIA", "EQ", "USD"),
        ],
        primary_key=["instrument_id"],
        column_tags={
            "isin": {"classification": "isin_code", "authority_tier": "tier_1"},
            "cusip": {"classification": "cusip_code", "authority_tier": "tier_1"},
        },
        table_tags={"domain": "asset_management"},
        comment="Security Reference Table",
        version=1,
    )

    emulator.register_table(
        catalog=cat,
        schema=sch,
        table="positions",
        columns=[
            ("position_id", "VARCHAR", False, "Position ID"),
            ("account_id", "VARCHAR", False, "Account"),
            ("instrument_id", "VARCHAR", False, "Instrument"),
            ("quantity", "DOUBLE", False, "Units"),
            ("as_of_date", "DATE", False, "Date"),
            ("market_value", "DOUBLE", False, "Valuation"),
            ("custodian_account_no", "VARCHAR", True, "Custodian Acct"),
            ("synthetic_tax_lot_id", "VARCHAR", True, "Tax Lot"),
        ],
        rows=[
            ("POS_1", "ALPHA", "INST_001", 10000.0, "2026-03-31", 1750000.0, "CUST-88", "L1"),
            ("POS_2", "ALPHA", "INST_002", 20000.0, "2026-03-31", 8400000.0, "CUST-88", "L2"),
            ("POS_3", "BETA", "INST_003", 15000.0, "2026-03-31", 2250000.0, "CUST-89", "L3"),
            ("POS_4", "BETA", "INST_004", 25000.0, "2026-03-31", 4500000.0, "CUST-89", "L4"),
            ("POS_5", "GAMMA", "INST_005", 30000.0, "2026-03-31", 3900000.0, "CUST-90", "L5"),
        ],
        primary_key=["position_id"],
        foreign_keys=[
            {
                "constraint_name": "fk_positions_security",
                "from_column": "instrument_id",
                "target_table": "security_master",
                "target_column": "instrument_id",
            }
        ],
        column_tags={
            "custodian_account_no": {"pii": "true", "sensitivity": "restricted"},
            "market_value": {"sensitivity": "confidential"},
        },
        table_tags={"domain": "ibor"},
        comment="IBOR Settled Positions",
        version=3,
    )

    emulator.register_table(
        catalog=cat,
        schema=sch,
        table="trades",
        columns=[
            ("trade_id", "VARCHAR", False, "Trade ID"),
            ("account_id", "VARCHAR", False, "Account"),
            ("instrument_id", "VARCHAR", False, "Instrument"),
            ("trade_date", "TIMESTAMP", False, "Trade Date"),
            ("settle_date", "DATE", False, "Settle Date"),
            ("side", "VARCHAR", False, "Side"),
            ("quantity", "DOUBLE", False, "Quantity"),
            ("price", "DOUBLE", False, "Price"),
            ("trader_email", "VARCHAR", True, "Trader Email"),
        ],
        rows=[
            ("T1", "A", "INST_001", "2026-03-30", "2026-04-01", "BUY", 1e4, 175.0, "a@e.co"),
            ("T2", "A", "INST_002", "2026-03-30", "2026-04-01", "BUY", 5e3, 420.0, "a@e.co"),
            ("T3", "B", "INST_003", "2026-03-30", "2026-04-01", "BUY", 2e3, 150.0, "b@e.co"),
            ("T4", "B", "INST_004", "2026-03-30", "2026-04-01", "BUY", 8e3, 180.0, "b@e.co"),
            ("T5", "C", "INST_005", "2026-03-30", "2026-04-01", "BUY", 12e3, 130.0, "c@e.co"),
        ],
        primary_key=["trade_id"],
        foreign_keys=[
            {
                "constraint_name": "fk_trades_security",
                "from_column": "instrument_id",
                "target_table": "security_master",
                "target_column": "instrument_id",
            }
        ],
        column_tags={"trader_email": {"pii": "true"}},
        table_tags={"domain": "transactions"},
        comment="OMS Trades",
        version=2,
    )

    # Always register walkthrough tables into am_prod.walkthrough schema
    seed_walkthrough_tables(emulator, catalog=cat, schema="walkthrough")

    # If include_walkthrough is requested, also seed them into am_prod.ibor
    if include_walkthrough:
        seed_walkthrough_tables(emulator, catalog=cat, schema=sch)
