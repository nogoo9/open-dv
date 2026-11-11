"""Empirical Adversarial Test Suite for Milestone M4 Quality Gate.

Focus:
1. Canonical PE Graph A SHA-256 Stability (exact digest 1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81)
2. All certified baseline fixtures stability
3. Genesis draft baseline digest stability (99d4aa051a49f40596c53210592853fde09ee6995db5b1ff0ca8e57170dffa8e)
4. Field invariance across None vs empty collection vs omitted extensions
5. Serialization roundtrips (YAML, JSON, JCS, key reordering, Unicode NFC)
6. Sensitivity tests (counter-examples showing genuine modifications alter the hash)
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
from pathlib import Path
from typing import Any

import pytest
import yaml

from opendv_core import compute_ontology_sha256, validate_graph_a
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    ConceptAttribute,
    ExternalMapping,
    GraphAOntology,
)
from opendv_core.ontology_hashing import (
    _norm_empty,
    canonicalize_graph_a_dict,
    dumps_jcs_bytes,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PE_PATH = REPO_ROOT / "examples" / "private_equity" / "pe_graph_a_ontology.yaml"
BASELINE_CATALOG_PATH = REPO_ROOT / "schemas" / "v1" / "baseline-digests.json"

EXPECTED_PE_SHA256 = "1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81"
EXPECTED_GENESIS_SHA256 = "99d4aa051a49f40596c53210592853fde09ee6995db5b1ff0ca8e57170dffa8e"


class TestCanonicalPESHA256Empirical:
    """Empirical adversarial verification of Canonical PE Graph A SHA-256."""

    def test_canonical_pe_sha256_exact(self) -> None:
        """Verify pe_graph_a_ontology.yaml evaluates bit-for-bit to EXPECTED_PE_SHA256."""
        assert PE_PATH.exists(), f"PE ontology fixture missing: {PE_PATH}"
        ont = validate_graph_a(PE_PATH)
        assert isinstance(ont, GraphAOntology)
        actual_sha = compute_ontology_sha256(ont)
        assert actual_sha == EXPECTED_PE_SHA256
        assert len(actual_sha) == 64

    def test_all_certified_baseline_digests(self) -> None:
        """Verify all certified ontologies in baseline-digests.json match exactly."""
        assert BASELINE_CATALOG_PATH.exists()
        catalog = json.loads(BASELINE_CATALOG_PATH.read_text(encoding="utf-8"))
        certified = catalog["certified_ontologies"]
        assert len(certified) >= 7

        for name, spec in certified.items():
            fpath = REPO_ROOT / spec["path"]
            assert fpath.exists(), f"Missing fixture {name}: {fpath}"
            ont = validate_graph_a(fpath)
            actual_sha = compute_ontology_sha256(ont)
            expected_sha = spec["canonical_sha256"]
            assert actual_sha == expected_sha, (
                f"Mismatch for {name}: {actual_sha} != {expected_sha}"
            )

    def test_genesis_draft_digest_registered(self) -> None:
        """Verify Genesis Draft digest in baseline-digests.json."""
        catalog = json.loads(BASELINE_CATALOG_PATH.read_text(encoding="utf-8"))
        genesis = catalog["genesis_draft"]
        assert genesis["canonical_sha256"] == EXPECTED_GENESIS_SHA256
        assert genesis["version"] == "0.0.0"

    def test_extension_defaults_none_invariance(self) -> None:
        """Explicitly set extension fields to None on all concepts and verify hash invariance."""
        ont = validate_graph_a(PE_PATH)
        base_sha = compute_ontology_sha256(ont)
        assert base_sha == EXPECTED_PE_SHA256

        raw = ont.model_dump(mode="json", exclude_none=True)
        for cbc in raw.get("canonical_business_concepts", []):
            cbc["parent_concept_uri"] = None
            cbc["attributes"] = None
            cbc["synonyms"] = None
            cbc["external_mappings"] = None
            cbc["metadata"] = None
            cbc["source_hints"] = None
            cbc["deprecated"] = None

        variant_sha = compute_ontology_sha256(raw)
        assert variant_sha == EXPECTED_PE_SHA256

    def test_extension_empty_collections_invariance(self) -> None:
        """Explicitly set extension fields to empty collections ([], {}, '') and verify hash invariance."""
        ont = validate_graph_a(PE_PATH)
        raw = ont.model_dump(mode="json", exclude_none=True)

        for cbc in raw.get("canonical_business_concepts", []):
            cbc["attributes"] = []
            cbc["synonyms"] = []
            cbc["external_mappings"] = []
            cbc["metadata"] = {}
            cbc["source_hints"] = []
            cbc["parent_concept_uri"] = ""
            cbc["deprecated"] = None

        for rel in raw.get("canonical_relationships", []):
            rel["metadata"] = {}
            rel["deprecated"] = None

        for enum in raw.get("reference_enumerations", []):
            enum["metadata"] = {}
            enum["deprecated"] = None

        raw["metadata"] = {}

        variant_sha = compute_ontology_sha256(raw)
        assert variant_sha == EXPECTED_PE_SHA256

    def test_randomized_key_order_invariance(self) -> None:
        """Adversarially shuffle dictionary keys in JSON / dict representations and verify hash invariance."""
        ont = validate_graph_a(PE_PATH)
        raw = ont.model_dump(mode="json", exclude_none=True)

        for seed in [42, 1337, 2026, 9999]:
            rng = random.Random(seed)

            def _shuffle_dict(d: Any) -> Any:
                if isinstance(d, dict):
                    keys = list(d.keys())
                    rng.shuffle(keys)
                    return {k: _shuffle_dict(d[k]) for k in keys}
                if isinstance(d, list):
                    return [_shuffle_dict(x) for x in d]
                return d

            shuffled = _shuffle_dict(raw)
            sha = compute_ontology_sha256(shuffled)
            assert sha == EXPECTED_PE_SHA256

    def test_yaml_and_json_roundtrip_invariance(self) -> None:
        """Verify multiple serialization/deserialization cycles preserve the exact hash."""
        ont = validate_graph_a(PE_PATH)
        sha_initial = compute_ontology_sha256(ont)
        assert sha_initial == EXPECTED_PE_SHA256

        current = ont.model_dump(mode="json", exclude_none=True)
        for _ in range(5):
            # JSON roundtrip
            json_text = json.dumps(current, indent=2)
            loaded_json = json.loads(json_text)
            assert compute_ontology_sha256(loaded_json) == EXPECTED_PE_SHA256

            # YAML roundtrip
            yaml_text = yaml.safe_dump(loaded_json)
            loaded_yaml = yaml.safe_load(yaml_text)
            assert compute_ontology_sha256(loaded_yaml) == EXPECTED_PE_SHA256

            # Pydantic model roundtrip
            remodel = GraphAOntology.model_validate(loaded_yaml)
            assert compute_ontology_sha256(remodel) == EXPECTED_PE_SHA256
            current = remodel.model_dump(mode="json", exclude_none=True)

    def test_sensitivity_oracle_genuine_modifications(self) -> None:
        """Verify that any genuine semantic modification strictly changes the hash."""
        ont = validate_graph_a(PE_PATH)
        base_sha = compute_ontology_sha256(ont)

        # 1. Attribute addition
        raw1 = ont.model_dump(mode="json", exclude_none=True)
        raw1["canonical_business_concepts"][0]["attributes"] = [
            {"attribute_name": "vintage_year", "data_type": "integer"}
        ]
        sha1 = compute_ontology_sha256(raw1)
        assert sha1 != base_sha

        # 2. Synonym addition
        raw2 = ont.model_dump(mode="json", exclude_none=True)
        raw2["canonical_business_concepts"][0]["synonyms"] = ["PE Fund Vehicle"]
        sha2 = compute_ontology_sha256(raw2)
        assert sha2 != base_sha
        assert sha2 != sha1

        # 3. External mapping addition
        raw3 = ont.model_dump(mode="json", exclude_none=True)
        raw3["canonical_business_concepts"][0]["external_mappings"] = [
            {
                "scheme": "FIBO",
                "target_uri": "https://spec.edmcouncil.org/fibo/ontology/SEC/Equities/Share",
                "reference_pack_id": "fibo-core",
                "reference_pack_sha256": "0" * 64,
                "confidence": 0.95,
            }
        ]
        sha3 = compute_ontology_sha256(raw3)
        assert sha3 != base_sha
        assert sha3 != sha1
        assert sha3 != sha2

        # 4. Parent concept URI addition
        raw4 = ont.model_dump(mode="json", exclude_none=True)
        raw4["canonical_business_concepts"][0]["parent_concept_uri"] = "urn:ontology:entity:fund"
        sha4 = compute_ontology_sha256(raw4)
        assert sha4 != base_sha

        # 5. Concept definition character alteration
        raw5 = ont.model_dump(mode="json", exclude_none=True)
        raw5["canonical_business_concepts"][0]["definition"] += "."
        sha5 = compute_ontology_sha256(raw5)
        assert sha5 != base_sha

    def test_legacy_collections_cannot_be_dropped_without_hash_change(self) -> None:
        """Verify that altering legacy collections alters the hash (they are protected)."""
        ont = validate_graph_a(PE_PATH)
        base_sha = compute_ontology_sha256(ont)

        raw = ont.model_dump(mode="json", exclude_none=True)
        # standards_references contains 4 items in PE baseline
        assert len(raw["standards_references"]) == 4
        # Drop one standards reference
        raw["standards_references"] = raw["standards_references"][:3]
        sha = compute_ontology_sha256(raw)
        assert sha != base_sha
