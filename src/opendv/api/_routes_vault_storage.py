"""Groups 5-6 API routes: Step 6 Runs/CEGAR/Waivers/Guided UX & Step 7 Storage/Query."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse

from opendv.models.diagnostics import AntiPatternJustification
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import ExternalMcpReceipt, RunEventLogPage, RunStep
from opendv.models.project import TargetManifest
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.models.wire_dtos_discovery import HoldAnswersReq, RunControlReq, RunStateResp
from opendv.models.wire_dtos_vault import (
    AttemptSnapshotSpec,
    GuidedRunView,
    HorizonActionReq,
    MigrationApplyResp,
    SqlQueryReq,
    SqlQueryResult,
    StorageCompileReq,
    StorageTargetReq,
    TargetBundleResp,
    VaultBuildReq,
    VaultVerifyResp,
    WaiverSubmitReq,
)

from ._route_helpers import get_service, get_task_manager
from .sse import SseStreamAdapter

router = APIRouter()
_SSE = SseStreamAdapter()


@router.get("/projects/{project_id}/runs", response_model=list[RunStateResp])
def list_runs(
    request: Request,
    project_id: str,
    step: Annotated[RunStep | None, Query()] = None,
) -> list[RunStateResp]:
    return get_service(request).list_runs(project_id, step=step)


@router.post("/projects/{project_id}/runs", status_code=202, response_model=RunStateResp)
async def build_vault(
    request: Request, project_id: str, body: VaultBuildReq
) -> RunStateResp:
    svc = get_service(request)
    task_mgr = get_task_manager(request)
    run_id = f"run6-{uuid.uuid4().hex[:10]}"
    return await task_mgr.launch_run(
        svc,
        project_id,
        run_id,
        lambda rid: svc.build_vault(project_id, body, run_id=rid),
        step="step6",
        policy=body.policy,
    )


@router.get("/projects/{project_id}/runs/{rid}", response_model=RunStateResp)
def get_run(request: Request, project_id: str, rid: str) -> RunStateResp:
    return get_service(request).get_run(project_id, rid)


@router.get("/projects/{project_id}/runs/{rid}/logs", response_model=RunEventLogPage)
def get_run_logs(
    request: Request,
    project_id: str,
    rid: str,
    since_seq: int = Query(default=0, ge=0),
    limit: int | None = Query(default=None, ge=1),
) -> RunEventLogPage:
    svc = get_service(request)
    svc.get_run(project_id, rid)
    return svc.get_run_logs(project_id, rid, since_seq=since_seq, limit=limit)


@router.get(
    "/projects/{project_id}/runs/{rid}/attempts",
    response_model=list[AttemptSnapshotSpec],
)
def list_vault_attempts(
    request: Request, project_id: str, rid: str
) -> list[AttemptSnapshotSpec]:
    return get_service(request).list_vault_attempts(project_id, rid)


@router.get(
    "/projects/{project_id}/runs/{rid}/events",
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
def stream_run_events(
    request: Request,
    project_id: str,
    rid: str,
    since_seq: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    return _SSE.stream_response(
        request,
        get_service(request),
        get_task_manager(request),
        project_id,
        rid,
        last_event_id=last_event_id,
        since_seq=since_seq,
        expected_step="step6",
    )


@router.post("/projects/{project_id}/runs/{rid}/controls", response_model=RunStateResp)
async def control_vault_run(
    request: Request, project_id: str, rid: str, body: RunControlReq
) -> RunStateResp:
    return await get_service(request).send_vault_control(project_id, rid, body)


@router.post(
    "/projects/{project_id}/runs/{rid}/decisions",
    status_code=202,
    response_model=RunStateResp,
)
async def submit_vault_decisions(
    request: Request, project_id: str, rid: str, body: HoldAnswersReq
) -> RunStateResp:
    svc = get_service(request)
    prior_state = svc.get_vault_run(project_id, rid)
    if prior_state.status not in ("HOLD_CHECKPOINTED", "UNCERTIFIED_DRAFT"):
        raise OpenDVError(
            f"Run '{rid}' cannot accept Hold decisions in status '{prior_state.status}'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            run_id=rid,
        )
    task_mgr = get_task_manager(request)
    return await task_mgr.launch_run(
        svc,
        project_id,
        rid,
        lambda r: svc.submit_vault_decisions(project_id, r, body),
        step="step6",
        allow_sync_failure_cleanup=False,
    )


@router.post(
    "/projects/{project_id}/runs/{rid}/horizon",
    status_code=202,
    response_model=RunStateResp,
)
async def submit_horizon_steering(
    request: Request, project_id: str, rid: str, body: HorizonActionReq
) -> RunStateResp:
    svc = get_service(request)
    prior_state = svc.get_vault_run(project_id, rid)
    if prior_state.status not in ("HOLD_CHECKPOINTED", "UNCERTIFIED_DRAFT", "COMPLETED"):
        raise OpenDVError(
            f"Run '{rid}' cannot accept Hold decisions in status '{prior_state.status}'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            run_id=rid,
        )
    task_mgr = get_task_manager(request)
    return await task_mgr.launch_run(
        svc,
        project_id,
        rid,
        lambda r: svc.submit_horizon_steering(project_id, r, body),
        step="step6",
        allow_sync_failure_cleanup=False,
    )


@router.post(
    "/projects/{project_id}/vault/waivers",
    response_model=AntiPatternJustification,
)
def submit_vault_waiver(
    request: Request, project_id: str, body: WaiverSubmitReq
) -> AntiPatternJustification:
    return get_service(request).submit_vault_waiver(project_id, body)


@router.get("/projects/{project_id}/runs/{rid}/guided-view", response_model=GuidedRunView)
def get_guided_view(request: Request, project_id: str, rid: str) -> GuidedRunView:
    return get_service(request).get_guided_view(project_id, run_id=rid)


@router.get("/projects/{project_id}/vault/ir", response_model=CertifiedVaultIR)
def get_vault_ir(request: Request, project_id: str) -> CertifiedVaultIR:
    return get_service(request).get_vault_ir(project_id)


@router.post("/projects/{project_id}/vault/verify", response_model=VaultVerifyResp)
def verify_vault(request: Request, project_id: str) -> VaultVerifyResp:
    return get_service(request).verify_vault(project_id)


@router.get(
    "/projects/{project_id}/storage/targets",
    response_model=list[TargetManifest],
)
def list_target_manifests(request: Request, project_id: str) -> list[TargetManifest]:
    return get_service(request).list_target_manifests(project_id)


@router.get(
    "/projects/{project_id}/receipts",
    response_model=list[ExternalMcpReceipt],
)
def list_external_mcp_receipts(
    request: Request, project_id: str
) -> list[ExternalMcpReceipt]:
    return get_service(request).list_external_mcp_receipts(project_id)


@router.post(
    "/projects/{project_id}/storage/compile",
    response_model=TargetBundleResp | list[TargetBundleResp],
)
def compile_target(
    request: Request, project_id: str, body: StorageCompileReq
) -> TargetBundleResp | list[TargetBundleResp]:
    return get_service(request).compile_target(project_id, body)


@router.post("/projects/{project_id}/storage/migrate", response_model=MigrationApplyResp)
def migrate_target(
    request: Request, project_id: str, body: StorageTargetReq
) -> MigrationApplyResp:
    return get_service(request).migrate_target(project_id, body)


@router.post(
    "/projects/{project_id}/storage/regenerate",
    response_model=MigrationApplyResp,
)
def regenerate_target(
    request: Request, project_id: str, body: StorageTargetReq
) -> MigrationApplyResp:
    return get_service(request).regenerate_target(project_id, body)


@router.post("/projects/{project_id}/storage/query", response_model=SqlQueryResult)
def query_target(
    request: Request, project_id: str, body: SqlQueryReq
) -> SqlQueryResult:
    return get_service(request).query_target(project_id, body)


__all__ = ["router"]
