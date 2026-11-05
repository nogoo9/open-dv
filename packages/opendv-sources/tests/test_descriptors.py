"""Unit tests for descriptor and capability models."""

from __future__ import annotations

from opendv_sources.models.descriptors import (
    ColumnDescriptor,
    SourceCapabilities,
    SourceDescriptor,
)


def test_column_descriptor_defaults() -> None:
    """Verify ColumnDescriptor defaults and properties."""
    col = ColumnDescriptor(name="customer_id", physical_type="VARCHAR")
    assert col.name == "customer_id"
    assert col.physical_type == "VARCHAR"
    assert col.nullable is True


def test_source_capabilities_defaults() -> None:
    """Verify SourceCapabilities default settings."""
    caps = SourceCapabilities()
    assert caps.supports_streaming is False
    assert caps.supports_pushdown is False
    assert caps.supports_drift_detection is True
    assert caps.client_uploadable is False
    assert caps.server_resolved is True


def test_source_descriptor_construction() -> None:
    """Verify SourceDescriptor aggregation."""
    cols = [
        ColumnDescriptor(name="id", physical_type="BIGINT", nullable=False),
        ColumnDescriptor(name="amount", physical_type="DOUBLE", nullable=True),
    ]
    desc = SourceDescriptor(
        adapter="file",
        format="parquet",
        redacted_uri="data/transactions.parquet",
        columns=cols,
        row_count_estimate=15000,
        fingerprint="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        capabilities=SourceCapabilities(supports_streaming=True),
    )

    assert desc.adapter == "file"
    assert desc.format == "parquet"
    assert desc.redacted_uri == "data/transactions.parquet"
    assert len(desc.columns) == 2
    assert desc.row_count_estimate == 15000
    assert desc.fingerprint == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert desc.capabilities.supports_streaming is True
