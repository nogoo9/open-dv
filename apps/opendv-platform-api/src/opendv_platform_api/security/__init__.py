"""Security module for Open-DV Platform API."""

from __future__ import annotations

from opendv_platform_api.security.csrf import verify_csrf
from opendv_platform_api.security.error_catalog import (
    StudioAPIError,
    build_studio_problem_response,
    get_error_catalog,
    get_error_entry,
)
from opendv_platform_api.security.studio_auth import (
    SERVER_MODE_LOCAL,
    SERVER_MODE_REMOTE,
    StudioPrincipal,
    authenticate_studio_request,
    exchange_launch_nonce,
    get_active_session_role,
    get_local_token_path,
    get_server_mode,
    initialize_local_token,
    is_loopback_client,
    mint_launch_nonce,
    resolve_local_token,
)

__all__ = [
    "SERVER_MODE_LOCAL",
    "SERVER_MODE_REMOTE",
    "StudioAPIError",
    "StudioPrincipal",
    "authenticate_studio_request",
    "build_studio_problem_response",
    "exchange_launch_nonce",
    "get_active_session_role",
    "get_error_catalog",
    "get_error_entry",
    "get_local_token_path",
    "get_server_mode",
    "initialize_local_token",
    "is_loopback_client",
    "mint_launch_nonce",
    "resolve_local_token",
    "verify_csrf",
]
