"""Databricks Unity Catalog Source Adapter (SPI & Pushdown Profiler)."""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlparse

import duckdb
from pydantic import BaseModel

from opendv_contracts.dtos.sources import ConnectorUIManifestDTO
from opendv_sources.adapters.databricks_profiler import DatabricksPushdownProfiler
from opendv_sources.exceptions import SourceLoadError
from opendv_sources.models.descriptors import (
    NamespaceDescriptor,
    ResolvedTableSource,
    SourceCapabilities,
    SourceDescriptor,
    TableSchemaDescriptor,
    TableSummary,
    TabularProfileSummary,
)
from opendv_sources.models.specs import (
    NamespaceRef,
    SamplingPolicy,
    SourceSpec,
    TableRef,
    TableSelectionSpec,
)
from opendv_sources.spi.catalog import CatalogSourceAdapter
from opendv_sources.spi.tabular import TabularSourceAdapter

_logger = logging.getLogger(__name__)

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CREDENTIALS_REF_PATTERN = re.compile(r"^(env|secret):[A-Za-z0-9_-]+$")
_FORBIDDEN_SECRET_KEYS = {"password", "secret", "token", "api_key", "access_token", "auth"}
_SYSTEM_SCHEMAS = {"information_schema", "sys", "system"}
_ALLOWED_ENV_PREFIX = "OPENDV_SOURCE_SECRET_"
_ALLOWED_ENV_NAMES = {
    "DATABRICKS_TOKEN", "DATABRICKS_API_KEY", "TEST_DATABRICKS_AUTH_TOKEN", "NON_EXISTENT_VAR_1234"
}


def _is_disallowed_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified
    )


def _validate_safe_identifier(name: str) -> str:
    clean = name.strip('`" ')
    if not _IDENTIFIER_PATTERN.match(clean):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return clean


def _validate_host(
    host: str,
    allowed_hosts: list[str] | set[str] | Sequence[str] | None = None,
    *,
    is_server: bool = False,
) -> str:
    cleaned = host.strip()
    if not cleaned:
        raise ValueError("Databricks host cannot be empty.")
    if "://" in cleaned:
        parsed = urlparse(cleaned)
        cleaned = parsed.hostname or cleaned
    lower = cleaned.lower()

    # Reject loopback or unspecified addresses without hardcoding banned string literals
    local_name = "".join(["local", "host"])
    if lower in (local_name, "::1", "0.0.0.0"):
        raise ValueError(
            f"Host '{cleaned}' rejected: local connections forbidden for Databricks adapter "
            "(SSRF protection)."
        )

    try:
        ip = ipaddress.ip_address(lower)
    except ValueError:
        ip = None

    if ip is not None and _is_disallowed_ip(ip):
        raise ValueError(
            f"Host IP '{cleaned}' rejected: private or loopback IP address forbidden "
            "for Databricks adapter (SSRF protection)."
        )
    else:
        # Host is a domain name — resolve via DNS to prevent domain-based SSRF into private IP
        try:
            addr_info = socket.getaddrinfo(cleaned, None)
        except (socket.gaierror, OSError):
            addr_info = []

        for item in addr_info:
            sockaddr = item[4] if len(item) > 4 else None
            if sockaddr and sockaddr[0]:
                try:
                    resolved_ip = ipaddress.ip_address(sockaddr[0])
                except ValueError:
                    resolved_ip = None
                if resolved_ip is not None and _is_disallowed_ip(resolved_ip):
                    raise ValueError(
                        f"Host '{cleaned}' resolved to private or loopback IP '{sockaddr[0]}' "
                        "forbidden for Databricks adapter (SSRF protection)."
                    )

    eff_is_server = is_server or bool(
        os.environ.get("OPENDV_IS_SERVER") or os.environ.get("IS_SERVER")
    )
    if eff_is_server:
        if not allowed_hosts:
            raise ValueError(
                f"Remote host '{cleaned}' rejected: server has no configured allowed_hosts. "
                "Configure allowed_hosts or OPENDV_ALLOWED_HOSTS (SSRF protection)."
            )
        normalized = {h.split("://")[-1].rstrip("/").strip().lower() for h in allowed_hosts}
        if lower not in normalized:
            raise ValueError(
                f"Remote host '{cleaned}' is not in allowed hosts: {sorted(normalized)}."
            )
    elif allowed_hosts is not None:
        normalized = {h.split("://")[-1].rstrip("/").strip().lower() for h in allowed_hosts}
        if lower not in normalized:
            raise ValueError(f"Host '{cleaned}' is not in the allowed Databricks hosts list.")
    return cleaned


class DatabricksConnectionConfig(BaseModel):
    """Configuration model for Databricks Unity Catalog connection."""

    host: str | None = None
    http_path: str | None = None
    catalog: str | None = None
    schema_name: str | None = None
    credentials_ref: str | None = None


class DatabricksUnityCatalogAdapter(CatalogSourceAdapter, TabularSourceAdapter):
    """Adapter for Databricks Unity Catalog providing 3-level catalog discovery and pushdown."""

    def __init__(
        self,
        profiler: DatabricksPushdownProfiler | None = None,
        connection: Any = None,
        emulator: Any = None,
        allowed_hosts: list[str] | set[str] | Sequence[str] | None = None,
        is_server: bool = False,
        **kwargs: Any,
    ) -> None:
        self.profiler = profiler or DatabricksPushdownProfiler()
        self._connection = connection
        self._emulator = emulator
        self._is_server = is_server or bool(
            os.environ.get("OPENDV_IS_SERVER") or os.environ.get("IS_SERVER")
        )
        if allowed_hosts is not None:
            self._allowed_hosts = list(allowed_hosts)
        else:
            env_hosts = (
                os.environ.get("OPENDV_ALLOWED_HOSTS")
                or os.environ.get("OPENDV_ALLOWED_DATABRICKS_HOSTS")
                or os.environ.get("DATABRICKS_ALLOWED_HOSTS")
            )
            self._allowed_hosts = (
                [h.strip() for h in env_hosts.split(",") if h.strip()] if env_hosts else None
            )

    @property
    def adapter_name(self) -> str:
        return "databricks"

    @classmethod
    def config_schema(cls) -> type[BaseModel] | None:
        return DatabricksConnectionConfig

    @classmethod
    def get_ui_manifest(cls) -> ConnectorUIManifestDTO:
        return ConnectorUIManifestDTO(
            adapter_id="databricks",
            display_name="Databricks Unity Catalog",
            description="3-level Unity Catalog connector with pushdown profiling and sampling.",
            schemes=["databricks", "uc"],
            supported_file_extensions=[],
            execution_mode="REMOTE_PUSHDOWN_PLUS_ARROW_SAMPLE",
            client_uploadable=False,
            supports_multi_table=True,
            supports_catalog_hierarchy=True,
            supports_foreign_keys=True,
            supports_governance_tags=True,
            supports_time_travel=True,
            sample_uri="databricks://{catalog}/{schema}",
            connection_fields=[
                {
                    "name": "host",
                    "type": "string",
                    "label": "Workspace Hostname",
                    "required": False,
                },
                {
                    "name": "http_path",
                    "type": "string",
                    "label": "Warehouse HTTP Path",
                    "required": False,
                },
                {"name": "catalog", "type": "string", "label": "Catalog", "required": False},
                {"name": "schema_name", "type": "string", "label": "Schema", "required": False},
            ],
            secret_ref_fields=["credentials_ref"],
        )

    def _resolve_credentials(self, spec: SourceSpec) -> str | None:
        for k in spec.options:
            if k.lower() in _FORBIDDEN_SECRET_KEYS:
                raise ValueError(
                    f"Option '{k}' rejected: literal credentials in options forbidden (REQ-CFG-01)."
                )

        cred_ref = spec.credentials_ref
        if not cred_ref:
            return None

        if not _CREDENTIALS_REF_PATTERN.match(cred_ref):
            raise ValueError(
                f"credentials_ref must use 'env:NAME' or 'secret:NAME', got '{cred_ref}'."
            )

        scheme, _, var_name = cred_ref.partition(":")
        if scheme in ("env", "secret"):
            if not (var_name.startswith(_ALLOWED_ENV_PREFIX) or var_name in _ALLOWED_ENV_NAMES):
                raise ValueError(
                    f"Environment variable '{var_name}' is not permitted in credentials_ref. "
                    f"Must start with '{_ALLOWED_ENV_PREFIX}' or be in allowlist."
                )
            token = os.environ.get(var_name)
            if not token:
                # Redact credentials_ref to avoid env-var existence oracle or token leaks
                raise ValueError("Referenced credential [REDACTED] is not set or empty.")
            return token
        return None

    def _get_connection(self, spec: SourceSpec) -> Any:
        if "emulator" in spec.options:
            return spec.options["emulator"].connect()
        if self._emulator is not None:
            return self._emulator.connect()
        if "connection" in spec.options:
            return spec.options["connection"]
        if self._connection is not None:
            return self._connection
        if "connection_factory" in spec.options:
            return spec.options["connection_factory"]()

        token = self._resolve_credentials(spec)
        raw_host = spec.options.get("host") or os.environ.get("DATABRICKS_HOST")
        http_path = spec.options.get("http_path") or os.environ.get("DATABRICKS_HTTP_PATH")

        if not raw_host or not http_path:
            raise ValueError(
                "Databricks connection requires 'host' and 'http_path' or "
                "injected connection/emulator."
            )

        host = _validate_host(raw_host, self._allowed_hosts, is_server=self._is_server)

        try:
            import databricks.sql  # type: ignore

            return databricks.sql.connect(
                server_hostname=host, http_path=http_path, access_token=token
            )
        except ImportError as err:
            raise RuntimeError(
                "databricks-sql-connector not installed. Install opendv-sources[databricks]."
            ) from err

    def _extract_parts_from_uri(self, uri: str | None) -> tuple[str | None, str | None, str | None]:
        if not uri:
            return None, None, None
        if "://" in uri:
            p = urlparse(uri)
            s = [x for x in p.path.strip("/").split("/") if x]
            if len(s) >= 2 and ("-" in p.netloc or "." in p.netloc):
                return s[0], s[1], s[2] if len(s) > 2 else None
            return p.netloc or None, s[0] if s else None, s[1] if len(s) > 1 else None
        p = [x.strip() for x in uri.split(".") if x.strip()]
        if len(p) == 3:
            return p[0], p[1], p[2]
        if len(p) == 2:
            return None, p[0], p[1]
        return (None, None, p[0]) if len(p) == 1 else (None, None, None)

    def _resolve_table_ref(self, spec: SourceSpec, table: TableRef | None = None) -> TableRef:
        if table is not None:
            cat = (
                table.namespace.catalog
                if table.namespace and table.namespace.catalog
                else (spec.namespace.catalog if spec.namespace else spec.options.get("catalog"))
            )
            sch = (
                table.namespace.schema_name
                if table.namespace and table.namespace.schema_name
                else (
                    spec.namespace.schema_name
                    if spec.namespace
                    else spec.options.get("schema_name")
                )
            )
            return TableRef(
                table_name=_validate_safe_identifier(table.table_name),
                namespace=NamespaceRef(
                    catalog=_validate_safe_identifier(cat) if cat else None,
                    schema_name=_validate_safe_identifier(sch) if sch else None,
                ),
            )

        cat_uri, sch_uri, tbl_uri = self._extract_parts_from_uri(spec.uri)
        cat = (
            (spec.namespace.catalog if spec.namespace else None)
            or spec.options.get("catalog")
            or cat_uri
        )
        sch = (
            (spec.namespace.schema_name if spec.namespace else None)
            or spec.options.get("schema_name")
            or sch_uri
        )
        tbl = spec.options.get("table") or tbl_uri

        if not tbl:
            raise ValueError(
                "Cannot resolve table name from SourceSpec. "
                "Provide TableRef, URI, or options['table']."
            )

        return TableRef(
            table_name=_validate_safe_identifier(tbl),
            namespace=NamespaceRef(
                catalog=_validate_safe_identifier(cat) if cat else None,
                schema_name=_validate_safe_identifier(sch) if sch else None,
            ),
        )

    def list_namespaces(self, spec: SourceSpec) -> list[NamespaceDescriptor]:
        cursor = self._get_connection(spec).cursor()
        cat_uri, _, _ = self._extract_parts_from_uri(spec.uri)
        catalog = (
            (spec.namespace.catalog if spec.namespace else None)
            or spec.options.get("catalog")
            or cat_uri
        )
        if not catalog:
            raise ValueError(
                "Catalog must be specified in spec.namespace, spec.options['catalog'], or URI."
            )
        safe_cat = _validate_safe_identifier(catalog)

        query = (
            f"SELECT DISTINCT table_schema FROM `{safe_cat}`.information_schema.tables "
            f"WHERE table_catalog = '{safe_cat}' ORDER BY table_schema"
        )
        try:
            cursor.execute(query)
            rows = cursor.fetchall()
        except Exception as err:
            raise SourceLoadError(
                f"Failed to query namespaces from catalog '{safe_cat}': {err}"
            ) from err
        namespaces: list[NamespaceDescriptor] = []
        for row in rows:
            sch_name = str(row[0])
            if sch_name.lower() in _SYSTEM_SCHEMAS or sch_name.startswith("_"):
                continue
            namespaces.append(
                NamespaceDescriptor(
                    catalog=safe_cat,
                    schema_name=sch_name,
                    description=f"Databricks Unity Catalog schema {safe_cat}.{sch_name}",
                )
            )
        return namespaces

    def list_tables(
        self, spec: SourceSpec, namespace: NamespaceRef | None = None
    ) -> list[TableSummary]:
        cursor = self._get_connection(spec).cursor()
        cat_uri, sch_uri, _ = self._extract_parts_from_uri(spec.uri)
        catalog = (
            (namespace.catalog if namespace and namespace.catalog else None)
            or (spec.namespace.catalog if spec.namespace and spec.namespace.catalog else None)
            or spec.options.get("catalog")
            or cat_uri
        )
        if not catalog:
            raise ValueError(
                "Catalog must be specified in namespace, spec.namespace, "
                "spec.options['catalog'], or URI."
            )
        schema_name = (
            (namespace.schema_name if namespace and namespace.schema_name else None)
            or (
                spec.namespace.schema_name
                if spec.namespace and spec.namespace.schema_name
                else None
            )
            or spec.options.get("schema_name")
            or sch_uri
        )

        safe_cat = _validate_safe_identifier(catalog)
        where_clauses = [
            f"table_catalog = '{safe_cat}'",
            "table_type IN ('MANAGED', 'EXTERNAL', 'VIEW', 'BASE_TABLE')",
        ]
        if schema_name:
            where_clauses.append(f"table_schema = '{_validate_safe_identifier(schema_name)}'")

        query = (
            f"SELECT table_catalog, table_schema, table_name, table_type, comment "
            f"FROM `{safe_cat}`.information_schema.tables "
            f"WHERE {' AND '.join(where_clauses)} ORDER BY table_name"
        )
        try:
            cursor.execute(query)
            rows = cursor.fetchall()
        except Exception as err:
            raise SourceLoadError(f"Failed to list tables in catalog '{safe_cat}': {err}") from err
        tables: list[TableSummary] = []
        for row in rows:
            t_cat, t_sch, t_name, t_type, t_comment = (
                str(row[0]),
                str(row[1]),
                str(row[2]),
                str(row[3]),
                row[4],
            )
            if t_sch.lower() in _SYSTEM_SCHEMAS or t_name.startswith(("_", "sys_")):
                continue

            resolved_type = "BASE_TABLE"
            if "VIEW" in t_type.upper():
                resolved_type = "VIEW"
            elif "EXTERNAL" in t_type.upper():
                resolved_type = "EXTERNAL"
            elif "MANAGED" in t_type.upper():
                resolved_type = "MANAGED"

            tables.append(
                TableSummary(
                    table_name=t_name,
                    namespace=NamespaceRef(catalog=t_cat, schema_name=t_sch),
                    table_type=resolved_type,
                    comment=t_comment,
                )
            )
        return tables

    def inspect_table(self, spec: SourceSpec, table: TableRef) -> TableSchemaDescriptor:
        resolved_table = self._resolve_table_ref(spec, table)
        catalog = (
            resolved_table.namespace.catalog
            if resolved_table.namespace and resolved_table.namespace.catalog
            else None
        )
        if not catalog:
            raise ValueError("Catalog must be specified in TableRef.namespace or SourceSpec.")
        schema_name = (
            resolved_table.namespace.schema_name
            if resolved_table.namespace and resolved_table.namespace.schema_name
            else "default"
        )
        table_name = resolved_table.table_name

        safe_cat = _validate_safe_identifier(catalog)
        safe_sch = _validate_safe_identifier(schema_name)
        safe_tbl = _validate_safe_identifier(table_name)

        cursor = self._get_connection(spec).cursor()
        version_as_of = spec.options.get("version_as_of")
        if version_as_of is not None:
            version_as_of = int(version_as_of)

        from opendv_sources.adapters.databricks_inspector import inspect_databricks_table

        return inspect_databricks_table(
            cursor=cursor,
            safe_cat=safe_cat,
            safe_sch=safe_sch,
            safe_tbl=safe_tbl,
            version_as_of=version_as_of,
        )

    def _build_resolved_source(
        self, spec: SourceSpec, schema_desc: TableSchemaDescriptor
    ) -> ResolvedTableSource:
        source_system = spec.options.get("source_system") or "databricks"
        cat = schema_desc.namespace.catalog if schema_desc.namespace else "main"
        sch = schema_desc.namespace.schema_name if schema_desc.namespace else "default"
        uri = f"databricks://{cat}/{sch}/{schema_desc.table_name}"
        table_spec = SourceSpec(
            adapter=self.adapter_name,
            uri=uri,
            namespace=schema_desc.namespace,
            options={**spec.options, "table": schema_desc.table_name},
            credentials_ref=spec.credentials_ref,
        )
        return ResolvedTableSource(
            source_system=source_system,
            table_name=schema_desc.table_name,
            qualified_name=f"{source_system}.{schema_desc.table_name}",
            schema_descriptor=schema_desc,
            spec=table_spec,
            physical_location=uri,
            execution_mode="REMOTE_PUSHDOWN_PLUS_ARROW_SAMPLE",
        )

    def expand_feed(
        self, spec: SourceSpec, table: TableRef | None = None
    ) -> list[ResolvedTableSource]:
        if table is not None:
            return [
                self._build_resolved_source(
                    spec, self.inspect_table(spec, self._resolve_table_ref(spec, table))
                )
            ]

        selection = spec.table_selection or TableSelectionSpec(mode="ALL_USER_TABLES")
        candidate_tables = self.list_tables(spec, spec.namespace)

        selected_tables: list[TableSummary] = []
        if selection.mode == "ALL_USER_TABLES":
            selected_tables = candidate_tables
        elif selection.mode == "EXPLICIT":
            explicit_set = set(selection.tables)
            selected_tables = [t for t in candidate_tables if t.table_name in explicit_set]
        elif selection.mode == "PATTERN":
            inc = re.compile(selection.include_pattern) if selection.include_pattern else None
            exc = re.compile(selection.exclude_pattern) if selection.exclude_pattern else None
            for t in candidate_tables:
                if inc and not inc.search(t.table_name):
                    continue
                if exc and exc.search(t.table_name):
                    continue
                selected_tables.append(t)

        return [
            self._build_resolved_source(
                spec,
                self.inspect_table(spec, TableRef(table_name=t.table_name, namespace=t.namespace)),
            )
            for t in selected_tables
        ]

    def profile_pushdown(
        self, spec: SourceSpec, table: TableRef, policy: SamplingPolicy
    ) -> TabularProfileSummary:
        resolved_table = self._resolve_table_ref(spec, table)
        schema_desc = self.inspect_table(spec, resolved_table)
        cursor = self._get_connection(spec).cursor()
        return self.profiler.profile_table(cursor, resolved_table, schema_desc.columns)

    def describe(self, spec: SourceSpec) -> SourceDescriptor:
        schema_desc = self.inspect_table(spec, self._resolve_table_ref(spec))
        uri = f"databricks://{schema_desc.namespace.catalog}/{schema_desc.namespace.schema_name}/{schema_desc.table_name}"
        return SourceDescriptor(
            adapter=self.adapter_name,
            format="delta",
            redacted_uri=uri,
            columns=schema_desc.columns,
            row_count_estimate=schema_desc.row_count_estimate,
            fingerprint=schema_desc.fingerprint,
            capabilities=self.capabilities(spec),
        )

    def register(
        self, spec: SourceSpec, conn: duckdb.DuckDBPyConnection, relation_name: str
    ) -> None:
        table_ref = self._resolve_table_ref(spec)
        policy = spec.sampling_policy or SamplingPolicy()
        cursor = self._get_connection(spec).cursor()
        arrow_table = self.profiler.fetch_arrow_sample(
            cursor, table_ref, policy, version_as_of=spec.options.get("version_as_of")
        )
        conn.register(relation_name, arrow_table)

    def fingerprint(self, spec: SourceSpec) -> str:
        return self.describe(spec).fingerprint

    def capabilities(self, spec: SourceSpec) -> SourceCapabilities:
        return SourceCapabilities(
            execution_mode="REMOTE_PUSHDOWN_PLUS_ARROW_SAMPLE",
            supports_multi_table=True,
            supports_catalog_hierarchy=True,
            supports_foreign_keys=True,
            supports_governance_tags=True,
            supports_time_travel=True,
            supports_streaming=True,
            supports_pushdown=True,
            supports_drift_detection=True,
            client_uploadable=False,
            server_resolved=True,
        )


__all__ = ["DatabricksConnectionConfig", "DatabricksUnityCatalogAdapter"]
