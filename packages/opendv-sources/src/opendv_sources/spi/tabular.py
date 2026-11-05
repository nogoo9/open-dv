"""High-level domain port for tabular data sources registering into DuckDB."""

from __future__ import annotations

from abc import ABC, abstractmethod

import duckdb

from opendv_sources.models.descriptors import SourceCapabilities, SourceDescriptor
from opendv_sources.models.specs import SourceSpec


class TabularSourceAdapter(ABC):
    """High-level domain port for registering relational feeds into DuckDB and profiling."""

    @property
    @abstractmethod
    def adapter_name(self) -> str:
        """Unique adapter identifier (e.g. 'file', 'inline', 'duckdb_attach')."""
        ...

    @abstractmethod
    def describe(self, spec: SourceSpec) -> SourceDescriptor:
        """Inspect schema, types, row count, and content hash without full materialization."""
        ...

    @abstractmethod
    def register(
        self,
        spec: SourceSpec,
        conn: duckdb.DuckDBPyConnection,
        relation_name: str,
    ) -> None:
        """Expose the source data as an in-memory or view DuckDB relation."""
        ...

    @abstractmethod
    def fingerprint(self, spec: SourceSpec) -> str:
        """Deterministic SHA-256 hash or version snapshot for CAS provenance."""
        ...

    @abstractmethod
    def capabilities(self, spec: SourceSpec) -> SourceCapabilities:
        """Declare drift, pushdown, and streaming capabilities."""
        ...


__all__ = ["TabularSourceAdapter"]
