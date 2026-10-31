"""Pydantic v2 models for the No-Mistake Data Vault 2.0 Model Contract (v5.0).

Strictly mirrors `schemas/no-mistake-dv2-contract-v5.schema.json` and validates
`examples/dv_model_contract.yaml` and `examples/private_equity/dv_pe_model_contract.yaml`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

type LifecycleStageLiteral = Literal[
    "COLD_START_N1",
    "MULTI_SOURCE_EXPANSION",
]

type ELMClassificationLiteral = Literal[
    "BUSINESS_KEY",
    "FOREIGN_KEY_REF",
    "OPTIONAL_FOREIGN_KEY_REF",
    "DEGENERATE_KEY",
    "DESCRIPTIVE_PAYLOAD",
    "REFERENCE_CODE",
    "SYSTEM_SURROGATE_DROPPED",
    "TECHNICAL_METADATA",
    "ESCALATED_AMBIGUOUS",
]

type KeyAuthorityTierLiteral = Literal[
    "TIER_1_GLOBAL_STANDARD",
    "TIER_2_ENTERPRISE_SOR",
    "TIER_3_LOCAL_SCOPED",
]

type ContractLinkTypeLiteral = Literal[
    "STANDARD_LINK",
    "NON_HISTORIZED_LINK",
    "SAME_AS_LINK",
    "HIERARCHICAL_LINK",
    "UNSTRUCTURED_EXTRACTION_LINK",
]

type ContractSatelliteTypeLiteral = Literal[
    "STANDARD_SATELLITE",
    "EFFECTIVITY_SATELLITE",
    "MULTI_ACTIVE_SATELLITE",
    "VECTOR_SATELLITE",
    "AI_PROVENANCE_SATELLITE",
]

type SecurityClassificationLiteral = Literal[
    "PUBLIC",
    "INTERNAL",
    "CONFIDENTIAL",
    "PII_HIGH",
    "PCI",
]

type VolatilityTierLiteral = Literal[
    "UNCLASSIFIED",
    "STATIC",
    "STANDARD",
    "HIGH_FREQ",
    "ADDITIVE_DRIFT_COMPANION",
]

type EscalationReasonLiteral = Literal[
    "MISSING_ONTOLOGY_CONCEPT",
    "COMPETING_NATURAL_KEYS",
    "DISJOINT_MULTI_SOURCE_BKS",
]


class StrictContractModel(BaseModel):
    """Base model forbidding undeclared properties (`additionalProperties: false`)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ColumnTriageEntry(StrictContractModel):
    """Single Bronze column triage ledger item enforcing 100% column accountability."""

    bronze_table: str
    column_name: str
    elm_classification: ELMClassificationLiteral
    target_dv_entity: str


class BusinessKeySource(StrictContractModel):
    """Source Bronze table mapping and 3-Tier BK Authority specification for a Hub."""

    bronze_table: str
    key_columns: list[str] = Field(min_length=1)
    record_source_code: str
    key_authority_tier: KeyAuthorityTierLiteral
    require_src_qualifier: bool
    bkcc_prefix: str | None = None
    system_scoped_bk_justification: str | None = None


class ContractHubSpec(StrictContractModel):
    """Data Vault 2.0 Hub contract specification."""

    name: str = Field(pattern=r"^hub_[a-z0-9_]+$")
    ontology_concept_uri: str = Field(pattern=r"^urn:ontology:(provisional:)?entity:[a-z0-9_]+$")
    business_key_sources: list[BusinessKeySource] = Field(min_length=1)


class ContractLinkHubReference(StrictContractModel):
    """Foreign key reference from a Link to a parent Hub."""

    target_hub: str = Field(pattern=r"^hub_[a-z0-9_]+$")
    role_alias: str
    source_fk_columns: list[str] = Field(min_length=1)


class ContractLinkSpec(StrictContractModel):
    """Data Vault 2.0 Link contract specification."""

    name: str = Field(pattern=r"^(lnk|t_lnk|nh_lnk|sal|hal|x_lnk)_[a-z0-9_]+$")
    link_type: ContractLinkTypeLiteral
    ontology_concept_uri: str = Field(
        pattern=r"^urn:ontology:(provisional:)?(event|relationship):[a-z0-9_]+$"
    )
    source_bronze_table: str
    driving_key_hub: str | None = None
    where_filter_condition: str | None = None
    degenerate_columns: list[str] | None = None
    hub_references: list[ContractLinkHubReference] = Field(min_length=2)


class ContractSatelliteSpec(StrictContractModel):
    """Data Vault 2.0 Satellite contract specification."""

    name: str = Field(pattern=r"^(sat|bsat|mas|ma_sat|esat|sts|rts)_[a-z0-9_]+$")
    satellite_type: ContractSatelliteTypeLiteral
    parent_entity: str = Field(pattern=r"^(hub|lnk|t_lnk|nh_lnk|sal|hal|x_lnk)_[a-z0-9_]+$")
    source_bronze_table: str
    driving_key_column: str | None = None
    sub_sequence_column: str | None = None
    security_classification: SecurityClassificationLiteral | None = None
    volatility_tier: VolatilityTierLiteral | None = None
    soft_deprecated_columns: list[str] | None = None
    payload_columns: list[str]


class ContractReferenceTableSpec(StrictContractModel):
    """Data Vault 2.0 Reference Table (`ref_*`) contract specification."""

    name: str = Field(pattern=r"^ref_[a-z0-9_]+$")
    ontology_concept_uri: str = Field(pattern=r"^urn:ontology:enum:[a-z0-9_]+$")
    source_bronze_table: str
    code_column: str
    description_columns: list[str]


class UnmappedEscalation(StrictContractModel):
    """Structured escalation entry for ambiguous Bronze columns requiring human decision."""

    bronze_table: str
    columns: list[str]
    escalation_reason: EscalationReasonLiteral
    architect_question: str


class NoMistakeDataVault2ContractV5(StrictContractModel):
    """Root model for `schemas/no-mistake-dv2-contract-v5.schema.json`."""

    contract_version: Literal["5.0"] = "5.0"
    domain_namespace: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    lifecycle_stage: LifecycleStageLiteral
    column_triage_ledger: list[ColumnTriageEntry] = Field(min_length=1)
    hubs: list[ContractHubSpec]
    links: list[ContractLinkSpec]
    satellites: list[ContractSatelliteSpec]
    reference_tables: list[ContractReferenceTableSpec]
    unmapped_escalations: list[UnmappedEscalation]


# Convenient class aliases
DV2ContractV5 = NoMistakeDataVault2ContractV5
ModelContractV5 = NoMistakeDataVault2ContractV5
HubSpec = ContractHubSpec
LinkSpec = ContractLinkSpec
SatelliteSpec = ContractSatelliteSpec
ReferenceTableSpec = ContractReferenceTableSpec
HubReference = ContractLinkHubReference

__all__ = [
    "BusinessKeySource",
    "ColumnTriageEntry",
    "ContractHubSpec",
    "ContractLinkHubReference",
    "ContractLinkSpec",
    "ContractLinkTypeLiteral",
    "ContractReferenceTableSpec",
    "ContractSatelliteSpec",
    "ContractSatelliteTypeLiteral",
    "DV2ContractV5",
    "ELMClassificationLiteral",
    "EscalationReasonLiteral",
    "HubReference",
    "HubSpec",
    "KeyAuthorityTierLiteral",
    "LifecycleStageLiteral",
    "LinkSpec",
    "ModelContractV5",
    "NoMistakeDataVault2ContractV5",
    "ReferenceTableSpec",
    "SatelliteSpec",
    "SecurityClassificationLiteral",
    "UnmappedEscalation",
    "VolatilityTierLiteral",
]
