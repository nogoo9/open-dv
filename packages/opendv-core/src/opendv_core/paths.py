"""Canonical workspace path containment and resolution utilities (DUP-04)."""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from pathlib import Path

from opendv_core.schema_registry import find_workspace_root


class PathContainmentError(ValueError):
    """Raised when a path violates workspace directory containment or attempts path traversal."""

    pass


def resolve_workspace_path(
    path: str | Path,
    *,
    workspace_root: Path | None = None,
    base_dir: Path | None = None,
    allowed_roots: Sequence[Path] | None = None,
    allow_temp: bool = True,
) -> Path:
    """Resolve a candidate path and enforce containment within permitted root directories.

    Security Invariants (T4 Traversal Compliance):
    1. Rejects empty or whitespace-only paths.
    2. Strictly forbids path traversal tokens ('..') in any part of the path.
    3. Resolves relative paths against `base_dir`, `workspace_root`, or candidate `allowed_roots`.
    4. For absolute paths, verifies that the resolved path is contained within `workspace_root`,
       `base_dir`, or any path in `allowed_roots` (and the operating system temp directory
       if `allow_temp=True`).
    5. Returns a fully resolved, normalized `pathlib.Path`.
    6. Raises `PathContainmentError` on any containment or traversal violation.
    """
    raw_str = str(path).strip()
    if not raw_str:
        raise PathContainmentError("Path must not be empty.")

    candidate = Path(raw_str)
    if ".." in candidate.parts:
        raise PathContainmentError(f"Path traversal ('..') is strictly forbidden: '{raw_str}'")

    root = (workspace_root or find_workspace_root()).resolve()
    permitted: set[Path] = {root}
    if base_dir is not None:
        permitted.add(base_dir.resolve())
    if allowed_roots:
        permitted.update(r.resolve() for r in allowed_roots)
    if allow_temp:
        permitted.add(Path(tempfile.gettempdir()).resolve())

    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        cand_list: list[Path] = []
        if base_dir is not None:
            cand_list.append((base_dir / candidate).resolve())
        cand_list.append((root / candidate).resolve())
        if allowed_roots:
            for extra_root in allowed_roots:
                cand_list.append((extra_root / candidate).resolve())

        resolved_match = None
        for cand in cand_list:
            if cand.exists():
                resolved_match = cand
                break
        resolved = resolved_match if resolved_match is not None else cand_list[0]

    if not any(resolved.is_relative_to(p) for p in permitted):
        raise PathContainmentError(
            f"Path '{raw_str}' is outside allowed workspace directories: resolves to '{resolved}', "
            f"escaping permitted workspace roots: {[str(p) for p in sorted(permitted)]}"
        )

    return resolved
