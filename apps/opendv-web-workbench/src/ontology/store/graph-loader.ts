/**
 * Graph Export Snapshot Loader Action
 * (apps/opendv-web-workbench/src/ontology/store/graph-loader.ts)
 */

import { parseLoadedGraphPayload } from "./graph-parser";
import { recomputeScorecard } from "./scorecard";
import type { StoreGet, StoreSet, StudioStoreState } from "./store-types";
import type { StudioFetcher } from "./studio-fetch";

export async function executeLoadGraph(
	set: StoreSet,
	get: StoreGet,
	studioFetch: StudioFetcher,
	bumpGraphReqId: () => number,
	getGraphReqId: () => number,
	version?: string,
	fetchImpl = fetch,
	targetSessionId?: string,
): Promise<void> {
	const activeSession = get().activeSession;
	if (!activeSession) return;
	const expectedId = targetSessionId ?? activeSession.session_id;
	if (get().hasUnsavedChanges) {
		get().addToast({
			type: "urn:opendv:error:unsaved-changes",
			title: "Unsaved Changes",
			status: 400,
			detail:
				"Cannot reload or switch ontology versions with unsaved working edits. Please commit or discard changes first.",
			code: "UNSAVED_CHANGES",
		});
		return;
	}

	const reqId = bumpGraphReqId();
	set({ isLoadingGraph: true });
	try {
		const ver = version || activeSession.active_version;
		const isHeadVersion = ver === activeSession.active_version;
		const res = await studioFetch(
			fetchImpl,
			`/api/v1/ontology/studio/sessions/${encodeURIComponent(expectedId)}/export?export_format=json&version=${encodeURIComponent(ver)}`,
		);

		if (!res.ok) {
			if (ver === "0.0.0" && res.status === 404) {
				if (
					reqId === getGraphReqId() &&
					get().activeSession?.session_id === expectedId &&
					!get().hasUnsavedChanges
				) {
					set((state) => ({
						viewedVersion: "0.0.0",
						isHistoricalView: !isHeadVersion,
						nodes: [],
						edges: [],
						committedNodes: isHeadVersion ? [] : state.committedNodes,
						committedEdges: isHeadVersion ? [] : state.committedEdges,
						hasUnsavedChanges: isHeadVersion ? false : state.hasUnsavedChanges,
						qualityScorecard: recomputeScorecard(state, [], []),
						isLoadingGraph: false,
					}));
				}
				return;
			}
			if (
				reqId === getGraphReqId() &&
				get().activeSession?.session_id === expectedId
			) {
				set({ isLoadingGraph: false });
				const err = (await res.json().catch(() => ({}))) as {
					detail?: string;
					code?: string;
				};
				get().addToast({
					type: "urn:opendv:error:graph-load-failed",
					title: "Failed to Load Graph",
					status: res.status,
					detail: err.detail || `Server returned HTTP ${res.status}`,
					code: err.code || "GRAPH_LOAD_FAILED",
				});
			}
			return;
		}

		if (
			reqId !== getGraphReqId() ||
			get().activeSession?.session_id !== expectedId
		) {
			return;
		}

		const ont = (await res.json().catch(() => null)) as Record<
			string,
			unknown
		> | null;

		if (
			reqId !== getGraphReqId() ||
			get().activeSession?.session_id !== expectedId
		) {
			return;
		}

		const hasOntologyPayload =
			ont !== null &&
			typeof ont === "object" &&
			!Array.isArray(ont) &&
			(Array.isArray(ont.canonical_business_concepts) ||
				Array.isArray(ont.concepts) ||
				Array.isArray(ont.canonical_concepts));
		if (!hasOntologyPayload || !ont) {
			set({ isLoadingGraph: false });
			return;
		}

		const cleanVer = ver.replace(/^v/i, "");
		const { loadedNodes, loadedEdges } = parseLoadedGraphPayload(ont, {
			isCommittedSnapshot: cleanVer !== "0.0.0",
			grandfatheredUris: new Set(
				get().activeSession?.grandfathered_concept_uris || [],
			),
			waivedUris: new Set(
				get().sessionWaivers[expectedId]?.waived_concept_uris || [],
			),
		});

		if (
			reqId !== getGraphReqId() ||
			get().activeSession?.session_id !== expectedId ||
			get().hasUnsavedChanges
		) {
			return;
		}

		set((state) => {
			const enrichedSession =
				isHeadVersion && state.activeSession
					? {
							...state.activeSession,
							...(Array.isArray(ont.reference_enumerations)
								? {
										reference_enumerations: ont.reference_enumerations as Array<
											Record<string, unknown>
										>,
									}
								: {}),
							...(Array.isArray(ont.standards_references)
								? { standards_references: ont.standards_references as string[] }
								: {}),
							...(Array.isArray(ont.decision_refs)
								? { decision_refs: ont.decision_refs as string[] }
								: {}),
							...(typeof ont.parent_sha256 === "string"
								? { parent_sha256: ont.parent_sha256 }
								: {}),
							...(typeof ont.description === "string"
								? { description: ont.description }
								: {}),
							...(ont.metadata && typeof ont.metadata === "object"
								? { metadata: ont.metadata as Record<string, unknown> }
								: {}),
						}
					: state.activeSession;

			const existingWaiver = state.sessionWaivers[expectedId];
			const nextSessionWaivers =
				isHeadVersion &&
				existingWaiver &&
				!existingWaiver.waived_concept_snapshots
					? {
							...state.sessionWaivers,
							[expectedId]: {
								...existingWaiver,
								waived_concept_snapshots: Object.fromEntries(
									loadedNodes
										.filter((n) =>
											existingWaiver.waived_concept_uris.includes(
												n.concept_uri,
											),
										)
										.map((n) => [n.concept_uri, n]),
								),
							},
						}
					: state.sessionWaivers;

			const nextState: StudioStoreState = {
				...state,
				activeSession: enrichedSession,
				committedNodes:
					isHeadVersion || cleanVer !== "0.0.0"
						? loadedNodes
						: state.committedNodes,
				committedEdges:
					isHeadVersion || cleanVer !== "0.0.0"
						? loadedEdges
						: state.committedEdges,
				sessionWaivers: nextSessionWaivers,
			};

			return {
				activeSession: enrichedSession,
				sessionWaivers: nextSessionWaivers,
				viewedVersion: ver,
				isHistoricalView: !isHeadVersion,
				nodes: loadedNodes,
				edges: loadedEdges,
				committedNodes: isHeadVersion ? loadedNodes : state.committedNodes,
				committedEdges: isHeadVersion ? loadedEdges : state.committedEdges,
				hasUnsavedChanges: isHeadVersion ? false : state.hasUnsavedChanges,
				qualityScorecard: recomputeScorecard(
					nextState,
					loadedNodes,
					loadedEdges,
				),
				isLoadingGraph: false,
			};
		});
	} catch (err) {
		if (
			reqId === getGraphReqId() &&
			get().activeSession?.session_id === expectedId
		) {
			set({ isLoadingGraph: false });
			get().addToast({
				type: "urn:opendv:error:network",
				title: "Network Error",
				status: 503,
				detail: String(err),
				code: "NETWORK_ERROR",
			});
		}
	}
}
