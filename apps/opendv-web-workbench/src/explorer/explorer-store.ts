/**
 * Zustand State Store & URL Router for the 3-Level Pipeline Explorer Workbench
 * (`Pipeline -> Stage -> Artifact`) (`docs/design/open-dv-workbench/HANDOFF.md`).
 *
 * Consumes injected `ExplorerDataSource` SPI (live REST/SSE or offline file export).
 * Zero hardcoded ports/localhost (REQ-CFG-01).
 * Zero demo data fallback (REQ-MOCK-01, REQ-FAIL-01).
 * Asynchronous HTTP 202 lifecycle streaming (Decision D2).
 */

import {
	buildExplorerBundleFromObservabilityEvents,
	createEmptyExplorerBundle,
} from "@opendv/protocol-ts";
import * as React from "react";
import { type StoreApi, type UseBoundStore, useStore } from "zustand";
import { createStore } from "zustand/vanilla";
import {
	type ExplorerDataSource,
	FileExplorerDataSource,
	RestSseExplorerDataSource,
} from "./data-source";
import {
	executeIngestLiveObservabilityEvent,
	executeLoadFromBundleText,
	executeLoadFromFile,
	executeLoadRun,
	executeLoadRunsList,
	executeResolveStewardHold,
	executeTriggerPipelineRun,
} from "./store-actions";
import {
	type ExplorerSelectionType,
	type ExplorerStore,
	type ExplorerStoreInitOptions,
	type ExplorerStoreState,
	type ExplorerViewLevel,
	cloneBundle,
} from "./types";
import { buildExplorerUrl, parseExplorerUrlState } from "./url-router";

export * from "./BlockingErrorBanner";
export * from "./data-source";
export * from "./types";
export * from "./url-router";

export function hasActiveReactDispatcher(): boolean {
	const internals = React as unknown as {
		__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE?: {
			H?: unknown;
		};
		__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED?: {
			ReactCurrentDispatcher?: { current?: unknown };
		};
	};
	return Boolean(
		internals.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE
			?.H ||
			internals.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED
				?.ReactCurrentDispatcher?.current,
	);
}

export function createExplorerStore(
	options: ExplorerStoreInitOptions = {},
): ExplorerStore {
	const initialBundle = cloneBundle(
		options.bundle ?? createEmptyExplorerBundle(),
	);
	const startV = options.startView ?? "pipeline";
	const initialView: ExplorerViewLevel =
		startV === "stage"
			? "stage"
			: startV === "builder"
				? "builder"
				: startV === "settings"
					? "settings"
					: "pipeline";
	const initialStageIdx = Math.max(0, Math.min(7, options.startStage ?? 5));
	const initialSelType: ExplorerSelectionType =
		startV === "stage" ? "step" : startV === "artifact" ? "artifact" : null;
	const initialSelId: string | number | null =
		startV === "stage"
			? Math.min(
					5,
					(initialBundle.stages[initialStageIdx]?.steps.length ?? 1) - 1,
				)
			: startV === "artifact"
				? (options.startArtifact ?? "ontology")
				: null;
	const initialFullId: string | null =
		startV === "artifact"
			? (options.startArtifact ?? "ontology")
			: (options.startArtifact ?? null);

	const initialDataSource: ExplorerDataSource =
		options.dataSource ??
		(options.bundle && !options.platformClient
			? new FileExplorerDataSource({
					bundle: initialBundle,
					sourceName: "Loaded Bundle",
				})
			: new RestSseExplorerDataSource({
					client: options.platformClient,
				}));

	const shouldSyncUrl = options.syncBrowserUrl ?? true;
	let unsubscribeSse: (() => void) | null = null;

	const pushBrowserUrl = (state: {
		view: ExplorerViewLevel;
		stageIdx: number;
		fullId: string | null;
		fullTab: "viz" | "raw" | "split";
		activeWave: "all" | "W0" | "W1" | "W2" | "W3";
	}) => {
		if (!shouldSyncUrl) return;
		if (
			typeof globalThis !== "undefined" &&
			"history" in globalThis &&
			typeof globalThis.history?.replaceState === "function"
		) {
			try {
				const nextUrl = buildExplorerUrl(state);
				globalThis.history.replaceState(null, "", nextUrl);
			} catch {
				// Ignore in sandboxed/headless environments
			}
		}
	};

	const api = createStore<ExplorerStoreState>()((set, get) => ({
		bundle: initialBundle,
		dataSource: initialDataSource,
		blockingError: null,
		sourceLabel: initialDataSource.sourceName,
		availableRuns: [],
		activeRunId: initialBundle.runId || null,
		isLoading: false,
		activeBundleKey: initialBundle.projectId || "default",
		view: initialView,
		stageIdx: initialStageIdx,
		selType: initialSelType,
		selId: initialSelId,
		fullId: initialFullId,
		fullTab: "split",
		activeWave: options.startWave ?? "all",
		runState: "sealed",
		activeRunningStageIdx: null,
		statusBannerText: initialBundle.statusLabel,
		holdResolutions: {},
		lastError: null,

		goPipeline: () => {
			set({ view: "pipeline", selType: null, selId: null });
			pushBrowserUrl(get());
		},

		goBuilder: () => {
			set({ view: "builder", selType: null, selId: null });
			pushBrowserUrl(get());
		},

		goSettings: () => {
			set({ view: "settings", selType: null, selId: null });
			pushBrowserUrl(get());
		},

		openStage: (stageIdx) => {
			const clamped = Math.max(0, Math.min(7, stageIdx));
			set({ view: "stage", stageIdx: clamped, selType: null, selId: null });
			pushBrowserUrl(get());
		},

		prevStage: () => {
			const nextIdx = Math.max(0, get().stageIdx - 1);
			set({ stageIdx: nextIdx, selType: null, selId: null });
			pushBrowserUrl(get());
		},

		nextStage: () => {
			const nextIdx = Math.min(7, get().stageIdx + 1);
			set({ stageIdx: nextIdx, selType: null, selId: null });
			pushBrowserUrl(get());
		},

		selectArtifact: (artifactId) => {
			if (!get().bundle.artifacts[artifactId]) return;
			set({ selType: "artifact", selId: artifactId });
		},

		selectStep: (stepIdx) => {
			set({ selType: "step", selId: stepIdx });
		},

		selectComponent: (compIdx) => {
			set({ selType: "component", selId: compIdx });
		},

		clearSelection: () => {
			set({ selType: null, selId: null });
		},

		openFullArtifact: (artifactId, tab = "split") => {
			if (!get().bundle.artifacts[artifactId]) return;
			set({ fullId: artifactId, fullTab: tab });
			pushBrowserUrl(get());
		},

		closeFullArtifact: () => {
			set({ fullId: null });
			pushBrowserUrl(get());
		},

		setFullTab: (tab) => {
			set({ fullTab: tab });
			pushBrowserUrl(get());
		},

		setWaveFilter: (wave) => {
			set({ activeWave: wave });
			pushBrowserUrl(get());
		},

		getVisibleInputIds: () => {
			const bundle = get().bundle;
			const wave = get().activeWave;
			if (wave === "all") return bundle.inputIds;
			return bundle.inputIds.filter((id) => {
				const art = bundle.artifacts[id];
				return (
					!art?.waves || art.waves.includes(wave as "W0" | "W1" | "W2" | "W3")
				);
			});
		},

		switchPresetBundle: (key) => {
			set({ activeBundleKey: key });
		},

		loadBundleFromEvents: (events, rbacRole = "INTERNAL_MODELER") => {
			const built = buildExplorerBundleFromObservabilityEvents(
				undefined,
				undefined,
				events,
				rbacRole,
				get().bundle,
			);
			set({
				bundle: built,
				statusBannerText: built.statusLabel,
			});
		},

		setDataSource: async (newDataSource: ExplorerDataSource) => {
			if (unsubscribeSse) {
				unsubscribeSse();
				unsubscribeSse = null;
			}
			set({
				dataSource: newDataSource,
				sourceLabel: newDataSource.sourceName,
				blockingError: null,
			});
			const runs = await get().loadRunsList();
			if (runs.length > 0 && runs[0]?.run_id) {
				await get().loadRun(runs[0].run_id);
			}
		},

		loadRunsList: () => executeLoadRunsList(get, set),

		loadRun: (runId: string, waveId?: string | undefined) =>
			executeLoadRun(
				get,
				set,
				runId,
				waveId,
				() => unsubscribeSse,
				(fn) => {
					unsubscribeSse = fn;
				},
			),

		loadFromFile: (file: File) => executeLoadFromFile(get, set, file),

		loadFromBundleText: (filename: string, content: string) =>
			executeLoadFromBundleText(get, set, filename, content),

		retryConnection: async () => {
			set({ blockingError: null, lastError: null });
			const runs = await get().loadRunsList();
			if (runs.length > 0 && runs[0]?.run_id) {
				await get().loadRun(runs[0].run_id);
			}
		},

		clearBlockingError: () => {
			set({ blockingError: null });
		},

		syncFromUrl: (pathname, search) => {
			const validIds = new Set(Object.keys(get().bundle.artifacts));
			const parsed = parseExplorerUrlState(pathname, search, validIds);
			set({
				view: parsed.view,
				stageIdx: parsed.stageIdx,
				fullId: parsed.fullId,
				fullTab: parsed.fullTab,
				activeWave: parsed.activeWave,
			});
		},

		getCurrentUrl: () => buildExplorerUrl(get()),

		ingestLiveObservabilityEvent: (event) =>
			executeIngestLiveObservabilityEvent(get, set, event),

		triggerPipelineRun: (runOptions) =>
			executeTriggerPipelineRun(get, set, runOptions),

		resolveStewardHold: (params) => executeResolveStewardHold(get, set, params),
	}));

	api.getInitialState = () => api.getState();

	const useBoundStore = (<T = ExplorerStoreState>(
		selector?: (state: ExplorerStoreState) => T,
	): T => {
		if (!hasActiveReactDispatcher()) {
			const state = api.getState();
			return selector ? selector(state) : (state as unknown as T);
		}
		try {
			return useStore(
				api,
				(selector ?? ((s: ExplorerStoreState) => s as unknown as T)) as (
					state: ExplorerStoreState,
				) => T,
			);
		} catch {
			const state = api.getState();
			return selector ? selector(state) : (state as unknown as T);
		}
	}) as UseBoundStore<StoreApi<ExplorerStoreState>>;

	Object.assign(useBoundStore, api);
	return useBoundStore;
}

export const useExplorerStore = createExplorerStore();
export const defaultExplorerStore = useExplorerStore;
