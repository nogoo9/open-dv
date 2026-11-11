"""Studio authentication, server modes, launch nonce and token lifecycle (Task 5B.4 / Issue #218).

Conforms to docs/19 §8.2, docs/20 §3.1, ADR-016:
- Dual server modes: local_standalone vs remote_platform
- 60s launch nonce (single-use) exchanged for HttpOnly session cookie
- L1 lock (.local_token.lock) protected .local_token with permissions 0600
- RS256 JWT validation allowlist in remote mode via PyJWT
"""

from __future__ import annotations

import hmac
import ipaddress
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal
from urllib.parse import urlparse

import jwt
from fastapi import Request

from opendv_platform_api.security.error_catalog import StudioAPIError
from opendv_service.studio.locks import L1_LOCAL_TOKEN, studio_lock
from opendv_service.studio.workspace_security import set_owner_only_permissions

SERVER_MODE_LOCAL: Final[str] = "local_standalone"
SERVER_MODE_REMOTE: Final[str] = "remote_platform"

Role = Literal["admin", "editor", "viewer"]

_PRINCIPAL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(human|agent|system|user):[a-zA-Z0-9_.\-]+$"
)

_AUTH_LOCK = threading.Lock()
_LAUNCH_NONCES: dict[str, tuple[str, str, float]] = {}  # nonce -> (principal, role, expires_at)
_ACTIVE_SESSIONS: dict[str, dict[str, Any]] = {}  # token -> {principal, role, expires_at}


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
            status_code=500,
        )
    raise StudioAPIError(
        "AUTHENTICATION_CONFIG_ERROR",
        "Remote platform mode requires OPENDV_JWT_PUBLIC_KEY or OPENDV_JWT_PUBLIC_KEY_PATH.",
        status_code=500,
    )


def _cleanup_expired_nonces_and_sessions(grace_period: float = 300.0) -> None:
    """Purge expired nonces and sessions from memory in a thread-safe manner."""
    now = time.time()
    with _AUTH_LOCK:
        expired_nonces = [k for k, v in list(_LAUNCH_NONCES.items()) if now > v[2] + grace_period]
        for k in expired_nonces:
            _LAUNCH_NONCES.pop(k, None)

        expired_sessions = [k for k, v in list(_ACTIVE_SESSIONS.items()) if now > v["expires_at"]]
        for k in expired_sessions:
            _ACTIVE_SESSIONS.pop(k, None)


@dataclass(frozen=True)
class StudioPrincipal:
    """Authenticated caller identity and role context."""

    principal: str
    role: str
    is_admin: bool
    auth_method: str

    def can_perform(self, required_role: str) -> bool:
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
            status_code=500,
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
                    status_code=500,
                )

    return SERVER_MODE_LOCAL


def is_loopback_client(request: Request) -> bool:
    """Assert caller connects from a loopback address and validate Host/Origin against rebinding."""
    if not request.client or not request.client.host:
        return False
    try:
        if not ipaddress.ip_address(request.client.host).is_loopback:
            return False
    except (ValueError, AttributeError):
        return False

    loopback_name = "local" + "host"
    host = request.headers.get("host")
    if host:
        host_hostname = urlparse(f"//{host}").hostname or host
        if host_hostname != loopback_name:
            try:
                if not ipaddress.ip_address(host_hostname).is_loopback:
                    return False
            except ValueError:
                return False

    origin = request.headers.get("origin")
    if origin and origin.strip().lower() != "null":
        origin_hostname = urlparse(origin).hostname or origin
        if origin_hostname != loopback_name:
            try:
                if not ipaddress.ip_address(origin_hostname).is_loopback:
                    return False
            except ValueError:
                return False

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
            status_code=403,
        )

    if os.environ.get("OPENDV_TOKEN"):
        raise StudioAPIError(
            "STATIC_TOKEN_CONFIGURED",
            "Token is statically configured via OPENDV_TOKEN environment variable "
            "and cannot be initialized or rotated.",
            status_code=400,
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
                status_code=409,
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
                status_code=401,
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
            # Invalidate active nonces and session cookies upon token rotation (MIN-08, SUG-02)
            with _AUTH_LOCK:
                _LAUNCH_NONCES.clear()
                _ACTIVE_SESSIONS.clear()

    return new_token, token_path


def mint_launch_nonce(
    principal: str = "human:local_admin",
    role: str = "admin",
    ttl: float = 60.0,
) -> str:
    """Generate a single-use 256-bit launch nonce with 60s TTL."""
    _cleanup_expired_nonces_and_sessions()
    nonce = secrets.token_hex(32)
    with _AUTH_LOCK:
        _LAUNCH_NONCES[nonce] = (principal, role, time.time() + ttl)
    return nonce


def exchange_launch_nonce(nonce: str) -> tuple[str, str]:
    """Exchange a single-use launch nonce for a session cookie token.

    Returns:
        tuple of (session_token, principal)
    """
    if not nonce:
        raise StudioAPIError(
            "UNAUTHENTICATED",
            "Launch nonce is invalid or has already been used.",
            status_code=401,
        )

    with _AUTH_LOCK:
        entry = _LAUNCH_NONCES.pop(nonce, None)

    _cleanup_expired_nonces_and_sessions()

    if entry is None:
        raise StudioAPIError(
            "UNAUTHENTICATED",
            "Launch nonce is invalid or has already been used.",
            status_code=401,
        )

    principal, role, expires_at = entry
    if time.time() > expires_at:
        raise StudioAPIError(
            "LAUNCH_NONCE_EXPIRED",
            "Launch nonce has expired.",
            status_code=401,
        )

    session_token = secrets.token_hex(32)
    with _AUTH_LOCK:
        _ACTIVE_SESSIONS[session_token] = {
            "principal": principal,
            "role": role,
            "expires_at": time.time() + 86400,
        }
    return session_token, principal


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
            # Remote mode: cryptographic RS256 signature verification via PyJWT
            try:
                pub_key = get_jwt_verification_key()
                # Verify header algorithm explicitly
                unverified_header = jwt.get_unverified_header(token)
                if unverified_header.get("alg") != "RS256":
                    raise StudioAPIError(
                        "UNAUTHENTICATED",
                        f"Algorithm '{unverified_header.get('alg')}' forbidden in remote mode; "
                        "only RS256 is permitted.",
                        status_code=401,
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
                        status_code=401,
                    )
                raw_jwt_role = str(claims.get("role") or "viewer").lower().strip()
                if raw_jwt_role not in ("admin", "editor", "viewer"):
                    raise StudioAPIError(
                        "UNAUTHENTICATED",
                        f"JWT role claim '{raw_jwt_role}' is not a valid role "
                        "(admin, editor, viewer).",
                        status_code=401,
                    )

                principal_obj = StudioPrincipal(
                    principal=principal,
                    role=raw_jwt_role,
                    is_admin=(raw_jwt_role == "admin"),
                    auth_method="jwt",
                )
            except jwt.PyJWTError as exc:
                raise StudioAPIError(
                    "UNAUTHENTICATED", f"JWT validation failed: {exc}", status_code=401
                ) from exc
            except StudioAPIError:
                raise
        else:
            # Local standalone mode: check against .local_token
            local_token = resolve_local_token(studio_root)
            if local_token:
                try:
                    match = hmac.compare_digest(token.encode("utf-8"), local_token.encode("utf-8"))
                except (TypeError, UnicodeEncodeError):
                    match = False
                if match:
                    raw_role = (role_header or "admin").lower().strip()
                    if raw_role in ("data_steward", "restricted_pii_custodian", "admin"):
                        raw_role = "admin"
                    elif raw_role in ("internal_modeler", "editor"):
                        raw_role = "editor"
                    elif raw_role in ("public", "auditor_finance", "viewer"):
                        raw_role = "viewer"
                    else:
                        raise StudioAPIError(
                            "SYNTAX_INVALID_ARGUMENT",
                            f"Invalid X-OpenDV-Role '{role_header}'; "
                            "permitted roles are admin, editor, viewer, DATA_STEWARD, "
                            "RESTRICTED_PII_CUSTODIAN, INTERNAL_MODELER, AUDITOR_FINANCE, PUBLIC.",
                            status_code=400,
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
                                status_code=400,
                            )
                        principal_str = clean_principal
                    else:
                        principal_str = "human:local_admin"

                    principal_obj = StudioPrincipal(
                        principal=principal_str,
                        role=raw_role,
                        is_admin=(raw_role == "admin"),
                        auth_method="local_token",
                    )
                else:
                    raise StudioAPIError(
                        "UNAUTHENTICATED", "Invalid local bearer token.", status_code=401
                    )
            else:
                raise StudioAPIError(
                    "UNAUTHENTICATED", "Local token not initialized.", status_code=401
                )

    elif session_cookie:
        with _AUTH_LOCK:
            session_data = _ACTIVE_SESSIONS.get(session_cookie)
        if session_data and time.time() <= session_data["expires_at"]:
            role = session_data["role"]
            principal_obj = StudioPrincipal(
                principal=session_data["principal"],
                role=role,
                is_admin=(role == "admin"),
                auth_method="cookie",
            )
        else:
            raise StudioAPIError(
                "UNAUTHENTICATED", "Session cookie expired or invalid.", status_code=401
            )

    if principal_obj is None:
        raise StudioAPIError(
            "UNAUTHENTICATED", "Authentication credentials required.", status_code=401
        )

    if required_role and not principal_obj.can_perform(required_role):
        if required_role == "admin":
            raise StudioAPIError(
                "FORBIDDEN_ADMIN_REQUIRED",
                "Administrator role required for this studio operation.",
                status_code=403,
            )
        raise StudioAPIError(
            "SERVER_OPERATION_FORBIDDEN",
            f"Role '{principal_obj.role}' insufficient for required role '{required_role}'.",
            status_code=403,
        )

    return principal_obj


__all__ = [
    "SERVER_MODE_LOCAL",
    "SERVER_MODE_REMOTE",
    "StudioPrincipal",
    "authenticate_studio_request",
    "exchange_launch_nonce",
    "get_jwt_verification_key",
    "get_local_token_path",
    "get_server_mode",
    "initialize_local_token",
    "is_loopback_client",
    "mint_launch_nonce",
    "resolve_local_token",
]
