/**
 * Agent Discovery, Clarifications, Staged Candidates, Citations & FIBO Q&A Slice
 * (apps/opendv-web-workbench/src/ontology/store/agent-discovery-slice.ts)
 */

import { normalizeClarificationsResponse } from "./clarification-normalizer";
import { createFiboQaActions } from "./fibo-qa-actions";
import type { JobMonitorController } from "./job-monitor";
import { extractActiveSessionJobId } from "./job-monitor-helpers";
import { EMPTY_ANSWERS_SHA256 } from "./scorecard";
import type { StoreGet, StoreSet } from "./store-types";
import type { StudioFetcher } from "./studio-fetch";
import type {
	BudgetHoldInfo,
	ClarificationAnswer,
	ClarificationQuestion,
	FiboQaResult,
	ResolvedCitationItem,
	StagedCandidateDTO,
} from "./types";

export function createAgentDiscoverySlice(
	set: StoreSet,
	get: StoreGet,
	studioFetch: StudioFetcher,
	jobMonitor: JobMonitorController,
	bumpClarifyReqId: () => number,
	getClarifyReqId: () => number,
) {
	return {
		clarifications: [] as ClarificationQuestion[],
		answers: {} as Record<string, ClarificationAnswer>,
		answers_sha256: undefined as string | undefined,
		isLoadingClarifications: false,
		isClarifying: false,
		isConstructing: false,
		activeSessionJobId: null as string | null,
		agentErrorCode: null as string | null,
		agentErrorDetail: null as string | null,
		budgetHold: null as BudgetHoldInfo | null,
		stagedCandidates: [] as StagedCandidateDTO[],
		fiboQaResult: null as FiboQaResult | null,
		resolvedCitations: [] as ResolvedCitationItem[],

		clearAgentError: () =>
			set({ agentErrorCode: null, agentErrorDetail: null }),
		clearBudgetHold: () => set({ budgetHold: null }),

		loadClarifications: async (fetchImpl = fetch, targetSessionId?: string) => {
			const activeSession = get().activeSession;
			if (!activeSession) return;
			const expectedId = targetSessionId ?? activeSession.session_id;
			if (activeSession.session_id !== expectedId) return;

			const reqId = bumpClarifyReqId();
			set({ isLoadingClarifications: true });
			try {
				const [res, answersRes] = await Promise.all([
					studioFetch(
						fetchImpl,
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(expectedId)}/questions`,
					),
					studioFetch(
						fetchImpl,
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(expectedId)}/answers`,
					).catch(() => null),
				]);

				if (!res.ok) {
					if (
						reqId === getClarifyReqId() &&
						get().activeSession?.session_id === expectedId
					) {
						set({ isLoadingClarifications: false });
						const err = (await res.json().catch(() => ({}))) as {
							detail?: string;
							code?: string;
						};
						get().addToast({
							type: "urn:opendv:error:clarifications-load-failed",
							title: "Failed to Load Clarifications",
							status: res.status,
							detail: err.detail || `Server returned HTTP ${res.status}`,
							code: err.code || "CLARIFICATIONS_LOAD_FAILED",
						});
					}
					return;
				}

				if (
					reqId !== getClarifyReqId() ||
					get().activeSession?.session_id !== expectedId
				)
					return;

				const questionsEtag = res.headers
					?.get?.("ETag")
					?.replace(/^W\//, "")
					.replace(/"/g, "");
				const answersEtag = answersRes?.headers
					?.get?.("ETag")
					?.replace(/^W\//, "")
					.replace(/"/g, "");

				const data = (await res.json()) as
					| Array<Record<string, unknown>>
					| {
							questions?: Array<Record<string, unknown>>;
							answers?:
								| Record<string, unknown>
								| Array<Record<string, unknown>>;
							answers_sha256?: string;
					  };

				let rawAnswersPayload: unknown = null;
				if (answersRes?.ok) {
					rawAnswersPayload = await answersRes.json().catch(() => null);
				}

				if (
					reqId !== getClarifyReqId() ||
					get().activeSession?.session_id !== expectedId
				)
					return;

				const { normalizedQuestions, answersMap, answersSha } =
					await normalizeClarificationsResponse(
						data,
						rawAnswersPayload,
						questionsEtag,
						answersEtag,
					);

				if (
					reqId !== getClarifyReqId() ||
					get().activeSession?.session_id !== expectedId
				)
					return;

				set({
					clarifications: normalizedQuestions,
					answers: answersMap,
					answers_sha256: answersSha,
					isLoadingClarifications: false,
				});
			} catch (err) {
				if (
					reqId === getClarifyReqId() &&
					get().activeSession?.session_id === expectedId
				) {
					set({ isLoadingClarifications: false });
					get().addToast({
						type: "urn:opendv:error:network",
						title: "Network Error",
						status: 503,
						detail: String(err),
						code: "NETWORK_ERROR",
					});
				}
			}
		},

		setAnswerDraft: (
			questionId: string,
			selectedOptionIds: string[],
			customAnswerText?: string,
		) => {
			set((state) => ({
				answers: {
					...state.answers,
					[questionId]: {
						question_id: questionId,
						selected_option_ids: selectedOptionIds,
						custom_answer_text: customAnswerText,
					},
				},
			}));
		},

		submitAnswer: async (
			answer: ClarificationAnswer,
			expectedAnswersSha256?: string,
			fetchImpl = fetch,
		) => {
			const activeSession = get().activeSession;
			if (!activeSession) return false;

			const sid = activeSession.session_id;
			const expectedSha =
				expectedAnswersSha256 || get().answers_sha256 || EMPTY_ANSWERS_SHA256;

			try {
				const payload: Record<string, unknown> = {
					answers: [
						{
							question_id: answer.question_id,
							selected_options: answer.selected_option_ids,
							user_text:
								answer.custom_answer_text !== undefined &&
								answer.custom_answer_text !== ""
									? answer.custom_answer_text
									: null,
						},
					],
					expected_answers_sha256: expectedSha,
				};

				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/answers`,
					{
						method: "POST",
						headers: {
							"Content-Type": "application/json",
							"If-Match": `"${expectedSha}"`,
						},
						body: JSON.stringify(payload),
					},
				);
				if (get().activeSession?.session_id !== sid) return false;

				if (!res.ok) {
					if (res.status === 409 || res.status === 412) {
						const currentDrafts = { ...get().answers };
						await get().loadClarifications(fetchImpl, sid);
						if (get().activeSession?.session_id !== sid) return false;
						set((state) => ({
							answers: { ...state.answers, ...currentDrafts },
						}));
					}

					const err = (await res.json().catch(() => ({}))) as {
						detail?: string;
						code?: string;
					};
					if (get().activeSession?.session_id !== sid) return false;
					get().addToast({
						type: "urn:opendv:error:answer-submit",
						title: "Failed to Submit Answer",
						status: res.status,
						detail: err.detail || `Server returned HTTP ${res.status}`,
						code: err.code || "SYNTAX_INVALID_ARGUMENT",
					});
					return false;
				}

				const updatedEtag = res.headers
					?.get?.("ETag")
					?.replace(/^W\//, "")
					.replace(/"/g, "");
				const resData = (await res.json().catch(() => ({}))) as {
					answers_sha256?: string;
				};
				if (get().activeSession?.session_id !== sid) return false;
				const newSha = resData.answers_sha256 || updatedEtag;
				const now = new Date().toISOString();

				set((state) => ({
					clarifications: state.clarifications.map((q) =>
						q.question_id === answer.question_id
							? {
									...q,
									status: "ANSWERED" as const,
									answered_at: now,
									selected_option_ids: answer.selected_option_ids,
									custom_answer_text: answer.custom_answer_text,
								}
							: q,
					),
					answers: {
						...state.answers,
						[answer.question_id]: { ...answer, answered_at: now },
					},
					answers_sha256: newSha || state.answers_sha256,
				}));
				return true;
			} catch (err) {
				if (get().activeSession?.session_id === sid) {
					get().addToast({
						type: "urn:opendv:error:network",
						title: "Network Error",
						status: 503,
						detail: String(err),
						code: "NETWORK_ERROR",
					});
				}
				return false;
			}
		},

		startClarify: async (
			clarifyOptions: {
				mode?: "auto" | "agent" | "deterministic";
				max_questions?: number;
			} = {},
			fetchImpl = fetch,
		) => {
			const activeSession = get().activeSession;
			if (!activeSession) return null;
			const sid = activeSession.session_id;
			const myGen = jobMonitor.nextJobGeneration();
			jobMonitor.cancelActiveJobWatchers();
			set({ isClarifying: true });
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/clarify`,
					{
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({
							mode: clarifyOptions.mode || "auto",
							max_questions: clarifyOptions.max_questions ?? 10,
						}),
					},
				);
				if (
					myGen !== jobMonitor.currentJobGeneration() ||
					get().activeSession?.session_id !== sid
				)
					return null;
				if (!res.ok) {
					const err = (await res.json().catch(() => ({}))) as {
						detail?: string;
						code?: string;
						error_code?: string;
						active_job_id?: string;
						job_id?: string;
					};
					if (
						myGen !== jobMonitor.currentJobGeneration() ||
						get().activeSession?.session_id !== sid
					)
						return null;
					const activeJobId = extractActiveSessionJobId(res.status, err);
					set({
						isClarifying: false,
						...(activeJobId ? { activeSessionJobId: activeJobId } : {}),
					});
					get().addToast({
						type: "urn:opendv:error:clarify-failed",
						title: "Clarification Generation Failed",
						status: res.status,
						detail: err.detail || `Server returned HTTP ${res.status}`,
						code: err.code || err.error_code || "CLARIFY_FAILED",
					});
					return null;
				}
				const job = (await res.json()) as {
					job_id: string;
					status?: string;
					stage?: string;
					progress_pct?: number;
				};
				if (
					myGen !== jobMonitor.currentJobGeneration() ||
					get().activeSession?.session_id !== sid
				)
					return null;
				jobMonitor.appendSseEvent({
					job_id: job.job_id,
					stage: job.stage || "clarify",
					message: `Clarify job ${job.job_id} queued`,
					progress_pct: job.progress_pct ?? 10,
					level: "info",
				});
				const isDone =
					String(job.status || "").toUpperCase() === "COMPLETED" ||
					String(job.status || "").toUpperCase() === "SUCCEEDED";
				if (isDone) {
					set({ isClarifying: false, activeSessionJobId: null });
					await get().loadClarifications(fetchImpl, sid);
				} else if (job.job_id) {
					set({ activeSessionJobId: job.job_id });
					jobMonitor.monitorStudioJob(sid, job.job_id, "clarify", fetchImpl, {
						myGen,
					});
				} else {
					set({ isClarifying: false });
				}
				return job.job_id || null;
			} catch (err) {
				if (
					myGen === jobMonitor.currentJobGeneration() &&
					get().activeSession?.session_id === sid
				) {
					set({ isClarifying: false });
					get().addToast({
						type: "urn:opendv:error:network",
						title: "Network Error",
						status: 503,
						detail: String(err),
						code: "NETWORK_ERROR",
					});
				}
				return null;
			}
		},

		startConstruct: async (
			constructOptions: {
				mode?: "auto" | "agent" | "deterministic";
				auto_commit?: boolean;
				rationale?: string;
				steering_prompt?: string;
				force?: boolean;
			} = {},
			fetchImpl = fetch,
		) => {
			const activeSession = get().activeSession;
			if (!activeSession) return null;
			const sid = activeSession.session_id;
			const isAutoCommit = Boolean(constructOptions.auto_commit);
			if (isAutoCommit && get().hasUnsavedChanges && !constructOptions.force) {
				get().addToast({
					type: "urn:opendv:warning:unsaved-changes",
					title: "Unsaved Canvas Edits",
					status: 409,
					detail:
						"Cannot start auto-commit construction while unsaved canvas edits exist. Please commit or discard your local canvas changes before running auto-commit construction.",
					code: "UNSAVED_CHANGES",
				});
				return null;
			}
			const headers: Record<string, string> = {
				"Content-Type": "application/json",
			};
			if (isAutoCommit && activeSession.head_sha256) {
				headers["If-Match"] = `"${activeSession.head_sha256}"`;
			}
			const myGen = jobMonitor.nextJobGeneration();
			jobMonitor.cancelActiveJobWatchers();
			set({
				isConstructing: true,
				agentErrorCode: null,
				agentErrorDetail: null,
				budgetHold: null,
			});
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/construct`,
					{
						method: "POST",
						headers,
						body: JSON.stringify({
							mode:
								constructOptions.mode ||
								(isAutoCommit ? "deterministic" : "auto"),
							auto_commit: isAutoCommit,
							...(constructOptions.steering_prompt
								? { steering_prompt: constructOptions.steering_prompt }
								: {}),
							...(isAutoCommit && activeSession.head_sha256
								? {
										base_sha256: activeSession.head_sha256,
										...(activeSession.active_version
											? { base_version: activeSession.active_version }
											: {}),
									}
								: {}),
						}),
					},
				);
				if (
					myGen !== jobMonitor.currentJobGeneration() ||
					get().activeSession?.session_id !== sid
				)
					return null;
				if (!res.ok) {
					const err = (await res.json().catch(() => ({}))) as {
						detail?: string;
						code?: string;
						error_code?: string;
						active_job_id?: string;
						job_id?: string;
					};
					if (
						myGen !== jobMonitor.currentJobGeneration() ||
						get().activeSession?.session_id !== sid
					)
						return null;
					const errCode = err.error_code || err.code || "CONSTRUCT_FAILED";
					const activeJobId = extractActiveSessionJobId(res.status, err);
					set({
						isConstructing: false,
						...(activeJobId ? { activeSessionJobId: activeJobId } : {}),
						agentErrorCode: res.status === 422 ? errCode : get().agentErrorCode,
						agentErrorDetail:
							res.status === 422
								? err.detail || "Agent harness is not configured."
								: get().agentErrorDetail,
					});
					get().addToast({
						type: "urn:opendv:error:construct-failed",
						title: "Ontology Construction Failed",
						status: res.status,
						detail: err.detail || `Server returned HTTP ${res.status}`,
						code: errCode,
					});
					return null;
				}
				const job = (await res.json()) as {
					job_id: string;
					status?: string;
					stage?: string;
					pct_complete?: number;
					progress_pct?: number;
					progress?: number;
				};
				if (
					myGen !== jobMonitor.currentJobGeneration() ||
					get().activeSession?.session_id !== sid
				)
					return null;
				const initialPct =
					job.pct_complete ?? job.progress_pct ?? job.progress ?? 10;
				jobMonitor.appendSseEvent({
					job_id: job.job_id,
					stage: job.stage || "construct",
					message: `Construct job ${job.job_id} queued`,
					progress_pct: initialPct,
					level: "info",
				});
				const jobRecord = job as Record<string, unknown>;
				const jobResult =
					jobRecord.result && typeof jobRecord.result === "object"
						? (jobRecord.result as Record<string, unknown>)
						: undefined;
				const didAutoCommit =
					isAutoCommit ||
					Boolean(jobRecord.auto_committed) ||
					Boolean(jobResult?.auto_committed);
				const isDone =
					String(job.status || "").toUpperCase() === "COMPLETED" ||
					String(job.status || "").toUpperCase() === "SUCCEEDED";
				if (isDone) {
					set({ isConstructing: false, activeSessionJobId: null });
					if (didAutoCommit) {
						await jobMonitor.handleAutoCommittedCompletion(sid, fetchImpl);
					} else {
						await Promise.all([
							get().loadProposals(fetchImpl, sid, {
								selectNewestPending: true,
							}),
							get().loadClarifications(fetchImpl, sid),
							get().loadStagedCandidates(fetchImpl, sid),
						]);
					}
				} else if (job.job_id) {
					set({ activeSessionJobId: job.job_id });
					jobMonitor.monitorStudioJob(sid, job.job_id, "construct", fetchImpl, {
						...(isAutoCommit ? { autoCommit: true } : {}),
						myGen,
					});
				} else {
					set({ isConstructing: false });
				}
				return job.job_id || null;
			} catch (err) {
				if (
					myGen === jobMonitor.currentJobGeneration() &&
					get().activeSession?.session_id === sid
				) {
					set({ isConstructing: false });
					get().addToast({
						type: "urn:opendv:error:network",
						title: "Network Error",
						status: 503,
						detail: String(err),
						code: "NETWORK_ERROR",
					});
				}
				return null;
			}
		},

		cancelActiveJob: async (fetchImpl = fetch) => {
			const sid = get().activeSession?.session_id;
			let jobId = get().activeSessionJobId;
			if (!sid || !jobId) return false;
			try {
				if (jobId === "active-session-job") {
					const logsRes = await studioFetch(
						fetchImpl,
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/logs?limit=50`,
					).catch(() => null);
					if (logsRes?.ok) {
						const data = (await logsRes.json().catch(() => ({}))) as {
							entries?: Array<{ job_id?: string | null }>;
						};
						const latestWithId = [...(data.entries ?? [])]
							.reverse()
							.find((e) => typeof e.job_id === "string" && e.job_id.trim());
						jobId = latestWithId?.job_id ?? null;
					} else {
						jobId = null;
					}
				}
				let ok = true;
				if (jobId && jobId !== "active-session-job") {
					const res = await studioFetch(
						fetchImpl,
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/jobs/${encodeURIComponent(jobId)}/cancel`,
						{
							method: "POST",
							headers: { "Content-Type": "application/json" },
							body: JSON.stringify({
								reason: "Cancelled by steward in wizard",
							}),
						},
					);
					ok = res.ok;
				}
				jobMonitor.cancelActiveJobWatchers();
				set({
					activeSessionJobId: null,
					isClarifying: false,
					isConstructing: false,
				});
				return ok;
			} catch {
				jobMonitor.cancelActiveJobWatchers();
				set({
					activeSessionJobId: null,
					isClarifying: false,
					isConstructing: false,
				});
				return false;
			}
		},

		...createFiboQaActions(set, get, studioFetch),
	};
}
