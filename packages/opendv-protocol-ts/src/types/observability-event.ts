/**
 * Normative TypeScript DTOs for `VaultObservabilityEvent` (`schemas/vault-observability-event.schema.json`)
 * and the 12-View Explainability & Observability Workbench (`WB-00` through `WB-11`).
 */

import type { DataClassification } from "./common";

export type { DataClassification };

export type StageId =
	| "STAGE_00_ORCHESTRATION"
	| "STAGE_01_SCOUT_SOURCE"
	| "STAGE_02_SCOUT_UNSTRUCTURED"
	| "STAGE_03_SHIP_VAULT_PASS1"
	| "STAGE_04_SHIP_VAULT_PASS2"
	| "STAGE_05_AXI_DV_STATIC_GATES"
	| "STAGE_06_AXI_DV_DYNAMIC_SANDBOX"
	| "STAGE_07_DETERMINISTIC_COMPILER";

export const ALL_STAGE_IDS: readonly StageId[] = [
	"STAGE_00_ORCHESTRATION",
	"STAGE_01_SCOUT_SOURCE",
	"STAGE_02_SCOUT_UNSTRUCTURED",
	"STAGE_03_SHIP_VAULT_PASS1",
	"STAGE_04_SHIP_VAULT_PASS2",
	"STAGE_05_AXI_DV_STATIC_GATES",
	"STAGE_06_AXI_DV_DYNAMIC_SANDBOX",
	"STAGE_07_DETERMINISTIC_COMPILER",
] as const;

export type PhaseId =
	| "P1_INGESTION"
	| "P2_PROFILING"
	| "P3_EXTRACTION"
	| "P4_SYNTHESIS_PASS1"
	| "P5_SYNTHESIS_PASS2"
	| "P6_VERIFICATION"
	| "P7_DYNAMIC_SANDBOX"
	| "P8_OPERATIONS";

export type WorkbenchViewId =
	| "WB-00"
	| "WB-01"
	| "WB-02"
	| "WB-03"
	| "WB-04"
	| "WB-05"
	| "WB-06"
	| "WB-07"
	| "WB-08"
	| "WB-09"
	| "WB-10"
	| "WB-11";

export const ALL_WORKBENCH_VIEW_IDS: readonly WorkbenchViewId[] = [
	"WB-00",
	"WB-01",
	"WB-02",
	"WB-03",
	"WB-04",
	"WB-05",
	"WB-06",
	"WB-07",
	"WB-08",
	"WB-09",
	"WB-10",
	"WB-11",
] as const;

export type StepId =
	| "VIZ-PIPE-00"
	| "VIZ-STEP-01"
	| "VIZ-STEP-02"
	| "VIZ-STEP-03"
	| "VIZ-STEP-04"
	| "VIZ-STEP-05"
	| "VIZ-STEP-06"
	| "VIZ-STEP-07"
	| "VIZ-STEP-08";

export type VizId =
	| StepId
	| "VIZ-GATE-01"
	| "VIZ-GATE-02"
	| "VIZ-GATE-03"
	| "VIZ-GATE-04"
	| "VIZ-GATE-05"
	| "VIZ-GATE-06"
	| "VIZ-GATE-07"
	| "VIZ-GATE-08";

export type GateId =
	| "G1"
	| "G2"
	| "G3"
	| "G4"
	| "G5"
	| "G6"
	| "G_DYN"
	| "G_AUD";

export type AgentPersona =
	| "vault-first-mate"
	| "scout-source"
	| "scout-unstructured"
	| "ship-vault"
	| "axi-dv"
	| "deterministic-compiler";

export type VerdictStatus =
	| "PASS"
	| "AUTO-FIX"
	| "AUTO_REPAIRED"
	| "REJECT-WITH-TRACE"
	| "ASK-USER";

export interface SemanticHashPillDTO {
	pill_label: string; // e.g. "[HK: 5D15..0AE4 | GLOBAL:LP-101 CalPERS]"
	full_sha256_hex: string; // 64 uppercase hex characters
	bkcc_prefix: string; // e.g. "GLOBAL" or "LEGACY_GP_ADMIN"
	business_key: string; // e.g. "LP-101"
	entity_label: string; // e.g. "CalPERS"
}

export interface Layer1StoryBannerDTO {
	headline: string; // Max 140 chars, plain-English
	business_impact: string; // Max 200 chars, business/cost impact
	verdict_pill: VerdictStatus;
}

export interface Layer2VisualPayloadDTO {
	semantic_hash_pills: SemanticHashPillDTO[];
	summary_metrics: Record<string, number | string | boolean>;
	[key: string]: unknown;
}

export interface Layer3FormalProofDTO {
	algorithm_ref?: string | undefined;
	formula_latex?: string | undefined;
	toon_diagnostic_payload?: string | undefined;
	contract_yaml_diff?: string | undefined;
	sqlglot_ast_summary?: string | undefined;
}

export interface ContextRefsDTO {
	graph_a_sha256?: string | undefined;
	graph_b_sha256?: string | undefined;
	contract_yaml_pre_sha256?: string | null | undefined;
	contract_yaml_post_sha256?: string | null | undefined;
	canonical_ir_sha256?: string | null | undefined;
	compiled_sql_sha256?: string | null | undefined;
	toon_trace_sha256?: string | null | undefined;
}

export interface RemediationDTO {
	codemod_type: string;
	target_entity: string;
	patch_diff: string;
	action_description?: string | undefined;
}

export interface VaultObservabilityEventDTO {
	event_id: string;
	run_id: string;
	trace_id?: string | undefined;
	span_id?: string | undefined;
	parent_span_id?: string | null | undefined;
	timestamp_utc: string;
	stage_id: StageId;
	phase_id?: PhaseId | undefined;
	step_id?: StepId | undefined;
	gate_id?: GateId | undefined;
	rule_code?: string | undefined;
	contract_version?: "5.0" | "6.0" | undefined;
	confidence?: number | undefined;
	workbench_view_id: WorkbenchViewId;
	viz_id: VizId;
	agent_persona: AgentPersona;
	status: VerdictStatus;
	remediation?: RemediationDTO | undefined;
	rule_ids_evaluated?: string[] | undefined;
	context_refs?: ContextRefsDTO | undefined;
	data_classification?: DataClassification | undefined;
	layer1_story_banner: Layer1StoryBannerDTO;
	layer2_visual_payload: Layer2VisualPayloadDTO;
	layer3_formal_proof?: Layer3FormalProofDTO | undefined;
}

export interface WorkbenchViewBindingMeta {
	viewId: WorkbenchViewId;
	stageId: StageId;
	primaryVizId: VizId;
	title: string;
	shortTitle: string;
	sceneNumber: number;
}

/**
 * Canonical 12-View (`WB-00`–`WB-11`) stage and visualization binding metadata (docs/10 §4–§5).
 */
export const WORKBENCH_VIEW_STAGE_MAP: Record<
	WorkbenchViewId,
	WorkbenchViewBindingMeta
> = {
	"WB-00": {
		viewId: "WB-00",
		stageId: "STAGE_00_ORCHESTRATION",
		primaryVizId: "VIZ-PIPE-00",
		title: "Master Pipeline Command Center & 6-Gate Scoreboard",
		shortTitle: "Command Center",
		sceneNumber: 1,
	},
	"WB-01": {
		viewId: "WB-01",
		stageId: "STAGE_00_ORCHESTRATION",
		primaryVizId: "VIZ-STEP-01",
		title: "Dual-Graph Grounding & Anti-Pattern Counterfactual Inspector",
		shortTitle: "Dual-Graph Grounding",
		sceneNumber: 1,
	},
	"WB-02": {
		viewId: "WB-02",
		stageId: "STAGE_01_SCOUT_SOURCE",
		primaryVizId: "VIZ-STEP-02",
		title: "Empirical Column Profiler & Candidate Discovery Matrix",
		shortTitle: "Empirical Profiler",
		sceneNumber: 1,
	},
	"WB-03": {
		viewId: "WB-03",
		stageId: "STAGE_02_SCOUT_UNSTRUCTURED",
		primaryVizId: "VIZ-STEP-04",
		title: "Unstructured PDF Span-Grounding & 3-Tier ER Inspector",
		shortTitle: "Unstructured PDF & ER",
		sceneNumber: 2,
	},
	"WB-04": {
		viewId: "WB-04",
		stageId: "STAGE_03_SHIP_VAULT_PASS1",
		primaryVizId: "VIZ-STEP-05",
		title: "Pass 1 Exhaustive Column Triage Sankey & Surrogate Sink",
		shortTitle: "Column Triage Sankey",
		sceneNumber: 3,
	},
	"WB-05": {
		viewId: "WB-05",
		stageId: "STAGE_04_SHIP_VAULT_PASS2",
		primaryVizId: "VIZ-STEP-06",
		title: "Pass 2/3 ELM Topology & 3-Tier BKCC Collision Shield",
		shortTitle: "ELM Topology & BKCC",
		sceneNumber: 4,
	},
	"WB-06": {
		viewId: "WB-06",
		stageId: "STAGE_05_AXI_DV_STATIC_GATES",
		primaryVizId: "VIZ-GATE-03",
		title: "Gate 3 Topological DAG & Grain Matrix Inspector",
		shortTitle: "Topological DAG",
		sceneNumber: 5,
	},
	"WB-07": {
		viewId: "WB-07",
		stageId: "STAGE_05_AXI_DV_STATIC_GATES",
		primaryVizId: "VIZ-GATE-05",
		title: "Nullable-FK Link Splitter & Gate 4 YAGNI Cost Guard",
		shortTitle: "Link Splitter & YAGNI",
		sceneNumber: 3,
	},
	"WB-08": {
		viewId: "WB-08",
		stageId: "STAGE_05_AXI_DV_STATIC_GATES",
		primaryVizId: "VIZ-GATE-07",
		title: "Closed-Loop Self-Healing Diff & TOON Diagnostic Workbench",
		shortTitle: "Self-Healing & TOON",
		sceneNumber: 5,
	},
	"WB-09": {
		viewId: "WB-09",
		stageId: "STAGE_01_SCOUT_SOURCE",
		primaryVizId: "VIZ-STEP-03",
		title: "Intra-Source Schema Drift Radar (S1(t) -> S1(t+1))",
		shortTitle: "Schema Drift Radar",
		sceneNumber: 6,
	},
	"WB-10": {
		viewId: "WB-10",
		stageId: "STAGE_06_AXI_DV_DYNAMIC_SANDBOX",
		primaryVizId: "VIZ-GATE-08",
		title: "Gate 5 Dynamic DuckDB Shadow Sandbox & Evolution Matrix",
		shortTitle: "DuckDB Sandbox",
		sceneNumber: 6,
	},
	"WB-11": {
		viewId: "WB-11",
		stageId: "STAGE_07_DETERMINISTIC_COMPILER",
		primaryVizId: "VIZ-STEP-07",
		title: "Pre-Hash Stepper (Alg A.5) & 4-Zone SQL Rosetta Stone",
		shortTitle: "4-Zone SQL Rosetta",
		sceneNumber: 7,
	},
};
