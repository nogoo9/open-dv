"""Tests for V2 static JSON FIBO pack, subclass closure, and V2 starter packs."""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

import jsonschema
import pytest

from opendv.models.errors import ErrorCode, OpenDVError, ReferenceOntologyCorruptError
from opendv.models.ontology import ExternalMapping, GraphAOntology
from opendv.sources.fibo import (
    BROAD_MATCH_PREDICATE,
    CANONICAL_FIBO_CORE_SHA256,
    FiboReferenceOntologyLoader,
    SubclassClosureResolver,
    TaxonomyCycleError,
    UngroundedSeedMappingError,
    can_satisfy_path_a_grounding,
    clear_fibo_loader_cache,
    get_default_fibo_loader,
    resolve_subclass_closure,
    validate_closure_grounding_eligibility,
)
from opendv.sources.starter_packs import (
    STARTER_PACK_IDS,
    compose_starter_packs,
    compute_starter_pack_sha256,
    get_starter_pack_ontology,
    list_starter_packs,
    preview_starter_pack,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BASELINE_DIGESTS_PATH = REPO_ROOT / "schemas" / "v2" / "baseline-digests.json"
BASELINE_SCHEMA_PATH = REPO_ROOT / "schemas" / "v2" / "baseline-digests.schema.json"


def test_starter_packs_v2_models_and_baseline_digests() -> None:
    """UAT-S1-05: All 8 V2 starter packs validate against V2 GraphAOntology & baseline-digests."""
    baselines = json.loads(BASELINE_DIGESTS_PATH.read_text(encoding="utf-8"))
    schema = json.loads(BASELINE_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(instance=baselines, schema=schema)

    summaries = list_starter_packs()
    assert [s.pack_id for s in summaries] == list(STARTER_PACK_IDS)
    assert len(summaries) == 8

    loader = get_default_fibo_loader()

    for summary in summaries:
        preview = preview_starter_pack(summary.pack_id)
        ont = preview.ontology
        assert isinstance(ont, GraphAOntology)
        assert len(ont.concepts) == summary.hub_count
        assert all("planned_stub" in c.governance_tags for c in ont.concepts)
        assert all(c.table_bindings == [] for c in ont.concepts)
        assert any(c.key_authority_patterns for c in ont.concepts)
        assert all(c.external_mappings for c in ont.concepts)
        for concept in ont.concepts:
            for mapping in concept.external_mappings:
                assert loader.lookup_by_uri(mapping.standard_uri) is not None, (
                    summary.pack_id,
                    concept.concept_name,
                    mapping.standard_uri,
                )
                assert can_satisfy_path_a_grounding(mapping.mapping_type)
                assert mapping.confidence >= Decimal("0.6500")

        digest = compute_starter_pack_sha256(ont)
        entry = baselines["starter_packs"][summary.pack_id]
        assert "path" not in entry
        assert entry["domain_namespace"] == summary.domain_namespace
        assert entry["hub_count"] == summary.hub_count
        assert entry["sha256"] == digest

    all_ont = get_starter_pack_ontology("all_domains")
    assert len(all_ont.concepts) == 31
    assert len(all_ont.relationships) == 29
    assert len(all_ont.enumerations) == 7

    composed = compose_starter_packs(["public_equities_oms", "custody_clearing"])
    assert len(composed.concepts) == 14

    with pytest.raises(OpenDVError) as exc_info:
        get_starter_pack_ontology("unknown_pack_999")
    assert exc_info.value.error_code == ErrorCode.RESOURCE_NOT_FOUND


def test_fibo_loader_integrity_and_zero_rdflib_runtime(tmp_path: Path) -> None:
    """FIBO pack loads fast with zero rdflib, verifies digests, and fails loudly on tamper."""
    clear_fibo_loader_cache()
    assert "rdflib" not in sys.modules or "opendv.sources" not in str(sys.modules["rdflib"])

    loader = get_default_fibo_loader()
    assert loader.pack_sha256 == CANONICAL_FIBO_CORE_SHA256
    assert loader.class_count == 2206
    assert loader.property_count == 999

    le_uri = "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity"
    entry = loader.lookup_by_uri(le_uri)
    assert entry is not None
    assert entry["entity_type"] == "class"

    hits = loader.search_candidates("Bond", limit=5)
    assert len(hits) >= 1
    assert hits[0]["match_score"] == 1.0

    # Tampered copy triggers ReferenceOntologyCorruptError (REFERENCE_PACK_INTEGRITY_FAILURE)
    tampered = tmp_path / "fibo-core-pack.json"
    raw = loader.pack_path.read_bytes()
    tampered.write_bytes(raw[:-1] + b" ")
    with pytest.raises(ReferenceOntologyCorruptError) as corrupt_exc:
        FiboReferenceOntologyLoader(pack_path=tampered)
    assert corrupt_exc.value.error_code == ErrorCode.REFERENCE_PACK_INTEGRITY_FAILURE


def test_fibo_closure_grounding_cycle_and_v2_external_mapping() -> None:
    """SubclassClosureResolver enforces >=0.6500 threshold, cycles, and V2 ExternalMapping."""
    loader = get_default_fibo_loader()
    bond_uri = "https://spec.edmcouncil.org/fibo/ontology/SEC/Debt/Bonds/Bond"
    matches = resolve_subclass_closure(loader, bond_uri, Decimal("0.8500"))
    assert len(matches) >= 1
    assert all(m.predicate == BROAD_MATCH_PREDICATE for m in matches)
    validate_closure_grounding_eligibility(matches)
    assert can_satisfy_path_a_grounding(BROAD_MATCH_PREDICATE) is False
    assert can_satisfy_path_a_grounding("skos:closeMatch") is True

    v2_mapping = matches[0].to_external_mapping()
    assert isinstance(v2_mapping, ExternalMapping)
    assert v2_mapping.mapping_type == "skos:closeMatch"
    assert v2_mapping.confidence == Decimal("0.8500")

    with pytest.raises(UngroundedSeedMappingError):
        resolve_subclass_closure(loader, bond_uri, Decimal("0.6499"), raise_on_ungrounded=True)
    assert (
        resolve_subclass_closure(loader, bond_uri, Decimal("0.6499"), raise_on_ungrounded=False)
        == []
    )

    class _CyclicLoader:
        pack_id = "mock-cycle"
        pack_sha256 = "0" * 64

        def get_direct_superclasses(self, uri: str) -> list[str]:
            return {"urn:a": ["urn:b"], "urn:b": ["urn:a"]}.get(uri, [])

        def lookup_by_uri(self, uri: str) -> dict[str, str]:
            return {"uri": uri, "label": uri}

    resolver = SubclassClosureResolver(_CyclicLoader())
    safe_res = resolver.resolve("urn:a", Decimal("0.8000"), raise_on_cycle=False)
    assert [m.uri for m in safe_res] == ["urn:b"]
    with pytest.raises(TaxonomyCycleError):
        resolver.resolve("urn:a", Decimal("0.8000"), raise_on_cycle=True)
