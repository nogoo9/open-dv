"""Studio authentication, server modes, launch nonce and token lifecycle (Task 5B.4 / Issue #218).

Conforms to docs/19 §8.2, docs/20 §3.1, ADR-016:
- Dual server modes: local_standalone vs remote_platform
- 4-tier RBAC: admin, editor, viewer, automated
- 60s launch nonce (single-use) exchanged for HttpOnly session cookie (local_standalone only)
- Bounded in-memory capacity (max 256 active nonces / 256 active cookie sessions)
- L1 lock (.local_token.lock) protected .local_token with permissions 0600
- RS256 JWT validation allowlist in remote mode via PyJWT
"""

from __future__ import annotations

import hmac
import ipaddress
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal
from urllib.parse import urlparse

import jwt
from fastapi import Request

from opendv_platform_api.security.csrf import is_loopback_host
from opendv_platform_api.security.error_catalog import StudioAPIError
from opendv_service.studio.locks import L1_LOCAL_TOKEN, studio_lock
from opendv_service.studio.workspace_security import (
    _ACTIVE_SESSIONS,
    _AUTH_LOCK,
    _LAUNCH_NONCES,
    MAX_ACTIVE_NONCES,
    MAX_ACTIVE_SESSIONS,
    _cleanup_expired_nonces_and_sessions,
    _evict_overflow_locked,
    mint_launch_nonce,
    set_owner_only_permissions,
)

SERVER_MODE_LOCAL: Final[str] = "local_standalone"
SERVER_MODE_REMOTE: Final[str] = "remote_platform"

Role = Literal["admin", "editor", "viewer", "automated"]

_PRINCIPAL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(human|agent|system|user):[a-zA-Z0-9_.\-]+$"
)


def get_jwt_verification_key() -> str:
    """Return PEM public key for remote-mode RS256 validation.

    Requires OPENDV_JWT_PUBLIC_KEY (PEM string) or OPENDV_JWT_PUBLIC_KEY_PATH (path to PEM file).
    Fails loudly if not configured or file not found.
    """
    env_key = os.environ.get("OPENDV_JWT_PUBLIC_KEY")
    if env_key and env_key.strip():
        return env_key.strip()
    key_path_str = os.environ.get("OPENDV_JWT_PUBLIC_KEY_PATH")
    if key_path_str:
        p = Path(key_path_str)
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
        raise StudioAPIError(
            "AUTHENTICATION_CONFIG_ERROR",
            f"Configured OPENDV_JWT_PUBLIC_KEY_PATH file does not exist: {key_path_str}",
        )
    raise StudioAPIError(
        "AUTHENTICATION_CONFIG_ERROR",
        "Remote platform mode requires OPENDV_JWT_PUBLIC_KEY or OPENDV_JWT_PUBLIC_KEY_PATH.",
    )


@dataclass(frozen=True)
class StudioPrincipal:
    """Authenticated caller identity and 4-tier RBAC role context."""

    principal: str
    role: str
    is_admin: bool
    auth_method: str

    @property
    def is_automated(self) -> bool:
        """Return True for `automated` role or `agent:*` principal identities."""
        return self.role == "automated" or self.principal.startswith("agent:")

    @property
    def is_agent_identity(self) -> bool:
        """Return True strictly when principal string carries an `agent:` prefix."""
        return self.principal.startswith("agent:")

    def can_perform(self, required_role: str) -> bool:
        if self.is_automated:
            return required_role == "viewer"
        if self.role == "admin":
            return True
        if self.role == "editor" and required_role in ("editor", "viewer"):
            return True
        return self.role == "viewer" and required_role == "viewer"


def get_server_mode(request: Request | None = None) -> str:
    """Resolve current studio server mode ('local_standalone' vs 'remote_platform')."""
    mode_raw = os.environ.get("OPENDV_SERVER_MODE")
    if mode_raw is not None and mode_raw.strip():
        mode = mode_raw.strip().lower()
        if mode in (SERVER_MODE_LOCAL, SERVER_MODE_REMOTE):
            return mode
        raise StudioAPIError(
            "AUTHENTICATION_CONFIG_ERROR",
            f"Invalid OPENDV_SERVER_MODE '{mode_raw}'. "
            f"Must be '{SERVER_MODE_LOCAL}' or '{SERVER_MODE_REMOTE}'.",
        )

    if request is not None:
        settings = getattr(request.app.state, "settings", None)
        if settings is not None:
            cfg_mode = getattr(settings, "server_mode", None)
            if cfg_mode:
                cfg_mode_str = str(cfg_mode).strip().lower()
                if cfg_mode_str in (SERVER_MODE_LOCAL, SERVER_MODE_REMOTE):
                    return cfg_mode_str
                raise StudioAPIError(
                    "AUTHENTICATION_CONFIG_ERROR",
                    f"Invalid settings.server_mode '{cfg_mode}'. "
                    f"Must be '{SERVER_MODE_LOCAL}' or '{SERVER_MODE_REMOTE}'.",
                )

    return SERVER_MODE_LOCAL


def _resolve_expected_loopback_port(request: Request) -> int | None:
    """Determine expected listening port for loopback Host header verification."""
    env_port = os.environ.get("OPENDV_SERVER_PORT", "").strip()
    if env_port.isdigit():
        return int(env_port)

    server_tuple = request.scope.get("server")
    if isinstance(server_tuple, (tuple, list)) and len(server_tuple) >= 2 and server_tuple[1]:
        return int(server_tuple[1])

    settings = getattr(getattr(request, "app", None), "state", None)
    cfg = getattr(settings, "settings", None) if settings is not None else None
    cfg_port = getattr(cfg, "server_port", None) if cfg is not None else None
    if cfg_port is not None:
        return int(cfg_port)
    return None


def is_loopback_client(request: Request) -> bool:
    """Verify caller connects from loopback and validate Host/Origin/Fetch-Metadata."""
    sec_fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if sec_fetch_site == "cross-site":
        raise StudioAPIError(
            "CSRF_ORIGIN_MISMATCH",
            "Cross-site request rejected on loopback bootstrap endpoint.",
            instance=str(request.url.path),
        )

    origin = request.headers.get("origin")
    if origin is not None and origin.strip().lower() == "null":
        raise StudioAPIError(
            "CSRF_ORIGIN_MISMATCH",
            "Opaque Origin 'null' rejected on loopback bootstrap endpoint.",
            instance=str(request.url.path),
        )

    if not request.client or not request.client.host:
        return False
    try:
        client_ip = ipaddress.ip_address(request.client.host)
        if not client_ip.is_loopback:
            return False
    except (ValueError, AttributeError):
        return False

    host = request.headers.get("host")
    if not host:
        return False
    parsed_host = urlparse(f"//{host.strip()}")
    host_hostname = parsed_host.hostname
    if not is_loopback_host(host_hostname):
        return False

    expected_port = _resolve_expected_loopback_port(request)
    if (
        parsed_host.port is not None
        and expected_port is not None
        and parsed_host.port != expected_port
    ):
        return False

    if origin:
        parsed_origin = urlparse(origin.strip())
        origin_hostname = parsed_origin.hostname
        if not is_loopback_host(origin_hostname):
            raise StudioAPIError(
                "CSRF_ORIGIN_MISMATCH",
                f"Cross-origin request to loopback bootstrap rejected: Origin '{origin}'.",
                instance=str(request.url.path),
            )
        scheme = request.url.scheme or "http"
        host_port = parsed_host.port or expected_port or (443 if scheme == "https" else 80)
        origin_port = parsed_origin.port or (443 if parsed_origin.scheme == "https" else 80)
        if origin_port != host_port:
            raise StudioAPIError(
                "CSRF_ORIGIN_MISMATCH",
                f"Cross-port loopback Origin '{origin}' does not match Host '{host}'.",
                instance=str(request.url.path),
            )

    return True


def get_local_token_path(studio_root: Path) -> Path:
    """Resolve absolute path to .local_token file."""
    override = os.environ.get("OPENDV_LOCAL_TOKEN_PATH")
    if override:
        return Path(override).resolve()
    return (studio_root / ".local_token").resolve()


def resolve_local_token(studio_root: Path) -> str | None:
    """Read local token using precedence: OPENDV_TOKEN -> .local_token."""
    env_token = os.environ.get("OPENDV_TOKEN")
    if env_token:
        return env_token.strip()

    token_path = get_local_token_path(studio_root)
    if token_path.is_file():
        try:
            return token_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None

    return None


def initialize_local_token(
    studio_root: Path,
    *,
    force: bool = False,
    caller_token: str | None = None,
    is_remote: bool = False,
) -> tuple[str, Path]:
    """Bootstrap or rotate .local_token under L1 lock hierarchy with atomic 0600 creation."""
    if is_remote:
        raise StudioAPIError(
            "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE",
            "Studio initialization (/init) is forbidden in remote_platform mode.",
        )

    if os.environ.get("OPENDV_TOKEN"):
        raise StudioAPIError(
            "STATIC_TOKEN_CONFIGURED",
            "Token is statically configured via OPENDV_TOKEN environment variable "
            "and cannot be initialized or rotated.",
        )

    token_path = get_local_token_path(studio_root)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = token_path.with_name(f"{token_path.name}.lock")

    with studio_lock(lock_path, level=L1_LOCAL_TOKEN):
        existing_token = resolve_local_token(studio_root)

        if existing_token and not force:
            raise StudioAPIError(
                "STUDIO_ALREADY_INITIALIZED",
                "Studio has already been initialized. Pass force=true to rotate the token.",
            )

        if (
            force
            and existing_token
            and (
                not caller_token
                or not hmac.compare_digest(
                    caller_token.strip().encode("utf-8"), existing_token.encode("utf-8")
                )
            )
        ):
            raise StudioAPIError(
                "UNAUTHENTICATED",
                "Rotating an initialized token requires valid existing token "
                "in Authorization header.",
            )

        new_token = secrets.token_hex(32)
        tmp_token = token_path.parent / f".local_token.{secrets.token_hex(4)}.tmp"
        try:
            fd = os.open(str(tmp_token), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with open(fd, "w", encoding="utf-8") as f:
                f.write(new_token)
                f.flush()
                os.fsync(f.fileno())
            set_owner_only_permissions(tmp_token)
            tmp_token.replace(token_path)
        except Exception:
            if tmp_token.is_file():
                tmp_token.unlink(missing_ok=True)
            raise

        if force:
            with _AUTH_LOCK:
                _LAUNCH_NONCES.clear()
                _ACTIVE_SESSIONS.clear()

    return new_token, token_path


def exchange_launch_nonce(nonce: str) -> tuple[str, str]:
    """Exchange a single-use launch nonce for a session cookie token.

    Returns:
        tuple of (session_token, principal)
    """
    if not nonce:
        raise StudioAPIError(
            "UNAUTHENTICATED",
            "Launch nonce is invalid or has already been used.",
        )

    with _AUTH_LOCK:
        entry = _LAUNCH_NONCES.pop(nonce, None)

    _cleanup_expired_nonces_and_sessions()

    if entry is None:
        raise StudioAPIError(
            "UNAUTHENTICATED",
            "Launch nonce is invalid or has already been used.",
        )

    principal, role, expires_at = entry
    if time.time() > expires_at:
        raise StudioAPIError(
            "LAUNCH_NONCE_EXPIRED",
            "Launch nonce has expired.",
        )

    session_token = secrets.token_hex(32)
    with _AUTH_LOCK:
        _ACTIVE_SESSIONS[session_token] = {
            "principal": principal,
            "role": role,
            "expires_at": time.time() + 86400,
        }
        _evict_overflow_locked()
    return session_token, principal


def get_active_session_role(session_token: str) -> str | None:
    """Return the RBAC role bound to an active HttpOnly session token, if present."""
    with _AUTH_LOCK:
        entry = _ACTIVE_SESSIONS.get(session_token)
        if not isinstance(entry, dict):
            return None
        role = entry.get("role")
        return str(role) if isinstance(role, str) else None


def _normalize_studio_role(raw_role: str, *, allow_legacy_aliases: bool = True) -> str | None:
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


def authenticate_studio_request(
    request: Request,
    studio_root: Path,
    *,
    required_role: str | None = None,
) -> StudioPrincipal:
    """Authenticate incoming HTTP request against studio auth policies."""
    _cleanup_expired_nonces_and_sessions()
    server_mode = get_server_mode(request)

    auth_header = request.headers.get("authorization")
    session_cookie = request.cookies.get("opendv_session")
    role_header = request.headers.get("x-opendv-role")

    principal_obj: StudioPrincipal | None = None

    if auth_header and auth_header.strip().lower().startswith("bearer "):
        token = auth_header.strip()[7:].strip()
        if server_mode == SERVER_MODE_REMOTE:
            try:
                pub_key = get_jwt_verification_key()
                unverified_header = jwt.get_unverified_header(token)
                if unverified_header.get("alg") != "RS256":
                    raise StudioAPIError(
                        "UNAUTHENTICATED",
                        f"Algorithm '{unverified_header.get('alg')}' forbidden in remote mode; "
                        "only RS256 is permitted.",
                    )

                expected_issuer = os.environ.get("OPENDV_JWT_ISSUER", "opendv-platform")
                expected_audience = os.environ.get("OPENDV_JWT_AUDIENCE", "opendv-api")
                claims = jwt.decode(
                    token,
                    pub_key,
                    algorithms=["RS256"],
                    issuer=expected_issuer,
                    audience=expected_audience,
                    leeway=60,
                    options={"require": ["exp", "sub", "iss", "aud"]},
                )
                principal = str(claims["sub"]).strip()
                if not _PRINCIPAL_PATTERN.match(principal) or len(principal) > 128:
                    raise StudioAPIError(
                        "UNAUTHENTICATED",
                        f"JWT sub claim '{principal}' does not match pattern "
                        "^(human|agent|system|user):[a-zA-Z0-9_.-]+$ or exceeds 128 characters.",
                    )
                raw_jwt_role = str(claims.get("role") or "viewer")
                normalized_role = _normalize_studio_role(raw_jwt_role, allow_legacy_aliases=True)
                if normalized_role is None:
                    raise StudioAPIError(
                        "UNAUTHENTICATED",
                        f"JWT role claim '{raw_jwt_role}' is not a valid role "
                        "(admin, editor, viewer, automated).",
                    )
                if principal.startswith("agent:"):
                    normalized_role = "automated"

                principal_obj = StudioPrincipal(
                    principal=principal,
                    role=normalized_role,
                    is_admin=(normalized_role == "admin"),
                    auth_method="jwt",
                )
            except jwt.PyJWTError as exc:
                raise StudioAPIError("UNAUTHENTICATED", f"JWT validation failed: {exc}") from exc
            except StudioAPIError:
                raise
        else:
            local_token = resolve_local_token(studio_root)
            if local_token:
                try:
                    match = hmac.compare_digest(token.encode("utf-8"), local_token.encode("utf-8"))
                except (TypeError, UnicodeEncodeError):
                    match = False
                if match:
                    normalized_role = _normalize_studio_role(
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
                        if (
                            not _PRINCIPAL_PATTERN.match(clean_principal)
                            or len(clean_principal) > 128
                        ):
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
                        principal_str = "human:local_admin"

                    if principal_str.startswith("agent:"):
                        normalized_role = "automated"

                    principal_obj = StudioPrincipal(
                        principal=principal_str,
                        role=normalized_role,
                        is_admin=(normalized_role == "admin"),
                        auth_method="local_token",
                    )
                else:
                    raise StudioAPIError("UNAUTHENTICATED", "Invalid local bearer token.")
            else:
                raise StudioAPIError("UNAUTHENTICATED", "Local token not initialized.")

    elif session_cookie:
        if server_mode == SERVER_MODE_REMOTE:
            raise StudioAPIError(
                "UNAUTHENTICATED",
                "Session cookies are disabled in remote_platform mode; RS256 Bearer JWT required.",
            )
        with _AUTH_LOCK:
            session_data = _ACTIVE_SESSIONS.get(session_cookie)
        if session_data and time.time() <= session_data["expires_at"]:
            role = str(session_data["role"])
            principal_str = str(session_data["principal"])
            if principal_str.startswith("agent:"):
                role = "automated"
            principal_obj = StudioPrincipal(
                principal=principal_str,
                role=role,
                is_admin=(role == "admin"),
                auth_method="cookie",
            )
        else:
            raise StudioAPIError("UNAUTHENTICATED", "Session cookie expired or invalid.")

    if principal_obj is None:
        raise StudioAPIError("UNAUTHENTICATED", "Authentication credentials required.")

    request.state.studio_principal = principal_obj

    if required_role and not principal_obj.can_perform(required_role):
        if principal_obj.is_automated:
            code = (
                "AGENT_ADMIN_OP_FORBIDDEN"
                if required_role == "admin"
                else "AGENT_OPERATION_FORBIDDEN"
            )
            raise StudioAPIError(
                code,
                f"Automated principal '{principal_obj.principal}' is forbidden from "
                f"performing '{required_role}' operations.",
            )
        if required_role == "admin":
            raise StudioAPIError(
                "FORBIDDEN_ADMIN_REQUIRED",
                "Administrator role required for this studio operation.",
            )
        raise StudioAPIError(
            "SERVER_OPERATION_FORBIDDEN",
            f"Role '{principal_obj.role}' insufficient for required role '{required_role}'.",
        )

    return principal_obj


__all__ = [
    "MAX_ACTIVE_NONCES",
    "MAX_ACTIVE_SESSIONS",
    "SERVER_MODE_LOCAL",
    "SERVER_MODE_REMOTE",
    "StudioPrincipal",
    "authenticate_studio_request",
    "exchange_launch_nonce",
    "get_active_session_role",
    "get_jwt_verification_key",
    "get_local_token_path",
    "get_server_mode",
    "initialize_local_token",
    "is_loopback_client",
    "mint_launch_nonce",
    "resolve_local_token",
]
