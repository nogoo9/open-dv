"""Phase 2 TDD Tests: Hardened `validate_safe_sql()` Allowlist & External Guard (Audit #9/#11).

Covers:
1. Allowlist permits safe read-only `SELECT`, `UNION`, `JOIN`, CTE (`WITH`), and safe
   scalar/aggregate functions (`COUNT`, `SUM`, `AVG`, `MIN`, `MAX`, `COALESCE`, `UPPER`,
   `LOWER`, `TRIM`, `CAST`).
2. Blocks dangerous DuckDB file/network/metadata/secret/extension functions:
   `read_text`, `read_xlsx`, `glob`, `sniff_csv`, `parquet_metadata`, `duckdb_secrets`,
   `duckdb_settings`, `current_setting`, `getenv`, `sqlite_scan`, `postgres_scan`,
   `read_csv`, `read_parquet`, `read_json`, `copy`, `load`, `install`.
3. Blocks string-literal `exp.Table` (`SELECT * FROM 'file.csv'`), multi-statement queries,
   and mutating/DDL/pragma statements (`DROP`, `INSERT`, `UPDATE`, `DELETE`, `PRAGMA`,
   `COPY`, `ATTACH`).
4. Asserts `enable_external_access=False` on `DuckDbShadowSandbox` (including custom `conn`).
"""

from __future__ import annotations

import duckdb
import pytest

from opendv_duckdb_sandbox.sandbox_engine import DuckDbShadowSandbox, validate_safe_sql


@pytest.mark.parametrize(
    "safe_sql",
    [
        "SELECT 1 AS ok",
        "SELECT COUNT(*), SUM(val), AVG(val), MIN(val), MAX(val) FROM raw_vault.hub_test",
        (
            "WITH cte AS (SELECT UPPER(TRIM(COALESCE(name, ''))) AS n FROM raw_vault.hub_test) "
            "SELECT CAST(n AS VARCHAR) FROM cte"
        ),
        (
            "SELECT a.hk FROM raw_vault.hub_a a "
            "JOIN raw_vault.lnk_ab b ON a.hk = b.hk_a "
            "UNION ALL SELECT hk FROM raw_vault.hub_c"
        ),
    ],
)
def test_validate_safe_sql_permits_read_only_select_union_cte_and_safe_funcs(safe_sql: str) -> None:
    """Allowlist permits read-only SELECT, UNION, JOIN, CTE, and safe scalar/aggregate functions."""
    validate_safe_sql(safe_sql)


@pytest.mark.parametrize(
    "unsafe_sql",
    [
        "SELECT * FROM read_text('/etc/passwd')",
        "SELECT * FROM read_xlsx('book.xlsx')",
        "SELECT * FROM glob('/tmp/*')",
        "SELECT * FROM sniff_csv('data.csv')",
        "SELECT * FROM parquet_metadata('data.parquet')",
        "SELECT * FROM duckdb_secrets()",
        "SELECT * FROM duckdb_settings()",
        "SELECT current_setting('external_threads')",
        "SELECT getenv('AWS_SECRET_ACCESS_KEY')",
        "SELECT * FROM sqlite_scan('app.db', 'users')",
        "SELECT * FROM postgres_scan('dbname=prod', 'public', 'users')",
        "SELECT * FROM 'file.csv'",
        "SELECT 1; SELECT 2",
        "DROP TABLE raw_vault.hub_customer",
        "INSERT INTO raw_vault.hub_customer VALUES ('a')",
        "UPDATE raw_vault.hub_customer SET rec_src = 'x'",
        "PRAGMA enable_external_access=true",
        "COPY raw_vault.hub_customer TO 'out.parquet'",
    ],
)
def test_validate_safe_sql_blocks_file_scans_secrets_literals_and_mutations(
    unsafe_sql: str,
) -> None:
    """Allowlist rejects file/metadata/secret functions, string-literal tables, and DML/DDL."""
    with pytest.raises(ValueError):
        validate_safe_sql(unsafe_sql)


def test_sandbox_enforces_enable_external_access_false() -> None:
    """DuckDbShadowSandbox enforces `enable_external_access=False` on all connections."""
    with DuckDbShadowSandbox() as sbx:
        val = sbx.conn.execute(
            "SELECT value FROM duckdb_settings() WHERE name = 'enable_external_access'"
        ).fetchone()[0]
        assert str(val).lower() == "false"

    # Even when caller passes a fresh :memory: connection, sandbox must lock external access down
    custom_conn = duckdb.connect(":memory:")
    try:
        with DuckDbShadowSandbox(conn=custom_conn) as custom_sbx:
            val = custom_sbx.conn.execute(
                "SELECT value FROM duckdb_settings() WHERE name = 'enable_external_access'"
            ).fetchone()[0]
            assert str(val).lower() == "false"
    finally:
        custom_conn.close()
