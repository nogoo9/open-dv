"""Unit tests for DatabricksUnityCatalogAdapter.

Validates entry points, credentials validation (REQ-CFG-01), safe identifier
guards, 3-level browsing, PK/FK/tags inspection, feed expansion, and DuckDB registration.
"""

from __future__ import annotations

import importlib.metadata
import socket
from typing import Any

import pytest

from opendv_contracts.dtos.sources import ConnectorUIManifestDTO
from opendv_sources.adapters.databricks_uc import (
    DatabricksConnectionConfig,
    DatabricksUnityCatalogAdapter,
    _validate_safe_identifier,
)
from opendv_sources.models.descriptors import SourceCapabilities, TableSchemaDescriptor
from opendv_sources.models.specs import (
    NamespaceRef,
    SamplingPolicy,
    SourceSpec,
    TableRef,
    TableSelectionSpec,
)
from opendv_testkit.uc_emulator import DuckDBUnityCatalogEmulator


@pytest.fixture
def uc_emulator() -> DuckDBUnityCatalogEmulator:
    """Fixture providing local DuckDB Unity Catalog emulator seeded with IBOR data."""
    emulator = DuckDBUnityCatalogEmulator(default_catalog="am_prod")
    yield emulator
    emulator.close()


@pytest.fixture
def uc_adapter() -> DatabricksUnityCatalogAdapter:
    """Fixture providing Databricks adapter instance."""
    return DatabricksUnityCatalogAdapter()


def test_entry_point_registration() -> None:
    """Verify adapter is registered under opendv.source_adapters entry points."""
    eps = importlib.metadata.entry_points(group="opendv.source_adapters")
    adapter_names = {ep.name for ep in eps}
    assert "databricks" in adapter_names
    assert "uc" in adapter_names


def test_config_schema_and_manifest(uc_adapter: DatabricksUnityCatalogAdapter) -> None:
    """Verify config_schema and dynamic ConnectorUIManifest."""
    schema_cls = uc_adapter.config_schema()
    assert schema_cls is DatabricksConnectionConfig

    manifest = uc_adapter.get_ui_manifest()
    assert isinstance(manifest, ConnectorUIManifestDTO)
    assert manifest.adapter_id == "databricks"
    assert "databricks" in manifest.schemes
    assert manifest.execution_mode == "REMOTE_PUSHDOWN_PLUS_ARROW_SAMPLE"
    assert manifest.supports_catalog_hierarchy is True
    assert manifest.supports_governance_tags is True
    assert manifest.supports_foreign_keys is True
    assert manifest.supports_time_travel is True
    assert "credentials_ref" in manifest.secret_ref_fields


def test_capabilities(uc_adapter: DatabricksUnityCatalogAdapter) -> None:
    """Verify capabilities exposed by Databricks adapter."""
    spec = SourceSpec(adapter="databricks", uri="databricks://am_prod/ibor/positions")
    caps = uc_adapter.capabilities(spec)
    assert isinstance(caps, SourceCapabilities)
    assert caps.execution_mode == "REMOTE_PUSHDOWN_PLUS_ARROW_SAMPLE"
    assert caps.supports_multi_table is True
    assert caps.supports_catalog_hierarchy is True
    assert caps.supports_foreign_keys is True
    assert caps.supports_governance_tags is True
    assert caps.supports_time_travel is True
    assert caps.supports_pushdown is True


def test_credentials_validation_fails_on_literal_tokens(
    uc_adapter: DatabricksUnityCatalogAdapter,
) -> None:
    """Fail closed when literal passwords/tokens are present in options (REQ-CFG-01)."""
    # Pydantic validator on SourceSpec rejects disallowed option keys
    with pytest.raises(ValueError, match="literal credentials"):
        SourceSpec(
            adapter="databricks",
            options={"token": "dapi12345abcdef"},
        )

    with pytest.raises(ValueError, match="literal credentials"):
        SourceSpec(
            adapter="databricks",
            options={"password": "supersecretpassword"},
        )


def test_credentials_resolution_env_and_secret(
    uc_adapter: DatabricksUnityCatalogAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify credentials_ref format validation and environment resolution."""
    # Malformed credentials_ref
    with pytest.raises(ValueError, match="credentials_ref must use 'env:NAME'"):
        SourceSpec(adapter="databricks", credentials_ref="raw_token_xyz")

    # Missing env variable
    spec_missing = SourceSpec(adapter="databricks", credentials_ref="env:NON_EXISTENT_VAR_1234")
    with pytest.raises(ValueError, match="is not set"):
        uc_adapter._resolve_credentials(spec_missing)

    # Valid env variable
    monkeypatch.setenv("TEST_DATABRICKS_AUTH_TOKEN", "valid-auth-token-999")
    spec_valid = SourceSpec(adapter="databricks", credentials_ref="env:TEST_DATABRICKS_AUTH_TOKEN")
    token = uc_adapter._resolve_credentials(spec_valid)
    assert token == "valid-auth-token-999"


def test_safe_identifier_guards() -> None:
    """Verify strict identifier validation blocks SQL injection attempts."""
    valid_idents = ["am_prod", "ibor", "positions_2026", "_private_table", "table1"]
    for ident in valid_idents:
        assert _validate_safe_identifier(ident) == ident.strip('`" ')

    invalid_idents = [
        "am_prod; DROP TABLE users",
        "tbl' OR 1=1 --",
        "cat.schema.tbl",
        "has spaces",
        "table-with-dash",
        "tbl$injection",
    ]
    for bad in invalid_idents:
        with pytest.raises(ValueError, match="Unsafe SQL identifier"):
            _validate_safe_identifier(bad)


def test_list_namespaces(
    uc_adapter: DatabricksUnityCatalogAdapter, uc_emulator: DuckDBUnityCatalogEmulator
) -> None:
    """Test 3-level catalog namespace browsing."""
    spec = SourceSpec(
        adapter="databricks",
        uri="databricks://am_prod",
        options={"emulator": uc_emulator, "catalog": "am_prod"},
    )
    namespaces = uc_adapter.list_namespaces(spec)
    assert len(namespaces) >= 1
    ns_schemas = [ns.schema_name for ns in namespaces]
    assert "ibor" in ns_schemas
    # Ensure system schemas are excluded
    assert "information_schema" not in ns_schemas


def test_list_tables(
    uc_adapter: DatabricksUnityCatalogAdapter, uc_emulator: DuckDBUnityCatalogEmulator
) -> None:
    """Test listing user tables filtering out internal and system schemas."""
    spec = SourceSpec(
        adapter="databricks",
        uri="databricks://am_prod/ibor",
        options={"emulator": uc_emulator, "catalog": "am_prod", "schema_name": "ibor"},
    )
    tables = uc_adapter.list_tables(spec, NamespaceRef(catalog="am_prod", schema_name="ibor"))
    assert len(tables) == 3
    table_names = {t.table_name for t in tables}
    assert table_names == {"security_master", "positions", "trades"}
    for t in tables:
        assert t.table_type in ("MANAGED", "BASE_TABLE")


def test_inspect_table_with_pk_fk_and_tags(
    uc_adapter: DatabricksUnityCatalogAdapter, uc_emulator: DuckDBUnityCatalogEmulator
) -> None:
    """Test full schema inspection with PKs, FKs, comments, and governance tags."""
    spec = SourceSpec(
        adapter="databricks",
        uri="databricks://am_prod/ibor/positions",
        options={"emulator": uc_emulator},
    )
    tbl_ref = TableRef(
        table_name="positions",
        namespace=NamespaceRef(catalog="am_prod", schema_name="ibor"),
    )
    desc = uc_adapter.inspect_table(spec, tbl_ref)
    assert isinstance(desc, TableSchemaDescriptor)
    assert desc.table_name == "positions"
    assert desc.primary_key_columns == ["position_id"]

    col_map = {c.name: c for c in desc.columns}
    assert "position_id" in col_map
    assert col_map["position_id"].is_primary_key is True
    assert col_map["position_id"].primary_key_ordinal == 1

    # Check Foreign Key hint on instrument_id
    assert "instrument_id" in col_map
    assert col_map["instrument_id"].foreign_key_ref is not None
    assert col_map["instrument_id"].foreign_key_ref.target_column == "instrument_id"

    # Check governance tags
    assert "custodian_account_no" in col_map
    assert col_map["custodian_account_no"].governance_tags.get("pii") == "true"
    assert col_map["custodian_account_no"].governance_tags.get("sensitivity") == "restricted"

    assert "market_value" in col_map
    assert col_map["market_value"].governance_tags.get("sensitivity") == "confidential"

    # Check deterministic fingerprint
    assert len(desc.fingerprint) == 64


def test_fingerprint_delta_version_sensitivity(
    uc_adapter: DatabricksUnityCatalogAdapter, uc_emulator: DuckDBUnityCatalogEmulator
) -> None:
    """Verify Delta version pinning affects table fingerprint deterministically."""
    spec_v1 = SourceSpec(
        adapter="databricks",
        uri="databricks://am_prod/ibor/positions",
        options={"emulator": uc_emulator, "version_as_of": 1},
    )
    spec_v2 = SourceSpec(
        adapter="databricks",
        uri="databricks://am_prod/ibor/positions",
        options={"emulator": uc_emulator, "version_as_of": 2},
    )
    tbl_ref = TableRef(
        table_name="positions",
        namespace=NamespaceRef(catalog="am_prod", schema_name="ibor"),
    )

    desc_v1 = uc_adapter.inspect_table(spec_v1, tbl_ref)
    desc_v2 = uc_adapter.inspect_table(spec_v2, tbl_ref)
    assert desc_v1.fingerprint != desc_v2.fingerprint


def test_expand_feed_all_tables_explicit_and_pattern(
    uc_adapter: DatabricksUnityCatalogAdapter, uc_emulator: DuckDBUnityCatalogEmulator
) -> None:
    """Test feed expansion for ALL_USER_TABLES, EXPLICIT, and PATTERN modes."""
    # 1. ALL_USER_TABLES
    spec_all = SourceSpec(
        adapter="databricks",
        uri="databricks://am_prod/ibor",
        namespace=NamespaceRef(catalog="am_prod", schema_name="ibor"),
        table_selection=TableSelectionSpec(mode="ALL_USER_TABLES"),
        options={"emulator": uc_emulator, "source_system": "am_prod"},
    )
    resolved_all = uc_adapter.expand_feed(spec_all)
    assert len(resolved_all) == 3
    assert {r.table_name for r in resolved_all} == {
        "security_master",
        "positions",
        "trades",
    }
    assert all(r.qualified_name.startswith("am_prod.") for r in resolved_all)

    # 2. EXPLICIT
    spec_explicit = SourceSpec(
        adapter="databricks",
        uri="databricks://am_prod/ibor",
        namespace=NamespaceRef(catalog="am_prod", schema_name="ibor"),
        table_selection=TableSelectionSpec(mode="EXPLICIT", tables=["positions"]),
        options={"emulator": uc_emulator},
    )
    resolved_exp = uc_adapter.expand_feed(spec_explicit)
    assert len(resolved_exp) == 1
    assert resolved_exp[0].table_name == "positions"

    # 3. PATTERN
    spec_pattern = SourceSpec(
        adapter="databricks",
        uri="databricks://am_prod/ibor",
        namespace=NamespaceRef(catalog="am_prod", schema_name="ibor"),
        table_selection=TableSelectionSpec(mode="PATTERN", include_pattern=".*master$"),
        options={"emulator": uc_emulator},
    )
    resolved_pat = uc_adapter.expand_feed(spec_pattern)
    assert len(resolved_pat) == 1
    assert resolved_pat[0].table_name == "security_master"


def test_register_into_duckdb(
    uc_adapter: DatabricksUnityCatalogAdapter, uc_emulator: DuckDBUnityCatalogEmulator
) -> None:
    """Test streaming bounded Arrow sample and registering into local DuckDB."""
    import duckdb

    spec = SourceSpec(
        adapter="databricks",
        uri="databricks://am_prod/ibor/positions",
        namespace=NamespaceRef(catalog="am_prod", schema_name="ibor"),
        sampling_policy=SamplingPolicy(max_sandbox_rows=3),
        options={"emulator": uc_emulator, "table": "positions"},
    )
    local_conn = duckdb.connect(":memory:")
    uc_adapter.register(spec, local_conn, "stg_databricks_positions")

    res = local_conn.execute(
        "SELECT count(*), max(quantity) FROM stg_databricks_positions"
    ).fetchall()
    row_count = res[0][0]
    assert row_count == 3
    local_conn.close()


def test_ssrf_host_protection(uc_adapter: DatabricksUnityCatalogAdapter) -> None:
    """Verify C6: Databricks adapter blocks localhost and private IPs (SSRF protection)."""
    forbidden_hosts = [
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "10.0.0.1",
        "192.168.1.100",
        "172.16.0.5",
    ]
    for bad_host in forbidden_hosts:
        spec = SourceSpec(
            adapter="databricks",
            uri="databricks://main/default",
            options={"host": bad_host, "http_path": "/sql/1.0/endpoints/1234"},
        )
        with pytest.raises(ValueError, match="forbidden for Databricks adapter"):
            uc_adapter._get_connection(spec)


def test_credentials_ref_restricted_prefix_and_redaction(
    uc_adapter: DatabricksUnityCatalogAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify C6: allowed env prefixes/names are accessible, and credentials_ref is redacted."""
    # 1. Disallowed env var (e.g. system env var like PATH)
    monkeypatch.setenv("PATH", "/usr/bin")
    disallowed_spec = SourceSpec(adapter="databricks", credentials_ref="env:PATH")
    with pytest.raises(ValueError, match="is not permitted in credentials_ref"):
        uc_adapter._resolve_credentials(disallowed_spec)

    # 2. Allowed prefix OPENDV_SOURCE_SECRET_* resolves successfully
    monkeypatch.setenv("OPENDV_SOURCE_SECRET_DB_TOKEN", "super-token-42")
    allowed_spec = SourceSpec(
        adapter="databricks", credentials_ref="env:OPENDV_SOURCE_SECRET_DB_TOKEN"
    )
    assert uc_adapter._resolve_credentials(allowed_spec) == "super-token-42"

    # 3. Missing env var produces redacted message without leaking secret name
    missing_spec = SourceSpec(
        adapter="databricks", credentials_ref="env:OPENDV_SOURCE_SECRET_UNSET_VAR"
    )
    with pytest.raises(ValueError) as exc_info:
        uc_adapter._resolve_credentials(missing_spec)
    err = str(exc_info.value)
    assert "UNSET_VAR" not in err
    assert "[REDACTED]" in err
    assert "is not set" in err


def test_missing_catalog_raises_error(uc_adapter: DatabricksUnityCatalogAdapter) -> None:
    """Verify M5: omitting catalog raises clean ValueError instead of am_prod fallback."""
    from unittest.mock import MagicMock

    mock_emulator = MagicMock()
    spec_no_cat = SourceSpec(
        adapter="databricks",
        uri="databricks://",
        options={"emulator": mock_emulator},
    )
    with pytest.raises(ValueError, match="Catalog must be specified"):
        uc_adapter.list_namespaces(spec_no_cat)


def test_composite_foreign_key_hint_ordering(
    uc_adapter: DatabricksUnityCatalogAdapter, uc_emulator: DuckDBUnityCatalogEmulator
) -> None:
    """Verify M3: multi-column composite foreign keys preserve ordered source and target columns."""
    from opendv_sources.models.descriptors import ForeignKeyHint

    # Register a table with composite foreign key in uc_emulator
    uc_emulator.register_table(
        catalog="am_prod",
        schema="ibor",
        table="trade_allocations",
        columns=[
            ("allocation_id", "VARCHAR", False, None),
            ("trade_id", "VARCHAR", False, None),
            ("account_id", "VARCHAR", False, None),
            ("allocated_qty", "DECIMAL(18,4)", True, None),
        ],
        primary_key=["allocation_id"],
        foreign_keys=[
            {
                "target_table": "trades",
                "from_columns": ["trade_id", "account_id"],
                "target_columns": ["trade_id", "account_id"],
            }
        ],
    )

    spec = SourceSpec(
        adapter="databricks",
        uri="databricks://am_prod/ibor/trade_allocations",
        options={"emulator": uc_emulator},
    )
    tbl_ref = TableRef(
        table_name="trade_allocations",
        namespace=NamespaceRef(catalog="am_prod", schema_name="ibor"),
    )
    desc = uc_adapter.inspect_table(spec, tbl_ref)
    assert len(desc.foreign_keys) >= 1
    fk = desc.foreign_keys[0]
    assert isinstance(fk, ForeignKeyHint)
    assert fk.target_table == "trades"
    assert fk.source_columns == ["trade_id", "account_id"]
    assert fk.target_columns == ["trade_id", "account_id"]


def test_validate_host_ssrf_and_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify C6: SSRF protection with IP literal and DNS resolution checks."""
    from opendv_sources.adapters.databricks_uc import _validate_host

    # 1. IP literal loopback, private, link-local, and unspecified rejected
    bad_ips = (
        "127.0.0.1",
        "10.1.2.3",
        "192.168.1.1",
        "172.16.0.5",
        "169.254.169.254",
        "0.0.0.0",
        "::1",
    )
    for bad_ip in bad_ips:
        with pytest.raises(ValueError, match="forbidden for Databricks adapter"):
            _validate_host(bad_ip)

    # 2. Localhost rejected
    with pytest.raises(ValueError, match="forbidden for Databricks adapter"):
        _validate_host("localhost")

    # 3. DNS resolution resolving to private or loopback IP rejected
    def _mock_getaddrinfo(host: str, port: Any) -> list[Any]:
        if host == "evil.local":
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.99", 0))]
        if host == "imds.attacker.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.169.254", 0))]
        if host == "valid.cloud.databricks.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("54.230.1.1", 0))]
        raise socket.gaierror("Name or service not known")

    target = "opendv_sources.adapters.databricks_uc.socket.getaddrinfo"
    monkeypatch.setattr(target, _mock_getaddrinfo)

    with pytest.raises(ValueError, match="resolved to private or loopback IP"):
        _validate_host("evil.local")

    with pytest.raises(ValueError, match="resolved to private or loopback IP"):
        _validate_host("imds.attacker.com")

    # 4. Valid host passing DNS resolution
    assert _validate_host("valid.cloud.databricks.com") == "valid.cloud.databricks.com"

    # 5. Allowed hosts allowlist enforcement
    assert (
        _validate_host("valid.cloud.databricks.com", allowed_hosts=["valid.cloud.databricks.com"])
        == "valid.cloud.databricks.com"
    )
    with pytest.raises(ValueError, match="not in the allowed Databricks hosts list"):
        _validate_host("valid.cloud.databricks.com", allowed_hosts=["other.cloud.databricks.com"])


def test_credentials_ref_secret_scheme_allowlist(
    uc_adapter: DatabricksUnityCatalogAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify R2-C1: secret: scheme enforces the same OPENDV_SOURCE_SECRET_* allowlist as env:."""
    # 1. Disallowed variable via secret: scheme is rejected
    monkeypatch.setenv("PATH", "/usr/bin")
    with pytest.raises(ValueError, match="is not permitted in credentials_ref"):
        uc_adapter._resolve_credentials(
            SourceSpec(adapter="databricks", credentials_ref="secret:PATH")
        )

    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-key")
    with pytest.raises(ValueError, match="is not permitted in credentials_ref"):
        uc_adapter._resolve_credentials(
            SourceSpec(adapter="databricks", credentials_ref="secret:OPENAI_API_KEY")
        )

    # 2. Allowed prefix OPENDV_SOURCE_SECRET_* resolves successfully
    monkeypatch.setenv("OPENDV_SOURCE_SECRET_TEST_TOKEN", "secret-token-123")
    spec_allowed = SourceSpec(
        adapter="databricks", credentials_ref="secret:OPENDV_SOURCE_SECRET_TEST_TOKEN"
    )
    assert uc_adapter._resolve_credentials(spec_allowed) == "secret-token-123"

    # 3. Explicit allowlisted name DATABRICKS_TOKEN resolves successfully
    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi123456789")
    spec_name = SourceSpec(adapter="databricks", credentials_ref="secret:DATABRICKS_TOKEN")
    assert uc_adapter._resolve_credentials(spec_name) == "dapi123456789"

    # 4. Missing allowed secret produces redacted error
    spec_unset = SourceSpec(
        adapter="databricks", credentials_ref="secret:OPENDV_SOURCE_SECRET_UNSET"
    )
    with pytest.raises(ValueError) as exc_info:
        uc_adapter._resolve_credentials(spec_unset)
    assert "[REDACTED]" in str(exc_info.value)
    assert "OPENDV_SOURCE_SECRET_UNSET" not in str(exc_info.value)


def test_allowed_hosts_enforcement_and_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify R2-C1: allowed_hosts rejects unapproved hosts and reads from environment variables."""
    from opendv_sources.adapters.databricks_uc import _validate_host

    # Empty collection rejects any host
    with pytest.raises(ValueError, match="not in the allowed Databricks hosts list"):
        _validate_host("valid.cloud.databricks.com", allowed_hosts=[])

    # Normalization strips https:// and trailing slashes
    allowed = ["https://corp.cloud.databricks.com/"]
    assert (
        _validate_host("corp.cloud.databricks.com", allowed_hosts=allowed)
        == "corp.cloud.databricks.com"
    )

    # Adapter respects explicit allowed_hosts
    adapter = DatabricksUnityCatalogAdapter(allowed_hosts=["corp.cloud.databricks.com"])
    spec = SourceSpec(
        adapter="databricks",
        uri="databricks://corp/sch",
        options={"host": "attacker.com", "http_path": "/sql/1.0/endpoints/123"},
    )
    with pytest.raises(ValueError, match="not in the allowed Databricks hosts list"):
        adapter._get_connection(spec)

    # Adapter resolves allowed_hosts from environment variable
    monkeypatch.setenv(
        "OPENDV_ALLOWED_DATABRICKS_HOSTS", "corp1.cloud.databricks.com, corp2.cloud.databricks.com"
    )
    env_adapter = DatabricksUnityCatalogAdapter()
    assert env_adapter._allowed_hosts == [
        "corp1.cloud.databricks.com",
        "corp2.cloud.databricks.com",
    ]
    with pytest.raises(ValueError, match="not in the allowed Databricks hosts list"):
        env_adapter._get_connection(spec)


def test_databricks_inspector_describe_detail_order_and_row_count_estimate() -> None:
    """Verify R2-M7 & R2-C3: DESCRIBE DETAIL runs before DESCRIBE HISTORY."""
    query_log: list[str] = []

    class MockCursor:
        def __init__(self) -> None:
            self.description = [
                ("column_name",),
                ("ordinal_position",),
                ("data_type",),
                ("is_nullable",),
                ("comment",),
            ]
            self._rows: list[Any] = []

        def execute(self, sql: str) -> None:
            query_log.append(sql.strip())
            if "information_schema.columns" in sql:
                self._rows = [("id", 1, "BIGINT", "NO", None)]
                self.description = [
                    ("column_name",),
                    ("ordinal_position",),
                    ("data_type",),
                    ("is_nullable",),
                    ("comment",),
                ]
            elif "DESCRIBE DETAIL" in sql:
                self.description = [("version",), ("numrecords",)]
                self._rows = [(2, 4200)]
            elif "DESCRIBE HISTORY" in sql:
                self.description = [("version",)]
                self._rows = [(2,)]
            else:
                self._rows = []
                self.description = []

        def fetchall(self) -> list[Any]:
            return self._rows

        def fetchone(self) -> Any:
            return self._rows[0] if self._rows else None

    from opendv_sources.adapters.databricks_inspector import inspect_databricks_table

    desc = inspect_databricks_table(MockCursor(), "am_prod", "ibor", "positions")

    # Assert DESCRIBE DETAIL was invoked before DESCRIBE HISTORY
    detail_idx = next(i for i, q in enumerate(query_log) if "DESCRIBE DETAIL" in q)
    history_idx = next(i for i, q in enumerate(query_log) if "DESCRIBE HISTORY" in q)
    assert detail_idx < history_idx, "DESCRIBE DETAIL must run before DESCRIBE HISTORY"

    # Assert row_count_estimate was populated
    assert desc.row_count_estimate == 4200
    assert "v2" in desc.comment


def test_databricks_inspector_catches_dbapi_exception_and_reraises_source_error() -> None:
    """Verify R2-M7: Exception caught on optional metadata while SourceError is re-raised."""
    from opendv_sources.adapters.databricks_inspector import inspect_databricks_table
    from opendv_sources.exceptions import SourceLoadError

    class MockDBAPIError(Exception):
        """Simulated DB-API driver exception."""

    class FailingOptionalCursor:
        def __init__(self) -> None:
            self.description = [
                ("column_name",),
                ("ordinal_position",),
                ("data_type",),
                ("is_nullable",),
                ("comment",),
            ]
            self._rows: list[Any] = []

        def execute(self, sql: str) -> None:
            if "information_schema.columns" in sql:
                self._rows = [("id", 1, "BIGINT", "NO", None)]
                self.description = [
                    ("column_name",),
                    ("ordinal_position",),
                    ("data_type",),
                    ("is_nullable",),
                    ("comment",),
                ]
            elif "table_constraints" in sql or "column_tags" in sql or "DESCRIBE" in sql:
                # Simulate a generic DB-API driver exception
                raise MockDBAPIError("DB-API Driver Error: Permission denied on view")
            else:
                self._rows = []

        def fetchall(self) -> list[Any]:
            return self._rows

        def fetchone(self) -> Any:
            return None

    # Optional metadata failures must NOT crash the inspection
    desc = inspect_databricks_table(FailingOptionalCursor(), "am_prod", "ibor", "positions")
    assert desc.table_name == "positions"
    assert desc.primary_key_columns == []
    assert desc.foreign_keys == []
    assert desc.row_count_estimate is None

    class DomainErrorCursor:
        def execute(self, sql: str) -> None:
            raise SourceLoadError("Explicit domain error")

    # SourceError must NOT be caught as optional metadata failure
    with pytest.raises(SourceLoadError):
        inspect_databricks_table(DomainErrorCursor(), "am_prod", "ibor", "positions")
