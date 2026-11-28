"""Source path resolution, table indexing/preview, DocumentMap, and scan consent helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, cast

from opendv.models.document_ir import (
    DOC_SOURCE_KINDS as DOC_SOURCE_KINDS,
)
from opendv.models.document_ir import ParseOptions, SourceBlob
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.project import EvolutionLockSpec, SecretPrecedence
from opendv.models.sources import (
    ConnectorConfigPayload,
    DocumentMap,
    PageTextView,
    SourceKind,
    SourceProfile,
    TableIndexEntry,
    TablePreviewPage,
    TableProfile,
)
from opendv.models.wire_dtos_discovery import ScanApprovalReq
from opendv.service._remediation import build_remediation
from opendv.service._state_ddl import VALID_SOURCE_KINDS
from opendv.service.os_platform import validate_workspace_containment
from opendv.service.parsing_svc import ParsingService
from opendv.service.secrets import KeyringSecretStore
from opendv.service.state_store import ProjectStateManager
from opendv.sources import (
    build_table_index_entries,
    cluster_tables,
    profile_source,
    validate_databricks_host,
    validate_databricks_options,
)
from opendv.sources import (
    preview_table as run_table_preview,
)


def as_doc_format(kind: str) -> Literal["pdf", "markdown", "docx", "domain_note"]:
    if kind == "pdf":
        return "pdf"
    if kind == "markdown":
        return "markdown"
    if kind == "docx":
        return "docx"
    if kind == "domain_note":
        return "domain_note"
    raise OpenDVError(
        f"Unsupported document kind '{kind}'.",
        error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
    )


def as_source_kind(kind: str) -> SourceKind:
    if kind in VALID_SOURCE_KINDS:
        return cast(SourceKind, kind)
    raise OpenDVError(
        f"Unsupported source kind '{kind}'.",
        error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
    )


def resolve_local_source_path(
    uri_or_path: str,
    workspace_root: Path,
    extra_roots: tuple[Path, ...] = (),
) -> Path:
    raw = uri_or_path.strip()
    if not raw:
        raise OpenDVError(
            "Source path cannot be empty.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    cand = Path(raw)
    if not cand.is_absolute():
        cand = workspace_root / cand
    env_val = os.environ.get("OPENDV_WORKSPACE_ROOTS", "").strip()
    if env_val:
        env_roots = tuple(Path(item.strip()) for item in env_val.split(os.pathsep) if item.strip())
        roots = (*env_roots, *extra_roots)
    else:
        roots = (workspace_root, *extra_roots)
    return validate_workspace_containment(cand, allowed_roots=roots)


def active_evolution_locks(
    state_manager: ProjectStateManager,
    project_id: str,
) -> list[EvolutionLockSpec]:
    revs = state_manager.list_revisions(project_id, rev_kind="evolution_lock")
    return [EvolutionLockSpec.model_validate(row["payload"]) for row in revs]


def locked_columns_for_source(
    locks: list[EvolutionLockSpec],
    source_id: str,
    tables: list[TableProfile],
) -> dict[str, list[str]]:
    locked: dict[str, list[str]] = {}
    prefix = f"{source_id}:"
    known_ids = {t.table_id for t in tables}
    for lock in locks:
        for table_id, cols in lock.locked_source_columns.items():
            if table_id.startswith(prefix) or table_id in known_ids:
                existing = set(locked.get(table_id, []))
                existing.update(cols)
                locked[table_id] = sorted(existing)
    return locked


def resolve_readable_source_path(
    state_manager: ProjectStateManager,
    workspace_root: Path,
    project_id: str,
    rec: dict[str, object],
) -> Path | None:
    cfg = ConnectorConfigPayload.model_validate(rec["connector_config"])
    if cfg.uploaded_relpath:
        proj_dir = state_manager.project_dir(project_id)
        cand = validate_workspace_containment(
            proj_dir / cfg.uploaded_relpath, allowed_roots=(proj_dir,)
        )
        if cand.is_file():
            return cand
    raw = str(cfg.uri_or_path or rec["uri_or_filename"])
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            p = workspace_root / p
        if p.is_file():
            return p
    return None


def load_doc_pages_for_record(
    state_manager: ProjectStateManager,
    workspace_root: Path,
    project_id: str,
    source_id: str,
    rec: dict[str, object],
    dmap: DocumentMap,
    *,
    parsing_service: ParsingService | None = None,
) -> dict[int, str] | None:
    """Resolve and extract 1-based normalized pages for a stored document if binary exists."""
    _ = source_id, dmap
    disk_path = resolve_readable_source_path(state_manager, workspace_root, project_id, rec)
    if disk_path is None:
        return None
    svc = parsing_service or ParsingService(
        workspace_root=workspace_root,
        allowed_roots=[workspace_root, state_manager.sources_dir(project_id), disk_path.parent],
    )
    ir = svc.parse_document(
        SourceBlob.from_path(disk_path),
        sink_class="LOCAL_HUMAN_IO",
        redaction="off",
    )
    return {pg.page_number: pg.canonical_text for pg in ir.pages}


def list_tables_index(
    state_manager: ProjectStateManager,
    project_id: str,
) -> list[TableIndexEntry]:
    """Return MinHash-clustered table index entries across all project sources."""
    state_manager.get_project(project_id)
    sources = state_manager.list_sources(project_id)
    all_tables: list[tuple[str, TableProfile]] = []
    for s in sources:
        rec = state_manager.get_source_record(project_id, s.source_id)
        prof = (
            rec["profile"]
            if isinstance(rec["profile"], SourceProfile)
            else SourceProfile.model_validate(rec["profile"])
        )
        for tbl in prof.tables:
            all_tables.append((s.source_id, tbl))
    if not all_tables:
        return []
    if len(sources) == 1 and all(tbl.cluster_id for _sid, tbl in all_tables):
        cluster_map = {tbl.table_id: tbl.cluster_id or "cluster-0" for _sid, tbl in all_tables}
    else:
        cluster_map = cluster_tables([t for _sid, t in all_tables]).table_to_cluster
    locks = active_evolution_locks(state_manager, project_id)
    locked_cols: dict[str, set[str]] = {}
    locked_wave_by_tbl: dict[str, int] = {}
    for lk in locks:
        for tid, cols in lk.locked_source_columns.items():
            locked_cols.setdefault(tid, set()).update(cols)
            if lk.wave_number > locked_wave_by_tbl.get(tid, 0):
                locked_wave_by_tbl[tid] = lk.wave_number
    badge_map: dict[str, Literal["NEW", "UPDATED", "UNCHANGED"]] = {}
    for _sid, tbl in all_tables:
        if tbl.table_id in locked_cols:
            cur_cols = {c.col_name for c in tbl.columns}
            badge_map[tbl.table_id] = (
                "UNCHANGED" if cur_cols == locked_cols[tbl.table_id] else "UPDATED"
            )
        else:
            badge_map[tbl.table_id] = "NEW"
    out: list[TableIndexEntry] = []
    for sid, tbl in all_tables:
        tbl_wave = locked_wave_by_tbl.get(tbl.table_id)
        modeled_ver = f"v{tbl_wave}" if tbl_wave is not None else None
        out.extend(
            build_table_index_entries(
                [tbl],
                sid,
                badge_map=badge_map,
                modeled_version=modeled_ver,
                precomputed_clusters=cluster_map,
            )
        )
    return out


def attach_driver_remediation(exc: OpenDVError) -> OpenDVError:
    """Attach DRIVER_MISSING remediation when absent without mutating other errors."""
    if exc.error_code == ErrorCode.DRIVER_MISSING and exc.remediation is None:
        return OpenDVError(
            exc.detail,
            error_code=exc.error_code,
            diagnostics=exc.diagnostics,
            defect_report=exc.defect_report,
            run_id=exc.run_id,
            remediation=build_remediation(ErrorCode.DRIVER_MISSING),
        )
    return exc


def preview_source_table(
    state_manager: ProjectStateManager,
    secret_store: KeyringSecretStore,
    workspace_root: Path,
    project_id: str,
    source_id: str,
    table_id: str,
    *,
    offset: int = 0,
    limit: int = 50,
) -> TablePreviewPage:
    """Return masked table preview, with offline metadata-only fallback if binary absent."""
    if offset < 0 or limit < 1:
        raise OpenDVError(
            f"Invalid pagination parameters offset={offset}, limit={limit}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    proj = state_manager.get_project(project_id)
    rec = state_manager.get_source_record(project_id, source_id)
    profile = SourceProfile.model_validate(rec["profile"])
    target_tbl = next(
        (t for t in profile.tables if t.table_id == table_id or t.table_name == table_id),
        None,
    )
    if target_tbl is None:
        raise OpenDVError(
            f"Table '{table_id}' not found in source '{source_id}'.",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )
    kind = as_source_kind(str(rec["kind"]))
    status = str(rec["status"])
    cfg = ConnectorConfigPayload.model_validate(rec["connector_config"])
    access_token: str | None = None
    if status == "NEEDS_LOCAL_SECRET_BINDING" or (kind == "databricks_uc" and cfg.secret_refs):
        proj_prec = proj.settings_snapshot.secret_precedence
        missing: list[str] = []
        resolved_tokens: list[str] = []
        for ref in cfg.secret_refs:
            val = secret_store.get_secret(ref, project_precedence=proj_prec)
            if val is None:
                missing.append(ref)
            else:
                resolved_tokens.append(val)
        if missing:
            raise OpenDVError(
                f"Missing secret binding(s) for source '{source_id}': {', '.join(missing)}",
                error_code=ErrorCode.NEEDS_LOCAL_SECRET_BINDING,
                remediation=build_remediation(
                    ErrorCode.NEEDS_LOCAL_SECRET_BINDING,
                    missing_secrets=missing,
                ),
            )
        if resolved_tokens:
            access_token = resolved_tokens[0]
    if kind == "databricks_uc":
        try:
            return run_table_preview(
                source_id,
                kind,
                cfg.uri_or_path,
                target_tbl.table_name,
                options=cfg.options,
                secret_refs=cfg.secret_refs,
                offset=offset,
                limit=limit,
                profile_timeout_seconds=cfg.profile_timeout_seconds,
                access_token=access_token,
            )
        except OpenDVError as exc:
            raise attach_driver_remediation(exc) from exc
    disk_path = (
        None
        if status == "METADATA_ONLY_BINARY_ABSENT"
        else resolve_readable_source_path(state_manager, workspace_root, project_id, rec)
    )
    if disk_path is None:
        cached_count = max((len(c.sample_values) for c in target_tbl.columns), default=0)
        cached_rows: list[list[str | None]] = [
            [
                c.sample_values[i] if i < len(c.sample_values) else None
                for c in target_tbl.columns
            ]
            for i in range(cached_count)
        ]
        return TablePreviewPage(
            table_id=target_tbl.table_id,
            source_id=source_id,
            columns=[c.col_name for c in target_tbl.columns],
            rows=cached_rows[offset : offset + limit],
            total_rows=target_tbl.row_count_est,
        )
    return run_table_preview(
        source_id,
        kind,
        str(disk_path),
        target_tbl.table_name,
        options=cfg.options,
        secret_refs=cfg.secret_refs,
        offset=offset,
        limit=limit,
        profile_timeout_seconds=cfg.profile_timeout_seconds,
    )


def preview_table_by_id(
    state_manager: ProjectStateManager,
    secret_store: KeyringSecretStore,
    workspace_root: Path,
    project_id: str,
    table_id: str,
    *,
    offset: int = 0,
    limit: int = 50,
    source_id: str | None = None,
) -> TablePreviewPage:
    """Resolve source_id (when omitted) for table_id and return masked TablePreviewPage."""
    if offset < 0 or limit < 1:
        raise OpenDVError(
            f"Invalid pagination parameters offset={offset}, limit={limit}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    resolved_source_id = source_id
    if resolved_source_id is None:
        state_manager.get_project(project_id)
        name_match: str | None = None
        for src in state_manager.list_sources(project_id):
            rec = state_manager.get_source_record(project_id, src.source_id)
            profile = SourceProfile.model_validate(rec["profile"])
            for tbl in profile.tables:
                if tbl.table_id == table_id:
                    resolved_source_id = src.source_id
                    break
                if name_match is None and tbl.table_name == table_id:
                    name_match = src.source_id
            if resolved_source_id is not None:
                break
        if resolved_source_id is None:
            resolved_source_id = name_match
    if resolved_source_id is None:
        raise OpenDVError(
            f"Table '{table_id}' not found in project '{project_id}'.",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )
    return preview_source_table(
        state_manager,
        secret_store,
        workspace_root,
        project_id,
        resolved_source_id,
        table_id,
        offset=offset,
        limit=limit,
    )


def list_document_maps(
    state_manager: ProjectStateManager,
    project_id: str,
) -> list[DocumentMap]:
    """List persisted DocumentMap records across all unstructured sources in project_id."""
    state_manager.get_project(project_id)
    maps: list[DocumentMap] = []
    for src in state_manager.list_sources(project_id):
        rec = state_manager.get_source_record(project_id, src.source_id)
        if rec["document_map"] is not None:
            maps.append(DocumentMap.model_validate(rec["document_map"]))
    return maps


def get_document_map(
    state_manager: ProjectStateManager,
    project_id: str,
    source_id: str,
) -> DocumentMap:
    """Return persisted DocumentMap for an attached unstructured source."""
    rec = state_manager.get_source_record(project_id, source_id)
    if rec["document_map"] is None:
        raise OpenDVError(
            f"Source '{source_id}' does not have a DocumentMap.",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )
    return DocumentMap.model_validate(rec["document_map"])


def get_document_pages(
    state_manager: ProjectStateManager,
    workspace_root: Path,
    project_id: str,
    source_id: str,
    *,
    page_start: int = 1,
    page_end: int | None = None,
    parsing_service: ParsingService | None = None,
) -> list[PageTextView]:
    """Return masked page text views, blocking absent binaries and unapproved scans."""
    rec = state_manager.get_source_record(project_id, source_id)
    if rec["document_map"] is None:
        raise OpenDVError(
            f"Source '{source_id}' does not have a DocumentMap.",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )
    doc_map = DocumentMap.model_validate(rec["document_map"])
    if str(rec["status"]) == "METADATA_ONLY_BINARY_ABSENT":
        raise OpenDVError(
            f"Document binary for source '{source_id}' is absent (metadata-only import).",
            error_code=ErrorCode.SOURCE_BINARY_ABSENT,
        )
    disk_path = resolve_readable_source_path(state_manager, workspace_root, project_id, rec)
    if disk_path is None:
        raise OpenDVError(
            f"Document binary for source '{source_id}' cannot be located on disk.",
            error_code=ErrorCode.SOURCE_BINARY_ABSENT,
        )
    approved = {int(p) for p in rec["approved_scan_pages"]} | set(doc_map.approved_scan_pages)
    end = min(page_end, doc_map.page_count) if page_end is not None else doc_map.page_count
    if page_start < 1 or end < page_start:
        raise OpenDVError(
            f"Invalid page range [{page_start}, {page_end}] for '{source_id}'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    for page in doc_map.pages:
        if (
            page_start <= page.page_number <= end
            and page.is_scanned_or_low_density
            and page.page_number not in approved
        ):
            raise OpenDVError(
                f"Scanned page {page.page_number} of '{source_id}' requires Steward consent.",
                error_code=ErrorCode.UNREDACTED_SCAN_BLOCKED,
            )
    svc = parsing_service or ParsingService(
        workspace_root=workspace_root,
        allowed_roots=[workspace_root, state_manager.sources_dir(project_id), disk_path.parent],
    )
    is_pdf = doc_map.format == "pdf"
    ir = svc.parse_document(
        SourceBlob.from_path(disk_path),
        options=ParseOptions(pages=list(range(page_start, end + 1))) if is_pdf else None,
        sink_class="MODEL_EGRESS" if is_pdf else "LOCAL_HUMAN_IO",
        redaction="on" if is_pdf else "off",
    )
    proj = svc.export_ir(
        ir,
        target="document_map",
        sink_class="MODEL_EGRESS",
        redaction="on",
        doc_id=source_id,
        filename=doc_map.filename,
        approved_scan_pages=sorted(approved),
    )
    assert isinstance(proj, dict)
    views = cast(list[PageTextView], proj["page_text_views"])
    return views if is_pdf else [v for v in views if page_start <= v.page_number <= end]


def approve_scan_pages(
    state_manager: ProjectStateManager,
    project_id: str,
    source_id: str,
    req: ScanApprovalReq,
) -> DocumentMap:
    """Record Steward consent for scanned PDF pages and refresh DocumentMap."""
    rec = state_manager.get_source_record(project_id, source_id)
    if rec["document_map"] is None:
        raise OpenDVError(
            f"Source '{source_id}' does not have a DocumentMap.",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )
    doc_map = DocumentMap.model_validate(rec["document_map"])
    if any(p < 1 or p > doc_map.page_count for p in req.page_numbers):
        raise OpenDVError(
            f"Page numbers {req.page_numbers} exceed bounds (1..{doc_map.page_count}).",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    merged = sorted({int(p) for p in rec["approved_scan_pages"]} | set(req.page_numbers))
    updated_map = doc_map.model_copy(update={"approved_scan_pages": merged})
    cfg = ConnectorConfigPayload.model_validate(rec["connector_config"])
    profile = SourceProfile.model_validate(rec["profile"])
    tables_index = [TableIndexEntry.model_validate(t) for t in rec["tables_index"]]
    state_manager.upsert_source(
        project_id,
        source_id=source_id,
        kind=str(rec["kind"]),
        uri_or_filename=str(rec["uri_or_filename"]),
        binary_sha256=rec["binary_sha256"],
        connector_config=cfg,
        status=rec["status"],
        profile=profile,
        document_map=updated_map,
        tables_index=tables_index,
        approved_scan_pages=merged,
    )
    return updated_map


def check_databricks_binding(
    uri_or_path: str,
    options: dict[str, str | int | bool],
    secret_refs: list[str],
    secret_store: KeyringSecretStore,
    *,
    project_precedence: SecretPrecedence | None = None,
) -> tuple[str, str]:
    """Validate Databricks connector options, host, and resolve required token secret."""
    if "host" in options and not isinstance(options["host"], str):
        raise OpenDVError(
            "Databricks connector option 'host' must be a string.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    try:
        validate_databricks_options(options, secret_refs, require_secret_ref=True)
    except OpenDVError as exc:
        if exc.error_code == ErrorCode.NEEDS_LOCAL_SECRET_BINDING and exc.remediation is None:
            raise OpenDVError(
                exc.detail,
                error_code=exc.error_code,
                remediation=build_remediation(
                    ErrorCode.NEEDS_LOCAL_SECRET_BINDING,
                    missing_secrets=secret_refs,
                ),
            ) from exc
        raise
    raw_opt_host = options.get("host")
    opt_host = raw_opt_host.strip() if isinstance(raw_opt_host, str) else ""
    uri_stripped = uri_or_path.strip()
    eff_host = opt_host or uri_stripped
    validate_databricks_host(eff_host, resolve_dns=True)
    if opt_host and uri_stripped and uri_stripped != opt_host:
        validate_databricks_host(uri_stripped, resolve_dns=True)
    missing: list[str] = []
    tokens: list[str] = []
    for ref in secret_refs:
        val = secret_store.get_secret(ref, project_precedence=project_precedence)
        if val is None:
            missing.append(ref)
        else:
            tokens.append(val)
    if missing:
        raise OpenDVError(
            f"Missing required local secret binding(s): {', '.join(missing)}",
            error_code=ErrorCode.NEEDS_LOCAL_SECRET_BINDING,
            remediation=build_remediation(
                ErrorCode.NEEDS_LOCAL_SECRET_BINDING,
                missing_secrets=missing,
            ),
        )
    return eff_host, tokens[0]


def profile_with_remediation(
    source_id: str,
    kind: SourceKind,
    uri: str,
    *,
    options: dict[str, str | int | bool],
    secret_refs: list[str],
    selected_tables: list[str] | None = None,
    profile_timeout_seconds: int = 30,
    access_token: str | None = None,
) -> SourceProfile:
    """Profile structured source and enrich missing-driver errors with remediation."""
    try:
        return profile_source(
            source_id,
            kind,
            uri,
            options=options,
            secret_refs=secret_refs,
            selected_tables=selected_tables,
            profile_timeout_seconds=profile_timeout_seconds,
            access_token=access_token,
        )
    except OpenDVError as exc:
        raise attach_driver_remediation(exc) from exc
