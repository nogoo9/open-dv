import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { readFileSync } from "node:fs";
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
	DEFAULT_REPLAY_STEP_DELAY_MS,
	ENTITY_VISUAL_GRAMMAR,
	HIGH_CONTRAST_THEME,
	Layer1StoryBanner,
	Layer3ProofDrawer,
	OBSIDIAN_THEME,
	PAPER_THEME,
	PRESET_THEMES,
	PlaybackControls,
	type PresetThemeId,
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
	isInteractiveInputTarget,
	registerCustomEsgGatePlugin,
	routeWorkbenchKeyboardShortcut,
	sanitizeLayer1Text,
	syncBackgroundInertAttribute,
	useWorkbenchStore,
} from "../src/index";
import { viteConfig } from "../vite.config";

const WORKBENCH_ROOT = resolve(import.meta.dir, "..");

function parseThemeCssBlocks(
	cssText: string,
): Record<string, Record<string, string>> {
	const withoutComments = cssText.replace(/\/\*[\s\S]*?\*\//g, "");
	const blocks: Record<string, Record<string, string>> = {};
	const ruleRegex = /([^{}]+)\{([^{}]+)\}/g;
	for (const match of withoutComments.matchAll(ruleRegex)) {
		const selectorGroup = match[1]?.trim() ?? "";
		const body = match[2] ?? "";
		const themeSelectors = Array.from(
			selectorGroup.matchAll(/\[data-theme="([a-z0-9-]+)"\]/g),
		).map((m) => m[1] ?? "");
		if (themeSelectors.length === 0) {
			continue;
		}
		const props: Record<string, string> = {};
		for (const decl of body.split(";")) {
			const colonIdx = decl.indexOf(":");
			if (colonIdx === -1) {
				continue;
			}
			const propName = decl.slice(0, colonIdx).trim();
			const propVal = decl.slice(colonIdx + 1).trim();
			if (propName.startsWith("--dv-") && propVal) {
				props[propName] = propVal;
			}
		}
		for (const themeId of themeSelectors) {
			if (themeId) {
				blocks[themeId] = { ...(blocks[themeId] ?? {}), ...props };
			}
		}
	}
	return blocks;
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

		it("bundles cleanly for a browser target with zero Node/backend imports and escapes untrusted HTML payloads", async () => {
			const buildResult = await Bun.build({
				entrypoints: [join(WORKBENCH_ROOT, "src", "index.ts")],
				target: "browser",
				external: ["react", "react-dom", "zustand"],
			});
			expect(buildResult.success).toBe(true);
			expect(buildResult.logs).toHaveLength(0);
			expect(buildResult.outputs.length).toBeGreaterThanOrEqual(1);

			const baseEvent = PE_DEMO_EVENTS[0] as VaultObservabilityEventDTO;
			const adversarialEvent: VaultObservabilityEventDTO = {
				...baseEvent,
				layer1_story_banner: {
					...baseEvent.layer1_story_banner,
					headline: "<script>window.__xss=1</script>Headline",
					business_impact: "<img src=x onerror=alert(1)>Impact",
				},
			};
			const bannerMarkup = renderToStaticMarkup(
				createElement(Layer1StoryBanner, { currentEvent: adversarialEvent }),
			);
			expect(bannerMarkup).not.toContain("<script>window.__xss=1</script>");
			expect(bannerMarkup).toContain(
				"&lt;script&gt;window.__xss=1&lt;/script&gt;",
			);
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
		it("executes the zero-FOUC inline script in index.html to resolve data-theme across saved, media-query, and invalid states", () => {
			const html = readFileSync(join(WORKBENCH_ROOT, "index.html"), "utf-8");
			const inlineScriptMatch = html.match(/<script>([\s\S]*?)<\/script>/);
			expect(inlineScriptMatch).not.toBeNull();
			const inlineScript = inlineScriptMatch?.[1] ?? "";
			const runZeroFoucScript = new Function(
				"window",
				"localStorage",
				"document",
				inlineScript,
			) as (
				win: { matchMedia?: (q: string) => { matches: boolean } },
				storage: { getItem: (k: string) => string | null },
				doc: {
					documentElement: { setAttribute: (k: string, v: string) => void };
				},
			) => void;

			const evaluateBootTheme = (options: {
				saved?: string | null;
				throwStorage?: boolean;
				prefersContrastMore?: boolean;
				prefersLight?: boolean;
			}): string | null => {
				let appliedTheme: string | null = null;
				const mockWindow = {
					matchMedia: (query: string) => ({
						matches:
							(query === "(prefers-contrast: more)" &&
								Boolean(options.prefersContrastMore)) ||
							(query === "(prefers-color-scheme: light)" &&
								Boolean(options.prefersLight)),
					}),
				};
				const mockStorage = {
					getItem: (key: string) => {
						if (options.throwStorage) {
							throw new Error("Storage blocked");
						}
						return key === THEME_STORAGE_KEY ? (options.saved ?? null) : null;
					},
				};
				const mockDoc = {
					documentElement: {
						setAttribute: (attr: string, val: string) => {
							if (attr === "data-theme") {
								appliedTheme = val;
							}
						},
					},
				};
				runZeroFoucScript(mockWindow, mockStorage, mockDoc);
				return appliedTheme;
			};

			expect(evaluateBootTheme({ saved: "paper" })).toBe("paper");
			expect(evaluateBootTheme({ saved: "high-contrast" })).toBe(
				"high-contrast",
			);
			expect(
				evaluateBootTheme({ saved: null, prefersContrastMore: true }),
			).toBe("high-contrast");
			expect(evaluateBootTheme({ saved: null, prefersLight: true })).toBe(
				"paper",
			);
			expect(
				evaluateBootTheme({ saved: "Invalid_Upper", prefersLight: false }),
			).toBe("obsidian");
			expect(evaluateBootTheme({ throwStorage: true })).toBe("obsidian");
		});

		it("defines all 21 semantic --dv-* CSS Custom Properties matching PRESET_THEMES across obsidian, paper, and high-contrast in src/index.css", () => {
			const css = readFileSync(
				join(WORKBENCH_ROOT, "src", "index.css"),
				"utf-8",
			);
			const parsedBlocks = parseThemeCssBlocks(css);
			const presetIds: PresetThemeId[] = ["obsidian", "paper", "high-contrast"];

			for (const themeId of presetIds) {
				const block = parsedBlocks[themeId];
				expect(block).toBeDefined();
				for (const token of REQUIRED_DV_THEME_TOKENS) {
					expect(block?.[token]).toBe(PRESET_THEMES[themeId].tokens[token]);
				}
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
					id: "Nordic-Sovereign",
					label: "Nordic Sovereign Slate",
					colorScheme: "dark",
					contrastStandard: "WCAG_AA",
					tokens: {
						"--dv-bg-canvas": "#0f172a",
						"--dv-text-primary": "#f1f5f9",
						"--dv-hub-accent": "#38bdf8",
					},
				});
				expect(nordicTheme.id).toBe("nordic-sovereign");
				expect(nordicTheme.isPreset).toBe(false);
				expect(registry.listThemes().length).toBe(4);
				expect(registry.getTheme("NORDIC-SOVEREIGN")?.id).toBe(
					"nordic-sovereign",
				);

				registry.setActiveTheme("Nordic-Sovereign");
				expect(registry.getActiveThemeId()).toBe("nordic-sovereign");
				expect(storageMap.get(THEME_STORAGE_KEY)).toBe("nordic-sovereign");
				expect(appliedAttributes.get("data-theme")).toBe("nordic-sovereign");
				expect(appliedCssVars.get("--dv-hub-accent")).toBe("#38bdf8");

				storageMap.set(THEME_STORAGE_KEY, "paper");
				const reloadedRegistry = new ThemeRegistry(mockStorage);
				expect(reloadedRegistry.getActiveThemeId()).toBe("paper");
				expect(appliedAttributes.get("data-theme")).toBe("paper");

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
				WorkbenchPluginRegistry.getGateCard(`  ${CUSTOM_ESG_GATE_ID}  `)?.title,
			).toBe("ILPA ESG Rating & Drift Companion Gate");
			expect(WorkbenchPluginRegistry.listGateCards().length).toBe(1);

			WorkbenchPluginRegistry.registerView({
				id: "WB-01",
				title: "  Contract Intake  ",
				shortTitle: "  Intake  ",
				stageId: "STAGE_01_CONTRACT_INTAKE",
				sceneNumber: 1,
				iconName: "file",
				component: MockWB00View,
				gateCards: [
					{
						gateId: "  VIZ-GATE-COLOCATED  ",
						title: "  Co-located Gate  ",
						ruleIds: ["  RULE-COL-1  "],
						component: CustomEsgGateCard,
					},
				],
			});
			expect(
				WorkbenchPluginRegistry.getGateCard("VIZ-GATE-COLOCATED")?.ruleIds,
			).toEqual(["RULE-COL-1"]);
			expect(() =>
				WorkbenchPluginRegistry.registerView({
					id: "WB-02",
					title: "Invalid Gate View",
					shortTitle: "Invalid",
					stageId: "STAGE_02_PROFILING",
					sceneNumber: 2,
					iconName: "alert",
					component: MockWB00View,
					gateCards: [
						{
							gateId: "   ",
							title: "Bad Gate",
							ruleIds: [],
							component: CustomEsgGateCard,
						},
					],
				}),
			).toThrow();

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
			expect(store.getState().activeHoldIds).toEqual([
				"hold-tier-u3-horizon-sovereign-vehicle",
			]);

			store.getState().seekReplay(2);
			expect(store.getState().activeHoldIds).toEqual([]);
			expect(store.getState().selectedEventId).toBe("evt-pe-001");
			store.getState().seekReplay(0);
			expect(store.getState().selectedEventId).toBeNull();
			store.getState().seekReplay(5);

			// Change playback speed
			expect(DEFAULT_REPLAY_STEP_DELAY_MS).toBeGreaterThan(0);
			store.getState().setPlaybackSpeed(10);
			expect(store.getState().playbackSpeed).toBe(10);
			expect(store.getState().getMockReplayClient().getSpeed()).toBe(10);

			store.getState().togglePlayPause();
			expect(store.getState().isPlaying).toBe(true);
			expect(store.getState().runStatus).toBe("RUNNING");
			store.getState().pauseReplay();
			expect(store.getState().isPlaying).toBe(false);
			expect(store.getState().runStatus).toBe("PAUSED");

			const pendingReplay = store.getState().playReplay({ stepDelayMs: 40 });
			expect(store.getState().isPlaying).toBe(true);
			store.getState().resetReplay();
			await pendingReplay;
			expect(store.getState().isPlaying).toBe(false);
			expect(store.getState().runStatus).toBe("IDLE");
			expect(store.getState().replayCursor).toBe(0);
			expect(store.getState().selectedEventId).toBeNull();

			store.getState().seekReplay(5);
			store.getState().setPlaybackSpeed(5);

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

			expect(
				isInteractiveInputTarget({
					tagName: "BUTTON",
				} as unknown as EventTarget),
			).toBe(true);
			expect(
				isInteractiveInputTarget({
					tagName: "SPAN",
					parentElement: { tagName: "BUTTON" },
				} as unknown as EventTarget),
			).toBe(true);
			expect(
				isInteractiveInputTarget({
					tagName: "DIV",
					getAttribute: (attr: string) => (attr === "role" ? "tab" : null),
				} as unknown as EventTarget),
			).toBe(true);
			expect(
				isInteractiveInputTarget({ tagName: "MAIN" } as unknown as EventTarget),
			).toBe(false);
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

			const customRegistry = new WorkbenchPluginRegistry();
			customRegistry.registerView({
				id: "WB-09",
				title: "Custom Registry Schema Evolution Canvas",
				shortTitle: "Custom Drift Rail",
				stageId: "STAGE_06_SCHEMA_EVOLUTION",
				sceneNumber: 6,
				iconName: "git-branch",
				component: () =>
					createElement(
						"div",
						{ "data-testid": "custom-registry-wb09" },
						"Custom WB-09 Body",
					),
			});
			const customShellHtml = renderToStaticMarkup(
				createElement(WorkbenchShell, { pluginRegistry: customRegistry }),
			);
			expect(customShellHtml).toContain("Custom Drift Rail");
			expect(customShellHtml).toContain(
				"Custom Registry Schema Evolution Canvas",
			);
			expect(customShellHtml).toContain("Custom WB-09 Body");

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
