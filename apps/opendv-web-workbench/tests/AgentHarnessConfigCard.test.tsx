/**
 * M3 Failing-First TDD Tests — Shared `<AgentHarnessConfigCard />`, Auth/Deterministic Cleanup & Verify-and-Save Readiness
 * (`apps/opendv-web-workbench/tests/AgentHarnessConfigCard.test.tsx`)
 *
 * Covers:
 * 1. Shared `<AgentHarnessConfigCard />` rendering in `"gate"` and `"settings"` variants (`P2`, `FR-AGT-02`).
 * 2. Zero legacy `X-OpenDV-Role` headers across config REST helpers (`FR-AUTH-01`).
 * 3. Zero `symbolic_completion` or deterministic mode toggles in UI/Settings (`D28`).
 * 4. Preservation of all 5 `HarnessBudget` controls in `AuthBudgetPolicySection` (`D21`).
 * 5. `verifyAndSaveHarnessConfig` + `isActiveProviderReady` readiness contract (`FR-AGT-02`).
 */

import { describe, expect, it } from "bun:test";
import type {
	PlatformConfigResponse,
	PlatformConfigValidationResponse,
} from "@opendv/protocol-ts";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { AgentHarnessConfigCard } from "../src/explorer/settings/AgentHarnessConfigCard";
import { AuthBudgetPolicySection } from "../src/explorer/settings/AuthBudgetPolicySection";
import { HarnessModelSettingsSection } from "../src/explorer/settings/HarnessModelSettingsSection";
import { PlatformSettingsView } from "../src/explorer/settings/PlatformSettingsView";
import {
	HARNESS_PROVIDERS,
	createDefaultPlatformConfig,
	fetchPlatformConfig,
	isActiveProviderReady,
	savePlatformConfig,
	validatePlatformConfig,
	verifyAndSaveHarnessConfig,
} from "../src/explorer/settings/usePlatformConfigActions";

function hasHeaderCaseInsensitive(
	headers: HeadersInit | undefined,
	targetName: string,
): boolean {
	if (!headers) return false;
	const needle = targetName.toLowerCase();
	if (headers instanceof Headers) {
		return headers.has(targetName);
	}
	if (Array.isArray(headers)) {
		return headers.some(([k]) => k.toLowerCase() === needle);
	}
	return Object.keys(headers).some((k) => k.toLowerCase() === needle);
}

describe("M3: Shared <AgentHarnessConfigCard /> & Settings Cleanup", () => {
	describe("1. Shared <AgentHarnessConfigCard /> Variants (P2, FR-AGT-02)", () => {
		it("renders 'gate' variant with provider readiness cards, auth mode, API key env var, tier overrides, persona routing, and Verify & Save button", () => {
			const cfg = createDefaultPlatformConfig();
			const html = renderToStaticMarkup(
				createElement(AgentHarnessConfigCard, {
					variant: "gate",
					initialConfig: cfg,
					showActionBar: true,
					showPersonaRouting: true,
				}),
			);

			expect(html).toContain('data-testid="agent-harness-config-card"');
			for (const provider of HARNESS_PROVIDERS) {
				expect(html).toContain(`data-testid="provider-readiness-${provider}"`);
			}
			expect(html).toContain('data-testid="settings-select-provider"');
			expect(html).toContain('data-testid="settings-select-auth-mode"');
			expect(html).toContain('data-testid="settings-input-api-key-env"');
			expect(html).toContain('data-testid="settings-input-reasoning-model"');
			expect(html).toContain('data-testid="settings-input-fast-model"');
			expect(html).toContain('data-testid="settings-input-persona-ship-vault"');
			expect(html).toContain('data-testid="btn-verify-save-agent-harness"');
		});

		it("renders 'settings' variant cleanly inside PlatformSettingsView", () => {
			const cfg = createDefaultPlatformConfig();
			const cardHtml = renderToStaticMarkup(
				createElement(AgentHarnessConfigCard, {
					variant: "settings",
					config: cfg,
					showActionBar: false,
					showPersonaRouting: true,
					onProviderChange: () => {},
					onTierModelChange: () => {},
					onPersonaModelChange: () => {},
					onAuthChange: () => {},
				}),
			);
			expect(cardHtml).toContain('data-testid="agent-harness-config-card"');
			expect(cardHtml).not.toContain(
				'data-testid="btn-verify-save-agent-harness"',
			);

			const settingsHtml = renderToStaticMarkup(
				createElement(PlatformSettingsView, {
					initialConfig: cfg,
				}),
			);
			expect(settingsHtml).toContain('data-testid="agent-harness-config-card"');
		});
	});

	describe("2. Zero Legacy X-OpenDV-Role Header (FR-AUTH-01)", () => {
		it("sends ZERO X-OpenDV-Role header in fetchPlatformConfig, savePlatformConfig, validatePlatformConfig, and verifyAndSaveHarnessConfig", async () => {
			const recordedHeaders: Array<{
				method: string;
				url: string;
				headers: HeadersInit | undefined;
			}> = [];
			const cfg = createDefaultPlatformConfig();

			const mockFetch = (async (
				input: RequestInfo | URL,
				init?: RequestInit,
			) => {
				const url = String(input);
				const method = init?.method ?? "GET";
				recordedHeaders.push({ method, url, headers: init?.headers });

				if (url.endsWith("/api/v1/platform/config/validate")) {
					const valResp: PlatformConfigValidationResponse = {
						passed: true,
						diagnostics: [],
						harness: cfg.harness,
						resolved_persona_models: cfg.resolved_persona_models,
						providers: cfg.providers,
					};
					return new Response(JSON.stringify(valResp), {
						status: 200,
						headers: { "Content-Type": "application/json" },
					});
				}
				return new Response(JSON.stringify(cfg), {
					status: 200,
					headers: { "Content-Type": "application/json" },
				});
			}) as unknown as typeof fetch;

			await fetchPlatformConfig({
				apiUrl: "http://platform.local",
				fetchImpl: mockFetch,
			});
			await validatePlatformConfig(cfg.harness, {
				apiUrl: "http://platform.local",
				fetchImpl: mockFetch,
			});
			await savePlatformConfig(
				{ harness: cfg.harness, persist: true },
				{ apiUrl: "http://platform.local", fetchImpl: mockFetch },
			);
			await verifyAndSaveHarnessConfig(cfg, {
				apiUrl: "http://platform.local",
				fetchImpl: mockFetch,
			});

			expect(recordedHeaders.length).toBeGreaterThanOrEqual(5);
			for (const entry of recordedHeaders) {
				expect(hasHeaderCaseInsensitive(entry.headers, "X-OpenDV-Role")).toBe(
					false,
				);
			}
		});
	});

	describe("3. Zero Deterministic / Symbolic Completion UI & Preserved Budgets (D28, D21)", () => {
		it("renders ZERO symbolic_completion or deterministic mode toggles across all settings components", () => {
			const cfg = createDefaultPlatformConfig();
			const surfaces = [
				renderToStaticMarkup(
					createElement(AgentHarnessConfigCard, {
						variant: "gate",
						initialConfig: cfg,
					}),
				),
				renderToStaticMarkup(
					createElement(HarnessModelSettingsSection, {
						harness: cfg.harness,
						providers: cfg.providers,
						resolvedPersonaModels: cfg.resolved_persona_models,
						onProviderChange: () => {},
						onTierModelChange: () => {},
						onPersonaModelChange: () => {},
					}),
				),
				renderToStaticMarkup(
					createElement(AuthBudgetPolicySection, {
						config: cfg,
						onAuthChange: () => {},
						onBudgetChange: () => {},
						onSandboxChange: () => {},
						onPolicyChange: () => {},
						onMaxInlineBytesChange: () => {},
					}),
				),
				renderToStaticMarkup(
					createElement(PlatformSettingsView, {
						initialConfig: cfg,
					}),
				),
			];

			for (const html of surfaces) {
				const lower = html.toLowerCase();
				expect(lower).not.toContain("symbolic_completion");
				expect(lower).not.toContain("deterministic");
			}
		});

		it("preserves all 5 HarnessBudget controls in AuthBudgetPolicySection", () => {
			const cfg = createDefaultPlatformConfig();
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

			expect(html).toContain('data-testid="settings-input-max-turns"');
			expect(html).toContain('data-testid="settings-input-max-input-tokens"');
			expect(html).toContain('data-testid="settings-input-max-output-tokens"');
			expect(html).toContain('data-testid="settings-input-timeout-seconds"');
			expect(html).toContain('data-testid="settings-input-max-cost-usd"');
		});
	});

	describe("4. Verify & Save Readiness Contract (isActiveProviderReady + verifyAndSaveHarnessConfig)", () => {
		it("validates then saves and invokes onVerifiedAndSaved(saved, true) when validation passes and active provider is available", async () => {
			const calls: string[] = [];
			const verifiedCallbacks: Array<{
				saved: PlatformConfigResponse;
				ready: boolean;
			}> = [];
			const readyCfg = createDefaultPlatformConfig();
			readyCfg.harness.provider = "claude-cli";
			readyCfg.providers = readyCfg.providers.map((p) =>
				p.provider === "claude-cli" ? { ...p, available: true } : p,
			);

			expect(isActiveProviderReady(readyCfg).ready).toBe(true);

			const mockFetch = (async (
				input: RequestInfo | URL,
				init?: RequestInit,
			) => {
				const url = String(input);
				const method = init?.method ?? "GET";
				calls.push(`${method} ${url}`);

				if (url.endsWith("/api/v1/platform/config/validate")) {
					return new Response(
						JSON.stringify({
							passed: true,
							diagnostics: [],
							harness: readyCfg.harness,
							resolved_persona_models: readyCfg.resolved_persona_models,
							providers: readyCfg.providers,
						} satisfies PlatformConfigValidationResponse),
						{ status: 200, headers: { "Content-Type": "application/json" } },
					);
				}

				if (url.endsWith("/api/v1/platform/config") && method === "PUT") {
					return new Response(JSON.stringify(readyCfg), {
						status: 200,
						headers: { "Content-Type": "application/json" },
					});
				}

				return new Response("Not found", { status: 404 });
			}) as unknown as typeof fetch;

			const result = await verifyAndSaveHarnessConfig(readyCfg, {
				apiUrl: "http://platform.local",
				fetchImpl: mockFetch,
				onVerifiedAndSaved: (saved, ready) => {
					verifiedCallbacks.push({ saved, ready });
				},
			});

			expect(calls).toEqual([
				"POST http://platform.local/api/v1/platform/config/validate",
				"PUT http://platform.local/api/v1/platform/config",
			]);
			expect(result.ok).toBe(true);
			expect(result.ready).toBe(true);
			expect(result.saved?.harness.provider).toBe("claude-cli");
			expect(verifiedCallbacks).toHaveLength(1);
			expect(verifiedCallbacks[0]?.ready).toBe(true);
			expect(verifiedCallbacks[0]?.saved.harness.provider).toBe("claude-cli");
		});

		it("returns ready === false and invokes onVerifiedAndSaved(saved, false) when active provider has available: false", async () => {
			const verifiedCallbacks: Array<{
				saved: PlatformConfigResponse;
				ready: boolean;
			}> = [];
			const unreadyCfg = createDefaultPlatformConfig();
			unreadyCfg.harness.provider = "claude-cli";
			unreadyCfg.providers = unreadyCfg.providers.map((p) =>
				p.provider === "claude-cli" ? { ...p, available: false } : p,
			);

			expect(isActiveProviderReady(unreadyCfg).ready).toBe(false);

			const mockFetch = (async (
				input: RequestInfo | URL,
				init?: RequestInit,
			) => {
				const url = String(input);
				if (url.endsWith("/api/v1/platform/config/validate")) {
					return new Response(
						JSON.stringify({
							passed: true,
							diagnostics: [],
							harness: unreadyCfg.harness,
							resolved_persona_models: unreadyCfg.resolved_persona_models,
							providers: unreadyCfg.providers,
						}),
						{ status: 200, headers: { "Content-Type": "application/json" } },
					);
				}
				return new Response(JSON.stringify(unreadyCfg), {
					status: 200,
					headers: { "Content-Type": "application/json" },
				});
			}) as unknown as typeof fetch;

			const result = await verifyAndSaveHarnessConfig(unreadyCfg, {
				apiUrl: "http://platform.local",
				fetchImpl: mockFetch,
				onVerifiedAndSaved: (saved, ready) => {
					verifiedCallbacks.push({ saved, ready });
				},
			});

			expect(result.ok).toBe(true);
			expect(result.ready).toBe(false);
			expect(verifiedCallbacks).toHaveLength(1);
			expect(verifiedCallbacks[0]?.ready).toBe(false);
		});
	});
});
