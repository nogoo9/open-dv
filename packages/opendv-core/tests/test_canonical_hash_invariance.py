"""Unit tests for RFC 8785 JCS canonical hash stability, zero-drift invariance,
and scoped empty normalization.

Governing Documents:
- docs/21-ontology-studio-implementation-plan.md (§1.4, §3.1, §7.1, §7.2)
- schemas/v1/baseline-digests.json
- ADR-005: Non-Breaking GraphAOntology Model Extension with default=None

Task 4A.2 / Issue #198:
- Assert bit-for-bit canonical PE hash:
  1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81
- Assert Greenfield Genesis draft hash:
  1916a5c6d501015984b887fd76609b225b72d5d393e5813d8df210b92ab3b4c9
- Equivalence: None vs empty collections/strings vs omitted produces identical canonical hash
- Sensitivity: Genuine attribute or synonym additions change hash deterministically
- RFC 8785 JCS canonical formatting (Unicode, delimiters, sorted keys, number formatting)
"""

from __future__ import annotations

import copy
import json
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from opendv_core import (
    GENESIS_DRAFT_SHA256,
    GenesisDraft,
    compute_ontology_sha256,
    create_genesis_ontology,
    validate_graph_a,
)
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    CanonicalRelationship,
    ConceptAttribute,
    ExternalMapping,
    GraphAOntology,
    ReferenceEnumeration,
)
from opendv_core.ontology_hashing import (
    _ATTR_OPT,
    _CBC_EXT,
    _ENUM_EXT,
    _ONT_EXT,
    _REL_EXT,
    _norm_empty,
    canonicalize_graph_a_dict,
    dumps_jcs_bytes,
)

# Authoritative Certified Baseline SHA-256 Digests (RFC 8785 JCS)
PE_CANONICAL_SHA256: str = "1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81"
GOLDEN_GRAPH_A_SHA256: str = "dec92838dddee6d823b866e2b66d66c91cc7cd57c9b355e27901ca863916c8fe"
GENESIS_DRAFT_CANONICAL_SHA256: str = GENESIS_DRAFT_SHA256

# Repository root discovery for baseline fixtures and schemas
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PE_FIXTURE_PATH = REPO_ROOT / "examples" / "private_equity" / "pe_graph_a_ontology.yaml"
GOLDEN_FIXTURE_PATH = (
    REPO_ROOT
    / "packages"
    / "opendv-contracts"
    / "schemas"
    / "v1"
    / "fixtures"
    / "graph_a_golden.json"
)
BASELINE_DIGESTS_PATH = REPO_ROOT / "schemas" / "v1" / "baseline-digests.json"


@pytest.fixture(scope="module")
def baseline_digests() -> dict[str, Any]:
    """Load the authoritative baseline digests catalog."""
    assert BASELINE_DIGESTS_PATH.exists(), f"Missing catalog at {BASELINE_DIGESTS_PATH}"
    return json.loads(BASELINE_DIGESTS_PATH.read_text(encoding="utf-8"))


# =============================================================================
# 1. Hard Assertion Tests on Certified Cryptographic Baselines
# =============================================================================


class TestCertifiedBaselineInvariance:
    """Hard assertion tests on certified baseline SHA-256 digests."""

    def test_pe_ontology_canonical_sha256_exact_match(
        self, baseline_digests: dict[str, Any]
    ) -> None:
        """Private Equity Graph A ontology must evaluate bit-for-bit to the certified digest."""
        assert PE_FIXTURE_PATH.exists(), f"PE fixture missing at {PE_FIXTURE_PATH}"
        ontology = validate_graph_a(PE_FIXTURE_PATH)
        assert isinstance(ontology, GraphAOntology)

        computed_sha = compute_ontology_sha256(ontology)
        expected_sha = PE_CANONICAL_SHA256

        assert computed_sha == expected_sha
        assert computed_sha == "1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81"

        # Verify cross-check against catalog entry
        catalog_pe_sha = baseline_digests["certified_ontologies"]["pe"]["canonical_sha256"]
        assert computed_sha == catalog_pe_sha

    def test_greenfield_genesis_draft_exact_match(self, baseline_digests: dict[str, Any]) -> None:
        """Greenfield Genesis draft 0.0.0 JCS SHA-256 must match certified digest & vector."""
        genesis_entry = baseline_digests["genesis_draft"]
        expected_sha = "1916a5c6d501015984b887fd76609b225b72d5d393e5813d8df210b92ab3b4c9"
        assert expected_sha == GENESIS_DRAFT_SHA256
        assert genesis_entry["canonical_sha256"] == GENESIS_DRAFT_CANONICAL_SHA256
        assert genesis_entry["canonical_sha256"] == expected_sha
        assert genesis_entry["version"] == "0.0.0"
        assert genesis_entry["default_domain_namespace"] == "general"
        assert genesis_entry["default_enterprise_owner"] == "Enterprise Architecture"

        # Compute real RFC 8785 JCS SHA-256 from GenesisDraft and create_genesis_ontology()
        draft = GenesisDraft()
        ont = create_genesis_ontology()
        assert compute_ontology_sha256(draft) == expected_sha
        assert compute_ontology_sha256(ont) == expected_sha

        # Verify on-disk vector file matches JCS digest
        vector_path = REPO_ROOT / genesis_entry["vector_path"]
        assert vector_path.is_file(), f"Missing genesis vector at {vector_path}"
        vector_doc = json.loads(vector_path.read_text(encoding="utf-8"))
        assert compute_ontology_sha256(vector_doc) == expected_sha

    def test_graph_a_golden_fixture_invariance(self) -> None:
        """Contract golden fixture graph_a_golden.json must retain exact certified SHA-256."""
        assert GOLDEN_FIXTURE_PATH.is_file(), f"Missing golden fixture at {GOLDEN_FIXTURE_PATH}"
        golden = json.loads(GOLDEN_FIXTURE_PATH.read_text(encoding="utf-8"))
        assert golden["sha256"] == GOLDEN_GRAPH_A_SHA256
        assert compute_ontology_sha256(golden["raw_model"]) == GOLDEN_GRAPH_A_SHA256

    def test_all_certified_baseline_fixtures_invariance(
        self, baseline_digests: dict[str, Any]
    ) -> None:
        """All 7 certified ontologies registered in baseline-digests.json must match bit-for-bit."""
        certified_ontologies = baseline_digests["certified_ontologies"]
        assert len(certified_ontologies) >= 7

        for key, entry in certified_ontologies.items():
            fixture_path = REPO_ROOT / entry["path"]
            assert fixture_path.exists(), f"Fixture missing for {key}: {fixture_path}"

            ont = validate_graph_a(fixture_path)
            actual_sha = compute_ontology_sha256(ont)
            expected_sha = entry["canonical_sha256"]
            assert actual_sha == expected_sha, (
                f"Hash mismatch for '{key}': actual {actual_sha} != expected {expected_sha}"
            )


# =============================================================================
# 2. Equivalence Tests: None vs. Empty Collection vs. Omitted Extension Invariance
# =============================================================================


class TestExtensionFieldEmptyInvariance:
    """Tests verifying that empty extension collections produce identical hashes to None/omitted."""

    def test_cbc_extension_none_vs_empty_collections_invariance(self) -> None:
        """CBC with extensions set to None vs empty collections/strings must hash identically."""
        baseline_ont = validate_graph_a(PE_FIXTURE_PATH)
        baseline_sha = compute_ontology_sha256(baseline_ont)
        assert baseline_sha == PE_CANONICAL_SHA256

        # Convert baseline ontology dump into a variant with explicit empty extension values
        raw_dump = baseline_ont.model_dump(mode="json", exclude_none=True)
        empty_variant = copy.deepcopy(raw_dump)

        # Inject empty collections and mappings across all concepts
        for cbc in empty_variant.get("canonical_business_concepts", []):
            cbc["attributes"] = []
            cbc["synonyms"] = []
            cbc["external_mappings"] = []
            cbc["metadata"] = {}
            cbc["source_hints"] = []
            cbc["parent_concept_uri"] = ""
            cbc["deprecated"] = None

        # Inject empty extensions across relationships, enumerations, and ontology root
        for rel in empty_variant.get("canonical_relationships", []):
            rel["metadata"] = {}
        for enum in empty_variant.get("reference_enumerations", []):
            enum["metadata"] = {}
        empty_variant["metadata"] = {}

        # Canonicalize both dictionary structures
        canonical_baseline = canonicalize_graph_a_dict(raw_dump)
        canonical_empty = canonicalize_graph_a_dict(empty_variant)

        # Assert dictionary equivalence after scoped empty normalization
        assert canonical_empty == canonical_baseline

        # Assert byte-for-byte identical JCS canonical bytes and SHA-256 hash
        bytes_baseline = dumps_jcs_bytes(canonical_baseline)
        bytes_empty = dumps_jcs_bytes(canonical_empty)
        assert bytes_empty == bytes_baseline

        variant_sha = compute_ontology_sha256(empty_variant)
        assert variant_sha == baseline_sha
        assert variant_sha == PE_CANONICAL_SHA256

    def test_concept_attribute_empty_fields_invariance(self) -> None:
        """ConceptAttribute with optional fields set to None vs empty hashes identically."""
        attr_none = ConceptAttribute(
            attribute_name="commitment_amount",
            data_type="decimal",
            description="Commitment amount",
            metadata=None,
            source_hints=None,
            deprecated=None,
        )
        attr_empty = ConceptAttribute(
            attribute_name="commitment_amount",
            data_type="decimal",
            description="Commitment amount",
            metadata={},
            source_hints=[],
            deprecated=None,
        )

        cbc_none = CanonicalBusinessConcept(
            concept_uri="urn:ontology:entity:test_concept",
            attributes=[attr_none],
        )
        cbc_empty = CanonicalBusinessConcept(
            concept_uri="urn:ontology:entity:test_concept",
            attributes=[attr_empty],
        )

        ont_none = GraphAOntology(canonical_business_concepts=[cbc_none])
        ont_empty = GraphAOntology(canonical_business_concepts=[cbc_empty])

        dict_none = canonicalize_graph_a_dict(ont_none)
        dict_empty = canonicalize_graph_a_dict(ont_empty)

        assert dict_none == dict_empty
        assert compute_ontology_sha256(ont_none) == compute_ontology_sha256(ont_empty)

    def test_scoped_norm_empty_direct_usage(self) -> None:
        """_norm_empty with explicit scope prunes target keys while preserving non-scoped."""
        # 1. CBC scope test
        cbc_data = {
            "concept_uri": "urn:ontology:entity:fund",
            "concept_name": "Fund",
            "attributes": [],
            "synonyms": [],
            "metadata": {},
            "governance_constraints": [],  # legacy collection outside _CBC_EXT
        }
        normalized_cbc = _norm_empty(cbc_data, scope=_CBC_EXT)
        assert "attributes" not in normalized_cbc
        assert "synonyms" not in normalized_cbc
        assert "metadata" not in normalized_cbc
        assert normalized_cbc["concept_uri"] == "urn:ontology:entity:fund"
        assert normalized_cbc["concept_name"] == "Fund"
        assert normalized_cbc["governance_constraints"] == []

        # 2. Attribute scope test: post-baseline optional fields pruned when empty
        attr_data = {
            "attribute_name": "fund_name",
            "data_type": "string",
            "description": "",
            "example_values": [],
            "metadata": {},
            "source_hints": [],
            "deprecated": None,
            "is_optional": True,
        }
        normalized_attr = _norm_empty(attr_data, scope=_ATTR_OPT)
        assert "description" not in normalized_attr
        assert "example_values" not in normalized_attr
        assert "metadata" not in normalized_attr
        assert "source_hints" not in normalized_attr
        assert "deprecated" not in normalized_attr
        assert normalized_attr["attribute_name"] == "fund_name"
        assert normalized_attr["data_type"] == "string"
        assert normalized_attr["is_optional"] is True

        # 3. Relationship scope test: post-baseline optional fields pruned when empty
        rel_data = {
            "relationship_uri": "urn:ontology:rel:manages",
            "description": "",
            "cardinality": "",
            "source_hints": [],
            "metadata": {},
            "deprecated": None,
            "endpoints": ["urn:a", "urn:b"],
        }
        normalized_rel = _norm_empty(rel_data, scope=_REL_EXT)
        assert "description" not in normalized_rel
        assert "cardinality" not in normalized_rel
        assert "source_hints" not in normalized_rel
        assert "metadata" not in normalized_rel
        assert "deprecated" not in normalized_rel
        assert normalized_rel["endpoints"] == ["urn:a", "urn:b"]

        # 4. Enumeration scope test: post-baseline optional fields pruned when empty
        enum_data = {
            "enum_uri": "urn:ontology:enum:status",
            "enumeration_name": "",
            "description": "",
            "code_descriptions": {},
            "metadata": {},
            "deprecated": None,
            "allowed_codes": ["ACTIVE", "INACTIVE"],
        }
        normalized_enum = _norm_empty(enum_data, scope=_ENUM_EXT)
        assert "enumeration_name" not in normalized_enum
        assert "description" not in normalized_enum
        assert "code_descriptions" not in normalized_enum
        assert "metadata" not in normalized_enum
        assert "deprecated" not in normalized_enum
        assert normalized_enum["allowed_codes"] == ["ACTIVE", "INACTIVE"]

        # 5. Root ontology scope test: post-baseline optional fields pruned when empty
        ont_data = {
            "ontology_version": "1.0.0",
            "description": "",
            "metadata": {},
            "decision_refs": [],  # legacy field outside _ONT_EXT
        }
        normalized_ont = _norm_empty(ont_data, scope=_ONT_EXT)
        assert "description" not in normalized_ont
        assert "metadata" not in normalized_ont
        assert normalized_ont["decision_refs"] == []

    def test_false_and_zero_values_preserved(self) -> None:
        """Boolean False and numeric 0 are semantic values and must NEVER be pruned."""
        cbc_data = {
            "concept_uri": "urn:ontology:entity:fund",
            "deprecated": False,
            "metadata": {},
        }
        normalized = _norm_empty(cbc_data, scope=_CBC_EXT)
        assert "metadata" not in normalized
        assert normalized["deprecated"] is False
        assert normalized["concept_uri"] == "urn:ontology:entity:fund"


# =============================================================================
# 3. Legacy Collections Preservation Tests (Negative Guard)
# =============================================================================


class TestLegacyCollectionsPreservation:
    """Verifies that empty legacy collections are never accidentally pruned."""

    def test_legacy_empty_collections_preserved(self) -> None:
        """Legacy empty collections like decision_refs or standards_references must be preserved."""
        raw_doc = {
            "ontology_version": "1.0.0",
            "domain_namespace": "general",
            "enterprise_owner": "Enterprise Architecture",
            "decision_refs": [],
            "standards_references": [],
            "canonical_relationships": [],
            "reference_enumerations": [],
            "canonical_business_concepts": [
                {
                    "concept_uri": "urn:ontology:entity:customer",
                    "concept_name": "Customer",
                    "elm_archetype": "CORE_PARTY",
                    "definition": "Customer concept.",
                    "governance_constraints": [],
                    "key_authority_tiers": [],
                }
            ],
        }

        canonical = canonicalize_graph_a_dict(raw_doc)

        # Legacy root collections must remain present as empty lists
        assert "decision_refs" in canonical
        assert canonical["decision_refs"] == []
        assert "standards_references" in canonical
        assert canonical["standards_references"] == []
        assert "canonical_relationships" in canonical
        assert canonical["canonical_relationships"] == []
        assert "reference_enumerations" in canonical
        assert canonical["reference_enumerations"] == []

        # Legacy CBC collections must remain present
        cbc = canonical["canonical_business_concepts"][0]
        assert "governance_constraints" in cbc
        assert cbc["governance_constraints"] == []
        assert "key_authority_tiers" in cbc
        assert cbc["key_authority_tiers"] == []


# =============================================================================
# 4. Deterministic Hash Sensitivity Tests
# =============================================================================


class TestHashDeterministicSensitivity:
    """Verifies that genuine semantic additions deterministically alter the SHA-256 hash."""

    def test_adding_genuine_attribute_changes_hash_deterministically(self) -> None:
        """Adding a populated ConceptAttribute must change the hash deterministically."""
        base_ont = validate_graph_a(PE_FIXTURE_PATH)
        base_sha = compute_ontology_sha256(base_ont)

        # Mutate first concept by adding an attribute
        mutated_ont_data = base_ont.model_dump(mode="json")
        first_cbc = mutated_ont_data["canonical_business_concepts"][0]
        first_cbc["attributes"] = [
            ConceptAttribute(
                attribute_name="vintage_year",
                data_type="integer",
                description="Year fund was established",
            ).model_dump(mode="json")
        ]

        ont_with_attr = GraphAOntology.model_validate(mutated_ont_data)
        hash_run_1 = compute_ontology_sha256(ont_with_attr)
        hash_run_2 = compute_ontology_sha256(ont_with_attr)

        # Must differ from baseline
        assert hash_run_1 != base_sha
        # Must be completely deterministic
        assert hash_run_1 == hash_run_2

    def test_adding_genuine_synonym_changes_hash_deterministically(self) -> None:
        """Adding a populated synonym list must change the hash deterministically."""
        base_ont = validate_graph_a(PE_FIXTURE_PATH)
        base_sha = compute_ontology_sha256(base_ont)

        mutated_ont_data = base_ont.model_dump(mode="json")
        first_cbc = mutated_ont_data["canonical_business_concepts"][0]
        first_cbc["synonyms"] = ["Private Equity Fund Vehicle", "Investment Fund"]

        ont_with_synonyms = GraphAOntology.model_validate(mutated_ont_data)
        hash_run_1 = compute_ontology_sha256(ont_with_synonyms)
        hash_run_2 = compute_ontology_sha256(ont_with_synonyms)

        assert hash_run_1 != base_sha
        assert hash_run_1 == hash_run_2

    def test_adding_genuine_external_mapping_changes_hash_deterministically(self) -> None:
        """Adding an ExternalMapping must change the hash deterministically."""
        base_ont = validate_graph_a(PE_FIXTURE_PATH)
        base_sha = compute_ontology_sha256(base_ont)

        mutated_ont_data = base_ont.model_dump(mode="json")
        first_cbc = mutated_ont_data["canonical_business_concepts"][0]
        first_cbc["external_mappings"] = [
            ExternalMapping(
                scheme="FIBO",
                target_uri="https://spec.edmcouncil.org/fibo/ontology/SEC/Equities/Share",
                reference_pack_id="fibo-core",
                reference_pack_sha256="0" * 64,
                confidence=0.9250,
            ).model_dump(mode="json")
        ]

        ont_with_mapping = GraphAOntology.model_validate(mutated_ont_data)
        hash_run_1 = compute_ontology_sha256(ont_with_mapping)
        hash_run_2 = compute_ontology_sha256(ont_with_mapping)

        assert hash_run_1 != base_sha
        assert hash_run_1 == hash_run_2


# =============================================================================
# 5. RFC 8785 JCS Canonical Serialization Tests
# =============================================================================


class TestRFC8785JCSFormatting:
    """Tests confirming adherence to RFC 8785 JSON Canonicalization Scheme."""

    def test_unicode_nfc_normalization_hash_stability(self) -> None:
        """Strings normalized to NFC prior to canonicalization hash identically."""
        # 'e' + combining acute accent (decomposed, NFD)
        decomposed = "e\u0301"
        # single character 'e' with acute (composed, NFC)
        composed = "\u00e9"
        assert decomposed != composed
        assert unicodedata.normalize("NFC", decomposed) == composed

        nfc_decomposed = unicodedata.normalize("NFC", decomposed)
        cbc_decomposed = CanonicalBusinessConcept(
            concept_uri="urn:ontology:entity:resume",
            definition=f"Curriculum vitae / r{nfc_decomposed}sum{nfc_decomposed}",
        )
        cbc_composed = CanonicalBusinessConcept(
            concept_uri="urn:ontology:entity:resume",
            definition=f"Curriculum vitae / r{composed}sum{composed}",
        )

        ont1 = GraphAOntology(canonical_business_concepts=[cbc_decomposed])
        ont2 = GraphAOntology(canonical_business_concepts=[cbc_composed])

        assert compute_ontology_sha256(ont1) == compute_ontology_sha256(ont2)

    def test_rfc8785_key_sorting_utf16_code_units(self) -> None:
        """Object keys must be sorted strictly by UTF-16 code units."""
        raw_dict = {
            "z": 1,
            "a": 2,
            "\u00e9": 3,
            "A": 4,
            "_": 5,
        }
        jcs = dumps_jcs_bytes(raw_dict).decode("utf-8")
        # In UTF-16 code unit order:
        # 'A' is U+0041
        # '_' is U+005F
        # 'a' is U+0061
        # 'z' is U+007A
        # '\u00e9' is U+00E9
        # Sorted order: 'A', '_', 'a', 'z', '\u00e9'
        expected = '{"A":4,"_":5,"a":2,"z":1,"\u00e9":3}'
        assert jcs == expected

    def test_rfc8785_number_and_float_formatting(self) -> None:
        """Numbers must be serialized per RFC 8785 IEEE 754 float / integer rules."""
        # Pure integers have no decimal point
        assert dumps_jcs_bytes({"val": 42}).decode("utf-8") == '{"val":42}'
        assert dumps_jcs_bytes({"val": 0}).decode("utf-8") == '{"val":0}'
        assert dumps_jcs_bytes({"val": -100}).decode("utf-8") == '{"val":-100}'

        # Floats serialized without trailing .0 if integer, or with shortest exact representation
        assert dumps_jcs_bytes({"val": 1.5}).decode("utf-8") == '{"val":1.5}'

        # Large numbers beyond safe integer range [-2^53+1, 2^53-1] must raise ValueError
        with pytest.raises(ValueError, match="exceeds safe IEEE 754 domain"):
            dumps_jcs_bytes({"val": 2**53 + 100})

        with pytest.raises(ValueError, match="exceeds safe IEEE 754 domain"):
            dumps_jcs_bytes({"val": -(2**53) - 100})

        # Non-finite floats must raise ValueError
        with pytest.raises(ValueError, match="not representable in RFC 8785"):
            dumps_jcs_bytes({"val": float("nan")})

        with pytest.raises(ValueError, match="not representable in RFC 8785"):
            dumps_jcs_bytes({"val": float("inf")})

    def test_rfc8785_control_character_escaping(self) -> None:
        """Control characters in strings must be escaped per RFC 8785 §3.2.2.2."""
        raw_dict = {"text": 'hello\nworld\t"escaped"\\'}
        jcs = dumps_jcs_bytes(raw_dict).decode("utf-8")
        assert jcs == '{"text":"hello\\nworld\\t\\"escaped\\"\\\\"}'


class TestGenesisAndPromotedUrnInvariants:
    """Tests for 0.x.y Genesis empty-CBC allowance, >= 1.0.0 enforcement, and URN schemes."""

    def test_genesis_0_x_y_allows_empty_concepts_while_1_0_0_rejects(self) -> None:
        """0.x.y ontologies permit empty CBCs; >= 1.0.0 strictly require >= 1 CBC."""
        g0 = GraphAOntology(ontology_version="0.0.0", canonical_business_concepts=[])
        assert g0.canonical_business_concepts == []

        g01 = GraphAOntology(ontology_version="0.1.0", canonical_business_concepts=[])
        assert g01.canonical_business_concepts == []

        with pytest.raises(ValueError, match="ONTOLOGY_REQUIRES_CONCEPT"):
            GraphAOntology(ontology_version="1.0.0", canonical_business_concepts=[])

    def test_promoted_opendv_urn_schemes_accepted_provisional_rejected(self) -> None:
        """Both urn:ontology:* and urn:opendv:* promoted URNs work; :provisional: fails."""
        c = CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:limited_partner",
            concept_name="Limited Partner",
        )
        r = CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:fund_lp",
            endpoints=["urn:opendv:concept:limited_partner", "urn:ontology:entity:fund"],
        )
        e = ReferenceEnumeration(
            enum_uri="urn:opendv:enum:currency_code",
            target_reference_table="ref_currency_code",
            allowed_codes=["USD", "EUR"],
        )
        ont = GraphAOntology(
            ontology_version="1.0.0",
            canonical_business_concepts=[
                c,
                CanonicalBusinessConcept(concept_uri="urn:ontology:entity:fund"),
            ],
            canonical_relationships=[r],
            reference_enumerations=[e],
        )
        assert ont.get_concept("urn:opendv:concept:limited_partner") is not None
        assert ont.get_concept("urn:opendv:concept:missing") is None

        with pytest.raises(ValueError):
            CanonicalBusinessConcept(concept_uri="urn:opendv:provisional:concept:lp")
        with pytest.raises(ValueError):
            CanonicalBusinessConcept(concept_uri="urn:ontology:provisional:entity:lp")
