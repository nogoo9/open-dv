/**
 * Quality Scorecard, Administrative Waiver & Steward Feedback Slice
 * (apps/opendv-web-workbench/src/ontology/store/waiver-feedback-slice.ts)
 */

import { MIN_RATIONALE_LENGTH } from "../constants";
import { isConceptMutatedFromBaseline } from "./graph-parser";
import {
	createDefaultQualityScorecard,
	deriveGroundingStatus,
	recomputeScorecard,
} from "./scorecard";
import type { StoreGet, StoreSet, StudioStoreState } from "./store-types";
import type { StudioFetcher } from "./studio-fetch";
import type { ConceptNode, ExternalMapping, QualityScorecard } from "./types";

export function createWaiverFeedbackSlice(
	set: StoreSet,
	get: StoreGet,
	studioFetch: StudioFetcher,
) {
	return {
		isSubmittingWaiver: false,
		qualityScorecard: createDefaultQualityScorecard(),
		sessionWaivers: {} as StudioStoreState["sessionWaivers"],

		updateQualityScorecard: (updates: Partial<QualityScorecard>) => {
			set((state) => {
				const {
					waiver_applied: _w,
					waived_concept_uris: _u,
					waived_base_sha256: _s,
					waiver_rationale: _r,
					checks: _c,
					...safeUpdates
				} = updates as Partial<QualityScorecard>;
				return {
					qualityScorecard: {
						...state.qualityScorecard,
						...safeUpdates,
					},
				};
			});
		},

		submitWaiver: async (
			rationale: string,
			targetOrLegacy?: string | string[] | typeof fetch,
			maybeTargetsOrFetch?: string[] | typeof fetch,
			maybeFetchImpl?: typeof fetch,
		): Promise<boolean> => {
			const explicitTargetUris = Array.isArray(targetOrLegacy)
				? targetOrLegacy
				: Array.isArray(maybeTargetsOrFetch)
					? maybeTargetsOrFetch
					: undefined;
			const fetchImpl =
				typeof targetOrLegacy === "function"
					? targetOrLegacy
					: typeof maybeTargetsOrFetch === "function"
						? maybeTargetsOrFetch
						: (maybeFetchImpl ?? fetch);

			if (get().isSubmittingWaiver) return false;
			const activeSession = get().activeSession;
			if (!activeSession) {
				get().addToast({
					type: "urn:opendv:error:no-session",
					title: "No Active Session",
					status: 400,
					detail: "Select an active session before requesting a waiver.",
					code: "SYNTAX_INVALID_ARGUMENT",
				});
				return false;
			}
			if (!activeSession.head_sha256) {
				get().addToast({
					type: "urn:opendv:error:missing-head-sha",
					title: "Missing Session Head SHA",
					status: 400,
					detail:
						"Cannot authorize grounding waiver on uncommitted session head.",
					code: "MISSING_HEAD_SHA",
				});
				return false;
			}
			if (!rationale || rationale.trim().length < MIN_RATIONALE_LENGTH) {
				get().addToast({
					type: "urn:opendv:error:waiver-rationale-short",
					title: "Waiver Rationale Too Short",
					status: 400,
					detail: `Steward administrative waiver rationale must be at least ${MIN_RATIONALE_LENGTH} characters.`,
					code: "INVALID_GROUNDING_WAIVER",
				});
				return false;
			}

			const normalizedExplicitTargets = explicitTargetUris
				? explicitTargetUris.map((u) => u.trim()).filter(Boolean)
				: undefined;

			const pendingProposalUngroundedUris: string[] = [];
			const pendingProposalAllConceptUris = new Set<string>();
			for (const prop of get().proposals || []) {
				if (prop.status && prop.status.toUpperCase() !== "PENDING") continue;
				for (const op of prop.operations || []) {
					if (
						op &&
						typeof op === "object" &&
						op.op_type === "add_concept" &&
						typeof op.concept_uri === "string" &&
						op.concept_uri.trim().length > 0
					) {
						const uri = op.concept_uri.trim();
						pendingProposalAllConceptUris.add(uri);
						const rawMappings = Array.isArray(op.external_mappings)
							? (op.external_mappings as ExternalMapping[])
							: [];
						const rawConf =
							typeof op.confidence === "number" ? op.confidence : undefined;
						const status =
							rawMappings.length > 0
								? deriveGroundingStatus(rawMappings, rawConf).grounding_status
								: "UNGROUNDED";
						if (status !== "GROUNDED") {
							pendingProposalUngroundedUris.push(uri);
						}
					}
				}
			}

			if (
				get().nodes.length === 0 &&
				(!normalizedExplicitTargets ||
					normalizedExplicitTargets.length === 0) &&
				pendingProposalAllConceptUris.size === 0
			) {
				get().addToast({
					type: "urn:opendv:notice:empty-graph",
					title: "Empty Graph",
					status: 400,
					detail: "Cannot request administrative waiver for an empty graph.",
					code: "EMPTY_GRAPH",
				});
				return false;
			}

			const committedMap = new Map(
				get().committedNodes.map((cn) => [cn.concept_uri, cn]),
			);
			const effectiveGrandfathered = new Set(
				(activeSession.grandfathered_concept_uris || []).filter((uri) => {
					const comm = committedMap.get(uri);
					const curr = get().nodes.find((n) => n.concept_uri === uri);
					if (!curr) return false;
					if (!comm) return true;
					return !isConceptMutatedFromBaseline(curr, comm);
				}),
			);

			const ungroundedNodeUris = get()
				.nodes.filter((n) => {
					if (effectiveGrandfathered.has(n.concept_uri)) return false;
					const hasMappings =
						Array.isArray(n.external_mappings) &&
						n.external_mappings.length > 0;
					const status = hasMappings
						? deriveGroundingStatus(n.external_mappings, n.confidence)
								.grounding_status
						: "UNGROUNDED";
					return status !== "GROUNDED";
				})
				.map((n) => n.concept_uri);

			const ungroundedUris = Array.from(
				new Set([...ungroundedNodeUris, ...pendingProposalUngroundedUris]),
			);

			const targetUris =
				normalizedExplicitTargets && normalizedExplicitTargets.length > 0
					? get().nodes.length === 0 && pendingProposalAllConceptUris.size === 0
						? normalizedExplicitTargets
						: normalizedExplicitTargets.filter((u) =>
								ungroundedUris.includes(u),
							)
					: ungroundedUris;

			if (targetUris.length === 0) {
				get().addToast({
					type: "urn:opendv:notice:no-ungrounded",
					title: "No Ungrounded Concepts Selected",
					status: 400,
					detail:
						"All selected concepts in this session are already grounded or grandfathered. Administrative waiver is not needed.",
					code: "NO_UNGROUNDED_CONCEPTS",
				});
				return false;
			}

			const sid = activeSession.session_id;
			set({ isSubmittingWaiver: true });
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/waiver`,
					{
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({
							target_concept_uris: targetUris,
							rationale: rationale.trim(),
							base_sha256: activeSession.head_sha256,
						}),
					},
				);
				const currentSession = get().activeSession;
				if (!currentSession || currentSession.session_id !== sid) return false;

				if (!res.ok) {
					const err = (await res.json().catch(() => ({}))) as {
						detail?: string;
						code?: string;
					};
					if (get().activeSession?.session_id !== sid) return false;
					get().addToast({
						type: "urn:opendv:error:waiver-rejected",
						title: "Waiver Authorization Rejected",
						status: res.status,
						detail: err.detail || `Server returned HTTP ${res.status}`,
						code: err.code || "FORBIDDEN_ADMIN_REQUIRED",
					});
					return false;
				}

				const waiverData = (await res.json().catch(() => ({}))) as {
					waiver_applied?: boolean;
					waived_concept_uris?: string[];
					target_concept_uris?: string[];
					waived_base_sha256?: string;
					base_sha256?: string;
					detail?: string;
					code?: string;
				};

				if (waiverData.waiver_applied === false) {
					if (get().activeSession?.session_id !== sid) return false;
					get().addToast({
						type: "urn:opendv:error:waiver-denied",
						title: "Waiver Authorization Denied",
						status: 200,
						detail:
							waiverData.detail || "Server did not authorize grounding waiver.",
						code: waiverData.code || "WAIVER_DENIED",
					});
					return false;
				}

				const serverWaivedUris = Array.isArray(waiverData.waived_concept_uris)
					? waiverData.waived_concept_uris
					: Array.isArray(waiverData.target_concept_uris)
						? waiverData.target_concept_uris
						: targetUris;
				const serverWaivedSha =
					waiverData.waived_base_sha256 ||
					waiverData.base_sha256 ||
					currentSession.head_sha256;

				set((state) => {
					const prevWaiver = state.sessionWaivers[sid];
					const mergedWaivedUris = Array.from(
						new Set([
							...(prevWaiver &&
							prevWaiver.waived_base_sha256 === serverWaivedSha
								? prevWaiver.waived_concept_uris
								: []),
							...serverWaivedUris,
						]),
					);
					const snapshots: Record<string, ConceptNode> = {
						...(prevWaiver &&
						prevWaiver.waived_base_sha256 === serverWaivedSha &&
						prevWaiver.waived_concept_snapshots
							? prevWaiver.waived_concept_snapshots
							: {}),
					};
					for (const n of state.nodes) {
						if (serverWaivedUris.includes(n.concept_uri)) {
							snapshots[n.concept_uri] = n;
						}
					}
					const nextSessionWaivers = {
						...state.sessionWaivers,
						[sid]: {
							waiver_applied: true,
							waiver_rationale: rationale.trim(),
							waived_concept_uris: mergedWaivedUris,
							waived_base_sha256: serverWaivedSha,
							waived_concept_snapshots: snapshots,
						},
					};
					const nextState: StudioStoreState = {
						...state,
						sessionWaivers: nextSessionWaivers,
					};
					return {
						sessionWaivers: nextSessionWaivers,
						qualityScorecard: recomputeScorecard(
							nextState,
							state.nodes,
							state.edges,
						),
					};
				});
				get().addToast({
					type: "urn:opendv:notice:waiver-authorized",
					title: "Administrative Waiver Authorized",
					status: 200,
					detail: "Administrative grounding waiver successfully recorded.",
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
			} finally {
				set({ isSubmittingWaiver: false });
			}
		},

		submitFeedback: async (
			rating: number,
			comment: string,
			conceptUriOrFetch?: string | null | typeof fetch,
			maybeFetchImpl?: typeof fetch,
		): Promise<boolean> => {
			const conceptUri =
				typeof conceptUriOrFetch === "string" || conceptUriOrFetch === null
					? conceptUriOrFetch
					: null;
			const fetchImpl =
				typeof conceptUriOrFetch === "function"
					? conceptUriOrFetch
					: (maybeFetchImpl ?? fetch);

			const activeSession = get().activeSession;
			if (!activeSession) return false;
			if (!Number.isInteger(rating) || rating < 1 || rating > 5) {
				get().addToast({
					type: "urn:opendv:error:invalid-feedback-rating",
					title: "Invalid Feedback Rating",
					status: 400,
					detail: "Feedback rating must be an integer between 1 and 5.",
					code: "INVALID_FEEDBACK_RATING",
				});
				return false;
			}

			const sid = activeSession.session_id;
			const normalizedConceptUri =
				typeof conceptUri === "string" && conceptUri.trim().length > 0
					? conceptUri.trim()
					: null;
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/feedback`,
					{
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({
							rating,
							comment: comment.trim(),
							concept_uri: normalizedConceptUri,
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
						type: "urn:opendv:error:feedback-rejected",
						title: "Feedback Submission Failed",
						status: res.status,
						detail: err.detail || `Server returned HTTP ${res.status}`,
						code: err.code || "FEEDBACK_SUBMISSION_FAILED",
					});
					return false;
				}
				set((state) => ({
					qualityScorecard: {
						...state.qualityScorecard,
						feedback_rating: rating,
						feedback_comment: comment.trim(),
						feedback_concept_uri: normalizedConceptUri,
						feedback_submitted: true,
					},
				}));
				get().addToast({
					type: "urn:opendv:notice:feedback-submitted",
					title: "Feedback Recorded",
					status: 200,
					detail: `Recorded ${rating}-star steward feedback.`,
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
	};
}
