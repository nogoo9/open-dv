"""Unified path resolution and credential redaction for source adapters."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from opendv_core.paths import resolve_workspace_path

_CREDENTIAL_PATTERN = re.compile(r"([^/:@\s]*):([^/:@\s]+)@")


def redact_credentials(text: str) -> str:
    """Mask userinfo password/token segments in URIs and error strings."""
    if not text:
        return text
    return _CREDENTIAL_PATTERN.sub(r"\1:***@", text)


def resolve_adapter_path(
    raw_path: str,
    adapter: Any,
    *,
    allow_temp: bool | None = None,
) -> Path:
    """Resolve a path strictly within the adapter's server-authorized workspace root.

    Client options cannot supply base_dir or override workspace boundaries.
    """
    clean_path = raw_path.strip()
    ws_root = getattr(adapter, "workspace_root", None)
    if ws_root is None:
        ws_root = Path.cwd()
    adapter_allow_temp = bool(getattr(adapter, "allow_temp", False))
    eff_allow_temp = allow_temp if allow_temp is not None else adapter_allow_temp
    allowed_roots = getattr(adapter, "allowed_roots", None)

    return resolve_workspace_path(
        clean_path,
        workspace_root=ws_root,
        base_dir=None,
        allowed_roots=allowed_roots,
        allow_temp=eff_allow_temp,
    )


__all__ = ["redact_credentials", "resolve_adapter_path"]
