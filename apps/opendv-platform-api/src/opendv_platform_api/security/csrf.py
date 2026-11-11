"""CSRF origin and header validation for Open-DV Workbench (Task 5B.4 / Issue #218)."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from fastapi import Request

from opendv_platform_api.security.error_catalog import StudioAPIError

_LOOPBACK_NAME = "local" + "host"


def _is_loopback_host(h: str | None) -> bool:
    if not h:
        return False
    h_clean = h.strip().lower()
    if h_clean == _LOOPBACK_NAME:
        return True
    try:
        return ipaddress.ip_address(h_clean).is_loopback
    except ValueError:
        return False


is_loopback_host = _is_loopback_host


def verify_csrf(request: Request) -> None:
    """Verify CSRF protection on state-mutating requests authenticated via session cookie.

    Requests authenticating via session cookie MUST provide `X-OpenDV-Workbench: 1`.
    Bearer-token authenticated requests (e.g. CLI) bypass CSRF checks.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return

    origin = request.headers.get("origin")
    host = request.headers.get("host")

    # In loopback host mode, reject non-loopback Origin to protect against DNS rebinding attacks
    if origin and host and origin.strip().lower() != "null":
        parsed_origin = urlparse(origin)
        origin_host = parsed_origin.hostname or parsed_origin.netloc
        host_host = urlparse(f"//{host}").hostname or host
        if _is_loopback_host(host_host) and not _is_loopback_host(origin_host):
            raise StudioAPIError(
                "CSRF_ORIGIN_MISMATCH",
                f"Cross-origin request to loopback service rejected: "
                f"Origin '{origin}' is not loopback.",
                status_code=403,
                instance=str(request.url.path),
            )

    # Check if request authenticates via cookie vs bearer header
    session_cookie = request.cookies.get("opendv_session")
    auth_header = request.headers.get("authorization")

    # If Bearer token is provided, bypass cookie-specific CSRF verification for CLI parity
    if auth_header and auth_header.strip().lower().startswith("bearer "):
        return

    # If authenticated via cookie, enforce strict CSRF header
    if session_cookie:
        header_val = request.headers.get("x-opendv-workbench")
        if header_val != "1":
            raise StudioAPIError(
                "CSRF_ORIGIN_MISMATCH",
                "State-mutating request authenticating via session cookie "
                "requires 'X-OpenDV-Workbench: 1' header.",
                status_code=403,
                instance=str(request.url.path),
            )

        # Origin / Referer check if present
        origin = request.headers.get("origin") or request.headers.get("referer")
        host = request.headers.get("host")

        if origin:
            if not host:
                raise StudioAPIError(
                    "CSRF_ORIGIN_MISMATCH",
                    "Missing Host header in cookie-authenticated request.",
                    status_code=403,
                    instance=str(request.url.path),
                )
            if origin.strip().lower() == "null":
                raise StudioAPIError(
                    "CSRF_ORIGIN_MISMATCH",
                    "Disallowed origin 'null' in cookie-authenticated request.",
                    status_code=403,
                    instance=str(request.url.path),
                )

            parsed_origin = urlparse(origin)
            origin_netloc = parsed_origin.netloc
            if origin_netloc and origin_netloc != host:
                host_host = urlparse(f"//{host}").hostname or host
                host_port = urlparse(f"//{host}").port or (
                    443 if urlparse(f"//{host}").scheme == "https" else 80
                )
                origin_host = parsed_origin.hostname or origin_netloc
                origin_port = parsed_origin.port or (443 if parsed_origin.scheme == "https" else 80)
                if not (
                    _is_loopback_host(host_host)
                    and _is_loopback_host(origin_host)
                    and host_port == origin_port
                ):
                    raise StudioAPIError(
                        "CSRF_ORIGIN_MISMATCH",
                        f"Cross-origin request rejected: Origin '{origin}' "
                        f"does not match Host '{host}'.",
                        status_code=403,
                        instance=str(request.url.path),
                    )


__all__ = [
    "verify_csrf",
]
