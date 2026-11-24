"""OS filesystem mount guards for SQLite WAL and cross-OS dead-PID / process start checks."""

from __future__ import annotations

import ctypes
import ntpath
import os
import posixpath
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Final

from opendv.models.errors import ErrorCode, OpenDVError

_UNSAFE_WAL_FS_TYPES: Final[frozenset[str]] = frozenset(
    {
        "nfs",
        "nfs4",
        "cifs",
        "smb",
        "smb2",
        "smb3",
        "smbfs",
        "9p",
        "fuse.sshfs",
        "fuse.rclone",
    }
)
_WSL_MNT_RE: Final[re.Pattern[str]] = re.compile(r"^/mnt/[a-z](/|$)")
_DRIVE_REMOTE: Final[int] = 4
_DRIVE_CDROM: Final[int] = 5

if sys.platform == "win32":
    from ctypes import wintypes

    _PROCESS_QUERY_LIMITED_INFORMATION: Final[int] = 0x1000
    _STILL_ACTIVE: Final[int] = 259

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetDriveTypeW.restype = wintypes.UINT
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    _kernel32.GetProcessTimes.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL


def _unescape_proc_mount_field(raw: str) -> str:
    return (
        raw.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def _parse_proc_mounts(mounts_text: str) -> list[tuple[str, str]]:
    mounts: list[tuple[str, str]] = []
    for line in mounts_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if len(fields) >= 3:
            mounts.append((_unescape_proc_mount_field(fields[1]), fields[2].lower()))
    return mounts


def _resolve_nearest_existing_realpath(path: Path) -> str:
    cur = path
    tail: list[str] = []
    while not cur.exists():
        parent = cur.parent
        if parent == cur:
            break
        tail.append(cur.name)
        cur = parent
    base = os.path.realpath(os.fspath(cur))
    if not tail:
        return base
    return os.path.join(base, *reversed(tail))


def _default_win32_drive_type(root: str) -> int:
    if sys.platform == "win32":
        return int(_kernel32.GetDriveTypeW(root))
    return 3


def _check_win32_wal_path(
    raw_path: str,
    *,
    native_os: bool,
    drive_type_fn: Callable[[str], int] | None,
) -> Path:
    raw_norm = raw_path.replace("/", "\\").upper()
    if raw_norm.startswith("\\\\?\\UNC\\"):
        raise OpenDVError(
            f"UNC path '{raw_path}' is unsafe for SQLite WAL.",
            error_code=ErrorCode.UNSAFE_WAL_FILESYSTEM,
        )
    if raw_path.startswith(("\\\\", "//")) and not raw_path.startswith("\\\\?\\"):
        raise OpenDVError(
            f"UNC network share '{raw_path}' is unsafe for SQLite WAL.",
            error_code=ErrorCode.UNSAFE_WAL_FILESYSTEM,
        )

    resolved_str = (
        _resolve_nearest_existing_realpath(Path(raw_path))
        if native_os
        else ntpath.normpath(raw_path.replace("/", "\\"))
    )
    resolved_norm = resolved_str.replace("/", "\\").upper()
    if resolved_norm.startswith("\\\\?\\UNC\\") or (
        resolved_str.startswith(("\\\\", "//")) and not resolved_str.startswith("\\\\?\\")
    ):
        raise OpenDVError(
            f"Resolved UNC path '{resolved_str}' is unsafe for SQLite WAL.",
            error_code=ErrorCode.UNSAFE_WAL_FILESYSTEM,
        )

    check_target = resolved_str[4:] if resolved_str.startswith("\\\\?\\") else resolved_str
    drive, _ = ntpath.splitdrive(check_target)
    if drive:
        root = drive + "\\"
        probe = drive_type_fn or _default_win32_drive_type
        dtype = probe(root)
        if dtype in (_DRIVE_REMOTE, _DRIVE_CDROM):
            raise OpenDVError(
                f"Drive '{root}' (type={dtype}) is a remote/optical mount unsafe for SQLite WAL.",
                error_code=ErrorCode.UNSAFE_WAL_FILESYSTEM,
            )

    return Path(resolved_str)


def _check_linux_wal_path(
    raw_path: str,
    *,
    native_os: bool,
    mounts_text: str | None,
) -> Path:
    norm_path = (
        _resolve_nearest_existing_realpath(Path(raw_path))
        if native_os
        else posixpath.normpath(raw_path.replace("\\", "/"))
    )

    text = mounts_text
    if text is None:
        proc_mounts = Path("/proc/mounts")
        if not proc_mounts.is_file():
            return Path(norm_path)
        try:
            text = proc_mounts.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return Path(norm_path)

    best_mp = ""
    best_fs = ""
    for mp, fs_type in _parse_proc_mounts(text):
        mp_clean = mp.rstrip("/") or "/"
        matched = (
            norm_path == mp_clean
            or mp_clean == "/"
            or norm_path.startswith(mp_clean + "/")
        )
        if matched and len(mp_clean) >= len(best_mp):
            best_mp = mp_clean
            best_fs = fs_type

    if best_fs in _UNSAFE_WAL_FS_TYPES:
        raise OpenDVError(
            f"Path '{norm_path}' resides on '{best_fs}' mount '{best_mp}', unsafe for SQLite WAL.",
            error_code=ErrorCode.UNSAFE_WAL_FILESYSTEM,
        )
    if _WSL_MNT_RE.match(norm_path) and best_fs in {"drvfs", "9p"}:
        raise OpenDVError(
            f"WSL2 Windows mount '{norm_path}' ({best_fs}) is unsafe for SQLite WAL.",
            error_code=ErrorCode.UNSAFE_WAL_FILESYSTEM,
        )

    return Path(norm_path)


def check_wal_filesystem_safe(
    path: Path | str,
    *,
    mounts_text: str | None = None,
    drive_type_fn: Callable[[str], int] | None = None,
    platform_name: str | None = None,
) -> Path:
    """Validate that path is on a local filesystem safe for SQLite WAL locking."""
    raw_path = os.fspath(path)
    target_plat = sys.platform if platform_name is None else platform_name.lower()
    native_os = target_plat == sys.platform

    if target_plat.startswith("win") or target_plat == "nt":
        return _check_win32_wal_path(
            raw_path,
            native_os=native_os,
            drive_type_fn=drive_type_fn,
        )
    if target_plat.startswith("linux"):
        return _check_linux_wal_path(
            raw_path,
            native_os=native_os,
            mounts_text=mounts_text,
        )
    return Path(_resolve_nearest_existing_realpath(Path(raw_path)))


def parse_linux_proc_stat(stat_text: str) -> tuple[str, int] | None:
    """Parse (state, starttime_ticks) from Linux /proc/<pid>/stat safely using rfind(')')."""
    rparen = stat_text.rfind(")")
    if rparen == -1:
        return None
    tail = stat_text[rparen + 1 :].strip()
    fields = tail.split()
    if len(fields) <= 19:
        return None
    try:
        return fields[0], int(fields[19])
    except ValueError:
        return None


def get_process_start_ts(pid: int) -> int:
    """Return OS-monotonic process start timestamp (0 if process is dead or inaccessible)."""
    if pid <= 0:
        return 0

    if sys.platform.startswith("linux"):
        stat_path = Path(f"/proc/{pid}/stat")
        try:
            parsed = parse_linux_proc_stat(stat_path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return 0
        if parsed is None:
            return 0
        state, start_ticks = parsed
        if state in ("Z", "X"):
            return 0
        return start_ticks

    if sys.platform == "win32":
        handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return 0
        try:
            exit_code = wintypes.DWORD(0)
            if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return 0
            if exit_code.value != _STILL_ACTIVE:
                return 0
            creation = wintypes.FILETIME()
            exit_t = wintypes.FILETIME()
            kernel_t = wintypes.FILETIME()
            user_t = wintypes.FILETIME()
            if not _kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_t),
                ctypes.byref(kernel_t),
                ctypes.byref(user_t),
            ):
                return 0
            return int((creation.dwHighDateTime << 32) | creation.dwLowDateTime)
        finally:
            _kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
    except OSError:
        return 0
    return 1


def is_process_instance_alive(pid: int, expected_start_ts: int) -> bool:
    """Verify PID is alive and matches the recorded process_start_ts (prevents PID reuse)."""
    if pid <= 0 or expected_start_ts <= 0:
        return False
    current_ts = get_process_start_ts(pid)
    return current_ts != 0 and current_ts == expected_start_ts


__all__ = [
    "check_wal_filesystem_safe",
    "get_process_start_ts",
    "is_process_instance_alive",
    "parse_linux_proc_stat",
]
