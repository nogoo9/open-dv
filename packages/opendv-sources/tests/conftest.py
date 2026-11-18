"""Test fixtures for packages/opendv-sources."""

from __future__ import annotations

import duckdb
import pytest


@pytest.fixture
def duckdb_conn() -> duckdb.DuckDBPyConnection:
    """Provide an in-memory DuckDB connection for testing."""
    conn = duckdb.connect(":memory:")
    yield conn
    conn.close()


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers dynamically for source adapter tests."""
    config.addinivalue_line(
        "markers", "databricks_conformance: Databricks Unity Catalog conformance tests"
    )
    config.addinivalue_line(
        "markers", "live_databricks: Tests requiring live Databricks connection"
    )
