"""Cross-OS user state resolution, WAL mount safety, owner-only ACLs, and workspace boundaries."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.project import WINDOWS_RESERVED_NAMES
from opendv.service._os_acl import (
    atomic_write_bytes,
    atomic_write_text,
    replace_with_retry,
    set_owner_only_permissions,
    verify_owner_only_permissions,
)
from opendv.service._os_locks import locked_file_access
from opendv.service._os_proc_fs import (
    check_wal_filesystem_safe,
    get_process_start_ts,
    is_process_instance_alive,
    parse_linux_proc_stat,
)

_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")


def resolve_user_state_dir(
    state_dir: Path | str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    platform_name: str | None = None,
) -> Path:
    """Resolve <OS_USER_STATE>/opendv/ purely from env vars and tempfile fallback."""
    if state_dir is not None:
        return Path(state_dir)

    active_env = os.environ if env is None else env
    os_name = os.name if platform_name is None else platform_name

    if os_name == "nt":
        local_app = active_env.get("LOCALAPPDATA", "").strip()
        if local_app:
            return Path(local_app) / "opendv"
        user_profile = active_env.get("USERPROFILE", "").strip()
        if user_profile:
            return Path(user_profile) / "AppData" / "Local" / "opendv"
        home = active_env.get("HOME", "").strip()
        if home:
            return Path(home) / "AppData" / "Local" / "opendv"
        return Path(tempfile.gettempdir()) / "opendv-state"

    xdg_state = active_env.get("XDG_STATE_HOME", "").strip()
    if xdg_state:
        return Path(xdg_state) / "opendv"
    home = active_env.get("HOME", "").strip()
    if home:
        return Path(home) / ".local" / "state" / "opendv"
    return Path(tempfile.gettempdir()) / "opendv-state"


def resolve_opendv_home(
    workspace_root: Path | str | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Resolve <OPENDV_HOME> from explicit workspace_root, OPENDV_HOME env, or <cwd>/.opendv/."""
    if workspace_root is not None:
        ws = Path(workspace_root)
        return ws if ws.name == ".opendv" else ws / ".opendv"

    active_env = os.environ if env is None else env
    env_home = active_env.get("OPENDV_HOME", "").strip()
    if env_home:
        return Path(env_home)
    return Path.cwd() / ".opendv"


def validate_workspace_containment(
    workspace_path: Path | str,
    allowed_roots: Sequence[Path | str] | None = None,
    *,
    is_remote_request: bool = False,
) -> Path:
    """Validate a local path against traversal, UNC, ADS, reserved names, and allowed roots."""
    if is_remote_request:
        raise OpenDVError(
            "Server filesystem paths are forbidden on remote requests.",
            error_code=ErrorCode.LOOPBACK_ORIGIN_FORBIDDEN,
        )

    raw = os.fspath(workspace_path)
    if not raw or not raw.strip():
        raise OpenDVError(
            "Workspace path cannot be empty.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    if "\x00" in raw or any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise OpenDVError(
            "Workspace path contains forbidden control or null bytes.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    if raw.startswith(("\\\\", "//")):
        raise OpenDVError(
            f"UNC workspace paths are forbidden: '{raw}'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    remainder = raw[2:] if _DRIVE_PREFIX_RE.match(raw) else raw
    if ":" in remainder:
        raise OpenDVError(
            f"Workspace path contains forbidden NTFS Alternate Data Stream ':': '{raw}'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    for seg in remainder.replace("\\", "/").split("/"):
        if not seg:
            continue
        stem = seg.split(".")[0].lower()
        if seg.lower() in WINDOWS_RESERVED_NAMES or stem in WINDOWS_RESERVED_NAMES:
            raise OpenDVError(
                f"Workspace path targets forbidden Windows reserved device '{seg}'.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )

    try:
        resolved_str = os.path.realpath(raw)
    except (OSError, ValueError) as exc:
        raise OpenDVError(
            f"Invalid workspace path '{raw}': {exc}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        ) from exc

    if resolved_str.startswith("\\\\?\\UNC\\") or (
        resolved_str.startswith(("\\\\", "//")) and not resolved_str.startswith("\\\\?\\")
    ):
        raise OpenDVError(
            f"Resolved workspace path targets forbidden UNC share: '{resolved_str}'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    effective_roots: list[Path] = []
    if allowed_roots is not None:
        for r in allowed_roots:
            r_str = os.fspath(r).strip()
            if r_str:
                effective_roots.append(Path(os.path.realpath(r_str)))
    else:
        env_val = os.environ.get("OPENDV_WORKSPACE_ROOTS", "").strip()
        if env_val:
            for item in env_val.split(os.pathsep):
                cleaned = item.strip()
                if cleaned:
                    effective_roots.append(Path(os.path.realpath(cleaned)))
        else:
            effective_roots.append(Path(os.path.realpath(os.getcwd())))

    if not effective_roots:
        raise OpenDVError(
            "No allowed workspace roots configured.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    cand_norm = os.path.normcase(resolved_str)
    for root in effective_roots:
        root_str = os.fspath(root)
        root_norm = os.path.normcase(root_str)
        try:
            common = os.path.commonpath([cand_norm, root_norm])
        except ValueError:
            continue
        if common == root_norm:
            return Path(resolved_str)

    raise OpenDVError(
        f"Workspace path '{workspace_path}' escapes configured OPENDV_WORKSPACE_ROOTS boundary.",
        error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
    )


__all__ = [
    "atomic_write_bytes",
    "atomic_write_text",
    "check_wal_filesystem_safe",
    "get_process_start_ts",
    "is_process_instance_alive",
    "locked_file_access",
    "parse_linux_proc_stat",
    "replace_with_retry",
    "resolve_opendv_home",
    "resolve_user_state_dir",
    "set_owner_only_permissions",
    "validate_workspace_containment",
    "verify_owner_only_permissions",
]
