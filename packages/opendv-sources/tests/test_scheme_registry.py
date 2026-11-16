"""Unit tests for SourceSchemeRegistry and declarative defaults (WS2A.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from opendv_sources.config.defaults import (
    SourceDefaultsConfig,
    get_source_defaults,
    reset_source_defaults,
    set_source_defaults,
)
from opendv_sources.exceptions import UnsupportedFormatError
from opendv_sources.registry.scheme_registry import (
    SourceSchemeRegistry,
    get_scheme_registry,
    reset_scheme_registry,
)


@pytest.fixture(autouse=True)
def _cleanup_registries() -> None:
    """Ensure clean registry state before and after each test."""
    reset_scheme_registry()
    reset_source_defaults()
    yield
    reset_scheme_registry()
    reset_source_defaults()


def test_source_defaults_config_properties() -> None:
    """Verify default configurations, scheme tuples, and internal table exclusions."""
    cfg = get_source_defaults()
    assert "sqlite" in cfg.sqlite_schemes
    assert "sqlite3" in cfg.sqlite_schemes
    assert "databricks" in cfg.databricks_schemes
    assert "uc" in cfg.databricks_schemes

    assert ".sqlite" in cfg.sqlite_extensions
    assert ".sqlite3" in cfg.sqlite_extensions
    assert ".db" in cfg.sqlite_extensions

    assert "sqlite_sequence" in cfg.sqlite_internal_tables
    assert "sqlite_stat1" in cfg.sqlite_internal_tables
    assert "sqlite_" in cfg.sqlite_internal_prefixes

    assert cfg.sqlite_read_only_mode == "ro"
    assert cfg.default_max_sandbox_rows == 10000


def test_source_defaults_override_and_reset() -> None:
    """Verify modifying source defaults persists until reset."""
    custom = SourceDefaultsConfig(default_max_sandbox_rows=50000)
    set_source_defaults(custom)
    assert get_source_defaults().default_max_sandbox_rows == 50000

    reset_source_defaults()
    assert get_source_defaults().default_max_sandbox_rows == 10000


def test_scheme_registry_resolves_sqlite_uris_and_suffixes() -> None:
    """Verify sqlite://, .sqlite, .sqlite3, and .db resolve to sqlite adapter."""
    reg = SourceSchemeRegistry()

    assert reg.resolve_adapter("sqlite:///data/test.sqlite") == "sqlite"
    assert reg.resolve_adapter("sqlite://data/test.db") == "sqlite"
    assert reg.resolve_adapter("sqlite3://data/my.sqlite3") == "sqlite"
    assert reg.resolve_adapter("data/production.sqlite") == "sqlite"
    assert reg.resolve_adapter(Path("data/store.db")) == "sqlite"
    assert reg.resolve_adapter("test.sqlite3") == "sqlite"

    assert reg.is_sqlite("sqlite://remote_path") is True
    assert reg.is_sqlite("data/asset_management.sqlite") is True
    assert reg.is_sqlite(Path("data/chinook.db")) is True
    assert reg.is_sqlite("data/transactions.parquet") is False


def test_scheme_registry_resolves_cloud_and_file_connectors() -> None:
    """Verify databricks, duckdb, parquet, and csv resolution."""
    reg = SourceSchemeRegistry()

    assert (
        reg.resolve_adapter("databricks://dbc-123.cloud.databricks.com/am_prod/ibor")
        == "databricks"
    )
    assert reg.resolve_adapter("uc://am_prod/ibor/positions") == "databricks"
    assert reg.resolve_adapter("duckdb:///data/lake.duckdb") == "duckdb"
    assert reg.resolve_adapter("data/lake.duckdb") == "duckdb"

    assert reg.resolve_adapter("data/feed.parquet") == "file"
    assert reg.resolve_adapter("data/feed.pq") == "file"
    assert reg.resolve_adapter("data/feed.csv") == "file"
    assert reg.resolve_adapter("data/feed.json") == "file"
    assert reg.resolve_adapter("data/offering_memo.pdf") == "pdf"
    assert reg.resolve_adapter("data/notes.txt") == "text"


def test_scheme_registry_explicit_override_takes_precedence() -> None:
    """Verify explicit adapter override bypasses scheme and extension detection."""
    reg = SourceSchemeRegistry()
    assert (
        reg.resolve_adapter("data/feed.parquet", explicit_adapter="custom_lake_adapter")
        == "custom_lake_adapter"
    )


def test_scheme_registry_unrecognized_target_raises_unsupported_format() -> None:
    """Verify unrecognized URI scheme or file suffix raises UnsupportedFormatError."""
    reg = SourceSchemeRegistry()
    with pytest.raises(UnsupportedFormatError) as exc_info:
        reg.resolve_adapter("data/unknown_archive.xyz123")
    assert "Unable to resolve source adapter" in str(exc_info.value)


def test_scheme_registry_resolve_format() -> None:
    """Verify format detection from scheme and extension."""
    reg = SourceSchemeRegistry()
    assert reg.resolve_format("data/am.sqlite") == "sqlite"
    assert reg.resolve_format("sqlite://am") == "sqlite"
    assert reg.resolve_format("databricks://host/cat/sch") == "delta"
    assert reg.resolve_format("data/trades.parquet") == "parquet"
    assert reg.resolve_format("data/trades.csv") == "csv"
    assert (
        reg.resolve_format("data/trades.csv", explicit_format="custom_delimited")
        == "custom_delimited"
    )
    assert reg.resolve_format("mystery_file_no_ext") is None


def test_scheme_registry_multi_table_capable() -> None:
    """Verify multi-table capable detection for catalogs vs flat files."""
    reg = SourceSchemeRegistry()
    assert reg.is_multi_table_capable("data/am.sqlite") is True
    assert reg.is_multi_table_capable("sqlite://cat") is True
    assert reg.is_multi_table_capable("databricks://host/cat/sch") is True
    assert reg.is_multi_table_capable("data/lake.duckdb") is True
    assert reg.is_multi_table_capable("data/positions.parquet") is False
    assert reg.is_multi_table_capable("data/rates.csv") is False


def test_scheme_registry_dynamic_registration() -> None:
    """Verify dynamic addition of custom schemes and extensions."""
    reg = SourceSchemeRegistry()
    reg.register_scheme("snowflake", "snowflake_catalog")
    assert (
        reg.resolve_adapter("snowflake://acct.snowflakecomputing.com/db/schema")
        == "snowflake_catalog"
    )

    reg.register_extension(".avro", "avro_adapter", "avro")
    assert reg.resolve_adapter("data/stream.avro") == "avro_adapter"
    assert reg.resolve_format("data/stream.avro") == "avro"


def test_singleton_scheme_registry_lifecycle() -> None:
    """Verify global singleton access and reset."""
    reg1 = get_scheme_registry()
    reg2 = get_scheme_registry()
    assert reg1 is reg2

    reg1.register_scheme("test_scheme", "test_adapter")
    assert get_scheme_registry().resolve_adapter("test_scheme://xyz") == "test_adapter"

    reset_scheme_registry()
    reg3 = get_scheme_registry()
    assert reg3 is not reg1


def test_cas_uri_scheme_does_not_default_to_sqlite() -> None:
    """Verify R2-C3: cas:// scheme does not force sqlite, but resolves by extension or query."""
    reg = SourceSchemeRegistry()

    # Parquet
    p_uri = "cas://e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855/data.parquet"
    assert reg.resolve_adapter(p_uri) == "file"
    assert reg.resolve_format(p_uri) == "parquet"
    assert reg.is_sqlite(p_uri) is False
    assert reg.is_multi_table_capable(p_uri) is False

    # CSV
    c_uri = "cas://e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855/data.csv"
    assert reg.resolve_adapter(c_uri) == "file"
    assert reg.resolve_format(c_uri) == "csv"
    assert reg.is_sqlite(c_uri) is False
    assert reg.is_multi_table_capable(c_uri) is False

    # DuckDB
    d_uri = "cas://e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855/lake.duckdb"
    assert reg.resolve_adapter(d_uri) == "duckdb"
    assert reg.resolve_format(d_uri) == "duckdb"
    assert reg.is_sqlite(d_uri) is False
    assert reg.is_multi_table_capable(d_uri) is True

    # SQLite
    s_uri = "cas://e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855/store.sqlite"
    assert reg.resolve_adapter(s_uri) == "sqlite"
    assert reg.resolve_format(s_uri) == "sqlite"
    assert reg.is_sqlite(s_uri) is True
    assert reg.is_multi_table_capable(s_uri) is True

    # Query param overrides and format detection
    q_fmt = "cas://e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855?format=parquet"
    assert reg.resolve_adapter(q_fmt) == "file"
    assert reg.resolve_format(q_fmt) == "parquet"

    q_ext = "cas://e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855?ext=csv"
    assert reg.resolve_adapter(q_ext) == "file"
    assert reg.resolve_format(q_ext) == "csv"

    q_adp = "cas://e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855?adapter=duckdb"
    assert reg.resolve_adapter(q_adp) == "duckdb"

    # Explicit override on bare CAS hash
    bare_uri = "cas://e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert reg.resolve_adapter(bare_uri, explicit_adapter="file") == "file"
    assert reg.resolve_adapter(bare_uri, explicit_adapter="sqlite") == "sqlite"

    # Bare CAS hash without adapter or extension fails cleanly with UnsupportedFormatError
    with pytest.raises(UnsupportedFormatError):
        reg.resolve_adapter(bare_uri)
