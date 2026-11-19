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

import contextlib
import hmac
import ipaddress
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

import jwt
from fastapi import Request

from opendv_platform_api.security.csrf import origin_matches_host, verify_csrf
from opendv_platform_api.security.error_catalog import StudioAPIError
from opendv_platform_api.security.loopback_policy import (
    _PRINCIPAL_PATTERN,
    is_loopback_request,
    is_remote_unauthenticated_enabled,
    normalize_studio_role,
    resolve_open_access_identity,
)
from opendv_service.rbac import ROLE_CLEARANCE_LEVELS, RbacRole, normalize_rbac_role
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
_STUDIO_TO_RBAC_ROLE: Final[dict[str, RbacRole]] = {
    "viewer": RbacRole.PUBLIC,
    "editor": RbacRole.INTERNAL_MODELER,
    "automated": RbacRole.INTERNAL_MODELER,
    "admin": RbacRole.PRIVILEGED_CUSTODIAN,
}


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


def is_loopback_client(request: Request) -> bool:
    """Verify caller connects from loopback and validate Host/Origin/Fetch-Metadata."""
    return is_loopback_request(request, allow_testclient=True)


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


_normalize_studio_role = normalize_studio_role


def _verify_remote_jwt(token: str) -> StudioPrincipal:
    """Verify RS256 Bearer JWT (signature, alg, iss, aud, exp, sub, role) for remote_platform."""
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

    return StudioPrincipal(
        principal=principal,
        role=normalized_role,
        is_admin=(normalized_role == "admin"),
        auth_method="jwt",
    )


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

    principal_obj: StudioPrincipal | None = None

    if server_mode == SERVER_MODE_REMOTE:
        if auth_header and auth_header.strip().lower().startswith("bearer "):
            token = auth_header.strip()[7:].strip()
            try:
                principal_obj = _verify_remote_jwt(token)
            except jwt.PyJWTError as exc:
                raise StudioAPIError("UNAUTHENTICATED", f"JWT validation failed: {exc}") from exc
        elif session_cookie:
            raise StudioAPIError(
                "UNAUTHENTICATED",
                "Session cookies are disabled in remote_platform mode; RS256 Bearer JWT required.",
            )
    else:
        is_loopback = is_loopback_request(request, allow_testclient=True)
        if not is_loopback:
            _verify_remote_unauth_perimeter(request)
        if session_cookie:
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
        if principal_obj is None:
            principal_str, resolved_role = resolve_open_access_identity(request)
            principal_obj = StudioPrincipal(
                principal=principal_str,
                role=resolved_role,
                is_admin=(resolved_role == "admin"),
                auth_method="loopback",
            )

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


def _is_loopback_client_ip(host: str) -> bool:
    if host == "testclient":
        return True
    try:
        return bool(ipaddress.ip_address(host).is_loopback)
    except ValueError:
        return False


def _verify_remote_unauth_perimeter(request: Request) -> None:
    """Enforce --allow-remote-unauthenticated DNS-rebinding and Origin/Host perimeter."""
    client_host = request.client.host if request.client else ""
    if not is_remote_unauthenticated_enabled(request) or _is_loopback_client_ip(client_host):
        raise StudioAPIError(
            "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE",
            "Studio access requires a loopback caller with a valid loopback Host header.",
            instance=str(request.url.path),
        )
    origin = request.headers.get("origin")
    host = request.headers.get("host") or ""
    if origin and (not host or not origin_matches_host(origin, host)):
        raise StudioAPIError(
            "CSRF_ORIGIN_MISMATCH",
            f"Cross-origin request rejected: Origin '{origin}' does not match Host '{host}'.",
            status_code=403,
            instance=str(request.url.path),
        )


def is_authenticated_request(request: Request) -> bool:
    """Verify whether caller is authenticated via principal, loopback, or remote JWT."""
    if getattr(request.state, "studio_principal", None) is not None:
        return True
    if get_server_mode(request) == SERVER_MODE_REMOTE:
        auth_header = request.headers.get("authorization")
        if auth_header and auth_header.strip().lower().startswith("bearer "):
            token = auth_header.strip()[7:].strip()
            with contextlib.suppress(jwt.PyJWTError, StudioAPIError):
                request.state.studio_principal = _verify_remote_jwt(token)
                return True
        return False
    try:
        is_loopback = is_loopback_request(request, allow_testclient=True)
    except StudioAPIError:
        return False
    if is_loopback:
        with contextlib.suppress(StudioAPIError):
            verify_csrf(request)
            return True
        return False
    # Reject forged Host on loopback socket (DNS rebinding) or cross-origin/Referer attacks
    # even when --allow-remote-unauthenticated is active.
    with contextlib.suppress(StudioAPIError):
        _verify_remote_unauth_perimeter(request)
        verify_csrf(request)
        return True
    return False


def resolve_remote_rbac_role(
    request: Request,
    requested_header: str | None,
    *,
    authenticated: bool,
) -> RbacRole:
    """Map remote JWT role to 5-tier RbacRole and allow only privilege downgrades."""
    if not authenticated:
        return RbacRole.PUBLIC
    principal = getattr(request.state, "studio_principal", None)
    max_role = (
        _STUDIO_TO_RBAC_ROLE.get(principal.role, RbacRole.PUBLIC)
        if principal is not None
        else RbacRole.PUBLIC
    )
    if not requested_header or not requested_header.strip():
        return max_role
    requested = normalize_rbac_role(requested_header, default=max_role, authenticated=True)
    if ROLE_CLEARANCE_LEVELS[requested].issubset(ROLE_CLEARANCE_LEVELS[max_role]):
        return requested
    return max_role


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
    "is_authenticated_request",
    "is_loopback_client",
    "mint_launch_nonce",
    "resolve_local_token",
    "resolve_remote_rbac_role",
]
