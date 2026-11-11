"""Workspace security and HKDF-SHA256 key derivation for Open-DV Ontology Studio.

Implements Task 4D.1 / Issue #208 and ADR-009 / ADR-016:
- Persists 256-bit CSPRNG root workspace secret in `.odv_workspace_secret`.
- Configures owner-only permissions (0600 on POSIX, owner-only DACL on Windows).
- Derives purpose-isolated 256-bit keys via RFC 5869 HKDF-SHA256:
    * K_pseudo: LLM prompt scrub and HMAC pseudonymization
    * K_audit: Write-ahead audit ledger forward HMAC chaining
    * K_waiver: Administrative steward waiver HMAC signing
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import hmac
import io
import math
import os
import secrets
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final

WORKSPACE_SECRET_FILENAME: Final[str] = ".odv_workspace_secret"
DEFAULT_STUDIO_DIR: Final[str] = ".opendv/ontology_studio"

# Authoritative HKDF salts and info context strings per Task 4D.1 specification
DEFAULT_HKDF_SALT: Final[bytes] = b"opendv-salt-v1"
INFO_PSEUDONYMIZATION: Final[bytes] = b"opendv-ontology-studio-pseudonymization-v1"
INFO_AUDIT_CHAIN: Final[bytes] = b"opendv-ontology-studio-audit-chain-v1"
INFO_STEWARD_WAIVER: Final[bytes] = b"opendv-ontology-studio-steward-waiver-v1"


class InsecurePermissionsError(RuntimeError):
    """Raised when studio root or secret file permissions fail security checks."""

    def __init__(self, message: str, error_code: str = "INSECURE_STUDIO_ROOT_PERMISSIONS") -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = 500
        self.exit_code = 1


class WorkspaceSecretCorruptError(RuntimeError):
    """Raised when .odv_workspace_secret is corrupted or truncated (fails closed)."""

    def __init__(self, message: str, error_code: str = "WORKSPACE_SECRET_CORRUPT") -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = 500
        self.exit_code = 1


def hkdf_extract(salt: bytes | None, ikm: bytes) -> bytes:
    """RFC 5869 HKDF-Extract using HMAC-SHA256.

    Args:
        salt: Optional salt value (a non-secret random value). If not provided,
            defaults to a string of HashLen (32) zeros.
        ikm: Input keying material.

    Returns:
        32-byte pseudorandom key (PRK).
    """
    if not salt:
        salt = bytes(32)
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def hkdf_expand(prk: bytes, info: bytes, length: int = 32) -> bytes:
    """RFC 5869 HKDF-Expand using HMAC-SHA256.

    Args:
        prk: Pseudorandom key of at least HashLen (32) octets (usually from hkdf_extract).
        info: Optional context and application specific information.
        length: Length of output keying material in octets (<= 255 * HashLen).

    Returns:
        Output keying material (OKM) of requested length.
    """
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


def _get_current_identity() -> tuple[str, str]:
    """Resolve current user's (domain_qualified_name, sid) on Windows or POSIX.

    Uses `whoami /user /fo csv /nh` on Windows to read identity directly from the process token.
    Returns:
        (domain_username, sid) on Windows, or (user, "") on POSIX.
    """
    if os.name != "nt":
        user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
        return user, ""
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
    user = os.environ.get("USERNAME") or ""
    return user, ""


def set_owner_only_permissions(path: Path | str) -> None:
    """Set strict owner-only permissions on a file or directory.

    On Windows: Purges explicit ACEs with /reset, disables inheritance with /inheritance:r,
    and grants Full Control exclusively to the current user's SID (or username).
    On POSIX: Uses chmod (0700 for directories, 0600 for files).

    Raises:
        InsecurePermissionsError: If setting permissions fails on Windows or POSIX.
    """
    target = Path(path)
    if not target.exists():
        return

    if os.name == "nt":
        user_name, user_sid = _get_current_identity()
        if not user_name and not user_sid:
            raise InsecurePermissionsError(
                f"Cannot resolve current user identity for '{target.name}'"
            )

        # 1. Reset explicit permissions first so any foreign explicit ACEs are purged
        subprocess.run(["icacls", str(target), "/reset"], capture_output=True, check=False)

        # 2. Enforce inheritance removal and grant Full Control only to owner SID (or username)
        principal = f"*{user_sid}" if user_sid else user_name
        grant_spec = f"{principal}:(OI)(CI)(F)" if target.is_dir() else f"{principal}:(F)"
        cmd = ["icacls", str(target), "/inheritance:r", "/grant:r", grant_spec]
        try:
            proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
            if proc.returncode != 0:
                err = proc.stderr or proc.stdout
                raise InsecurePermissionsError(
                    f"Failed to set owner-only permissions on '{target.name}': {err}"
                )
        except OSError as e:
            raise InsecurePermissionsError(
                f"Failed to invoke icacls on '{target.name}': {e}"
            ) from e
    else:
        # POSIX: 0700 for directories, 0600 for files
        mode = 0o700 if target.is_dir() else 0o600
        try:
            os.chmod(target, mode)
        except OSError as e:
            raise InsecurePermissionsError(
                f"Failed to set mode {oct(mode)} on '{target.name}': {e}"
            ) from e


def verify_owner_only_permissions(path: Path | str) -> bool:
    """Verify that a path is accessible only by the current owner.

    On POSIX: Asserts that group and others have no read/write/execute bits (0o077 == 0).
    On Windows: Uses an allow-list approach on parsed icacls entries:
      1. Inheritance must be disabled (no '(I)' flag anywhere).
      2. Every listed principal must be strictly within the allowed owner/system set.
      3. The current owner must have Full Control '(F)' or '(OI)(CI)(F)'.
    """
    target = Path(path)
    if not target.exists():
        return False

    if os.name == "nt":
        user_name, user_sid = _get_current_identity()
        if not user_name and not user_sid:
            return False

        try:
            proc = subprocess.run(
                ["icacls", str(target)], capture_output=True, text=True, check=False
            )
            if proc.returncode != 0:
                return False

            raw_lines = [
                line.strip()
                for line in proc.stdout.splitlines()
                if line.strip()
                and not line.startswith("Successfully processed")
                and not line.startswith("Failed processing")
                and not line.startswith("processed file:")
            ]
            if not raw_lines:
                return False

            # Strip target path prefix from first line
            target_str = str(target)
            if raw_lines[0].startswith(target_str):
                raw_lines[0] = raw_lines[0][len(target_str) :].strip()

            ace_lines = [line for line in raw_lines if line]
            if not ace_lines:
                return False

            # 1. No inherited ACEs permitted anywhere
            full_text = " ".join(ace_lines)
            if "(I)" in full_text or "(i)" in full_text:
                return False

            # Allow-listed principals (lowercased)
            allowed_principals: set[str] = {
                "nt authority\\system",
                "system",
                "s-1-5-18",
                "builtin\\administrators",
                "administrators",
                "s-1-5-32-544",
            }
            if user_name:
                u_norm = user_name.lower()
                allowed_principals.add(u_norm)
                if "\\" in u_norm:
                    allowed_principals.add(u_norm.split("\\")[-1])
            if user_sid:
                s_norm = user_sid.lower()
                allowed_principals.add(s_norm)
                allowed_principals.add(f"*{s_norm}")

            owner_has_full_control = False

            # Parse each ACE
            for ace_line in ace_lines:
                if ":" not in ace_line:
                    continue
                principal_str, perms_str = ace_line.split(":", 1)
                principal_norm = principal_str.strip().lower()
                perms_norm = perms_str.strip().lower()

                # Check allow-list: principal MUST be in allowed_principals
                if principal_norm not in allowed_principals:
                    return False

                # Check if this principal is the owner and has full control
                unqualified_match = (
                    "\\" in user_name and principal_norm == user_name.split("\\")[-1].lower()
                )
                is_owner = (
                    user_name and (principal_norm == user_name.lower() or unqualified_match)
                ) or (
                    user_sid
                    and (
                        principal_norm == user_sid.lower()
                        or principal_norm == f"*{user_sid.lower()}"
                    )
                )
                if is_owner and ("(f)" in perms_norm or "(oi)(ci)(f)" in perms_norm):
                    owner_has_full_control = True

            return owner_has_full_control
        except OSError:
            return False

    # POSIX permission check
    try:
        mode = target.stat().st_mode
        # Disallow group (0o070) and other (0o007) access
        return (mode & 0o077) == 0
    except OSError:
        return False


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
                # Check permissions
                if not verify_owner_only_permissions(self.secret_path):
                    set_owner_only_permissions(self.secret_path)
                    if not verify_owner_only_permissions(self.secret_path):
                        raise InsecurePermissionsError(
                            f"Secret file '{self.secret_path.name}' has insecure permissions."
                        )

                secret = self.secret_path.read_bytes()
                if len(secret) == 32:
                    return secret

                # MIN-02: If file is momentarily short during concurrent start, wait briefly
                if attempt < max_retries - 1:
                    time.sleep(0.01)
                    continue

                # MAJ-02: Fail closed! Never silently regenerate an existing secret.
                raise WorkspaceSecretCorruptError(
                    f"Workspace secret file '{self.secret_path.name}' is corrupted "
                    f"(expected 32 bytes, got {len(secret)} bytes). Refusing to regenerate."
                )

            # MIN-02: Write to temp file first, fsync, then publish atomically via os.link
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

                # Atomically link into place (fails if secret_path already exists)
                try:
                    os.link(str(temp_path), str(self.secret_path))
                    set_owner_only_permissions(self.secret_path)
                    return candidate
                except (FileExistsError, OSError):
                    if self.secret_path.exists():
                        continue
                    # Fallback for systems where link is not permitted: atomic exclusive create
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

        # If loop exited without return, final read
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
        """Return derived purpose-isolated keys.

        Never exposes the raw root secret to client callers.
        """
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

    def sign_waiver(self, waiver_bytes: bytes) -> str:
        """Sign steward waiver bytes with K_waiver, returning hex digest."""
        return (
            hmac.new(self._derived_keys.k_waiver, waiver_bytes, hashlib.sha256).hexdigest().lower()
        )

    def __repr__(self) -> str:
        """Sanitize repr to avoid leaking secrets."""
        return f"WorkspaceSecurityManager(studio_root={self.studio_root}, status=PROTECTED)"
