/**
 * Normative TypeScript DTOs for `schemas/dv2-canonical-ir.schema.json`
 * (Open-DV Data Vault 2.0 Canonical Intermediate Representation v1.0.0).
 */

export type IrVersion = "1.0.0";

export type HashAlgorithm = "SHA256" | "MD5";

export type HashStorageType =
	| "BINARY(32)"
	| "CHAR(64)"
	| "BINARY(16)"
	| "CHAR(32)";

export type HashEncodingScheme = "DELIMITER_ESCAPED" | "LENGTH_PREFIXED";

export type HashNormalization = "UPPER_TRIM" | "EXACT_TRIM";

export type LoadDtsType =
	| "TIMESTAMP_NTZ(9)"
	| "TIMESTAMP_NTZ(6)"
	| "TIMESTAMPTZ"
	| "TIMESTAMP";

export type RecSrcType = "VARCHAR(255)" | "VARCHAR(128)" | "TEXT";

export type IrLinkType =
	| "STANDARD"
	| "HIERARCHICAL"
	| "SAME_AS"
	| "NON_HISTORIZED"
	| "EXPLORATION";

export type IrSatelliteType =
	| "STANDARD"
	| "BUSINESS"
	| "MULTI_ACTIVE"
	| "EFFECTIVITY"
	| "STATUS_TRACKING"
	| "RECORD_TRACKING";

export type IrSplitClassification =
	| "CORE"
	| "HIGH_VELOCITY"
	| "STATIC"
	| "PII_RESTRICTED"
	| "DERIVED_BV";

export type IrHashDiffMigrationPolicy =
	| "SPLIT_NEW_SATELLITE"
	| "NULL_EQUIVALENT_PADDING"
	| "VERSIONED_CUTOVER";

export type IrSnapshotWindowPolicy = "DAILY" | "LOGARITHMIC" | "CHANGE_DRIVEN";

export interface IrHashConfigDTO {
	algorithm: HashAlgorithm;
	storage_type: HashStorageType;
	delimiter: string;
	null_token_bk: string;
	null_token_payload: string;
	zero_key_unknown_token: "-1";
	zero_key_error_token: "-2";
	encoding_scheme?: HashEncodingScheme | undefined;
}

export interface IrSystemColumnsDTO {
	load_dts_col: "load_dts";
	load_dts_type: LoadDtsType;
	rec_src_col: "rec_src";
	rec_src_type: RecSrcType;
	hash_diff_col: "hash_diff";
	applied_dts_col?: string | undefined;
}

export interface IrColumnSpecDTO {
	name: string;
	data_type: string;
	nullable: boolean;
}

export interface IrHashExpressionDTO {
	algorithm: string;
	input_columns: string[];
	normalization: HashNormalization;
}

export interface IrHkColumnDTO {
	name: string;
	data_type: string;
	nullable: boolean;
	hash_expression: IrHashExpressionDTO;
}

export interface IrBusinessKeyColumnDTO extends IrColumnSpecDTO {
	is_bkcc?: boolean | undefined;
	is_tenant_id?: boolean | undefined;
}

export interface IrHubSourceMappingDTO {
	staging_table: string;
	bk_source_columns: string[];
	rec_src_value: string;
}

export interface IrHubDTO {
	name: string;
	business_concept?: string | undefined;
	verified_global_master_key?: boolean | undefined;
	hk_column: IrHkColumnDTO;
	business_keys: IrBusinessKeyColumnDTO[];
	load_dts_column: IrColumnSpecDTO;
	rec_src_column: IrColumnSpecDTO;
	extra_columns?: Record<string, unknown>[] | undefined;
	source_mappings: IrHubSourceMappingDTO[];
}

export interface IrLinkHubReferenceDTO {
	fk_column_name: string;
	target_hub: string;
	target_hk_column: string;
	data_type: string;
	nullable: boolean;
	role_name: string;
}

export interface IrLinkSourceMappingDTO {
	staging_table?: string | undefined;
	rec_src_value?: string | undefined;
	[key: string]: unknown;
}

export interface IrLinkDTO {
	name: string;
	link_type: IrLinkType;
	semantic_role?: string | undefined;
	max_recursion_depth?: number | undefined;
	cycle_prevention_predicate?: boolean | undefined;
	hk_column: IrHkColumnDTO;
	hub_references: IrLinkHubReferenceDTO[];
	degenerate_columns?: IrColumnSpecDTO[] | undefined;
	immutable_measures?: IrColumnSpecDTO[] | undefined;
	load_dts_column: IrColumnSpecDTO;
	rec_src_column: IrColumnSpecDTO;
	source_mappings: IrLinkSourceMappingDTO[];
}

export interface IrParentHkColumnDTO {
	name: string;
	target_column: string;
	data_type: string;
	nullable: boolean;
}

export interface IrSatelliteSourceMappingDTO {
	staging_table: string;
	rec_src_value: string;
}

export interface IrSatelliteDTO {
	name: string;
	satellite_type: IrSatelliteType;
	split_classification?: IrSplitClassification | undefined;
	parent_entity: string;
	additional_parents?: string[] | undefined;
	parent_hk_column: IrParentHkColumnDTO;
	child_dependent_keys?: IrColumnSpecDTO[] | undefined;
	driving_keys?: string[] | undefined;
	load_dts_column: IrColumnSpecDTO;
	applied_dts_column?: IrColumnSpecDTO | undefined;
	rec_src_column: IrColumnSpecDTO;
	hash_diff_column: IrHkColumnDTO;
	hash_diff_migration_policy?: IrHashDiffMigrationPolicy | undefined;
	payload_columns: IrColumnSpecDTO[];
	source_mapping: IrSatelliteSourceMappingDTO;
}

export interface IrPitTableDTO {
	name: string;
	parent_entity: string;
	as_of_dts_column: "as_of_dts";
	snapshot_window_policy?: IrSnapshotWindowPolicy | undefined;
	tracked_satellites: string[];
}

export interface IrBridgeTableDTO {
	name: string;
	as_of_dts_column?: string | undefined;
	traversed_hubs: string[];
	traversed_links: string[];
}

export interface IrReferenceTableDTO {
	name: string;
	natural_code_column: string;
	satellites: string[];
}

export interface CanonicalIrDTO {
	ir_version: IrVersion;
	model_name: string;
	hash_config: IrHashConfigDTO;
	system_columns: IrSystemColumnsDTO;
	hubs: IrHubDTO[];
	links: IrLinkDTO[];
	satellites: IrSatelliteDTO[];
	pit_tables?: IrPitTableDTO[] | undefined;
	bridge_tables?: IrBridgeTableDTO[] | undefined;
	reference_tables?: IrReferenceTableDTO[] | undefined;
}

export type DV2CanonicalIRDTO = CanonicalIrDTO;
