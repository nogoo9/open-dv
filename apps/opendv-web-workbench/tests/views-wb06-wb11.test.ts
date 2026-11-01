/**
 * Behavioral Unit Tests for Workbench Views Part 2 (WB-06 through WB-11) (`tests/views-wb06-wb11.test.ts`)
 * Verifies 12-view plugin registration (WB-00 through WB-11), Gate 3 Topological DAG & Grain Matrix (WB-06),
 * Algorithm A.4 Link Splitter & Gate 4 YAGNI Cost Guard (WB-07), Closed-Loop Self-Healing & @toon_v1 (WB-08),
 * Algorithm A.9a 5-Category Schema Drift Radar (WB-09), Gate 5 DuckDB Shadow Sandbox & Bi-Temporal Timeline (WB-10),
 * and Algorithm A.5 Pre-Hash Stepper & 4-Zone SQL Rosetta Stone (WB-11).
 */

import { beforeEach, describe, expect, it } from "bun:test";
import {
	ALL_WORKBENCH_VIEW_IDS,
	PE_CANONICAL_HASH_PILLS,
	PE_DEMO_EVENTS,
	type StageId,
	type VaultObservabilityEventDTO,
	type WorkbenchViewId,
	computeDv2LengthPrefixedSha256,
	computeDv2PrehashString,
} from "@opendv/protocol-ts";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { WorkbenchShell } from "../src/components/shell/WorkbenchShell";
import * as workbenchEntrypoint from "../src/index";
import { WorkbenchPluginRegistry } from "../src/plugins/plugin-registry";
import { useWorkbenchStore } from "../src/store/workbench-store";
import {
	ALL_WB06_TO_WB11_PLUGINS,
	VIZ_GATE_03_PLUGIN,
	VIZ_GATE_05_PART_B_PLUGIN,
	VIZ_GATE_07_PLUGIN,
	VIZ_GATE_08_PLUGIN,
	registerAllWorkbenchViews,
	registerWb06ToWb11Views,
	wb06ViewPlugin,
	wb07ViewPlugin,
	wb08ViewPlugin,
	wb09ViewPlugin,
	wb10ViewPlugin,
	wb11ViewPlugin,
} from "../src/views/register-wb06-wb11";
import { GrainAdjacencyMatrix } from "../src/views/wb06/GrainAdjacencyMatrix";
import { WB06TopologicalDAGView } from "../src/views/wb06/WB06TopologicalDAGView";
import { WB07LinkSplitterYagniView } from "../src/views/wb07/WB07LinkSplitterYagniView";
import { WriteAmplificationGauge } from "../src/views/wb07/WriteAmplificationGauge";
import { AttemptDiffViewer } from "../src/views/wb08/AttemptDiffViewer";
import { ToonTraceHighlighter } from "../src/views/wb08/ToonTraceHighlighter";
import { WB08SelfHealingWorkbench } from "../src/views/wb08/WB08SelfHealingWorkbench";
import { DriftDeltaGrid } from "../src/views/wb09/DriftDeltaGrid";
import { WB09SchemaDriftRadar } from "../src/views/wb09/WB09SchemaDriftRadar";
import { AdversarialBatchGrid } from "../src/views/wb10/AdversarialBatchGrid";
import { BiTemporalTimeline } from "../src/views/wb10/BiTemporalTimeline";
import { WB10DuckDBSandboxView } from "../src/views/wb10/WB10DuckDBSandboxView";
import { FourZoneSQLViewer } from "../src/views/wb11/FourZoneSQLViewer";
import {
	DEFAULT_PREHASH_PRESETS,
	PreHashStepper,
} from "../src/views/wb11/PreHashStepper";
import { WB11RosettaStoneView } from "../src/views/wb11/WB11RosettaStoneView";

describe("Work Package WP-12: Workbench Views Part 3 (WB-06 through WB-11 & Full 12-View Registry)", () => {
	let registry: WorkbenchPluginRegistry;

	const getDemoEvent = (
		viewId: WorkbenchViewId,
	): VaultObservabilityEventDTO => {
		const ev = PE_DEMO_EVENTS.find((e) => e.workbench_view_id === viewId);
		if (!ev) {
			throw new Error(
				`Demo event for view ${viewId} not found in PE_DEMO_EVENTS`,
			);
		}
		return ev;
	};

	beforeEach(() => {
		registry = new WorkbenchPluginRegistry();
		registerAllWorkbenchViews(registry);
	});

	describe("1. 12-View Plugin Registration (WB-00 through WB-11) & Co-Located Gate Cards", () => {
		it("registers all 12 views (WB-00 through WB-11) simultaneously in canonical order", () => {
			expect(ALL_WB06_TO_WB11_PLUGINS).toHaveLength(6);
			expect(registry.listViews().map((v) => v.id)).toEqual([
				...ALL_WORKBENCH_VIEW_IDS,
			]);

			const expectedWb06ToWb11: Array<{
				id: WorkbenchViewId;
				stageId: StageId;
				sceneNumber: number;
			}> = [
				{
					id: "WB-06",
					stageId: "STAGE_05_AXI_DV_STATIC_GATES",
					sceneNumber: 5,
				},
				{
					id: "WB-07",
					stageId: "STAGE_05_AXI_DV_STATIC_GATES",
					sceneNumber: 5,
				},
				{
					id: "WB-08",
					stageId: "STAGE_05_AXI_DV_STATIC_GATES",
					sceneNumber: 5,
				},
				{
					id: "WB-09",
					stageId: "STAGE_01_SCOUT_SOURCE",
					sceneNumber: 2,
				},
				{
					id: "WB-10",
					stageId: "STAGE_06_AXI_DV_DYNAMIC_SANDBOX",
					sceneNumber: 6,
				},
				{
					id: "WB-11",
					stageId: "STAGE_07_DETERMINISTIC_COMPILER",
					sceneNumber: 7,
				},
			];

			for (const expected of expectedWb06ToWb11) {
				const plugin = registry.getView(expected.id);
				expect(plugin).toBeDefined();
				expect(plugin?.stageId).toBe(expected.stageId);
				expect(plugin?.sceneNumber).toBe(expected.sceneNumber);
				expect(typeof plugin?.component).toBe("function");
			}
		});

		it("registers co-located gate cards for WB-06, WB-07, WB-08, and WB-10 without colliding with WB-04", () => {
			expect(registry.getGateCard("VIZ-GATE-03")).toBeDefined();
			expect(registry.getGateCard("VIZ-GATE-05")).toBeDefined();
			expect(registry.getGateCard("VIZ-GATE-05-PART-B")).toBeDefined();
			expect(registry.getGateCard("VIZ-GATE-07")).toBeDefined();
			expect(registry.getGateCard("VIZ-GATE-08")).toBeDefined();

			expect(
				registry
					.findGateCardsForEvent(getDemoEvent("WB-06"))
					.map((c) => c.gateId),
			).toEqual(["VIZ-GATE-03"]);
			expect(
				registry
					.findGateCardsForEvent(getDemoEvent("WB-07"))
					.map((c) => c.gateId),
			).toEqual(["VIZ-GATE-05-PART-B"]);
			expect(
				registry
					.findGateCardsForEvent(getDemoEvent("WB-08"))
					.map((c) => c.gateId),
			).toEqual(["VIZ-GATE-07"]);
			expect(
				registry
					.findGateCardsForEvent(getDemoEvent("WB-10"))
					.map((c) => c.gateId),
			).toEqual(["VIZ-GATE-08"]);

			for (const [gatePlugin, viewId] of [
				[VIZ_GATE_03_PLUGIN, "WB-06"],
				[VIZ_GATE_05_PART_B_PLUGIN, "WB-07"],
				[VIZ_GATE_07_PLUGIN, "WB-08"],
				[VIZ_GATE_08_PLUGIN, "WB-10"],
			] as const) {
				const ev = getDemoEvent(viewId);
				const collapsedHtml = renderToStaticMarkup(
					createElement(gatePlugin.component, {
						event: ev,
						isExpanded: false,
						onToggleExpand: () => {},
					}),
				);
				expect(collapsedHtml).toBe("");

				const expandedHtml = renderToStaticMarkup(
					createElement(gatePlugin.component, {
						event: ev,
						isExpanded: true,
						onToggleExpand: () => {},
					}),
				);
				expect(expandedHtml).toContain("Collapse");
			}
		});

		it("exports WB-06 through WB-11 components and registration helpers from package entrypoint", () => {
			expect(typeof workbenchEntrypoint.registerWb06ToWb11Views).toBe(
				"function",
			);
			expect(typeof workbenchEntrypoint.registerAllWorkbenchViews).toBe(
				"function",
			);
			expect(typeof workbenchEntrypoint.WB06TopologicalDAGView).toBe(
				"function",
			);
			expect(typeof workbenchEntrypoint.WB07LinkSplitterYagniView).toBe(
				"function",
			);
			expect(typeof workbenchEntrypoint.WB08SelfHealingWorkbench).toBe(
				"function",
			);
			expect(typeof workbenchEntrypoint.WB09SchemaDriftRadar).toBe("function");
			expect(typeof workbenchEntrypoint.WB10DuckDBSandboxView).toBe("function");
			expect(typeof workbenchEntrypoint.WB11RosettaStoneView).toBe("function");
			expect(wb06ViewPlugin.gateCards?.[0]).toBe(VIZ_GATE_03_PLUGIN);
			expect(wb07ViewPlugin.gateCards?.[0]).toBe(VIZ_GATE_05_PART_B_PLUGIN);
			expect(wb08ViewPlugin.gateCards?.[0]).toBe(VIZ_GATE_07_PLUGIN);
			expect(wb09ViewPlugin.gateCards).toBeUndefined();
			expect(wb10ViewPlugin.gateCards?.[0]).toBe(VIZ_GATE_08_PLUGIN);
			expect(wb11ViewPlugin.gateCards).toBeUndefined();
		});
	});

	describe("2. WB-06: Gate 3 Topological DAG & Grain Matrix Inspector", () => {
		it("renders GrainAdjacencyMatrix with 3x3 block matrix, DV-STR-001..014, and INV-01..06", () => {
			const event = getDemoEvent("WB-06");
			const html = renderToStaticMarkup(
				createElement(GrainAdjacencyMatrix, {
					event,
					isExpanded: true,
				}),
			);

			expect(html).toContain('data-testid="grain-adjacency-matrix"');
			expect(html).toContain("DV-STR-001");
			expect(html).toContain("DV-STR-014");
			expect(html).toContain("INV-01");
			expect(html).toContain("INV-06");
			expect(html).toContain("hub_fund");
			expect(html).toContain("lnk_lp_commitment_co_invest");
		});

		it("renders WB06TopologicalDAGView with Bipartite DAG degree proofs (outdeg(h)=0, Link arity >= 2, outdeg(s)=1)", () => {
			const event = getDemoEvent("WB-06");
			const html = renderToStaticMarkup(
				createElement(WB06TopologicalDAGView, {
					currentEvent: event,
					eventsForView: [event],
					onInspectProof: () => {},
				}),
			);

			expect(html).toContain('data-testid="wb06-topological-dag-view"');
			expect(html).toContain('data-testid="bipartite-dag-proof-canvas"');
			expect(html).toContain("outdeg(h) = 0");
			expect(html).toContain("outdeg_H(l) &gt;= 2");
			expect(html).toContain("outdeg(s) = 1");
		});
	});

	describe("3. WB-07: Nullable-FK Link Splitter & Gate 4 YAGNI Cost Guard", () => {
		it("renders WriteAmplificationGauge with W_amp = 4.20 MB/day < 50.0 MB/day and auto-fix: merge_satellites", () => {
			const event = getDemoEvent("WB-07");
			const html = renderToStaticMarkup(
				createElement(WriteAmplificationGauge, {
					event,
					isExpanded: true,
				}),
			);

			expect(html).toContain('data-testid="write-amplification-gauge"');
			expect(html).toContain("4.20 MB/day");
			expect(html).toContain("50.0 MB/day");
			expect(html).toContain("auto-fix: merge_satellites");
			expect(html).toContain("sat_lp_commitment_fx_high_freq");
		});

		it("renders WB07LinkSplitterYagniView showing Algorithm A.4 split (U_nn = 0.286 < 0.95 -> lnk_lp_commitment_co_invest)", () => {
			const event = getDemoEvent("WB-07");
			const html = renderToStaticMarkup(
				createElement(WB07LinkSplitterYagniView, {
					currentEvent: event,
					eventsForView: [event],
					onInspectProof: () => {},
				}),
			);

			expect(html).toContain('data-testid="wb07-link-splitter-yagni-view"');
			expect(html).toContain('data-testid="nullable-fk-link-splitter-pane"');
			expect(html).toContain("U_nn = 0.286");
			expect(html).toContain("co_invest_portco_lei");
			expect(html).toContain("lnk_lp_commitment_co_invest");
		});
	});

	describe("4. WB-08: Closed-Loop Self-Healing Diff & TOON Diagnostic Workbench", () => {
		it("renders ToonTraceHighlighter with @toon_v1 48-token payload and violations[2]{gate,rule,action,target,fix}", () => {
			const html = renderToStaticMarkup(createElement(ToonTraceHighlighter));

			expect(html).toContain('data-testid="toon-trace-highlighter"');
			expect(html).toContain("@toon_v1");
			expect(html).toContain("1,REJECT,48");
			expect(html).toContain("G1,DV-COL-012,REJECT,hub_limited_partner");
			expect(html).toContain("G4,ALG-A1,AUTO-FIX,sat_lp_comm_fx");
			expect(html).toContain('data-testid="toon-violation-DV-COL-012"');
			expect(html).toContain('data-testid="toon-violation-ALG-A1"');
		});

		it("renders AttemptDiffViewer with pre/post NoMistakeDataVault2ContractV5 YAML diff", () => {
			const html = renderToStaticMarkup(createElement(AttemptDiffViewer));

			expect(html).toContain('data-testid="attempt-diff-viewer"');
			expect(html).toContain("require_src_qualifier: false");
			expect(html).toContain("require_src_qualifier: true");
			expect(html).toContain("LEGACY_GP_ADMIN");
			expect(html).toContain("sat_lp_commitment_fx_high_freq");
		});

		it("renders WB08SelfHealingWorkbench with k=1 -> k=2 convergence state flow and 4-quadrant proof card", () => {
			const event = getDemoEvent("WB-08");
			const html = renderToStaticMarkup(
				createElement(WB08SelfHealingWorkbench, {
					currentEvent: event,
					eventsForView: [event],
					onInspectProof: () => {},
				}),
			);

			expect(html).toContain('data-testid="wb08-self-healing-view"');
			expect(html).toContain("CONVERGED · k = 1 → k = 2");
			expect(html).toContain('data-testid="wb08-counterfactual-proof-card"');
			expect(html).toContain("[HK: 5D15..0AE4 | GLOBAL:LP-101 CalPERS]");
			expect(html).toContain(
				"[HK: 6B7C..D8B9 | LEGACY_GP_ADMIN:LP-101 Bavarian Pension]",
			);
		});
	});

	describe("5. WB-09: Intra-Source Schema Drift Radar (S1(t) -> S1(t+1))", () => {
		it("renders DriftDeltaGrid with all 5 Algorithm A.9a drift categories and Levenshtein/Jaccard scores", () => {
			const html = renderToStaticMarkup(createElement(DriftDeltaGrid));

			expect(html).toContain('data-testid="drift-delta-grid"');
			expect(html).toContain("ADDITIVE");
			expect(html).toContain("SOFT_DEPRECATED");
			expect(html).toContain("RENAMED");
			expect(html).toContain("TYPE_WIDENED");
			expect(html).toContain("KEY_DRIFT");
			expect(html).toContain("ilpa_esg_rating");
			expect(html).toContain("deprecated_fax_no");
			expect(html).toContain("committed_amt → commitment_amount_usd");
			expect(html).toContain("sim=0.87 (&gt;=0.85) · J=0.994 (&gt;=0.90)");
			expect(html).toContain("VARCHAR(128) → VARCHAR(255)");
		});

		it("renders WB09SchemaDriftRadar with 5 radar spokes and zero broken historical hash_diffs", () => {
			const event = getDemoEvent("WB-09");
			const html = renderToStaticMarkup(
				createElement(WB09SchemaDriftRadar, {
					currentEvent: event,
					eventsForView: [event],
					onInspectProof: () => {},
				}),
			);

			expect(html).toContain('data-testid="wb09-schema-drift-radar-view"');
			expect(html).toContain('data-testid="wb09-zero-hashdiff-breakage-badge"');
			expect(html).toContain("0 BROKEN HASH_DIFFS");
		});
	});

	describe("6. WB-10: Gate 5 Dynamic DuckDB Shadow Sandbox & Evolution Matrix", () => {
		it("renders AdversarialBatchGrid with Batches 1–4 and DV-DYN-001 through DV-DYN-015", () => {
			const html = renderToStaticMarkup(createElement(AdversarialBatchGrid));

			expect(html).toContain('data-testid="adversarial-batch-grid"');
			expect(html).toContain('data-testid="sandbox-batch-card-1"');
			expect(html).toContain('data-testid="sandbox-batch-card-2"');
			expect(html).toContain('data-testid="sandbox-batch-card-3"');
			expect(html).toContain('data-testid="sandbox-batch-card-4"');
			expect(html).toContain("Δrows=0");
			for (let i = 1; i <= 15; i += 1) {
				const ruleId = `DV-DYN-${String(i).padStart(3, "0")}`;
				expect(html).toContain(`data-testid="dyn-rule-${ruleId}"`);
			}
		});

		it("renders BiTemporalTimeline with load_dts vs effective_dts and 12.80x -> 14.50x Q3 valuation restatement", () => {
			const html = renderToStaticMarkup(createElement(BiTemporalTimeline));

			expect(html).toContain('data-testid="bitemporal-timeline"');
			expect(html).toContain("529900T8BM49AURSDO55");
			expect(html).toContain("2026-10-15 12:00:00");
			expect(html).toContain("2026-11-15 23:59:59");
			expect(html).toContain("12.80x");
			expect(html).toContain("14.50x");
			expect(html).toContain("$216,280,000.00");
			expect(html).toContain("$245,000,000.00");
		});

		it("renders WB10DuckDBSandboxView integrating adversarial batches and bi-temporal timeline", () => {
			const event = getDemoEvent("WB-10");
			const html = renderToStaticMarkup(
				createElement(WB10DuckDBSandboxView, {
					currentEvent: event,
					eventsForView: [event],
					onInspectProof: () => {},
				}),
			);

			expect(html).toContain('data-testid="wb10-duckdb-sandbox-view"');
			expect(html).toContain(
				"PASS · BATCHES 1–4 · DV-DYN-001..015 · Δrows = 0",
			);
		});
	});

	describe("7. WB-11: Pre-Hash Stepper (Algorithm A.5) & 4-Zone SQL Rosetta Stone", () => {
		it("renders PreHashStepper with 6 canonicalization stages producing 6:GLOBAL||6:LP-101 and exact SHA-256 digests", async () => {
			const html = renderToStaticMarkup(createElement(PreHashStepper));

			expect(html).toContain('data-testid="prehash-stepper"');
			for (let stage = 1; stage <= 6; stage += 1) {
				expect(html).toContain(`data-testid="prehash-stage-${stage}"`);
			}
			expect(html).toContain("6:GLOBAL||6:LP-101");
			expect(html).toContain(
				"5D15EB10C8D7621D3167849C41C5F9CD9244FD79FB720CB17104D49E4B820AE4",
			);
			expect(html).toContain("[HK: 5D15..0AE4 | GLOBAL:LP-101 CalPERS]");

			for (const preset of DEFAULT_PREHASH_PRESETS) {
				expect(computeDv2PrehashString(preset.rawTokens)).toBe(
					preset.canonicalPreHashString,
				);
				expect(await computeDv2LengthPrefixedSha256(preset.rawTokens)).toBe(
					preset.sha256HexUpper,
				);
			}
			expect(DEFAULT_PREHASH_PRESETS[0]?.dualProjectionPill).toBe(
				PE_CANONICAL_HASH_PILLS.CALPERS_LP_101.pill_label,
			);
			expect(DEFAULT_PREHASH_PRESETS[1]?.dualProjectionPill).toBe(
				PE_CANONICAL_HASH_PILLS.BAVARIAN_PENSION_LP_101.pill_label,
			);
			expect(DEFAULT_PREHASH_PRESETS[2]?.dualProjectionPill).toBe(
				PE_CANONICAL_HASH_PILLS.FUND_IV_APEX_BO_IV.pill_label,
			);
		});

		it("renders FourZoneSQLViewer with Zones 0–4 SQL, DV-AST-001..006 badges, and Query D.10 / D.10b results", () => {
			const html = renderToStaticMarkup(createElement(FourZoneSQLViewer));

			expect(html).toContain('data-testid="four-zone-sql-viewer"');
			expect(html).toContain('data-testid="sql-zone-tab-ZONE_0"');
			expect(html).toContain('data-testid="sql-zone-tab-ZONE_1"');
			expect(html).toContain('data-testid="sql-zone-tab-ZONE_2"');
			expect(html).toContain('data-testid="sql-zone-tab-ZONE_3"');
			expect(html).toContain('data-testid="sql-zone-tab-ZONE_4"');
			for (let i = 1; i <= 6; i += 1) {
				const ruleId = `DV-AST-${String(i).padStart(3, "0")}`;
				expect(html).toContain(`data-testid="ast-badge-${ruleId}"`);
			}
			expect(html).toContain('data-testid="query-d10-results-card"');
			expect(html).toContain(
				PE_CANONICAL_HASH_PILLS.FUND_IV_APEX_BO_IV.pill_label,
			);
			expect(html).toContain("125.00 bps");
			expect(html).toContain("12.80x");
			expect(html).toContain("14.50x");
		});

		it("renders WB11RosettaStoneView integrating PreHashStepper and FourZoneSQLViewer", () => {
			const event = getDemoEvent("WB-11");
			const html = renderToStaticMarkup(
				createElement(WB11RosettaStoneView, {
					currentEvent: event,
					eventsForView: [event],
					onInspectProof: () => {},
				}),
			);

			expect(html).toContain('data-testid="wb11-rosetta-stone-view"');
			expect(html).toContain("PASS · ZONES 0–4 COMPILED · DV-AST-001..006");
		});
	});

	describe("8. Full 12-View WorkbenchShell Integration", () => {
		it("renders every registered view WB-06 through WB-11 inside WorkbenchShell without errors", () => {
			useWorkbenchStore.getState().replayAllInstant();

			const viewTestIds: Record<string, string> = {
				"WB-06": 'data-testid="wb06-topological-dag-view"',
				"WB-07": 'data-testid="wb07-link-splitter-yagni-view"',
				"WB-08": 'data-testid="wb08-self-healing-view"',
				"WB-09": 'data-testid="wb09-schema-drift-radar-view"',
				"WB-10": 'data-testid="wb10-duckdb-sandbox-view"',
				"WB-11": 'data-testid="wb11-rosetta-stone-view"',
			};

			for (const [viewId, expectedTestId] of Object.entries(viewTestIds)) {
				useWorkbenchStore.getState().setActiveView(viewId as WorkbenchViewId);
				const html = renderToStaticMarkup(
					createElement(WorkbenchShell, {
						pluginRegistry: registry,
					}),
				);
				expect(html).toContain(
					'data-testid="registered-view-plugin-container"',
				);
				expect(html).toContain(expectedTestId);
			}
		});
	});
});
