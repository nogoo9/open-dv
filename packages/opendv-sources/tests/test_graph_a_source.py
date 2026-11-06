"""Unit tests for load_graph_a_source and LoadedGraphA (ONT-01)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from opendv_core.models.graph_a import GraphAOntology
from opendv_sources import (
    FormatError,
    LoadedGraphA,
    PathContainmentError,
    SourceNotFoundError,
    SourceSpec,
    load_graph_a_source,
)

SAMPLE_ONTOLOGY_DICT = {
    "ontology_version": "1.0.0",
    "domain_namespace": "wealth_mgmt",
    "enterprise_owner": "Enterprise Architecture",
    "canonical_business_concepts": [
        {
            "concept_uri": "urn:ontology:entity:portfolio",
            "concept_name": "Portfolio",
            "elm_archetype": "ACCOUNT_LEDGER",
            "definition": "An aggregation of financial holdings managed for a client.",
            "target_hub": "hub_portfolio",
        }
    ],
}


def test_load_graph_a_from_file_path(tmp_path: Path) -> None:
    ont_file = tmp_path / "graph_a.yaml"
    raw_content = yaml.safe_dump(SAMPLE_ONTOLOGY_DICT, sort_keys=False)
    ont_file.write_bytes(raw_content.encode("utf-8"))

    result = load_graph_a_source(ont_file, workspace_root=tmp_path)
    assert isinstance(result, LoadedGraphA)
    assert isinstance(result, tuple)

    # Tuple unpacking
    ont, sha, raw_bytes = result
    assert isinstance(ont, GraphAOntology)
    assert ont.domain_namespace == "wealth_mgmt"
    assert sha == hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
    assert raw_bytes == raw_content.encode("utf-8")

    # Named attributes
    assert result.ontology.enterprise_owner == "Enterprise Architecture"
    assert result.sha256 == sha
    assert result.raw_bytes == raw_bytes


def test_load_graph_a_from_inline_yaml_string() -> None:
    raw_content = yaml.safe_dump(SAMPLE_ONTOLOGY_DICT, sort_keys=False)
    result = load_graph_a_source(raw_content)

    assert result.ontology.ontology_version == "1.0.0"
    assert result.sha256 == hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
    assert result.ontology.canonical_business_concepts[0].concept_name == "Portfolio"


def test_load_graph_a_from_inline_json_string() -> None:
    raw_content = json.dumps(SAMPLE_ONTOLOGY_DICT, indent=2)
    result = load_graph_a_source(raw_content)

    assert result.ontology.ontology_version == "1.0.0"
    assert result.sha256 == hashlib.sha256(raw_content.encode("utf-8")).hexdigest()


def test_load_graph_a_from_source_spec_inline() -> None:
    raw_content = yaml.safe_dump(SAMPLE_ONTOLOGY_DICT, sort_keys=False)
    spec = SourceSpec(
        adapter="inline",
        inline_content=raw_content,
    )
    result = load_graph_a_source(spec)
    assert result.ontology.domain_namespace == "wealth_mgmt"
    assert result.sha256 == hashlib.sha256(raw_content.encode("utf-8")).hexdigest()


def test_load_graph_a_from_source_spec_file(tmp_path: Path) -> None:
    ont_file = tmp_path / "ontology.yaml"
    raw_content = yaml.safe_dump(SAMPLE_ONTOLOGY_DICT, sort_keys=False)
    ont_file.write_bytes(raw_content.encode("utf-8"))

    spec = SourceSpec(
        adapter="file",
        uri="ontology.yaml",
    )
    result = load_graph_a_source(spec, workspace_root=tmp_path)
    assert result.ontology.domain_namespace == "wealth_mgmt"
    assert result.sha256 == hashlib.sha256(raw_content.encode("utf-8")).hexdigest()


def test_load_graph_a_from_source_spec_dict() -> None:
    raw_content = yaml.safe_dump(SAMPLE_ONTOLOGY_DICT, sort_keys=False)
    spec_dict = {
        "adapter": "inline",
        "inline_content": raw_content,
    }
    result = load_graph_a_source(spec_dict)
    assert result.ontology.domain_namespace == "wealth_mgmt"


def test_load_graph_a_from_raw_ontology_dict() -> None:
    result = load_graph_a_source(SAMPLE_ONTOLOGY_DICT)
    assert result.ontology.domain_namespace == "wealth_mgmt"
    assert len(result.sha256) == 64
    assert result.raw_bytes is not None


def test_load_graph_a_with_parent_sha256_and_decision_refs() -> None:
    parent_sha = "f" * 64
    evolved_dict = {
        **SAMPLE_ONTOLOGY_DICT,
        "ontology_version": "1.1.0",
        "parent_sha256": parent_sha,
        "decision_refs": ["DEC-42", "DEC-43"],
    }
    result = load_graph_a_source(evolved_dict)
    assert result.ontology.ontology_version == "1.1.0"
    assert result.ontology.parent_sha256 == parent_sha
    assert result.ontology.decision_refs == ["DEC-42", "DEC-43"]


def test_load_graph_a_not_found(tmp_path: Path) -> None:
    with pytest.raises(SourceNotFoundError):
        load_graph_a_source("non_existent_ontology.yaml", workspace_root=tmp_path)


def test_load_graph_a_path_traversal_rejected(tmp_path: Path) -> None:
    with pytest.raises(PathContainmentError):
        load_graph_a_source("../outside.yaml", workspace_root=tmp_path)


def test_load_graph_a_empty_content_rejected() -> None:
    with pytest.raises(FormatError):
        load_graph_a_source("   ")


def test_load_graph_a_invalid_schema_rejected() -> None:
    invalid_dict = {"ontology_version": "1.0.0", "missing_domain": True}
    with pytest.raises(ValidationError):
        load_graph_a_source(invalid_dict)
