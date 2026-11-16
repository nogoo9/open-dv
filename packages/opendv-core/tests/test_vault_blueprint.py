"""Unit tests for VaultBlueprint v1.0 and BlueprintLineage (WS1.1 / ADR-017).

Validates:
- BlueprintLineage schema and RFC 8785 JCS SHA-256 cryptographic lineage tracking
- VaultBlueprint envelope with GraphAOntology and optional DataVault2ContractV5
- Draft 2020-12 JSON Schema validation against schemas/vault-blueprint-v1.schema.json
- Preservation of GraphAOntology benchmark canonical SHA-256 (1c1004...)
- Multi-run parent blueprint SHA-256 chaining
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from opendv_core.models.contract_v5 import (
    BusinessKeySource,
    ColumnTriageEntry,
    ContractHubSpec,
    ContractLinkHubReference,
    ContractLinkSpec,
    ContractSatelliteSpec,
    NoMistakeDataVault2ContractV5,
)
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    GraphAOntology,
)
from opendv_core.models.vault_blueprint import (
    BlueprintLineage,
    VaultBlueprint,
    compute_blueprint_sha256,
    compute_contract_sha256,
    create_vault_blueprint,
)
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_core.schema_registry import load_structured_file

PE_BENCHMARK_SHA256 = "1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81"


@pytest.fixture
def sample_ontology() -> GraphAOntology:
    return GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="asset_management",
        enterprise_owner="Enterprise Architecture",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:fund",
                concept_name="InvestmentFund",
                definition="A pooled investment vehicle.",
                target_hub="hub_fund",
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:investor",
                concept_name="Investor",
                definition="A legal entity or person committing capital.",
                target_hub="hub_investor",
            ),
        ],
    )


@pytest.fixture
def sample_contract() -> NoMistakeDataVault2ContractV5:
    return NoMistakeDataVault2ContractV5(
        contract_version="5.0",
        domain_namespace="asset_management",
        lifecycle_stage="COLD_START_N1",
        column_triage_ledger=[
            ColumnTriageEntry(
                bronze_table="stg_funds",
                column_name="fund_code",
                elm_classification="BUSINESS_KEY",
                target_dv_entity="hub_fund",
                source_sql_type="VARCHAR(64)",
            ),
            ColumnTriageEntry(
                bronze_table="stg_funds",
                column_name="fund_name",
                elm_classification="DESCRIPTIVE_PAYLOAD",
                target_dv_entity="sat_fund_profile",
                source_sql_type="VARCHAR(255)",
            ),
        ],
        hubs=[
            ContractHubSpec(
                name="hub_fund",
                ontology_concept_uri="urn:ontology:entity:fund",
                business_key_sources=[
                    BusinessKeySource(
                        bronze_table="stg_funds",
                        key_columns=["fund_code"],
                        record_source_code="STG_FUNDS",
                        key_authority_tier="TIER_1_GLOBAL_STANDARD",
                        require_src_qualifier=False,
                    )
                ],
            )
        ],
        links=[
            ContractLinkSpec(
                name="lnk_fund_investor",
                ontology_concept_uri="urn:ontology:relationship:fund_investor",
                link_type="STANDARD_LINK",
                source_bronze_table="stg_funds",
                hub_references=[
                    ContractLinkHubReference(
                        target_hub="hub_fund",
                        role_alias="fund",
                        source_fk_columns=["fund_code"],
                    ),
                    ContractLinkHubReference(
                        target_hub="hub_investor",
                        role_alias="investor",
                        source_fk_columns=["investor_code"],
                    ),
                ],
            )
        ],
        satellites=[
            ContractSatelliteSpec(
                name="sat_fund_profile",
                parent_entity="hub_fund",
                satellite_type="STANDARD_SATELLITE",
                source_bronze_table="stg_funds",
                security_classification="INTERNAL",
                volatility_tier="STANDARD",
                payload_columns=["fund_name"],
            )
        ],
        reference_tables=[],
        unmapped_escalations=[],
    )


@pytest.fixture
def schema_v1() -> dict:
    schema_path = Path(__file__).resolve().parents[3] / "schemas" / "vault-blueprint-v1.schema.json"
    with open(schema_path, encoding="utf-8") as f:
        return json.load(f)


def test_vault_blueprint_greenfield_creation(sample_ontology: GraphAOntology) -> None:
    """Test creating a Day-0 greenfield VaultBlueprint (contract=None)."""
    bp = create_vault_blueprint(
        domain_namespace="asset_management",
        ontology=sample_ontology,
        contract=None,
        lineage_notes="Day-0 Studio Genesis Export",
    )

    assert bp.schema_version == "1.0.0"
    assert bp.domain_namespace == "asset_management"
    assert bp.contract is None
    assert bp.lineage.blueprint_revision == 1
    assert bp.lineage.contract_sha256 is None
    assert bp.lineage.parent_blueprint_sha256 is None
    assert bp.lineage.lineage_notes == "Day-0 Studio Genesis Export"
    assert len(bp.lineage.ontology_sha256) == 64

    # Compute blueprint SHA-256
    bp_sha = compute_blueprint_sha256(bp)
    assert len(bp_sha) == 64
    assert all(c in "0123456789abcdef" for c in bp_sha)


def test_vault_blueprint_with_contract(
    sample_ontology: GraphAOntology, sample_contract: NoMistakeDataVault2ContractV5
) -> None:
    """Test creating a VaultBlueprint with deployed contract."""
    bp = create_vault_blueprint(
        domain_namespace="asset_management",
        ontology=sample_ontology,
        contract=sample_contract,
        blueprint_revision=2,
        parent_blueprint_sha256="a" * 64,
        lineage_notes="Run 1 pipeline output",
    )

    assert bp.contract is not None
    assert bp.lineage.blueprint_revision == 2
    assert bp.lineage.parent_blueprint_sha256 == "a" * 64
    assert bp.lineage.contract_sha256 is not None
    assert len(bp.lineage.contract_sha256) == 64

    # Direct contract sha calculation check
    expected_contract_sha = compute_contract_sha256(sample_contract)
    assert bp.lineage.contract_sha256 == expected_contract_sha


def test_vault_blueprint_revision_chain(
    sample_ontology: GraphAOntology, sample_contract: NoMistakeDataVault2ContractV5
) -> None:
    """Test cryptographic parent lineage chaining across runs."""
    # Run 1: Greenfield
    bp_run1 = create_vault_blueprint(
        domain_namespace="asset_management",
        ontology=sample_ontology,
        contract=None,
        blueprint_revision=1,
    )
    bp1_sha = compute_blueprint_sha256(bp_run1)

    # Run 2: Deployed contract chained from Run 1
    bp_run2 = create_vault_blueprint(
        domain_namespace="asset_management",
        ontology=sample_ontology,
        contract=sample_contract,
        parent_blueprint_sha256=bp1_sha,
        blueprint_revision=2,
    )

    assert bp_run2.lineage.parent_blueprint_sha256 == bp1_sha
    assert bp_run2.lineage.blueprint_revision == 2

    # Determinism: recomputing sha of bp_run1 gives identical digest
    assert compute_blueprint_sha256(bp_run1) == bp1_sha


def test_vault_blueprint_schema_validation(
    sample_ontology: GraphAOntology,
    sample_contract: NoMistakeDataVault2ContractV5,
    schema_v1: dict,
) -> None:
    """Validate serialized VaultBlueprint against schemas/vault-blueprint-v1.schema.json."""
    bp = create_vault_blueprint(
        domain_namespace="asset_management",
        ontology=sample_ontology,
        contract=sample_contract,
        blueprint_revision=1,
    )

    payload = bp.model_dump(mode="json")
    jsonschema.validate(instance=payload, schema=schema_v1)

    # Also test greenfield payload (contract=None)
    bp_greenfield = create_vault_blueprint(
        domain_namespace="asset_management",
        ontology=sample_ontology,
        contract=None,
        blueprint_revision=1,
    )
    payload_greenfield = bp_greenfield.model_dump(mode="json", exclude_none=True)
    jsonschema.validate(instance=payload_greenfield, schema=schema_v1)


def test_vault_blueprint_validation_errors() -> None:
    """Test invalid fields reject properly with ValidationError."""
    # Invalid revision (< 1)
    with pytest.raises(ValidationError):
        BlueprintLineage(
            blueprint_revision=0,
            domain_namespace="asset_management",
            ontology_sha256="0" * 64,
        )

    # Invalid sha256 (not 64 hex chars)
    with pytest.raises(ValidationError):
        BlueprintLineage(
            blueprint_revision=1,
            domain_namespace="asset_management",
            ontology_sha256="invalid_hex",
        )

    # Invalid domain namespace pattern
    with pytest.raises(ValidationError):
        BlueprintLineage(
            blueprint_revision=1,
            domain_namespace="123_invalid-name!",
            ontology_sha256="0" * 64,
        )


def test_pe_benchmark_ontology_hash_invariant() -> None:
    """Invariant test: Private Equity benchmark GraphAOntology hash remains unchanged."""
    pe_path = (
        Path(__file__).resolve().parents[3]
        / "examples"
        / "private_equity"
        / "pe_graph_a_ontology.yaml"
    )
    data = load_structured_file(pe_path)
    onto = GraphAOntology.model_validate(data)
    computed_sha = compute_ontology_sha256(onto)
    assert computed_sha == PE_BENCHMARK_SHA256


def test_blueprint_alias_support(sample_ontology: GraphAOntology) -> None:
    """Test blueprint_version alias and version normalization."""
    bp = create_vault_blueprint(
        domain_namespace="asset_management",
        ontology=sample_ontology,
    )
    data = bp.model_dump(mode="json", by_alias=True)
    # Allows reading with blueprint_version
    data["blueprint_version"] = "1.0"
    bp_loaded = VaultBlueprint.model_validate(data)
    assert bp_loaded.schema_version == "1.0.0"


def test_hash_determinism_dict_vs_model(
    sample_ontology: GraphAOntology,
    sample_contract: NoMistakeDataVault2ContractV5,
) -> None:
    """Ensure compute_blueprint_sha256 & compute_contract_sha256 match between model and dict."""
    # 1. Greenfield blueprint
    bp_greenfield = create_vault_blueprint(
        domain_namespace="asset_management",
        ontology=sample_ontology,
        contract=None,
    )
    dict_greenfield = bp_greenfield.model_dump(mode="json")
    assert compute_blueprint_sha256(bp_greenfield) == compute_blueprint_sha256(dict_greenfield)

    # 2. Deployed blueprint with contract
    bp_deployed = create_vault_blueprint(
        domain_namespace="asset_management",
        ontology=sample_ontology,
        contract=sample_contract,
        blueprint_revision=2,
        parent_blueprint_sha256=compute_blueprint_sha256(bp_greenfield),
        lineage_notes="Test evolution",
    )
    dict_deployed = bp_deployed.model_dump(mode="json")
    assert compute_blueprint_sha256(bp_deployed) == compute_blueprint_sha256(dict_deployed)

    # 3. Contract alone
    dict_contract = sample_contract.model_dump(mode="json")
    assert compute_contract_sha256(sample_contract) == compute_contract_sha256(dict_contract)

