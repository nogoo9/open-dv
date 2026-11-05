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
