/**
 * `@opendv/web-workbench`
 * Modern 3-Level Pipeline Explorer Web Workbench (Pipeline -> Stage -> Artifact).
 * Legacy 12-View Shell excised per Architecture Plan v3.9 Decision D3 (#109, #110).
 */

import { PROTOCOL_VERSION } from "@opendv/protocol-ts";
import { type ReactElement, createElement } from "react";
import { type Root, createRoot } from "react-dom/client";

export function getWorkbenchVersion(): string {
	return `Open-DV Web Workbench (Protocol ${PROTOCOL_VERSION})`;
}

/**
 * OpenDVWorkbenchExplorer component.
 * Clean Explorer entrypoint; PR #60 is still open and full 3-level pipeline explorer integration lands in Phase P9 (#114).
 */
export function OpenDVWorkbenchExplorer(): ReactElement {
	return createElement(
		"main",
		{
			className: "opendv-workbench-explorer",
			"data-testid": "workbench-explorer-root",
		},
		createElement(
			"header",
			{ className: "explorer-header" },
			createElement("h1", null, "Open-DV Workbench Explorer"),
			createElement("p", null, getWorkbenchVersion()),
		),
		createElement(
			"section",
			{ className: "explorer-content" },
			createElement(
				"div",
				{ className: "explorer-placeholder-banner" },
				"3-Level Pipeline Explorer (Pipeline Overview, Stage Drill-Down, Artifact Inspector)",
			),
		),
	);
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
