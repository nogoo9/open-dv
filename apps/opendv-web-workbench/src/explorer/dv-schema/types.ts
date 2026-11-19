/**
 * Types for the 4-View Data Vault 2.0 Schema Explorer (`FR-DVE-01..07`).
 */

import type { components } from "@opendv/protocol-ts";

export type DvSchemaProjectionDTO =
	components["schemas"]["DvSchemaProjectionDTO"];
export type DvHubSchemaDTO = components["schemas"]["DvHubSchemaDTO"];
export type DvLinkSchemaDTO = components["schemas"]["DvLinkSchemaDTO"];
export type DvSatelliteSchemaDTO =
	components["schemas"]["DvSatelliteSchemaDTO"];
export type DvReferenceTableSchemaDTO =
	components["schemas"]["DvReferenceTableSchemaDTO"];
export type DvPitTableSchemaDTO = components["schemas"]["DvPitTableSchemaDTO"];
export type DvSchemaTableRowsDTO =
	components["schemas"]["DvSchemaTableRowsDTO"];
export type DvSchemaQueryRequestDTO =
	components["schemas"]["DvSchemaQueryRequestDTO"];
export type DvSchemaQueryResponseDTO =
	components["schemas"]["DvSchemaQueryResponseDTO"];
export type DvSchemaWaveDiffDTO = components["schemas"]["DvSchemaWaveDiffDTO"];

export type DvSchemaTabId = "graph" | "triage" | "rows" | "sql";

export type ZoneSqlMap = Partial<
	Record<"Z0" | "Z1" | "Z2" | "Z3" | "Z4", string>
>;

export type DvSchemaZoneFilter =
	| "all"
	| "z0_refs"
	| "z1_staging"
	| "z2_hubs"
	| "z2_links"
	| "z2_sats"
	| "z3_pit"
	| "pii_only"
	| "wave_delta";

export type DvEntityKind =
	| "HUB"
	| "LINK"
	| "SAL"
	| "SAT"
	| "SAT_PII"
	| "PIT"
	| "REF"
	| "STG";

export interface DvColumnMeta {
	name: string;
	sqlType: string;
	role: string;
	pii?: boolean | undefined;
}

export interface DvGraphEntity {
	id: string;
	name: string;
	schemaName: string;
	kind: DvEntityKind;
	zone: "Z0" | "Z1" | "Z2" | "Z3";
	subtitle: string;
	columns: DvColumnMeta[];
	bkccExpr?: string | null | undefined;
	ontologyUri?: string | null | undefined;
	whereCondition?: string | null | undefined;
	parentEntity?: string | undefined;
	hubReferences?: string[] | undefined;
	trackedSatellites?: string[] | undefined;
	softDeprecated?: string[] | undefined;
	isWaveDelta: boolean;
	isPii: boolean;
	algA5Sql: string;
	sourceLineage: string;
}

export interface DvSchemaExplorerViewProps {
	runId?: string | null | undefined;
	compareRunId?: string | null | undefined;
	sessionId?: string | null | undefined;
	onGoPipeline: () => void;
	onGoOntology: () => void;
	apiUrl?: string | undefined;
	fetchImpl?: typeof fetch | undefined;
	initialTab?: DvSchemaTabId | undefined;
	initialProjection?: DvSchemaProjectionDTO | null | undefined;
	selectedEntityName?: string | undefined;
	isDdlDialogOpen?: boolean | undefined;
	entityDdlSql?: { duckdb: string; databricks: string } | undefined;
	selectedTableName?: string | undefined;
	tableRowsData?: DvSchemaTableRowsDTO | null | undefined;
	sqlQueryText?: string | undefined;
	sqlQueryResult?: DvSchemaQueryResponseDTO | null | undefined;
	viewportWidth?: number | undefined;
	zoneSqlDuckDb?: ZoneSqlMap | undefined;
	zoneSqlDatabricks?: ZoneSqlMap | undefined;
	finalContractYaml?: string | undefined;
	finalCanonicalIrJson?: string | undefined;
}
