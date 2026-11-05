"""Reusable conformance test suite for TabularSourceAdapter plugins (T16)."""

from __future__ import annotations

from typing import Any

import duckdb

from opendv_sources.exceptions import SourceEmptyError, SourceError, SourceLoadError
from opendv_sources.models.descriptors import SourceCapabilities, SourceDescriptor
from opendv_sources.models.specs import SourceSpec
from opendv_sources.spi.tabular import TabularSourceAdapter


class TabularAdapterConformanceSuite:
    """Base conformance test suite that all TabularSourceAdapter implementations must satisfy.

    Plugin implementations inherit from this class in their test suites and provide
    the required fixtures (adapter, sample_spec, mutated_spec, empty_spec, corrupt_spec, conn).
    """

    @staticmethod
    def _assert_raises(
        expected_type: type[BaseException] | tuple[type[BaseException], ...],
        fn: Any,
        *args: Any,
        **kwargs: Any,
    ) -> BaseException:
        """Assert that executing `fn(*args, **kwargs)` raises an exception of `expected_type`."""
        try:
            fn(*args, **kwargs)
        except expected_type as exc:
            return exc
        except BaseException as exc:
            type_name = (
                "/".join(t.__name__ for t in expected_type)
                if isinstance(expected_type, tuple)
                else expected_type.__name__
            )
            raise AssertionError(
                f"Expected exception {type_name}, but {type(exc).__name__} was raised: {exc}"
            ) from exc
        type_name = (
            "/".join(t.__name__ for t in expected_type)
            if isinstance(expected_type, tuple)
            else expected_type.__name__
        )
        raise AssertionError(f"Expected exception {type_name}, but no exception was raised.")

    def test_describe_matches_data(
        self,
        adapter: TabularSourceAdapter,
        sample_spec: SourceSpec,
    ) -> None:
        """Verify describe() returns a valid SourceDescriptor matching source metadata."""
        desc = adapter.describe(sample_spec)
        assert isinstance(desc, SourceDescriptor), f"Expected SourceDescriptor, got {type(desc)}"
        assert desc.adapter == adapter.adapter_name
        assert desc.format, "Resolved format must be specified"
        assert desc.columns, "Columns list must not be empty"
        for col in desc.columns:
            assert col.name, "Column name must not be empty"
            assert col.physical_type, f"Column '{col.name}' missing physical_type"
        assert desc.fingerprint, "Deterministic fingerprint must be populated"

    def test_register_exposes_all_rows_and_columns(
        self,
        adapter: TabularSourceAdapter,
        sample_spec: SourceSpec,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        """Verify register() attaches an accessible DuckDB relation with expected rows."""
        relation_name = "conformance_tbl_test"
        adapter.register(sample_spec, conn, relation_name)

        result = conn.execute(f'SELECT * FROM "{relation_name}"').fetchall()
        assert len(result) > 0, "Registered relation returned zero rows"

        desc = adapter.describe(sample_spec)
        if desc.row_count_estimate is not None:
            assert len(result) == desc.row_count_estimate, (
                f"Row count mismatch: expected {desc.row_count_estimate}, got {len(result)}"
            )

    def test_fingerprint_stability_and_mutation(
        self,
        adapter: TabularSourceAdapter,
        sample_spec: SourceSpec,
        mutated_spec: SourceSpec,
    ) -> None:
        """Verify fingerprint is deterministic and mutates when source data changes."""
        fp1 = adapter.fingerprint(sample_spec)
        fp2 = adapter.fingerprint(sample_spec)
        assert fp1 == fp2, "Fingerprint must be deterministic across repeated calls"

        fp_mut = adapter.fingerprint(mutated_spec)
        assert fp1 != fp_mut, "Fingerprint must change when underlying source data mutates"

    def test_empty_source_raises_typed_error(
        self,
        adapter: TabularSourceAdapter,
        empty_spec: SourceSpec,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        """Verify empty sources raise SourceEmptyError or SourceLoadError."""
        self._assert_raises(
            (SourceEmptyError, SourceLoadError),
            adapter.register,
            empty_spec,
            conn,
            "empty_relation",
        )

    def test_corrupt_source_raises_typed_error(
        self,
        adapter: TabularSourceAdapter,
        corrupt_spec: SourceSpec,
        conn: duckdb.DuckDBPyConnection,
    ) -> None:
        """Verify corrupt data raises a typed SourceLoadError, not raw crashes."""
        self._assert_raises(
            SourceError,
            adapter.register,
            corrupt_spec,
            conn,
            "corrupt_relation",
        )

    def test_credentials_redacted_in_descriptor(
        self,
        adapter: TabularSourceAdapter,
        secure_spec: SourceSpec,
    ) -> None:
        """Verify descriptors never leak raw secret values in redacted_uri or fields."""
        desc = adapter.describe(secure_spec)
        for secret_token in ["password", "secret123", "supersecret", "token"]:
            assert secret_token not in desc.redacted_uri.lower(), (
                f"Secret token '{secret_token}' found in redacted_uri: {desc.redacted_uri}"
            )

    def test_capabilities_contract(
        self,
        adapter: TabularSourceAdapter,
        sample_spec: SourceSpec,
    ) -> None:
        """Verify capabilities() returns valid SourceCapabilities."""
        caps = adapter.capabilities(sample_spec)
        assert isinstance(caps, SourceCapabilities)


__all__ = ["TabularAdapterConformanceSuite"]
