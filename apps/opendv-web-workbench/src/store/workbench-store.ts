/**
 * Zustand Workbench State Store (`workbench-store.ts`) for `@opendv/web-workbench` (`docs/10` §2.1 & §3.2).
 *
 * Binds exclusively to `@opendv/protocol-ts`:
 * - `OpenDVPlatformClient` (REST API & Captain-Hold decision submission)
 * - `VaultEventStreamClient` (Live SSE telemetry stream with `Last-Event-ID` & deduplication)
 * - `MockVaultEventStreamClient` (Offline `.jsonl` & Private Equity demo bundle replay)
 */

import {
	ALL_WORKBENCH_VIEW_IDS,
	type CaptainHoldDecisionRequest,
	type CaptainHoldDecisionResponse,
	type EventSourceFactory,
	LedgerReplayClient,
	type OfflineReplayOptions,
	OpenDVPlatformClient,
	type RbacRole,
	VaultEventStreamClient,
	type VaultObservabilityEventDTO,
	type WorkbenchViewId,
	getLatestEventForView,
	groupEventsByViewId,
	redactEventForRole,
} from "@opendv/protocol-ts";
import { type StoreApi, type UseBoundStore, useStore } from "zustand";
import { createStore } from "zustand/vanilla";

export type WorkbenchExecutionMode = "live-sse" | "offline-replay";

export type ProofDrawerTabId = "3A" | "3B" | "3C" | "3D";

export const ALL_PROOF_DRAWER_TABS: readonly ProofDrawerTabId[] = [
	"3A",
	"3B",
	"3C",
	"3D",
] as const;

export const PLAYBACK_SPEED_PRESETS: readonly number[] = [
	0.5, 1, 2, 5, 10,
] as const;

export const DEFAULT_REPLAY_STEP_DELAY_MS = 250;

export interface LiveStreamConnectionConfig {
	baseUrl?: string | undefined;
	runId?: string | undefined;
	streamUrl?: string | undefined;
	lastEventId?: string | undefined;
	rbacRole?: RbacRole | undefined;
	eventSourceFactory?: EventSourceFactory | undefined;
	fetchImpl?: typeof fetch | undefined;
}

export interface LoadOfflineReplayConfig {
	startAtZero?: boolean | undefined;
	runId?: string | undefined;
}

export interface WorkbenchStoreInitOptions {
	initialMode?: WorkbenchExecutionMode | undefined;
	initialRunId?: string | undefined;
	initialViewId?: WorkbenchViewId | undefined;
	initialEvents?: readonly VaultObservabilityEventDTO[] | undefined;
	startAtZero?: boolean | undefined;
	rbacRole?: RbacRole | undefined;
	platformClient?: OpenDVPlatformClient | undefined;
}

export interface WorkbenchStoreState {
	// Execution Mode & Connection State
	mode: WorkbenchExecutionMode;
	runId: string;
	rbacRole: RbacRole;
	isConnected: boolean;
	runStatus: string;
	lastError: string | null;

	// Event Stream & View State
	rawEvents: VaultObservabilityEventDTO[];
	events: VaultObservabilityEventDTO[];
	allReplayEvents: readonly VaultObservabilityEventDTO[];
	eventsByView: Record<WorkbenchViewId, VaultObservabilityEventDTO[]>;
	activeViewId: WorkbenchViewId;
	currentEvent: VaultObservabilityEventDTO | null;
	selectedEventId: string | null;
	isSidebarCollapsed: boolean;

	// Layer 3 Formal Proof Drawer State
	isProofDrawerOpen: boolean;
	activeProofTab: ProofDrawerTabId;

	// Playback Transport State
	isPlaying: boolean;
	replayCursor: number;
	totalReplayEvents: number;
	playbackSpeed: number;

	// Captain-Hold Governance State
	activeHoldIds: string[];
	decisionsHistory: CaptainHoldDecisionResponse[];

	// Actions — Navigation & UI Shell
	setMode: (mode: WorkbenchExecutionMode) => void;
	setActiveView: (viewId: WorkbenchViewId) => void;
	selectView: (viewId: WorkbenchViewId) => void;
	toggleSidebar: () => void;
	setSidebarCollapsed: (collapsed: boolean) => void;
	openProofDrawer: (tab?: ProofDrawerTabId) => void;
	closeProofDrawer: () => void;
	toggleProofDrawer: () => void;
	setActiveProofTab: (tab: ProofDrawerTabId) => void;
	setRbacRole: (role: RbacRole) => void;

	// Actions — Offline Replay (`MockVaultEventStreamClient`)
	loadOfflineReplay: (
		eventsOrJsonl?: readonly VaultObservabilityEventDTO[] | string,
		config?: LoadOfflineReplayConfig,
	) => void;
	stepForward: () => VaultObservabilityEventDTO | null;
	step: () => VaultObservabilityEventDTO | null;
	seekReplay: (index: number) => VaultObservabilityEventDTO | null;
	seek: (index: number) => VaultObservabilityEventDTO | null;
	setPlaybackSpeed: (speed: number) => void;
	setSpeed: (speed: number) => void;
	playReplay: (options?: OfflineReplayOptions) => Promise<number>;
	play: (options?: OfflineReplayOptions) => Promise<number>;
	pauseReplay: () => void;
	pause: () => void;
	togglePlayPause: () => void;
	resetReplay: () => void;
	reset: () => void;
	replayAllInstant: () => number;

	// Actions — Live SSE (`VaultEventStreamClient` & `OpenDVPlatformClient`)
	connectLiveStream: (
		config?: LiveStreamConnectionConfig,
	) => VaultEventStreamClient;
	disconnectLiveStream: () => void;
	ingestLiveSseChunk: (rawChunk: string) => void;
	setPlatformClient: (client: OpenDVPlatformClient) => void;

	// Actions — Captain-Hold Decision Submission (`POST /api/v1/runs/{id}/decisions`)
	submitCaptainHoldDecision: (
		decision: CaptainHoldDecisionRequest,
		runIdOverride?: string,
	) => Promise<CaptainHoldDecisionResponse>;
	submitDecision: (
		decision: CaptainHoldDecisionRequest,
		runIdOverride?: string,
	) => Promise<CaptainHoldDecisionResponse>;

	// Client Accessors
	getPlatformClient: () => OpenDVPlatformClient | null;
	getSseClient: () => VaultEventStreamClient | null;
	getMockReplayClient: () => LedgerReplayClient;
	getLedgerReplayClient: () => LedgerReplayClient;
	getEventsForActiveView: () => VaultObservabilityEventDTO[];
}

function applyRoleRedaction(
	rawEvents: readonly VaultObservabilityEventDTO[],
	role: RbacRole,
): VaultObservabilityEventDTO[] {
	return rawEvents.map((ev) => redactEventForRole(ev, role));
}

function resolveCurrentEventForView(
	events: readonly VaultObservabilityEventDTO[],
	viewId: WorkbenchViewId,
): VaultObservabilityEventDTO | null {
	const forView = getLatestEventForView(events, viewId);
	if (forView) {
		return forView;
	}
	return events.length > 0 ? (events[events.length - 1] ?? null) : null;
}

function extractHoldIdsFromEvents(
	events: readonly VaultObservabilityEventDTO[],
): string[] {
	const holds = new Set<string>();
	for (const ev of events) {
		if (ev.status === "ASK-USER") {
			const holdObj = ev.layer2_visual_payload.captain_hold as
				| { hold_id?: string }
				| undefined;
			if (holdObj?.hold_id) {
				holds.add(holdObj.hold_id);
			} else {
				holds.add(`hold-${ev.event_id}`);
			}
		}
	}
	return Array.from(holds);
}

export function createWorkbenchStore(options: WorkbenchStoreInitOptions = {}) {
	const initialRbacRole: RbacRole =
		options.rbacRole ?? "RESTRICTED_PII_CUSTODIAN";
	const initialViewId: WorkbenchViewId = options.initialViewId ?? "WB-00";
	const sourceEvents: readonly VaultObservabilityEventDTO[] =
		options.initialEvents ?? [];
	const startAtZero = options.startAtZero ?? false;

	let mockClient = new LedgerReplayClient(sourceEvents);
	let sseClient: VaultEventStreamClient | null = null;
	let platformClient: OpenDVPlatformClient | null =
		options.platformClient ?? null;
	let replayAbortController: AbortController | null = null;

	const initialRawEvents: VaultObservabilityEventDTO[] = startAtZero
		? []
		: [...sourceEvents];
	if (!startAtZero) {
		mockClient.seek(sourceEvents.length);
	}
	const initialVisibleEvents = applyRoleRedaction(
		initialRawEvents,
		initialRbacRole,
	);
	const initialGrouped = groupEventsByViewId(initialVisibleEvents);
	const initialCurrent = resolveCurrentEventForView(
		initialVisibleEvents,
		initialViewId,
	);
	const initialHolds = extractHoldIdsFromEvents(initialRawEvents);

	const api = createStore<WorkbenchStoreState>()((set, get) => {
		const syncEventsState = (
			nextRawEvents: VaultObservabilityEventDTO[],
			nextCursor: number,
			overrideViewId?: WorkbenchViewId,
		) => {
			const role = get().rbacRole;
			const viewId = overrideViewId ?? get().activeViewId;
			const redacted = applyRoleRedaction(nextRawEvents, role);
			const grouped = groupEventsByViewId(redacted);
			const currentEvent = resolveCurrentEventForView(redacted, viewId);
			const latestEvent =
				redacted.length > 0 ? (redacted[redacted.length - 1] ?? null) : null;
			const detectedHolds = extractHoldIdsFromEvents(nextRawEvents);

			set({
				rawEvents: nextRawEvents,
				events: redacted,
				eventsByView: grouped,
				currentEvent,
				selectedEventId: latestEvent?.event_id ?? null,
				replayCursor: nextCursor,
				activeHoldIds: detectedHolds,
			});
		};

		const stepInternal = (): VaultObservabilityEventDTO | null => {
			let steppedEvent: VaultObservabilityEventDTO | null = null;
			mockClient.step((ev) => {
				steppedEvent = ev;
			});
			if (!steppedEvent) {
				set({ isPlaying: false });
				return null;
			}
			const nextRaw = [...get().rawEvents, steppedEvent];
			syncEventsState(nextRaw, mockClient.getCursor());
			return get().events[get().events.length - 1] ?? steppedEvent;
		};

		const seekInternal = (index: number): VaultObservabilityEventDTO | null => {
			const target = mockClient.seek(index);
			const allEvents = get().allReplayEvents;
			const nextRaw = allEvents.slice(0, index);
			syncEventsState(nextRaw, mockClient.getCursor());
			return target ? redactEventForRole(target, get().rbacRole) : null;
		};

		const setSpeedInternal = (speed: number) => {
			mockClient.setSpeed(speed);
			set({ playbackSpeed: speed });
		};

		const pauseInternal = () => {
			const wasPlaying = replayAbortController !== null || get().isPlaying;
			if (replayAbortController) {
				replayAbortController.abort();
				replayAbortController = null;
			}
			set((prev) => ({
				isPlaying: false,
				runStatus:
					wasPlaying && prev.runStatus === "RUNNING"
						? "PAUSED"
						: prev.runStatus,
			}));
		};

		const playInternal = async (
			replayOptions: OfflineReplayOptions = {},
		): Promise<number> => {
			pauseInternal();
			if (mockClient.getCursor() >= get().allReplayEvents.length) {
				mockClient.reset();
				syncEventsState([], 0);
			}
			const abortController = new AbortController();
			if (replayOptions.signal) {
				if (replayOptions.signal.aborted) {
					abortController.abort();
				} else {
					replayOptions.signal.addEventListener(
						"abort",
						() => abortController.abort(),
						{ once: true },
					);
				}
			}
			replayAbortController = abortController;
			set({ isPlaying: true, runStatus: "RUNNING" });

			try {
				const emittedCount = await mockClient.replay(
					(ev) => {
						if (
							replayAbortController !== abortController ||
							abortController.signal.aborted
						) {
							return;
						}
						const nextRaw = [...get().rawEvents, ev];
						syncEventsState(nextRaw, mockClient.getCursor());
					},
					{
						stepDelayMs:
							replayOptions.stepDelayMs ?? DEFAULT_REPLAY_STEP_DELAY_MS,
						speedMultiplier:
							replayOptions.speedMultiplier ?? get().playbackSpeed,
						signal: abortController.signal,
					},
				);
				if (replayAbortController === abortController) {
					const reachedEnd =
						mockClient.getCursor() >= get().allReplayEvents.length;
					set({
						isPlaying: false,
						runStatus: reachedEnd ? "COMPLETED" : "PAUSED",
					});
				}
				return emittedCount;
			} finally {
				if (replayAbortController === abortController) {
					replayAbortController = null;
				}
			}
		};

		const resetInternal = () => {
			pauseInternal();
			mockClient.reset();
			syncEventsState([], 0);
			set({
				activeHoldIds: [],
				runStatus: "IDLE",
			});
		};

		const selectViewInternal = (viewId: WorkbenchViewId) => {
			if (!ALL_WORKBENCH_VIEW_IDS.includes(viewId)) {
				return;
			}
			const currentEvent = resolveCurrentEventForView(get().events, viewId);
			set({
				activeViewId: viewId,
				currentEvent,
			});
		};

		const submitDecisionInternal = async (
			decision: CaptainHoldDecisionRequest,
			runIdOverride?: string,
		): Promise<CaptainHoldDecisionResponse> => {
			const targetRunId = runIdOverride ?? get().runId;
			let response: CaptainHoldDecisionResponse;

			if (get().mode === "live-sse" && platformClient !== null) {
				response = await platformClient.submitDecision(targetRunId, decision);
			} else {
				response = {
					decision_id: `dec-${decision.event_id}-${decision.action.toLowerCase()}`,
					status: decision.action === "REJECT" ? "REJECTED" : "ACCEPTED",
					resumed_run_id: targetRunId,
					next_stage_id: "STAGE_03_SHIP_VAULT_PASS1",
					next_event_id: "evt-pe-005",
				};
			}

			const resolvedHoldId = decision.hold_id;
			const updatedRawEvents = get().rawEvents.map((ev) => {
				if (ev.event_id !== decision.event_id) {
					return ev;
				}
				const nextStatus =
					decision.action === "REJECT"
						? ("REJECT-WITH-TRACE" as const)
						: ("PASS" as const);
				return {
					...ev,
					status: nextStatus,
					layer1_story_banner: {
						...ev.layer1_story_banner,
						verdict_pill: nextStatus,
					},
				};
			});

			syncEventsState(updatedRawEvents, get().replayCursor);

			set((prev) => ({
				activeHoldIds: resolvedHoldId
					? prev.activeHoldIds.filter((id) => id !== resolvedHoldId)
					: prev.activeHoldIds.slice(1),
				decisionsHistory: [...prev.decisionsHistory, response],
				runStatus: response.status === "ACCEPTED" ? "RUNNING" : prev.runStatus,
			}));

			return response;
		};

		return {
			mode: options.initialMode ?? "offline-replay",
			runId: options.initialRunId ?? sourceEvents[0]?.run_id ?? "",
			rbacRole: initialRbacRole,
			isConnected: false,
			runStatus: startAtZero ? "IDLE" : "COMPLETED",
			lastError: null,

			rawEvents: initialRawEvents,
			events: initialVisibleEvents,
			allReplayEvents: sourceEvents,
			eventsByView: initialGrouped,
			activeViewId: initialViewId,
			currentEvent: initialCurrent,
			selectedEventId:
				initialVisibleEvents.length > 0
					? (initialVisibleEvents[initialVisibleEvents.length - 1]?.event_id ??
						null)
					: null,
			isSidebarCollapsed: false,

			isProofDrawerOpen: false,
			activeProofTab: "3A",

			isPlaying: false,
			replayCursor: mockClient.getCursor(),
			totalReplayEvents: sourceEvents.length,
			playbackSpeed: 1,

			activeHoldIds: initialHolds,
			decisionsHistory: [],

			setMode: (mode) => {
				if (mode !== "offline-replay") {
					pauseInternal();
				}
				if (mode === "offline-replay" && sseClient) {
					sseClient.disconnect();
					sseClient = null;
					set({ mode, isConnected: false });
					return;
				}
				set({ mode });
			},

			setActiveView: selectViewInternal,
			selectView: selectViewInternal,

			toggleSidebar: () =>
				set((prev) => ({ isSidebarCollapsed: !prev.isSidebarCollapsed })),
			setSidebarCollapsed: (collapsed) =>
				set({ isSidebarCollapsed: collapsed }),

			openProofDrawer: (tab) =>
				set((prev) => ({
					isProofDrawerOpen: true,
					activeProofTab: tab ?? prev.activeProofTab,
				})),
			closeProofDrawer: () => set({ isProofDrawerOpen: false }),
			toggleProofDrawer: () =>
				set((prev) => ({ isProofDrawerOpen: !prev.isProofDrawerOpen })),
			setActiveProofTab: (tab) => set({ activeProofTab: tab }),

			setRbacRole: (role) => {
				set({ rbacRole: role });
				syncEventsState(get().rawEvents, get().replayCursor);
			},

			loadOfflineReplay: (eventsOrJsonl, config) => {
				pauseInternal();
				if (sseClient) {
					sseClient.disconnect();
					sseClient = null;
				}
				if (typeof eventsOrJsonl === "string") {
					mockClient = LedgerReplayClient.loadFromJSONL(eventsOrJsonl);
				} else if (Array.isArray(eventsOrJsonl)) {
					mockClient = new LedgerReplayClient(eventsOrJsonl);
				} else {
					mockClient = new LedgerReplayClient([]);
				}

				const loadedEvents = mockClient.getEvents();
				const shouldStartAtZero = config?.startAtZero ?? true;
				const initialSlice = shouldStartAtZero ? [] : [...loadedEvents];
				if (!shouldStartAtZero) {
					mockClient.seek(loadedEvents.length);
				}
				mockClient.setSpeed(get().playbackSpeed);

				set({
					mode: "offline-replay",
					isConnected: false,
					runId: config?.runId ?? loadedEvents[0]?.run_id ?? "",
					allReplayEvents: loadedEvents,
					totalReplayEvents: loadedEvents.length,
					activeHoldIds: shouldStartAtZero
						? []
						: extractHoldIdsFromEvents(initialSlice),
					runStatus: shouldStartAtZero ? "IDLE" : "COMPLETED",
					lastError: null,
				});
				syncEventsState(initialSlice, mockClient.getCursor());
			},

			stepForward: stepInternal,
			step: stepInternal,

			seekReplay: seekInternal,
			seek: seekInternal,

			setPlaybackSpeed: setSpeedInternal,
			setSpeed: setSpeedInternal,

			playReplay: playInternal,
			play: playInternal,

			pauseReplay: pauseInternal,
			pause: pauseInternal,

			togglePlayPause: () => {
				if (get().isPlaying) {
					pauseInternal();
				} else {
					void playInternal();
				}
			},

			resetReplay: resetInternal,
			reset: resetInternal,

			replayAllInstant: () => {
				pauseInternal();
				const allEvents = get().allReplayEvents;
				mockClient.seek(allEvents.length);
				syncEventsState([...allEvents], allEvents.length);
				set({
					isPlaying: false,
					runStatus: "COMPLETED",
				});
				return allEvents.length;
			},

			connectLiveStream: (config = {}) => {
				pauseInternal();
				if (sseClient) {
					sseClient.disconnect();
				}
				const targetRunId = config.runId ?? get().runId;
				const baseUrl = config.baseUrl ?? "http://127.0.0.1:8000";
				const role = config.rbacRole ?? get().rbacRole;

				platformClient = new OpenDVPlatformClient({
					baseUrl,
					rbacRole: role,
					fetchImpl: config.fetchImpl,
				});

				const streamUrl =
					config.streamUrl ??
					`${baseUrl.replace(/\/+$/, "")}/api/v1/runs/${encodeURIComponent(targetRunId)}/events`;

				const nextSseClient = new VaultEventStreamClient({
					streamUrl,
					lastEventId: config.lastEventId,
					eventSourceFactory: config.eventSourceFactory,
					onEvent: (incomingEvent) => {
						const nextRaw = [...get().rawEvents, incomingEvent];
						syncEventsState(nextRaw, nextRaw.length);
						if (incomingEvent.status === "ASK-USER") {
							set({ runStatus: "PAUSED_HOLD" });
						}
					},
					onRunComplete: (summary) => {
						set({
							isConnected: false,
							runStatus: summary.status || "COMPLETED",
						});
					},
					onError: (err) => {
						set({
							lastError:
								err instanceof Error
									? err.message
									: "SSE stream connection error",
						});
					},
				});

				sseClient = nextSseClient;
				set({
					mode: "live-sse",
					runId: targetRunId,
					rbacRole: role,
					rawEvents: [],
					events: [],
					eventsByView: groupEventsByViewId([]),
					currentEvent: null,
					selectedEventId: null,
					replayCursor: 0,
					activeHoldIds: [],
					isConnected: true,
					runStatus: "RUNNING",
					lastError: null,
				});

				if (
					config.eventSourceFactory !== undefined ||
					typeof globalThis.EventSource !== "undefined"
				) {
					nextSseClient.connect();
				}

				return nextSseClient;
			},

			disconnectLiveStream: () => {
				if (sseClient) {
					sseClient.disconnect();
					sseClient = null;
				}
				set({ isConnected: false });
			},

			ingestLiveSseChunk: (rawChunk) => {
				if (!sseClient) {
					get().connectLiveStream();
				}
				sseClient?.ingestSseChunk(rawChunk);
			},

			setPlatformClient: (client) => {
				platformClient = client;
			},

			submitCaptainHoldDecision: submitDecisionInternal,
			submitDecision: submitDecisionInternal,

			getPlatformClient: () => platformClient,
			getSseClient: () => sseClient,
			getMockReplayClient: () => mockClient,
			getLedgerReplayClient: () => mockClient,
			getEventsForActiveView: () =>
				get().eventsByView[get().activeViewId] ?? [],
		};
	});

	api.getInitialState = () => api.getState();

	const useBoundStore = (<T = WorkbenchStoreState>(
		selector?: (state: WorkbenchStoreState) => T,
	): T =>
		useStore(
			api,
			(selector ?? ((s: WorkbenchStoreState) => s as unknown as T)) as (
				state: WorkbenchStoreState,
			) => T,
		)) as UseBoundStore<StoreApi<WorkbenchStoreState>>;

	Object.assign(useBoundStore, api);
	return useBoundStore;
}

export const useWorkbenchStore = createWorkbenchStore();
export const defaultWorkbenchStore = useWorkbenchStore;
