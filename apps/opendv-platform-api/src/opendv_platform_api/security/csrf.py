"""CSRF origin and header validation for Open-DV Workbench (Task 5B.4 / Issue #218)."""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import Request

from opendv_platform_api.security.error_catalog import StudioAPIError
from opendv_service.settings import (
    is_loopback_hostname as _is_loopback_host,
)


def _normalize_hostname(raw: str | None) -> str:
    if not raw:
        return ""
    cleaned = raw.strip().lower()
    if cleaned.startswith("[") and cleaned.endswith("]"):
        cleaned = cleaned[1:-1]
    return cleaned


is_loopback_host = _is_loopback_host


def _parse_host_and_port(host_header: str, default_scheme: str = "http") -> tuple[str, int]:
    parsed = urlparse(f"//{host_header.strip()}")
    hostname = _normalize_hostname(parsed.hostname)
    port = parsed.port or (443 if default_scheme == "https" else 80)
    return hostname, port


def verify_csrf(request: Request) -> None:
    """Verify CSRF protection on state-mutating requests.

    - Unconditionally rejects `Sec-Fetch-Site: cross-site` and `Origin: null` on
      mutating requests regardless of credential mechanism (`CRITICAL-03`).
    - Rejects cross-origin or port-mismatched `Origin` targeting a loopback `Host`.
    - Requests authenticating via session cookie MUST provide `X-OpenDV-Workbench: 1`
      and pass strict Origin/Referer matching.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return

    sec_fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if sec_fetch_site == "cross-site":
        raise StudioAPIError(
            "CSRF_ORIGIN_MISMATCH",
            "Cross-site request rejected via Sec-Fetch-Site Fetch Metadata guard.",
            status_code=403,
            instance=str(request.url.path),
        )

    origin = request.headers.get("origin")
    host = request.headers.get("host")
    scheme = request.url.scheme or "http"

    if origin is not None and origin.strip().lower() == "null":
        raise StudioAPIError(
            "CSRF_ORIGIN_MISMATCH",
            "Disallowed opaque Origin 'null' in state-mutating request.",
            status_code=403,
            instance=str(request.url.path),
        )

    # In loopback host mode, reject non-loopback or cross-port Origin against DNS rebinding
    if origin and host:
        parsed_origin = urlparse(origin.strip())
        origin_host = _normalize_hostname(parsed_origin.hostname)
        origin_port = parsed_origin.port or (443 if parsed_origin.scheme == "https" else 80)
        host_host, host_port = _parse_host_and_port(host, default_scheme=scheme)
        if parsed_origin.port is not None and urlparse(f"//{host.strip()}").port is None:
            server_tuple = request.scope.get("server")
            if (
                isinstance(server_tuple, (tuple, list))
                and len(server_tuple) >= 2
                and server_tuple[1]
            ):
                host_port = int(server_tuple[1])
        if _is_loopback_host(host_host) and (
            not _is_loopback_host(origin_host) or origin_port != host_port
        ):
            raise StudioAPIError(
                "CSRF_ORIGIN_MISMATCH",
                f"Cross-origin request to loopback service rejected: "
                f"Origin '{origin}' does not match loopback Host '{host}'.",
                status_code=403,
                instance=str(request.url.path),
            )

    # Check if request authenticates via cookie vs bearer header
    session_cookie = request.cookies.get("opendv_session")
    auth_header = request.headers.get("authorization")

    # If Bearer token is provided, bypass cookie-specific CSRF header verification for CLI parity
    if auth_header and auth_header.strip().lower().startswith("bearer "):
        return

    # /auth/token authenticates via single-use launch_nonce body capability (not existing cookie)
    is_nonce_exchange = request.url.path.rstrip("/").endswith("/ontology/studio/auth/token")

    # If authenticated via cookie, enforce strict CSRF header and Origin/Referer check
    if session_cookie and not is_nonce_exchange:
        header_val = request.headers.get("x-opendv-workbench")
        if header_val != "1":
            raise StudioAPIError(
                "CSRF_ORIGIN_MISMATCH",
                "State-mutating request authenticating via session cookie "
                "requires 'X-OpenDV-Workbench: 1' header.",
                status_code=403,
                instance=str(request.url.path),
            )

        origin_or_ref = request.headers.get("origin") or request.headers.get("referer")
        if origin_or_ref:
            if not host:
                raise StudioAPIError(
                    "CSRF_ORIGIN_MISMATCH",
                    "Missing Host header in cookie-authenticated request.",
                    status_code=403,
                    instance=str(request.url.path),
                )
            if origin_or_ref.strip().lower() == "null":
                raise StudioAPIError(
                    "CSRF_ORIGIN_MISMATCH",
                    "Disallowed origin 'null' in cookie-authenticated request.",
                    status_code=403,
                    instance=str(request.url.path),
                )

            parsed_origin = urlparse(origin_or_ref.strip())
            origin_netloc = parsed_origin.netloc
            if origin_netloc and origin_netloc != host:
                host_host, host_port = _parse_host_and_port(host, default_scheme=scheme)
                origin_host = _normalize_hostname(parsed_origin.hostname or origin_netloc)
                origin_port = parsed_origin.port or (443 if parsed_origin.scheme == "https" else 80)
                if not (
                    _is_loopback_host(host_host)
                    and _is_loopback_host(origin_host)
                    and host_port == origin_port
                ):
                    raise StudioAPIError(
                        "CSRF_ORIGIN_MISMATCH",
                        f"Cross-origin request rejected: Origin '{origin_or_ref}' "
                        f"does not match Host '{host}'.",
                        status_code=403,
                        instance=str(request.url.path),
                    )


__all__ = [
    "is_loopback_host",
    "verify_csrf",
]
