"""Ontology Studio clarification, construction, citation resolution, and FIBO Q&A endpoints."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import ValidationError

from opendv_contracts.dtos.clarification import ClarificationAnswerResponse, ClarificationQuestion
from opendv_platform_api import studio_deps as _sd
from opendv_platform_api.job_supervisor import run_clarify_job, run_construct_job
from opendv_platform_api.schemas import ontology_studio as _s
from opendv_platform_api.security.error_catalog import StudioAPIError
from opendv_platform_api.security.studio_auth import StudioPrincipal
from opendv_platform_api.studio_deps import EdAutoDep, EditorDep, MgrDep, ViewerDep
from opendv_service.studio import wizard_workflows as _wiz
from opendv_service.studio.proposal_helpers import ensure_session_version_exists
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.studio.session_source_workflows import submit_clarification_answers_workflow

router = APIRouter(prefix="/ontology/studio", tags=["ontology-studio"])


@router.get("/sessions/{session_id}/clarifications", response_model=list[ClarificationQuestion])
@router.get("/sessions/{session_id}/questions", response_model=list[ClarificationQuestion])
def get_clarification_questions(
    session_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(_sd.get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(_sd.require_viewer)],
) -> list[ClarificationQuestion]:
    """Read generated ambiguity questions from session clarifications/questions.json."""
    _sd.get_session_or_404(session_mgr, session_id)
    qfile = session_mgr.get_session_dir(session_id) / "clarifications" / "questions.json"
    if not qfile.is_file():
        return []
    try:
        raw_data = json.loads(qfile.read_text(encoding="utf-8"))
        if not isinstance(raw_data, list):
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Corrupt questions.json in session '{session_id}': expected JSON list.",
            )
        return [ClarificationQuestion.model_validate(q) for q in raw_data]
    except StudioAPIError:
        raise
    except (OSError, json.JSONDecodeError, ValidationError, ValueError, TypeError) as exc:
        raise StudioAPIError(
            "CORRUPT_SESSION_RECORD", f"Corrupt questions.json in session '{session_id}': {exc}"
        ) from exc


@router.post("/sessions/{session_id}/clarify", response_model=_s.JobHandleDTO, status_code=202)
def trigger_clarification(
    request: Request, session_id: str, payload: _s.StudioClarifyRequest,
    session_mgr: MgrDep, principal: EditorDep,
) -> _s.JobHandleDTO:
    """Dispatch background clarification question generation."""
    _sd.get_session_or_404(session_mgr, session_id)
    pstate = getattr(request.app.state, "platform_state", None)
    _sd.ensure_no_active_session_job(session_mgr, session_id, platform_state=pstate)
    with _sd.translate_studio_errors():
        ensure_session_version_exists(session_mgr.get_session_dir(session_id), payload.base_version)
    return _s.JobHandleDTO(
        **_sd.get_job_supervisor().submit_job(
            session_mgr=session_mgr, session_id=session_id, job_type="clarify",
            runner_fn=run_clarify_job, principal=principal.principal, role=principal.role,
            mode=payload.mode, max_questions=payload.max_questions,
            focus_concepts=payload.focus_concepts,
        )
    )


@router.get("/sessions/{session_id}/answers", response_model=list[dict[str, Any]])
def get_clarification_answers(
    session_id: str, session_mgr: MgrDep, _p: ViewerDep
) -> list[dict[str, Any]]:
    """Read persisted clarification answers from session clarifications/answers.json."""
    _sd.get_session_or_404(session_mgr, session_id)
    answers_file = session_mgr.get_session_dir(session_id) / "clarifications" / "answers.json"
    if not answers_file.is_file():
        return []
    try:
        raw_data = json.loads(answers_file.read_text(encoding="utf-8"))
        if not isinstance(raw_data, list) or not all(isinstance(x, dict) for x in raw_data):
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Corrupt answers.json in session '{session_id}': expected list of objects.",
            )
        return raw_data
    except StudioAPIError:
        raise
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise StudioAPIError(
            "CORRUPT_SESSION_RECORD", f"Corrupt answers.json in session '{session_id}': {exc}"
        ) from exc


@router.post("/sessions/{session_id}/answers", response_model=ClarificationAnswerResponse)
def submit_clarification_answers(
    session_id: str, payload: _s.StudioClarificationAnswerRequest,
    session_mgr: MgrDep, principal: EditorDep,
) -> ClarificationAnswerResponse:
    """Persist human clarification answers with mandatory OCC expected_answers_sha256 check."""
    _sd.get_session_or_404(session_mgr, session_id)
    with _sd.translate_studio_errors():
        count, new_sha = submit_clarification_answers_workflow(
            session_mgr=session_mgr, sdir=session_mgr.get_session_dir(session_id),
            session_id=session_id, raw_answers=payload.answers,
            expected_answers_sha256=payload.expected_answers_sha256,
            principal_id=principal.principal,
        )
        return ClarificationAnswerResponse(saved_count=count, answers_sha256=new_sha)


@router.post("/sessions/{session_id}/construct", response_model=_s.JobHandleDTO, status_code=202)
def trigger_construction(
    request: Request, session_id: str, payload: _s.ConstructRequest,
    session_mgr: MgrDep, principal: EdAutoDep,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> _s.JobHandleDTO:
    """Trigger background construction job (deterministic or agent-first)."""
    meta = _sd.get_session_or_404(session_mgr, session_id)
    pstate = getattr(request.app.state, "platform_state", None)
    _sd.ensure_no_active_session_job(session_mgr, session_id, platform_state=pstate)
    agent_runner = getattr(request.app.state, "studio_agent_runner", None)
    with _sd.translate_studio_errors():
        clean_mode, base_ver, base_sha, clean_if = _wiz.validate_construct_request_workflow(
            meta=meta, sdir=session_mgr.get_session_dir(session_id),
            base_version_req=payload.base_version, base_sha256_req=payload.base_sha256,
            auto_commit=payload.auto_commit, mode_req=payload.mode, if_match=if_match,
            is_agent_identity=principal.is_agent_identity, agent_runner=agent_runner,
        )
    return _s.JobHandleDTO(
        **_sd.get_job_supervisor().submit_job(
            session_mgr=session_mgr, session_id=session_id, job_type="construct",
            runner_fn=run_construct_job, principal=principal.principal, role=principal.role,
            base_version=base_ver, base_sha256=base_sha, auto_commit=payload.auto_commit,
            fibo_modules=payload.fibo_modules, mode=clean_mode, if_match=clean_if,
            steering_prompt=payload.steering_prompt, agent_runner=agent_runner,
        )
    )


@router.post("/sessions/{session_id}/citations/resolve")
def resolve_citations_endpoint(
    session_id: str, payload: _s.ResolveCitationsRequest, session_mgr: MgrDep, _p: ViewerDep
) -> dict[str, Any]:
    """Resolve document spans, table profiles, and FIBO definitions for EvidenceRef citations."""
    _sd.get_session_or_404(session_mgr, session_id)
    with _sd.translate_studio_errors():
        return _wiz.resolve_citations_workflow(session_mgr, session_id, payload.citations)


@router.get("/fibo/lookup")
def fibo_lookup_endpoint(
    _p: ViewerDep, q: str = Query(..., min_length=1), limit: int = Query(default=25, ge=1, le=100)
) -> dict[str, Any]:
    """Search bundled FIBO core ontology classes, definitions, and parent hierarchy."""
    _ = _p
    return _wiz.lookup_fibo_workflow(q, limit=limit)


@router.post("/sessions/{session_id}/fibo-qa")
def ask_fibo_qa_endpoint(
    request: Request, session_id: str, payload: _s.FiboQAApiRequest,
    session_mgr: MgrDep, _p: ViewerDep,
) -> dict[str, Any]:
    """Answer grounded FIBO + session evidence question using live agent harness."""
    _sd.get_session_or_404(session_mgr, session_id)
    pstate = getattr(request.app.state, "platform_state", None)
    _sd.ensure_no_active_session_job(session_mgr, session_id, platform_state=pstate)
    with _sd.translate_studio_errors():
        return _wiz.ask_fibo_qa_workflow(
            session_mgr=session_mgr, session_id=session_id, question=payload.question,
            source_ids=payload.source_ids, concept_uri=payload.concept_uri,
            agent_runner=getattr(request.app.state, "studio_agent_runner", None),
        )


__all__ = ["router"]
