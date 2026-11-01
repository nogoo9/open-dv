"""Smoke test for opendv-duckdb-sandbox."""

from opendv_duckdb_sandbox import __version__


def test_smoke() -> None:
    assert __version__ == "0.1.0"
