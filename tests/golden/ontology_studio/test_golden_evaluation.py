"""Automated CI Golden Benchmark Evaluation Harness (Phase 6 Track 6A / Task 6A.1 / Issue #228).

Evaluates constructed Graph A ontologies and proposals against canonical Private Equity (PE)
and Wealth Management (WM) benchmark ground truth models.
Enforces:
- Mathematical Precision >= 85.0% on concepts and relationships.
- Mathematical Recall >= 80.0% on concepts and relationships.
- Competency Question (CQ) answer coverage >= 90.0% across 20 formal CQs (golden_cqs.json).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from opendv_contracts.dtos.ingestion import TableStructure
from opendv_contracts.dtos.ontology import (
    AddAttributeOp,
    AddConceptOp,
    AddExternalMappingOp,
    AddReferenceEnumCodeOp,
    AddReferenceEnumerationOp,
    AddRelationshipOp,
    OntologyProposal,
)
from opendv_core import validate_graph_a
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    CanonicalRelationship,
    ConceptAttribute,
    ExternalMapping,
    GraphAOntology,
    ReferenceEnumeration,
)
from opendv_service.construction.deterministic_pipeline import (
    DeterministicConstructionPipeline,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
PE_GROUND_TRUTH_PATH = REPO_ROOT / "examples" / "private_equity" / "pe_graph_a_ontology.yaml"
WM_GROUND_TRUTH_PATH = (
    REPO_ROOT / "tests" / "golden" / "ontology_studio" / "wm_graph_a_ontology.yaml"
)
GOLDEN_CQS_PATH = REPO_ROOT / "tests" / "golden" / "ontology_studio" / "golden_cqs.json"

# Minimum quality thresholds governing CI Golden Evaluation (docs/21 §Task 6A.1)
MIN_PRECISION_THRESHOLD: float = 0.8500
MIN_RECALL_THRESHOLD: float = 0.8000
MIN_CQ_COVERAGE_THRESHOLD: float = 0.9000


def _slugify(name: str) -> str:
    """Normalize identifiers, hub names, links, and URIs into canonical token slugs."""
    if not name:
        return ""
    val = name.strip()
    if ":" in val:
        val = val.rsplit(":", 1)[-1]
    if val.startswith("hub_") or val.startswith("lnk_") or val.startswith("ref_"):
        val = val[4:]
    cleaned = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", val)
    cleaned = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", cleaned)
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", cleaned).lower()
    return re.sub(r"_+", "_", cleaned).strip("_")


@dataclass
class CQEvaluationResult:
    """Detailed evaluation result for a single competency question."""

    cq_id: str
    domain: str
    question: str
    is_satisfied: bool
    missing_concepts: list[str] = field(default_factory=list)
    missing_relationships: list[str] = field(default_factory=list)
    missing_attributes: list[str] = field(default_factory=list)
    missing_enums: list[str] = field(default_factory=list)
    missing_mappings: list[str] = field(default_factory=list)


@dataclass
class EvaluationReport:
    """Comprehensive evaluation score card across concepts, relationships, and CQs."""

    domain: str
    concept_precision: float
    concept_recall: float
    concept_f1: float
    relationship_precision: float
    relationship_recall: float
    relationship_f1: float
    cq_coverage: float
    cq_satisfied_count: int
    cq_total_count: int
    matched_concepts: list[str] = field(default_factory=list)
    unmatched_pred_concepts: list[str] = field(default_factory=list)
    unmatched_gt_concepts: list[str] = field(default_factory=list)
    matched_relationships: list[str] = field(default_factory=list)
    unmatched_pred_relationships: list[str] = field(default_factory=list)
    unmatched_gt_relationships: list[str] = field(default_factory=list)
    cq_results: list[CQEvaluationResult] = field(default_factory=list)

    @property
    def passes_ci_gate(self) -> bool:
        """Assert whether all mathematical evaluation thresholds are met."""
        return (
            self.concept_precision >= MIN_PRECISION_THRESHOLD
            and self.concept_recall >= MIN_RECALL_THRESHOLD
            and self.relationship_precision >= MIN_PRECISION_THRESHOLD
            and self.relationship_recall >= MIN_RECALL_THRESHOLD
            and self.cq_coverage >= MIN_CQ_COVERAGE_THRESHOLD
        )


class GoldenEvaluator:
    """Automated benchmark scoring engine evaluating candidate ontologies against ground truth."""

    def __init__(self, cqs_catalog_path: Path | None = None) -> None:
        cqs_file = cqs_catalog_path or GOLDEN_CQS_PATH
        if cqs_file.exists():
            payload = json.loads(cqs_file.read_text(encoding="utf-8"))
            self.cqs: list[dict[str, Any]] = payload.get("competency_questions", [])
        else:
            self.cqs = []

    def evaluate(
        self,
        predicted: GraphAOntology | OntologyProposal,
        ground_truth: GraphAOntology,
        domain_filter: str | None = None,
    ) -> EvaluationReport:
        """Compute precision, recall, and CQ coverage of predicted model against ground truth."""
        pred_ont = self._normalize_to_ontology(predicted, ground_truth)
        domain = domain_filter or ground_truth.domain_namespace

        # 1. Concept Evaluation (slug matching or hub matching)
        gt_concepts = ground_truth.canonical_business_concepts or []
        pred_concepts = pred_ont.canonical_business_concepts or []

        matched_gt_c: set[str] = set()
        matched_pred_c: set[str] = set()

        concept_alias_map: dict[str, str] = {}
        for pc in pred_concepts:
            p_slug = _slugify(pc.concept_name or pc.concept_uri)
            p_hub = _slugify(pc.target_hub or "")
            for gc in gt_concepts:
                g_slug = _slugify(gc.concept_name or gc.concept_uri)
                g_hub = _slugify(gc.target_hub or "")
                syn_slugs = {_slugify(s) for s in (gc.synonyms or [])}

                # Match if slug, hub, or synonym matches or substrings overlap
                is_match = (
                    (
                        p_slug
                        and g_slug
                        and (p_slug == g_slug or g_slug in p_slug or p_slug in g_slug)
                    )
                    or (p_hub and g_hub and p_hub == g_hub)
                    or (p_slug in syn_slugs)
                )
                if is_match:
                    matched_gt_c.add(gc.concept_uri)
                    matched_pred_c.add(pc.concept_uri)
                    if p_slug and g_slug:
                        concept_alias_map[p_slug] = g_slug
                    if p_hub and g_hub:
                        concept_alias_map[p_hub] = g_hub

        c_prec = len(matched_pred_c) / len(pred_concepts) if pred_concepts else 1.0
        c_rec = len(matched_gt_c) / len(gt_concepts) if gt_concepts else 1.0
        c_f1 = (2 * c_prec * c_rec / (c_prec + c_rec)) if (c_prec + c_rec) > 0 else 0.0

        # 2. Relationship Evaluation
        gt_rels = ground_truth.canonical_relationships or []
        pred_rels = pred_ont.canonical_relationships or []

        matched_gt_r: set[str] = set()
        matched_pred_r: set[str] = set()
        rel_alias_map: dict[str, set[str]] = {}

        for pr in pred_rels:
            pr_slug = _slugify(pr.relationship_name or pr.relationship_uri)
            pr_link = _slugify(pr.target_link or "")
            pr_endpoints = {_slugify(e) for e in pr.endpoints}
            pr_endpoints_mapped = {concept_alias_map.get(e, e) for e in pr_endpoints}

            best_match: tuple[float, Any] | None = None
            for gr in gt_rels:
                gr_slug = _slugify(gr.relationship_name or gr.relationship_uri)
                gr_link = _slugify(gr.target_link or "")
                gr_endpoints = {_slugify(e) for e in gr.endpoints}

                score = 0.0
                if (pr_slug and gr_slug and pr_slug == gr_slug) or (
                    pr_link and gr_link and pr_link == gr_link
                ):
                    score = 5.0
                elif gr_endpoints and gr_endpoints == pr_endpoints_mapped:
                    score = 4.0
                elif pr_slug and gr_slug and (gr_slug in pr_slug or pr_slug in gr_slug):
                    score = 3.0
                elif (
                    gr_endpoints
                    and pr_endpoints_mapped
                    and (
                        pr_endpoints_mapped.issubset(gr_endpoints)
                        or gr_endpoints.issubset(pr_endpoints_mapped)
                    )
                ):
                    overlap = len(pr_endpoints_mapped & gr_endpoints)
                    score = 2.0 + (overlap / 10.0)
                elif pr_link and gr_link and (gr_link in pr_link or pr_link in gr_link):
                    score = 1.0

                if score > 0.0 and (best_match is None or score > best_match[0]):
                    best_match = (score, gr)

            if best_match is not None:
                gr = best_match[1]
                matched_gt_r.add(gr.relationship_uri)
                matched_pred_r.add(pr.relationship_uri)
                gr_slug = _slugify(gr.relationship_name or gr.relationship_uri)
                if pr_slug and gr_slug:
                    rel_alias_map.setdefault(pr_slug, set()).add(gr_slug)
                    rel_alias_map.setdefault(gr_slug, set()).add(pr_slug)
                if pr.relationship_name and gr.relationship_name:
                    p_name_slug = _slugify(pr.relationship_name)
                    g_name_slug = _slugify(gr.relationship_name)
                    rel_alias_map.setdefault(p_name_slug, set()).add(g_name_slug)
                    rel_alias_map.setdefault(g_name_slug, set()).add(p_name_slug)
                if pr.target_link and gr.target_link:
                    p_link_slug = _slugify(pr.target_link)
                    g_link_slug = _slugify(gr.target_link)
                    rel_alias_map.setdefault(p_link_slug, set()).add(g_link_slug)
                    rel_alias_map.setdefault(g_link_slug, set()).add(p_link_slug)

        r_prec = len(matched_pred_r) / len(pred_rels) if pred_rels else 1.0
        r_rec = len(matched_gt_r) / len(gt_rels) if gt_rels else 1.0
        r_f1 = (2 * r_prec * r_rec / (r_prec + r_rec)) if (r_prec + r_rec) > 0 else 0.0

        # 3. Competency Question Evaluation
        target_cqs = [
            cq
            for cq in self.cqs
            if domain_filter is None or self._domain_matches(cq.get("domain", ""), domain)
        ]

        cq_results: list[CQEvaluationResult] = []
        for cq in target_cqs:
            cq_res = self._evaluate_single_cq(cq, pred_ont, concept_alias_map, rel_alias_map)
            cq_results.append(cq_res)

        satisfied_count = sum(1 for r in cq_results if r.is_satisfied)
        total_cqs = len(target_cqs)
        cq_coverage = (satisfied_count / total_cqs) if total_cqs > 0 else 1.0

        return EvaluationReport(
            domain=domain,
            concept_precision=round(c_prec, 4),
            concept_recall=round(c_rec, 4),
            concept_f1=round(c_f1, 4),
            relationship_precision=round(r_prec, 4),
            relationship_recall=round(r_rec, 4),
            relationship_f1=round(r_f1, 4),
            cq_coverage=round(cq_coverage, 4),
            cq_satisfied_count=satisfied_count,
            cq_total_count=total_cqs,
            matched_concepts=[
                c.concept_name for c in pred_concepts if c.concept_uri in matched_pred_c
            ],
            unmatched_pred_concepts=[
                c.concept_name for c in pred_concepts if c.concept_uri not in matched_pred_c
            ],
            unmatched_gt_concepts=[
                c.concept_name for c in gt_concepts if c.concept_uri not in matched_gt_c
            ],
            matched_relationships=[
                r.relationship_name for r in pred_rels if r.relationship_uri in matched_pred_r
            ],
            unmatched_pred_relationships=[
                r.relationship_name for r in pred_rels if r.relationship_uri not in matched_pred_r
            ],
            unmatched_gt_relationships=[
                r.relationship_name for r in gt_rels if r.relationship_uri not in matched_gt_r
            ],
            cq_results=cq_results,
        )

    def _domain_matches(self, cq_domain: str, target_domain: str) -> bool:
        cd = _slugify(cq_domain)
        td = _slugify(target_domain)
        if cd == td or cd in td or td in cd:
            return True
        if ("pe" in cd or "private_equity" in cd) and ("pe" in td or "asset_management" in td):
            return True
        return bool(("wm" in cd or "wealth" in cd) and ("wm" in td or "wealth" in td))

    def _evaluate_single_cq(
        self,
        cq: dict[str, Any],
        ont: GraphAOntology,
        concept_alias_map: dict[str, str] | None = None,
        rel_alias_map: dict[str, str] | None = None,
    ) -> CQEvaluationResult:
        cq_id = cq.get("cq_id", "CQ-UNKNOWN")
        domain = cq.get("domain", "general")
        question = cq.get("question", "")

        # Available concept tokens in ontology
        ont_concept_tokens: set[str] = set()
        ont_attributes: set[str] = set()
        ont_mappings: set[str] = set()

        for c in ont.canonical_business_concepts:
            ont_concept_tokens.add(_slugify(c.concept_name))
            ont_concept_tokens.add(_slugify(c.concept_uri))
            if c.target_hub:
                ont_concept_tokens.add(_slugify(c.target_hub))
            for s in c.synonyms or []:
                ont_concept_tokens.add(_slugify(s))
            for a in c.attributes or []:
                ont_attributes.add(_slugify(a.attribute_name))
            if getattr(c, "governance_constraints", None):
                gc = c.governance_constraints
                for pii_a in getattr(gc, "pii_confidential_attributes", None) or []:
                    ont_attributes.add(_slugify(pii_a))
                for mnpi_a in getattr(gc, "mnpi_confidential_attributes", None) or []:
                    ont_attributes.add(_slugify(mnpi_a))
            for m in c.external_mappings or []:
                ont_mappings.add(_slugify(m.target_uri))
                if "/" in m.target_uri:
                    ont_mappings.add(_slugify(m.target_uri.rsplit("/", 1)[-1]))

        # Include mapped concept aliases
        if concept_alias_map:
            for tok in list(ont_concept_tokens):
                if tok in concept_alias_map:
                    ont_concept_tokens.add(concept_alias_map[tok])

        # Check required concepts
        missing_c: list[str] = []
        for req_c in cq.get("required_concepts", []):
            req_slug = _slugify(req_c)
            found = any(
                req_slug == tok
                or req_slug in tok
                or tok in req_slug
                or req_slug.replace("_", "") == tok.replace("_", "")
                for tok in ont_concept_tokens
            )
            if not found:
                missing_c.append(req_c)

        # Check required relationships
        ont_rel_tokens: set[str] = set()
        for r in ont.canonical_relationships:
            ont_rel_tokens.add(_slugify(r.relationship_name))
            ont_rel_tokens.add(_slugify(r.relationship_uri))
            if r.target_link:
                ont_rel_tokens.add(_slugify(r.target_link))
            # Endpoint co-occurrence tokens
            ep_slugs = sorted([_slugify(e) for e in r.endpoints])
            ont_rel_tokens.add("_".join(ep_slugs))

        # Include mapped relationship aliases
        if rel_alias_map:
            for tok in list(ont_rel_tokens):
                if tok in rel_alias_map:
                    val = rel_alias_map[tok]
                    if isinstance(val, (set, list)):
                        ont_rel_tokens.update(val)
                    else:
                        ont_rel_tokens.add(val)

        missing_r: list[str] = []
        for req_r in cq.get("required_relationships", []):
            req_slug = _slugify(req_r)
            found = any(
                req_slug == tok
                or req_slug in tok
                or tok in req_slug
                or req_slug.replace("_", "") == tok.replace("_", "")
                for tok in ont_rel_tokens
            )
            if not found:
                missing_r.append(req_r)

        # Check required attributes
        missing_a: list[str] = []
        for req_a in cq.get("required_attributes", []):
            req_slug = _slugify(req_a)
            found = any(
                req_slug == a_tok or req_slug in a_tok or a_tok in req_slug
                for a_tok in ont_attributes
            )
            if not found:
                missing_a.append(req_a)

        # Check required enums
        ont_enum_tokens: set[str] = set()
        for e in ont.reference_enumerations:
            ont_enum_tokens.add(_slugify(e.enum_uri))
            ont_enum_tokens.add(_slugify(e.target_reference_table))
            if e.enumeration_name:
                ont_enum_tokens.add(_slugify(e.enumeration_name))

        missing_e: list[str] = []
        for req_e in cq.get("required_enums", []):
            req_slug = _slugify(req_e)
            found = any(
                req_slug == tok or req_slug in tok or tok in req_slug for tok in ont_enum_tokens
            )
            if not found:
                missing_e.append(req_e)

        # Check required mappings
        missing_m: list[str] = []
        for req_m in cq.get("required_mappings", []):
            req_slug = _slugify(req_m)
            found = any(req_slug in m_tok for m_tok in ont_mappings)
            if not found:
                missing_m.append(req_m)

        satisfied = (
            len(missing_c) == 0
            and len(missing_r) == 0
            and len(missing_a) == 0
            and len(missing_e) == 0
            and len(missing_m) == 0
        )

        return CQEvaluationResult(
            cq_id=cq_id,
            domain=domain,
            question=question,
            is_satisfied=satisfied,
            missing_concepts=missing_c,
            missing_relationships=missing_r,
            missing_attributes=missing_a,
            missing_enums=missing_e,
            missing_mappings=missing_m,
        )

    def _normalize_to_ontology(
        self, candidate: GraphAOntology | OntologyProposal, base: GraphAOntology
    ) -> GraphAOntology:
        """Convert a proposal or ontology into a verified GraphAOntology for evaluation."""
        if isinstance(candidate, GraphAOntology):
            return candidate

        # Assemble GraphAOntology purely from candidate delta operations
        concepts: list[CanonicalBusinessConcept] = []
        concept_map: dict[str, CanonicalBusinessConcept] = {}
        rels: list[CanonicalRelationship] = []
        rel_map: dict[str, CanonicalRelationship] = {}
        enums: list[ReferenceEnumeration] = []
        enum_map: dict[str, ReferenceEnumeration] = {}

        # Gather enum codes from delta
        enum_codes: dict[str, list[str]] = {}
        for op in candidate.delta.operations:
            if isinstance(op, AddReferenceEnumCodeOp):
                ename = op.enumeration_name or op.enum_uri or "ref_default"
                enum_codes.setdefault(ename, []).append(op.code)

        for op in candidate.delta.operations:
            if isinstance(op, AddConceptOp):
                if op.concept_uri not in concept_map:
                    c = CanonicalBusinessConcept(
                        concept_uri=op.concept_uri,
                        concept_name=op.concept_name or op.concept_uri.split(":")[-1].title(),
                        target_hub=op.target_hub,
                        attributes=[],
                        external_mappings=[],
                    )
                    concept_map[op.concept_uri] = c
                    concepts.append(c)
            elif isinstance(op, AddRelationshipOp):
                if op.relationship_uri not in rel_map:
                    eps = (
                        op.endpoints
                        if op.endpoints
                        else [op.source_concept_uri, op.target_concept_uri]
                    )
                    r = CanonicalRelationship(
                        relationship_uri=op.relationship_uri,
                        relationship_name=op.relationship_name
                        or op.relationship_uri.split(":")[-1].title(),
                        target_link=op.target_link or f"lnk_{op.relationship_uri.split(':')[-1]}",
                        endpoints=[e for e in eps if e],
                        grain_semantics=op.grain_semantics or "Unit-of-work link.",
                    )
                    rel_map[op.relationship_uri] = r
                    rels.append(r)
            elif isinstance(op, AddAttributeOp):
                if op.concept_uri in concept_map:
                    c = concept_map[op.concept_uri]
                    if c.attributes is None:
                        c.attributes = []
                    c.attributes.append(
                        ConceptAttribute(
                            attribute_name=op.attribute_name,
                            data_type=op.data_type or "string",
                        )
                    )
            elif isinstance(op, AddExternalMappingOp):
                if op.concept_uri in concept_map:
                    c = concept_map[op.concept_uri]
                    if c.external_mappings is None:
                        c.external_mappings = []
                    c.external_mappings.append(
                        ExternalMapping(
                            scheme=op.scheme,
                            target_uri=op.target_uri,
                            confidence=op.confidence,
                            predicate=op.predicate,
                        )
                    )
            elif isinstance(op, AddReferenceEnumerationOp):
                key = op.name or op.target_reference_table or "ref_default"
                codes = enum_codes.get(key, ["DEFAULT"])
                e = ReferenceEnumeration(
                    enum_uri=op.enum_uri,
                    target_reference_table=op.target_reference_table or f"ref_{key}",
                    allowed_codes=codes if codes else ["ACTIVE"],
                )
                enum_map[op.enum_uri] = e
                enums.append(e)

        return GraphAOntology(
            ontology_version="1.1.0",
            domain_namespace=base.domain_namespace,
            enterprise_owner=base.enterprise_owner,
            canonical_business_concepts=concepts,
            canonical_relationships=rels,
            reference_enumerations=enums,
        )


# =============================================================================
# Pytest Test Suite
# =============================================================================


@pytest.fixture(scope="module")
def evaluator() -> GoldenEvaluator:
    return GoldenEvaluator(GOLDEN_CQS_PATH)


@pytest.fixture(scope="module")
def pe_ground_truth() -> GraphAOntology:
    assert PE_GROUND_TRUTH_PATH.exists(), f"PE ground truth missing at {PE_GROUND_TRUTH_PATH}"
    ont = validate_graph_a(PE_GROUND_TRUTH_PATH)
    assert isinstance(ont, GraphAOntology)
    return ont


@pytest.fixture(scope="module")
def wm_ground_truth() -> GraphAOntology:
    assert WM_GROUND_TRUTH_PATH.exists(), f"WM ground truth missing at {WM_GROUND_TRUTH_PATH}"
    ont = validate_graph_a(WM_GROUND_TRUTH_PATH)
    assert isinstance(ont, GraphAOntology)
    return ont


def test_ground_truth_ontologies_load_and_validate(
    pe_ground_truth: GraphAOntology, wm_ground_truth: GraphAOntology
) -> None:
    """Validate schema conformance and structural integrity of canonical ground truth ontologies."""
    # PE Ground Truth asserts
    assert pe_ground_truth.domain_namespace == "pe_asset_management"
    assert len(pe_ground_truth.canonical_business_concepts) == 5
    assert len(pe_ground_truth.canonical_relationships) == 5
    assert len(pe_ground_truth.reference_enumerations) == 2
    pe_cbc_names = {c.concept_name for c in pe_ground_truth.canonical_business_concepts}
    assert pe_cbc_names == {
        "Fund",
        "LimitedPartner",
        "PortfolioCompany",
        "Document",
        "CustodianBank",
    }

    # WM Ground Truth asserts
    assert wm_ground_truth.domain_namespace == "wealth_mgmt"
    assert len(wm_ground_truth.canonical_business_concepts) == 6
    assert len(wm_ground_truth.canonical_relationships) == 5
    assert len(wm_ground_truth.reference_enumerations) == 2
    wm_cbc_names = {c.concept_name for c in wm_ground_truth.canonical_business_concepts}
    assert wm_cbc_names == {
        "Party",
        "Customer",
        "Portfolio",
        "FinancialInstrument",
        "CustodianBank",
        "AdvisoryAccount",
    }


def test_golden_competency_questions_catalog_schema(evaluator: GoldenEvaluator) -> None:
    """Verify golden competency questions catalog contains 20 formal questions (10 PE, 10 WM)."""
    assert len(evaluator.cqs) == 20
    pe_cqs = [cq for cq in evaluator.cqs if cq.get("domain") == "private_equity"]
    wm_cqs = [cq for cq in evaluator.cqs if cq.get("domain") == "wealth_mgmt"]
    assert len(pe_cqs) == 10
    assert len(wm_cqs) == 10

    pe_ids = {cq["cq_id"] for cq in pe_cqs}
    wm_ids = {cq["cq_id"] for cq in wm_cqs}
    assert pe_ids == {f"CQ-PE-{i:02d}" for i in range(1, 11)}
    assert wm_ids == {f"CQ-WM-{i:02d}" for i in range(1, 11)}

    for cq in evaluator.cqs:
        assert cq.get("question") and len(cq["question"]) > 10
        assert cq.get("required_concepts") and len(cq["required_concepts"]) >= 1


def test_ground_truth_self_evaluation_reaches_perfect_score(
    evaluator: GoldenEvaluator,
    pe_ground_truth: GraphAOntology,
    wm_ground_truth: GraphAOntology,
) -> None:
    """Assert ground truth ontologies achieve 100% precision, recall, and CQ coverage."""
    pe_report = evaluator.evaluate(pe_ground_truth, pe_ground_truth, domain_filter="private_equity")
    assert pe_report.concept_precision == 1.0000
    assert pe_report.concept_recall == 1.0000
    assert pe_report.relationship_precision == 1.0000
    assert pe_report.relationship_recall == 1.0000
    assert pe_report.cq_coverage == 1.0000
    assert pe_report.cq_satisfied_count == 10
    assert pe_report.passes_ci_gate

    wm_report = evaluator.evaluate(wm_ground_truth, wm_ground_truth, domain_filter="wealth_mgmt")
    assert wm_report.concept_precision == 1.0000
    assert wm_report.concept_recall == 1.0000
    assert wm_report.relationship_precision == 1.0000
    assert wm_report.relationship_recall == 1.0000
    assert wm_report.cq_coverage == 1.0000
    assert wm_report.cq_satisfied_count == 10
    assert wm_report.passes_ci_gate


def test_evaluator_mathematical_precision_recall_formulation(
    evaluator: GoldenEvaluator, pe_ground_truth: GraphAOntology
) -> None:
    """Unit test for scoring engine formulas testing subsets, empty sets, and partial matches."""
    # Create partial predicted ontology with only 4 of 5 concepts and 1 extra hallucinated concept
    partial_concepts = [
        c for c in pe_ground_truth.canonical_business_concepts if c.concept_name != "CustodianBank"
    ]
    hallucinated = CanonicalBusinessConcept(
        concept_uri="urn:ontology:entity:hallucinated_entity",
        concept_name="HallucinatedEntity",
        target_hub="hub_hallucinated",
    )
    predicted = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="pe_asset_management",
        enterprise_owner="Apex Horizon Capital",
        canonical_business_concepts=[*partial_concepts, hallucinated],
        canonical_relationships=list(pe_ground_truth.canonical_relationships or []),
        reference_enumerations=list(pe_ground_truth.reference_enumerations or []),
    )

    report = evaluator.evaluate(predicted, pe_ground_truth, domain_filter="private_equity")
    # Expected precision: 4 matched / 5 predicted = 0.8000
    assert report.concept_precision == 0.8000
    # Expected recall: 4 matched / 5 ground truth = 0.8000
    assert report.concept_recall == 0.8000
    # Precision 0.8000 is < 0.8500 threshold, so CI gate fails
    assert not report.passes_ci_gate


def test_pipeline_constructed_pe_model_meets_golden_thresholds(
    evaluator: GoldenEvaluator, pe_ground_truth: GraphAOntology
) -> None:
    """Validate that Deterministic Construction Pipeline builds a PE ontology meeting CI gates."""
    pipeline = DeterministicConstructionPipeline()

    base_ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="pe_asset_management",
        enterprise_owner="Apex Horizon Capital",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:initial_seed",
                concept_name="Initial Seed",
                target_hub="hub_initial_seed",
            )
        ],
    )

    # Realistic enterprise PE evidence tables matching ground truth domain
    t_funds = TableStructure(
        table_id="tbl_equity_funds",
        headers=["equity_fund_id", "fund_name", "isin"],
        rows=[["FUND-001", "Apex Horizon Buyout Fund I", "US1234567890"]],
    )
    t_investors = TableStructure(
        table_id="tbl_investors",
        headers=["limited_partner_id", "investor_name", "lei", "wire_iban", "tax_id_num"],
        rows=[
            [
                "LP-001",
                "CalPERS Pension Fund",
                "5493006MHB84DD0ZWV18",
                "GB29NWBK60161331926819",
                "US987654321",
            ]
        ],
    )
    t_portcos = TableStructure(
        table_id="tbl_portfolio_companies",
        headers=["portfolio_company_id", "company_name", "fair_value_usd", "entry_ebitda_usd"],
        rows=[["PORTCO-001", "CyberShield Technologies", "450000000.00", "32000000.00"]],
    )
    t_documents = TableStructure(
        table_id="tbl_legal_documents",
        headers=["legal_document_id", "doc_uri", "title"],
        rows=[["DOC-001", "s3://lake/side_letters/sl_01.pdf", "LP Side Letter Agreement"]],
    )
    t_custodians = TableStructure(
        table_id="tbl_custodian_banks",
        headers=["custodian_bank_id", "custodian_name"],
        rows=[["CUST-001", "State Street Bank & Trust"]],
    )
    t_commitments = TableStructure(
        table_id="tbl_lp_commitments",
        headers=["equity_fund_id", "limited_partner_id", "committed_usd"],
        rows=[["FUND-001", "LP-001", "50000000.00"]],
    )
    t_co_invest = TableStructure(
        table_id="tbl_co_invest_allocations",
        headers=["limited_partner_id", "portfolio_company_id", "allocation_usd"],
        rows=[["LP-001", "PORTCO-001", "10000000.00"]],
    )
    t_same_as = TableStructure(
        table_id="tbl_same_as_lp",
        headers=["limited_partner_id", "same_as_limited_partner_id", "effective_date"],
        rows=[["LP-001", "LEGACY-001", "2020-01-01"]],
    )
    t_side_letters = TableStructure(
        table_id="tbl_side_letter_links",
        headers=["legal_document_id", "equity_fund_id", "execution_date"],
        rows=[["DOC-001", "FUND-001", "2024-01-15"]],
    )
    t_ic_memos = TableStructure(
        table_id="tbl_ic_valuation_memos",
        headers=["legal_document_id", "portfolio_company_id", "approval_stage"],
        rows=[["DOC-001", "PORTCO-001", "FINAL_STAGE"]],
    )
    t_strategies = TableStructure(
        table_id="tbl_strategies",
        headers=["equity_fund_id", "fund_strategy_code"],
        rows=[
            ["FUND-001", "BUYOUT"],
            ["FUND-001", "CO_INVEST"],
            ["FUND-001", "GROWTH_EQUITY"],
            ["FUND-001", "SECONDARIES"],
        ],
    )
    t_sectors = TableStructure(
        table_id="tbl_sectors",
        headers=["portfolio_company_id", "industry_sector_code"],
        rows=[
            ["PORTCO-001", "B2B_SAAS"],
            ["PORTCO-001", "FINTECH"],
            ["PORTCO-001", "HEALTHCARE_IT"],
            ["PORTCO-001", "INDUSTRIAL_TECH"],
        ],
    )

    tables = [
        t_funds,
        t_investors,
        t_portcos,
        t_documents,
        t_custodians,
        t_commitments,
        t_co_invest,
        t_same_as,
        t_side_letters,
        t_ic_memos,
        t_strategies,
        t_sectors,
    ]

    proposal = pipeline.construct_proposal(base_ontology=base_ont, tables=tables)
    assert isinstance(proposal, OntologyProposal)

    report = evaluator.evaluate(proposal, pe_ground_truth, domain_filter="private_equity")
    assert report.concept_precision >= MIN_PRECISION_THRESHOLD, (
        f"PE Concept Precision {report.concept_precision} < {MIN_PRECISION_THRESHOLD}"
    )
    assert report.concept_recall >= MIN_RECALL_THRESHOLD, (
        f"PE Concept Recall {report.concept_recall} < {MIN_RECALL_THRESHOLD}"
    )
    assert report.relationship_precision >= MIN_PRECISION_THRESHOLD, (
        f"PE Relationship Precision {report.relationship_precision} < {MIN_PRECISION_THRESHOLD}"
    )
    assert report.relationship_recall >= MIN_RECALL_THRESHOLD, (
        f"PE Relationship Recall {report.relationship_recall} < {MIN_RECALL_THRESHOLD}"
    )
    assert report.cq_coverage >= MIN_CQ_COVERAGE_THRESHOLD, (
        f"PE CQ Coverage {report.cq_coverage} < {MIN_CQ_COVERAGE_THRESHOLD}"
    )
    assert report.passes_ci_gate


def test_pipeline_constructed_wm_model_meets_golden_thresholds(
    evaluator: GoldenEvaluator, wm_ground_truth: GraphAOntology
) -> None:
    """Validate that Deterministic Construction Pipeline builds a WM ontology meeting CI gates."""
    pipeline = DeterministicConstructionPipeline()

    base_ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="wealth_mgmt",
        enterprise_owner="Global Wealth Architecture Committee",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:initial_seed",
                concept_name="Initial Seed",
                target_hub="hub_initial_seed",
            )
        ],
    )

    # Realistic enterprise Wealth Management evidence tables matching ground truth domain
    t_party = TableStructure(
        table_id="tbl_parties",
        headers=["legal_entity_id", "party_name", "total_exposure"],
        rows=[["LE-001", "Apex Sovereign Trust", "25000000.00"]],
    )
    t_cust = TableStructure(
        table_id="tbl_customers",
        headers=["customer_id", "credit_limit"],
        rows=[["CUST-001", "5000000.00"]],
    )
    t_acc = TableStructure(
        table_id="tbl_advisory_accounts",
        headers=["account_id", "account_number", "opened_date"],
        rows=[["ACC-001", "WM-ACC-987654", "2021-03-15"]],
    )
    t_port = TableStructure(
        table_id="tbl_portfolios",
        headers=["portfolio_id", "portfolio_name", "base_currency"],
        rows=[["PORT-001", "Discretionary Multi-Asset Growth", "USD"]],
    )
    t_inst = TableStructure(
        table_id="tbl_financial_instruments",
        headers=["financial_instrument_id", "isin", "ticker"],
        rows=[["INST-001", "US0378331005", "AAPL"]],
    )
    t_bank = TableStructure(
        table_id="tbl_custodian_banks",
        headers=["custodian_bank_id", "custodian_name", "bic_swift"],
        rows=[["BANK-001", "BNY Mellon Depository", "BKTRUS33XXX"]],
    )
    t_guar = TableStructure(
        table_id="tbl_party_guarantors",
        headers=["legal_entity_id", "customer_id", "guarantee_amount"],
        rows=[["LE-001", "CUST-001", "10000000.00"]],
    )
    t_ca = TableStructure(
        table_id="tbl_customer_accounts",
        headers=["customer_id", "account_id", "relationship_type"],
        rows=[["CUST-001", "ACC-001", "PRIMARY_BENEFICIARY"]],
    )
    t_ap = TableStructure(
        table_id="tbl_account_portfolios",
        headers=["account_id", "portfolio_id", "mandate_agreement"],
        rows=[["ACC-001", "PORT-001", "FULL_DISCRETION"]],
    )
    t_ph = TableStructure(
        table_id="tbl_portfolio_holdings",
        headers=["portfolio_id", "financial_instrument_id", "quantity_held"],
        rows=[["PORT-001", "INST-001", "15000"]],
    )
    t_cs = TableStructure(
        table_id="tbl_custodian_settlements",
        headers=["portfolio_id", "custodian_bank_id", "escrow_account_id"],
        rows=[["PORT-001", "BANK-001", "ESCROW-BNY-01"]],
    )
    t_status = TableStructure(
        table_id="tbl_customer_status",
        headers=["customer_id", "customer_status"],
        rows=[["CUST-001", "ACTIVE"], ["CUST-001", "SUSPENDED"], ["CUST-001", "CLOSED"]],
    )
    t_risk = TableStructure(
        table_id="tbl_risk_profile",
        headers=["portfolio_id", "risk_profile_code"],
        rows=[["PORT-001", "CONSERVATIVE"], ["PORT-001", "MODERATE"], ["PORT-001", "AGGRESSIVE"]],
    )

    tables = [
        t_party,
        t_cust,
        t_acc,
        t_port,
        t_inst,
        t_bank,
        t_guar,
        t_ca,
        t_ap,
        t_ph,
        t_cs,
        t_status,
        t_risk,
    ]

    proposal = pipeline.construct_proposal(base_ontology=base_ont, tables=tables)
    assert isinstance(proposal, OntologyProposal)

    report = evaluator.evaluate(proposal, wm_ground_truth, domain_filter="wealth_mgmt")
    assert report.concept_precision >= MIN_PRECISION_THRESHOLD, (
        f"WM Concept Precision {report.concept_precision} < {MIN_PRECISION_THRESHOLD}"
    )
    assert report.concept_recall >= MIN_RECALL_THRESHOLD, (
        f"WM Concept Recall {report.concept_recall} < {MIN_RECALL_THRESHOLD}"
    )
    assert report.relationship_precision >= MIN_PRECISION_THRESHOLD, (
        f"WM Relationship Precision {report.relationship_precision} < {MIN_PRECISION_THRESHOLD}"
    )
    assert report.relationship_recall >= MIN_RECALL_THRESHOLD, (
        f"WM Relationship Recall {report.relationship_recall} < {MIN_RECALL_THRESHOLD}"
    )
    assert report.cq_coverage >= MIN_CQ_COVERAGE_THRESHOLD, (
        f"WM CQ Coverage {report.cq_coverage} < {MIN_CQ_COVERAGE_THRESHOLD}"
    )
    assert report.passes_ci_gate


def test_ontology_studio_golden_integrity() -> None:
    """Verify bit-for-bit SHA-256 integrity of committed ontology studio golden benchmarks."""
    expected_digests = {
        WM_GROUND_TRUTH_PATH: "f2c1dd00d129ca2df926f7223904df942c35321165bff4519e369d5cc9d01fb5",
        GOLDEN_CQS_PATH: "89d2d4ff73efa68ef12096f93926bf41189634174c2fd78d6948bdec576640bf",
    }
    for file_path, expected_hash in expected_digests.items():
        assert file_path.is_file(), f"Golden benchmark missing: {file_path}"
        content = file_path.read_bytes()
        actual_hash = hashlib.sha256(content).hexdigest()
        assert actual_hash == expected_hash, (
            f"Digest mismatch for {file_path.name}: expected {expected_hash}, got {actual_hash}"
        )

