/**
 * Studio Job Monitor Payload Processing Helper
 * (apps/opendv-web-workbench/src/ontology/store/job-monitor-helpers.ts)
 */

import type { StoreGet, StoreSet } from "./store-types";
import type { EvidenceChunk, StudioEventLogEntry } from "./types";

const BUDGET_STOP_RE =
	/(?:TOKEN_BUDGET_EXCEEDED|COST_BUDGET_EXCEEDED|MAX_TURNS_EXCEEDED|TIMEOUT)/i;

export function extractActiveSessionJobId(
	status: number,
	err: {
		active_job_id?: string | undefined;
		job_id?: string | undefined;
		detail?: string | undefined;
		code?: string | undefined;
		error_code?: string | undefined;
	},
): string | null {
	const code = err.code || err.error_code || "";
	if (status !== 409 && code !== "SESSION_JOB_ACTIVE") return null;
	if (code && code !== "SESSION_JOB_ACTIVE") return null;
	return (
		err.active_job_id ||
		err.job_id ||
		/job-[a-z0-9-]+/i.exec(err.detail || "")?.[0] ||
		"active-session-job"
	);
}

export async function processJobPayloadUpdate(params: {
	set: StoreSet;
	get: StoreGet;
	appendSseEvent: (
		entry: Omit<StudioEventLogEntry, "id" | "timestamp">,
	) => void;
	handleAutoCommittedCompletion: (
		sid: string,
		fetchImpl: typeof fetch,
	) => Promise<void>;
	getCurrentGen: () => number;
	sid: string;
	jobId: string;
	jobKind: "parse" | "clarify" | "construct";
	payload: Record<string, unknown>;
	fetchImpl: typeof fetch;
	opts?: { eventType?: string; autoCommit?: boolean; myGen?: number };
}): Promise<boolean> {
	const {
		set,
		get,
		appendSseEvent,
		handleAutoCommittedCompletion,
		getCurrentGen,
		sid,
		jobId,
		jobKind,
		payload,
		fetchImpl,
		opts,
	} = params;

	const myGen = opts?.myGen ?? getCurrentGen();
	if (myGen !== getCurrentGen()) return true;

	const eventType =
		opts?.eventType || (typeof payload.event === "string" ? payload.event : "");
	const rawStatus = String(payload.status || payload.state || "").toUpperCase();

	const tokUsage =
		payload.token_usage && typeof payload.token_usage === "object"
			? (payload.token_usage as Record<string, unknown>)
			: null;
	const liveTokens = Number(
		payload.total_tokens ?? tokUsage?.total_tokens ?? 0,
	);
	const liveCost = Number(
		payload.cumulative_cost_usd ?? payload.cost_usd ?? tokUsage?.cost_usd ?? 0,
	);
	if (liveTokens > 0 || liveCost > 0) {
		set((state) => ({
			sessionCostTokens: Math.max(state.sessionCostTokens, liveTokens),
			sessionCostUsd: Math.max(state.sessionCostUsd, liveCost),
		}));
	}

	if (eventType === "budget_hold") {
		set((state) => ({
			budgetHold: {
				job_id: jobId,
				reason: String(
					payload.escalation_reason ||
						payload.reason ||
						"TOKEN_BUDGET_EXCEEDED",
				),
				total_tokens: Math.max(liveTokens, state.sessionCostTokens),
				cost_usd: Math.max(liveCost, state.sessionCostUsd),
			},
		}));
	}

	const isTerminalSuccess =
		eventType === "job_completed" ||
		rawStatus === "COMPLETED" ||
		rawStatus === "SUCCEEDED" ||
		rawStatus === "DONE";
	const isTerminalFailure =
		eventType === "job_failed" ||
		eventType === "job_cancelled" ||
		eventType === "JOB_ABANDONED_SERVER_CRASH" ||
		rawStatus === "FAILED" ||
		rawStatus === "ERROR" ||
		rawStatus === "CANCELLED" ||
		rawStatus === "CANCELED" ||
		rawStatus === "ABANDONED" ||
		rawStatus === "JOB_ABANDONED_SERVER_CRASH";

	if (get().activeSession?.session_id !== sid) {
		return isTerminalSuccess || isTerminalFailure;
	}

	const resultObj =
		payload.result && typeof payload.result === "object"
			? (payload.result as Record<string, unknown>)
			: payload;
	const stage = String(payload.stage || payload.step || eventType || jobKind);
	const pct =
		typeof payload.pct_complete === "number"
			? payload.pct_complete
			: typeof payload.progress_pct === "number"
				? payload.progress_pct
				: typeof payload.progress === "number"
					? payload.progress
					: typeof resultObj.pct_complete === "number"
						? resultObj.pct_complete
						: typeof resultObj.progress_pct === "number"
							? resultObj.progress_pct
							: typeof resultObj.progress === "number"
								? resultObj.progress
								: undefined;
	const chunksCountRaw =
		typeof payload.chunks_count === "number"
			? payload.chunks_count
			: typeof resultObj.chunks_count === "number"
				? resultObj.chunks_count
				: undefined;

	appendSseEvent({
		job_id: jobId,
		stage,
		message: String(
			payload.message ||
				payload.detail ||
				payload.error ||
				`${jobKind} ${eventType || rawStatus || stage}`,
		),
		progress_pct: pct,
		level: isTerminalFailure ? "error" : isTerminalSuccess ? "success" : "info",
	});

	if (isTerminalFailure) {
		if (myGen !== getCurrentGen() || get().activeSession?.session_id !== sid) {
			return true;
		}
		const isCrashAbandoned =
			eventType === "JOB_ABANDONED_SERVER_CRASH" ||
			rawStatus === "JOB_ABANDONED_SERVER_CRASH";
		const isCancelled =
			eventType === "job_cancelled" ||
			rawStatus === "CANCELLED" ||
			rawStatus === "CANCELED";
		const errMsg = String(
			payload.error ||
				payload.detail ||
				payload.message ||
				(isCrashAbandoned
					? "Background job was abandoned after a server restart."
					: isCancelled
						? `${jobKind.charAt(0).toUpperCase() + jobKind.slice(1)} job was cancelled.`
						: `${jobKind.charAt(0).toUpperCase() + jobKind.slice(1)} job failed`),
		);
		const budgetMatch = BUDGET_STOP_RE.exec(errMsg);
		set((state) => ({
			activeSessionJobId: null,
			...(budgetMatch && !state.budgetHold
				? {
						budgetHold: {
							job_id: jobId,
							reason: budgetMatch[0].toUpperCase(),
							total_tokens: state.sessionCostTokens,
							cost_usd: state.sessionCostUsd,
						},
					}
				: {}),
			...(jobKind === "clarify" ? { isClarifying: false } : {}),
			...(jobKind === "construct" ? { isConstructing: false } : {}),
			...(jobKind === "parse" || state.parsingState.status === "parsing"
				? {
						parsingState: {
							...state.parsingState,
							status: "failed" as const,
							stage: "error",
							error: errMsg,
						},
					}
				: {}),
		}));
		get().addToast({
			type: "urn:opendv:error:job-failed",
			title: `${jobKind.charAt(0).toUpperCase() + jobKind.slice(1)} Job Failed`,
			status: 500,
			detail: errMsg,
			code: isCrashAbandoned
				? "JOB_ABANDONED_SERVER_CRASH"
				: isCancelled
					? "JOB_CANCELLED"
					: String(payload.code || "JOB_FAILED"),
		});
		return true;
	}

	if (jobKind === "parse") {
		const chunks = Array.isArray(resultObj.evidence_chunks)
			? (resultObj.evidence_chunks as EvidenceChunk[])
			: Array.isArray(payload.evidence_chunks)
				? (payload.evidence_chunks as EvidenceChunk[])
				: undefined;
		if (isTerminalSuccess) {
			if (
				myGen !== getCurrentGen() ||
				get().activeSession?.session_id !== sid
			) {
				return true;
			}
			set((state) => ({
				activeSessionJobId: null,
				parsingState: {
					...state.parsingState,
					status: "completed",
					progress_pct: 100,
					stage: "completed",
					evidence_chunks: chunks ?? state.parsingState.evidence_chunks,
					chunks_count:
						chunksCountRaw ??
						(chunks ? chunks.length : state.parsingState.chunks_count),
					error: null,
				},
			}));
			void Promise.all([
				get().loadEvidenceTables(fetchImpl, sid),
				get().loadEvidenceChunks(fetchImpl, sid),
			]);
			return true;
		}
		if (myGen !== getCurrentGen() || get().activeSession?.session_id !== sid) {
			return false;
		}
		set((state) => {
			const nextChunksCount = chunks
				? chunks.length
				: chunksCountRaw !== undefined
					? chunksCountRaw
					: eventType === "chunk_parsed"
						? (state.parsingState.chunks_count || 0) + 1
						: state.parsingState.chunks_count;
			return {
				parsingState: {
					...state.parsingState,
					status: "parsing",
					stage: stage || state.parsingState.stage,
					progress_pct: pct ?? state.parsingState.progress_pct,
					...(chunks ? { evidence_chunks: chunks } : {}),
					...(nextChunksCount !== undefined
						? { chunks_count: nextChunksCount }
						: {}),
				},
			};
		});
		return false;
	}

	if (jobKind === "clarify") {
		if (isTerminalSuccess) {
			if (
				myGen === getCurrentGen() &&
				get().activeSession?.session_id === sid
			) {
				set({ isClarifying: false, activeSessionJobId: null });
				await get().loadClarifications(fetchImpl, sid);
			}
			return true;
		}
		return false;
	}

	if (jobKind === "construct") {
		if (isTerminalSuccess) {
			if (
				myGen === getCurrentGen() &&
				get().activeSession?.session_id === sid
			) {
				set({ isConstructing: false, activeSessionJobId: null });
				if (opts?.autoCommit || Boolean(resultObj.auto_committed)) {
					await handleAutoCommittedCompletion(sid, fetchImpl);
				} else {
					await Promise.all([
						get().loadProposals(fetchImpl, sid, { selectNewestPending: true }),
						get().loadClarifications(fetchImpl, sid),
						get().loadStagedCandidates(fetchImpl, sid),
					]);
				}
			}
			return true;
		}
		return false;
	}
	return false;
}
