"""Group 4 API routes (Steps 4-5 Discovery/Ontology) + Groups 5-6 Vault/Storage router."""

from __future__ import annotations

import asyncio
import uuid
from typing import Literal

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import StreamingResponse

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.grounding import GroundingResponse, OntologyGroundReq
from opendv.models.wire_dtos_discovery import (
    CitationResolveReq,
    CitationSpanResp,
    FiboQueryReq,
    FiboQueryResp,
    HoldAnswersReq,
    OntologyBundleResp,
    OntologyCommitReq,
    OntologyConstructReq,
    OntologyRevisionResp,
    OntologyValidateReq,
    OntologyValidationResp,
    RunControlReq,
    RunStateResp,
)

from ._route_helpers import (
    enforce_grounding_asset_path,
    get_service,
    get_task_manager,
    read_grounding_asset_bytes,
)
from ._routes_vault_storage import router as vault_storage_router
from .sse import SseStreamAdapter

router = APIRouter()
_SSE = SseStreamAdapter()
_MAX_FIBO_PACK_BYTES = 4 * 1024 * 1024
_MAX_GLOSSARY_BYTES = 2 * 1024 * 1024


@router.get("/projects/{project_id}/ontology", response_model=OntologyBundleResp)
def get_ontology_bundle(request: Request, project_id: str) -> OntologyBundleResp:
    return get_service(request).get_ontology_bundle(project_id)


@router.post(
    "/projects/{project_id}/ontology/construct",
    status_code=202,
    response_model=RunStateResp,
)
async def start_ontology_construction(
    request: Request, project_id: str, body: OntologyConstructReq
) -> RunStateResp:
    svc = get_service(request)
    task_mgr = get_task_manager(request)
    run_id = f"run4-{uuid.uuid4().hex[:10]}"
    return await task_mgr.launch_run(
        svc,
        project_id,
        run_id,
        lambda rid: svc.start_ontology_construction(project_id, body, run_id=rid),
        step="step4",
    )


@router.get(
    "/projects/{project_id}/ontology/runs/{rid}",
    response_model=RunStateResp,
)
def get_ontology_run(request: Request, project_id: str, rid: str) -> RunStateResp:
    return get_service(request).get_ontology_run(project_id, rid)


@router.get(
    "/projects/{project_id}/ontology/runs/{rid}/events",
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
def stream_ontology_events(
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
        expected_step="step4",
    )


@router.post(
    "/projects/{project_id}/ontology/runs/{rid}/controls",
    response_model=RunStateResp,
)
def control_ontology_run(
    request: Request, project_id: str, rid: str, body: RunControlReq
) -> RunStateResp:
    if body.action in ("accept-best", "horizon-review"):
        raise OpenDVError(
            f"Control action '{body.action}' is only valid for Step 6 runs.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            run_id=rid,
        )
    svc = get_service(request)
    svc.get_ontology_run(project_id, rid)
    return svc.control_ontology_run(project_id, rid, body)


@router.post(
    "/projects/{project_id}/ontology/runs/{rid}/answers",
    status_code=202,
    response_model=RunStateResp,
)
async def answer_ontology_hold(
    request: Request, project_id: str, rid: str, body: HoldAnswersReq
) -> RunStateResp:
    svc = get_service(request)
    prior_state = svc.get_ontology_run(project_id, rid)
    if prior_state.status != "HOLD_CHECKPOINTED":
        raise OpenDVError(
            f"Run '{rid}' is in status '{prior_state.status}', not HOLD_CHECKPOINTED.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            run_id=rid,
        )
    task_mgr = get_task_manager(request)
    return await task_mgr.launch_run(
        svc,
        project_id,
        rid,
        lambda r: svc.answer_ontology_hold(project_id, r, body),
        step="step4",
        allow_sync_failure_cleanup=False,
    )


@router.post("/ontology/ground", response_model=GroundingResponse)
async def ground_ontology(request: Request, body: OntologyGroundReq) -> GroundingResponse:
    svc = get_service(request)
    fibo_pack_bytes: bytes | None = None
    glossary_bytes: bytes | None = None
    if body.fibo_pack is not None:
        r_path, r_root = enforce_grounding_asset_path(request, svc, body.fibo_pack)
        fibo_pack_bytes = read_grounding_asset_bytes(
            r_path, r_root, max_bytes=_MAX_FIBO_PACK_BYTES, label="fibo_pack"
        )
    if body.glossary_path is not None:
        r_path, r_root = enforce_grounding_asset_path(request, svc, body.glossary_path)
        glossary_bytes = read_grounding_asset_bytes(
            r_path, r_root, max_bytes=_MAX_GLOSSARY_BYTES, label="glossary"
        )
    sem = getattr(request.app.state, "ground_semaphore", None)
    if sem is None:
        sem = asyncio.Semaphore(4)
        request.app.state.ground_semaphore = sem
    async with sem:
        return await svc.ground_ontology(
            body, fibo_pack_bytes=fibo_pack_bytes, glossary_bytes=glossary_bytes
        )


@router.post("/ontology/fibo-qa", response_model=FiboQueryResp)
def fibo_qa(request: Request, body: FiboQueryReq) -> FiboQueryResp:
    return get_service(request).fibo_qa(body)


@router.post("/projects/{project_id}/ontology/fibo-qa", response_model=FiboQueryResp)
def fibo_qa_project(
    request: Request, project_id: str, body: FiboQueryReq
) -> FiboQueryResp:
    return get_service(request).fibo_qa(body, project_id=project_id)


@router.post(
    "/projects/{project_id}/ontology/citations/resolve",
    response_model=CitationSpanResp,
)
def resolve_citation_span(
    request: Request, project_id: str, body: CitationResolveReq
) -> CitationSpanResp:
    return get_service(request).resolve_citation_span(project_id, body)


@router.post(
    "/projects/{project_id}/ontology/validate",
    response_model=OntologyValidationResp,
)
def validate_ontology(
    request: Request, project_id: str, body: OntologyValidateReq
) -> OntologyValidationResp:
    return get_service(request).validate_ontology(project_id, body)


@router.post(
    "/projects/{project_id}/ontology/commit",
    response_model=OntologyRevisionResp,
)
def commit_ontology(
    request: Request, project_id: str, body: OntologyCommitReq
) -> OntologyRevisionResp:
    return get_service(request).commit_ontology(project_id, body)


@router.get(
    "/projects/{project_id}/ontology/export",
    responses={
        200: {
            "content": {
                "application/yaml": {"schema": {"type": "string"}},
                "application/json": {"schema": {"type": "string"}},
                "text/turtle": {"schema": {"type": "string"}},
            }
        }
    },
)
def export_ontology(
    request: Request,
    project_id: str,
    fmt: Literal["yaml", "json", "ttl"] = Query(default="yaml", alias="format"),
    include_staged: bool = Query(default=False),
) -> Response:
    text, media_type = get_service(request).export_ontology(
        project_id, fmt=fmt, include_staged=include_staged
    )
    return Response(content=text, media_type=media_type)


router.include_router(vault_storage_router)

__all__ = ["router"]
