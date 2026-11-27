"""Deterministic in-memory/failing keyring backends and owner-ACL helpers for V2 tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import keyring.backend
import keyring.credentials
import keyring.errors

from opendv.service.os_platform import (
    atomic_write_text,
    set_owner_only_permissions,
    verify_owner_only_permissions,
)


class InMemoryKeyringBackend(keyring.backend.KeyringBackend):
    """Deterministic in-memory KeyringBackend mimicking WinVault/SecretService delete rules."""

    priority: float = 10.0

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[str, str, str]] = []

    def get_password(self, service: str, username: str) -> str | None:
        self.calls.append(("get", service, username))
        return self._store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.calls.append(("set", service, username))
        self._store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.calls.append(("delete", service, username))
        if (service, username) not in self._store:
            raise keyring.errors.PasswordDeleteError(
                f"No stored credential for ({service}, {username})"
            )
        del self._store[(service, username)]

    def get_credential(
        self,
        service: str,
        username: str | None,
    ) -> keyring.credentials.Credential | None:
        if username is None:
            for (svc, user), pwd in self._store.items():
                if svc == service:
                    return keyring.credentials.SimpleCredential(user, pwd)
            return None
        val = self._store.get((service, username))
        if val is None:
            return None
        return keyring.credentials.SimpleCredential(username, val)

    def services(self) -> set[str]:
        return {svc for svc, _ in self._store}

    def keys_for_service(self, service: str) -> list[str]:
        return sorted(user for svc, user in self._store if svc == service)

    def snapshot(self) -> dict[tuple[str, str], str]:
        return dict(self._store)

    def clear(self) -> None:
        self._store.clear()
        self.calls.clear()


class FailingKeyringBackend(keyring.backend.KeyringBackend):
    """KeyringBackend raising a configurable KeyringError to test headless CI fallback."""

    priority: float = 1.0

    def __init__(
        self,
        error_cls: type[keyring.errors.KeyringError] = keyring.errors.NoKeyringError,
        message: str = "Simulated unavailable OS keyring daemon",
    ) -> None:
        self._error_cls = error_cls
        self._message = message
        self.calls: list[tuple[str, str, str]] = []

    def get_password(self, service: str, username: str) -> str | None:
        self.calls.append(("get", service, username))
        raise self._error_cls(self._message)

    def set_password(self, service: str, username: str, password: str) -> None:
        self.calls.append(("set", service, username))
        raise self._error_cls(self._message)

    def delete_password(self, service: str, username: str) -> None:
        self.calls.append(("delete", service, username))
        raise self._error_cls(self._message)


class OwnerAclStateHelper:
    """Helper wrapping OS user-state owner-only ACL enforcement and non-owner grant testing."""

    def __init__(self, state_dir: Path | str) -> None:
        self.state_dir = Path(state_dir)
        self.secrets_path = self.state_dir / "secrets.json"
        self.secret_keys_path = self.state_dir / "secret_keys.json"
        self.readiness_path = self.state_dir / "readiness.json"
        self.settings_path = self.state_dir / "settings.json"
        self.mcp_servers_path = self.state_dir / "mcp_servers.json"

    def ensure_secure_dir(self) -> Path:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        set_owner_only_permissions(self.state_dir)
        return self.state_dir

    def write_secrets_payload(self, payload: dict[str, Any], *, owner_only: bool = True) -> Path:
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        atomic_write_text(self.secrets_path, text, owner_only=owner_only)
        return self.secrets_path

    def apply_owner_only(self, path: Path | None = None) -> None:
        set_owner_only_permissions(path or self.secrets_path)

    def verify_owner_only(self, path: Path | None = None) -> bool:
        return verify_owner_only_permissions(path or self.secrets_path)

    def grant_non_owner_access(self, path: Path | None = None) -> None:
        """Grant world-readable access to verify fail-closed ACL checks across Windows/POSIX."""
        target = path or self.secrets_path
        if sys.platform == "win32":
            subprocess.run(
                ["icacls", str(target), "/grant", "*S-1-1-0:(R)"],
                check=True,
                capture_output=True,
                text=True,
            )
        else:
            os.chmod(target, 0o755 if target.is_dir() else 0o644)

    def read_raw_text(self, path: Path | None = None) -> str:
        return (path or self.secrets_path).read_text(encoding="utf-8")


__all__ = [
    "FailingKeyringBackend",
    "InMemoryKeyringBackend",
    "OwnerAclStateHelper",
]
