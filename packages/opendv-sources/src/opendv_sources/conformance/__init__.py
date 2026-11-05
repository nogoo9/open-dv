"""Conformance test kits for Open-DV data source plugins (T16)."""

from opendv_sources.conformance.document_suite import DocumentAdapterConformanceSuite
from opendv_sources.conformance.tabular_suite import TabularAdapterConformanceSuite

__all__ = [
    "DocumentAdapterConformanceSuite",
    "TabularAdapterConformanceSuite",
]
