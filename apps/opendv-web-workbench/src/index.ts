/**
 * `@opendv/web-workbench`
 * Decoupled 12-View Explainability & Observability Web Workbench Shell,
 * Semantic Theme Engine (`Obsidian Dark`, `Daylight Paper`, `High-Contrast Audit`),
 * Zustand State Store, and Extensible Plugin Registry.
 */

import { PROTOCOL_VERSION } from "@opendv/protocol-ts";
import { createElement } from "react";
import { type Root, createRoot } from "react-dom/client";
import { WorkbenchShell } from "./components/shell/WorkbenchShell";
import { defaultThemeRegistry } from "./theme/theme-registry";

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
	defaultThemeRegistry.initializeFromEnvironment();
	const root = createRoot(target);
	root.render(createElement(WorkbenchShell));
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
