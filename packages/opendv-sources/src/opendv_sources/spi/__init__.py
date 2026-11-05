"""Service Provider Interfaces (SPI) for Open-DV data sources and formats."""

from opendv_sources.spi.adapter import SourceAdapter
from opendv_sources.spi.document import DocumentSourceAdapter
from opendv_sources.spi.parser import FormatParser
from opendv_sources.spi.tabular import TabularSourceAdapter

__all__ = [
    "DocumentSourceAdapter",
    "FormatParser",
    "SourceAdapter",
    "TabularSourceAdapter",
]
