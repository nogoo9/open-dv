"""Held-back candidate concept query and promotion engine.

Supports:
- Path A: Synonym re-scoring against FIBO reference classes asserting score >= 0.6500.
- Path B: Admin waiver promotion requiring admin role, >= 10 character rationale,
  HMAC-signed StewardWaiverRecord in waivers.json, and low_confidence_grounding_waiver=True.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from opendv_contracts.dtos.ontology import (
    PATH_A_GROUNDING_THRESHOLD,
    AddAttributeOp,
    AddConceptOp,
    AddExternalMappingOp,
    AddRelationshipOp,
    OntologyDelta,
    StewardWaiverRecord,
)
from opendv_contracts.dtos.promotion import (
    PromoteCandidateRequest,
    PromoteCandidateResponse,
    StagedCandidateDTO,
)
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_service.ontology.applier import parse_semver
from opendv_service.ontology.matching.verifier import get_default_grounding_verifier
from opendv_service.studio.locks import reentrant_session_lock
from opendv_service.studio.session_manager import (
    StudioSessionManager,
    append_signed_audit_record,
    replace_with_retry,
    set_owner_only_permissions,
)

logger = logging.getLogger(__name__)

# Grounding score thresholds and validation constants
MIN_GROUNDING_THRESHOLD: float = PATH_A_GROUNDING_THRESHOLD
EXACT_MATCH_GROUNDING_THRESHOLD: float = 0.8500
MIN_WAIVER_RATIONALE_LENGTH: int = 10
GENESIS_VERSION_BUMP: str = "1.0.0"


def _canonical_slug(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip()).strip("_").lower()
    return cleaned or "concept"


class StagedCandidatePromoter:
    """Manages queries and Path A / Path B promotion for held-back candidate concepts."""

    def __init__(self, session_dir: Path, session_mgr: StudioSessionManager) -> None:
        self.session_dir = session_dir
        self.session_mgr = session_mgr
        self.evidence_file = session_dir / "evidence" / "staged_candidates.json"
        self.lock_file = session_dir / ".session.lock"

    def list_staged_candidates(self) -> list[StagedCandidateDTO]:
        """Query held-back candidate concepts from session evidence."""
        if not self.evidence_file.is_file():
            return []
        try:
            data = json.loads(self.evidence_file.read_text(encoding="utf-8"))
            raw_list = data.get("staged_candidates", [])
            return [
                StagedCandidateDTO(
                    candidate_id=c.get("candidate_id", ""),
                    concept_name=c.get("concept_name", ""),
                    suggested_hub=c.get("suggested_hub", ""),
                    top_match_uri=c.get("top_match_uri"),
                    top_match_score=float(c.get("top_match_score", 0.0)),
                    reason_held_back=c.get("reason_held_back", "GROUNDING_THRESHOLD_UNMET"),
                    evidence_refs=c.get("evidence_refs", []),
                    attributes=c.get("attributes", []),
                    dependent_links=c.get("dependent_links", []),
                )
                for c in raw_list
                if isinstance(c, dict)
            ]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("Failed to read staged_candidates.json: %s", exc)
            return []

    def promote_candidate(
        self,
        candidate_id: str,
        request: PromoteCandidateRequest,
        principal: Any,
    ) -> PromoteCandidateResponse:
        """Promote a held-back candidate via Path A or Path B."""
        session_id = self.session_dir.name
        with reentrant_session_lock(self.lock_file, session_id=session_id):
            if not self.evidence_file.is_file():
                raise OpenDVError(
                    f"Candidate '{candidate_id}' not found in staged holding area.",
                    error_code=ErrorCode.SOURCE_NOT_FOUND,
                )

            data = json.loads(self.evidence_file.read_text(encoding="utf-8"))
            candidates = data.get("staged_candidates", [])
            target = next((c for c in candidates if c.get("candidate_id") == candidate_id), None)
            if not target:
                raise OpenDVError(
                    f"Candidate '{candidate_id}' not found in staged holding area.",
                    error_code=ErrorCode.SOURCE_NOT_FOUND,
                )

            concept_name = target.get("concept_name", "")
            suggested_hub = target.get("suggested_hub") or f"hub_{_canonical_slug(concept_name)}"
            concept_uri = f"urn:opendv:concept:{_canonical_slug(concept_name)}"

            operations: list[Any] = []
            applied_waiver_id: str | None = None
            fibo_uri: str | None = None
            confidence: float = 0.0

            if request.path == "path_a":
                synonyms = [s.strip() for s in (request.synonyms or []) if s.strip()]
                if not synonyms:
                    raise OpenDVError(
                        "Path A promotion requires at least one synonym in 'synonyms'.",
                        error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                    )

                verifier = get_default_grounding_verifier()
                queries = [concept_name, *synonyms, f"{concept_name} {' '.join(synonyms)}"]
                best_score = 0.0
                best_match = None

                for q in queries:
                    matches = verifier._matcher.match(q, top_k=5)
                    for m in matches:
                        if m.normalized_score > best_score:
                            best_score = m.normalized_score
                            best_match = m

                if best_score < MIN_GROUNDING_THRESHOLD or best_match is None:
                    raise OpenDVError(
                        f"Re-evaluated BM25 score ({best_score:.4f}) does not meet "
                        f"the {MIN_GROUNDING_THRESHOLD:.4f} grounding threshold.",
                        error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
                    )

                confidence = best_score
                fibo_uri = best_match.concept_uri
                predicate = (
                    "skos:exactMatch"
                    if best_score >= EXACT_MATCH_GROUNDING_THRESHOLD
                    else "skos:closeMatch"
                )

                add_concept = AddConceptOp(
                    op_id=f"op-concept-{_canonical_slug(concept_name)}-{uuid.uuid4().hex[:6]}",
                    concept_uri=concept_uri,
                    concept_name=concept_name,
                    target_hub=suggested_hub,
                    synonyms=synonyms,
                )
                add_mapping = AddExternalMappingOp(
                    op_id=f"op-map-{_canonical_slug(concept_name)}-{uuid.uuid4().hex[:6]}",
                    concept_uri=concept_uri,
                    target_uri=fibo_uri,
                    scheme="fibo",
                    confidence=best_score,
                    predicate=predicate,
                )
                operations = [
                    add_concept.model_dump(mode="json", exclude_none=True),
                    add_mapping.model_dump(mode="json", exclude_none=True),
                ]

            elif request.path == "path_b":
                role = getattr(principal, "role", "viewer")
                if role != "admin":
                    raise OpenDVError(
                        "Path B promotion requires admin role.",
                        error_code=ErrorCode.FORBIDDEN_ADMIN_REQUIRED,
                    )

                rationale = (request.waiver_rationale or "").strip()
                if len(rationale) < MIN_WAIVER_RATIONALE_LENGTH:
                    raise OpenDVError(
                        f"Waiver rationale must be at least "
                        f"{MIN_WAIVER_RATIONALE_LENGTH} characters.",
                        error_code=ErrorCode.INVALID_GROUNDING_WAIVER,
                    )

                meta = self.session_mgr.get_session(session_id)
                waiver_id = uuid.uuid4()
                applied_waiver_id = str(waiver_id)
                confidence = float(target.get("top_match_score", 0.0))
                fibo_uri = target.get("top_match_uri")

                draft_delta_sha = "0" * 64
                actor_name = getattr(principal, "principal", str(principal))

                waiver_record = StewardWaiverRecord(
                    waiver_id=waiver_id,
                    proposal_id=uuid.uuid4(),
                    proposal_delta_sha256=draft_delta_sha,
                    target_concept_uris=[concept_uri],
                    base_sha256=meta.head_sha256,
                    rationale=rationale,
                    authorized_by=actor_name,
                    authorized_role="admin",
                    signature_or_token=self.session_mgr.security_manager.sign_waiver(
                        f"{actor_name}:{meta.head_sha256}:{rationale}".encode()
                    ),
                )

                waivers_file = self.session_dir / "waivers.json"
                wdata: dict[str, Any] = {"waivers": []}
                if waivers_file.is_file():
                    try:
                        wdata = json.loads(waivers_file.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        wdata = {"waivers": []}
                wdata.setdefault("waivers", []).append(waiver_record.model_dump(mode="json"))
                tmp_w = self.session_dir / f".waivers.{uuid.uuid4().hex[:8]}.tmp"
                tmp_w.write_text(json.dumps(wdata, indent=2), encoding="utf-8")
                replace_with_retry(tmp_w, waivers_file)
                set_owner_only_permissions(waivers_file)

                add_concept = AddConceptOp(
                    op_id=f"op-concept-{_canonical_slug(concept_name)}-{uuid.uuid4().hex[:6]}",
                    concept_uri=concept_uri,
                    concept_name=concept_name,
                    target_hub=suggested_hub,
                )
                operations = [add_concept.model_dump(mode="json", exclude_none=True)]
                if fibo_uri:
                    add_mapping = AddExternalMappingOp(
                        op_id=f"op-map-{_canonical_slug(concept_name)}-{uuid.uuid4().hex[:6]}",
                        concept_uri=concept_uri,
                        target_uri=fibo_uri,
                        scheme="fibo",
                        confidence=confidence,
                        predicate="skos:closeMatch",
                    )
                    operations.append(add_mapping.model_dump(mode="json", exclude_none=True))

            for attr in target.get("attributes", []):
                attr_name = attr.get("attribute_name", "attribute")
                attr_op = AddAttributeOp(
                    op_id=f"op-attr-{_canonical_slug(concept_name)}-{_canonical_slug(attr_name)}-{uuid.uuid4().hex[:6]}",
                    concept_uri=concept_uri,
                    attribute_name=attr_name,
                    data_type=attr.get("data_type", "string"),
                )
                operations.append(attr_op.model_dump(mode="json", exclude_none=True))

            for lnk in target.get("dependent_links", []):
                rel_op = AddRelationshipOp(
                    op_id=f"op-rel-{_canonical_slug(concept_name)}-{uuid.uuid4().hex[:6]}",
                    relationship_uri=lnk.get(
                        "relationship_uri",
                        f"urn:opendv:relationship:{_canonical_slug(concept_name)}_rel",
                    ),
                    target_link=lnk.get("target_link", f"lnk_{_canonical_slug(concept_name)}"),
                    endpoints=lnk.get(
                        "endpoints", [concept_uri, "urn:opendv:concept:legal_entity"]
                    ),
                )
                operations.append(rel_op.model_dump(mode="json", exclude_none=True))

            updated_candidates = [c for c in candidates if c.get("candidate_id") != candidate_id]
            data["staged_candidates"] = updated_candidates
            tmp_c = self.evidence_file.parent / f".staged.{uuid.uuid4().hex[:8]}.tmp"
            tmp_c.write_text(json.dumps(data, indent=2), encoding="utf-8")
            replace_with_retry(tmp_c, self.evidence_file)
            set_owner_only_permissions(self.evidence_file)

            safe_cid = _canonical_slug(candidate_id)
            prop_id = f"prop-promote-{safe_cid}-{uuid.uuid4().hex[:6]}"
            meta = self.session_mgr.get_session(session_id)
            delta_metadata: dict[str, Any] = {
                "promoted_candidate_id": candidate_id,
                "promotion_path": request.path,
            }
            if request.path == "path_b":
                delta_metadata["low_confidence_grounding_waiver"] = True
                delta_metadata["waiver_id"] = applied_waiver_id

            b_maj, b_min, _ = parse_semver(meta.head_version)
            target_ver = GENESIS_VERSION_BUMP if b_maj == 0 else f"{b_maj}.{b_min + 1}.0"

            delta = OntologyDelta(
                delta_id=f"delta-{uuid.uuid4().hex[:12]}",
                parent_sha256=meta.head_sha256,
                base_version=meta.head_version,
                target_version=target_ver,
                domain_namespace=meta.domain_namespace,
                operations=operations,
                author=getattr(principal, "principal", str(principal)),
                rationale=f"Promoted staged candidate '{candidate_id}' via {request.path.upper()}.",
                metadata=delta_metadata,
            )

            record = {
                "proposal_id": prop_id,
                "session_id": session_id,
                "base_version": meta.head_version,
                "target_version": target_ver,
                "base_sha256": meta.head_sha256,
                "status": "PENDING",
                "operations": operations,
                "proposer": getattr(principal, "principal", str(principal)),
                "created_at": datetime.now(UTC).isoformat(),
                "rationale": delta.rationale,
                "delta": delta.model_dump(mode="json", exclude_none=True),
            }
            proposals_dir = self.session_dir / "proposals"
            proposals_dir.mkdir(parents=True, exist_ok=True)
            props_real = os.path.realpath(proposals_dir)
            props_pfx = props_real if props_real.endswith(os.sep) else props_real + os.sep
            prop_real = os.path.realpath(os.path.join(props_real, f"{prop_id}.json"))
            tmp_real = os.path.realpath(
                os.path.join(props_real, f".{prop_id}.{uuid.uuid4().hex[:8]}.tmp")
            )
            if not prop_real.startswith(props_pfx) or not tmp_real.startswith(props_pfx):
                raise ValueError(f"Invalid proposal path for candidate '{candidate_id}'")
            prop_file = Path(prop_real)
            tmp_prop = Path(tmp_real)
            tmp_prop.write_text(json.dumps(record, indent=2), encoding="utf-8", newline="\n")
            replace_with_retry(tmp_prop, prop_file)
            set_owner_only_permissions(prop_file)

            append_signed_audit_record(
                session_dir=self.session_dir,
                action="STAGED_CANDIDATE_PROMOTED",
                actor=getattr(principal, "principal", str(principal)),
                details={
                    "candidate_id": candidate_id,
                    "concept_uri": concept_uri,
                    "path": request.path,
                    "confidence": confidence,
                    "fibo_uri": fibo_uri,
                    "proposal_id": prop_id,
                },
                security_manager=self.session_mgr.security_manager,
            )

            return PromoteCandidateResponse(
                candidate_id=candidate_id,
                status="PROMOTED",
                path=request.path,
                concept_uri=concept_uri,
                target_hub=suggested_hub,
                fibo_uri=fibo_uri,
                confidence_score=confidence,
                operations_count=len(operations),
                operations=operations,
                waiver_id=applied_waiver_id,
                proposal_id=prop_id,
            )


__all__ = ["StagedCandidatePromoter"]
