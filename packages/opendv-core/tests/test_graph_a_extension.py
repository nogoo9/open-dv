"""Comprehensive tests for GraphAOntology model extensions (Task 4A.1 / Issue #197).

Tests backward compatibility, default None semantics, Pydantic v2 validation,
collection uniqueness guards, alias conflict detection, hierarchy validation, and hash stability.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from opendv_core import compute_ontology_sha256, validate_graph_a
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    CanonicalRelationship,
    ConceptAttribute,
    ExternalMapping,
    GraphAOntology,
    ReferenceEnumeration,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
PE_FIXTURE_PATH = REPO_ROOT / "examples" / "private_equity" / "pe_graph_a_ontology.yaml"


class TestConceptAttribute:
    """Tests for the ConceptAttribute model."""

    def test_default_instantiation(self) -> None:
        """All optional fields must default strictly to None."""
        attr = ConceptAttribute(attribute_name="nav_amount")
        assert attr.attribute_name == "nav_amount"
        assert attr.data_type == "string"
        assert attr.description is None
        assert attr.definition is None
        assert attr.is_optional is None
        assert attr.example_values is None
        assert attr.deprecated is None
        assert attr.metadata is None
        assert attr.source_hints is None

    def test_full_instantiation(self) -> None:
        """Verify full instantiation with all fields populated."""
        attr = ConceptAttribute(
            attribute_name="total_commitments",
            data_type="decimal",
            description="Total capital committed by limited partners.",
            is_optional=False,
            example_values=["10000000.00", "50000000.00"],
            deprecated=False,
            metadata={"currency": "USD", "unit": "cents"},
            source_hints=["investor_portal_db.commitments.amount"],
        )
        assert attr.attribute_name == "total_commitments"
        assert attr.data_type == "decimal"
        assert attr.description == "Total capital committed by limited partners."
        assert attr.definition == "Total capital committed by limited partners."
        assert attr.is_optional is False
        assert attr.example_values == ["10000000.00", "50000000.00"]
        assert attr.deprecated is False
        assert attr.metadata == {"currency": "USD", "unit": "cents"}
        assert attr.source_hints == ["investor_portal_db.commitments.amount"]

    def test_attribute_name_normalization_from_dict(self) -> None:
        """Dict inputs with 'name' and 'definition' should normalize cleanly."""
        data = {
            "name": "vintage_year",
            "definition": "Fund inception year",
            "data_type": "integer",
        }
        attr = ConceptAttribute.model_validate(data)
        assert attr.attribute_name == "vintage_year"
        assert attr.description == "Fund inception year"
        assert attr.definition == "Fund inception year"
        assert attr.data_type == "integer"

    def test_conflicting_name_alias_rejected(self) -> None:
        """Providing conflicting name and attribute_name must raise ValidationError."""
        with pytest.raises(ValidationError, match="Conflicting attribute name"):
            ConceptAttribute.model_validate({"name": "vintage", "attribute_name": "vintage_year"})

    def test_conflicting_definition_alias_rejected(self) -> None:
        """Providing conflicting definition and description must raise ValidationError."""
        with pytest.raises(ValidationError, match="Conflicting description"):
            ConceptAttribute.model_validate(
                {
                    "attribute_name": "vintage_year",
                    "description": "Desc A",
                    "definition": "Desc B",
                }
            )

    @pytest.mark.parametrize(
        "invalid_name",
        [
            "VintageYear",  # Uppercase not allowed
            "vintage-year",  # Hyphens not allowed
            "vintage year",  # Spaces not allowed
            "vintage.year",  # Dots not allowed
            "vintage$",  # Special characters not allowed
            "",  # Empty string not allowed
            "a" * 65,  # Exceeds max_length 64
        ],
    )
    def test_attribute_name_validation_errors(self, invalid_name: str) -> None:
        """Invalid attribute names must fail Pydantic regex/length validation."""
        with pytest.raises(ValidationError):
            ConceptAttribute(attribute_name=invalid_name)

    @pytest.mark.parametrize("valid_name", ["vintage_year", "123vintage", "nav_2024", "a"])
    def test_valid_attribute_names(self, valid_name: str) -> None:
        """Valid lowercase attribute names matching ^[a-z0-9_]{1,64}$ must succeed."""
        attr = ConceptAttribute(attribute_name=valid_name)
        assert attr.attribute_name == valid_name

    def test_extra_fields_forbidden(self) -> None:
        """Undeclared properties must be rejected under extra='forbid'."""
        with pytest.raises(ValidationError):
            ConceptAttribute.model_validate({"attribute_name": "valid_name", "unknown_field": 123})


class TestExternalMapping:
    """Tests for the ExternalMapping model."""

    def test_default_instantiation(self) -> None:
        """All optional fields must default strictly to None."""
        target = "https://spec.edmcouncil.org/fibo/ontology/SEC/Equities/EquityInstruments/Share"
        mapping = ExternalMapping(
            scheme="fibo",
            target_uri=target,
        )
        assert mapping.scheme == "fibo"
        assert mapping.target_uri == target
        assert mapping.confidence is None
        assert mapping.predicate is None
        assert mapping.pinned is None
        assert mapping.provenance is None
        assert mapping.reference_pack_id is None
        assert mapping.reference_pack_sha256 is None
        # Properties
        assert mapping.standard_name == "fibo"
        assert mapping.standard_uri == target
        assert mapping.mapping_type is None

    def test_full_instantiation(self) -> None:
        """Verify full instantiation with all fields populated."""
        target = "https://spec.edmcouncil.org/fibo/ontology/FND/Arrangements/Documents/Agreement"
        pack_sha = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        mapping = ExternalMapping(
            scheme="fibo",
            target_uri=target,
            confidence=0.9250,
            predicate="skos:exactMatch",
            pinned=True,
            provenance="human",
            reference_pack_id="fibo-core",
            reference_pack_sha256=pack_sha,
        )
        assert mapping.scheme == "fibo"
        assert mapping.confidence == 0.9250
        assert mapping.predicate == "skos:exactMatch"
        assert mapping.pinned is True
        assert mapping.provenance == "human"
        assert mapping.reference_pack_id == "fibo-core"
        assert mapping.reference_pack_sha256 == pack_sha
        assert mapping.standard_name == "fibo"
        assert mapping.standard_uri == target
        assert mapping.mapping_type == "skos:exactMatch"

    def test_confidence_quantization(self) -> None:
        """Confidence score must round to 4 decimal places via ROUND_HALF_EVEN."""
        m1 = ExternalMapping(scheme="fibo", target_uri="urn:fibo:x", confidence=0.64995)
        assert m1.confidence == 0.6500

        m2 = ExternalMapping(scheme="fibo", target_uri="urn:fibo:y", confidence=0.64994)
        assert m2.confidence == 0.6499

    @pytest.mark.parametrize("invalid_confidence", [-0.1, 1.01, 2.0, -10.0])
    def test_confidence_out_of_bounds(self, invalid_confidence: float) -> None:
        """Confidence outside [0.0, 1.0] must raise ValidationError."""
        with pytest.raises(ValidationError):
            ExternalMapping(
                scheme="fibo", target_uri="urn:fibo:test", confidence=invalid_confidence
            )

    def test_alias_normalization_from_dict(self) -> None:
        """Support alias normalization without duplicating fields in model."""
        data = {
            "standard_name": "fibo",
            "standard_uri": "https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Commitment",
            "mapping_type": "closeMatch",
            "confidence": 0.88,
        }
        mapping = ExternalMapping.model_validate(data)
        assert mapping.scheme == "fibo"
        assert (
            mapping.target_uri
            == "https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Commitment"
        )
        assert mapping.predicate == "closeMatch"
        # Dump excludes alias fields since they are properties
        dumped = mapping.model_dump(mode="json", exclude_none=True)
        assert "standard_name" not in dumped
        assert "standard_uri" not in dumped
        assert "mapping_type" not in dumped
        assert dumped["scheme"] == "fibo"

    def test_conflicting_scheme_alias_rejected(self) -> None:
        """Conflicting scheme and standard_name must raise ValidationError."""
        with pytest.raises(ValidationError, match="Conflicting alias"):
            ExternalMapping.model_validate(
                {"scheme": "fibo", "standard_name": "iso20022", "target_uri": "urn:x"}
            )

    def test_conflicting_target_uri_alias_rejected(self) -> None:
        """Conflicting target_uri and standard_uri must raise ValidationError."""
        with pytest.raises(ValidationError, match="Conflicting alias"):
            ExternalMapping.model_validate(
                {"scheme": "fibo", "target_uri": "urn:a", "standard_uri": "urn:b"}
            )

    def test_conflicting_predicate_alias_rejected(self) -> None:
        """Conflicting predicate and mapping_type must raise ValidationError."""
        with pytest.raises(ValidationError, match="Conflicting alias"):
            ExternalMapping.model_validate(
                {
                    "scheme": "fibo",
                    "target_uri": "urn:a",
                    "predicate": "exactMatch",
                    "mapping_type": "closeMatch",
                }
            )

    def test_invalid_reference_pack_sha256(self) -> None:
        """reference_pack_sha256 must match 64-character lowercase hex."""
        with pytest.raises(ValidationError):
            ExternalMapping(
                scheme="fibo",
                target_uri="urn:test",
                reference_pack_sha256="invalid_hex",
            )

    def test_extra_fields_forbidden(self) -> None:
        """Undeclared properties must be rejected."""
        with pytest.raises(ValidationError):
            ExternalMapping.model_validate(
                {"scheme": "fibo", "target_uri": "urn:x", "extra_prop": True}
            )


class TestCanonicalBusinessConceptExtension:
    """Tests for CanonicalBusinessConcept extended fields and validators."""

    def test_default_instantiation_extension_fields_are_none(self) -> None:
        """CBC instantiated without extension fields must have all extension fields set to None."""
        cbc = CanonicalBusinessConcept(concept_uri="urn:ontology:entity:fund")
        assert cbc.parent_concept_uri is None
        assert cbc.attributes is None
        assert cbc.synonyms is None
        assert cbc.external_mappings is None
        assert cbc.deprecated is None
        assert cbc.metadata is None
        assert cbc.source_hints is None

    def test_full_instantiation(self) -> None:
        """CBC instantiated with all extension fields."""
        attr = ConceptAttribute(attribute_name="vintage_year", data_type="integer")
        mapping = ExternalMapping(
            scheme="fibo", target_uri="urn:fibo:Fund", predicate="skos:exactMatch"
        )
        cbc = CanonicalBusinessConcept(
            concept_uri="urn:ontology:entity:fund",
            parent_concept_uri="urn:ontology:entity:investment_vehicle",
            attributes=[attr],
            synonyms=["Private Equity Fund", "PE Vehicle"],
            external_mappings=[mapping],
            deprecated=False,
            metadata={"tier": 1, "governance_status": "APPROVED"},
            source_hints=["investor_portal_feed"],
        )
        assert cbc.parent_concept_uri == "urn:ontology:entity:investment_vehicle"
        assert len(cbc.attributes) == 1
        assert cbc.attributes[0].attribute_name == "vintage_year"
        assert cbc.synonyms == ["Private Equity Fund", "PE Vehicle"]
        assert len(cbc.external_mappings) == 1
        assert cbc.external_mappings[0].target_uri == "urn:fibo:Fund"
        assert cbc.deprecated is False
        assert cbc.metadata == {"tier": 1, "governance_status": "APPROVED"}
        assert cbc.source_hints == ["investor_portal_feed"]

    def test_duplicate_synonyms_rejected(self) -> None:
        """Duplicate synonyms must raise ValidationError with DUPLICATE_SYNONYM."""
        with pytest.raises(ValidationError, match="DUPLICATE_SYNONYM"):
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:fund",
                synonyms=["Fund", "PE Fund", "Fund"],
            )

    def test_case_insensitive_duplicate_synonyms_rejected(self) -> None:
        """Case/whitespace duplicates must raise ValidationError with DUPLICATE_SYNONYM."""
        with pytest.raises(ValidationError, match="DUPLICATE_SYNONYM"):
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:fund",
                synonyms=["Fund", " fund "],
            )

    def test_synonym_length_validation(self) -> None:
        """Synonym must be between 1 and 256 characters."""
        with pytest.raises(ValidationError):
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:fund",
                synonyms=[""],
            )
        with pytest.raises(ValidationError):
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:fund",
                synonyms=["   "],
            )
        with pytest.raises(ValidationError):
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:fund",
                synonyms=["x" * 257],
            )

    def test_duplicate_attribute_names_rejected(self) -> None:
        """Duplicate attribute names must raise ValidationError with DUPLICATE_ATTRIBUTE_NAME."""
        with pytest.raises(ValidationError, match="DUPLICATE_ATTRIBUTE_NAME"):
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:fund",
                attributes=[
                    ConceptAttribute(attribute_name="amount"),
                    ConceptAttribute(attribute_name="amount"),
                ],
            )

    def test_duplicate_external_mappings_rejected(self) -> None:
        """Duplicate mapping pairs must raise ValidationError with AMBIGUOUS_EXTERNAL_MAPPING."""
        with pytest.raises(ValidationError, match="AMBIGUOUS_EXTERNAL_MAPPING"):
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:fund",
                external_mappings=[
                    ExternalMapping(
                        scheme="fibo", target_uri="urn:fibo:x", predicate="skos:exactMatch"
                    ),
                    ExternalMapping(
                        scheme="fibo", target_uri="urn:fibo:x", predicate="skos:exactMatch"
                    ),
                ],
            )

    def test_ambiguous_none_predicate_rejected(self) -> None:
        """Ambiguous mappings with None predicate on same target_uri must be rejected."""
        with pytest.raises(ValidationError, match="AMBIGUOUS_EXTERNAL_MAPPING"):
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:fund",
                external_mappings=[
                    ExternalMapping(scheme="fibo", target_uri="urn:fibo:x", predicate=None),
                    ExternalMapping(
                        scheme="fibo", target_uri="urn:fibo:x", predicate="skos:exactMatch"
                    ),
                ],
            )

    def test_different_predicates_for_same_target_uri_allowed(self) -> None:
        """Different non-None predicates for same target_uri are allowed."""
        cbc = CanonicalBusinessConcept(
            concept_uri="urn:ontology:entity:fund",
            external_mappings=[
                ExternalMapping(
                    scheme="fibo", target_uri="urn:fibo:x", predicate="skos:exactMatch"
                ),
                ExternalMapping(
                    scheme="fibo", target_uri="urn:fibo:x", predicate="skos:closeMatch"
                ),
            ],
        )
        assert len(cbc.external_mappings) == 2

    def test_invalid_parent_concept_uri(self) -> None:
        """parent_concept_uri must match URN pattern."""
        with pytest.raises(ValidationError):
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:fund",
                parent_concept_uri="not_a_valid_urn",
            )


class TestCanonicalRelationshipExtension:
    """Tests for CanonicalRelationship extension fields."""

    def test_default_instantiation(self) -> None:
        """Extension fields must default strictly to None."""
        rel = CanonicalRelationship(
            relationship_uri="urn:ontology:relationship:fund_investment",
            target_link="lnk_fund_investment",
            endpoints=["urn:ontology:entity:fund", "urn:ontology:entity:company"],
        )
        assert rel.description is None
        assert rel.cardinality is None
        assert rel.deprecated is None
        assert rel.metadata is None
        assert rel.source_hints is None

    def test_full_instantiation(self) -> None:
        """Full instantiation with extension fields."""
        rel = CanonicalRelationship(
            relationship_uri="urn:ontology:relationship:fund_investment",
            target_link="lnk_fund_investment",
            endpoints=["urn:ontology:entity:fund", "urn:ontology:entity:company"],
            description="Portfolio investment link between fund and company.",
            cardinality="one_to_many",
            deprecated=False,
            metadata={"priority": "HIGH"},
            source_hints=["deal_tracker_db"],
        )
        assert rel.description == "Portfolio investment link between fund and company."
        assert rel.cardinality == "one_to_many"
        assert rel.deprecated is False
        assert rel.metadata == {"priority": "HIGH"}
        assert rel.source_hints == ["deal_tracker_db"]


class TestReferenceEnumerationExtension:
    """Tests for ReferenceEnumeration extension fields."""

    def test_default_instantiation(self) -> None:
        """Extension fields must default strictly to None."""
        ref = ReferenceEnumeration(
            enum_uri="urn:ontology:enum:fund_status",
            target_reference_table="ref_fund_status",
            allowed_codes=["ACTIVE", "LIQUIDATING", "CLOSED"],
        )
        assert ref.enumeration_name is None
        assert ref.description is None
        assert ref.code_descriptions is None
        assert ref.deprecated is None
        assert ref.metadata is None

    def test_full_instantiation_with_code_descriptions(self) -> None:
        """ReferenceEnumeration with valid code_descriptions."""
        ref = ReferenceEnumeration(
            enum_uri="urn:ontology:enum:fund_status",
            target_reference_table="ref_fund_status",
            allowed_codes=["ACTIVE", "CLOSED"],
            enumeration_name="Fund Lifecycle Status",
            description="Operational lifecycle states of private equity funds.",
            code_descriptions={"ACTIVE": "Actively investing", "CLOSED": "Fully liquidated"},
            deprecated=False,
            metadata={"domain": "fund_admin"},
        )
        assert ref.enumeration_name == "Fund Lifecycle Status"
        assert ref.code_descriptions == {
            "ACTIVE": "Actively investing",
            "CLOSED": "Fully liquidated",
        }

    def test_dangling_enum_code_description_rejected(self) -> None:
        """Code descriptions containing codes not in allowed_codes must raise ValidationError."""
        with pytest.raises(ValidationError, match="DANGLING_ENUM_CODE_DESCRIPTION"):
            ReferenceEnumeration(
                enum_uri="urn:ontology:enum:fund_status",
                target_reference_table="ref_fund_status",
                allowed_codes=["ACTIVE", "CLOSED"],
                code_descriptions={"ACTIVE": "Active", "UNKNOWN_STATUS": "Invalid"},
            )


class TestMetadataJsonValidation:
    """Tests for JSON metadata validation (MAJ-01)."""

    def test_valid_json_metadata_types(self) -> None:
        """Valid JSON types (string, int, float, bool, list, dict, None) are accepted."""
        valid_meta = {
            "str_key": "hello",
            "int_key": 42,
            "float_key": 3.14159,
            "bool_key": True,
            "null_key": None,
            "list_key": ["a", 1, 2.5],
            "nested_dict": {"sub": "val"},
        }
        attr = ConceptAttribute(attribute_name="amount", metadata=valid_meta)
        assert attr.metadata == valid_meta

    @pytest.mark.parametrize("bad_float", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_float_rejected_in_metadata(self, bad_float: float) -> None:
        """NaN and Inf floats in metadata must raise ValidationError."""
        with pytest.raises(ValidationError, match="Non-finite float"):
            ConceptAttribute(attribute_name="amount", metadata={"score": bad_float})

    def test_non_finite_float_in_nested_metadata_rejected(self) -> None:
        """NaN float in nested structure must raise ValidationError."""
        with pytest.raises(ValidationError, match="Non-finite float"):
            ConceptAttribute(attribute_name="amount", metadata={"nested": {"bad": float("nan")}})


class TestConceptHierarchyValidation:
    """Tests for ontology-level concept hierarchy validation (MAJ-03)."""

    def test_valid_hierarchy(self) -> None:
        """Valid tree hierarchy passes cleanly."""
        parent = CanonicalBusinessConcept(concept_uri="urn:ontology:entity:asset")
        child = CanonicalBusinessConcept(
            concept_uri="urn:ontology:entity:equity",
            parent_concept_uri="urn:ontology:entity:asset",
        )
        grandchild = CanonicalBusinessConcept(
            concept_uri="urn:ontology:entity:common_stock",
            parent_concept_uri="urn:ontology:entity:equity",
        )
        ont = GraphAOntology(canonical_business_concepts=[parent, child, grandchild])
        assert len(ont.canonical_business_concepts) == 3

    def test_self_referential_parent_rejected(self) -> None:
        """Concept being its own parent must raise SELF_REFERENTIAL_PARENT."""
        self_parent = CanonicalBusinessConcept(
            concept_uri="urn:ontology:entity:fund",
            parent_concept_uri="urn:ontology:entity:fund",
        )
        with pytest.raises(ValidationError, match="SELF_REFERENTIAL_PARENT"):
            GraphAOntology(canonical_business_concepts=[self_parent])

    def test_dangling_parent_concept_rejected(self) -> None:
        """Parent concept not present in ontology must raise DANGLING_PARENT_CONCEPT."""
        orphan = CanonicalBusinessConcept(
            concept_uri="urn:ontology:entity:fund",
            parent_concept_uri="urn:ontology:entity:missing_parent",
        )
        with pytest.raises(ValidationError, match="DANGLING_PARENT_CONCEPT"):
            GraphAOntology(canonical_business_concepts=[orphan])

    def test_cyclic_concept_hierarchy_rejected(self) -> None:
        """Cyclic concept hierarchy A -> B -> A must raise CYCLIC_CONCEPT_HIERARCHY."""
        node_a = CanonicalBusinessConcept(
            concept_uri="urn:ontology:entity:a",
            parent_concept_uri="urn:ontology:entity:b",
        )
        node_b = CanonicalBusinessConcept(
            concept_uri="urn:ontology:entity:b",
            parent_concept_uri="urn:ontology:entity:a",
        )
        with pytest.raises(ValidationError, match="CYCLIC_CONCEPT_HIERARCHY"):
            GraphAOntology(canonical_business_concepts=[node_a, node_b])

    def test_three_node_cycle_rejected(self) -> None:
        """3-node cycle A -> B -> C -> A must raise CYCLIC_CONCEPT_HIERARCHY."""
        node_a = CanonicalBusinessConcept(
            concept_uri="urn:ontology:entity:a",
            parent_concept_uri="urn:ontology:entity:b",
        )
        node_b = CanonicalBusinessConcept(
            concept_uri="urn:ontology:entity:b",
            parent_concept_uri="urn:ontology:entity:c",
        )
        node_c = CanonicalBusinessConcept(
            concept_uri="urn:ontology:entity:c",
            parent_concept_uri="urn:ontology:entity:a",
        )
        with pytest.raises(ValidationError, match="CYCLIC_CONCEPT_HIERARCHY"):
            GraphAOntology(canonical_business_concepts=[node_a, node_b, node_c])

    def test_duplicate_concept_uri_rejected(self) -> None:
        """Duplicate concept URIs in ontology must raise DUPLICATE_CONCEPT_URI."""
        c1 = CanonicalBusinessConcept(concept_uri="urn:ontology:entity:fund")
        c2 = CanonicalBusinessConcept(concept_uri="urn:ontology:entity:fund")
        with pytest.raises(ValidationError, match="DUPLICATE_CONCEPT_URI"):
            GraphAOntology(canonical_business_concepts=[c1, c2])


class TestGraphAOntologyExtension:
    """Tests for GraphAOntology extension fields and hash preservation."""

    def test_default_instantiation(self) -> None:
        """Ontology extension fields must default to None."""
        cbc = CanonicalBusinessConcept(concept_uri="urn:ontology:entity:customer")
        ont = GraphAOntology(canonical_business_concepts=[cbc])
        assert ont.description is None
        assert ont.metadata is None

    def test_version_consistency_check(self) -> None:
        """Version and ontology_version must match if both are specified."""
        cbc = CanonicalBusinessConcept(concept_uri="urn:ontology:entity:customer")
        # Matching versions succeed
        ont = GraphAOntology(
            canonical_business_concepts=[cbc],
            ontology_version="1.0.0",
            version="1.0.0",
        )
        assert ont.ontology_version == "1.0.0"

        # Mismatched versions fail
        with pytest.raises(ValidationError, match="VERSION_CONSISTENCY_MISMATCH"):
            GraphAOntology(
                canonical_business_concepts=[cbc],
                ontology_version="1.0.0",
                version="2.0.0",
            )

    def test_pe_ontology_yaml_loading_and_hash_stability(self) -> None:
        """Certified PE Graph A YAML fixture must deserialize cleanly and preserve exact SHA-256."""
        ontology = validate_graph_a(PE_FIXTURE_PATH)
        assert isinstance(ontology, GraphAOntology)

        # Assert all extension fields on deserialized legacy ontology evaluate to None
        assert ontology.description is None
        assert ontology.metadata is None
        for cbc in ontology.canonical_business_concepts:
            assert cbc.parent_concept_uri is None
            assert cbc.attributes is None
            assert cbc.synonyms is None
            assert cbc.external_mappings is None
            assert cbc.deprecated is None
            assert cbc.metadata is None
            assert cbc.source_hints is None
        for rel in ontology.canonical_relationships:
            assert rel.description is None
            assert rel.cardinality is None
            assert rel.deprecated is None
            assert rel.metadata is None
            assert rel.source_hints is None
        for enum in ontology.reference_enumerations:
            assert enum.enumeration_name is None
            assert enum.description is None
            assert enum.code_descriptions is None
            assert enum.deprecated is None
            assert enum.metadata is None

        # Verify bit-for-bit SHA-256 hash invariance
        computed_sha256 = compute_ontology_sha256(ontology)
        expected_sha256 = "1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81"
        assert computed_sha256 == expected_sha256

    def test_model_dump_exclude_none_strips_extensions(self) -> None:
        """model_dump(exclude_none=True) strips all unpopulated extension fields."""
        cbc = CanonicalBusinessConcept(concept_uri="urn:ontology:entity:customer")
        dumped = cbc.model_dump(mode="json", exclude_none=True)
        assert "parent_concept_uri" not in dumped
        assert "attributes" not in dumped
        assert "synonyms" not in dumped
        assert "external_mappings" not in dumped
        assert "deprecated" not in dumped
        assert "metadata" not in dumped
        assert "source_hints" not in dumped
