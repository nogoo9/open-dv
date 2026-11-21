/**
 * Candidate Proposals Stewardship Slice
 * (apps/opendv-web-workbench/src/ontology/store/proposal-stewardship-slice.ts)
 */

import { GENESIS_SEMVER_VERSION, NEW_PROPOSAL_DRAFT_KEY } from "../constants";
import { createDraftOpId } from "../utils/identifiers";
import type { StoreGet, StoreSet } from "./store-types";
import type { StudioFetcher } from "./studio-fetch";
import type {
	CandidateProposalSummary,
	ProposalDraftEdit,
	StudioSessionSummary,
} from "./types";

export function sortProposalsForStewardship(
	proposals: ReadonlyArray<CandidateProposalSummary>,
): CandidateProposalSummary[] {
	return [...proposals].sort((a, b) => {
		const aPending = !a.status || a.status === "PENDING" ? 0 : 1;
		const bPending = !b.status || b.status === "PENDING" ? 0 : 1;
		if (aPending !== bPending) return aPending - bPending;
		const aTime = a.created_at ? Date.parse(a.created_at) : Number.NaN;
		const bTime = b.created_at ? Date.parse(b.created_at) : Number.NaN;
		if (!Number.isNaN(aTime) && !Number.isNaN(bTime) && aTime !== bTime) {
			return bTime - aTime;
		}
		return 0;
	});
}

export function resolveProposalBaseVersionAndSha(
	activeSession: Pick<StudioSessionSummary, "active_version" | "head_sha256">,
	existing?: CandidateProposalSummary,
): { base_version: string; base_sha256?: string } {
	const useExistingPair =
		Boolean(existing) &&
		existing?.status === "PENDING" &&
		Boolean(existing.base_version) &&
		(Boolean(existing.base_sha256) ||
			existing.base_version === GENESIS_SEMVER_VERSION);
	if (useExistingPair && existing) {
		return {
			base_version: existing.base_version,
			...(existing.base_sha256 ? { base_sha256: existing.base_sha256 } : {}),
		};
	}
	return {
		base_version: activeSession.active_version || GENESIS_SEMVER_VERSION,
		...(activeSession.head_sha256
			? { base_sha256: activeSession.head_sha256 }
			: {}),
	};
}

export function normalizeProposalOperationsForSubmission(
	operations: ReadonlyArray<Record<string, unknown>>,
): Array<Record<string, unknown>> {
	return operations.map((op) => {
		const copy: Record<string, unknown> = { ...op };
		if (typeof copy.op_id !== "string" || !copy.op_id.trim()) {
			copy.op_id = createDraftOpId();
		}
		if (copy.op_type === "add_external_mapping") {
			const numericConf =
				typeof copy.mapping_confidence === "number" &&
				Number.isFinite(copy.mapping_confidence)
					? copy.mapping_confidence
					: typeof copy.confidence === "number" &&
							Number.isFinite(copy.confidence)
						? copy.confidence
						: undefined;
			if (numericConf !== undefined) {
				copy.mapping_confidence = numericConf;
				copy.confidence = numericConf;
			}
		}
		return copy;
	});
}

export function createProposalStewardshipSlice(
	set: StoreSet,
	get: StoreGet,
	studioFetch: StudioFetcher,
	bumpProposalsReqId: () => number,
	getProposalsReqId: () => number,
) {
	return {
		proposals: [] as CandidateProposalSummary[],
		selectedProposalId: null as string | null,
		proposalDrafts: {} as Record<string, ProposalDraftEdit>,
		isLoadingProposals: false,

		loadProposals: async (
			fetchImpl = fetch,
			targetSessionId?: string,
			loadOptions?: { selectNewestPending?: boolean },
		) => {
			const activeSession = get().activeSession;
			if (!activeSession) return;
			const expectedId = targetSessionId ?? activeSession.session_id;
			if (activeSession.session_id !== expectedId) return;

			const reqId = bumpProposalsReqId();
			set({ isLoadingProposals: true });
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(expectedId)}/proposals`,
				);
				if (!res.ok) {
					if (
						reqId === getProposalsReqId() &&
						get().activeSession?.session_id === expectedId
					) {
						set({ isLoadingProposals: false });
					}
					return;
				}
				const data = (await res.json()) as
					| CandidateProposalSummary[]
					| { proposals?: CandidateProposalSummary[] };
				if (
					reqId !== getProposalsReqId() ||
					get().activeSession?.session_id !== expectedId
				)
					return;
				const list = Array.isArray(data) ? data : data.proposals || [];
				const sorted = sortProposalsForStewardship(list);
				set((state) => {
					let nextSelected = state.selectedProposalId;
					if (loadOptions?.selectNewestPending) {
						const newestPending = sorted.find(
							(p) => !p.status || p.status === "PENDING",
						);
						nextSelected =
							newestPending?.proposal_id ??
							sorted[0]?.proposal_id ??
							state.selectedProposalId;
					} else if (
						nextSelected &&
						nextSelected !== NEW_PROPOSAL_DRAFT_KEY &&
						!sorted.some((p) => p.proposal_id === nextSelected)
					) {
						nextSelected = null;
					}
					return {
						proposals: sorted,
						selectedProposalId: nextSelected,
						isLoadingProposals: false,
					};
				});
			} catch {
				if (
					reqId === getProposalsReqId() &&
					get().activeSession?.session_id === expectedId
				) {
					set({ isLoadingProposals: false });
				}
			}
		},

		acceptProposal: async (
			proposalId: string,
			rationale?: string,
			maybeFetchImpl?: typeof fetch,
			acceptOptions?: {
				force?: boolean;
				selfAcceptanceOverride?: boolean;
				lowConfidenceWaiver?: boolean;
			},
		) => {
			const fetchImpl = maybeFetchImpl ?? fetch;
			const activeSession = get().activeSession;
			if (!activeSession) return false;
			const role = get().userRole;
			if (role === "viewer" || role === "automated") {
				get().addToast({
					type: "urn:opendv:error:forbidden-role",
					title: "Human Steward Role Required",
					status: 403,
					detail: `Role '${role}' cannot manually accept candidate proposals. Switch to an authenticated Editor or Admin steward session.`,
					code: "FORBIDDEN_ROLE",
				});
				return false;
			}
			if (get().hasUnsavedChanges && !acceptOptions?.force) {
				get().addToast({
					type: "urn:opendv:error:unsaved-changes",
					title: "Unsaved Canvas Changes",
					status: 409,
					detail:
						"Cannot accept a proposal while unsaved canvas edits exist. Please commit or discard your local canvas changes before accepting a proposal.",
					code: "UNSAVED_CHANGES",
				});
				return false;
			}
			const sid = activeSession.session_id;
			const headers: Record<string, string> = {
				"Content-Type": "application/json",
			};
			if (activeSession.head_sha256) {
				headers["If-Match"] = `"${activeSession.head_sha256}"`;
			}
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/proposals/${encodeURIComponent(proposalId)}/accept`,
					{
						method: "POST",
						headers,
						body: JSON.stringify({
							...(activeSession.head_sha256
								? { base_sha256: activeSession.head_sha256 }
								: {}),
							grounding_rationale:
								rationale ||
								`Accepted candidate proposal ${proposalId} via Workbench Studio`,
							...(acceptOptions?.selfAcceptanceOverride
								? { self_acceptance_override: true }
								: {}),
							...(acceptOptions?.lowConfidenceWaiver
								? { low_confidence_waiver: true }
								: {}),
						}),
					},
				);
				if (get().activeSession?.session_id !== sid) return false;
				if (!res.ok) {
					const err = (await res.json().catch(() => ({}))) as {
						detail?: string;
						code?: string;
					};
					get().addToast({
						type: "urn:opendv:error:proposal-accept-failed",
						title: "Proposal Acceptance Failed",
						status: res.status,
						detail: err.detail || `Server returned HTTP ${res.status}`,
						code: err.code || "PROPOSAL_ACCEPT_FAILED",
					});
					return false;
				}
				set((state) => {
					const nextDrafts = { ...state.proposalDrafts };
					delete nextDrafts[proposalId];
					return { proposalDrafts: nextDrafts };
				});
				await get().selectSession(sid, fetchImpl, true);
				get().addToast({
					type: "urn:opendv:notice:proposal-accepted",
					title: "Proposal Accepted",
					status: 200,
					detail: `Proposal ${proposalId} ratified into session head.`,
					code: "OK",
				});
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

		rejectProposal: async (
			proposalId: string,
			reason?: string,
			fetchImpl = fetch,
		) => {
			const activeSession = get().activeSession;
			if (!activeSession) return false;
			const role = get().userRole;
			if (role === "viewer" || role === "automated") {
				get().addToast({
					type: "urn:opendv:error:forbidden-role",
					title: "Human Steward Role Required",
					status: 403,
					detail: `Role '${role}' cannot manually reject candidate proposals. Switch to an authenticated Editor or Admin steward session.`,
					code: "FORBIDDEN_ROLE",
				});
				return false;
			}
			const sid = activeSession.session_id;
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/proposals/${encodeURIComponent(proposalId)}/reject`,
					{
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({
							reason: reason || "Rejected by steward in Workbench Studio",
						}),
					},
				);
				if (get().activeSession?.session_id !== sid) return false;
				if (!res.ok) {
					const err = (await res.json().catch(() => ({}))) as {
						detail?: string;
						code?: string;
					};
					get().addToast({
						type: "urn:opendv:error:proposal-reject-failed",
						title: "Proposal Rejection Failed",
						status: res.status,
						detail: err.detail || `Server returned HTTP ${res.status}`,
						code: err.code || "PROPOSAL_REJECT_FAILED",
					});
					return false;
				}
				set((state) => {
					const nextDrafts = { ...state.proposalDrafts };
					delete nextDrafts[proposalId];
					return { proposalDrafts: nextDrafts };
				});
				await get().loadProposals(fetchImpl, sid);
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

		rebaseProposal: async (proposalId: string, fetchImpl = fetch) => {
			const activeSession = get().activeSession;
			if (!activeSession) return false;
			const role = get().userRole;
			if (role === "viewer" || role === "automated") {
				get().addToast({
					type: "urn:opendv:error:forbidden-role",
					title: "Human Steward Role Required",
					status: 403,
					detail: `Role '${role}' cannot manually rebase candidate proposals. Switch to an authenticated Editor or Admin steward session.`,
					code: "FORBIDDEN_ROLE",
				});
				return false;
			}
			const sid = activeSession.session_id;
			const headers: Record<string, string> = {
				"Content-Type": "application/json",
			};
			if (activeSession.head_sha256) {
				headers["If-Match"] = `"${activeSession.head_sha256}"`;
			}
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/proposals/${encodeURIComponent(proposalId)}/rebase`,
					{ method: "POST", headers, body: "{}" },
				);
				if (get().activeSession?.session_id !== sid) return false;
				if (!res.ok) {
					const err = (await res.json().catch(() => ({}))) as {
						detail?: string;
						code?: string;
					};
					get().addToast({
						type: "urn:opendv:error:proposal-rebase-failed",
						title: "Proposal Rebase Failed",
						status: res.status,
						detail: err.detail || `Server returned HTTP ${res.status}`,
						code: err.code || "PROPOSAL_REBASE_FAILED",
					});
					return false;
				}
				set((state) => {
					const nextDrafts = { ...state.proposalDrafts };
					delete nextDrafts[proposalId];
					return { proposalDrafts: nextDrafts };
				});
				await get().loadProposals(fetchImpl, sid);
				get().addToast({
					type: "urn:opendv:notice:proposal-rebased",
					title: "Proposal Rebased",
					status: 200,
					detail: `Proposal ${proposalId} rebased onto current session head.`,
					code: "OK",
				});
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

		selectProposal: (proposalId: string | null) => {
			set((state) => {
				if (proposalId !== null) {
					return {
						selectedProposalId: proposalId,
						layout: {
							...state.layout,
							rightDrawerOpen: true,
							rightDrawerTab: "proposal",
						},
					};
				}
				const hasConcept = state.selectedNodeId !== null;
				const isBlueprint = state.layout.rightDrawerTab === "blueprint";
				return {
					selectedProposalId: null,
					layout: {
						...state.layout,
						rightDrawerOpen: hasConcept || isBlueprint,
						rightDrawerTab: isBlueprint ? "blueprint" : "concept",
					},
				};
			});
		},

		setProposalDraft: (proposalKey: string, draft: ProposalDraftEdit) => {
			set((state) => ({
				proposalDrafts: {
					...state.proposalDrafts,
					[proposalKey]: {
						rationale: draft.rationale,
						operations: draft.operations.map((op) => ({ ...op })),
					},
				},
			}));
		},

		clearProposalDraft: (proposalKey: string) => {
			set((state) => {
				const nextDrafts = { ...state.proposalDrafts };
				delete nextDrafts[proposalKey];
				return { proposalDrafts: nextDrafts };
			});
		},

		saveEditedProposal: async (
			proposalId: string,
			updates: {
				rationale?: string | undefined;
				operations: Array<Record<string, unknown>>;
			},
			fetchImpl = fetch,
		) => {
			const activeSession = get().activeSession;
			if (!activeSession) return null;
			const role = get().userRole;
			if (role === "viewer") {
				get().addToast({
					type: "urn:opendv:error:forbidden-role",
					title: "Read-Only Role",
					status: 403,
					detail: "Viewer role cannot create or edit candidate proposals.",
					code: "FORBIDDEN_ROLE",
				});
				return null;
			}
			const sid = activeSession.session_id;
			const existing = get().proposals.find(
				(p) => p.proposal_id === proposalId,
			);
			const rationale =
				updates.rationale !== undefined
					? updates.rationale
					: existing?.rationale || `Steward revision based on ${proposalId}`;
			const normalizedOperations = normalizeProposalOperationsForSubmission(
				updates.operations,
			);
			const basePair = resolveProposalBaseVersionAndSha(
				activeSession,
				existing,
			);
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/proposals`,
					{
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({
							base_version: basePair.base_version,
							...(basePair.base_sha256
								? { base_sha256: basePair.base_sha256 }
								: {}),
							rationale,
							operations: normalizedOperations,
						}),
					},
				);
				if (get().activeSession?.session_id !== sid) return null;
				if (!res.ok) {
					const err = (await res.json().catch(() => ({}))) as {
						detail?: string;
						code?: string;
					};
					get().addToast({
						type: "urn:opendv:error:proposal-save-failed",
						title: "Proposal Save Failed",
						status: res.status,
						detail: err.detail || `Server returned HTTP ${res.status}`,
						code: err.code || "PROPOSAL_SAVE_FAILED",
					});
					return null;
				}
				const saved = (await res.json()) as CandidateProposalSummary;
				const normalizedSaved: CandidateProposalSummary = {
					...saved,
					operations: Array.isArray(saved.operations)
						? saved.operations
						: normalizedOperations,
					operations_count: Array.isArray(saved.operations)
						? saved.operations.length
						: normalizedOperations.length,
					ops_count: Array.isArray(saved.operations)
						? saved.operations.length
						: normalizedOperations.length,
				};
				set((state) => {
					const nextDrafts = { ...state.proposalDrafts };
					delete nextDrafts[proposalId];
					delete nextDrafts[normalizedSaved.proposal_id];
					delete nextDrafts[NEW_PROPOSAL_DRAFT_KEY];
					return {
						proposals: sortProposalsForStewardship([
							normalizedSaved,
							...state.proposals.filter(
								(p) => p.proposal_id !== normalizedSaved.proposal_id,
							),
						]),
						selectedProposalId: normalizedSaved.proposal_id,
						proposalDrafts: nextDrafts,
						layout: {
							...state.layout,
							rightDrawerOpen: true,
							rightDrawerTab: "proposal",
						},
					};
				});
				get().addToast({
					type: "urn:opendv:notice:proposal-saved",
					title: "Proposal Saved",
					status: 201,
					detail: `Saved pending candidate proposal ${normalizedSaved.proposal_id} (${normalizedSaved.operations_count} ops).`,
					code: "OK",
				});
				return normalizedSaved;
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
				return null;
			}
		},
	};
}
