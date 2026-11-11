"""Tests for 3 Ontology Studio personas & privacy."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Any

import pytest

from opendv_agent_harness.models import AgentPersona, ModelTier
from opendv_agent_harness.personas import (
    DomainClarifier,
    OntologyArchitect,
    TermScout,
    build_domain_clarifier_subagent_spec,
    build_ontology_architect_subagent_spec,
    build_ontology_architect_task_spec,
    build_persona_subagent_spec,
    build_persona_task_spec,
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
    AddReferenceEnumCodeOp,
    AddReferenceEnumerationOp,
    AddRelationshipOp,
    EvidenceRef,
    OntologyDelta,
    OntologyProposal,
    RemoveConceptOp,
)
from opendv_contracts.jcs import dumps_jcs
from opendv_contracts.spi.agent import ConstructionAgent
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    GraphAOntology,
    ReferenceEnumeration,
)
from opendv_core.ontology_hashing import compute_ontology_sha256


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


def _c(uid: str, name: str = "", hub: str = "", ev: list[Any] | None = None) -> AddConceptOp:
    slug = uid.rsplit(":", 1)[-1] if ":" in uid else uid
    curi = uid if uid.startswith("urn:ontology:") else f"urn:ontology:provisional:entity:{slug}"
    return AddConceptOp(
        op_id=f"c-{slug}",
        concept_uri=curi,
        concept_name=name or slug.replace("_", " ").title(),
        target_hub=hub or f"hub_{slug}",
        evidence_refs=ev or [],
    )


def _r(
    uid: str, src: str, tgt: str, link: str = "", ev: list[Any] | None = None
) -> AddRelationshipOp:
    slug = uid.rsplit(":", 1)[-1] if ":" in uid else uid
    ruri = (
        uid if uid.startswith("urn:ontology:") else f"urn:ontology:provisional:relationship:{slug}"
    )
    return AddRelationshipOp(
        op_id=f"r-{slug}",
        relationship_uri=ruri,
        target_link=link or f"lnk_{slug}",
        source_concept_uri=src,
        target_concept_uri=tgt,
        evidence_refs=ev or [],
    )


def _a(curi: str, name: str, dt: str = "varchar") -> AddAttributeOp:
    return AddAttributeOp(
        op_id=f"a-{name}",
        concept_uri=curi,
        attribute_name=name,
        data_type=dt,
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
        allowed_codes=codes or ["ACTIVE"],
    )


def _m(curi: str, uri: str, pred: str = "exactMatch") -> AddExternalMappingOp:
    return AddExternalMappingOp(
        op_id=f"m-{hashlib.sha256(uri.encode()).hexdigest()[:6]}",
        concept_uri=curi,
        scheme="FIBO",
        target_uri=uri,
        predicate=pred,
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


def test_pseudonymizer_hmac_and_de_pseudonymize() -> None:
    p = Pseudonymizer(b"k-pseudo-test-key-0123456789abcdef")
    t1 = p.pseudonymize_token("Legal Entity")
    assert t1.startswith("pseudo_")
    assert len(t1) == 19
    assert p.pseudonymize_token("Legal Entity") == t1

    orig_text = "The Legal Entity entered into an agreement with another Legal Entity."
    masked = p.pseudonymize_text(orig_text, ["Legal Entity"])
    assert "Legal Entity" not in masked
    assert t1 in masked
    assert p.de_pseudonymize(masked) == orig_text


def test_pseudonymizer_chunk_handling() -> None:
    p = Pseudonymizer(b"chunk-pseudo-key-secret-bytes-32")
    chunk = _chk(
        "chk-001", "Acme Corp holds Treasury Bonds.", taint="confidential", start=0, end=31
    )
    masked = p.pseudonymize_chunk(chunk, sensitive_terms=["Acme Corp", "Treasury Bonds"])
    assert "Acme Corp" not in masked.text
    assert "Treasury Bonds" not in masked.text
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
        "Hub hub_pseudo_1234567890ab",
        "Pseudo 1234567890ab entity",
        "pseudo_1234567890ab_status",
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
    assert len(account_q.options) >= 2
    option_labels = [opt.label for opt in account_q.options]
    assert "General Ledger Account" in option_labels
    assert "Customer / Client Account" in option_labels
    assert all(opt.rationale and opt.fibo_uri for opt in account_q.options)


def test_ontology_architect_propose_delta_additive() -> None:
    architect = OntologyArchitect()
    assert isinstance(architect, ConstructionAgent)
    tbl = _tbl("tbl_trades", ["legal_entity_id", "status_code"], [["LE-1", "ACTIVE"]])
    proposal = architect.propose_delta(base_ontology=None, evidence=[tbl])
    assert isinstance(proposal, OntologyProposal)
    assert proposal.delta.author == "agent:ontology-architect"

    op_types = {op.op_type for op in proposal.delta.operations}
    assert "add_concept" in op_types and "add_reference_enumeration" in op_types

    c_ops = [op for op in proposal.delta.operations if isinstance(op, AddConceptOp)]
    e_ops = [op for op in proposal.delta.operations if isinstance(op, AddReferenceEnumerationOp)]
    assert c_ops and c_ops[0].concept_uri.startswith("urn:ontology:provisional:entity:")
    assert e_ops and e_ops[0].enum_uri.startswith("urn:ontology:provisional:enum:")


def test_scout_harvested_candidates_are_provisional_and_carry_evidence() -> None:
    chunk = _chk("chk-prov-1", "The Legal Entity signed the contract.", taint="public")
    proposal = OntologyArchitect().propose_delta(base_ontology=None, evidence=[chunk])
    c_ops = [op for op in proposal.delta.operations if isinstance(op, AddConceptOp)]
    assert c_ops and c_ops[0].concept_uri.startswith("urn:ontology:provisional:entity:")
    assert c_ops[0].evidence_refs[0].reference_uri == "urn:opendv:evidence:chunk:chk-prov-1"


def test_ontology_architect_rejects_destructive_ops() -> None:
    destructive = RemoveConceptOp(
        op_id="del-1", concept_uri="urn:ontology:entity:old", reason="Del"
    )
    with pytest.raises(AgentDestructiveOpForbiddenError) as exc_info:
        OntologyArchitect().propose_delta(base_ontology=None, operations=[destructive])
    assert exc_info.value.exit_code == 7 and exc_info.value.http_status == 403


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
    architect = OntologyArchitect()
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
        operations=[_c("account", name="Account")],
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
    attr = _a("urn:ontology:entity:legal_entity", "jurisdiction_code", "string")
    code = _code("urn:ontology:enum:status", "ACTIVE", desc="Active Status")
    tbl = _tbl("tbl_entities", ["legal_entity_id", "status_code"], [["LE-1", "ACTIVE"]])
    proposal = OntologyArchitect().propose_delta(
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
        == "urn:ontology:provisional:entity:legal_entity"
    )
    assert (
        ops[enum_idx].enum_uri == ops[code_idx].enum_uri == "urn:ontology:provisional:enum:status"
    )
    assert "ACTIVE" not in ops[enum_idx].allowed_codes


def test_pseudonymized_table_headers_de_pseudonymize() -> None:
    p = Pseudonymizer(k_pseudo=b"k-pseudo-secret-test")
    masked_token = p.pseudonymize_token("legal_entity")
    tbl = _tbl("tbl_masked", [f"{masked_token}_id"], [["1"]])
    candidates = TermScout(pseudonymizer=p).extract_candidates(tables=[tbl])
    assert "Legal Entity" in [c["term"] for c in candidates if c["suggested_type"] == "entity"]


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
    tbl = _tbl("t1", ["trade_status_code"], [["ACTIVE"], ["SETTLED"]], taint="confidential")
    assert TermScout(pseudonymizer=None).extract_candidates(tables=[tbl])[0]["codes"] == []

    p = Pseudonymizer(k_pseudo=b"k-pseudo-secret-test")
    scout = TermScout(pseudonymizer=p)
    assert scout.extract_candidates(tables=[tbl])[0]["codes"] == []

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
    assert TermScout().extract_candidates(tables=[tbl])[0]["codes"] == []


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
    fibo_uri = "urn:ontology:provisional:entity:fibo_legal_entity"
    explicit_concept = AddConceptOp(
        op_id="op-fibo-le",
        concept_uri=fibo_uri,
        concept_name="Legal Entity",
        target_hub="hub_legal_entity",
    )
    chunk = _chk(
        "chk-le-1", "A Legal Entity signed the contract with the counterparty.", taint="public"
    )
    proposal = architect.propose_delta(
        base_ontology=None, evidence=[chunk], operations=[explicit_concept]
    )
    concept_ops = [op for op in proposal.delta.operations if isinstance(op, AddConceptOp)]
    assert [op.target_hub for op in concept_ops].count("hub_legal_entity") == 1
    assert concept_ops[0].concept_uri == fibo_uri


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
    concept_ops = [op for op in proposal.delta.operations if isinstance(op, AddConceptOp)]
    assert len(concept_ops) >= 1
    assert concept_ops[0].concept_uri.startswith("urn:ontology:provisional:entity:")
    assert concept_ops[0].evidence_refs[0].snippet is None


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
    concept_ops = [op for op in proposal.delta.operations if isinstance(op, AddConceptOp)]
    enum_ops = [op for op in proposal.delta.operations if isinstance(op, AddReferenceEnumerationOp)]
    assert len(concept_ops) >= 1
    assert concept_ops[0].concept_uri.startswith("urn:ontology:provisional:entity:")
    assert len(enum_ops) == 0


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


def test_no_agent_created_op_without_evidence_carries_canonical_uri() -> None:
    architect = OntologyArchitect()
    tbl = _tbl(
        "tbl_customers", ["customer_id", "tier_code"], [["CUST-1", "PLATINUM"], ["CUST-2", "GOLD"]]
    )
    proposal = architect.propose_delta(base_ontology=None, evidence=[tbl])
    for op in proposal.delta.operations:
        if isinstance(op, AddConceptOp):
            assert op.concept_uri.startswith("urn:ontology:provisional:entity:")
            assert len(op.evidence_refs) > 0
        elif isinstance(op, AddReferenceEnumerationOp):
            assert op.enum_uri.startswith("urn:ontology:provisional:enum:")
            assert len(op.evidence_refs) > 0


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
    assert {op.enumeration_name for op in code_ops} == {"country", "currency"}


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
    rel_ops = [op for op in proposal.delta.operations if isinstance(op, AddRelationshipOp)]
    assert len(rel_ops) == 1
    assert rel_ops[0].source_concept_uri == "urn:ontology:provisional:entity:legal_entity"
    assert rel_ops[0].target_concept_uri == "urn:ontology:provisional:entity:counterparty"

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
    m1 = _m(c_op.concept_uri, "https://spec.edmcouncil.org/fibo/Trade", "exactMatch")
    m2 = _m(c_op.concept_uri, "https://spec.edmcouncil.org/fibo/Transaction", "broadMatch")
    proposal = OntologyArchitect().propose_delta(
        base_ontology=None, evidence=[], operations=[c_op, m1, m2]
    )
    mapping_ops = [op for op in proposal.delta.operations if isinstance(op, AddExternalMappingOp)]
    assert {op.target_uri for op in mapping_ops} == {m1.target_uri, m2.target_uri}


def test_relationships_with_default_uri_do_not_collide() -> None:
    c1, c2, c3 = _c("party"), _c("account"), _c("contract")
    r1 = _r("party_account", c1.concept_uri, c2.concept_uri)
    r2 = _r("party_contract", c1.concept_uri, c3.concept_uri)
    proposal = OntologyArchitect().propose_delta(
        base_ontology=None, evidence=[], operations=[c1, c2, c3, r1, r2]
    )
    rel_ops = [op for op in proposal.delta.operations if isinstance(op, AddRelationshipOp)]
    assert {op.target_concept_uri for op in rel_ops} == {c2.concept_uri, c3.concept_uri}


def test_explicit_concept_name_collision_with_base_remaps_child_ops() -> None:
    base = GraphAOntology(
        canonical_business_concepts=[
            CanonicalBusinessConcept(concept_uri="urn:ontology:entity:customer")
        ]
    )
    prov_customer = _c("customer")
    attr_op = _a(prov_customer.concept_uri, "email", "string")
    proposal = OntologyArchitect().propose_delta(
        base_ontology=base, evidence=[], operations=[prov_customer, attr_op]
    )
    assert not any(isinstance(op, AddConceptOp) for op in proposal.delta.operations)
    attr_ops = [op for op in proposal.delta.operations if isinstance(op, AddAttributeOp)]
    assert len(attr_ops) == 1 and attr_ops[0].concept_uri == "urn:ontology:entity:customer"


def test_explicit_ungrounded_canonical_ops_rewritten_to_provisional_namespace() -> None:
    canonical_concept = _c("urn:ontology:entity:portfolio", ev=[])
    child_attr = _a("urn:ontology:entity:portfolio", "total_value", "decimal(18,2)")
    proposal = OntologyArchitect().propose_delta(
        base_ontology=None, evidence=[], operations=[canonical_concept, child_attr]
    )
    assert (
        proposal.delta.author == "agent:ontology-architect+caller"
        and proposal.confidence_score < 1.0
    )
    c_ops = [op for op in proposal.delta.operations if isinstance(op, AddConceptOp)]
    a_ops = [op for op in proposal.delta.operations if isinstance(op, AddAttributeOp)]
    assert len(c_ops) == 1 and c_ops[0].concept_uri == "urn:ontology:provisional:entity:portfolio"
    assert len(a_ops) == 1 and a_ops[0].concept_uri == "urn:ontology:provisional:entity:portfolio"


def test_explicit_homonyms_with_distinct_uris_preserved() -> None:
    base = GraphAOntology(
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:gl_account",
                concept_name="Account",
            )
        ]
    )
    cust_acc = _c("customer_account", name="Account")
    vend_acc = _c("vendor_account", name="Account")
    proposal = OntologyArchitect().propose_delta(
        base_ontology=base, evidence=[], operations=[cust_acc, vend_acc]
    )
    uris = {op.concept_uri for op in proposal.delta.operations if isinstance(op, AddConceptOp)}
    assert "urn:ontology:provisional:entity:customer_account" in uris
    assert "urn:ontology:provisional:entity:vendor_account" in uris


def test_explicit_ungrounded_canonical_relationship_rewritten_to_provisional() -> None:
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
    assert (
        rel_ops[0].relationship_uri == "urn:ontology:provisional:relationship:contract_counterparty"
    )


def test_explicit_grounded_canonical_ops_keep_canonical_uri() -> None:
    ev = [
        EvidenceRef(
            evidence_type="document_span",
            reference_uri="urn:opendv:evidence:chunk:c1",
            snippet="Signed bond.",
        )
    ]
    c = _c("urn:ontology:entity:bond", ev=ev)
    base = GraphAOntology(
        canonical_business_concepts=[
            CanonicalBusinessConcept(concept_uri="urn:ontology:entity:legal_entity")
        ]
    )
    rel = _r(
        "urn:ontology:relationship:issues_bond",
        "urn:ontology:entity:legal_entity",
        "urn:ontology:entity:bond",
        ev=ev,
    )
    proposal = OntologyArchitect().propose_delta(
        base_ontology=base, evidence=[], operations=[c, rel]
    )
    c_ops = [op for op in proposal.delta.operations if isinstance(op, AddConceptOp)]
    r_ops = [op for op in proposal.delta.operations if isinstance(op, AddRelationshipOp)]
    assert len(c_ops) == 1 and c_ops[0].concept_uri == "urn:ontology:entity:bond"
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

    cust = _c("customer_account", name="Account")
    vend = _c("vendor_account", name="Account")
    with pytest.raises(DanglingParentReferenceError):
        OntologyArchitect().propose_delta(
            base_ontology=None, evidence=[], operations=[cust, vend, attr]
        )


def test_enum_rewritten_to_provisional_preserves_caller_identifier() -> None:
    op = _enum("urn:ontology:enum:iso_country", name="country", codes=["US", "CA"])
    proposal = OntologyArchitect().propose_delta(base_ontology=None, evidence=[], operations=[op])
    enum_ops = [op for op in proposal.delta.operations if isinstance(op, AddReferenceEnumerationOp)]
    assert (
        len(enum_ops) == 1 and enum_ops[0].enum_uri == "urn:ontology:provisional:enum:iso_country"
    )


def test_rewritten_ops_pass_pydantic_validation_roundtrip() -> None:
    c = _c("urn:ontology:entity:asset")
    rel = _r(
        "urn:ontology:relationship:owns_asset",
        "urn:ontology:entity:asset",
        "urn:ontology:entity:asset",
    )
    code = _code("urn:ontology:enum:status", "ACTIVE")
    enum_op = _enum("urn:ontology:enum:status", name="status", codes=["PENDING"])
    proposal = OntologyArchitect().propose_delta(
        base_ontology=None, evidence=[], operations=[c, rel, enum_op, code]
    )
    restored = OntologyDelta.model_validate(proposal.delta.model_dump(mode="json"))
    assert len(restored.operations) == 4
