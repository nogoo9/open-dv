/**
 * M3 Iteration 2 Regression Tests (`apps/opendv-web-workbench/tests/m3-remediations.test.tsx`)
 *
 * Verifies:
 * 1. Header Cost Meter per-`job_id` deduplication (`aggregateSessionCostFromLogs`).
 * 2. `<AgentHarnessConfigCard />` validation diagnostics rendering, `#3d3d3a` contrast on
 *    selected provider cards, and shared `applyTierModelChange` / `applyPersonaModelChange`.
 * 3. Anchored `/stages/` regex in `url-router.ts` (`/dv-schema/stages/02` -> `dv_schema`).
 * 4. `SPIKE_MARK_SVG` `flex-shrink:0` and `ExplorerTopBar` responsive wrapper markup.
 */

import { describe, expect, it } from "bun:test";
import type { PlatformConfigValidationResponse } from "@opendv/protocol-ts";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ExplorerTopBar } from "../src/explorer/ExplorerTopBar";
import {
	OpenDVWorkbenchExplorer,
	aggregateSessionCostFromLogs,
} from "../src/explorer/OpenDVWorkbenchExplorer";
import { SPIKE_MARK_SVG } from "../src/explorer/design-tokens";
import { createExplorerStore } from "../src/explorer/explorer-store";
import { AgentHarnessConfigCard } from "../src/explorer/settings/AgentHarnessConfigCard";
import { AuthBudgetPolicySection } from "../src/explorer/settings/AuthBudgetPolicySection";
import {
	PlatformSettingsView,
	applyHydratedPlatformConfig,
} from "../src/explorer/settings/PlatformSettingsView";
import {
	applyPersonaModelChange,
	applyTierModelChange,
	createDefaultPlatformConfig,
} from "../src/explorer/settings/usePlatformConfigActions";
import { parseExplorerUrlState } from "../src/explorer/url-router";
import { GLOBAL_COMMERCE_EXPLORER_BUNDLE } from "./fixtures/commerce-explorer-fixture";

describe("M3 Iteration 2 Remediations", () => {
	describe("1. Header Cost Meter Per-Job Deduplication", () => {
		it("deduplicates cumulative agent_stream and agent_telemetry entries per job_id while summing across distinct jobs", () => {
			const result = aggregateSessionCostFromLogs([
				{
					job_id: "job-1",
					event: "agent_stream",
					token_usage: { total_tokens: 200, cost_usd: 0.005 },
					data: { cumulative_cost_usd: 0.005 },
				},
				{
					job_id: "job-1",
					event: "agent_stream",
					token_usage: { total_tokens: 750, cost_usd: 0.018 },
					data: { cumulative_cost_usd: 0.018 },
				},
				{
					job_id: "job-1",
					event: "agent_telemetry",
					token_usage: { total_tokens: 1000, cost_usd: 0.025 },
					data: { cumulative_cost_usd: 0.025 },
				},
				{
					job_id: "job-2",
					event: "agent_stream",
					token_usage: { total_tokens: 400 },
					data: { cumulative_cost_usd: 0.012 },
				},
				{
					job_id: "job-2",
					event: "agent_telemetry",
					token_usage: { total_tokens: 500, cost_usd: 0.015 },
					data: { cumulative_cost_usd: 0.015 },
				},
			]);

			expect(result.totalTokens).toBe(1500);
			expect(result.totalUsd).toBeCloseTo(0.04, 6);
		});

		it("counts entries lacking job_id individually without collapsing them into one bucket", () => {
			const result = aggregateSessionCostFromLogs([
				{
					event: "agent_telemetry",
					token_usage: { total_tokens: 120, cost_usd: 0.004 },
				},
				{
					event: "agent_telemetry",
					token_usage: { total_tokens: 80, cost_usd: 0.002 },
				},
			]);

			expect(result.totalTokens).toBe(200);
			expect(result.totalUsd).toBeCloseTo(0.006, 6);
		});
	});

	describe("2. AgentHarnessConfigCard Diagnostics, Contrast & Shared Updaters", () => {
		it("renders validationResult.diagnostics details in agent-harness-validation-banner", () => {
			const cfg = createDefaultPlatformConfig();
			const validation: PlatformConfigValidationResponse = {
				passed: false,
				diagnostics: [
					{
						code: "MISSING_API_KEY",
						severity: "ERROR",
						message: "GEMINI_API_KEY is not set in environment",
						target: "auth.api_key_env_var",
					},
				],
				resolved_persona_models: cfg.resolved_persona_models,
				providers: cfg.providers,
			};

			const html = renderToStaticMarkup(
				createElement(AgentHarnessConfigCard, {
					variant: "gate",
					initialConfig: cfg,
					initialValidationResult: validation,
				}),
			);

			expect(html).toContain('data-testid="agent-harness-validation-banner"');
			expect(html).toContain("MISSING_API_KEY");
			expect(html).toContain("ERROR");
			expect(html).toContain("GEMINI_API_KEY is not set in environment");
			expect(html).toContain("auth.api_key_env_var");
		});

		it("uses #3d3d3a (body) color on provider card status_message for WCAG AA >= 4.5:1 contrast against #e8e0d2", () => {
			const cfg = createDefaultPlatformConfig();
			const html = renderToStaticMarkup(
				createElement(AgentHarnessConfigCard, {
					variant: "settings",
					config: cfg,
				}),
			);

			expect(html).toContain("Google Antigravity SDK ready");
			const msgIdx = html.indexOf("Google Antigravity SDK ready");
			const snippet = html
				.slice(Math.max(0, msgIdx - 120), msgIdx)
				.toLowerCase();
			expect(
				snippet.includes("#3d3d3a") || snippet.includes("rgb(61, 61, 58)"),
			).toBe(true);
			expect(snippet).not.toContain("#6c6a64");
		});

		it("updates model tiers and persona overrides consistently via applyTierModelChange and applyPersonaModelChange", () => {
			const base = createDefaultPlatformConfig();
			const afterTier = applyTierModelChange(
				base,
				"reasoning",
				"gemini-4-pro-test",
			);
			expect(afterTier.harness.model_overrides?.reasoning).toBe(
				"gemini-4-pro-test",
			);
			expect(afterTier.resolved_persona_models["vault-first-mate"]).toBe(
				"gemini-4-pro-test",
			);
			expect(afterTier.resolved_persona_models["ship-vault"]).toBe(
				"gemini-4-pro-test",
			);

			const afterPersona = applyPersonaModelChange(
				afterTier,
				"ship-vault",
				"claude-opus-override",
			);
			expect(afterPersona.harness.agent_models?.["ship-vault"]).toBe(
				"claude-opus-override",
			);
			expect(afterPersona.resolved_persona_models["ship-vault"]).toBe(
				"claude-opus-override",
			);

			const afterClear = applyPersonaModelChange(
				afterPersona,
				"ship-vault",
				"   ",
			);
			expect(afterClear.harness.agent_models?.["ship-vault"]).toBeUndefined();
			expect(afterClear.resolved_persona_models["ship-vault"]).toBe(
				"gemini-4-pro-test",
			);
		});
	});

	describe("3. Anchored /stages/ Router & TopBar Responsive Markup", () => {
		it("does not misroute nested /dv-schema/stages/02 as stage view", () => {
			const nested = parseExplorerUrlState("/dv-schema/stages/02");
			expect(nested.view).toBe("dv_schema");

			const direct = parseExplorerUrlState("/stages/02");
			expect(direct.view).toBe("stage");
			expect(direct.stageIdx).toBe(2);
		});

		it("marks SPIKE_MARK_SVG flex-shrink:0 and renders claude-topbar-left / claude-topbar-run-controls / claude-topbar-right classes", () => {
			expect(SPIKE_MARK_SVG).toContain("flex-shrink:0");

			const html = renderToStaticMarkup(
				createElement(ExplorerTopBar, {
					bundle: GLOBAL_COMMERCE_EXPLORER_BUNDLE,
					view: "pipeline",
					stageShortCode: "STAGE_00",
					runState: "sealed",
					statusBannerText: "Sealed",
					statusDotColor: "#5db872",
					streamedTokens: 0,
					isRunSelectorOpen: false,
					isLoadingRuns: false,
					runsList: [],
					onToggleRunSelector: () => {},
					onSelectRun: () => {},
					onOpenNewRunModal: () => {},
					onGoPipeline: () => {},
				}),
			);

			expect(html).toContain("claude-topbar-left");
			expect(html).toContain("claude-topbar-run-controls");
			expect(html).toContain("claude-topbar-right");
			expect(html).toContain("flex-shrink:0");
		});

		it("separates 54px explorer-top-header from explorer-subbar (claude-subbar) when showRunControls is active (view=pipeline|stage)", () => {
			const pipelineHtml = renderToStaticMarkup(
				createElement(ExplorerTopBar, {
					bundle: GLOBAL_COMMERCE_EXPLORER_BUNDLE,
					view: "pipeline",
					stageShortCode: "STAGE_00",
					runState: "sealed",
					statusBannerText: "Sealed · 6/6 gates PASS",
					statusDotColor: "#5db872",
					streamedTokens: 0,
					isRunSelectorOpen: false,
					isLoadingRuns: false,
					runsList: [],
					onToggleRunSelector: () => {},
					onSelectRun: () => {},
					onOpenNewRunModal: () => {},
					onGoPipeline: () => {},
				}),
			);

			const headerCloseIdx = pipelineHtml.indexOf("</header>");
			const subbarIdx = pipelineHtml.indexOf('data-testid="explorer-subbar"');
			const runSelectorIdx = pipelineHtml.indexOf(
				'data-testid="btn-run-selector-pill"',
			);
			const runPipelineIdx = pipelineHtml.indexOf(
				'data-testid="btn-explorer-run-pipeline"',
			);

			expect(headerCloseIdx).toBeGreaterThan(0);
			expect(subbarIdx).toBeGreaterThan(headerCloseIdx);
			expect(runSelectorIdx).toBeGreaterThan(subbarIdx);
			expect(runPipelineIdx).toBeGreaterThan(subbarIdx);

			const headerSlice = pipelineHtml.slice(0, headerCloseIdx);
			expect(headerSlice).toContain('data-testid="explorer-top-header"');
			expect(headerSlice).toMatch(/height:\s*54px/i);
			expect(headerSlice).not.toContain('data-testid="btn-run-selector-pill"');
			expect(headerSlice).not.toContain(
				'data-testid="btn-explorer-run-pipeline"',
			);
			expect(headerSlice).not.toContain("flex-wrap:wrap");

			expect(pipelineHtml).toContain(
				"claude-subbar claude-topbar-run-controls",
			);

			const ontologyHtml = renderToStaticMarkup(
				createElement(ExplorerTopBar, {
					bundle: GLOBAL_COMMERCE_EXPLORER_BUNDLE,
					view: "ontology",
					stageShortCode: "STAGE_00",
					runState: "sealed",
					statusBannerText: "Sealed",
					statusDotColor: "#5db872",
					streamedTokens: 0,
					isRunSelectorOpen: false,
					isLoadingRuns: false,
					runsList: [],
					onToggleRunSelector: () => {},
					onSelectRun: () => {},
					onOpenNewRunModal: () => {},
					onGoPipeline: () => {},
				}),
			);
			expect(ontologyHtml).not.toContain('data-testid="explorer-subbar"');
		});
	});
});

function findElementByTestId(
	node: unknown,
	testId: string,
): { type: unknown; props: Record<string, unknown> } | null {
	if (!node || typeof node !== "object") return null;
	if (Array.isArray(node)) {
		for (const child of node) {
			const hit = findElementByTestId(child, testId);
			if (hit) return hit;
		}
		return null;
	}
	const el = node as { type?: unknown; props?: Record<string, unknown> };
	if (el.props?.["data-testid"] === testId) {
		return { type: el.type, props: el.props };
	}
	if (el.props?.children) {
		return findElementByTestId(el.props.children, testId);
	}
	return null;
}

describe("M3 Iteration 4 Remediations", () => {
	describe("1. AuthBudgetPolicySection HTML5 Step Validity", () => {
		it("uses step=1 for max_output_tokens (default 32000) and step=0.01 for max_cost_usd (default 5)", () => {
			const cfg = createDefaultPlatformConfig();
			expect(cfg.harness.budget.max_output_tokens).toBe(32000);
			expect(cfg.harness.budget.max_cost_usd).toBe(5);

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

			const maxOutputMatch = html.match(
				/<input[^>]*data-testid="settings-input-max-output-tokens"[^>]*\/>/,
			);
			expect(maxOutputMatch?.[0]).toContain('min="512"');
			expect(maxOutputMatch?.[0]).toContain('step="1"');
			expect(maxOutputMatch?.[0]).toContain('value="32000"');

			const maxCostMatch = html.match(
				/<input[^>]*data-testid="settings-input-max-cost-usd"[^>]*\/>/,
			);
			expect(maxCostMatch?.[0]).toContain('min="0.01"');
			expect(maxCostMatch?.[0]).toContain('step="0.01"');
			expect(maxCostMatch?.[0]).toContain('value="5"');

			// Verify PlatformSettingsView form inherits valid step attributes
			const settingsHtml = renderToStaticMarkup(
				createElement(PlatformSettingsView, { initialConfig: cfg }),
			);
			expect(settingsHtml).toContain('step="1"');
			expect(settingsHtml).toContain('step="0.01"');
		});
	});

	describe("2. Run Selector URL & Cross-Tab runId Sync", () => {
		it("updates store.runId, browser URL (?run=...), and DV Schema Explorer view when loadRun switches runs", async () => {
			const recordedUrls: string[] = [];
			const origHistory = globalThis.history;
			Object.defineProperty(globalThis, "history", {
				configurable: true,
				value: {
					replaceState: (
						_data: unknown,
						_unused: string,
						url?: string | URL | null,
					) => {
						if (typeof url === "string") {
							recordedUrls.push(url);
						}
					},
				},
			});
			try {
				const store = createExplorerStore({
					syncBrowserUrl: true,
					dataSource: {
						mode: "live",
						sourceName: "Mock API",
						fetchRunsList: async () => [],
						fetchExplorerBundle: async (runId: string) => ({
							...GLOBAL_COMMERCE_EXPLORER_BUNDLE,
							runId,
							displayName: `Run ${runId}`,
						}),
						fetchArtifactContent: async () => "",
						createPipelineRun: async () => ({
							run_id: "run-new",
							status: "RUNNING",
							created_at_utc: "2026-11-19T00:00:00Z",
							status_url: "/api/v1/runs/run-new",
							events_sse_url: "/api/v1/runs/run-new/events",
							event_stream_url: "/api/v1/runs/run-new/events",
							has_initial_contract: false,
							lifecycle_stage: "COLD_START_N1",
						}),
						validatePipeline: async () => ({
							stage: "INPUT_VALIDATION",
							passed: true,
							checked_items_count: 1,
							diagnostics: [],
						}),
						submitDecision: async () => ({
							decision_id: "dec-1",
							status: "ACCEPTED",
							resumed_run_id: "run-w2-002",
							next_stage_id: "STAGE_00",
							next_event_id: "evt-1",
						}),
						subscribeEvents: () => () => {},
					},
				});

				store
					.getState()
					.syncFromUrl("/explorer", "?run=run-w1-001&session=ses-w1-001");
				expect(store.getState().runId).toBe("run-w1-001");

				await store.getState().loadRun("run-w2-002");

				expect(store.getState().runId).toBe("run-w2-002");
				expect(store.getState().activeRunId).toBe("run-w2-002");
				expect(store.getState().bundle.runId).toBe("run-w2-002");
				expect(recordedUrls.at(-1)).toBe(
					"/explorer?run=run-w2-002&session=ses-w1-001",
				);

				store.getState().goDvSchema();
				expect(recordedUrls.at(-1)).toBe(
					"/dv-schema?run=run-w2-002&session=ses-w1-001",
				);

				const dvHtml = renderToStaticMarkup(
					createElement(OpenDVWorkbenchExplorer, { store }),
				);
				expect(dvHtml).toContain("dv-schema-explorer-view");
				expect(dvHtml).toContain("run-w2-002");
			} finally {
				Object.defineProperty(globalThis, "history", {
					configurable: true,
					value: origHistory,
				});
			}
		});
	});

	describe("3. Clickable breadcrumb-stage Navigation", () => {
		it("renders breadcrumb-stage as a clickable button invoking onGoPipeline", () => {
			let goPipelineCount = 0;
			const props = {
				bundle: GLOBAL_COMMERCE_EXPLORER_BUNDLE,
				view: "stage" as const,
				currentStage: GLOBAL_COMMERCE_EXPLORER_BUNDLE.stages[0],
				stageShortCode: "STAGE_00",
				runState: "sealed" as const,
				statusBannerText: "Sealed",
				statusDotColor: "#5db872",
				streamedTokens: 0,
				isRunSelectorOpen: false,
				isLoadingRuns: false,
				runsList: [],
				onToggleRunSelector: () => {},
				onSelectRun: () => {},
				onOpenNewRunModal: () => {},
				onGoPipeline: () => {
					goPipelineCount += 1;
				},
			};

			const html = renderToStaticMarkup(createElement(ExplorerTopBar, props));
			expect(html).toContain("<button");
			expect(html).toContain('data-testid="breadcrumb-stage"');
			expect(html).toContain("STAGE_00 · ");

			const tree = ExplorerTopBar(props);
			const btn = findElementByTestId(tree, "breadcrumb-stage");
			expect(btn).not.toBeNull();
			expect(btn?.type).toBe("button");
			expect(btn?.props.type).toBe("button");
			(btn?.props.onClick as () => void)();
			expect(goPipelineCount).toBe(1);
		});
	});

	describe("4. Slow-GET Dirty Guard & Hardened aggregateSessionCostFromLogs", () => {
		it("does not overwrite edited config when initial GET resolves after user edits", () => {
			const edited = createDefaultPlatformConfig();
			edited.harness.model_overrides = {
				reasoning: "user-typed-early",
				fast: "gemini-3.8-flash-high",
			};
			const serverLoaded = createDefaultPlatformConfig();
			serverLoaded.harness.model_overrides = {
				reasoning: "server-hydrated-reasoning",
				fast: "gemini-3.8-flash-high",
			};

			const kept = applyHydratedPlatformConfig(edited, serverLoaded, true);
			expect(kept.harness.model_overrides?.reasoning).toBe("user-typed-early");

			const hydrated = applyHydratedPlatformConfig(edited, serverLoaded, false);
			expect(hydrated.harness.model_overrides?.reasoning).toBe(
				"server-hydrated-reasoning",
			);
		});

		it("keeps terminal agent_telemetry authoritative over out-of-order agent_stream and falls back past 0/NaN cost_usd", () => {
			const outOfOrder = aggregateSessionCostFromLogs([
				{
					job_id: "job-order",
					event: "agent_telemetry",
					token_usage: { total_tokens: 500, cost_usd: 0.01 },
				},
				{
					job_id: "job-order",
					event: "agent_stream",
					token_usage: { total_tokens: 900, cost_usd: 0.09 },
				},
			]);
			expect(outOfOrder.totalTokens).toBe(500);
			expect(outOfOrder.totalUsd).toBeCloseTo(0.01, 6);

			const zeroAndNanFallback = aggregateSessionCostFromLogs([
				{
					job_id: "job-zero",
					event: "agent_stream",
					token_usage: { total_tokens: 400, cost_usd: 0 },
					data: { cumulative_cost_usd: 0.012 },
				},
				{
					job_id: "   ",
					event: "agent_telemetry",
					token_usage: { total_tokens: -50, cost_usd: Number.NaN },
					data: { job_id: "job-nan", cumulative_cost_usd: 0.007 },
				},
			]);
			expect(zeroAndNanFallback.totalTokens).toBe(400);
			expect(zeroAndNanFallback.totalUsd).toBeCloseTo(0.019, 6);
		});
	});
});
