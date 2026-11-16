"""Unified path resolution and credential redaction for source adapters."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from opendv_core.paths import resolve_workspace_path


def _redact_segment(seg: str) -> str:
    at_idx = seg.rfind("@")
    if at_idx <= 0:
        return seg
    colon_idx = seg.find(":", 0, at_idx)
    if colon_idx <= 0:
        return seg
    return f"{seg[:colon_idx]}:***@{seg[at_idx + 1 :]}"


def redact_credentials(text: str) -> str:
    """Mask userinfo password/token segments in URIs, filesystem paths, and error strings."""
    if not text or "@" not in text or ":" not in text:
        return text
    return "".join(
        _redact_segment(part) if idx % 2 == 0 else part
        for idx, part in enumerate(re.split(r"([/\\'\"\s]+)", text))
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
