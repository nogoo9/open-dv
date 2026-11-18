"""Service Provider Interface (SPI) for catalog-aware data source adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel

from opendv_sources.models.descriptors import (
    NamespaceDescriptor,
    ResolvedTableSource,
    TableSchemaDescriptor,
    TableSummary,
    TabularProfileSummary,
)
from opendv_sources.models.specs import (
    NamespaceRef,
    SamplingPolicy,
    SourceSpec,
    TableRef,
)


class CatalogSourceAdapter(ABC):
    """High-level domain port for multi-table and hierarchical catalog data sources."""

    @property
    @abstractmethod
    def adapter_name(self) -> str:
        """Unique adapter identifier (e.g. 'sqlite', 'duckdb_attach', 'databricks')."""
        ...

    @abstractmethod
    def list_namespaces(self, spec: SourceSpec) -> list[NamespaceDescriptor]:
        """Discover database, catalog, or schema namespaces exposed by this source."""
        ...

    @abstractmethod
    def list_tables(
        self,
        spec: SourceSpec,
        namespace: NamespaceRef | None = None,
    ) -> list[TableSummary]:
        """List tables available in the specified namespace, skipping internal system tables."""
        ...

    @abstractmethod
    def inspect_table(self, spec: SourceSpec, table: TableRef) -> TableSchemaDescriptor:
        """Inspect schema, primary keys, foreign keys, and metadata for a single table."""
        ...

    @abstractmethod
    def expand_feed(
        self,
        spec: SourceSpec,
        table: TableRef | None = None,
    ) -> list[ResolvedTableSource]:
        """Expand a multi-table or catalog feed into concrete per-table source descriptors."""
        ...

    @abstractmethod
    def profile_pushdown(
        self,
        spec: SourceSpec,
        table: TableRef,
        policy: SamplingPolicy,
    ) -> TabularProfileSummary:
        """Execute remote aggregate SQL profiling directly in the source system."""
        ...

    @classmethod
    def config_schema(cls) -> type[BaseModel] | None:
        """Declare optional connector configuration model for UI and validation."""
        return None


__all__ = ["CatalogSourceAdapter"]
