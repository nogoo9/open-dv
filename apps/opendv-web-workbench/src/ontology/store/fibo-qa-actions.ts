/**
 * Staged Candidates, Citation Resolution, FIBO Lookup & FIBO Q&A Slice Actions
 * (apps/opendv-web-workbench/src/ontology/store/fibo-qa-actions.ts)
 */

import type { StoreGet, StoreSet } from "./store-types";
import type { StudioFetcher } from "./studio-fetch";
import type {
	FiboQaResult,
	ResolvedCitationItem,
	StagedCandidateDTO,
} from "./types";

export function createFiboQaActions(
	set: StoreSet,
	get: StoreGet,
	studioFetch: StudioFetcher,
) {
	return {
		loadStagedCandidates: async (
			fetchImpl = fetch,
			targetSessionId?: string,
		) => {
			const activeSession = get().activeSession;
			if (!activeSession) return;
			const sid = targetSessionId ?? activeSession.session_id;
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/staged-candidates`,
				);
				if (!res.ok || get().activeSession?.session_id !== sid) return;
				const data = (await res.json()) as {
					staged_candidates?: StagedCandidateDTO[];
					candidates?: StagedCandidateDTO[];
				};
				set({
					stagedCandidates: data.staged_candidates ?? data.candidates ?? [],
				});
			} catch {
				// ignore failure
			}
		},

		promoteStagedCandidate: async (
			candidateId: string,
			path: "path_a" | "path_b",
			rationale?: string,
			fetchImpl = fetch,
		) => {
			const sid = get().activeSession?.session_id;
			if (!sid) return false;
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/staged-candidates/${encodeURIComponent(candidateId)}/promote`,
					{
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({
							path,
							...(rationale ? { rationale, waiver_rationale: rationale } : {}),
						}),
					},
				);
				if (!res.ok) return false;
				await get().loadStagedCandidates(fetchImpl, sid);
				return true;
			} catch {
				return false;
			}
		},

		resolveCitations: async (
			citations: Array<Record<string, unknown>>,
			fetchImpl = fetch,
		) => {
			const sid = get().activeSession?.session_id;
			if (!sid || citations.length === 0) return [];
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/citations/resolve`,
					{
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({ citations }),
					},
				);
				if (!res.ok) return [];
				const data = (await res.json()) as {
					resolved?: ResolvedCitationItem[];
				};
				const resolved = data.resolved ?? [];
				set({ resolvedCitations: resolved });
				return resolved;
			} catch {
				return [];
			}
		},

		lookupFibo: async (query: string, fetchImpl = fetch) => {
			if (!query.trim()) return [];
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/fibo/lookup?q=${encodeURIComponent(query.trim())}`,
				);
				if (!res.ok) return [];
				const data = (await res.json()) as {
					matches?: Array<Record<string, unknown>>;
				};
				return data.matches ?? [];
			} catch {
				return [];
			}
		},

		askFiboQa: async (
			question: string,
			qaOptions: { sourceIds?: string[]; conceptUri?: string } = {},
			fetchImpl = fetch,
		) => {
			const sid = get().activeSession?.session_id;
			if (!sid || !question.trim()) return null;
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/fibo-qa`,
					{
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({
							question: question.trim(),
							...(qaOptions.sourceIds
								? { source_ids: qaOptions.sourceIds }
								: {}),
							...(qaOptions.conceptUri
								? { concept_uri: qaOptions.conceptUri }
								: {}),
						}),
					},
				);
				if (!res.ok) {
					const err = (await res.json().catch(() => ({}))) as {
						detail?: string;
						code?: string;
						error_code?: string;
					};
					const errCode = err.error_code || err.code || "FIBO_QA_FAILED";
					if (res.status === 422) {
						set({
							agentErrorCode: errCode,
							agentErrorDetail: err.detail || "Agent harness not configured.",
						});
					}
					get().addToast({
						type: "urn:opendv:error:fibo-qa",
						title: "FIBO Q&A Error",
						status: res.status,
						detail: err.detail || `Server returned HTTP ${res.status}`,
						code: errCode,
					});
					return null;
				}
				const data = (await res.json()) as {
					answer: string;
					citations?: Array<Record<string, unknown>>;
					suggested_actions?: Array<Record<string, unknown>>;
				};
				const result: FiboQaResult = {
					question: question.trim(),
					answer: data.answer,
					citations: data.citations ?? [],
					suggested_actions: data.suggested_actions ?? [],
				};
				set({ fiboQaResult: result });
				return result;
			} catch {
				return null;
			}
		},
	};
}
