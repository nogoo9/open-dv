"""Static V2 source adapters, extension mapping, and Databricks SSRF guards."""

from __future__ import annotations

import contextlib
import ipaddress
import re
import socket
from collections.abc import Mapping, Sequence
from importlib import import_module
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlparse

import httpx

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.sources import (
    ConnectorAdapterSpec,
    ConnectorFieldSpec,
    SourceKind,
)
from opendv.sources._extractors import (
    RawColumnData,
    RawTableData,
    dispatch_structured_extract,
)

SQL_IDENTIFIER_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FORBIDDEN_SECRET_KEY_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:"
    r"(?i:password|passwd|secret|token|api_?key|access_?key|private_?key|credentials?)"
    r"|(?:(?<![A-Za-z0-9])|(?<=[a-z0-9])(?=[A-Z]))(?i:pat|auth|pwd)(?:(?![A-Za-z0-9])|(?=[A-Z]))"
    r")"
)

BLOCKED_SSRF_CIDRS: Final[tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::/128"),
)

UPLOAD_EXT_TO_KIND: Final[dict[str, SourceKind]] = {
    ".sqlite": "sqlite",
    ".db": "sqlite",
    ".sqlite3": "sqlite",
    ".duckdb": "duckdb",
    ".ddb": "duckdb",
    ".parquet": "parquet",
    ".pq": "parquet",
    ".csv": "csv",
    ".xlsx": "xlsx",
    ".xlsm": "xlsx",
    ".xls": "xlsx",
    ".pdf": "pdf",
    ".md": "markdown",
    ".markdown": "markdown",
    ".docx": "docx",
    ".txt": "domain_note",
}

SOURCE_ADAPTERS: Final[tuple[ConnectorAdapterSpec, ...]] = (
    ConnectorAdapterSpec(
        adapter_id="sqlite",
        display_name="SQLite Database",
        fields=[
            ConnectorFieldSpec(
                key="uri_or_path",
                label="Database File Path",
                input_kind="path",
                required=True,
                secret_ref=False,
            ),
            ConnectorFieldSpec(
                key="selected_tables",
                label="Selected Tables",
                input_kind="text",
                required=False,
                secret_ref=False,
            ),
        ],
    ),
    ConnectorAdapterSpec(
        adapter_id="duckdb",
        display_name="DuckDB Database",
        fields=[
            ConnectorFieldSpec(
                key="uri_or_path",
                label="DuckDB File Path",
                input_kind="path",
                required=True,
                secret_ref=False,
            ),
            ConnectorFieldSpec(
                key="selected_tables",
                label="Selected Tables",
                input_kind="text",
                required=False,
                secret_ref=False,
            ),
        ],
    ),
    ConnectorAdapterSpec(
        adapter_id="parquet",
        display_name="Apache Parquet File",
        fields=[
            ConnectorFieldSpec(
                key="uri_or_path",
                label="Parquet File Path",
                input_kind="path",
                required=True,
                secret_ref=False,
            ),
        ],
    ),
    ConnectorAdapterSpec(
        adapter_id="csv",
        display_name="CSV Table",
        fields=[
            ConnectorFieldSpec(
                key="uri_or_path",
                label="CSV File Path",
                input_kind="path",
                required=True,
                secret_ref=False,
            ),
        ],
    ),
    ConnectorAdapterSpec(
        adapter_id="xlsx",
        display_name="Excel Workbook (XLSX)",
        fields=[
            ConnectorFieldSpec(
                key="uri_or_path",
                label="Workbook Path",
                input_kind="path",
                required=True,
                secret_ref=False,
            ),
            ConnectorFieldSpec(
                key="selected_tables",
                label="Selected Worksheets",
                input_kind="text",
                required=False,
                secret_ref=False,
            ),
        ],
    ),
    ConnectorAdapterSpec(
        adapter_id="databricks_uc",
        display_name="Databricks Unity Catalog",
        fields=[
            ConnectorFieldSpec(
                key="host",
                label="Workspace Host (HTTPS)",
                input_kind="text",
                required=True,
                secret_ref=False,
            ),
            ConnectorFieldSpec(
                key="http_path",
                label="SQL Warehouse HTTP Path",
                input_kind="text",
                required=True,
                secret_ref=False,
            ),
            ConnectorFieldSpec(
                key="catalog",
                label="Unity Catalog Name",
                input_kind="text",
                required=True,
                secret_ref=False,
            ),
            ConnectorFieldSpec(
                key="schema",
                label="Schema Name",
                input_kind="text",
                required=True,
                secret_ref=False,
            ),
            ConnectorFieldSpec(
                key="DATABRICKS_TOKEN",
                label="Databricks Personal Access Token",
                input_kind="secret",
                required=True,
                secret_ref=True,
            ),
            ConnectorFieldSpec(
                key="selected_tables",
                label="Selected Tables",
                input_kind="text",
                required=False,
                secret_ref=False,
            ),
        ],
    ),
)

_ADAPTER_BY_ID: Final[dict[str, ConnectorAdapterSpec]] = {
    spec.adapter_id: spec for spec in SOURCE_ADAPTERS
}


def get_source_adapter(adapter_id: str) -> ConnectorAdapterSpec:
    """Return a static ConnectorAdapterSpec by adapter_id."""
    spec = _ADAPTER_BY_ID.get(adapter_id)
    if spec is None:
        raise OpenDVError(
            f"Unknown source adapter '{adapter_id}'",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )
    return spec


def list_source_adapters() -> list[ConnectorAdapterSpec]:
    """Return all 6 static V2 structured source adapter specs."""
    return list(SOURCE_ADAPTERS)


def infer_kind_from_filename(filename: str) -> SourceKind:
    """Infer V2 SourceKind from filename extension."""
    ext = Path(filename).suffix.lower()
    kind = UPLOAD_EXT_TO_KIND.get(ext)
    if kind is None:
        raise OpenDVError(
            f"Unsupported source file extension '{ext}' for '{filename}'",
            error_code=ErrorCode.UNSUPPORTED_MEDIA_TYPE,
        )
    return kind


def validate_sql_identifier(name: str) -> str:
    """Enforce strict SQL identifier validation (`^[A-Za-z_][A-Za-z0-9_]*$`)."""
    clean = name.strip()
    if not SQL_IDENTIFIER_RE.fullmatch(clean):
        raise OpenDVError(
            f"Invalid SQL identifier '{name}': must match ^[A-Za-z_][A-Za-z0-9_]*$",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    return clean


def _is_blocked_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if (
        isinstance(ip_obj, ipaddress.IPv6Address)
        and ip_obj.ipv4_mapped is not None
        and _is_blocked_ip(ip_obj.ipv4_mapped)
    ):
        return True
    if any(ip_obj in net for net in BLOCKED_SSRF_CIDRS):
        return True
    return bool(
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    )


def validate_databricks_host(host_or_url: str, *, resolve_dns: bool = True) -> str:
    """Validate Databricks SQL Warehouse URL/host against HTTPS-only and SSRF CIDRs."""
    raw = host_or_url.strip()
    if (
        not raw
        or "\\" in raw
        or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in raw)
    ):
        raise OpenDVError(
            f"Databricks host '{raw}' blocked by SSRF guard",
            error_code=ErrorCode.DATABRICKS_SSRF_BLOCKED,
        )

    if "://" in raw:
        parsed = urlparse(raw)
        if parsed.scheme.lower() != "https":
            raise OpenDVError(
                f"Databricks connection must use HTTPS; rejected scheme '{parsed.scheme}'",
                error_code=ErrorCode.DATABRICKS_SSRF_BLOCKED,
            )
        if (
            parsed.username is not None
            or parsed.password is not None
            or "@" in parsed.netloc
            or "%" in parsed.netloc
        ):
            raise OpenDVError(
                f"Databricks URL '{raw}' cannot contain userinfo/authority '@' or '%'",
                error_code=ErrorCode.DATABRICKS_SSRF_BLOCKED,
            )
        try:
            parsed_port = parsed.port
        except ValueError as err:
            raise OpenDVError(
                f"Databricks URL '{raw}' has invalid port",
                error_code=ErrorCode.DATABRICKS_SSRF_BLOCKED,
            ) from err
        if parsed_port is not None and not (1 <= parsed_port <= 65535):
            raise OpenDVError(
                f"Databricks URL '{raw}' has invalid port {parsed_port}",
                error_code=ErrorCode.DATABRICKS_SSRF_BLOCKED,
            )
        hostname = (parsed.hostname or "").strip()
    else:
        if any(ch in raw for ch in ("?", "#", "@")):
            raise OpenDVError(
                f"Databricks host '{raw}' blocked by SSRF guard",
                error_code=ErrorCode.DATABRICKS_SSRF_BLOCKED,
            )
        host_part = raw.split("/", 1)[0].strip()
        if "%" in host_part:
            raise OpenDVError(
                f"Databricks host '{raw}' blocked by SSRF guard",
                error_code=ErrorCode.DATABRICKS_SSRF_BLOCKED,
            )
        if host_part.startswith("["):
            if "]" not in host_part:
                raise OpenDVError(
                    f"Databricks host '{raw}' blocked by SSRF guard",
                    error_code=ErrorCode.DATABRICKS_SSRF_BLOCKED,
                )
            end_idx = host_part.index("]")
            hostname = host_part[1:end_idx]
            remainder = host_part[end_idx + 1 :]
            if remainder and (
                not remainder.startswith(":")
                or not remainder[1:].isdigit()
                or not (1 <= int(remainder[1:]) <= 65535)
            ):
                raise OpenDVError(
                    f"Databricks host '{raw}' blocked by SSRF guard",
                    error_code=ErrorCode.DATABRICKS_SSRF_BLOCKED,
                )
        elif ":" in host_part:
            if host_part.count(":") > 1:
                raise OpenDVError(
                    f"Databricks host '{raw}' blocked by SSRF guard",
                    error_code=ErrorCode.DATABRICKS_SSRF_BLOCKED,
                )
            hostname, port_str = host_part.split(":", 1)
            if not port_str.isdigit() or not (1 <= int(port_str) <= 65535):
                raise OpenDVError(
                    f"Databricks host '{raw}' blocked by SSRF guard",
                    error_code=ErrorCode.DATABRICKS_SSRF_BLOCKED,
                )
        else:
            hostname = host_part

    bare_host = hostname.lower().rstrip(".")
    if (
        not bare_host
        or bare_host == "localhost"
        or bare_host.endswith(".localhost")
        or bare_host == "localhost.localdomain"
        or bare_host.endswith(".localhost.localdomain")
    ):
        raise OpenDVError(
            f"Databricks host '{hostname}' blocked by SSRF guard",
            error_code=ErrorCode.DATABRICKS_SSRF_BLOCKED,
        )

    try:
        ip_literal: ipaddress.IPv4Address | ipaddress.IPv6Address | None = ipaddress.ip_address(
            bare_host
        )
    except ValueError:
        ip_literal = None

    if ip_literal is None:
        noncanonical_ip: ipaddress.IPv4Address | None = None
        with contextlib.suppress(OSError):
            noncanonical_ip = ipaddress.IPv4Address(socket.inet_aton(bare_host))
        if noncanonical_ip is not None:
            raise OpenDVError(
                f"Databricks non-canonical IP '{hostname}' blocked by SSRF guard",
                error_code=ErrorCode.DATABRICKS_SSRF_BLOCKED,
            )

    if ip_literal is not None:
        if _is_blocked_ip(ip_literal):
            raise OpenDVError(
                f"Databricks IP '{hostname}' falls in blocked SSRF CIDR",
                error_code=ErrorCode.DATABRICKS_SSRF_BLOCKED,
            )
        return hostname

    if resolve_dns:
        try:
            infos = socket.getaddrinfo(hostname, None)
        except OSError:
            infos = []
        for item in infos:
            sockaddr = item[4] if len(item) > 4 else ()
            if not sockaddr:
                continue
            addr_text = str(sockaddr[0]).split("%", 1)[0]
            try:
                resolved_ip = ipaddress.ip_address(addr_text)
            except ValueError:
                continue
            if _is_blocked_ip(resolved_ip):
                raise OpenDVError(
                    f"Databricks host '{hostname}' resolved to blocked SSRF IP '{addr_text}'",
                    error_code=ErrorCode.DATABRICKS_SSRF_BLOCKED,
                )
    return hostname


def probe_databricks_https_endpoint(
    url: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Response:
    """Issue a redirect-disabled HTTPS check, blocking any 3xx redirect via SSRF guard."""
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() != "https":
        raise OpenDVError(
            f"Databricks endpoint requires HTTPS; got '{parsed.scheme}'",
            error_code=ErrorCode.DATABRICKS_SSRF_BLOCKED,
        )
    validate_databricks_host(url)
    with httpx.Client(follow_redirects=False, timeout=10.0, transport=transport) as client:
        resp = client.get(url)
    if 300 <= resp.status_code < 400:
        raise OpenDVError(
            f"Databricks endpoint attempted HTTP redirect ({resp.status_code}), blocked",
            error_code=ErrorCode.DATABRICKS_SSRF_BLOCKED,
        )
    return resp


def validate_databricks_options(
    options: Mapping[str, Any],
    secret_refs: Sequence[str],
    *,
    require_secret_ref: bool = True,
) -> None:
    """Reject plaintext secrets in options and require secret_refs for Databricks."""
    for key in options:
        if _FORBIDDEN_SECRET_KEY_RE.search(key):
            raise OpenDVError(
                f"Plaintext secret key '{key}' is forbidden in connector options; use secret_refs",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
    if require_secret_ref and not secret_refs:
        raise OpenDVError(
            "Databricks Unity Catalog connector requires a configured secret_ref",
            error_code=ErrorCode.NEEDS_LOCAL_SECRET_BINDING,
        )


def open_databricks_connection(
    *,
    host: str,
    http_path: str,
    access_token: str | None = None,
    secret_refs: Sequence[str] = (),
    options: Mapping[str, Any] | None = None,
) -> Any:
    """Validate host + secrets and open a databricks.sql connection (DRIVER_MISSING if absent)."""
    opts = dict(options) if options else {}
    validate_databricks_options(opts, secret_refs, require_secret_ref=access_token is None)
    safe_host = validate_databricks_host(host)
    if not http_path.strip():
        raise OpenDVError(
            "Databricks 'http_path' cannot be empty",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    try:
        db_sql = import_module("databricks.sql")
    except ImportError as err:
        raise OpenDVError(
            "Optional 'databricks' extra (databricks-sql-connector) is not installed",
            error_code=ErrorCode.DRIVER_MISSING,
        ) from err
    return db_sql.connect(
        server_hostname=safe_host,
        http_path=http_path.strip(),
        access_token=access_token,
    )


def extract_structured_source(
    kind: SourceKind,
    uri_or_path: str,
    *,
    options: Mapping[str, Any] | None = None,
    secret_refs: Sequence[str] = (),
    selected_tables: Sequence[str] | None = None,
    sample_limit: int = 5,
    preview_offset: int = 0,
    preview_limit: int = 50,
) -> list[RawTableData]:
    """Dispatch extraction across the 6 static V2 structured source kinds."""
    return dispatch_structured_extract(
        kind,
        uri_or_path,
        options=options,
        secret_refs=secret_refs,
        selected_tables=selected_tables,
        sample_limit=sample_limit,
        preview_offset=preview_offset,
        preview_limit=preview_limit,
        validate_ident_fn=validate_sql_identifier,
        open_conn_fn=open_databricks_connection,
    )


__all__ = [
    "BLOCKED_SSRF_CIDRS",
    "SOURCE_ADAPTERS",
    "SQL_IDENTIFIER_RE",
    "UPLOAD_EXT_TO_KIND",
    "RawColumnData",
    "RawTableData",
    "extract_structured_source",
    "get_source_adapter",
    "infer_kind_from_filename",
    "list_source_adapters",
    "open_databricks_connection",
    "probe_databricks_https_endpoint",
    "validate_databricks_host",
    "validate_databricks_options",
    "validate_sql_identifier",
]
