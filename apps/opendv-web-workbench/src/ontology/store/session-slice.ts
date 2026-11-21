/**
 * Studio Session Lifecycle, Ontology Import, and Starter Packs Slice
 * (apps/opendv-web-workbench/src/ontology/store/session-slice.ts)
 */

import { createEmptyZoneSqlByDialect } from "../wizard/step6-run-hydration";
import { createDefaultDiffState } from "./graph-diff";
import type { JobMonitorController } from "./job-monitor";
import { createDefaultQualityScorecard } from "./scorecard";
import { createDefaultParsingState } from "./sources-preview-slice";
import type { StoreGet, StoreSet } from "./store-types";
import { type StudioFetcher, normalizeStudioRole } from "./studio-fetch";
import type {
	CreateSessionRequest,
	ImportOntologyRequest,
	StarterPackMetadata,
	StudioMode,
	StudioSessionSummary,
	StudioStoreOptions,
	StudioUserRole,
} from "./types";

export function createSessionSlice(
	set: StoreSet,
	get: StoreGet,
	studioFetch: StudioFetcher,
	jobMonitor: JobMonitorController,
	options: StudioStoreOptions,
	bumpSessionReqId: () => number,
	getSessionReqId: () => number,
) {
	return {
		activeMode: (options.activeMode ?? "local_standalone") as StudioMode,
		userRole: (options.userRole ?? "editor") as StudioUserRole,
		activeSession: options.initialSession ?? null,
		sessions: options.initialSession ? [options.initialSession] : [],
		legacySessionCount: 0,
		starterPacks: [] as StarterPackMetadata[],
		isLoadingSessions: false,

		setMode: (mode: StudioMode) => set({ activeMode: mode }),
		setUserRole: (role: StudioUserRole) => set({ userRole: role }),

		loadSessions: async (
			fetchImpl = fetch,
			opts?: { wizardOnly?: boolean },
		) => {
			set({ isLoadingSessions: true });
			try {
				const query = opts?.wizardOnly ? "?wizard_only=true" : "";
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions${query}`,
				);
				if (!res.ok) {
					const errorJson = (await res.json().catch(() => ({}))) as {
						detail?: string;
						code?: string;
						title?: string;
					};
					get().addToast({
						type: "urn:opendv:error:session-load-failed",
						title: errorJson.title || "Failed to Load Sessions",
						status: res.status,
						detail: errorJson.detail || `Server returned HTTP ${res.status}`,
						code: errorJson.code || "SESSION_NOT_FOUND",
					});
					set({ isLoadingSessions: false });
					return;
				}
				const data = (await res.json()) as
					| StudioSessionSummary[]
					| {
							sessions?: StudioSessionSummary[];
							legacy_session_count?: number;
							role?: string;
							user_role?: string;
							principal?: string;
					  };
				const sessionList = Array.isArray(data)
					? data
					: Array.isArray(data.sessions)
						? data.sessions
						: get().sessions;
				const legacyCount = !Array.isArray(data)
					? Number(data.legacy_session_count ?? 0)
					: 0;
				const hydratedRole = !Array.isArray(data)
					? normalizeStudioRole(data.user_role ?? data.role, data.principal)
					: undefined;
				const currentActive = get().activeSession;
				const refreshedActive = currentActive
					? sessionList.find((s) => s.session_id === currentActive.session_id)
					: undefined;
				const summaryHasHeadSha =
					typeof refreshedActive?.head_sha256 === "string" &&
					refreshedActive.head_sha256.length > 0;
				const activeHeadChanged = Boolean(
					currentActive &&
						refreshedActive &&
						((summaryHasHeadSha &&
							refreshedActive.head_sha256 !== currentActive.head_sha256) ||
							refreshedActive.active_version !== currentActive.active_version),
				);
				const isDirty = get().hasUnsavedChanges;

				const nextActiveSession = currentActive
					? refreshedActive
						? {
								...currentActive,
								...refreshedActive,
								head_sha256:
									activeHeadChanged && isDirty
										? currentActive.head_sha256
										: summaryHasHeadSha
											? refreshedActive.head_sha256
											: currentActive.head_sha256,
								active_version:
									activeHeadChanged && isDirty
										? currentActive.active_version
										: refreshedActive.active_version,
							}
						: currentActive
					: (sessionList[0] ?? null);

				set({
					sessions: sessionList,
					legacySessionCount: legacyCount,
					isLoadingSessions: false,
					activeSession: nextActiveSession,
					...(hydratedRole ? { userRole: hydratedRole } : {}),
				});

				const hasActiveBackgroundJob =
					get().isClarifying ||
					get().isConstructing ||
					get().parsingState.status === "parsing";

				if (
					nextActiveSession &&
					(!currentActive ||
						currentActive.session_id !== nextActiveSession.session_id)
				) {
					await get().selectSession(nextActiveSession.session_id, fetchImpl);
				} else if (
					currentActive &&
					activeHeadChanged &&
					!isDirty &&
					!hasActiveBackgroundJob
				) {
					await get().selectSession(currentActive.session_id, fetchImpl, true);
				}
			} catch (err) {
				get().addToast({
					type: "urn:opendv:error:network",
					title: "Network Error",
					status: 503,
					detail: String(err),
					code: "NETWORK_ERROR",
				});
				set({ isLoadingSessions: false });
			}
		},

		selectSession: async (
			sessionId: string,
			fetchImpl = fetch,
			force = false,
		) => {
			if (get().hasUnsavedChanges && !force) {
				get().addToast({
					type: "urn:opendv:error:unsaved-changes",
					title: "Unsaved Changes",
					status: 400,
					detail:
						"Cannot switch session with unsaved changes. Please commit, discard, or specify force=true.",
					code: "UNSAVED_CHANGES",
				});
				return;
			}

			const prevSessionId = get().activeSession?.session_id;
			if (sessionId !== prevSessionId) {
				jobMonitor.nextJobGeneration();
				jobMonitor.cancelActiveJobWatchers();
			}

			const reqId = bumpSessionReqId();
			const found = get().sessions.find((s) => s.session_id === sessionId);
			const sessionWaivers = get().sessionWaivers || {};
			const savedWaiver = sessionWaivers[sessionId];
			const initialScorecard = createDefaultQualityScorecard();
			if (savedWaiver) {
				initialScorecard.waiver_applied = savedWaiver.waiver_applied;
				initialScorecard.waiver_rationale = savedWaiver.waiver_rationale;
				initialScorecard.waived_concept_uris = savedWaiver.waived_concept_uris;
			}

			set({
				activeSession: found ?? null,
				isLoadingSessions: true,
				isLoadingSources: false,
				isLoadingGraph: false,
				isLoadingClarifications: false,
				isLoadingProposals: false,
				isClarifying: false,
				isConstructing: false,
				activeSessionJobId: null,
				agentErrorCode: null,
				agentErrorDetail: null,
				budgetHold: null,
				hasUnsavedChanges: false,
				viewedVersion: null,
				isHistoricalView: false,
				nodes: [],
				edges: [],
				committedNodes: [],
				committedEdges: [],
				clarifications: [],
				proposals: [],
				stagedCandidates: [],
				selectedProposalId: null,
				...(sessionId !== prevSessionId
					? {
							proposalDrafts: {},
							activeRunId: null,
							isRunningPipeline: false,
							stageStatuses: {},
							topologyRows: [],
							zoneSqlByZone: {},
							zoneSqlByDialect: createEmptyZoneSqlByDialect(),
							gateScorecard: null,
							sessionRuns: [],
							activeHold: null,
							driftColumns: [],
							layout: {
								...get().layout,
								rightDrawerTab: "concept",
								rightDrawerOpen: false,
							},
						}
					: {}),
				sources: [],
				evidenceTables: [],
				evidenceChunks: [],
				observedTermCounts: {},
				answers: {},
				answers_sha256: undefined,
				parsingState: createDefaultParsingState(),
				selectedNodeId: null,
				selectedEdgeId: null,
				qualityScorecard: initialScorecard,
				diffState: createDefaultDiffState(),
			});

			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(sessionId)}`,
				);
				if (reqId !== getSessionReqId()) return;

				if (res.ok) {
					const raw = (await res.json().catch(() => null)) as unknown;
					if (reqId !== getSessionReqId()) return;

					const sessionData = (
						Array.isArray(raw)
							? raw[0]
							: ((raw as { session?: StudioSessionSummary })?.session ?? raw)
					) as StudioSessionSummary | null | undefined;

					if (sessionData?.session_id) {
						const rawObj = (
							raw && typeof raw === "object" ? raw : {}
						) as Record<string, unknown>;
						const sessionObj = sessionData as unknown as Record<
							string,
							unknown
						>;
						const hydratedRole = normalizeStudioRole(
							sessionObj.user_role ??
								sessionObj.role ??
								rawObj.user_role ??
								rawObj.role,
							sessionObj.principal ?? rawObj.principal,
						);
						const serverWaiver = (sessionObj.active_waiver ||
							rawObj.active_waiver ||
							(sessionObj.waiver_applied ? sessionObj : null)) as {
							waiver_applied?: boolean;
							waiver_rationale?: string;
							waived_concept_uris?: string[];
							waived_base_sha256?: string;
						} | null;

						const isWaiverFresh =
							Boolean(serverWaiver?.waiver_applied) &&
							(!serverWaiver?.waived_base_sha256 ||
								serverWaiver.waived_base_sha256 === sessionData.head_sha256);

						set((state) => {
							const updatedWaivers = isWaiverFresh
								? {
										...state.sessionWaivers,
										[sessionId]: {
											waiver_applied: true,
											waiver_rationale:
												serverWaiver?.waiver_rationale ||
												"Server authorized waiver",
											waived_concept_uris:
												serverWaiver?.waived_concept_uris || [],
											waived_base_sha256:
												serverWaiver?.waived_base_sha256 ||
												sessionData.head_sha256 ||
												"",
										},
									}
								: state.sessionWaivers;

							const updatedScorecard = isWaiverFresh
								? {
										...state.qualityScorecard,
										waiver_applied: true,
										waiver_rationale:
											serverWaiver?.waiver_rationale ||
											"Server authorized waiver",
										waived_concept_uris:
											serverWaiver?.waived_concept_uris || [],
										waived_base_sha256:
											serverWaiver?.waived_base_sha256 ||
											sessionData.head_sha256 ||
											"",
									}
								: state.qualityScorecard;

							return {
								activeSession: sessionData,
								sessions: state.sessions.some((s) => s.session_id === sessionId)
									? state.sessions.map((s) =>
											s.session_id === sessionId ? sessionData : s,
										)
									: [sessionData, ...state.sessions],
								sessionWaivers: updatedWaivers,
								qualityScorecard: updatedScorecard,
								isLoadingSessions: false,
								...(hydratedRole ? { userRole: hydratedRole } : {}),
							};
						});

						if (reqId !== getSessionReqId()) return;

						await Promise.all([
							get().loadGraph(sessionData.active_version, fetchImpl, sessionId),
							get().loadClarifications(fetchImpl, sessionId),
							get().loadSources(fetchImpl, sessionId),
							get().loadProposals(fetchImpl, sessionId),
							get().loadEvidenceTables(fetchImpl, sessionId),
							get().loadEvidenceChunks(fetchImpl, sessionId),
							get().hydrateSessionLatestRun(fetchImpl, sessionId),
						]);
					} else {
						set({ isLoadingSessions: false });
						get().addToast({
							type: "urn:opendv:error:invalid-session-data",
							title: "Invalid Session Metadata",
							status: 500,
							detail: `Received malformed session metadata for ${sessionId}`,
							code: "INVALID_SESSION_DATA",
						});
					}
				} else {
					set({ isLoadingSessions: false });
					const err = (await res.json().catch(() => ({}))) as {
						detail?: string;
						code?: string;
					};
					if (reqId !== getSessionReqId()) return;
					get().addToast({
						type: "urn:opendv:error:session-fetch-failed",
						title: "Failed to Select Session",
						status: res.status,
						detail:
							err.detail || `Session ${sessionId} returned HTTP ${res.status}`,
						code: err.code || "SESSION_NOT_FOUND",
					});
				}
			} catch (err) {
				if (reqId === getSessionReqId()) {
					set({ isLoadingSessions: false });
					get().addToast({
						type: "urn:opendv:error:network",
						title: "Session Selection Error",
						status: 503,
						detail: String(err),
						code: "NETWORK_ERROR",
					});
				}
			}
		},

		createSession: async (
			req: CreateSessionRequest,
			fetchImpl = fetch,
			force = false,
		) => {
			if (get().hasUnsavedChanges && !force) {
				get().addToast({
					type: "urn:opendv:error:unsaved-changes",
					title: "Unsaved Changes",
					status: 400,
					detail:
						"Cannot create new session with unsaved changes. Please commit, discard, or specify force=true.",
					code: "UNSAVED_CHANGES",
				});
				return null;
			}

			if (req.session_id && !/^[a-z0-9_-]+$/.test(req.session_id)) {
				get().addToast({
					type: "urn:opendv:error:invalid-session-id",
					title: "Invalid Session Identifier",
					status: 400,
					detail:
						"Session ID must match regex ^[a-z0-9_-]+$ with lowercase letters, numbers, underscores, and dashes.",
					code: "SYNTAX_INVALID_ARGUMENT",
				});
				return null;
			}

			try {
				const res = await studioFetch(
					fetchImpl,
					"/api/v1/ontology/studio/sessions",
					{
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify(req),
					},
				);
				if (!res.ok) {
					const err = (await res.json().catch(() => ({}))) as {
						detail?: string;
						code?: string;
						title?: string;
					};
					get().addToast({
						type: "urn:opendv:error:session-create",
						title: err.title || "Failed to Create Session",
						status: res.status,
						detail: err.detail || `Server returned HTTP ${res.status}`,
						code: err.code || "SESSION_ALREADY_EXISTS",
					});
					return null;
				}
				const created = (await res.json()) as StudioSessionSummary;
				set((state) => ({
					sessions: [created, ...state.sessions],
					layout: { ...state.layout, activeModal: null },
				}));
				await get().selectSession(created.session_id, fetchImpl, true);
				return created;
			} catch (err) {
				get().addToast({
					type: "urn:opendv:error:network",
					title: "Network Error",
					status: 503,
					detail: String(err),
					code: "NETWORK_ERROR",
				});
				return null;
			}
		},

		importOntologySession: async (
			req: ImportOntologyRequest,
			fetchImpl = fetch,
		) => {
			try {
				const res = await studioFetch(
					fetchImpl,
					"/api/v1/ontology/studio/sessions/import-ontology",
					{
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify(req),
					},
				);
				if (!res.ok) {
					const err = (await res.json().catch(() => ({}))) as {
						detail?: string;
						code?: string;
					};
					get().addToast({
						type: "urn:opendv:error:import-ontology",
						title: "Ontology Import Failed",
						status: res.status,
						detail: err.detail || `Server returned HTTP ${res.status}`,
						code: err.code || "INVALID_WORKSPACE_ONTOLOGY",
					});
					return null;
				}
				const created = (await res.json()) as StudioSessionSummary;
				set((state) => ({
					sessions: [created, ...state.sessions],
				}));
				await get().selectSession(created.session_id, fetchImpl, true);
				return created;
			} catch (err) {
				get().addToast({
					type: "urn:opendv:error:network",
					title: "Network Error",
					status: 503,
					detail: String(err),
					code: "NETWORK_ERROR",
				});
				return null;
			}
		},

		loadStarterPacks: async (fetchImpl = fetch) => {
			try {
				const res = await studioFetch(
					fetchImpl,
					"/api/v1/ontology/studio/starter-packs",
				);
				if (!res.ok) return;
				const data = (await res.json()) as { packs?: StarterPackMetadata[] };
				if (Array.isArray(data.packs)) {
					set({ starterPacks: data.packs });
				}
			} catch {
				// ignore offline starter pack load failure
			}
		},

		applyStarterPacks: async (packIds: string[], fetchImpl = fetch) => {
			const activeSession = get().activeSession;
			if (!activeSession || packIds.length === 0) return false;
			const headers: Record<string, string> = {
				"Content-Type": "application/json",
			};
			if (activeSession.head_sha256) {
				headers["If-Match"] = `"${activeSession.head_sha256}"`;
			}
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(activeSession.session_id)}/starter-packs/apply`,
					{
						method: "POST",
						headers,
						body: JSON.stringify({
							pack_ids: packIds,
							base_version: activeSession.active_version,
							base_sha256: activeSession.head_sha256,
						}),
					},
				);
				if (!res.ok) return false;
				await get().selectSession(activeSession.session_id, fetchImpl, true);
				return true;
			} catch {
				return false;
			}
		},
	};
}
