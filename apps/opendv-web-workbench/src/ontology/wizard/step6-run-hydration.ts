/**
 * Step 6 Cold-Reload Run Hydration & Dual-Dialect Extraction Helpers
 * (apps/opendv-web-workbench/src/ontology/wizard/step6-run-hydration.ts)
 */

import type {
	PipelineBaselineOption,
	PipelineHoldSummary,
	PipelineTopologyRow,
	ZoneSqlByDialect,
} from "../store/store-types";
import type { StudioFetcher } from "../store/studio-fetch";
import {
	type StagePillState,
	extractBaselineContractFromReportOrArtifacts,
	extractTopologyRowsFromReport,
	mapZoneBundle,
	reconcileStageStatuses,
} from "./wizard-step6-adapter";

export function createEmptyZoneSqlByDialect(): ZoneSqlByDialect {
	return { duckdb: {}, databricks: {} };
}

function getRunTimestamp(run: Record<string, unknown>): string {
	return String(
		run.completed_at_utc ??
			run.completed_at ??
			run.started_at_utc ??
			run.created_at_utc ??
			run.created_at ??
			"",
	);
}

export function pickLatestSessionRun(
	runs: Array<Record<string, unknown>>,
	sessionId: string,
): {
	latestRun: Record<string, unknown> | null;
	sessionRuns: Array<Record<string, unknown>>;
} {
	const matching = runs
		.filter((r) => String(r.session_id ?? "") === sessionId)
		.sort((a, b) => getRunTimestamp(b).localeCompare(getRunTimestamp(a)));
	return {
		latestRun: matching[0] ?? null,
		sessionRuns: matching,
	};
}

export function extractAllDialectZoneSql(
	report: Record<string, unknown> | null,
	artifacts: Array<Record<string, unknown>> = [],
	selectedDialects: Array<"duckdb" | "databricks"> = ["duckdb"],
): ZoneSqlByDialect {
	const result = createEmptyZoneSqlByDialect();
	const rawSql = report?.four_zone_sql as Record<string, unknown> | undefined;

	if (rawSql && typeof rawSql === "object") {
		result.duckdb = mapZoneBundle(rawSql.duckdb);
		result.databricks = mapZoneBundle(rawSql.databricks);
		if (
			Object.keys(result.duckdb).length === 0 &&
			Object.keys(result.databricks).length === 0
		) {
			const flat = mapZoneBundle(rawSql);
			if (Object.keys(flat).length > 0) {
				if (
					selectedDialects.includes("databricks") &&
					!selectedDialects.includes("duckdb")
				) {
					result.databricks = flat;
				} else {
					result.duckdb = flat;
				}
			}
		}
	}

	for (const art of artifacts) {
		const fname = String(art.filename ?? "").toLowerCase();
		const aid = String(art.artifact_id ?? "").toLowerCase();
		if (
			typeof art.content !== "string" ||
			!art.content.trim() ||
			(aid !== "four_zone_sql" && !fname.endsWith(".sql"))
		) {
			continue;
		}
		if (fname.includes("databricks") || fname.includes("delta")) {
			if (Object.keys(result.databricks).length === 0) {
				result.databricks = { Z4: art.content };
			}
		} else if (Object.keys(result.duckdb).length === 0) {
			result.duckdb = { Z4: art.content };
		}
	}

	return result;
}

export function pickActiveZoneSql(
	byDialect: ZoneSqlByDialect,
	selectedDialects: Array<"duckdb" | "databricks">,
): Record<string, string> {
	const preferDatabricks =
		selectedDialects.includes("databricks") &&
		!selectedDialects.includes("duckdb");
	if (preferDatabricks && Object.keys(byDialect.databricks).length > 0) {
		return byDialect.databricks;
	}
	if (Object.keys(byDialect.duckdb).length > 0) {
		return byDialect.duckdb;
	}
	return byDialect.databricks;
}

export function extractGateScorecardFromRunAndReport(
	run: Record<string, unknown> | null,
	report: Record<string, unknown> | null,
): Record<string, unknown> | null {
	const raw =
		report?.gate_scorecard ??
		report?.scorecard ??
		run?.gate_scorecard ??
		run?.scorecard;
	if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
		return null;
	}
	const card = { ...(raw as Record<string, unknown>) };
	const fallbackSandbox = report?.sandbox_summary ?? run?.sandbox_summary;
	if (
		!card.sandbox_summary &&
		fallbackSandbox &&
		typeof fallbackSandbox === "object"
	) {
		card.sandbox_summary = fallbackSandbox;
	}
	return card;
}

export function extractDriftFindingsFromReport(
	report: Record<string, unknown> | null,
): Array<Record<string, unknown>> {
	if (!report) return [];
	if (
		Array.isArray(report.drift_findings) &&
		report.drift_findings.length > 0
	) {
		return report.drift_findings.filter(
			(item): item is Record<string, unknown> =>
				Boolean(item) && typeof item === "object",
		);
	}
	if (Array.isArray(report.wave_reports)) {
		const out: Array<Record<string, unknown>> = [];
		for (const wr of report.wave_reports) {
			if (!wr || typeof wr !== "object") continue;
			const events = (wr as Record<string, unknown>).schema_drift_events;
			if (!Array.isArray(events)) continue;
			for (const ev of events) {
				if (ev && typeof ev === "object") {
					out.push(ev as Record<string, unknown>);
				}
			}
		}
		return out;
	}
	return [];
}

export function extractDriftColumnsFromReport(
	report: Record<string, unknown> | null,
): string[] {
	const out = new Set<string>();
	if (Array.isArray(report?.drift_columns)) {
		for (const item of report.drift_columns) {
			const s = String(item ?? "").trim();
			if (s) out.add(s);
		}
	}
	if (Array.isArray(report?.drift_findings)) {
		for (const item of report.drift_findings) {
			if (typeof item === "string" && item.trim()) {
				out.add(item.trim());
			} else if (item && typeof item === "object") {
				const rec = item as Record<string, unknown>;
				const col = String(
					rec.column_name ?? rec.column ?? rec.field ?? "",
				).trim();
				if (col) out.add(col);
			}
		}
	}
	for (const rec of extractDriftFindingsFromReport(report)) {
		const col = String(rec.column_name ?? rec.column ?? rec.field ?? "").trim();
		if (col) out.add(col);
	}
	return [...out];
}

export function formatDriftSteeringPrompt(
	driftColumns: string[],
	driftFindings: Array<Record<string, unknown>> = [],
): string {
	const added: string[] = [];
	const dropped: string[] = [];
	const renamed: string[] = [];
	const keyDrift: string[] = [];
	const unmappedFeeds: string[] = [];
	const coveredCols = new Set<string>();

	for (const f of driftFindings) {
		const cat = String(f.category ?? "").toUpperCase();
		const code = String(f.code ?? "").toUpperCase();
		const dtype = String(f.drift_type ?? "").toUpperCase();
		const col = String(f.column_name ?? f.column ?? "").trim();
		const newCol = String(f.new_column_name ?? "").trim();
		const srcTbl = String(f.source_table ?? "").trim();

		if (cat === "ADDITIVE" || dtype === "ADDITIVE_COLUMN" || code === "UF-1") {
			if (col) {
				if (!added.includes(col)) added.push(col);
				coveredCols.add(col);
			}
		} else if (
			cat === "SOFT_DEPRECATED" ||
			dtype === "COLUMN_REMOVAL" ||
			code === "UF-3"
		) {
			if (col) {
				if (!dropped.includes(col)) dropped.push(col);
				coveredCols.add(col);
			}
		} else if (
			cat === "RENAMED" ||
			dtype === "COLUMN_RENAME" ||
			code === "UF-2"
		) {
			if (col) {
				const pair = newCol ? `${col} -> ${newCol}` : col;
				if (!renamed.includes(pair)) renamed.push(pair);
				coveredCols.add(col);
			}
		} else if (
			cat === "KEY_DRIFT" ||
			dtype === "KEY_DRIFT" ||
			code === "UF-4"
		) {
			if (col) {
				if (!keyDrift.includes(col)) keyDrift.push(col);
				coveredCols.add(col);
			}
		} else if (
			cat === "UNMAPPED_FEED" ||
			dtype === "UNMAPPED_SOURCE_FEED" ||
			code === "UF-5"
		) {
			if (srcTbl && !unmappedFeeds.includes(srcTbl)) unmappedFeeds.push(srcTbl);
			if (col) coveredCols.add(col);
		}
	}

	const remaining = driftColumns.filter((c) => c && !coveredCols.has(c));
	const parts: string[] = [];
	if (added.length > 0) {
		parts.push(
			`Additive columns [+${added.join(", ")}] -> propose add_attribute.`,
		);
	}
	if (dropped.length > 0) {
		parts.push(
			`Dropped columns [-${dropped.join(", ")}] -> propose remove_attribute (Step 4b isolates it in unchecked MAJOR · Opt-In remove_attribute; when delta.operations includes remove_attribute, set delta.target_ontology_version='2.0.0').`,
		);
	}
	if (renamed.length > 0) {
		parts.push(
			`Renamed columns [${renamed.join(", ")}] -> preserve canonical attribute and map alias.`,
		);
	}
	if (keyDrift.length > 0) {
		parts.push(`Key drift [${keyDrift.join(", ")}] -> review key migration.`);
	}
	if (unmappedFeeds.length > 0) {
		parts.push(
			`Unmapped feeds [${unmappedFeeds.join(", ")}] -> propose add_concept and attributes.`,
		);
	}
	if (remaining.length > 0) {
		parts.push(
			`Unmapped columns [${remaining.join(", ")}] -> align ontology concepts and attributes.`,
		);
	}
	if (parts.length === 0) {
		return "Re-evaluate new source columns discovered during pipeline execution.";
	}
	return `Schema drift detected: ${parts.join(" ")}`;
}

export interface HydratedRunBundle {
	runId: string;
	report: Record<string, unknown> | null;
	stageStatuses: Record<string, StagePillState>;
	topologyRows: PipelineTopologyRow[];
	zoneSqlByDialect: ZoneSqlByDialect;
	zoneSqlByZone: Record<string, string>;
	gateScorecard: Record<string, unknown> | null;
	activeHold: PipelineHoldSummary | null;
	driftColumns: string[];
	driftFindings: Array<Record<string, unknown>>;
	isRunning: boolean;
}

export async function fetchPipelineBaselines(
	studioFetch: StudioFetcher,
	fetchImpl: typeof fetch,
	activeSid: string | null,
): Promise<PipelineBaselineOption[] | null> {
	const res = await studioFetch(fetchImpl, "/api/v1/runs");
	if (!res.ok) return null;
	const runs = (await res.json()) as Array<Record<string, unknown>>;
	if (!Array.isArray(runs)) return null;
	const certified = runs
		.filter((r) => String(r.status ?? "").toUpperCase() === "COMPLETED")
		.sort((a, b) => {
			const aMatch = activeSid && a.session_id === activeSid ? 1 : 0;
			const bMatch = activeSid && b.session_id === activeSid ? 1 : 0;
			if (aMatch !== bMatch) return bMatch - aMatch;
			return getRunTimestamp(b).localeCompare(getRunTimestamp(a));
		});
	return [
		{
			run_id: "__cold_start__",
			label: "Fresh Cold-Start Vault",
			is_latest_certified: false,
		},
		...certified.map((r, idx) => ({
			run_id: String(r.run_id ?? ""),
			label: `${String(r.run_id ?? "")}${idx === 0 ? " (Latest Certified)" : ""}`,
			is_latest_certified: idx === 0,
		})),
	];
}

export async function fetchInitialBaselineContract(
	studioFetch: StudioFetcher,
	fetchImpl: typeof fetch,
	baselineId: string,
): Promise<string | null> {
	const repRes = await studioFetch(
		fetchImpl,
		`/api/v1/runs/${encodeURIComponent(baselineId)}/report`,
	).catch(() => null);
	const rep = repRes?.ok
		? ((await repRes.json().catch(() => null)) as Record<
				string,
				unknown
			> | null)
		: null;
	const fromRep = extractBaselineContractFromReportOrArtifacts(rep, []);
	if (fromRep) return fromRep;
	const artRes = await studioFetch(
		fetchImpl,
		`/api/v1/runs/${encodeURIComponent(baselineId)}/artifacts`,
	).catch(() => null);
	const arts = artRes?.ok
		? ((await artRes.json().catch(() => [])) as Array<Record<string, unknown>>)
		: [];
	return extractBaselineContractFromReportOrArtifacts(rep, arts);
}

export async function fetchRunHydrationBundle(
	studioFetch: StudioFetcher,
	fetchImpl: typeof fetch,
	run: Record<string, unknown>,
	selectedDialects: Array<"duckdb" | "databricks">,
): Promise<HydratedRunBundle> {
	const runId = String(run.run_id ?? "");
	const runStatus = String(run.status ?? "").toUpperCase();

	const repRes = await studioFetch(
		fetchImpl,
		`/api/v1/runs/${encodeURIComponent(runId)}/report`,
	).catch(() => null);
	const report = repRes?.ok
		? ((await repRes.json().catch(() => null)) as Record<
				string,
				unknown
			> | null)
		: null;

	let arts: Array<Record<string, unknown>> = [];
	let zoneSqlByDialect = extractAllDialectZoneSql(
		report,
		arts,
		selectedDialects,
	);
	let topologyRows = report ? extractTopologyRowsFromReport(report, arts) : [];

	const hasAnySql =
		Object.keys(zoneSqlByDialect.duckdb).length > 0 ||
		Object.keys(zoneSqlByDialect.databricks).length > 0;
	if (!hasAnySql || topologyRows.length === 0) {
		const artRes = await studioFetch(
			fetchImpl,
			`/api/v1/runs/${encodeURIComponent(runId)}/artifacts`,
		).catch(() => null);
		if (artRes?.ok) {
			const rawArts = await artRes.json().catch(() => []);
			if (Array.isArray(rawArts)) {
				arts = rawArts as Array<Record<string, unknown>>;
				if (!hasAnySql) {
					zoneSqlByDialect = extractAllDialectZoneSql(
						report,
						arts,
						selectedDialects,
					);
				}
				if (topologyRows.length === 0) {
					topologyRows = extractTopologyRowsFromReport(report ?? {}, arts);
				}
			}
		}
	}

	const effectiveStatus = String(report?.status ?? runStatus).toUpperCase();
	const stageStatuses = reconcileStageStatuses({}, report, effectiveStatus);

	let activeHold: PipelineHoldSummary | null = null;
	if (effectiveStatus === "PAUSED_HOLD" || effectiveStatus === "HOLD") {
		const decRes = await studioFetch(
			fetchImpl,
			`/api/v1/runs/${encodeURIComponent(runId)}/decisions`,
		).catch(() => null);
		if (decRes?.ok) {
			const dec = (await decRes.json().catch(() => ({}))) as {
				active_holds?: Array<Record<string, unknown>>;
			};
			const first = dec.active_holds?.[0];
			if (first) {
				activeHold = {
					hold_id: String(first.hold_id ?? `hold-${runId}`),
					run_id: runId,
					stage_id: String(first.stage_id ?? "STAGE_04_SHIP_VAULT_PASS2"),
					headline: String(
						first.headline ??
							first.reason ??
							first.summary ??
							"Steward Hold Required",
					),
					status: String(first.status ?? "PENDING"),
				};
			}
		}
	}

	return {
		runId,
		report,
		stageStatuses,
		topologyRows,
		zoneSqlByDialect,
		zoneSqlByZone: pickActiveZoneSql(zoneSqlByDialect, selectedDialects),
		gateScorecard: extractGateScorecardFromRunAndReport(run, report),
		activeHold,
		driftColumns: extractDriftColumnsFromReport(report),
		driftFindings: extractDriftFindingsFromReport(report),
		isRunning: effectiveStatus === "RUNNING",
	};
}

export async function pollRunUntilSettled(
	studioFetch: StudioFetcher,
	fetchImpl: typeof fetch,
	runId: string,
	shouldContinue: () => boolean,
	onSettled: () => Promise<void>,
): Promise<void> {
	const deadline = Date.now() + 600_000;
	while (Date.now() < deadline && shouldContinue()) {
		await new Promise((r) => setTimeout(r, 150));
		if (!shouldContinue()) return;
		const res = await studioFetch(
			fetchImpl,
			`/api/v1/runs/${encodeURIComponent(runId)}`,
		).catch(() => null);
		if (!res?.ok) continue;
		const run = (await res.json().catch(() => null)) as {
			status?: string;
		} | null;
		const st = String(run?.status ?? "").toUpperCase();
		if (st && st !== "RUNNING" && st !== "PENDING" && st !== "QUEUED") {
			await onSettled();
			return;
		}
	}
}
