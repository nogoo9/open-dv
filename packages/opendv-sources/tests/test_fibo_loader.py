"""Comprehensive automated test suite for FiboReferenceOntologyLoader (Task 4C.2 / #206).

Tests SPI protocol conformance, invariant budgets (cold load latency < 70 ms, peak heap < 18.0 MB),
URI lookups across all 6 core modules, candidate search ranking (with class vs property order),
taxonomic navigation, integrity validation, baseline digests, and edge/corrupted error paths.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path

import pytest

import opendv_sources.fibo.loader as loader_module
from opendv_contracts.errors import ErrorCode, get_error_mapping
from opendv_contracts.spi.reference_ontology import ReferenceOntologyAdapter
from opendv_sources.exceptions import SourceNotFoundError
from opendv_sources.fibo import (
    CANONICAL_FIBO_CORE_PREFIX,
    CANONICAL_FIBO_CORE_SHA256,
    MAX_DISK_SIZE_BYTES,
    MAX_LOAD_LATENCY_MS,
    MAX_MEMORY_BYTES,
    FiboReferenceOntologyLoader,
    ReferenceOntologyCorruptError,
    clear_fibo_loader_cache,
    get_default_fibo_loader,
)

# Pinned test URIs across modules
BE_JOINT_STOCK_COMPANY = (
    "https://spec.edmcouncil.org/fibo/ontology/BE/Corporations/Corporations/JointStockCompany"
)
BE_BOARD_AGREEMENT = (
    "https://spec.edmcouncil.org/fibo/ontology/BE/Corporations/Corporations/BoardAgreement"
)
BE_STOCK_CORPORATION = (
    "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/CorporateBodies/StockCorporation"
)
FND_CAPITAL_SURPLUS = (
    "https://spec.edmcouncil.org/fibo/ontology/FND/Accounting/AccountingEquity/CapitalSurplus"
)
FBC_BANKRUPTCY = (
    "https://spec.edmcouncil.org/fibo/ontology/FBC/DebtAndEquities/CreditEvents/Bankruptcy"
)
SEC_AUTO_ABS = "https://spec.edmcouncil.org/fibo/ontology/SEC/Debt/AssetBackedSecurities/AutoAssetBackedSecurity"
LOAN_BORROWER_INCOME = "https://spec.edmcouncil.org/fibo/ontology/LOAN/LoansGeneral/LoanApplications/AllBorrowersMonthlyIncome"
IND_DAILY_EARNINGS = "https://spec.edmcouncil.org/fibo/ontology/IND/EconomicIndicators/EconomicIndicators/AverageDailyEarnings"
FND_HAS_AMOUNT = "https://spec.edmcouncil.org/fibo/ontology/FND/Accounting/CurrencyAmount/hasAmount"
BE_HAS_INCORP_DATE = (
    "https://spec.edmcouncil.org/fibo/ontology/BE/Corporations/Corporations/hasDateOfIncorporation"
)


@pytest.fixture
def default_loader() -> FiboReferenceOntologyLoader:
    """Provide initialized default FiboReferenceOntologyLoader fixture."""
    return FiboReferenceOntologyLoader()


# =============================================================================
# 1. SPI Protocol Conformance
# =============================================================================


def test_spi_protocol_conformance(default_loader: FiboReferenceOntologyLoader) -> None:
    """Verify that FiboReferenceOntologyLoader implements ReferenceOntologyAdapter SPI."""
    assert isinstance(default_loader, ReferenceOntologyAdapter)
    assert callable(getattr(default_loader, "lookup_by_uri", None))
    assert callable(getattr(default_loader, "search_candidates", None))


# =============================================================================
# 2. Invariant Resource Budgets (ADR-003: < 70 ms latency, < 18.0 MB heap)
# =============================================================================


def test_cold_load_latency_budget() -> None:
    """Verify genuine load latency strictly satisfies < 70.0 ms budget (ADR-003, MIN-04)."""
    latencies: list[float] = []
    loader: FiboReferenceOntologyLoader | None = None
    for _ in range(5):
        t_start = time.perf_counter()
        loader = FiboReferenceOntologyLoader()
        t_end = time.perf_counter()
        latencies.append((t_end - t_start) * 1000.0)

    assert loader is not None and loader.class_count > 0
    min_latency = min(latencies)
    sorted_latencies = sorted(latencies)
    median_latency = sorted_latencies[len(sorted_latencies) // 2]
    assert median_latency < MAX_LOAD_LATENCY_MS, (
        f"Median load latency {median_latency:.2f} ms exceeds budget {MAX_LOAD_LATENCY_MS} ms "
        f"(min={min_latency:.2f} ms)"
    )


def test_heap_memory_footprint_budget() -> None:
    """Verify peak heap memory during load is strictly < 18.0 MB."""
    tracemalloc.start()
    _ = FiboReferenceOntologyLoader()
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    peak_mb = peak_bytes / (1024 * 1024)
    budget_mb = MAX_MEMORY_BYTES / (1024 * 1024)

    assert peak_bytes < MAX_MEMORY_BYTES, (
        f"Peak heap memory {peak_mb:.2f} MB exceeds budget {budget_mb:.2f} MB"
    )


def test_zero_runtime_rdflib_overhead() -> None:
    """Verify that FiboReferenceOntologyLoader has zero runtime rdflib dependency (SUG-03)."""
    source_file = Path(inspect.getsourcefile(loader_module) or "")
    assert source_file.is_file(), f"Could not find source file for {loader_module}"

    content = source_file.read_text(encoding="utf-8")
    assert "import rdflib" not in content
    assert "from rdflib" not in content

    script = (
        "import sys, opendv_sources.fibo.loader; "
        "assert 'rdflib' not in sys.modules, 'rdflib was imported!'"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"Subprocess rdflib check failed: {result.stderr}"


# =============================================================================
# 3. Exact URI Lookup Capabilities across Core Modules
# =============================================================================


def test_exact_lookup_known_classes_across_modules(
    default_loader: FiboReferenceOntologyLoader,
) -> None:
    """Verify O(1) exact lookup retrieves known classes from all 6 modules."""
    test_cases = [
        ("BE", BE_JOINT_STOCK_COMPANY, "joint stock company"),
        ("BE", BE_BOARD_AGREEMENT, "board agreement"),
        ("BE", BE_STOCK_CORPORATION, "stock corporation"),
        ("FND", FND_CAPITAL_SURPLUS, "capital surplus"),
        ("FBC", FBC_BANKRUPTCY, "bankruptcy"),
        ("SEC", SEC_AUTO_ABS, "auto asset-backed security"),
        ("LOAN", LOAN_BORROWER_INCOME, "all borrowers' monthly income"),
        ("IND", IND_DAILY_EARNINGS, "average daily earnings"),
    ]

    for expected_mod, uri, expected_label in test_cases:
        entry = default_loader.lookup_by_uri(uri)
        assert entry is not None, f"Failed lookup for {uri}"
        assert entry["uri"] == uri
        assert entry["module"] == expected_mod
        assert entry["label"].lower() == expected_label.lower()
        assert entry["entity_type"] == "class"
        assert "definition" in entry
        assert "sub_class_of" in entry
        assert isinstance(entry["sub_class_of"], list)


def test_exact_lookup_known_properties(default_loader: FiboReferenceOntologyLoader) -> None:
    """Verify lookup_by_uri retrieves property definitions."""
    prop = default_loader.lookup_by_uri(FND_HAS_AMOUNT)
    assert prop is not None
    assert prop["uri"] == FND_HAS_AMOUNT
    assert prop["label"] == "has amount"
    assert prop["module"] == "FND"
    assert prop["entity_type"] == "property"

    incorp_date_prop = default_loader.lookup_by_uri(BE_HAS_INCORP_DATE)
    assert incorp_date_prop is not None
    assert incorp_date_prop["uri"] == BE_HAS_INCORP_DATE
    assert incorp_date_prop["module"] == "BE"
    assert incorp_date_prop["entity_type"] == "property"


def test_lookup_missing_and_empty_uri(default_loader: FiboReferenceOntologyLoader) -> None:
    """Verify lookup_by_uri returns None on missing or empty URIs."""
    assert default_loader.lookup_by_uri("https://example.org/nonexistent/Entity") is None
    assert default_loader.lookup_by_uri("urn:ontology:entity:unknown") is None
    assert default_loader.lookup_by_uri("") is None
    assert default_loader.lookup_by_uri("   ") is None


def test_lookup_returns_deep_copy(default_loader: FiboReferenceOntologyLoader) -> None:
    """Verify lookup_by_uri returns a deep copy protecting nested lists from mutation (MIN-01)."""
    entry1 = default_loader.lookup_by_uri(BE_BOARD_AGREEMENT)
    assert entry1 is not None
    entry1["custom_polluted_key"] = "test"
    entry1["sub_class_of"].append("https://polluted.org/FakeParent")
    entry1["synonyms"].append("polluted synonym")

    entry2 = default_loader.lookup_by_uri(BE_BOARD_AGREEMENT)
    assert entry2 is not None
    assert "custom_polluted_key" not in entry2
    assert "https://polluted.org/FakeParent" not in entry2["sub_class_of"]
    assert "polluted synonym" not in entry2["synonyms"]


# =============================================================================
# 4. Search Candidates Capability & Multi-Tier Ranking (MAJ-01, MAJ-02)
# =============================================================================


def test_search_candidates_limit(default_loader: FiboReferenceOntologyLoader) -> None:
    """Verify search_candidates respects the limit parameter."""
    results_5 = default_loader.search_candidates("corporation", limit=5)
    assert len(results_5) <= 5
    assert len(results_5) > 0

    results_2 = default_loader.search_candidates("corporation", limit=2)
    assert len(results_2) == 2

    assert default_loader.search_candidates("corporation", limit=0) == []
    assert default_loader.search_candidates("corporation", limit=-1) == []


def test_search_candidates_exact_ranking(default_loader: FiboReferenceOntologyLoader) -> None:
    """Verify exact match on label, local name, or synonym receives top score 1.0."""
    matches = default_loader.search_candidates("corporation", limit=5)
    assert len(matches) > 0
    assert matches[0]["label"] == "corporation"
    assert matches[0]["match_score"] == 1.0

    stock_matches = default_loader.search_candidates("StockCorporation", limit=5)
    assert len(stock_matches) > 0
    assert stock_matches[0]["label"] == "stock corporation"
    assert stock_matches[0]["match_score"] == 1.0


def test_search_candidates_prefix_matching(default_loader: FiboReferenceOntologyLoader) -> None:
    """Verify prefix match receives score 0.85."""
    matches = default_loader.search_candidates("board ag", limit=5)
    assert len(matches) > 0
    top = matches[0]
    assert "board agreement" in top["label"]
    assert top["match_score"] == 0.85


def test_search_candidates_token_and_substring(default_loader: FiboReferenceOntologyLoader) -> None:
    """Verify multi-word token match and substring contains match."""
    token_matches = default_loader.search_candidates("joint stock company", limit=5)
    assert any(m["label"] == "joint stock company" for m in token_matches)

    sub_matches = default_loader.search_candidates("capital surplus", limit=5)
    assert any("capital surplus" in m["label"] for m in sub_matches)


def test_search_candidates_exact_property_outranks_class_substring(
    default_loader: FiboReferenceOntologyLoader,
) -> None:
    """Regression test for MAJ-02: Exact property match must outrank class substring matches.

    A query like 'has amount' matches property FND_HAS_AMOUNT exactly (score 1.0).
    Even when numerous classes contain 'amount' as a weak substring, the property must be returned
    at the top of candidates with match_score 1.0.
    """
    candidates = default_loader.search_candidates("has amount", limit=10)
    assert len(candidates) > 0

    top = candidates[0]
    assert top["label"] == "has amount"
    assert top["uri"] == FND_HAS_AMOUNT
    assert top["match_score"] == 1.0
    assert top["entity_type"] == "property"


def test_search_candidates_substring_tier_deep_copy_and_entity_type(
    default_loader: FiboReferenceOntologyLoader,
) -> None:
    """Regression test for MAJ-01: Substring tier must return deep copies with entity_type."""
    candidates = default_loader.search_candidates("surplus", limit=5)
    assert len(candidates) > 0

    for cand in candidates:
        assert "entity_type" in cand
        assert cand["entity_type"] in ("class", "property")
        # Mutating candidate should not affect loader state
        cand.setdefault("sub_class_of", []).append("https://polluted.org/Leaked")
        cand["custom_polluted"] = True

    # Re-query and verify no pollution
    fresh = default_loader.search_candidates("surplus", limit=5)
    for cand in fresh:
        assert "custom_polluted" not in cand
        assert "https://polluted.org/Leaked" not in cand.get("sub_class_of", [])


def test_search_candidates_empty_and_unknown(default_loader: FiboReferenceOntologyLoader) -> None:
    """Verify empty or non-matching queries return empty lists."""
    assert default_loader.search_candidates("") == []
    assert default_loader.search_candidates("   ") == []
    assert default_loader.search_candidates("xyznonexistentterm12345") == []


def test_search_candidates_candidate_schema(default_loader: FiboReferenceOntologyLoader) -> None:
    """Verify candidate dictionaries contain required metadata fields (SUG-02)."""
    results = default_loader.search_candidates("agreement", limit=3)
    assert len(results) > 0
    for cand in results:
        assert "uri" in cand
        assert "label" in cand
        assert "module" in cand
        assert "definition" in cand
        assert "entity_type" in cand
        assert cand["entity_type"] in ("class", "property")
        assert "match_score" in cand
        assert 0.0 <= cand["match_score"] <= 1.0


# =============================================================================
# 5. Taxonomy Navigation & Inspection Helpers
# =============================================================================


def test_taxonomy_superclasses_and_subclasses(
    default_loader: FiboReferenceOntologyLoader,
) -> None:
    """Verify get_direct_superclasses and get_direct_subclasses."""
    superclasses = default_loader.get_direct_superclasses(BE_JOINT_STOCK_COMPANY)
    assert len(superclasses) > 0
    business_entity_uri = (
        "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/BusinessEntity"
    )
    assert business_entity_uri in superclasses

    subclasses = default_loader.get_direct_subclasses(business_entity_uri)
    assert BE_JOINT_STOCK_COMPANY in subclasses

    assert default_loader.get_direct_superclasses("https://unknown.org/Entity") == []
    assert default_loader.get_direct_subclasses("https://unknown.org/Entity") == []


def test_get_classes_and_properties_collections(
    default_loader: FiboReferenceOntologyLoader,
) -> None:
    """Verify get_classes and get_properties return complete collections and protect state."""
    classes = default_loader.get_classes()
    properties = default_loader.get_properties()

    assert len(classes) >= 2000
    assert len(properties) >= 900
    assert BE_JOINT_STOCK_COMPANY in classes
    assert FND_HAS_AMOUNT in properties

    classes[BE_JOINT_STOCK_COMPANY]["synonyms"].append("polluted")
    fresh_classes = default_loader.get_classes()
    assert "polluted" not in fresh_classes[BE_JOINT_STOCK_COMPANY]["synonyms"]


def test_pack_properties_and_metadata(default_loader: FiboReferenceOntologyLoader) -> None:
    """Verify metadata properties on FiboReferenceOntologyLoader."""
    assert len(default_loader.pack_sha256) == 64
    assert len(default_loader.pack_sha256_prefix) == 8
    assert default_loader.pack_sha256.startswith(default_loader.pack_sha256_prefix)
    assert default_loader.pack_id == "fibo-core"
    assert default_loader.version == "1.0.0"
    assert set(default_loader.modules) == {"FND", "BE", "FBC", "SEC", "LOAN", "IND"}
    assert default_loader.class_count == len(default_loader.get_classes())
    assert default_loader.property_count == len(default_loader.get_properties())
    assert default_loader.pack_path.is_file()

    meta = default_loader.get_pack_metadata()
    assert meta["pack_id"] == "fibo-core"
    assert meta["upstream_tag"] == "master_2024Q4"
    assert meta["upstream_commit"] == "69dbbe7a6a4e32049ba3f3988b48877bc954f061"


def test_packaged_fibo_pack_artifact_committed(
    default_loader: FiboReferenceOntologyLoader,
) -> None:
    """Verify packaged pack file exists, is non-empty, and matches canonical digest (MAJ-03)."""
    pkg_dir = Path(inspect.getsourcefile(loader_module) or "").parent
    canonical_pack = pkg_dir / f"fibo-core-{CANONICAL_FIBO_CORE_PREFIX}.json"
    alias_pack = pkg_dir / "fibo-core-pack.json"

    assert canonical_pack.is_file(), f"Missing canonical pack {canonical_pack}"
    assert alias_pack.is_file(), f"Missing alias pack {alias_pack}"

    size = canonical_pack.stat().st_size
    assert 1_000_000 < size < MAX_DISK_SIZE_BYTES
    assert default_loader.pack_sha256 == CANONICAL_FIBO_CORE_SHA256


# =============================================================================
# 6. Integrity Validation & Error Edge Cases (MAJ-02, MIN-02..05, MIN-08)
# =============================================================================


def test_missing_pack_path_raises_source_not_found(tmp_path: Path) -> None:
    """Verify loading from a nonexistent file raises SourceNotFoundError."""
    nonexistent = tmp_path / "fibo-core-bebd6ae8.json"
    with pytest.raises(SourceNotFoundError):
        FiboReferenceOntologyLoader(pack_path=nonexistent)


def test_missing_env_override_pack_path_raises_not_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify nonexistent OPENDV_FIBO_PACK_PATH raises SourceNotFoundError (MIN-02)."""
    bad_path = str(tmp_path / "nonexistent.json")
    monkeypatch.setenv("OPENDV_FIBO_PACK_PATH", bad_path)
    with pytest.raises(SourceNotFoundError) as exc_info:
        FiboReferenceOntologyLoader()
    assert "OPENDV_FIBO_PACK_PATH not found" in str(exc_info.value)


def test_missing_env_override_baseline_digests_path_raises_not_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify nonexistent OPENDV_BASELINE_DIGESTS_PATH raises SourceNotFoundError (MIN-02)."""
    bad_path = str(tmp_path / "nonexistent_baseline.json")
    monkeypatch.setenv("OPENDV_BASELINE_DIGESTS_PATH", bad_path)
    with pytest.raises(SourceNotFoundError) as exc_info:
        FiboReferenceOntologyLoader(verify_integrity=True)
    assert "OPENDV_BASELINE_DIGESTS_PATH not found" in str(exc_info.value)


def _assert_corrupt_error_taxonomy(err: ReferenceOntologyCorruptError) -> None:
    """Assert ReferenceOntologyCorruptError aligns with canonical ErrorCode mapping."""
    expected_code = getattr(
        ErrorCode,
        "REFERENCE_ONTOLOGY_CORRUPT",
        ErrorCode.REFERENCE_PACK_INTEGRITY_FAILURE,
    )
    expected_exit, expected_http = get_error_mapping(expected_code)
    assert err.error_code == expected_code
    assert err.exit_code == expected_exit
    assert err.http_status == expected_http
    assert err.error_code_symbol == "REFERENCE_ONTOLOGY_CORRUPT"
    problem = err.to_problem_detail()
    assert problem.error_code == expected_code
    assert problem.status == expected_http


def test_invalid_json_pack_raises_corrupt_error(tmp_path: Path) -> None:
    """Verify loading non-JSON content raises ReferenceOntologyCorruptError."""
    bad_json_file = tmp_path / "fibo-core-bad.json"
    bad_json_file.write_text("NOT_VALID_JSON{", encoding="utf-8")

    with pytest.raises(ReferenceOntologyCorruptError) as exc_info:
        FiboReferenceOntologyLoader(pack_path=bad_json_file, verify_integrity=False)

    _assert_corrupt_error_taxonomy(exc_info.value)


def test_non_dict_json_root_raises_corrupt_error(tmp_path: Path) -> None:
    """Verify valid JSON with non-dict root raises ReferenceOntologyCorruptError (MIN-03)."""
    list_json_file = tmp_path / "fibo-core-list.json"
    list_json_file.write_text(json.dumps(["element1", "element2"]), encoding="utf-8")

    with pytest.raises(ReferenceOntologyCorruptError) as exc_info:
        FiboReferenceOntologyLoader(pack_path=list_json_file, verify_integrity=False)
    _assert_corrupt_error_taxonomy(exc_info.value)
    assert "Pack root must be a JSON object" in str(exc_info.value)


def test_missing_required_sections_raises_corrupt_error(tmp_path: Path) -> None:
    """Verify missing 'classes', 'properties', or 'metadata' raises
    ReferenceOntologyCorruptError.
    """
    incomplete_file = tmp_path / "fibo-core-incomplete.json"
    incomplete_file.write_text(json.dumps({"classes": {}}), encoding="utf-8")

    with pytest.raises(ReferenceOntologyCorruptError) as exc_info:
        FiboReferenceOntologyLoader(pack_path=incomplete_file, verify_integrity=False)
    _assert_corrupt_error_taxonomy(exc_info.value)


def test_missing_metadata_version_raises_corrupt_error(tmp_path: Path) -> None:
    """Verify missing version in metadata raises ReferenceOntologyCorruptError (MIN-08)."""
    bad_meta_file = tmp_path / "fibo-core-nometa.json"
    bad_meta_file.write_text(
        json.dumps(
            {
                "classes": {"http://test.org/C": {"uri": "http://test.org/C", "sub_class_of": []}},
                "properties": {"http://test.org/P": {"uri": "http://test.org/P"}},
                "metadata": {"pack_id": "fibo-core"},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ReferenceOntologyCorruptError) as exc_info:
        FiboReferenceOntologyLoader(pack_path=bad_meta_file, verify_integrity=False)
    assert "missing valid 'version'" in str(exc_info.value)


def test_malformed_class_entry_schema_raises_corrupt_error(tmp_path: Path) -> None:
    """Verify non-dict class or non-matching URI raises ReferenceOntologyCorruptError (MIN-05)."""
    bad_class_file = tmp_path / "fibo-core-badclass.json"
    bad_class_file.write_text(
        json.dumps(
            {
                "classes": {"http://test.org/C": "not a dict"},
                "properties": {"http://test.org/P": {"uri": "http://test.org/P"}},
                "metadata": {"pack_id": "fibo-core", "version": "1.0.0"},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ReferenceOntologyCorruptError) as exc_info:
        FiboReferenceOntologyLoader(pack_path=bad_class_file, verify_integrity=False)
    assert "Malformed class entry" in str(exc_info.value)


def test_non_string_subclasses_or_synonyms_raises_corrupt(tmp_path: Path) -> None:
    """Verify non-string elements in sub_class_of raise ReferenceOntologyCorruptError (MIN-02)."""
    bad_types_file = tmp_path / "fibo-core-badtypes.json"
    bad_types_file.write_text(
        json.dumps(
            {
                "classes": {
                    "http://test.org/C": {
                        "uri": "http://test.org/C",
                        "sub_class_of": [12345],  # non-string
                    }
                },
                "properties": {"http://test.org/P": {"uri": "http://test.org/P"}},
                "metadata": {"pack_id": "fibo-core", "version": "1.0.0"},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ReferenceOntologyCorruptError) as exc_info:
        FiboReferenceOntologyLoader(pack_path=bad_types_file, verify_integrity=False)
    assert "sub_class_of must be a list of strings" in str(exc_info.value)


def test_filename_hash_prefix_mismatch_raises_corrupt_error(
    default_loader: FiboReferenceOntologyLoader, tmp_path: Path
) -> None:
    """Verify mismatched 8-char hash in filename raises ReferenceOntologyCorruptError."""
    mismatched_file = tmp_path / "fibo-core-deadbeef.json"
    mismatched_file.write_bytes(default_loader.pack_path.read_bytes())

    with pytest.raises(ReferenceOntologyCorruptError) as exc_info:
        FiboReferenceOntologyLoader(pack_path=mismatched_file, verify_integrity=True)
    _assert_corrupt_error_taxonomy(exc_info.value)
    assert "prefix 'deadbeef' does not match" in str(exc_info.value)


def test_explicit_expected_sha256_mismatch_raises_corrupt_error(
    default_loader: FiboReferenceOntologyLoader,
) -> None:
    """Verify explicit expected_sha256 parameter mismatch raises ReferenceOntologyCorruptError."""
    with pytest.raises(ReferenceOntologyCorruptError) as exc_info:
        FiboReferenceOntologyLoader(
            pack_path=default_loader.pack_path,
            expected_sha256="0" * 64,
        )
    _assert_corrupt_error_taxonomy(exc_info.value)

    with pytest.raises(ReferenceOntologyCorruptError) as exc_info:
        FiboReferenceOntologyLoader(
            pack_path=default_loader.pack_path,
            expected_sha256="ffffffff",
        )
    _assert_corrupt_error_taxonomy(exc_info.value)

    with pytest.raises(ReferenceOntologyCorruptError) as exc_info:
        FiboReferenceOntologyLoader(
            pack_path=default_loader.pack_path,
            expected_sha256="1234567890ab",
        )
    _assert_corrupt_error_taxonomy(exc_info.value)


def test_pack_size_budget_guard(tmp_path: Path) -> None:
    """Verify pack exceeding MAX_DISK_SIZE_BYTES (>4.0 MB) raises ReferenceOntologyCorruptError."""
    oversized_file = tmp_path / "fibo-core-huge.json"
    oversized_file.write_bytes(b" " * (MAX_DISK_SIZE_BYTES + 1024))

    with pytest.raises(ReferenceOntologyCorruptError) as exc_info:
        FiboReferenceOntologyLoader(pack_path=oversized_file, verify_integrity=False)
    _assert_corrupt_error_taxonomy(exc_info.value)
    assert "exceeds budget" in str(exc_info.value)


def test_mandatory_integrity_blocks_tampered_pack_without_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for MAJ-02 & MIN-06: Reject tampered pack when no baseline present."""
    monkeypatch.delenv("OPENDV_BASELINE_DIGESTS_PATH", raising=False)
    monkeypatch.setattr(loader_module, "_find_baseline_digests_path", lambda: None)
    isolated_dir = tmp_path / "isolated" / "deep" / "dir"
    isolated_dir.mkdir(parents=True)

    fake_pack = isolated_dir / "custom-pack.json"
    fake_pack.write_text(
        json.dumps(
            {
                "classes": {"http://test.org/C": {"uri": "http://test.org/C", "sub_class_of": []}},
                "properties": {"http://test.org/P": {"uri": "http://test.org/P"}},
                "metadata": {"pack_id": "fibo-core", "version": "1.0.0", "is_fixture": True},
            }
        ),
        encoding="utf-8",
    )

    # When verify_integrity=True, must be rejected because hash != CANONICAL_FIBO_CORE_SHA256
    with pytest.raises(ReferenceOntologyCorruptError) as exc_info:
        FiboReferenceOntologyLoader(pack_path=fake_pack, verify_integrity=True)
    assert "does not match" in str(exc_info.value)


# =============================================================================
# 7. Baseline Digests Integrity Branches (MIN-04, MAJ-02)
# =============================================================================


def test_baseline_mismatched_upstream_commit_raises_corrupt(
    default_loader: FiboReferenceOntologyLoader,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify mismatched upstream_commit in baseline raises ReferenceOntologyCorruptError."""
    baseline = {
        "reference_packs": {
            "fibo_core": {
                "upstream_commit": "wrong_commit_sha",
            }
        }
    }
    b_file = tmp_path / "baseline-digests.json"
    b_file.write_text(json.dumps(baseline), encoding="utf-8")
    monkeypatch.setenv("OPENDV_BASELINE_DIGESTS_PATH", str(b_file))

    with pytest.raises(ReferenceOntologyCorruptError) as exc_info:
        FiboReferenceOntologyLoader(pack_path=default_loader.pack_path, verify_integrity=True)
    assert "does not match baseline commit" in str(exc_info.value)


def test_baseline_mismatched_canonical_sha256_raises_corrupt(
    default_loader: FiboReferenceOntologyLoader,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify mismatched canonical_sha256 in baseline raises ReferenceOntologyCorruptError."""
    baseline = {
        "reference_packs": {
            "fibo_core": {
                "canonical_sha256": "0" * 64,
            }
        }
    }
    b_file = tmp_path / "baseline-digests.json"
    b_file.write_text(json.dumps(baseline), encoding="utf-8")
    monkeypatch.setenv("OPENDV_BASELINE_DIGESTS_PATH", str(b_file))

    with pytest.raises(ReferenceOntologyCorruptError) as exc_info:
        FiboReferenceOntologyLoader(pack_path=default_loader.pack_path, verify_integrity=True)
    assert "Authoritative baseline digest" in str(exc_info.value)
    assert "does not match" in str(exc_info.value)


def test_baseline_corrupt_json_raises_corrupt(
    default_loader: FiboReferenceOntologyLoader,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify unparseable baseline digests raises ReferenceOntologyCorruptError."""
    b_file = tmp_path / "corrupt-baseline.json"
    b_file.write_text("NOT_JSON{", encoding="utf-8")
    monkeypatch.setenv("OPENDV_BASELINE_DIGESTS_PATH", str(b_file))

    with pytest.raises(ReferenceOntologyCorruptError) as exc_info:
        FiboReferenceOntologyLoader(pack_path=default_loader.pack_path, verify_integrity=True)
    assert "baseline digests file at" in str(exc_info.value)


def test_baseline_missing_fibo_core_section_raises_corrupt(
    default_loader: FiboReferenceOntologyLoader,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify baseline digests without fibo_core section raises ReferenceOntologyCorruptError."""
    b_file = tmp_path / "missing-fibo.json"
    b_file.write_text(json.dumps({"reference_packs": {}}), encoding="utf-8")
    monkeypatch.setenv("OPENDV_BASELINE_DIGESTS_PATH", str(b_file))

    with pytest.raises(ReferenceOntologyCorruptError) as exc_info:
        FiboReferenceOntologyLoader(pack_path=default_loader.pack_path, verify_integrity=True)
    assert "missing 'fibo_core'" in str(exc_info.value)


# =============================================================================
# 8. Factory & Singleton Helper Functions (MIN-03, MIN-09)
# =============================================================================


def test_factory_method_and_caching() -> None:
    """Verify FiboReferenceOntologyLoader.load, get_default_fibo_loader caching and clearing."""
    clear_fibo_loader_cache()
    loader1 = FiboReferenceOntologyLoader.load()
    assert isinstance(loader1, ReferenceOntologyAdapter)
    assert loader1.pack_sha256_prefix == CANONICAL_FIBO_CORE_PREFIX

    loader2 = get_default_fibo_loader(expected_sha256=CANONICAL_FIBO_CORE_SHA256)
    loader3 = get_default_fibo_loader(expected_sha256=CANONICAL_FIBO_CORE_SHA256)
    assert loader2 is loader3

    clear_fibo_loader_cache()
    loader4 = get_default_fibo_loader(expected_sha256=CANONICAL_FIBO_CORE_SHA256)
    assert loader4 is not loader2  # fresh after clear


def test_get_default_fibo_loader_missing_file_raises_source_not_found(tmp_path: Path) -> None:
    """Verify get_default_fibo_loader on missing file raises SourceNotFoundError (MIN-01)."""
    non_existent = tmp_path / "does_not_exist.json"
    with pytest.raises(SourceNotFoundError) as exc_info:
        get_default_fibo_loader(pack_path=non_existent)
    assert "not found" in str(exc_info.value).lower()


def test_baseline_upstream_commit_matches_canonical_constant() -> None:
    """Verify loader canonical constants match schemas/v1/baseline-digests.json (MIN-06)."""
    baseline_path = loader_module._find_baseline_digests_path()
    assert baseline_path is not None, "baseline-digests.json must exist in repository"
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    fibo_core_spec = data["reference_packs"]["fibo_core"]

    assert fibo_core_spec["upstream_commit"] == loader_module.CANONICAL_FIBO_CORE_UPSTREAM_COMMIT, (
        "CANONICAL_FIBO_CORE_UPSTREAM_COMMIT out of sync with baseline-digests.json"
    )
    assert fibo_core_spec["upstream_tag"] == loader_module.CANONICAL_FIBO_CORE_UPSTREAM_TAG, (
        "CANONICAL_FIBO_CORE_UPSTREAM_TAG out of sync with baseline-digests.json"
    )


def test_untrusted_pack_cannot_override_baseline_from_parent_dir(tmp_path: Path) -> None:
    """Regression test for MAJ-01: Untrusted pack cannot spoof baseline via parent dir."""
    attacker_root = tmp_path / "attacker_workspace"
    attacker_schemas = attacker_root / "schemas" / "v1"
    attacker_schemas.mkdir(parents=True)

    fake_pack_path = attacker_root / "fake_pack.json"
    fake_pack_content = {
        "classes": {"http://fake.org/C": {"uri": "http://fake.org/C", "sub_class_of": []}},
        "properties": {"http://fake.org/P": {"uri": "http://fake.org/P"}},
        "metadata": {
            "pack_id": "fibo-core",
            "version": "1.0.0",
            "upstream_commit": "attacker_commit",
        },
    }
    raw_bytes = json.dumps(fake_pack_content).encode("utf-8")
    fake_pack_path.write_bytes(raw_bytes)
    attacker_sha = hashlib.sha256(raw_bytes).hexdigest()

    # Attacker places a forged baseline-digests.json inside attacker_workspace/schemas/v1/
    forged_baseline = {
        "reference_packs": {
            "fibo_core": {
                "canonical_sha256": attacker_sha,
                "upstream_commit": "attacker_commit",
            }
        }
    }
    (attacker_schemas / "baseline-digests.json").write_text(
        json.dumps(forged_baseline), encoding="utf-8"
    )

    # Loader must reject the untrusted pack because it does not search from the pack's parent dir
    # and enforces CANONICAL_FIBO_CORE_SHA256
    with pytest.raises(ReferenceOntologyCorruptError) as exc_info:
        FiboReferenceOntologyLoader(pack_path=fake_pack_path, verify_integrity=True)
    _assert_corrupt_error_taxonomy(exc_info.value)


def test_canonical_pack_artifact_integrity_and_packaging() -> None:
    """Verify vendored FIBO pack artifact exists, matches SHA-256, and is packaged (MAJ-02)."""
    pkg_dir = Path(loader_module.__file__).resolve().parent
    canonical_file = pkg_dir / f"fibo-core-{CANONICAL_FIBO_CORE_PREFIX}.json"
    alias_file = pkg_dir / "fibo-core-pack.json"

    assert canonical_file.is_file(), f"Vendored artifact missing at {canonical_file}"
    assert alias_file.is_file(), f"Vendored alias missing at {alias_file}"

    canonical_bytes = canonical_file.read_bytes()
    assert len(canonical_bytes) <= MAX_DISK_SIZE_BYTES, (
        f"Pack size {len(canonical_bytes)} exceeds budget {MAX_DISK_SIZE_BYTES}"
    )

    computed_sha = hashlib.sha256(canonical_bytes).hexdigest()
    assert computed_sha == CANONICAL_FIBO_CORE_SHA256, (
        f"Pack SHA-256 mismatch: {computed_sha} != {CANONICAL_FIBO_CORE_SHA256}"
    )

    alias_bytes = alias_file.read_bytes()
    alias_sha = hashlib.sha256(alias_bytes).hexdigest()
    assert alias_sha == CANONICAL_FIBO_CORE_SHA256


def test_pack_matching_8char_prefix_but_failing_full_digest_rejected(tmp_path: Path) -> None:
    """Regression for MAJ-01: Pack matching 8-char prefix but failing 64-char SHA is rejected."""
    tampered_pack = tmp_path / "custom-forged-pack.json"
    tampered_content = {
        "classes": {
            "http://forged.org/MaliciousClass": {
                "uri": "http://forged.org/MaliciousClass",
                "label": "malicious class",
                "sub_class_of": [],
            }
        },
        "properties": {
            "http://forged.org/maliciousProp": {
                "uri": "http://forged.org/maliciousProp",
                "label": "malicious prop",
            }
        },
        "metadata": {
            "pack_id": "fibo-core",
            "version": "1.0.0",
            "upstream_commit": loader_module.CANONICAL_FIBO_CORE_UPSTREAM_COMMIT,
        },
    }
    raw = json.dumps(tampered_content).encode("utf-8")
    tampered_pack.write_bytes(raw)
    forged_prefix = hashlib.sha256(raw).hexdigest()[:8]

    # Even though caller passes expected_sha256 matching the forged pack's 8-char prefix,
    # verify_integrity=True MUST enforce full 64-char match and reject the forged pack
    with pytest.raises(ReferenceOntologyCorruptError) as exc_info:
        FiboReferenceOntologyLoader(
            pack_path=tampered_pack,
            expected_sha256=forged_prefix,
            verify_integrity=True,
        )
    _assert_corrupt_error_taxonomy(exc_info.value)
    assert "authoritative canonical FIBO digest" in str(exc_info.value)


def test_cache_invalidates_on_baseline_digests_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify get_default_fibo_loader cache invalidates when baseline digests changes (MIN-02)."""
    b1 = tmp_path / "baseline1.json"
    b1.write_text(
        json.dumps(
            {
                "reference_packs": {
                    "fibo_core": {
                        "upstream_commit": "mismatched_commit_hash",
                        "canonical_sha256": CANONICAL_FIBO_CORE_SHA256,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENDV_BASELINE_DIGESTS_PATH", str(b1))

    # Clear cache and verify loading fails with corrupt error on b1 (mismatched commit)
    clear_fibo_loader_cache()
    with pytest.raises(ReferenceOntologyCorruptError):
        get_default_fibo_loader()

    # Revert env var to valid repo baseline: cache key detects baseline path change and succeeds
    monkeypatch.delenv("OPENDV_BASELINE_DIGESTS_PATH")
    loader = get_default_fibo_loader()
    assert loader is not None
    assert loader.class_count > 0
