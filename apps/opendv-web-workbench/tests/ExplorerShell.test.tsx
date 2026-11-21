/**
 * M3 Failing-First TDD Tests — Claude `DESIGN.md` Tokens, Local Fonts & 4-Tab Application Shell
 * (`apps/opendv-web-workbench/tests/ExplorerShell.test.tsx`)
 *
 * Covers:
 * 1. Claude `DESIGN.md` Tokens, Fonts & WCAG AA >= 4.5:1 Contrast (`NFR-UI-01`)
 * 2. Light-Only Static Build, Bundled `.woff2` Fonts & Responsive/React Flow CSS (`NFR-UI-01`, `D26`)
 * 3. 4-Tab `54px` `ExplorerTopBar` + Agent Readiness Pill + Cost Meter + Logs & Traces + Remote Banner (`NFR-UI-01`, `FR-DVE-01`, `FR-AGT-10`, `FR-AUTH-04`, `D18`, `D21`)
 * 4. 4-Tab `url-router.ts` (`FR-DVE-01`)
 * 5. `OpenDVWorkbenchExplorer` Shell Mounting (`FR-DVE-01`)
 */

import { describe, expect, it } from "bun:test";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
	buildStaticWorkbenchAssets,
	generateStaticIndexHtml,
} from "../src/build-static";
import { ExplorerTopBar } from "../src/explorer/ExplorerTopBar";
import { OpenDVWorkbenchExplorer } from "../src/explorer/OpenDVWorkbenchExplorer";
import * as DesignTokens from "../src/explorer/design-tokens";
import { createExplorerStore } from "../src/explorer/explorer-store";
import { createDefaultPlatformConfig } from "../src/explorer/settings/usePlatformConfigActions";
import {
	buildExplorerUrl,
	parseExplorerUrlState,
} from "../src/explorer/url-router";
import { GLOBAL_COMMERCE_EXPLORER_BUNDLE } from "./fixtures/commerce-explorer-fixture";

function hexToRgb(hex: string): [number, number, number] {
	const clean = hex.replace("#", "").trim();
	if (clean.length === 3) {
		return [
			Number.parseInt((clean[0] ?? "0") + (clean[0] ?? "0"), 16),
			Number.parseInt((clean[1] ?? "0") + (clean[1] ?? "0"), 16),
			Number.parseInt((clean[2] ?? "0") + (clean[2] ?? "0"), 16),
		];
	}
	return [
		Number.parseInt(clean.slice(0, 2), 16),
		Number.parseInt(clean.slice(2, 4), 16),
		Number.parseInt(clean.slice(4, 6), 16),
	];
}

function channelLuminance(c: number): number {
	const s = c / 255;
	return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

function contrastRatio(hex1: string, hex2: string): number {
	const [r1, g1, b1] = hexToRgb(hex1);
	const [r2, g2, b2] = hexToRgb(hex2);
	const l1 =
		0.2126 * channelLuminance(r1) +
		0.7152 * channelLuminance(g1) +
		0.0722 * channelLuminance(b1);
	const l2 =
		0.2126 * channelLuminance(r2) +
		0.7152 * channelLuminance(g2) +
		0.0722 * channelLuminance(b2);
	return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}

describe("M3: Claude DESIGN.md Tokens, Fonts & 4-Tab Application Shell", () => {
	const workbenchRoot = resolve(__dirname, "..");

	describe("1. Claude DESIGN.md Tokens, Fonts & WCAG AA Contrast (NFR-UI-01)", () => {
		it("exports CLAUDE_TOKENS, EXPLORER_COLORS, EXPLORER_FONTS, and SPIKE_MARK_SVG matching DESIGN.md", () => {
			const tokensMod = DesignTokens as Record<string, unknown>;
			expect(tokensMod.CLAUDE_TOKENS).toBeDefined();
			expect(tokensMod.SPIKE_MARK_SVG).toBeDefined();

			const serializedTokens = JSON.stringify(
				tokensMod.CLAUDE_TOKENS,
			).toLowerCase();
			for (const requiredHex of [
				"#faf9f5",
				"#f5f0e8",
				"#efe9de",
				"#e8e0d2",
				"#cc785c",
				"#a9583e",
				"#e6dfd8",
				"#d5cdc2",
				"#181715",
				"#141413",
				"#3d3d3a",
				"#6c6a64",
				"#5db872",
				"#5db8a6",
				"#e8a55a",
				"#d4a017",
				"#c64545",
			]) {
				expect(serializedTokens).toContain(requiredHex);
			}
			expect(serializedTokens).toContain("instrument serif");
			expect(serializedTokens).toContain("dm sans");
			expect(serializedTokens).toContain("jetbrains mono");
			expect(
				serializedTokens.includes("-0.45px") ||
					serializedTokens.includes("-0.4px"),
			).toBe(true);

			// EXPLORER_COLORS compatibility mapping to Claude cream/coral/obsidian palette
			const colors = DesignTokens.EXPLORER_COLORS;
			expect(colors.bgPage.toLowerCase()).toBe("#faf9f5");
			expect(colors.bgSubtle.toLowerCase()).toBe("#f5f0e8");
			expect(colors.bgWarmStrip.toLowerCase()).toBe("#efe9de");
			expect(colors.borderLine.toLowerCase()).toBe("#e6dfd8");
			expect(colors.borderRail.toLowerCase()).toBe("#d5cdc2");
			expect(colors.borderSelected.toLowerCase()).toBe("#cc785c");
			expect(colors.inkPrimary.toLowerCase()).toBe("#141413");
			expect(colors.inkBody.toLowerCase()).toBe("#3d3d3a");
			expect(colors.inkMuted.toLowerCase()).toBe("#6c6a64");
			expect(colors.codeBg.toLowerCase()).toBe("#181715");
			expect(colors.codeFg.toLowerCase()).toBe("#faf9f5");

			// EXPLORER_FONTS includes Instrument Serif (400), DM Sans, and JetBrains Mono
			const fonts = DesignTokens.EXPLORER_FONTS as Record<string, string>;
			expect(fonts.serif).toContain("Instrument Serif");
			expect(fonts.sans).toContain("DM Sans");
			expect(fonts.mono).toContain("JetBrains Mono");

			// SPIKE_MARK_SVG inline 4-spoke radial brand mark
			const spikeSvg = String(tokensMod.SPIKE_MARK_SVG);
			expect(spikeSvg).toContain("<svg");
			expect(spikeSvg).toContain('viewBox="0 0 24 24"');
		});

		it("satisfies WCAG AA >= 4.5:1 contrast for Claude text tokens across cream and dark surfaces", () => {
			const {
				inkPrimary,
				inkBody,
				inkMuted,
				bgPage,
				bgWarmStrip,
				codeBg,
				codeFg,
			} = DesignTokens.EXPLORER_COLORS;

			for (const bg of [bgPage, bgWarmStrip, "#ffffff"]) {
				expect(contrastRatio(inkPrimary, bg)).toBeGreaterThanOrEqual(4.5);
				expect(contrastRatio(inkBody, bg)).toBeGreaterThanOrEqual(4.5);
			}
			for (const bg of [bgPage, "#ffffff"]) {
				expect(contrastRatio(inkMuted, bg)).toBeGreaterThanOrEqual(4.5);
			}
			expect(contrastRatio("#a9583e", bgPage)).toBeGreaterThanOrEqual(4.5);
			expect(contrastRatio("#ffffff", "#a9583e")).toBeGreaterThanOrEqual(4.5);
			expect(contrastRatio(codeFg, codeBg)).toBeGreaterThanOrEqual(4.5);
		});
	});

	describe("2. Light-Only Static Build, Bundled .woff2 Fonts & CSS Rules (NFR-UI-01, D26)", () => {
		it("emits light Claude theme without data-theme='obsidian' in generateStaticIndexHtml() and index.html", () => {
			const generated = generateStaticIndexHtml();
			expect(generated).not.toContain('data-theme="obsidian"');
			expect(
				generated.includes('data-theme="claude"') ||
					generated.includes("color-scheme: light"),
			).toBe(true);

			const devIndexHtml = readFileSync(
				join(workbenchRoot, "index.html"),
				"utf-8",
			);
			expect(devIndexHtml).not.toContain('data-theme="obsidian"');
		});

		it("copies all 4 .woff2 font files to <outDir>/assets/fonts/ during buildStaticWorkbenchAssets()", async () => {
			const tempDir = mkdtempSync(join(tmpdir(), "opendv-m3-fonts-"));
			try {
				await buildStaticWorkbenchAssets({
					outDirs: [tempDir],
					silent: true,
				});
				for (const fontFile of [
					"instrument-serif-latin.woff2",
					"instrument-serif-italic-latin.woff2",
					"dm-sans-latin.woff2",
					"jetbrains-mono-latin.woff2",
				]) {
					const fontPath = join(tempDir, "assets", "fonts", fontFile);
					expect(existsSync(fontPath)).toBe(true);
					expect(readFileSync(fontPath).byteLength).toBeGreaterThan(1000);
				}
			} finally {
				rmSync(tempDir, { recursive: true, force: true });
			}
		});

		it("declares @font-face /assets/fonts/*.woff2, dialog:modal containment, React Flow contrast vars, and @media (max-width: 767px) in src/index.css", () => {
			const css = readFileSync(
				join(workbenchRoot, "src", "index.css"),
				"utf-8",
			);

			expect(css).toContain("@font-face");
			for (const fontFile of [
				"/assets/fonts/instrument-serif-latin.woff2",
				"/assets/fonts/instrument-serif-italic-latin.woff2",
				"/assets/fonts/dm-sans-latin.woff2",
				"/assets/fonts/jetbrains-mono-latin.woff2",
			]) {
				expect(css).toContain(fontFile);
			}

			expect(css).toContain("dialog:modal");
			expect(css).toContain("overscroll-behavior: contain");
			expect(css).toContain("--xy-controls-");
			expect(css).toContain("--xy-minimap-");
			expect(css).toContain("@media (max-width: 767px)");
		});
	});

	describe("3. 4-Tab 54px ExplorerTopBar, Readiness Pill, Cost Meter & Remote Banner", () => {
		const buildBaseTopBarProps = (overrides: Record<string, unknown> = {}) => ({
			bundle: GLOBAL_COMMERCE_EXPLORER_BUNDLE,
			view: "ontology" as never,
			stageShortCode: "STAGE_00",
			activeWave: "all" as const,
			runState: "sealed" as const,
			statusBannerText: "Sealed · 6/6 gates PASS",
			statusDotColor: "#5db872",
			streamedTokens: 0,
			isRunSelectorOpen: false,
			isLoadingRuns: false,
			runsList: [],
			onToggleRunSelector: () => {},
			onSelectRun: () => {},
			onOpenNewRunModal: () => {},
			onGoOntology: () => {},
			onGoDvSchema: () => {},
			onGoPipeline: () => {},
			onGoSettings: () => {},
			onOpenLogs: () => {},
			sessionCostTokens: 1250,
			sessionCostUsd: 0.042,
			sessionLogsCount: 7,
			sessionDigest: "ses-w1 · v0.1.0",
			...overrides,
		});

		it("renders 54px height, open-dv + SPIKE_MARK_SVG, all 4 tabs, Cost Meter, Logs & Traces button, and dark #181715 session digest pill", () => {
			const readyConfig = createDefaultPlatformConfig();
			readyConfig.providers = readyConfig.providers.map((p) =>
				p.provider === readyConfig.harness.provider
					? { ...p, available: true }
					: p,
			);

			const html = renderToStaticMarkup(
				createElement(
					ExplorerTopBar as unknown as React.ComponentType<
						Record<string, unknown>
					>,
					buildBaseTopBarProps({
						platformConfig: readyConfig,
						remoteUnauthenticatedMode: false,
					}),
				),
			);

			expect(html).toMatch(/height:\s*54px/i);
			expect(html).toContain("open-dv");
			expect(html).toContain("<svg");
			expect(html).toContain("Ontology &amp; Pipeline");
			expect(html).toContain("DV Schema Explorer");
			expect(html).toContain("Run Explorer");
			expect(html).toContain("Settings");

			// Live Agent Ready pill
			expect(html).toContain("Agent Ready");
			expect(html).not.toContain("Agent Setup Required");

			// Live Cost Meter (tokens + USD)
			expect(html).toMatch(/1,?250/);
			expect(html).toContain("$0.04");

			// Logs & Traces trigger & dark #181715 session digest pill
			expect(html).toContain("Logs &amp; Traces");
			expect(html.toLowerCase()).toContain("#181715");
			expect(html).toContain("ses-w1");

			// Remote banner absent when remote_unauthenticated_mode is false
			expect(html).not.toContain("Unauthenticated Remote Mode");
		});

		it("switches Agent Readiness pill to 'Agent Setup Required' when active provider is unavailable", () => {
			const unreadyConfig = createDefaultPlatformConfig();
			unreadyConfig.providers = unreadyConfig.providers.map((p) =>
				p.provider === unreadyConfig.harness.provider
					? { ...p, available: false }
					: p,
			);

			const html = renderToStaticMarkup(
				createElement(
					ExplorerTopBar as unknown as React.ComponentType<
						Record<string, unknown>
					>,
					buildBaseTopBarProps({
						platformConfig: unreadyConfig,
						agentReady: false,
					}),
				),
			);

			expect(html).toContain("Agent Setup Required");
			expect(html).not.toContain("Agent Ready");
		});

		it("renders coral Unauthenticated Remote Mode banner when remote_unauthenticated_mode is true", () => {
			const remoteConfig = {
				...createDefaultPlatformConfig(),
				remote_unauthenticated_mode: true,
			};

			const html = renderToStaticMarkup(
				createElement(
					ExplorerTopBar as unknown as React.ComponentType<
						Record<string, unknown>
					>,
					buildBaseTopBarProps({
						platformConfig: remoteConfig,
						remoteUnauthenticatedMode: true,
					}),
				),
			);

			expect(html).toContain("Unauthenticated Remote Mode");
			expect(
				html.toLowerCase().includes("#cc785c") ||
					html.toLowerCase().includes("#c64545"),
			).toBe(true);
		});
	});

	describe("4. 4-Tab url-router.ts (FR-DVE-01)", () => {
		it("maps /, /ontology, /builder -> ontology; /dv-schema -> dv_schema; /explorer -> pipeline; /stages/00..07 -> stage; /settings -> settings", () => {
			expect(parseExplorerUrlState("/").view).toBe("ontology" as never);
			expect(parseExplorerUrlState("/ontology").view).toBe("ontology" as never);
			expect(parseExplorerUrlState("/builder").view).toBe("ontology" as never);
			expect(parseExplorerUrlState("/dv-schema").view).toBe(
				"dv_schema" as never,
			);
			expect(parseExplorerUrlState("/explorer").view).toBe("pipeline");
			expect(parseExplorerUrlState("/settings").view).toBe("settings");

			const s0 = parseExplorerUrlState("/stages/00");
			expect(s0.view).toBe("stage");
			expect(s0.stageIdx).toBe(0);

			const s7 = parseExplorerUrlState("/stages/07");
			expect(s7.view).toBe("stage");
			expect(s7.stageIdx).toBe(7);
		});

		it("preserves query params run, compare, session, artifact, tab, wave, session_id, source, domain_namespace, pipeline_id", () => {
			const parsed = parseExplorerUrlState(
				"/dv-schema",
				"?run=run-101&compare=run-100&session=ses-7&artifact=final_contract&tab=raw&wave=W2&session_id=legacy-1&source=crm.csv&domain_namespace=wealth&pipeline_id=pipe-9",
			) as unknown as Record<string, unknown>;

			expect(parsed.view).toBe("dv_schema");
			expect(parsed.run ?? parsed.runId).toBe("run-101");
			expect(parsed.compare ?? parsed.compareRunId).toBe("run-100");
			expect(parsed.session ?? parsed.sessionId).toBe("ses-7");
			expect(parsed.fullId).toBe("final_contract");
			expect(parsed.fullTab).toBe("raw");
			expect(parsed.activeWave).toBe("W2");
			expect(parsed.builderSessionId).toBe("legacy-1");
			expect(parsed.builderSource).toBe("crm.csv");
			expect(parsed.builderDomain).toBe("wealth");
			expect(parsed.builderPipelineId).toBe("pipe-9");

			const rebuilt = buildExplorerUrl(parsed as never);
			expect(rebuilt.startsWith("/dv-schema?")).toBe(true);
			const roundTripped = new URL(rebuilt, "http://localhost");
			expect(roundTripped.searchParams.get("run")).toBe("run-101");
			expect(roundTripped.searchParams.get("compare")).toBe("run-100");
			expect(roundTripped.searchParams.get("session")).toBe("ses-7");
			expect(roundTripped.searchParams.get("artifact")).toBe("final_contract");
			expect(roundTripped.searchParams.get("tab")).toBe("raw");
			expect(roundTripped.searchParams.get("wave")).toBe("W2");
			expect(roundTripped.searchParams.get("session_id")).toBe("legacy-1");
			expect(roundTripped.searchParams.get("source")).toBe("crm.csv");
			expect(roundTripped.searchParams.get("domain_namespace")).toBe("wealth");
			expect(roundTripped.searchParams.get("pipeline_id")).toBe("pipe-9");

			expect(
				buildExplorerUrl({
					view: "pipeline",
					stageIdx: 0,
					fullId: null,
					fullTab: "split",
					activeWave: "all",
				}),
			).toBe("/explorer");
		});
	});

	describe("5. OpenDVWorkbenchExplorer Shell Mounting", () => {
		it("renders DV Schema Explorer shell view when active while keeping main[data-testid='workbench-explorer-root'] intact", () => {
			const store = createExplorerStore({
				bundle: GLOBAL_COMMERCE_EXPLORER_BUNDLE,
				startView: "dv_schema" as never,
				syncBrowserUrl: false,
			});

			const html = renderToStaticMarkup(
				createElement(OpenDVWorkbenchExplorer, { store }),
			);

			expect(html).toContain('data-testid="workbench-explorer-root"');
			expect(html).toMatch(/<main[^>]*data-testid="workbench-explorer-root"/);
			expect(html).toContain("DV Schema Explorer");
			expect(html).not.toContain("Outputs · 5");
		});
	});
});
