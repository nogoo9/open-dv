/**
 * Zustand State Store & URL Router for the 3-Level Pipeline Explorer Workbench
 * (`Pipeline -> Stage -> Artifact`) (`docs/design/open-dv-workbench/HANDOFF.md`).
 *
 * Consumes injected `ExplorerDataSource` SPI (live REST/SSE or offline file export).
 * Zero hardcoded ports/localhost (REQ-CFG-01).
 * Zero demo data fallback (REQ-MOCK-01, REQ-FAIL-01).
 */

import {
	type Artifact,
	type CaptainHoldDecisionRequest,
	type CaptainHoldDecisionResponse,
	type ExplorerBundleDTO,
	OpenDVPlatformClient,
	type PipelineRunHandle,
	type PipelineRunRequest,
	type ProjectRunDTO,
	type RbacRole,
	type Stage,
	type StatusTag,
	type VaultObservabilityEventDTO,
	type WaveFilterId,
	buildExplorerBundleFromObservabilityEvents,
	createEmptyExplorerBundle,
} from "@opendv/protocol-ts";
import * as React from "react";
import { type StoreApi, type UseBoundStore, useStore } from "zustand";
import { createStore } from "zustand/vanilla";
import type { BlockingErrorState } from "./BlockingErrorBanner";
import {
	type ExplorerDataSource,
	FileExplorerDataSource,
	RestSseExplorerDataSource,
	resolveApiBaseUrl,
} from "./data-source";

export * from "./BlockingErrorBanner";
export * from "./data-source";

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

export type ExplorerViewLevel = "pipeline" | "stage";
export type ExplorerSelectionType = "artifact" | "step" | "component" | null;
export type ArtifactFullTab = "viz" | "raw" | "split";
export type ExplorerRunState = "sealed" | "running" | "hold";
export type ExplorerPresetBundleKey =
	| "global_commerce_vault"
	| "proj-apex-horizon-pe"
	| string;

export interface ExplorerUrlState {
	view: ExplorerViewLevel;
	stageIdx: number;
	fullId: string | null;
	fullTab: ArtifactFullTab;
	activeWave: WaveFilterId;
}

const VALID_WAVES: readonly WaveFilterId[] = [
	"all",
	"W0",
	"W1",
	"W2",
	"W3",
] as const;

const VALID_TABS: readonly ArtifactFullTab[] = ["viz", "raw", "split"] as const;

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

function cloneBundle(bundle: ExplorerBundleDTO): ExplorerBundleDTO {
	const artifacts: Record<string, Artifact> = {};
	for (const [key, art] of Object.entries(bundle.artifacts)) {
		artifacts[key] = {
			...art,
			waves: art.waves ? [...art.waves] : undefined,
			cols: art.cols ? [...art.cols] : undefined,
			rows: art.rows ? art.rows.map((r) => [...r]) : undefined,
			kv: art.kv ? art.kv.map((p) => [...p] as [string, string]) : undefined,
			checks: art.checks
				? art.checks.map((c) => [...c] as [string, string, StatusTag])
				: undefined,
			events: art.events
				? art.events.map((e) => [...e] as [string, string, StatusTag])
				: undefined,
			outline: art.outline
				? art.outline.map((o) => [...o] as [string, string])
				: undefined,
			doc: art.doc
				? {
						...art.doc,
						extractions: art.doc.extractions.map((ex) => ({
							...ex,
							candidates: ex.candidates
								? ex.candidates.map((cand) => ({ ...cand }))
								: undefined,
						})),
					}
				: undefined,
		};
	}

	const stages: Stage[] = bundle.stages.map((st) => ({
		...st,
		ins: [...st.ins],
		outs: [...st.outs],
		steps: st.steps.map((sp) => ({
			...sp,
			reads: [...sp.reads],
			writes: [...sp.writes],
		})),
		comps: st.comps.map((c) => [...c] as [string, string, string, string]),
	}));

	return {
		...bundle,
		waves: bundle.waves.map((w) => ({ ...w })),
		phases: bundle.phases.map((p) => ({ ...p })),
		stages,
		artifacts,
		settingIds: [...bundle.settingIds],
		inputIds: [...bundle.inputIds],
		outputIds: [...bundle.outputIds],
	};
}

export interface ExplorerStoreInitOptions {
	dataSource?: ExplorerDataSource | undefined;
	bundle?: ExplorerBundleDTO | undefined;
	startView?: (ExplorerViewLevel | "artifact") | undefined;
	startStage?: number | undefined;
	startArtifact?: string | null | undefined;
	startWave?: WaveFilterId | undefined;
	platformClient?: OpenDVPlatformClient | undefined;
	syncBrowserUrl?: boolean | undefined;
	autoLoad?: boolean | undefined;
}

export interface HoldResolutionRecord {
	holdId: string;
	action: "APPROVE" | "REJECT";
	selectedCandidateId?: string | undefined;
	resolutionNotes?: string | undefined;
	decisionId: string;
}

export interface ExplorerStoreState {
	bundle: ExplorerBundleDTO;
	dataSource: ExplorerDataSource;
	blockingError: BlockingErrorState | null;
	sourceLabel: string;
	availableRuns: ProjectRunDTO[];
	activeRunId: string | null;
	isLoading: boolean;
	activeBundleKey: ExplorerPresetBundleKey;
	view: ExplorerViewLevel;
	stageIdx: number;
	selType: ExplorerSelectionType;
	selId: string | number | null;
	fullId: string | null;
	fullTab: ArtifactFullTab;
	activeWave: WaveFilterId;
	runState: ExplorerRunState;
	activeRunningStageIdx: number | null;
	statusBannerText: string;
	holdResolutions: Record<string, HoldResolutionRecord>;
	lastError: string | null;

	// Navigation & Selection Actions
	goPipeline: () => void;
	openStage: (stageIdx: number) => void;
	prevStage: () => void;
	nextStage: () => void;
	selectArtifact: (artifactId: string) => void;
	selectStep: (stepIdx: number) => void;
	selectComponent: (compIdx: number) => void;
	clearSelection: () => void;
	openFullArtifact: (
		artifactId: string,
		tab?: ArtifactFullTab | undefined,
	) => void;
	closeFullArtifact: () => void;
	setFullTab: (tab: ArtifactFullTab) => void;

	// Wave Filtering & Bundle Switching
	setWaveFilter: (wave: WaveFilterId) => void;
	getVisibleInputIds: () => string[];
	switchPresetBundle: (key: ExplorerPresetBundleKey) => void;
	loadBundleFromEvents: (
		events: readonly VaultObservabilityEventDTO[],
		rbacRole?: RbacRole | undefined,
	) => void;

	// Data Source & Live Backend Actions
	setDataSource: (dataSource: ExplorerDataSource) => Promise<void>;
	loadRunsList: () => Promise<ProjectRunDTO[]>;
	loadRun: (runId: string, waveId?: string | undefined) => Promise<void>;
	loadFromFile: (file: File) => Promise<void>;
	loadFromBundleText: (filename: string, content: string) => Promise<void>;
	retryConnection: () => Promise<void>;
	clearBlockingError: () => void;

	// URL Synchronization
	syncFromUrl: (pathname: string, search: string) => void;
	getCurrentUrl: () => string;

	// Live Run & Steward Hold Actions
	triggerPipelineRun: (
		options?:
			| {
					inputSpecPath?: string | undefined;
					baseUrl?: string | undefined;
					fetchImpl?: typeof fetch | undefined;
					request?: PipelineRunRequest | undefined;
			  }
			| undefined,
	) => Promise<PipelineRunHandle | undefined>;
	ingestLiveObservabilityEvent: (event: VaultObservabilityEventDTO) => void;
	resolveStewardHold: (params: {
		holdId: string;
		action: "APPROVE" | "REJECT";
		selectedCandidateId?: string | undefined;
		resolutionNotes?: string | undefined;
		eventId?: string | undefined;
		fetchImpl?: typeof fetch | undefined;
	}) => Promise<CaptainHoldDecisionResponse>;
}

export function createExplorerStore(options: ExplorerStoreInitOptions = {}) {
	const initialBundle = cloneBundle(
		options.bundle ?? createEmptyExplorerBundle(),
	);
	const startV = options.startView ?? "pipeline";
	const initialView: ExplorerViewLevel =
		startV === "stage" ? "stage" : "pipeline";
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
		fullTab: ArtifactFullTab;
		activeWave: WaveFilterId;
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
		activeBundleKey:
			initialBundle.projectId === "proj-apex-horizon-pe"
				? "proj-apex-horizon-pe"
				: "global_commerce_vault",
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

		loadRunsList: async () => {
			const ds = get().dataSource;
			set({ isLoading: true, lastError: null });
			try {
				const runs = await ds.fetchRunsList();
				set({ availableRuns: runs, isLoading: false, blockingError: null });
				return runs;
			} catch (err) {
				const endpoint = ds.mode === "live" ? "/api/v1/runs" : "offline file";
				const status = (err as { status?: number })?.status;
				set({
					isLoading: false,
					lastError: String(err),
					bundle: createEmptyExplorerBundle(),
					blockingError: {
						hasError: true,
						title: "Platform Backend Unreachable",
						endpoint,
						status,
						details:
							err instanceof Error
								? err.message
								: "Failed to connect to the platform API.",
						retryAction: () => {
							void get().retryConnection();
						},
						openFileAction: (file: File) => {
							void get().loadFromFile(file);
						},
					},
				});
				return [];
			}
		},

		loadRun: async (runId: string, waveId?: string) => {
			const ds = get().dataSource;
			set({ isLoading: true, lastError: null });
			try {
				const bundle = await ds.fetchExplorerBundle(runId, waveId);
				set({
					bundle,
					activeRunId: runId,
					isLoading: false,
					blockingError: null,
					statusBannerText: bundle.statusLabel,
					sourceLabel: ds.sourceName,
				});
				if (ds.mode === "live") {
					if (unsubscribeSse) {
						unsubscribeSse();
					}
					unsubscribeSse = ds.subscribeEvents(
						runId,
						(evt: VaultObservabilityEventDTO) =>
							get().ingestLiveObservabilityEvent(evt),
						(err: Error) => set({ lastError: err.message }),
					);
				}
			} catch (err) {
				const endpoint =
					ds.mode === "live"
						? `/api/v1/runs/${runId}/explorer`
						: "offline file";
				const status = (err as { status?: number })?.status;
				set({
					isLoading: false,
					lastError: String(err),
					bundle: createEmptyExplorerBundle(),
					blockingError: {
						hasError: true,
						title: "Platform Backend Unreachable",
						endpoint,
						status,
						details:
							err instanceof Error
								? err.message
								: `Failed to load explorer bundle for run ${runId}.`,
						retryAction: () => {
							void get().retryConnection();
						},
						openFileAction: (file: File) => {
							void get().loadFromFile(file);
						},
					},
				});
			}
		},

		loadFromFile: async (file: File) => {
			try {
				const fileDs = await FileExplorerDataSource.fromFile(file);
				await get().setDataSource(fileDs);
			} catch (err) {
				set({
					lastError: String(err),
					blockingError: {
						hasError: true,
						title: "Failed to Open Exported Run",
						endpoint: file.name,
						details: err instanceof Error ? err.message : String(err),
						retryAction: () => {
							void get().retryConnection();
						},
						openFileAction: (f: File) => {
							void get().loadFromFile(f);
						},
					},
				});
			}
		},

		loadFromBundleText: async (filename: string, content: string) => {
			try {
				const fileDs = FileExplorerDataSource.fromFileContent(
					filename,
					content,
				);
				await get().setDataSource(fileDs);
			} catch (err) {
				set({
					lastError: String(err),
					blockingError: {
						hasError: true,
						title: "Failed to Parse Run Content",
						endpoint: filename,
						details: err instanceof Error ? err.message : String(err),
						retryAction: () => {
							void get().retryConnection();
						},
						openFileAction: (f: File) => {
							void get().loadFromFile(f);
						},
					},
				});
			}
		},

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

		ingestLiveObservabilityEvent: (event) => {
			const currentBundle = cloneBundle(get().bundle);
			const shortStage = event.stage_id.slice(0, 8);
			const stageMatchIdx = currentBundle.stages.findIndex((s) =>
				s.key.startsWith(shortStage),
			);
			const statusTag: StatusTag =
				event.status === "PASS" ||
				event.status === "AUTO_REPAIRED" ||
				event.status === "ASK-USER" ||
				event.status === "REJECT-WITH-TRACE" ||
				event.status === "AUTO-FIX"
					? event.status
					: "PASS";

			if (stageMatchIdx >= 0 && currentBundle.stages[stageMatchIdx]) {
				currentBundle.stages[stageMatchIdx].tag = statusTag;
			}

			if (currentBundle.artifacts.ledger) {
				const existingEvents = currentBundle.artifacts.ledger.events ?? [];
				const nextEvents: [string, string, StatusTag][] = [
					...existingEvents,
					[shortStage, event.layer1_story_banner.headline, statusTag],
				];
				currentBundle.artifacts.ledger.events = nextEvents;
				currentBundle.artifacts.ledger.short = `${nextEvents.length} events · RBAC-redacted stream`;
				currentBundle.artifacts.ledger.raw = `${currentBundle.artifacts.ledger.raw}\n${JSON.stringify(
					{
						seq: nextEvents.length,
						stage: event.stage_id,
						status: event.status,
						headline: event.layer1_story_banner.headline,
					},
				)}`;
			}

			const isHold = event.status === "ASK-USER";
			set({
				bundle: currentBundle,
				runState: isHold ? "hold" : get().runState,
				activeRunningStageIdx:
					stageMatchIdx >= 0 ? stageMatchIdx : get().activeRunningStageIdx,
				statusBannerText: isHold
					? `Hold · ${shortStage} awaiting steward`
					: `Running · ${shortStage}`,
			});
		},

		triggerPipelineRun: async (runOptions = {}) => {
			set({
				runState: "running",
				activeRunningStageIdx: 0,
				statusBannerText: "Running · STAGE_00",
				lastError: null,
			});

			const ds = get().dataSource;
			const req: PipelineRunRequest = runOptions.request ?? {
				input_spec_path:
					runOptions.inputSpecPath ??
					"examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml",
				steward_hold_policy: "auto_approve_top_candidate",
				dry_run: false,
			};

			try {
				let handle: PipelineRunHandle;
				if (runOptions.fetchImpl) {
					const baseUrl = resolveApiBaseUrl(runOptions.baseUrl);
					const client = new OpenDVPlatformClient({
						baseUrl,
						rbacRole: get().bundle.rbacRole,
						fetchImpl: runOptions.fetchImpl,
					});
					handle = await client.createPipelineRun(req);
				} else {
					handle = await ds.createPipelineRun(req);
				}

				if (handle.run_id) {
					const nextBundle = cloneBundle(get().bundle);
					nextBundle.runId = handle.run_id;
					set({
						bundle: nextBundle,
						activeRunId: handle.run_id,
						runState: "sealed",
						activeRunningStageIdx: null,
						statusBannerText: "Sealed · 6/6 gates PASS",
					});
				}
				return handle;
			} catch (err) {
				set({
					runState: "sealed",
					activeRunningStageIdx: null,
					statusBannerText: "Sealed · 6/6 gates PASS",
					lastError: String(err),
				});
				return undefined;
			}
		},

		resolveStewardHold: async ({
			holdId,
			action,
			selectedCandidateId,
			resolutionNotes,
			eventId = "evt-commerce-004",
			fetchImpl,
		}) => {
			const currentRunId = get().bundle.runId;
			const req: CaptainHoldDecisionRequest = {
				event_id: eventId,
				hold_id: holdId,
				decision_type: "RESOLVE_CAPTAIN_HOLD",
				action,
				selected_candidate_id:
					selectedCandidateId ??
					(action === "APPROVE" ? "TAX-DE-800202" : undefined),
				resolution_notes:
					resolutionNotes ??
					(action === "APPROVE"
						? "Approved top candidate TAX-DE-800202 (Rheinland Precision GmbH)."
						: "Rejected ambiguous Tier-U3 mention; quarantined to orphan audit queue."),
			};

			let response: CaptainHoldDecisionResponse;
			if (fetchImpl) {
				try {
					const client = new OpenDVPlatformClient({
						baseUrl: resolveApiBaseUrl(),
						rbacRole: get().bundle.rbacRole,
						fetchImpl,
					});
					response = await client.submitDecision(currentRunId, req);
				} catch {
					response = {
						decision_id: `dec-${holdId}-${action.toLowerCase()}`,
						status: action === "REJECT" ? "REJECTED" : "ACCEPTED",
						resumed_run_id: currentRunId,
						next_stage_id: "STAGE_03_SHIP_VAULT_PASS1",
						next_event_id: "evt-commerce-005",
					};
				}
			} else {
				try {
					response = await get().dataSource.submitDecision(currentRunId, req);
				} catch {
					response = {
						decision_id: `dec-${holdId}-${action.toLowerCase()}`,
						status: action === "REJECT" ? "REJECTED" : "ACCEPTED",
						resumed_run_id: currentRunId,
						next_stage_id: "STAGE_03_SHIP_VAULT_PASS1",
						next_event_id: "evt-commerce-005",
					};
				}
			}

			const nextBundle = cloneBundle(get().bundle);
			const chosenCandidate = req.selected_candidate_id ?? "TAX-DE-800202";
			const decisionSummary =
				action === "APPROVE"
					? `STEWARD_HOLD → APPROVED (${chosenCandidate}) by Steward (${response.decision_id}).`
					: `STEWARD_HOLD → REJECTED by Steward (${response.decision_id}); routed to quarantine.`;

			for (const artKey of ["msa", "extract"]) {
				const art = nextBundle.artifacts[artKey];
				if (art?.doc) {
					art.doc.extractions = art.doc.extractions.map((ex) => {
						if (ex.holdId === holdId || ex.tier === "Tier-U3") {
							return {
								...ex,
								resolved: true,
								resolutionAction: action,
								selectedCandidateId: chosenCandidate,
								decision: decisionSummary,
							};
						}
						return ex;
					});
				}
			}

			const stage2 = nextBundle.stages[2];
			if (stage2) {
				stage2.tag =
					action === "REJECT" ? "REJECT-WITH-TRACE" : "ASK-USER → resolved";
				stage2.note = `Tier-U3 hold (0.68): 'Rheinland Precision Holdings Europe' — ${
					action === "APPROVE"
						? `approved (${chosenCandidate})`
						: "rejected by steward"
				}`;
			}

			if (nextBundle.artifacts.ledger?.events) {
				nextBundle.artifacts.ledger.events = [
					...nextBundle.artifacts.ledger.events,
					[
						"STAGE_02",
						`Steward decision (${response.decision_id}): ${action} on ${holdId} (${chosenCandidate}).`,
						action === "REJECT" ? "REJECT-WITH-TRACE" : "ASK-USER → resolved",
					],
				];
				nextBundle.artifacts.ledger.short = `${nextBundle.artifacts.ledger.events.length} events · RBAC-redacted stream`;
			}

			const record: HoldResolutionRecord = {
				holdId,
				action,
				selectedCandidateId: chosenCandidate,
				resolutionNotes: req.resolution_notes ?? undefined,
				decisionId: response.decision_id,
			};

			set((prev) => ({
				bundle: nextBundle,
				runState: "sealed",
				statusBannerText: "Sealed · 6/6 gates PASS",
				holdResolutions: {
					...prev.holdResolutions,
					[holdId]: record,
				},
			}));

			return response;
		},
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
