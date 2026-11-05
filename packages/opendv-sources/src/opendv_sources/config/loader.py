"""Consolidated structured document loader for YAML and JSON configurations (SRC-05)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from opendv_core.paths import resolve_workspace_path
from opendv_sources.exceptions import FormatError, SourceNotFoundError


def load_structured_document(
    path_or_content: str | Path,
    *,
    workspace_root: Path | None = None,
    base_dir: Path | None = None,
    allowed_roots: Sequence[Path] | None = None,
    allow_temp: bool = True,
) -> dict[str, Any]:
    """Deterministically load a YAML or JSON mapping document with workspace containment.

    Consolidates duplicated logic across the codebase into a single canonical entrypoint.

    Args:
        path_or_content: File path (Path or str) or inline JSON/YAML string.
        workspace_root: Workspace root directory for relative path containment.
        base_dir: Optional base directory to resolve relative paths against.
        allowed_roots: Additional allowed directories for path containment.
        allow_temp: Whether to permit paths inside the system temp directory.

    Returns:
        Loaded mapping as a Python dictionary.

    Raises:
        PathContainmentError: If path violates workspace jail boundaries or contains '..'.
        SourceNotFoundError: If file path cannot be found.
        FormatError: If parsing fails or the root document is not a mapping.
    """
    if isinstance(path_or_content, str) and not path_or_content.strip():
        raise FormatError("Expected non-empty structured document mapping, got empty content.")

    is_path = isinstance(path_or_content, Path) or (
        isinstance(path_or_content, str)
        and "\n" not in path_or_content
        and not path_or_content.strip().startswith(("{", "["))
    )

    if is_path:
        file_path = resolve_workspace_path(
            path_or_content,
            workspace_root=workspace_root,
            base_dir=base_dir,
            allowed_roots=allowed_roots,
            allow_temp=allow_temp,
        )
        if not file_path.is_file():
            raise SourceNotFoundError(f"Config document file not found: '{file_path}'")
        raw_text = file_path.read_text(encoding="utf-8")
        is_json = file_path.suffix.lower() == ".json"
    else:
        raw_text = str(path_or_content).strip()
        is_json = raw_text.startswith(("{", "["))

    if not raw_text:
        raise FormatError("Expected non-empty structured document mapping, got empty content.")

    try:
        loaded = json.loads(raw_text) if is_json else yaml.safe_load(raw_text)
    except Exception as exc:
        raise FormatError(f"Failed to parse structured document: {exc}") from exc

    if not isinstance(loaded, dict):
        raise FormatError(f"Expected object mapping, got {type(loaded).__name__}")
    return loaded


__all__ = ["load_structured_document"]
