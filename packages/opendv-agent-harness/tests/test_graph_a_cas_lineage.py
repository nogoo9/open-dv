"""Tests for Graph A CAS registration, lineage fields, and source adapters (ONT-01)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from opendv_agent_harness.pipeline import (
    AgenticPipelineReport,
    PipelineInputSpec,
    run_agentic_vault_pipeline,
)
from opendv_sources import SourceSpec
from opendv_telemetry import ContentAddressableStore
from opendv_testkit import build_deterministic_agy_sdk_shim

REPO_ROOT = Path(__file__).resolve().parents[3]


def _sample_dir() -> Path:
    return REPO_ROOT / "examples" / "pipeline_samples" / "global_commerce_vault"


def test_pipeline_input_spec_graph_a_source_and_validation() -> None:
    """PipelineInputSpec requires either graph_a_source or graph_a_ontology_path."""
    sample_dir = _sample_dir()
    base_spec = PipelineInputSpec.from_file(sample_dir / "pipeline_input.yaml")

    # Pass with graph_a_ontology_path
    assert base_spec.graph_a_ontology_path is not None

    # Pass with graph_a_source (SourceSpec)
    spec_with_source = base_spec.model_copy(
        update={
            "graph_a_ontology_path": None,
            "graph_a_source": SourceSpec(
                adapter="file",
                uri="ontology/commerce_graph_a_ontology.yaml",
            ),
        }
    )
    assert spec_with_source.graph_a_source is not None
    assert spec_with_source.graph_a_ontology_path is None

    # Fail if neither is provided
    with pytest.raises(ValidationError) as exc:
        spec_dict = base_spec.model_dump()
        spec_dict["graph_a_ontology_path"] = None
        spec_dict["graph_a_source"] = None
        PipelineInputSpec.model_validate(spec_dict)
    assert "Either 'graph_a_source' or 'graph_a_ontology_path' must be provided" in str(exc.value)


def test_pipeline_report_carries_graph_a_lineage_and_cas_sha(tmp_path: Path) -> None:
    """Report carries graph_a_sha256, ontology_version, and registers bytes in CAS."""
    sample_dir = _sample_dir()
    out_dir = tmp_path / "out"
    cas = ContentAddressableStore(out_dir / "cas")

    report: AgenticPipelineReport = run_agentic_vault_pipeline(
        sample_dir / "pipeline_input.yaml",
        sample_dir / "harness_config.yaml",
        output_dir=out_dir,
        base_dir=sample_dir,
        cas_store=cas,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )

    # 1. Report carries lineage
    assert report.graph_a_sha256 is not None
    assert len(report.graph_a_sha256) == 64
    assert report.ontology_version == "1.0.0"
    assert report.parent_sha256 is None
    assert report.decision_refs == []

    # 2. Verify CAS registration
    ont_bytes = cas.get_bytes(report.graph_a_sha256)
    assert ont_bytes is not None
    assert hashlib.sha256(ont_bytes).hexdigest() == report.graph_a_sha256

    # 3. Artifact reference matches
    s0_art = next(
        a for a in report.intermediate_artifacts if a.artifact_id == "art-stage00-graph-a-ontology"
    )
    assert s0_art.sha256.lower() == report.graph_a_sha256


def test_pipeline_with_evolved_graph_a_source_changes_sha_and_preserves_lineage(
    tmp_path: Path,
) -> None:
    """Evolved Graph A passed via graph_a_source produces distinct SHA and propagates lineage."""
    sample_dir = _sample_dir()
    out_dir = tmp_path / "out"
    cas = ContentAddressableStore(out_dir / "cas")

    base_spec = PipelineInputSpec.from_file(sample_dir / "pipeline_input.yaml")
    ont_path = sample_dir / base_spec.graph_a_ontology_path
    ont_data = yaml.safe_load(ont_path.read_text(encoding="utf-8"))

    # Evolve ontology: bump version, add parent_sha256 and decision_refs
    parent_sha = "d" * 64
    ont_data["ontology_version"] = "1.2.0"
    ont_data["parent_sha256"] = parent_sha
    ont_data["decision_refs"] = ["DEC-101", "DEC-102"]

    evolved_yaml = yaml.safe_dump(ont_data, sort_keys=False)
    evolved_sha = hashlib.sha256(evolved_yaml.encode("utf-8")).hexdigest()

    evolved_spec = base_spec.model_copy(
        update={
            "graph_a_ontology_path": None,
            "graph_a_source": SourceSpec(
                adapter="inline",
                inline_content=evolved_yaml,
            ),
        }
    )

    report: AgenticPipelineReport = run_agentic_vault_pipeline(
        evolved_spec,
        sample_dir / "harness_config.yaml",
        output_dir=out_dir,
        base_dir=sample_dir,
        cas_store=cas,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )

    assert report.graph_a_sha256 == evolved_sha
    assert report.ontology_version == "1.2.0"
    assert report.parent_sha256 == parent_sha
    assert report.decision_refs == ["DEC-101", "DEC-102"]

    # Verify CAS entry
    stored_bytes = cas.get_bytes(evolved_sha)
    assert stored_bytes == evolved_yaml.encode("utf-8")
