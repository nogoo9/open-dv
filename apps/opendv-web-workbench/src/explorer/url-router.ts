/**
 * URL Routing & Deep-Linking Serializer for Workbench Explorer (User Directive R9 Modularity).
 */

import type { WaveFilterId } from "@opendv/protocol-ts";
import type { ArtifactFullTab, ExplorerViewLevel } from "./types";

export interface ExplorerUrlState {
	view: ExplorerViewLevel;
	stageIdx: number;
	fullId: string | null;
	fullTab: ArtifactFullTab;
	activeWave: WaveFilterId;
}

export const VALID_WAVES: readonly WaveFilterId[] = [
	"all",
	"W0",
	"W1",
	"W2",
	"W3",
] as const;

export const VALID_TABS: readonly ArtifactFullTab[] = [
	"viz",
	"raw",
	"split",
] as const;

/**
 * Parse deep-linked URL pathname and query string into explorer state.
 */
export function parseExplorerUrlState(
	pathname = "/",
	search = "",
	validArtifactIds?: ReadonlySet<string>,
): ExplorerUrlState {
	let view: ExplorerViewLevel = "pipeline";
	let stageIdx = 5;

	const stageMatch = pathname.match(/\/stages\/0?([0-7])(?:\/|$)/);
	if (stageMatch?.[1] !== undefined) {
		view = "stage";
		stageIdx = Math.max(0, Math.min(7, Number.parseInt(stageMatch[1], 10)));
	}

	const params = new URLSearchParams(
		search.startsWith("?") ? search.slice(1) : search,
	);
	const rawArtifact = params.get("artifact")?.trim() ?? null;
	const fullId =
		rawArtifact && (!validArtifactIds || validArtifactIds.has(rawArtifact))
			? rawArtifact
			: null;

	const rawTab = (params.get("tab")?.trim().toLowerCase() ??
		"split") as ArtifactFullTab;
	const fullTab: ArtifactFullTab = VALID_TABS.includes(rawTab)
		? rawTab
		: "split";

	const rawWave = (params.get("wave")?.trim() ?? "all") as WaveFilterId;
	const activeWave: WaveFilterId = VALID_WAVES.includes(rawWave)
		? rawWave
		: "all";

	return { view, stageIdx, fullId, fullTab, activeWave };
}

/**
 * Serialize current explorer state into a clean deep-link URL.
 */
export function buildExplorerUrl(state: {
	view: ExplorerViewLevel;
	stageIdx: number;
	fullId: string | null;
	fullTab: ArtifactFullTab;
	activeWave: WaveFilterId;
}): string {
	const path =
		state.view === "stage"
			? `/stages/${String(state.stageIdx).padStart(2, "0")}`
			: "/";
	const params = new URLSearchParams();
	if (state.fullId) {
		params.set("artifact", state.fullId);
		if (state.fullTab !== "split") {
			params.set("tab", state.fullTab);
		}
	}
	if (state.activeWave !== "all") {
		params.set("wave", state.activeWave);
	}
	const qs = params.toString();
	return qs ? `${path}?${qs}` : path;
}
