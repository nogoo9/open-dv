import { describe, expect, it } from "bun:test";
import {
	OpenDVPlatformClient,
	type PlatformConfigResponse,
	type PlatformConfigValidationResponse,
} from "../src/index";

describe("OpenDVPlatformClient platform config methods", () => {
	const mockConfigResponse: PlatformConfigResponse = {
		workspace_root: "/repo",
		state_dir: "/repo/.opendv/state",
		cas_dir: "/repo/.opendv/state/cas",
		ledgers_dir: "/repo/.opendv/state/ledgers",
		server_host: "127.0.0.1",
		server_port: 8420,
		max_inline_bytes: 10485760,
		cors_origins: ["http://127.0.0.1:8420"],
		config_file_path: "opendv.config.yaml",
		persisted_config_path: "/repo/.opendv/state/platform_config.json",
		web_ui_bundled: true,
		harness: {
			provider: "agy",
			model_overrides: {
				reasoning: "gemini-4-argon-high",
				fast: "gemini-3.8-flash-high",
			},
			agent_models: {},
			auth: {
				auth_mode: "none",
				require_credentials_present: false,
			},
			budget: {
				max_turns: 30,
				max_input_tokens: 200000,
				max_output_tokens: 32000,
				timeout_seconds: 300,
				max_cost_usd: 5.0,
			},
			sandbox: {
				read_only_workspace: false,
				allow_shell_execution: false,
				allow_network_access: false,
				allowed_builtin_tools: [],
				denied_builtin_tools: ["run_command"],
			},
			steward_hold_policy: "auto_approve_top_candidate",
			ontology_policy: "off",
			stage_output_policy: "llm_only",
			allow_cli_schema_omission_retry: false,
		},
		resolved_persona_models: {
			"vault-first-mate": "gemini-4-argon-high",
			"scout-source": "gemini-3.8-flash-high",
		},
		providers: [
			{
				provider: "agy",
				execution_mode: "cli",
				available: true,
				binary_name: "agy",
				binary_found: true,
				sdk_installed: false,
				auth_mode: "none",
				api_key_env_var: "GEMINI_API_KEY",
				credentials_present: true,
				default_reasoning_model: "gemini-4-argon-high",
				default_fast_model: "gemini-3.8-flash-high",
				status_message: "Ready",
			},
		],
	};

	it("fetches, updates, and validates platform config via REST endpoints", async () => {
		const calls: Array<{
			url: string;
			method: string;
			body?: string | undefined;
		}> = [];
		const mockFetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
			const url = String(input);
			const method = init?.method ?? "GET";
			calls.push({ url, method, body: init?.body as string | undefined });

			if (url.endsWith("/api/v1/platform/config") && method === "GET") {
				return new Response(JSON.stringify(mockConfigResponse), {
					status: 200,
				});
			}
			if (url.endsWith("/api/v1/platform/config") && method === "PUT") {
				const updated = {
					...mockConfigResponse,
					harness: {
						...mockConfigResponse.harness,
						provider: "claude-cli",
					},
				};
				return new Response(JSON.stringify(updated), { status: 200 });
			}
			if (
				url.endsWith("/api/v1/platform/config/validate") &&
				method === "POST"
			) {
				const valResp: PlatformConfigValidationResponse = {
					passed: true,
					diagnostics: [],
					harness: mockConfigResponse.harness,
					resolved_persona_models: mockConfigResponse.resolved_persona_models,
					providers: mockConfigResponse.providers,
				};
				return new Response(JSON.stringify(valResp), { status: 200 });
			}
			return new Response("Not Found", { status: 404 });
		}) as typeof fetch;

		const client = new OpenDVPlatformClient({
			baseUrl: "http://127.0.0.1:8420",
			fetchImpl: mockFetch,
		});

		const cfg = await client.getPlatformConfig();
		expect(cfg.web_ui_bundled).toBe(true);
		expect(cfg.harness.provider).toBe("agy");

		const updated = await client.updatePlatformConfig({
			harness: { provider: "claude-cli" },
			persist: true,
		});
		expect(updated.harness.provider).toBe("claude-cli");

		const val = await client.validatePlatformConfig({
			harness: { provider: "agy" },
		});
		expect(val.passed).toBe(true);
		expect(calls.length).toBe(3);
	});

	it("handles HTTP error responses for get, update, and validate", async () => {
		const errorFetch = (async (
			input: RequestInfo | URL,
			init?: RequestInit,
		) => {
			const url = String(input);
			const method = init?.method ?? "GET";

			if (url.endsWith("/api/v1/platform/config") && method === "GET") {
				return new Response("Internal Server Error", { status: 500 });
			}
			if (url.endsWith("/api/v1/platform/config") && method === "PUT") {
				return new Response(
					JSON.stringify({ detail: "Invalid provider name" }),
					{ status: 422 },
				);
			}
			if (
				url.endsWith("/api/v1/platform/config/validate") &&
				method === "POST"
			) {
				return new Response(
					JSON.stringify({
						error_code: "INVALID_HARNESS_CONFIG",
						detail: "Unknown provider",
					}),
					{ status: 422 },
				);
			}
			return new Response("Not Found", { status: 404 });
		}) as typeof fetch;

		const client = new OpenDVPlatformClient({
			baseUrl: "http://127.0.0.1:8420",
			fetchImpl: errorFetch,
		});

		await expect(client.getPlatformConfig()).rejects.toThrow(
			"Failed to fetch platform config: HTTP 500",
		);
		await expect(
			client.updatePlatformConfig({ harness: { provider: "bad" } }),
		).rejects.toThrow("Invalid provider name");

		const valResult = await client.validatePlatformConfig({
			harness: { provider: "bad" },
		});
		expect(valResult.passed).toBe(false);
		expect(valResult.diagnostics[0]?.code).toBe("INVALID_HARNESS_CONFIG");
		expect(valResult.diagnostics[0]?.message).toBe("Unknown provider");
	});
});
