"""Step 4-5 Graph A hydration, SemVer bump, BM25/citation grounding, and OCC commit."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import yaml

from opendv.grounding.providers.fibo.loader import FiboPackQueryAdapter
from opendv.models.diagnostics import VerifierDiagnostic
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import GroundingReceipt, OcrPageReceipt
from opendv.models.json_hasher import sha256_jcs
from opendv.models.ontology import GraphAOntology
from opendv.models.project import (
    EvolutionLockSpec,
    OntologyProposalSpec,
    OntologyWaiverSpec,
    RevisionMetadata,
    StagedCandidateSpec,
)
from opendv.models.sources import DocumentMap, SourceProfile
from opendv.models.wire_dtos_discovery import (
    CitationResolveReq,
    OntologyBundleResp,
    OntologyCommitReq,
    OntologyRevisionResp,
    OntologyValidateReq,
    OntologyValidationResp,
)
from opendv.service.citations import ensure_no_redacted_placeholder, resolve_citation
from opendv.service.parsing_svc import ParsingService
from opendv.service.state_store import ProjectStateManager
from opendv.service.workflows._ontology_deltas import (
    check_evolution_locks_diff,
    compile_and_apply_deltas,
)
from opendv.service.workflows._ontology_grounding import (
    collect_explicit_grounding_uris,
    collect_ungrounded_changed_concepts,
    compute_bump_spec,
)
from opendv.service.workflows._ontology_human_ops import reconcile_yaml_ontology
from opendv.service.workflows._source_docs import load_doc_pages_for_record
from opendv.verifier import lint_ontology_antipatterns


class DiscoveryOntologyHelper:
    """Server-side Step 4-5 Graph A hydration, validation, quarantine, and OCC commit."""

    def __init__(
        self,
        state_manager: ProjectStateManager,
        matcher: FiboPackQueryAdapter,
        workspace_root: Path,
        *,
        parsing_service: ParsingService | None = None,
    ) -> None:
        self._state = state_manager
        self._matcher = matcher
        self._workspace_root = workspace_root
        self._parsing = parsing_service

    def _load_active_ontology(self, project_id: str) -> tuple[str | None, GraphAOntology]:
        proj = self._state.get_project(project_id)
        if proj.active_ontology_rev_id is None:
            return None, GraphAOntology(
                ontology_version="0.0.0",
                domain_namespace=proj.domain_namespace,
            )
        rev = self._state.get_revision(project_id, proj.active_ontology_rev_id)
        return proj.active_ontology_rev_id, GraphAOntology.model_validate(rev["payload"])

    def _load_locks(self, project_id: str) -> list[EvolutionLockSpec]:
        revs = self._state.list_revisions(project_id, rev_kind="evolution_lock")
        return [EvolutionLockSpec.model_validate(r["payload"]) for r in revs]

    def _load_source_profiles(self, project_id: str) -> list[SourceProfile]:
        profiles: list[SourceProfile] = []
        for s in self._state.list_sources(project_id):
            rec = self._state.get_source_record(project_id, s.source_id)
            profiles.append(SourceProfile.model_validate(rec["profile"]))
        return profiles

    def _load_grounding_receipts(self, project_id: str) -> dict[str, GroundingReceipt]:
        out: dict[str, GroundingReceipt] = {}
        for row in self._state.list_events_or_receipts(
            project_id, record_kind="grounding_receipt"
        ):
            rcpt = GroundingReceipt.model_validate(row["payload"])
            out[rcpt.receipt_id] = rcpt
        return out

    def _verify_citations(
        self,
        project_id: str,
        ontology: GraphAOntology,
        *,
        strict_raise: bool = False,
    ) -> list[VerifierDiagnostic]:
        diags: list[VerifierDiagnostic] = []
        ocr_receipts = [
            OcrPageReceipt.model_validate(r["payload"])
            for r in self._state.list_events_or_receipts(project_id, record_kind="ocr_receipt")
        ]
        docs_by_id: dict[str, tuple[DocumentMap, dict[int, str] | None, bool]] = {}
        for s in self._state.list_sources(project_id):
            rec = self._state.get_source_record(project_id, s.source_id)
            if rec["document_map"] is None:
                continue
            dmap = DocumentMap.model_validate(rec["document_map"])
            meta_only = str(rec["status"]) == "METADATA_ONLY_BINARY_ABSENT"
            pages = (
                None
                if meta_only
                else load_doc_pages_for_record(
                    self._state,
                    self._workspace_root,
                    project_id,
                    s.source_id,
                    rec,
                    dmap,
                    parsing_service=self._parsing,
                )
            )
            docs_by_id[s.source_id] = (dmap, pages, meta_only)
            docs_by_id[dmap.doc_id] = (dmap, pages, meta_only)

        for concept in ontology.concepts:
            for ev in concept.mcp_evidence:
                try:
                    ensure_no_redacted_placeholder(ev.masked_excerpt)
                except OpenDVError as exc:
                    if strict_raise:
                        raise
                    diags.append(
                        VerifierDiagnostic(
                            gate="ONT",
                            rule_id="ONT-CITATION",
                            severity="ERROR",
                            entity_name=concept.concept_uri,
                            concept_uri=concept.concept_uri,
                            message=str(exc),
                        )
                    )
            for cit in concept.source_citations:
                try:
                    ensure_no_redacted_placeholder(cit.verbatim_quote)
                    doc_entry = docs_by_id.get(cit.doc_id)
                    if doc_entry is None:
                        raise OpenDVError(
                            f"Citation on '{concept.concept_uri}' references unknown "
                            f"document '{cit.doc_id}'.",
                            error_code=ErrorCode.CITATION_SPAN_UNRESOLVED,
                        )
                    dmap, page_map, meta_only = doc_entry
                    resp = resolve_citation(
                        CitationResolveReq(
                            doc_id=cit.doc_id,
                            page_number=cit.page_number,
                            verbatim_quote=cit.verbatim_quote,
                            occurrence=cit.occurrence,
                            content_sha256=cit.content_sha256,
                        ),
                        parsed_pages=page_map,
                        ocr_receipts=ocr_receipts,
                        metadata_only_binary_absent=meta_only,
                        document_map=dmap,
                    )
                    if not resp.matched:
                        raise OpenDVError(
                            f"Unverified citation span on '{concept.concept_uri}' "
                            f"(doc={cit.doc_id}, page={cit.page_number}).",
                            error_code=ErrorCode.CITATION_SPAN_UNRESOLVED,
                        )
                except OpenDVError as exc:
                    if strict_raise:
                        raise
                    diags.append(
                        VerifierDiagnostic(
                            gate="ONT",
                            rule_id="ONT-CITATION",
                            severity="ERROR",
                            entity_name=concept.concept_uri,
                            concept_uri=concept.concept_uri,
                            message=str(exc),
                        )
                    )
        return diags

    def get_ontology_bundle(self, project_id: str) -> OntologyBundleResp:
        """Return active Graph A ontology, staged candidates, proposals, and locks."""
        _rev_id, ont = self._load_active_ontology(project_id)
        staged = [
            StagedCandidateSpec.model_validate(r["payload"])
            for r in self._state.list_revisions(project_id, rev_kind="staged_candidate")
        ]
        proposals = [
            OntologyProposalSpec.model_validate(r["payload"])
            for r in self._state.list_revisions(project_id, rev_kind="ontology_proposal")
        ]
        locks = self._load_locks(project_id)
        latest_lock = max(locks, key=lambda lk: lk.wave_number, default=None)
        return OntologyBundleResp(
            ontology=ont,
            staged_candidates=staged,
            ontology_proposals=proposals,
            evolution_locks=latest_lock,
        )

    def _parse_yaml_ontology(self, yaml_text: str) -> GraphAOntology:
        try:
            loaded = yaml.safe_load(yaml_text)
            if not isinstance(loaded, dict):
                raise OpenDVError(
                    "Ontology YAML payload must decode to a mapping object.",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                )
            ont = GraphAOntology.model_validate(loaded)
        except OpenDVError:
            raise
        except (yaml.YAMLError, ValueError, TypeError) as exc:
            raise OpenDVError(
                f"Invalid Graph A ontology YAML: {exc}",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            ) from exc
        return ont

    def validate_ontology(
        self,
        project_id: str,
        req: OntologyValidateReq,
    ) -> OntologyValidationResp:
        """Validate Graph A YAML or delta ops against locks, antipatterns, and citations."""
        active_rev, base = self._load_active_ontology(project_id)
        if req.base_revision_id is not None and req.base_revision_id != active_rev:
            raise OpenDVError(
                f"Base revision mismatch: expected '{active_rev}', got '{req.base_revision_id}'.",
                error_code=ErrorCode.REVISION_CONFLICT,
            )
        locks = self._load_locks(project_id)
        profiles = self._load_source_profiles(project_id)
        receipts_by_id = self._load_grounding_receipts(project_id)
        if req.operations is not None:
            candidate = compile_and_apply_deltas(
                base,
                req.operations,
                actor="human",
                locks=locks,
                matcher=self._matcher,
                receipts_by_id=receipts_by_id,
                profiles=profiles,
            )
        else:
            parsed = self._parse_yaml_ontology(req.yaml_text or "")
            candidate = reconcile_yaml_ontology(
                base,
                parsed,
                self._matcher,
                receipts_by_id=receipts_by_id,
                profiles=profiles,
            )
            check_evolution_locks_diff(base, candidate, locks)

        diags = lint_ontology_antipatterns(candidate, profiles)
        diags.extend(self._verify_citations(project_id, candidate))
        for ungrounded in collect_ungrounded_changed_concepts(base, candidate, set()):
            diags.append(
                VerifierDiagnostic(
                    gate="ONT",
                    rule_id="ONT-GROUNDING",
                    severity="WARN",
                    entity_name=ungrounded.concept_uri,
                    concept_uri=ungrounded.concept_uri,
                    waivable=True,
                    message=(
                        f"Concept '{ungrounded.concept_uri}' lacks Path A (>=0.6500) or Path B "
                        "grounding and will be quarantined unless grounded or waived."
                    ),
                )
            )
        bump = compute_bump_spec(base, candidate)
        has_errors = any(d.severity in ("BLOCKER", "ERROR") for d in diags)
        return OntologyValidationResp(valid=not has_errors, diagnostics=diags, proposed_bump=bump)

    def commit_ontology(
        self,
        project_id: str,
        req: OntologyCommitReq,
    ) -> OntologyRevisionResp:
        """OCC-protected Graph A commit with server delta compilation, quarantine, and proposals."""
        active_rev, base = self._load_active_ontology(project_id)
        if req.base_revision_id != active_rev:
            raise OpenDVError(
                f"OCC conflict: active revision is '{active_rev}', got '{req.base_revision_id}'.",
                error_code=ErrorCode.REVISION_CONFLICT,
            )
        locks = self._load_locks(project_id)
        profiles = self._load_source_profiles(project_id)
        receipts_by_id = self._load_grounding_receipts(project_id)
        if req.operations is not None:
            candidate = compile_and_apply_deltas(
                base,
                req.operations,
                actor="human",
                locks=locks,
                matcher=self._matcher,
                receipts_by_id=receipts_by_id,
                profiles=profiles,
            )
        else:
            parsed = self._parse_yaml_ontology(req.yaml_text or "")
            candidate = reconcile_yaml_ontology(
                base,
                parsed,
                self._matcher,
                receipts_by_id=receipts_by_id,
                profiles=profiles,
            )
            check_evolution_locks_diff(base, candidate, locks)

        explicit_uris = collect_explicit_grounding_uris(req)
        ungrounded = collect_ungrounded_changed_concepts(base, candidate, explicit_uris)
        waiver_signed = False
        waiver_rationale: str | None = None
        if ungrounded:
            gw = req.grounding_waiver
            use_low_conf = req.low_confidence_grounding_waiver or (
                gw is not None and gw.low_confidence_grounding_waiver
            )
            allowed_concepts = (
                set(req.low_confidence_waiver_concepts)
                if req.low_confidence_waiver_concepts is not None
                else (
                    set(gw.low_confidence_waiver_concepts)
                    if gw is not None and gw.low_confidence_waiver_concepts is not None
                    else None
                )
            )
            eff_rationale = (
                req.grounding_rationale
                or (gw.grounding_rationale if gw is not None else None)
                or req.rationale
            )
            waive_all = req.waive_quarantine and len(req.rationale.strip()) >= 20
            waive_low = (
                use_low_conf
                and len(eff_rationale.strip()) >= 20
                and (
                    allowed_concepts is None
                    or all(c.concept_uri in allowed_concepts for c in ungrounded)
                )
            )
            if not (waive_all or waive_low):
                uris = [c.concept_uri for c in ungrounded]
                raise OpenDVError(
                    f"Ungrounded concept(s) quarantined (Path B2): {uris}",
                    error_code=ErrorCode.UNGROUNDED_CANDIDATE_QUARANTINED,
                )
            waiver_signed = True
            waiver_rationale = eff_rationale if waive_low and not waive_all else req.rationale

        for waiver_spec in req.ont_ap_waivers:
            if len(waiver_spec.rationale.strip()) < 20:
                raise OpenDVError(
                    f"Ontology waiver rationale for '{waiver_spec.rule_id}' must be at least "
                    "20 non-whitespace characters.",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                )

        self._verify_citations(project_id, candidate, strict_raise=True)

        bump = compute_bump_spec(base, candidate)
        final_version = bump.next_version if bump.bump_kind != "NONE" else base.ontology_version
        committed_ont = candidate.model_copy(update={"ontology_version": final_version})
        content_sha = sha256_jcs(committed_ont)
        rev_id = f"ont-{final_version}-{uuid.uuid4().hex[:8]}"
        now_iso = datetime.now(tz=UTC).isoformat()

        if waiver_signed and ungrounded and waiver_rationale is not None:
            concept_uris = ",".join(c.concept_uri for c in ungrounded)
            digest = sha256_jcs(
                {
                    "project_id": project_id,
                    "rev_id": rev_id,
                    "rule_id": "ONT-GROUNDING",
                    "concept_uri": concept_uris,
                    "rationale": waiver_rationale.strip(),
                }
            )[:8]
            waiver_id = f"ONTW-{digest}"
            waiver = OntologyWaiverSpec(
                waiver_id=waiver_id,
                rule_id="ONT-GROUNDING",
                concept_uri=concept_uris,
                rationale=waiver_rationale,
                created_at=now_iso,
            )
            self._state.save_revision(
                project_id,
                revision_id=f"ontw-{waiver_id}-{uuid.uuid4().hex[:6]}",
                rev_kind="ontology_waiver",
                rev_key=waiver_id,
                parent_rev_id=active_rev,
                payload=waiver,
                metadata=RevisionMetadata(
                    actor="human",
                    reason=waiver_rationale,
                    base_revision_id=active_rev,
                    is_waiver_signed=True,
                    waiver_rationale=waiver_rationale,
                ),
            )

        for idx, waiver_spec in enumerate(req.ont_ap_waivers):
            ap_digest = sha256_jcs(
                {
                    "project_id": project_id,
                    "rev_id": rev_id,
                    "index": idx,
                    "rule_id": waiver_spec.rule_id,
                    "concept_uri": waiver_spec.concept_uri,
                    "rationale": waiver_spec.rationale.strip(),
                }
            )[:8]
            ap_waiver_id = f"ONTW-{ap_digest}"
            stamped_waiver = waiver_spec.model_copy(
                update={"waiver_id": ap_waiver_id, "created_at": now_iso}
            )
            self._state.save_revision(
                project_id,
                revision_id=f"ontw-{ap_waiver_id}-{uuid.uuid4().hex[:6]}",
                rev_kind="ontology_waiver",
                rev_key=ap_waiver_id,
                parent_rev_id=active_rev,
                payload=stamped_waiver,
                metadata=RevisionMetadata(
                    actor="human",
                    reason=stamped_waiver.rationale,
                    base_revision_id=active_rev,
                    is_waiver_signed=True,
                    waiver_rationale=stamped_waiver.rationale,
                ),
            )

        self._state.save_revision(
            project_id,
            revision_id=rev_id,
            rev_kind="ontology_version",
            rev_key=rev_id,
            parent_rev_id=active_rev,
            payload=committed_ont,
            metadata=RevisionMetadata(
                actor="human",
                reason=req.rationale,
                base_revision_id=active_rev,
                is_waiver_signed=waiver_signed,
                waiver_rationale=waiver_rationale,
            ),
        )
        updated_proj = self._state.update_project_pointers(
            project_id, active_ontology_rev_id=rev_id
        )

        committed_set = set(req.committed_proposal_ids)
        dismissed_set = set(req.dismissed_proposal_ids)
        if committed_set or dismissed_set:
            for row in self._state.list_revisions(project_id, rev_kind="ontology_proposal"):
                prop = OntologyProposalSpec.model_validate(row["payload"])
                next_status: Literal["COMMITTED", "DISMISSED"] | None = None
                if prop.proposal_id in committed_set:
                    next_status = "COMMITTED"
                elif prop.proposal_id in dismissed_set:
                    next_status = "DISMISSED"
                if next_status is not None and prop.status != next_status:
                    updated_prop = prop.model_copy(update={"status": next_status})
                    self._state.save_revision(
                        project_id,
                        revision_id=f"prop-{prop.proposal_id}-{next_status.lower()}",
                        rev_kind="ontology_proposal",
                        rev_key=prop.proposal_id,
                        parent_rev_id=str(row["revision_id"]),
                        payload=updated_prop,
                        metadata=RevisionMetadata(
                            actor="human",
                            reason=f"Steward commit {rev_id}: {next_status}",
                            base_revision_id=active_rev,
                        ),
                    )
            for row in self._state.list_revisions(project_id, rev_kind="staged_candidate"):
                cand = StagedCandidateSpec.model_validate(row["payload"])
                if cand.candidate_id in committed_set and cand.status != "PROMOTED":
                    self._state.save_revision(
                        project_id,
                        revision_id=f"cand-{cand.candidate_id}-promoted-{uuid.uuid4().hex[:4]}",
                        rev_kind="staged_candidate",
                        rev_key=cand.candidate_id,
                        parent_rev_id=str(row["revision_id"]),
                        payload=cand.model_copy(update={"status": "PROMOTED"}),
                        metadata=RevisionMetadata(
                            actor="human",
                            reason=f"Steward commit {rev_id}: PROMOTED",
                            base_revision_id=active_rev,
                        ),
                    )

        return OntologyRevisionResp(
            revision_id=rev_id,
            ontology_version=final_version,
            content_sha256=content_sha,
            updated_at=updated_proj.updated_at,
        )
