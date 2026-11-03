/**
 * `@opendv/web-workbench`
 * Decoupled 12-View Explainability & Observability Web Workbench Shell,
 * Semantic Theme Engine (`Obsidian Dark`, `Daylight Paper`, `High-Contrast Audit`),
 * Zustand State Store, Extensible Plugin Registry, and Workbench Views `WB-00`–`WB-11`
 * with Co-Located Gate Cards (`VIZ-GATE-01` through `VIZ-GATE-08`).
 */

import { PROTOCOL_VERSION } from "@opendv/protocol-ts";
import { createElement } from "react";
import { type Root, createRoot } from "react-dom/client";
import { WorkbenchShell } from "./components/shell/WorkbenchShell";
import { OpenDVWorkbenchExplorer } from "./explorer/OpenDVWorkbenchExplorer";
import { defaultWorkbenchPluginRegistry } from "./plugins/plugin-registry";
import { defaultThemeRegistry } from "./theme/theme-registry";
import { registerWB00toWB05 } from "./views/register-wb00-wb05";
import { registerWb06ToWb11Views } from "./views/register-wb06-wb11";

export function getWorkbenchVersion(): string {
	return `Open-DV Web Workbench (Protocol ${PROTOCOL_VERSION})`;
}

export function mountWorkbenchApp(container?: HTMLElement | null): Root | null {
	registerWB00toWB05(defaultWorkbenchPluginRegistry);
	const target =
		container !== undefined
			? container
			: typeof globalThis !== "undefined" &&
					"document" in globalThis &&
					typeof globalThis.document?.getElementById === "function"
				? globalThis.document.getElementById("root")
				: null;
	if (!target) {
		return null;
	}
	registerWb06ToWb11Views(defaultWorkbenchPluginRegistry);
	defaultThemeRegistry.initializeFromEnvironment();
	const useLegacyShell =
		target.getAttribute?.("data-shell-mode") === "legacy-12-view";
	const root = createRoot(target);
	root.render(
		createElement(useLegacyShell ? WorkbenchShell : OpenDVWorkbenchExplorer),
	);
	return root;
}

if (
	typeof globalThis !== "undefined" &&
	"document" in globalThis &&
	typeof globalThis.document?.getElementById === "function" &&
	globalThis.document.getElementById("root")
) {
	mountWorkbenchApp();
}

// Theme Engine & Accessible Theme Selector
export * from "./theme/theme-registry";
export * from "./theme/ThemeToggle";

// Zustand Workbench State Store
export * from "./store/workbench-store";

// Extensible View & Gate Card Plugin Registry
export * from "./plugins/plugin-types";
export * from "./plugins/plugin-registry";
export * from "./plugins/custom-esg-gate-plugin";

// Responsive 3-Layer Progressive Disclosure Shell Components
export * from "./components/shell/PlaybackControls";
export * from "./components/shell/Layer1StoryBanner";
export * from "./components/shell/SidebarNav";
export * from "./components/shell/Layer3ProofDrawer";
export * from "./components/shell/WorkbenchShell";

// Workbench Views Part 1 (WB-00 through WB-05) & Co-Located Gate Cards
export * from "./views/register-wb00-wb05";
export * from "./views/wb00/MultiAgentTimeline";
export * from "./views/wb00/SixGateFunnelCard";
export * from "./views/wb00/WB00CommandCenter";
export * from "./views/wb01/BipartiteMapCanvas";
export * from "./views/wb01/CounterfactualTrapDrawer";
export * from "./views/wb01/WB01DualGraphView";
export * from "./views/wb02/FourQuadrantScatterPlot";
export * from "./views/wb02/InclusionDependencyDAG";
export * from "./views/wb02/WB02EmpiricalProfiler";
export * from "./views/wb03/CaptainHoldDecisionModal";
export * from "./views/wb03/DocumentSpanHighlighter";
export * from "./views/wb03/ThreeTierERFunnel";
export * from "./views/wb03/WB03UnstructuredView";
export * from "./views/wb04/ColumnAccountingSankey";
export * from "./views/wb04/TriageLedgerTable";
export * from "./views/wb04/WB04ColumnTriageView";
export * from "./views/wb05/BkccCollisionShieldDrawer";
export * from "./views/wb05/TopologyCanvas";
export * from "./views/wb05/WB05ELMTopologyView";

// Workbench Views Part 2 (WB-06 through WB-11) & Co-Located Gate Cards
export * from "./views/register-wb06-wb11";
export * from "./views/wb06/GrainAdjacencyMatrix";
export * from "./views/wb06/WB06TopologicalDAGView";
export * from "./views/wb07/WriteAmplificationGauge";
export * from "./views/wb07/WB07LinkSplitterYagniView";
export * from "./views/wb08/AttemptDiffViewer";
export * from "./views/wb08/ToonTraceHighlighter";
export * from "./views/wb08/WB08SelfHealingWorkbench";
export * from "./views/wb09/DriftDeltaGrid";
export * from "./views/wb09/WB09SchemaDriftRadar";
export * from "./views/wb10/AdversarialBatchGrid";
export * from "./views/wb10/BiTemporalTimeline";
export * from "./views/wb10/WB10DuckDBSandboxView";
export * from "./views/wb11/PreHashStepper";
export * from "./views/wb11/FourZoneSQLViewer";
export * from "./views/wb11/WB11RosettaStoneView";

// 3-Level Pipeline Explorer Workbench (Pipeline -> Stage -> Artifact) (HANDOFF.md)
export * from "./explorer/design-tokens";
export * from "./explorer/explorer-store";
export * from "./explorer/ArtifactVisualizers";
export * from "./explorer/ArtifactFullModal";
export * from "./explorer/InspectorPanel";
export * from "./explorer/PipelineOverviewView";
export * from "./explorer/StageDrillDownView";
export * from "./explorer/OpenDVWorkbenchExplorer";
