"""Canonical workspace path containment and resolution utilities (DUP-04)."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from pathlib import Path


class PathContainmentError(ValueError):
    """Raised when a path violates workspace directory containment or attempts path traversal."""

    pass


def _match_permitted_root(cand_str: str, permitted_reals: list[str]) -> Path | None:
    cand_real = os.path.realpath(cand_str)
    cand_norm = os.path.normcase(cand_real)
    for p_real in permitted_reals:
        p_pfx = p_real if p_real.endswith(os.sep) else p_real + os.sep
        if cand_real == p_real or cand_norm == os.path.normcase(p_real):
            return Path(p_real)
        if cand_real.startswith(p_pfx):
            return Path(cand_real)
        p_norm_pfx = os.path.normcase(p_pfx)
        if cand_norm.startswith(p_norm_pfx):
            reanchored = os.path.realpath(os.path.join(p_real, cand_real[len(p_pfx) :]))
            if reanchored.startswith(p_pfx):
                return Path(reanchored)
    return None


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

    if "\0" in raw_str or "\x00" in raw_str:
        raise PathContainmentError(f"Path must not contain null bytes: '{raw_str}'")

    candidate = Path(raw_str)
    if ".." in candidate.parts:
        raise PathContainmentError(f"Path traversal ('..') is strictly forbidden: '{raw_str}'")

    if workspace_root is None and base_dir is None and not allowed_roots:
        raise PathContainmentError(
            "workspace_root or base_dir must be provided for path resolution (REQ-CFG-01). "
            "Ambient repository root fallback is disabled."
        )

    permitted: set[Path] = set()
    if workspace_root is not None:
        permitted.add(workspace_root.resolve())
    if base_dir is not None:
        permitted.add(base_dir.resolve())
    if allowed_roots:
        permitted.update(r.resolve() for r in allowed_roots)
    if allow_temp:
        permitted.add(Path(tempfile.gettempdir()).resolve())

    permitted_reals = [os.path.realpath(str(p)) for p in permitted]

    if candidate.is_absolute():
        matched = _match_permitted_root(str(candidate), permitted_reals)
        if matched is not None:
            return matched
        resolved_preview = os.path.realpath(str(candidate))
    else:
        cand_list: list[Path] = []
        if base_dir is not None:
            cand_list.append(base_dir / candidate)
        if workspace_root is not None:
            cand_list.append(workspace_root / candidate)
        if allowed_roots:
            for extra_root in allowed_roots:
                cand_list.append(extra_root / candidate)

        for cand in cand_list:
            matched = _match_permitted_root(str(cand), permitted_reals)
            if matched is not None and matched.exists():
                return matched

        fallback = _match_permitted_root(str(cand_list[0]), permitted_reals)
        if fallback is not None:
            return fallback
        resolved_preview = os.path.realpath(str(cand_list[0]))

    raise PathContainmentError(
        f"Path '{raw_str}' is outside allowed workspace directories: "
        f"resolves to '{resolved_preview}', "
        f"escaping permitted workspace roots: {[str(p) for p in sorted(permitted)]}"
    )
