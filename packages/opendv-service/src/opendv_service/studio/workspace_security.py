"""Workspace security, containment, and HKDF-SHA256 key derivation for Open-DV Ontology Studio.

Implements Task 4D.1 / Issue #208 and ADR-009 / ADR-016:
- Persists 256-bit CSPRNG root workspace secret in `.odv_workspace_secret`.
- Configures owner-only permissions (0600/0700 on POSIX, atomic locale-independent
  protected DACL via Win32 advapi32 on Windows).
- Enforces ADR-016 §2 workspace path containment (`OPENDV_WORKSPACE_ROOTS`,
  `realpath` + `normcase` + `commonpath`, UNC/ADS/reserved-device rejection,
  and remote `server_path` prohibition).
- Derives purpose-isolated 256-bit keys via RFC 5869 HKDF-SHA256:
    * K_pseudo: LLM prompt scrub and HMAC pseudonymization
    * K_audit: Write-ahead audit ledger forward HMAC chaining
    * K_waiver: Administrative steward waiver HMAC signing
"""

from __future__ import annotations

import contextlib
import csv
import ctypes
import functools
import hashlib
import hmac
import io
import math
import os
import random
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from opendv_contracts.errors import ErrorCode, OpenDVError

WORKSPACE_SECRET_FILENAME: Final[str] = ".odv_workspace_secret"
DEFAULT_STUDIO_DIR: Final[str] = ".opendv/ontology_studio"

# Authoritative HKDF salts and info context strings per Task 4D.1 specification
DEFAULT_HKDF_SALT: Final[bytes] = b"opendv-salt-v1"
INFO_PSEUDONYMIZATION: Final[bytes] = b"opendv-ontology-studio-pseudonymization-v1"
INFO_AUDIT_CHAIN: Final[bytes] = b"opendv-ontology-studio-audit-chain-v1"
INFO_STEWARD_WAIVER: Final[bytes] = b"opendv-ontology-studio-steward-waiver-v1"

WINDOWS_RESERVED_DEVICES: Final[frozenset[str]] = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        "com1",
        "com2",
        "com3",
        "com4",
        "com5",
        "com6",
        "com7",
        "com8",
        "com9",
        "lpt1",
        "lpt2",
        "lpt3",
        "lpt4",
        "lpt5",
        "lpt6",
        "lpt7",
        "lpt8",
        "lpt9",
    }
)
_WINDOWS_RESERVED_DEVICES = WINDOWS_RESERVED_DEVICES

MAX_ACTIVE_NONCES: Final[int] = 256
MAX_ACTIVE_SESSIONS: Final[int] = 256
_AUTH_LOCK = threading.Lock()
_LAUNCH_NONCES: dict[str, tuple[str, str, float]] = {}
_ACTIVE_SESSIONS: dict[str, dict[str, Any]] = {}


def _evict_overflow_locked() -> None:
    """Evict earliest-expiring entries when maps exceed MAX_ACTIVE_* bounds."""
    while len(_LAUNCH_NONCES) > MAX_ACTIVE_NONCES:
        oldest_nonce = min(_LAUNCH_NONCES.items(), key=lambda kv: kv[1][2])[0]
        _LAUNCH_NONCES.pop(oldest_nonce, None)

    while len(_ACTIVE_SESSIONS) > MAX_ACTIVE_SESSIONS:
        oldest_tok = min(_ACTIVE_SESSIONS.items(), key=lambda kv: float(kv[1]["expires_at"]))[0]
        _ACTIVE_SESSIONS.pop(oldest_tok, None)


def _cleanup_expired_nonces_and_sessions(grace_period: float = 300.0) -> None:
    """Purge expired nonces and sessions and enforce capacity bounds in a thread-safe manner."""
    now = time.time()
    with _AUTH_LOCK:
        expired_nonces = [k for k, v in list(_LAUNCH_NONCES.items()) if now > v[2] + grace_period]
        for k in expired_nonces:
            _LAUNCH_NONCES.pop(k, None)

        expired_sessions = [k for k, v in list(_ACTIVE_SESSIONS.items()) if now > v["expires_at"]]
        for k in expired_sessions:
            _ACTIVE_SESSIONS.pop(k, None)

        _evict_overflow_locked()


def mint_launch_nonce(
    principal: str = "human:local_admin",
    role: str = "admin",
    ttl: float = 60.0,
) -> str:
    """Generate a single-use 256-bit launch nonce with 60s TTL and bounded map size."""
    _cleanup_expired_nonces_and_sessions()
    nonce = secrets.token_hex(32)
    with _AUTH_LOCK:
        _LAUNCH_NONCES[nonce] = (principal, role, time.time() + ttl)
        _evict_overflow_locked()
    return nonce


class InsecurePermissionsError(OpenDVError, RuntimeError):
    """Raised when studio root or secret file permissions fail security checks."""

    error_code = ErrorCode.INSECURE_STUDIO_ROOT_PERMISSIONS

    def __init__(
        self,
        message: str,
        error_code: str | ErrorCode = ErrorCode.INSECURE_STUDIO_ROOT_PERMISSIONS,
    ) -> None:
        code_enum = (
            error_code
            if isinstance(error_code, ErrorCode)
            else ErrorCode(error_code)
            if error_code in ErrorCode._value2member_map_
            else ErrorCode.INSECURE_STUDIO_ROOT_PERMISSIONS
        )
        OpenDVError.__init__(self, message, error_code=code_enum)
        self.error_code = code_enum
        self.status_code = self.http_status


class WorkspaceSecretCorruptError(OpenDVError, RuntimeError):
    """Raised when .odv_workspace_secret is corrupted or truncated (fails closed)."""

    error_code = ErrorCode.CORRUPT_SESSION_RECORD

    def __init__(
        self,
        message: str,
        error_code: str | ErrorCode = ErrorCode.CORRUPT_SESSION_RECORD,
    ) -> None:
        RuntimeError.__init__(self, message)
        code_enum = (
            error_code
            if isinstance(error_code, ErrorCode)
            else ErrorCode(error_code)
            if error_code in ErrorCode._value2member_map_
            else ErrorCode.CORRUPT_SESSION_RECORD
        )
        OpenDVError.__init__(self, message, error_code=code_enum)
        self.error_code = code_enum
        self.status_code = 500
        self.http_status = 500
        self.exit_code = 1


class InvalidWorkspacePathError(OpenDVError, ValueError):
    """Raised when a workspace path fails containment or syntax checks (HTTP 400 / exit 2)."""

    error_code = ErrorCode.INVALID_WORKSPACE_PATH

    def __init__(
        self,
        message: str,
        error_code: str | ErrorCode = ErrorCode.INVALID_WORKSPACE_PATH,
        status_code: int = 400,
        exit_code: int = 2,
    ) -> None:
        code_enum = (
            error_code if isinstance(error_code, ErrorCode) else ErrorCode.INVALID_WORKSPACE_PATH
        )
        OpenDVError.__init__(self, message, error_code=code_enum)
        self.status_code = status_code
        self.http_status = status_code
        self.exit_code = exit_code


class RemoteServerPathForbiddenError(OpenDVError, PermissionError):
    """Raised when a remote caller supplies a server-side filesystem path (HTTP 400 / exit 2)."""

    error_code = ErrorCode.REMOTE_SERVER_PATH_FORBIDDEN

    def __init__(
        self,
        message: str = "Server filesystem paths are forbidden on remote requests.",
        error_code: str | ErrorCode = ErrorCode.REMOTE_SERVER_PATH_FORBIDDEN,
        status_code: int = 400,
        exit_code: int = 2,
    ) -> None:
        code_enum = (
            error_code
            if isinstance(error_code, ErrorCode)
            else ErrorCode.REMOTE_SERVER_PATH_FORBIDDEN
        )
        OpenDVError.__init__(self, message, error_code=code_enum)
        self.status_code = status_code
        self.http_status = status_code
        self.exit_code = exit_code


def replace_with_retry(
    src: Path,
    dst: Path,
    max_timeout: float = 5.0,
    initial_delay: float = 0.010,
    max_delay: float = 0.250,
) -> None:
    """Atomically replace dst with src, retrying transient Windows sharing violations.

    On Windows, concurrent indexing, antivirus, or file handles can raise WinError 32
    (ERROR_SHARING_VIOLATION) or WinError 5 (ERROR_ACCESS_DENIED).
    Implements truncated exponential backoff with jitter between 10ms and 250ms.
    """
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


def hkdf_extract(salt: bytes | None, ikm: bytes) -> bytes:
    """RFC 5869 HKDF-Extract using HMAC-SHA256."""
    if not salt:
        salt = bytes(32)
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def hkdf_expand(prk: bytes, info: bytes, length: int = 32) -> bytes:
    """RFC 5869 HKDF-Expand using HMAC-SHA256."""
    hash_len = 32
    if length > 255 * hash_len:
        raise ValueError(f"Cannot expand to {length} bytes: exceeds 255 * {hash_len}")

    n = math.ceil(length / hash_len)
    okm = b""
    t = b""
    for i in range(1, n + 1):
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        okm += t
    return okm[:length]


def hkdf_sha256(ikm: bytes, salt: bytes | None, info: bytes, length: int = 32) -> bytes:
    """RFC 5869 HKDF using SHA-256 (Extract then Expand)."""
    prk = hkdf_extract(salt, ikm)
    return hkdf_expand(prk, info, length)


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
            # TOKEN_USER starts with SID_AND_ATTRIBUTES { PSID Sid; DWORD Attributes; }
            psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
            if not psid:
                return ""
            return _psid_to_string(psid) or ""
        finally:
            _kernel32.CloseHandle(tok)


@functools.lru_cache(maxsize=1)
def _get_current_identity() -> tuple[str, str]:
    """Resolve current user's (domain_qualified_name, sid) on Windows or POSIX (cached)."""
    if os.name != "nt":
        user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
        return user, ""

    user = os.environ.get("USERNAME") or ""
    domain = os.environ.get("USERDOMAIN") or ""
    qualified = f"{domain}\\{user}" if domain and user else user

    if sys.platform == "win32":
        sid = _get_win32_process_user_sid()
        if sid:
            return qualified, sid

    try:
        proc = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            reader = csv.reader(io.StringIO(proc.stdout.strip()))
            row = next(reader, [])
            if len(row) >= 2:
                return row[0].strip(), row[1].strip()
    except OSError:
        pass
    return qualified, ""


def set_owner_only_permissions(path: Path | str) -> None:
    """Set strict owner-only permissions on a file or directory.

    On Windows: Atomically sets a protected DACL via Win32 SetNamedSecurityInfoW
    (disabling inheritance without any transient /reset window and granting Full
    Control solely to the current token user SID, SYSTEM S-1-5-18, and
    Administrators S-1-5-32-544).
    On POSIX: Uses chmod (0700 for directories, 0600 for files).

    Raises:
        InsecurePermissionsError: If setting permissions fails on Windows or POSIX.
    """
    target = Path(path)
    if not target.exists():
        return

    if sys.platform == "win32":
        _, user_sid = _get_current_identity()
        if not user_sid:
            raise InsecurePermissionsError(f"Cannot resolve current user SID for '{target.name}'")

        sid_strings = [user_sid, "S-1-5-18", "S-1-5-32-544"]
        psids: list[ctypes.c_void_p] = []
        try:
            total_sid_bytes = 0
            for sid_str in sid_strings:
                psid = ctypes.c_void_p()
                if not _advapi32.ConvertStringSidToSidW(sid_str, ctypes.byref(psid)):
                    err_code = ctypes.get_last_error()
                    raise InsecurePermissionsError(
                        f"ConvertStringSidToSidW failed for '{sid_str}' (winerror={err_code})"
                    )
                psids.append(psid)
                total_sid_bytes += int(_advapi32.GetLengthSid(psid))

            # ACL header (8 bytes) + 3 * ACCESS_ALLOWED_ACE overhead (12 bytes each) + padding
            acl_size = ((8 + len(psids) * 16 + total_sid_bytes + 31) // 4) * 4
            acl_buf = ctypes.create_string_buffer(acl_size)
            if not _advapi32.InitializeAcl(acl_buf, acl_size, _ACL_REVISION):
                err_code = ctypes.get_last_error()
                raise InsecurePermissionsError(
                    f"InitializeAcl failed on '{target.name}' (winerror={err_code})"
                )

            ace_flags = (_OBJECT_INHERIT_ACE | _CONTAINER_INHERIT_ACE) if target.is_dir() else 0
            for psid in psids:
                if not _advapi32.AddAccessAllowedAceEx(
                    acl_buf, _ACL_REVISION, ace_flags, _FILE_ALL_ACCESS, psid
                ):
                    err_code = ctypes.get_last_error()
                    raise InsecurePermissionsError(
                        f"AddAccessAllowedAceEx failed on '{target.name}' (winerror={err_code})"
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
                raise InsecurePermissionsError(
                    f"SetNamedSecurityInfoW failed on '{target.name}' (rc={rc})"
                )
        finally:
            for psid in psids:
                if psid.value:
                    _kernel32.LocalFree(psid)
    else:
        mode = 0o700 if target.is_dir() else 0o600
        try:
            os.chmod(target, mode)
        except OSError as e:
            raise InsecurePermissionsError(
                f"Failed to set mode {oct(mode)} on '{target.name}': {e}"
            ) from e


def verify_owner_only_permissions(path: Path | str) -> bool:
    """Verify that a path is accessible only by the current owner (and OS system/admin).

    On POSIX: Asserts that group and others have no read/write/execute bits (0o077 == 0).
    On Windows: Locale-independently inspects the security descriptor DACL via Win32 advapi32:
      1. DACL must be non-null and SE_DACL_PROTECTED must be set (inheritance disabled).
      2. No ACE may carry INHERITED_ACE.
      3. Every ACE SID must belong to {current_user_sid, S-1-5-18, S-1-5-32-544}.
      4. The current user SID must hold Full Control (FILE_ALL_ACCESS or GENERIC_ALL).
    """
    target = Path(path)
    if not target.exists():
        return False

    if sys.platform == "win32":
        _, user_sid = _get_current_identity()
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
                # NULL DACL grants unrestricted access to Everyone
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
            owner_has_full_control = False

            for idx in range(acl_hdr.AceCount):
                p_ace = ctypes.c_void_p()
                if not _advapi32.GetAce(p_dacl, idx, ctypes.byref(p_ace)) or not p_ace.value:
                    return False

                ace_hdr = _AceHeader.from_address(p_ace.value)
                if ace_hdr.AceFlags & _INHERITED_ACE:
                    return False

                if ace_hdr.AceType != _ACCESS_ALLOWED_ACE_TYPE:
                    return False

                # ACCESS_ALLOWED_ACE layout: ACE_HEADER (4 bytes) + ACCESS_MASK (4 bytes) + SidStart
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
                    owner_has_full_control = True

            return owner_has_full_control
        finally:
            _kernel32.LocalFree(p_sd)

    try:
        mode = target.stat().st_mode
        return (mode & 0o077) == 0
    except OSError:
        return False


def validate_workspace_containment(
    workspace_path: Path | str,
    allowed_roots: Sequence[Path | str] | None = None,
    *,
    is_remote_request: bool = False,
) -> Path:
    """Validate and resolve a workspace path under ADR-016 §2 containment boundaries.

    Enforces:
    1. Rejects server-side filesystem paths on remote requests (`REMOTE_SERVER_PATH_FORBIDDEN`).
    2. Rejects empty paths, null/control characters, UNC shares (`\\\\` / `//`), NTFS
       Alternate Data Streams (`:` beyond Windows drive designator), and Windows reserved
       device names (`INVALID_WORKSPACE_PATH`).
    3. Resolves symlinks/junctions via `os.path.realpath` and verifies containment inside
       `allowed_roots` (defaulting to `OPENDV_WORKSPACE_ROOTS` split by `os.pathsep`, or
       current working directory when unset) using `os.path.normcase` + `os.path.commonpath`.
    """
    if is_remote_request:
        raise RemoteServerPathForbiddenError(
            "Supplying server_path / workspace filesystem paths is forbidden on remote requests."
        )

    if not isinstance(workspace_path, (str, Path)):
        raise InvalidWorkspacePathError("Workspace path must be a string or Path.")

    raw = str(workspace_path)
    if not raw or not raw.strip():
        raise InvalidWorkspacePathError("Workspace path cannot be empty.")

    if "\x00" in raw or any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise InvalidWorkspacePathError("Workspace path contains forbidden control or null bytes.")

    # Reject UNC network paths (\\server\share or //server/share)
    if raw.startswith(("\\\\", "//")):
        raise InvalidWorkspacePathError(f"UNC workspace paths are forbidden: '{raw}'")

    # Reject NTFS Alternate Data Streams (':' anywhere after optional drive letter 'C:')
    remainder = raw[2:] if re.match(r"^[A-Za-z]:", raw) else raw
    if ":" in remainder:
        raise InvalidWorkspacePathError(
            f"Workspace path contains forbidden NTFS Alternate Data Stream token ':': '{raw}'"
        )

    # Reject Windows reserved device names in any path segment
    normalized_slashes = remainder.replace("\\", "/")
    for seg in normalized_slashes.split("/"):
        if not seg:
            continue
        stem = seg.split(".")[0].lower()
        if seg.lower() in _WINDOWS_RESERVED_DEVICES or stem in _WINDOWS_RESERVED_DEVICES:
            raise InvalidWorkspacePathError(
                f"Workspace path targets forbidden Windows reserved device '{seg}'."
            )

    try:
        resolved_str = os.path.realpath(raw)
    except (OSError, ValueError) as exc:
        raise InvalidWorkspacePathError(f"Invalid workspace path '{raw}': {exc}") from exc

    if resolved_str.startswith("\\\\?\\UNC\\") or (
        resolved_str.startswith(("\\\\", "//")) and not resolved_str.startswith("\\\\?\\")
    ):
        raise InvalidWorkspacePathError(
            f"Resolved workspace path targets forbidden UNC share: '{resolved_str}'"
        )

    resolved = Path(resolved_str)

    # Resolve allowed workspace roots
    effective_roots: list[Path] = []
    if allowed_roots is not None:
        for r in allowed_roots:
            r_str = str(r).strip()
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
        raise InvalidWorkspacePathError("No allowed workspace roots configured.")

    cand_norm = os.path.normcase(str(resolved))
    for root in effective_roots:
        root_norm = os.path.normcase(str(root))
        try:
            common = os.path.commonpath([cand_norm, root_norm])
        except ValueError:
            # Different drives on Windows
            continue
        if common == root_norm:
            return resolved

    raise InvalidWorkspacePathError(
        f"Workspace path '{workspace_path}' escapes configured OPENDV_WORKSPACE_ROOTS boundary."
    )


@dataclass(frozen=True)
class DerivedKeys:
    """Purpose-isolated 256-bit cryptographic keys derived via HKDF-SHA256."""

    k_pseudo: bytes
    k_audit: bytes
    k_waiver: bytes

    @property
    def K_pseudo(self) -> bytes:  # noqa: N802
        """Alias for k_pseudo (LLM prompt scrub / HMAC pseudonymization)."""
        return self.k_pseudo

    @property
    def K_audit(self) -> bytes:  # noqa: N802
        """Alias for k_audit (forward-only audit HMAC chaining)."""
        return self.k_audit

    @property
    def K_waiver(self) -> bytes:  # noqa: N802
        """Alias for k_waiver (steward grounding waiver signing)."""
        return self.k_waiver

    def __repr__(self) -> str:
        """Prevent cryptographic keys from being exposed in logs or exception tracebacks."""
        return "DerivedKeys(K_pseudo=b'***', K_audit=b'***', K_waiver=b'***')"


class WorkspaceSecurityManager:
    """Manages workspace secret generation and purpose-isolated key derivation."""

    def __init__(
        self,
        studio_root: Path | str | None = None,
        salt: bytes = DEFAULT_HKDF_SALT,
    ) -> None:
        if studio_root is None:
            configured = os.environ.get("OPENDV_STUDIO_ROOT", DEFAULT_STUDIO_DIR)
            self.studio_root = Path(configured).resolve()
        else:
            self.studio_root = Path(studio_root).resolve()

        self.secret_path = self.studio_root / WORKSPACE_SECRET_FILENAME
        self._salt = salt

        # Initialize root secret and derive purpose keys
        self._root_secret = self._load_or_create_root_secret()
        self._derived_keys = self._derive_keys()

    def _load_or_create_root_secret(self) -> bytes:
        """Load existing 32-byte root secret or atomically create one without overwrite races."""
        self.studio_root.mkdir(parents=True, exist_ok=True)
        set_owner_only_permissions(self.studio_root)

        max_retries = 10
        for attempt in range(max_retries):
            if self.secret_path.exists():
                if not verify_owner_only_permissions(self.secret_path):
                    set_owner_only_permissions(self.secret_path)
                    if not verify_owner_only_permissions(self.secret_path):
                        raise InsecurePermissionsError(
                            f"Secret file '{self.secret_path.name}' has insecure permissions."
                        )

                secret = self.secret_path.read_bytes()
                if len(secret) == 32:
                    return secret

                if attempt < max_retries - 1:
                    time.sleep(0.01)
                    continue

                raise WorkspaceSecretCorruptError(
                    f"Workspace secret file '{self.secret_path.name}' is corrupted "
                    f"(expected 32 bytes, got {len(secret)} bytes). Refusing to regenerate."
                )

            candidate = secrets.token_bytes(32)
            temp_path = self.studio_root / f".secret.tmp.{uuid.uuid4().hex}"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
            try:
                fd = os.open(str(temp_path), flags, 0o600)
                try:
                    os.write(fd, candidate)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                set_owner_only_permissions(temp_path)

                try:
                    os.link(str(temp_path), str(self.secret_path))
                    set_owner_only_permissions(self.secret_path)
                    return candidate
                except (FileExistsError, OSError):
                    if self.secret_path.exists():
                        continue
                    fd2 = os.open(str(self.secret_path), flags, 0o600)
                    try:
                        os.write(fd2, candidate)
                        os.fsync(fd2)
                    finally:
                        os.close(fd2)
                    set_owner_only_permissions(self.secret_path)
                    return candidate
            except FileExistsError:
                continue
            finally:
                with contextlib.suppress(OSError):
                    temp_path.unlink()

        secret = self.secret_path.read_bytes()
        if len(secret) != 32:
            raise WorkspaceSecretCorruptError(
                f"Workspace secret file '{self.secret_path.name}' is corrupted "
                f"(expected 32 bytes, got {len(secret)} bytes). Refusing to regenerate."
            )
        return secret

    def _derive_keys(self) -> DerivedKeys:
        """Derive purpose-isolated keys via RFC 5869 HKDF-SHA256."""
        k_pseudo = hkdf_sha256(self._root_secret, self._salt, INFO_PSEUDONYMIZATION, 32)
        k_audit = hkdf_sha256(self._root_secret, self._salt, INFO_AUDIT_CHAIN, 32)
        k_waiver = hkdf_sha256(self._root_secret, self._salt, INFO_STEWARD_WAIVER, 32)
        return DerivedKeys(k_pseudo=k_pseudo, k_audit=k_audit, k_waiver=k_waiver)

    def get_keys(self) -> DerivedKeys:
        """Return derived purpose-isolated keys without exposing raw root secret."""
        return self._derived_keys

    def get_pseudo_key(self) -> bytes:
        """Return purpose key for pseudonymization and LLM prompt scrubbing."""
        return self._derived_keys.k_pseudo

    def get_audit_key(self) -> bytes:
        """Return purpose key for HMAC write-ahead audit chaining."""
        return self._derived_keys.k_audit

    def get_waiver_key(self) -> bytes:
        """Return purpose key for steward grounding waiver HMAC signing."""
        return self._derived_keys.k_waiver

    def sign_audit_entry(self, entry_bytes: bytes) -> str:
        """Sign audit entry bytes with K_audit, returning hex digest."""
        return hmac.new(self._derived_keys.k_audit, entry_bytes, hashlib.sha256).hexdigest().lower()

    def verify_audit_signature(self, entry_bytes: bytes, signature: str) -> bool:
        """Constant-time verification of an audit entry HMAC signature under K_audit."""
        expected = self.sign_audit_entry(entry_bytes)
        return hmac.compare_digest((signature or "").lower(), expected)

    def sign_waiver(self, waiver_bytes: bytes) -> str:
        """Sign steward waiver bytes with K_waiver, returning hex digest."""
        return (
            hmac.new(self._derived_keys.k_waiver, waiver_bytes, hashlib.sha256).hexdigest().lower()
        )

    def verify_waiver_signature(self, waiver_bytes: bytes, signature: str) -> bool:
        """Constant-time verification of a steward waiver HMAC signature under K_waiver."""
        expected = self.sign_waiver(waiver_bytes)
        return hmac.compare_digest((signature or "").lower(), expected)

    def __repr__(self) -> str:
        """Sanitize repr to avoid leaking secrets."""
        return f"WorkspaceSecurityManager(studio_root={self.studio_root}, status=PROTECTED)"
