"""Zero-trust .opendv.zip importer with conflict resolution and ImportReadinessReport."""

from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.project import (
    BundleProjectSnapshot,
    ImportReadinessReport,
    TargetManifest,
    validate_project_id,
)
from opendv.models.sources import (
    ConnectorConfigPayload,
    SourceProfile,
    SourceStatus,
)
from opendv.service.bundles._bundle_ports import (
    BundleSecretStorePort,
    BundleStateStorePort,
    atomic_write_bytes,
    replace_with_retry,
)
from opendv.service.bundles._bundle_validate import (
    parse_json,
    prevalidate_bundle_entries,
)
from opendv.service.bundles.zip_guard import (
    CHUNK_SIZE,
    inspect_and_verify_archive,
    reject_symlinks_on_path_and_ancestors,
    safe_extract_binary_entries,
)

ConflictPolicy = Literal["fail", "rename", "replace"]


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _resolve_renamed_project_id(base_id: str, existing: set[str]) -> str:
    for idx in range(0, 1000):
        suffix = "-copy" if idx == 0 else f"-copy-{idx}"
        prefix = base_id[: 64 - len(suffix)].rstrip("-_") or "proj"
        candidate = f"{prefix}{suffix}"
        if candidate not in existing:
            validate_project_id(candidate)
            return candidate
    raise OpenDVError(
        f"Unable to allocate unique renamed project_id for '{base_id}'.",
        error_code=ErrorCode.PROJECT_ID_CONFLICT,
    )


def _rename_duckdb_in_staging(
    staging_dir: Path,
    orig_pid: str,
    resolved_pid: str,
) -> None:
    if orig_pid == resolved_pid:
        return
    duck_dir = staging_dir / "targets" / "duckdb_local"
    if not duck_dir.is_dir():
        return
    orig_db = duck_dir / f"{orig_pid}.duckdb"
    fallback_db = duck_dir / "demo_vault.duckdb"
    new_db = duck_dir / f"{resolved_pid}.duckdb"
    renamed_from: str | None = None
    if orig_db.is_file():
        replace_with_retry(orig_db, new_db)
        renamed_from = orig_db.name
    elif fallback_db.is_file():
        replace_with_retry(fallback_db, new_db)
        renamed_from = fallback_db.name
    tm_path = duck_dir / "target_manifest.json"
    if renamed_from is not None and tm_path.is_file():
        try:
            tm = TargetManifest.model_validate_json(tm_path.read_text(encoding="utf-8"))
        except ValidationError:
            return
        updated_files = [
            (new_db.name if f == renamed_from else f) for f in tm.generated_files
        ]
        updated_tm = tm.model_copy(update={"generated_files": updated_files})
        atomic_write_bytes(tm_path, (updated_tm.model_dump_json(indent=2) + "\n").encode("utf-8"))


def _local_binary_present(
    source_root: Path,
    source_id: str,
    expected_sha256: str | None,
) -> bool:
    src_dir = source_root / "sources" / source_id
    if not src_dir.is_dir():
        return False
    files = [p for p in src_dir.rglob("*") if p.is_file()]
    if not files:
        return False
    if expected_sha256 is None:
        return True
    return any(_sha256_file(p) == expected_sha256 for p in files)


def import_project_bundle(
    state_store: BundleStateStorePort,
    secret_store: BundleSecretStorePort,
    bundle_path: Path,
    *,
    projects_root: Path,
    conflict: ConflictPolicy = "fail",
    trust_workspace: bool = False,
) -> ImportReadinessReport:
    """Verify and import an .opendv.zip archive without extracting on any security violation."""
    _ = trust_workspace
    verified = inspect_and_verify_archive(bundle_path)
    entries = verified.json_entries

    raw_snapshot = parse_json(entries.get("project.json"), None, "project.json")
    if raw_snapshot is None:
        raise OpenDVError(
            "Bundle is missing required project.json.",
            error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
        )
    try:
        snapshot = BundleProjectSnapshot.model_validate(raw_snapshot)
    except ValidationError as exc:
        raise OpenDVError(
            f"Invalid project.json in bundle: {exc}",
            error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
        ) from exc

    orig_pid = snapshot.project_id
    if orig_pid != verified.manifest.project_id:
        raise OpenDVError(
            "Bundle manifest project_id disagrees with project.json.",
            error_code=ErrorCode.BUNDLE_HASH_MISMATCH,
        )

    existing_ids = {p.project_id for p in state_store.list_projects()}
    resolved_pid = orig_pid
    resolved_conflict: Literal["none", "renamed", "replaced"] = "none"

    if orig_pid in existing_ids:
        if conflict == "fail":
            raise OpenDVError(
                f"Project '{orig_pid}' already exists.",
                error_code=ErrorCode.PROJECT_ID_CONFLICT,
            )
        if conflict == "replace":
            current = state_store.get_project(orig_pid)
            if current.has_active_lease:
                raise OpenDVError(
                    f"Cannot replace project '{orig_pid}' while an active job lease is held.",
                    error_code=ErrorCode.PROJECT_JOB_ACTIVE,
                )
            resolved_conflict = "replaced"
        else:
            resolved_pid = _resolve_renamed_project_id(orig_pid, existing_ids)
            resolved_conflict = "renamed"

    prevalidated = prevalidate_bundle_entries(entries, orig_pid, resolved_pid)

    reject_symlinks_on_path_and_ancestors(projects_root)
    projects_root.mkdir(parents=True, exist_ok=True)
    target_project_dir = projects_root / resolved_pid
    reject_symlinks_on_path_and_ancestors(target_project_dir, stop_roots=[projects_root])
    if (
        target_project_dir.exists() or target_project_dir.is_symlink()
    ) and not target_project_dir.is_dir():
        raise OpenDVError(
            f"Target project path '{target_project_dir.name}' exists as a non-directory.",
            error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
        )
    staging_dir = projects_root / f".staging-import-{uuid.uuid4().hex[:12]}"
    backup_dir: Path | None = None
    backup_created = False
    staged_target = False

    try:
        if verified.binary_entries:
            staging_dir.mkdir(parents=True, exist_ok=True)
            safe_extract_binary_entries(
                bundle_path,
                verified.binary_entries,
                staging_dir,
                stop_roots=[projects_root],
            )
            _rename_duckdb_in_staging(staging_dir, orig_pid, resolved_pid)

        if resolved_conflict == "replaced" and target_project_dir.exists():
            reject_symlinks_on_path_and_ancestors(
                target_project_dir, stop_roots=[projects_root]
            )
            candidate_backup = projects_root / f".backup-replace-{uuid.uuid4().hex[:12]}"
            reject_symlinks_on_path_and_ancestors(
                candidate_backup, stop_roots=[projects_root]
            )
            if candidate_backup.exists() or candidate_backup.is_symlink():
                raise OpenDVError(
                    f"Backup target '{candidate_backup.name}' already exists.",
                    error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
                )
            replace_with_retry(target_project_dir, candidate_backup)
            backup_dir = candidate_backup
            backup_created = True
    except Exception:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        if backup_created and backup_dir is not None and backup_dir.exists():
            replace_with_retry(backup_dir, target_project_dir)
        raise

    missing_secret_refs: list[str] = []
    missing_source_ids: list[str] = []
    seen_missing_secrets: set[str] = set()
    unverified_receipt_ids: list[str] = []
    project_created = False

    try:
        if resolved_conflict == "replaced":
            state_store.delete_project(orig_pid)
        if staging_dir.exists():
            if target_project_dir.exists() or target_project_dir.is_symlink():
                if not target_project_dir.is_dir():
                    raise OpenDVError(
                        f"Target project path '{target_project_dir.name}' is not a directory.",
                        error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
                    )
                shutil.rmtree(target_project_dir)
            replace_with_retry(staging_dir, target_project_dir)
            staged_target = True
        state_store.create_project(
            resolved_pid,
            snapshot.domain_namespace,
            snapshot.settings_snapshot,
        )
        project_created = True

        for src in snapshot.sources:
            cfg = src.connector_config or ConnectorConfigPayload(
                adapter_id=src.kind,
                display_name=src.kind,
                uri_or_path=src.uri_or_filename,
            )
            src_missing_secrets: list[str] = []
            for s_key in cfg.secret_refs:
                if (
                    secret_store.get_secret(
                        s_key, project_precedence=snapshot.settings_snapshot.secret_precedence
                    )
                    is None
                ):
                    src_missing_secrets.append(s_key)
                    if s_key not in seen_missing_secrets:
                        seen_missing_secrets.add(s_key)
                        missing_secret_refs.append(s_key)

            digest_meta = prevalidated.source_digests.get(src.source_id)
            binary_sha = digest_meta.binary_sha256 if digest_meta else None
            approved_pages = list(digest_meta.approved_scan_pages) if digest_meta else []
            requires_binary = src.kind != "databricks_uc" and (
                binary_sha is not None or src.kind != "domain_note"
            )
            has_binary = (
                _local_binary_present(target_project_dir, src.source_id, binary_sha)
                if requires_binary
                else True
            )

            if src_missing_secrets:
                status: SourceStatus = "NEEDS_LOCAL_SECRET_BINDING"
                if requires_binary and not has_binary:
                    missing_source_ids.append(src.source_id)
            elif requires_binary and not has_binary:
                status = "METADATA_ONLY_BINARY_ABSENT"
                missing_source_ids.append(src.source_id)
            else:
                status = "READY_OFFLINE"

            profile = prevalidated.profiles_by_src.get(src.source_id) or SourceProfile(
                source_id=src.source_id,
                schema_fingerprint="",
                table_count=0,
                tables=[],
            )
            doc_map = prevalidated.doc_maps_by_id.get(src.source_id)
            state_store.upsert_source(
                resolved_pid,
                source_id=src.source_id,
                kind=src.kind,
                uri_or_filename=src.uri_or_filename,
                binary_sha256=binary_sha,
                connector_config=cfg,
                status=status,
                profile=profile,
                document_map=doc_map,
                tables_index=prevalidated.tables_by_src.get(src.source_id, []),
                approved_scan_pages=approved_pages,
            )

        for (
            rev_id,
            r_kind,
            r_key,
            parent_rev_id,
            payload_obj,
            meta_obj,
        ) in prevalidated.validated_revs:
            state_store.save_revision(
                resolved_pid,
                revision_id=rev_id,
                rev_kind=r_kind,
                rev_key=r_key,
                parent_rev_id=parent_rev_id,
                payload=payload_obj,
                metadata=meta_obj,
            )

        state_store.update_project_pointers(
            resolved_pid,
            active_ontology_rev_id=snapshot.active_ontology_rev_id,
            active_wave_id=snapshot.active_wave_id,
        )

        for mcp_rec in prevalidated.mcp_receipts:
            state_store.append_event_or_receipt(
                resolved_pid, record_kind="mcp_receipt", run_id=None, payload=mcp_rec
            )
            unverified_receipt_ids.append(mcp_rec.receipt_id)

        missing_set = set(missing_source_ids)
        for ocr_rec in prevalidated.ocr_receipts:
            state_store.append_event_or_receipt(
                resolved_pid, record_kind="ocr_receipt", run_id=None, payload=ocr_rec
            )
            dm = prevalidated.doc_maps_by_id.get(ocr_rec.doc_id)
            page_match = (
                dm is not None
                and any(
                    p.page_number == ocr_rec.page_number
                    and p.canonical_text_sha256 == ocr_rec.content_sha256
                    for p in dm.pages
                )
            )
            if ocr_rec.doc_id in missing_set or not page_match:
                unverified_receipt_ids.append(ocr_rec.receipt_id)

        for audit_rec in prevalidated.audit_records:
            state_store.append_event_or_receipt(
                resolved_pid, record_kind="audit_log", run_id=None, payload=audit_rec
            )
    except Exception:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        if project_created:
            state_store.delete_project(resolved_pid)
        if (resolved_conflict == "replaced" or staged_target) and target_project_dir.exists():
            shutil.rmtree(target_project_dir, ignore_errors=True)
        if backup_created and backup_dir is not None and backup_dir.exists():
            replace_with_retry(backup_dir, target_project_dir)
        raise

    if backup_created and backup_dir is not None and backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)

    readiness_flags: list[
        Literal["READY_OFFLINE", "NEEDS_LOCAL_SECRET_BINDING", "METADATA_ONLY_BINARY_ABSENT"]
    ] = ["READY_OFFLINE"]
    if missing_secret_refs:
        readiness_flags.append("NEEDS_LOCAL_SECRET_BINDING")
    if missing_source_ids:
        readiness_flags.append("METADATA_ONLY_BINARY_ABSENT")

    return ImportReadinessReport(
        project_id=resolved_pid,
        resolved_conflict=resolved_conflict,
        readiness_flags=readiness_flags,
        missing_secret_refs=missing_secret_refs,
        missing_source_ids=missing_source_ids,
        unverified_receipt_ids=unverified_receipt_ids,
        audit_claim_notice=(
            "Imported audit logs and external/OCR receipts are producer-asserted claims "
            "protected only by unkeyed bundle SHA-256; verify source digests out-of-band."
        ),
    )


__all__ = [
    "ConflictPolicy",
    "import_project_bundle",
]
