"""Backward-compatible facade for Ontology Studio router modules (`D16`)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from opendv_platform_api.schemas.ontology_studio import StudioSessionDTO
from opendv_platform_api.security.studio_auth import StudioPrincipal
from opendv_platform_api.studio_deps import (
    MAX_BUNDLE_IMPORT_BYTES,
    MAX_SOURCE_UPLOAD_BYTES,
    get_job_supervisor,
    get_session_manager,
    raise_from_opendv_error,
    require_admin,
    require_editor,
    require_editor_or_automated,
    require_principal,
    require_viewer,
)
from opendv_service.studio.delta_normalizer import (
    StudioWorkflowError,
)
from opendv_service.studio.delta_normalizer import (
    normalize_delta_operation_wire as _normalize_delta_operation,
)
from opendv_service.studio.session_export_workflows import (
    import_bundle_sync as _svc_import_bundle_sync,
)
from opendv_service.studio.session_manager import StudioSessionManager

router = APIRouter(prefix="/ontology/studio", tags=["ontology-studio"])


def _import_bundle_sync(
    *,
    tmp_in: Path,
    session_mgr: StudioSessionManager,
    principal: StudioPrincipal,
    new_session_id: str | None,
    acknowledge_unverified_import_attribution: bool,
    acknowledgement_rationale: str | None,
    force: bool,
) -> StudioSessionDTO:
    """Backward-compatible wrapper around `session_export_workflows.import_bundle_sync`."""
    try:
        res = _svc_import_bundle_sync(
            tmp_in=tmp_in,
            session_mgr=session_mgr,
            principal_id=principal.principal,
            principal_role=principal.role,
            new_session_id=new_session_id,
            acknowledge_unverified_import_attribution=acknowledge_unverified_import_attribution,
            acknowledgement_rationale=acknowledgement_rationale,
            force=force,
        )
        return StudioSessionDTO(**res)
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc


__all__ = [
    "MAX_BUNDLE_IMPORT_BYTES",
    "MAX_SOURCE_UPLOAD_BYTES",
    "StudioSessionManager",
    "_import_bundle_sync",
    "_normalize_delta_operation",
    "get_job_supervisor",
    "get_session_manager",
    "require_admin",
    "require_editor",
    "require_editor_or_automated",
    "require_principal",
    "require_viewer",
    "router",
]
