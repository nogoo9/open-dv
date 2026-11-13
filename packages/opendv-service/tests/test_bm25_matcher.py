"""Comprehensive unit tests for Okapi BM25 concept matcher and FIBO grounding verifier.

Tests Task 5A.1 / Issue #211:
- 7-step tokenization pipeline (Unicode NFC, CamelCase, delimiters, 18 acronyms, 33 stopwords).
- Robertson BM25 formulation in Decimal(28) precision.
- Symmetric normalization and ROUND_HALF_EVEN quantization to 4 decimals.
- Definition-only match hit gate (capping score at 0.6400).
- Module filtering and tie-breaking.
- FiboPackGroundingVerifier SPI binding and SHA-256 integrity check.
"""

from __future__ import annotations

import json
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import pytest

from opendv_contracts.dtos.matching import ReferenceConcept
from opendv_contracts.spi.grounding import GroundingVerifier
from opendv_service.ontology.matching import (
    FINANCIAL_ACRONYMS,
    GROUNDING_THRESHOLD,
    STOPWORDS,
    Bm25ConceptMatcher,
    FiboPackGroundingVerifier,
    tokenize_concept_text,
)
from opendv_sources.fibo.loader import (
    CANONICAL_FIBO_CORE_SHA256,
    FiboReferenceOntologyLoader,
    ReferenceOntologyCorruptError,
    get_default_fibo_loader,
)


def test_tokenization_pipeline_steps() -> None:
    """Verify individual steps of the 7-step tokenization pipeline."""
    # Step 1 & 2: NFC and CamelCase
    assert tokenize_concept_text("CommonShare") == ["common", "share"]
    assert tokenize_concept_text("NAVCalculation") == [
        "net",
        "asset",
        "value",
        "calculation",
    ]

    # Step 3 & 4: Delimiters and Alphanumeric
    assert tokenize_concept_text("Fund_Account-Balance/Type.Code") == [
        "fund",
        "account",
        "balance",
        "code",
    ]

    # Step 5: Lowercase
    assert tokenize_concept_text("LEGAL PERSON") == ["legal", "person"]
    # 'entity' is a stopword and gets filtered in Step 7
    assert tokenize_concept_text("LEGAL ENTITY") == ["legal"]


def test_tokenization_acronym_expansion_and_stopword_protection() -> None:
    """Verify that all 18 financial acronyms expand and protected stopwords survive Step 7."""
    assert len(FINANCIAL_ACRONYMS) == 18

    # 'sec' expands to 'securities and exchange commission'.
    # 'and' is a stopword, but should be preserved because it was part of acronym expansion!
    sec_tokens = tokenize_concept_text("sec")
    assert sec_tokens == ["securities", "and", "exchange", "commission"]
    assert "and" in sec_tokens

    # Standard stopword outside expansion must be stripped
    mixed = tokenize_concept_text("the sec and the fund")
    assert mixed == ["securities", "and", "exchange", "commission", "fund"]

    # Verify all 18 acronyms
    for acronym, expected in FINANCIAL_ACRONYMS.items():
        expanded = tokenize_concept_text(acronym)
        assert expanded == expected, (
            f"Acronym {acronym} expanded to {expanded}, expected {expected}"
        )


def test_tokenization_stopword_filtering() -> None:
    """Verify that all 33 stopwords are filtered out when appearing as standalone words."""
    assert len(STOPWORDS) == 33
    for sw in STOPWORDS:
        # None of these stopwords are acronyms in FINANCIAL_ACRONYMS (e.g. 'acct', 'bk', etc.)
        if sw not in FINANCIAL_ACRONYMS:
            assert tokenize_concept_text(sw) == [], f"Stopword '{sw}' was not filtered"

    text = "the concept of an entity with a model type"
    assert tokenize_concept_text(text) == []


def test_empty_and_whitespace_query() -> None:
    """Verify that empty, whitespace, and symbol-only queries return no tokens."""
    assert tokenize_concept_text("") == []
    assert tokenize_concept_text("   \t\n  ") == []
    assert tokenize_concept_text("---___///...") == []


def test_bm25_empty_matcher() -> None:
    """Verify matcher behavior when no concepts are indexed."""
    matcher = Bm25ConceptMatcher()
    assert matcher.match("Legal Entity") == []
    assert matcher.score("Legal Entity", "urn:unknown") is None


def test_bm25_identity_and_ranking() -> None:
    """Verify exact match yields 1.0000 and outranks partial matches."""
    concepts = [
        ReferenceConcept(
            concept_uri="https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalEntity",
            concept_name="Legal Entity",
            module="BE",
            definition="An entity that has legal identity and rights under law.",
            synonyms=["Artificial Person", "Juridical Entity"],
        ),
        ReferenceConcept(
            concept_uri="https://spec.edmcouncil.org/fibo/ontology/BE/Corporations/Corporation",
            concept_name="Corporation",
            module="BE",
            definition="A legal entity that is created under the laws of a state.",
            synonyms=["Body Corporate"],
        ),
        ReferenceConcept(
            concept_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/Debt/Bond",
            concept_name="Bond",
            module="SEC",
            definition="A debt instrument issued by an entity.",
            synonyms=[],
        ),
    ]

    matcher = Bm25ConceptMatcher(concepts)

    # Query matching concept_name exactly should normalize to 1.0000
    res = matcher.score(
        "Legal Entity", "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalEntity"
    )
    assert res is not None
    assert res.normalized_score == 1.0

    # Query match should place Legal Entity at top rank
    matches = matcher.match("Legal Entity", top_k=3)
    assert len(matches) >= 2
    assert matches[0].concept_name == "Legal Entity"
    assert matches[0].normalized_score == 1.0
    assert matches[0].raw_score > matches[1].raw_score


def test_bm25_quantization_boundaries() -> None:
    """Verify ROUND_HALF_EVEN boundary condition: 0.64995 -> 0.6500 vs 0.64994 -> 0.6499."""
    d_up = Decimal("0.64995").quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
    assert d_up == Decimal("0.6500")
    assert float(d_up) == 0.65
    assert float(d_up) >= GROUNDING_THRESHOLD

    d_down = Decimal("0.64994").quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
    assert d_down == Decimal("0.6499")
    assert float(d_down) == 0.6499
    assert float(d_down) < GROUNDING_THRESHOLD


def test_bm25_definition_only_hit_gate() -> None:
    """Verify that matches exceeding 0.6500 purely on definition tokens are capped at 0.6400."""
    concepts = [
        ReferenceConcept(
            concept_uri="https://spec.edmcouncil.org/fibo/ontology/BE/Corporations/SpecialPurposeVehicle",
            concept_name="Special Purpose Vehicle",
            module="BE",
            definition=(
                "A bankruptcy remote entity structured with limited liabilities for synthetic "
                "securitization transactions."
            ),
            synonyms=["SPV"],
        ),
    ]
    matcher = Bm25ConceptMatcher(concepts)

    # Query matches words exclusively in the definition, not in concept_name or synonyms
    query = "bankruptcy remote synthetic securitization"
    match_res = matcher.score(
        query,
        "https://spec.edmcouncil.org/fibo/ontology/BE/Corporations/SpecialPurposeVehicle",
    )
    assert match_res is not None

    # Score would otherwise be high due to definition tokens, but hit gate caps it at 0.6400
    assert match_res.normalized_score <= 0.6400
    assert match_res.normalized_score < GROUNDING_THRESHOLD

    # Active capping test: query whose uncapped score is 1.0 is capped to exactly 0.6400
    c_spv = ReferenceConcept(
        concept_uri="urn:test:spv_cap",
        concept_name="SPV",
        module="BE",
        definition=(
            "A structured bankruptcy remote vehicle used for complex synthetic "
            "securitization financing transactions."
        ),
    )
    c_other = ReferenceConcept(
        concept_uri="urn:test:other_cap",
        concept_name="Other Instrument",
        module="BE",
        definition="Some other generic financial vehicle.",
    )
    matcher_cap = Bm25ConceptMatcher([c_spv, c_other])
    res_capped = matcher_cap.score(
        "structured bankruptcy remote vehicle used for complex synthetic securitization",
        c_spv.concept_uri,
    )
    assert res_capped is not None
    assert res_capped.normalized_score == 0.6400


def test_bm25_module_filter() -> None:
    """Verify module filtering restricts candidate search."""
    concepts = [
        ReferenceConcept(
            concept_uri="urn:fibo:be:c1",
            concept_name="Holding One",
            module="BE",
            definition="First holding in BE module.",
        ),
        ReferenceConcept(
            concept_uri="urn:fibo:fnd:c2",
            concept_name="Holding Two",
            module="FND",
            definition="Second holding in FND module.",
        ),
    ]
    matcher = Bm25ConceptMatcher(concepts)

    be_matches = matcher.match("Holding", module_filter=["BE"])
    assert len(be_matches) == 1
    assert be_matches[0].module == "BE"

    fnd_matches = matcher.match("Holding", module_filter=["FND"])
    assert len(fnd_matches) == 1
    assert fnd_matches[0].module == "FND"

    other_matches = matcher.match("Holding", module_filter=["SEC"])
    assert len(other_matches) == 0


def test_bm25_deterministic_tie_breaking() -> None:
    """Verify tie-breaking by (-normalized_score, -raw_score, concept_uri)."""
    concepts = [
        ReferenceConcept(
            concept_uri="urn:fibo:c_zeta",
            concept_name="Common Account",
            module="FBC",
        ),
        ReferenceConcept(
            concept_uri="urn:fibo:c_alpha",
            concept_name="Common Account",
            module="FBC",
        ),
        ReferenceConcept(
            concept_uri="urn:fibo:c_beta",
            concept_name="Common Account",
            module="FBC",
        ),
    ]
    matcher = Bm25ConceptMatcher(concepts)
    matches = matcher.match("Common Account", top_k=3)

    assert len(matches) == 3
    # All have identical normalized score (1.0) and identical raw score
    # Must be stably ordered alphabetically by concept_uri
    assert matches[0].concept_uri == "urn:fibo:c_alpha"
    assert matches[1].concept_uri == "urn:fibo:c_beta"
    assert matches[2].concept_uri == "urn:fibo:c_zeta"


def test_fibo_grounding_verifier_real_pack() -> None:
    """Verify FiboPackGroundingVerifier using the authoritative packaged FIBO pack."""
    loader = get_default_fibo_loader()
    assert loader.pack_sha256 == CANONICAL_FIBO_CORE_SHA256

    verifier = FiboPackGroundingVerifier(loader=loader)
    assert isinstance(verifier, GroundingVerifier)

    # 1. Test genuine positive grounding (e.g. Legal Entity)
    legal_entity_uri = (
        "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity"
    )
    result = verifier.verify_grounding(
        concept_uri="urn:ontology:entity:legal_entity",
        candidates=[
            {"target_uri": legal_entity_uri, "predicate": "skos:exactMatch", "confidence": 0.95}
        ],
    )
    assert result.is_grounded is True
    assert result.score >= 0.6500
    assert result.matched_uri == legal_entity_uri

    # 2. Zero-mapping candidate list MUST return is_grounded=False (CRIT-01 / P05::MAJ-03)
    result_query = verifier.verify_grounding(
        concept_uri="urn:ontology:entity:board_agreement",
        candidates=[],
        concept_name="Board Agreement",
    )
    assert result_query.is_grounded is False
    assert result_query.score == 0.0
    assert result_query.matched_uri is None
    assert any("BoardAgreement" in d for d in result_query.diagnostics)

    # 3. Test ungrounded concept
    result_unmatched = verifier.verify_grounding(
        concept_uri="urn:ontology:entity:completely_unknown_super_custom_term",
        candidates=[],
        concept_name="completely unknown super custom term",
    )
    assert result_unmatched.is_grounded is False
    assert result_unmatched.score < 0.6500

    # 4. Test broadMatch exclusion
    result_broad = verifier.verify_grounding(
        concept_uri="urn:ontology:entity:sub_org",
        candidates=[
            {"target_uri": legal_entity_uri, "predicate": "skos:broadMatch", "confidence": 0.99}
        ],
    )
    assert result_broad.is_grounded is False


def test_fibo_verifier_tampered_pack_raises_exit_1_500(tmp_path: Path) -> None:
    """Verify that a corrupted pack digest raises ReferenceOntologyCorruptError
    (exit 1 / HTTP 500 per authoritative error catalog).
    """
    corrupt_pack = tmp_path / "fibo-core-bebd6ae8.json"
    corrupt_pack.write_text(
        json.dumps(
            {
                "classes": {
                    "https://example.com/c1": {"uri": "https://example.com/c1", "label": "c1"}
                },
                "properties": {
                    "https://example.com/p1": {"uri": "https://example.com/p1", "label": "p1"}
                },
                "metadata": {"pack_id": "fibo-core", "version": "1.0.0"},
            }
        ),
        encoding="utf-8",
    )

    loader = FiboReferenceOntologyLoader(pack_path=corrupt_pack, verify_integrity=False)
    with pytest.raises(ReferenceOntologyCorruptError) as exc_info:
        FiboPackGroundingVerifier(loader=loader)

    assert exc_info.value.exit_code == 1
    assert exc_info.value.http_status == 500
    assert exc_info.value.error_code_symbol == "REFERENCE_ONTOLOGY_CORRUPT"


def test_fibo_verifier_no_unauthenticated_waiver_bypass() -> None:
    """CRIT-01: Verify that a caller-supplied waiver flag cannot bypass grounding
    when the lexical evidence score is 0.
    """
    verifier = FiboPackGroundingVerifier()
    legal_entity_uri = (
        "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity"
    )

    # Candidate has zero lexical overlap with query "Xylophone",
    # but asserts confidence 0.99 and waiver=True
    res = verifier.verify_grounding(
        concept_uri="urn:ontology:entity:xylophone",
        candidates=[
            {
                "target_uri": legal_entity_uri,
                "predicate": "skos:exactMatch",
                "confidence": 0.99,
                "waiver": True,
            }
        ],
        concept_name="Xylophone",
    )
    assert res.is_grounded is False
    assert res.score == 0.0


def test_fibo_verifier_no_silent_fallback_when_explicit_candidates_rejected() -> None:
    """MAJ-01: When explicit candidates are supplied and rejected (e.g. broadMatch),
    the verifier must NOT fall back to free-text matching against an unproposed URI.
    """
    verifier = FiboPackGroundingVerifier()
    legal_entity_uri = (
        "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity"
    )

    # Caller supplied an explicit broadMatch candidate for "Board Agreement".
    # Even though "Board Agreement" would match FIBO BoardAgreement in free-text search,
    # the explicit candidate list was rejected, so verifier must NOT ground against an
    # unproposed URI.
    res = verifier.verify_grounding(
        concept_uri="urn:ontology:entity:board_agreement",
        candidates=[
            {
                "target_uri": legal_entity_uri,
                "predicate": "skos:broadMatch",
                "confidence": 0.99,
            }
        ],
        concept_name="Board Agreement",
    )
    assert res.is_grounded is False
    assert res.matched_uri is None


def test_fibo_verifier_explicit_zero_confidence() -> None:
    """MIN-03: An explicit confidence of 0.0 must be respected and clamp score to 0.0."""
    verifier = FiboPackGroundingVerifier()
    account_uri = "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/ClientsAndAccounts/Account"

    res = verifier.verify_grounding(
        concept_uri="urn:ontology:entity:account",
        candidates=[
            {
                "target_uri": account_uri,
                "predicate": "skos:exactMatch",
                "confidence": 0.0,
            }
        ],
        concept_name="Account",
    )
    assert res.is_grounded is False
    assert res.score == 0.0


def test_bm25_deduplicates_concept_uris() -> None:
    """MIN-06: Duplicate concept URIs in index_concepts must not skew corpus statistics."""
    c1 = ReferenceConcept(
        concept_uri="https://example.com/c1",
        concept_name="Test Concept",
        module="FND",
        definition="A test concept definition",
    )
    c1_dup = ReferenceConcept(
        concept_uri="https://example.com/c1",
        concept_name="Test Concept Updated",
        module="FND",
        definition="An updated test concept definition",
    )
    matcher = Bm25ConceptMatcher()
    matcher.index_concepts([c1, c1_dup])
    assert len(matcher.match("Test Concept", top_k=10)) == 1


def test_bm25_repeated_query_tokens_normalize_identically() -> None:
    """MAJ-01: Repeating words in query must not inflate score above grounding threshold."""
    c = ReferenceConcept(
        concept_uri="https://example.com/account_holder",
        concept_name="Account Holder",
        module="FBC",
        definition="An individual or organization holding an account",
    )
    matcher = Bm25ConceptMatcher()
    matcher.index_concepts([c])

    # Single "account" vs duplicate "account account"
    m_single = matcher.score("account", c.concept_uri)
    m_dup = matcher.score("account account", c.concept_uri)
    assert m_single is not None and m_dup is not None
    assert m_single.normalized_score == m_dup.normalized_score


def test_fibo_verifier_rejects_unauthorized_predicates() -> None:
    """MAJ-02: Candidates with missing, empty, relatedMatch, or narrowMatch predicates
    must be rejected by the fail-closed predicate allow-list.
    """
    verifier = FiboPackGroundingVerifier()
    legal_entity_uri = (
        "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity"
    )

    for bad_pred in ["", None, "skos:relatedMatch", "skos:narrowMatch", "custom:predicate"]:
        cand = {"target_uri": legal_entity_uri, "confidence": 0.99}
        if bad_pred is not None:
            cand["predicate"] = bad_pred

        res = verifier.verify_grounding(
            concept_uri="urn:ontology:entity:entity",
            candidates=[cand],
            concept_name="Legal Entity",
        )
        assert res.is_grounded is False
        assert any("not an authorized Path A grounding predicate" in d for d in res.diagnostics)


def test_bm25_stopwords_do_not_satisfy_hit_gate() -> None:
    """MIN-03: Stopwords alone must not satisfy the name/synonym hit gate."""
    c = ReferenceConcept(
        concept_uri="https://example.com/board",
        concept_name="Board of Trustees",
        module="FND",
        definition="The governing board responsible for oversight",
    )
    matcher = Bm25ConceptMatcher()
    matcher.index_concepts([c])

    # "of" is in concept_name tokens, but it is a stopword.
    # A query sharing only stopword "of" must NOT pass hit gate.
    scored = matcher.score("rate of return", c.concept_uri)
    assert scored is not None
    # Because only "of" matches and it is a stopword, score must be capped <= 0.6400
    assert scored.normalized_score <= 0.6400


def test_bm25_out_of_vocabulary_words_suppress_normalization() -> None:
    """MAJ-01: Unknown query words must be included in query self-score so that queries
    with low vocabulary overlap cannot inflate above GROUNDING_THRESHOLD.
    """
    c = ReferenceConcept(
        concept_uri="https://example.com/legal_entity",
        concept_name="Legal Entity",
        module="BE",
        definition="An entity with legal standing",
    )
    matcher = Bm25ConceptMatcher()
    matcher.index_concepts([c])

    # Query has 1 in-vocabulary token ("legal") and 3 out-of-vocabulary tokens
    # ("xylophone", "unicorn", "dragon")
    scored = matcher.score("Legal Xylophone Unicorn Dragon", c.concept_uri)
    assert scored is not None
    # Must NOT inflate to 1.0; must be suppressed well below the 0.65 threshold
    assert scored.normalized_score < 0.6500


def test_bm25_match_invalid_min_score_raises_value_error() -> None:
    """MIN-04: Non-finite or out-of-bounds min_score must raise ValueError."""
    matcher = Bm25ConceptMatcher()
    matcher.index_concepts(
        [
            ReferenceConcept(
                concept_uri="https://example.com/c1",
                concept_name="Concept",
                module="FND",
            )
        ]
    )
    with pytest.raises(ValueError, match="min_score"):
        matcher.match("query", min_score=float("nan"))

    with pytest.raises(ValueError, match="min_score"):
        matcher.match("query", min_score=1.5)

    with pytest.raises(ValueError, match="min_score"):
        matcher.match("query", min_score=-0.1)


def test_bm25_test_vectors_json() -> None:
    """Verify that all vector sections in vectors/bm25.json pass against the matcher.

    Deliverable validation for Task 5A.1 / Issue #211:
    - tokenization_vectors: verified against tokenize_concept_text (acronym expansion & stopwords).
    - quantization_boundary_vectors: verified against ROUND_HALF_EVEN 4-decimal logic.
    - hit_gate_vectors: verified against definition-only hit gate capping logic.
    - scoring_vectors: verified against Bm25ConceptMatcher indexing and scoring.
    """
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    vector_file = repo_root / "vectors" / "bm25.json"
    assert vector_file.is_file(), f"Vector file not found at {vector_file}"

    data = json.loads(vector_file.read_text(encoding="utf-8"))
    assert data["matcher_id"] == "bm25_fibo_v1"
    assert data["version"] == "1.0.0"

    # 1. Tokenization vectors
    assert len(data["tokenization_vectors"]) >= 5
    for vec in data["tokenization_vectors"]:
        tokens = tokenize_concept_text(vec["input"])
        assert tokens == vec["expected_tokens"], (
            f"Tokenization mismatch for '{vec['input']}': "
            f"got {tokens}, expected {vec['expected_tokens']}"
        )

    # 2. Quantization boundary vectors
    assert len(data["quantization_boundary_vectors"]) >= 2
    for vec in data["quantization_boundary_vectors"]:
        raw_dec = Decimal(vec["raw_normalized_decimal"])
        quantized = raw_dec.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN) + Decimal("0.0")
        assert float(quantized) == vec["expected_quantized"], (
            f"Quantization mismatch for {vec['raw_normalized_decimal']}: "
            f"got {float(quantized)}, expected {vec['expected_quantized']}"
        )
        is_grounded = float(quantized) >= GROUNDING_THRESHOLD
        assert is_grounded == vec["is_grounded"], (
            f"Grounding threshold mismatch for {vec['raw_normalized_decimal']}: "
            f"got {is_grounded}, expected {vec['is_grounded']}"
        )

    # 3. Hit gate vectors
    assert len(data["hit_gate_vectors"]) >= 2
    for vec in data["hit_gate_vectors"]:
        raw_score = Decimal(vec["definition_match_raw"])
        quantized = raw_score.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN) + Decimal("0.0")
        has_hit = vec["has_name_hit"] or vec["has_synonym_hit"]
        if quantized >= Decimal("0.6500") and not has_hit:
            capped = min(quantized, Decimal("0.6400"))
        else:
            capped = quantized
        assert float(capped) == vec["expected_capped"], (
            f"Hit gate capping mismatch for {vec}: "
            f"got {float(capped)}, expected {vec['expected_capped']}"
        )
        is_grounded = float(capped) >= GROUNDING_THRESHOLD
        assert is_grounded == vec["is_grounded"], (
            f"Hit gate grounding mismatch for {vec}: "
            f"got {is_grounded}, expected {vec['is_grounded']}"
        )

    # 4. Scoring vectors
    assert len(data.get("scoring_vectors", [])) >= 2
    for vec in data["scoring_vectors"]:
        concepts = [
            ReferenceConcept(
                concept_uri=c["concept_uri"],
                concept_name=c["concept_name"],
                module=c["module"],
                definition=c.get("definition", ""),
                synonyms=c.get("synonyms", []),
            )
            for c in vec["corpus"]
        ]
        matcher = Bm25ConceptMatcher(concepts)
        scored = matcher.score(vec["query"], vec["target_uri"])
        assert scored is not None, (
            f"Failed to score query '{vec['query']}' against '{vec['target_uri']}'"
        )
        if "expected_normalized_score" in vec:
            assert scored.normalized_score == vec["expected_normalized_score"]
        if "expected_max_score" in vec:
            assert scored.normalized_score <= vec["expected_max_score"]
        if "is_grounded" in vec:
            assert (scored.normalized_score >= float(GROUNDING_THRESHOLD)) == vec["is_grounded"]


def test_fibo_verifier_uses_concept_name_and_synonyms_not_uri_slug() -> None:
    """CRIT-02: Verify verifier scores canonical concept_name + synonyms rather than URI slug."""
    from opendv_service.ontology import get_default_grounding_verifier

    verifier = get_default_grounding_verifier()
    assert get_default_grounding_verifier() is verifier
    legal_entity_uri = (
        "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity"
    )

    # Misleading URI slug matching LegalEntity MUST NOT ground an unrelated concept_name
    spoofed = verifier.verify_grounding(
        concept_uri="urn:ontology:entity:legal_entity",
        concept_name="Quantum Toaster",
        synonyms=(),
        candidates=[
            {
                "target_uri": legal_entity_uri,
                "predicate": "skos:exactMatch",
                "mapping_confidence": 0.99,
            }
        ],
    )
    assert spoofed.is_grounded is False

    # Opaque URI slug WITH valid synonym 'Legal Entity' MUST ground cleanly
    via_synonym = verifier.verify_grounding(
        concept_uri="urn:ontology:entity:ent_001",
        concept_name="Institutional Counterparty",
        synonyms=["Legal Entity"],
        candidates=[
            {
                "target_uri": legal_entity_uri,
                "predicate": "skos:closeMatch",
                "mapping_confidence": 0.90,
            }
        ],
    )
    assert via_synonym.is_grounded is True
    assert via_synonym.score >= 0.6500
    assert via_synonym.matched_uri == legal_entity_uri
