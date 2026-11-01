/**
 * Registration helper for Workbench Views Part 1 (WB-00 through WB-05) and Co-Located Gate Cards
 * (`register-wb00-wb05.ts`) (`docs/10` §3.3 & §4, WP-11).
 */

import {
	type WorkbenchPluginRegistry,
	defaultWorkbenchPluginRegistry,
} from "../plugins/plugin-registry";
import type { WorkbenchViewPlugin } from "../plugins/plugin-types";
import {
	SixGateFunnelCard,
	VIZ_GATE_01_PLUGIN,
} from "./wb00/SixGateFunnelCard";
import { WB00CommandCenter } from "./wb00/WB00CommandCenter";
import {
	CounterfactualTrapDrawer,
	VIZ_GATE_02_PLUGIN,
} from "./wb01/CounterfactualTrapDrawer";
import { WB01DualGraphView } from "./wb01/WB01DualGraphView";
import { WB02EmpiricalProfiler } from "./wb02/WB02EmpiricalProfiler";
import {
	ThreeTierERFunnel,
	VIZ_GATE_06_PLUGIN,
} from "./wb03/ThreeTierERFunnel";
import { WB03UnstructuredView } from "./wb03/WB03UnstructuredView";
import {
	TriageLedgerTable,
	VIZ_GATE_05_PART_A_PLUGIN,
} from "./wb04/TriageLedgerTable";
import { WB04ColumnTriageView } from "./wb04/WB04ColumnTriageView";
import {
	BkccCollisionShieldDrawer,
	VIZ_GATE_04_PLUGIN,
} from "./wb05/BkccCollisionShieldDrawer";
import { WB05ELMTopologyView } from "./wb05/WB05ELMTopologyView";

export {
	VIZ_GATE_01_PLUGIN,
	VIZ_GATE_02_PLUGIN,
	VIZ_GATE_06_PLUGIN,
	VIZ_GATE_05_PART_A_PLUGIN,
	VIZ_GATE_04_PLUGIN,
};

export const wb00ViewPlugin: WorkbenchViewPlugin = {
	id: "WB-00",
	title: "Master Pipeline Command Center & 6-Gate Scoreboard",
	shortTitle: "Command Center",
	stageId: "STAGE_00_ORCHESTRATION",
	sceneNumber: 1,
	iconName: "activity",
	component: WB00CommandCenter,
	gateCards: [VIZ_GATE_01_PLUGIN],
};

export const wb01ViewPlugin: WorkbenchViewPlugin = {
	id: "WB-01",
	title: "Dual-Graph Grounding & Anti-Pattern Counterfactual Inspector",
	shortTitle: "Dual-Graph Grounding",
	stageId: "STAGE_00_ORCHESTRATION",
	sceneNumber: 1,
	iconName: "git-branch",
	component: WB01DualGraphView,
	gateCards: [VIZ_GATE_02_PLUGIN],
};

export const wb02ViewPlugin: WorkbenchViewPlugin = {
	id: "WB-02",
	title: "Empirical Column Profiler & Candidate Discovery Matrix",
	shortTitle: "Empirical Profiler",
	stageId: "STAGE_01_SCOUT_SOURCE",
	sceneNumber: 1,
	iconName: "table",
	component: WB02EmpiricalProfiler,
};

export const wb03ViewPlugin: WorkbenchViewPlugin = {
	id: "WB-03",
	title: "Unstructured PDF Span-Grounding & 3-Tier ER Inspector",
	shortTitle: "Unstructured PDF & ER",
	stageId: "STAGE_02_SCOUT_UNSTRUCTURED",
	sceneNumber: 2,
	iconName: "file-text",
	component: WB03UnstructuredView,
	gateCards: [VIZ_GATE_06_PLUGIN],
};

export const wb04ViewPlugin: WorkbenchViewPlugin = {
	id: "WB-04",
	title: "Pass 1 Exhaustive Column Triage Sankey & Surrogate Sink",
	shortTitle: "Column Triage Sankey",
	stageId: "STAGE_03_SHIP_VAULT_PASS1",
	sceneNumber: 3,
	iconName: "filter",
	component: WB04ColumnTriageView,
	gateCards: [VIZ_GATE_05_PART_A_PLUGIN],
};

export const wb05ViewPlugin: WorkbenchViewPlugin = {
	id: "WB-05",
	title: "Pass 2/3 ELM Topology & 3-Tier BKCC Collision Shield",
	shortTitle: "ELM Topology & BKCC",
	stageId: "STAGE_04_SHIP_VAULT_PASS2",
	sceneNumber: 4,
	iconName: "shield",
	component: WB05ELMTopologyView,
	gateCards: [VIZ_GATE_04_PLUGIN],
};

export const ALL_WB00_TO_WB05_PLUGINS: readonly WorkbenchViewPlugin[] = [
	wb00ViewPlugin,
	wb01ViewPlugin,
	wb02ViewPlugin,
	wb03ViewPlugin,
	wb04ViewPlugin,
	wb05ViewPlugin,
];

/**
 * Register WB-00 through WB-05 and their co-located gate cards into a WorkbenchPluginRegistry.
 */
export function registerWB00toWB05(
	registry: WorkbenchPluginRegistry = defaultWorkbenchPluginRegistry,
): void {
	for (const plugin of ALL_WB00_TO_WB05_PLUGINS) {
		registry.registerView(plugin);
	}
}

// Auto-register with default registry
registerWB00toWB05(defaultWorkbenchPluginRegistry);
