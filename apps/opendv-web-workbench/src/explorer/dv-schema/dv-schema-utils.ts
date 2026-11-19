/**
 * Pure projection, CSV export, Algorithm A.5 SQL, and filtering helpers
 * for DV Schema Explorer (`FR-DVE-01..07`).
 */

import type {
	DvColumnMeta,
	DvGraphEntity,
	DvSchemaProjectionDTO,
	DvSchemaZoneFilter,
} from "./types";

function csvCell(val: unknown): string {
	const str = val === null || val === undefined ? "" : String(val);
	if (/[",\r\n]/.test(str)) {
		return `"${str.replace(/"/g, '""')}"`;
	}
	return str;
}

export function formatTriageCsv(
	ledger: readonly Record<string, unknown>[] = [],
): string {
	const headers = [
		"wave",
		"source_table",
		"source_column",
		"triage_role",
		"graph_a_uri",
		"staging_expr",
		"target_entity",
	];
	const lines = [headers.join(",")];
	for (const row of ledger) {
		lines.push(headers.map((h) => csvCell(row[h])).join(","));
	}
	return lines.join("\n");
}

export function buildAlgorithmA5Sql(columns: readonly string[]): string {
	const cols = columns.length > 0 ? columns : ["business_key"];
	const parts = cols.map(
		(c) => `OCTET_LENGTH(COALESCE(${c},''))||':'||COALESCE(${c},'-1:^^')`,
	);
	return `LOWER(SHA256(${parts.join(" || '||' || ")}))`;
}

export function buildDvEntities(
	projection: DvSchemaProjectionDTO,
): DvGraphEntity[] {
	const diff = projection.wave_diff;
	const addedHubs = new Set(diff?.added_hubs ?? []);
	const addedLinks = new Set(diff?.added_links ?? []);
	const addedSats = new Set(diff?.added_satellites ?? []);
	const entities: DvGraphEntity[] = [];

	for (const r of projection.reference_tables ?? []) {
		const cols: DvColumnMeta[] = [
			{
				name: r.natural_code_column || "code",
				sqlType: "VARCHAR(64)",
				role: "PK · Natural Code",
			},
			...(r.description_columns ?? []).map((dc) => ({
				name: dc,
				sqlType: "VARCHAR(256)",
				role: "Reference Attr",
			})),
		];
		entities.push({
			id: `ref:${r.name}`,
			name: r.name,
			schemaName: "raw_vault",
			kind: "REF",
			zone: "Z0",
			subtitle: `Ref · ${r.natural_code_column}`,
			columns: cols,
			isWaveDelta: false,
			isPii: false,
			algA5Sql: `TRIM(UPPER(${r.natural_code_column || "code"}))`,
			sourceLineage: `Zone 0 Reference Table -> raw_vault.${r.name}`,
		});
	}

	const stgSet = new Set<string>();
	for (const row of projection.column_triage_ledger ?? []) {
		const st = String(row.source_table ?? "").trim();
		if (st) stgSet.add(st);
	}
	for (const st of stgSet) {
		const stgName = st.startsWith("stg_") ? st : `stg_${st}`;
		const mappedCols = (projection.column_triage_ledger ?? [])
			.filter((r) => String(r.source_table ?? "") === st)
			.map((r) => ({
				name: String(r.source_column ?? ""),
				sqlType: "VARCHAR",
				role: String(r.triage_role ?? "STAGED"),
			}));
		entities.push({
			id: `stg:${stgName}`,
			name: stgName,
			schemaName: "staging",
			kind: "STG",
			zone: "Z1",
			subtitle: `Z1 View · ${mappedCols.length} cols`,
			columns: mappedCols,
			isWaveDelta: false,
			isPii: mappedCols.some((c) => c.role.toUpperCase().includes("PII")),
			algA5Sql: buildAlgorithmA5Sql(mappedCols.slice(0, 2).map((c) => c.name)),
			sourceLineage: `source.${st} -> staging.${stgName}`,
		});
	}

	for (const h of projection.hubs ?? []) {
		const bks = h.business_keys ?? [];
		const cols: DvColumnMeta[] = [
			{ name: h.hk_column, sqlType: "CHAR(64)", role: "PK · Hash Key" },
			...bks.map((bk) => ({
				name: bk,
				sqlType: "VARCHAR(128)",
				role: "Business Key",
			})),
			{ name: "bkcc", sqlType: "VARCHAR(64)", role: "BK Collision Code" },
			{ name: "load_dts", sqlType: "TIMESTAMP", role: "Assertion Time" },
			{ name: "record_source", sqlType: "VARCHAR(128)", role: "Provenance" },
		];
		const mappings = (h.source_mappings ?? [])
			.map(
				(m) =>
					`${String(m.source_table ?? "src")}.${String(m.source_column ?? "bk")}`,
			)
			.join(", ");
		entities.push({
			id: `hub:${h.name}`,
			name: h.name,
			schemaName: "raw_vault",
			kind: "HUB",
			zone: "Z2",
			subtitle: `BK: ${bks.join(", ") || "key"}`,
			columns: cols,
			bkccExpr: h.bkcc_column_expr,
			ontologyUri: h.ontology_concept_uri,
			isWaveDelta: addedHubs.has(h.name),
			isPii: false,
			algA5Sql: buildAlgorithmA5Sql([
				...bks,
				...(h.bkcc_column_expr ? ["bkcc"] : []),
			]),
			sourceLineage: mappings
				? `${mappings} -> ${h.ontology_concept_uri ?? h.name} -> raw_vault.${h.name}`
				: `raw_vault.${h.name}`,
		});
	}

	for (const lnk of projection.links ?? []) {
		const isSal =
			lnk.link_type.toUpperCase().includes("SAL") ||
			lnk.name.startsWith("sal_");
		const hubRefs = (lnk.hub_references ?? []).map((r) =>
			String(r.hub_name ?? ""),
		);
		const cols: DvColumnMeta[] = [
			{ name: lnk.hk_column, sqlType: "CHAR(64)", role: "PK · Link HK" },
			...(lnk.hub_references ?? []).map((r) => ({
				name: String(r.hk_column ?? r.hub_name ?? "hk"),
				sqlType: "CHAR(64)",
				role: `FK -> ${String(r.hub_name ?? "hub")}`,
			})),
			...(lnk.degenerate_columns ?? []).map((dc) => ({
				name: dc,
				sqlType: "VARCHAR(128)",
				role: "Degenerate Attr",
			})),
			{ name: "load_dts", sqlType: "TIMESTAMP", role: "Assertion Time" },
		];
		entities.push({
			id: `lnk:${lnk.name}`,
			name: lnk.name,
			schemaName: isSal ? "business_vault" : "raw_vault",
			kind: isSal ? "SAL" : "LINK",
			zone: "Z2",
			subtitle: lnk.where_filter_condition
				? `WHERE ${lnk.where_filter_condition}`
				: `${lnk.link_type} (${hubRefs.length} hubs)`,
			columns: cols,
			whereCondition: lnk.where_filter_condition,
			hubReferences: hubRefs,
			isWaveDelta: addedLinks.has(lnk.name),
			isPii: false,
			algA5Sql: buildAlgorithmA5Sql(
				(lnk.hub_references ?? []).map((r) =>
					String(r.hk_column ?? r.hub_name ?? "hk"),
				),
			),
			sourceLineage: `Bipartite Link connecting ${hubRefs.join(" <-> ")}${lnk.where_filter_condition ? ` (WHERE ${lnk.where_filter_condition})` : ""}`,
		});
	}

	for (const sat of projection.satellites ?? []) {
		const isPii =
			sat.satellite_type.toUpperCase().includes("PII") ||
			sat.name.includes("_pii");
		const payloadNames: string[] = [];
		const cols: DvColumnMeta[] = [
			{
				name: `hk_${sat.parent_entity.replace(/^(hub_|lnk_)/, "")}`,
				sqlType: "CHAR(64)",
				role: `PK/FK -> ${sat.parent_entity}`,
			},
			{ name: "load_dts", sqlType: "TIMESTAMP", role: "PK · Assertion Time" },
			{
				name: "applied_dts",
				sqlType: "TIMESTAMP",
				role: "Bi-Temporal Effective",
			},
			...(sat.payload_columns ?? []).map((pc) => {
				const cname = String(pc.name ?? "");
				if (cname) payloadNames.push(cname);
				return {
					name: cname,
					sqlType: String(pc.sql_type ?? "VARCHAR"),
					role: pc.pii ? "PII Sensitive Attr" : "Descriptive Payload",
					pii: Boolean(pc.pii),
				};
			}),
			{
				name: sat.hash_diff_column || "hash_diff",
				sqlType: "CHAR(64)",
				role: "Alg A.5 Payload Digest",
			},
		];
		entities.push({
			id: `sat:${sat.name}`,
			name: sat.name,
			schemaName: "raw_vault",
			kind: isPii ? "SAT_PII" : "SAT",
			zone: "Z2",
			subtitle: `${sat.satellite_type} · ${payloadNames.length} cols`,
			columns: cols,
			parentEntity: sat.parent_entity,
			softDeprecated: sat.soft_deprecated_columns ?? [],
			isWaveDelta:
				addedSats.has(sat.name) ||
				(diff?.added_columns ?? []).some((ac) => ac.satellite === sat.name),
			isPii,
			algA5Sql: buildAlgorithmA5Sql(payloadNames),
			sourceLineage: `${sat.parent_entity} -> ${sat.name} (policy: ${sat.hash_diff_migration_policy})`,
		});
	}

	for (const pit of projection.pit_tables ?? []) {
		const isBridge = pit.name.startsWith("brg_");
		const cols: DvColumnMeta[] = [
			{
				name: `hk_${pit.target_hub.replace(/^hub_/, "")}`,
				sqlType: "CHAR(64)",
				role: `PK/FK -> ${pit.target_hub}`,
			},
			{ name: "snapshot_dts", sqlType: "TIMESTAMP", role: "PK · Snapshot" },
			...(pit.tracked_satellites ?? []).map((ts) => ({
				name: `${ts}_ldts`,
				sqlType: "TIMESTAMP",
				role: `Ghost Coalesced (${ts})`,
			})),
		];
		entities.push({
			id: `pit:${pit.name}`,
			name: pit.name,
			schemaName: "business_vault",
			kind: "PIT",
			zone: "Z3",
			subtitle: `${isBridge ? "Bridge" : "PIT"} · ${(pit.tracked_satellites ?? []).length} Sats`,
			columns: cols,
			parentEntity: pit.target_hub,
			trackedSatellites: pit.tracked_satellites ?? [],
			isWaveDelta: false,
			isPii: false,
			algA5Sql: `COALESCE(${pit.tracked_satellites?.[0] ?? "sat"}.load_dts, TIMESTAMP '1900-01-01 00:00:00')`,
			sourceLineage: `${pit.target_hub} + [${(pit.tracked_satellites ?? []).join(", ")}] -> business_vault.${pit.name}`,
		});
	}

	return entities;
}

export function filterDvEntities(
	entities: readonly DvGraphEntity[],
	zoneFilter: DvSchemaZoneFilter,
	searchQuery: string,
): DvGraphEntity[] {
	const q = searchQuery.trim().toLowerCase();
	return entities.filter((e) => {
		if (zoneFilter === "z0_refs" && e.zone !== "Z0") return false;
		if (zoneFilter === "z1_staging" && e.zone !== "Z1") return false;
		if (zoneFilter === "z2_hubs" && e.kind !== "HUB") return false;
		if (zoneFilter === "z2_links" && e.kind !== "LINK" && e.kind !== "SAL") {
			return false;
		}
		if (zoneFilter === "z2_sats" && e.kind !== "SAT" && e.kind !== "SAT_PII") {
			return false;
		}
		if (zoneFilter === "z3_pit" && e.zone !== "Z3") return false;
		if (zoneFilter === "pii_only" && !e.isPii) return false;
		if (zoneFilter === "wave_delta" && !e.isWaveDelta) return false;

		if (q) {
			const hit =
				e.name.toLowerCase().includes(q) ||
				e.subtitle.toLowerCase().includes(q) ||
				(e.ontologyUri ?? "").toLowerCase().includes(q) ||
				e.columns.some((c) => c.name.toLowerCase().includes(q));
			if (!hit) return false;
		}
		return true;
	});
}
