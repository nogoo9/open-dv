/**
 * Normative HTTP REST & SSE Wire DTOs for `/api/v1/*` (`docs/08` §5 & `docs/10` §2.3).
 */

import type { HarnessProvider, ModelTier, RbacRole, RunStatus } from "./common";
import type {
	StageId,
	VaultObservabilityEventDTO,
	WorkbenchViewId,
} from "./observability-event";

export interface OpenDVClientOptions {
	baseUrl: string;
	rbacRole?: string | undefined;
	headers?: Record<string, string> | undefined;
	fetchImpl?: typeof fetch | undefined;
}

export type GateStatus =
	| "PENDING"
	| "PASS"
	| "AUTO-FIX"
	| "AUTO_REPAIRED"
	| "REJECT-WITH-TRACE"
	| "ASK-USER";

export interface GateScorecardSummaryDTO {
	gate_0_profile_drift: GateStatus;
	gate_1_contract_grain: GateStatus;
	gate_2_sqlglot_ast: GateStatus;
	gate_3_networkx_dag: GateStatus;
	gate_4_yagni_cost_guard: GateStatus;
	gate_5_duckdb_sandbox: GateStatus;
}

export interface ProjectDTO {
	project_id: string;
	display_name: string;
	domain_namespace: string;
	graph_a_sha256: string;
	latest_run_id?: string | null | undefined;
	created_at_utc: string;
}

export interface CreateProjectRequest {
	project_id: string;
	display_name: string;
	domain_namespace: string;
	graph_a_ontology_path: string;
	bronze_sources: string[];
}

export interface CreateRunRequest {
	project_id: string;
	harness_provider?: HarnessProvider | undefined;
	model_tier_override?: ModelTier | null | undefined;
	contract_path?: string | null | undefined;
	max_repair_retries?: number | undefined;
	run_sandbox_batches?: boolean | undefined;
}

export interface ProjectRunDTO {
	run_id: string;
	project_id: string;
	harness_provider: HarnessProvider;
	status: RunStatus;
	started_at_utc: string;
	completed_at_utc?: string | null | undefined;
	current_stage_id: StageId;
	current_view_id: WorkbenchViewId;
	event_count: number;
	gate_scorecard: GateScorecardSummaryDTO;
	active_hold_ids: string[];
	events_sse_url: string;
	gates_url: string;
	sandbox_url: string;
	decisions_url: string;
}

export interface CaptainHoldDecisionRequest {
	event_id: string;
	hold_id?: string | null | undefined;
	decision_type: "RESOLVE_CAPTAIN_HOLD";
	action: "APPROVE" | "REJECT" | "ANSWER";
	selected_candidate_id?: string | null | undefined;
	resolution_notes?: string | null | undefined;
}

export interface CaptainHoldDecisionResponse {
	decision_id: string;
	status: "ACCEPTED" | "REJECTED";
	resumed_run_id: string;
	next_stage_id: StageId;
	next_event_id: string;
}

export interface SandboxQueryRequest {
	query_type:
		| "ADVERSARIAL_BATCHES_1_TO_4"
		| "QUERY_D10_PIT"
		| "QUERY_D10B_VALUATION";
	assertion_cutoff_ts?: string | undefined;
}

export interface SandboxExecutionResponse {
	run_id: string;
	all_batches_passed: boolean;
	batch_verdicts: Record<string, "PASS" | "FAIL">;
	dynamic_rules_passed: number;
	dynamic_rules_total: number;
	replay_delta_rows: number;
	query_d10_rows: Record<string, unknown>[];
}

export interface WorkbenchViewProjectionDTO {
	run_id: string;
	view_id: WorkbenchViewId;
	rbac_role?: RbacRole | string | undefined;
	latest_event?: VaultObservabilityEventDTO | null | undefined;
	events?: VaultObservabilityEventDTO[] | undefined;
	[key: string]: unknown;
}

export interface HealthResponseDTO {
	status: string;
	version: string;
	[key: string]: unknown;
}
