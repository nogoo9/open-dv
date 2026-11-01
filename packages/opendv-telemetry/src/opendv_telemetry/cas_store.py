"""Immutable Content-Addressable Store (CAS) for Open-DV artifact snapshots.

Conforms to docs/06-explainability-and-observability-spec.md (§1.1, §1.2).
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Final

HEX_64_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9A-Fa-f]{64}$")
CHUNK_SIZE: Final[int] = 65536


class ArtifactNotFoundError(KeyError, FileNotFoundError):
    """Raised when an artifact cannot be found in the Content-Addressable Store."""


class ArtifactCorruptedError(ValueError):
    """Raised when an artifact's cryptographic digest on disk does not match its address."""


def compute_cas_sha256(content: str | bytes) -> str:
    """Compute uppercase 64-hex SHA-256 digest of utf-8 string or raw bytes."""
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(raw).hexdigest().upper()


class ContentAddressableStore:
    """Manages immutable file-backed content-addressable storage under `state/artifacts/<sha256>`.

    Key properties:
    1. Content-addressed by uppercase 64-hex SHA-256 digests.
    2. Writes are atomic via temporary files in the same filesystem.
    3. Verification guarantees zero-tamper byte integrity on read.
    """

    def __init__(self, base_dir: Path | str = "state/artifacts") -> None:
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def compute_sha256(content: str | bytes) -> str:
        """Compute uppercase 64-hex SHA-256 digest."""
        return compute_cas_sha256(content)

    def _validate_key(self, sha256: str) -> str:
        clean = sha256.strip().upper()
        if not HEX_64_RE.match(clean):
            msg = f"Invalid CAS key '{sha256}': expected 64-character hexadecimal SHA-256 digest"
            raise ValueError(msg)
        return clean

    def put(self, content: str | bytes) -> str:
        """Store content in the CAS and return its uppercase 64-hex SHA-256 address."""
        raw = content.encode("utf-8") if isinstance(content, str) else content
        digest = compute_cas_sha256(raw)
        target_path = self.base_dir / digest

        if target_path.exists():
            return digest

        self.base_dir.mkdir(parents=True, exist_ok=True)
        # Atomic write via temporary file in same directory
        with tempfile.NamedTemporaryFile(dir=self.base_dir, delete=False) as tmp_file:
            tmp_path = Path(tmp_file.name)
            tmp_file.write(raw)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())

        tmp_path.replace(target_path)
        return digest

    def put_file(self, file_path: Path | str) -> str:
        """Stream a source file into the CAS and return its uppercase 64-hex SHA-256 address."""
        src = Path(file_path).resolve()
        if not src.is_file():
            msg = f"Source file does not exist: {src}"
            raise FileNotFoundError(msg)

        hasher = hashlib.sha256()
        with src.open("rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                hasher.update(chunk)
        digest = hasher.hexdigest().upper()
        target_path = self.base_dir / digest

        if target_path.exists():
            return digest

        self.base_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=self.base_dir, delete=False) as tmp_file:
            tmp_path = Path(tmp_file.name)
            with src.open("rb") as in_f:
                while chunk := in_f.read(CHUNK_SIZE):
                    tmp_file.write(chunk)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())

        tmp_path.replace(target_path)
        return digest

    def get_bytes(self, sha256: str) -> bytes:
        """Retrieve artifact raw bytes and verify cryptographic integrity."""
        key = self._validate_key(sha256)
        path = self.base_dir / key
        if not path.is_file():
            msg = f"Artifact not found in CAS: {key} (looked in {path})"
            raise ArtifactNotFoundError(msg)

        raw = path.read_bytes()
        actual_digest = compute_cas_sha256(raw)
        if actual_digest != key:
            msg = (
                f"CAS artifact integrity violation for {key}: "
                f"content hash on disk is {actual_digest}"
            )
            raise ArtifactCorruptedError(msg)
        return raw

    def get_str(self, sha256: str, encoding: str = "utf-8") -> str:
        """Retrieve artifact content decoded as string."""
        return self.get_bytes(sha256).decode(encoding)

    def has(self, sha256: str) -> bool:
        """Check if an artifact exists in the CAS by address."""
        try:
            key = self._validate_key(sha256)
        except ValueError:
            return False
        return (self.base_dir / key).is_file()

    def verify(self, sha256: str) -> bool:
        """Verify that an artifact exists and matches its cryptographic digest."""
        try:
            self.get_bytes(sha256)
        except (ArtifactNotFoundError, ArtifactCorruptedError, ValueError):
            return False
        return True

    def verify_all(self) -> dict[str, bool]:
        """Verify all artifacts currently stored in the base directory."""
        results: dict[str, bool] = {}
        if not self.base_dir.exists():
            return results

        for child in self.base_dir.iterdir():
            if child.is_file() and HEX_64_RE.match(child.name):
                results[child.name] = self.verify(child.name)
        return results

    def list_artifacts(self) -> list[str]:
        """List all valid 64-hex SHA-256 artifact addresses in the store."""
        if not self.base_dir.exists():
            return []
        return sorted(
            child.name
            for child in self.base_dir.iterdir()
            if child.is_file() and HEX_64_RE.match(child.name)
        )


__all__ = [
    "ArtifactCorruptedError",
    "ArtifactNotFoundError",
    "ContentAddressableStore",
    "compute_cas_sha256",
]
