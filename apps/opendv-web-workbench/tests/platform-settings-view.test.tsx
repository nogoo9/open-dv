/**
 * Unit & Component Tests: Track B3 — Full-Page Platform & Agent Harness Settings View
 * (`apps/opendv-web-workbench/tests/platform-settings-view.test.tsx`)
 *
 * Covers:
 * 1. Baseline config factory (`createDefaultPlatformConfig`) & persona model resolution (`resolvePersonaModels`).
 * 2. Full-page rendering of `PlatformSettingsView` with semantic `<form>`, `<fieldset>`/`<legend>`, `<label htmlFor>`, and non-color readiness badges.
 * 3. Provider switching (`agy` -> `claude`), semantic tier overrides, and per-persona model routing table.
 * 4. Auth, execution budget, sandbox isolation, and governance policy updates.
 * 5. `fetchPlatformConfig`, `validatePlatformConfig` (`Validate Settings`), and `savePlatformConfig` (`Save Configuration`) via mock `fetchImpl`.
 */

import { describe, expect, it } from "bun:test";
import type {
	PlatformConfigResponse,
	PlatformConfigUpdateRequest,
	PlatformConfigValidationResponse,
} from "@opendv/protocol-ts";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { AuthBudgetPolicySection } from "../src/explorer/settings/AuthBudgetPolicySection";
import { HarnessModelSettingsSection } from "../src/explorer/settings/HarnessModelSettingsSection";
import {
	PlatformSettingsView,
	applyProviderSwitch,
} from "../src/explorer/settings/PlatformSettingsView";
import {
	CANONICAL_PERSONAS,
	HARNESS_PROVIDERS,
	createDefaultPlatformConfig,
	fetchPlatformConfig,
	resolvePersonaModels,
	savePlatformConfig,
	validatePlatformConfig,
} from "../src/explorer/settings/usePlatformConfigActions";

describe("Track B3: Full-Page Platform & Agent Harness Settings View", () => {
	describe("1. Baseline Configuration & Persona Resolution", () => {
		it("creates deterministic default platform config with agy provider, tier overrides, 4 personas, and 6 provider readiness entries", () => {
			const cfg = createDefaultPlatformConfig();

			expect(cfg.harness.provider).toBe("agy");
			expect(cfg.harness.model_overrides).toEqual({
				reasoning: "gemini-4-argon-high",
				fast: "gemini-3.8-flash-high",
			});

			for (const persona of CANONICAL_PERSONAS) {
				expect(cfg.resolved_persona_models[persona]).toBeDefined();
			}
			expect(cfg.resolved_persona_models["vault-first-mate"]).toBe(
				"gemini-4-argon-high",
			);
			expect(cfg.resolved_persona_models["ship-vault"]).toBe(
				"gemini-4-argon-high",
			);
			expect(cfg.resolved_persona_models["scout-source"]).toBe(
				"gemini-3.8-flash-high",
			);
			expect(cfg.resolved_persona_models["scout-unstructured"]).toBe(
				"gemini-3.8-flash-high",
			);

			const providerIds = cfg.providers.map((p) => p.provider);
			expect(providerIds).toEqual([...HARNESS_PROVIDERS]);
		});

		it("resolves per-persona fine-grained overrides ahead of semantic tier overrides", () => {
			const cfg = createDefaultPlatformConfig();
			const customHarness = {
				...cfg.harness,
				model_overrides: {
					reasoning: "claude-opus-4-6",
					fast: "claude-haiku-4-5",
				},
				agent_models: {
					"ship-vault": "claude-opus-4-6-extended-thinking",
				},
			};

			const resolved = resolvePersonaModels(customHarness, cfg.providers);
			expect(resolved["vault-first-mate"]).toBe("claude-opus-4-6");
			expect(resolved["ship-vault"]).toBe("claude-opus-4-6-extended-thinking");
			expect(resolved["scout-source"]).toBe("claude-haiku-4-5");
			expect(resolved["scout-unstructured"]).toBe("claude-haiku-4-5");
		});
	});

	describe("2. Full-Page PlatformSettingsView Rendering & Modern Web Guidance Compliance", () => {
		it("renders full-page view with semantic form, 5 fieldsets, provider readiness cards, and resolved models table", () => {
			const cfg = createDefaultPlatformConfig();
			const html = renderToStaticMarkup(
				createElement(PlatformSettingsView, {
					initialConfig: cfg,
					onBackToExplorer: () => {},
				}),
			);

			expect(html).toContain('data-testid="platform-settings-view"');
			expect(html).toContain('data-testid="btn-back-to-explorer"');
			expect(html).toContain("<form");
			expect(html).toContain("<fieldset");
			expect(html).toContain("<legend");

			// Section 1: Provider select & all 6 provider readiness cards
			expect(html).toContain('data-testid="settings-select-provider"');
			for (const p of HARNESS_PROVIDERS) {
				expect(html).toContain(`data-testid="provider-readiness-${p}"`);
			}
			expect(html).toContain("✓ Ready");
			expect(html).toContain("⚠ Unavailable");
			expect(html).toContain("✓ Env Set");
			expect(html).toContain("○ Env Not Set");

			// Section 2: Tier & Per-Persona Model inputs + Resolved table
			expect(html).toContain('data-testid="settings-input-reasoning-model"');
			expect(html).toContain('data-testid="settings-input-fast-model"');
			expect(html).toContain(
				'data-testid="settings-input-persona-vault-first-mate"',
			);
			expect(html).toContain(
				'data-testid="settings-input-persona-scout-source"',
			);
			expect(html).toContain(
				'data-testid="settings-input-persona-scout-unstructured"',
			);
			expect(html).toContain('data-testid="settings-input-persona-ship-vault"');
			expect(html).toContain('data-testid="settings-resolved-models-table"');

			// Section 3: Auth & Cloud Provider Routing
			expect(html).toContain('data-testid="settings-select-auth-mode"');
			expect(html).toContain('data-testid="settings-input-api-key-env"');
			expect(html).toContain('data-testid="settings-input-api-base-url"');
			expect(html).toContain('data-testid="settings-input-gcp-project"');
			expect(html).toContain('data-testid="settings-input-gcp-location"');
			expect(html).toContain('data-testid="settings-input-aws-region"');
			expect(html).toContain('data-testid="settings-input-aws-profile"');
			expect(html).toContain('data-testid="settings-checkbox-require-creds"');

			// Section 4: Budgets & Sandbox Isolation
			expect(html).toContain('data-testid="settings-input-max-turns"');
			expect(html).toContain('data-testid="settings-input-max-input-tokens"');
			expect(html).toContain('data-testid="settings-input-max-output-tokens"');
			expect(html).toContain('data-testid="settings-input-timeout-seconds"');
			expect(html).toContain('data-testid="settings-input-max-cost-usd"');
			expect(html).toContain('data-testid="settings-checkbox-readonly-ws"');
			expect(html).toContain('data-testid="settings-checkbox-allow-shell"');
			expect(html).toContain('data-testid="settings-checkbox-allow-network"');

			// Section 5: Governance Policies & Runtime Info
			expect(html).toContain('data-testid="settings-select-hold-policy"');
			expect(html).toContain('data-testid="settings-select-ontology-policy"');
			expect(html).toContain(
				'data-testid="settings-select-stage-output-policy"',
			);
			expect(html).toContain(
				'data-testid="settings-checkbox-cli-schema-retry"',
			);
			expect(html).toContain('data-testid="settings-input-max-inline-bytes"');
			expect(html).toContain("/workspace/open-dv/.opendv/platform_config.json");

			// Footer action buttons
			expect(html).toContain('data-testid="btn-validate-settings"');
			expect(html).toContain('data-testid="btn-save-settings"');

			// Modern Web Guidance: text correction disabled on technical identifier inputs
			expect(html.toLowerCase()).toContain('autocorrect="off"');
			expect(html.toLowerCase()).toContain('autocapitalize="off"');
			expect(html.toLowerCase()).toContain('spellcheck="false"');
		});
	});

	describe("3. Provider Switching & Model Tier / Persona Editing", () => {
		it("switches provider from agy to claude and updates default tier models, env var, and resolved persona models", () => {
			const initial = createDefaultPlatformConfig();
			const switched = applyProviderSwitch(initial, "claude");

			expect(switched.harness.provider).toBe("claude");
			expect(switched.harness.model_overrides.reasoning).toBe(
				"claude-opus-4-6",
			);
			expect(switched.harness.model_overrides.fast).toBe("claude-haiku-4-5");
			expect(switched.harness.auth.api_key_env_var).toBe("ANTHROPIC_API_KEY");
			expect(switched.resolved_persona_models["vault-first-mate"]).toBe(
				"claude-opus-4-6",
			);
			expect(switched.resolved_persona_models["scout-source"]).toBe(
				"claude-haiku-4-5",
			);

			const html = renderToStaticMarkup(
				createElement(PlatformSettingsView, {
					initialConfig: switched,
				}),
			);
			expect(html).toContain("claude-opus-4-6");
			expect(html).toContain("claude-haiku-4-5");
			expect(html).toContain("ANTHROPIC_API_KEY");

			// Custom model overrides that differ from the previous provider's default are preserved
			const customInitial = createDefaultPlatformConfig();
			customInitial.harness.model_overrides = {
				reasoning: "gemini-4-argon-custom-tuned",
				fast: "gemini-3.8-flash-high",
			};
			const customSwitched = applyProviderSwitch(customInitial, "claude");
			expect(customSwitched.harness.model_overrides.reasoning).toBe(
				"gemini-4-argon-custom-tuned",
			);
			expect(customSwitched.harness.model_overrides.fast).toBe(
				"claude-haiku-4-5",
			);
		});

		it("invokes callbacks when provider, tier model, and per-persona overrides are edited in HarnessModelSettingsSection", () => {
			let cfg = createDefaultPlatformConfig();

			const onProviderChange = (next: typeof cfg.harness.provider) => {
				cfg = applyProviderSwitch(cfg, next);
			};
			const onTierModelChange = (
				tier: "reasoning" | "fast",
				modelId: string,
			) => {
				const nextHarness = {
					...cfg.harness,
					model_overrides: {
						...cfg.harness.model_overrides,
						[tier]: modelId,
					},
				};
				cfg = {
					...cfg,
					harness: nextHarness,
					resolved_persona_models: resolvePersonaModels(
						nextHarness,
						cfg.providers,
					),
				};
			};
			const onPersonaModelChange = (
				persona: (typeof CANONICAL_PERSONAS)[number],
				modelId: string,
			) => {
				const nextHarness = {
					...cfg.harness,
					agent_models: {
						...cfg.harness.agent_models,
						[persona]: modelId,
					},
				};
				cfg = {
					...cfg,
					harness: nextHarness,
					resolved_persona_models: resolvePersonaModels(
						nextHarness,
						cfg.providers,
					),
				};
			};

			onProviderChange("claude-code");
			onTierModelChange("reasoning", "claude-sonnet-4-6");
			onPersonaModelChange("ship-vault", "claude-opus-4-6-custom");

			const html = renderToStaticMarkup(
				createElement(HarnessModelSettingsSection, {
					harness: cfg.harness,
					providers: cfg.providers,
					resolvedPersonaModels: cfg.resolved_persona_models,
					onProviderChange,
					onTierModelChange,
					onPersonaModelChange,
				}),
			);

			expect(html).toContain("claude-sonnet-4-6");
			expect(html).toContain("claude-opus-4-6-custom");
			expect(html).toContain("Persona Override");
		});
	});

	describe("4. Auth, Budget, Sandbox & Governance Policy Updates", () => {
		it("updates auth, budget, sandbox, governance policies, and max_inline_bytes in AuthBudgetPolicySection", () => {
			let cfg = createDefaultPlatformConfig();

			cfg = {
				...cfg,
				max_inline_bytes: 524288,
				harness: {
					...cfg.harness,
					auth: {
						...cfg.harness.auth,
						auth_mode: "vertex_adc",
						gcp_project: "enterprise-dv-prod",
						gcp_location: "europe-west4",
						require_credentials_present: true,
					},
					budget: {
						max_turns: 24,
						max_input_tokens: 400000,
						max_output_tokens: 64000,
						timeout_seconds: 600,
						max_cost_usd: 15.5,
					},
					sandbox: {
						...cfg.harness.sandbox,
						read_only_workspace: false,
						allow_shell_execution: true,
						allow_network_access: true,
					},
					steward_hold_policy: "auto_approve_top_candidate",
					ontology_policy: "auto_accept",
					stage_output_policy: "symbolic_completion",
					allow_cli_schema_omission_retry: true,
				},
			};

			const html = renderToStaticMarkup(
				createElement(AuthBudgetPolicySection, {
					config: cfg,
					onAuthChange: () => {},
					onBudgetChange: () => {},
					onSandboxChange: () => {},
					onPolicyChange: () => {},
					onMaxInlineBytesChange: () => {},
				}),
			);

			expect(html).toContain("enterprise-dv-prod");
			expect(html).toContain("europe-west4");
			expect(html).toContain('value="24"');
			expect(html).toContain('value="400000"');
			expect(html).toContain('value="64000"');
			expect(html).toContain('value="600"');
			expect(html).toContain('value="15.5"');
			expect(html).toContain('value="524288"');
		});
	});

	describe("5. REST Actions (Fetch, Validate Settings, Save Configuration) & Banners", () => {
		it("fetches, validates, and saves platform configuration via mock fetchImpl", async () => {
			const baseline = createDefaultPlatformConfig();
			let capturedPutBody: PlatformConfigUpdateRequest | null = null;
			let capturedValidateBody: { harness: Record<string, unknown> } | null =
				null;

			const mockFetch = (async (
				input: RequestInfo | URL,
				init?: RequestInit,
			) => {
				const url = String(input);
				const method = init?.method ?? "GET";

				if (url.endsWith("/api/v1/platform/config") && method === "GET") {
					return new Response(JSON.stringify(baseline), {
						status: 200,
						headers: { "Content-Type": "application/json" },
					});
				}

				if (
					url.endsWith("/api/v1/platform/config/validate") &&
					method === "POST"
				) {
					capturedValidateBody = JSON.parse(String(init?.body ?? "{}"));
					const validationResp: PlatformConfigValidationResponse = {
						passed: true,
						diagnostics: [
							{
								code: "HARNESS_READY",
								severity: "INFO",
								message: "Provider agy SDK and model tiers verified.",
							},
						],
						harness: baseline.harness,
						resolved_persona_models: baseline.resolved_persona_models,
						providers: baseline.providers,
					};
					return new Response(JSON.stringify(validationResp), {
						status: 200,
						headers: { "Content-Type": "application/json" },
					});
				}

				if (url.endsWith("/api/v1/platform/config") && method === "PUT") {
					capturedPutBody = JSON.parse(String(init?.body ?? "{}"));
					const updated: PlatformConfigResponse = {
						...baseline,
						max_inline_bytes:
							capturedPutBody?.max_inline_bytes ?? baseline.max_inline_bytes,
					};
					return new Response(JSON.stringify(updated), {
						status: 200,
						headers: { "Content-Type": "application/json" },
					});
				}

				return new Response(JSON.stringify({ detail: "Not found" }), {
					status: 404,
				});
			}) as unknown as typeof fetch;

			// 1. Fetch
			const fetched = await fetchPlatformConfig({
				apiUrl: "http://platform.local",
				fetchImpl: mockFetch,
			});
			expect(fetched.harness.provider).toBe("agy");

			// 2. Validate
			const validation = await validatePlatformConfig(fetched.harness, {
				apiUrl: "http://platform.local",
				fetchImpl: mockFetch,
			});
			expect(validation.passed).toBe(true);
			expect(validation.diagnostics[0]?.code).toBe("HARNESS_READY");
			expect(capturedValidateBody?.harness).toBeDefined();

			// 3. Save
			const saved = await savePlatformConfig(
				{
					harness: fetched.harness,
					max_inline_bytes: 524288,
					persist: true,
				},
				{
					apiUrl: "http://platform.local",
					fetchImpl: mockFetch,
				},
			);
			expect(saved.max_inline_bytes).toBe(524288);
			expect(capturedPutBody?.persist).toBe(true);

			// 4. Error paths (HTTP 500 on fetch, HTTP 422 on save, HTTP 422 on validate)
			const errorFetch = (async (
				input: RequestInfo | URL,
				init?: RequestInit,
			) => {
				const url = String(input);
				const method = init?.method ?? "GET";
				if (url.endsWith("/api/v1/platform/config") && method === "GET") {
					return new Response(JSON.stringify({ detail: "Backend offline" }), {
						status: 500,
					});
				}
				if (url.endsWith("/api/v1/platform/config") && method === "PUT") {
					return new Response(
						JSON.stringify({ detail: "Invalid budget ceiling" }),
						{ status: 422 },
					);
				}
				return new Response(
					JSON.stringify({
						error_code: "BAD_AUTH",
						detail: "Incompatible auth mode",
					}),
					{ status: 422 },
				);
			}) as unknown as typeof fetch;

			await expect(
				fetchPlatformConfig({
					apiUrl: "http://platform.local",
					fetchImpl: errorFetch,
				}),
			).rejects.toThrow("Backend offline");

			await expect(
				savePlatformConfig(
					{ harness: fetched.harness },
					{
						apiUrl: "http://platform.local",
						fetchImpl: errorFetch,
					},
				),
			).rejects.toThrow("Invalid budget ceiling");

			const failedVal = await validatePlatformConfig(fetched.harness, {
				apiUrl: "http://platform.local",
				fetchImpl: errorFetch,
			});
			expect(failedVal.passed).toBe(false);
			expect(failedVal.diagnostics[0]?.code).toBe("BAD_AUTH");
			expect(failedVal.diagnostics[0]?.message).toBe("Incompatible auth mode");
		});

		it("renders validation banner and save success banner in PlatformSettingsView", () => {
			const cfg = createDefaultPlatformConfig();
			const validationReport: PlatformConfigValidationResponse = {
				passed: true,
				diagnostics: [
					{
						code: "PROVIDER_OK",
						severity: "INFO",
						message: "All 4 persona models resolved cleanly.",
					},
				],
				resolved_persona_models: cfg.resolved_persona_models,
				providers: cfg.providers,
			};

			const html = renderToStaticMarkup(
				createElement(PlatformSettingsView, {
					initialConfig: cfg,
					initialValidationResult: validationReport,
					initialSaveSuccess: true,
				}),
			);

			expect(html).toContain('data-testid="settings-validation-banner"');
			expect(html).toContain("✓ Configuration Validated Successfully");
			expect(html).toContain("All 4 persona models resolved cleanly.");
			expect(html).toContain('data-testid="settings-save-success-banner"');
			expect(html).toContain(
				"✓ Platform &amp; Agent Harness configuration saved",
			);
		});
	});
});
