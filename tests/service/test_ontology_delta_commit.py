"""M2C tests for server-side 34-op OntologyDelta compiler, OCC, locks, quarantine, and Step 4 safety."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from opendv.models.diagnostics import (
    ClarificationQuestion,
    DocumentCitationRef,
    ExternalMcpEvidenceRef,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import OcrPageReceipt
from opendv.models.json_hasher import sha256_jcs
from opendv.models.ontology import (
    CanonicalBusinessConcept,
    ConceptAttribute,
    ConceptTableBinding,
    ExternalMapping,
    KeyAuthorityPattern,
    OntologyEnumeration,
    OntologyRelationship,
)
from opendv.models.ontology_deltas import (
    AGENT_ADDITIVE_OPS,
    HUMAN_ONLY_OPS,
    SERVER_ONLY_OPS,
    AddAttribute,
    AddConcept,
    AddEnumeration,
    AddEnumerationCode,
    AddExternalMapping,
    AddKeyAuthorityPattern,
    AddMcpEvidence,
    AddRelationship,
    AddSourceCitation,
    AddSynonym,
    AddTableBinding,
    ChangeDomainNamespace,
    ClearParentConcept,
    DeleteConcept,
    DeleteEnumeration,
    DeleteRelationship,
    RemoveAttribute,
    RemoveEnumerationCode,
    RemoveExternalMapping,
    RemoveKeyAuthorityPattern,
    RemoveMcpEvidence,
    RemoveSourceCitation,
    RemoveSynonym,
    RemoveTableBinding,
    RenameConcept,
    ReparentConcept,
    RepinExternalMapping,
    RescoreExternalMapping,
    ReviseDescription,
    SetConceptGovernanceTags,
    SetConceptKeyAuthority,
    SetDescription,
    SetParentConcept,
    UpdateAttributeMetadata,
)
from opendv.models.project import (
    EvolutionLockSpec,
    OntologyProposalSpec,
    OntologyWaiverSpec,
    RevisionMetadata,
)
from opendv.models.sources import ConnectorConfigPayload, DocumentMap, SourceProfile
from opendv.models.vault_ir_certified import (
    CertifiedHubSpec,
    CertifiedVaultIR,
    PhysicalLayoutHints,
    SourceBindingSpec,
)
from opendv.models.wire_dtos_discovery import (
    OntologyCommitReq,
    OntologyConstructReq,
    OntologyValidateReq,
    ProjectInitReq,
    SourceAttachReq,
)
from opendv.service import OpenDVService
from opendv.service.workflows._ontology_deltas import (
    check_evolution_locks_diff,
    compile_and_apply_deltas,
)
from opendv.service.workflows._vault_helpers import load_verifier_inputs
from opendv.sources.document_map import compute_canonical_text_sha256
from opendv.sources.fibo.loader import CANONICAL_FIBO_CORE_SHA256
from opendv.verifier._v4_unstructured import verify_v4_unstructured
from tests.helpers import DeterministicMockHarness, MockTurnScript


def _make_service(tmp_path: Path, harness: DeterministicMockHarness | None = None) -> OpenDVService:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "state"
    return OpenDVService(ws, state_dir=state_dir, harness=harness)


def _seeded_concept_ops() -> list[AddConcept]:
    return [
        AddConcept(
            concept_uri="urn:asset_mgmt:InstitutionalFiler",
            concept_name="Institutional Filer",
            description="Registered institutional investment manager filing Form 13F.",
            key_authority_tier="TIER_1_GLOBAL_STANDARD",
            bkcc_default="SEC_CIK",
            attributes=[
                ConceptAttribute(
                    name="filer_cik",
                    logical_type="UTF8_STRING",
                    sql_type_hint="VARCHAR",
                    nullable=False,
                    is_business_key=True,
                    is_pii=False,
                ),
                ConceptAttribute(
                    name="filer_name",
                    logical_type="UTF8_STRING",
                    sql_type_hint="VARCHAR",
                    nullable=False,
                    is_business_key=False,
                    is_pii=False,
                ),
            ],
            table_bindings=[
                ConceptTableBinding(
                    source_id="src-1",
                    table_id="src-1:institutional_filers",
                    col_name="filer_cik",
                )
            ],
        )
    ]


def test_all_34_delta_ops_accounted_and_compiled(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    proj = svc.init_project(ProjectInitReq(project_id="delta-proj", domain_namespace="asset_mgmt"))
    assert proj.active_ontology_rev_id is not None

    notes = svc.workspace_root / "notes.md"
    notes.write_text("# Stewardship Notes\nProxy voting requires documented rationale.\n", encoding="utf-8")
    doc_sum = svc.attach_source(
        "delta-proj", SourceAttachReq(kind="markdown", uri_or_filename=str(notes))
    )

    fibo_hit = svc.fibo_qa(
        __import__("opendv.models.wire_dtos_discovery", fromlist=["FiboQueryReq"]).FiboQueryReq(
            query="Legal Entity", top_k=1
        )
    ).matches[0]

    add_ops = [
        *_seeded_concept_ops(),
        AddConcept(
            concept_uri="urn:asset_mgmt:Security",
            concept_name="Security",
            description="Tradable financial instrument.",
            key_authority_tier="TIER_1_GLOBAL_STANDARD",
            table_bindings=[
                ConceptTableBinding(
                    source_id="src-1", table_id="src-1:securities", col_name="cusip"
                )
            ],
        ),
        AddAttribute(
            concept_uri="urn:asset_mgmt:Security",
            attribute=ConceptAttribute(
                name="cusip",
                logical_type="UTF8_STRING",
                sql_type_hint="VARCHAR",
                nullable=False,
                is_business_key=True,
                is_pii=False,
            ),
        ),
        AddRelationship(
            relationship=OntologyRelationship(
                relationship_uri="urn:asset_mgmt:rel:Holds",
                relationship_name="Holds",
                source_concept_uri="urn:asset_mgmt:InstitutionalFiler",
                target_concept_uri="urn:asset_mgmt:Security",
                cardinality="ONE_TO_MANY",
                target_link="lnk_filer_security",
                grain_semantics="One row per filer-security holding.",
                description="Filer holds security.",
            )
        ),
        AddEnumeration(
            enumeration=OntologyEnumeration(
                enum_uri="urn:asset_mgmt:enum:VoteCast",
                enumeration_name="VoteCast",
                target_reference_table="ref_vote_cast",
                codes={"FOR": "In favor"},
                description="Proxy vote choices.",
            )
        ),
        AddEnumerationCode(
            enum_uri="urn:asset_mgmt:enum:VoteCast",
            code="AGAINST",
            description="Opposed",
        ),
        AddSynonym(concept_uri="urn:asset_mgmt:Security", synonym="Legal Entity"),
        AddExternalMapping(
            concept_uri="urn:asset_mgmt:Security",
            mapping=ExternalMapping(
                standard_uri=fibo_hit.concept_uri,
                reference_pack_id="fibo-core",
                reference_pack_sha256=CANONICAL_FIBO_CORE_SHA256,
                mapping_type="skos:exactMatch",
                confidence=Decimal("0.5000"),
            ),
        ),
        SetParentConcept(
            concept_uri="urn:asset_mgmt:Security",
            parent_concept_uri="urn:asset_mgmt:InstitutionalFiler",
        ),
        SetDescription(
            concept_uri="urn:asset_mgmt:Security",
            description="Updated instrument description.",
        ),
        SetConceptKeyAuthority(
            concept_uri="urn:asset_mgmt:Security",
            key_authority_tier="TIER_2_ENTERPRISE_SOR",
            bkcc_default="CUSIP",
        ),
        SetConceptGovernanceTags(
            concept_uri="urn:asset_mgmt:Security",
            governance_tags=["REGULATORY"],
            governance_constraints={"scope": "SEC_13F"},
        ),
        UpdateAttributeMetadata(
            concept_uri="urn:asset_mgmt:Security",
            attribute_name="cusip",
            nullable=False,
            is_pii=False,
        ),
        AddTableBinding(
            concept_uri="urn:asset_mgmt:Security",
            binding=ConceptTableBinding(
                source_id="src-1", table_id="src-1:holdings_13f", col_name="cusip"
            ),
        ),
        AddSourceCitation(
            concept_uri="urn:asset_mgmt:Security",
            citation=DocumentCitationRef(
                doc_id=doc_sum.source_id,
                page_number=1,
                verbatim_quote="Proxy voting requires documented rationale.",
            ),
        ),
        AddMcpEvidence(
            concept_uri="urn:asset_mgmt:Security",
            evidence=ExternalMcpEvidenceRef(
                receipt_id="mcpr-1",
                server_id="sec-edgar",
                tool_name="lookup_cusip",
                content_sha256="a" * 64,
                masked_excerpt="Validated CUSIP definition.",
            ),
        ),
        AddKeyAuthorityPattern(
            concept_uri="urn:asset_mgmt:Security",
            pattern=KeyAuthorityPattern(
                tier="TIER_1_GLOBAL_STANDARD",
                authority_name="CUSIP_GLOBAL",
                column_patterns=["cusip"],
                bkcc_policy="CUSIP",
            ),
        ),
    ]
    assert {op.op_type for op in add_ops} == AGENT_ADDITIVE_OPS

    val_resp = svc.validate_ontology("delta-proj", OntologyValidateReq(operations=add_ops))
    assert val_resp.valid
    assert val_resp.proposed_bump.bump_kind == "MINOR"

    rev1 = svc.commit_ontology(
        "delta-proj",
        OntologyCommitReq(
            operations=add_ops,
            base_revision_id=proj.active_ontology_rev_id,
            rationale="Commit additive Graph A concepts.",
        ),
    )
    assert rev1.ontology_version == "0.1.0"

    # Now exercise all 17 HUMAN_ONLY_OPS
    human_ops = [
        ReparentConcept(
            concept_uri="urn:asset_mgmt:Security",
            parent_concept_uri="urn:asset_mgmt:Root",
        ),
        ClearParentConcept(concept_uri="urn:asset_mgmt:Security"),
        RenameConcept(
            concept_uri="urn:asset_mgmt:Security", new_concept_name="Financial Security"
        ),
        RemoveExternalMapping(
            concept_uri="urn:asset_mgmt:Security", standard_uri=fibo_hit.concept_uri
        ),
        RepinExternalMapping(
            concept_uri="urn:asset_mgmt:Security",
            standard_uri=fibo_hit.concept_uri,
            reference_pack_id="fibo-core",
            reference_pack_sha256=CANONICAL_FIBO_CORE_SHA256,
        ),
        RemoveSynonym(concept_uri="urn:asset_mgmt:Security", synonym="Legal Entity"),
        RemoveTableBinding(
            concept_uri="urn:asset_mgmt:Security",
            source_id="src-1",
            table_id="src-1:holdings_13f",
            col_name="cusip",
        ),
        RemoveSourceCitation(
            concept_uri="urn:asset_mgmt:Security",
            doc_id=doc_sum.source_id,
            page_number=1,
            verbatim_quote="Proxy voting requires documented rationale.",
        ),
        RemoveMcpEvidence(concept_uri="urn:asset_mgmt:Security", receipt_id="mcpr-1"),
        RemoveKeyAuthorityPattern(
            concept_uri="urn:asset_mgmt:Security", authority_name="CUSIP_GLOBAL"
        ),
        ReviseDescription(
            concept_uri="urn:asset_mgmt:Security", description="Steward-revised security node."
        ),
        RemoveAttribute(concept_uri="urn:asset_mgmt:Security", attribute_name="cusip"),
        DeleteRelationship(relationship_uri="urn:asset_mgmt:rel:Holds"),
        RemoveEnumerationCode(enum_uri="urn:asset_mgmt:enum:VoteCast", code="AGAINST"),
        DeleteEnumeration(enum_uri="urn:asset_mgmt:enum:VoteCast"),
        DeleteConcept(concept_uri="urn:asset_mgmt:Security"),
        ChangeDomainNamespace(domain_namespace="asset_mgmt_v2"),
    ]
    assert {op.op_type for op in human_ops} == HUMAN_ONLY_OPS
    assert len(AGENT_ADDITIVE_OPS) + len(HUMAN_ONLY_OPS) + len(SERVER_ONLY_OPS) == 34

    rev2 = svc.commit_ontology(
        "delta-proj",
        OntologyCommitReq(
            operations=human_ops,
            base_revision_id=rev1.revision_id,
            rationale="Steward cleanup via human-only destructive delta operations.",
        ),
    )
    assert rev2.ontology_version == "1.0.0"
    bundle = svc.get_ontology_bundle("delta-proj")
    assert bundle.ontology.domain_namespace == "asset_mgmt_v2"
    assert [c.concept_uri for c in bundle.ontology.concepts] == [
        "urn:asset_mgmt:InstitutionalFiler"
    ]


def test_occ_server_only_agent_dops_and_evolution_locks(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    proj = svc.init_project(ProjectInitReq(project_id="occ-proj", domain_namespace="asset_mgmt"))
    assert proj.active_ontology_rev_id is not None

    rev1 = svc.commit_ontology(
        "occ-proj",
        OntologyCommitReq(
            operations=_seeded_concept_ops(),
            base_revision_id=proj.active_ontology_rev_id,
            rationale="Initial grounded concept commit.",
        ),
    )

    # Stale base_revision_id -> 409 REVISION_CONFLICT
    with pytest.raises(OpenDVError) as exc_occ:
        svc.commit_ontology(
            "occ-proj",
            OntologyCommitReq(
                operations=[
                    SetDescription(
                        concept_uri="urn:asset_mgmt:InstitutionalFiler",
                        description="Updated desc",
                    )
                ],
                base_revision_id="stale-rev-id",
                rationale="Should fail OCC check.",
            ),
        )
    assert exc_occ.value.error_code == ErrorCode.REVISION_CONFLICT

    # Client-submitted SERVER_ONLY_OPS (RescoreExternalMapping) rejected
    with pytest.raises(OpenDVError) as exc_srv:
        svc.validate_ontology(
            "occ-proj",
            OntologyValidateReq(
                operations=[
                    RescoreExternalMapping(
                        concept_uri="urn:asset_mgmt:InstitutionalFiler",
                        standard_uri="urn:fibo:x",
                        confidence=Decimal("0.9000"),
                    )
                ]
            ),
        )
    assert exc_srv.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    # Agent actor forbidden from HUMAN_ONLY_OPS
    bundle = svc.get_ontology_bundle("occ-proj")
    with pytest.raises(OpenDVError) as exc_agent:
        compile_and_apply_deltas(
            bundle.ontology,
            [DeleteConcept(concept_uri="urn:asset_mgmt:InstitutionalFiler")],
            actor="agent",
        )
    assert exc_agent.value.error_code == ErrorCode.ONT_EVO_CONTRACT_VIOLATION

    # Active evolution_locks prevent deleting locked hub concepts or changing namespace
    lock = EvolutionLockSpec(
        wave_number=1,
        locked_hub_bks={"urn:asset_mgmt:InstitutionalFiler": ["filer_cik"]},
    )
    svc.state_manager.save_revision(
        "occ-proj",
        revision_id="lock-w1",
        rev_kind="evolution_lock",
        rev_key="lock-w1",
        parent_rev_id=None,
        payload=lock,
        metadata=RevisionMetadata(actor="agent", reason="Sealed wave 1"),
    )
    with pytest.raises(OpenDVError) as exc_lock:
        svc.commit_ontology(
            "occ-proj",
            OntologyCommitReq(
                operations=[DeleteConcept(concept_uri="urn:asset_mgmt:InstitutionalFiler")],
                base_revision_id=rev1.revision_id,
                rationale="Attempt to delete locked concept.",
            ),
        )
    assert exc_lock.value.error_code == ErrorCode.ONT_EVO_CONTRACT_VIOLATION


def test_quarantine_waiver_and_proposal_transitions(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    proj = svc.init_project(ProjectInitReq(project_id="quar-proj", domain_namespace="asset_mgmt"))
    assert proj.active_ontology_rev_id is not None

    # Bogus ungrounded concept with inflated confidence=0.99 gets rescored to 0.0000 -> quarantined
    ungrounded_op = AddConcept(
        concept_uri="urn:asset_mgmt:GhostConcept",
        concept_name="Xyzzy Nonexistent Concept",
        description="Ungrounded concept.",
        key_authority_tier="TIER_3_LOCAL_SCOPED",
        external_mappings=[
            ExternalMapping(
                standard_uri="https://spec.edmcouncil.org/fibo/ontology/FND/Agents/AutonomousAgent",
                reference_pack_id="fibo-core",
                reference_pack_sha256=CANONICAL_FIBO_CORE_SHA256,
                mapping_type="skos:exactMatch",
                confidence=Decimal("0.9900"),
            )
        ],
    )
    with pytest.raises(OpenDVError) as exc_q:
        svc.commit_ontology(
            "quar-proj",
            OntologyCommitReq(
                operations=[ungrounded_op],
                base_revision_id=proj.active_ontology_rev_id,
                rationale="Missing grounding should quarantine.",
            ),
        )
    assert exc_q.value.error_code == ErrorCode.UNGROUNDED_CANDIDATE_QUARANTINED

    # Short <20 char rationale rejected by OntologyCommitReq validator
    with pytest.raises(ValidationError):
        OntologyCommitReq(
            operations=[ungrounded_op],
            base_revision_id=proj.active_ontology_rev_id,
            rationale="Too short",
            waive_quarantine=True,
        )

    # Seed two PENDING_STEWARD proposals to verify committed/dismissed transitions
    for pid in ("prop-keep", "prop-drop"):
        prop = OntologyProposalSpec(
            proposal_id=pid,
            wave_number=1,
            delta_ops=[
                AddConcept(
                    concept_uri="urn:asset_mgmt:GhostConcept",
                    concept_name="Xyzzy Nonexistent Concept",
                    description="Ungrounded concept.",
                    key_authority_tier="TIER_3_LOCAL_SCOPED",
                )
            ],
            summary="Pending steward decision.",
            status="PENDING_STEWARD",
        )
        svc.state_manager.save_revision(
            "quar-proj",
            revision_id=f"rev-{pid}",
            rev_kind="ontology_proposal",
            rev_key=pid,
            parent_rev_id=None,
            payload=prop,
            metadata=RevisionMetadata(actor="agent", reason="Seed proposal"),
        )

    rev = svc.commit_ontology(
        "quar-proj",
        OntologyCommitReq(
            operations=[ungrounded_op],
            base_revision_id=proj.active_ontology_rev_id,
            rationale="Steward explicitly approves domain ghost concept for wave 1 staging.",
            waive_quarantine=True,
            committed_proposal_ids=["prop-keep"],
            dismissed_proposal_ids=["prop-drop"],
        ),
    )
    assert rev.ontology_version == "0.1.0"
    waivers = svc.state_manager.list_revisions("quar-proj", rev_kind="ontology_waiver")
    assert len(waivers) == 1
    assert str(waivers[0]["rev_key"]).startswith("ONTW-")

    statuses = {
        p.proposal_id: p.status for p in svc.get_ontology_bundle("quar-proj").ontology_proposals
    }
    assert statuses["prop-keep"] == "COMMITTED"
    assert statuses["prop-drop"] == "DISMISSED"


@pytest.mark.asyncio
async def test_step4_never_autonomously_commits_graph_a(tmp_path: Path) -> None:
    harness = DeterministicMockHarness(
        scripts_by_agent={
            "ontology-first-mate": [
                MockTurnScript(
                    structured_output={
                        "proposed_ops": [
                            _seeded_concept_ops()[0].model_dump(mode="json"),
                        ],
                        "clarification_questions": [],
                        "summary": "Proposed InstitutionalFiler.",
                    }
                )
            ]
        }
    )
    svc = _make_service(tmp_path, harness=harness)
    proj = svc.init_project(ProjectInitReq(project_id="step4-proj", domain_namespace="asset_mgmt"))
    initial_rev = proj.active_ontology_rev_id

    run = await svc.start_ontology_construction("step4-proj", OntologyConstructReq())
    assert run.status == "COMPLETED"

    after_proj = svc.get_project("step4-proj")
    assert after_proj.active_ontology_rev_id == initial_rev
    assert not after_proj.has_active_lease

    bundle = svc.get_ontology_bundle("step4-proj")
    assert bundle.ontology.concepts == []
    assert len(bundle.staged_candidates) == 1
    assert bundle.staged_candidates[0].status in ("QUARANTINED", "WAIVED")


@pytest.mark.asyncio
async def test_uploaded_doc_citations_commit_enforcement_and_step4_prompt(tmp_path: Path) -> None:
    captured_prompts: list[str] = []

    def _selector(req):
        captured_prompts.append(req.prompt)
        return MockTurnScript(
            structured_output={
                "proposed_ops": [],
                "clarification_questions": [],
                "summary": "Checked uploaded doc.",
            }
        )

    svc = _make_service(tmp_path, harness=DeterministicMockHarness(selector=_selector))
    proj = svc.init_project(ProjectInitReq(project_id="up-cit", domain_namespace="asset_mgmt"))
    assert proj.active_ontology_rev_id is not None

    up_doc = svc.upload_source(
        "up-cit",
        filename="policy.md",
        content=b"# Stewardship Policy\nStewardshipEngagementCase must be logged.\n",
        kind="markdown",
    )
    run4 = await svc.start_ontology_construction("up-cit", OntologyConstructReq())
    assert run4.status == "COMPLETED"
    assert captured_prompts
    assert "policy.md" in captured_prompts[0]
    assert f"source={up_doc.source_id}" in captured_prompts[0]

    def _concept_with_quote(quote: str, doc_id: str = up_doc.source_id) -> list[AddConcept]:
        return [
            AddConcept(
                concept_uri="urn:asset_mgmt:StewardshipEngagementCase",
                concept_name="Stewardship Engagement Case",
                description="Formal stewardship engagement tracking.",
                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                source_citations=[
                    DocumentCitationRef(doc_id=doc_id, page_number=1, verbatim_quote=quote)
                ],
            )
        ]

    # Valid citation on uploaded doc passes validate_ontology
    ok_val = svc.validate_ontology(
        "up-cit",
        OntologyValidateReq(operations=_concept_with_quote("StewardshipEngagementCase must be logged.")),
    )
    assert ok_val.valid is True
    assert not any(d.rule_id == "ONT-CITATION" for d in ok_val.diagnostics)

    # Unmatched quote returns valid=False with ONT-CITATION ERROR diagnostic (never crashes)
    bad_val = svc.validate_ontology(
        "up-cit",
        OntologyValidateReq(operations=_concept_with_quote("Nonexistent bogus quote")),
    )
    assert bad_val.valid is False
    assert any(d.gate == "ONT" and d.rule_id == "ONT-CITATION" and d.severity == "ERROR" for d in bad_val.diagnostics)

    # Leaked placeholder returns valid=False with ONT-CITATION diagnostic
    leak_val = svc.validate_ontology(
        "up-cit",
        OntologyValidateReq(operations=_concept_with_quote("Email <MASKED_PII> leak")),
    )
    assert leak_val.valid is False
    assert any(d.rule_id == "ONT-CITATION" for d in leak_val.diagnostics)

    # Commit enforces citation resolution & placeholder guard BEFORE Graph A persistence
    with pytest.raises(OpenDVError) as exc_bad:
        svc.commit_ontology(
            "up-cit",
            OntologyCommitReq(
                operations=_concept_with_quote("Nonexistent bogus quote"),
                base_revision_id=proj.active_ontology_rev_id,
                rationale="Should reject unresolved quote before Graph A write.",
            ),
        )
    assert exc_bad.value.error_code == ErrorCode.CITATION_SPAN_UNRESOLVED
    assert svc.get_project("up-cit").active_ontology_rev_id == proj.active_ontology_rev_id

    with pytest.raises(OpenDVError) as exc_leak:
        svc.commit_ontology(
            "up-cit",
            OntologyCommitReq(
                operations=_concept_with_quote("Officer <MASKED_PII> quote"),
                base_revision_id=proj.active_ontology_rev_id,
                rationale="Should reject leaked placeholder before Graph A write.",
            ),
        )
    assert exc_leak.value.error_code == ErrorCode.REDACTED_PLACEHOLDER_LEAK
    assert svc.get_project("up-cit").active_ontology_rev_id == proj.active_ontology_rev_id

    # OCR receipt resolution also succeeds in validate_ontology and commit_ontology
    ocr_text = "OCR custody clause alpha"
    ocr_sha = compute_canonical_text_sha256(ocr_text)
    svc.state_manager.append_event_or_receipt(
        "up-cit",
        record_kind="ocr_receipt",
        run_id="ocr-test",
        payload=OcrPageReceipt(
            receipt_id="ocr-rec-1",
            doc_id=up_doc.source_id,
            page_number=2,
            ocr_text_masked=ocr_text,
            content_sha256=ocr_sha,
            ocr_box_count=1,
        ),
    )
    ocr_ops = [
        AddConcept(
            concept_uri="urn:asset_mgmt:CustodyClause",
            concept_name="Custody Clause",
            description="OCR-backed concept.",
            key_authority_tier="TIER_1_GLOBAL_STANDARD",
            source_citations=[
                DocumentCitationRef(
                    doc_id=up_doc.source_id,
                    page_number=2,
                    verbatim_quote="custody clause alpha",
                    content_sha256=ocr_sha,
                )
            ],
        )
    ]
    assert svc.validate_ontology("up-cit", OntologyValidateReq(operations=ocr_ops)).valid is True
    ocr_rev = svc.commit_ontology(
        "up-cit",
        OntologyCommitReq(
            operations=ocr_ops,
            base_revision_id=proj.active_ontology_rev_id,
            rationale="Commit OCR-backed citation cleanly.",
        ),
    )
    assert ocr_rev.ontology_version == "0.1.0"


def test_server_stamps_ont_ap_waivers_jcs_hash_and_delta_guards(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    proj = svc.init_project(ProjectInitReq(project_id="waiv-proj", domain_namespace="asset_mgmt"))
    assert proj.active_ontology_rev_id is not None

    ungrounded = AddConcept(
        concept_uri="urn:asset_mgmt:CustomNode",
        concept_name="Xyzzy Custom Node",
        description="Initial description.",
        key_authority_tier="TIER_2_ENTERPRISE_SOR",
    )

    # Short ont_ap_waivers rationale (<20 chars) rejected with SYNTAX_INVALID_ARGUMENT
    with pytest.raises(OpenDVError) as exc_short:
        svc.commit_ontology(
            "waiv-proj",
            OntologyCommitReq(
                operations=[ungrounded],
                base_revision_id=proj.active_ontology_rev_id,
                rationale="Valid twenty-plus char commit rationale here.",
                waive_quarantine=True,
                ont_ap_waivers=[
                    OntologyWaiverSpec(
                        waiver_id="CLIENT-SPOOFED",
                        rule_id="ONT-AP-001",
                        concept_uri="urn:asset_mgmt:CustomNode",
                        rationale="too short",
                    )
                ],
            ),
        )
    assert exc_short.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    # Empty & client-spoofed waiver_id are server-stamped ONTW-<sha8> with UTC ISO created_at
    rev = svc.commit_ontology(
        "waiv-proj",
        OntologyCommitReq(
            operations=[ungrounded],
            base_revision_id=proj.active_ontology_rev_id,
            rationale="Steward approves waiver rationale with twenty plus characters.",
            waive_quarantine=True,
            ont_ap_waivers=[
                OntologyWaiverSpec(
                    waiver_id="",
                    rule_id="ONT-AP-001",
                    concept_uri="urn:asset_mgmt:CustomNode",
                    rationale="Approved ONT-AP-001 waiver with sufficient rationale.",
                ),
                OntologyWaiverSpec(
                    waiver_id="CLIENT-SPOOFED",
                    rule_id="ONT-AP-002",
                    concept_uri="urn:asset_mgmt:CustomNode",
                    rationale="Approved ONT-AP-002 waiver overriding client ID cleanly.",
                ),
            ],
        ),
    )
    bundle = svc.get_ontology_bundle("waiv-proj")
    assert rev.content_sha256 == sha256_jcs(bundle.ontology)
    stored_rev = svc.state_manager.get_revision("waiv-proj", rev.revision_id)
    assert rev.content_sha256 == stored_rev["content_sha256"]

    waivers = svc.state_manager.list_revisions("waiv-proj", rev_kind="ontology_waiver")
    assert len(waivers) == 3
    for w_row in waivers:
        w = OntologyWaiverSpec.model_validate(w_row["payload"])
        assert w.waiver_id.startswith("ONTW-")
        assert len(w.waiver_id) == 13
        assert w.waiver_id != "CLIENT-SPOOFED"
        assert w.created_at
        datetime.fromisoformat(w.created_at)

    # Agent SetDescription rejects overwriting non-empty description
    with pytest.raises(OpenDVError) as exc_desc:
        compile_and_apply_deltas(
            bundle.ontology,
            [
                SetDescription(
                    concept_uri="urn:asset_mgmt:CustomNode",
                    description="Agent overwrite attempt.",
                )
            ],
            actor="agent",
        )
    assert exc_desc.value.error_code == ErrorCode.ONT_EVO_CONTRACT_VIOLATION

    # Locked relationship cannot be overwritten via AddRelationship or mutated in diff
    rel = OntologyRelationship(
        relationship_uri="urn:asset_mgmt:rel:Own",
        relationship_name="Owns",
        source_concept_uri="urn:asset_mgmt:CustomNode",
        target_concept_uri="urn:asset_mgmt:CustomNode",
        cardinality="ONE_TO_MANY",
        target_link="lnk_custom_own",
        grain_semantics="One row per ownership link.",
        description="Custom node ownership link.",
    )
    with_rel = compile_and_apply_deltas(bundle.ontology, [AddRelationship(relationship=rel)])
    lock = EvolutionLockSpec(wave_number=1, locked_links={"lnk_custom_own": ["hk_a"]})
    mutated_rel = rel.model_copy(update={"target_link": "lnk_mutated"})
    with pytest.raises(OpenDVError) as exc_rel_op:
        compile_and_apply_deltas(
            with_rel, [AddRelationship(relationship=mutated_rel)], locks=[lock]
        )
    assert exc_rel_op.value.error_code == ErrorCode.ONT_EVO_CONTRACT_VIOLATION
    with pytest.raises(OpenDVError) as exc_rel_diff:
        check_evolution_locks_diff(
            with_rel,
            with_rel.model_copy(update={"relationships": [mutated_rel]}),
            [lock],
        )
    assert exc_rel_diff.value.error_code == ErrorCode.ONT_EVO_CONTRACT_VIOLATION


@pytest.mark.asyncio
async def test_step4_lease_release_on_failure_and_hold_with_proposed_ops(
    tmp_path: Path,
) -> None:
    svc = _make_service(
        tmp_path,
        harness=DeterministicMockHarness(
            scripts_by_agent={"ontology-first-mate": [MockTurnScript(text_output="{}")]}
        ),
    )
    svc.init_project(ProjectInitReq(project_id="lease-proj", domain_namespace="asset_mgmt"))
    # Insert a corrupt document_map row so build_source_and_doc_context fails inside try
    svc.state_manager.upsert_source(
        "lease-proj",
        source_id="src-corrupt",
        kind="markdown",
        uri_or_filename="missing.md",
        binary_sha256=None,
        connector_config=ConnectorConfigPayload(adapter_id="markdown", display_name="MD"),
        status="READY_OFFLINE",
        profile=SourceProfile(source_id="src-corrupt", schema_fingerprint="", table_count=0, tables=[]),
        document_map=DocumentMap(
            doc_id="src-corrupt",
            filename="missing.md",
            format="markdown",
            page_count=1,
            approved_scan_pages=[],
            outline=[],
            pages=[],
        ),
        tables_index=[],
    )
    # Corrupt document_map_json directly in SQLite to trigger ValidationError after lease claim
    from opendv.service._state_ddl import begin_immediate

    with begin_immediate(svc.state_manager.db_path) as conn:
        conn.execute(
            "UPDATE sources_and_tables SET document_map_json = '{bad' WHERE project_id = ? AND source_id = ?",
            ("lease-proj", "src-corrupt"),
        )
    with pytest.raises(ValueError):
        await svc.start_ontology_construction("lease-proj", OntologyConstructReq())
    assert not svc.get_project("lease-proj").has_active_lease

    # Coordinator calling MCP ask_domain_clarification AND returning proposed_ops ends HOLD_CHECKPOINTED
    from opendv.agents.ports import AgentTurnResult

    class _HoldAndProposeHarness(DeterministicMockHarness):
        async def run_turn(self, request, *, event_sink=None, scout_dispatcher=None):
            import httpx

            headers = {
                "Authorization": f"Bearer {request.role_token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            }
            q_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "ask_domain_clarification",
                    "arguments": {
                        "questions": [
                            ClarificationQuestion(
                                question_id="q-hold-1",
                                concept_name="Filer",
                                title="Confirm key",
                                prompt="Confirm filer key.",
                                sample_rows=["filer_cik=0001"],
                                impact_if_locked="Locks key.",
                            ).model_dump(mode="json")
                        ]
                    },
                },
            }
            async with httpx.AsyncClient() as client:
                init_resp = await client.post(
                    request.broker_url,
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 0,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "test", "version": "1"},
                        },
                    },
                )
                sid = init_resp.headers.get("mcp-session-id")
                if sid:
                    headers["mcp-session-id"] = sid
                await client.post(
                    request.broker_url,
                    headers=headers,
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                )
                call_resp = await client.post(request.broker_url, headers=headers, json=q_payload)
                assert call_resp.status_code == 200
            payload = {
                "proposed_ops": [_seeded_concept_ops()[0].model_dump(mode="json")],
                "clarification_questions": [],
                "summary": "Asked hold and also emitted ops.",
            }
            return AgentTurnResult(
                agent_id=request.agent_id,
                provider="agy",
                model_id="mock-model",
                text_output="",
                structured_output=payload,
                turns_used=1,
                input_tokens=10,
                output_tokens=10,
                total_tokens=20,
                token_count_source="estimated",
            )

    svc2 = _make_service(tmp_path / "s2", harness=_HoldAndProposeHarness())
    svc2.init_project(ProjectInitReq(project_id="hold-ops", domain_namespace="asset_mgmt"))
    run_hold = await svc2.start_ontology_construction("hold-ops", OntologyConstructReq())
    assert run_hold.status == "HOLD_CHECKPOINTED"
    assert not svc2.get_project("hold-ops").has_active_lease


def test_load_verifier_inputs_mixed_absent_and_present_docs(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    proj = svc.init_project(ProjectInitReq(project_id="mix-proj", domain_namespace="asset_mgmt"))
    assert proj.active_ontology_rev_id is not None

    # First source (alphabetically/chronologically early): METADATA_ONLY_BINARY_ABSENT
    svc.state_manager.upsert_source(
        "mix-proj",
        source_id="src-00-absent",
        kind="csv",
        uri_or_filename="absent.csv",
        binary_sha256=None,
        connector_config=ConnectorConfigPayload(adapter_id="csv", display_name="CSV"),
        status="METADATA_ONLY_BINARY_ABSENT",
        profile=SourceProfile(source_id="src-00-absent", schema_fingerprint="", table_count=0, tables=[]),
        document_map=None,
        tables_index=[],
    )
    # Second source: locally present markdown document
    up_doc = svc.upload_source(
        "mix-proj",
        filename="present.md",
        content=b"# Present Doc\nLocal mandate clause verified on disk.\n",
        kind="markdown",
    )

    _ont, _profs, _cls, _locks, _prior, _assump, ctx = load_verifier_inputs(
        svc.state_manager, svc.workspace_root, "mix-proj"
    )
    assert ctx.metadata_only_binary_absent is True
    assert up_doc.source_id in ctx.parsed_doc_pages
    assert 1 in ctx.parsed_doc_pages[up_doc.source_id]

    # V4 verifies citation on present.md cleanly even without content_sha256 when another source is absent
    quote = "Local mandate clause verified on disk."
    page1 = ctx.parsed_doc_pages[up_doc.source_id][1]
    start = page1.index(quote)
    ir = CertifiedVaultIR(
        project_id="mix-proj",
        ontology_version="0.1.0",
        wave_number=1,
        hubs=[
            CertifiedHubSpec(
                hub_name="hub_mandate",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[],
                is_planned_concept=True,
                source_bindings=[
                    SourceBindingSpec(
                        source_id=up_doc.source_id,
                        table_id="doc:present",
                        col_mappings={},
                        doc_citations=[
                            DocumentCitationRef(
                                doc_id=up_doc.source_id,
                                page_number=1,
                                verbatim_quote=quote,
                                char_start=start,
                                char_end=start + len(quote),
                            )
                        ],
                    )
                ],
                physical_hints=PhysicalLayoutHints(),
            )
        ],
    )
    assert verify_v4_unstructured(ir, ctx) == []
