"""Alias module for SourceAdapterRegistry."""

from opendv_sources.registry.source_registry import (
    SOURCE_ADAPTER_ENTRY_POINT_GROUP,
    SourceAdapterRegistry,
    get_source_registry,
)

__all__ = [
    "SOURCE_ADAPTER_ENTRY_POINT_GROUP",
    "SourceAdapterRegistry",
    "get_source_registry",
]
