/**
 * Step 6 Adapter: Strict PipelineInputDTO SourceFeedDTO Builder,
 * Run Report / Artifact 5-Zone SQL & Topology Hydrators, & Stage Pill Aggregation.
 * (apps/opendv-web-workbench/src/ontology/wizard/wizard-step6-adapter.ts)
 */

import type { SourceFeedDTO } from "@opendv/protocol-ts";
import type { PipelineTopologyRow } from "../store/store-types";
import type { EvidenceChunk, SourceDocument } from "../store/types";

const STRUCTURED_EXT_RE =
	/\.(parquet|csv|tsv|xlsx|xls|sqlite|sqlite3|db|duckdb|ddb|jsonl|ndjson|json)$/i;
const UNSTRUCTURED_EXT_RE = /\.(pdf|docx|doc|md|markdown|txt|html|htm|rtf)$/i;
const UNSTRUCTURED_MIMES = new Set([
	"application/pdf",
	"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
	"application/msword",
	"text/markdown",
	"text/plain",
	"text/html",
	"text/rtf",
]);

const STAGE_STATUS_RANK: Record<string, number> = {
	FAILED: 5,
	HOLD: 4,
	RUNNING: 3,
	COMPLETED: 2,
	PENDING: 1,
};

function slugSystem(raw: string): string {
	return (
		raw
			.trim()
			.toLowerCase()
			.replace(/[^a-z0-9]+/g, "_")
			.replace(/^_+|_+$/g, "") || "source"
	);
}

export function isUnstructuredSource(s: SourceDocument): boolean {
	const fname = (s.file_name || s.filename || s.source_id || "")
		.trim()
		.toLowerCase();
	const mime = (s.mime_type || "").split(";")[0]?.trim().toLowerCase() ?? "";
	if (STRUCTURED_EXT_RE.test(fname)) return false;
	if (UNSTRUCTURED_EXT_RE.test(fname)) return true;
	return (
		UNSTRUCTURED_MIMES.has(mime) ||
		mime.startsWith("text/markdown") ||
		mime.startsWith("text/html")
	);
}

export function partitionPipelineSources(
	sources: SourceDocument[],
	evidenceChunks: EvidenceChunk[] = [],
	overrideUris?: string[],
): {
	sources: SourceFeedDTO[];
	unstructured_documents: Array<Record<string, unknown>>;
} {
	if (overrideUris && overrideUris.length > 0) {
		return {
			sources: overrideUris.map((uri, idx) => ({
				feed_id: `feed-${idx + 1}`,
				source_system: slugSystem(uri.split("/").pop() || `feed_${idx + 1}`),
				uri,
			})),
			unstructured_documents: [],
		};
	}

	const feeds: SourceFeedDTO[] = [];
	const docs: Array<Record<string, unknown>> = [];
	const visible = sources.filter((s) => !s.do_not_send_to_agent);

	visible.forEach((s, idx) => {
		const fname = s.file_name || s.filename || s.source_id;
		if (isUnstructuredSource(s)) {
			const docId = s.source_id || `doc-${idx + 1}`;
			const casUri = s.source_sha256 ? `cas://${s.source_sha256}` : undefined;
			const chunkText = evidenceChunks
				.filter(
					(c) =>
						(c.source_id && c.source_id === s.source_id) ||
						(c.document_id && c.document_id === s.source_id) ||
						(c.source_file && c.source_file === fname),
				)
				.map((c) => c.text ?? c.text_snippet ?? "")
				.filter((t) => t.trim().length > 0)
				.join("\n\n");
			docs.push({
				doc_id: docId,
				...(casUri
					? { document_path: casUri }
					: fname
						? { document_path: fname }
						: {}),
				...(s.mime_type ? { mime_type: s.mime_type } : {}),
				...(chunkText ? { content: chunkText } : {}),
				metadata: {
					title: fname,
					source_id: s.source_id,
					...(s.source_sha256 ? { source_sha256: s.source_sha256 } : {}),
					...(casUri ? { uri: casUri } : {}),
				},
			});
			return;
		}
		const mime = (s.mime_type || "").toLowerCase();
		const adapter = mime.includes("sqlite")
			? "sqlite"
			: mime.includes("duckdb")
				? "duckdb"
				: mime.includes("databricks")
					? "databricks"
					: undefined;
		feeds.push({
			feed_id: s.source_id || `feed-${idx + 1}`,
			source_system: slugSystem(fname),
			uri: s.source_sha256 ? `cas://${s.source_sha256}` : fname,
			...(adapter ? { adapter } : {}),
		});
	});

	return { sources: feeds, unstructured_documents: docs };
}

export function buildPipelineSourceFeeds(
	sources: SourceDocument[],
	overrideUris?: string[],
): SourceFeedDTO[] {
	return partitionPipelineSources(sources, [], overrideUris).sources;
}

function mapZoneBundle(raw: unknown): Record<string, string> {
	if (!raw || typeof raw !== "object") return {};
	const obj = raw as Record<string, unknown>;
	const out: Record<string, string> = {};
	const z0 = obj.Z0 ?? obj.zone_0_staging ?? obj.raw_staging;
	const z1 = obj.Z1 ?? obj.zone_1_raw_vault ?? obj.raw_vault;
	const z2 = obj.Z2 ?? obj.zone_2_business_vault ?? obj.business_vault;
	const z3 = obj.Z3 ?? obj.zone_3_info_mart ?? obj.info_mart;
	const z4 = obj.Z4 ?? obj.all_zones;
	if (typeof z0 === "string" && z0.trim()) out.Z0 = z0;
	if (typeof z1 === "string" && z1.trim()) out.Z1 = z1;
	if (typeof z2 === "string" && z2.trim()) out.Z2 = z2;
	if (typeof z3 === "string" && z3.trim()) out.Z3 = z3;
	if (typeof z4 === "string" && z4.trim()) out.Z4 = z4;
	return out;
}

export function extractZoneSqlFromReport(
	report: Record<string, unknown>,
	selectedDialects: Array<"duckdb" | "databricks">,
	artifacts: Array<Record<string, unknown>> = [],
): Record<string, string> {
	const rawSql = report.four_zone_sql as Record<string, unknown> | undefined;
	if (rawSql && typeof rawSql === "object") {
		const preferDb =
			selectedDialects.includes("databricks") && rawSql.databricks;
		const nested = preferDb ? rawSql.databricks : rawSql.duckdb;
		const mapped = mapZoneBundle(nested ?? rawSql);
		if (Object.keys(mapped).length > 0) return mapped;
	}
	const sqlArtifact = artifacts.find(
		(a) =>
			a.artifact_id === "four_zone_sql" ||
			String(a.filename ?? "").endsWith(".sql"),
	);
	if (typeof sqlArtifact?.content === "string" && sqlArtifact.content.trim()) {
		return { Z4: sqlArtifact.content };
	}
	return {};
}

export function extractTopologyRowsFromReport(
	report: Record<string, unknown>,
	artifacts: Array<Record<string, unknown>> = [],
): PipelineTopologyRow[] {
	const rows: PipelineTopologyRow[] = [];
	const bp = report.blueprint as Record<string, unknown> | undefined;
	const contractObj = (bp?.contract ?? report.contract) as
		| Record<string, unknown>
		| undefined;

	const pushList = (list: unknown, kind: string, zone: string) => {
		if (!Array.isArray(list)) return;
		for (const item of list) {
			if (!item || typeof item !== "object") continue;
			const rec = item as Record<string, unknown>;
			const name = String(rec.name ?? rec.entity_name ?? "");
			if (!name) continue;
			const srcMaps = Array.isArray(rec.source_mappings)
				? rec.source_mappings.map(String).join(", ")
				: String(rec.source_mapping ?? rec.parent_hub ?? "");
			rows.push({
				entity_name: name,
				entity_kind: kind,
				zone,
				source_mapping: srcMaps,
			});
		}
	};

	if (contractObj && typeof contractObj === "object") {
		pushList(contractObj.hubs, "HUB", "Z2");
		pushList(contractObj.links, "LINK", "Z2");
		pushList(contractObj.satellites, "SAT", "Z2");
		pushList(contractObj.pit_tables, "PIT", "Z3");
		pushList(contractObj.bridge_tables, "BRIDGE", "Z3");
		pushList(contractObj.reference_tables, "REF", "Z0");
	}

	if (rows.length === 0) {
		const irArt = artifacts.find(
			(a) =>
				a.artifact_id === "final_ir" ||
				a.filename === "final_canonical_ir.json",
		);
		if (typeof irArt?.content === "string") {
			try {
				const ir = JSON.parse(irArt.content) as Record<string, unknown>;
				pushList(ir.hubs, "HUB", "Z2");
				pushList(ir.links, "LINK", "Z2");
				pushList(ir.satellites, "SAT", "Z2");
				pushList(ir.pit_tables, "PIT", "Z3");
				pushList(ir.bridge_tables, "BRIDGE", "Z3");
			} catch {
				// ignore invalid JSON IR
			}
		}
	}

	return rows;
}

export function extractBaselineContractFromReportOrArtifacts(
	report: Record<string, unknown> | null,
	artifacts: Array<Record<string, unknown>> = [],
): string | null {
	if (typeof report?.contract_content === "string" && report.contract_content) {
		return report.contract_content;
	}
	const art = artifacts.find(
		(a) =>
			a.artifact_id === "final_contract" ||
			a.filename === "final_contract.yaml",
	);
	if (typeof art?.content === "string" && art.content.trim()) {
		return art.content;
	}
	const bp = report?.blueprint as Record<string, unknown> | undefined;
	if (bp?.contract && typeof bp.contract === "object") {
		return JSON.stringify(bp.contract);
	}
	return null;
}

export function aggregateStagePillStatus(
	internalStages: string[],
	stageStatuses: Record<string, string>,
	pill: string,
): string {
	const candidates = [
		stageStatuses[pill],
		...internalStages.map((s) => stageStatuses[s]),
	].filter((v): v is string => Boolean(v));
	if (candidates.length === 0) return "PENDING";
	let best = "PENDING";
	let bestRank = 0;
	for (const st of candidates) {
		const rank = STAGE_STATUS_RANK[st.toUpperCase()] ?? 1;
		if (rank > bestRank) {
			bestRank = rank;
			best = st;
		}
	}
	return best;
}
