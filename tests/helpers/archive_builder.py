"""Test fixture builder for valid and adversarial .opendv.zip archives."""

from __future__ import annotations

import hashlib
import json
import stat
import unicodedata
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from opendv.models.json_hasher import dumps_jcs_bytes
from opendv.models.project import (
    BundleEntryDigest,
    BundleManifest,
    BundleProjectSnapshot,
    ProjectSettingsSnapshot,
)
from opendv.models.sources import SourceSummary


class ArchiveFixtureBuilder:
    """Builds valid and security-adversarial .opendv.zip archives for UAT-V25-SVC-05."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def _base_core_entries(
        self,
        *,
        project_id: str = "am-ibor-2026",
        domain_namespace: str = "asset_management",
        sources: list[SourceSummary] | None = None,
        extra_entries: dict[str, bytes] | None = None,
    ) -> dict[str, bytes]:
        snap = BundleProjectSnapshot(
            project_id=project_id,
            domain_namespace=domain_namespace,
            settings_snapshot=ProjectSettingsSnapshot(),
            sources=sources or [],
            revision_keys=[],
        )
        entries: dict[str, bytes] = {
            "project.json": dumps_jcs_bytes(snap.model_dump(mode="json")) + b"\n",
            "tables_index.json": b"[]\n",
            "profile.json": b"[]\n",
            "document_map.json": b"[]\n",
            "source_digests.json": b"{}\n",
            "vault_waivers.json": b"[]\n",
            "external_mcp_receipts.jsonl": b"",
            "ocr_receipts.jsonl": b"",
            "audit.jsonl": b"",
        }
        if extra_entries:
            entries.update(extra_entries)
        return entries

    def build_valid_bundle(
        self,
        filename: str = "valid.core.opendv.zip",
        *,
        project_id: str = "am-ibor-2026",
        mode: Literal["core_project_state", "full_demo"] = "core_project_state",
        source_os: Literal["posix", "nt"] = "posix",
        entries: dict[str, bytes] | None = None,
    ) -> Path:
        """Write a valid SHA-256-manifested .opendv.zip archive."""
        data_entries = entries if entries is not None else self._base_core_entries(project_id=project_id)
        manifest_entries: dict[str, BundleEntryDigest] = {}
        dest = self.out_dir / filename
        with zipfile.ZipFile(dest, "w") as zf:
            for name, raw in sorted(data_entries.items()):
                comp = (
                    zipfile.ZIP_STORED
                    if name.startswith(("sources/", "targets/"))
                    else zipfile.ZIP_DEFLATED
                )
                zf.writestr(name, raw, compress_type=comp)
                manifest_entries[name] = BundleEntryDigest(
                    sha256=hashlib.sha256(raw).hexdigest(),
                    uncompressed_bytes=len(raw),
                )
            manifest = BundleManifest(
                bundle_version="2.0",
                mode=mode,
                project_id=project_id,
                created_at=datetime.now(tz=UTC).isoformat(),
                source_os=source_os,
                entries=manifest_entries,
            )
            zf.writestr(
                "manifest.json",
                dumps_jcs_bytes(manifest.model_dump(mode="json")) + b"\n",
                compress_type=zipfile.ZIP_DEFLATED,
            )
        return dest

    def build_custom_zipinfo_attack(
        self,
        filename: str,
        *,
        member_name: str,
        payload: bytes = b"attack\n",
        external_attr: int = 0,
        create_system: int = 3,
    ) -> Path:
        """Write a zip containing a crafted ZipInfo header alongside valid base metadata."""
        entries = self._base_core_entries()
        entries[member_name] = payload
        dest = self.out_dir / filename
        manifest_entries: dict[str, BundleEntryDigest] = {}
        with zipfile.ZipFile(dest, "w") as zf:
            for name, raw in sorted(entries.items()):
                info = zipfile.ZipInfo(name)
                if name == member_name:
                    # Preserve literal member_name (including backslashes) bypassing ZipInfo.__init__
                    info.filename = member_name
                    info.external_attr = external_attr
                    info.create_system = create_system
                zf.writestr(info, raw, compress_type=zipfile.ZIP_DEFLATED)
                manifest_entries[name] = BundleEntryDigest(
                    sha256=hashlib.sha256(raw).hexdigest(),
                    uncompressed_bytes=len(raw),
                )
            manifest = BundleManifest(
                bundle_version="2.0",
                mode="core_project_state",
                project_id="am-ibor-2026",
                created_at=datetime.now(tz=UTC).isoformat(),
                source_os="posix",
                entries=manifest_entries,
            )
            zf.writestr(
                "manifest.json",
                dumps_jcs_bytes(manifest.model_dump(mode="json")) + b"\n",
                compress_type=zipfile.ZIP_DEFLATED,
            )
        return dest

    def build_symlink_attack(self, filename: str = "symlink.opendv.zip") -> Path:
        """Create an archive with a POSIX symlink entry."""
        return self.build_custom_zipinfo_attack(
            filename,
            member_name="sources/src1/link.txt",
            payload=b"/etc/passwd",
            external_attr=(stat.S_IFLNK | 0o777) << 16,
            create_system=3,
        )

    def build_reparse_attack(
        self,
        filename: str = "reparse.opendv.zip",
        *,
        create_system: int = 3,
    ) -> Path:
        """Create an archive with NTFS reparse point attribute 0x0400."""
        return self.build_custom_zipinfo_attack(
            filename,
            member_name="sources/src1/junction.txt",
            payload=b"junction",
            external_attr=0x0400,
            create_system=create_system,
        )

    def build_device_node_attack(self, filename: str = "device.opendv.zip") -> Path:
        """Create an archive with a Unix character device entry."""
        return self.build_custom_zipinfo_attack(
            filename,
            member_name="sources/src1/devnull",
            payload=b"",
            external_attr=(stat.S_IFCHR | 0o666) << 16,
            create_system=3,
        )

    def build_nfc_casefold_collision(self, filename: str = "collision.opendv.zip") -> Path:
        """Create an archive with NFC/NFD + case-folded colliding filenames."""
        nfd_name = unicodedata.normalize("NFD", "versions/Caf\u00e9.json")
        nfc_lower = unicodedata.normalize("NFC", "versions/caf\u00e9.json")
        entries = self._base_core_entries(
            extra_entries={
                nfd_name: b"{}\n",
                nfc_lower: b"{}\n",
            }
        )
        return self.build_valid_bundle(filename, entries=entries)

    def build_entry_count_overflow(
        self, filename: str = "too_many.opendv.zip", *, count: int = 505
    ) -> Path:
        """Create an archive exceeding the 500-entry limit."""
        extra = {f"extra/item_{i:04d}.json": b"{}\n" for i in range(count)}
        return self.build_valid_bundle(filename, entries=self._base_core_entries(extra_entries=extra))

    def build_zip_bomb_ratio(self, filename: str = "bomb_ratio.opendv.zip") -> Path:
        """Create a > 1 MB entry whose compression ratio exceeds 100:1."""
        bomb = b"\x00" * (2 * 1024 * 1024)
        entries = self._base_core_entries(extra_entries={"extra/bomb.txt": bomb})
        return self.build_valid_bundle(filename, entries=entries)

    def build_size_overflow_250mb(self, filename: str = "over_250mb.opendv.zip") -> Path:
        """Create a 255-entry archive totaling 255 MB (> 250 MB) without triggering 100:1."""
        one_mb = b"\x00" * (1024 * 1024)
        extra = {f"extra/chunk_{i:03d}.bin": one_mb for i in range(255)}
        return self.build_valid_bundle(filename, entries=self._base_core_entries(extra_entries=extra))

    def build_hash_tamper(self, filename: str = "tampered.opendv.zip") -> Path:
        """Create an archive whose payload does not match manifest.json SHA-256."""
        dest = self.build_valid_bundle(filename)
        tampered = self.out_dir / f"tampered_{filename}"
        with zipfile.ZipFile(dest, "r") as src, zipfile.ZipFile(tampered, "w") as dst:
            for info in src.infolist():
                raw = src.read(info.filename)
                if info.filename == "tables_index.json":
                    raw = json.dumps([{"tampered": True}]).encode("utf-8")
                dst.writestr(info.filename, raw, compress_type=zipfile.ZIP_DEFLATED)
        return tampered


__all__ = ["ArchiveFixtureBuilder"]
