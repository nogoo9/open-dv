"""Out-of-repo SettingsStore for <OS_USER_STATE>/opendv/{settings.json,mcp_servers.json}."""

from __future__ import annotations

import json
import random
import re
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.project import WorkspaceSettings
from opendv.models.sources import ExternalMcpServerSpec
from opendv.service.os_platform import (
    atomic_write_text,
    locked_file_access,
    resolve_user_state_dir,
    verify_owner_only_permissions,
)

_FORBIDDEN_OPTION_KEY_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:"
    r"(?i:password|passwd|secret|token|api_?key|access_?key|private_?key|credentials?)"
    r"|(?:(?<![A-Za-z0-9])|(?<=[a-z0-9])(?=[A-Z]))(?i:pat|auth|pwd)(?:(?![A-Za-z0-9])|(?=[A-Z]))"
    r")"
)
_INLINE_SECRET_VALUE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:dapi[0-9a-f]{32}|sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,})"
)


def _validate_no_secrets_in_settings(settings: WorkspaceSettings) -> None:
    for profile_name, profile in settings.connection_profiles.items():
        for opt_key, opt_val in profile.options.items():
            if _FORBIDDEN_OPTION_KEY_RE.search(opt_key):
                raise OpenDVError(
                    f"Connection profile '{profile_name}' options cannot contain secret key "
                    f"'{opt_key}'; use secret_refs instead.",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                )
            if isinstance(opt_val, str) and _INLINE_SECRET_VALUE_RE.search(opt_val):
                raise OpenDVError(
                    f"Connection profile '{profile_name}' option '{opt_key}' appears to contain "
                    "a raw credential literal; store secrets in KeyringSecretStore.",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                )


def _read_utf8_with_retry(path: Path, *, timeout: float = 2.0) -> str:
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


class SettingsStore:
    """Manages non-secret WorkspaceSettings and ExternalMcpServerSpec registry in OS user state."""

    def __init__(self, user_state_dir: Path | str | None = None) -> None:
        self.state_dir: Path = resolve_user_state_dir(user_state_dir)
        self.settings_path: Path = self.state_dir / "settings.json"
        self.mcp_servers_path: Path = self.state_dir / "mcp_servers.json"
        self._settings_lock_path: Path = self.state_dir / "settings.json.lock"
        self._mcp_servers_lock_path: Path = self.state_dir / "mcp_servers.json.lock"

    def load_settings(self) -> WorkspaceSettings:
        """Load WorkspaceSettings from settings.json, returning defaults if absent."""
        with locked_file_access(self._settings_lock_path):
            if not self.settings_path.is_file():
                return WorkspaceSettings()
            if not verify_owner_only_permissions(self.settings_path):
                raise OpenDVError(
                    "settings.json failed owner-only permission verification.",
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                )
            try:
                raw = _read_utf8_with_retry(self.settings_path)
                return WorkspaceSettings.model_validate_json(raw)
            except (OSError, ValidationError, ValueError) as exc:
                raise OpenDVError(
                    f"Failed to load WorkspaceSettings from '{self.settings_path}': {exc}",
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                ) from exc

    def save_settings(self, settings: WorkspaceSettings) -> WorkspaceSettings:
        """Validate zero secrets, stamp updated_at, and persist settings.json atomically."""
        _validate_no_secrets_in_settings(settings)
        stamped = settings.model_copy(
            update={"updated_at": datetime.now(tz=UTC).isoformat()},
        )
        payload = json.dumps(stamped.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        with locked_file_access(self._settings_lock_path):
            atomic_write_text(self.settings_path, payload, owner_only=True)
        return stamped

    def load_mcp_servers(self) -> list[ExternalMcpServerSpec]:
        """Load registered ExternalMcpServerSpec entries from mcp_servers.json."""
        with locked_file_access(self._mcp_servers_lock_path):
            if not self.mcp_servers_path.is_file():
                return []
            if not verify_owner_only_permissions(self.mcp_servers_path):
                raise OpenDVError(
                    "mcp_servers.json failed owner-only permission verification.",
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                )
            try:
                raw = _read_utf8_with_retry(self.mcp_servers_path)
                parsed = json.loads(raw)
            except (OSError, json.JSONDecodeError) as exc:
                raise OpenDVError(
                    f"Failed to parse mcp_servers.json: {exc}",
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                ) from exc

        if isinstance(parsed, dict) and "servers" in parsed:
            items = parsed["servers"]
        elif isinstance(parsed, list):
            items = parsed
        else:
            raise OpenDVError(
                "mcp_servers.json must be a JSON array or {'servers': [...]} object.",
                error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            )

        if not isinstance(items, list):
            raise OpenDVError(
                "mcp_servers.json 'servers' field must be a list.",
                error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            )

        try:
            return [ExternalMcpServerSpec.model_validate(item) for item in items]
        except ValidationError as exc:
            raise OpenDVError(
                f"Invalid ExternalMcpServerSpec in mcp_servers.json: {exc}",
                error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            ) from exc

    def save_mcp_servers(self, servers: Sequence[ExternalMcpServerSpec]) -> None:
        """Validate unique server_ids and persist ExternalMcpServerSpec list atomically."""
        seen: set[str] = set()
        serialized: list[dict[str, object]] = []
        for spec in servers:
            if spec.server_id in seen:
                raise OpenDVError(
                    f"Duplicate ExternalMcpServerSpec server_id '{spec.server_id}'.",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                )
            seen.add(spec.server_id)
            serialized.append(spec.model_dump(mode="json"))

        payload = json.dumps({"schema_version": "2.0", "servers": serialized}, indent=2) + "\n"
        with locked_file_access(self._mcp_servers_lock_path):
            atomic_write_text(self.mcp_servers_path, payload, owner_only=True)


__all__ = ["SettingsStore"]
