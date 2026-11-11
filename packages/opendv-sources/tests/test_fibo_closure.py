"""Comprehensive automated test suite for Transitive subClassOf Closure Resolver (Task 4C.3 / #207).

Tests:
1. Real FIBO concept closure resolution via FiboReferenceOntologyLoader.
2. Grounding Guard: score >= 0.6500 resolves superclasses; score < 0.6500 is strictly blocked.
3. Classification Invariant: all inferred superclasses are strictly tagged skos:broadMatch,
   inherit direct mapping confidence, and cannot satisfy standalone Path A grounding.
4. Cycle Detection & Graph Safety: synthetic cycles (self-loop, 2-cycle, 3-cycle),
   clean termination without infinite loops, optional TaxonomyCycleError.
5. Multi-inheritance diamond patterns & shortest depth deduplication.
6. Configurable max_depth truncation and invalid depth rejection.
7. Zero rdflib runtime import invariant (subprocess sys.modules probe).
8. Performance / latency benchmark: closure resolution completes in sub-millisecond to < 5ms.
"""

from __future__ import annotations

import inspect
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import opendv_sources.fibo.closure as closure_module
from opendv_contracts.errors import ErrorCode
from opendv_core.models.graph_a import ExternalMapping
from opendv_sources.exceptions import SourceNotFoundError
from opendv_sources.fibo import (
    BROAD_MATCH_PREDICATE,
    DEFAULT_PROVENANCE,
    GROUNDING_THRESHOLD,
    FiboReferenceOntologyLoader,
    SubclassClosureMatch,
    SubclassClosureResolver,
    TaxonomyCycleError,
    UngroundedSeedMappingError,
    can_satisfy_path_a_grounding,
    get_default_fibo_loader,
    resolve_subclass_closure,
    validate_closure_grounding_eligibility,
)

# Known canonical URIs from FIBO core pack
JOINT_STOCK_COMPANY = (
    "https://spec.edmcouncil.org/fibo/ontology/BE/Corporations/Corporations/JointStockCompany"
)
STOCK_CORPORATION = (
    "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/CorporateBodies/StockCorporation"
)
BUSINESS_ENTITY = (
    "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/BusinessEntity"
)
FORMAL_ORGANIZATION = "https://spec.edmcouncil.org/fibo/ontology/FND/Organizations/FormalOrganizations/FormalOrganization"
ORGANIZATION = (
    "https://spec.edmcouncil.org/fibo/ontology/FND/Organizations/Organizations/Organization"
)
BANKRUPTCY = "https://spec.edmcouncil.org/fibo/ontology/FBC/DebtAndEquities/CreditEvents/Bankruptcy"


class SyntheticOntologyAdapter:
    """Lightweight test adapter mimicking reference ontology with customizable taxonomy."""

    def __init__(
        self,
        taxonomy: dict[str, list[str]],
        metadata: dict[str, dict[str, Any]] | None = None,
        pack_id: str = "synthetic-test-pack",
        pack_sha256: str = "a" * 64,
    ) -> None:
        self.taxonomy = taxonomy
        self.metadata = metadata or {}
        self.pack_id = pack_id
        self.pack_sha256 = pack_sha256

    def get_direct_superclasses(self, uri: str) -> list[str]:
        return list(self.taxonomy.get(uri, []))

    def lookup_by_uri(self, uri: str) -> dict[str, Any] | None:
        if uri not in self.taxonomy and uri not in self.metadata:
            return None
        info = self.metadata.get(uri, {})
        return {
            "uri": uri,
            "label": info.get("label", uri.split("/")[-1]),
            "definition": info.get("definition"),
            "sub_class_of": list(self.taxonomy.get(uri, [])),
        }


@pytest.fixture
def default_loader() -> FiboReferenceOntologyLoader:
    """Fixture providing initialized default FIBO reference ontology loader."""
    return get_default_fibo_loader()


# =============================================================================
# 1. Real FIBO Concepts Transitive Closure
# =============================================================================


def test_real_fibo_joint_stock_company_closure(
    default_loader: FiboReferenceOntologyLoader,
) -> None:
    """Verify transitive closure for JointStockCompany over real FIBO pack."""
    matches = resolve_subclass_closure(
        default_loader,
        JOINT_STOCK_COMPANY,
        direct_seed_score=0.8500,
        max_depth=32,
    )

    assert len(matches) >= 3

    # BusinessEntity should be direct parent (depth = 1)
    be_match = next((m for m in matches if m.uri == BUSINESS_ENTITY), None)
    assert be_match is not None
    assert be_match.depth == 1
    assert be_match.predicate == BROAD_MATCH_PREDICATE
    assert be_match.confidence == 0.8500
    assert be_match.path == (JOINT_STOCK_COMPANY, BUSINESS_ENTITY)
    assert be_match.reference_pack_id == "fibo-core"
    assert be_match.reference_pack_sha256 == default_loader.pack_sha256

    # FormalOrganization should be at depth = 2
    fo_match = next((m for m in matches if m.uri == FORMAL_ORGANIZATION), None)
    assert fo_match is not None
    assert fo_match.depth == 2
    assert fo_match.predicate == BROAD_MATCH_PREDICATE
    assert fo_match.confidence == 0.8500

    # Organization should be at depth = 3
    org_match = next((m for m in matches if m.uri == ORGANIZATION), None)
    assert org_match is not None
    assert org_match.depth == 3

    # Results must be strictly sorted by depth ascending, then uri ascending
    for i in range(len(matches) - 1):
        curr_m, next_m = matches[i], matches[i + 1]
        assert (curr_m.depth, curr_m.uri) <= (next_m.depth, next_m.uri)


def test_real_fibo_stock_corporation_closure(
    default_loader: FiboReferenceOntologyLoader,
) -> None:
    """Verify transitive closure for StockCorporation."""
    matches = resolve_subclass_closure(
        default_loader,
        STOCK_CORPORATION,
        direct_seed_score=Decimal("0.7250"),
    )
    assert len(matches) >= 2
    assert all(m.confidence == 0.7250 for m in matches)
    assert all(m.predicate == BROAD_MATCH_PREDICATE for m in matches)
    assert all(m.depth >= 1 for m in matches)


def test_real_fibo_root_concept_closure(
    default_loader: FiboReferenceOntologyLoader,
) -> None:
    """Verify that a concept with no superclasses in pack returns an empty closure."""
    matches = resolve_subclass_closure(
        default_loader,
        "https://www.omg.org/spec/Commons/PartiesAndSituations/Party",
        direct_seed_score=0.9000,
    )
    assert matches == []


# =============================================================================
# 2. Grounding Guard Invariants (score >= 0.6500)
# =============================================================================


def test_grounding_guard_exact_boundary(
    default_loader: FiboReferenceOntologyLoader,
) -> None:
    """Verify that direct score exactly equal to 0.6500 succeeds."""
    matches = resolve_subclass_closure(
        default_loader,
        JOINT_STOCK_COMPANY,
        direct_seed_score=0.6500,
    )
    assert len(matches) > 0
    assert all(m.confidence == 0.6500 for m in matches)


def test_grounding_guard_decimal_boundary(
    default_loader: FiboReferenceOntologyLoader,
) -> None:
    """Verify Decimal score boundary at 0.6500."""
    matches = resolve_subclass_closure(
        default_loader,
        JOINT_STOCK_COMPANY,
        direct_seed_score=GROUNDING_THRESHOLD,
    )
    assert len(matches) > 0


def test_grounding_guard_rejects_sub_threshold(
    default_loader: FiboReferenceOntologyLoader,
) -> None:
    """Verify that score < 0.6500 raises UngroundedSeedMappingError."""
    with pytest.raises(UngroundedSeedMappingError) as exc_info:
        resolve_subclass_closure(
            default_loader,
            JOINT_STOCK_COMPANY,
            direct_seed_score=0.6499,
        )

    err = exc_info.value
    assert err.exit_code == 4
    assert err.http_status == 422
    assert err.error_code == ErrorCode.SOURCE_LOAD_ERROR
    assert err.details.get("error_code_symbol") == "ONT_EVO_UNGROUNDED_CONCEPT"
    assert err.details.get("concept_uri") == JOINT_STOCK_COMPANY
    assert err.details.get("direct_seed_score") == 0.6499
    assert err.details.get("grounding_threshold") == 0.65


def test_grounding_guard_rejects_zero_score(
    default_loader: FiboReferenceOntologyLoader,
) -> None:
    """Verify that score = 0.0 raises UngroundedSeedMappingError."""
    with pytest.raises(UngroundedSeedMappingError):
        resolve_subclass_closure(
            default_loader,
            JOINT_STOCK_COMPANY,
            direct_seed_score=0.0,
        )


def test_grounding_guard_raise_on_ungrounded_false(
    default_loader: FiboReferenceOntologyLoader,
) -> None:
    """Verify that raise_on_ungrounded=False suppresses error and returns empty list."""
    matches = resolve_subclass_closure(
        default_loader,
        JOINT_STOCK_COMPANY,
        direct_seed_score=0.6499,
        raise_on_ungrounded=False,
    )
    assert matches == []


def test_score_rounding_quantization(
    default_loader: FiboReferenceOntologyLoader,
) -> None:
    """Verify that confidence score is quantized to 4 decimal places with round half-up."""
    # 0.64995 rounds up to 0.6500, satisfying threshold
    matches = resolve_subclass_closure(
        default_loader,
        JOINT_STOCK_COMPANY,
        direct_seed_score=0.64995,
    )
    assert len(matches) > 0
    assert matches[0].confidence == 0.6500

    # 0.64994 rounds down to 0.6499, failing threshold
    with pytest.raises(UngroundedSeedMappingError):
        resolve_subclass_closure(
            default_loader,
            JOINT_STOCK_COMPANY,
            direct_seed_score=0.64994,
        )


# =============================================================================
# 3. Classification Invariant & Path A Grounding Exclusion
# =============================================================================


def test_classification_is_strictly_skos_broad_match(
    default_loader: FiboReferenceOntologyLoader,
) -> None:
    """Verify all inferred superclasses are classified as skos:broadMatch."""
    matches = resolve_subclass_closure(
        default_loader,
        JOINT_STOCK_COMPANY,
        direct_seed_score=0.9500,
    )
    for m in matches:
        assert m.predicate == "skos:broadMatch"
        assert m.predicate != "skos:exactMatch"
        assert m.predicate != "skos:closeMatch"
        assert m.predicate != "rdfs:subClassOf"


def test_inferred_matches_cannot_satisfy_path_a_grounding(
    default_loader: FiboReferenceOntologyLoader,
) -> None:
    """Verify satisfies_path_a and is_standalone_grounding_eligible are strictly False."""
    matches = resolve_subclass_closure(
        default_loader,
        JOINT_STOCK_COMPANY,
        direct_seed_score=0.9000,
    )
    assert len(matches) > 0
    for m in matches:
        assert m.satisfies_path_a is False
        assert m.is_standalone_grounding_eligible is False

    # Validator check passes on legitimate closure matches
    validate_closure_grounding_eligibility(matches)


def test_predicate_path_a_grounding_checker() -> None:
    """Verify predicate helper function can_satisfy_path_a_grounding."""
    assert can_satisfy_path_a_grounding("skos:exactMatch") is True
    assert can_satisfy_path_a_grounding("exactMatch") is True
    assert can_satisfy_path_a_grounding("skos:closeMatch") is True
    assert can_satisfy_path_a_grounding("closeMatch") is True

    # broadMatch cannot satisfy Path A
    assert can_satisfy_path_a_grounding("skos:broadMatch") is False
    assert can_satisfy_path_a_grounding("broadMatch") is False
    assert can_satisfy_path_a_grounding("rdfs:subClassOf") is False
    assert can_satisfy_path_a_grounding(None) is False
    assert can_satisfy_path_a_grounding("") is False


def test_to_external_mapping_conversion(
    default_loader: FiboReferenceOntologyLoader,
) -> None:
    """Verify conversion of SubclassClosureMatch to ExternalMapping domain model."""
    matches = resolve_subclass_closure(
        default_loader,
        JOINT_STOCK_COMPANY,
        direct_seed_score=0.8800,
    )
    assert len(matches) > 0
    match = matches[0]

    ext_mapping = match.to_external_mapping(scheme="fibo")
    assert isinstance(ext_mapping, ExternalMapping)
    assert ext_mapping.scheme == "fibo"
    assert ext_mapping.target_uri == match.uri
    assert ext_mapping.confidence == match.confidence
    assert ext_mapping.predicate == "skos:broadMatch"
    assert ext_mapping.provenance == DEFAULT_PROVENANCE
    assert ext_mapping.pinned is False
    assert ext_mapping.reference_pack_id == "fibo-core"
    assert ext_mapping.reference_pack_sha256 == default_loader.pack_sha256


# =============================================================================
# 4. Cycle Detection & Graph Traversal Safety
# =============================================================================


def test_self_loop_graceful_handling() -> None:
    """Verify self-loop on seed concept terminates cleanly without infinite recursion."""
    # Seed A -> [A]
    adapter = SyntheticOntologyAdapter({"urn:test:A": ["urn:test:A"]})
    matches = resolve_subclass_closure(adapter, "urn:test:A", direct_seed_score=0.75)
    assert matches == []


def test_self_loop_strict_error() -> None:
    """Verify self-loop on seed raises TaxonomyCycleError when raise_on_cycle=True."""
    adapter = SyntheticOntologyAdapter({"urn:test:A": ["urn:test:A"]})
    with pytest.raises(TaxonomyCycleError) as exc_info:
        resolve_subclass_closure(
            adapter,
            "urn:test:A",
            direct_seed_score=0.75,
            raise_on_cycle=True,
        )
    assert exc_info.value.exit_code == 4
    assert exc_info.value.http_status == 422


def test_two_node_cycle_graceful_termination() -> None:
    """Verify 2-node circular inheritance (A -> B -> A) terminates cleanly."""
    adapter = SyntheticOntologyAdapter(
        {
            "urn:test:A": ["urn:test:B"],
            "urn:test:B": ["urn:test:A"],
        }
    )
    matches = resolve_subclass_closure(
        adapter,
        "urn:test:A",
        direct_seed_score=0.80,
        max_depth=32,
    )
    # B is recorded once at depth 1; traversal does not loop back to A
    assert len(matches) == 1
    assert matches[0].uri == "urn:test:B"
    assert matches[0].depth == 1


def test_three_node_cycle_strict_error() -> None:
    """Verify 3-node cycle (A -> B -> C -> A) raises TaxonomyCycleError under strict flag."""
    adapter = SyntheticOntologyAdapter(
        {
            "urn:test:A": ["urn:test:B"],
            "urn:test:B": ["urn:test:C"],
            "urn:test:C": ["urn:test:A"],
        }
    )
    with pytest.raises(TaxonomyCycleError) as exc_info:
        resolve_subclass_closure(
            adapter,
            "urn:test:A",
            direct_seed_score=0.80,
            raise_on_cycle=True,
        )
    assert "Taxonomic cycle detected" in str(exc_info.value)
    assert exc_info.value.details.get("error_code_symbol") == "HIERARCHICAL_CYCLE_DETECTED"


def test_indirect_sub_cycle_graceful_termination() -> None:
    """Verify cycle not including seed concept (A -> B -> C -> B) terminates cleanly."""
    adapter = SyntheticOntologyAdapter(
        {
            "urn:test:A": ["urn:test:B"],
            "urn:test:B": ["urn:test:C"],
            "urn:test:C": ["urn:test:B", "urn:test:D"],
            "urn:test:D": [],
        }
    )
    matches = resolve_subclass_closure(
        adapter,
        "urn:test:A",
        direct_seed_score=0.80,
        raise_on_cycle=False,
    )
    uris = {m.uri for m in matches}
    assert uris == {"urn:test:B", "urn:test:C", "urn:test:D"}


# =============================================================================
# 5. Multi-Inheritance Diamond Patterns & Shortest Depth Deduplication
# =============================================================================


def test_symmetric_diamond_inheritance() -> None:
    """Verify symmetric diamond pattern deduplicates node D and resolves E."""
    # A -> [B, C]
    # B -> [D]
    # C -> [D]
    # D -> [E]
    adapter = SyntheticOntologyAdapter(
        {
            "urn:test:A": ["urn:test:B", "urn:test:C"],
            "urn:test:B": ["urn:test:D"],
            "urn:test:C": ["urn:test:D"],
            "urn:test:D": ["urn:test:E"],
            "urn:test:E": [],
        }
    )

    matches = resolve_subclass_closure(adapter, "urn:test:A", direct_seed_score=0.80)
    match_dict = {m.uri: m for m in matches}

    assert len(match_dict) == 4
    assert match_dict["urn:test:B"].depth == 1
    assert match_dict["urn:test:C"].depth == 1
    assert match_dict["urn:test:D"].depth == 2
    assert match_dict["urn:test:E"].depth == 3


def test_asymmetric_shortcut_diamond_retains_shortest_depth() -> None:
    """Verify asymmetric shortcut diamond retains shortest depth (1 instead of 2)."""
    # A -> [B, D] (direct parent of D)
    # B -> [D] (grandparent of D via B)
    # D -> [E]
    adapter = SyntheticOntologyAdapter(
        {
            "urn:test:A": ["urn:test:B", "urn:test:D"],
            "urn:test:B": ["urn:test:D"],
            "urn:test:D": ["urn:test:E"],
            "urn:test:E": [],
        }
    )

    matches = resolve_subclass_closure(adapter, "urn:test:A", direct_seed_score=0.80)
    match_dict = {m.uri: m for m in matches}

    assert match_dict["urn:test:B"].depth == 1
    assert match_dict["urn:test:D"].depth == 1  # Shortest depth from direct link!
    assert match_dict["urn:test:E"].depth == 2


# =============================================================================
# 6. Max Depth Truncation & Validation
# =============================================================================


def test_max_depth_truncation() -> None:
    """Verify that traversal stops at max_depth."""
    # Chain: A -> B -> C -> D -> E
    adapter = SyntheticOntologyAdapter(
        {
            "urn:test:A": ["urn:test:B"],
            "urn:test:B": ["urn:test:C"],
            "urn:test:C": ["urn:test:D"],
            "urn:test:D": ["urn:test:E"],
            "urn:test:E": [],
        }
    )

    # max_depth = 1: only B
    m1 = resolve_subclass_closure(adapter, "urn:test:A", direct_seed_score=0.75, max_depth=1)
    assert len(m1) == 1
    assert m1[0].uri == "urn:test:B"

    # max_depth = 2: B and C
    m2 = resolve_subclass_closure(adapter, "urn:test:A", direct_seed_score=0.75, max_depth=2)
    assert [m.uri for m in m2] == ["urn:test:B", "urn:test:C"]

    # max_depth = 3: B, C, D
    m3 = resolve_subclass_closure(adapter, "urn:test:A", direct_seed_score=0.75, max_depth=3)
    assert [m.uri for m in m3] == ["urn:test:B", "urn:test:C", "urn:test:D"]


def test_invalid_max_depth_raises_value_error() -> None:
    """Verify that max_depth < 1 raises ValueError."""
    adapter = SyntheticOntologyAdapter({"urn:test:A": ["urn:test:B"]})
    with pytest.raises(ValueError, match="max_depth must be at least 1"):
        resolve_subclass_closure(adapter, "urn:test:A", direct_seed_score=0.75, max_depth=0)

    with pytest.raises(ValueError, match="max_depth must be at least 1"):
        resolve_subclass_closure(adapter, "urn:test:A", direct_seed_score=0.75, max_depth=-5)


# =============================================================================
# 7. Zero rdflib Runtime Import Invariant
# =============================================================================


def test_zero_runtime_rdflib_in_closure() -> None:
    """Verify closure.py contains no rdflib imports and does not load rdflib into sys.modules."""
    source_file = inspect.getsourcefile(closure_module)
    assert source_file is not None
    code_content = Path(source_file).read_text(encoding="utf-8")

    assert "import rdflib" not in code_content
    assert "from rdflib" not in code_content

    # Subprocess check
    script = (
        "import sys\n"
        "from opendv_sources.fibo import get_default_fibo_loader, resolve_subclass_closure\n"
        "loader = get_default_fibo_loader()\n"
        "matches = resolve_subclass_closure(\n"
        "    loader,\n"
        "    'https://spec.edmcouncil.org/fibo/ontology/BE/Corporations/Corporations/JointStockCompany',\n"
        "    0.85,\n"
        ")\n"
        "assert len(matches) > 0\n"
        "assert 'rdflib' not in sys.modules, f'rdflib found in sys.modules: {sys.modules.keys()}'\n"
        "print('ZERO_RDFLIB_SUCCESS')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        f"Subprocess failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    assert "ZERO_RDFLIB_SUCCESS" in proc.stdout


# =============================================================================
# 8. Performance & Latency Budget
# =============================================================================


def test_closure_resolution_latency(default_loader: FiboReferenceOntologyLoader) -> None:
    """Verify transitive closure resolution takes < 5.0 ms over in-memory FIBO pack."""
    # Warm up
    _ = resolve_subclass_closure(default_loader, JOINT_STOCK_COMPANY, 0.85)

    times: list[float] = []
    matches = []
    for _ in range(10):
        t0 = time.perf_counter()
        matches = resolve_subclass_closure(default_loader, JOINT_STOCK_COMPANY, 0.85)
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1000.0)

    median_ms = sorted(times)[len(times) // 2]
    assert len(matches) > 0
    assert median_ms < 5.0, f"Closure median latency {median_ms:.3f} ms exceeds 5.0 ms budget"


# =============================================================================
# 9. Edge Cases & Input Validation
# =============================================================================


def test_invalid_concept_uri() -> None:
    """Verify that empty or invalid concept_uri raises ValueError."""
    adapter = SyntheticOntologyAdapter({})
    with pytest.raises(ValueError, match="concept_uri must be a non-empty string"):
        resolve_subclass_closure(adapter, "", direct_seed_score=0.85)

    with pytest.raises(ValueError, match="concept_uri must be a non-empty string"):
        resolve_subclass_closure(adapter, "   ", direct_seed_score=0.85)


def test_invalid_confidence_score_out_of_bounds() -> None:
    """Verify that score < 0.0 or > 1.0 raises ValueError."""
    adapter = SyntheticOntologyAdapter({})
    with pytest.raises(ValueError, match=r"between 0\.0 and 1\.0"):
        resolve_subclass_closure(adapter, "urn:test:A", direct_seed_score=1.5)

    with pytest.raises(ValueError, match=r"between 0\.0 and 1\.0"):
        resolve_subclass_closure(adapter, "urn:test:A", direct_seed_score=-0.1)


def test_require_known_seed_flag(default_loader: FiboReferenceOntologyLoader) -> None:
    """Verify require_known_seed raises SourceNotFoundError when seed is unknown."""
    with pytest.raises(SourceNotFoundError, match="not found in reference ontology pack"):
        resolve_subclass_closure(
            default_loader,
            "urn:nonexistent:concept",
            direct_seed_score=0.85,
            require_known_seed=True,
        )


def test_resolver_properties_and_attributes(
    default_loader: FiboReferenceOntologyLoader,
) -> None:
    """Verify SubclassClosureResolver properties and defaults."""
    resolver = SubclassClosureResolver(
        default_loader,
        default_max_depth=16,
        grounding_threshold=0.70,
    )
    assert resolver.loader is default_loader
    assert resolver.default_max_depth == 16
    assert resolver.grounding_threshold == Decimal("0.7000")


def test_string_and_integer_confidence_inputs(
    default_loader: FiboReferenceOntologyLoader,
) -> None:
    """Verify that string and integer confidence representations are supported."""
    m_str = resolve_subclass_closure(default_loader, JOINT_STOCK_COMPANY, "0.8500")
    assert len(m_str) > 0
    assert m_str[0].confidence == 0.8500

    m_int = resolve_subclass_closure(default_loader, JOINT_STOCK_COMPANY, 1)
    assert len(m_int) > 0
    assert m_int[0].confidence == 1.0


def test_validate_closure_grounding_eligibility_failures() -> None:
    """Verify validate_closure_grounding_eligibility raises ValueError on invalid matches."""
    valid_match = SubclassClosureMatch(
        uri="urn:test:C",
        depth=1,
        predicate=BROAD_MATCH_PREDICATE,
        confidence=0.85,
    )

    # Valid list passes
    validate_closure_grounding_eligibility([valid_match])

    # SubclassClosureMatch with forbidden predicate
    invalid_pred_match = SubclassClosureMatch.model_construct(
        uri="urn:test:C",
        depth=1,
        predicate="skos:exactMatch",
        confidence=0.85,
    )
    with pytest.raises(ValueError, match="Invalid closure predicate"):
        validate_closure_grounding_eligibility([invalid_pred_match])


def test_only_pack_concepts_filter(default_loader: FiboReferenceOntologyLoader) -> None:
    """Verify only_pack_concepts=True excludes external superclasses (such as OMG Commons Party)."""
    # JointStockCompany has Party at depth 4 which is outside the FIBO pack
    matches_all = resolve_subclass_closure(
        default_loader,
        JOINT_STOCK_COMPANY,
        0.85,
        only_pack_concepts=False,
    )
    matches_pack_only = resolve_subclass_closure(
        default_loader,
        JOINT_STOCK_COMPANY,
        0.85,
        only_pack_concepts=True,
    )

    party_uri = "https://www.omg.org/spec/Commons/PartiesAndSituations/Party"
    assert any(m.uri == party_uri for m in matches_all)
    assert not any(m.uri == party_uri for m in matches_pack_only)
