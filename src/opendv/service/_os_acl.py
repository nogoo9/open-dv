"""Owner-only ACL enforcement (Win32 advapi32 DACL + POSIX 0o600/0o700) and atomic file writes."""

from __future__ import annotations

import ctypes
import functools
import os
import random
import sys
import tempfile
import time
from pathlib import Path
from typing import Final

from opendv.models.errors import ErrorCode, OpenDVError

if sys.platform == "win32":
    from ctypes import wintypes

    _SE_FILE_OBJECT: Final[int] = 1
    _DACL_SECURITY_INFORMATION: Final[int] = 0x00000004
    _PROTECTED_DACL_SECURITY_INFORMATION: Final[int] = 0x80000000
    _SE_DACL_PROTECTED: Final[int] = 0x1000
    _ACL_REVISION: Final[int] = 2
    _ACCESS_ALLOWED_ACE_TYPE: Final[int] = 0
    _INHERITED_ACE: Final[int] = 0x10
    _OBJECT_INHERIT_ACE: Final[int] = 0x01
    _CONTAINER_INHERIT_ACE: Final[int] = 0x02
    _FILE_ALL_ACCESS: Final[int] = 0x001F01FF
    _GENERIC_ALL: Final[int] = 0x10000000
    _TOKEN_QUERY: Final[int] = 0x0008
    _TOKEN_USER_CLASS: Final[int] = 1

    class _AclStruct(ctypes.Structure):
        _fields_ = [
            ("AclRevision", wintypes.BYTE),
            ("Sbz1", wintypes.BYTE),
            ("AclSize", wintypes.WORD),
            ("AceCount", wintypes.WORD),
            ("Sbz2", wintypes.WORD),
        ]

    class _AceHeader(ctypes.Structure):
        _fields_ = [
            ("AceType", wintypes.BYTE),
            ("AceFlags", wintypes.BYTE),
            ("AceSize", wintypes.WORD),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    _kernel32.GetCurrentProcess.argtypes = []
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    _kernel32.LocalFree.restype = wintypes.HLOCAL

    _advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    _advapi32.OpenProcessToken.restype = wintypes.BOOL
    _advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.GetTokenInformation.restype = wintypes.BOOL
    _advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    _advapi32.ConvertStringSidToSidW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
    _advapi32.GetLengthSid.argtypes = [ctypes.c_void_p]
    _advapi32.GetLengthSid.restype = wintypes.DWORD
    _advapi32.InitializeAcl.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
    _advapi32.InitializeAcl.restype = wintypes.BOOL
    _advapi32.AddAccessAllowedAceEx.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    _advapi32.AddAccessAllowedAceEx.restype = wintypes.BOOL
    _advapi32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    _advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi32.GetSecurityDescriptorControl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
    _advapi32.GetAce.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _advapi32.GetAce.restype = wintypes.BOOL

    def _psid_to_string(psid: ctypes.c_void_p | int) -> str | None:
        str_ptr = ctypes.c_void_p()
        if not _advapi32.ConvertSidToStringSidW(psid, ctypes.byref(str_ptr)):
            return None
        try:
            return ctypes.wstring_at(str_ptr.value) if str_ptr.value else None
        finally:
            if str_ptr.value:
                _kernel32.LocalFree(str_ptr)

    @functools.lru_cache(maxsize=1)
    def _get_win32_process_user_sid() -> str:
        tok = wintypes.HANDLE()
        if not _advapi32.OpenProcessToken(
            _kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(tok)
        ):
            return ""
        try:
            needed = wintypes.DWORD(0)
            _advapi32.GetTokenInformation(tok, _TOKEN_USER_CLASS, None, 0, ctypes.byref(needed))
            if needed.value == 0:
                return ""
            buf = ctypes.create_string_buffer(needed.value)
            if not _advapi32.GetTokenInformation(
                tok, _TOKEN_USER_CLASS, buf, needed.value, ctypes.byref(needed)
            ):
                return ""
            psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
            if not psid:
                return ""
            return _psid_to_string(psid) or ""
        finally:
            _kernel32.CloseHandle(tok)


def replace_with_retry(
    src: Path,
    dst: Path,
    max_timeout: float = 5.0,
    initial_delay: float = 0.010,
    max_delay: float = 0.250,
) -> None:
    """Atomically replace dst with src, retrying transient Windows sharing violations."""
    start_time = time.monotonic()
    delay = initial_delay

    while True:
        try:
            os.replace(src, dst)
            return
        except OSError as exc:
            winerror = getattr(exc, "winerror", None)
            is_transient = sys.platform == "win32" and winerror in (32, 5)
            if not is_transient or (time.monotonic() - start_time >= max_timeout):
                raise
            jitter = random.uniform(0.005, 0.020)
            time.sleep(delay + jitter)
            delay = min(delay * 2.0, max_delay)


def set_owner_only_permissions(path: Path | str) -> None:
    """Set strict owner-only permissions on a file or directory (Win32 DACL or POSIX 0600/0700)."""
    target = Path(path)
    if not target.exists():
        return

    if sys.platform == "win32":
        user_sid = _get_win32_process_user_sid()
        if not user_sid:
            raise OpenDVError(
                f"Cannot resolve current user SID for '{target.name}'.",
                error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            )

        sid_strings = [user_sid, "S-1-5-18", "S-1-5-32-544"]
        psids: list[ctypes.c_void_p] = []
        try:
            total_sid_bytes = 0
            for sid_str in sid_strings:
                psid = ctypes.c_void_p()
                if not _advapi32.ConvertStringSidToSidW(sid_str, ctypes.byref(psid)):
                    err_code = ctypes.get_last_error()
                    raise OpenDVError(
                        f"ConvertStringSidToSidW failed for '{sid_str}' (winerror={err_code}).",
                        error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                    )
                psids.append(psid)
                total_sid_bytes += int(_advapi32.GetLengthSid(psid))

            acl_size = ((8 + len(psids) * 16 + total_sid_bytes + 31) // 4) * 4
            acl_buf = ctypes.create_string_buffer(acl_size)
            if not _advapi32.InitializeAcl(acl_buf, acl_size, _ACL_REVISION):
                err_code = ctypes.get_last_error()
                raise OpenDVError(
                    f"InitializeAcl failed on '{target.name}' (winerror={err_code}).",
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                )

            ace_flags = (_OBJECT_INHERIT_ACE | _CONTAINER_INHERIT_ACE) if target.is_dir() else 0
            for psid in psids:
                if not _advapi32.AddAccessAllowedAceEx(
                    acl_buf, _ACL_REVISION, ace_flags, _FILE_ALL_ACCESS, psid
                ):
                    err_code = ctypes.get_last_error()
                    raise OpenDVError(
                        f"AddAccessAllowedAceEx failed on '{target.name}' (winerror={err_code}).",
                        error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                    )

            rc = _advapi32.SetNamedSecurityInfoW(
                str(target),
                _SE_FILE_OBJECT,
                _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
                None,
                None,
                ctypes.cast(acl_buf, ctypes.c_void_p),
                None,
            )
            if rc != 0:
                raise OpenDVError(
                    f"SetNamedSecurityInfoW failed on '{target.name}' (rc={rc}).",
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                )
        finally:
            for psid in psids:
                if psid.value:
                    _kernel32.LocalFree(psid)
        return

    mode = 0o700 if target.is_dir() else 0o600
    try:
        os.chmod(target, mode)
    except OSError as exc:
        raise OpenDVError(
            f"Failed to set mode {oct(mode)} on '{target.name}': {exc}",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
        ) from exc


def verify_owner_only_permissions(path: Path | str) -> bool:
    """Return True if path is accessible solely by current owner (and OS SYSTEM/Admins)."""
    target = Path(path)
    if not target.exists():
        return False

    if sys.platform == "win32":
        user_sid = _get_win32_process_user_sid()
        if not user_sid:
            return False

        p_dacl = ctypes.c_void_p()
        p_sd = ctypes.c_void_p()
        rc = _advapi32.GetNamedSecurityInfoW(
            str(target),
            _SE_FILE_OBJECT,
            _DACL_SECURITY_INFORMATION,
            None,
            None,
            ctypes.byref(p_dacl),
            None,
            ctypes.byref(p_sd),
        )
        if rc != 0 or not p_sd.value:
            return False

        try:
            if not p_dacl.value:
                return False

            control = wintypes.WORD(0)
            revision = wintypes.DWORD(0)
            if not _advapi32.GetSecurityDescriptorControl(
                p_sd, ctypes.byref(control), ctypes.byref(revision)
            ):
                return False
            if (control.value & _SE_DACL_PROTECTED) == 0:
                return False

            acl_hdr = _AclStruct.from_address(p_dacl.value)
            allowed_sids = {user_sid.upper(), "S-1-5-18", "S-1-5-32-544"}
            owner_has_full = False

            for idx in range(acl_hdr.AceCount):
                p_ace = ctypes.c_void_p()
                if not _advapi32.GetAce(p_dacl, idx, ctypes.byref(p_ace)) or not p_ace.value:
                    return False

                ace_hdr = _AceHeader.from_address(p_ace.value)
                if (ace_hdr.AceFlags & _INHERITED_ACE) != 0:
                    return False
                if ace_hdr.AceType != _ACCESS_ALLOWED_ACE_TYPE:
                    return False

                mask = wintypes.DWORD.from_address(p_ace.value + 4).value
                psid_addr = ctypes.c_void_p(p_ace.value + 8)
                sid_str = _psid_to_string(psid_addr)
                if not sid_str:
                    return False

                sid_upper = sid_str.upper()
                if sid_upper not in allowed_sids:
                    return False
                if sid_upper == user_sid.upper() and (
                    (mask & _FILE_ALL_ACCESS) == _FILE_ALL_ACCESS or (mask & _GENERIC_ALL) != 0
                ):
                    owner_has_full = True

            return owner_has_full
        finally:
            _kernel32.LocalFree(p_sd)

    try:
        mode = target.stat().st_mode
    except OSError:
        return False
    return (mode & 0o077) == 0


def atomic_write_bytes(path: Path, data: bytes, *, owner_only: bool = True) -> None:
    """Atomically write bytes to path using temp file + retry replace + optional owner ACL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if owner_only and not verify_owner_only_permissions(path.parent):
        set_owner_only_permissions(path.parent)

    tmp_path: Path | None = None
    replaced = False
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as fh:
            tmp_path = Path(fh.name)
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if owner_only:
            set_owner_only_permissions(tmp_path)
        replace_with_retry(tmp_path, path)
        replaced = True
    except OSError as exc:
        raise OpenDVError(
            f"Failed to write file '{path.name}': {exc}",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
        ) from exc
    finally:
        if not replaced and tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def atomic_write_text(path: Path, content: str, *, owner_only: bool = True) -> None:
    """Atomically write UTF-8 text to path with optional owner-only permissions."""
    atomic_write_bytes(path, content.encode("utf-8"), owner_only=owner_only)


__all__ = [
    "atomic_write_bytes",
    "atomic_write_text",
    "replace_with_retry",
    "set_owner_only_permissions",
    "verify_owner_only_permissions",
]
