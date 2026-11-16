"""Universal source adapter metadata and catalog introspection use cases (WS4.1).

Exposes dynamic connector manifests (list_adapters) and catalog / table introspection
(inspect_source) for SQLite, Databricks Unity Catalog, DuckDB, and file sources.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from opendv_contracts.dtos.sources import (
    ConnectorUIManifestDTO,
    SourceInspectRequestDTO,
    SourceInspectResponseDTO,
    TableSelectionDTO,
)
from opendv_core.paths import PathContainmentError
from opendv_sources.adapters.databricks_uc import DatabricksUnityCatalogAdapter
from opendv_sources.adapters.duckdb_attach import DuckdbAttachSourceAdapter
from opendv_sources.adapters.sqlite_inspector import SqliteCatalogInspector
from opendv_sources.exceptions import SourceError, SourceNotFoundError
from opendv_sources.models.specs import NamespaceRef, SourceSpec, TableRef
from opendv_sources.registry.scheme_registry import SourceSchemeRegistry, get_scheme_registry

_CREDENTIALS_REF_RE = re.compile(r"^(env|secret):[A-Za-z0-9_-]+$")
_HEX64_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_FORBIDDEN_SECRET_KEYS = frozenset(
    {"password", "secret", "token", "api_key", "access_token", "auth", "access_key", "private_key"}
)


def list_adapters(
    registry: SourceSchemeRegistry | None = None,
) -> list[ConnectorUIManifestDTO]:
    """Return declarative UI manifests for all registered connectors."""
    _reg = registry or get_scheme_registry()
    manifests: list[ConnectorUIManifestDTO] = []

    # 1. SQLite Database
    manifests.append(
        ConnectorUIManifestDTO(
            adapter_id="sqlite",
            display_name="SQLite Database",
            description=(
                "Local or staged SQLite database with full PRAGMA schema "
                "and foreign key extraction."
            ),
            schemes=["sqlite", "sqlite3"],
            supported_file_extensions=[".sqlite", ".db", ".sqlite3"],
            execution_mode="DIRECT_DUCKDB_ATTACH",
            client_uploadable=True,
            supports_multi_table=True,
            supports_catalog_hierarchy=False,
            supports_foreign_keys=True,
            supports_governance_tags=False,
            supports_time_travel=False,
            sample_uri="sqlite:///data/warehouse.db",
            connection_fields=[
                {
                    "name": "uri",
                    "type": "string",
                    "label": "Database URI / File Path",
                    "required": True,
                    "description": "Path to SQLite .db/.sqlite file or staged CAS upload handle",
                }
            ],
            secret_ref_fields=[],
        )
    )

    # 2. Databricks Unity Catalog
    manifests.append(DatabricksUnityCatalogAdapter.get_ui_manifest())

    # 3. DuckDB Embedded Database
    manifests.append(
        ConnectorUIManifestDTO(
            adapter_id="duckdb",
            display_name="DuckDB Database",
            description="Embedded analytical DuckDB database with multi-table attachment.",
            schemes=["duckdb", "duckdb_attach"],
            supported_file_extensions=[".duckdb", ".ddb"],
            execution_mode="DIRECT_DUCKDB_ATTACH",
            client_uploadable=True,
            supports_multi_table=True,
            supports_catalog_hierarchy=False,
            supports_foreign_keys=False,
            supports_governance_tags=False,
            supports_time_travel=False,
            sample_uri="duckdb:///data/analytics.duckdb",
            connection_fields=[
                {
                    "name": "uri",
                    "type": "string",
                    "label": "Database URI / File Path",
                    "required": True,
                    "description": "Path to local or staged .duckdb database file",
                }
            ],
            secret_ref_fields=[],
        )
    )

    # 4. Tabular Files (CSV, Parquet, JSON, JSONL)
    manifests.append(
        ConnectorUIManifestDTO(
            adapter_id="file",
            display_name="Delimited & Columnar Files",
            description=(
                "Local or staged tabular files (CSV, Parquet, JSON, JSONL) loaded via DuckDB."
            ),
            schemes=["file"],
            supported_file_extensions=[".csv", ".tsv", ".parquet", ".json", ".jsonl"],
            execution_mode="DIRECT_DUCKDB_ATTACH",
            client_uploadable=True,
            supports_multi_table=False,
            supports_catalog_hierarchy=False,
            supports_foreign_keys=False,
            supports_governance_tags=False,
            supports_time_travel=False,
            sample_uri="file:///data/transactions.parquet",
            connection_fields=[
                {
                    "name": "uri",
                    "type": "string",
                    "label": "File URI / Path",
                    "required": True,
                    "description": "Path to CSV, TSV, Parquet, or JSON file",
                }
            ],
            secret_ref_fields=[],
        )
    )

    return manifests


list_adapter_manifests = list_adapters


def inspect_source(
    request: SourceInspectRequestDTO | dict[str, Any],
    cas_store: Any | None = None,
    *,
    workspace_root: Path | None = None,
    allowed_roots: Sequence[Path] | None = None,
    allowed_hosts: list[str] | set[str] | None = None,
) -> SourceInspectResponseDTO:
    """Inspect and enumerate namespaces, tables, columns, PKs, FKs, and tags from a data source."""
    req: SourceInspectRequestDTO = (
        request
        if isinstance(request, SourceInspectRequestDTO)
        else SourceInspectRequestDTO.model_validate(request)
    )

    # 1. Enforce security constraints: fail closed on invalid or literal credentials
    if req.credentials_ref is not None:
        clean_cred = req.credentials_ref.strip()
        if not _CREDENTIALS_REF_RE.match(clean_cred):
            raise ValueError(
                f"Invalid credentials_ref '{req.credentials_ref}'. "
                "Must match '^(env|secret):[A-Za-z0-9_-]+$' "
                "to prevent hardcoded literal secrets in pipeline specifications."
            )

    if req.options:
        for opt_key in req.options:
            if opt_key.lower() in _FORBIDDEN_SECRET_KEYS:
                raise ValueError(
                    f"Option '{opt_key}' rejected: literal credentials in options forbidden."
                )

    # 2. Resolve CAS-staged binary files (e.g. SQLite uploads)
    eff_uri = req.uri
    cas_hash = req.cas_hash or req.options.get("cas_hash")
    if not cas_hash and req.uri and req.uri.startswith("cas://"):
        cas_hash = req.uri.removeprefix("cas://")
    if cas_hash:
        clean_hash = str(cas_hash).strip()
        if not _HEX64_RE.match(clean_hash):
            raise ValueError(
                f"Invalid cas_hash '{clean_hash}'. Must match '^[0-9a-fA-F]{{64}}$'."
            )
        if cas_store is None:
            raise ValueError(f"Cannot resolve cas_hash '{clean_hash}': no cas_store provided.")

        cas_base_real = (
            os.path.realpath(cas_store.base_dir) if hasattr(cas_store, "base_dir") else None
        )
        staged_path: Path | None = None
        if cas_base_real:
            cas_pfx = cas_base_real if cas_base_real.endswith(os.sep) else cas_base_real + os.sep
            for name in (clean_hash.upper(), clean_hash.lower()):
                creal = os.path.realpath(os.path.join(cas_base_real, name))
                if creal.startswith(cas_pfx) and os.path.isfile(creal):
                    staged_path = Path(creal)
                    break
            if staged_path is None and hasattr(cas_store, "has") and cas_store.has(clean_hash):
                creal = os.path.realpath(os.path.join(cas_base_real, clean_hash.upper()))
                if creal.startswith(cas_pfx) and os.path.isfile(creal):
                    staged_path = Path(creal)

        if staged_path is None:
            raise SourceNotFoundError(f"CAS artifact '{clean_hash}' not found in store.")

        eff_uri = str(staged_path)

    # 3. Resolve source adapter identifier and format
    registry = get_scheme_registry()
    opt_fmt = req.options.get("source_format") or req.options.get("format")
    spec_fmt: str | None = None
    if opt_fmt and isinstance(opt_fmt, str) and opt_fmt.strip():
        spec_fmt = opt_fmt.lower().strip().lstrip(".")

    adapter_id = req.adapter
    if not adapter_id or not adapter_id.strip():
        if spec_fmt:
            if spec_fmt in {"csv", "tsv", "parquet", "json", "jsonl", "ndjson"}:
                adapter_id = "file"
            elif spec_fmt in {"sqlite", "sqlite3", "db"}:
                adapter_id = "sqlite"
            elif spec_fmt in {"duckdb", "ddb"}:
                adapter_id = "duckdb"
        if not adapter_id:
            try:
                adapter_id = registry.resolve_adapter(eff_uri)
            except (SourceError, ValueError, KeyError):
                try:
                    adapter_id = registry.resolve_adapter(req.uri)
                except (SourceError, ValueError, KeyError):
                    if staged_path and staged_path.is_file():
                        try:
                            with open(staged_path, "rb") as bf:
                                head = bf.read(16)
                                if head.startswith(b"SQLite format 3\x00"):
                                    adapter_id = "sqlite"
                                elif head.startswith(b"PAR1"):
                                    adapter_id = "file"
                                    if not spec_fmt:
                                        spec_fmt = "parquet"
                        except OSError:
                            pass
                    if not adapter_id and cas_store and hasattr(cas_store, "get"):
                        c_hash = cas_hash or (
                            req.uri[len("cas://") :].split("/")[0].split("?")[0].strip()
                            if req.uri.startswith("cas://")
                            else None
                        )
                        if c_hash:
                            try:
                                content = cas_store.get(c_hash)
                            except (OSError, KeyError, ValueError):
                                content = None
                            if content:
                                if content.startswith(b"SQLite format 3\x00"):
                                    adapter_id = "sqlite"
                                elif content.startswith(b"PAR1"):
                                    adapter_id = "file"
                                    if not spec_fmt:
                                        spec_fmt = "parquet"
                    if not adapter_id:
                        raise
    clean_adapter_id = adapter_id.lower().strip()

    # 4. Construct SourceSpec and instantiate adapter
    ns_ref: NamespaceRef | None = None
    if req.namespace:
        ns_ref = NamespaceRef(
            catalog=req.namespace.catalog,
            schema_name=req.namespace.schema_name,
        )

    spec = SourceSpec(
        uri=eff_uri,
        adapter=clean_adapter_id,
        format=spec_fmt,
        credentials_ref=req.credentials_ref,
        namespace=ns_ref,
        options=dict(req.options),
    )

    roots: list[Path] = []
    if workspace_root:
        roots.append(Path(workspace_root).resolve())
    if allowed_roots:
        roots.extend(Path(r).resolve() for r in allowed_roots)
    cas_base: Path | None = None
    if cas_store and hasattr(cas_store, "base_dir"):
        cas_base = Path(cas_store.base_dir).resolve()
        if cas_base not in roots:
            roots.append(cas_base)

    roots = [
        r for r in roots if (cas_base is not None and r == cas_base) or ".opendv" not in r.parts
    ]

    eff_allowed_roots: list[Path] | None = None
    if cas_hash and cas_base:
        eff_allowed_roots = [cas_base]
        if workspace_root:
            eff_allowed_roots.append(Path(workspace_root).resolve())
    elif roots:
        eff_allowed_roots = roots
    elif workspace_root is None and eff_uri and not eff_uri.startswith(
        ("cas://", "databricks://", "https://", "http://")
    ):
        cand_real = os.path.realpath(eff_uri)
        cand_norm = os.path.normcase(cand_real)
        safe_bases = (os.path.realpath(tempfile.gettempdir()), os.path.realpath(os.getcwd()))
        in_safe_base = any(
            cand_real.startswith(b if b.endswith(os.sep) else b + os.sep)
            or cand_norm.startswith(os.path.normcase(b).rstrip(os.sep) + os.sep)
            for b in safe_bases
        )
        if in_safe_base and os.path.isfile(cand_real):
            eff_allowed_roots = [Path(cand_real).parent]

    if eff_allowed_roots:
        eff_allowed_roots = [
            r
            for r in eff_allowed_roots
            if (cas_base is not None and r == cas_base) or ".opendv" not in r.parts
        ]

    if eff_uri and not eff_uri.startswith(("cas://", "databricks://", "https://", "http://")):
        cand_real = os.path.realpath(eff_uri)
        cand_parts = Path(cand_real).parts
        in_cas = cas_base is not None and Path(cand_real).is_relative_to(cas_base)
        if ".opendv" in cand_parts and not in_cas:
            raise PathContainmentError(
                f"Path '{cand_real}' inside '.opendv' is not an allowed inspect root."
            )

    server_allowed_hosts = allowed_hosts
    if server_allowed_hosts is None:
        env_hosts = os.environ.get("OPENDV_ALLOWED_HOSTS") or os.environ.get(
            "OPENDV_ALLOWED_DATABRICKS_HOSTS"
        )
        if env_hosts:
            server_allowed_hosts = [
                h.strip().lower() for h in env_hosts.split(",") if h.strip()
            ]

    # SSRF Protection (R3-C4): When allowed_hosts is unset/empty, reject remote options.host.
    req_host = spec.options.get("host") or req.options.get("host")
    if req_host and isinstance(req_host, str) and req_host.strip():
        cleaned_host = req_host.strip()
        if not server_allowed_hosts:
            raise ValueError(
                f"Remote host '{cleaned_host}' rejected: server has no configured allowed_hosts. "
                "Configure allowed_hosts or OPENDV_ALLOWED_HOSTS (SSRF protection)."
            )
        norm_req = cleaned_host.split("://")[-1].rstrip("/").strip().lower()
        norm_allowed = {
            h.split("://")[-1].rstrip("/").strip().lower() for h in server_allowed_hosts
        }
        if norm_req not in norm_allowed:
            raise ValueError(
                f"Remote host '{cleaned_host}' is not in allowed hosts: {sorted(norm_allowed)}."
            )

    if eff_uri and eff_uri.startswith(("https://", "http://")):
        parsed_uri = urlparse(eff_uri)
        uri_host = parsed_uri.hostname
        if uri_host:
            if not server_allowed_hosts:
                raise ValueError(
                    f"Remote host '{uri_host}' rejected: server has no configured allowed_hosts. "
                    "Configure allowed_hosts or OPENDV_ALLOWED_HOSTS (SSRF protection)."
                )
            norm_uri_host = uri_host.strip().lower()
            norm_allowed = {
                h.split("://")[-1].rstrip("/").strip().lower() for h in server_allowed_hosts
            }
            if norm_uri_host not in norm_allowed:
                raise ValueError(
                    f"Remote host '{uri_host}' is not in allowed hosts: {sorted(norm_allowed)}."
                )

    adapter: Any
    if clean_adapter_id in {"sqlite", "sqlite3"}:
        adapter = SqliteCatalogInspector(
            workspace_root=workspace_root,
            allowed_roots=eff_allowed_roots,
            allow_temp=False,
        )
    elif clean_adapter_id in {"databricks", "uc"}:
        adapter = DatabricksUnityCatalogAdapter(allowed_hosts=server_allowed_hosts)
        # Eagerly validate connection config and credentials (R3-M7)
        if hasattr(adapter, "_resolve_credentials"):
            adapter._resolve_credentials(spec)
        if (
            "emulator" not in spec.options
            and "connection" not in spec.options
            and "connection_factory" not in spec.options
            and not (
                (spec.options.get("host") or os.environ.get("DATABRICKS_HOST"))
                and (spec.options.get("http_path") or os.environ.get("DATABRICKS_HTTP_PATH"))
            )
        ):
            raise ValueError(
                "Databricks connection requires 'host' and 'http_path' or "
                "injected connection/emulator."
            )
    elif clean_adapter_id in {"duckdb", "duckdb_attach"}:
        adapter = DuckdbAttachSourceAdapter(
            workspace_root=workspace_root,
            allowed_roots=eff_allowed_roots,
            allow_temp=False,
        )
    else:
        from opendv_sources.adapters.filesystem import LocalFilesystemSourceAdapter

        adapter = LocalFilesystemSourceAdapter(
            workspace_root=workspace_root,
            allowed_roots=eff_allowed_roots,
            allow_temp=False,
        )

    # 5. Discover namespaces
    namespaces_list: list[dict[str, Any]] = []
    if hasattr(adapter, "list_namespaces"):
        try:
            ns_descriptors = adapter.list_namespaces(spec)
            namespaces_list = [
                ns.model_dump(mode="json") if hasattr(ns, "model_dump") else dict(ns)
                for ns in ns_descriptors
            ]
        except (PathContainmentError, ValueError, SourceError):
            raise
        except (RuntimeError, OSError, AttributeError):
            namespaces_list = []

    # 6. Discover and inspect tables
    tables_list: list[dict[str, Any]] = []
    table_summaries: list[Any] = []
    if hasattr(adapter, "list_tables"):
        try:
            table_summaries = adapter.list_tables(spec, namespace=ns_ref)
        except (PathContainmentError, ValueError, SourceError):
            raise
        except (RuntimeError, OSError, AttributeError):
            table_summaries = []

    # Apply table selection filtering if provided
    raw_selection = req.options.get("table_selection")
    selection: TableSelectionDTO | None = None
    if isinstance(raw_selection, TableSelectionDTO):
        selection = raw_selection
    elif isinstance(raw_selection, dict):
        selection = TableSelectionDTO.model_validate(raw_selection)

    if selection is not None and table_summaries:
        if selection.mode == "EXPLICIT" and selection.tables:
            allowed = set(selection.tables)
            table_summaries = [t for t in table_summaries if t.table_name in allowed]
        elif selection.mode == "PATTERN":
            try:
                if selection.include_pattern:
                    pat_inc = re.compile(selection.include_pattern)
                    table_summaries = [t for t in table_summaries if pat_inc.search(t.table_name)]
                if selection.exclude_pattern:
                    pat_exc = re.compile(selection.exclude_pattern)
                    table_summaries = [
                        t for t in table_summaries if not pat_exc.search(t.table_name)
                    ]
            except re.error as err:
                raise ValueError(f"Invalid regex pattern in table_selection: {err}") from err

    for t_sum in table_summaries:
        t_ref = TableRef(table_name=t_sum.table_name, namespace=t_sum.namespace)
        try:
            schema_desc = adapter.inspect_table(spec, t_ref)
            tables_list.append(schema_desc.model_dump(mode="json"))
        except (PathContainmentError, ValueError, SourceError):
            raise
        except (RuntimeError, OSError, AttributeError):
            tables_list.append(
                t_sum.model_dump(mode="json") if hasattr(t_sum, "model_dump") else dict(t_sum)
            )

    # Fallback for single table specified in options when table_summaries is empty
    single_table_name = req.options.get("table") or req.options.get("table_name")
    if not tables_list and single_table_name:
        single_ref = TableRef(table_name=str(single_table_name), namespace=ns_ref)
        try:
            schema_desc = adapter.inspect_table(spec, single_ref)
            tables_list.append(schema_desc.model_dump(mode="json"))
        except (PathContainmentError, ValueError, SourceError):
            raise
        except (RuntimeError, OSError, AttributeError):
            pass

    # Fallback for tabular adapters supporting describe() (e.g. LocalFilesystemSourceAdapter)
    if not tables_list and hasattr(adapter, "describe"):
        try:
            desc = adapter.describe(spec)
            t_name = single_table_name
            if not t_name:
                cand_name = Path(eff_uri).stem if eff_uri else "data"
                t_name = "data" if _HEX64_RE.match(cand_name) else cand_name
            from opendv_sources.models.descriptors import TableSchemaDescriptor

            schema_desc = TableSchemaDescriptor(
                table_name=str(t_name),
                namespace=ns_ref,
                columns=desc.columns,
                primary_key_columns=[c.name for c in desc.columns if c.is_primary_key],
                foreign_keys=[],
                row_count_estimate=desc.row_count_estimate,
                fingerprint=desc.fingerprint,
                governance_tags={},
            )
            tables_list.append(schema_desc.model_dump(mode="json"))
        except (PathContainmentError, ValueError, SourceError):
            raise
        except (RuntimeError, OSError, AttributeError):
            pass

    # 7. Extract capabilities
    caps_dict: dict[str, Any] = {}
    if hasattr(adapter, "capabilities"):
        try:
            caps = adapter.capabilities(spec)
            caps_dict = caps.model_dump(mode="json") if hasattr(caps, "model_dump") else dict(caps)
        except (PathContainmentError, ValueError, SourceError):
            raise
        except (RuntimeError, OSError, AttributeError):
            caps_dict = {}

    # 8. Compute deterministic fingerprint
    fp_hasher = hashlib.sha256()
    fp_hasher.update(clean_adapter_id.encode())
    for t_dict in sorted(tables_list, key=lambda x: str(x.get("table_name", ""))):
        fp_token = f":{t_dict.get('table_name')}:{t_dict.get('fingerprint', '')}"
        fp_hasher.update(fp_token.encode())
    fingerprint = fp_hasher.hexdigest()

    now_utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return SourceInspectResponseDTO(
        adapter_id=clean_adapter_id,
        namespaces=namespaces_list,
        tables=tables_list,
        capabilities=caps_dict,
        fingerprint=fingerprint,
        inspected_at_utc=now_utc,
    )


__all__ = [
    "inspect_source",
    "list_adapter_manifests",
    "list_adapters",
]
