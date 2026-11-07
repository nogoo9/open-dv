/**
 * Normative TypeScript DTOs for `VaultObservabilityEvent` (`schemas/vault-observability-event.schema.json`).
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
	event_type?: string | undefined;
	run_id: string;
	trace_id?: string | undefined;
	span_id?: string | undefined;
	parent_span_id?: string | null | undefined;
	timestamp_utc: string;
	stage_id: StageId;
	phase_id?: PhaseId | undefined;
	step_id?: string | undefined;
	gate_id?: GateId | undefined;
	rule_code?: string | undefined;
	contract_version?: "5.0" | "6.0" | undefined;
	confidence?: number | undefined;
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
