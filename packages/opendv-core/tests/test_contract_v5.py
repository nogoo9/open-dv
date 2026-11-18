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
