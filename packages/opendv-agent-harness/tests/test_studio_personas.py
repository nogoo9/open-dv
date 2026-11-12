"""Tests for 3 Ontology Studio personas & privacy."""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from opendv_agent_harness.models import AgentPersona, ModelTier
from opendv_agent_harness.personas import (
    DomainClarifier,
    OntologyArchitect,
    TermScout,
    build_domain_clarifier_subagent_spec,
    build_domain_clarifier_task_spec,
    build_ontology_architect_subagent_spec,
    build_ontology_architect_task_spec,
    build_persona_subagent_spec,
    build_persona_task_spec,
    build_term_scout_task_spec,
)
from opendv_agent_harness.privacy import (
    AgentDestructiveOpForbiddenError,
    AgentUnresolvedPseudonymError,
    DanglingParentReferenceError,
    Pseudonymizer,
    RedactedPlaceholderLeakError,
    scan_redacted_placeholder_leak,
    scan_unresolved_pseudonyms,
)
from opendv_agent_harness.routing import get_persona_capability
from opendv_contracts.dtos.clarification import ClarificationQuestion
from opendv_contracts.dtos.ingestion import EvidenceChunk, TableStructure, TaintClassification
from opendv_contracts.dtos.ontology import (
    AddAttributeOp,
    AddConceptOp,
    AddExternalMappingOp,
    AddGovernanceConstraintOp,
    AddKeyAuthorityPatternOp,
    AddReferenceEnumCodeOp,
    AddReferenceEnumerationOp,
    AddRelationshipOp,
    AddSynonymOp,
    EvidenceRef,
    OntologyDelta,
    OntologyProposal,
    RemoveConceptOp,
    RescoreExternalMappingOp,
    SetConceptElmArchetypeOp,
    SetParentConceptOp,
    StewardWaiverRecord,
)
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_contracts.jcs import dumps_jcs
from opendv_contracts.spi.agent import ConstructionAgent
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    ExternalMapping,
    GraphAOntology,
    ReferenceEnumeration,
)
from opendv_core.ontology_hashing import compute_ontology_sha256


@dataclass(frozen=True)
class _StubMatch:
    concept_uri: str
    normalized_score: float


class _StubMatcher:
    """Deterministic local matcher stub for Path A grounding tests."""

    def __init__(self, score_by_term: dict[str, tuple[str, float]]) -> None:
        self._score_by_term = {k.lower(): v for k, v in score_by_term.items()}

    def match(self, query: str, top_k: int = 1) -> list[_StubMatch]:
        q = query.strip().lower()
        if q in self._score_by_term:
            uri, score = self._score_by_term[q]
            return [_StubMatch(concept_uri=uri, normalized_score=score)][:top_k]
        return []


def _fibo_ext(
    uri: str = "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity",
    pred: str = "skos:exactMatch",
    conf: float = 0.90,
) -> ExternalMapping:
    return ExternalMapping(
        scheme="FIBO",
        target_uri=uri,
        predicate=pred,
        confidence=conf,
    )


def _chk(
    cid: str = "c1",
    text: str = "A Legal Entity signed.",
    taint: str | None = None,
    meta: dict[str, Any] | None = None,
    start: int = 0,
    end: int | None = None,
) -> EvidenceChunk:
    kw: dict[str, Any] = {
        "chunk_id": cid,
        "document_id": "d1",
        "text": text,
        "start_char": start,
        "end_char": end if end is not None else (start + len(text)),
    }
    if taint is not None:
        kw["taint"] = taint
    if meta is not None:
        kw["metadata"] = meta
    return EvidenceChunk(**kw)


def _tbl(
    tid: str = "t1",
    headers: list[str] | None = None,
    rows: list[list[str]] | None = None,
    taint: str | None = "public",
    meta: dict[str, Any] | None = None,
) -> TableStructure:
    kw: dict[str, Any] = {
        "table_id": tid,
        "headers": headers or ["id"],
        "rows": rows or [["1"]],
    }
    if taint is not None:
        kw["taint"] = taint
    if meta is not None:
        kw["metadata"] = meta
    return TableStructure(**kw)


def _c(
    uid: str,
    name: str = "",
    hub: str = "",
    ev: list[Any] | None = None,
    ext: list[ExternalMapping] | None = None,
) -> AddConceptOp:
    slug = uid.rsplit(":", 1)[-1] if ":" in uid else uid
    curi = uid if uid.startswith("urn:") else f"urn:ontology:entity:{slug}"
    return AddConceptOp(
        op_id=f"c-{slug}",
        concept_uri=curi,
        concept_name=name or slug.replace("_", " ").title(),
        target_hub=hub or f"hub_{slug}",
        evidence_refs=ev or [],
        external_mappings=ext,
    )


def _r(
    uid: str, src: str, tgt: str, link: str = "", ev: list[Any] | None = None
) -> AddRelationshipOp:
    slug = uid.rsplit(":", 1)[-1] if ":" in uid else uid
    ruri = uid if uid.startswith("urn:") else f"urn:ontology:relationship:{slug}"
    return AddRelationshipOp(
        op_id=f"r-{slug}",
        relationship_uri=ruri,
        target_link=link or f"lnk_{slug}",
        source_concept_uri=src,
        target_concept_uri=tgt,
        evidence_refs=ev or [],
    )


def _a(curi: str, name: str, dt: str = "varchar", is_optional: bool = True) -> AddAttributeOp:
    return AddAttributeOp(
        op_id=f"a-{name}",
        concept_uri=curi,
        attribute_name=name,
        data_type=dt,
        is_optional=is_optional,
    )


def _code(euri: str, code: str, ename: str = "", desc: str | None = None) -> AddReferenceEnumCodeOp:
    return AddReferenceEnumCodeOp(
        op_id=f"cd-{code.lower()}",
        enum_uri=euri,
        enumeration_name=ename,
        code=code,
        description=desc,
    )


def _enum(
    euri: str = "", name: str = "", codes: list[str] | None = None, tbl: str = ""
) -> AddReferenceEnumerationOp:
    slug = euri.rsplit(":", 1)[-1] if euri else name
    return AddReferenceEnumerationOp(
        op_id=f"ref-{slug}",
        enum_uri=euri,
        name=name or slug,
        target_reference_table=tbl or f"ref_{slug}",
        allowed_codes=["ACTIVE"] if codes is None else codes,
    )


def _m(
    curi: str,
    uri: str,
    pred: str = "skos:exactMatch",
    conf: float = 0.85,
) -> AddExternalMappingOp:
    return AddExternalMappingOp(
        op_id=f"m-{hashlib.sha256(uri.encode()).hexdigest()[:6]}",
        concept_uri=curi,
        scheme="FIBO",
        target_uri=uri,
        predicate=pred,
        confidence=conf,
        mapping_confidence=conf,
    )


def test_persona_capabilities_and_tiers() -> None:
    t = get_persona_capability(AgentPersona.TERM_SCOUT)
    c = get_persona_capability(AgentPersona.DOMAIN_CLARIFIER)
    a = get_persona_capability(AgentPersona.ONTOLOGY_ARCHITECT)
    assert t.default_model_tier == ModelTier.FAST_TIER and t.sandbox_policy.read_only_workspace
    assert c.default_model_tier == a.default_model_tier == ModelTier.HIGH_REASONING
    assert a.allowed_subagents == (
        AgentPersona.TERM_SCOUT.value,
        AgentPersona.DOMAIN_CLARIFIER.value,
    )
    assert "extract_pdf_spans" in t.authorized_tools
    assert "request_steward_decision" in c.authorized_tools
    assert "run_axi_dv_gates" in a.authorized_tools


def test_subagent_and_task_spec_builders() -> None:
    s_scout = build_persona_subagent_spec(AgentPersona.TERM_SCOUT)
    s_clar = build_domain_clarifier_subagent_spec()
    s_arch = build_ontology_architect_subagent_spec()
    assert (
        s_scout.model_tier == ModelTier.FAST_TIER and s_clar.model_tier == ModelTier.HIGH_REASONING
    )
    assert s_arch.persona == AgentPersona.ONTOLOGY_ARCHITECT
    assert (
        build_persona_task_spec(AgentPersona.ONTOLOGY_ARCHITECT, prompt="Build delta").prompt
        == "Build delta"
    )


def test_task_spec_privacy_guards_reject_redacted_placeholders_and_raw_tainted_inputs() -> None:
    for builder in (
        build_term_scout_task_spec,
        build_domain_clarifier_task_spec,
        build_ontology_architect_task_spec,
    ):
        with pytest.raises(RedactedPlaceholderLeakError):
            builder(prompt="Summarize [REDACTED_ODV_CONFIDENTIAL_SOURCE] columns")

    raw_chunk = _chk(
        "chk-raw-1",
        "Apollo Global signed the loan facility.",
        taint="confidential",
        meta={"sensitive_entities": ["Apollo Global"]},
    )
    with pytest.raises(OpenDVError) as exc_info:
        build_term_scout_task_spec(input_data=raw_chunk)
    assert exc_info.value.error_code == ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN

    p = Pseudonymizer(b"k-pseudo-task-spec-guard-0123456")
    masked_chunk = p.pseudonymize_chunk(raw_chunk, sensitive_terms=["Apollo Global"])
    spec = build_term_scout_task_spec(input_data=masked_chunk)
    assert spec.persona == AgentPersona.TERM_SCOUT


def test_pseudonymizer_hmac_and_de_pseudonymize() -> None:
    p = Pseudonymizer(b"k-pseudo-test-key-0123456789abcdef")
    t1 = p.pseudonymize_token("Legal Entity")
    assert t1.startswith("pseudo_")
    # CRIT-05: 16-hex HMAC token => 7 + 16 = 23 chars
    assert len(t1) == 23
    assert p.pseudonymize_token("Legal Entity") == t1

    orig_text = "The Legal Entity entered into an agreement with another Legal Entity."
    masked = p.pseudonymize_text(orig_text, ["Legal Entity"])
    assert "Legal Entity" not in masked
    assert t1 in masked
    assert p.de_pseudonymize(masked) == orig_text

    # CRIT-05: snake_case SQL/DV/URN compound identifiers are masked & restored
    compound_src = "Columns apollo_deal_id on hub_apollo and urn:ontology:entity:apollo_fund"
    masked_comp = p.pseudonymize_text(compound_src, ["apollo"])
    assert "apollo" not in masked_comp.lower()
    assert p.de_pseudonymize(masked_comp).lower() == compound_src.lower()


def test_pseudonymizer_chunk_handling() -> None:
    p = Pseudonymizer(b"chunk-pseudo-key-secret-bytes-32")
    chunk = _chk(
        "chk-001",
        "Acme Corp holds Treasury Bonds.",
        taint="confidential",
        meta={"title": "Acme Corp Portfolio", "sensitive_entities": ["Acme Corp"]},
        start=0,
        end=31,
    )
    masked = p.pseudonymize_chunk(chunk, sensitive_terms=["Acme Corp", "Treasury Bonds"])
    assert "Acme Corp" not in masked.text
    assert "Treasury Bonds" not in masked.text
    assert masked.metadata is not None
    assert "Acme Corp" not in str(masked.metadata.get("title"))
    assert masked.metadata.get("pseudonymized") is True
    assert masked.chunk_id == chunk.chunk_id
    assert p.de_pseudonymize(masked.text) == chunk.text


def test_redacted_placeholder_leak_scanner() -> None:
    scan_redacted_placeholder_leak("Normal business concept description without leaks.")
    with pytest.raises(RedactedPlaceholderLeakError) as exc_info:
        scan_redacted_placeholder_leak("Mentions [REDACTED_ODV_CONFIDENTIAL_SOURCE] incorrectly.")
    assert exc_info.value.exit_code == 4 and exc_info.value.http_status == 422


def test_unresolved_pseudonym_scanner() -> None:
    scan_unresolved_pseudonyms("Normal legal entity label without raw pseudonyms.")
    for bad in (
        "Name was pseudo_a1b2c3d4e5f6.",
        "Name was pseudo_a1b2c3d4e5f60718.",
        "Hub hub_pseudo_1234567890abcdef",
        "Pseudo 1234567890abcdef entity",
        "pseudo_1234567890abcdef_status",
        "hub_pseudo_1234567890abx",
    ):
        with pytest.raises(AgentUnresolvedPseudonymError) as exc_info:
            scan_unresolved_pseudonyms(bad)
        assert exc_info.value.exit_code == 4 and exc_info.value.http_status == 422


def test_term_scout_candidate_extraction() -> None:
    scout = TermScout()
    chunk = _chk("c1", "A counterparty holds a facility agreement with the bank.")
    tbl = _tbl("t1", ["account_id", "status_code", "balance"], [["ACC-1", "OPEN", "1000"]])
    candidates = scout.extract_candidates(evidence_chunks=[chunk], tables=[tbl])
    terms = {c["term"].lower() for c in candidates}
    for t in ("counterparty", "facility", "agreement", "account", "status"):
        assert t in terms


def test_domain_clarifier_polysemy_detection() -> None:
    clarifier = DomainClarifier()
    candidate_terms = [
        {"term": "Account", "evidence_citations": []},
        {"term": "Position", "evidence_citations": []},
        {"term": "Bond", "evidence_citations": []},
    ]
    questions = clarifier.generate_questions_for_terms(candidate_terms, max_questions=5)
    assert len(questions) == 2
    account_q = next(q for q in questions if q.focus_concept == "Account")
    assert isinstance(account_q, ClarificationQuestion)
    assert account_q.matched_key == "account"
    assert len(account_q.options) >= 2
    option_labels = [opt.label for opt in account_q.options]
    assert "General Ledger Account" in option_labels
    assert "Customer / Client Account" in option_labels
    assert all(opt.rationale and opt.fibo_uri for opt in account_q.options)

    # MAJ-10: multi-token compound phrase polysemy detection + generate_questions alias
    phrase_qs = clarifier.generate_questions(
        [{"term": "Primary Customer Account", "evidence_citations": []}], max_questions=5
    )
    assert len(phrase_qs) == 1
    assert phrase_qs[0].matched_key == "account"


def test_ontology_architect_propose_delta_additive() -> None:
    """CRIT-06: Ungrounded scout concepts are staged in delta.metadata['staged_candidates']
    while valid reference enumerations with non-empty codes stay canonical in delta.operations.
    """
    architect = OntologyArchitect()
    assert isinstance(architect, ConstructionAgent)
    tbl = _tbl("tbl_trades", ["legal_entity_id", "status_code"], [["LE-1", "ACTIVE"]])
    proposal = architect.propose_delta(base_ontology=None, evidence=[tbl])
    assert isinstance(proposal, OntologyProposal)
    assert proposal.delta.author == "agent:ontology-architect"

    op_types = {op.op_type for op in proposal.delta.operations}
    assert "add_reference_enumeration" in op_types
    assert "add_concept" not in op_types

    e_ops = [op for op in proposal.delta.operations if isinstance(op, AddReferenceEnumerationOp)]
    assert e_ops and e_ops[0].enum_uri == "urn:ontology:enum:status"

    staged = (proposal.delta.metadata or {}).get("staged_candidates", [])
    assert len(staged) == 1
    assert staged[0]["provisional_uri"] == "urn:opendv:provisional:entity:legal_entity"
    assert staged[0]["reason_held_back"] == "ONT_EVO_UNGROUNDED_CONCEPT"


def test_scout_harvested_candidates_are_provisional_and_carry_evidence() -> None:
    chunk = _chk("chk-prov-1", "The Legal Entity signed the contract.", taint="public")
    proposal = OntologyArchitect().propose_delta(base_ontology=None, evidence=[chunk])
    assert proposal.delta.operations == []
    assert proposal.delta.base_ontology_version == "0.0.0"
    assert proposal.delta.target_ontology_version == "0.0.0"
    staged = (proposal.delta.metadata or {}).get("staged_candidates", [])
    assert len(staged) >= 1
    le_staged = next(
        s for s in staged if s["provisional_uri"] == "urn:opendv:provisional:entity:legal_entity"
    )
    ev_refs = le_staged["operation"]["evidence_refs"]
    assert ev_refs[0]["reference_uri"] == "urn:opendv:evidence:chunk:chk-prov-1"


def test_scout_harvested_candidates_grounded_via_matcher_commit_canonically() -> None:
    matcher = _StubMatcher(
        {
            "legal entity": (
                "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity",
                0.92,
            )
        }
    )
    chunk = _chk("chk-prov-2", "The Legal Entity signed the contract.", taint="public")
    proposal = OntologyArchitect(matcher=matcher).propose_delta(
        base_ontology=None, evidence=[chunk]
    )
    c_ops = [op for op in proposal.delta.operations if isinstance(op, AddConceptOp)]
    assert [op.concept_uri for op in c_ops] == ["urn:ontology:entity:legal_entity"]
    assert proposal.delta.target_ontology_version == "1.0.0"


def test_ontology_architect_rejects_destructive_ops_and_allows_non_destructive_ops() -> None:
    destructive = RemoveConceptOp(
        op_id="del-1", concept_uri="urn:ontology:entity:old", reason="Del"
    )
    with pytest.raises(AgentDestructiveOpForbiddenError) as exc_info:
        OntologyArchitect().propose_delta(base_ontology=None, operations=[destructive])
    assert exc_info.value.exit_code == 7 and exc_info.value.http_status == 403

    base = GraphAOntology(
        ontology_version="1.0.0",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:party",
                concept_name="Party",
                target_hub="hub_party",
                parent_concept_uri="urn:ontology:entity:actor",
                elm_archetype="CORE_CONCEPT",
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:actor",
                concept_name="Actor",
                target_hub="hub_actor",
                external_mappings=[
                    _fibo_ext("https://spec.edmcouncil.org/fibo/Actor", "skos:exactMatch", 0.90)
                ],
            ),
        ],
    )
    # MAJ-10: non-optional attribute on existing concept is MAJOR/destructive -> forbidden
    with pytest.raises(AgentDestructiveOpForbiddenError):
        OntologyArchitect().propose_delta(
            base_ontology=base,
            operations=[_a("urn:ontology:entity:party", "tax_id", "string", is_optional=False)],
        )
    # MAJ-10: overwriting existing parent_concept_uri is MAJOR/destructive -> forbidden
    with pytest.raises(AgentDestructiveOpForbiddenError):
        OntologyArchitect().propose_delta(
            base_ontology=base,
            operations=[
                SetParentConceptOp(
                    op_id="sp-1",
                    concept_uri="urn:ontology:entity:party",
                    parent_concept_uri=None,
                )
            ],
        )
    # MAJ-10: non-destructive MINOR/PATCH ops on unset slots succeed
    non_destructive_ops = [
        AddSynonymOp(op_id="syn-1", concept_uri="urn:ontology:entity:actor", synonym="Participant"),
        SetParentConceptOp(
            op_id="sp-ok",
            concept_uri="urn:ontology:entity:actor",
            parent_concept_uri="urn:ontology:entity:party",
        ),
        AddKeyAuthorityPatternOp(
            op_id="kap-1",
            concept_uri="urn:ontology:entity:actor",
            authority_name="GLEIF",
            pattern_regex="^LEI-[0-9]+$",
        ),
        AddGovernanceConstraintOp(
            op_id="gov-1",
            concept_uri="urn:ontology:entity:actor",
            constraint_id="gov-pii-1",
            constraint_type="PII",
        ),
        SetConceptElmArchetypeOp(
            op_id="arch-ok",
            concept_uri="urn:ontology:entity:actor",
            elm_archetype="CORE_CONCEPT",
        ),
        RescoreExternalMappingOp(
            op_id="resc-1",
            concept_uri="urn:ontology:entity:actor",
            target_uri="https://spec.edmcouncil.org/fibo/Actor",
            mapping_confidence=0.88,
        ),
    ]
    prop = OntologyArchitect().propose_delta(base_ontology=base, operations=non_destructive_ops)
    assert len(prop.delta.operations) == 6
    assert prop.delta.target_ontology_version == "1.1.0"


def test_ast_import_isolation() -> None:
    base = Path(__file__).resolve().parents[1] / "src/opendv_agent_harness"
    banned = (
        "opendv_service",
        "opendv_sources",
        "opendv_platform_api",
        "opendv_cli",
        "fastapi",
        "starlette",
    )
    py_files = [*base.glob("personas/*.py"), base / "privacy.py"]
    assert len(py_files) >= 5
    for pf in py_files:
        for node in ast.walk(ast.parse(pf.read_text(encoding="utf-8"), filename=str(pf))):
            mod = (
                node.module
                if isinstance(node, ast.ImportFrom)
                else (node.names[0].name if isinstance(node, ast.Import) and node.names else None)
            )
            if mod:
                assert not any(mod.startswith(b) for b in banned), f"AST leak in {pf}: {mod}"


def test_tainted_chunks_with_pseudonymizer() -> None:
    pseudo = Pseudonymizer(k_pseudo=b"k-pseudo-secret-test")
    scout = TermScout(pseudonymizer=pseudo)
    chunk = _chk(
        "chk-conf-1",
        "The Legal Entity with Secret Corp signed the agreement.",
        taint="confidential",
        meta={"sensitive_entities": ["Secret Corp"]},
        start=100,
    )
    candidates = scout.extract_candidates(evidence_chunks=[chunk])
    entity_cands = [c for c in candidates if c["term"] == "Legal Entity"]
    assert len(entity_cands) == 1
    citation = entity_cands[0]["evidence_citations"][0]
    assert citation.char_start == 104 and citation.char_end == 116
    assert "Secret Corp" not in (citation.snippet or "")
    assert "pseudo_" in (citation.snippet or "")


def test_propose_delta_with_graph_a_base_ontology() -> None:
    matcher = _StubMatcher(
        {
            "legal entity": (
                "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity",
                0.90,
            )
        }
    )
    architect = OntologyArchitect(matcher=matcher)
    base = GraphAOntology(
        ontology_version="1.0.0",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:initial_seed",
                concept_name="Initial Seed",
                target_hub="hub_initial_seed",
            )
        ],
    )
    expected_sha = compute_ontology_sha256(base)
    chunk = _chk("chk-1", "The Legal Entity signed the contract.", taint="public")
    proposal = architect.propose_delta(base_ontology=base, evidence=[chunk])
    assert proposal.delta.parent_sha256 == expected_sha
    assert proposal.delta.base_ontology_version == "1.0.0"
    assert proposal.delta.target_ontology_version == "1.1.0"


def test_propose_delta_skips_existing_concepts_and_enums() -> None:
    architect = OntologyArchitect()
    base = GraphAOntology(
        ontology_version="1.0.0",
        canonical_business_concepts=[
            CanonicalBusinessConcept(concept_uri="urn:ontology:entity:legal_entity")
        ],
    )
    chunk = _chk("chk-1", "The Legal Entity entered into an agreement.", taint="public")
    proposal = architect.propose_delta(base_ontology=base, evidence=[chunk])
    concept_uris = [
        op.concept_uri for op in proposal.delta.operations if isinstance(op, AddConceptOp)
    ]
    assert "urn:ontology:entity:legal_entity" not in concept_uris


def test_clarify_context_variants() -> None:
    architect = OntologyArchitect()
    delta = OntologyDelta(
        delta_id="d1",
        base_ontology_version="1.0.0",
        target_ontology_version="1.1.0",
        parent_sha256="0" * 64,
        author="test",
        operations=[_c("account", name="Account", ext=[_fibo_ext()])],
        rationale="test",
    )
    prop = OntologyProposal(proposal_id="p1", recommender_id="test", delta=delta)
    assert any("Account" in q.focus_concept for q in architect.clarify(prop))
    assert any(
        "Account" in q.focus_concept
        for q in architect.clarify([_chk("c1", "Company opened an Account.", taint="public")])
    )
    assert len(architect.clarify(["Account", "Position"])) >= 1


def test_tainted_without_pseudonymizer_or_empty_sensitive_entities_fails_closed() -> None:
    chunk1 = _chk("chk-conf-1", "The Legal Entity entered into an agreement.", taint="confidential")
    assert (
        TermScout(pseudonymizer=None)
        .extract_candidates(evidence_chunks=[chunk1])[0]["evidence_citations"][0]
        .snippet
        is None
    )

    p = Pseudonymizer(k_pseudo=b"k-pseudo-secret-test")
    chunk2 = _chk(
        "chk-conf-2", "The Legal Entity entered into an agreement.", taint="confidential", meta={}
    )
    assert (
        TermScout(pseudonymizer=p)
        .extract_candidates(evidence_chunks=[chunk2])[0]["evidence_citations"][0]
        .snippet
        is None
    )


def test_explicit_additive_child_ops_survive_and_deduplicate() -> None:
    matcher = _StubMatcher(
        {
            "legal entity": (
                "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity",
                0.91,
            )
        }
    )
    attr = _a("urn:ontology:entity:legal_entity", "jurisdiction_code", "string")
    code = _code("urn:ontology:enum:status", "ACTIVE", desc="Active Status")
    tbl = _tbl("tbl_entities", ["legal_entity_id", "status_code"], [["LE-1", "ACTIVE"]])
    proposal = OntologyArchitect(matcher=matcher).propose_delta(
        base_ontology=None, evidence=[tbl], operations=[attr, code]
    )
    ops = proposal.delta.operations
    assert {attr.op_id, code.op_id}.issubset({op.op_id for op in ops})
    concept_idx = next(i for i, op in enumerate(ops) if op.op_type == "add_concept")
    attr_idx = next(i for i, op in enumerate(ops) if op.op_type == "add_attribute")
    assert concept_idx < attr_idx
    enum_idx = next(i for i, op in enumerate(ops) if op.op_type == "add_reference_enumeration")
    code_idx = next(i for i, op in enumerate(ops) if op.op_type == "add_reference_enum_code")
    assert enum_idx < code_idx
    assert (
        ops[concept_idx].concept_uri
        == ops[attr_idx].concept_uri
        == "urn:ontology:entity:legal_entity"
    )
    assert ops[enum_idx].enum_uri == ops[code_idx].enum_uri == "urn:ontology:enum:status"
    assert "ACTIVE" not in ops[enum_idx].allowed_codes


def test_term_scout_never_de_pseudonymizes_and_masks_tainted_table_identifiers() -> None:
    """CRIT-05: TermScout must NOT possess de_pseudonymize() and must keep tokens masked
    until OntologyArchitect locally resolves them.
    """
    p = Pseudonymizer(k_pseudo=b"k-pseudo-secret-test")
    scout = TermScout(pseudonymizer=p)
    assert not hasattr(scout, "de_pseudonymize")

    tbl = _tbl(
        "tbl_apollo_deals",
        ["apollo_entity_id"],
        [["1"]],
        taint="confidential",
        meta={"sensitive_entities": ["apollo"]},
    )
    candidates = scout.extract_candidates(tables=[tbl])
    assert len(candidates) == 1
    assert "apollo" not in candidates[0]["term"].lower()
    assert "pseudo_" in candidates[0]["term"].lower()
    assert "apollo" not in candidates[0]["evidence_citations"][0].reference_uri.lower()

    # OntologyArchitect locally resolves pseudonymized terms on the host boundary
    architect = OntologyArchitect(pseudonymizer=p, term_scout=scout)
    proposal = architect.propose_delta(base_ontology=None, evidence=[tbl])
    staged = (proposal.delta.metadata or {}).get("staged_candidates", [])
    assert any(
        s["provisional_uri"] == "urn:opendv:provisional:entity:apollo_entity" for s in staged
    )


def test_tainted_sensitive_entity_crossing_window_edge_is_masked() -> None:
    p = Pseudonymizer(k_pseudo=b"k-pseudo-secret-test")
    chunk = _chk(
        "chk-conf-edge",
        "The Legal Entity entered into an agreement with Secret Corporation Long Name.",
        taint="confidential",
        meta={"sensitive_entities": ["Secret Corporation Long Name"]},
    )
    cands = TermScout(pseudonymizer=p).extract_candidates(evidence_chunks=[chunk])
    snippet = cands[0]["evidence_citations"][0].snippet or ""
    assert "Secret" not in snippet and "Corporation" not in snippet


def test_sensitive_entity_with_punctuation_and_case_variation_masked() -> None:
    p = Pseudonymizer(k_pseudo=b"k-pseudo-secret-test")
    chunk = _chk(
        "chk-punc-1",
        "The Legal Entity entered into an agreement with Acme Corp. and ACME CORP.",
        taint="confidential",
        meta={"sensitive_entities": ["Acme Corp."]},
    )
    snippet = (
        TermScout(pseudonymizer=p)
        .extract_candidates(evidence_chunks=[chunk])[0]["evidence_citations"][0]
        .snippet
        or ""
    )
    assert "Acme" not in snippet and "ACME" not in snippet


def test_confidential_table_taint_and_cell_masking() -> None:
    # CRIT-05: Tainted table without pseudonymizer or sensitive_entities is skipped completely
    tbl = _tbl("t1", ["trade_status_code"], [["ACTIVE"], ["SETTLED"]], taint="confidential")
    assert TermScout(pseudonymizer=None).extract_candidates(tables=[tbl]) == []

    p = Pseudonymizer(k_pseudo=b"k-pseudo-secret-test")
    scout = TermScout(pseudonymizer=p)
    assert scout.extract_candidates(tables=[tbl]) == []

    tbl_pii = _tbl(
        "t2",
        ["client_status_code"],
        [["ACTIVE"], ["Secret Entity Name"]],
        taint="confidential",
        meta={"sensitive_entities": ["Secret Entity Name"]},
    )
    assert scout.extract_candidates(tables=[tbl_pii])[0]["codes"] == []

    tbl_clean = _tbl(
        "t3",
        ["trade_status_code"],
        [["ACTIVE"], ["PENDING"]],
        taint="confidential",
        meta={"sensitive_entities": ["Secret Counterparty"]},
    )
    assert scout.extract_candidates(tables=[tbl_clean])[0]["codes"] == ["ACTIVE", "PENDING"]


def test_de_pseudonymize_case_insensitive() -> None:
    p = Pseudonymizer(k_pseudo=b"k-pseudo-secret-test")
    tok = p.pseudonymize_token("Acme Corp")
    restored = p.de_pseudonymize(f"Agreement between {tok.capitalize()} and {tok.upper()}.")
    assert tok not in restored and "Acme Corp" in restored


def test_clarify_drops_pseudonymized_snippets_to_fail_closed() -> None:
    p = Pseudonymizer(b"k" * 32)
    chunk = _chk(
        "c1",
        "Account managed by Secret Entity LLC.",
        taint="confidential",
        meta={"sensitive_entities": ["Secret Entity LLC"]},
    )
    questions = OntologyArchitect(term_scout=TermScout(pseudonymizer=p)).clarify([chunk])
    assert questions and all(cit.snippet is None for q in questions for cit in q.evidence_citations)


def test_pseudonymizer_handles_backslash_and_escape_sequences() -> None:
    p = Pseudonymizer(b"k" * 32)
    terms = [r"CORP\Legal", r"DOMAIN\user", r"PATH\to\file", r"Test\n\t\1"]
    text = r"Document from CORP\Legal and DOMAIN\user under PATH\to\file with Test\n\t\1."
    masked = p.pseudonymize_text(text, terms)
    assert not any(t in masked for t in terms)
    assert p.de_pseudonymize(masked) == text


def test_propose_delta_with_base_ontology_reference_enumerations() -> None:
    architect = OntologyArchitect()
    base = GraphAOntology(
        ontology_version="1.0.0",
        canonical_business_concepts=[
            CanonicalBusinessConcept(concept_uri="urn:ontology:entity:initial_seed")
        ],
        reference_enumerations=[
            ReferenceEnumeration(
                enum_uri="urn:ontology:enum:status",
                target_reference_table="ref_status",
                allowed_codes=["ACTIVE", "INACTIVE"],
            )
        ],
    )
    tbl = _tbl("tbl_status", ["status_code"], [["ACTIVE"], ["PENDING"]])
    proposal = architect.propose_delta(base_ontology=base, evidence=[tbl])
    enum_ops = [op for op in proposal.delta.operations if isinstance(op, AddReferenceEnumerationOp)]
    assert not any(op.enum_uri == "urn:ontology:enum:status" for op in enum_ops)


def test_unclassified_table_missing_taint_fails_closed() -> None:
    tbl = _tbl(
        "tbl_unclassified",
        ["account_status_code"],
        [["Confidential Secret"], ["ACTIVE"]],
        taint=None,
    )
    assert TermScout().extract_candidates(tables=[tbl]) == []


def test_table_structure_taint_and_metadata_contracts() -> None:
    tbl_default = TableStructure(table_id="tbl_1")
    assert (
        tbl_default.taint is None
        and tbl_default.taint_classification is None
        and tbl_default.metadata is None
    )
    tbl_explicit = TableStructure(table_id="tbl_2", taint="public", metadata={"source": "edgar"})
    assert tbl_explicit.taint == TaintClassification.PUBLIC
    assert tbl_explicit.metadata == {"source": "edgar"}
    dumped = tbl_default.model_dump(exclude_none=True)
    assert "metadata" not in dumped and "taint" not in dumped


def test_explicit_grounded_concept_not_duplicated_by_scout_candidate() -> None:
    architect = OntologyArchitect()
    explicit_concept = AddConceptOp(
        op_id="op-fibo-le",
        concept_uri="urn:ontology:entity:fibo_legal_entity",
        concept_name="Legal Entity",
        target_hub="hub_legal_entity",
        external_mappings=[_fibo_ext()],
    )
    chunk = _chk(
        "chk-le-1", "A Legal Entity signed the contract with the counterparty.", taint="public"
    )
    proposal = architect.propose_delta(
        base_ontology=None, evidence=[chunk], operations=[explicit_concept]
    )
    concept_ops = [op for op in proposal.delta.operations if isinstance(op, AddConceptOp)]
    assert [op.target_hub for op in concept_ops] == ["hub_legal_entity"]
    assert concept_ops[0].concept_uri == "urn:ontology:entity:fibo_legal_entity"
    staged = (proposal.delta.metadata or {}).get("staged_candidates", [])
    assert not any("legal_entity" in s["provisional_uri"] for s in staged)


def test_propose_delta_with_tainted_evidence_and_pseudonymizer_succeeds() -> None:
    p = Pseudonymizer(b"k-pseudo-key-0123456789abcdef01")
    architect = OntologyArchitect(pseudonymizer=p)
    chunk = _chk(
        "chk-conf-1",
        "Acme Bank issued the Legal Entity a commercial mortgage.",
        taint="confidential",
        meta={"sensitive_entities": ["Acme Bank"]},
    )
    proposal = architect.propose_delta(base_ontology=None, evidence=[chunk])
    assert isinstance(proposal, OntologyProposal)
    assert proposal.delta.operations == []
    staged = (proposal.delta.metadata or {}).get("staged_candidates", [])
    assert len(staged) >= 1
    assert staged[0]["provisional_uri"].startswith("urn:opendv:provisional:entity:")
    ev_refs = staged[0]["operation"].get("evidence_refs", [])
    assert all(ev.get("snippet") is None for ev in ev_refs)


def test_scout_harvested_enumerations_and_concepts_are_provisional() -> None:
    architect = OntologyArchitect()
    tbl = _tbl(
        "tbl_trades", ["legal_entity_id", "status_code"], [["LE-1", "ACTIVE"], ["LE-2", "INACTIVE"]]
    )
    base = GraphAOntology(
        ontology_version="1.0.0",
        canonical_business_concepts=[
            CanonicalBusinessConcept(concept_uri="urn:ontology:entity:customer")
        ],
        reference_enumerations=[
            ReferenceEnumeration(
                enum_uri="urn:ontology:enum:status",
                enumeration_name="status",
                target_reference_table="ref_status",
                allowed_codes=["ACTIVE", "INACTIVE"],
            )
        ],
    )
    proposal = architect.propose_delta(base_ontology=base, evidence=[tbl])
    assert proposal.delta.operations == []
    assert proposal.delta.target_ontology_version == "1.0.0"
    staged = (proposal.delta.metadata or {}).get("staged_candidates", [])
    assert len(staged) == 1
    assert staged[0]["provisional_uri"] == "urn:opendv:provisional:entity:legal_entity"


def test_ontology_architect_task_spec_empty_subagents() -> None:
    assert build_ontology_architect_task_spec(subagents=()).subagents == ()


def test_table_structure_hash_stability() -> None:
    tbl = TableStructure(table_id="tbl_base", headers=["col_a", "col_b"], rows=[["val1", "val2"]])
    dumped = tbl.model_dump(mode="json", exclude_none=True)
    assert "taint" not in dumped and "metadata" not in dumped
    canonical_json = dumps_jcs(dumped)
    assert (
        canonical_json
        == '{"headers":["col_a","col_b"],"rows":[["val1","val2"]],"table_id":"tbl_base"}'
    )
    assert (
        hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
        == "496acdd20a6183fb40125afd16fb0640c17c0710637c6dd9be290c6002abc5f3"
    )


def test_dangling_child_op_without_parent_or_evidence_raises_error() -> None:
    dangling_attr = _a("urn:ontology:entity:ghost_concept", "orphan_column", "VARCHAR(100)")
    with pytest.raises(DanglingParentReferenceError) as exc_info:
        OntologyArchitect().propose_delta(
            base_ontology=None, evidence=[], operations=[dangling_attr]
        )
    assert exc_info.value.exit_code == 4 and "ghost_concept" in str(exc_info.value)


def test_no_agent_created_op_without_grounding_enters_delta_operations() -> None:
    """CRIT-06 & P09::MAJ-01: Ungrounded concepts are staged as urn:opendv:provisional:*
    while committable reference enumerations carry evidence and canonical URIs.
    """
    architect = OntologyArchitect()
    tbl = _tbl(
        "tbl_customers", ["customer_id", "tier_code"], [["CUST-1", "PLATINUM"], ["CUST-2", "GOLD"]]
    )
    proposal = architect.propose_delta(base_ontology=None, evidence=[tbl])
    for op in proposal.delta.operations:
        assert not isinstance(op, AddConceptOp)
        if isinstance(op, AddReferenceEnumerationOp):
            assert op.enum_uri == "urn:ontology:enum:tier"
            assert len(op.evidence_refs) > 0
    staged = (proposal.delta.metadata or {}).get("staged_candidates", [])
    assert [s["provisional_uri"] for s in staged] == ["urn:opendv:provisional:entity:customer"]
    assert len(staged[0]["operation"]["evidence_refs"]) > 0


def test_two_name_only_enumerations_and_codes_do_not_collide() -> None:
    ops_in = [
        _enum(name="country", codes=["CA"]),
        _enum(name="currency", codes=["CAD"]),
        _code("", "US", ename="country", desc="United States"),
        _code("", "USD", ename="currency", desc="US Dollar"),
    ]
    proposal = OntologyArchitect().propose_delta(base_ontology=None, evidence=[], operations=ops_in)
    ref_ops = [op for op in proposal.delta.operations if isinstance(op, AddReferenceEnumerationOp)]
    code_ops = [op for op in proposal.delta.operations if isinstance(op, AddReferenceEnumCodeOp)]
    assert len(ref_ops) == 2 and len(code_ops) == 2
    assert {op.name for op in ref_ops} == {"country", "currency"}
    assert {op.enum_uri for op in code_ops} == {
        "urn:ontology:enum:country",
        "urn:ontology:enum:currency",
    }


def test_scout_discovered_enum_resolves_caller_code_op_by_bare_name() -> None:
    """P09::MAJ-02: Caller AddReferenceEnumCodeOp(enumeration_name='currency') resolves
    against scout-created enum from currency_code table header without DanglingParentReferenceError.
    """
    tbl = _tbl("tbl_fx", ["currency_code"], [["USD"], ["CAD"]], taint="public")
    code_op = _code("", "EUR", ename="currency", desc="Euro")
    proposal = OntologyArchitect().propose_delta(
        base_ontology=None, evidence=[tbl], operations=[code_op]
    )
    ref_ops = [op for op in proposal.delta.operations if isinstance(op, AddReferenceEnumerationOp)]
    code_ops = [op for op in proposal.delta.operations if isinstance(op, AddReferenceEnumCodeOp)]
    assert len(ref_ops) == 1 and ref_ops[0].enum_uri == "urn:ontology:enum:currency"
    assert len(code_ops) == 1 and code_ops[0].enum_uri == "urn:ontology:enum:currency"


def test_code_op_referencing_base_ontology_enum_by_name_or_missing() -> None:
    base = GraphAOntology(
        canonical_business_concepts=[
            CanonicalBusinessConcept(concept_uri="urn:ontology:entity:customer")
        ],
        reference_enumerations=[
            ReferenceEnumeration(
                enum_uri="urn:ontology:enum:status",
                target_reference_table="ref_status",
                allowed_codes=["ACTIVE"],
            )
        ],
    )
    code_op = _code("", "PENDING", ename="ref_status", desc="Pending review")
    proposal = OntologyArchitect().propose_delta(
        base_ontology=base, evidence=[], operations=[code_op]
    )
    code_ops = [op for op in proposal.delta.operations if isinstance(op, AddReferenceEnumCodeOp)]
    assert len(code_ops) == 1 and code_ops[0].enum_uri == "urn:ontology:enum:status"

    code_orphan = _code("", "VAL_1", ename="nonexistent_table")
    with pytest.raises(DanglingParentReferenceError) as exc_info:
        OntologyArchitect().propose_delta(base_ontology=None, evidence=[], operations=[code_orphan])
    assert exc_info.value.exit_code == 4 and "nonexistent_table" in str(exc_info.value)


def test_relationship_reconciliation_and_dangling_error() -> None:
    architect = OntologyArchitect()
    tbl = _tbl("tbl_trades", ["legal_entity_id", "counterparty_id"], [["LE-1", "CP-1"]])
    rel_op = _r(
        "entity_counterparty",
        "urn:ontology:entity:legal_entity",
        "urn:ontology:entity:counterparty",
    )
    proposal = architect.propose_delta(base_ontology=None, evidence=[tbl], operations=[rel_op])
    assert proposal.delta.operations == []
    staged = (proposal.delta.metadata or {}).get("staged_candidates", [])
    rel_staged = [s for s in staged if s["op_type"] == "add_relationship"]
    assert len(rel_staged) == 1
    assert (
        rel_staged[0]["operation"]["source_concept_uri"]
        == "urn:opendv:provisional:entity:legal_entity"
    )
    assert (
        rel_staged[0]["operation"]["target_concept_uri"]
        == "urn:opendv:provisional:entity:counterparty"
    )

    bad_rel = _r(
        "entity_ghost",
        "urn:ontology:entity:legal_entity",
        "urn:ontology:entity:ghost_concept",
    )
    with pytest.raises(DanglingParentReferenceError) as exc_info:
        architect.propose_delta(base_ontology=None, evidence=[tbl], operations=[bad_rel])
    assert exc_info.value.exit_code == 4
    assert "ghost_concept" in str(exc_info.value)


def test_ontology_delta_json_roundtrip_validation() -> None:
    architect = OntologyArchitect()
    tbl = _tbl(
        "tbl_accounts", ["account_id", "status_code"], [["ACC-1", "OPEN"], ["ACC-2", "CLOSED"]]
    )
    proposal = architect.propose_delta(base_ontology=None, evidence=[tbl])
    raw_json = proposal.delta.model_dump(mode="json")
    rehydrated = OntologyDelta.model_validate(raw_json)
    assert rehydrated.delta_id == proposal.delta.delta_id
    assert len(rehydrated.operations) == len(proposal.delta.operations)


def test_external_mappings_with_same_standard_do_not_collide() -> None:
    c_op = _c("trade")
    m1 = _m(
        "urn:ontology:entity:trade",
        "https://spec.edmcouncil.org/fibo/Trade",
        "skos:exactMatch",
        0.91,
    )
    m2 = _m(
        "urn:ontology:entity:trade",
        "https://spec.edmcouncil.org/fibo/Transaction",
        "skos:broadMatch",
        0.75,
    )
    proposal = OntologyArchitect().propose_delta(
        base_ontology=None, evidence=[], operations=[c_op, m1, m2]
    )
    mapping_ops = [op for op in proposal.delta.operations if isinstance(op, AddExternalMappingOp)]
    assert {op.target_uri for op in mapping_ops} == {m1.target_uri, m2.target_uri}


def test_relationships_with_default_uri_do_not_collide() -> None:
    c1 = _c("party", ext=[_fibo_ext()])
    c2 = _c("account", ext=[_fibo_ext()])
    c3 = _c("contract", ext=[_fibo_ext()])
    r1 = _r("party_account", c1.concept_uri, c2.concept_uri)
    r2 = _r("party_contract", c1.concept_uri, c3.concept_uri)
    proposal = OntologyArchitect().propose_delta(
        base_ontology=None, evidence=[], operations=[c1, c2, c3, r1, r2]
    )
    rel_ops = [op for op in proposal.delta.operations if isinstance(op, AddRelationshipOp)]
    assert {op.target_concept_uri for op in rel_ops} == {
        "urn:ontology:entity:account",
        "urn:ontology:entity:contract",
    }


def test_explicit_concept_name_collision_with_base_remaps_child_ops() -> None:
    base = GraphAOntology(
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:customer",
                external_mappings=[_fibo_ext()],
            )
        ]
    )
    prov_customer = _c("urn:opendv:provisional:entity:customer")
    attr_op = _a(prov_customer.concept_uri, "email", "string")
    proposal = OntologyArchitect().propose_delta(
        base_ontology=base, evidence=[], operations=[prov_customer, attr_op]
    )
    assert not any(isinstance(op, AddConceptOp) for op in proposal.delta.operations)
    attr_ops = [op for op in proposal.delta.operations if isinstance(op, AddAttributeOp)]
    assert len(attr_ops) == 1 and attr_ops[0].concept_uri == "urn:ontology:entity:customer"


def test_explicit_ungrounded_canonical_ops_staged_with_opendv_provisional_namespace() -> None:
    canonical_concept = _c("urn:ontology:entity:portfolio", ev=[])
    child_attr = _a("urn:ontology:entity:portfolio", "total_value", "decimal(18,2)")
    proposal = OntologyArchitect().propose_delta(
        base_ontology=None, evidence=[], operations=[canonical_concept, child_attr]
    )
    assert (
        proposal.delta.author == "agent:ontology-architect+caller"
        and proposal.confidence_score < 1.0
    )
    assert proposal.delta.operations == []
    staged = (proposal.delta.metadata or {}).get("staged_candidates", [])
    assert len(staged) == 2
    assert staged[0]["provisional_uri"] == "urn:opendv:provisional:entity:portfolio"
    assert staged[1]["provisional_uri"] == "urn:opendv:provisional:entity:portfolio"
    assert staged[1]["reason_held_back"] == "DEPENDENCY_CLOSURE_UNMET"


def test_explicit_homonyms_with_distinct_uris_preserved() -> None:
    base = GraphAOntology(
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:gl_account",
                concept_name="Account",
            )
        ]
    )
    cust_acc = _c("customer_account", name="Account", ext=[_fibo_ext()])
    vend_acc = _c("vendor_account", name="Account", ext=[_fibo_ext()])
    proposal = OntologyArchitect().propose_delta(
        base_ontology=base, evidence=[], operations=[cust_acc, vend_acc]
    )
    uris = {op.concept_uri for op in proposal.delta.operations if isinstance(op, AddConceptOp)}
    assert "urn:ontology:entity:customer_account" in uris
    assert "urn:ontology:entity:vendor_account" in uris


def test_relationship_between_grounded_base_concepts_keeps_canonical_uri() -> None:
    base = GraphAOntology(
        canonical_business_concepts=[
            CanonicalBusinessConcept(concept_uri="urn:ontology:entity:contract"),
            CanonicalBusinessConcept(concept_uri="urn:ontology:entity:counterparty"),
        ]
    )
    rel_op = _r(
        "urn:ontology:relationship:contract_counterparty",
        "urn:ontology:entity:contract",
        "urn:ontology:entity:counterparty",
        link="lnk_contract_counterparty",
        ev=[],
    )
    proposal = OntologyArchitect().propose_delta(
        base_ontology=base, evidence=[], operations=[rel_op]
    )
    rel_ops = [op for op in proposal.delta.operations if isinstance(op, AddRelationshipOp)]
    assert len(rel_ops) == 1
    assert rel_ops[0].relationship_uri == "urn:ontology:relationship:contract_counterparty"


def test_snippet_only_or_broadmatch_does_not_satisfy_path_a_grounding() -> None:
    """P09::MAJ-01: EvidenceRef snippets and skos:broadMatch mappings do NOT satisfy ONT-EVO-002."""
    ev = [
        EvidenceRef(
            evidence_type="document_span",
            reference_uri="urn:opendv:evidence:chunk:c1",
            snippet="Signed bond.",
        )
    ]
    c_snippet_only = _c("urn:ontology:entity:bond", ev=ev)
    broad_map = _m(
        "urn:ontology:entity:bond",
        "https://spec.edmcouncil.org/fibo/ontology/SEC/Debt/Bonds/Bond",
        pred="skos:broadMatch",
        conf=0.95,
    )
    proposal = OntologyArchitect().propose_delta(
        base_ontology=None, evidence=[], operations=[c_snippet_only, broad_map]
    )
    assert proposal.delta.operations == []
    staged = (proposal.delta.metadata or {}).get("staged_candidates", [])
    assert any(s["provisional_uri"] == "urn:opendv:provisional:entity:bond" for s in staged)


def test_explicit_grounded_or_waived_canonical_ops_keep_canonical_uri() -> None:
    base = GraphAOntology(
        canonical_business_concepts=[
            CanonicalBusinessConcept(concept_uri="urn:ontology:entity:legal_entity")
        ]
    )
    c_grounded = _c(
        "urn:ontology:entity:bond",
        ext=[
            _fibo_ext(
                "https://spec.edmcouncil.org/fibo/ontology/SEC/Debt/Bonds/Bond",
                "skos:exactMatch",
                0.88,
            )
        ],
    )
    c_waived = _c("urn:ontology:entity:bespoke_desk")
    import hmac

    waiver_secret = b"r3-studio-architect-waiver-secret"
    unsigned_waiver = StewardWaiverRecord(
        proposal_id=uuid4(),
        proposal_delta_sha256="a" * 64,
        base_sha256=compute_ontology_sha256(base),
        target_concept_uris=["urn:ontology:entity:bespoke_desk"],
        rationale="Approved firm-internal desk concept waiver",
        authorized_by="chief-architect",
        authorized_role="admin",
        signature_or_token="placeholder",
    )
    waiver_sig = hmac.new(
        waiver_secret,
        unsigned_waiver.signing_payload().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    waiver = unsigned_waiver.model_copy(update={"signature_or_token": waiver_sig})
    rel = _r(
        "urn:ontology:relationship:issues_bond",
        "urn:ontology:entity:legal_entity",
        "urn:ontology:entity:bond",
    )
    proposal = OntologyArchitect().propose_delta(
        base_ontology=base,
        evidence=[],
        operations=[c_grounded, c_waived, rel],
        waivers=[waiver],
        waiver_secret=waiver_secret,
    )
    c_ops = [op for op in proposal.delta.operations if isinstance(op, AddConceptOp)]
    r_ops = [op for op in proposal.delta.operations if isinstance(op, AddRelationshipOp)]
    assert {op.concept_uri for op in c_ops} == {
        "urn:ontology:entity:bond",
        "urn:ontology:entity:bespoke_desk",
    }
    assert len(r_ops) == 1 and r_ops[0].relationship_uri == "urn:ontology:relationship:issues_bond"


def test_child_referencing_nonexistent_concept_raises_dangling_error() -> None:
    base = GraphAOntology(
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:gl_account",
                concept_name="Account",
            )
        ]
    )
    attr = _a("urn:ontology:entity:account", "num")
    with pytest.raises(DanglingParentReferenceError):
        OntologyArchitect().propose_delta(base_ontology=base, evidence=[], operations=[attr])

    cust = _c("customer_account", name="Account", ext=[_fibo_ext()])
    vend = _c("vendor_account", name="Account", ext=[_fibo_ext()])
    with pytest.raises(DanglingParentReferenceError):
        OntologyArchitect().propose_delta(
            base_ontology=None, evidence=[], operations=[cust, vend, attr]
        )


def test_enum_preserves_caller_identifier_and_empty_enum_stages_cleanly() -> None:
    op = _enum("urn:ontology:enum:iso_country", name="country", codes=["US", "CA"])
    empty_op = _enum("urn:ontology:enum:empty_codes", name="empty_codes", codes=[])
    proposal = OntologyArchitect().propose_delta(
        base_ontology=None, evidence=[], operations=[op, empty_op]
    )
    enum_ops = [op for op in proposal.delta.operations if isinstance(op, AddReferenceEnumerationOp)]
    assert len(enum_ops) == 1 and enum_ops[0].enum_uri == "urn:ontology:enum:iso_country"
    staged = (proposal.delta.metadata or {}).get("staged_candidates", [])
    assert any(
        s["provisional_uri"] == "urn:opendv:provisional:enum:empty_codes"
        and s["reason_held_back"] == "EMPTY_ENUM_CODES"
        for s in staged
    )


def test_rewritten_ops_pass_pydantic_validation_roundtrip() -> None:
    c = _c("urn:opendv:provisional:entity:asset", ext=[_fibo_ext()])
    rel = _r(
        "urn:opendv:provisional:relationship:owns_asset",
        "urn:opendv:provisional:entity:asset",
        "urn:opendv:provisional:entity:asset",
    )
    code = _code("urn:opendv:provisional:enum:status", "ACTIVE")
    enum_op = _enum("urn:opendv:provisional:enum:status", name="status", codes=["PENDING"])
    proposal = OntologyArchitect().propose_delta(
        base_ontology=None, evidence=[], operations=[c, rel, enum_op, code]
    )
    assert all(":provisional:" not in op.model_dump_json() for op in proposal.delta.operations)
    restored = OntologyDelta.model_validate(proposal.delta.model_dump(mode="json"))
    assert len(restored.operations) == 4


def test_r3_pseudonymize_chunk_strips_cleartext_sensitive_entities() -> None:
    """P09 MAJ-03: pseudonymize_chunk must remove cleartext sensitive_entities and store only
    redacted_entity_count without leaking any cleartext terms in serialized metadata.
    """
    import json

    p = Pseudonymizer(b"r3-pseudo-key-0123456789abcdef01")
    chunk = _chk(
        "chk-r3-1",
        "Secret Alpha LLC acquired Secret Beta Corp.",
        taint="confidential",
        meta={
            "title": "Deal memo for Secret Alpha LLC",
            "sensitive_entities": ["Secret Alpha LLC", "Secret Beta Corp"],
            "nested": {"sensitive_entities": ["Secret Alpha LLC"], "note": "Secret Beta Corp"},
        },
    )
    masked = p.pseudonymize_chunk(chunk)
    assert masked.metadata is not None
    assert "sensitive_entities" not in masked.metadata
    assert "sensitive_entities" not in masked.metadata["nested"]
    assert masked.metadata["redacted_entity_count"] == 2
    assert masked.metadata["pseudonymized"] is True
    serialized = json.dumps(masked.model_dump(mode="json")).lower()
    assert "secret alpha llc" not in serialized
    assert "secret beta corp" not in serialized


def test_r3_validate_task_spec_privacy_recursive_do_not_send_and_unconditional_taint() -> None:
    """S2-CRIT-02: validate_task_spec_privacy recursively walks Mapping, Sequence/Set, and
    BaseModel inputs; rejects do_not_send_to_agent=True; and unconditionally requires
    pseudonymized=True on all non-public chunks and tables (even when sensitive_entities is empty).
    """
    from pydantic import BaseModel

    from opendv_contracts.dtos.ingestion import IngestionResult

    class _NestedEnvelope(BaseModel):
        payload: dict[str, Any]

    # 1. Tainted chunk with NO sensitive_entities and pseudonymized missing -> rejected!
    tainted_empty_sens = _chk("c-unp", "Internal facility ledger.", taint="internal", meta={})
    with pytest.raises(OpenDVError) as e1:
        build_term_scout_task_spec(input_data={"nested": [tainted_empty_sens]})
    assert e1.value.error_code == ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN

    # 2. Tainted table inside BaseModel without sensitive_entities or pseudonymized -> rejected!
    tainted_tbl = _tbl("t-unp", ["account_id"], [["A1"]], taint="confidential", meta={})
    with pytest.raises(OpenDVError) as e2:
        build_domain_clarifier_task_spec(
            input_data=_NestedEnvelope(payload={"tables": [tainted_tbl]})
        )
    assert e2.value.error_code == ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN

    # 3. Public chunk/table marked do_not_send_to_agent=True inside nested structure -> rejected!
    dns_chunk = _chk(
        "c-dns",
        "Public text but restricted source.",
        taint="public",
        meta={"do_not_send_to_agent": True},
    )
    with pytest.raises(OpenDVError) as e3:
        build_ontology_architect_task_spec(input_data={"items": (dns_chunk,)})
    assert e3.value.error_code == ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN

    dns_tbl = _tbl(
        "t-dns",
        ["col"],
        [["val"]],
        taint="public",
        meta={"do_not_send_to_agent": True},
    )
    with pytest.raises(OpenDVError) as e4:
        build_term_scout_task_spec(input_data={"tables": [dns_tbl]})
    assert e4.value.error_code == ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN

    # 4. IngestionResult BaseModel walk catches nested unpseudonymized chunk & placeholder leaks
    ing_bad = IngestionResult(
        document_id="doc-1",
        filename="secret.pdf",
        mime_type="application/pdf",
        taint="public",
        chunks=[tainted_empty_sens],
    )
    with pytest.raises(OpenDVError) as e5:
        build_term_scout_task_spec(input_data=ing_bad)
    assert e5.value.error_code == ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN

    with pytest.raises(RedactedPlaceholderLeakError):
        build_term_scout_task_spec(
            input_data={"set_items": frozenset({"leak [REDACTED_ODV_PII] here"})}
        )


def test_r3_persona_task_specs_clamp_unauthorized_explicit_tools() -> None:
    """S2-MAJ-01: Explicit tools= passed to Studio persona TaskSpec builders must be authorized
    in both profile.authorized_tools and tool.allowed_personas.
    """
    from opendv_agent_harness.tools.catalog import (
        build_default_domain_tool_map,
        get_tools_for_persona,
    )

    tool_map = build_default_domain_tool_map()
    forbidden_codemod = tool_map["apply_ast_codemod"]  # ship-vault only

    for builder, persona in (
        (build_term_scout_task_spec, AgentPersona.TERM_SCOUT),
        (build_domain_clarifier_task_spec, AgentPersona.DOMAIN_CLARIFIER),
        (build_ontology_architect_task_spec, AgentPersona.ONTOLOGY_ARCHITECT),
    ):
        with pytest.raises(PermissionError, match="not authorized"):
            builder(tools=[forbidden_codemod])
        allowed = get_tools_for_persona(persona)
        spec = builder(tools=allowed[:1])
        assert len(spec.tools) == len(allowed[:1])


def test_r3_ontology_architect_enforces_hmac_waiver_and_existing_concept_grounding() -> None:
    """P09 MAJ-01 & MAJ-02:
    - Rejects waivers when waiver_secret is omitted, forged, expired, wrong base_sha256,
      or scoped to a different namespace URI (no slug widening);
    - Segregates mutating child ops targeting ungrounded existing base concepts unless paired
      with verified FIBO AddExternalMappingOp or verified K_waiver;
    - Enforces scheme == 'FIBO' and optional GroundingVerifier on candidate mappings.
    """
    import hmac
    from datetime import UTC, datetime, timedelta

    from opendv_contracts.spi.grounding import GroundingVerificationResult

    class _StrictStubVerifier:
        def __init__(self, allowed_uris: set[str]) -> None:
            self._allowed = allowed_uris

        def verify_grounding(
            self,
            concept_uri: str,
            candidates: list[Any],
            concept_name: str = "",
            synonyms: Any = (),
            **kwargs: Any,
        ) -> GroundingVerificationResult:
            for c in candidates:
                t_uri = getattr(c, "target_uri", "")
                conf = float(getattr(c, "confidence", 0.0) or 0.0)
                if t_uri in self._allowed and conf >= 0.65:
                    return GroundingVerificationResult(
                        is_grounded=True,
                        score=conf,
                        matched_uri=t_uri,
                    )
            return GroundingVerificationResult(is_grounded=False, score=0.0)

    base = GraphAOntology(
        ontology_version="1.0.0",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:legacy_ungrounded",
                concept_name="Legacy Ungrounded",
                target_hub="hub_legacy_ungrounded",
            )
        ],
    )
    secret = b"r3-architect-k-waiver-secret-key"
    base_sha = compute_ontology_sha256(base)

    def _sign(w: StewardWaiverRecord, key: bytes = secret) -> StewardWaiverRecord:
        sig = hmac.new(key, w.signing_payload().encode("utf-8"), hashlib.sha256).hexdigest()
        return w.model_copy(update={"signature_or_token": sig})

    c_desk = _c("urn:ontology:entity:bespoke_desk")

    # 1. Validly signed waiver WITHOUT waiver_secret is NOT trusted -> staged
    valid_w = _sign(
        StewardWaiverRecord(
            proposal_id=uuid4(),
            proposal_delta_sha256="b" * 64,
            base_sha256=base_sha,
            target_concept_uris=["urn:ontology:entity:bespoke_desk"],
            rationale="Approved internal trading desk concept",
            authorized_by="admin-1",
            authorized_role="admin",
            signature_or_token="tmp",
        )
    )
    p_no_secret = OntologyArchitect().propose_delta(
        base_ontology=base, operations=[c_desk], waivers=[valid_w]
    )
    assert p_no_secret.delta.operations == []

    # 2. Expired waiver, wrong base_sha256, and cross-namespace slug widening -> all staged!
    expired_w = _sign(
        valid_w.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(minutes=5)})
    )
    wrong_base_w = _sign(valid_w.model_copy(update={"base_sha256": "f" * 64}))
    foreign_ns_w = _sign(
        valid_w.model_copy(update={"target_concept_uris": ["urn:x:entity:bespoke_desk"]})
    )
    for bad_w in (expired_w, wrong_base_w, foreign_ns_w):
        prop_bad = OntologyArchitect(waiver_secret=secret).propose_delta(
            base_ontology=base, operations=[c_desk], waivers=[bad_w]
        )
        assert prop_bad.delta.operations == []

    # 3. P09 MAJ-02: Mutating ungrounded existing base concept -> segregated into staged_candidates!
    mut_attr = _a("urn:ontology:entity:legacy_ungrounded", "risk_rating", "string")
    mut_syn = AddSynonymOp(
        op_id="syn-ungrounded",
        concept_uri="urn:ontology:entity:legacy_ungrounded",
        synonym="Old Counterparty",
    )
    p_ungrounded_mut = OntologyArchitect().propose_delta(
        base_ontology=base,
        operations=[mut_attr, mut_syn],
    )
    assert p_ungrounded_mut.delta.operations == []
    staged_mut = (p_ungrounded_mut.delta.metadata or {}).get("staged_candidates", [])
    assert len(staged_mut) == 2
    assert all(s["reason_held_back"] == "ONT_EVO_UNGROUNDED_CONCEPT" for s in staged_mut)

    # 4. Pairing verified FIBO AddExternalMappingOp grounds the existing concept mutation;
    #    fabricated FIBO URI or non-FIBO scheme is rejected by GroundingVerifier!
    real_fibo = (
        "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity"
    )
    verifier = _StrictStubVerifier({real_fibo})

    fake_map = _m(
        "urn:ontology:entity:legacy_ungrounded",
        "https://spec.edmcouncil.org/fibo/ontology/FAKE/BogusEntity",
        "skos:exactMatch",
        0.95,
    )
    p_fake_verifier = OntologyArchitect(grounding_verifier=verifier).propose_delta(
        base_ontology=base,
        operations=[mut_attr, fake_map],
    )
    assert p_fake_verifier.delta.operations == []

    spoof_scheme_concept = _c(
        "urn:ontology:entity:spoof_scheme",
        ext=[
            ExternalMapping(
                scheme="CUSTOM",
                target_uri=real_fibo,
                predicate="skos:exactMatch",
                confidence=0.95,
            )
        ],
    )
    p_spoof_scheme = OntologyArchitect().propose_delta(
        base_ontology=base,
        operations=[spoof_scheme_concept],
    )
    assert p_spoof_scheme.delta.operations == []

    real_map = _m(
        "urn:ontology:entity:legacy_ungrounded",
        real_fibo,
        "skos:exactMatch",
        0.91,
    )
    p_paired_ok = OntologyArchitect(grounding_verifier=verifier).propose_delta(
        base_ontology=base,
        operations=[mut_attr, real_map],
    )
    assert len(p_paired_ok.delta.operations) == 2
