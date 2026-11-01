/**
 * Registration helper for Workbench Views Part 2 (WB-06 through WB-11) and Co-Located Gate Cards
 * (`register-wb06-wb11.ts`) (`docs/10` §3.3 & §4, WP-12).
 */

import {
	type WorkbenchPluginRegistry,
	defaultWorkbenchPluginRegistry,
} from "../plugins/plugin-registry";
import type { WorkbenchViewPlugin } from "../plugins/plugin-types";
import { registerWB00toWB05 } from "./register-wb00-wb05";
import { VIZ_GATE_03_PLUGIN } from "./wb06/GrainAdjacencyMatrix";
import { WB06TopologicalDAGView } from "./wb06/WB06TopologicalDAGView";
import { WB07LinkSplitterYagniView } from "./wb07/WB07LinkSplitterYagniView";
import { VIZ_GATE_05_PART_B_PLUGIN } from "./wb07/WriteAmplificationGauge";
import {
	VIZ_GATE_07_PLUGIN,
	WB08SelfHealingWorkbench,
} from "./wb08/WB08SelfHealingWorkbench";
import { WB09SchemaDriftRadar } from "./wb09/WB09SchemaDriftRadar";
import {
	VIZ_GATE_08_PLUGIN,
	WB10DuckDBSandboxView,
} from "./wb10/WB10DuckDBSandboxView";
import { WB11RosettaStoneView } from "./wb11/WB11RosettaStoneView";

export {
	VIZ_GATE_03_PLUGIN,
	VIZ_GATE_05_PART_B_PLUGIN,
	VIZ_GATE_07_PLUGIN,
	VIZ_GATE_08_PLUGIN,
};

export const wb06ViewPlugin: WorkbenchViewPlugin = {
	id: "WB-06",
	title: "Gate 3 Topological DAG & Grain Matrix Inspector",
	shortTitle: "Topological DAG & Grain",
	stageId: "STAGE_05_AXI_DV_STATIC_GATES",
	sceneNumber: 5,
	iconName: "share-2",
	component: WB06TopologicalDAGView,
	gateCards: [VIZ_GATE_03_PLUGIN],
};

export const wb07ViewPlugin: WorkbenchViewPlugin = {
	id: "WB-07",
	title: "Nullable-FK Link Splitter & Gate 4 YAGNI Cost Guard",
	shortTitle: "Link Splitter & YAGNI",
	stageId: "STAGE_05_AXI_DV_STATIC_GATES",
	sceneNumber: 5,
	iconName: "scissors",
	component: WB07LinkSplitterYagniView,
	gateCards: [VIZ_GATE_05_PART_B_PLUGIN],
};

export const wb08ViewPlugin: WorkbenchViewPlugin = {
	id: "WB-08",
	title: "Closed-Loop Self-Healing Diff & TOON Diagnostic Workbench",
	shortTitle: "Self-Healing & TOON",
	stageId: "STAGE_05_AXI_DV_STATIC_GATES",
	sceneNumber: 5,
	iconName: "refresh-cw",
	component: WB08SelfHealingWorkbench,
	gateCards: [VIZ_GATE_07_PLUGIN],
};

export const wb09ViewPlugin: WorkbenchViewPlugin = {
	id: "WB-09",
	title: "Intra-Source Schema Drift Radar (S₁(t) → S₁(t+1))",
	shortTitle: "Schema Drift Radar",
	stageId: "STAGE_01_SCOUT_SOURCE",
	sceneNumber: 2,
	iconName: "radar",
	component: WB09SchemaDriftRadar,
};

export const wb10ViewPlugin: WorkbenchViewPlugin = {
	id: "WB-10",
	title: "Gate 5 Dynamic DuckDB Shadow Sandbox & Evolution Matrix",
	shortTitle: "DuckDB Shadow Sandbox",
	stageId: "STAGE_06_AXI_DV_DYNAMIC_SANDBOX",
	sceneNumber: 6,
	iconName: "database",
	component: WB10DuckDBSandboxView,
	gateCards: [VIZ_GATE_08_PLUGIN],
};

export const wb11ViewPlugin: WorkbenchViewPlugin = {
	id: "WB-11",
	title: "Pre-Hash Stepper (Algorithm A.5) & 4-Zone SQL Rosetta Stone",
	shortTitle: "Pre-Hash & 4-Zone SQL",
	stageId: "STAGE_07_DETERMINISTIC_COMPILER",
	sceneNumber: 7,
	iconName: "code",
	component: WB11RosettaStoneView,
};

export const ALL_WB06_TO_WB11_PLUGINS: readonly WorkbenchViewPlugin[] = [
	wb06ViewPlugin,
	wb07ViewPlugin,
	wb08ViewPlugin,
	wb09ViewPlugin,
	wb10ViewPlugin,
	wb11ViewPlugin,
];

/**
 * Register WB-06 through WB-11 and their co-located gate cards into a WorkbenchPluginRegistry.
 */
export function registerWb06ToWb11Views(
	registry: WorkbenchPluginRegistry = defaultWorkbenchPluginRegistry,
): void {
	for (const plugin of ALL_WB06_TO_WB11_PLUGINS) {
		registry.registerView(plugin);
	}
}

export const registerWB06toWB11 = registerWb06ToWb11Views;

/**
 * Register all 12 workbench views (WB-00 through WB-11) into a WorkbenchPluginRegistry.
 */
export function registerAllWorkbenchViews(
	registry: WorkbenchPluginRegistry = defaultWorkbenchPluginRegistry,
): void {
	registerWB00toWB05(registry);
	registerWb06ToWb11Views(registry);
}

// Auto-register with default registry at startup
registerWb06ToWb11Views(defaultWorkbenchPluginRegistry);
