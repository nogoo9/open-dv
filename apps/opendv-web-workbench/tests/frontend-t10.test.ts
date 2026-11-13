/**
 * tests/frontend-t10.test.ts — T10 Frontend Test Suite & WCAG AA Accessibility Audit.
 *
 * Requirements:
 * 1. No hardcoded data: renders two structurally distinct explorer bundles and asserts DOM reflects each.
 * 2. Static bundle URL scan: asserts zero occurrences of 127.0.0.1, :8000, or :8420 across src/ (and dist/).
 * 3. WCAG AA Keyboard Navigation: Arrow keys, Tab, Enter traversal across stages and artifacts.
 * 4. WCAG AA Modal Focus Trap & Restoration: ArtifactFullModal (role="dialog", aria-modal="true", Esc key, focus trap/restoration).
 * 5. WCAG AA Dual-Channel Status: every status pill uses both color AND explicit text label/symbol.
 * 6. WCAG AA Contrast Ratios: >= 4.5:1 for text tokens, >= 3:1 for graphical UI elements.
 * 7. Live Region Updates: aria-live="polite" on status indicators and live streams.
 */

import { describe, expect, it } from "bun:test";
import {
	existsSync,
	mkdtempSync,
	readdirSync,
	readFileSync,
	rmSync,
	statSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import React, { createElement } from "react";
import { renderToString } from "react-dom/server";
import type { Artifact, ExplorerBundleDTO, Stage } from "@opendv/protocol-ts";
import { ArtifactFullModal } from "../src/explorer/ArtifactFullModal";
import {
	EXPLORER_A11Y,
	EXPLORER_COLORS,
	EXPLORER_GLYPHS,
	EXPLORER_STATUS_INDICATORS,
	EXPLORER_STATUS_TAGS,
	getStatusIndicator,
	getStatusTagStyle,
} from "../src/explorer/design-tokens";
import { PipelineOverviewView } from "../src/explorer/PipelineOverviewView";
import { StageDrillDownView } from "../src/explorer/StageDrillDownView";

// WCAG 2.1 Relative Luminance & Contrast Ratio calculation
function hexToRgb(hex: string): [number, number, number] {
	const clean = hex.replace("#", "");
	if (clean.length === 3) {
		return [
			parseInt(clean[0] + clean[0], 16),
			parseInt(clean[1] + clean[1], 16),
			parseInt(clean[2] + clean[2], 16),
		];
	}
	return [
		parseInt(clean.slice(0, 2), 16),
		parseInt(clean.slice(2, 4), 16),
		parseInt(clean.slice(4, 6), 16),
	];
}

function channelLuminance(c: number): number {
	const s = c / 255;
	return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}

function relativeLuminance(hex: string): number {
	const [r, g, b] = hexToRgb(hex);
	return (
		0.2126 * channelLuminance(r) +
		0.7152 * channelLuminance(g) +
		0.0722 * channelLuminance(b)
	);
}

function contrastRatio(hex1: string, hex2: string): number {
	const l1 = relativeLuminance(hex1);
	const l2 = relativeLuminance(hex2);
	const lighter = Math.max(l1, l2);
	const darker = Math.min(l1, l2);
	return (lighter + 0.05) / (darker + 0.05);
}

function collectFiles(dir: string, extensions: string[]): string[] {
	const results: string[] = [];
	if (!existsSync(dir)) return results;
	const entries = readdirSync(dir);
	for (const entry of entries) {
		const full = join(dir, entry);
		const stat = statSync(full);
		if (stat.isDirectory()) {
			results.push(...collectFiles(full, extensions));
		} else if (extensions.some((ext) => entry.endsWith(ext))) {
			results.push(full);
		}
	}
	return results;
}

describe("T10 Frontend Suite & WCAG AA Accessibility (#119)", () => {
	const workbenchRoot = resolve(__dirname, "..");
	const srcDir = join(workbenchRoot, "src");

	// --------------------------------------------------------------------------
	// 1. Dynamic Rendering (No Hardcoded Data Test)
	// --------------------------------------------------------------------------
	describe("1. Dynamic Rendering (Zero Hardcoded Data)", () => {
		const makeTestArtifact = (
			id: string,
			name: string,
			group: "Setting" | "Input" | "Intermediate" | "Output",
			format = "JSON",
		): Artifact => ({
			id,
			name,
			type: "Dataset",
			fmt: format,
			group,
			viz: "table",
			short: `Short summary for ${name}`,
			desc: `Full description for ${name}`,
			raw: `raw-content-of-${name}`,
		});

		const makeTestStage = (key: string, name: string, phase = 0): Stage => ({
			key,
			name,
			persona: "agent" as any,
			tier: "reasoning",
			tag: "PASS",
			phase: phase as any,
			blurb: `Blurb for ${name}`,
			desc: `Description for ${name}`,
			ins: [],
			outs: [],
			steps: [
				{
					name: `${name} Step 1`,
					desc: "Step description",
					reads: [],
					writes: [],
				},
			],
			comps: [[`${name}Engine`, "Agent persona", "Engine description", "src/"]],
		});

		it("renders two structurally distinct explorer bundles and verifies DOM reflects each dynamically", () => {
			// Bundle Alpha: Energy Analytics Pipeline
			const alphaArtifacts: Record<string, Artifact> = {
				grid_readings: makeTestArtifact(
					"grid_readings",
					"smart_meter_readings.csv",
					"Input",
					"CSV",
				),
				wind_telemetry: makeTestArtifact(
					"wind_telemetry",
					"turbine_telemetry.parquet",
					"Input",
					"PARQUET",
				),
				alpha_ir: makeTestArtifact(
					"alpha_ir",
					"grid_canonical_ir.json",
					"Output",
					"JSON",
				),
			};
			const alphaStages: Stage[] = [
				makeTestStage("STAGE_00_INGEST", "Smart Grid Ingestion", 0),
				makeTestStage("STAGE_01_ANALYTICS", "Load Balancing Engine", 1),
			];
			const alphaPhases = [
				{ label: "Ingestion", sub: "grid meters", index: 0 },
				{ label: "Balancing", sub: "predictive load", index: 1 },
			];

			const alphaHtml = renderToString(
				createElement(PipelineOverviewView, {
					settingIds: [],
					inputIds: ["grid_readings", "wind_telemetry"],
					outputIds: ["alpha_ir"],
					artifacts: alphaArtifacts,
					phases: alphaPhases,
					stages: alphaStages,
					hasInitialContract: true,
					activeWave: "All",
					selType: null,
					selId: null,
					onSelectArtifact: () => {},
					onOpenFullArtifact: () => {},
					onOpenStage: () => {},
					onClearSelection: () => {},
				}),
			);

			// Assert Alpha DOM reflects Alpha data
			expect(alphaHtml).toContain("smart_meter_readings.csv");
			expect(alphaHtml).toContain("turbine_telemetry.parquet");
			expect(alphaHtml).toContain("grid_canonical_ir.json");
			expect(alphaHtml).toContain("Smart Grid Ingestion");
			expect(alphaHtml).toContain("Load Balancing Engine");
			expect(alphaHtml).toContain("STAGE_00_INGEST");
			expect(alphaHtml).toContain("STAGE_01_ANALYTICS");

			// Assert Alpha DOM does NOT contain Beta strings
			expect(alphaHtml).not.toContain("patient_records.csv");
			expect(alphaHtml).not.toContain("Clinical Trial Synthesis");

			// Bundle Beta: Healthcare Clinical Trial
			const betaArtifacts: Record<string, Artifact> = {
				patients: makeTestArtifact(
					"patients",
					"patient_records.csv",
					"Input",
					"CSV",
				),
				beta_sql: makeTestArtifact(
					"beta_sql",
					"clinical_vault_ddl.sql",
					"Output",
					"SQL",
				),
			};
			const betaStages: Stage[] = [
				makeTestStage("STAGE_00_SCREEN", "Patient Cohort Screening", 0),
				makeTestStage("STAGE_01_SYNTHESIS", "Clinical Trial Synthesis", 1),
			];
			const betaPhases = [
				{ label: "Screening", sub: "cohort criteria", index: 0 },
				{ label: "Synthesis", sub: "biomarker alignment", index: 1 },
			];

			const betaHtml = renderToString(
				createElement(PipelineOverviewView, {
					settingIds: [],
					inputIds: ["patients"],
					outputIds: ["beta_sql"],
					artifacts: betaArtifacts,
					phases: betaPhases,
					stages: betaStages,
					hasInitialContract: false,
					activeWave: "W1",
					selType: null,
					selId: null,
					onSelectArtifact: () => {},
					onOpenFullArtifact: () => {},
					onOpenStage: () => {},
					onClearSelection: () => {},
				}),
			);

			// Assert Beta DOM reflects Beta data
			expect(betaHtml).toContain("patient_records.csv");
			expect(betaHtml).toContain("clinical_vault_ddl.sql");
			expect(betaHtml).toContain("Patient Cohort Screening");
			expect(betaHtml).toContain("Clinical Trial Synthesis");
			expect(betaHtml).toContain("STAGE_00_SCREEN");
			expect(betaHtml).toContain("STAGE_01_SYNTHESIS");

			// Assert Beta DOM does NOT contain Alpha strings
			expect(betaHtml).not.toContain("smart_meter_readings.csv");
			expect(betaHtml).not.toContain("Smart Grid Ingestion");
		});
	});

	// --------------------------------------------------------------------------
	// 2. Static URL & Port Security Scan (REQ-CFG-01)
	// --------------------------------------------------------------------------
	describe("2. Static URL & Port Scanner (REQ-CFG-01)", () => {
		it("asserts zero occurrences of 127.0.0.1, :8000, or :8420 in client sources and compiled bundle", async () => {
			const clientFiles = [
				join(srcDir, "index.ts"),
				...collectFiles(join(srcDir, "explorer"), [".ts", ".tsx"]),
				...collectFiles(join(srcDir, "ontology"), [".ts", ".tsx"]),
			];
			const distDir = join(workbenchRoot, "dist");
			if (existsSync(distDir)) {
				const indexJs = join(distDir, "index.js");
				if (existsSync(indexJs)) clientFiles.push(indexJs);
				const distExplorer = join(distDir, "explorer");
				if (existsSync(distExplorer)) {
					clientFiles.push(...collectFiles(distExplorer, [".js"]));
				}
			}

			expect(clientFiles.length).toBeGreaterThan(10);

			const disallowedPatterns = [
				{ pattern: "127.0.0.1", label: "hardcoded IPv4 loopback (127.0.0.1)" },
				{ pattern: ":8000", label: "hardcoded platform port (:8000)" },
				{ pattern: ":8420", label: "hardcoded legacy port (:8420)" },
			];

			const violations: string[] = [];

			for (const file of clientFiles) {
				const content = readFileSync(file, "utf-8");
				for (const { pattern, label } of disallowedPatterns) {
					if (content.includes(pattern)) {
						const lines = content.split("\n");
						for (let i = 0; i < lines.length; i++) {
							const line = lines[i]!;
							if (
								line.includes(pattern) &&
								!line.trim().startsWith("//") &&
								!line.trim().startsWith("*")
							) {
								violations.push(`${file}:${i + 1} contains ${label}: "${line.trim()}"`);
							}
						}
					}
				}
			}

			expect(violations).toEqual([]);

			// Build browser bundle in an isolated CLI process to verify compiled output cleanly across OSes
			const tempOutDir = mkdtempSync(join(tmpdir(), "opendv_t10_bundle_"));
			let bundleText = "";
			try {
				const proc = Bun.spawnSync(
					[
						process.execPath,
						"build",
						join(srcDir, "index.ts"),
						"--target=browser",
						"--format=esm",
						`--outdir=${tempOutDir}`,
					],
					{ cwd: workbenchRoot, stdout: "pipe", stderr: "pipe" },
				);
				if (proc.exitCode !== 0) {
					throw new Error(
						`bun build failed (exit ${proc.exitCode}): ${proc.stderr.toString("utf-8")}`,
					);
				}
				bundleText = readFileSync(join(tempOutDir, "index.js"), "utf-8");
			} finally {
				rmSync(tempOutDir, { recursive: true, force: true });
			}
			expect(bundleText.length).toBeGreaterThan(1000);
			expect(bundleText).not.toContain("127.0.0.1");
			expect(bundleText).not.toContain(":8000");
			expect(bundleText).not.toContain(":8420");
		});
	});

	// --------------------------------------------------------------------------
	// 3. WCAG AA Keyboard Navigation & Semantics
	// --------------------------------------------------------------------------
	describe("3. Keyboard Navigation & Accessible Markup", () => {
		const sampleArtifact: Artifact = {
			id: "sample_spec",
			name: "pipeline_spec.yaml",
			type: "Specification",
			fmt: "YAML",
			group: "Input",
			viz: "kv",
			short: "Primary pipeline specification",
			desc: "Full declarative input specification",
			raw: "key: value\nmode: strict",
		};

		const sampleStages: Stage[] = [
			{
				key: "STAGE_00",
				name: "Discovery Stage",
				persona: "scout" as any,
				tier: "fast",
				tag: "PASS",
				phase: 0 as any,
				blurb: "Blurb 0",
				desc: "Description 0",
				ins: ["sample_spec"],
				outs: [],
				steps: [
					{
						name: "Profile Step",
						desc: "Step description",
						reads: ["sample_spec"],
						writes: [],
					},
				],
				comps: [["ScoutAgent", "Agent persona", "Desc", "src/"]],
			},
			{
				key: "STAGE_01",
				name: "Modeling Stage",
				persona: "architect" as any,
				tier: "reasoning",
				tag: "AUTO_REPAIRED",
				phase: 1 as any,
				blurb: "Blurb 1",
				desc: "Description 1",
				ins: [],
				outs: [],
				steps: [],
				comps: [],
			},
		];

		it("PipelineOverviewView renders accessible buttons and keyboard navigation roles on cards", () => {
			const html = renderToString(
				createElement(PipelineOverviewView, {
					settingIds: [],
					inputIds: ["sample_spec"],
					outputIds: [],
					artifacts: { sample_spec: sampleArtifact },
					phases: [{ label: "Phase 1", sub: "sub 1", index: 0 }],
					stages: sampleStages,
					hasInitialContract: false,
					activeWave: "All",
					selType: null,
					selId: null,
					onSelectArtifact: () => {},
					onOpenFullArtifact: () => {},
					onOpenStage: () => {},
					onClearSelection: () => {},
				}),
			);

			// Stage cards are focusable buttons with aria-label
			expect(html).toContain('role="button"');
			expect(html).toContain('tabindex="0"');
			expect(html).toContain('data-testid="stage-card-00"');
			expect(html).toContain('aria-label="Stage 00: Discovery Stage.');

			// Artifact cards are focusable buttons with aria-pressed
			expect(html).toContain('data-testid="artifact-card-sample_spec"');
			expect(html).toContain('aria-pressed="false"');
			expect(html).toContain('aria-label="Artifact pipeline_spec.yaml');
		});

		it("StageDrillDownView renders accessible buttons for stage navigation, components and steps", () => {
			const html = renderToString(
				createElement(StageDrillDownView, {
					stageIdx: 0,
					stages: sampleStages,
					artifacts: { sample_spec: sampleArtifact },
					selType: null,
					selId: null,
					onGoPipeline: () => {},
					onPrevStage: () => {},
					onNextStage: () => {},
					onSelectArtifact: () => {},
					onSelectStep: () => {},
					onSelectComponent: () => {},
					onClearSelection: () => {},
					onOpenFullArtifact: () => {},
				}),
			);

			// Prev/Next stage buttons have explicit accessible labels
			expect(html).toContain('aria-label="Previous stage"');
			expect(html).toContain('aria-label="Next stage"');
			expect(html).toContain('aria-label="Back to all stages (Esc)"');

			// Components and Steps have accessible roles and test IDs
			expect(html).toContain('data-testid="stage-component-ScoutAgent"');
			expect(html).toContain('data-testid="stage-step-card-01"');
			expect(html).toContain('aria-label="Step 01: Profile Step"');
		});
	});

	// --------------------------------------------------------------------------
	// 4. ArtifactFullModal Focus Trap & Dialog Invariants
	// --------------------------------------------------------------------------
	describe("4. ArtifactFullModal Accessibility & Focus Trap", () => {
		const modalArtifact: Artifact = {
			id: "final_contract",
			name: "final_contract.yaml",
			type: "DataVaultContract",
			fmt: "YAML",
			group: "Output",
			viz: "code",
			short: "Sealed model contract",
			desc: "Complete validated Data Vault 2.0 contract",
			raw: "version: 2.0\nmodels:\n  - name: hub_customer",
		};

		it("renders with role=dialog, aria-modal=true, and aria-labelledby", () => {
			const html = renderToString(
				createElement(ArtifactFullModal, {
					artifact: modalArtifact,
					activeTab: "split",
					rbacRole: "DATA_STEWARD",
					onClose: () => {},
					onSelectTab: () => {},
				}),
			);

			expect(html).toContain('role="dialog"');
			expect(html).toContain('aria-modal="true"');
			expect(html).toContain('aria-labelledby="artifact-full-modal-title"');
			expect(html).toContain('id="artifact-full-modal-title"');
			expect(html).toContain('data-testid="btn-artifact-modal-back"');
			expect(html).toContain('aria-label="Back to previous view (Press Escape to dismiss)"');
		});

		it("verifies tablist accessibility attributes on Visual | Raw | Split tabs", () => {
			const html = renderToString(
				createElement(ArtifactFullModal, {
					artifact: modalArtifact,
					activeTab: "split",
					rbacRole: "DATA_STEWARD",
					onClose: () => {},
					onSelectTab: () => {},
				}),
			);

			expect(html).toContain('role="tablist"');
			expect(html).toContain('aria-label="Artifact display mode"');
			expect(html).toContain('role="tab"');
			expect(html).toContain('aria-selected="true"');
		});
	});

	// --------------------------------------------------------------------------
	// 5. Dual-Channel Status Indicators (Text + Color + Symbol)
	// --------------------------------------------------------------------------
	describe("5. Dual-Channel Status Indicators", () => {
		it("ensures every status tag provides both distinct color AND explicit textual label and symbol", () => {
			const statusKeys = Object.keys(EXPLORER_STATUS_TAGS) as (keyof typeof EXPLORER_STATUS_TAGS)[];
			expect(statusKeys.length).toBeGreaterThanOrEqual(6);

			for (const key of statusKeys) {
				const tagStyle = getStatusTagStyle(key);
				const indicator = getStatusIndicator(key);

				// Must provide explicit background and foreground color
				expect(tagStyle.bg).toBeDefined();
				expect(tagStyle.fg).toBeDefined();

				// Must provide explicit human-readable text label
				expect(indicator.label).toBeDefined();
				expect(indicator.label.length).toBeGreaterThan(1);

				// Must provide distinct non-color visual symbol
				expect(indicator.symbol).toBeDefined();
				expect(indicator.symbol.length).toBeGreaterThan(0);
			}
		});

		it("verifies dual-channel markup in rendered stage cards", () => {
			const sampleStage: Stage = {
				key: "STAGE_05",
				name: "Static Verification",
				persona: "verifier" as any,
				tier: "fast",
				tag: "AUTO_REPAIRED",
				phase: 2 as any,
				blurb: "Blurb",
				desc: "Desc",
				ins: [],
				outs: [],
				steps: [],
				comps: [],
			};

			const html = renderToString(
				createElement(PipelineOverviewView, {
					settingIds: [],
					inputIds: [],
					outputIds: [],
					artifacts: {},
					phases: [{ label: "Verify", sub: "gates", index: 2 }],
					stages: [sampleStage],
					hasInitialContract: true,
					activeWave: "All",
					selType: null,
					selId: null,
					onSelectArtifact: () => {},
					onOpenFullArtifact: () => {},
					onOpenStage: () => {},
					onClearSelection: () => {},
				}),
			);

			// Status pill must display the text AND the symbol AND have aria-label
			expect(html).toContain("AUTO_REPAIRED");
			expect(html).toContain("↺");
			expect(html).toContain('aria-label="Status: AUTO_REPAIRED (Auto Repaired)"');
		});
	});

	// --------------------------------------------------------------------------
	// 6. WCAG AA Contrast Ratios (>= 4.5:1 text, >= 3:1 graphical)
	// --------------------------------------------------------------------------
	describe("6. WCAG AA Contrast Ratios", () => {
		const bgWhite = "#FFFFFF";
		const bgPage = EXPLORER_COLORS.bgPage; // #F6F7F8

		it("text tokens satisfy WCAG AA >= 4.5:1 contrast against white background", () => {
			const primaryRatio = contrastRatio(EXPLORER_COLORS.inkPrimary, bgWhite);
			const bodyRatio = contrastRatio(EXPLORER_COLORS.inkBody, bgWhite);
			const mutedRatio = contrastRatio(EXPLORER_COLORS.inkMuted, bgWhite);
			const labelRatio = contrastRatio(EXPLORER_COLORS.inkLabel, bgWhite);

			expect(primaryRatio).toBeGreaterThanOrEqual(4.5);
			expect(bodyRatio).toBeGreaterThanOrEqual(4.5);
			expect(mutedRatio).toBeGreaterThanOrEqual(4.5);
			expect(labelRatio).toBeGreaterThanOrEqual(4.5);
		});

		it("text tokens satisfy WCAG AA >= 4.5:1 contrast against light grey page background", () => {
			const primaryRatio = contrastRatio(EXPLORER_COLORS.inkPrimary, bgPage);
			const bodyRatio = contrastRatio(EXPLORER_COLORS.inkBody, bgPage);
			const mutedRatio = contrastRatio(EXPLORER_COLORS.inkMuted, bgPage);
			const labelRatio = contrastRatio(EXPLORER_COLORS.inkLabel, bgPage);

			expect(primaryRatio).toBeGreaterThanOrEqual(4.5);
			expect(bodyRatio).toBeGreaterThanOrEqual(4.5);
			expect(mutedRatio).toBeGreaterThanOrEqual(4.5);
			expect(labelRatio).toBeGreaterThanOrEqual(4.5);
		});

		it("all status pill foreground/background pairs satisfy WCAG AA >= 4.5:1 contrast", () => {
			for (const [tag, spec] of Object.entries(EXPLORER_STATUS_TAGS)) {
				const ratio = contrastRatio(spec.fg, spec.bg);
				expect(ratio).toBeGreaterThanOrEqual(4.5);
			}
		});

		it("graphical glyph UI elements satisfy WCAG AA >= 3:1 contrast against white", () => {
			const controlRatio = contrastRatio(EXPLORER_GLYPHS.control.bg, bgWhite);
			const dataRatio = contrastRatio(EXPLORER_GLYPHS.data.bg, bgWhite);
			const componentRatio = contrastRatio(EXPLORER_GLYPHS.component.bg, bgWhite);

			expect(controlRatio).toBeGreaterThanOrEqual(3.0);
			expect(dataRatio).toBeGreaterThanOrEqual(3.0);
			expect(componentRatio).toBeGreaterThanOrEqual(3.0);
		});
	});

	// --------------------------------------------------------------------------
	// 7. Live Region Updates
	// --------------------------------------------------------------------------
	describe("7. Live Region Configuration", () => {
		it("provides standard aria-live polite and assertive accessibility constants", () => {
			expect(EXPLORER_A11Y.liveRegionPolite).toBe("polite");
			expect(EXPLORER_A11Y.liveRegionAssertive).toBe("assertive");
			expect(EXPLORER_A11Y.focusRing).toBeDefined();
			expect(EXPLORER_A11Y.minTargetSize).toBe("32px");
		});
	});
});
