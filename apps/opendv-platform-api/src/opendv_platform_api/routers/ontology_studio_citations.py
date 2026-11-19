"""Ontology Studio clarification, construction, citation resolution, and FIBO Q&A endpoints."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import ValidationError

from opendv_contracts.dtos.clarification import ClarificationAnswerResponse, ClarificationQuestion
from opendv_platform_api.job_supervisor import run_clarify_job, run_construct_job
from opendv_platform_api.schemas.ontology_studio import (
    ConstructRequest,
    FiboQAApiRequest,
    JobHandleDTO,
    ResolveCitationsRequest,
    StudioClarificationAnswerRequest,
    StudioClarifyRequest,
)
from opendv_platform_api.security.error_catalog import StudioAPIError
from opendv_platform_api.security.studio_auth import StudioPrincipal
from opendv_platform_api.studio_deps import (
    ensure_no_active_session_job,
    get_job_supervisor,
    get_session_manager,
    get_session_or_404,
    raise_from_opendv_error,
    require_editor,
    require_editor_or_automated,
    require_viewer,
)
from opendv_service.studio.delta_normalizer import StudioWorkflowError
from opendv_service.studio.proposal_helpers import ensure_session_version_exists
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.studio.session_source_workflows import submit_clarification_answers_workflow
from opendv_service.studio.wizard_workflows import (
    ask_fibo_qa_workflow,
    lookup_fibo_workflow,
    resolve_citations_workflow,
    validate_construct_request_workflow,
)

router = APIRouter(prefix="/ontology/studio", tags=["ontology-studio"])


@router.get("/sessions/{session_id}/clarifications", response_model=list[ClarificationQuestion])
@router.get("/sessions/{session_id}/questions", response_model=list[ClarificationQuestion])
def get_clarification_questions(session_id: str, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], _principal: Annotated[StudioPrincipal, Depends(require_viewer)]) -> list[ClarificationQuestion]:
    """Read generated ambiguity questions from session clarifications/questions.json."""
    get_session_or_404(session_mgr, session_id)
    qfile = session_mgr.get_session_dir(session_id) / "clarifications" / "questions.json"
    if not qfile.is_file():
        return []
    try:
        raw_data = json.loads(qfile.read_text(encoding="utf-8"))
        if not isinstance(raw_data, list):
            raise StudioAPIError("CORRUPT_SESSION_RECORD", f"Corrupt questions.json in session '{session_id}': expected JSON list.")
        return [ClarificationQuestion.model_validate(q) for q in raw_data]
    except StudioAPIError:
        raise
    except (OSError, json.JSONDecodeError, ValidationError, ValueError, TypeError) as exc:
        raise StudioAPIError("CORRUPT_SESSION_RECORD", f"Corrupt questions.json in session '{session_id}': {exc}") from exc


@router.post("/sessions/{session_id}/clarify", response_model=JobHandleDTO, status_code=202)
def trigger_clarification(session_id: str, payload: StudioClarifyRequest, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], principal: Annotated[StudioPrincipal, Depends(require_editor)]) -> JobHandleDTO:
    """Dispatch background clarification question generation."""
    get_session_or_404(session_mgr, session_id)
    try:
        ensure_session_version_exists(session_mgr.get_session_dir(session_id), payload.base_version)
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc
    return JobHandleDTO(**get_job_supervisor().submit_job(session_mgr=session_mgr, session_id=session_id, job_type="clarify", runner_fn=run_clarify_job, principal=principal.principal, role=principal.role, mode=payload.mode, max_questions=payload.max_questions, focus_concepts=payload.focus_concepts))


@router.get("/sessions/{session_id}/answers", response_model=list[dict[str, Any]])
def get_clarification_answers(session_id: str, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], _principal: Annotated[StudioPrincipal, Depends(require_viewer)]) -> list[dict[str, Any]]:
    """Read persisted clarification answers from session clarifications/answers.json."""
    get_session_or_404(session_mgr, session_id)
    answers_file = session_mgr.get_session_dir(session_id) / "clarifications" / "answers.json"
    if not answers_file.is_file():
        return []
    try:
        raw_data = json.loads(answers_file.read_text(encoding="utf-8"))
        if not isinstance(raw_data, list) or not all(isinstance(x, dict) for x in raw_data):
            raise StudioAPIError("CORRUPT_SESSION_RECORD", f"Corrupt answers.json in session '{session_id}': expected list of objects.")
        return raw_data
    except StudioAPIError:
        raise
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise StudioAPIError("CORRUPT_SESSION_RECORD", f"Corrupt answers.json in session '{session_id}': {exc}") from exc


@router.post("/sessions/{session_id}/answers", response_model=ClarificationAnswerResponse)
def submit_clarification_answers(session_id: str, payload: StudioClarificationAnswerRequest, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], principal: Annotated[StudioPrincipal, Depends(require_editor)]) -> ClarificationAnswerResponse:
    """Persist human clarification answers with mandatory OCC expected_answers_sha256 check."""
    get_session_or_404(session_mgr, session_id)
    try:
        count, new_sha = submit_clarification_answers_workflow(session_mgr=session_mgr, sdir=session_mgr.get_session_dir(session_id), session_id=session_id, raw_answers=payload.answers, expected_answers_sha256=payload.expected_answers_sha256, principal_id=principal.principal)
        return ClarificationAnswerResponse(saved_count=count, answers_sha256=new_sha)
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc


@router.post("/sessions/{session_id}/construct", response_model=JobHandleDTO, status_code=202)
def trigger_construction(request: Request, session_id: str, payload: ConstructRequest, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], principal: Annotated[StudioPrincipal, Depends(require_editor_or_automated)], if_match: Annotated[str | None, Header(alias="If-Match")] = None) -> JobHandleDTO:
    """Trigger background construction job (deterministic or agent-first)."""
    meta = get_session_or_404(session_mgr, session_id)
    agent_runner = getattr(request.app.state, "studio_agent_runner", None)
    try:
        clean_mode, base_ver, base_sha, clean_if_match = validate_construct_request_workflow(
            meta=meta, sdir=session_mgr.get_session_dir(session_id), base_version_req=payload.base_version, base_sha256_req=payload.base_sha256,
            auto_commit=payload.auto_commit, mode_req=payload.mode, if_match=if_match, is_agent_identity=principal.is_agent_identity, agent_runner=agent_runner,
        )
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc
    return JobHandleDTO(**get_job_supervisor().submit_job(session_mgr=session_mgr, session_id=session_id, job_type="construct", runner_fn=run_construct_job, principal=principal.principal, role=principal.role, base_version=base_ver, base_sha256=base_sha, auto_commit=payload.auto_commit, fibo_modules=payload.fibo_modules, mode=clean_mode, if_match=clean_if_match, steering_prompt=payload.steering_prompt, agent_runner=agent_runner))


@router.post("/sessions/{session_id}/citations/resolve")
def resolve_citations_endpoint(session_id: str, payload: ResolveCitationsRequest, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], _principal: Annotated[StudioPrincipal, Depends(require_viewer)]) -> dict[str, Any]:
    """Resolve document spans, table profiles, and FIBO definitions for EvidenceRef citations."""
    get_session_or_404(session_mgr, session_id)
    try:
        return resolve_citations_workflow(session_mgr, session_id, payload.citations)
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc


@router.get("/fibo/lookup")
def fibo_lookup_endpoint(_principal: Annotated[StudioPrincipal, Depends(require_viewer)], q: str = Query(..., min_length=1), limit: int = Query(default=25, ge=1, le=100)) -> dict[str, Any]:
    """Search bundled FIBO core ontology classes, definitions, and parent hierarchy."""
    _ = _principal
    return lookup_fibo_workflow(q, limit=limit)


@router.post("/sessions/{session_id}/fibo-qa")
def ask_fibo_qa_endpoint(request: Request, session_id: str, payload: FiboQAApiRequest, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], _principal: Annotated[StudioPrincipal, Depends(require_viewer)]) -> dict[str, Any]:
    """Answer grounded FIBO + session evidence question using live agent harness."""
    get_session_or_404(session_mgr, session_id)
    ensure_no_active_session_job(session_mgr, session_id)
    try:
        return ask_fibo_qa_workflow(session_mgr=session_mgr, session_id=session_id, question=payload.question, source_ids=payload.source_ids, concept_uri=payload.concept_uri, agent_runner=getattr(request.app.state, "studio_agent_runner", None))
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc


__all__ = ["router"]
