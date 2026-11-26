"""Shared owner-only secret manifest I/O and Gemini API key provenance resolution."""

from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.project import SecretBackend, SecretPrecedence
from opendv.service.os_platform import atomic_write_text, verify_owner_only_permissions

if TYPE_CHECKING:
    from opendv.service.secrets import KeyringSecretStore

SecretSource = Literal["keyring", "user_state_file", "env", "unset"]


def read_utf8_with_retry(path: Path, *, timeout: float = 2.0) -> str:
    """Read UTF-8 file contents, retrying transient Win32 sharing violations."""
    deadline = time.monotonic() + timeout
    delay = 0.005
    while True:
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError as exc:
            is_win_transient = sys.platform == "win32" and getattr(exc, "winerror", None) in (5, 32)
            if not is_win_transient or time.monotonic() >= deadline:
                raise
            time.sleep(delay + random.uniform(0.001, 0.005))
            delay = min(delay * 2.0, 0.1)


def read_owner_only_json(
    path: Path,
    *,
    default: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    """Read and ACL-verify an owner-only JSON object file, returning default when absent."""
    if not path.exists():
        return {
            k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
            for k, v in default.items()
        }
    if not verify_owner_only_permissions(path):
        raise OpenDVError(
            f"{label} failed owner-only permission verification.",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
        )
    try:
        raw = read_utf8_with_retry(path)
        parsed = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise OpenDVError(
            f"Failed to read {label}: {exc}",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
        ) from exc
    if not isinstance(parsed, dict):
        raise OpenDVError(
            f"Corrupt {label}: expected JSON object.",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
        )
    return parsed


def load_secret_keys_manifest(path: Path) -> list[str]:
    """Load sorted keyring-stored key names from secret_keys.json."""
    parsed = read_owner_only_json(
        path,
        default={"schema_version": "2.0", "keys": []},
        label="secret_keys.json",
    )
    keys = parsed.get("keys")
    if not isinstance(keys, list) or any(not isinstance(k, str) for k in keys):
        raise OpenDVError(
            "Corrupt secret_keys.json: expected JSON object with 'keys' list of strings.",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
        )
    return sorted({k for k in keys if k})


def mutate_secret_keys_manifest(
    path: Path,
    *,
    add: str | None = None,
    remove: str | None = None,
) -> None:
    """Atomically add or remove a key in secret_keys.json (caller must hold secrets.json.lock)."""
    if add is None and remove is not None and not path.exists():
        return
    keys = set(load_secret_keys_manifest(path))
    if add is not None:
        keys.add(add)
    if remove is not None:
        keys.discard(remove)
    serialized = json.dumps({"schema_version": "2.0", "keys": sorted(keys)}, indent=2) + "\n"
    atomic_write_text(path, serialized, owner_only=True)


def resolve_gemini_api_key_with_source(
    secret_store: KeyringSecretStore,
    *,
    project_precedence: SecretPrecedence | None = None,
    cli_env_first: bool = False,
    backend_kind: SecretBackend | None = None,
    precedence: SecretPrecedence | None = None,
) -> tuple[str | None, SecretSource]:
    """Resolve GEMINI_API_KEY before GOOGLE_API_KEY within each active precedence tier."""
    eff_prec = (
        precedence
        if precedence is not None
        else secret_store.resolve_precedence(
            project_precedence=project_precedence,
            cli_env_first=cli_env_first,
        )
    )
    env = secret_store.effective_env()

    def _from_env() -> tuple[str | None, SecretSource]:
        for key_name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            raw = env.get(key_name)
            if raw is not None and raw.strip():
                return raw, "env"
        return None, "unset"

    def _from_store() -> tuple[str | None, SecretSource]:
        eff_backend = (
            backend_kind if backend_kind is not None else secret_store.effective_backend()
        )
        for key_name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            val, used_backend = secret_store.read_store_with_source(
                key_name, backend_kind=eff_backend
            )
            if val is not None:
                return val, used_backend
        return None, "unset"

    if eff_prec == "env_first":
        val, src = _from_env()
        if val is not None:
            return val, src
        return _from_store()

    val, src = _from_store()
    if val is not None:
        return val, src
    return _from_env()


__all__ = [
    "SecretSource",
    "load_secret_keys_manifest",
    "mutate_secret_keys_manifest",
    "read_owner_only_json",
    "read_utf8_with_retry",
    "resolve_gemini_api_key_with_source",
]
