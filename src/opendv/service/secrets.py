"""User-global KeyringSecretStore with OS keyring, owner-ACL fallback & 4-tier precedence."""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

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
from opendv.service._remediation import build_remediation
from opendv.service._secret_manifest import (
    SecretSource,
    load_secret_keys_manifest,
    mutate_secret_keys_manifest,
    read_owner_only_json,
    resolve_gemini_api_key_with_source,
)
from opendv.service.os_platform import (
    atomic_write_text,
    locked_file_access,
    resolve_user_state_dir,
)
from opendv.service.settings_store import SettingsStore

_LOGGER = logging.getLogger(__name__)
_HEADLESS_KEYRING_ERRORS = (NoKeyringError, InitError, KeyringLocked)
_KEYRING_IO_ERRORS = (KeyringError, OSError, RuntimeError)


def probe_keyring_available(backend: keyring.backend.KeyringBackend | None = None) -> bool:
    """Return True if an OS keyring backend is viable in the current session."""
    try:
        kr = backend if backend is not None else keyring.get_keyring()
    except (*_HEADLESS_KEYRING_ERRORS, *_KEYRING_IO_ERRORS):
        return False

    if isinstance(kr, (fail.Keyring, null.Keyring)):
        return False
    if isinstance(kr, chainer.ChainerBackend) and not kr.backends:
        return False
    if getattr(kr, "priority", 0) <= 0:
        return False

    try:
        kr.get_password("opendv", "__opendv_probe__")
    except (*_HEADLESS_KEYRING_ERRORS, *_KEYRING_IO_ERRORS):
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


_CLI_ENV_FIRST: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "opendv_cli_env_first", default=False
)


@contextlib.contextmanager
def scoped_env_first(enabled: bool) -> Iterator[None]:
    """Scope `--env-first` secret precedence across async tasks without mutating `os.environ`."""
    if not enabled:
        yield
        return
    token = _CLI_ENV_FIRST.set(True)
    try:
        yield
    finally:
        _CLI_ENV_FIRST.reset(token)


class KeyringSecretStore:
    """User-global credential store supporting OS keyring, user_state_file, & precedence."""

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
        _ = workspace_root
        self.service_name: str = "opendv"
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
        self.secret_keys_path: Path = effective_state_dir / "secret_keys.json"
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

    def effective_env(self) -> Mapping[str, str]:
        """Return effective environment mapping without mutating os.environ."""
        return os.environ if self._environ_override is None else self._environ_override

    def is_env_first_override_active(self) -> bool:
        """Return True when --env-first ContextVar override is active."""
        return _CLI_ENV_FIRST.get()

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
        if cli_env_first or _CLI_ENV_FIRST.get():
            return "env_first"
        env_raw = self.effective_env().get("OPENDV_SECRET_PRECEDENCE", "").strip()
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

    def _read_user_state_payload(self) -> dict[str, Any]:
        with locked_file_access(self._lock_path):
            parsed = read_owner_only_json(
                self.secrets_path,
                default={"schema_version": "2.0", "secrets": {}},
                label="user_state_file secret store",
            )
            if "workspaces" in parsed and "secrets" not in parsed:
                raise OpenDVError(
                    "Legacy workspace-scoped secrets.json ('workspaces') is no longer supported; "
                    "re-bind secrets under flat schema_version '2.0' {'secrets': {...}}.",
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                )
            if not isinstance(parsed.get("secrets"), dict):
                raise OpenDVError(
                    "Corrupt secrets.json: expected JSON object with 'secrets' dictionary.",
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                )
            return parsed

    def _write_user_state_payload(self, payload: dict[str, Any]) -> None:
        serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        atomic_write_text(self.secrets_path, serialized, owner_only=True)

    def _get_from_user_state_file(self, key: str) -> str | None:
        with locked_file_access(self._lock_path):
            data = self._read_user_state_payload()
            val = data["secrets"].get(key)
            return str(val) if isinstance(val, str) and val != "" else None

    def _set_in_user_state_file(self, key: str, value: str) -> None:
        with locked_file_access(self._lock_path):
            data = self._read_user_state_payload()
            data["secrets"][key] = value
            self._write_user_state_payload(data)

    def _delete_from_user_state_file(self, key: str) -> None:
        with locked_file_access(self._lock_path):
            if not self.secrets_path.exists():
                return
            data = self._read_user_state_payload()
            if key in data["secrets"]:
                del data["secrets"][key]
                self._write_user_state_payload(data)

    def _best_effort_delete_user_state_key(self, key: str) -> None:
        if not self.secrets_path.exists():
            return
        try:
            self._delete_from_user_state_file(key)
        except OpenDVError as exc:
            _LOGGER.warning(
                "Best-effort cleanup of stale secrets.json entry '%s' failed: %s", key, exc
            )

    def _active_keyring(self) -> keyring.backend.KeyringBackend:
        return self._keyring_backend if self._keyring_backend is not None else keyring.get_keyring()

    def _store_get(
        self,
        key: str,
        *,
        backend_kind: SecretBackend | None = None,
    ) -> tuple[str | None, SecretBackend]:
        eff_backend = backend_kind if backend_kind is not None else self.effective_backend()
        if eff_backend == "keyring":
            try:
                val = self._active_keyring().get_password(self.service_name, key)
                return (val if val else None), "keyring"
            except _HEADLESS_KEYRING_ERRORS as exc:
                if not self._allow_headless_fallback:
                    raise OpenDVError(
                        f"Keyring backend failure while reading '{key}': {exc}",
                        error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                    ) from exc
                return self._get_from_user_state_file(key), "user_state_file"
            except _KEYRING_IO_ERRORS as exc:
                raise OpenDVError(
                    f"Keyring error while reading '{key}': {exc}",
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                ) from exc
        return self._get_from_user_state_file(key), "user_state_file"

    def read_store_with_source(
        self,
        key: str,
        *,
        backend_kind: SecretBackend | None = None,
    ) -> tuple[str | None, SecretBackend]:
        """Read directly from active store backend (keyring or user_state_file)."""
        return self._store_get(key, backend_kind=backend_kind)

    def _store_set(
        self,
        key: str,
        value: str,
        *,
        backend_kind: SecretBackend | None = None,
    ) -> SecretBackend:
        eff_backend = backend_kind if backend_kind is not None else self.effective_backend()
        if eff_backend == "keyring":
            try:
                self._active_keyring().set_password(self.service_name, key, value)
                return "keyring"
            except _HEADLESS_KEYRING_ERRORS as exc:
                if not self._allow_headless_fallback:
                    raise OpenDVError(
                        f"Keyring backend failure while writing '{key}': {exc}",
                        error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                    ) from exc
                self._set_in_user_state_file(key, value)
                return "user_state_file"
            except _KEYRING_IO_ERRORS as exc:
                raise OpenDVError(
                    f"Keyring error while writing '{key}': {exc}",
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                ) from exc
        self._set_in_user_state_file(key, value)
        return "user_state_file"

    def _store_delete(
        self,
        key: str,
        *,
        backend_kind: SecretBackend | None = None,
    ) -> bool:
        eff_backend = backend_kind if backend_kind is not None else self.effective_backend()
        if eff_backend == "keyring":
            try:
                with contextlib.suppress(PasswordDeleteError):
                    self._active_keyring().delete_password(self.service_name, key)
                return True
            except _HEADLESS_KEYRING_ERRORS as exc:
                if not self._allow_headless_fallback:
                    raise OpenDVError(
                        f"Keyring backend failure while deleting '{key}': {exc}",
                        error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                    ) from exc
            except _KEYRING_IO_ERRORS as exc:
                raise OpenDVError(
                    f"Keyring error while deleting '{key}': {exc}",
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                ) from exc
        self._delete_from_user_state_file(key)
        return False

    def get_secret_with_source(
        self,
        key: str,
        *,
        project_precedence: SecretPrecedence | None = None,
        cli_env_first: bool = False,
        backend_kind: SecretBackend | None = None,
        precedence: SecretPrecedence | None = None,
    ) -> tuple[str | None, SecretSource]:
        """Retrieve a secret value and its active provenance tier."""
        clean_key = _validate_secret_key(key)
        eff_prec = (
            precedence
            if precedence is not None
            else self.resolve_precedence(
                project_precedence=project_precedence,
                cli_env_first=cli_env_first,
            )
        )
        env_val = _read_env_secret(self.effective_env(), clean_key)
        if eff_prec == "env_first" and env_val is not None:
            return env_val, "env"

        eff_backend = backend_kind if backend_kind is not None else self.effective_backend()
        store_val, used_backend = self._store_get(clean_key, backend_kind=eff_backend)
        if eff_prec == "env_first":
            return (store_val, used_backend) if store_val is not None else (None, "unset")
        if store_val is not None:
            return store_val, used_backend
        if env_val is not None:
            return env_val, "env"
        return None, "unset"

    def get_secret(
        self,
        key: str,
        *,
        project_precedence: SecretPrecedence | None = None,
        cli_env_first: bool = False,
    ) -> str | None:
        """Retrieve a user-global secret honoring the 4-layer precedence chain."""
        val, _ = self.get_secret_with_source(
            key,
            project_precedence=project_precedence,
            cli_env_first=cli_env_first,
        )
        return val

    def resolve_gemini_api_key_with_source(
        self,
        *,
        project_precedence: SecretPrecedence | None = None,
        cli_env_first: bool = False,
        backend_kind: SecretBackend | None = None,
        precedence: SecretPrecedence | None = None,
    ) -> tuple[str | None, SecretSource]:
        """Resolve GEMINI_API_KEY before GOOGLE_API_KEY within each active precedence tier."""
        return resolve_gemini_api_key_with_source(
            self,
            project_precedence=project_precedence,
            cli_env_first=cli_env_first,
            backend_kind=backend_kind,
            precedence=precedence,
        )

    def set_secret(self, key: str, value: str) -> SecretSummary:
        """Persist a secret in the active user-global backend."""
        clean_key = _validate_secret_key(key)
        if not value:
            raise OpenDVError(
                f"Secret value for '{clean_key}' cannot be empty.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        backend_kind = self.effective_backend()
        with locked_file_access(self._lock_path):
            used_backend = self._store_set(clean_key, value, backend_kind=backend_kind)
            if used_backend == "keyring":
                mutate_secret_keys_manifest(self.secret_keys_path, add=clean_key)
                self._best_effort_delete_user_state_key(clean_key)
        return SecretSummary(key=clean_key, present=True, backend=used_backend)

    def delete_secret(self, key: str) -> None:
        """Delete a secret from the active store (and stale user_state_file if present)."""
        clean_key = _validate_secret_key(key)
        backend_kind = self.effective_backend()
        with locked_file_access(self._lock_path):
            keyring_deleted = self._store_delete(clean_key, backend_kind=backend_kind)
            if keyring_deleted:
                mutate_secret_keys_manifest(self.secret_keys_path, remove=clean_key)
                self._best_effort_delete_user_state_key(clean_key)

    def list_secret_summaries(
        self,
        keys: Sequence[str] = (),
        *,
        project_precedence: SecretPrecedence | None = None,
        cli_env_first: bool = False,
        include_stored: bool = True,
    ) -> list[SecretSummary]:
        """Return masked presence/backend summaries for candidate and/or stored keys."""
        precedence = self.resolve_precedence(
            project_precedence=project_precedence,
            cli_env_first=cli_env_first,
        )
        ordered_keys: list[str] = []
        requested_keys: set[str] = set()
        for raw_k in keys:
            clean_k = _validate_secret_key(raw_k)
            if clean_k not in requested_keys:
                requested_keys.add(clean_k)
                ordered_keys.append(clean_k)

        env = self.effective_env()
        if not include_stored:
            cached_backend: SecretBackend | None = None
            out: list[SecretSummary] = []
            for k in ordered_keys:
                env_val = _read_env_secret(env, k)
                if precedence == "env_first" and env_val is not None:
                    out.append(SecretSummary(key=k, present=True, backend="env"))
                    continue
                if cached_backend is None:
                    cached_backend = self.effective_backend()
                val, src = self.get_secret_with_source(
                    k,
                    backend_kind=cached_backend,
                    precedence=precedence,
                )
                out.append(SecretSummary(key=k, present=(val is not None), backend=src))
            return out

        backend_kind: SecretBackend = self.effective_backend()
        if backend_kind == "keyring" and self.secrets_path.exists():
            try:
                self._active_keyring().get_password(self.service_name, "__opendv_probe__")
            except _HEADLESS_KEYRING_ERRORS as exc:
                if not self._allow_headless_fallback:
                    raise OpenDVError(
                        f"Keyring backend failure while listing secrets: {exc}",
                        error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                    ) from exc
                backend_kind = "user_state_file"
            except _KEYRING_IO_ERRORS as exc:
                raise OpenDVError(
                    f"Keyring error while listing secrets: {exc}",
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                ) from exc

        seen = set(requested_keys)
        with locked_file_access(self._lock_path):
            if backend_kind == "keyring":
                candidate_keys = load_secret_keys_manifest(self.secret_keys_path)
            else:
                data = self._read_user_state_payload()
                file_keys = sorted(k for k in data["secrets"] if isinstance(k, str))
                manifest_keys = (
                    load_secret_keys_manifest(self.secret_keys_path)
                    if self.secret_keys_path.exists()
                    else []
                )
                candidate_keys = sorted({*file_keys, *manifest_keys})
        for k in candidate_keys:
            if k not in seen:
                seen.add(k)
                ordered_keys.append(k)

        summaries: list[SecretSummary] = []
        for k in ordered_keys:
            env_val = _read_env_secret(env, k)
            if precedence == "env_first" and env_val is not None:
                summaries.append(SecretSummary(key=k, present=True, backend="env"))
                continue
            store_val, used_backend = self._store_get(k, backend_kind=backend_kind)
            if store_val is not None:
                summaries.append(SecretSummary(key=k, present=True, backend=used_backend))
            elif env_val is not None:
                summaries.append(SecretSummary(key=k, present=True, backend="env"))
            elif k in requested_keys:
                summaries.append(SecretSummary(key=k, present=False, backend="unset"))
        return summaries

    def require_secret(
        self,
        key: str,
        *,
        project_precedence: SecretPrecedence | None = None,
        cli_env_first: bool = False,
    ) -> str:
        """Return secret value or fail closed with NEEDS_LOCAL_SECRET_BINDING + remediation."""
        clean_key = _validate_secret_key(key)
        val = self.get_secret(
            clean_key,
            project_precedence=project_precedence,
            cli_env_first=cli_env_first,
        )
        if val is None:
            raise OpenDVError(
                f"Missing local secret binding for '{clean_key}'. "
                "Bind it in Settings or environment.",
                error_code=ErrorCode.NEEDS_LOCAL_SECRET_BINDING,
                remediation=build_remediation(
                    ErrorCode.NEEDS_LOCAL_SECRET_BINDING,
                    missing_secrets=[clean_key],
                ),
            )
        return val


__all__ = [
    "KeyringSecretStore",
    "SecretSource",
    "probe_keyring_available",
    "scoped_env_first",
]
