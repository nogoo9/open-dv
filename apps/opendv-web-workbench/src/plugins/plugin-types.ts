/**
 * Extensibility Contract for the Open-DV 12-View Explainability Workbench (`docs/10` §3.3).
 *
 * Allows third-party domain packages and future Work Packages (`WP-11`, `WP-12`) to register
 * custom Workbench Views (`WB-00`–`WB-11`) and Gate Cards (`VIZ-GATE-*`) without modifying
 * core shell files.
 */

import type {
	StageId,
	VaultObservabilityEventDTO,
	VizId,
	WorkbenchViewId,
} from "@opendv/protocol-ts";
import type { ComponentType } from "react";

export interface ViewPluginProps {
	currentEvent: VaultObservabilityEventDTO | null;
	eventsForView: VaultObservabilityEventDTO[];
	onInspectProof: () => void;
}

export interface GateCardPluginProps {
	event: VaultObservabilityEventDTO;
	isExpanded: boolean;
	onToggleExpand: () => void;
}

export interface WorkbenchGateCardPlugin {
	gateId: VizId | string;
	title: string;
	ruleIds: string[];
	component: ComponentType<GateCardPluginProps>;
}

export interface WorkbenchViewPlugin {
	id: WorkbenchViewId;
	title: string;
	shortTitle: string;
	stageId: StageId;
	sceneNumber: number; // 1 to 7
	iconName: string;
	component: ComponentType<ViewPluginProps>;
	gateCards?: WorkbenchGateCardPlugin[] | undefined;
}
