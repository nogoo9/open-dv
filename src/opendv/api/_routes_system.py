"""Group 1 API routes: System health, settings, secrets, external MCP registry, agent packs."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from opendv.models.project import WorkspaceSettings
from opendv.models.sources import ExternalMcpServerSpec
from opendv.models.wire_dtos_discovery import (
    AgentPackDetail,
    AgentPackSummary,
    HealthResp,
    SecretSummary,
    SecretWriteReq,
)

from ._route_helpers import get_service

router = APIRouter()


@router.get("/health", response_model=HealthResp)
def get_health(request: Request) -> HealthResp:
    return get_service(request).get_health()


@router.get("/settings", response_model=WorkspaceSettings)
def get_settings(request: Request) -> WorkspaceSettings:
    return get_service(request).get_settings()


@router.put("/settings", response_model=WorkspaceSettings)
def put_settings(request: Request, body: WorkspaceSettings) -> WorkspaceSettings:
    return get_service(request).update_settings(body)


@router.post("/secrets", response_model=SecretSummary)
def post_secret(request: Request, body: SecretWriteReq) -> SecretSummary:
    return get_service(request).set_secret(body.key, body.value)


@router.get("/secrets", response_model=list[SecretSummary])
def list_secrets(
    request: Request,
    keys: Annotated[list[str] | None, Query()] = None,
) -> list[SecretSummary]:
    parsed = [k.strip() for item in (keys or []) for k in item.split(",") if k.strip()]
    return get_service(request).list_secrets(parsed)


@router.delete("/secrets/{key}", status_code=204)
def delete_secret(request: Request, key: str) -> Response:
    get_service(request).delete_secret(key)
    return Response(status_code=204)


@router.get("/mcp/servers", response_model=list[ExternalMcpServerSpec])
def list_mcp_servers(request: Request) -> list[ExternalMcpServerSpec]:
    return get_service(request).list_mcp_servers()


@router.post("/mcp/servers", response_model=ExternalMcpServerSpec)
def upsert_mcp_server(
    request: Request, body: ExternalMcpServerSpec
) -> ExternalMcpServerSpec:
    return get_service(request).upsert_mcp_server(body)


@router.delete("/mcp/servers/{server_id}", status_code=204)
def delete_mcp_server(request: Request, server_id: str) -> Response:
    get_service(request).delete_mcp_server(server_id)
    return Response(status_code=204)


@router.get("/agents", response_model=list[AgentPackSummary])
def list_agents(request: Request) -> list[AgentPackSummary]:
    return get_service(request).list_agent_packs()


@router.get("/agents/{agent_id}", response_model=AgentPackDetail)
def get_agent(request: Request, agent_id: str) -> AgentPackDetail:
    return get_service(request).get_agent_pack(agent_id)


__all__ = ["router"]
