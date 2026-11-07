import { afterAll, beforeAll, describe, expect, it } from "bun:test";
import { existsSync, unlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
	buildConfigUpdateRequest,
	executeConfigCommand,
} from "../src/http-executor";
import { runCli } from "../src/main";
import { loadCliSpec } from "../src/spec";

describe("opendvctl config command group (show, set, validate)", () => {
	const spec = loadCliSpec();
	let server: ReturnType<typeof Bun.serve>;
	let serverUrl: string;
	let lastPutBody: Record<string, unknown> | null = null;
	let lastValidateBody: Record<string, unknown> | null = null;

	beforeAll(() => {
		server = Bun.serve({
			port: 0,
			async fetch(req) {
				const url = new URL(req.url);

				if (url.pathname === "/api/v1/health") {
					return Response.json({
						status: "HEALTHY",
						version: "0.1.0",
						contract_version: spec.contract_version,
						mode: "PLATFORM_API",
					});
				}

				if (
					url.pathname === "/api/v1/platform/config" &&
					req.method === "GET"
				) {
					return Response.json({
						workspace_root: "/workspace/open-dv",
						state_dir: "/workspace/open-dv/.opendv/state",
						cas_dir: "/workspace/open-dv/.opendv/state/cas",
						ledgers_dir: "/workspace/open-dv/.opendv/state/ledgers",
						server_host: "127.0.0.1",
						server_port: 8420,
						max_inline_bytes: 10485760,
						cors_origins: ["http://127.0.0.1:8420"],
						config_file_path: null,
						persisted_config_path:
							"/workspace/open-dv/.opendv/state/platform_config.json",
						web_ui_bundled: true,
						harness: {
							provider: "agy",
							model_overrides: {
								reasoning: "gemini-4-argon-high",
								fast: "gemini-3.8-flash-high",
							},
							agent_models: {},
							auth: {
								auth_mode: "cli_session",
								mode: "cli_session",
								api_key_env_var: null,
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
							"scout-unstructured": "gemini-3.8-flash-high",
							"ship-vault": "gemini-4-argon-high",
						},
						providers: [
							{
								provider: "agy",
								execution_mode: "cli",
								available: true,
								binary_name: "agy",
								binary_found: true,
								sdk_installed: false,
								auth_mode: "cli_session",
								api_key_env_var: "",
								credentials_present: true,
								default_reasoning_model: "gemini-4-argon-high",
								default_fast_model: "gemini-3.8-flash-high",
								status_message: "agy CLI ready",
							},
						],
					});
				}

				if (
					url.pathname === "/api/v1/platform/config" &&
					req.method === "PUT"
				) {
					const body = (await req.json()) as Record<string, unknown>;
					lastPutBody = body;
					const harness = (body.harness || {}) as Record<string, unknown>;
					const overrides = (harness.model_overrides || {}) as Record<
						string,
						string
					>;
					const reasoning = overrides.reasoning || "claude-opus-4-6";
					const fast = overrides.fast || "claude-haiku-4-5";

					return Response.json({
						workspace_root: "/workspace/open-dv",
						state_dir: "/workspace/open-dv/.opendv/state",
						cas_dir: "/workspace/open-dv/.opendv/state/cas",
						ledgers_dir: "/workspace/open-dv/.opendv/state/ledgers",
						server_host: "127.0.0.1",
						server_port: 8420,
						max_inline_bytes: 10485760,
						cors_origins: ["http://127.0.0.1:8420"],
						config_file_path: null,
						persisted_config_path:
							"/workspace/open-dv/.opendv/state/platform_config.json",
						web_ui_bundled: true,
						harness: {
							provider: harness.provider || "claude-cli",
							model_overrides: { reasoning, fast },
							agent_models: harness.agent_models || {},
							auth: harness.auth || {
								auth_mode: "cli_session",
								mode: "cli_session",
							},
							budget: harness.budget || {
								max_turns: 30,
								timeout_seconds: 300,
							},
							steward_hold_policy:
								harness.steward_hold_policy || "auto_approve_top_candidate",
							ontology_policy: harness.ontology_policy || "off",
							stage_output_policy: harness.stage_output_policy || "llm_only",
						},
						resolved_persona_models: {
							"vault-first-mate": reasoning,
							"scout-source": fast,
							"scout-unstructured": fast,
							"ship-vault": reasoning,
						},
						providers: [],
					});
				}

				if (
					url.pathname === "/api/v1/platform/config/validate" &&
					req.method === "POST"
				) {
					const body = (await req.json()) as Record<string, unknown>;
					lastValidateBody = body;
					const harness = (body.harness || {}) as Record<string, unknown>;
					const auth = (harness.auth || {}) as Record<string, unknown>;

					if (harness.provider === "agy" && auth.auth_mode === "aws_bedrock") {
						return Response.json({
							passed: false,
							diagnostics: [
								{
									code: "HARNESS_AUTH_INCOMPATIBLE",
									severity: "ERROR",
									target: "auth.auth_mode",
									message:
										"Provider 'agy' is incompatible with auth_mode 'aws_bedrock'.",
								},
							],
							harness: null,
							resolved_persona_models: {},
							providers: [],
						});
					}

					return Response.json({
						passed: true,
						diagnostics: [],
						harness: {
							provider: harness.provider || "agy",
							model_overrides: {
								reasoning: "gemini-4-argon-high",
								fast: "gemini-3.8-flash-high",
							},
						},
						resolved_persona_models: {
							"vault-first-mate": "gemini-4-argon-high",
							"scout-source": "gemini-3.8-flash-high",
							"scout-unstructured": "gemini-3.8-flash-high",
							"ship-vault": "gemini-4-argon-high",
						},
						providers: [],
					});
				}

				return new Response("Not found", { status: 404 });
			},
		});

		serverUrl = `http://127.0.0.1:${server.port}`;
	});

	afterAll(() => {
		server.stop();
	});

	it("executes 'opendvctl config show' and returns exit code 0 with platform config", async () => {
		const outLines: string[] = [];
		const code = await runCli(
			["config", "show", "--api-url", serverUrl, "--json"],
			(msg) => outLines.push(msg),
		);

		expect(code).toBe(0);
		const parsed = JSON.parse(outLines.join("\n"));
		expect(parsed.server_port).toBe(8420);
		expect(parsed.web_ui_bundled).toBe(true);
		expect(parsed.harness.provider).toBe("agy");
		expect(parsed.resolved_persona_models["ship-vault"]).toBe(
			"gemini-4-argon-high",
		);
	});

	it("executes 'opendvctl config set' with CLI flags and sends PUT /api/v1/platform/config", async () => {
		const outLines: string[] = [];
		lastPutBody = null;

		const code = await runCli(
			[
				"config",
				"set",
				"--api-url",
				serverUrl,
				"--provider",
				"claude-cli",
				"--reasoning-model",
				"claude-opus-4-6",
				"--fast-model",
				"claude-haiku-4-5",
				"--auth-mode",
				"cli_session",
				"--api-key-env-var",
				"ANTHROPIC_API_KEY",
				"--hold-policy",
				"pause",
				"--ontology-policy",
				"recommend",
				"--stage-output-policy",
				"symbolic_completion",
				"--max-turns",
				"45",
				"--timeout-seconds",
				"420",
				"--json",
			],
			(msg) => outLines.push(msg),
		);

		expect(code).toBe(0);
		expect(lastPutBody).not.toBeNull();
		const sentHarness = (lastPutBody?.harness || {}) as Record<string, unknown>;
		expect(sentHarness.provider).toBe("claude-cli");
		expect(sentHarness.steward_hold_policy).toBe("pause");
		expect(sentHarness.ontology_policy).toBe("recommend");
		expect(sentHarness.stage_output_policy).toBe("symbolic_completion");
		expect(
			(sentHarness.model_overrides as Record<string, unknown>).reasoning,
		).toBe("claude-opus-4-6");
		expect((sentHarness.model_overrides as Record<string, unknown>).fast).toBe(
			"claude-haiku-4-5",
		);
		expect((sentHarness.auth as Record<string, unknown>).auth_mode).toBe(
			"cli_session",
		);
		expect((sentHarness.auth as Record<string, unknown>).api_key_env_var).toBe(
			"ANTHROPIC_API_KEY",
		);
		expect((sentHarness.budget as Record<string, unknown>).max_turns).toBe(45);
		expect(
			(sentHarness.budget as Record<string, unknown>).timeout_seconds,
		).toBe(420);

		const parsed = JSON.parse(outLines.join("\n"));
		expect(parsed.harness.provider).toBe("claude-cli");
		expect(parsed.harness.steward_hold_policy).toBe("pause");
	});

	it("executes 'opendvctl config set --harness-config' loading YAML file and merging CLI overrides", async () => {
		const outLines: string[] = [];
		const tmpYaml = join(tmpdir(), `temp_harness_${Date.now()}.yaml`);
		await Bun.write(
			tmpYaml,
			[
				"provider: antigravity",
				"model_overrides:",
				"  reasoning: gemini-4-argon-high",
				"  fast: gemini-3.8-flash-high",
				"steward_hold_policy: auto_approve_top_candidate",
			].join("\n"),
		);

		try {
			const code = await runCli(
				[
					"config",
					"set",
					"--api-url",
					serverUrl,
					"--harness-config",
					tmpYaml,
					"--hold-policy",
					"reject_on_hold",
					"--json",
				],
				(msg) => outLines.push(msg),
			);

			expect(code).toBe(0);
			const sentHarness = (lastPutBody?.harness || {}) as Record<
				string,
				unknown
			>;
			expect(sentHarness.provider).toBe("antigravity");
			expect(sentHarness.steward_hold_policy).toBe("reject_on_hold");
		} finally {
			if (existsSync(tmpYaml)) {
				unlinkSync(tmpYaml);
			}
		}
	});

	it("executes 'opendvctl config validate' returning exit code 0 on pass and 1 on fail", async () => {
		const passLines: string[] = [];
		const passCode = await runCli(
			[
				"config",
				"validate",
				"--api-url",
				serverUrl,
				"--provider",
				"claude-cli",
				"--json",
			],
			(msg) => passLines.push(msg),
		);
		expect(passCode).toBe(0);
		const passJson = JSON.parse(passLines.join("\n"));
		expect(passJson.passed).toBe(true);
		expect(
			(lastValidateBody?.harness as Record<string, unknown>).provider,
		).toBe("claude-cli");

		// Test failing validation via YAML file with incompatible auth_mode
		const badYaml = join(tmpdir(), `temp_bad_harness_${Date.now()}.yaml`);
		await Bun.write(
			badYaml,
			["provider: agy", "auth:", "  auth_mode: aws_bedrock"].join("\n"),
		);

		try {
			const failLines: string[] = [];
			const failCode = await runCli(
				[
					"config",
					"validate",
					"--api-url",
					serverUrl,
					"--harness-config",
					badYaml,
					"--json",
				],
				(msg) => failLines.push(msg),
			);
			expect(failCode).toBe(1);
			const failJson = JSON.parse(failLines.join("\n"));
			expect(failJson.passed).toBe(false);
			expect(failJson.diagnostics[0].code).toBe("HARNESS_AUTH_INCOMPATIBLE");
		} finally {
			if (existsSync(badYaml)) {
				unlinkSync(badYaml);
			}
		}
	});

	it("supports direct executeConfigCommand tuple [exitCode, data] invocation and persist=false option", async () => {
		const [showCode, showData] = await executeConfigCommand(
			serverUrl,
			"show",
			{},
			{},
		);
		expect(showCode).toBe(0);
		expect(showData.server_port).toBe(8420);

		const updateNoPersist = await buildConfigUpdateRequest({
			provider: "agy",
			persist: false,
		});
		expect(updateNoPersist.persist).toBe(false);
	});
});
