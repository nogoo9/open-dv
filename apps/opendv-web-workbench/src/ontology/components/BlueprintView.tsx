import type React from "react";
import { useMemo, useState } from "react";
import { useStudioStore } from "../store/studio-store";
import type {
	ConceptNode,
	RelationshipEdge,
	StudioSessionSummary,
} from "../store/types";
import type {
	BlueprintSchema,
	HubTable,
	LinkTable,
	RawVaultColumn,
	SatTable,
} from "../types";

function toSnakeCase(str: string): string {
	return str
		.trim()
		.replace(/([a-z0-9])([A-Z])/g, "$1_$2")
		.replace(/[^a-zA-Z0-9_]+/g, "_")
		.replace(/^_+|_+$/g, "")
		.toLowerCase();
}

function mapDataTypeToSql(dataType: string): string {
	const dt = dataType.trim().toLowerCase();
	if (dt === "string" || dt === "text" || dt === "varchar") return "VARCHAR";
	if (dt === "integer" || dt === "int" || dt === "bigint") return "BIGINT";
	if (dt === "decimal" || dt === "number" || dt === "numeric")
		return "DECIMAL(18,2)";
	if (dt === "float" || dt === "double") return "DECIMAL(18,4)";
	if (dt === "date") return "DATE";
	if (dt === "timestamp" || dt === "datetime") return "TIMESTAMP";
	if (dt === "boolean" || dt === "bool") return "BOOLEAN";
	return "VARCHAR";
}

export function generate4ZoneSql(): string {
	return `-- Open-DV Asset Management 4-Zone Data Vault 2.0 Schema
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS raw_vault;
CREATE SCHEMA IF NOT EXISTS business_vault;
CREATE SCHEMA IF NOT EXISTS marts;

CREATE TABLE IF NOT EXISTS staging.stg_sec_security_master (
    ticker VARCHAR NOT NULL,
    cik VARCHAR NOT NULL,
    company_name VARCHAR,
    exchange VARCHAR,
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS staging.stg_sec_13f_holdings (
    holding_id VARCHAR NOT NULL,
    cik VARCHAR NOT NULL,
    cusip VARCHAR,
    ticker VARCHAR,
    issuer_name VARCHAR,
    shares BIGINT,
    market_value_usd DECIMAL(18,2),
    investment_discretion VARCHAR,
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS staging.stg_fred_treasury_rates (
    observation_date DATE NOT NULL,
    series_id VARCHAR NOT NULL,
    yield_pct DECIMAL(6,4),
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_vault.hub_security (
    hk_security VARCHAR PRIMARY KEY,
    bk_ticker VARCHAR NOT NULL,
    bk_cik VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_vault.hub_investor (
    hk_investor VARCHAR PRIMARY KEY,
    bk_cik VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_vault.hub_rate_index (
    hk_rate_index VARCHAR PRIMARY KEY,
    bk_series_id VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_vault.hub_document (
    hk_document VARCHAR PRIMARY KEY,
    bk_document_id VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_vault.lnk_investor_holding (
    hk_investor_holding VARCHAR PRIMARY KEY,
    hk_investor VARCHAR NOT NULL,
    hk_security VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_vault.sat_security_details (
    hk_security VARCHAR NOT NULL,
    company_name VARCHAR,
    exchange VARCHAR,
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL,
    hash_diff VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_vault.sat_holding_position (
    hk_investor_holding VARCHAR NOT NULL,
    holding_id VARCHAR,
    shares BIGINT,
    market_value_usd DECIMAL(18,2),
    investment_discretion VARCHAR,
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL,
    hash_diff VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_vault.sat_rate_observation (
    hk_rate_index VARCHAR NOT NULL,
    observation_date DATE NOT NULL,
    yield_pct DECIMAL(6,4),
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL,
    hash_diff VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_vault.sat_document_metadata (
    hk_document VARCHAR NOT NULL,
    file_hash VARCHAR NOT NULL,
    byte_count BIGINT,
    mime_type VARCHAR,
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL,
    hash_diff VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS business_vault.pit_security (
    hk_security VARCHAR NOT NULL,
    snapshot_dts TIMESTAMP NOT NULL,
    sat_security_details_load_dts TIMESTAMP,
    PRIMARY KEY (hk_security, snapshot_dts)
);

CREATE TABLE IF NOT EXISTS business_vault.bridge_portfolio_holdings (
    hk_investor_holding VARCHAR NOT NULL,
    hk_investor VARCHAR NOT NULL,
    hk_security VARCHAR NOT NULL,
    PRIMARY KEY (hk_investor_holding)
);

CREATE TABLE IF NOT EXISTS marts.dim_security (
    security_key VARCHAR PRIMARY KEY,
    ticker VARCHAR NOT NULL,
    cik VARCHAR NOT NULL,
    company_name VARCHAR,
    exchange VARCHAR
);

CREATE TABLE IF NOT EXISTS marts.fct_institutional_holding (
    holding_key VARCHAR PRIMARY KEY,
    investor_cik VARCHAR NOT NULL,
    security_ticker VARCHAR NOT NULL,
    shares BIGINT,
    market_value_usd DECIMAL(18,2),
    observation_date DATE
);
`;
}

const deriveHubTableName = (concept: string) => `hub_${concept.toLowerCase().trim()}`;

function getFallbackRawVaultTables(): {
	hubs: HubTable[];
	links: LinkTable[];
	satellites: SatTable[];
} {
	const hubs: HubTable[] = [
		{
			type: "hub",
			tableName: deriveHubTableName("security"),
			conceptUri: "urn:opendv:concept:security",
			conceptName: "Security",
			hkColumn: "hk_security",
			businessKeys: ["bk_ticker", "bk_cik"],
			columns: [
				{ name: "hk_security", dataType: "VARCHAR", isPrimaryKey: true },
				{ name: "bk_ticker", dataType: "VARCHAR", isBusinessKey: true },
				{ name: "bk_cik", dataType: "VARCHAR", isBusinessKey: true },
				{ name: "load_dts", dataType: "TIMESTAMP", isAuditColumn: true },
				{ name: "record_source", dataType: "VARCHAR", isAuditColumn: true },
			],
			ddl: "CREATE TABLE IF NOT EXISTS raw_vault.hub_security (\n    hk_security VARCHAR PRIMARY KEY,\n    bk_ticker VARCHAR NOT NULL,\n    bk_cik VARCHAR NOT NULL,\n    load_dts TIMESTAMP NOT NULL,\n    record_source VARCHAR NOT NULL\n);",
		},
		{
			type: "hub",
			tableName: "hub_investor",
			conceptUri: "urn:opendv:concept:investor",
			conceptName: "Investor",
			hkColumn: "hk_investor",
			businessKeys: ["bk_cik"],
			columns: [
				{ name: "hk_investor", dataType: "VARCHAR", isPrimaryKey: true },
				{ name: "bk_cik", dataType: "VARCHAR", isBusinessKey: true },
				{ name: "load_dts", dataType: "TIMESTAMP", isAuditColumn: true },
				{ name: "record_source", dataType: "VARCHAR", isAuditColumn: true },
			],
			ddl: "CREATE TABLE IF NOT EXISTS raw_vault.hub_investor (\n    hk_investor VARCHAR PRIMARY KEY,\n    bk_cik VARCHAR NOT NULL,\n    load_dts TIMESTAMP NOT NULL,\n    record_source VARCHAR NOT NULL\n);",
		},
		{
			type: "hub",
			tableName: "hub_rate_index",
			conceptUri: "urn:opendv:concept:rate_index",
			conceptName: "Rate Index",
			hkColumn: "hk_rate_index",
			businessKeys: ["bk_series_id"],
			columns: [
				{ name: "hk_rate_index", dataType: "VARCHAR", isPrimaryKey: true },
				{ name: "bk_series_id", dataType: "VARCHAR", isBusinessKey: true },
				{ name: "load_dts", dataType: "TIMESTAMP", isAuditColumn: true },
				{ name: "record_source", dataType: "VARCHAR", isAuditColumn: true },
			],
			ddl: "CREATE TABLE IF NOT EXISTS raw_vault.hub_rate_index (\n    hk_rate_index VARCHAR PRIMARY KEY,\n    bk_series_id VARCHAR NOT NULL,\n    load_dts TIMESTAMP NOT NULL,\n    record_source VARCHAR NOT NULL\n);",
		},
		{
			type: "hub",
			tableName: "hub_document",
			conceptUri: "urn:opendv:concept:document",
			conceptName: "Document",
			hkColumn: "hk_document",
			businessKeys: ["bk_document_id"],
			columns: [
				{ name: "hk_document", dataType: "VARCHAR", isPrimaryKey: true },
				{ name: "bk_document_id", dataType: "VARCHAR", isBusinessKey: true },
				{ name: "load_dts", dataType: "TIMESTAMP", isAuditColumn: true },
				{ name: "record_source", dataType: "VARCHAR", isAuditColumn: true },
			],
			ddl: "CREATE TABLE IF NOT EXISTS raw_vault.hub_document (\n    hk_document VARCHAR PRIMARY KEY,\n    bk_document_id VARCHAR NOT NULL,\n    load_dts TIMESTAMP NOT NULL,\n    record_source VARCHAR NOT NULL\n);",
		},
	];

	const links: LinkTable[] = [
		{
			type: "link",
			tableName: "lnk_investor_holding",
			relationshipUri: "urn:opendv:rel:investor_holding",
			relationshipName: "Investor Holding",
			hkColumn: "hk_investor_holding",
			foreignKeys: [
				{ name: "hk_investor", targetHub: "hub_investor" },
				{ name: "hk_security", targetHub: deriveHubTableName("security") },
			],
			columns: [
				{
					name: "hk_investor_holding",
					dataType: "VARCHAR",
					isPrimaryKey: true,
				},
				{ name: "hk_investor", dataType: "VARCHAR", isForeignKey: true },
				{ name: "hk_security", dataType: "VARCHAR", isForeignKey: true },
				{ name: "load_dts", dataType: "TIMESTAMP", isAuditColumn: true },
				{ name: "record_source", dataType: "VARCHAR", isAuditColumn: true },
			],
			ddl: "CREATE TABLE IF NOT EXISTS raw_vault.lnk_investor_holding (\n    hk_investor_holding VARCHAR PRIMARY KEY,\n    hk_investor VARCHAR NOT NULL,\n    hk_security VARCHAR NOT NULL,\n    load_dts TIMESTAMP NOT NULL,\n    record_source VARCHAR NOT NULL\n);",
		},
	];

	const satellites: SatTable[] = [
		{
			type: "satellite",
			tableName: "sat_security_details",
			parentTable: deriveHubTableName("security"),
			parentHkColumn: "hk_security",
			columns: [
				{ name: "hk_security", dataType: "VARCHAR", isForeignKey: true },
				{ name: "company_name", dataType: "VARCHAR" },
				{ name: "exchange", dataType: "VARCHAR" },
				{ name: "load_dts", dataType: "TIMESTAMP", isAuditColumn: true },
				{ name: "record_source", dataType: "VARCHAR", isAuditColumn: true },
				{ name: "hash_diff", dataType: "VARCHAR", isAuditColumn: true },
			],
			ddl: "CREATE TABLE IF NOT EXISTS raw_vault.sat_security_details (\n    hk_security VARCHAR NOT NULL,\n    company_name VARCHAR,\n    exchange VARCHAR,\n    load_dts TIMESTAMP NOT NULL,\n    record_source VARCHAR NOT NULL,\n    hash_diff VARCHAR NOT NULL\n);",
		},
		{
			type: "satellite",
			tableName: "sat_holding_position",
			parentTable: "lnk_investor_holding",
			parentHkColumn: "hk_investor_holding",
			columns: [
				{
					name: "hk_investor_holding",
					dataType: "VARCHAR",
					isForeignKey: true,
				},
				{ name: "holding_id", dataType: "VARCHAR" },
				{ name: "shares", dataType: "BIGINT" },
				{ name: "market_value_usd", dataType: "DECIMAL(18,2)" },
				{ name: "investment_discretion", dataType: "VARCHAR" },
				{ name: "load_dts", dataType: "TIMESTAMP", isAuditColumn: true },
				{ name: "record_source", dataType: "VARCHAR", isAuditColumn: true },
				{ name: "hash_diff", dataType: "VARCHAR", isAuditColumn: true },
			],
			ddl: "CREATE TABLE IF NOT EXISTS raw_vault.sat_holding_position (\n    hk_investor_holding VARCHAR NOT NULL,\n    holding_id VARCHAR,\n    shares BIGINT,\n    market_value_usd DECIMAL(18,2),\n    investment_discretion VARCHAR,\n    load_dts TIMESTAMP NOT NULL,\n    record_source VARCHAR NOT NULL,\n    hash_diff VARCHAR NOT NULL\n);",
		},
		{
			type: "satellite",
			tableName: "sat_rate_observation",
			parentTable: "hub_rate_index",
			parentHkColumn: "hk_rate_index",
			columns: [
				{ name: "hk_rate_index", dataType: "VARCHAR", isForeignKey: true },
				{ name: "observation_date", dataType: "DATE" },
				{ name: "yield_pct", dataType: "DECIMAL(6,4)" },
				{ name: "load_dts", dataType: "TIMESTAMP", isAuditColumn: true },
				{ name: "record_source", dataType: "VARCHAR", isAuditColumn: true },
				{ name: "hash_diff", dataType: "VARCHAR", isAuditColumn: true },
			],
			ddl: "CREATE TABLE IF NOT EXISTS raw_vault.sat_rate_observation (\n    hk_rate_index VARCHAR NOT NULL,\n    observation_date DATE NOT NULL,\n    yield_pct DECIMAL(6,4),\n    load_dts TIMESTAMP NOT NULL,\n    record_source VARCHAR NOT NULL,\n    hash_diff VARCHAR NOT NULL\n);",
		},
		{
			type: "satellite",
			tableName: "sat_document_metadata",
			parentTable: "hub_document",
			parentHkColumn: "hk_document",
			columns: [
				{ name: "hk_document", dataType: "VARCHAR", isForeignKey: true },
				{ name: "file_hash", dataType: "VARCHAR" },
				{ name: "byte_count", dataType: "BIGINT" },
				{ name: "mime_type", dataType: "VARCHAR" },
				{ name: "load_dts", dataType: "TIMESTAMP", isAuditColumn: true },
				{ name: "record_source", dataType: "VARCHAR", isAuditColumn: true },
				{ name: "hash_diff", dataType: "VARCHAR", isAuditColumn: true },
			],
			ddl: "CREATE TABLE IF NOT EXISTS raw_vault.sat_document_metadata (\n    hk_document VARCHAR NOT NULL,\n    file_hash VARCHAR NOT NULL,\n    byte_count BIGINT,\n    mime_type VARCHAR,\n    load_dts TIMESTAMP NOT NULL,\n    record_source VARCHAR NOT NULL,\n    hash_diff VARCHAR NOT NULL\n);",
		},
	];

	return { hubs, links, satellites };
}

export function deriveRawVaultBlueprint(
	nodes: ConceptNode[] = [],
	edges: RelationshipEdge[] = [],
	activeSession?: StudioSessionSummary | null,
): BlueprintSchema {
	if (!nodes || nodes.length === 0) {
		const fallback = getFallbackRawVaultTables();
		const fourZoneDdl = generate4ZoneSql();
		const zone2Ddl = [
			"CREATE SCHEMA IF NOT EXISTS raw_vault;",
			...fallback.hubs.map((h) => h.ddl),
			...fallback.links.map((l) => l.ddl),
			...fallback.satellites.map((s) => s.ddl),
		].join("\n\n");
		return {
			hubs: fallback.hubs,
			links: fallback.links,
			satellites: fallback.satellites,
			zone2Ddl,
			fourZoneDdl,
			tableCount: 16,
		};
	}

	const hubs: HubTable[] = [];
	const satellites: SatTable[] = [];
	const links: LinkTable[] = [];

	const nodeByUri = new Map<string, ConceptNode>();
	for (const node of nodes) {
		nodeByUri.set(node.concept_uri, node);
	}

	for (const node of nodes) {
		const tableName = node.target_hub?.trim()
			? node.target_hub.trim().toLowerCase()
			: `hub_${toSnakeCase(node.concept_name)}`;
		const coreName = tableName.replace(/^hub_/, "");
		const hkColumn = `hk_${coreName}`;

		const bkAttrs = (node.attributes || []).filter((attr) =>
			/(?:_id|_code|ticker|cik|cusip|isin|symbol|lei|series_id)$/i.test(
				attr.attribute_name,
			),
		);

		const businessKeys: string[] =
			bkAttrs.length > 0
				? bkAttrs.map(
						(attr) =>
							`bk_${toSnakeCase(attr.attribute_name).replace(/^bk_/, "")}`,
					)
				: [`bk_${coreName}_id`];

		const hubCols: RawVaultColumn[] = [
			{ name: hkColumn, dataType: "VARCHAR", isPrimaryKey: true },
			...businessKeys.map((bk) => ({
				name: bk,
				dataType: "VARCHAR",
				isBusinessKey: true,
			})),
			{ name: "load_dts", dataType: "TIMESTAMP", isAuditColumn: true },
			{ name: "record_source", dataType: "VARCHAR", isAuditColumn: true },
		];

		const hubDdl = `CREATE TABLE IF NOT EXISTS raw_vault.${tableName} (\n    ${hkColumn} VARCHAR PRIMARY KEY,\n    ${businessKeys.map((bk) => `${bk} VARCHAR NOT NULL`).join(",\n    ")},\n    load_dts TIMESTAMP NOT NULL,\n    record_source VARCHAR NOT NULL\n);`;

		if (!hubs.some((h) => h.tableName === tableName)) {
			hubs.push({
				type: "hub",
				tableName,
				conceptUri: node.concept_uri,
				conceptName: node.concept_name,
				hkColumn,
				businessKeys,
				columns: hubCols,
				ddl: hubDdl,
			});
		}

		// Concept satellite
		const satName =
			coreName === "security"
				? "sat_security_details"
				: coreName === "rate_index"
					? "sat_rate_observation"
					: `sat_${coreName}_details`;

		if (!satellites.some((s) => s.tableName === satName)) {
			const descriptiveAttrs = (node.attributes || []).filter(
				(attr) => !bkAttrs.includes(attr),
			);
			const satCols: RawVaultColumn[] = [
				{ name: hkColumn, dataType: "VARCHAR", isForeignKey: true },
				...descriptiveAttrs.map((attr) => ({
					name: toSnakeCase(attr.attribute_name),
					dataType: mapDataTypeToSql(attr.data_type),
				})),
				{ name: "load_dts", dataType: "TIMESTAMP", isAuditColumn: true },
				{ name: "record_source", dataType: "VARCHAR", isAuditColumn: true },
				{ name: "hash_diff", dataType: "VARCHAR", isAuditColumn: true },
			];

			const satFieldLines = descriptiveAttrs.map(
				(attr) =>
					`${toSnakeCase(attr.attribute_name)} ${mapDataTypeToSql(attr.data_type)}`,
			);
			const satDdl = `CREATE TABLE IF NOT EXISTS raw_vault.${satName} (\n    ${hkColumn} VARCHAR NOT NULL,\n    ${satFieldLines.length > 0 ? `${satFieldLines.join(",\n    ")},\n    ` : ""}load_dts TIMESTAMP NOT NULL,\n    record_source VARCHAR NOT NULL,\n    hash_diff VARCHAR NOT NULL\n);`;

			satellites.push({
				type: "satellite",
				tableName: satName,
				parentTable: tableName,
				parentHkColumn: hkColumn,
				columns: satCols,
				ddl: satDdl,
			});
		}
	}

	// Synthesize Document Hub and Satellite if absent
	const hasDocHub = hubs.some((h) => h.tableName === "hub_document");
	if (!hasDocHub) {
		hubs.push({
			type: "hub",
			tableName: "hub_document",
			conceptUri: "urn:opendv:concept:document",
			conceptName: "Document",
			hkColumn: "hk_document",
			businessKeys: ["bk_document_id"],
			columns: [
				{ name: "hk_document", dataType: "VARCHAR", isPrimaryKey: true },
				{ name: "bk_document_id", dataType: "VARCHAR", isBusinessKey: true },
				{ name: "load_dts", dataType: "TIMESTAMP", isAuditColumn: true },
				{ name: "record_source", dataType: "VARCHAR", isAuditColumn: true },
			],
			ddl: "CREATE TABLE IF NOT EXISTS raw_vault.hub_document (\n    hk_document VARCHAR PRIMARY KEY,\n    bk_document_id VARCHAR NOT NULL,\n    load_dts TIMESTAMP NOT NULL,\n    record_source VARCHAR NOT NULL\n);",
		});

		satellites.push({
			type: "satellite",
			tableName: "sat_document_metadata",
			parentTable: "hub_document",
			parentHkColumn: "hk_document",
			columns: [
				{ name: "hk_document", dataType: "VARCHAR", isForeignKey: true },
				{ name: "file_hash", dataType: "VARCHAR" },
				{ name: "byte_count", dataType: "BIGINT" },
				{ name: "mime_type", dataType: "VARCHAR" },
				{ name: "load_dts", dataType: "TIMESTAMP", isAuditColumn: true },
				{ name: "record_source", dataType: "VARCHAR", isAuditColumn: true },
				{ name: "hash_diff", dataType: "VARCHAR", isAuditColumn: true },
			],
			ddl: "CREATE TABLE IF NOT EXISTS raw_vault.sat_document_metadata (\n    hk_document VARCHAR NOT NULL,\n    file_hash VARCHAR NOT NULL,\n    byte_count BIGINT,\n    mime_type VARCHAR,\n    load_dts TIMESTAMP NOT NULL,\n    record_source VARCHAR NOT NULL,\n    hash_diff VARCHAR NOT NULL\n);",
		});
	}

	for (const edge of edges) {
		const tableName = edge.target_link?.trim()
			? edge.target_link.trim().toLowerCase()
			: `lnk_${toSnakeCase(edge.relationship_name)}`;
		const coreName = tableName.replace(/^(lnk_|link_)/, "");
		const hkColumn = `hk_${coreName}`;

		const srcNode = nodeByUri.get(edge.source_concept_uri);
		const tgtNode = nodeByUri.get(edge.target_concept_uri);

		const srcCore = srcNode
			? srcNode.target_hub?.replace(/^hub_/, "").toLowerCase() ||
				toSnakeCase(srcNode.concept_name)
			: "source";
		const tgtCore = tgtNode
			? tgtNode.target_hub?.replace(/^hub_/, "").toLowerCase() ||
				toSnakeCase(tgtNode.concept_name)
			: "target";

		const foreignKeys = [
			{ name: `hk_${srcCore}`, targetHub: `hub_${srcCore}` },
			{ name: `hk_${tgtCore}`, targetHub: `hub_${tgtCore}` },
		];

		const linkCols: RawVaultColumn[] = [
			{ name: hkColumn, dataType: "VARCHAR", isPrimaryKey: true },
			...foreignKeys.map((fk) => ({
				name: fk.name,
				dataType: "VARCHAR",
				isForeignKey: true,
			})),
			{ name: "load_dts", dataType: "TIMESTAMP", isAuditColumn: true },
			{ name: "record_source", dataType: "VARCHAR", isAuditColumn: true },
		];

		const linkDdl = `CREATE TABLE IF NOT EXISTS raw_vault.${tableName} (\n    ${hkColumn} VARCHAR PRIMARY KEY,\n    ${foreignKeys.map((fk) => `${fk.name} VARCHAR NOT NULL`).join(",\n    ")},\n    load_dts TIMESTAMP NOT NULL,\n    record_source VARCHAR NOT NULL\n);`;

		if (!links.some((l) => l.tableName === tableName)) {
			links.push({
				type: "link",
				tableName,
				relationshipUri: edge.relationship_uri,
				relationshipName: edge.relationship_name,
				hkColumn,
				foreignKeys,
				columns: linkCols,
				ddl: linkDdl,
			});
		}

		// Link satellite
		const satLinkName =
			coreName === "investor_holding"
				? "sat_holding_position"
				: `sat_${coreName}_details`;
		const satLinkCols: RawVaultColumn[] = [
			{ name: hkColumn, dataType: "VARCHAR", isForeignKey: true },
			{ name: "load_dts", dataType: "TIMESTAMP", isAuditColumn: true },
			{ name: "record_source", dataType: "VARCHAR", isAuditColumn: true },
			{ name: "hash_diff", dataType: "VARCHAR", isAuditColumn: true },
		];
		const satLinkDdl =
			coreName === "investor_holding"
				? "CREATE TABLE IF NOT EXISTS raw_vault.sat_holding_position (\n    hk_investor_holding VARCHAR NOT NULL,\n    holding_id VARCHAR,\n    shares BIGINT,\n    market_value_usd DECIMAL(18,2),\n    investment_discretion VARCHAR,\n    load_dts TIMESTAMP NOT NULL,\n    record_source VARCHAR NOT NULL,\n    hash_diff VARCHAR NOT NULL\n);"
				: `CREATE TABLE IF NOT EXISTS raw_vault.${satLinkName} (\n    ${hkColumn} VARCHAR NOT NULL,\n    load_dts TIMESTAMP NOT NULL,\n    record_source VARCHAR NOT NULL,\n    hash_diff VARCHAR NOT NULL\n);`;

		if (!satellites.some((s) => s.tableName === satLinkName)) {
			satellites.push({
				type: "satellite",
				tableName: satLinkName,
				parentTable: tableName,
				parentHkColumn: hkColumn,
				columns: satLinkCols,
				ddl: satLinkDdl,
			});
		}
	}

	// Always synthesize lnk_investor_holding if no link was provided but hubs contain security and investor
	const hasHoldingLink = links.some((l) =>
		l.tableName.includes("investor_holding"),
	);
	const hasInvestorHub = hubs.some((h) => h.tableName === "hub_investor");
	const hasSecurityHub = hubs.some((h) => h.tableName === deriveHubTableName("security"));
	if (!hasHoldingLink && hasInvestorHub && hasSecurityHub) {
		links.push({
			type: "link",
			tableName: "lnk_investor_holding",
			relationshipUri: "urn:opendv:rel:investor_holding",
			relationshipName: "Investor Holding",
			hkColumn: "hk_investor_holding",
			foreignKeys: [
				{ name: "hk_investor", targetHub: "hub_investor" },
				{ name: "hk_security", targetHub: deriveHubTableName("security") },
			],
			columns: [
				{
					name: "hk_investor_holding",
					dataType: "VARCHAR",
					isPrimaryKey: true,
				},
				{ name: "hk_investor", dataType: "VARCHAR", isForeignKey: true },
				{ name: "hk_security", dataType: "VARCHAR", isForeignKey: true },
				{ name: "load_dts", dataType: "TIMESTAMP", isAuditColumn: true },
				{ name: "record_source", dataType: "VARCHAR", isAuditColumn: true },
			],
			ddl: "CREATE TABLE IF NOT EXISTS raw_vault.lnk_investor_holding (\n    hk_investor_holding VARCHAR PRIMARY KEY,\n    hk_investor VARCHAR NOT NULL,\n    hk_security VARCHAR NOT NULL,\n    load_dts TIMESTAMP NOT NULL,\n    record_source VARCHAR NOT NULL\n);",
		});

		if (!satellites.some((s) => s.tableName === "sat_holding_position")) {
			satellites.push({
				type: "satellite",
				tableName: "sat_holding_position",
				parentTable: "lnk_investor_holding",
				parentHkColumn: "hk_investor_holding",
				columns: [
					{
						name: "hk_investor_holding",
						dataType: "VARCHAR",
						isForeignKey: true,
					},
					{ name: "holding_id", dataType: "VARCHAR" },
					{ name: "shares", dataType: "BIGINT" },
					{ name: "market_value_usd", dataType: "DECIMAL(18,2)" },
					{ name: "investment_discretion", dataType: "VARCHAR" },
					{ name: "load_dts", dataType: "TIMESTAMP", isAuditColumn: true },
					{ name: "record_source", dataType: "VARCHAR", isAuditColumn: true },
					{ name: "hash_diff", dataType: "VARCHAR", isAuditColumn: true },
				],
				ddl: "CREATE TABLE IF NOT EXISTS raw_vault.sat_holding_position (\n    hk_investor_holding VARCHAR NOT NULL,\n    holding_id VARCHAR,\n    shares BIGINT,\n    market_value_usd DECIMAL(18,2),\n    investment_discretion VARCHAR,\n    load_dts TIMESTAMP NOT NULL,\n    record_source VARCHAR NOT NULL,\n    hash_diff VARCHAR NOT NULL\n);",
			});
		}
	}

	const zone2Ddl = [
		"CREATE SCHEMA IF NOT EXISTS raw_vault;",
		...hubs.map((h) => h.ddl),
		...links.map((l) => l.ddl),
		...satellites.map((s) => s.ddl),
	].join("\n\n");

	const domain = activeSession?.domain_namespace || "asset_management";
	const isAm = domain.includes("asset_management");

	const stagingSection = isAm
		? `CREATE SCHEMA IF NOT EXISTS staging;

CREATE TABLE IF NOT EXISTS staging.stg_sec_security_master (
    ticker VARCHAR NOT NULL,
    cik VARCHAR NOT NULL,
    company_name VARCHAR,
    exchange VARCHAR,
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS staging.stg_sec_13f_holdings (
    holding_id VARCHAR NOT NULL,
    cik VARCHAR NOT NULL,
    cusip VARCHAR,
    ticker VARCHAR,
    issuer_name VARCHAR,
    shares BIGINT,
    market_value_usd DECIMAL(18,2),
    investment_discretion VARCHAR,
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS staging.stg_fred_treasury_rates (
    observation_date DATE NOT NULL,
    series_id VARCHAR NOT NULL,
    yield_pct DECIMAL(6,4),
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL
);`
		: "CREATE SCHEMA IF NOT EXISTS staging;";

	const bvSection = `CREATE SCHEMA IF NOT EXISTS business_vault;

CREATE TABLE IF NOT EXISTS business_vault.pit_security (
    hk_security VARCHAR NOT NULL,
    snapshot_dts TIMESTAMP NOT NULL,
    sat_security_details_load_dts TIMESTAMP,
    PRIMARY KEY (hk_security, snapshot_dts)
);

CREATE TABLE IF NOT EXISTS business_vault.bridge_portfolio_holdings (
    hk_investor_holding VARCHAR NOT NULL,
    hk_investor VARCHAR NOT NULL,
    hk_security VARCHAR NOT NULL,
    PRIMARY KEY (hk_investor_holding)
);`;

	const martsSection = `CREATE SCHEMA IF NOT EXISTS marts;

CREATE TABLE IF NOT EXISTS marts.dim_security (
    security_key VARCHAR PRIMARY KEY,
    ticker VARCHAR NOT NULL,
    cik VARCHAR NOT NULL,
    company_name VARCHAR,
    exchange VARCHAR
);

CREATE TABLE IF NOT EXISTS marts.fct_institutional_holding (
    holding_key VARCHAR PRIMARY KEY,
    investor_cik VARCHAR NOT NULL,
    security_ticker VARCHAR NOT NULL,
    shares BIGINT,
    market_value_usd DECIMAL(18,2),
    observation_date DATE
);`;

	const fourZoneDdl = `-- Open-DV ${isAm ? "Asset Management" : domain} 4-Zone Data Vault 2.0 Schema
${stagingSection}

${zone2Ddl}

${bvSection}

${martsSection}
`;

	return {
		hubs,
		links,
		satellites,
		zone2Ddl,
		fourZoneDdl,
		tableCount: 3 + hubs.length + links.length + satellites.length + 2 + 2,
	};
}

export const SqlSyntaxHighlighter: React.FC<{ sql: string }> = ({ sql }) => {
	const elements = useMemo(() => {
		const lines = sql.split("\n");
		const renderedLines: React.ReactNode[] = [];

		const TOKEN_REGEX =
			/(--[^\n]*)|('(?:''|[^'\\]|\\.)*')|(\b(?:staging|raw_vault|business_vault|marts)\b\.)|(\b(?:hk_[a-zA-Z0-9_]+|bk_[a-zA-Z0-9_]+|load_dts|record_source|hash_diff)\b)|(\b(?:CREATE|TABLE|IF|NOT|EXISTS|SCHEMA|PRIMARY|KEY|FOREIGN|REFERENCES|INSERT|INTO|VALUES|SELECT|FROM|WHERE|AND|OR)\b)|(\b(?:VARCHAR|CHAR|BIGINT|INTEGER|INT|DECIMAL|DATE|TIMESTAMP|BOOLEAN|BLOB|FLOAT|DOUBLE|NUMERIC)\b)|(\b\d+\b)|([a-zA-Z0-9_]+|[^\s\w]+|\s+)/g;

		for (let lineIdx = 0; lineIdx < lines.length; lineIdx++) {
			const line = lines[lineIdx];
			if (line === undefined) continue;

			if (line.trim().startsWith("--")) {
				renderedLines.push(
					<span
						key={`l-${lineIdx}`}
						className="sql-comment"
						style={{ color: "#64748B", fontStyle: "italic" }}
					>
						{line}
					</span>,
				);
			} else {
				const tokens: React.ReactNode[] = [];
				TOKEN_REGEX.lastIndex = 0;
				let tokenKey = 0;
				let match = TOKEN_REGEX.exec(line);

				while (match !== null) {
					const [
						full,
						comment,
						str,
						schema,
						keyToken,
						keyword,
						dataType,
						num,
						other,
					] = match;
					const key = `t-${lineIdx}-${tokenKey++}`;

					if (comment) {
						tokens.push(
							<span
								key={key}
								className="sql-comment"
								style={{ color: "#64748B", fontStyle: "italic" }}
							>
								{comment}
							</span>,
						);
					} else if (str) {
						tokens.push(
							<span
								key={key}
								className="sql-string"
								style={{ color: "#FDE047" }}
							>
								{str}
							</span>,
						);
					} else if (schema) {
						tokens.push(
							<span
								key={key}
								className="sql-schema"
								style={{ color: "#F472B6" }}
							>
								{schema}
							</span>,
						);
					} else if (keyToken) {
						tokens.push(
							<span key={key} className="sql-key" style={{ color: "#A78BFA" }}>
								{keyToken}
							</span>,
						);
					} else if (keyword) {
						tokens.push(
							<span
								key={key}
								className="sql-keyword"
								style={{ color: "#93C5FD", fontWeight: "bold" }}
							>
								{keyword}
							</span>,
						);
					} else if (dataType) {
						tokens.push(
							<span key={key} className="sql-type" style={{ color: "#6EE7B7" }}>
								{dataType}
							</span>,
						);
					} else if (num) {
						tokens.push(
							<span
								key={key}
								className="sql-number"
								style={{ color: "#FCA5A5" }}
							>
								{num}
							</span>,
						);
					} else {
						tokens.push(<span key={key}>{other ?? full}</span>);
					}
					match = TOKEN_REGEX.exec(line);
				}
				renderedLines.push(<span key={`l-${lineIdx}`}>{tokens}</span>);
			}

			if (lineIdx < lines.length - 1) {
				renderedLines.push("\n");
			}
		}

		return renderedLines;
	}, [sql]);

	return <>{elements}</>;
};

export const BlueprintView: React.FC = () => {
	const activeSession = useStudioStore((s) => s.activeSession);
	const nodes = useStudioStore((s) => s.nodes);
	const edges = useStudioStore((s) => s.edges);
	const [copied, setCopied] = useState(false);

	const blueprint = useMemo(
		() => deriveRawVaultBlueprint(nodes, edges, activeSession),
		[nodes, edges, activeSession],
	);

	const ddlContent = blueprint.fourZoneDdl;

	const handleCopy = () => {
		if (typeof navigator !== "undefined" && navigator.clipboard) {
			void navigator.clipboard.writeText(ddlContent);
		}
		setCopied(true);
		setTimeout(() => setCopied(false), 2000);
	};

	const handleDownload = () => {
		if (typeof document === "undefined" || typeof URL === "undefined") return;
		const blob = new Blob([ddlContent], { type: "text/plain;charset=utf-8" });
		const url = URL.createObjectURL(blob);
		const a = document.createElement("a");
		a.href = url;
		a.download = "final_four_zone_am.sql";
		a.click();
		URL.revokeObjectURL(url);
	};

	const handleHandoff = () => {
		const targetUrl = `/builder?session_id=${activeSession?.session_id || "am-walkthrough"}&source=ontology_studio&domain_namespace=${activeSession?.domain_namespace || "asset_management"}&pipeline_id=pl_am_walkthrough`;
		if (typeof window !== "undefined") {
			window.history.pushState({}, "", targetUrl);
			window.dispatchEvent(new PopStateEvent("popstate"));
		}
	};

	return (
		<div
			className="right-panel-blueprint-view"
			data-testid="right-panel-blueprint-view"
			style={{
				padding: "20px",
				display: "flex",
				flexDirection: "column",
				gap: "16px",
				height: "100%",
				overflowY: "auto",
			}}
		>
			{/* Top Header */}
			<div
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					borderBottom: "1px solid #334155",
					paddingBottom: "12px",
				}}
			>
				<div>
					<h3 style={{ margin: 0, fontSize: "16px", color: "#F8FAFC" }}>
						Data Vault 2.0 Blueprint & Schema Preview
					</h3>
					<p style={{ margin: "4px 0 0", fontSize: "12px", color: "#94A3B8" }}>
						Deterministic projection of active ontology ({nodes.length}{" "}
						concepts, {edges.length} relationships) to 4-zone DuckDB Data Vault
						architecture.
					</p>
				</div>
				<div style={{ display: "flex", gap: "8px" }}>
					<button
						type="button"
						onClick={handleCopy}
						data-testid="btn-copy-blueprint-ddl"
						style={{
							padding: "6px 12px",
							background: "#1E293B",
							border: "1px solid #475569",
							borderRadius: "4px",
							color: "#F8FAFC",
							cursor: "pointer",
							fontSize: "12px",
						}}
					>
						{copied ? "Copied!" : "Copy DDL"}
					</button>
					<button
						type="button"
						onClick={handleDownload}
						data-testid="btn-download-blueprint-ddl"
						style={{
							padding: "6px 12px",
							background: "#1E293B",
							border: "1px solid #475569",
							borderRadius: "4px",
							color: "#F8FAFC",
							cursor: "pointer",
							fontSize: "12px",
						}}
					>
						Download DDL (.sql)
					</button>
					<button
						type="button"
						onClick={handleHandoff}
						data-testid="btn-open-in-pipeline-builder"
						style={{
							padding: "6px 14px",
							background: "#2563EB",
							border: "none",
							borderRadius: "4px",
							color: "#FFFFFF",
							cursor: "pointer",
							fontWeight: 600,
							fontSize: "12px",
						}}
					>
						Open in Pipeline Builder ➔
					</button>
				</div>
			</div>

			{/* Target Raw Vault Tables inventory summary */}
			<div
				className="blueprint-inventory-summary"
				data-testid="blueprint-inventory-summary"
				style={{
					background: "#0F172A",
					border: "1px solid #1E293B",
					borderRadius: "6px",
					padding: "12px 16px",
					display: "flex",
					flexDirection: "column",
					gap: "10px",
				}}
			>
				<div style={{ fontSize: "12px", fontWeight: 600, color: "#E2E8F0" }}>
					Target Raw Vault Tables (
					{blueprint.hubs.length +
						blueprint.links.length +
						blueprint.satellites.length}
					)
				</div>
				{/* Hubs */}
				<div
					style={{
						display: "flex",
						alignItems: "center",
						gap: "8px",
						flexWrap: "wrap",
					}}
				>
					<span
						style={{
							fontSize: "11px",
							fontWeight: 600,
							color: "#93C5FD",
							background: "rgba(59, 130, 246, 0.15)",
							padding: "2px 8px",
							borderRadius: "12px",
							border: "1px solid rgba(59, 130, 246, 0.3)",
						}}
					>
						Hubs ({blueprint.hubs.length})
					</span>
					{blueprint.hubs.map((hub) => (
						<span
							key={hub.tableName}
							data-testid={`pill-hub-${hub.tableName}`}
							style={{
								fontSize: "11px",
								background: "#1E293B",
								color: "#F1F5F9",
								padding: "2px 8px",
								borderRadius: "4px",
								border: "1px solid #334155",
								fontFamily: "monospace",
							}}
						>
							{hub.tableName.toUpperCase()}
						</span>
					))}
				</div>
				{/* Links */}
				<div
					style={{
						display: "flex",
						alignItems: "center",
						gap: "8px",
						flexWrap: "wrap",
					}}
				>
					<span
						style={{
							fontSize: "11px",
							fontWeight: 600,
							color: "#A78BFA",
							background: "rgba(167, 139, 250, 0.15)",
							padding: "2px 8px",
							borderRadius: "12px",
							border: "1px solid rgba(167, 139, 250, 0.3)",
						}}
					>
						Links ({blueprint.links.length})
					</span>
					{blueprint.links.map((link) => (
						<span
							key={link.tableName}
							data-testid={`pill-link-${link.tableName}`}
							style={{
								fontSize: "11px",
								background: "#1E293B",
								color: "#F1F5F9",
								padding: "2px 8px",
								borderRadius: "4px",
								border: "1px solid #334155",
								fontFamily: "monospace",
							}}
						>
							{link.tableName.toUpperCase()}
						</span>
					))}
				</div>
				{/* Satellites */}
				<div
					style={{
						display: "flex",
						alignItems: "center",
						gap: "8px",
						flexWrap: "wrap",
					}}
				>
					<span
						style={{
							fontSize: "11px",
							fontWeight: 600,
							color: "#6EE7B7",
							background: "rgba(110, 231, 183, 0.15)",
							padding: "2px 8px",
							borderRadius: "12px",
							border: "1px solid rgba(110, 231, 183, 0.3)",
						}}
					>
						Satellites ({blueprint.satellites.length})
					</span>
					{blueprint.satellites.map((sat) => (
						<span
							key={sat.tableName}
							data-testid={`pill-sat-${sat.tableName}`}
							style={{
								fontSize: "11px",
								background: "#1E293B",
								color: "#F1F5F9",
								padding: "2px 8px",
								borderRadius: "4px",
								border: "1px solid #334155",
								fontFamily: "monospace",
							}}
						>
							{sat.tableName.toUpperCase()}
						</span>
					))}
				</div>
			</div>

			{/* DDL Code Viewer with Syntax Highlighting */}
			<div
				style={{
					background: "#0F172A",
					border: "1px solid #334155",
					borderRadius: "6px",
					padding: "14px",
					overflowX: "auto",
				}}
			>
				<pre
					style={{
						margin: 0,
						fontFamily: "monospace",
						fontSize: "12px",
						color: "#E2E8F0",
						lineHeight: "1.5",
					}}
				>
					<code data-testid="blueprint-ddl-code">
						<SqlSyntaxHighlighter sql={ddlContent} />
					</code>
				</pre>
			</div>
		</div>
	);
};
