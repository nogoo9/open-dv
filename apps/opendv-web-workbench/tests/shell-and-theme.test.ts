import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import {
	PE_DEMO_EVENTS,
	PE_DEMO_EVENTS_JSONL,
	PE_DEMO_RUN_ID,
	type VaultObservabilityEventDTO,
} from "@opendv/protocol-ts";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
	CUSTOM_ESG_GATE_ID,
	CustomEsgGateCard,
	DEFAULT_BACKGROUND_WORKSPACE_ID,
	ENTITY_VISUAL_GRAMMAR,
	HIGH_CONTRAST_THEME,
	Layer1StoryBanner,
	Layer3ProofDrawer,
	OBSIDIAN_THEME,
	PAPER_THEME,
	PlaybackControls,
	REQUIRED_DV_THEME_TOKENS,
	SHORTCUT_KEY_TO_VIEW_ID,
	SidebarNav,
	THEME_STORAGE_KEY,
	ThemeRegistry,
	ThemeToggle,
	VERDICT_VISUAL_GRAMMAR,
	type ViewPluginProps,
	WorkbenchPluginRegistry,
	WorkbenchShell,
	createWorkbenchStore,
	registerCustomEsgGatePlugin,
	routeWorkbenchKeyboardShortcut,
	sanitizeLayer1Text,
	syncBackgroundInertAttribute,
	useWorkbenchStore,
} from "../src/index";
import { viteConfig } from "../vite.config";

const WORKBENCH_ROOT = resolve(import.meta.dir, "..");

function collectSourceFiles(dir: string): string[] {
	const results: string[] = [];
	for (const entry of readdirSync(dir)) {
		const fullPath = join(dir, entry);
		const st = statSync(fullPath);
		if (st.isDirectory()) {
			results.push(...collectSourceFiles(fullPath));
		} else if (fullPath.endsWith(".ts") || fullPath.endsWith(".tsx")) {
			results.push(fullPath);
		}
	}
	return results;
}

describe("WP-10: Decoupled Web Workbench Shell, Semantic Theme Engine & Plugin Registry", () => {
	beforeEach(() => {
		ThemeRegistry.resetForTesting();
		WorkbenchPluginRegistry.clearForTesting();
	});

	afterEach(() => {
		ThemeRegistry.resetForTesting();
		WorkbenchPluginRegistry.clearForTesting();
	});

	describe("1. Strict UI-to-Backend Decoupling & Minimal Dependency Footprint", () => {
		it("enforces only 3 external runtime dependencies (react, react-dom, zustand) plus @opendv/protocol-ts", () => {
			const pkgJson = JSON.parse(
				readFileSync(join(WORKBENCH_ROOT, "package.json"), "utf-8"),
			) as {
				dependencies: Record<string, string>;
			};
			const depKeys = Object.keys(pkgJson.dependencies).sort();
			expect(depKeys).toEqual([
				"@opendv/protocol-ts",
				"react",
				"react-dom",
				"zustand",
			]);
		});

		it("contains zero backend Python package or filesystem imports across all source files", () => {
			const files = collectSourceFiles(join(WORKBENCH_ROOT, "src"));
			expect(files.length).toBeGreaterThanOrEqual(10);

			for (const file of files) {
				const content = readFileSync(file, "utf-8");
				expect(content).not.toContain("opendv_core");
				expect(content).not.toContain("opendv_telemetry");
				expect(content).not.toContain("opendv_axi_verifier");
				expect(content).not.toContain("opendv_duckdb_sandbox");
				expect(content).not.toContain("opendv_agent_harness");
				expect(content).not.toContain("opendv_platform_api");
				expect(content).not.toContain("node:fs");
				expect(content).not.toContain("dangerouslySetInnerHTML");
				expect(content).not.toContain("innerHTML");
			}
		});

		it("configures Vite 6 dev server strictly on localhost (127.0.0.1:5173) with /api proxy", () => {
			expect(viteConfig.server.host).toBe("127.0.0.1");
			expect(viteConfig.server.port).toBe(5173);
			expect(viteConfig.server.strictPort).toBe(true);
			expect(viteConfig.server.proxy["/api"]?.target).toBe(
				"http://127.0.0.1:8000",
			);
		});
	});

	describe("2. Zero-FOUC index.html, CSS Custom Properties & ThemeRegistry", () => {
		it("includes zero-FOUC inline script in index.html reading open-dv-active-theme and setting data-theme", () => {
			const html = readFileSync(join(WORKBENCH_ROOT, "index.html"), "utf-8");
			expect(html).toContain("open-dv-active-theme");
			expect(html).toContain("prefers-color-scheme: light");
			expect(html).toContain(
				"document.documentElement.setAttribute('data-theme'",
			);
		});

		it("defines all 21 semantic --dv-* CSS Custom Properties across obsidian, paper, and high-contrast in src/index.css", () => {
			const css = readFileSync(
				join(WORKBENCH_ROOT, "src", "index.css"),
				"utf-8",
			);
			expect(css).toContain('[data-theme="obsidian"]');
			expect(css).toContain('[data-theme="paper"]');
			expect(css).toContain('[data-theme="high-contrast"]');

			for (const token of REQUIRED_DV_THEME_TOKENS) {
				expect(css).toContain(token);
			}
		});

		it("switches across Obsidian Dark, Daylight Paper, High-Contrast Audit (WCAG AAA), and custom themes with DOM & storage sync", () => {
			const storageMap = new Map<string, string>();
			const mockStorage = {
				getItem: (k: string) => storageMap.get(k) ?? null,
				setItem: (k: string, v: string) => {
					storageMap.set(k, v);
				},
				removeItem: (k: string) => {
					storageMap.delete(k);
				},
			};

			const appliedAttributes = new Map<string, string>();
			const appliedCssVars = new Map<string, string>();
			const prevDocument = (globalThis as { document?: unknown }).document;
			(globalThis as { document?: unknown }).document = {
				documentElement: {
					setAttribute: (k: string, v: string) => {
						appliedAttributes.set(k, v);
					},
					getAttribute: (k: string) => appliedAttributes.get(k) ?? null,
					style: {
						setProperty: (k: string, v: string) => {
							appliedCssVars.set(k, v);
						},
					},
				},
			};

			try {
				const registry = new ThemeRegistry(mockStorage);
				expect(registry.listThemes().map((t) => t.id)).toEqual([
					"obsidian",
					"paper",
					"high-contrast",
				]);
				expect(OBSIDIAN_THEME.label).toBe("Obsidian Dark");
				expect(PAPER_THEME.label).toBe("Daylight Paper");
				expect(HIGH_CONTRAST_THEME.label).toBe("High-Contrast Audit");
				expect(HIGH_CONTRAST_THEME.contrastStandard).toBe("WCAG_AAA");

				// Switch to Daylight Paper
				const paper = registry.setActiveTheme("paper");
				expect(paper.id).toBe("paper");
				expect(storageMap.get(THEME_STORAGE_KEY)).toBe("paper");
				expect(appliedAttributes.get("data-theme")).toBe("paper");
				expect(appliedCssVars.get("--dv-bg-canvas")).toBe("#f8fafc");

				// Switch to High-Contrast Audit (WCAG AAA)
				const hc = registry.setActiveTheme("high-contrast");
				expect(hc.contrastStandard).toBe("WCAG_AAA");
				expect(storageMap.get(THEME_STORAGE_KEY)).toBe("high-contrast");
				expect(appliedAttributes.get("data-theme")).toBe("high-contrast");
				expect(appliedCssVars.get("--dv-bg-canvas")).toBe("#000000");

				// Register and activate custom theme
				const nordicTheme = registry.registerTheme({
					id: "nordic-sovereign",
					label: "Nordic Sovereign Slate",
					colorScheme: "dark",
					contrastStandard: "WCAG_AA",
					tokens: {
						"--dv-bg-canvas": "#0f172a",
						"--dv-text-primary": "#f1f5f9",
						"--dv-hub-accent": "#38bdf8",
					},
				});
				expect(nordicTheme.isPreset).toBe(false);
				expect(registry.listThemes().length).toBe(4);

				registry.setActiveTheme("nordic-sovereign");
				expect(registry.getActiveThemeId()).toBe("nordic-sovereign");
				expect(storageMap.get(THEME_STORAGE_KEY)).toBe("nordic-sovereign");
				expect(appliedAttributes.get("data-theme")).toBe("nordic-sovereign");
				expect(appliedCssVars.get("--dv-hub-accent")).toBe("#38bdf8");

				// Verify ThemeToggle renders all 4 registered themes including WCAG AAA badge
				const toggleMarkup = renderToStaticMarkup(
					createElement(ThemeToggle, { registry }),
				);
				expect(toggleMarkup).toContain("Obsidian Dark");
				expect(toggleMarkup).toContain("Daylight Paper");
				expect(toggleMarkup).toContain("High-Contrast Audit (WCAG AAA)");
				expect(toggleMarkup).toContain("Nordic Sovereign Slate");
			} finally {
				(globalThis as { document?: unknown }).document = prevDocument;
			}
		});

		it("exposes WCAG AA Dual-Encoded Entity and Verdict Visual Grammar", () => {
			expect(ENTITY_VISUAL_GRAMMAR.HUB.prefixBadge).toBe("[HUB]");
			expect(ENTITY_VISUAL_GRAMMAR.LNK.prefixBadge).toBe("[LNK]");
			expect(ENTITY_VISUAL_GRAMMAR.SAL.prefixBadge).toBe("[SAL]");
			expect(ENTITY_VISUAL_GRAMMAR.SAT.prefixBadge).toBe("[SAT]");
			expect(ENTITY_VISUAL_GRAMMAR.SAT_PII.prefixBadge).toBe("[SAT-PII]");
			expect(ENTITY_VISUAL_GRAMMAR.REF.prefixBadge).toBe("[REF]");
			expect(ENTITY_VISUAL_GRAMMAR.DROPPED_SURROGATE.prefixBadge).toBe(
				"[DROPPED-SURROGATE]",
			);

			expect(VERDICT_VISUAL_GRAMMAR.PASS.shapeSymbol).toBe("●");
			expect(VERDICT_VISUAL_GRAMMAR["AUTO-FIX"].shapeSymbol).toBe("◆");
			expect(VERDICT_VISUAL_GRAMMAR["REJECT-WITH-TRACE"].shapeSymbol).toBe("■");
			expect(VERDICT_VISUAL_GRAMMAR["ASK-USER"].shapeSymbol).toBe("▲");
		});
	});

	describe("3. WorkbenchPluginRegistry & Custom ESG Gate Card Plugin", () => {
		it("registers and retrieves view plugins in canonical WB-00..WB-11 order and gate card plugins", () => {
			const MockWB05View = ({ currentEvent }: ViewPluginProps) =>
				createElement(
					"div",
					{ "data-testid": "mock-wb05-view" },
					currentEvent?.event_id ?? "none",
				);
			const MockWB00View = ({ currentEvent }: ViewPluginProps) =>
				createElement(
					"div",
					{ "data-testid": "mock-wb00-view" },
					currentEvent?.event_id ?? "none",
				);

			WorkbenchPluginRegistry.registerView({
				id: "WB-05",
				title: "Pass 2/3 ELM Topology & 3-Tier BKCC Collision Shield",
				shortTitle: "ELM Topology",
				stageId: "STAGE_04_SHIP_VAULT_PASS2",
				sceneNumber: 4,
				iconName: "network",
				component: MockWB05View,
			});
			WorkbenchPluginRegistry.registerView({
				id: "WB-00",
				title: "Master Pipeline Command Center & 6-Gate Scoreboard",
				shortTitle: "Command Center",
				stageId: "STAGE_00_ORCHESTRATION",
				sceneNumber: 1,
				iconName: "compass",
				component: MockWB00View,
			});

			// Verify canonical ordering WB-00 before WB-05
			const listedViews = WorkbenchPluginRegistry.listViews();
			expect(listedViews.map((v) => v.id)).toEqual(["WB-00", "WB-05"]);
			expect(WorkbenchPluginRegistry.getView("WB-05")?.shortTitle).toBe(
				"ELM Topology",
			);

			// Register reference custom ESG gate card plugin
			const esgPlugin = registerCustomEsgGatePlugin();
			expect(esgPlugin.gateId).toBe(CUSTOM_ESG_GATE_ID);
			expect(
				WorkbenchPluginRegistry.getGateCard(CUSTOM_ESG_GATE_ID)?.title,
			).toBe("ILPA ESG Rating & Drift Companion Gate");
			expect(WorkbenchPluginRegistry.listGateCards().length).toBe(1);

			// Match against WB-09 Schema Drift event (which evaluates ALG-A9A & EVO-01)
			const wb09Event = PE_DEMO_EVENTS.find(
				(e) => e.workbench_view_id === "WB-09",
			);
			expect(wb09Event).toBeDefined();
			if (wb09Event) {
				const matched =
					WorkbenchPluginRegistry.findGateCardsForEvent(wb09Event);
				expect(matched.map((g) => g.gateId)).toContain(CUSTOM_ESG_GATE_ID);

				const esgCardHtml = renderToStaticMarkup(
					createElement(CustomEsgGateCard, {
						event: wb09Event,
						isExpanded: true,
						onToggleExpand: () => {},
					}),
				);
				expect(esgCardHtml).toContain("ILPA ESG Rating");
				expect(esgCardHtml).toContain("ALG-A9A");
				expect(esgCardHtml).toContain("Fund IV");
			}
		});
	});

	describe("4. Zustand Workbench Store: Offline Replay, Live SSE, RBAC & Captain-Hold", () => {
		it("supports offline replay step, seek, speed, play/pause, instant replay, and RBAC redaction", async () => {
			const store = createWorkbenchStore({ startAtZero: true });
			expect(store.getState().mode).toBe("offline-replay");
			expect(store.getState().replayCursor).toBe(0);
			expect(store.getState().events.length).toBe(0);
			expect(store.getState().totalReplayEvents).toBe(PE_DEMO_EVENTS.length);

			// Step forward 1 event (WB-00)
			const first = store.getState().stepForward();
			expect(first?.event_id).toBe("evt-pe-000");
			expect(store.getState().replayCursor).toBe(1);
			expect(store.getState().events.length).toBe(1);

			// Seek to index 5 (first 5 events emitted)
			const fifthTarget = store.getState().seekReplay(5);
			expect(fifthTarget?.event_id).toBe("evt-pe-005");
			expect(store.getState().replayCursor).toBe(5);
			expect(store.getState().events.length).toBe(5);

			// Change playback speed
			store.getState().setPlaybackSpeed(5);
			expect(store.getState().playbackSpeed).toBe(5);
			expect(store.getState().getMockReplayClient().getSpeed()).toBe(5);

			// Play remaining events to completion
			const emitted = await store.getState().playReplay({ stepDelayMs: 0 });
			expect(emitted).toBe(PE_DEMO_EVENTS.length - 5);
			expect(store.getState().replayCursor).toBe(PE_DEMO_EVENTS.length);
			expect(store.getState().runStatus).toBe("COMPLETED");

			// Test dynamic RBAC role redaction on CONFIDENTIAL / MNPI events
			store.getState().setActiveView("WB-11");
			expect(store.getState().currentEvent?.data_classification).toBe("MNPI");
			expect(
				store.getState().currentEvent?.layer1_story_banner.headline,
			).toContain("14.50x");

			store.getState().setRbacRole("PUBLIC");
			expect(
				store.getState().currentEvent?.layer1_story_banner.headline,
			).toContain("[REDACTED MNPI]");

			store.getState().setRbacRole("RESTRICTED_PII_CUSTODIAN");
			expect(
				store.getState().currentEvent?.layer1_story_banner.headline,
			).toContain("14.50x");

			// Load from JSONL string
			store.getState().loadOfflineReplay(PE_DEMO_EVENTS_JSONL, {
				startAtZero: true,
			});
			expect(store.getState().replayCursor).toBe(0);
			const instantCount = store.getState().replayAllInstant();
			expect(instantCount).toBe(PE_DEMO_EVENTS.length);
		});

		it("supports Live SSE stream ingestion and Captain-Hold decision submission via OpenDVPlatformClient", async () => {
			let capturedDecisionBody: unknown = null;
			const mockFetch = (async (
				url: string | URL | Request,
				init?: RequestInit,
			) => {
				const urlStr = String(url);
				if (urlStr.endsWith("/decisions") && init?.method === "POST") {
					capturedDecisionBody = JSON.parse(String(init.body));
					return new Response(
						JSON.stringify({
							decision_id: "dec-live-001",
							status: "ACCEPTED",
							resumed_run_id: PE_DEMO_RUN_ID,
							next_stage_id: "STAGE_03_SHIP_VAULT_PASS1",
							next_event_id: "evt-pe-005",
						}),
						{ status: 200, headers: { "Content-Type": "application/json" } },
					);
				}
				return new Response("{}", { status: 200 });
			}) as unknown as typeof fetch;

			const store = createWorkbenchStore({ startAtZero: true });
			store.getState().connectLiveStream({
				baseUrl: "http://127.0.0.1:8000",
				runId: PE_DEMO_RUN_ID,
				fetchImpl: mockFetch,
				eventSourceFactory: () => ({
					addEventListener: () => {},
					close: () => {},
					onerror: null,
				}),
			});

			expect(store.getState().mode).toBe("live-sse");
			expect(store.getState().isConnected).toBe(true);

			// FeedWB-03 ASK-USER event over SSE wire format
			const wb03HoldEvent = PE_DEMO_EVENTS.find(
				(e) => e.workbench_view_id === "WB-03",
			) as VaultObservabilityEventDTO;
			const sseFrame = `id: ${wb03HoldEvent.event_id}\nevent: message\ndata: ${JSON.stringify(wb03HoldEvent)}\n\n`;
			store.getState().ingestLiveSseChunk(sseFrame);

			expect(store.getState().events.length).toBe(1);
			expect(store.getState().runStatus).toBe("PAUSED_HOLD");
			expect(store.getState().activeHoldIds).toContain(
				"hold-tier-u3-horizon-sovereign-vehicle",
			);

			// Submit Captain-Hold decision
			const decisionResp = await store.getState().submitCaptainHoldDecision({
				event_id: wb03HoldEvent.event_id,
				hold_id: "hold-tier-u3-horizon-sovereign-vehicle",
				decision_type: "RESOLVE_CAPTAIN_HOLD",
				action: "APPROVE",
				selected_candidate_id: "APEX_CO_I",
				resolution_notes: "Confirmed Apex Co-Invest Fund I LP with Steward.",
			});

			expect(decisionResp.decision_id).toBe("dec-live-001");
			expect(decisionResp.status).toBe("ACCEPTED");
			expect(capturedDecisionBody).toEqual({
				event_id: wb03HoldEvent.event_id,
				hold_id: "hold-tier-u3-horizon-sovereign-vehicle",
				decision_type: "RESOLVE_CAPTAIN_HOLD",
				action: "APPROVE",
				selected_candidate_id: "APEX_CO_I",
				resolution_notes: "Confirmed Apex Co-Invest Fund I LP with Steward.",
			});
			expect(store.getState().activeHoldIds).not.toContain(
				"hold-tier-u3-horizon-sovereign-vehicle",
			);

			// Feed run_complete SSE event
			store
				.getState()
				.ingestLiveSseChunk(
					`event: run_complete\ndata: {"status":"COMPLETED"}\n\n`,
				);
			expect(store.getState().isConnected).toBe(false);
			expect(store.getState().runStatus).toBe("COMPLETED");
		});
	});

	describe("5. Keyboard Shortcut Routing & 3-Layer Progressive Disclosure Shell Rendering", () => {
		it("routes Space/K, L/ArrowRight, P, Escape, and 1-9,0,-,= to WB-00..WB-11", () => {
			let playPauseToggled = 0;
			let stepped = 0;
			let proofToggled = 0;
			let proofClosed = 0;
			const selectedViews: string[] = [];

			const actions = {
				togglePlayPause: () => {
					playPauseToggled += 1;
				},
				stepForward: () => {
					stepped += 1;
				},
				toggleProofDrawer: () => {
					proofToggled += 1;
				},
				closeProofDrawer: () => {
					proofClosed += 1;
				},
				setActiveView: (v: string) => {
					selectedViews.push(v);
				},
			};

			expect(routeWorkbenchKeyboardShortcut(" ", actions)).toBe(true);
			expect(routeWorkbenchKeyboardShortcut("k", actions)).toBe(true);
			expect(routeWorkbenchKeyboardShortcut("K", actions)).toBe(true);
			expect(playPauseToggled).toBe(3);

			expect(routeWorkbenchKeyboardShortcut("l", actions)).toBe(true);
			expect(routeWorkbenchKeyboardShortcut("ArrowRight", actions)).toBe(true);
			expect(stepped).toBe(2);

			expect(routeWorkbenchKeyboardShortcut("p", actions)).toBe(true);
			expect(routeWorkbenchKeyboardShortcut("P", actions)).toBe(true);
			expect(proofToggled).toBe(2);

			expect(routeWorkbenchKeyboardShortcut("Escape", actions)).toBe(true);
			expect(proofClosed).toBe(1);

			const shortcutKeys = [
				"1",
				"2",
				"3",
				"4",
				"5",
				"6",
				"7",
				"8",
				"9",
				"0",
				"-",
				"=",
			];
			for (const key of shortcutKeys) {
				expect(routeWorkbenchKeyboardShortcut(key, actions)).toBe(true);
			}
			expect(selectedViews).toEqual([
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
			]);
			expect(Object.keys(SHORTCUT_KEY_TO_VIEW_ID).length).toBe(12);
			expect(routeWorkbenchKeyboardShortcut("z", actions)).toBe(false);
		});

		it("scrubs raw 64-hex digests from Layer 1 Story Banner and renders all 3 Layers + 4 Proof Tabs + inert background management", () => {
			// Verify Layer 1 64-hex scrubber
			const rawWithDigest =
				"Collision on 5D15EB10C8D7621D3167849C41C5F9CD9244FD79FB720CB17104D49E4B820AE4 in Hub";
			const scrubbed = sanitizeLayer1Text(rawWithDigest, 140);
			expect(scrubbed).toContain("[HK: 5D15..0AE4]");
			expect(scrubbed).not.toContain(
				"5D15EB10C8D7621D3167849C41C5F9CD9244FD79FB720CB17104D49E4B820AE4",
			);

			// Verify inert background attribute synchronization helper
			const targetAttrs = new Map<string, string>();
			const mockDoc = {
				getElementById: (id: string) =>
					id === DEFAULT_BACKGROUND_WORKSPACE_ID
						? {
								setAttribute: (k: string, v: string) => targetAttrs.set(k, v),
								removeAttribute: (k: string) => targetAttrs.delete(k),
							}
						: null,
			} as unknown as Document;

			syncBackgroundInertAttribute(true, mockDoc);
			expect(targetAttrs.has("inert")).toBe(true);
			syncBackgroundInertAttribute(false, mockDoc);
			expect(targetAttrs.has("inert")).toBe(false);

			// Render full WorkbenchShell with Proof Drawer open and Custom ESG Gate Card registered
			registerCustomEsgGatePlugin();
			useWorkbenchStore.getState().replayAllInstant();
			useWorkbenchStore.getState().setActiveView("WB-09");
			useWorkbenchStore.getState().openProofDrawer("3A");

			const shellHtml = renderToStaticMarkup(createElement(WorkbenchShell));
			expect(shellHtml).toContain('aria-live="polite"');
			expect(shellHtml).toContain('data-layer="layer-1-story-banner"');
			expect(shellHtml).toContain('data-layer="layer-2-visual-canvas"');
			expect(shellHtml).toContain('data-layer="layer-3-proof-drawer"');
			expect(shellHtml).toContain("<dialog");
			expect(shellHtml).toContain("inert");
			expect(shellHtml).toContain("3A: LaTeX Proof");
			expect(shellHtml).toContain("3B: YAML/IR Diff");
			expect(shellHtml).toContain("3C: SQLGlot AST &amp; Test Vectors");
			expect(shellHtml).toContain("3D: @toon_v1 &amp; Raw JSONL");
			expect(shellHtml).toContain("ILPA ESG Rating");

			// Render each of the 4 Layer 3 Proof Tabs (3A, 3B, 3C, 3D)
			const wb08Event = PE_DEMO_EVENTS.find((e) => e.event_id === "evt-pe-010");
			for (const tab of ["3A", "3B", "3C", "3D"] as const) {
				const drawerHtml = renderToStaticMarkup(
					createElement(Layer3ProofDrawer, {
						isOpen: true,
						activeTab: tab,
						currentEvent: wb08Event,
					}),
				);
				expect(drawerHtml).toContain(`data-testid="proof-content-${tab}"`);
			}

			// Verify individual shell components render cleanly
			expect(renderToStaticMarkup(createElement(Layer1StoryBanner))).toContain(
				'aria-live="polite"',
			);
			expect(renderToStaticMarkup(createElement(SidebarNav))).toContain(
				"WB-11",
			);
			expect(renderToStaticMarkup(createElement(PlaybackControls))).toContain(
				"Speed",
			);
		});
	});
});
