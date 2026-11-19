/**
 * Studio Background Job SSE & Polling Monitor Controller
 * (apps/opendv-web-workbench/src/ontology/store/job-monitor.ts)
 */

import { processJobPayloadUpdate } from "./job-monitor-helpers";
import type { StoreGet, StoreSet } from "./store-types";
import type { StudioFetcher } from "./studio-fetch";
import type { StudioEventLogEntry, StudioStoreOptions } from "./types";

const INITIAL_EVENT_SOURCE = (globalThis as Record<string, unknown>)
	.EventSource;

export interface JobMonitorController {
	nextJobGeneration: () => number;
	currentJobGeneration: () => number;
	appendSseEvent: (
		entry: Omit<StudioEventLogEntry, "id" | "timestamp">,
	) => void;
	cancelActiveJobWatchers: () => void;
	monitorStudioJob: (
		sid: string,
		jobId: string,
		jobKind: "parse" | "clarify" | "construct",
		fetchImpl: typeof fetch,
		jobOpts?: { autoCommit?: boolean; myGen?: number },
	) => void;
	handleAutoCommittedCompletion: (
		sid: string,
		fetchImpl: typeof fetch,
	) => Promise<void>;
}

export function createJobMonitorController(
	set: StoreSet,
	get: StoreGet,
	studioFetch: StudioFetcher,
	resolveUrl: (path: string) => string,
	options: StudioStoreOptions,
): JobMonitorController {
	let activeJobGeneration = 0;
	const activeEventSources = new Set<{ close: () => void }>();
	const activePollTimers = new Set<ReturnType<typeof setTimeout>>();

	const appendSseEvent = (
		entry: Omit<StudioEventLogEntry, "id" | "timestamp">,
	) => {
		const newEntry: StudioEventLogEntry = {
			...entry,
			id: `evt-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
			timestamp: new Date().toISOString(),
		};
		set((state) => ({
			sseEvents: [newEntry, ...state.sseEvents].slice(0, 50),
		}));
	};

	const cancelActiveJobWatchers = () => {
		for (const es of activeEventSources) {
			try {
				es.close();
			} catch {
				// ignore cleanup errors
			}
		}
		activeEventSources.clear();
		for (const timer of activePollTimers) {
			clearTimeout(timer);
		}
		activePollTimers.clear();
	};

	const handleAutoCommittedCompletion = async (
		sid: string,
		fetchImpl: typeof fetch,
	): Promise<void> => {
		if (get().hasUnsavedChanges) {
			await get().loadSessions(fetchImpl);
			if (get().activeSession?.session_id === sid) {
				get().addToast({
					type: "urn:opendv:warning:server-head-advanced",
					title: "New Version Committed on Server",
					status: 409,
					detail:
						"Auto-commit construction committed a new ontology version on the server while unsaved canvas edits exist. Your local draft was preserved — review, rebase, or discard local changes to load the latest head.",
					code: "SERVER_HEAD_ADVANCED",
				});
			}
			return;
		}
		await get().selectSession(sid, fetchImpl, true);
	};

	const handleJobPayloadUpdate = (
		sid: string,
		jobId: string,
		jobKind: "parse" | "clarify" | "construct",
		payload: Record<string, unknown>,
		fetchImpl: typeof fetch,
		opts?: { eventType?: string; autoCommit?: boolean; myGen?: number },
	): Promise<boolean> =>
		processJobPayloadUpdate({
			set,
			get,
			appendSseEvent,
			handleAutoCommittedCompletion,
			getCurrentGen: () => activeJobGeneration,
			sid,
			jobId,
			jobKind,
			payload,
			fetchImpl,
			...(opts !== undefined ? { opts } : {}),
		});

	const monitorStudioJob: JobMonitorController["monitorStudioJob"] = (
		sid,
		jobId,
		jobKind,
		fetchImpl,
		jobOpts,
	) => {
		if (!jobId) return;
		const myGen = jobOpts?.myGen ?? activeJobGeneration;

		const schedulePoll = (fn: () => void, delayMs: number) => {
			if (myGen !== activeJobGeneration) return;
			const timer = setTimeout(() => {
				activePollTimers.delete(timer);
				if (myGen !== activeJobGeneration) return;
				fn();
			}, delayMs);
			activePollTimers.add(timer);
		};

		const failWatchedJob = (
			reason: string,
			code = "JOB_POLLING_FAILED",
			status = 504,
		) => {
			if (
				myGen !== activeJobGeneration ||
				get().activeSession?.session_id !== sid
			) {
				return;
			}
			appendSseEvent({
				job_id: jobId,
				stage: "error",
				message: reason,
				level: "error",
			});
			set((state) => ({
				activeSessionJobId: null,
				...(jobKind === "clarify" ? { isClarifying: false } : {}),
				...(jobKind === "construct" ? { isConstructing: false } : {}),
				...(jobKind === "parse" || state.parsingState.status === "parsing"
					? {
							parsingState: {
								...state.parsingState,
								status: "failed" as const,
								stage: "error",
								error: reason,
							},
						}
					: {}),
			}));
			get().addToast({
				type: "urn:opendv:error:job-failed",
				title: `${jobKind.charAt(0).toUpperCase() + jobKind.slice(1)} Job Failed`,
				status,
				detail: reason,
				code,
			});
		};

		const startPollingFallback = () => {
			const startedAt = Date.now();
			let attempts = 0;
			let consecutiveErrors = 0;
			const maxDurationMs = options.maxPollingDurationMs ?? 600_000;
			const maxAttempts = options.maxPollAttempts ?? Number.POSITIVE_INFINITY;
			const maxErrorRetries = options.maxPollErrorRetries ?? 5;
			const initialIntervalMs = options.initialPollIntervalMs ?? 200;
			const maxIntervalMs = options.maxPollIntervalMs ?? 1000;

			const pollOnce = async () => {
				if (
					myGen !== activeJobGeneration ||
					get().activeSession?.session_id !== sid
				) {
					return;
				}
				if (jobKind === "parse" && get().parsingState.status === "completed") {
					return;
				}
				if (attempts >= maxAttempts || Date.now() - startedAt > maxDurationMs) {
					if (myGen === activeJobGeneration) {
						failWatchedJob(
							`Timed out waiting for ${jobKind} job ${jobId} to complete.`,
							"JOB_POLL_TIMEOUT",
							504,
						);
					}
					return;
				}
				attempts += 1;
				const nextDelay =
					attempts <= 5
						? initialIntervalMs
						: Math.min(
								maxIntervalMs,
								initialIntervalMs * 2 ** Math.min(attempts - 5, 4),
							);
				try {
					const pollRes = await studioFetch(
						fetchImpl,
						`/api/v1/ontology/studio/jobs/${encodeURIComponent(jobId)}`,
					);
					if (
						myGen !== activeJobGeneration ||
						get().activeSession?.session_id !== sid
					) {
						return;
					}
					if (
						jobKind === "parse" &&
						get().parsingState.status === "completed"
					) {
						return;
					}
					if (!pollRes.ok) {
						if (
							pollRes.status === 429 ||
							pollRes.status === 502 ||
							pollRes.status === 503 ||
							pollRes.status === 504
						) {
							consecutiveErrors += 1;
							if (consecutiveErrors <= maxErrorRetries) {
								schedulePoll(() => {
									void pollOnce();
								}, nextDelay);
								return;
							}
						}
						const errData = (await pollRes.json().catch(() => ({}))) as {
							detail?: string;
							error?: string;
							code?: string;
						};
						if (
							myGen !== activeJobGeneration ||
							get().activeSession?.session_id !== sid
						) {
							return;
						}
						failWatchedJob(
							errData.detail ||
								errData.error ||
								`Polling ${jobKind} job ${jobId} failed with HTTP ${pollRes.status}.`,
							errData.code || "JOB_POLL_HTTP_ERROR",
							pollRes.status,
						);
						return;
					}
					consecutiveErrors = 0;
					const pollData = (await pollRes.json().catch(() => null)) as Record<
						string,
						unknown
					> | null;
					if (
						myGen !== activeJobGeneration ||
						get().activeSession?.session_id !== sid
					) {
						return;
					}
					if (
						!pollData ||
						(pollData.status === undefined &&
							pollData.state === undefined &&
							pollData.pct_complete === undefined &&
							pollData.progress_pct === undefined &&
							pollData.progress === undefined &&
							pollData.chunks_count === undefined &&
							pollData.result === undefined &&
							pollData.error === undefined)
					) {
						return;
					}
					const done = await handleJobPayloadUpdate(
						sid,
						jobId,
						jobKind,
						pollData,
						fetchImpl,
						{
							...(jobOpts?.autoCommit ? { autoCommit: true } : {}),
							myGen,
						},
					);
					if (
						!done &&
						myGen === activeJobGeneration &&
						get().activeSession?.session_id === sid
					) {
						schedulePoll(() => {
							void pollOnce();
						}, nextDelay);
					}
				} catch (err) {
					if (
						myGen !== activeJobGeneration ||
						get().activeSession?.session_id !== sid
					) {
						return;
					}
					consecutiveErrors += 1;
					if (consecutiveErrors <= maxErrorRetries) {
						schedulePoll(() => {
							void pollOnce();
						}, nextDelay);
						return;
					}
					failWatchedJob(
						`Network error while polling ${jobKind} job ${jobId}: ${String(err)}`,
						"NETWORK_ERROR",
						503,
					);
				}
			};
			schedulePoll(
				() => {
					void pollOnce();
				},
				Math.min(50, initialIntervalMs),
			);
		};

		const currentEs = (globalThis as Record<string, unknown>).EventSource;
		const isCustomMockEs =
			typeof currentEs === "function" && currentEs !== INITIAL_EVENT_SOURCE;
		const forceMockEs = Boolean(
			(globalThis as Record<string, unknown>).__FORCE_STUDIO_EVENTSOURCE__,
		);
		const canUseEventSource =
			typeof EventSource !== "undefined" &&
			(fetchImpl === fetch || isCustomMockEs || forceMockEs);

		if (canUseEventSource) {
			try {
				const esUrl = resolveUrl(
					`/api/v1/ontology/studio/jobs/${encodeURIComponent(jobId)}/events`,
				);
				const es = new EventSource(esUrl, { withCredentials: true });
				activeEventSources.add(es);
				let settled = false;

				const cleanupEs = () => {
					activeEventSources.delete(es);
					try {
						es.close();
					} catch {
						// ignore
					}
				};

				const onEventData = async (ev: MessageEvent, eventType?: string) => {
					if (myGen !== activeJobGeneration) {
						settled = true;
						cleanupEs();
						return;
					}
					let parsed: Record<string, unknown> | null = null;
					const rawData =
						ev.data !== undefined && ev.data !== null ? String(ev.data) : "";
					if (rawData.trim()) {
						try {
							const candidate = JSON.parse(rawData) as unknown;
							if (
								candidate &&
								typeof candidate === "object" &&
								!Array.isArray(candidate)
							) {
								parsed = candidate as Record<string, unknown>;
							} else if (eventType) {
								parsed = { message: rawData };
							}
						} catch {
							if (eventType) {
								parsed = { message: rawData };
							}
						}
					} else if (eventType) {
						parsed = {};
					}
					if (!parsed) return;

					const done = await handleJobPayloadUpdate(
						sid,
						jobId,
						jobKind,
						parsed,
						fetchImpl,
						{
							...(eventType ? { eventType } : {}),
							...(jobOpts?.autoCommit ? { autoCommit: true } : {}),
							myGen,
						},
					);
					if (done) {
						settled = true;
						cleanupEs();
					}
				};

				es.onmessage = (ev) => {
					void onEventData(ev);
				};
				const namedSseEvents = [
					"job_started",
					"stage",
					"stage_update",
					"chunk_parsed",
					"clarification_generated",
					"agent_stream",
					"agent_telemetry",
					"budget_hold",
					"fibo_grounding",
					"deterministic_construct",
					"noop_construct",
					"version_committed",
					"job_completed",
					"job_failed",
					"job_cancelled",
					"JOB_ABANDONED_SERVER_CRASH",
					"progress",
					"complete",
					"completed",
				] as const;
				for (const evtName of namedSseEvents) {
					es.addEventListener(evtName, (ev) => {
						void onEventData(ev as MessageEvent, evtName);
					});
				}
				es.onerror = () => {
					cleanupEs();
					if (
						!settled &&
						myGen === activeJobGeneration &&
						get().activeSession?.session_id === sid
					) {
						startPollingFallback();
					}
				};
				return;
			} catch {
				if (
					myGen === activeJobGeneration &&
					get().activeSession?.session_id === sid
				) {
					startPollingFallback();
				}
				return;
			}
		}

		if (
			myGen === activeJobGeneration &&
			get().activeSession?.session_id === sid
		) {
			startPollingFallback();
		}
	};

	return {
		nextJobGeneration: () => {
			activeJobGeneration += 1;
			return activeJobGeneration;
		},
		currentJobGeneration: () => activeJobGeneration,
		appendSseEvent,
		cancelActiveJobWatchers,
		monitorStudioJob,
		handleAutoCommittedCompletion,
	};
}
