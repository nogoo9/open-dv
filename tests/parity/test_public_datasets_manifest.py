"""Parity tests validating dataset_manifest.yaml and fetch_public_datasets.py (WS2A.4)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from opendv.sources.adapters import extract_structured_source
from tests.fixtures.public_am.fetch_public_datasets import (
    AM_SQLITE_FILENAME,
    CHINOOK_FILENAME,
    MANIFEST_FILENAME,
    NORTHWIND_FILENAME,
    PublicDataFetcher,
    fetch_benchmark_sqlite_datasets,
    fetch_public_am_datasets,
    load_dataset_manifest,
)


def test_dataset_manifest_schema_and_integrity() -> None:
    """Verify dataset_manifest.yaml exists, parses, and declares required suites."""
    manifest = load_dataset_manifest()
    assert manifest["manifest_version"] == "1.0"
    assert "suites" in manifest

    am_suite = manifest["suites"]["asset_management"]
    assert am_suite["target_database"] == "data/public_am/am_sec_treasury_ibor.sqlite"
    assert "sec_company_tickers" in am_suite["sources"]
    assert "fred_treasury_yields" in am_suite["sources"]
    assert "sec_13f_holdings" in am_suite["sources"]

    table_names = [t["name"] for t in am_suite["tables"]]
    assert "issuers" in table_names
    assert "securities" in table_names
    assert "institutional_filers" in table_names
    assert "holdings_13f" in table_names
    assert "treasury_rates" in table_names
    assert "yield_curves" in table_names

    benchmarks = manifest["suites"]["benchmarks"]
    assert "northwind" in benchmarks
    assert "chinook" in benchmarks


def test_public_datasets_offline_fallback_generation(tmp_path: Path) -> None:
    """Verify fetch_public_am_datasets generates normalized SQLite and CSVs deterministically."""
    out_dir = tmp_path / "public_am"
    results = fetch_public_am_datasets(output_dir=out_dir, mode="fixture")

    assert AM_SQLITE_FILENAME in results
    db_path = results[AM_SQLITE_FILENAME]
    assert db_path.exists()
    assert db_path.stat().st_size > 0

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall()]
    assert "issuers" in tables
    assert "securities" in tables
    assert "institutional_filers" in tables
    assert "holdings_13f" in tables
    assert "treasury_rates" in tables
    assert "yield_curves" in tables

    # Assert non-zero row counts across all tables
    for tbl in tables:
        cur.execute(f'SELECT count(*) FROM "{tbl}"')
        count = cur.fetchone()[0]
        assert count > 0, f"Table '{tbl}' is empty in {AM_SQLITE_FILENAME}"

    # Assert Primary Keys via PRAGMA table_info
    cur.execute("PRAGMA table_info(issuers)")
    pk_cols = [r[1] for r in cur.fetchall() if r[5] > 0]
    assert pk_cols == ["cik"]

    cur.execute("PRAGMA table_info(securities)")
    pk_cols = [r[1] for r in cur.fetchall() if r[5] > 0]
    assert pk_cols == ["ticker"]

    cur.execute("PRAGMA table_info(treasury_rates)")
    pk_cols = [r[1] for r in cur.fetchall() if r[5] > 0]
    assert set(pk_cols) == {"observation_date", "series_id"}

    # Assert Foreign Keys via PRAGMA foreign_key_list
    cur.execute("PRAGMA foreign_key_list(securities)")
    fks = cur.fetchall()
    assert any(fk[2] == "issuers" and fk[3] == "cik" for fk in fks)

    cur.execute("PRAGMA foreign_key_list(holdings_13f)")
    fks = cur.fetchall()
    assert any(fk[2] == "securities" and fk[3] == "ticker" for fk in fks)
    assert any(fk[2] == "institutional_filers" and fk[3] == "filer_cik" for fk in fks)

    conn.close()


def test_benchmark_sqlite_offline_fallback_generation(tmp_path: Path) -> None:
    """Verify Northwind and Chinook offline SQLite benchmark generation."""
    out_dir = tmp_path / "public_sqlite"
    results = fetch_benchmark_sqlite_datasets(output_dir=out_dir, mode="fixture")

    nw_path = results[NORTHWIND_FILENAME]
    ch_path = results[CHINOOK_FILENAME]
    assert nw_path.exists() and nw_path.stat().st_size > 0
    assert ch_path.exists() and ch_path.stat().st_size > 0

    # Verify Northwind tables
    nw_conn = sqlite3.connect(nw_path)
    nw_cur = nw_conn.cursor()
    nw_cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    nw_tables = {r[0] for r in nw_cur.fetchall()}
    assert {"Customers", "Employees", "Orders", "Order Details", "Products"}.issubset(nw_tables)
    nw_conn.close()

    # Verify Chinook tables
    ch_conn = sqlite3.connect(ch_path)
    ch_cur = ch_conn.cursor()
    ch_cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    ch_tables = {r[0] for r in ch_cur.fetchall()}
    assert {"Artist", "Album", "Track", "Customer", "Invoice", "InvoiceLine"}.issubset(ch_tables)
    ch_conn.close()


def test_v2_sqlite_adapter_extracts_am_database(tmp_path: Path) -> None:
    """Verify V2 `extract_structured_source` introspects `am_sec_treasury_ibor.sqlite` cleanly."""
    out_dir = tmp_path / "public_am"
    results = fetch_public_am_datasets(output_dir=out_dir, mode="fixture")
    db_path = results[AM_SQLITE_FILENAME]

    extracted = extract_structured_source("sqlite", str(db_path))
    by_name = {tbl.table_name: tbl for tbl in extracted}
    assert {
        "issuers",
        "securities",
        "institutional_filers",
        "holdings_13f",
        "treasury_rates",
        "yield_curves",
    }.issubset(by_name)

    holdings = by_name["holdings_13f"]
    pk_cols = [c.col_name for c in holdings.columns if c.is_pk]
    assert pk_cols == ["holding_id"]
    fk_targets = {c.fk_ref.split(".", 1)[0] for c in holdings.columns if c.fk_ref}
    assert "securities" in fk_targets
    assert "institutional_filers" in fk_targets
    assert holdings.row_count_est > 0
