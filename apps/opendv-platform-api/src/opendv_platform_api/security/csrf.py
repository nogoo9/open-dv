"""CSRF origin and header validation for Open-DV Workbench (Task 5B.4 / Issue #218)."""

from __future__ import annotations

import os
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


def parse_http_origin(origin_or_url: str) -> tuple[str, str, int] | None:
    raw = origin_or_url.strip()
    if not raw or raw.lower() == "null":
        return None
    try:
        parsed = urlparse(raw)
        scheme = parsed.scheme.lower()
        if scheme not in ("http", "https"):
            return None
        if not parsed.netloc or parsed.username is not None or parsed.password is not None:
            return None
        hostname = _normalize_hostname(parsed.hostname)
        if not hostname:
            return None
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, hostname, port


def parse_http_host(
    host_header: str, default_scheme: str = "http"
) -> tuple[str, int] | None:
    raw = host_header.strip()
    if not raw:
        return None
    try:
        parsed = urlparse(f"//{raw}")
        if (
            not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            return None
        hostname = _normalize_hostname(parsed.hostname)
        if not hostname:
            return None
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if default_scheme.lower() == "https" else 80
    return hostname, port


def origin_matches_host(origin: str, host: str) -> bool:
    """Return True iff HTTP(S) Origin matches Host after normalizing case and default ports."""
    origin_parts = parse_http_origin(origin)
    if origin_parts is None:
        return False
    origin_scheme, origin_host, origin_port = origin_parts
    host_parts = parse_http_host(host, default_scheme=origin_scheme)
    if host_parts is None:
        return False
    host_host, host_port = host_parts
    return origin_host == host_host and origin_port == host_port


def verify_csrf(request: Request) -> None:
    """Verify CSRF protection on state-mutating requests.

    - Unconditionally rejects `Sec-Fetch-Site: cross-site` and `Origin: null` on
      mutating requests regardless of credential mechanism (`CRITICAL-03`).
    - Rejects cross-origin or port-mismatched `Origin` targeting a loopback `Host`.
    - Validates `Origin` (or `Referer` when `Origin` is absent) against `Host` on
      mutating requests (exempting `remote_platform` Bearer JWT requests).
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
        origin_parts = parse_http_origin(origin)
        host_parts = parse_http_host(
            host, default_scheme=origin_parts[0] if origin_parts else scheme
        )
        if host_parts is not None and _is_loopback_host(host_parts[0]):
            if origin_parts is None:
                raise StudioAPIError(
                    "CSRF_ORIGIN_MISMATCH",
                    f"Cross-origin request to loopback service rejected: "
                    f"Origin '{origin}' does not match loopback Host '{host}'.",
                    status_code=403,
                    instance=str(request.url.path),
                )
            _, origin_host, origin_port = origin_parts
            _, host_port = host_parts
            if not _is_loopback_host(origin_host) or origin_port != host_port:
                raise StudioAPIError(
                    "CSRF_ORIGIN_MISMATCH",
                    f"Cross-origin request to loopback service rejected: "
                    f"Origin '{origin}' does not match loopback Host '{host}'.",
                    status_code=403,
                    instance=str(request.url.path),
                )

    # Check Origin (or Referer when Origin is absent) on all mutating requests;
    # only exempt Bearer auth in remote_platform where JWTs are verified (never loopback no-op).
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.strip().lower().startswith("bearer "):
        mode_raw = (os.environ.get("OPENDV_SERVER_MODE") or "").strip().lower()
        if not mode_raw:
            app_state = getattr(getattr(request, "app", None), "state", None)
            settings = getattr(app_state, "settings", None) if app_state is not None else None
            mode_raw = str(getattr(settings, "server_mode", "") or "").strip().lower()
        if mode_raw == "remote_platform":
            return

    origin_or_ref = origin or request.headers.get("referer")
    if origin_or_ref:
        if not host:
            raise StudioAPIError(
                "CSRF_ORIGIN_MISMATCH",
                "Missing Host header in state-mutating request.",
                status_code=403,
                instance=str(request.url.path),
            )
        if origin_or_ref.strip().lower() == "null":
            raise StudioAPIError(
                "CSRF_ORIGIN_MISMATCH",
                "Disallowed origin 'null' in state-mutating request.",
                status_code=403,
                instance=str(request.url.path),
            )

        if not origin_matches_host(origin_or_ref, host):
            origin_parts = parse_http_origin(origin_or_ref)
            host_parts = parse_http_host(
                host, default_scheme=origin_parts[0] if origin_parts else scheme
            )
            is_loopback_equiv = (
                origin_parts is not None
                and host_parts is not None
                and _is_loopback_host(host_parts[0])
                and _is_loopback_host(origin_parts[1])
                and host_parts[1] == origin_parts[2]
            )
            if not is_loopback_equiv:
                raise StudioAPIError(
                    "CSRF_ORIGIN_MISMATCH",
                    f"Cross-origin request rejected: Origin '{origin_or_ref}' "
                    f"does not match Host '{host}'.",
                    status_code=403,
                    instance=str(request.url.path),
                )


__all__ = [
    "is_loopback_host",
    "origin_matches_host",
    "parse_http_host",
    "parse_http_origin",
    "verify_csrf",
]
