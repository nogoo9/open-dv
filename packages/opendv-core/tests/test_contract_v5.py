"""Unit tests for Contract V5 model extensions (WS1.2).

Validates:
- ColumnTriageEntry extension with optional source_sql_type
- Backward-compatible deserialization when source_sql_type is omitted
- Forward-compatible serialization preserving physical SQL types for Alg A.9a
- Full NoMistakeDataVault2ContractV5 round-trip with source_sql_type populated
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from opendv_core.models.contract_v5 import (
    BusinessKeySource,
    ColumnTriageEntry,
    ContractHubSpec,
    NoMistakeDataVault2ContractV5,
)


def test_column_triage_entry_backward_compatibility() -> None:
    """Validate that omitting source_sql_type defaults to None (backward compatibility)."""
    entry = ColumnTriageEntry(
        bronze_table="bronze_orders",
        column_name="order_id",
        elm_classification="BUSINESS_KEY",
        target_dv_entity="hub_order",
    )
    assert entry.bronze_table == "bronze_orders"
    assert entry.column_name == "order_id"
    assert entry.elm_classification == "BUSINESS_KEY"
    assert entry.target_dv_entity == "hub_order"
    assert entry.source_sql_type is None

    dumped = entry.model_dump(mode="json")
    assert "source_sql_type" in dumped
    assert dumped["source_sql_type"] is None


def test_column_triage_entry_with_source_sql_type() -> None:
    """Validate that source_sql_type preserves physical column data type for Alg A.9a."""
    entry = ColumnTriageEntry(
        bronze_table="bronze_orders",
        column_name="order_total",
        elm_classification="DESCRIPTIVE_PAYLOAD",
        target_dv_entity="sat_order_financials",
        source_sql_type="NUMERIC(18,4)",
    )
    assert entry.source_sql_type == "NUMERIC(18,4)"

    # Deserialization from JSON payload
    data = {
        "bronze_table": "bronze_orders",
        "column_name": "order_date",
        "elm_classification": "DESCRIPTIVE_PAYLOAD",
        "target_dv_entity": "sat_order_profile",
        "source_sql_type": "TIMESTAMPTZ",
    }
    loaded = ColumnTriageEntry.model_validate(data)
    assert loaded.source_sql_type == "TIMESTAMPTZ"


def test_column_triage_entry_extra_fields_forbidden() -> None:
    """Validate that unknown fields are rejected under StrictContractModel (extra='forbid')."""
    with pytest.raises(ValidationError):
        ColumnTriageEntry.model_validate(
            {
                "bronze_table": "bronze_orders",
                "column_name": "order_id",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_order",
                "unknown_property": "not_allowed",
            }
        )


def test_contract_roundtrip_with_source_sql_type() -> None:
    """Validate full contract round-trip preserving source_sql_type across triage ledger."""
    contract = NoMistakeDataVault2ContractV5(
        contract_version="5.0",
        domain_namespace="sales",
        lifecycle_stage="COLD_START_N1",
        column_triage_ledger=[
            ColumnTriageEntry(
                bronze_table="raw_customers",
                column_name="cust_id",
                elm_classification="BUSINESS_KEY",
                target_dv_entity="hub_customer",
                source_sql_type="BIGINT",
            ),
            ColumnTriageEntry(
                bronze_table="raw_customers",
                column_name="email",
                elm_classification="DESCRIPTIVE_PAYLOAD",
                target_dv_entity="sat_customer_pii",
                source_sql_type="VARCHAR(320)",
            ),
        ],
        hubs=[
            ContractHubSpec(
                name="hub_customer",
                ontology_concept_uri="urn:ontology:entity:customer",
                business_key_sources=[
                    BusinessKeySource(
                        bronze_table="raw_customers",
                        key_columns=["cust_id"],
                        record_source_code="RAW_CUSTOMERS",
                        key_authority_tier="TIER_1_GLOBAL_STANDARD",
                        require_src_qualifier=False,
                    )
                ],
            )
        ],
        links=[],
        satellites=[],
        reference_tables=[],
        unmapped_escalations=[],
    )

    dumped = contract.model_dump(mode="json")
    ledger = dumped["column_triage_ledger"]
    assert ledger[0]["source_sql_type"] == "BIGINT"
    assert ledger[1]["source_sql_type"] == "VARCHAR(320)"

    reloaded = NoMistakeDataVault2ContractV5.model_validate(dumped)
    assert reloaded.column_triage_ledger[0].source_sql_type == "BIGINT"
    assert reloaded.column_triage_ledger[1].source_sql_type == "VARCHAR(320)"


def test_contract_v5_and_blueprint_schema_accept_opendv_and_ontology_urns() -> None:
    """Bug A TDD: validate_contract_v5 and vault-blueprint-v1.schema.json accept
    urn:opendv:concept:*, urn:opendv:relationship:*, urn:opendv:enum:* alongside urn:ontology:*,
    while rejecting invalid URI schemes.
    """
    import json
    from pathlib import Path

    import jsonschema

    from opendv_core.models.contract_v5 import (
        ContractLinkHubReference,
        ContractLinkSpec,
        ContractReferenceTableSpec,
        ContractSatelliteSpec,
    )
    from opendv_core.models.graph_a import CanonicalBusinessConcept, GraphAOntology
    from opendv_core.models.vault_blueprint import create_vault_blueprint
    from opendv_core.schema_registry import validate_contract_v5

    contract = NoMistakeDataVault2ContractV5(
        contract_version="5.0",
        domain_namespace="wealth_mgmt",
        lifecycle_stage="COLD_START_N1",
        column_triage_ledger=[
            ColumnTriageEntry(
                bronze_table="positions",
                column_name="position_id",
                elm_classification="BUSINESS_KEY",
                target_dv_entity="hub_position",
                source_sql_type="VARCHAR(64)",
            ),
            ColumnTriageEntry(
                bronze_table="positions",
                column_name="cik",
                elm_classification="FOREIGN_KEY_REF",
                target_dv_entity="lnk_position_issuer",
                source_sql_type="VARCHAR(32)",
            ),
            ColumnTriageEntry(
                bronze_table="currency_code",
                column_name="currency_code",
                elm_classification="REFERENCE_CODE",
                target_dv_entity="ref_currency_code",
                source_sql_type="VARCHAR(8)",
            ),
            ColumnTriageEntry(
                bronze_table="currency_code",
                column_name="currency_name",
                elm_classification="REFERENCE_CODE",
                target_dv_entity="ref_currency_code",
                source_sql_type="VARCHAR(64)",
            ),
        ],
        hubs=[
            ContractHubSpec(
                name="hub_position",
                ontology_concept_uri="urn:opendv:concept:position",
                business_key_sources=[
                    BusinessKeySource(
                        bronze_table="positions",
                        key_columns=["position_id"],
                        record_source_code="POSITIONS",
                        key_authority_tier="TIER_1_GLOBAL_STANDARD",
                        require_src_qualifier=False,
                        bkcc_prefix="GLOBAL",
                    )
                ],
            ),
            ContractHubSpec(
                name="hub_issuer",
                ontology_concept_uri="urn:ontology:entity:issuer",
                business_key_sources=[
                    BusinessKeySource(
                        bronze_table="issuers",
                        key_columns=["cik"],
                        record_source_code="ISSUERS",
                        key_authority_tier="TIER_1_GLOBAL_STANDARD",
                        require_src_qualifier=False,
                        bkcc_prefix="GLOBAL",
                    )
                ],
            ),
        ],
        links=[
            ContractLinkSpec(
                name="lnk_position_issuer",
                link_type="STANDARD_LINK",
                ontology_concept_uri="urn:opendv:relationship:position_issuer",
                source_bronze_table="positions",
                degenerate_columns=[],
                hub_references=[
                    ContractLinkHubReference(
                        target_hub="hub_position",
                        role_alias="position",
                        source_fk_columns=["position_id"],
                    ),
                    ContractLinkHubReference(
                        target_hub="hub_issuer",
                        role_alias="issuer",
                        source_fk_columns=["cik"],
                    ),
                ],
            )
        ],
        satellites=[
            ContractSatelliteSpec(
                name="sat_position",
                satellite_type="STANDARD_SATELLITE",
                parent_entity="hub_position",
                source_bronze_table="positions",
                payload_columns=["position_id"],
            )
        ],
        reference_tables=[
            ContractReferenceTableSpec(
                name="ref_currency_code",
                ontology_concept_uri="urn:opendv:enum:currency",
                source_bronze_table="currency_code",
                code_column="currency_code",
                description_columns=["currency_name"],
            )
        ],
        unmapped_escalations=[],
    )

    payload = contract.model_dump(mode="json", exclude_none=True)
    validate_contract_v5(payload)

    ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="wealth_mgmt",
        enterprise_owner="Enterprise Architecture",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:concept:position",
                concept_name="Position",
                target_hub="hub_position",
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:issuer",
                concept_name="Issuer",
                target_hub="hub_issuer",
            ),
        ],
    )
    bp = create_vault_blueprint(
        domain_namespace="wealth_mgmt",
        ontology=ont,
        contract=contract,
        blueprint_revision=1,
    )
    schema_path = Path(__file__).resolve().parents[3] / "schemas" / "vault-blueprint-v1.schema.json"
    schema_v1 = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(instance=bp.model_dump(mode="json"), schema=schema_v1)

    for bad_uri in ("http://example.com/issuer", "urn:ontology:provisional:issuer"):
        with pytest.raises(ValidationError):
            ContractHubSpec(
                name="hub_issuer",
                ontology_concept_uri=bad_uri,
                business_key_sources=[
                    BusinessKeySource(
                        bronze_table="issuers",
                        key_columns=["cik"],
                        record_source_code="ISSUERS",
                        key_authority_tier="TIER_1_GLOBAL_STANDARD",
                        require_src_qualifier=False,
                    )
                ],
            )
        bad_payload = contract.model_dump(mode="json", exclude_none=True)
        bad_payload["hubs"][0]["ontology_concept_uri"] = bad_uri
        with pytest.raises(jsonschema.ValidationError):
            validate_contract_v5(bad_payload)
