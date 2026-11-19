"""Loopback open-access policy and identity synthesis for local_standalone mode."""

from __future__ import annotations

import ipaddress
import re
from typing import Final

from fastapi import Request

from opendv_platform_api.security.csrf import (
    is_loopback_host,
    parse_http_host,
    parse_http_origin,
)
from opendv_platform_api.security.error_catalog import StudioAPIError

_PRINCIPAL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(human|agent|system|user):[a-zA-Z0-9_.\-]+$"
)
_TEST_HOSTNAMES: Final[frozenset[str]] = frozenset({"testclient", "testserver"})


def normalize_studio_role(raw_role: str, *, allow_legacy_aliases: bool = True) -> str | None:
    """Normalize explicit studio or 5-tier RBAC role string into 4-tier studio role."""
    role = raw_role.lower().strip()
    if role == "admin" or (
        allow_legacy_aliases and role in ("data_steward", "restricted_pii_custodian")
    ):
        return "admin"
    if role == "editor":
        return "editor"
    if role == "automated" or (allow_legacy_aliases and role == "internal_modeler"):
        return "automated"
    if role == "viewer" or (allow_legacy_aliases and role in ("public", "auditor_finance")):
        return "viewer"
    return None


def resolve_expected_loopback_port(request: Request) -> int | None:
    """Determine expected listening port for loopback Host header verification."""
    import os

    env_port = os.environ.get("OPENDV_SERVER_PORT", "").strip()
    if env_port.isdigit():
        return int(env_port)

    server_tuple = request.scope.get("server")
    if isinstance(server_tuple, (tuple, list)) and len(server_tuple) >= 2 and server_tuple[1]:
        return int(server_tuple[1])

    app_state = getattr(getattr(request, "app", None), "state", None)
    cfg = getattr(app_state, "settings", None) if app_state is not None else None
    cfg_port = getattr(cfg, "server_port", None) if cfg is not None else None
    if cfg_port is not None:
        return int(cfg_port)
    return None


def is_remote_unauthenticated_enabled(request: Request) -> bool:
    """Return True if server was explicitly started with --allow-remote-unauthenticated."""
    app_state = getattr(getattr(request, "app", None), "state", None)
    cfg = getattr(app_state, "settings", None) if app_state is not None else None
    return bool(getattr(cfg, "remote_unauthenticated_mode", False))


def _check_fetch_and_origin_headers(request: Request) -> tuple[str | None, str | None]:
    sec_fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if sec_fetch_site == "cross-site":
        raise StudioAPIError(
            "CSRF_ORIGIN_MISMATCH",
            "Cross-site request rejected on loopback endpoint.",
            instance=str(request.url.path),
        )
    origin = request.headers.get("origin")
    if origin is not None and origin.strip().lower() == "null":
        raise StudioAPIError(
            "CSRF_ORIGIN_MISMATCH",
            "Opaque Origin 'null' rejected on loopback endpoint.",
            instance=str(request.url.path),
        )
    return origin, request.headers.get("host")


def is_loopback_request(request: Request, *, allow_testclient: bool = False) -> bool:
    """Verify caller connects from loopback (or TestClient) with valid Host/Origin."""
    origin, host = _check_fetch_and_origin_headers(request)
    if not request.client or not request.client.host:
        return False

    client_host = request.client.host
    is_test_client = allow_testclient and client_host == "testclient"
    if not is_test_client:
        try:
            if not ipaddress.ip_address(client_host).is_loopback:
                return False
        except (ValueError, AttributeError):
            return False

    if not host:
        return is_test_client

    scheme = request.url.scheme or "http"
    host_parts = parse_http_host(host, default_scheme=scheme)
    if host_parts is None:
        return False
    host_hostname, host_port = host_parts
    is_test_host = allow_testclient and host_hostname in _TEST_HOSTNAMES
    if not is_loopback_host(host_hostname) and not is_test_host:
        return False

    expected_port = resolve_expected_loopback_port(request)
    if not is_test_host and expected_port is not None and host_port != expected_port:
        return False

    if origin:
        origin_parts = parse_http_origin(origin)
        if origin_parts is None:
            raise StudioAPIError(
                "CSRF_ORIGIN_MISMATCH",
                f"Cross-origin request to loopback rejected: Origin '{origin}'.",
                instance=str(request.url.path),
            )
        _, origin_hostname, origin_port = origin_parts
        origin_is_test = allow_testclient and origin_hostname in _TEST_HOSTNAMES
        if not is_loopback_host(origin_hostname) and not origin_is_test:
            raise StudioAPIError(
                "CSRF_ORIGIN_MISMATCH",
                f"Cross-origin request to loopback rejected: Origin '{origin}'.",
                instance=str(request.url.path),
            )
        if not is_test_host and not origin_is_test and origin_port != host_port:
            raise StudioAPIError(
                "CSRF_ORIGIN_MISMATCH",
                f"Cross-port loopback Origin '{origin}' does not match Host '{host}'.",
                instance=str(request.url.path),
            )

    return True


def resolve_open_access_identity(request: Request) -> tuple[str, str]:
    """Resolve `(principal, role)` for open-access loopback requests, honoring explicit headers."""
    role_header = request.headers.get("x-opendv-role")
    normalized_role = normalize_studio_role(
        role_header or "admin", allow_legacy_aliases=True
    )
    if normalized_role is None:
        raise StudioAPIError(
            "SYNTAX_INVALID_ARGUMENT",
            f"Invalid X-OpenDV-Role '{role_header}'; "
            "permitted roles are admin, editor, viewer, automated, DATA_STEWARD, "
            "RESTRICTED_PII_CUSTODIAN, INTERNAL_MODELER, AUDITOR_FINANCE, PUBLIC.",
        )

    raw_principal = request.headers.get("X-OpenDV-Principal")
    if raw_principal:
        clean_principal = raw_principal.strip()
        if not _PRINCIPAL_PATTERN.match(clean_principal) or len(clean_principal) > 128:
            raise StudioAPIError(
                "SYNTAX_INVALID_ARGUMENT",
                f"Invalid X-OpenDV-Principal '{raw_principal}'; "
                "must match pattern ^(human|agent|system|user):[a-zA-Z0-9_.-]+$ "
                "and not exceed 128 characters.",
            )
        principal_str = clean_principal
    elif normalized_role == "automated":
        principal_str = "system:automated_runner"
    else:
        principal_str = "human:local_user"

    if principal_str.startswith("agent:"):
        normalized_role = "automated"

    return principal_str, normalized_role
