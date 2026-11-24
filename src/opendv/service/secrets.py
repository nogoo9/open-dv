"""Workspace-scoped KeyringSecretStore with OS keyring + owner-ACL fallback & 4-tier precedence."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, cast

import keyring
import keyring.backend
from keyring.backends import chainer, fail, null
from keyring.errors import (
    InitError,
    KeyringError,
    KeyringLocked,
    NoKeyringError,
    PasswordDeleteError,
)

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.project import SecretBackend, SecretPrecedence, WorkspaceSettings
from opendv.models.wire_dtos_discovery import SecretSummary
from opendv.service.os_platform import (
    atomic_write_text,
    locked_file_access,
    resolve_opendv_home,
    resolve_user_state_dir,
    verify_owner_only_permissions,
)
from opendv.service.settings_store import SettingsStore


def resolve_workspace_namespace(workspace_root: Path | str | None = None) -> tuple[str, str, str]:
    """Return (canonical_opendv_home, sha256_digest, keyring_service_name) for a workspace."""
    resolved = resolve_opendv_home(workspace_root)
    canonical = os.path.normcase(os.path.realpath(os.fspath(resolved)))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return canonical, digest, f"opendv:{digest}"


def probe_keyring_available(backend: keyring.backend.KeyringBackend | None = None) -> bool:
    """Return True if an OS keyring backend is viable in the current session."""
    try:
        kr = backend if backend is not None else keyring.get_keyring()
    except (NoKeyringError, InitError, KeyringLocked, KeyringError, RuntimeError, OSError):
        return False

    if isinstance(kr, (fail.Keyring, null.Keyring)):
        return False
    if isinstance(kr, chainer.ChainerBackend) and not kr.backends:
        return False
    if getattr(kr, "priority", 0) <= 0:
        return False

    try:
        kr.get_password("opendv:probe", "__opendv_probe__")
    except (NoKeyringError, InitError, KeyringLocked, KeyringError, RuntimeError, OSError):
        return False
    return True


def _validate_secret_key(key: str) -> str:
    cleaned = key.strip()
    if not cleaned or any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned):
        raise OpenDVError(
            "Secret key must be non-empty without control characters.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    return cleaned


def _read_env_secret(env: Mapping[str, str], key: str) -> str | None:
    raw = env.get(key)
    if raw is None or not raw.strip():
        return None
    return raw


class KeyringSecretStore:
    """Workspace-isolated credential store supporting OS keyring, user_state_file, & precedence."""

    def __init__(
        self,
        workspace_root: Path | str | None = None,
        *,
        state_dir: Path | str | None = None,
        settings_store: SettingsStore | None = None,
        settings: WorkspaceSettings | None = None,
        keyring_backend: keyring.backend.KeyringBackend | None = None,
        environ: Mapping[str, str] | None = None,
        allow_headless_fallback: bool = True,
    ) -> None:
        self.canonical_workspace, self.workspace_digest, self.service_name = (
            resolve_workspace_namespace(workspace_root)
        )
        effective_state_dir = (
            Path(state_dir)
            if state_dir is not None
            else (
                settings_store.state_dir
                if settings_store is not None
                else resolve_user_state_dir()
            )
        )
        self.state_dir: Path = effective_state_dir
        self.secrets_path: Path = effective_state_dir / "secrets.json"
        self._lock_path: Path = effective_state_dir / "secrets.json.lock"
        self._settings_store: SettingsStore | None = (
            settings_store
            if settings_store is not None
            else (SettingsStore(user_state_dir=effective_state_dir) if settings is None else None)
        )
        self._static_settings: WorkspaceSettings | None = settings
        self._keyring_backend: keyring.backend.KeyringBackend | None = keyring_backend
        self._environ_override: Mapping[str, str] | None = environ
        self._allow_headless_fallback: bool = allow_headless_fallback
        self._known_keys: set[str] = set()
        self._mem_lock: threading.RLock = threading.RLock()

    def _env(self) -> Mapping[str, str]:
        return os.environ if self._environ_override is None else self._environ_override

    def _current_settings(self) -> WorkspaceSettings:
        if self._static_settings is not None:
            return self._static_settings
        if self._settings_store is not None:
            return self._settings_store.load_settings()
        return WorkspaceSettings()

    def resolve_precedence(
        self,
        *,
        project_precedence: SecretPrecedence | None = None,
        cli_env_first: bool = False,
    ) -> SecretPrecedence:
        """Resolve precedence: --env-first > OPENDV_SECRET_PRECEDENCE > Project > Workspace."""
        if cli_env_first:
            return "env_first"
        env_raw = self._env().get("OPENDV_SECRET_PRECEDENCE", "").strip()
        if env_raw:
            if env_raw not in ("store_first", "env_first"):
                raise OpenDVError(
                    f"Invalid OPENDV_SECRET_PRECEDENCE '{env_raw}'; "
                    "expected 'store_first' or 'env_first'.",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                )
            return cast(SecretPrecedence, env_raw)
        if project_precedence is not None:
            if project_precedence not in ("store_first", "env_first"):
                raise OpenDVError(
                    f"Invalid project secret_precedence '{project_precedence}'.",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                )
            return project_precedence
        return self._current_settings().secret_precedence

    def effective_backend(self) -> SecretBackend:
        """Determine active storage backend ('keyring' vs 'user_state_file')."""
        configured = self._current_settings().secret_backend
        if configured == "user_state_file":
            return "user_state_file"
        if self._keyring_backend is not None:
            return "keyring"
        if probe_keyring_available(None):
            return "keyring"
        if self._allow_headless_fallback:
            return "user_state_file"
        raise OpenDVError(
            "OS keyring backend is unavailable and headless fallback is disabled.",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
        )

    def _read_secrets_text_with_retry(self) -> str:
        deadline = time.monotonic() + 2.0
        delay = 0.005
        while True:
            try:
                return self.secrets_path.read_text(encoding="utf-8")
            except PermissionError as exc:
                winerror = getattr(exc, "winerror", None)
                if sys.platform == "win32" and winerror in (5, 32) and time.monotonic() < deadline:
                    time.sleep(delay)
                    delay = min(delay * 2.0, 0.1)
                    continue
                raise

    def _read_user_state_payload(self) -> dict[str, Any]:
        with locked_file_access(self._lock_path):
            if not self.secrets_path.exists():
                return {"schema_version": "2.0", "workspaces": {}}
            if not verify_owner_only_permissions(self.secrets_path):
                raise OpenDVError(
                    "Secret store file failed owner-only permission verification.",
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                )
            try:
                raw = self._read_secrets_text_with_retry()
                parsed = json.loads(raw)
            except (OSError, json.JSONDecodeError) as exc:
                raise OpenDVError(
                    f"Failed to read user_state_file secret store: {exc}",
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                ) from exc

            if not isinstance(parsed, dict) or not isinstance(parsed.get("workspaces"), dict):
                raise OpenDVError(
                    "Corrupt secrets.json: expected JSON object with 'workspaces' dictionary.",
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                )
            return parsed

    def _write_user_state_payload(self, payload: dict[str, Any]) -> None:
        serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        atomic_write_text(self.secrets_path, serialized, owner_only=True)

    def _get_from_user_state_file(self, key: str) -> str | None:
        with locked_file_access(self._lock_path):
            data = self._read_user_state_payload()
            ws_map = data["workspaces"].get(self.workspace_digest, {})
            if not isinstance(ws_map, dict):
                return None
            val = ws_map.get(key)
            return str(val) if isinstance(val, str) and val != "" else None

    def _set_in_user_state_file(self, key: str, value: str) -> None:
        with locked_file_access(self._lock_path):
            data = self._read_user_state_payload()
            workspaces = data["workspaces"]
            ws_map = workspaces.get(self.workspace_digest)
            if not isinstance(ws_map, dict):
                ws_map = {}
                workspaces[self.workspace_digest] = ws_map
            ws_map[key] = value
            self._write_user_state_payload(data)

    def _delete_from_user_state_file(self, key: str) -> None:
        with locked_file_access(self._lock_path):
            if not self.secrets_path.exists():
                return
            data = self._read_user_state_payload()
            ws_map = data["workspaces"].get(self.workspace_digest)
            if isinstance(ws_map, dict) and key in ws_map:
                del ws_map[key]
                self._write_user_state_payload(data)

    def _active_keyring(self) -> keyring.backend.KeyringBackend:
        return self._keyring_backend if self._keyring_backend is not None else keyring.get_keyring()

    def _store_get(self, key: str) -> tuple[str | None, SecretBackend]:
        backend_kind = self.effective_backend()
        if backend_kind == "keyring":
            try:
                val = self._active_keyring().get_password(self.service_name, key)
                return (val if val else None), "keyring"
            except (NoKeyringError, InitError, KeyringLocked) as exc:
                if not self._allow_headless_fallback:
                    raise OpenDVError(
                        f"Keyring backend failure while reading '{key}': {exc}",
                        error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                    ) from exc
                return self._get_from_user_state_file(key), "user_state_file"
            except (KeyringError, OSError, RuntimeError) as exc:
                raise OpenDVError(
                    f"Keyring error while reading '{key}': {exc}",
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                ) from exc
        return self._get_from_user_state_file(key), "user_state_file"

    def _store_set(self, key: str, value: str) -> SecretBackend:
        backend_kind = self.effective_backend()
        if backend_kind == "keyring":
            try:
                self._active_keyring().set_password(self.service_name, key, value)
                return "keyring"
            except (NoKeyringError, InitError, KeyringLocked) as exc:
                if not self._allow_headless_fallback:
                    raise OpenDVError(
                        f"Keyring backend failure while writing '{key}': {exc}",
                        error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                    ) from exc
                self._set_in_user_state_file(key, value)
                return "user_state_file"
            except (KeyringError, OSError, RuntimeError) as exc:
                raise OpenDVError(
                    f"Keyring error while writing '{key}': {exc}",
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                ) from exc
        self._set_in_user_state_file(key, value)
        return "user_state_file"

    def _store_delete(self, key: str) -> None:
        backend_kind = self.effective_backend()
        if backend_kind == "keyring":
            try:
                self._active_keyring().delete_password(self.service_name, key)
            except PasswordDeleteError:
                pass
            except (NoKeyringError, InitError, KeyringLocked) as exc:
                if not self._allow_headless_fallback:
                    raise OpenDVError(
                        f"Keyring backend failure while deleting '{key}': {exc}",
                        error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                    ) from exc
            except (KeyringError, OSError, RuntimeError) as exc:
                raise OpenDVError(
                    f"Keyring error while deleting '{key}': {exc}",
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                ) from exc
        self._delete_from_user_state_file(key)

    def get_secret(
        self,
        key: str,
        *,
        project_precedence: SecretPrecedence | None = None,
        cli_env_first: bool = False,
    ) -> str | None:
        """Retrieve a workspace-scoped secret honoring the 4-layer precedence chain."""
        clean_key = _validate_secret_key(key)
        precedence = self.resolve_precedence(
            project_precedence=project_precedence,
            cli_env_first=cli_env_first,
        )
        env_val = _read_env_secret(self._env(), clean_key)
        store_val, _ = self._store_get(clean_key)
        if precedence == "env_first":
            return env_val if env_val is not None else store_val
        return store_val if store_val is not None else env_val

    def set_secret(self, key: str, value: str) -> SecretSummary:
        """Persist a secret in the active workspace-scoped backend."""
        clean_key = _validate_secret_key(key)
        if not value:
            raise OpenDVError(
                f"Secret value for '{clean_key}' cannot be empty.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        with locked_file_access(self._lock_path), self._mem_lock:
            used_backend = self._store_set(clean_key, value)
            self._known_keys.add(clean_key)
        return SecretSummary(key=clean_key, present=True, backend=used_backend)

    def delete_secret(self, key: str) -> None:
        """Delete a secret from the active store (and fallback user_state_file if present)."""
        clean_key = _validate_secret_key(key)
        with locked_file_access(self._lock_path), self._mem_lock:
            self._store_delete(clean_key)
            self._known_keys.discard(clean_key)

    def list_secret_summaries(
        self,
        keys: Sequence[str] = (),
        *,
        project_precedence: SecretPrecedence | None = None,
        cli_env_first: bool = False,
    ) -> list[SecretSummary]:
        """Return masked presence/backend summaries for candidate keys."""
        precedence = self.resolve_precedence(
            project_precedence=project_precedence,
            cli_env_first=cli_env_first,
        )
        ordered_keys: list[str] = []
        seen: set[str] = set()
        for raw_k in keys:
            clean_k = _validate_secret_key(raw_k)
            if clean_k not in seen:
                seen.add(clean_k)
                ordered_keys.append(clean_k)

        with locked_file_access(self._lock_path), self._mem_lock:
            if self.secrets_path.exists():
                data = self._read_user_state_payload()
                ws_map = data["workspaces"].get(self.workspace_digest, {})
                if isinstance(ws_map, dict):
                    for k in sorted(ws_map):
                        if isinstance(k, str) and k not in seen:
                            seen.add(k)
                            ordered_keys.append(k)
            known_snapshot = sorted(self._known_keys)
        for k in known_snapshot:
            if k not in seen:
                seen.add(k)
                ordered_keys.append(k)

        env = self._env()
        summaries: list[SecretSummary] = []
        for k in ordered_keys:
            store_val, used_backend = self._store_get(k)
            env_val = _read_env_secret(env, k)
            backend: Literal["keyring", "user_state_file", "env", "unset"]
            if precedence == "env_first":
                if env_val is not None:
                    summaries.append(SecretSummary(key=k, present=True, backend="env"))
                elif store_val is not None:
                    backend = used_backend
                    summaries.append(SecretSummary(key=k, present=True, backend=backend))
                else:
                    summaries.append(SecretSummary(key=k, present=False, backend="unset"))
            else:
                if store_val is not None:
                    backend = used_backend
                    summaries.append(SecretSummary(key=k, present=True, backend=backend))
                elif env_val is not None:
                    summaries.append(SecretSummary(key=k, present=True, backend="env"))
                else:
                    summaries.append(SecretSummary(key=k, present=False, backend="unset"))
        return summaries

    def require_secret(
        self,
        key: str,
        *,
        project_precedence: SecretPrecedence | None = None,
        cli_env_first: bool = False,
    ) -> str:
        """Return secret value or fail closed with NEEDS_LOCAL_SECRET_BINDING."""
        val = self.get_secret(
            key,
            project_precedence=project_precedence,
            cli_env_first=cli_env_first,
        )
        if val is None:
            raise OpenDVError(
                f"Missing local secret binding for '{key}'. Bind it in Settings or environment.",
                error_code=ErrorCode.NEEDS_LOCAL_SECRET_BINDING,
            )
        return val


__all__ = [
    "KeyringSecretStore",
    "probe_keyring_available",
    "resolve_workspace_namespace",
]
