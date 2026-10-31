/**
 * Normative TypeScript DTOs for `schemas/no-mistake-dv2-contract-v5.schema.json`
 * (`NoMistakeDataVault2ContractV5` Constrained Decoding Model Contract v5.0).
 */

export type ContractVersionV5 = "5.0";

export type ContractLifecycleStage = "COLD_START_N1" | "MULTI_SOURCE_EXPANSION";

export type ElmClassification =
	| "BUSINESS_KEY"
	| "FOREIGN_KEY_REF"
	| "OPTIONAL_FOREIGN_KEY_REF"
	| "DEGENERATE_KEY"
	| "DESCRIPTIVE_PAYLOAD"
	| "REFERENCE_CODE"
	| "SYSTEM_SURROGATE_DROPPED"
	| "TECHNICAL_METADATA"
	| "ESCALATED_AMBIGUOUS";

export type KeyAuthorityTier =
	| "TIER_1_GLOBAL_STANDARD"
	| "TIER_2_ENTERPRISE_SOR"
	| "TIER_3_LOCAL_SCOPED";

export type ContractLinkType =
	| "STANDARD_LINK"
	| "NON_HISTORIZED_LINK"
	| "SAME_AS_LINK"
	| "HIERARCHICAL_LINK"
	| "UNSTRUCTURED_EXTRACTION_LINK";

export type ContractSatelliteType =
	| "STANDARD_SATELLITE"
	| "EFFECTIVITY_SATELLITE"
	| "MULTI_ACTIVE_SATELLITE"
	| "VECTOR_SATELLITE"
	| "AI_PROVENANCE_SATELLITE";

export type ContractSecurityClassification =
	| "PUBLIC"
	| "INTERNAL"
	| "CONFIDENTIAL"
	| "PII_HIGH"
	| "PCI";

export type ContractVolatilityTier =
	| "UNCLASSIFIED"
	| "STATIC"
	| "STANDARD"
	| "HIGH_FREQ"
	| "ADDITIVE_DRIFT_COMPANION";

export type ContractEscalationReason =
	| "MISSING_ONTOLOGY_CONCEPT"
	| "COMPETING_NATURAL_KEYS"
	| "DISJOINT_MULTI_SOURCE_BKS";

export interface ColumnTriageLedgerEntryDTO {
	bronze_table: string;
	column_name: string;
	elm_classification: ElmClassification;
	target_dv_entity: string;
}

export interface HubBusinessKeySourceDTO {
	bronze_table: string;
	key_columns: string[];
	record_source_code: string;
	key_authority_tier: KeyAuthorityTier;
	require_src_qualifier: boolean;
	bkcc_prefix?: string | null | undefined;
	system_scoped_bk_justification?: string | undefined;
}

export interface ContractHubDTO {
	name: string;
	ontology_concept_uri: string;
	business_key_sources: HubBusinessKeySourceDTO[];
}

export interface ContractLinkHubReferenceDTO {
	target_hub: string;
	role_alias: string;
	source_fk_columns: string[];
}

export interface ContractLinkDTO {
	name: string;
	link_type: ContractLinkType;
	ontology_concept_uri: string;
	source_bronze_table: string;
	driving_key_hub?: string | undefined;
	where_filter_condition?: string | undefined;
	degenerate_columns?: string[] | undefined;
	hub_references: ContractLinkHubReferenceDTO[];
}

export interface ContractSatelliteDTO {
	name: string;
	satellite_type: ContractSatelliteType;
	parent_entity: string;
	source_bronze_table: string;
	driving_key_column?: string | undefined;
	sub_sequence_column?: string | undefined;
	security_classification?: ContractSecurityClassification | undefined;
	volatility_tier?: ContractVolatilityTier | undefined;
	soft_deprecated_columns?: string[] | undefined;
	payload_columns: string[];
}

export interface ContractReferenceTableDTO {
	name: string;
	ontology_concept_uri: string;
	source_bronze_table: string;
	code_column: string;
	description_columns: string[];
}

export interface UnmappedEscalationDTO {
	bronze_table: string;
	columns: string[];
	escalation_reason: ContractEscalationReason;
	architect_question: string;
}

export interface NoMistakeDataVault2ContractV5DTO {
	contract_version: ContractVersionV5;
	domain_namespace: string;
	lifecycle_stage: ContractLifecycleStage;
	column_triage_ledger: ColumnTriageLedgerEntryDTO[];
	hubs: ContractHubDTO[];
	links: ContractLinkDTO[];
	satellites: ContractSatelliteDTO[];
	reference_tables: ContractReferenceTableDTO[];
	unmapped_escalations: UnmappedEscalationDTO[];
}

export type ContractV5DTO = NoMistakeDataVault2ContractV5DTO;
