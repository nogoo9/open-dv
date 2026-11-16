"""Centralized configuration defaults for source adapters and scheme registries.

Eliminates magic strings and numbers per ADR-020.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field

from opendv_contracts.dtos.common import BaseContractModel

# Authoritative Module Constants (ADR-003, ADR-010, ADR-020)
ZIP_LOCAL_MAGIC_PREFIXES: tuple[bytes, ...] = (b"PK\x03\x04", b"PK\x05\x06")
OLE_CFB_MAGIC: bytes = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
GROUNDING_THRESHOLD: Decimal = Decimal("0.6500")
FIBO_GROUNDING_THRESHOLD: Decimal = Decimal("0.6500")
DEFAULT_SPARSE_TEXT_THRESHOLD: int = 50
DEFAULT_RASTER_RATIO_THRESHOLD: float = 0.40


class SourceDefaultsConfig(BaseContractModel):
    """Declarative settings and defaults for source resolution and catalog adapters."""

    # Supported scheme tuples
    sqlite_schemes: tuple[str, ...] = ("sqlite", "sqlite3")
    duckdb_schemes: tuple[str, ...] = ("duckdb", "duckdb_attach")
    databricks_schemes: tuple[str, ...] = ("databricks", "uc")
    file_schemes: tuple[str, ...] = ("file",)
    cas_schemes: tuple[str, ...] = ("cas",)

    # Supported file extension tuples
    sqlite_extensions: tuple[str, ...] = (".sqlite", ".sqlite3", ".db")
    duckdb_extensions: tuple[str, ...] = (".duckdb", ".ddb")
    parquet_extensions: tuple[str, ...] = (".parquet", ".pq")
    csv_extensions: tuple[str, ...] = (".csv", ".tsv")
    json_extensions: tuple[str, ...] = (".json", ".jsonl", ".ndjson")
    document_extensions: tuple[str, ...] = (".txt", ".text", ".md", ".markdown", ".pdf")

    # Declarative scheme -> adapter mappings
    scheme_to_adapter: dict[str, str] = Field(
        default_factory=lambda: {
            "sqlite": "sqlite",
            "sqlite3": "sqlite",
            "duckdb": "duckdb",
            "duckdb_attach": "duckdb_attach",
            "databricks": "databricks",
            "uc": "databricks",
            "file": "file",
        }
    )

    # Declarative extension -> adapter mappings
    extension_to_adapter: dict[str, str] = Field(
        default_factory=lambda: {
            ".sqlite": "sqlite",
            ".sqlite3": "sqlite",
            ".db": "sqlite",
            ".duckdb": "duckdb",
            ".ddb": "duckdb",
            ".parquet": "file",
            ".pq": "file",
            ".csv": "file",
            ".tsv": "file",
            ".json": "file",
            ".jsonl": "file",
            ".ndjson": "file",
            ".pdf": "pdf",
            ".txt": "text",
            ".text": "text",
            ".md": "text",
            ".markdown": "text",
        }
    )

    # Declarative extension -> format mappings
    extension_to_format: dict[str, str] = Field(
        default_factory=lambda: {
            ".sqlite": "sqlite",
            ".sqlite3": "sqlite",
            ".db": "sqlite",
            ".duckdb": "duckdb",
            ".ddb": "duckdb",
            ".parquet": "parquet",
            ".pq": "parquet",
            ".csv": "csv",
            ".tsv": "tsv",
            ".json": "json",
            ".jsonl": "jsonl",
            ".ndjson": "ndjson",
            ".pdf": "pdf",
            ".txt": "text",
            ".text": "text",
            ".md": "markdown",
            ".markdown": "markdown",
        }
    )

    # Internal tables to exclude during catalog enumeration
    sqlite_internal_tables: tuple[str, ...] = (
        "sqlite_sequence",
        "sqlite_stat1",
        "sqlite_stat2",
        "sqlite_stat3",
        "sqlite_stat4",
    )
    sqlite_internal_prefixes: tuple[str, ...] = ("sqlite_",)

    # SQLite read-only connection parameters
    sqlite_read_only_mode: str = "ro"
    sqlite_uri_query: str = "?mode=ro"

    # Pushdown & sampling defaults
    default_max_sandbox_rows: int = 10000
    default_timeout_seconds: float = 30.0
    bernoulli_sample_percentage: float = 10.0

    # LibreOffice / OLE file magic prefixes (ADR-020)
    zip_local_magic_prefixes: tuple[bytes, ...] = ZIP_LOCAL_MAGIC_PREFIXES
    ole_cfb_magic: bytes = OLE_CFB_MAGIC

    # FIBO & ingestion thresholds (ADR-003, ADR-010)
    fibo_grounding_threshold: Decimal = FIBO_GROUNDING_THRESHOLD
    default_sparse_text_threshold: int = DEFAULT_SPARSE_TEXT_THRESHOLD
    default_raster_ratio_threshold: float = DEFAULT_RASTER_RATIO_THRESHOLD


# Type alias for external modules per ADR-020
SourcesConfig = SourceDefaultsConfig

_SOURCE_DEFAULTS_INSTANCE: SourceDefaultsConfig | None = None


def get_source_defaults() -> SourceDefaultsConfig:
    """Retrieve global default configuration instance."""
    global _SOURCE_DEFAULTS_INSTANCE
    if _SOURCE_DEFAULTS_INSTANCE is None:
        _SOURCE_DEFAULTS_INSTANCE = SourceDefaultsConfig()
    return _SOURCE_DEFAULTS_INSTANCE


def set_source_defaults(config: SourceDefaultsConfig) -> None:
    """Override global defaults instance (primarily for testing)."""
    global _SOURCE_DEFAULTS_INSTANCE
    _SOURCE_DEFAULTS_INSTANCE = config


def reset_source_defaults() -> None:
    """Reset global defaults instance to fresh defaults."""
    global _SOURCE_DEFAULTS_INSTANCE
    _SOURCE_DEFAULTS_INSTANCE = None


__all__ = [
    "DEFAULT_RASTER_RATIO_THRESHOLD",
    "DEFAULT_SPARSE_TEXT_THRESHOLD",
    "FIBO_GROUNDING_THRESHOLD",
    "GROUNDING_THRESHOLD",
    "OLE_CFB_MAGIC",
    "ZIP_LOCAL_MAGIC_PREFIXES",
    "SourceDefaultsConfig",
    "SourcesConfig",
    "get_source_defaults",
    "reset_source_defaults",
    "set_source_defaults",
]
