/**
 * Bipartite elkjs layout & 5-Zone DDL/SQL emitters for DV Schema Explorer (`FR-DVE-02,07`).
 */

import type { Edge, Node } from "@xyflow/react";
import ELK from "elkjs/lib/elk.bundled.js";
import { buildAlgorithmA5Sql } from "./dv-schema-utils";
import type { DvGraphEntity, DvSchemaProjectionDTO, ZoneSqlMap } from "./types";

let sharedElk: InstanceType<typeof ELK> | null = null;
function getElk(): InstanceType<typeof ELK> {
	if (!sharedElk) sharedElk = new ELK();
	return sharedElk;
}

export interface DvBipartiteNodeData extends Record<string, unknown> {
	entity: DvGraphEntity;
	isSelected: boolean;
}

export function buildBipartiteFlowElements(
	entities: readonly DvGraphEntity[],
	selectedName?: string | null,
): { nodes: Node<DvBipartiteNodeData>[]; edges: Edge[] } {
	const byName = new Map<string, DvGraphEntity>();
	for (const e of entities) {
		byName.set(e.name, e);
	}

	const laneX: Record<string, number> = {
		REF: 30,
		STG: 30,
		LINK: 290,
		SAL: 290,
		HUB: 550,
		SAT: 820,
		SAT_PII: 820,
		PIT: 550,
	};
	const laneCounts: Record<string, number> = {};

	const nodes: Node<DvBipartiteNodeData>[] = entities.map((entity) => {
		const bucket = entity.kind === "SAT_PII" ? "SAT" : entity.kind;
		const rowIdx = laneCounts[bucket] ?? 0;
		laneCounts[bucket] = rowIdx + 1;
		const baseY =
			entity.kind === "PIT" ? 360 + rowIdx * 115 : 40 + rowIdx * 115;
		return {
			id: entity.id,
			type: "dvEntityNode",
			position: { x: laneX[entity.kind] ?? 300, y: baseY },
			data: {
				entity,
				isSelected: entity.name === selectedName,
			},
		};
	});

	const edges: Edge[] = [];
	for (const e of entities) {
		// INV-05: Bipartite Hub <-> Link only (never Hub->Hub or Link->Link)
		if (e.kind === "LINK" || e.kind === "SAL") {
			for (const hubName of e.hubReferences ?? []) {
				const hub = byName.get(hubName);
				if (hub && hub.kind === "HUB") {
					edges.push({
						id: `edge:${hub.id}->${e.id}`,
						source: hub.id,
						target: e.id,
						style: { stroke: "#6c6a64", strokeWidth: 1.6 },
					});
				}
			}
		} else if (e.kind === "SAT" || e.kind === "SAT_PII") {
			const parent = e.parentEntity ? byName.get(e.parentEntity) : undefined;
			if (parent) {
				edges.push({
					id: `edge:${parent.id}->${e.id}`,
					source: parent.id,
					target: e.id,
					animated: e.isWaveDelta,
					style: {
						stroke: e.isPii || e.isWaveDelta ? "#cc785c" : "#6c6a64",
						strokeWidth: e.isWaveDelta ? 2.2 : 1.5,
						strokeDasharray: e.isPii ? "5,3" : undefined,
					},
				});
			}
		} else if (e.kind === "PIT") {
			const hub = e.parentEntity ? byName.get(e.parentEntity) : undefined;
			if (hub) {
				edges.push({
					id: `edge:${e.id}->${hub.id}`,
					source: e.id,
					target: hub.id,
					style: {
						stroke: "#5db8a6",
						strokeWidth: 1.6,
						strokeDasharray: "3,3",
					},
				});
			}
			for (const satName of e.trackedSatellites ?? []) {
				const sat = byName.get(satName);
				if (sat) {
					edges.push({
						id: `edge:${e.id}->${sat.id}`,
						source: e.id,
						target: sat.id,
						style: {
							stroke: "#5db8a6",
							strokeWidth: 1.4,
							strokeDasharray: "3,3",
						},
					});
				}
			}
		}
	}

	return { nodes, edges };
}

export async function layoutDvEntitiesWithElk(
	nodes: Node<DvBipartiteNodeData>[],
	edges: Edge[],
): Promise<Node<DvBipartiteNodeData>[]> {
	if (nodes.length === 0) return nodes;
	try {
		const elk = getElk();
		const res = await elk.layout({
			id: "dv-bipartite-root",
			layoutOptions: {
				"elk.algorithm": "layered",
				"elk.direction": "RIGHT",
				"elk.spacing.nodeNode": "42",
				"elk.layered.spacing.nodeNodeBetweenLayers": "88",
			},
			children: nodes.map((n) => ({ id: n.id, width: 224, height: 84 })),
			edges: edges.map((e) => ({
				id: e.id,
				sources: [e.source],
				targets: [e.target],
			})),
		});
		const posMap = new Map<string, { x: number; y: number }>();
		for (const child of res.children ?? []) {
			posMap.set(child.id, { x: (child.x ?? 0) + 24, y: (child.y ?? 0) + 24 });
		}
		return nodes.map((n) => ({
			...n,
			position: posMap.get(n.id) ?? n.position,
		}));
	} catch {
		return nodes;
	}
}

export function generateEntityDdlDml(entity: DvGraphEntity): {
	duckdb: string;
	databricks: string;
} {
	const colsDuck = entity.columns
		.map((c) => `    ${c.name} ${c.sqlType}`)
		.join(",\n");
	const colsDelta = entity.columns
		.map(
			(c) =>
				`    ${c.name} ${c.sqlType.replace(/CHAR\(\d+\)/i, "STRING").replace(/VARCHAR\(\d+\)/i, "STRING")}`,
		)
		.join(",\n");
	return {
		duckdb: `CREATE TABLE IF NOT EXISTS ${entity.schemaName}.${entity.name} (\n${colsDuck}\n);\n-- Hash Digest (Algorithm A.5): ${entity.algA5Sql}`,
		databricks: `CREATE TABLE IF NOT EXISTS cat.${entity.schemaName}.${entity.name} (\n${colsDelta}\n) USING DELTA;\n-- Hash Digest (Algorithm A.5): ${entity.algA5Sql}`,
	};
}

export function generateZoneSqlFromProjection(
	projection: DvSchemaProjectionDTO,
	dialect: "duckdb" | "databricks",
): Record<"Z0" | "Z1" | "Z2" | "Z3" | "Z4", string> {
	const prefix = dialect === "databricks" ? "cat." : "";
	const firstHub = projection.hubs?.[0]?.name ?? "hub_entity";
	const firstSat = projection.satellites?.[0]?.name ?? "sat_entity_core";
	const firstPit =
		projection.pit_tables?.[0]?.name ?? `pit_${firstHub.replace(/^hub_/, "")}`;
	const viewSuffix = firstHub.replace(/^hub_/, "");

	return {
		Z0: [
			"-- Z0: Schemas, Zero/Ghost Keys & Reference Tables",
			`CREATE SCHEMA IF NOT EXISTS ${prefix}bronze;`,
			`CREATE SCHEMA IF NOT EXISTS ${prefix}staging;`,
			`CREATE SCHEMA IF NOT EXISTS ${prefix}raw_vault;`,
			`CREATE SCHEMA IF NOT EXISTS ${prefix}business_vault;`,
			`CREATE SCHEMA IF NOT EXISTS ${prefix}information_mart;`,
		].join("\n"),
		Z1: [
			"-- Z1: Hashed Staging Views (Algorithm A.5 + 3-Tier BKCC)",
			`CREATE OR REPLACE VIEW ${prefix}staging.stg_${viewSuffix} AS`,
			`SELECT ${projection.hubs?.[0] ? buildAlgorithmA5Sql(projection.hubs[0].business_keys ?? []) : "LOWER(SHA256('key'))"} AS hk_${viewSuffix}, * FROM ${prefix}bronze.${viewSuffix};`,
		].join("\n"),
		Z2: [
			"-- Z2: Insert-Only Raw Vault Loads",
			`INSERT INTO ${prefix}raw_vault.${firstHub} SELECT * FROM ${prefix}staging.stg_${viewSuffix};`,
			`INSERT INTO ${prefix}raw_vault.${firstSat} SELECT * FROM ${prefix}staging.stg_${viewSuffix};`,
		].join("\n"),
		Z3: [
			"-- Z3: Business Vault PITs, Bridges & SALs",
			`INSERT INTO ${prefix}business_vault.${firstPit} SELECT * FROM ${prefix}raw_vault.${firstHub};`,
		].join("\n"),
		Z4: [
			"-- Z4: Information Mart Bi-Temporal Current Views (Query D.10b)",
			`CREATE OR REPLACE VIEW ${prefix}information_mart.vw_${viewSuffix}_current AS`,
			`SELECT h.*, s.* FROM ${prefix}raw_vault.${firstHub} h JOIN ${prefix}business_vault.${firstPit} p USING (hk_${viewSuffix}) JOIN ${prefix}raw_vault.${firstSat} s USING (hk_${viewSuffix});`,
		].join("\n"),
	};
}

function extractDialectZoneMap(raw: unknown): ZoneSqlMap | undefined {
	if (!raw || typeof raw !== "object") return undefined;
	const obj = raw as Record<string, unknown>;
	const out: ZoneSqlMap = {};
	const mapKeys: Array<[keyof ZoneSqlMap, readonly string[]]> = [
		["Z0", ["Z0", "zone_0_staging", "raw_staging"]],
		["Z1", ["Z1", "zone_1_raw_vault", "raw_vault"]],
		["Z2", ["Z2", "zone_2_business_vault", "business_vault"]],
		["Z3", ["Z3", "zone_3_info_mart", "info_mart"]],
		["Z4", ["Z4", "all_zones"]],
	];
	for (const [zoneKey, candidates] of mapKeys) {
		for (const candidate of candidates) {
			const val = obj[candidate];
			if (typeof val === "string" && val.trim().length > 0) {
				out[zoneKey] = val;
				break;
			}
		}
	}
	return Object.keys(out).length > 0 ? out : undefined;
}

export function normalizeFourZoneSql(rawFourZoneSql: unknown): {
	zoneSqlDuckDb?: ZoneSqlMap | undefined;
	zoneSqlDatabricks?: ZoneSqlMap | undefined;
} {
	if (!rawFourZoneSql || typeof rawFourZoneSql !== "object") return {};
	const raw = rawFourZoneSql as Record<string, unknown>;
	if ("duckdb" in raw || "databricks" in raw) {
		return {
			zoneSqlDuckDb: extractDialectZoneMap(raw.duckdb),
			zoneSqlDatabricks: extractDialectZoneMap(raw.databricks),
		};
	}
	return { zoneSqlDuckDb: extractDialectZoneMap(raw) };
}

export interface HydratedRunArtifacts {
	contractYaml?: string | undefined;
	irJson?: string | undefined;
	zoneSqlDuckDb?: ZoneSqlMap | undefined;
	zoneSqlDatabricks?: ZoneSqlMap | undefined;
}

export async function fetchDvRunReportAndArtifacts(
	base: string,
	runId: string,
	fetchFn: typeof fetch,
): Promise<HydratedRunArtifacts> {
	const encodedRun = encodeURIComponent(runId);
	const [report, artifacts] = await Promise.all([
		fetchFn(`${base}/api/v1/runs/${encodedRun}/report`, {
			headers: { Accept: "application/json" },
		})
			.then(async (resp) =>
				resp.ok ? ((await resp.json()) as Record<string, unknown>) : null,
			)
			.catch(() => null),
		fetchFn(`${base}/api/v1/runs/${encodedRun}/artifacts`, {
			headers: { Accept: "application/json" },
		})
			.then(async (resp) =>
				resp.ok
					? ((await resp.json()) as Array<{
							artifact_id?: string;
							filename?: string;
							content?: string;
						}>)
					: null,
			)
			.catch(() => null),
	]);
	const artList = Array.isArray(artifacts) ? artifacts : [];
	const contractArt = artList.find(
		(a) =>
			a.artifact_id === "final_contract" ||
			a.filename === "final_contract.yaml",
	);
	const irArt = artList.find(
		(a) =>
			a.artifact_id === "final_ir" || a.filename === "final_canonical_ir.json",
	);
	const bpContract =
		report?.blueprint &&
		typeof report.blueprint === "object" &&
		(report.blueprint as Record<string, unknown>).contract;
	const normalizedZones = report?.four_zone_sql
		? normalizeFourZoneSql(report.four_zone_sql)
		: {};
	const reportContract =
		typeof report?.contract_content === "string"
			? report.contract_content
			: bpContract
				? typeof bpContract === "string"
					? bpContract
					: JSON.stringify(bpContract, null, 2)
				: undefined;
	const reportIr = report?.canonical_ir
		? typeof report.canonical_ir === "string"
			? report.canonical_ir
			: JSON.stringify(report.canonical_ir, null, 2)
		: undefined;
	return {
		contractYaml:
			typeof contractArt?.content === "string" && contractArt.content
				? contractArt.content
				: reportContract,
		irJson:
			typeof irArt?.content === "string" && irArt.content
				? irArt.content
				: reportIr,
		zoneSqlDuckDb: normalizedZones.zoneSqlDuckDb,
		zoneSqlDatabricks: normalizedZones.zoneSqlDatabricks,
	};
}
