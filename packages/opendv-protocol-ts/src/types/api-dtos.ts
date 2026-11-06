/**
 * Normative HTTP REST & SSE Wire DTOs for `/api/v1/*` (`docs/08` §5 & `docs/10` §2.3).
 * Re-exported directly from OpenAPI 3.1 generated types (Issue #97).
 */

import type { VaultObservabilityEventDTO } from "./observability-event";
import type { components, paths } from "./openapi";

export type { components, paths };

// Direct OpenAPI Schema Re-exports
export type ProfileRequest = components["schemas"]["ProfileRequest"];
export type ProfileResponse = components["schemas"]["ProfileResponse"];
export type CompileRequest = components["schemas"]["CompileRequest"];
export type CompileResponse = components["schemas"]["CompileResponse"];
export type VerifyRequest = components["schemas"]["VerifyRequest"];
export type VerifyResponse = components["schemas"]["VerifyResponse"];
export type SandboxQueryRequest = components["schemas"]["SandboxQueryRequest"];
export type SandboxQueryResponse =
	components["schemas"]["SandboxQueryResponse"];
export type ExplorerBundleDTO = components["schemas"]["ExplorerBundleDTO"];
export type RunArtifactDTO = components["schemas"]["RunArtifactDTO"];
export type PipelineRunHandle = components["schemas"]["PipelineRunHandle"];
export type PipelineRunRequest = components["schemas"]["PipelineRunRequest"];
export type PipelineValidateRequest =
	components["schemas"]["PipelineValidateRequest"];
export type CreateProjectRequest =
	components["schemas"]["CreateProjectRequest"];
export type CreateRunRequest = components["schemas"]["CreateRunRequest"];
export type ProjectRunDTO = components["schemas"]["ProjectRunDTO"];
export type RawCaptainHoldDecisionRequest =
	components["schemas"]["CaptainHoldDecisionRequest"];
export type RawCaptainHoldDecisionResponse =
	components["schemas"]["CaptainHoldDecisionResponse"];

export interface CaptainHoldDecisionRequest {
	action: "APPROVE" | "REJECT" | "ANSWER";
	decision?: ("APPROVE" | "REJECT" | "ANSWER") | null | undefined;
	decision_type?: "RESOLVE_CAPTAIN_HOLD" | undefined;
	event_id?: string | null | undefined;
	hold_id?: string | null | undefined;
	resolution_notes?: string | null | undefined;
	selected_candidate_id?: string | null | undefined;
	steward_id?: string | undefined;
	steward_rationale?: string | null | undefined;
}

export interface CaptainHoldDecisionResponse {
	decision_id: string;
	status: "ACCEPTED" | "REJECTED";
	resumed_run_id: string;
	next_stage_id: string;
	next_event_id: string;
	action?: string | undefined;
	hold_id?: string | undefined;
	resolved_at_utc?: string | undefined;
	updated_run_status?:
		| components["schemas"]["opendv_service__models__RunStatus"]
		| undefined;
}
export type SandboxExecutionResponse =
	components["schemas"]["SandboxExecutionResponse"];
export type GateScorecardSummary =
	components["schemas"]["GateScorecardSummary"];
export type GateVerdict = components["schemas"]["GateVerdict"];
export type ArtifactDTO = components["schemas"]["ArtifactDTO"];
export type RenderedOutput = components["schemas"]["RenderedOutput"];

// Ergonomic Aliases
export type GateScorecardSummaryDTO = GateScorecardSummary;
export type GateVerdictDTO = GateVerdict;

// Canonical Error Taxonomy across REST and CLI
export type ErrorCode =
	| "OK"
	| "VERIFICATION_FAILED"
	| "PATH_CONTAINMENT_ERROR"
	| "INVALID_REQUEST"
	| "RESOURCE_NOT_FOUND"
	| "RUN_NOT_FOUND"
	| "PROJECT_NOT_FOUND"
	| "HOLD_NOT_FOUND"
	| "ARTIFACT_NOT_FOUND"
	| "PAYLOAD_TOO_LARGE"
	| "UNSUPPORTED_FORMAT"
	| "INPUT_VALIDATION_ERROR"
	| "SCHEMA_VALIDATION_ERROR"
	| "SOURCE_LOAD_ERROR"
	| "SANDBOX_EXECUTION_ERROR"
	| "PROVIDER_CONFIG_ERROR"
	| "DETERMINISTIC_PROVIDER_REJECTED"
	| "CONTRACT_VERSION_MISMATCH"
	| "CAS_INTEGRITY_FAILURE"
	| "INTERNAL_ERROR"
	| "API_UNREACHABLE"
	| "PROVIDER_UNAVAILABLE";

export interface ErrorDetail {
	field: string;
	message: string;
	error_type?: string | undefined;
}

export interface ProblemDetail {
	type: string;
	title: string;
	status: number;
	detail: string;
	instance?: string | null | undefined;
	error_code: ErrorCode;
	invalid_params?: ErrorDetail[] | undefined;
	extensions?: Record<string, unknown> | undefined;
}

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

export interface ProjectDTO {
	project_id: string;
	display_name: string;
	domain_namespace: string;
	graph_a_sha256: string;
	latest_run_id?: string | null | undefined;
	created_at_utc: string;
}

export interface WorkbenchViewProjectionDTO {
	run_id: string;
	view_id?: string | undefined;
	rbac_role?: string | undefined;
	latest_event?: VaultObservabilityEventDTO | null | undefined;
	events?: VaultObservabilityEventDTO[] | undefined;
	[key: string]: unknown;
}

export interface HealthResponseDTO {
	status: string;
	version: string;
	[key: string]: unknown;
}
