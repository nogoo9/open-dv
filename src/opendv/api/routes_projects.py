"""Group 2 API routes (Projects, Seeds, Starter Packs, Bundles) + System & Source routers."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request, Response

from opendv.models.project import (
    ImportReadinessReport,
    ProjectSettingsPatchReq,
    ProjectState,
    ProjectSummary,
)
from opendv.models.wire_dtos_discovery import (
    ProjectInitReq,
    StarterPackPreview,
    StarterPackSummary,
    WorkspaceSeedSummary,
)

from ._route_helpers import dual_request_body_openapi, get_service, handle_project_import
from ._routes_sources import router as sources_router
from ._routes_system import router as system_router

router = APIRouter()
router.include_router(system_router)

_IMPORT_PROJECT_OPENAPI = dual_request_body_openapi(
    "ProjectImportPathReq",
    {
        "conflict": {
            "type": "string",
            "enum": ["fail", "rename", "replace"],
            "default": "fail",
        },
        "trust_workspace": {"type": "boolean", "default": False},
    },
)


@router.get("/projects", response_model=list[ProjectSummary])
def list_projects(request: Request) -> list[ProjectSummary]:
    return get_service(request).list_projects()


@router.post("/projects", response_model=ProjectState)
def init_project(request: Request, body: ProjectInitReq) -> ProjectState:
    return get_service(request).init_project(body)


@router.post(
    "/projects/import",
    response_model=ImportReadinessReport,
    openapi_extra=_IMPORT_PROJECT_OPENAPI,
)
async def import_project(request: Request) -> ImportReadinessReport:
    svc = get_service(request)
    (
        bundle_path,
        conflict,
        trust_ws,
        temp_file,
        allowed_roots,
        is_remote,
        enforce_containment,
    ) = await handle_project_import(request, svc)
    try:
        return svc.import_bundle(
            bundle_path,
            conflict=conflict,
            trust_workspace=trust_ws,
            enforce_workspace_containment=enforce_containment,
            is_remote_request=is_remote,
            allowed_roots=allowed_roots,
        )
    finally:
        if temp_file is not None and temp_file.is_file():
            temp_file.unlink(missing_ok=True)


@router.get("/projects/{project_id}", response_model=ProjectState)
def get_project(request: Request, project_id: str) -> ProjectState:
    return get_service(request).get_project(project_id)


@router.patch("/projects/{project_id}/settings", response_model=ProjectState)
def patch_project_settings(
    request: Request, project_id: str, body: ProjectSettingsPatchReq
) -> ProjectState:
    return get_service(request).update_project_settings(project_id, body)


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(request: Request, project_id: str) -> Response:
    get_service(request).delete_project(project_id)
    return Response(status_code=204)


@router.get(
    "/projects/{project_id}/bundle",
    responses={
        200: {
            "content": {
                "application/zip": {"schema": {"type": "string", "format": "binary"}}
            }
        }
    },
)
def export_bundle(
    request: Request,
    project_id: str,
    mode: Literal["core_project_state", "full_demo"] = Query(default="core_project_state"),
) -> Response:
    zip_path, _manifest = get_service(request).export_bundle(project_id, mode=mode)
    payload = zip_path.read_bytes()
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_path.name}"'},
    )


@router.get("/workspace-seeds", response_model=list[WorkspaceSeedSummary])
def list_workspace_seeds(
    request: Request,
    domain_namespace: str | None = Query(default=None),
) -> list[WorkspaceSeedSummary]:
    return get_service(request).list_workspace_seeds(domain_namespace=domain_namespace)


@router.get("/starter-packs", response_model=list[StarterPackSummary])
def list_starter_packs(request: Request) -> list[StarterPackSummary]:
    return get_service(request).list_starter_packs()


@router.get("/starter-packs/{pack_id}/preview", response_model=StarterPackPreview)
def preview_starter_pack(request: Request, pack_id: str) -> StarterPackPreview:
    return get_service(request).preview_starter_pack(pack_id)


router.include_router(sources_router)

__all__ = ["router"]
