/**
 * TDD Tests for UAT-22 & UAT-24b..c Guards, Budget Hold Resume, and Hold Resolution
 */

import { describe, expect, it } from "bun:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { processJobPayloadUpdate } from "../src/ontology/store/job-monitor-helpers";
import { studioStore } from "../src/ontology/store/studio-store";
import { Step4AgentDiscovery } from "../src/ontology/wizard/Step4AgentDiscovery";

const BASE_SESSION = {
	session_id: "ses-guard-01",
	domain_namespace: "wealth_mgmt",
	enterprise_owner: "Enterprise",
	status: "ACTIVE",
	active_version: "1.0.0",
	head_sha256: "a".repeat(64),
	created_at: "2026-11-20T00:00:00Z",
	updated_at: "2026-11-20T00:00:00Z",
	wizard_schema_version: 2,
};

describe("Platform Guards, Budget Hold Resume & Hold Resolution TDD", () => {
	it("raiseBudgetAndResume sends valid PlatformConfigUpdateRequest (no extra response keys) and raises max_output_tokens on TOKEN_BUDGET_EXCEEDED", async () => {
		studioStore.getState().reset();
		studioStore.setState({
			activeSession: BASE_SESSION,
			budgetHold: {
				job_id: "job-tok-1",
				reason: "TOKEN_BUDGET_EXCEEDED",
				total_tokens: 1800,
				cost_usd: 0.02,
			},
		});

		let putPayload: Record<string, unknown> | null = null;
		let constructCalled = false;
		const mockFetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
			const url = String(input);
			if (
				url.endsWith("/api/v1/platform/config") &&
				(!init?.method || init.method === "GET")
			) {
				return new Response(
					JSON.stringify({
						workspace_root: "/tmp/ws",
						state_dir: "/tmp/state",
						cas_dir: "/tmp/cas",
						ledgers_dir: "/tmp/ledgers",
						persisted_config_path: "/tmp/platform_config.json",
						providers: [],
						resolved_persona_models: {},
						harness: {
							provider: "agy",
							budget: {
								max_turns: 12,
								max_input_tokens: 200000,
								max_output_tokens: 512,
								timeout_seconds: 600,
								max_cost_usd: 5.0,
							},
						},
					}),
					{ status: 200 },
				);
			}
			if (url.endsWith("/api/v1/platform/config") && init?.method === "PUT") {
				putPayload = JSON.parse(String(init.body ?? "{}")) as Record<
					string,
					unknown
				>;
				return new Response(JSON.stringify({ harness: putPayload.harness }), {
					status: 200,
				});
			}
			if (url.includes("/construct") && init?.method === "POST") {
				constructCalled = true;
				return new Response(
					JSON.stringify({ job_id: "job-resumed-2", status: "COMPLETED" }),
					{
						status: 202,
					},
				);
			}
			return new Response("[]", { status: 200 });
		}) as unknown as typeof fetch;

		const ok = await studioStore.getState().raiseBudgetAndResume(5, mockFetch);
		expect(ok).toBe(true);
		expect(constructCalled).toBe(true);
		expect(putPayload).not.toBeNull();
		expect("workspace_root" in (putPayload ?? {})).toBe(false);
		expect("providers" in (putPayload ?? {})).toBe(false);
		const putBudget = (
			(putPayload as Record<string, unknown>).harness as Record<string, unknown>
		).budget as Record<string, unknown>;
		expect(Number(putBudget.max_output_tokens)).toBeGreaterThanOrEqual(64000);
		expect(Number(putBudget.max_cost_usd)).toBe(10);
		studioStore.getState().reset();
	});

	it("processJobPayloadUpdate updates sessionCostTokens and sessionCostUsd live on budget_hold", async () => {
		studioStore.getState().reset();
		studioStore.setState({ activeSession: BASE_SESSION });

		await processJobPayloadUpdate({
			set: studioStore.setState,
			get: studioStore.getState,
			appendSseEvent: () => {},
			handleAutoCommittedCompletion: async () => {},
			getCurrentGen: () => 0,
			sid: BASE_SESSION.session_id,
			jobId: "job-cost-1",
			jobKind: "construct",
			payload: {
				escalation_reason: "TOKEN_BUDGET_EXCEEDED",
				total_tokens: 2345,
				cost_usd: 0.0321,
			},
			fetchImpl: fetch,
			opts: { eventType: "budget_hold", myGen: 0 },
		});

		expect(studioStore.getState().sessionCostTokens).toBeGreaterThanOrEqual(
			2345,
		);
		expect(studioStore.getState().sessionCostUsd).toBeGreaterThanOrEqual(
			0.0321,
		);
		studioStore.getState().reset();
	});

	it("Step4AgentDiscovery disables start button with 'Run in progress — view live logs' when pipeline run is active", () => {
		const html = renderToStaticMarkup(
			createElement(Step4AgentDiscovery, {
				sessionId: BASE_SESSION.session_id,
				isRunningPipeline: true,
				platformConfig: {
					workspace_root: "/ws",
					state_dir: "/ws/.opendv/state",
					cas_dir: "/ws/.opendv/state/cas",
					ledgers_dir: "/ws/.opendv/state/ledgers",
					persisted_config_path: "/ws/.opendv/state/platform_config.json",
					server_host: "127.0.0.1",
					server_port: 18420,
					max_inline_bytes: 52428800,
					cors_origins: [],
					web_ui_bundled: true,
					remote_unauthenticated_mode: false,
					resolved_persona_models: {},
					providers: [
						{
							provider: "agy",
							execution_mode: "cli",
							available: true,
							binary_name: "agy",
							binary_found: true,
							sdk_installed: false,
							auth_mode: "cli_session",
							credentials_present: true,
							default_reasoning_model: "gemini-4-argon-high",
							default_fast_model: "gemini-3.8-flash-high",
							status_message: "ok",
						},
					],
					harness: {
						provider: "agy",
						model_overrides: {
							fast: "gemini-3.8-flash-high",
							reasoning: "gemini-4-argon-high",
						},
						agent_models: {},
						auth: {
							auth_mode: "cli_session",
							mode: "cli_session",
							require_credentials_present: false,
							require_preflight: false,
						},
						budget: {
							max_turns: 12,
							max_input_tokens: 200000,
							max_output_tokens: 64000,
							timeout_seconds: 600,
							max_cost_usd: 5,
						},
						sandbox: {
							read_only_workspace: true,
							allow_shell_execution: false,
							allow_network_access: false,
							allowed_builtin_tools: [],
							denied_builtin_tools: [],
						},
						steward_hold_policy: "pause",
						ontology_policy: "recommend",
						stage_output_policy: "llm_only",
						allow_cli_schema_omission_retry: false,
					},
				},
			}),
		);
		expect(html).toContain("Run in progress — view live logs");
		expect(html).toMatch(
			/<button[^>]*data-testid="btn-run-agent-discovery"[^>]*disabled/i,
		);
	});

	it("resolveActiveHold polls run completion and hydrates terminal state when EventSource is not open", async () => {
		studioStore.getState().reset();
		studioStore.setState({
			activeSession: BASE_SESSION,
			activeRunId: "run-hold-1",
			activeHold: {
				hold_id: "hold-1",
				run_id: "run-hold-1",
				stage_id: "STAGE_05_AXI_DV_STATIC_GATES",
				headline: "Unmapped",
				status: "PENDING",
			},
		});

		let pollCalls = 0;
		const mockFetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
			const url = String(input);
			if (url.includes("/decisions") && init?.method === "POST") {
				return new Response(
					JSON.stringify({ updated_run_status: "RUNNING", status: "ACCEPTED" }),
					{ status: 200 },
				);
			}
			if (url.endsWith("/api/v1/runs")) {
				return new Response(
					JSON.stringify([
						{
							run_id: "run-hold-1",
							session_id: BASE_SESSION.session_id,
							status: pollCalls >= 1 ? "COMPLETED" : "RUNNING",
						},
					]),
					{ status: 200 },
				);
			}
			if (url.endsWith("/api/v1/runs/run-hold-1")) {
				pollCalls += 1;
				return new Response(
					JSON.stringify({
						run_id: "run-hold-1",
						session_id: BASE_SESSION.session_id,
						status: pollCalls >= 1 ? "COMPLETED" : "RUNNING",
					}),
					{ status: 200 },
				);
			}
			if (url.endsWith("/api/v1/runs/run-hold-1/report")) {
				return new Response(
					JSON.stringify({
						status: "COMPLETED",
						gate_scorecard: { gate_0_profile_drift: "PASS" },
						four_zone_sql: { duckdb: { zone_0_staging: "SELECT 1;" } },
					}),
					{ status: 200 },
				);
			}
			return new Response("[]", { status: 200 });
		}) as unknown as typeof fetch;

		const ok = await studioStore
			.getState()
			.resolveActiveHold({ holdId: "hold-1", action: "APPROVE" }, mockFetch);
		expect(ok).toBe(true);

		const deadline = Date.now() + 3000;
		while (Date.now() < deadline && studioStore.getState().isRunningPipeline) {
			await new Promise((r) => setTimeout(r, 30));
		}
		expect(studioStore.getState().isRunningPipeline).toBe(false);
		expect(studioStore.getState().zoneSqlByZone.Z0).toContain("SELECT 1;");
		studioStore.getState().reset();
	});
});
