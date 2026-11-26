"""Step 2-3 source connectors, uploads, profiling, reattach, and detach facade."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.sources import (
    ConnectorAdapterSpec,
    ConnectorConfigPayload,
    DocumentMap,
    PageTextView,
    SourceKind,
    SourceProfile,
    SourceStatus,
    SourceSummary,
    TableIndexEntry,
    TablePreviewPage,
)
from opendv.models.wire_dtos_discovery import (
    ScanApprovalReq,
    SourceAttachReq,
    SourceInspectReq,
    SourceReattachReq,
)
from opendv.service.os_platform import atomic_write_bytes, validate_workspace_containment
from opendv.service.secrets import KeyringSecretStore
from opendv.service.state_store import ProjectStateManager
from opendv.service.workflows._source_docs import (
    DOC_SOURCE_KINDS,
    active_evolution_locks,
    approve_scan_pages,
    as_doc_format,
    as_source_kind,
    get_document_map,
    get_document_pages,
    list_document_maps,
    list_tables_index,
    locked_columns_for_source,
    preview_source_table,
    preview_table_by_id,
    resolve_local_source_path,
)
from opendv.service.workflows._vault_proving import (
    ProvingSampleBatch,
    delete_proving_sample_cache,
    profile_structured_with_proving,
    sync_proving_sample_cache,
)
from opendv.sources import (
    DocumentMapBuilder,
    build_table_index_entries,
    get_source_adapter,
    infer_kind_from_filename,
    list_source_adapters,
    profile_source,
    validate_databricks_host,
    validate_databricks_options,
)

MAX_UPLOAD_BYTES: int = 250 * 1024 * 1024


def list_adapters() -> list[ConnectorAdapterSpec]:
    """Return the catalog of supported structured source connector adapters."""
    return list_source_adapters()


def _check_databricks_binding(
    uri_or_path: str,
    options: dict[str, str | int | bool],
    secret_refs: list[str],
    secret_store: KeyringSecretStore,
) -> list[str]:
    validate_databricks_options(options, secret_refs, require_secret_ref=True)
    validate_databricks_host(uri_or_path, resolve_dns=True)
    return [ref for ref in secret_refs if secret_store.get_secret(ref) is None]


class DiscoverySourcesHelper:
    """Service-layer source attachment, upload, reattach, detach, and doc/table access."""

    def __init__(
        self,
        state_manager: ProjectStateManager,
        secret_store: KeyringSecretStore,
        workspace_root: Path,
    ) -> None:
        self._state = state_manager
        self._secrets = secret_store
        self._workspace_root = workspace_root

    def inspect_source(self, req: SourceInspectReq) -> SourceProfile:
        """Inspect a candidate source before project attachment."""
        kind = as_source_kind(req.kind)
        if kind in DOC_SOURCE_KINDS:
            raise OpenDVError(
                "Document sources must be attached or uploaded rather than inspected as tables.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        get_source_adapter(kind)
        if kind == "databricks_uc":
            missing = _check_databricks_binding(
                req.uri_or_path, req.options, req.secret_refs, self._secrets
            )
            if missing:
                raise OpenDVError(
                    f"Missing required local secret binding(s): {', '.join(missing)}",
                    error_code=ErrorCode.NEEDS_LOCAL_SECRET_BINDING,
                )
            uri = req.uri_or_path.strip()
        else:
            resolved = resolve_local_source_path(req.uri_or_path, self._workspace_root)
            uri = str(resolved)
        return profile_source(
            f"inspect-{uuid.uuid4().hex[:8]}",
            kind,
            uri,
            options=req.options,
            secret_refs=req.secret_refs,
        )

    def attach_source(self, project_id: str, req: SourceAttachReq) -> SourceSummary:
        """Attach an existing workspace file or remote connector to a project."""
        proj = self._state.get_project(project_id)
        proving_limit = proj.settings_snapshot.proving_sample_rows
        real_row = proj.settings_snapshot.real_row_proving
        kind = as_source_kind(req.kind)
        source_id = f"src-{uuid.uuid4().hex[:10]}"
        status: SourceStatus = "READY_OFFLINE"
        binary_sha256: str | None = None
        doc_map: DocumentMap | None = None
        tables_index: list[TableIndexEntry] = []
        proving_batch: ProvingSampleBatch | None = None

        if kind == "databricks_uc":
            adapter_spec = get_source_adapter(kind)
            missing = _check_databricks_binding(
                req.uri_or_filename, req.options, req.secret_refs, self._secrets
            )
            if missing:
                raise OpenDVError(
                    f"Missing required local secret binding(s): {', '.join(missing)}",
                    error_code=ErrorCode.NEEDS_LOCAL_SECRET_BINDING,
                )
            uri_str = req.uri_or_filename.strip()
            profile, proving_batch = profile_structured_with_proving(
                self._secrets,
                project_id,
                source_id,
                kind,
                uri_str,
                proving_sample_rows=proving_limit,
                real_row_proving=real_row,
                options=req.options,
                secret_refs=req.secret_refs,
                selected_tables=req.selected_tables,
                profile_timeout_seconds=req.profile_timeout_seconds,
            )
            tables_index = build_table_index_entries(profile.tables, source_id)
        elif kind in DOC_SOURCE_KINDS:
            local_path = resolve_local_source_path(
                req.uri_or_filename,
                self._workspace_root,
                (self._state.sources_dir(project_id),),
            )
            if not local_path.is_file():
                raise OpenDVError(
                    f"Document source '{req.uri_or_filename}' does not exist.",
                    error_code=ErrorCode.SOURCE_BINARY_ABSENT,
                )
            binary_sha256 = hashlib.sha256(local_path.read_bytes()).hexdigest()
            doc_map, _pages = DocumentMapBuilder().build_from_path(
                source_id=source_id,
                path=local_path,
                doc_format=as_doc_format(kind),
            )
            profile = SourceProfile(
                source_id=source_id, schema_fingerprint="", table_count=0, tables=[]
            )
            uri_str = str(local_path)
            adapter_spec = ConnectorAdapterSpec(
                adapter_id=kind, display_name=kind.upper(), fields=[]
            )
        else:
            adapter_spec = get_source_adapter(kind)
            local_path = resolve_local_source_path(
                req.uri_or_filename,
                self._workspace_root,
                (self._state.sources_dir(project_id),),
            )
            if not local_path.is_file():
                raise OpenDVError(
                    f"Source file '{req.uri_or_filename}' does not exist.",
                    error_code=ErrorCode.SOURCE_BINARY_ABSENT,
                )
            binary_sha256 = hashlib.sha256(local_path.read_bytes()).hexdigest()
            uri_str = str(local_path)
            profile, proving_batch = profile_structured_with_proving(
                self._secrets,
                project_id,
                source_id,
                kind,
                uri_str,
                proving_sample_rows=proving_limit,
                real_row_proving=real_row,
                options=req.options,
                secret_refs=req.secret_refs,
                selected_tables=req.selected_tables,
                profile_timeout_seconds=req.profile_timeout_seconds,
            )
            tables_index = build_table_index_entries(profile.tables, source_id)

        if proving_batch is not None:
            sync_proving_sample_cache(self._state, proving_batch)
        cfg = ConnectorConfigPayload(
            adapter_id=adapter_spec.adapter_id,
            display_name=adapter_spec.display_name,
            uri_or_path=uri_str,
            options=dict(req.options),
            secret_refs=list(req.secret_refs),
            selected_tables=req.selected_tables,
            profile_timeout_seconds=req.profile_timeout_seconds,
        )
        return self._state.upsert_source(
            project_id,
            source_id=source_id,
            kind=kind,
            uri_or_filename=uri_str,
            binary_sha256=binary_sha256,
            connector_config=cfg,
            status=status,
            profile=profile,
            document_map=doc_map,
            tables_index=tables_index,
        )

    def upload_source(
        self,
        project_id: str,
        *,
        filename: str,
        content: bytes,
        kind: str | None = None,
    ) -> SourceSummary:
        """Persist an uploaded source binary (<= 250 MB), profile/map it, and register it."""
        proj = self._state.get_project(project_id)
        proving_limit = proj.settings_snapshot.proving_sample_rows
        real_row = proj.settings_snapshot.real_row_proving
        if len(content) > MAX_UPLOAD_BYTES:
            raise OpenDVError(
                f"Uploaded source '{filename}' exceeds 250 MB limit ({len(content)} bytes).",
                error_code=ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED,
            )
        safe_name = Path(filename).name
        if not safe_name or safe_name in {".", ".."}:
            raise OpenDVError(
                f"Invalid uploaded filename '{filename}'.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        resolved_kind: SourceKind = (
            as_source_kind(kind)
            if kind is not None
            else as_source_kind(infer_kind_from_filename(safe_name))
        )
        source_id = f"src-{uuid.uuid4().hex[:10]}"
        src_dir = self._state.sources_dir(project_id) / source_id
        src_dir.mkdir(parents=True, exist_ok=True)
        dest = validate_workspace_containment(src_dir / safe_name, allowed_roots=(src_dir,))
        atomic_write_bytes(dest, content)
        binary_sha256 = hashlib.sha256(content).hexdigest()
        relpath = f"sources/{source_id}/{safe_name}"

        doc_map: DocumentMap | None = None
        tables_index: list[TableIndexEntry] = []
        adapter_id: str
        display_name: str
        if resolved_kind in DOC_SOURCE_KINDS:
            doc_map, _pages = DocumentMapBuilder().build_from_bytes(
                source_id=source_id,
                filename=safe_name,
                doc_format=as_doc_format(resolved_kind),
                content=content,
            )
            profile = SourceProfile(
                source_id=source_id, schema_fingerprint="", table_count=0, tables=[]
            )
            adapter_id = str(resolved_kind)
            display_name = str(resolved_kind).upper()
        else:
            adapter_spec = get_source_adapter(resolved_kind)
            adapter_id = adapter_spec.adapter_id
            display_name = adapter_spec.display_name
            profile, proving_batch = profile_structured_with_proving(
                self._secrets,
                project_id,
                source_id,
                resolved_kind,
                str(dest),
                proving_sample_rows=proving_limit,
                real_row_proving=real_row,
            )
            tables_index = build_table_index_entries(profile.tables, source_id)
            sync_proving_sample_cache(self._state, proving_batch)

        cfg = ConnectorConfigPayload(
            adapter_id=adapter_id,
            display_name=display_name,
            uri_or_path=str(dest),
            uploaded_relpath=relpath,
        )
        return self._state.upsert_source(
            project_id,
            source_id=source_id,
            kind=resolved_kind,
            uri_or_filename=safe_name,
            binary_sha256=binary_sha256,
            connector_config=cfg,
            status="READY_OFFLINE",
            profile=profile,
            document_map=doc_map,
            tables_index=tables_index,
        )

    def list_sources(self, project_id: str) -> list[SourceSummary]:
        """List all attached sources for a project."""
        self._state.get_project(project_id)
        return self._state.list_sources(project_id)

    def reattach_source(
        self,
        project_id: str,
        source_id: str,
        req: SourceReattachReq,
    ) -> SourceSummary:
        """Reattach/update a source, enforcing evolution_locks column compatibility."""
        proj = self._state.get_project(project_id)
        proving_limit = proj.settings_snapshot.proving_sample_rows
        real_row = proj.settings_snapshot.real_row_proving
        rec = self._state.get_source_record(project_id, source_id)
        cfg = ConnectorConfigPayload.model_validate(rec["connector_config"])
        kind = as_source_kind(str(rec["kind"]))
        prior_profile = SourceProfile.model_validate(rec["profile"])

        new_uri = req.uri_or_filename if req.uri_or_filename is not None else cfg.uri_or_path
        new_uploaded_relpath: str | None = cfg.uploaded_relpath
        new_opts = req.options if req.options is not None else dict(cfg.options)
        new_refs = req.secret_refs if req.secret_refs is not None else list(cfg.secret_refs)
        new_tables = (
            req.selected_tables if req.selected_tables is not None else cfg.selected_tables
        )
        binary_sha256: str | None = rec["binary_sha256"]
        doc_map: DocumentMap | None = (
            DocumentMap.model_validate(rec["document_map"])
            if rec["document_map"] is not None
            else None
        )
        tables_index = [TableIndexEntry.model_validate(t) for t in rec["tables_index"]]
        proving_batch: ProvingSampleBatch | None = None

        if kind == "databricks_uc":
            if req.uri_or_filename is not None:
                new_uploaded_relpath = None
            missing = _check_databricks_binding(new_uri, new_opts, new_refs, self._secrets)
            if missing:
                raise OpenDVError(
                    f"Missing required local secret binding(s): {', '.join(missing)}",
                    error_code=ErrorCode.NEEDS_LOCAL_SECRET_BINDING,
                )
            new_profile, proving_batch = profile_structured_with_proving(
                self._secrets,
                project_id,
                source_id,
                kind,
                new_uri,
                proving_sample_rows=proving_limit,
                real_row_proving=real_row,
                options=new_opts,
                secret_refs=new_refs,
                selected_tables=new_tables,
                profile_timeout_seconds=cfg.profile_timeout_seconds,
            )
            tables_index = build_table_index_entries(new_profile.tables, source_id)
        elif kind in DOC_SOURCE_KINDS:
            local_path = resolve_local_source_path(
                new_uri, self._workspace_root, (self._state.sources_dir(project_id),)
            )
            if not local_path.is_file():
                raise OpenDVError(
                    f"Document source '{new_uri}' does not exist.",
                    error_code=ErrorCode.SOURCE_BINARY_ABSENT,
                )
            binary_sha256 = hashlib.sha256(local_path.read_bytes()).hexdigest()
            approved = list(rec["approved_scan_pages"])
            doc_map, _pages = DocumentMapBuilder().build_from_path(
                source_id=source_id,
                path=local_path,
                doc_format=as_doc_format(kind),
                approved_scan_pages=approved,
            )
            new_profile = prior_profile
            new_uri = str(local_path)
            if req.uri_or_filename is not None:
                project_dir = self._state.project_dir(project_id).resolve()
                resolved_local = local_path.resolve()
                new_uploaded_relpath = (
                    resolved_local.relative_to(project_dir).as_posix()
                    if resolved_local.is_relative_to(project_dir)
                    else None
                )
        else:
            local_path = resolve_local_source_path(
                new_uri, self._workspace_root, (self._state.sources_dir(project_id),)
            )
            if not local_path.is_file():
                raise OpenDVError(
                    f"Source file '{new_uri}' does not exist.",
                    error_code=ErrorCode.SOURCE_BINARY_ABSENT,
                )
            binary_sha256 = hashlib.sha256(local_path.read_bytes()).hexdigest()
            new_uri = str(local_path)
            if req.uri_or_filename is not None:
                project_dir = self._state.project_dir(project_id).resolve()
                resolved_local = local_path.resolve()
                new_uploaded_relpath = (
                    resolved_local.relative_to(project_dir).as_posix()
                    if resolved_local.is_relative_to(project_dir)
                    else None
                )
            new_profile, proving_batch = profile_structured_with_proving(
                self._secrets,
                project_id,
                source_id,
                kind,
                new_uri,
                proving_sample_rows=proving_limit,
                real_row_proving=real_row,
                options=new_opts,
                secret_refs=new_refs,
                selected_tables=new_tables,
                profile_timeout_seconds=cfg.profile_timeout_seconds,
            )
            locks = active_evolution_locks(self._state, project_id)
            locked_cols = locked_columns_for_source(locks, source_id, prior_profile.tables)
            by_table_id = {t.table_id: {c.col_name for c in t.columns} for t in new_profile.tables}
            for table_id, req_cols in locked_cols.items():
                present_cols = by_table_id.get(table_id)
                if present_cols is None:
                    raise OpenDVError(
                        f"Reattached source omits committed vault table '{table_id}'.",
                        error_code=ErrorCode.SOURCE_COMMITTED_IN_VAULT,
                    )
                missing_cols = [col for col in req_cols if col not in present_cols]
                if missing_cols:
                    raise OpenDVError(
                        f"Reattached table '{table_id}' omits locked columns: {missing_cols}",
                        error_code=ErrorCode.SOURCE_COMMITTED_IN_VAULT,
                    )
            tables_index = build_table_index_entries(new_profile.tables, source_id)

        if proving_batch is not None:
            sync_proving_sample_cache(self._state, proving_batch)
        updated_cfg = ConnectorConfigPayload(
            adapter_id=cfg.adapter_id,
            display_name=cfg.display_name,
            uri_or_path=new_uri,
            options=new_opts,
            secret_refs=new_refs,
            uploaded_relpath=new_uploaded_relpath,
            selected_tables=new_tables,
            profile_timeout_seconds=cfg.profile_timeout_seconds,
        )
        return self._state.upsert_source(
            project_id,
            source_id=source_id,
            kind=kind,
            uri_or_filename=new_uri,
            binary_sha256=binary_sha256,
            connector_config=updated_cfg,
            status="READY_OFFLINE",
            profile=new_profile,
            document_map=doc_map,
            tables_index=tables_index,
            approved_scan_pages=list(rec["approved_scan_pages"]),
        )

    def detach_source(self, project_id: str, source_id: str) -> None:
        """Detach a source unless its tables/columns are locked in a committed Vault wave."""
        rec = self._state.get_source_record(project_id, source_id)
        profile = SourceProfile.model_validate(rec["profile"])
        locks = active_evolution_locks(self._state, project_id)
        if locked_columns_for_source(locks, source_id, profile.tables):
            raise OpenDVError(
                f"Cannot detach source '{source_id}': tables are committed in the Vault.",
                error_code=ErrorCode.SOURCE_COMMITTED_IN_VAULT,
            )
        self._state.delete_source(project_id, source_id)
        delete_proving_sample_cache(self._state, project_id, source_id)

    def list_tables_index(self, project_id: str) -> list[TableIndexEntry]:
        return list_tables_index(self._state, project_id)

    def preview_table(
        self,
        project_id: str,
        source_id: str,
        table_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> TablePreviewPage:
        return preview_source_table(
            self._state,
            self._secrets,
            self._workspace_root,
            project_id,
            source_id,
            table_id,
            offset=offset,
            limit=limit,
        )

    def preview_table_by_id(
        self,
        project_id: str,
        table_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
        source_id: str | None = None,
    ) -> TablePreviewPage:
        return preview_table_by_id(
            self._state,
            self._secrets,
            self._workspace_root,
            project_id,
            table_id,
            offset=offset,
            limit=limit,
            source_id=source_id,
        )

    def list_document_maps(self, project_id: str) -> list[DocumentMap]:
        return list_document_maps(self._state, project_id)

    def get_document_map(self, project_id: str, source_id: str) -> DocumentMap:
        return get_document_map(self._state, project_id, source_id)

    def get_document_pages(
        self,
        project_id: str,
        source_id: str,
        *,
        page_start: int = 1,
        page_end: int | None = None,
    ) -> list[PageTextView]:
        return get_document_pages(
            self._state,
            self._workspace_root,
            project_id,
            source_id,
            page_start=page_start,
            page_end=page_end,
        )

    def approve_scan_pages(
        self, project_id: str, source_id: str, req: ScanApprovalReq
    ) -> DocumentMap:
        return approve_scan_pages(self._state, project_id, source_id, req)
