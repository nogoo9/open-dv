"""Thin API-layer adapters keeping split Ontology Studio routers under budget."""

from __future__ import annotations

from pathlib import PureWindowsPath
from typing import Any

from fastapi import UploadFile

from opendv_contracts.dtos.ontology import OntologyValidationResult
from opendv_contracts.dtos.promotion import PromoteCandidateRequest, PromoteCandidateResponse
from opendv_contracts.errors import OpenDVError
from opendv_platform_api.schemas import ontology_studio as _s
from opendv_platform_api.security.error_catalog import StudioAPIError
from opendv_platform_api.security.studio_auth import StudioPrincipal
from opendv_platform_api.studio_deps import (
    get_session_or_404,
    raise_from_opendv_error,
    resolve_proposal_file,
    translate_studio_errors,
)
from opendv_service.studio import delta_workflows as _dw
from opendv_service.studio import proposal_rebase_workflows as _prw
from opendv_service.studio import proposal_workflows as _pw
from opendv_service.studio.atomic_commit import delete_source
from opendv_service.studio.promoter import StagedCandidatePromoter
from opendv_service.studio.session_export_workflows import submit_feedback_workflow
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.studio.waiver_workflows import submit_session_waiver_workflow


def source_manifest_to_docs(files: list[dict[str, Any]]) -> list[_s.SourceDocumentDTO]:
    """Convert non-tombstoned CAS manifest records into SourceDocumentDTO instances."""
    return [
        _s.SourceDocumentDTO(
            source_id=f.get("source_id", ""), source_sha256=f.get("sha256", ""),
            file_name=f.get("filename", ""), filename=f.get("filename", ""),
            size_bytes=f.get("size_bytes", 0), byte_count=f.get("size_bytes", 0),
            mime_type=f.get("mime_type", "application/octet-stream"),
            do_not_send_to_agent=bool(f.get("do_not_send_to_agent", False)),
            tombstoned=False,
        )
        for f in files if not f.get("tombstoned", False)
    ]


def resolve_upload_filename_mime(file: UploadFile) -> tuple[str, str]:
    """Sanitize uploaded filename and normalize markdown/octet-stream MIME type."""
    base_name = PureWindowsPath((file.filename or "").strip()).name
    filename = base_name if base_name and base_name not in {".", ".."} else "upload.bin"
    raw_ct = (file.content_type or "").split(";", 1)[0].strip().lower()
    is_md = filename.lower().endswith((".md", ".markdown"))
    mime = (
        "text/markdown" if (not raw_ct or raw_ct == "application/octet-stream") and is_md
        else (raw_ct or "application/octet-stream")
    )
    return filename, mime


def delete_session_source_or_raise(
    session_mgr: StudioSessionManager, session_id: str, source_id: str
) -> None:
    """Purge an unreferenced source document or translate domain conflicts to StudioAPIError."""
    try:
        delete_source(session_dir=session_mgr.get_session_dir(session_id), source_id=source_id)
    except OpenDVError as exc:
        raise raise_from_opendv_error(exc) from exc
    except Exception as exc:
        msg, etype = str(exc), type(exc).__name__
        if "SOURCE_NOT_FOUND" in msg:
            raise StudioAPIError("SOURCE_NOT_FOUND", msg) from exc
        if "COMMITTED_VERSION" in msg or "SourceReferencedByCommittedVersionError" in etype:
            raise StudioAPIError(
                "SOURCE_REFERENCED_BY_COMMITTED_VERSION",
                f"Source '{source_id}' is referenced by a committed version and cannot be deleted.",
            ) from exc
        if "PENDING_PROPOSAL" in msg or "SourceReferencedByPendingProposalError" in etype:
            raise StudioAPIError(
                "SOURCE_REFERENCED_BY_PENDING_PROPOSAL",
                f"Source '{source_id}' is referenced by a pending proposal and cannot be deleted.",
            ) from exc
        raise StudioAPIError("EXECUTION_FAILURE", msg) from exc


def prop_dto(d: dict[str, Any], session_id: str, status: str | None = None) -> _s.ProposalRecordDTO:
    """Build ProposalRecordDTO from workflow dict."""
    return _s.ProposalRecordDTO(
        proposal_id=d["proposal_id"], session_id=session_id, base_version=d["base_version"],
        base_sha256=d["base_sha256"], status=status or d["status"], operations=d["operations"],
        proposer=d["proposer"], created_at=d["created_at"], rationale=d["rationale"],
    )


def validate_delta_adapter(
    session_mgr: StudioSessionManager, session_id: str,
    payload: _s.OntologyDeltaRequest, principal: StudioPrincipal,
) -> OntologyValidationResult:
    meta = get_session_or_404(session_mgr, session_id)
    with translate_studio_errors():
        return _dw.validate_session_delta_workflow(
            session_mgr=session_mgr, meta=meta, sdir=session_mgr.get_session_dir(session_id),
            payload_dict=payload.model_dump(mode="python"), principal_id=principal.principal,
            is_admin=principal.is_admin,
        )


def apply_delta_adapter(
    session_mgr: StudioSessionManager, session_id: str,
    payload: _s.OntologyDeltaRequest, principal: StudioPrincipal, if_match: str | None,
) -> _s.DeltaCommitResponse:
    meta = get_session_or_404(session_mgr, session_id)
    with translate_studio_errors():
        return _s.DeltaCommitResponse(
            **_dw.apply_session_delta_workflow(
                session_mgr=session_mgr, meta=meta, sdir=session_mgr.get_session_dir(session_id),
                payload_dict=payload.model_dump(mode="python"), if_match=if_match,
                principal_id=principal.principal, principal_role=principal.role,
                is_admin=principal.is_admin,
            )
        )


def create_proposal_adapter(
    session_mgr: StudioSessionManager, session_id: str,
    payload: _s.ProposalCreateRequest, principal: StudioPrincipal,
) -> _s.ProposalRecordDTO:
    meta = get_session_or_404(session_mgr, session_id)
    with translate_studio_errors():
        return _s.ProposalRecordDTO(
            **_pw.create_session_proposal_workflow(
                meta=meta, sdir=session_mgr.get_session_dir(session_id),
                proposal_id=payload.proposal_id, base_version_req=payload.base_version,
                base_sha256_req=payload.base_sha256, operations=payload.operations,
                rationale=payload.rationale, principal_id=principal.principal,
                is_automated=principal.is_automated,
            )
        )


def accept_proposal_adapter(
    session_mgr: StudioSessionManager, session_id: str, proposal_id: str,
    payload: _s.ProposalAcceptRequest, principal: StudioPrincipal, if_match: str | None,
) -> _s.DeltaCommitResponse:
    meta = get_session_or_404(session_mgr, session_id)
    with translate_studio_errors():
        return _s.DeltaCommitResponse(
            **_pw.accept_session_proposal_workflow(
                session_mgr=session_mgr, meta=meta, sdir=session_mgr.get_session_dir(session_id),
                proposal_id=proposal_id, if_match=if_match, base_sha256_req=payload.base_sha256,
                low_confidence_waiver=payload.low_confidence_waiver,
                self_acceptance_override=payload.self_acceptance_override,
                grounding_rationale=payload.grounding_rationale,
                explicit_ungrounded=payload.ungrounded_concept, principal_id=principal.principal,
                principal_role=principal.role, is_admin=principal.is_admin,
                is_automated=principal.is_automated,
            )
        )


def refine_proposal_adapter(
    session_mgr: StudioSessionManager, session_id: str, proposal_id: str,
    payload: _s.RefineProposalRequest, principal: StudioPrincipal, runner: Any,
) -> _s.ProposalRecordDTO:
    meta = get_session_or_404(session_mgr, session_id)
    with translate_studio_errors():
        return _s.ProposalRecordDTO(
            **_prw.refine_session_proposal_workflow(
                session_mgr=session_mgr, meta=meta, sdir=session_mgr.get_session_dir(session_id),
                proposal_id=proposal_id, steering_prompt=payload.steering_prompt, mode=payload.mode,
                principal_id=principal.principal, agent_runner=runner,
            )
        )


def promote_candidate_adapter(
    session_mgr: StudioSessionManager, session_id: str, candidate_id: str,
    payload: PromoteCandidateRequest, principal: StudioPrincipal,
) -> PromoteCandidateResponse:
    get_session_or_404(session_mgr, session_id)
    try:
        return StagedCandidatePromoter(
            session_mgr.get_session_dir(session_id), session_mgr
        ).promote_candidate(candidate_id, payload, principal)
    except OpenDVError as exc:
        raise raise_from_opendv_error(exc) from exc
    except Exception as exc:
        raise StudioAPIError("EXECUTION_FAILURE", str(exc)) from exc


def submit_waiver_adapter(
    session_mgr: StudioSessionManager, session_id: str,
    payload: _s.WaiverSubmissionRequest, principal: StudioPrincipal,
) -> dict[str, Any]:
    meta = get_session_or_404(session_mgr, session_id)
    with translate_studio_errors():
        return submit_session_waiver_workflow(
            session_mgr=session_mgr, meta=meta, sdir=session_mgr.get_session_dir(session_id),
            payload_dict=payload.model_dump(mode="python"), principal_id=principal.principal,
            resolve_proposal_fn=resolve_proposal_file,
        )


def submit_feedback_adapter(
    session_mgr: StudioSessionManager, session_id: str,
    payload: _s.FeedbackSubmissionRequest, principal: StudioPrincipal,
) -> _s.FeedbackSubmissionResponse:
    meta = get_session_or_404(session_mgr, session_id)
    with translate_studio_errors():
        return _s.FeedbackSubmissionResponse(
            **submit_feedback_workflow(
                session_mgr=session_mgr, meta=meta, sdir=session_mgr.get_session_dir(session_id),
                rating=payload.rating, comment=payload.comment, concept_uri=payload.concept_uri,
                proposal_id=payload.proposal_id, principal_id=principal.principal,
                principal_role=principal.role, is_automated=principal.is_automated,
            )
        )


def upload_text_source_adapter(
    session_mgr: StudioSessionManager, session_id: str,
    payload: _s.TextSourceUploadRequest, principal: StudioPrincipal,
) -> _s.SourceUploadResponse:
    get_session_or_404(session_mgr, session_id)
    try:
        res = session_mgr.add_text_source(
            session_id, text=payload.text, source_name=payload.source_name, format=payload.format,
            do_not_send_to_agent=payload.do_not_send_to_agent,
            description=payload.description, principal=principal.principal,
        )
        return _s.SourceUploadResponse(
            source_id=res["source_id"], source_sha256=res["source_sha256"],
            file_name=res["file_name"], filename=res["filename"], size_bytes=res["size_bytes"],
            byte_count=res["byte_count"], mime_type=res["mime_type"],
            do_not_send_to_agent=res["do_not_send_to_agent"],
        )
    except OpenDVError as exc:
        raise raise_from_opendv_error(exc) from exc
    except Exception as exc:
        raise StudioAPIError("EXECUTION_FAILURE", str(exc)) from exc


def read_source_content_bytes(
    session_mgr: StudioSessionManager, session_id: str, src: _s.SourceDocumentDTO,
    principal: StudioPrincipal,
) -> bytes:
    if principal.is_automated and src.do_not_send_to_agent:
        raise StudioAPIError(
            "CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN",
            f"Automated principal '{principal.principal}' is strictly forbidden "
            f"from accessing confidential source '{src.source_id}'.",
        )
    try:
        return session_mgr.get_cas(session_id).get(src.source_sha256)
    except Exception as exc:
        raise StudioAPIError(
            "SOURCE_NOT_FOUND", f"Raw blob for '{src.source_id}' missing in CAS."
        ) from exc


def reject_proposal_adapter(
    session_mgr: StudioSessionManager, session_id: str, proposal_id: str,
    payload: _s.RejectProposalRequest | None, principal: StudioPrincipal,
) -> _s.ProposalRecordDTO:
    get_session_or_404(session_mgr, session_id)
    with translate_studio_errors():
        d = _pw.reject_session_proposal_workflow(
            session_mgr=session_mgr, sdir=session_mgr.get_session_dir(session_id),
            session_id=session_id, proposal_id=proposal_id,
            reason=payload.reason if payload else None, principal_id=principal.principal,
            is_automated=principal.is_automated,
        )
        return prop_dto(d, session_id, status="REJECTED")
