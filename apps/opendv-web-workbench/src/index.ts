/**
 * `@opendv/web-workbench`
 * Modern 3-Level Pipeline Explorer Web Workbench (Pipeline -> Stage -> Artifact).
 * Legacy 12-View Shell excised per Architecture Plan v3.9 Decision D3 (#109, #110).
 */

import { PROTOCOL_VERSION } from "@opendv/protocol-ts";
import { Suspense, createElement, lazy } from "react";
import { type Root, createRoot } from "react-dom/client";
import { OpenDVWorkbenchExplorer } from "./explorer/OpenDVWorkbenchExplorer";
import { isOntologyRoute } from "./ontology/route-utils";

const LazyOntologyStudioApp = lazy(() =>
	import("./ontology/OntologyStudioApp").then((mod) => ({
		default: mod.OntologyStudioApp,
	})),
);

export { isOntologyRoute };

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
	const element = isOntologyRoute()
		? createElement(
				Suspense,
				{ fallback: createElement("div", { className: "studio-loading" }, "Loading Ontology Studio...") },
				createElement(LazyOntologyStudioApp),
			)
		: createElement(OpenDVWorkbenchExplorer);
	root.render(element);
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

// Ontology Construction Studio Workbench (/ontology) (Task 5D.1 - 5D.6)
export {
	studioStore,
	useStudioStore,
	createStudioStore,
	type StudioStore,
	type StudioStoreState,
} from "./ontology/store/studio-store";
export type * from "./ontology/store/types";
export {
	OntologyStudioApp,
	type OntologyStudioAppProps,
} from "./ontology/OntologyStudioApp";
