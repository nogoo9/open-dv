/**
 * Open-DV Ontology Construction Studio Blueprint & Raw Vault Types
 * (apps/opendv-web-workbench/src/ontology/types.ts)
 */

export * from "./store/types";

export type RawVaultTableType = "hub" | "link" | "satellite";

export interface RawVaultColumn {
	name: string;
	dataType: string;
	isPrimaryKey?: boolean;
	isForeignKey?: boolean;
	isBusinessKey?: boolean;
	isAuditColumn?: boolean;
	isNullable?: boolean;
	description?: string;
}

export interface HubTable {
	type: "hub";
	tableName: string;
	conceptUri: string;
	conceptName: string;
	hkColumn: string;
	businessKeys: string[];
	columns: RawVaultColumn[];
	ddl: string;
}

export interface LinkTable {
	type: "link";
	tableName: string;
	relationshipUri: string;
	relationshipName: string;
	hkColumn: string;
	foreignKeys: Array<{ name: string; targetHub: string }>;
	columns: RawVaultColumn[];
	ddl: string;
}

export interface SatTable {
	type: "satellite";
	tableName: string;
	parentTable: string;
	parentHkColumn: string;
	columns: RawVaultColumn[];
	ddl: string;
}

export interface BlueprintSchema {
	hubs: HubTable[];
	links: LinkTable[];
	satellites: SatTable[];
	zone2Ddl: string;
	fourZoneDdl: string;
	tableCount: number;
}

export interface PipelineBuilderDeepLinkPayload {
	session_id: string;
	domain_namespace: string;
	pipeline_id: string;
	source: "ontology_studio";
	target_tables?: string[];
}
