/**
 * URL Routing & Deep-Linking Serializer for Workbench Explorer (User Directive R9 Modularity).
 */

import type { WaveFilterId } from "@opendv/protocol-ts";
import type {
	ArtifactFullTab,
	ExplorerViewLevel,
	OntologySubView,
} from "./types";

export interface ExplorerUrlState {
	view: ExplorerViewLevel;
	ontologySubView?: OntologySubView | undefined;
	stageIdx: number;
	fullId: string | null;
	fullTab: ArtifactFullTab;
	activeWave: WaveFilterId;
	run?: string | null | undefined;
	compare?: string | null | undefined;
	session?: string | null | undefined;
	runId?: string | null | undefined;
	compareRunId?: string | null | undefined;
	sessionId?: string | null | undefined;
	builderSessionId?: string | null | undefined;
	builderSource?: string | null | undefined;
	builderDomain?: string | null | undefined;
	builderPipelineId?: string | null | undefined;
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
	let view: ExplorerViewLevel = "ontology";
	let ontologySubView: OntologySubView = "studio";
	let stageIdx = 5;

	const stageMatch = pathname.match(/^\/stages\/0?([0-7])(?:\/|$)/);
	if (stageMatch?.[1] !== undefined) {
		view = "stage";
		stageIdx = Math.max(0, Math.min(7, Number.parseInt(stageMatch[1], 10)));
	} else if (pathname === "/dv-schema" || pathname.startsWith("/dv-schema/")) {
		view = "dv_schema";
	} else if (pathname === "/explorer" || pathname.startsWith("/explorer/")) {
		view = "pipeline";
	} else if (pathname === "/settings" || pathname.startsWith("/settings/")) {
		view = "settings";
	} else if (pathname === "/builder" || pathname.startsWith("/builder/")) {
		view = "ontology";
		ontologySubView = "builder";
	} else {
		view = "ontology";
		ontologySubView = "studio";
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

	const run = params.get("run")?.trim() || undefined;
	const compare = params.get("compare")?.trim() || undefined;
	const session = params.get("session")?.trim() || undefined;
	const builderSessionId = params.get("session_id")?.trim() || undefined;
	const builderSource = params.get("source")?.trim() || undefined;
	const builderDomain = params.get("domain_namespace")?.trim() || undefined;
	const builderPipelineId = params.get("pipeline_id")?.trim() || undefined;

	return {
		view,
		...(ontologySubView === "builder" ? { ontologySubView } : {}),
		stageIdx,
		fullId,
		fullTab,
		activeWave,
		...(run ? { run, runId: run } : {}),
		...(compare ? { compare, compareRunId: compare } : {}),
		...(session ? { session, sessionId: session } : {}),
		...(builderSessionId ? { builderSessionId } : {}),
		...(builderSource ? { builderSource } : {}),
		...(builderDomain ? { builderDomain } : {}),
		...(builderPipelineId ? { builderPipelineId } : {}),
	};
}

/**
 * Serialize current explorer state into a clean deep-link URL.
 */
export function buildExplorerUrl(state: {
	view: ExplorerViewLevel;
	ontologySubView?: OntologySubView | undefined;
	stageIdx: number;
	fullId: string | null;
	fullTab: ArtifactFullTab;
	activeWave: WaveFilterId;
	run?: string | null | undefined;
	compare?: string | null | undefined;
	session?: string | null | undefined;
	runId?: string | null | undefined;
	compareRunId?: string | null | undefined;
	sessionId?: string | null | undefined;
	builderSessionId?: string | null | undefined;
	builderSource?: string | null | undefined;
	builderDomain?: string | null | undefined;
	builderPipelineId?: string | null | undefined;
}): string {
	const path =
		state.view === "stage"
			? `/stages/${String(state.stageIdx).padStart(2, "0")}`
			: state.view === "dv_schema"
				? "/dv-schema"
				: state.view === "pipeline"
					? "/explorer"
					: state.view === "settings"
						? "/settings"
						: state.view === "builder" || state.ontologySubView === "builder"
							? "/builder"
							: "/";

	const params = new URLSearchParams();
	const runVal = state.run ?? state.runId;
	const compareVal = state.compare ?? state.compareRunId;
	const sessionVal = state.session ?? state.sessionId;
	if (runVal) {
		params.set("run", runVal);
	}
	if (compareVal) {
		params.set("compare", compareVal);
	}
	if (sessionVal) {
		params.set("session", sessionVal);
	}
	if (state.fullId) {
		params.set("artifact", state.fullId);
		if (state.fullTab !== "split") {
			params.set("tab", state.fullTab);
		}
	}
	if (state.activeWave !== "all") {
		params.set("wave", state.activeWave);
	}
	if (state.builderSessionId) {
		params.set("session_id", state.builderSessionId);
	}
	if (state.builderSource) {
		params.set("source", state.builderSource);
	}
	if (state.builderDomain) {
		params.set("domain_namespace", state.builderDomain);
	}
	if (state.builderPipelineId) {
		params.set("pipeline_id", state.builderPipelineId);
	}
	const qs = params.toString();
	return qs ? `${path}?${qs}` : path;
}

/**
 * Strip trailing `/` characters in O(n) time without backtracking regexes.
 */
export function stripTrailingSlashes(value: string): string {
	let end = value.length;
	while (end > 0 && value.charCodeAt(end - 1) === 47 /* '/' */) {
		end -= 1;
	}
	return end === value.length ? value : value.slice(0, end);
}

/**
 * Normalize an optional API base URL by stripping trailing `/api/v1` (or `/api/v1/`)
 * and trailing `/` characters in strict O(n) linear time.
 */
export function normalizeApiBaseUrl(apiUrl?: string): string {
	let raw = apiUrl ?? "";
	if (raw.endsWith("/api/v1/")) {
		raw = raw.slice(0, -8);
	} else if (raw.endsWith("/api/v1")) {
		raw = raw.slice(0, -7);
	}
	return stripTrailingSlashes(raw);
}
