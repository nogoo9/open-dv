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
from opendv_contracts.errors import OpenDVError
from opendv_platform_api.schemas.ontology_studio import (
    DeltaCommitResponse,
    FeedbackSubmissionRequest,
    FeedbackSubmissionResponse,
    OntologyDeltaRequest,
    ProposalAcceptRequest,
    ProposalCreateRequest,
    ProposalRebaseRequest,
    ProposalRecordDTO,
    RefineProposalRequest,
    RejectProposalRequest,
    WaiverSubmissionRequest,
)
from opendv_platform_api.security.error_catalog import StudioAPIError
from opendv_platform_api.security.studio_auth import StudioPrincipal
from opendv_platform_api.studio_deps import (
    get_session_manager,
    get_session_or_404,
    raise_from_opendv_error,
    require_admin,
    require_editor,
    require_editor_or_automated,
    require_viewer,
    resolve_proposal_file,
)
from opendv_service.studio.delta_normalizer import StudioWorkflowError
from opendv_service.studio.delta_workflows import (
    apply_session_delta_workflow,
    validate_session_delta_workflow,
)
from opendv_service.studio.promoter import StagedCandidatePromoter
from opendv_service.studio.proposal_rebase_workflows import (
    rebase_session_proposal_workflow,
    refine_session_proposal_workflow,
)
from opendv_service.studio.proposal_workflows import (
    accept_session_proposal_workflow,
    create_session_proposal_workflow,
    get_session_proposal_detail,
    list_session_proposals,
    reject_session_proposal_workflow,
)
from opendv_service.studio.session_export_workflows import submit_feedback_workflow
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.studio.waiver_workflows import submit_session_waiver_workflow

router = APIRouter(prefix="/ontology/studio", tags=["ontology-studio"])


@router.post("/sessions/{session_id}/validate", response_model=OntologyValidationResult)
@router.post("/sessions/{session_id}/deltas/validate", response_model=OntologyValidationResult, include_in_schema=False)
def validate_delta_endpoint(session_id: str, payload: OntologyDeltaRequest, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], principal: Annotated[StudioPrincipal, Depends(require_viewer)]) -> OntologyValidationResult:
    """Dry-run two-phase structural and governance validation on an OntologyDelta."""
    meta = get_session_or_404(session_mgr, session_id)
    try:
        return validate_session_delta_workflow(session_mgr=session_mgr, meta=meta, sdir=session_mgr.get_session_dir(session_id), payload_dict=payload.model_dump(mode="python"), principal_id=principal.principal, is_admin=principal.is_admin)
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc


@router.post("/sessions/{session_id}/delta", response_model=DeltaCommitResponse)
@router.post("/sessions/{session_id}/deltas/apply", response_model=DeltaCommitResponse, include_in_schema=False)
def apply_delta_endpoint(session_id: str, payload: OntologyDeltaRequest, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], principal: Annotated[StudioPrincipal, Depends(require_editor)], if_match: Annotated[str | None, Header(alias="If-Match")] = None) -> DeltaCommitResponse:
    """Direct governed delta commit with strict OCC If-Match precondition."""
    meta = get_session_or_404(session_mgr, session_id)
    try:
        res = apply_session_delta_workflow(session_mgr=session_mgr, meta=meta, sdir=session_mgr.get_session_dir(session_id), payload_dict=payload.model_dump(mode="python"), if_match=if_match, principal_id=principal.principal, principal_role=principal.role, is_admin=principal.is_admin)
        return DeltaCommitResponse(**res)
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc


@router.get("/sessions/{session_id}/proposals", response_model=list[ProposalRecordDTO])
def list_proposals(session_id: str, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], _principal: Annotated[StudioPrincipal, Depends(require_viewer)]) -> list[ProposalRecordDTO]:
    """List all candidate proposals with current status."""
    get_session_or_404(session_mgr, session_id)
    return [ProposalRecordDTO(proposal_id=d["proposal_id"], session_id=session_id, base_version=d["base_version"], base_sha256=d["base_sha256"], status=d["status"], operations=d["operations"], proposer=d["proposer"], created_at=d["created_at"], rationale=d["rationale"]) for d in list_session_proposals(session_mgr.get_session_dir(session_id), session_id)]


@router.post("/sessions/{session_id}/proposals", response_model=ProposalRecordDTO, status_code=201)
def create_proposal(session_id: str, payload: ProposalCreateRequest, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], principal: Annotated[StudioPrincipal, Depends(require_editor_or_automated)]) -> ProposalRecordDTO:
    """Submit candidate proposal without committing."""
    meta = get_session_or_404(session_mgr, session_id)
    try:
        return ProposalRecordDTO(**create_session_proposal_workflow(meta=meta, sdir=session_mgr.get_session_dir(session_id), proposal_id=payload.proposal_id, base_version_req=payload.base_version, base_sha256_req=payload.base_sha256, operations=payload.operations, rationale=payload.rationale, principal_id=principal.principal, is_automated=principal.is_automated))
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc


@router.get("/sessions/{session_id}/proposals/{proposal_id}", response_model=ProposalRecordDTO)
def get_proposal_detail(session_id: str, proposal_id: str, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], _principal: Annotated[StudioPrincipal, Depends(require_viewer)]) -> ProposalRecordDTO:
    """Fetch proposal record and conflict status."""
    get_session_or_404(session_mgr, session_id)
    try:
        d = get_session_proposal_detail(session_mgr.get_session_dir(session_id), session_id, proposal_id)
        return ProposalRecordDTO(proposal_id=d["proposal_id"], session_id=session_id, base_version=d["base_version"], base_sha256=d["base_sha256"], status=d["status"], operations=d["operations"], proposer=d["proposer"], created_at=d["created_at"], rationale=d["rationale"])
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc


@router.post("/sessions/{session_id}/proposals/{proposal_id}/accept", response_model=DeltaCommitResponse)
def accept_proposal(session_id: str, proposal_id: str, payload: ProposalAcceptRequest, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], principal: Annotated[StudioPrincipal, Depends(require_viewer)], if_match: Annotated[str | None, Header(alias="If-Match")] = None) -> DeltaCommitResponse:
    """Accept pending proposal enforcing 11-step precedence, RBAC, strong ETag OCC, and ONT-EVO."""
    meta = get_session_or_404(session_mgr, session_id)
    try:
        res = accept_session_proposal_workflow(session_mgr=session_mgr, meta=meta, sdir=session_mgr.get_session_dir(session_id), proposal_id=proposal_id, if_match=if_match, base_sha256_req=payload.base_sha256, low_confidence_waiver=payload.low_confidence_waiver, self_acceptance_override=payload.self_acceptance_override, grounding_rationale=payload.grounding_rationale, explicit_ungrounded=payload.ungrounded_concept, principal_id=principal.principal, principal_role=principal.role, is_admin=principal.is_admin, is_automated=principal.is_automated)
        return DeltaCommitResponse(**res)
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc


@router.post("/sessions/{session_id}/proposals/{proposal_id}/reject", response_model=ProposalRecordDTO)
def reject_proposal(session_id: str, proposal_id: str, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], principal: Annotated[StudioPrincipal, Depends(require_editor)], payload: RejectProposalRequest | None = None) -> ProposalRecordDTO:
    """Mark proposal REJECTED while preserving in CAS for audit trace."""
    get_session_or_404(session_mgr, session_id)
    try:
        d = reject_session_proposal_workflow(session_mgr=session_mgr, sdir=session_mgr.get_session_dir(session_id), session_id=session_id, proposal_id=proposal_id, reason=payload.reason if payload else None, principal_id=principal.principal, is_automated=principal.is_automated)
        return ProposalRecordDTO(proposal_id=d["proposal_id"], session_id=session_id, base_version=d["base_version"], base_sha256=d["base_sha256"], status="REJECTED", operations=d["operations"], proposer=d["proposer"], created_at=d["created_at"], rationale=d["rationale"])
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc


@router.post("/sessions/{session_id}/proposals/{proposal_id}/rebase", response_model=ProposalRecordDTO)
def rebase_proposal(session_id: str, proposal_id: str, payload: ProposalRebaseRequest, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], principal: Annotated[StudioPrincipal, Depends(require_editor)], if_match: Annotated[str | None, Header(alias="If-Match")] = None) -> ProposalRecordDTO:
    """Rebase proposal onto new session head via 24-op conflict matrix."""
    get_session_or_404(session_mgr, session_id)
    try:
        d = rebase_session_proposal_workflow(session_mgr=session_mgr, sdir=session_mgr.get_session_dir(session_id), session_id=session_id, proposal_id=proposal_id, if_match=if_match, base_sha256_req=payload.base_sha256, principal_id=principal.principal)
        return ProposalRecordDTO(proposal_id=d["proposal_id"], session_id=session_id, base_version=d["base_version"], base_sha256=d["base_sha256"], status="PENDING", operations=d["operations"], proposer=d["proposer"], created_at=d["created_at"], rationale=d["rationale"])
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc


@router.post("/sessions/{session_id}/proposals/{proposal_id}/refine", response_model=ProposalRecordDTO)
def refine_proposal_endpoint(session_id: str, proposal_id: str, payload: RefineProposalRequest, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], principal: Annotated[StudioPrincipal, Depends(require_editor)], request: Request) -> ProposalRecordDTO:
    """Refine a pending ontology proposal with natural-language steering guidance."""
    meta = get_session_or_404(session_mgr, session_id)
    try:
        return ProposalRecordDTO(**refine_session_proposal_workflow(session_mgr=session_mgr, meta=meta, sdir=session_mgr.get_session_dir(session_id), proposal_id=proposal_id, steering_prompt=payload.steering_prompt, mode=payload.mode, principal_id=principal.principal, agent_runner=getattr(request.app.state, "studio_agent_runner", None)))
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc


@router.get("/sessions/{session_id}/staged-candidates", response_model=StagedCandidatesQueryResponse)
def list_staged_candidates(session_id: str, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], _principal: Annotated[StudioPrincipal, Depends(require_viewer)]) -> StagedCandidatesQueryResponse:
    """Query held-back candidate concepts (< 0.65 BM25 match score) for session."""
    get_session_or_404(session_mgr, session_id)
    cands = StagedCandidatePromoter(session_mgr.get_session_dir(session_id), session_mgr).list_staged_candidates()
    return StagedCandidatesQueryResponse(session_id=session_id, staged_candidates=cands, candidates=cands, total_count=len(cands))


@router.post("/sessions/{session_id}/staged-candidates/{candidate_id}/promote", response_model=PromoteCandidateResponse)
def promote_candidate_endpoint(session_id: str, candidate_id: str, payload: PromoteCandidateRequest, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], principal: Annotated[StudioPrincipal, Depends(require_editor_or_automated)]) -> PromoteCandidateResponse:
    """Promote a held-back candidate concept via Path A or Path B (admin waiver)."""
    get_session_or_404(session_mgr, session_id)
    try:
        return StagedCandidatePromoter(session_mgr.get_session_dir(session_id), session_mgr).promote_candidate(candidate_id, payload, principal)
    except OpenDVError as exc:
        raise raise_from_opendv_error(exc) from exc
    except Exception as exc:
        raise StudioAPIError("EXECUTION_FAILURE", str(exc)) from exc


@router.post("/sessions/{session_id}/waiver")
def submit_waiver(session_id: str, payload: WaiverSubmissionRequest, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], principal: Annotated[StudioPrincipal, Depends(require_admin)]) -> dict[str, Any]:
    """Authorize K_waiver-signed steward waiver and append signed audit record."""
    meta = get_session_or_404(session_mgr, session_id)
    try:
        return submit_session_waiver_workflow(session_mgr=session_mgr, meta=meta, sdir=session_mgr.get_session_dir(session_id), payload_dict=payload.model_dump(mode="python"), principal_id=principal.principal, resolve_proposal_fn=resolve_proposal_file)
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc


@router.post("/sessions/{session_id}/feedback", response_model=FeedbackSubmissionResponse, status_code=201)
def submit_feedback(session_id: str, payload: FeedbackSubmissionRequest, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], principal: Annotated[StudioPrincipal, Depends(require_editor)]) -> FeedbackSubmissionResponse:
    """Persist steward/user feedback record to feedback.jsonl under L5 session lock."""
    meta = get_session_or_404(session_mgr, session_id)
    try:
        return FeedbackSubmissionResponse(**submit_feedback_workflow(session_mgr=session_mgr, meta=meta, sdir=session_mgr.get_session_dir(session_id), rating=payload.rating, comment=payload.comment, concept_uri=payload.concept_uri, proposal_id=payload.proposal_id, principal_id=principal.principal, principal_role=principal.role, is_automated=principal.is_automated))
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc


__all__ = ["router"]
