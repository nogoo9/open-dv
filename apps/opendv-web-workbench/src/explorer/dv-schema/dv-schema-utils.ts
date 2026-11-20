/**
 * Pure projection, CSV export, Algorithm A.5 SQL, and filtering helpers
 * for DV Schema Explorer (`FR-DVE-01..07`).
 */

import type {
	DvColumnMeta,
	DvEntityKind,
	DvGraphEntity,
	DvSchemaProjectionDTO,
	DvSchemaZoneFilter,
	ZoneSqlMap,
} from "./types";

export interface NormalizedTriageRow {
	wave: string;
	source_table: string;
	source_column: string;
	triage_role: string;
	graph_a_uri: string;
	staging_expr: string;
	target_entity: string;
}

function nonEmptyStr(val: unknown): string | undefined {
	if (val === null || val === undefined) return undefined;
	const s = String(val).trim();
	return s.length > 0 ? s : undefined;
}

export function normalizeTriageRow(
	row: Record<string, unknown>,
): NormalizedTriageRow {
	const source_table =
		nonEmptyStr(row.source_table) ?? nonEmptyStr(row.bronze_table) ?? "";
	const source_column =
		nonEmptyStr(row.source_column) ?? nonEmptyStr(row.column_name) ?? "";
	const triage_role =
		nonEmptyStr(row.triage_role) ?? nonEmptyStr(row.elm_classification) ?? "";
	const target_entity =
		nonEmptyStr(row.target_entity) ?? nonEmptyStr(row.target_dv_entity) ?? "";
	const sqlType = nonEmptyStr(row.source_sql_type);
	const defaultStagingExpr = source_column
		? triage_role === "SYSTEM_SURROGATE_DROPPED"
			? "-- DROPPED (surrogate)"
			: sqlType
				? `CAST(${source_column} AS ${sqlType})`
				: source_column
		: "";
	return {
		wave: nonEmptyStr(row.wave) ?? "W1",
		source_table,
		source_column,
		triage_role,
		graph_a_uri:
			nonEmptyStr(row.graph_a_uri) ??
			nonEmptyStr(row.ontology_concept_uri) ??
			"",
		staging_expr: nonEmptyStr(row.staging_expr) ?? defaultStagingExpr,
		target_entity,
	};
}

export function resolveDvSchemaName(
	entityName: string,
	kind: DvEntityKind,
	satelliteType?: string,
	parentEntity?: string,
): string {
	if (kind === "STG") return "staging";
	if (kind === "PIT" || kind === "SAL") return "business_vault";
	const lower = entityName.toLowerCase();
	if (
		lower.startsWith("esat_") ||
		lower.startsWith("sal_") ||
		lower.startsWith("hal_") ||
		lower.startsWith("pit_") ||
		lower.startsWith("brg_") ||
		(satelliteType ?? "").toUpperCase().includes("EFFECTIVITY") ||
		/^(sal|hal)_/i.test(parentEntity ?? "")
	) {
		return "business_vault";
	}
	return "raw_vault";
}

function toCanonicalStgName(rawTable: string): string {
	const cleaned = rawTable.trim();
	if (cleaned.startsWith("stg_")) return cleaned;
	return `stg_${cleaned.replace(/[^a-zA-Z0-9_]+/g, "_")}`;
}

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
	const headers: ReadonlyArray<keyof NormalizedTriageRow> = [
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
		const norm = normalizeTriageRow(row);
		lines.push(headers.map((h) => csvCell(norm[h])).join(","));
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
	zoneSql?: ZoneSqlMap,
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
			schemaName: resolveDvSchemaName(r.name, "REF"),
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

	const stgMap = new Map<
		string,
		{ sourceTable: string; columns: DvColumnMeta[] }
	>();
	const upsertStg = (
		rawName: string,
		sourceLabel: string,
		col?: DvColumnMeta,
	) => {
		const stgName = toCanonicalStgName(rawName);
		let entry = stgMap.get(stgName);
		if (!entry) {
			entry = { sourceTable: sourceLabel, columns: [] };
			stgMap.set(stgName, entry);
		}
		if (col?.name && !entry.columns.some((c) => c.name === col.name)) {
			entry.columns.push(col);
		}
	};

	for (const rawRow of projection.column_triage_ledger ?? []) {
		const norm = normalizeTriageRow(rawRow);
		if (!norm.source_table) continue;
		upsertStg(
			norm.source_table,
			norm.source_table,
			norm.source_column
				? {
						name: norm.source_column,
						sqlType: nonEmptyStr(rawRow.source_sql_type) ?? "VARCHAR",
						role: norm.triage_role || "STAGED",
					}
				: undefined,
		);
	}

	const rawStagingViews = (
		projection as DvSchemaProjectionDTO & { staging_views?: unknown }
	).staging_views;
	if (Array.isArray(rawStagingViews)) {
		for (const sv of rawStagingViews) {
			if (typeof sv === "string" && sv.trim()) {
				upsertStg(sv, sv.trim());
			} else if (sv && typeof sv === "object") {
				const obj = sv as Record<string, unknown>;
				const svName =
					nonEmptyStr(obj.name) ??
					nonEmptyStr(obj.staging_table) ??
					nonEmptyStr(obj.source_table);
				if (svName) upsertStg(svName, svName);
			}
		}
	}

	for (const h of projection.hubs ?? []) {
		for (const m of h.source_mappings ?? []) {
			const mRec = m as Record<string, unknown>;
			const stgOrSrc =
				nonEmptyStr(mRec.staging_table) ??
				nonEmptyStr(mRec.source_table) ??
				nonEmptyStr(mRec.bronze_table);
			if (!stgOrSrc) continue;
			const srcLabel = nonEmptyStr(mRec.bronze_table) ?? stgOrSrc;
			const bkList = Array.isArray(mRec.bk_source_columns)
				? mRec.bk_source_columns.map(String).filter((c) => c !== "bkcc")
				: nonEmptyStr(mRec.source_column)
					? [String(mRec.source_column)]
					: [];
			if (bkList.length === 0) {
				upsertStg(stgOrSrc, srcLabel);
			} else {
				for (const colName of bkList) {
					upsertStg(stgOrSrc, srcLabel, {
						name: colName,
						sqlType: "VARCHAR",
						role: "BUSINESS_KEY",
					});
				}
			}
		}
	}

	if (stgMap.size === 0 && zoneSql?.Z1) {
		const viewRe =
			/CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+(?:[a-zA-Z0-9_]+\.)?(stg_[a-zA-Z0-9_]+)/gi;
		let match: RegExpExecArray | null = viewRe.exec(zoneSql.Z1);
		while (match !== null) {
			if (match[1]) upsertStg(match[1], match[1]);
			match = viewRe.exec(zoneSql.Z1);
		}
	}

	for (const [stgName, entry] of stgMap.entries()) {
		const mappedCols = entry.columns;
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
			sourceLineage: `source.${entry.sourceTable} -> staging.${stgName}`,
		});
	}

	for (const h of projection.hubs ?? []) {
		const hRec = h as typeof h & { bkcc_literal?: string | null };
		const bks = (h.business_keys ?? []).filter((bk) => bk !== "bkcc");
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
			.map((m) => {
				const mRec = m as Record<string, unknown>;
				const tbl =
					nonEmptyStr(mRec.source_table) ??
					nonEmptyStr(mRec.bronze_table) ??
					nonEmptyStr(mRec.staging_table) ??
					"src";
				const bkCols = Array.isArray(mRec.bk_source_columns)
					? mRec.bk_source_columns.map(String).filter((c) => c !== "bkcc")
					: [];
				const col =
					nonEmptyStr(mRec.source_column) ??
					(bkCols.length > 0 ? bkCols.join(", ") : "bk");
				return `${tbl}.${col}`;
			})
			.join(", ");
		const sm0 = (h.source_mappings ?? [])[0] as
			| Record<string, unknown>
			| undefined;
		const bkccTier = nonEmptyStr(sm0?.key_authority_tier);
		const bkccPrefix =
			nonEmptyStr(sm0?.bkcc_prefix) ??
			nonEmptyStr(sm0?.bkcc_literal) ??
			nonEmptyStr(hRec.bkcc_literal);
		const bkccExpr =
			nonEmptyStr(h.bkcc_column_expr) ??
			(bkccTier && bkccPrefix
				? `${bkccTier}:'${bkccPrefix}'`
				: (bkccPrefix ?? bkccTier));
		entities.push({
			id: `hub:${h.name}`,
			name: h.name,
			schemaName: resolveDvSchemaName(h.name, "HUB"),
			kind: "HUB",
			zone: "Z2",
			subtitle: `BK: ${bks.join(", ") || "key"}`,
			columns: cols,
			bkccExpr,
			ontologyUri: h.ontology_concept_uri,
			isWaveDelta: addedHubs.has(h.name),
			isPii: false,
			algA5Sql: buildAlgorithmA5Sql([...bks, ...(bkccExpr ? ["bkcc"] : [])]),
			sourceLineage: mappings
				? `${mappings} -> ${h.ontology_concept_uri ?? h.name} -> raw_vault.${h.name}`
				: `raw_vault.${h.name}`,
		});
	}

	for (const lnk of projection.links ?? []) {
		const isSal =
			lnk.link_type.toUpperCase().includes("SAL") ||
			lnk.link_type.toUpperCase().includes("SAME_AS") ||
			lnk.name.startsWith("sal_");
		const hubRefs = (lnk.hub_references ?? [])
			.map((r) => String(r.hub_name ?? r.target_hub ?? ""))
			.filter(Boolean);
		const cols: DvColumnMeta[] = [
			{ name: lnk.hk_column, sqlType: "CHAR(64)", role: "PK · Link HK" },
			...(lnk.hub_references ?? []).map((r) => {
				const hubName = String(r.hub_name ?? r.target_hub ?? "hub");
				const fkCol = String(
					r.hk_column ?? r.fk_column_name ?? r.target_hk_column ?? hubName,
				);
				return {
					name: fkCol,
					sqlType: "CHAR(64)",
					role: `FK -> ${hubName}`,
				};
			}),
			...(lnk.degenerate_columns ?? []).map((dc) => ({
				name: dc,
				sqlType: "VARCHAR(128)",
				role: "Degenerate Attr",
			})),
			{ name: "load_dts", sqlType: "TIMESTAMP", role: "Assertion Time" },
		];
		const linkKind: DvEntityKind = isSal ? "SAL" : "LINK";
		entities.push({
			id: `lnk:${lnk.name}`,
			name: lnk.name,
			schemaName: resolveDvSchemaName(lnk.name, linkKind),
			kind: linkKind,
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
					String(
						r.hk_column ??
							r.fk_column_name ??
							r.target_hk_column ??
							r.hub_name ??
							r.target_hub ??
							"hk",
					),
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
					sqlType: String(pc.sql_type ?? pc.data_type ?? "VARCHAR"),
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
		const satKind: DvEntityKind = isPii ? "SAT_PII" : "SAT";
		entities.push({
			id: `sat:${sat.name}`,
			name: sat.name,
			schemaName: resolveDvSchemaName(
				sat.name,
				satKind,
				sat.satellite_type,
				sat.parent_entity,
			),
			kind: satKind,
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

	const hubNames = new Set((projection.hubs ?? []).map((h) => h.name));
	for (const pit of projection.pit_tables ?? []) {
		const isBridge = pit.name.startsWith("brg_");
		const pitRec = pit as typeof pit & { parent_entity?: string };
		const candidateHub = `hub_${pit.name.replace(/^(pit_|brg_)/, "")}`;
		const targetHub =
			nonEmptyStr(pit.target_hub) ??
			nonEmptyStr(pitRec.parent_entity) ??
			(hubNames.has(candidateHub) ? candidateHub : "");
		const cols: DvColumnMeta[] = [
			{
				name: `hk_${targetHub.replace(/^hub_/, "")}`,
				sqlType: "CHAR(64)",
				role: `PK/FK -> ${targetHub}`,
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
			schemaName: resolveDvSchemaName(pit.name, "PIT"),
			kind: "PIT",
			zone: "Z3",
			subtitle: `${isBridge ? "Bridge" : "PIT"} · ${(pit.tracked_satellites ?? []).length} Sats`,
			columns: cols,
			parentEntity: targetHub,
			trackedSatellites: pit.tracked_satellites ?? [],
			isWaveDelta: false,
			isPii: false,
			algA5Sql: `COALESCE(${pit.tracked_satellites?.[0] ?? "sat"}.load_dts, TIMESTAMP '1900-01-01 00:00:00')`,
			sourceLineage: `${targetHub} + [${(pit.tracked_satellites ?? []).join(", ")}] -> business_vault.${pit.name}`,
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
