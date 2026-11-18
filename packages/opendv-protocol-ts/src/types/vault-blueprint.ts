/**
 * Normative TypeScript DTOs for VaultBlueprint v1.0, Single-Run Contracts,
 * and Universal Multi-Connector Sources (ADR-017, ADR-019, ADR-021).
 */

export interface BlueprintLineageDTO {
	schema_version?: "1.0.0";
	blueprint_revision?: number;
	domain_namespace: string;
	ontology_sha256: string;
	contract_sha256?: string | null | undefined;
	parent_blueprint_sha256?: string | null | undefined;
	lineage_notes?: string | null | undefined;
}

export interface VaultBlueprintDTO {
	schema_version?: "1.0.0";
	blueprint_version?: string | null | undefined;
	domain_namespace: string;
	lineage: BlueprintLineageDTO;
	ontology: Record<string, unknown>;
	contract?: Record<string, unknown> | null | undefined;
}

export interface BlueprintExportResponseDTO {
	blueprint: VaultBlueprintDTO;
	raw_vault_ddl?: string;
	target_tables?: Array<Record<string, unknown>>;
	exported_at_utc: string;
}

export interface BlueprintEvolutionDiffDTO {
	added_hubs?: string[];
	added_links?: string[];
	added_satellites?: string[];
	widened_columns?: Array<Record<string, unknown>>;
	renamed_columns?: Array<Record<string, unknown>>;
	companion_satellites?: string[];
	ontology_deltas_applied?: Array<Record<string, unknown>>;
	lineage_sha256_chain?: string[];
}

export interface NamespaceSpecDTO {
	catalog?: string | null | undefined;
	schema_name?: string | null | undefined;
}

export type TableSelectionMode = "ALL_USER_TABLES" | "EXPLICIT" | "PATTERN";

export interface TableSelectionDTO {
	mode?: TableSelectionMode;
	tables?: string[];
	include_pattern?: string | null | undefined;
	exclude_pattern?: string | null | undefined;
}

export type SamplingStrategy = "DETERMINISTIC_LIMIT" | "TABLESAMPLE_BERNOULLI";

export interface SamplingPolicyDTO {
	max_sandbox_rows?: number;
	strategy?: SamplingStrategy;
	sample_ratio?: number | null | undefined;
}

export interface SourceFeedDTO {
	feed_id: string;
	source_system: string;
	uri: string;
	adapter?: string | null | undefined;
	namespace?: NamespaceSpecDTO | null | undefined;
	table_selection?: TableSelectionDTO | null | undefined;
	credentials_ref?: string | null | undefined;
	sampling_policy?: SamplingPolicyDTO;
	target_dialect?: string | null | undefined;
	options?: Record<string, unknown>;
}

export type ConnectorExecutionMode =
	| "DIRECT_DUCKDB_ATTACH"
	| "REMOTE_PUSHDOWN_PLUS_ARROW_SAMPLE"
	| "INLINE_CAS_STAGED";

export interface ConnectorUIManifestDTO {
	adapter_id: string;
	display_name: string;
	description: string;
	schemes?: string[];
	supported_file_extensions?: string[];
	execution_mode: ConnectorExecutionMode;
	client_uploadable?: boolean;
	supports_multi_table?: boolean;
	supports_catalog_hierarchy?: boolean;
	supports_foreign_keys?: boolean;
	supports_governance_tags?: boolean;
	supports_time_travel?: boolean;
	sample_uri?: string | null | undefined;
	connection_fields?: Array<Record<string, unknown>>;
	secret_ref_fields?: string[];
}

export interface SourceInspectRequestDTO {
	uri: string;
	adapter?: string | null | undefined;
	credentials_ref?: string | null | undefined;
	namespace?: NamespaceSpecDTO | null | undefined;
	cas_hash?: string | null | undefined;
	options?: Record<string, unknown>;
}

export interface SourceInspectResponseDTO {
	adapter_id: string;
	namespaces?: Array<Record<string, unknown>>;
	tables?: Array<Record<string, unknown>>;
	capabilities?: Record<string, unknown>;
	fingerprint?: string | null | undefined;
	inspected_at_utc: string;
}

export type InferredPrimaryAction =
	| "COLD_START_N1"
	| "VERIFIED_NO_OP"
	| "MULTI_SOURCE_EXPANSION"
	| "SCHEMA_DRIFT"
	| "UNSTRUCTURED_FIRST"
	| "HYBRID_PER_SOURCE";

export type InferredTableAction =
	| "GREENFIELD_SYNTHESIS"
	| "INCREMENTAL_ONBOARDING"
	| "INTRA_SOURCE_DRIFT"
	| "NO_OP";

export interface TableExecutionActionDTO {
	source_system: string;
	table_name: string;
	action: InferredTableAction;
	matched_existing_entity?: string | null | undefined;
	drift_findings?: Array<Record<string, unknown>>;
}

export interface DocExecutionActionDTO {
	doc_id: string;
	action: "GROUND_ONTOLOGY_CONCEPT" | "EXTRACT_SPANS_AND_LINKS";
	target_hub?: string;
}

export interface InferredExecutionPlanDTO {
	plan_id: string;
	primary_action: InferredPrimaryAction;
	table_actions?: TableExecutionActionDTO[];
	doc_actions?: DocExecutionActionDTO[];
	inferred_at_utc: string;
	explanation?: string;
}

export interface PipelineInputDTO {
	blueprint?: VaultBlueprintDTO | Record<string, unknown> | null | undefined;
	blueprint_path?: string | null | undefined;
	blueprint_content?: string | null | undefined;
	ontology_source?: Record<string, unknown> | null | undefined;
	initial_contract_path?: string | null | undefined;
	initial_contract_content?: string | null | undefined;
	sources?: SourceFeedDTO[];
	unstructured_documents?: Array<Record<string, unknown>>;
	target_dialect?: string;
	governance_policy?: Record<string, unknown>;
	harness_provider?: string;
}
