"""Universal source adapter metadata and catalog introspection router (`/api/v1/sources`)."""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from opendv_contracts import RbacRole
from opendv_contracts.dtos.sources import (
    ConnectorUIManifestDTO,
    SourceInspectRequestDTO,
    SourceInspectResponseDTO,
)
from opendv_platform_api.deps import get_opendv_service, get_rbac_role
from opendv_service import OpenDVService
from opendv_service.use_cases.sources_inspect import inspect_source, list_adapters

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("/adapters", response_model=list[ConnectorUIManifestDTO])
def get_source_adapters() -> list[ConnectorUIManifestDTO]:
    """Return declarative UI manifests for all registered connectors."""
    return list_adapters()


@router.post("/inspect", response_model=SourceInspectResponseDTO)
def inspect_data_source(
    req: SourceInspectRequestDTO,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
    _role: Annotated[RbacRole, Depends(get_rbac_role)],
) -> SourceInspectResponseDTO:
    """Inspect and enumerate namespaces, tables, columns, PKs, FKs, and tags from a data source."""
    if _role == RbacRole.PUBLIC:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: unauthenticated public role not authorized for source inspection",
        )
    cas_store = (
        getattr(service, "cas_store", None)
        or getattr(service.state, "cas_store", None)
        or getattr(service.state, "cas", None)
    )
    ws_root = (
        getattr(service, "workspace_root", None)
        or getattr(service.state, "workspace_root", None)
    )
    raw_hosts = (
        getattr(getattr(service, "settings", None), "allowed_hosts", None)
        or os.environ.get("OPENDV_ALLOWED_HOSTS")
        or os.environ.get("OPENDV_ALLOWED_DATABRICKS_HOSTS")
    )
    server_allowed_hosts = None
    if isinstance(raw_hosts, str):
        server_allowed_hosts = [h.strip().lower() for h in raw_hosts.split(",") if h.strip()]
    elif isinstance(raw_hosts, (list, set)):
        server_allowed_hosts = [str(h).strip().lower() for h in raw_hosts if str(h).strip()]

    try:
        return inspect_source(
            req,
            cas_store=cas_store,
            workspace_root=ws_root,
            allowed_hosts=server_allowed_hosts,
        )
    except Exception as exc:
        msg = str(exc)
        if req.credentials_ref and req.credentials_ref in msg:
            msg = msg.replace(req.credentials_ref, "[REDACTED]")
        raise HTTPException(status_code=400, detail=msg) from exc


__all__ = ["router"]
