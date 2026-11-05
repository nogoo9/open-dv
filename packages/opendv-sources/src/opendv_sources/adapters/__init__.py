"""Concrete source adapter implementations."""

from __future__ import annotations

from opendv_sources.adapters.duckdb_attach import DuckdbAttachSourceAdapter
from opendv_sources.adapters.filesystem import LocalFilesystemSourceAdapter
from opendv_sources.adapters.inline import InlineSourceAdapter
from opendv_sources.registry.source_registry import SourceAdapterRegistry, get_source_registry


def register_default_source_adapters(registry: SourceAdapterRegistry | None = None) -> None:
    """Register default source adapters into the given or default registry."""
    reg = registry or get_source_registry()
    reg.register_adapter("file", LocalFilesystemSourceAdapter)
    reg.register_adapter("filesystem", LocalFilesystemSourceAdapter)
    reg.register_adapter("inline", InlineSourceAdapter)
    reg.register_adapter("duckdb_attach", DuckdbAttachSourceAdapter)
    reg.register_adapter("sqlite", DuckdbAttachSourceAdapter)
    reg.register_adapter("duckdb", DuckdbAttachSourceAdapter)


__all__ = [
    "DuckdbAttachSourceAdapter",
    "InlineSourceAdapter",
    "LocalFilesystemSourceAdapter",
    "register_default_source_adapters",
]
