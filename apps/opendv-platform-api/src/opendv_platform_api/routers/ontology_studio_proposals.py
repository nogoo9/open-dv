"""Ontology Studio deltas, proposals, staged candidates, waivers, and feedback endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request

from opendv_contracts.dtos.ontology import OntologyValidationResult
from opendv_contracts.dtos.promotion import (
    PromoteCandidateRequest,
    PromoteCandidateResponse,
    StagedCandidatesQueryResponse,
)
from opendv_platform_api import studio_deps as _sd
from opendv_platform_api import studio_router_adapters as _ad
from opendv_platform_api.schemas import ontology_studio as _s
from opendv_platform_api.security.studio_auth import StudioPrincipal
from opendv_platform_api.studio_deps import AdminDep, EdAutoDep, EditorDep, MgrDep, ViewerDep
from opendv_service.studio import proposal_rebase_workflows as _prw
from opendv_service.studio import proposal_workflows as _pw
from opendv_service.studio.promoter import StagedCandidatePromoter
from opendv_service.studio.session_manager import StudioSessionManager

router = APIRouter(prefix="/ontology/studio", tags=["ontology-studio"])


@router.post("/sessions/{session_id}/validate", response_model=OntologyValidationResult)
@router.post(
    "/sessions/{session_id}/deltas/validate",
    response_model=OntologyValidationResult, include_in_schema=False,
)
def validate_delta_endpoint(
    session_id: str, payload: _s.OntologyDeltaRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(_sd.get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(_sd.require_viewer)],
) -> OntologyValidationResult:
    """Dry-run two-phase structural and governance validation on an OntologyDelta."""
    return _ad.validate_delta_adapter(session_mgr, session_id, payload, principal)


@router.post("/sessions/{session_id}/delta", response_model=_s.DeltaCommitResponse)
@router.post(
    "/sessions/{session_id}/deltas/apply",
    response_model=_s.DeltaCommitResponse, include_in_schema=False,
)
def apply_delta_endpoint(
    session_id: str, payload: _s.OntologyDeltaRequest, session_mgr: MgrDep, principal: EditorDep,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> _s.DeltaCommitResponse:
    """Direct governed delta commit with strict OCC If-Match precondition."""
    return _ad.apply_delta_adapter(session_mgr, session_id, payload, principal, if_match)


@router.get("/sessions/{session_id}/proposals", response_model=list[_s.ProposalRecordDTO])
def list_proposals(
    session_id: str, session_mgr: MgrDep, _p: ViewerDep
) -> list[_s.ProposalRecordDTO]:
    """List all candidate proposals with current status."""
    _sd.get_session_or_404(session_mgr, session_id)
    sdir = session_mgr.get_session_dir(session_id)
    return [_ad.prop_dto(d, session_id) for d in _pw.list_session_proposals(sdir, session_id)]


@router.post(
    "/sessions/{session_id}/proposals", response_model=_s.ProposalRecordDTO, status_code=201
)
def create_proposal(
    session_id: str, payload: _s.ProposalCreateRequest, session_mgr: MgrDep, principal: EdAutoDep
) -> _s.ProposalRecordDTO:
    """Submit candidate proposal without committing."""
    return _ad.create_proposal_adapter(session_mgr, session_id, payload, principal)


@router.get("/sessions/{session_id}/proposals/{proposal_id}", response_model=_s.ProposalRecordDTO)
def get_proposal_detail(
    session_id: str, proposal_id: str, session_mgr: MgrDep, _p: ViewerDep
) -> _s.ProposalRecordDTO:
    """Fetch proposal record and conflict status."""
    _sd.get_session_or_404(session_mgr, session_id)
    with _sd.translate_studio_errors():
        sdir = session_mgr.get_session_dir(session_id)
        d = _pw.get_session_proposal_detail(sdir, session_id, proposal_id)
        return _ad.prop_dto(d, session_id)


@router.post(
    "/sessions/{session_id}/proposals/{proposal_id}/accept", response_model=_s.DeltaCommitResponse
)
def accept_proposal(
    session_id: str, proposal_id: str, payload: _s.ProposalAcceptRequest,
    session_mgr: MgrDep, principal: ViewerDep,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> _s.DeltaCommitResponse:
    """Accept pending proposal enforcing 11-step precedence, RBAC, strong ETag OCC, and ONT-EVO."""
    return _ad.accept_proposal_adapter(
        session_mgr, session_id, proposal_id, payload, principal, if_match
    )


@router.post(
    "/sessions/{session_id}/proposals/{proposal_id}/reject", response_model=_s.ProposalRecordDTO
)
def reject_proposal(
    session_id: str, proposal_id: str, session_mgr: MgrDep, principal: EditorDep,
    payload: _s.RejectProposalRequest | None = None,
) -> _s.ProposalRecordDTO:
    """Mark proposal REJECTED while preserving in CAS for audit trace."""
    return _ad.reject_proposal_adapter(session_mgr, session_id, proposal_id, payload, principal)


@router.post(
    "/sessions/{session_id}/proposals/{proposal_id}/rebase", response_model=_s.ProposalRecordDTO
)
def rebase_proposal(
    session_id: str, proposal_id: str, payload: _s.ProposalRebaseRequest,
    session_mgr: MgrDep, principal: EditorDep,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> _s.ProposalRecordDTO:
    """Rebase proposal onto new session head via 24-op conflict matrix."""
    _sd.get_session_or_404(session_mgr, session_id)
    with _sd.translate_studio_errors():
        d = _prw.rebase_session_proposal_workflow(
            session_mgr=session_mgr, sdir=session_mgr.get_session_dir(session_id),
            session_id=session_id, proposal_id=proposal_id, if_match=if_match,
            base_sha256_req=payload.base_sha256, principal_id=principal.principal,
        )
        return _ad.prop_dto(d, session_id, status="PENDING")


@router.post(
    "/sessions/{session_id}/proposals/{proposal_id}/refine", response_model=_s.ProposalRecordDTO
)
def refine_proposal_endpoint(
    session_id: str, proposal_id: str, payload: _s.RefineProposalRequest,
    session_mgr: MgrDep, principal: EditorDep, request: Request,
) -> _s.ProposalRecordDTO:
    """Refine a pending ontology proposal with natural-language steering guidance."""
    runner = getattr(request.app.state, "studio_agent_runner", None)
    return _ad.refine_proposal_adapter(
        session_mgr, session_id, proposal_id, payload, principal, runner
    )


@router.get(
    "/sessions/{session_id}/staged-candidates", response_model=StagedCandidatesQueryResponse
)
def list_staged_candidates(
    session_id: str, session_mgr: MgrDep, _p: ViewerDep
) -> StagedCandidatesQueryResponse:
    """Query held-back candidate concepts (< 0.65 BM25 match score) for session."""
    _sd.get_session_or_404(session_mgr, session_id)
    cands = StagedCandidatePromoter(
        session_mgr.get_session_dir(session_id), session_mgr
    ).list_staged_candidates()
    return StagedCandidatesQueryResponse(
        session_id=session_id, staged_candidates=cands, candidates=cands, total_count=len(cands)
    )


@router.post(
    "/sessions/{session_id}/staged-candidates/{candidate_id}/promote",
    response_model=PromoteCandidateResponse,
)
def promote_candidate_endpoint(
    session_id: str, candidate_id: str, payload: PromoteCandidateRequest,
    session_mgr: MgrDep, principal: EdAutoDep,
) -> PromoteCandidateResponse:
    """Promote a held-back candidate concept via Path A or Path B (admin waiver)."""
    return _ad.promote_candidate_adapter(session_mgr, session_id, candidate_id, payload, principal)


@router.post("/sessions/{session_id}/waiver")
def submit_waiver(
    session_id: str, payload: _s.WaiverSubmissionRequest, session_mgr: MgrDep, principal: AdminDep
) -> dict[str, Any]:
    """Authorize K_waiver-signed steward waiver and append signed audit record."""
    return _ad.submit_waiver_adapter(session_mgr, session_id, payload, principal)


@router.post(
    "/sessions/{session_id}/feedback", response_model=_s.FeedbackSubmissionResponse, status_code=201
)
def submit_feedback(
    session_id: str, payload: _s.FeedbackSubmissionRequest,
    session_mgr: MgrDep, principal: EditorDep,
) -> _s.FeedbackSubmissionResponse:
    """Persist steward/user feedback record to feedback.jsonl under L5 session lock."""
    return _ad.submit_feedback_adapter(session_mgr, session_id, payload, principal)


__all__ = ["router"]
