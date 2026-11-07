"""Registries for discovering and retrieving source adapters and format parsers."""

from opendv_sources.registry.format_registry import (
    FORMAT_PARSER_ENTRY_POINT_GROUP,
    FormatParserRegistry,
    get_format_registry,
)
from opendv_sources.registry.source_registry import (
    SOURCE_ADAPTER_ENTRY_POINT_GROUP,
    SourceAdapterRegistry,
    get_source_registry,
)

__all__ = [
    "FORMAT_PARSER_ENTRY_POINT_GROUP",
    "SOURCE_ADAPTER_ENTRY_POINT_GROUP",
    "FormatParserRegistry",
    "SourceAdapterRegistry",
    "get_format_registry",
    "get_source_registry",
]
