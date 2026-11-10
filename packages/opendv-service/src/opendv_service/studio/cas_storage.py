"""Content-Addressable Storage (CAS) for Open-DV Ontology Studio.

Implements Task 4D.1 / Issue #208:
- Stores content under `<storage_root>/cas/<sha256[:2]>/<sha256[2:]>`.
- Provides content deduplication: identical bytes write to identical path.
- Verifies bit-for-bit SHA-256 cryptographic integrity upon retrieval.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import secrets
from pathlib import Path
from typing import Final

_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"\A[0-9a-f]{64}\Z")


class CASIntegrityError(ValueError):
    """Raised when CAS content digest does not match its address."""

    def __init__(self, message: str, error_code: str = "CAS_INTEGRITY_MISMATCH") -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = 422
        self.exit_code = 4


class CASNotFoundError(FileNotFoundError):
    """Raised when a requested blob does not exist in CAS."""

    def __init__(self, message: str, error_code: str = "CAS_BLOB_NOT_FOUND") -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = 404
        self.exit_code = 3


class ContentAddressableStorage:
    """Content-Addressable Storage storing immutable blobs keyed by SHA-256."""

    def __init__(self, root_dir: Path | str) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.cas_dir = self.root_dir / "cas"

    def put(self, data: bytes) -> str:
        """Store bytes in CAS with SHA-256 addressing and deduplication.

        Args:
            data: Raw content bytes to store.

        Returns:
            64-character lowercase SHA-256 hex digest.
        """
        digest = hashlib.sha256(data).hexdigest().lower()
        shard = digest[:2]
        rest = digest[2:]

        shard_dir = self.cas_dir / shard
        blob_path = shard_dir / rest

        # MIN-06: Deduplication check with integrity verification
        if blob_path.exists():
            try:
                existing_bytes = blob_path.read_bytes()
                if hashlib.sha256(existing_bytes).hexdigest().lower() == digest:
                    return digest
            except OSError:
                pass
            # If existing blob was corrupted or unreadable, overwrite it atomically below

        # Write atomically using a temporary file in the same shard directory
        shard_dir.mkdir(parents=True, exist_ok=True)
        temp_file = shard_dir / f".{rest}.{secrets.token_hex(4)}.tmp"
        try:
            temp_file.write_bytes(data)
            temp_file.replace(blob_path)
        finally:
            if temp_file.exists():
                with contextlib.suppress(OSError):
                    temp_file.unlink()

        return digest

    def put_file(self, source_path: Path | str) -> str:
        """Store contents of an existing file into CAS."""
        data = Path(source_path).read_bytes()
        return self.put(data)

    def _resolve_path(self, sha256: str) -> Path | None:
        """Resolve blob path from sharded or fallback flat layout."""
        digest = sha256.strip().lower()
        if not _SHA256_PATTERN.fullmatch(digest):
            raise ValueError(f"Invalid SHA-256 hex digest: '{sha256}'")

        # Primary: sharded layout cas/<sha256[:2]>/<sha256[2:]>
        sharded_path = self.cas_dir / digest[:2] / digest[2:]
        if sharded_path.exists():
            return sharded_path

        # Fallback: flat layout cas/<sha256>
        flat_path = self.cas_dir / digest
        if flat_path.exists():
            return flat_path

        return None

    def get(self, sha256: str) -> bytes:
        """Retrieve bytes from CAS by SHA-256 digest with integrity verification.

        Args:
            sha256: 64-character SHA-256 hex digest.

        Returns:
            Verified raw content bytes.

        Raises:
            CASNotFoundError: If blob does not exist in CAS.
            CASIntegrityError: If retrieved bytes fail SHA-256 verification.
        """
        digest = sha256.strip().lower()
        blob_path = self._resolve_path(digest)
        if blob_path is None:
            # MIN-07: Avoid leaking absolute paths in error messages
            raise CASNotFoundError(f"Blob '{digest}' not found in CAS")

        data = blob_path.read_bytes()
        computed = hashlib.sha256(data).hexdigest().lower()
        if computed != digest:
            raise CASIntegrityError(
                f"CAS blob integrity mismatch for '{digest}': computed '{computed}'"
            )

        return data

    def get_path(self, sha256: str, verify: bool = True) -> Path:
        """Get the filesystem path of a CAS blob, optionally verifying integrity.

        Raises:
            CASNotFoundError: If blob does not exist.
            CASIntegrityError: If verify=True and content hash does not match.
        """
        digest = sha256.strip().lower()
        blob_path = self._resolve_path(digest)
        if blob_path is None:
            raise CASNotFoundError(f"Blob '{digest}' not found in CAS")

        if verify:
            data = blob_path.read_bytes()
            computed = hashlib.sha256(data).hexdigest().lower()
            if computed != digest:
                raise CASIntegrityError(
                    f"CAS blob integrity mismatch for '{digest}': computed '{computed}'"
                )

        return blob_path

    def has(self, sha256: str) -> bool:
        """Check whether a blob exists in CAS."""
        try:
            return self._resolve_path(sha256) is not None
        except ValueError:
            return False

    def delete(self, sha256: str) -> bool:
        """Delete a blob from CAS (used for mark-and-sweep GC).

        Returns:
            True if deleted, False if not found.
        """
        digest = sha256.strip().lower()
        blob_path = self._resolve_path(digest)
        if blob_path is None:
            return False

        try:
            blob_path.unlink()
            # Clean up empty shard directory if applicable
            parent = blob_path.parent
            if parent != self.cas_dir and not any(parent.iterdir()):
                with contextlib.suppress(OSError):
                    parent.rmdir()
            return True
        except OSError:
            return False

    def list_blobs(self) -> list[str]:
        """List all valid SHA-256 digests present in CAS."""
        if not self.cas_dir.exists():
            return []

        digests: list[str] = []
        for shard_dir in self.cas_dir.iterdir():
            if shard_dir.is_dir() and len(shard_dir.name) == 2:
                for file_path in shard_dir.iterdir():
                    if file_path.is_file():
                        full_digest = f"{shard_dir.name}{file_path.name}".lower()
                        if _SHA256_PATTERN.fullmatch(full_digest):
                            digests.append(full_digest)
            elif shard_dir.is_file():
                flat_name = shard_dir.name.lower()
                if _SHA256_PATTERN.fullmatch(flat_name):
                    digests.append(flat_name)

        return sorted(digests)
