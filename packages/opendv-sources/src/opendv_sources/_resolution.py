"""Unified path resolution and credential redaction for source adapters."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from opendv_core.paths import resolve_workspace_path


def _redact_token(tok: str) -> str:
    if "@" not in tok or ":" not in tok:
        return tok
    scheme_idx = tok.find("://")
    prefix = tok[: scheme_idx + 3] if scheme_idx >= 0 else ""
    rest = tok[scheme_idx + 3 :] if scheme_idx >= 0 else tok
    at_idx = rest.find("@")
    if at_idx < 0:
        return tok
    userinfo = rest[:at_idx]
    if "/" in userinfo or ":" not in userinfo:
        return tok
    user, _ = userinfo.split(":", 1)
    return f"{prefix}{user}:***@{rest[at_idx + 1 :]}"


def redact_credentials(text: str) -> str:
    """Mask userinfo password/token segments in URIs and error strings."""
    if not text or "@" not in text:
        return text
    return "".join(
        _redact_token(part) if not part.isspace() else part
        for part in re.split(r"(\s+)", text)
    )


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
