/**
 * Pipeline Specification Builder Utilities (`specBuilderUtils.ts`)
 *
 * Interactive draft models, default starter templates, and serialization
 * utilities for `PipelineInputSpec` (`packages/opendv-agent-harness/src/opendv_agent_harness/pipeline.py`).
 */

import type { StewardHoldPolicyOption } from "../modals/NewRunFormFields";
import { parse as parseYaml } from "yaml";
import { EXPLORER_COLORS as C, EXPLORER_FONTS as F } from "../design-tokens";

export const S_STEP4 = {
	sec: { backgroundColor: C.bgSurface, borderRadius: 8, border: `1px solid ${C.borderLine}`, padding: 16 },
	box: { backgroundColor: C.bgPage, borderRadius: 6, border: `1px solid ${C.borderLine}`, padding: 12, marginBottom: 12 },
	between: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 },
	btnRun: (exec: boolean) => ({ padding: "8px 16px", backgroundColor: exec ? "#94A3B8" : C.borderSelected, color: "#FFF", borderRadius: 5, fontSize: 13, fontWeight: 600, border: "none", cursor: exec ? "not-allowed" : "pointer" }),
	chip: (c: string, bg: string, b: string) => ({ padding: "2px 7px", borderRadius: 10, fontSize: 11, fontFamily: F.mono, fontWeight: 600, color: c, backgroundColor: bg, border: `1px solid ${b}`, display: "inline-flex", alignItems: "center" }),
	diag: (err: boolean) => ({ padding: "2px 7px", borderRadius: 10, fontSize: 11, fontFamily: F.mono, fontWeight: 600, color: err ? "#991B1B" : "#0369A1", backgroundColor: err ? "#FEF2F2" : "#F0F9FF", border: `1px solid ${err ? "#FECACA" : "#BAE6FD"}`, display: "inline-flex", alignItems: "center" }),
	step: (curr: boolean, done: boolean) => ({ padding: "4px 6px", borderRadius: 4, fontSize: 11, backgroundColor: curr ? "#EFF6FF" : done ? "#F0FDF4" : C.bgPage, border: `1px solid ${curr ? "#3B82F6" : done ? "#BBF7D0" : C.borderLine}`, color: curr ? "#1D4ED8" : done ? "#166534" : C.inkMuted }),
};

export type WaveTypeOption =
	| "COLD_START"
	| "INCREMENTAL_SOURCE"
	| "SCHEMA_DRIFT"
	| "UNSTRUCTURED_ONBOARDING";

export type OntologyPolicyOption = "off" | "recommend" | "auto_accept";

export interface InteractiveWaveSourceDraft {
	_key?: string | undefined;
	sourceSystem: string;
	tableName: string;
	dataPath: string;
	targetHubName: string;
	businessKeyColumns: string;
}

export interface InteractiveWaveDocDraft {
	_key?: string | undefined;
	docId: string;
	documentPath: string;
	targetHubName: string;
}

let draftItemCounter = 0;
function nextDraftKey(prefix: string): string {
	draftItemCounter += 1;
	return `${prefix}-${draftItemCounter}`;
}

export const toHubName = (slug: string): string => `hub_${slug.toLowerCase().trim()}`;

export interface InteractiveWaveDraft {
	waveIndex: number;
	waveName: string;
	waveType: WaveTypeOption;
	description: string;
	sources: InteractiveWaveSourceDraft[];
	unstructuredDocuments: InteractiveWaveDocDraft[];
}

export interface InteractivePipelineDraft {
	pipelineId: string;
	projectId: string;
	displayName: string;
	domainNamespace: string;
	graphAOntologyPath: string;
	initialContractPath: string;
	waveId: string;
	stewardHoldPolicy: StewardHoldPolicyOption;
	ontologyPolicy: OntologyPolicyOption;
	waves: InteractiveWaveDraft[];
}

export interface StagedPipelineSpec {
	mode: "interactive" | "yaml_upload";
	inputSpec?: Record<string, unknown> | undefined;
	inputSpecContent?: string | undefined;
	inputSpecPath?: string | undefined;
	fileName?: string | undefined;
	initialContractPath?: string | undefined;
	initialContractContent?: string | undefined;
	waveId?: string | undefined;
	stewardHoldPolicy: "pause" | "auto_approve_top_candidate" | "reject_on_hold";
	ontologyPolicy?: string | undefined;
	validated?: boolean | undefined;
}

export function createEmptySourceDraft(): InteractiveWaveSourceDraft {
	return {
		_key: nextDraftKey("src"),
		sourceSystem: "ERP_CORE",
		tableName: "bronze_orders",
		dataPath:
			"examples/pipeline_samples/global_commerce_vault/data/w1_erp_orders.csv",
		targetHubName: "hub_order",
		businessKeyColumns: "order_id",
	};
}

export function createEmptyDocDraft(): InteractiveWaveDocDraft {
	return {
		_key: nextDraftKey("doc"),
		docId: "doc_msa_001",
		documentPath:
			"examples/pipeline_samples/global_commerce_vault/docs/master_service_agreement.md",
		targetHubName: toHubName("customer"),
	};
}

export function createEmptyWaveDraft(waveIndex: number): InteractiveWaveDraft {
	return {
		waveIndex,
		waveName: `W${waveIndex}_Incremental`,
		waveType: waveIndex === 0 ? "COLD_START" : "INCREMENTAL_SOURCE",
		description: `Wave ${waveIndex} onboarding`,
		sources: [createEmptySourceDraft()],
		unstructuredDocuments: [],
	};
}

export function createDefaultPipelineDraft(): InteractivePipelineDraft {
	draftItemCounter = 0;
	return {
		pipelineId: "global_commerce_vault_run",
		projectId: "global_commerce_vault",
		displayName: "Global Commerce Data Vault 2.0",
		domainNamespace: "commerce",
		graphAOntologyPath:
			"examples/pipeline_samples/global_commerce_vault/ontology/graph_a_commerce.yaml",
		initialContractPath: "",
		waveId: "",
		stewardHoldPolicy: "pause",
		ontologyPolicy: "off",
		waves: [
			{
				waveIndex: 0,
				waveName: "W0_Cold_Start",
				waveType: "COLD_START",
				description: "Initial relational onboarding wave",
				sources: [
					{
						_key: nextDraftKey("src"),
						sourceSystem: "CRM_CORE",
						tableName: "bronze_customers",
						dataPath:
							"examples/pipeline_samples/global_commerce_vault/data/w0_crm_customers.csv",
						targetHubName: toHubName("customer"),
						businessKeyColumns: "customer_id",
					},
				],
				unstructuredDocuments: [],
			},
		],
	};
}

export function createOntologyStudioPipelineDraft(params?: {
	sessionId?: string | undefined;
	source?: string | undefined;
	domainNamespace?: string | undefined;
	pipelineId?: string | undefined;
	ontologyData?:
		| {
				canonical_business_concepts?: Array<{
					concept_name?: string;
					target_hub?: string;
				}>;
		  }
		| undefined;
}): InteractivePipelineDraft {
	const domain = params?.domainNamespace?.trim() || "asset_management";
	const pipelineId =
		params?.pipelineId?.trim() ||
		(params?.sessionId ? `pl_${params.sessionId}` : "pl_am_walkthrough");
	const isAmDomain = domain === "asset_management" || domain.includes("am");

	return {
		pipelineId,
		projectId: `proj_${domain}`,
		displayName: isAmDomain
			? "Asset Management Data Vault 2.0 Pipeline"
			: `${domain} Data Vault 2.0 Pipeline`,
		domainNamespace: domain,
		graphAOntologyPath: isAmDomain
			? "examples/asset_management/am_graph_a_ontology.yaml"
			: `examples/${domain}/${domain}_graph_a_ontology.yaml`,
		initialContractPath: "",
		waveId: "",
		stewardHoldPolicy: "pause",
		ontologyPolicy: "recommend",
		waves: [
			{
				waveIndex: 0,
				waveName: "W0_Cold_Start",
				waveType: "COLD_START",
				description: isAmDomain
					? "Initial Asset Management SEC EDGAR & FRED onboarding wave"
					: `Initial ${domain} onboarding wave`,
				sources: isAmDomain
					? [
							{
								_key: "src-sec-master",
								sourceSystem: "SEC_EDGAR",
								tableName: "stg_sec_security_master",
								dataPath:
									"examples/asset_management/data/sec_security_master.csv",
								targetHubName: toHubName("security"),
								businessKeyColumns: "ticker, cik",
							},
							{
								_key: "src-sec-13f",
								sourceSystem: "SEC_EDGAR",
								tableName: "stg_sec_13f_holdings",
								dataPath: "examples/asset_management/data/sec_13f_holdings.csv",
								targetHubName: "hub_investor",
								businessKeyColumns: "cik",
							},
							{
								_key: "src-fred-rates",
								sourceSystem: "FRED",
								tableName: "stg_fred_treasury_rates",
								dataPath:
									"examples/asset_management/data/fred_treasury_rates.csv",
								targetHubName: "hub_rate_index",
								businessKeyColumns: "series_id",
							},
						]
					: [createEmptySourceDraft()],
				unstructuredDocuments: isAmDomain
					? [
							{
								_key: "doc-sec-filing",
								docId: "doc_sec_filing_001",
								documentPath:
									"examples/asset_management/docs/sec_filing_sample.md",
								targetHubName: "hub_document",
							},
						]
					: [],
			},
		],
	};
}

export function draftToInputSpecObject(
	draft: InteractivePipelineDraft,
): Record<string, unknown> {
	const spec: Record<string, unknown> = {
		pipeline_id: draft.pipelineId.trim() || "custom_pipeline",
		project_id: draft.projectId.trim() || "custom_project",
		display_name: draft.displayName.trim() || "Open-DV Agentic Pipeline Run",
		domain_namespace: draft.domainNamespace.trim() || "general",
		graph_a_ontology_path: draft.graphAOntologyPath.trim(),
		ontology_policy: draft.ontologyPolicy,
		waves: draft.waves.map((wave, idx) => ({
			wave_index: wave.waveIndex ?? idx,
			wave_name: wave.waveName.trim() || `W${idx}`,
			wave_type: wave.waveType,
			description: wave.description.trim(),
			sources: wave.sources.map((src) => {
				const bks = src.businessKeyColumns
					.split(",")
					.map((col) => col.trim())
					.filter(Boolean);
				return {
					source_system: src.sourceSystem.trim(),
					table_name: src.tableName.trim(),
					data_path: src.dataPath.trim(),
					...(src.targetHubName.trim()
						? { target_hub_name: src.targetHubName.trim() }
						: {}),
					business_key_columns: bks,
				};
			}),
			unstructured_documents: wave.unstructuredDocuments.map((doc) => ({
				doc_id: doc.docId.trim(),
				document_path: doc.documentPath.trim(),
				...(doc.targetHubName.trim()
					? { target_hub_name: doc.targetHubName.trim() }
					: {}),
			})),
		})),
	};

	if (draft.initialContractPath.trim()) {
		spec.initial_contract_path = draft.initialContractPath.trim();
	}
	if (draft.waveId.trim()) {
		spec.wave_id = draft.waveId.trim();
	}

	return spec;
}

export function draftToYamlOrJsonText(draft: InteractivePipelineDraft): string {
	return JSON.stringify(draftToInputSpecObject(draft), null, 2);
}

export function draftToStagedSpec(
	draft: InteractivePipelineDraft,
	validated = false,
): StagedPipelineSpec {
	const inputSpec = draftToInputSpecObject(draft);
	const inputSpecContent = JSON.stringify(inputSpec, null, 2);
	return {
		mode: "interactive",
		inputSpec,
		inputSpecContent,
		initialContractPath: draft.initialContractPath.trim() || undefined,
		waveId: draft.waveId.trim() || undefined,
		stewardHoldPolicy: draft.stewardHoldPolicy,
		ontologyPolicy: draft.ontologyPolicy,
		validated,
	};
}

export const PIPELINE_STAGES = [
	{ id: "STAGE_00", label: "Orchestration" },
	{ id: "STAGE_01", label: "Scout Sources" },
	{ id: "STAGE_02", label: "Scout Docs" },
	{ id: "STAGE_03", label: "Ship Vault P1" },
	{ id: "STAGE_04", label: "Ship Vault P2" },
	{ id: "STAGE_05", label: "Static Gates" },
	{ id: "STAGE_06", label: "Dynamic Sandbox" },
	{ id: "STAGE_07", label: "Compiler" },
] as const;

export const INFERRED_ACTION_DESC: Record<string, string> = {
	COLD_START_N1: "Greenfield synthesis from ontology concepts.",
	VERIFIED_NO_OP: "Zero-Data deterministic verification of existing contract.",
	UNSTRUCTURED_FIRST: "Unstructured entity extraction.",
	SCHEMA_DRIFT: "Schema Drift repair & companion satellite synthesis.",
	HYBRID_PER_SOURCE: "Hybrid multi-source structured and unstructured ingestion.",
};

export interface RunArtifactsResult {
	duckdbSql: string;
	databricksSql: string;
	contractContent: string;
	outputBlueprint?: any;
}

export async function fetchRunArtifacts(
	fetcher: typeof fetch,
	apiUrl: string,
	rId: string,
	headers: Record<string, string>,
): Promise<RunArtifactsResult> {
	let duckdbSql = "", databricksSql = "", contractContent = "", outputBlueprint: any;
	try {
		const artRes = await fetcher(`${apiUrl}/runs/${encodeURIComponent(rId)}/artifacts`, { credentials: "include", headers });
		if (artRes.ok) {
			const arts: Array<{ artifact_id?: string; filename?: string; content?: string }> = await artRes.json();
			const fArt = (pat: string) => arts.find((a) => a.artifact_id?.includes(pat) || a.filename?.includes(pat))?.content ?? "";
			duckdbSql = fArt("four_zone") || fArt("final_sql");
			databricksSql = fArt("databricks");
			contractContent = fArt("final_contract") || fArt(".yaml");
			const bpArt = fArt("blueprint");
			if (bpArt) {
				try {
					outputBlueprint = parseYaml(bpArt);
				} catch {}
			}
		}
	} catch {}
	return { duckdbSql, databricksSql, contractContent, outputBlueprint };
}

export async function pollPipelineRun(
	fetcher: typeof fetch,
	apiUrl: string,
	rId: string,
	headers: Record<string, string>,
	onStageChange: (stageId: string) => void,
): Promise<Record<string, unknown> | null> {
	let pollAttempts = 0;
	while (pollAttempts < 600) {
		pollAttempts++;
		await new Promise((resolve) => setTimeout(resolve, 800));
		try {
			const sRes = await fetcher(`${apiUrl}/runs/${encodeURIComponent(rId)}`, { credentials: "include", headers });
			if (sRes.ok) {
				const statusData = (await sRes.json()) as Record<string, unknown>;
				const stageId = String(statusData.current_stage_id ?? "");
				if (stageId) onStageChange(stageId);
				const st = String(statusData.status ?? "");
				if (["COMPLETED", "FAILED", "PAUSED_HOLD", "ABORTED"].includes(st)) return statusData;
			}
		} catch {}
	}
	return null;
}

export const extractSqlString = (r: unknown): string =>
	typeof r === "string" ? r : (r && typeof r === "object") ? ((r as any).all_zones || (r as any).full_sql || Object.values(r as any).filter((v) => typeof v === "string").join("\n\n")) : "";

export function downloadBlob(content: string, filename: string, mime: string) {
	if (typeof window === "undefined" || !window.document || !content) return;
	const url = URL.createObjectURL(new Blob([content], { type: mime })), a = document.createElement("a");
	a.href = url; a.download = filename; document.body.appendChild(a); a.click(); document.body.removeChild(a); URL.revokeObjectURL(url);
}

export function resolveHubKeyTier(hubName: string, bp?: any): string {
	const hub = (bp?.contract?.hubs as Array<{ name?: string; business_key_tier?: string; business_key_sources?: Array<{ key_authority_tier?: string }> }> | undefined)
		?.find((h) => h.name === hubName);
	if (hub?.business_key_tier) return hub.business_key_tier;
	if (hub?.business_key_sources?.[0]?.key_authority_tier) return hub.business_key_sources[0].key_authority_tier;
	const lower = hubName.toLowerCase();
	if (lower.includes("instrument") || lower.includes("security") || lower.includes("party") || lower.includes("customer")) return "TIER_1";
	if (lower.includes("portfolio") || lower.includes("account") || lower.includes("holding") || lower.includes("rate")) return "TIER_2";
	return "TIER_3";
}

export function resolveDiffFallback(repData: any, outContract: any, outBp: any): any {
	const diff = repData?.evolution_diff;
	if (diff && (diff.added_hubs?.length || diff.added_links?.length || diff.added_satellites?.length)) return diff;
	const c = (outContract || outBp?.contract) as Record<string, unknown> | undefined;
	if (!c) return diff;
	const hubs = Array.isArray(c.hubs) ? c.hubs.map((h: any) => h.name || h.hub_name || String(h)).filter(Boolean) : [];
	const links = Array.isArray(c.links) ? c.links.map((l: any) => l.name || l.link_name || String(l)).filter(Boolean) : [];
	const sats = Array.isArray(c.satellites) ? c.satellites.map((s: any) => s.name || s.satellite_name || String(s)).filter(Boolean) : [];
	if (!hubs.length && !links.length && !sats.length) return diff;
	return {
		...(diff ?? {}),
		added_hubs: diff?.added_hubs?.length ? diff.added_hubs : hubs,
		added_links: diff?.added_links?.length ? diff.added_links : links,
		added_satellites: diff?.added_satellites?.length ? diff.added_satellites : sats,
		companion_satellites: diff?.companion_satellites ?? [],
		widened_columns: diff?.widened_columns ?? [],
		ontology_deltas_applied: diff?.ontology_deltas_applied ?? [],
	};
}

