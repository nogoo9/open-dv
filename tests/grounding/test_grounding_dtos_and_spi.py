"""M1.1 tests for Layer-1 grounding DTOs, SPI, LexicalIndex, and FIBO Pack v2 reader/closure."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

import opendv.models as models_pkg
from opendv.grounding import KnowledgeSource
from opendv.grounding.kernel.lexical import LexicalIndex, load_lexical_matcher_config
from opendv.grounding.providers.fibo import (
    FINANCIAL_ACRONYMS,
    FiboPackQueryAdapter,
    FiboReferenceOntologyLoader,
    SubclassClosureResolver,
    get_bundled_fibo_pack_sha256,
    minimal_taxonomic_leaves,
)
from opendv.models.errors import ErrorCode, OpenDVError, ReferenceOntologyCorruptError
from opendv.models.events import GroundingReceipt
from opendv.models.grounding import (
    CandidateMapping,
    ColumnEvidence,
    ColumnGroundingResult,
    ColumnPair,
    ConfidenceInterval,
    FiboDriftItem,
    FiboProjectDriftReport,
    FiboSyncReq,
    FiboSyncResp,
    GroundingPolicy,
    GroundingRequest,
    GroundingResponse,
    InferredValueProfile,
    OntologyGroundReq,
    SemanticContext,
    TableAnchorCandidate,
    TableEvidence,
    TableGroundingResult,
)
from opendv.models.grounding_spi import (
    BackendReasoningResult,
    CalibrationParams,
    KnowledgeItem,
    KnowledgeSourceMetadata,
    RecallHit,
    RerankCandidateSummary,
    RerankColumnPacket,
    TableReasoningContext,
    TableRerankAssignment,
    TableRerankResult,
)
from opendv.models.ontology import (
    ConceptAttribute,
    ExternalMapping,
    OntologyEnumeration,
    OntologyRelationship,
)
from opendv.models.ontology_deltas import (
    AGENT_ADDITIVE_OPS,
    HUMAN_ONLY_OPS,
    SERVER_ONLY_OPS,
)
from opendv.models.project import ProjectSettingsSnapshot
from opendv.models.sources import ColumnProfile, ColumnShapeStats
from opendv.models.vault_ir import DecimalType, map_sql_to_portable_type
from opendv.service._state_ddl import VALID_RECORD_KINDS
from opendv.service.state_store import ProjectStateManager
from opendv.verifier import map_sql_to_portable_type as verifier_map_sql
from opendv.verifier._l1_drift_and_bucket_a import map_sql_to_portable_type as l1_map_sql

REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_shape(*, pii_suppressed: bool = False, enum_vals: list[str] | None = None) -> ColumnShapeStats:
    return ColumnShapeStats(
        profiled_non_null_count=20,
        distinct_ratio=0.1,
        observed_enum_values=enum_vals or [],
        pii_suppressed=pii_suppressed,
    )


def _make_profile(
    *,
    pii_tags: list[str] | None = None,
    pii_suppressed: bool = False,
    enum_vals: list[str] | None = None,
) -> InferredValueProfile:
    return InferredValueProfile(
        observed_sample_count=20,
        non_null_count=20,
        masked_count=0,
        declared_logical_type="UTF8_STRING",
        inferred_logical_type="UTF8_STRING",
        type_confidence=1.0,
        pii_tags=pii_tags or [],
        shape=_make_shape(pii_suppressed=pii_suppressed, enum_vals=enum_vals),
    )


def _synthetic_pack_v2_payload() -> dict[str, object]:
    base = "https://spec.edmcouncil.org/fibo/ontology"
    return {
        "metadata": {
            "pack_id": "fibo-core-v2-test",
            "pack_schema_version": "2.0.0",
            "version": "2.0.0-test",
            "modules": ["BE", "FBC", "FND", "SEC"],
        },
        "classes": {
            f"{base}/FND/AgentsAndPeople/Agents/AutonomousAgent": {
                "uri": f"{base}/FND/AgentsAndPeople/Agents/AutonomousAgent",
                "label": "Autonomous Agent",
                "definition": "An autonomous agent.",
                "module": "FND",
                "synonyms": [],
                "sub_class_of": [],
                "disjoint_with": [f"{base}/FND/DatesAndTimes/FinancialDates/ExplicitDate"],
                "archetype": "entity",
            },
            f"{base}/BE/LegalEntities/LegalPersons/LegalEntity": {
                "uri": f"{base}/BE/LegalEntities/LegalPersons/LegalEntity",
                "label": "Legal Entity",
                "definition": "An entity that has legal identity and rights under law.",
                "module": "BE",
                "synonyms": ["Artificial Person", "Juridical Entity"],
                "sub_class_of": [f"{base}/FND/AgentsAndPeople/Agents/AutonomousAgent"],
                "archetype": "entity",
            },
            f"{base}/BE/LegalEntities/CorporateBodies/Corporation": {
                "uri": f"{base}/BE/LegalEntities/CorporateBodies/Corporation",
                "label": "Corporation",
                "definition": "A legal entity created under state law.",
                "module": "BE",
                "synonyms": ["Body Corporate"],
                "sub_class_of": [f"{base}/BE/LegalEntities/LegalPersons/LegalEntity"],
                "archetype": "entity",
            },
            f"{base}/FND/Parties/Roles/ThingInRole": {
                "uri": f"{base}/FND/Parties/Roles/ThingInRole",
                "label": "Thing In Role",
                "definition": "Generic role.",
                "module": "FND",
                "synonyms": [],
                "sub_class_of": [],
                "is_played_by": [f"{base}/FND/AgentsAndPeople/Agents/AutonomousAgent"],
                "archetype": "party_role",
            },
            f"{base}/FBC/ProductsAndServices/ClientsAndAccounts/Custodian": {
                "uri": f"{base}/FBC/ProductsAndServices/ClientsAndAccounts/Custodian",
                "label": "Custodian",
                "definition": "A party role holding assets in custody.",
                "module": "FBC",
                "synonyms": [],
                "sub_class_of": [f"{base}/FND/Parties/Roles/ThingInRole"],
                "is_played_by": [f"{base}/BE/LegalEntities/LegalPersons/LegalEntity"],
                "associated_properties": [
                    {
                        "property_uri": f"{base}/FBC/ProductsAndServices/ClientsAndAccounts/holdsAccount",
                        "target_uris": [
                            f"{base}/FND/DatesAndTimes/FinancialDates/ExplicitDate"
                        ],
                        "restriction_kind": "some",
                    }
                ],
                "archetype": "party_role",
            },
            f"{base}/BE/LegalEntities/LEIEntities/LegalEntityIdentifier": {
                "uri": f"{base}/BE/LegalEntities/LEIEntities/LegalEntityIdentifier",
                "label": "Legal Entity Identifier",
                "definition": "ISO 17442 global identifier for a legal entity.",
                "module": "BE",
                "synonyms": ["LEI"],
                "sub_class_of": [],
                "identified_entity_uris": [
                    f"{base}/BE/LegalEntities/LegalPersons/LegalEntity"
                ],
                "archetype": "identifier",
            },
            f"{base}/FND/DatesAndTimes/FinancialDates/ExplicitDate": {
                "uri": f"{base}/FND/DatesAndTimes/FinancialDates/ExplicitDate",
                "label": "Explicit Date",
                "definition": "A specific calendar date.",
                "module": "FND",
                "synonyms": [],
                "sub_class_of": [],
                "archetype": "temporal",
            },
            f"{base}/SEC/Debt/Bonds/OldBond": {
                "uri": f"{base}/SEC/Debt/Bonds/OldBond",
                "label": "Old Bond",
                "definition": "Deprecated bond concept.",
                "module": "SEC",
                "synonyms": [],
                "sub_class_of": [],
                "deprecated": True,
                "replaced_by": f"{base}/SEC/Debt/Bonds/LegacyBond",
            },
            f"{base}/SEC/Debt/Bonds/LegacyBond": {
                "uri": f"{base}/SEC/Debt/Bonds/LegacyBond",
                "label": "Legacy Bond",
                "definition": "Intermediate deprecated bond.",
                "module": "SEC",
                "synonyms": [],
                "sub_class_of": [],
                "deprecated": True,
                "replaced_by": f"{base}/SEC/Debt/Bonds/Bond",
            },
            f"{base}/SEC/Debt/Bonds/Bond": {
                "uri": f"{base}/SEC/Debt/Bonds/Bond",
                "label": "Bond",
                "definition": "A debt security.",
                "module": "SEC",
                "synonyms": ["Debt Bond"],
                "sub_class_of": [],
                "deprecated": False,
            },
        },
        "properties": {
            f"{base}/FBC/ProductsAndServices/ClientsAndAccounts/holdsAccount": {
                "uri": f"{base}/FBC/ProductsAndServices/ClientsAndAccounts/holdsAccount",
                "label": "holds account",
                "definition": "Links a custodian to an account or date.",
                "module": "FBC",
                "property_type": "ObjectProperty",
                "sub_property_of": [f"{base}/FND/Relations/Relations/has"],
                "inverse_of": [f"{base}/FBC/ProductsAndServices/ClientsAndAccounts/isHeldBy"],
            },
            f"{base}/FBC/ProductsAndServices/ClientsAndAccounts/isHeldBy": {
                "uri": f"{base}/FBC/ProductsAndServices/ClientsAndAccounts/isHeldBy",
                "label": "is held by",
                "definition": "Inverse of holds account.",
                "module": "FBC",
                "property_type": "ObjectProperty",
                "sub_property_of": [],
                "inverse_of": [],
            },
            f"{base}/FND/Relations/Relations/has": {
                "uri": f"{base}/FND/Relations/Relations/has",
                "label": "has",
                "definition": "Top-level relation property.",
                "module": "FND",
                "property_type": "ObjectProperty",
                "sub_property_of": [],
            },
        },
        "individuals": {
            f"{base}/IND/InterestRates/SOFR": {
                "uri": f"{base}/IND/InterestRates/SOFR",
                "label": "Secured Overnight Financing Rate",
                "module": "IND",
                "aliases": ["SOFR"],
                "types": [f"{base}/SEC/Debt/Bonds/Bond"],
                "deprecated": False,
            }
        },
    }


def test_map_sql_to_portable_type_relocation_and_canonical_passthrough() -> None:
    assert map_sql_to_portable_type is verifier_map_sql
    assert map_sql_to_portable_type is l1_map_sql

    canonical_cases = [
        ("FIXED_BINARY_32", "FIXED_BINARY_32"),
        ("UTF8_STRING", "UTF8_STRING"),
        ("INT32", "INT32"),
        ("int32 ", "INT32"),
        ("INT64", "INT64"),
        ("DATE32", "DATE32"),
        (" date32", "DATE32"),
        ("TIMESTAMP_UTC_US", "TIMESTAMP_UTC_US"),
        ("BOOLEAN", "BOOLEAN"),
        ("JSON_DOCUMENT", "JSON_DOCUMENT"),
    ]
    for raw, expected in canonical_cases:
        assert map_sql_to_portable_type(raw) == expected

    assert map_sql_to_portable_type("DECIMAL") == DecimalType(precision=38, scale=10)
    assert map_sql_to_portable_type("NUMERIC") == DecimalType(precision=38, scale=10)
    assert map_sql_to_portable_type("DECIMAL(12, 4)") == DecimalType(precision=12, scale=4)
    assert map_sql_to_portable_type("DOUBLE") == DecimalType(precision=38, scale=10)
    assert map_sql_to_portable_type("FLOAT4") == DecimalType(precision=38, scale=10)
    assert map_sql_to_portable_type("VARCHAR(255)") == "UTF8_STRING"


def test_column_shape_stats_and_profile_validation() -> None:
    stats = ColumnShapeStats(
        profiled_non_null_count=100,
        distinct_ratio=0.05,
        length_min=3,
        length_max=3,
        looks_like_enum=True,
        observed_enum_values=["USD", "EUR"],
        surrogate_kind="none",
        structural_patterns={"iso_4217_ccy": 1.0},
        detected_sentinels=["N/A"],
        pii_suppressed=False,
    )
    prof = ColumnProfile(
        col_name="currency_code",
        sql_type="VARCHAR(3)",
        nullable=False,
        is_pk=False,
        null_ratio=0.0,
        distinct_count_est=2,
        shape_stats=stats,
    )
    round_trip = ColumnProfile.model_validate_json(prof.model_dump_json())
    assert round_trip == prof

    with pytest.raises(ValidationError):
        ColumnShapeStats(
            profiled_non_null_count=10,
            distinct_ratio=0.5,
            extra_field="forbidden",  # type: ignore[call-arg]
        )

    for bad_kwargs in (
        {"length_min": 4},
        {"length_max": 10},
        {"numeric_min": 1.0},
        {"numeric_max": 99.0},
        {"observed_enum_values": ["USD"]},
    ):
        with pytest.raises(ValidationError):
            ColumnShapeStats(
                profiled_non_null_count=10,
                distinct_ratio=0.5,
                pii_suppressed=True,
                **bad_kwargs,  # type: ignore[arg-type]
            )

    for bad_bounds in (
        {"distinct_ratio": float("nan")},
        {"distinct_ratio": float("inf")},
        {"length_min": -1},
        {"length_max": -1},
        {"length_min": 5, "length_max": 2},
        {"numeric_min": float("nan")},
        {"numeric_max": float("inf")},
        {"numeric_min": 10.0, "numeric_max": 2.0},
        {"structural_patterns": {"cusip": float("nan")}},
        {"structural_patterns": {"cusip": -0.1}},
        {"structural_patterns": {"cusip": 1.1}},
    ):
        kwargs: dict[str, object] = {"profiled_non_null_count": 10, "distinct_ratio": 0.5}
        kwargs.update(bad_bounds)
        with pytest.raises(ValidationError):
            ColumnShapeStats(**kwargs)  # type: ignore[arg-type]


def test_grounding_receipt_and_sqlite_persistence(tmp_path: Path) -> None:
    assert "grounding_receipt" in VALID_RECORD_KINDS

    col_receipt = GroundingReceipt(
        target_kind="column",
        table_id="src1::holdings",
        source_column="cusip",
        schema_fingerprint="a" * 64,
        standard_uri="https://spec.edmcouncil.org/fibo/ontology/SEC/Cusip",
        reference_pack_id="fibo-core",
        reference_pack_sha256="b" * 64,
        confidence=Decimal("0.9850"),
        mapping_type="skos:exactMatch",
    )
    assert col_receipt.receipt_id.startswith("grnd_")
    assert col_receipt.provenance == "server_verified"

    anchor_receipt = GroundingReceipt(
        target_kind="table_anchor",
        table_id="src1::holdings",
        source_column=None,
        schema_fingerprint="a" * 64,
        standard_uri="https://spec.edmcouncil.org/fibo/ontology/SEC/Holding",
        reference_pack_id="fibo-core",
        reference_pack_sha256="b" * 64,
        confidence=Decimal("0.8800"),
        mapping_type="skos:closeMatch",
        table_anchor_uri="https://spec.edmcouncil.org/fibo/ontology/SEC/Holding",
    )
    assert anchor_receipt.source_column is None

    with pytest.raises(ValidationError):
        GroundingReceipt(
            target_kind="column",
            table_id="src1::holdings",
            source_column="   ",
            schema_fingerprint="a" * 64,
            standard_uri="urn:x",
            reference_pack_id="fibo-core",
            reference_pack_sha256="b" * 64,
            confidence=Decimal("0.9000"),
            mapping_type="skos:exactMatch",
        )

    with pytest.raises(ValidationError):
        GroundingReceipt(
            target_kind="table_anchor",
            table_id="src1::holdings",
            source_column="cusip",
            schema_fingerprint="a" * 64,
            standard_uri="urn:x",
            reference_pack_id="fibo-core",
            reference_pack_sha256="b" * 64,
            confidence=Decimal("0.9000"),
            mapping_type="skos:exactMatch",
        )

    mgr = ProjectStateManager(tmp_path / "ws")
    mgr.initialize()
    mgr.create_project("proj-gnd-01", "asset_management", ProjectSettingsSnapshot())
    seq_id = mgr.append_event_or_receipt(
        "proj-gnd-01",
        record_kind="grounding_receipt",
        run_id="run-1",
        payload=col_receipt,
    )
    assert seq_id >= 1
    rows = mgr.list_events_or_receipts("proj-gnd-01", record_kind="grounding_receipt")
    assert len(rows) == 1
    restored = GroundingReceipt.model_validate(rows[0]["payload"])
    assert restored == col_receipt


def test_ontology_optional_fields_and_m1_delta_count() -> None:
    attr = ConceptAttribute(
        name="maturity_date",
        logical_type="DATE32",
        sql_type_hint="DATE",
        nullable=True,
        is_business_key=False,
        is_pii=False,
    )
    assert attr.standard_property_uri is None

    mapping = ExternalMapping(
        standard_uri="urn:fibo:Bond",
        reference_pack_id="fibo-core",
        reference_pack_sha256="a" * 64,
        mapping_type="skos:exactMatch",
        confidence=Decimal("0.9500"),
    )
    assert mapping.grounding_receipt_id is None

    rel = OntologyRelationship(
        relationship_uri="urn:rel:custodian",
        relationship_name="custodian",
        source_concept_uri="urn:c:Fund",
        target_concept_uri="urn:c:LegalEntity",
        cardinality="MANY_TO_ONE",
        target_link="lnk_fund_custodian",
        grain_semantics="fund-custodian",
        description="Custodian link",
    )
    assert rel.standard_property_uri is None
    assert rel.link_attributes == []

    enum = OntologyEnumeration(
        enum_uri="urn:enum:ccy",
        enumeration_name="Currency",
        target_reference_table="ref_currency",
        codes={"USD": "US Dollar"},
        description="Currencies",
    )
    assert enum.standard_uri is None

    assert len(AGENT_ADDITIVE_OPS) == 16
    assert len(HUMAN_ONLY_OPS) == 19
    assert len(SERVER_ONLY_OPS) == 1
    assert len(AGENT_ADDITIVE_OPS) + len(HUMAN_ONLY_OPS) + len(SERVER_ONLY_OPS) == 36
    assert "UpdateRelationshipMetadata" in HUMAN_ONLY_OPS
    assert "UpdateEnumerationMetadata" in HUMAN_ONLY_OPS


def test_public_and_spi_grounding_dtos_and_exports() -> None:
    for pub_name in (
        "OntologyGroundReq",
        "GroundingRequest",
        "GroundingResponse",
        "FiboSyncReq",
        "FiboSyncResp",
    ):
        assert hasattr(models_pkg, pub_name)

    for spi_name in (
        "CalibrationParams",
        "KnowledgeSourceMetadata",
        "KnowledgeItem",
        "RecallHit",
        "TableReasoningContext",
        "BackendReasoningResult",
        "RerankCandidateSummary",
        "RerankColumnPacket",
        "TableRerankAssignment",
        "TableRerankResult",
    ):
        assert not hasattr(models_pkg, spi_name)

    cal = CalibrationParams.analytic_defaults()
    assert cal.calibration_mode == "analytic_logit_normal"
    assert cal.T == 1.0
    assert cal.matched_min_lower_bound == 0.45
    assert cal.max_effective_sample_count == 200

    with pytest.raises(ValidationError):
        GroundingPolicy(mode="deterministic_only", skip_reason="   ")
    with pytest.raises(ValidationError):
        GroundingPolicy(mode="agentic", skip_reason="not allowed")

    t1 = TableEvidence(table_name="trades", columns=[ColumnEvidence(column_name="id", data_type="INT")])
    t2 = TableEvidence(table_name="trades", columns=[ColumnEvidence(column_name="id", data_type="INT")])
    with pytest.raises(ValidationError):
        GroundingRequest(tables=[t1, t2])
    with pytest.raises(ValidationError):
        GroundingRequest(tables=[t1], max_tables=1) if False else GroundingRequest(
            tables=[
                TableEvidence(table_name="a", columns=[]),
                TableEvidence(table_name="b", columns=[]),
            ],
            max_tables=1,
        )
    with pytest.raises(ValidationError):
        GroundingRequest(
            tables=[
                TableEvidence(
                    table_name="a",
                    columns=[
                        ColumnEvidence(column_name="c1", data_type="INT"),
                        ColumnEvidence(column_name="c2", data_type="INT"),
                    ],
                )
            ],
            max_columns=1,
        )

    # OntologyGroundReq XOR, glossary_path iff glossary, policy override/conflict
    req_with_default_policy = GroundingRequest(tables=[t1])
    det_policy = GroundingPolicy(mode="deterministic_only", skip_reason="offline test")
    og_override = OntologyGroundReq(request=req_with_default_policy, policy=det_policy)
    assert og_override.request is not None
    assert og_override.request.policy == det_policy

    req_with_explicit_policy = GroundingRequest(
        tables=[t1],
        policy=GroundingPolicy(mode="agentic"),
    )
    with pytest.raises(ValidationError):
        OntologyGroundReq(request=req_with_explicit_policy, policy=det_policy)

    with pytest.raises(ValidationError):
        OntologyGroundReq(request=req_with_default_policy, project_id="proj-1")
    with pytest.raises(ValidationError):
        OntologyGroundReq(project_id="proj-1", knowledge_source="glossary", glossary_path="  ")
    with pytest.raises(ValidationError):
        OntologyGroundReq(project_id="proj-1", knowledge_source="fibo", glossary_path="g.yaml")

    # PII consistency & CI bounds & ROUND_HALF_UP quantization
    with pytest.raises(ValidationError):
        _make_profile(pii_tags=["EMAIL"], pii_suppressed=False)
    with pytest.raises(ValidationError):
        ConfidenceInterval(
            point=0.7, lower=0.8, upper=0.9, level=0.9, calibration_mode="analytic_logit_normal"
        )
    with pytest.raises(ValidationError):
        ConfidenceInterval(
            point=0.7, lower=0.5, upper=0.9, level=1.0, calibration_mode="analytic_logit_normal"
        )

    ci = ConfidenceInterval(
        point=0.65005,
        lower=0.5,
        upper=0.8,
        level=0.9,
        calibration_mode="analytic_logit_normal",
    )
    cand = CandidateMapping(
        rank=1,
        entry_id="urn:x",
        label="X",
        entry_kind="class",
        origin_kind="reference_standard",
        confidence=ci,
        suggested_predicate="skos:exactMatch",
        semantic_context=SemanticContext(),
    )
    assert cand.to_quantized_confidence() == Decimal("0.6501")  # ROUND_HALF_UP

    anchor = TableAnchorCandidate(
        entry_id="urn:x",
        label="X",
        archetype="entity",
        origin_kind="reference_standard",
        confidence=0.65005,
        suggested_predicate="skos:exactMatch",
    )
    assert anchor.to_quantized_confidence() == Decimal("0.6501")

    # RerankColumnPacket <= 15 & empty observed_enum_values
    summaries = [
        RerankCandidateSummary(
            index=i,
            label=f"C{i}",
            entry_kind="class",
            definition="def",
            prior_score=0.5,
        )
        for i in range(16)
    ]
    with pytest.raises(ValidationError):
        RerankColumnPacket(
            column_name="c",
            data_type="VARCHAR",
            inferred_value_profile=_make_profile(),
            candidates=summaries,
        )
    with pytest.raises(ValidationError):
        RerankColumnPacket(
            column_name="c",
            data_type="VARCHAR",
            inferred_value_profile=_make_profile(enum_vals=["USD"]),
            candidates=summaries[:1],
        )

    # TableRerankAssignment abstain/index XOR, bounds & rescue term <= 64 chars
    with pytest.raises(ValidationError):
        TableRerankAssignment(column_name="c", abstain=True, selected_index=0, confidence=0.5)
    with pytest.raises(ValidationError):
        TableRerankAssignment(column_name="c", abstain=False, selected_index=None, confidence=0.5)
    with pytest.raises(ValidationError):
        TableRerankAssignment(column_name="c", abstain=False, selected_index=-1, confidence=0.5)
    with pytest.raises(ValidationError):
        TableRerankAssignment(
            column_name="c", abstain=False, selected_index=0, confidence=float("nan")
        )
    with pytest.raises(ValidationError):
        TableRerankAssignment(
            column_name="c", abstain=False, selected_index=0, confidence=float("inf")
        )
    with pytest.raises(ValidationError):
        TableRerankAssignment(
            column_name="c",
            abstain=True,
            confidence=0.5,
            suggested_search_terms=["x" * 65],
        )

    # Smoke instantiate remaining DTOs to confirm StrictModel extra="forbid"
    _ = ColumnPair(kind="amount_currency", columns=["amt", "ccy"])
    _ = GroundingResponse(
        knowledge_source_id="fibo",
        knowledge_source_version="2.0.0",
        knowledge_source_sha256="a" * 64,
        engine_version="2.0.0",
        mode_used="deterministic_only",
        skip_reason="test",
        tables=[
            TableGroundingResult(
                table_name="t",
                columns=[
                    ColumnGroundingResult(
                        table_name="t",
                        column_name="c",
                        status="NO_MATCH",
                        no_match_probability=0.9,
                        no_match_category="out_of_vocabulary_domain",
                        steward_summary="No match",
                        inferred_value_profile=_make_profile(),
                        execution_mode_used="deterministic_only",
                        selection_rule_applied="no_match",
                    )
                ],
            )
        ],
    )
    _ = FiboSyncReq(pack_file="pack.json", tag="2026Q1")
    _ = FiboSyncResp(
        drift_report=FiboProjectDriftReport(
            project_id="p1",
            pack_sha256_orig=None,
            pack_sha256_new="c" * 64,
            items=[
                FiboDriftItem(
                    tier="deprecated_with_replacement",
                    target_kind="concept",
                    target_id="urn:c1",
                    old_standard_uri="urn:old",
                    replacement_uri="urn:new",
                    detail="replaced",
                )
            ],
        )
    )
    _ = TableRerankResult(
        assignments=[TableRerankAssignment(column_name="c", selected_index=0, confidence=0.8)]
    )


def test_knowledge_source_protocol_conformance_and_sync_signatures() -> None:
    class _StubKnowledgeSource:
        @property
        def metadata(self) -> KnowledgeSourceMetadata:
            return KnowledgeSourceMetadata(
                source_id="stub",
                version="1.0",
                sha256="0" * 64,
                anchor_entry_kinds=frozenset({"class"}),
                origin_kind="reference_standard",
            )

        @property
        def calibration_params(self) -> CalibrationParams | None:
            return None

        def expand_query_terms(
            self,
            tokens: Sequence[str],
            *,
            context_text: str | None = None,
        ) -> list[str]:
            _ = context_text
            return list(tokens)

        def recall_by_text(
            self,
            query: str,
            *,
            entry_kinds: AbstractSet[str] | None = None,
            top_k: int = 25,
        ) -> list[RecallHit]:
            _ = (query, entry_kinds, top_k)
            return []

        def match_values_or_mentions(
            self,
            surface_forms: Sequence[str],
            *,
            pii_tags: Sequence[str] = (),
            context_hint: str | None = None,
            structural_patterns: Mapping[str, float] | None = None,
        ) -> list[RecallHit]:
            _ = (surface_forms, pii_tags, context_hint, structural_patterns)
            return []

        def get_item(self, entry_id: str) -> KnowledgeItem | None:
            _ = entry_id
            return None

        def get_ancestors(self, entry_id: str, *, max_depth: int = 8) -> list[str]:
            _ = (entry_id, max_depth)
            return []

        def evaluate_structured_candidates(
            self,
            column: ColumnEvidence,
            profile: InferredValueProfile,
            candidates: Sequence[RecallHit],
            *,
            table_context: TableReasoningContext | None = None,
        ) -> list[BackendReasoningResult]:
            _ = (column, profile, candidates, table_context)
            return []

        def score(self, query: str, entry_id: str) -> Decimal:
            _ = (query, entry_id)
            return Decimal("0.0000")

    stub = _StubKnowledgeSource()
    assert isinstance(stub, KnowledgeSource)

    for method_name in (
        "expand_query_terms",
        "recall_by_text",
        "match_values_or_mentions",
        "get_item",
        "get_ancestors",
        "evaluate_structured_candidates",
        "score",
    ):
        fn = getattr(KnowledgeSource, method_name)
        assert not inspect.iscoroutinefunction(fn), f"{method_name} must be synchronous"


def test_lexical_index_bm25_parity_class_scoped_idf_and_sec_guard() -> None:
    cfg = load_lexical_matcher_config(REPO_ROOT / "schemas" / "v2" / "matchers" / "bm25_fibo_v1.json")
    assert cfg.matcher_id == "bm25_fibo_v1"

    vectors = json.loads((REPO_ROOT / "vectors" / "bm25.json").read_text(encoding="utf-8"))
    idx = LexicalIndex(acronyms=FINANCIAL_ACRONYMS)

    for vec in vectors["tokenization_vectors"]:
        assert idx.tokenize(vec["input"]) == vec["expected_tokens"], vec["description"]

    for vec in vectors["quantization_boundary_vectors"]:
        q = idx.quantize_score(Decimal(vec["raw_normalized_decimal"]))
        assert float(q) == pytest.approx(vec["expected_quantized"]), vec["description"]
        assert idx.is_grounded_score(q) is vec["is_grounded"]

    for vec in vectors["hit_gate_vectors"]:
        capped = idx.apply_hit_gate(
            Decimal(vec["definition_match_raw"]),
            has_name_or_syn_hit=bool(vec["has_name_hit"] or vec["has_synonym_hit"]),
        )
        assert float(capped) == pytest.approx(vec["expected_capped"]), vec["description"]
        assert idx.is_grounded_score(capped) is vec["is_grounded"]

    for vec in vectors["scoring_vectors"]:
        items = [
            KnowledgeItem(
                entry_id=c["concept_uri"],
                label=c["concept_name"],
                entry_kind="class",
                definition=c["definition"],
                aliases=c["synonyms"],
            )
            for c in vec["corpus"]
        ]
        vec_idx = LexicalIndex(acronyms=FINANCIAL_ACRONYMS)
        vec_idx.index_items(items)
        hits = vec_idx.match(vec["query"], top_k=5)
        target_hit = next(h for h in hits if h.entry_id == vec["target_uri"])
        assert float(target_hit.raw_score) == pytest.approx(vec["expected_normalized_score"])
        assert vec_idx.is_grounded_score(target_hit.raw_score) is vec["is_grounded"]

    # Class-scoped IDF invariance when properties/individuals are added
    class_items = [
        KnowledgeItem(
            entry_id="urn:c:1",
            label="Rate Benchmark",
            entry_kind="class",
            definition="Benchmark rate for financial instruments.",
        ),
        KnowledgeItem(
            entry_id="urn:c:2",
            label="Legal Entity",
            entry_kind="class",
            definition="Entity recognized by law.",
        ),
    ]
    extra_items = [
        KnowledgeItem(
            entry_id=f"urn:p:{i}",
            label="has rate",
            entry_kind="datatype_property",
            definition="rate property",
        )
        for i in range(10)
    ]
    idx_classes_only = LexicalIndex(acronyms=FINANCIAL_ACRONYMS)
    idx_classes_only.index_items(class_items)
    idx_mixed = LexicalIndex(acronyms=FINANCIAL_ACRONYMS)
    idx_mixed.index_items([*class_items, *extra_items])
    assert idx_classes_only.score("rate", "urn:c:1") == idx_mixed.score("rate", "urn:c:1")

    # Conditional "sec" acronym guard
    assert idx.tokenize("SEC filing") == [
        "securities",
        "and",
        "exchange",
        "commission",
        "filing",
    ]
    assert idx.tokenize("sec", context_tokens={"13f"}) == [
        "securities",
        "and",
        "exchange",
        "commission",
    ]
    assert idx.tokenize("sec") == ["sec"]
    assert idx.tokenize("sec", context_tokens={"13f", "sector"}) == ["sec"]
    assert idx.tokenize("sec 13f seconds") == ["sec", "13f", "seconds"]
    assert idx.tokenize("sec filing security_id") == ["sec", "filing", "security", "id"]


def test_fibo_pack_v2_loader_closure_and_query_adapter(tmp_path: Path) -> None:
    payload = _synthetic_pack_v2_payload()
    raw_bytes = json.dumps(payload).encode("utf-8")
    digest = hashlib.sha256(raw_bytes).hexdigest()

    # Path and bytes loading + mutual exclusion + filename digest check
    pack_file = tmp_path / f"fibo-test-{digest[:8]}.json"
    pack_file.write_bytes(raw_bytes)

    loader_path = FiboReferenceOntologyLoader(pack_path=pack_file, expected_sha256=digest)
    loader_bytes = FiboReferenceOntologyLoader.from_bytes(raw_bytes, expected_sha256=digest)
    assert loader_path.pack_sha256 == digest
    assert loader_bytes.pack_sha256 == digest
    assert loader_bytes.pack_path is None
    assert loader_bytes.individual_count == 1
    assert loader_bytes.integrity_verified is True

    with pytest.raises(OpenDVError) as both_exc:
        FiboReferenceOntologyLoader(pack_path=pack_file, pack_bytes=raw_bytes)
    assert both_exc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    bad_name_file = tmp_path / "fibo-test-00000000.json"
    bad_name_file.write_bytes(raw_bytes)
    with pytest.raises(ReferenceOntologyCorruptError):
        FiboReferenceOntologyLoader(pack_path=bad_name_file, verify_integrity=True)

    # M1.1 baseline-digests fallback for get_bundled_fibo_pack_sha256
    bundled_sha = get_bundled_fibo_pack_sha256()
    assert len(bundled_sha) == 64

    # Derived indexes: restrictions, symmetric inverse_of, ancestor-closed disjointness, replaces_uris
    base = "https://spec.edmcouncil.org/fibo/ontology"
    holds_uri = f"{base}/FBC/ProductsAndServices/ClientsAndAccounts/holdsAccount"
    held_by_uri = f"{base}/FBC/ProductsAndServices/ClientsAndAccounts/isHeldBy"
    custodian_uri = f"{base}/FBC/ProductsAndServices/ClientsAndAccounts/Custodian"
    date_uri = f"{base}/FND/DatesAndTimes/FinancialDates/ExplicitDate"
    corp_uri = f"{base}/BE/LegalEntities/CorporateBodies/Corporation"
    agent_uri = f"{base}/FND/AgentsAndPeople/Agents/AutonomousAgent"
    le_uri = f"{base}/BE/LegalEntities/LegalPersons/LegalEntity"
    bond_uri = f"{base}/SEC/Debt/Bonds/Bond"
    old_bond_uri = f"{base}/SEC/Debt/Bonds/OldBond"
    legacy_bond_uri = f"{base}/SEC/Debt/Bonds/LegacyBond"

    assert custodian_uri in loader_bytes.get_restriction_domains(holds_uri)
    assert date_uri in loader_bytes.get_restriction_ranges(holds_uri)
    assert held_by_uri in loader_bytes.get_inverse_properties(holds_uri)
    assert holds_uri in loader_bytes.get_inverse_properties(held_by_uri)
    assert loader_bytes.are_disjoint(corp_uri, date_uri) is True
    assert loader_bytes.are_disjoint(corp_uri, le_uri) is False
    assert loader_bytes.get_replaces_uris(bond_uri) == sorted([old_bond_uri, legacy_bond_uri])

    bond_lookup = loader_bytes.lookup_by_uri(bond_uri)
    assert bond_lookup is not None
    assert bond_lookup["replaces_uris"] == sorted([old_bond_uri, legacy_bond_uri])

    # Taxonomic vs role-player separation and minimal taxonomic leaf reduction
    resolver = SubclassClosureResolver(loader_bytes)
    tax_matches = resolver.resolve(custodian_uri, Decimal("0.8500"))
    assert [m.uri for m in tax_matches] == [f"{base}/FND/Parties/Roles/ThingInRole"]
    assert resolver.resolve_role_players(custodian_uri) == [le_uri]
    assert resolver.resolve_role_players(custodian_uri, reduce_leaves=False) == [
        le_uri,
        agent_uri,
    ]
    assert resolver.resolve_identified_entities(
        f"{base}/BE/LegalEntities/LEIEntities/LegalEntityIdentifier"
    ) == [le_uri]
    assert resolver.resolve_property_closure(holds_uri) == [
        f"{base}/FND/Relations/Relations/has"
    ]
    assert minimal_taxonomic_leaves(
        [agent_uri, corp_uri],
        lambda u: loader_bytes.derived_indexes.ancestors_with_self.get(u, {u}),
    ) == [corp_uri]

    # FiboPackQueryAdapter hides deprecated=True and rejects unverified loader when required
    adapter = FiboPackQueryAdapter(loader=loader_bytes)
    assert adapter.lookup_concept(bond_uri) is not None
    assert adapter.score("Legal Entity", le_uri) == Decimal("1.0000")

    from opendv.models.wire_dtos_discovery import FiboQueryReq

    resp = adapter.query(FiboQueryReq(query="Bond", top_k=5))
    assert [m.concept_uri for m in resp.matches] == [bond_uri]
    assert resp.matches[0].suggested_path == "PATH_A1"

    unverified = FiboReferenceOntologyLoader.from_bytes(raw_bytes, verify_integrity=False)
    with pytest.raises(ReferenceOntologyCorruptError):
        FiboPackQueryAdapter(loader=unverified, verify_integrity=True)


def test_table_rerank_result_json_schema_conditional_invariant() -> None:
    import jsonschema

    schema = TableRerankResult.model_json_schema()
    compact = json.dumps(schema, separators=(",", ":"))
    assert len(compact) <= 4096
    assert "discriminator" not in compact
    assert "oneOf" not in compact
    assert "TableRerankAssignment" not in schema["$defs"]
    assert schema["properties"]["assignments"]["items"] == {
        "anyOf": [
            {"$ref": "#/$defs/SelectedTableRerankAssignment"},
            {"$ref": "#/$defs/AbstainedTableRerankAssignment"},
        ]
    }
    for def_name, expected_const in (
        ("SelectedTableRerankAssignment", False),
        ("AbstainedTableRerankAssignment", True),
    ):
        defn = schema["$defs"][def_name]
        assert defn["additionalProperties"] is False
        assert defn["required"] == ["column_name", "selected_index", "abstain", "confidence"]
        assert defn["properties"]["abstain"]["const"] is expected_const

    # Valid selected, abstained, and empty list payloads pass jsonschema validation
    jsonschema.validate(
        {
            "assignments": [
                {
                    "column_name": "cusip",
                    "selected_index": 0,
                    "abstain": False,
                    "confidence": 0.91,
                    "suggested_predicate_hint": "exact",
                    "suggested_search_terms": [],
                    "rationale": "Exact match.",
                },
                {
                    "column_name": "unknown_col",
                    "selected_index": None,
                    "abstain": True,
                    "confidence": 0.2,
                    "suggested_search_terms": ["bond term"],
                    "rationale": "Abstained.",
                },
            ]
        },
        schema,
    )
    jsonschema.validate({"assignments": []}, schema)

    # Invalid combinations & missing/extra properties fail jsonschema validation
    invalid_items: list[dict[str, object]] = [
        {"column_name": "c", "selected_index": None, "abstain": False, "confidence": 0.8},
        {"column_name": "c", "selected_index": 0, "abstain": True, "confidence": 0.8},
        {"column_name": "c", "selected_index": 0, "confidence": 0.8},
        {"column_name": "c", "abstain": False, "confidence": 0.8},
        {"column_name": "c", "selected_index": -1, "abstain": False, "confidence": 0.8},
        {
            "column_name": "c",
            "selected_index": 0,
            "abstain": False,
            "confidence": 0.8,
            "extra_key": 1,
        },
    ]
    for bad_item in invalid_items:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"assignments": [bad_item]}, schema)

    # Python constructor defaults and empty TableRerankResult remain intact
    sel_default = TableRerankAssignment(column_name="c", selected_index=0, confidence=0.8)
    assert sel_default.abstain is False
    abs_default = TableRerankAssignment(column_name="c", abstain=True, confidence=0.5)
    assert abs_default.selected_index is None
    assert TableRerankResult(assignments=[]).assignments == []
