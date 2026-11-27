"""Portable .opendv.zip exporter supporting core_project_state and full_demo modes."""

from __future__ import annotations

import hashlib
import os
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.json_hasher import dumps_jcs_bytes
from opendv.models.project import (
    BundleEntryDigest,
    BundleManifest,
    BundleProjectSnapshot,
)
from opendv.models.sources import DocumentMap, SourceProfile, TableIndexEntry
from opendv.service.bundles._bundle_ports import (
    BundleStateStorePort,
    coerce_payload_dict,
    replace_with_retry,
)
from opendv.service.bundles.zip_guard import (
    MAX_BUNDLE_ENTRIES,
    MAX_BUNDLE_UNCOMPRESSED_BYTES,
    reject_symlinks_on_path_and_ancestors,
    safe_open_no_follow,
    validate_archive_member,
)

BundleMode = Literal["core_project_state", "full_demo"]

_REV_ARRAY_FILENAMES: dict[str, str] = {
    "evolution_lock": "evolution_locks.json",
    "horizon_assumption": "horizon_assumptions.json",
    "concept_memory": "concept_memory.json",
    "staged_candidate": "staged_candidates.json",
    "ontology_proposal": "ontology_proposals.json",
    "ontology_waiver": "ontology_waivers.json",
}


def _json_bytes(obj: Any) -> bytes:
    return dumps_jcs_bytes(obj) + b"\n"


def _jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    if not rows:
        return b""
    return b"".join(dumps_jcs_bytes(r) + b"\n" for r in rows)


def _sanitized_source_summary(src: Any) -> Any:
    if src.connector_config is not None:
        clean_cfg = src.connector_config.model_copy(
            update={"secret_refs": list(src.connector_config.secret_refs)}
        )
        return src.model_copy(update={"connector_config": clean_cfg})
    return src


def _rev_envelope(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "revision_id": rec.get("revision_id", ""),
        "rev_kind": rec.get("rev_kind", ""),
        "rev_key": rec.get("rev_key", ""),
        "parent_rev_id": rec.get("parent_rev_id"),
        "content_sha256": rec.get("content_sha256", ""),
        "payload": coerce_payload_dict(rec.get("payload", rec.get("payload_json"))),
        "metadata": coerce_payload_dict(rec.get("metadata", rec.get("metadata_json"))),
        "created_at": rec.get("created_at", ""),
    }


def _collect_full_demo_files(
    project_id: str,
    project_dir: Path,
) -> list[tuple[str, Path, int]]:
    reject_symlinks_on_path_and_ancestors(project_dir)
    collected: list[tuple[str, Path, int]] = []

    duck_dir = project_dir / "targets" / "duckdb_local"
    if duck_dir.is_dir():
        reject_symlinks_on_path_and_ancestors(duck_dir, stop_roots=[project_dir])
        primary_db = duck_dir / f"{project_id}.duckdb"
        fallback_db = duck_dir / "demo_vault.duckdb"
        db_file = (
            primary_db
            if primary_db.is_file()
            else (fallback_db if fallback_db.is_file() else None)
        )
        if db_file is not None:
            collected.append((f"targets/duckdb_local/{db_file.name}", db_file, zipfile.ZIP_STORED))
        tm = duck_dir / "target_manifest.json"
        if tm.is_file():
            collected.append(
                ("targets/duckdb_local/target_manifest.json", tm, zipfile.ZIP_DEFLATED)
            )

    sources_dir = project_dir / "sources"
    if sources_dir.is_dir():
        reject_symlinks_on_path_and_ancestors(sources_dir, stop_roots=[project_dir])
        for file_path in sorted(sources_dir.rglob("*")):
            if file_path.is_dir():
                reject_symlinks_on_path_and_ancestors(file_path, stop_roots=[project_dir])
                continue
            reject_symlinks_on_path_and_ancestors(file_path, stop_roots=[project_dir])
            rel_posix = file_path.relative_to(project_dir).as_posix()
            collected.append((rel_posix, file_path, zipfile.ZIP_STORED))

    return collected


def export_project_bundle(
    state_store: BundleStateStorePort,
    project_id: str,
    output_path: Path,
    *,
    mode: BundleMode = "core_project_state",
    project_dir: Path | None = None,
    created_at: str | None = None,
) -> tuple[Path, BundleManifest]:
    """Export a project as an archive-safe .opendv.zip bundle (core_project_state or full_demo)."""
    project = state_store.get_project(project_id)
    sources = [_sanitized_source_summary(s) for s in state_store.list_sources(project_id)]
    revisions = state_store.list_revisions(project_id)
    events = state_store.list_events_or_receipts(project_id)

    profiles: list[dict[str, Any]] = []
    tables_index: list[dict[str, Any]] = []
    document_maps: list[dict[str, Any]] = []
    source_digests: dict[str, dict[str, Any]] = {}

    for src in sources:
        rec = state_store.get_source_record(project_id, src.source_id)
        prof_raw = rec.get("profile")
        if isinstance(prof_raw, SourceProfile):
            profiles.append(prof_raw.model_dump(mode="json"))
        elif prof_raw is not None:
            profiles.append(coerce_payload_dict(prof_raw))

        t_idx_raw = rec.get("tables_index") or []
        for t_item in t_idx_raw:
            if isinstance(t_item, TableIndexEntry):
                tables_index.append(t_item.model_dump(mode="json"))
            elif isinstance(t_item, dict):
                tables_index.append(dict(t_item))

        dm_raw = rec.get("document_map")
        if isinstance(dm_raw, DocumentMap):
            document_maps.append(dm_raw.model_dump(mode="json"))
        elif dm_raw is not None:
            document_maps.append(coerce_payload_dict(dm_raw))

        source_digests[src.source_id] = {
            "binary_sha256": rec.get("binary_sha256"),
            "approved_scan_pages": list(rec.get("approved_scan_pages") or []),
        }

    vault_waivers: list[dict[str, Any]] = []
    grouped_revs: dict[str, list[dict[str, Any]]] = {k: [] for k in _REV_ARRAY_FILENAMES}
    memory_entries: dict[str, bytes] = {}
    revision_keys: list[str] = []

    for rev in revisions:
        r_kind = str(rev.get("rev_kind", ""))
        r_key = str(rev.get("rev_key", ""))
        revision_keys.append(f"{r_kind}:{r_key}")
        env = _rev_envelope(rev)
        if r_kind == "ontology_version":
            memory_entries[f"versions/{r_key}.json"] = _json_bytes(env)
        elif r_kind == "wave_ir":
            memory_entries[f"waves/{r_key}.json"] = _json_bytes(env)
        elif r_kind == "vault_waiver":
            vault_waivers.append(env)
        elif r_kind in grouped_revs:
            grouped_revs[r_kind].append(env)

    mcp_receipts: list[dict[str, Any]] = []
    ocr_receipts: list[dict[str, Any]] = []
    grounding_receipts: list[dict[str, Any]] = []
    audit_logs: list[dict[str, Any]] = []
    for ev in events:
        kind = str(ev.get("record_kind", ""))
        payload_dict = coerce_payload_dict(ev.get("payload", ev.get("payload_json")))
        if kind == "mcp_receipt":
            mcp_receipts.append(payload_dict)
        elif kind == "ocr_receipt":
            ocr_receipts.append(payload_dict)
        elif kind == "grounding_receipt":
            grounding_receipts.append(payload_dict)
        elif kind == "audit_log":
            audit_logs.append(payload_dict)

    snapshot = BundleProjectSnapshot(
        project_id=project.project_id,
        domain_namespace=project.domain_namespace,
        active_ontology_rev_id=project.active_ontology_rev_id,
        active_wave_id=project.active_wave_id,
        settings_snapshot=project.settings_snapshot,
        sources=sources,
        revision_keys=sorted(revision_keys),
    )

    memory_entries["project.json"] = _json_bytes(snapshot.model_dump(mode="json"))
    memory_entries["tables_index.json"] = _json_bytes(tables_index)
    memory_entries["profile.json"] = _json_bytes(profiles)
    memory_entries["document_map.json"] = _json_bytes(document_maps)
    memory_entries["source_digests.json"] = _json_bytes(source_digests)
    memory_entries["vault_waivers.json"] = _json_bytes(vault_waivers)
    memory_entries["external_mcp_receipts.jsonl"] = _jsonl_bytes(mcp_receipts)
    memory_entries["ocr_receipts.jsonl"] = _jsonl_bytes(ocr_receipts)
    memory_entries["grounding_receipts.jsonl"] = _jsonl_bytes(grounding_receipts)
    memory_entries["audit.jsonl"] = _jsonl_bytes(audit_logs)

    for r_kind, fname in _REV_ARRAY_FILENAMES.items():
        if grouped_revs[r_kind]:
            memory_entries[fname] = _json_bytes(grouped_revs[r_kind])

    binary_files: list[tuple[str, Path, int]] = []
    if mode == "full_demo" and project_dir is not None and project_dir.exists():
        binary_files = _collect_full_demo_files(project_id, project_dir)

    total_entries = len(memory_entries) + len(binary_files) + 1  # +1 for manifest.json
    if total_entries > MAX_BUNDLE_ENTRIES:
        raise OpenDVError(
            f"Export entry count {total_entries} exceeds {MAX_BUNDLE_ENTRIES} limit.",
            error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
        )

    seen_norm: set[str] = set()
    manifest_entries: dict[str, BundleEntryDigest] = {}
    total_uncompressed = 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".tmp-export-", suffix=".zip", dir=str(output_path.parent)
    )
    os.close(fd)
    tmp_zip = Path(tmp_name)

    try:
        with zipfile.ZipFile(tmp_zip, "w") as zf:
            for rel_name in sorted(memory_entries):
                raw_bytes = memory_entries[rel_name]
                info = zipfile.ZipInfo(rel_name)
                validate_archive_member(info, seen_norm)
                total_uncompressed += len(raw_bytes)
                if total_uncompressed > MAX_BUNDLE_UNCOMPRESSED_BYTES:
                    raise OpenDVError(
                        "Export uncompressed size exceeds 250 MB limit.",
                        error_code=ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED,
                    )
                zf.writestr(rel_name, raw_bytes, compress_type=zipfile.ZIP_DEFLATED)
                manifest_entries[rel_name] = BundleEntryDigest(
                    sha256=hashlib.sha256(raw_bytes).hexdigest(),
                    uncompressed_bytes=len(raw_bytes),
                )

            for rel_name, src_path, comp_type in binary_files:
                info = zipfile.ZipInfo(rel_name)
                validate_archive_member(info, seen_norm)
                rfd = safe_open_no_follow(src_path, os.O_RDONLY)
                with os.fdopen(rfd, "rb") as in_f:
                    data = in_f.read()
                total_uncompressed += len(data)
                if total_uncompressed > MAX_BUNDLE_UNCOMPRESSED_BYTES:
                    raise OpenDVError(
                        "Export uncompressed size exceeds 250 MB limit.",
                        error_code=ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED,
                    )
                zf.writestr(rel_name, data, compress_type=comp_type)
                manifest_entries[rel_name] = BundleEntryDigest(
                    sha256=hashlib.sha256(data).hexdigest(),
                    uncompressed_bytes=len(data),
                )

            source_os: Literal["posix", "nt"] = "nt" if os.name == "nt" else "posix"
            manifest = BundleManifest(
                bundle_version="2.0",
                mode=mode,
                project_id=project_id,
                created_at=created_at or datetime.now(tz=UTC).isoformat(),
                source_os=source_os,
                entries=manifest_entries,
            )
            manifest_bytes = _json_bytes(manifest.model_dump(mode="json"))
            validate_archive_member(zipfile.ZipInfo("manifest.json"), seen_norm)
            if total_uncompressed + len(manifest_bytes) > MAX_BUNDLE_UNCOMPRESSED_BYTES:
                raise OpenDVError(
                    "Export uncompressed size exceeds 250 MB limit.",
                    error_code=ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED,
                )
            zf.writestr("manifest.json", manifest_bytes, compress_type=zipfile.ZIP_DEFLATED)

        replace_with_retry(tmp_zip, output_path)
    except Exception:
        if tmp_zip.exists():
            tmp_zip.unlink(missing_ok=True)
        raise

    return output_path, manifest


__all__ = [
    "BundleMode",
    "export_project_bundle",
]
