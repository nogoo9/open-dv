/**
 * `@opendv/web-workbench`
 * Modern 3-Level Pipeline Explorer Web Workbench (Pipeline -> Stage -> Artifact).
 * Legacy 12-View Shell excised per Architecture Plan v3.9 Decision D3 (#109, #110).
 */

import { PROTOCOL_VERSION } from "@opendv/protocol-ts";
import { createElement } from "react";
import { type Root, createRoot } from "react-dom/client";
import { OpenDVWorkbenchExplorer } from "./explorer/OpenDVWorkbenchExplorer";

export function getWorkbenchVersion(): string {
	return `Open-DV Web Workbench (Protocol ${PROTOCOL_VERSION})`;
}

export function mountWorkbenchApp(container?: HTMLElement | null): Root | null {
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
	const root = createRoot(target);
	root.render(createElement(OpenDVWorkbenchExplorer));
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

// 3-Level Pipeline Explorer Workbench (Pipeline -> Stage -> Artifact) (HANDOFF.md)
export * from "./explorer/design-tokens";
export * from "./explorer/explorer-store";
export * from "./explorer/ArtifactVisualizers";
export * from "./explorer/ArtifactFullModal";
export * from "./explorer/InspectorPanel";
export * from "./explorer/PipelineOverviewView";
export * from "./explorer/StageDrillDownView";
export * from "./explorer/render-text-snapshot";
export {
	OpenDVWorkbenchExplorer,
	type OpenDVWorkbenchExplorerProps,
} from "./explorer/OpenDVWorkbenchExplorer";
