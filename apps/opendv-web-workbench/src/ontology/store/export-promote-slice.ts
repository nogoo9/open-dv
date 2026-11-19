/**
 * Ontology Export, Workspace Promotion & Blueprint Bridge Slice
 * (apps/opendv-web-workbench/src/ontology/store/export-promote-slice.ts)
 */

import { MIN_RATIONALE_LENGTH } from "../constants";
import type { StoreGet, StoreSet } from "./store-types";
import type { StudioFetcher } from "./studio-fetch";

export function createExportPromoteSlice(
	set: StoreSet,
	get: StoreGet,
	studioFetch: StudioFetcher,
) {
	return {
		isPromoting: false,
		exportOntology: async (
			format: string,
			version?: string,
			fetchImpl = fetch,
		): Promise<string> => {
			const activeSession = get().activeSession;
			if (!activeSession) throw new Error("No active session");

			const normalized = format.toLowerCase();
			const serverFormat =
				normalized === "rfc8785" || normalized === "jcs" ? "json" : format;
			const ver = version || activeSession.active_version;
			const path = `/api/v1/ontology/studio/sessions/${encodeURIComponent(activeSession.session_id)}/export?export_format=${encodeURIComponent(serverFormat)}&version=${encodeURIComponent(ver)}`;

			let res: Response;
			try {
				res = await studioFetch(fetchImpl, path);
			} catch (err) {
				get().addToast({
					type: "urn:opendv:error:export-network",
					title: "Export Network Error",
					status: 503,
					detail: String(err),
					code: "EXPORT_NETWORK_ERROR",
				});
				throw err;
			}
			if (!res.ok) {
				const err = (await res.json().catch(() => ({}))) as {
					detail?: string;
					code?: string;
					title?: string;
				};
				const detail = err.detail || `Export failed with HTTP ${res.status}`;
				get().addToast({
					type: "urn:opendv:error:export-failed",
					title: err.title || "Ontology Export Failed",
					status: res.status,
					detail,
					code: err.code || "EXPORT_FAILED",
				});
				throw new Error(detail);
			}
			return await res.text();
		},

		promoteWorkspace: async (
			req: {
				workspace?: string;
				workspace_sha256: string;
				head_sha256: string;
				rationale: string;
				acknowledge_historical_contract_warning?: boolean;
				historical_contract_rationale?: string;
			},
			fetchImpl = fetch,
		): Promise<boolean> => {
			if (get().isPromoting) return false;
			const activeSession = get().activeSession;
			if (!activeSession) return false;

			if (
				!req.rationale ||
				req.rationale.trim().length < MIN_RATIONALE_LENGTH
			) {
				get().addToast({
					type: "urn:opendv:error:promotion-rationale-short",
					title: "Promotion Rationale Too Short",
					status: 400,
					detail: `Promotion rationale must be at least ${MIN_RATIONALE_LENGTH} characters.`,
					code: "PROMOTION_RATIONALE_REQUIRED",
				});
				return false;
			}

			if (get().hasUnsavedChanges) {
				get().addToast({
					type: "urn:opendv:error:unsaved-changes",
					title: "Unsaved Changes",
					status: 400,
					detail:
						"Cannot promote workspace with uncommitted changes. Please commit or discard changes first.",
					code: "PROMOTION_UNSAVED_CHANGES",
				});
				return false;
			}

			const shaRegex = /^[0-9a-f]{64}$/i;
			if (
				!shaRegex.test(req.head_sha256) ||
				!shaRegex.test(req.workspace_sha256)
			) {
				get().addToast({
					type: "urn:opendv:error:invalid-sha256-format",
					title: "Invalid Digest Format",
					status: 400,
					detail:
						"head_sha256 and workspace_sha256 must be valid 64-character hexadecimal SHA-256 digests.",
					code: "INVALID_SHA256_DIGEST",
				});
				return false;
			}

			if (req.head_sha256 !== activeSession.head_sha256) {
				get().addToast({
					type: "urn:opendv:error:occ-precondition-failed",
					title: "Stale Session Head",
					status: 412,
					detail:
						"Provided head_sha256 does not match current session head. Refresh workspace state.",
					code: "OCC_PRECONDITION_FAILED",
				});
				return false;
			}

			const sid = activeSession.session_id;
			const trimmedWorkspace = req.workspace?.trim();
			const trimmedHistoricalRationale =
				req.historical_contract_rationale?.trim();
			const promoteBody: Record<string, unknown> = {
				workspace_sha256: req.workspace_sha256,
				head_sha256: req.head_sha256,
				rationale: req.rationale.trim(),
				...(trimmedWorkspace ? { workspace: trimmedWorkspace } : {}),
				...(req.acknowledge_historical_contract_warning !== undefined
					? {
							acknowledge_historical_contract_warning:
								req.acknowledge_historical_contract_warning,
						}
					: {}),
				...(trimmedHistoricalRationale
					? { historical_contract_rationale: trimmedHistoricalRationale }
					: {}),
			};
			set({ isPromoting: true });
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/promote`,
					{
						method: "POST",
						headers: {
							"Content-Type": "application/json",
							"If-Match": `"${req.head_sha256}"`,
							"X-OpenDV-Workspace-If-Match": `"${req.workspace_sha256}"`,
						},
						body: JSON.stringify(promoteBody),
					},
				);
				if (!res.ok) {
					const err = (await res.json().catch(() => ({}))) as {
						detail?: string;
						code?: string;
					};
					if (get().activeSession?.session_id !== sid) return false;
					get().addToast({
						type: "urn:opendv:error:promote-rejected",
						title: "Promotion Rejected",
						status: res.status,
						detail: err.detail || `Server returned HTTP ${res.status}`,
						code: err.code || "ONT_EVO_CONCURRENT_MODIFICATION",
					});
					return false;
				}
				const promoData = (await res.json().catch(() => ({}))) as {
					promoted_version?: string;
					workspace_sha256?: string;
					head_sha256?: string;
				};
				const currentSession = get().activeSession;
				if (!currentSession || currentSession.session_id !== sid) {
					if (promoData.promoted_version || promoData.head_sha256) {
						set((state) => ({
							sessions: state.sessions.map((s) =>
								s.session_id === sid
									? {
											...s,
											status: "PROMOTED",
											head_sha256: promoData.head_sha256 || s.head_sha256,
											active_version:
												promoData.promoted_version || s.active_version,
										}
									: s,
							),
						}));
					}
					return false;
				}
				const newSha = promoData.head_sha256 || currentSession.head_sha256;
				if (!newSha) {
					get().addToast({
						type: "urn:opendv:error:promote-no-sha",
						title: "Promotion SHA Missing",
						status: 500,
						detail:
							"Server promotion response did not include a valid SHA-256 digest.",
						code: "PROMOTION_SHA_MISSING",
					});
					return false;
				}
				const newVersion =
					promoData.promoted_version || currentSession.active_version;
				set((state) => ({
					activeSession: state.activeSession
						? {
								...state.activeSession,
								status: "PROMOTED",
								active_version: newVersion,
								head_sha256: newSha,
							}
						: null,
					sessions: state.sessions.map((s) =>
						s.session_id === sid
							? {
									...s,
									status: "PROMOTED",
									active_version: newVersion,
									head_sha256: newSha,
								}
							: s,
					),
					layout: { ...state.layout, activeModal: null },
				}));
				get().addToast({
					type: "urn:opendv:notice:promoted",
					title: "Workspace Promotion Succeeded",
					status: 200,
					detail:
						"Ontology version successfully promoted into workspace pipeline.",
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
				set({ isPromoting: false });
			}
		},

		exportBlueprint: async (
			versionTag?: string,
			fetchImpl = fetch,
		): Promise<Record<string, unknown> | null> => {
			const sid = get().activeSession?.session_id;
			if (!sid) return null;
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/export-blueprint`,
					{
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify(versionTag ? { version_tag: versionTag } : {}),
					},
				);
				if (!res.ok) return null;
				const data = (await res.json()) as Record<string, unknown>;
				const targetTables = Array.isArray(data.target_tables)
					? (data.target_tables as Array<Record<string, unknown>>)
					: [];
				if (targetTables.length > 0) {
					set({
						topologyRows: targetTables.map((row) => ({
							entity_name: String(row.entity_name ?? row.table_name ?? ""),
							entity_kind: String(row.entity_kind ?? row.kind ?? "Hub"),
							zone: String(row.zone ?? "Z2"),
							source_mapping: String(row.source_mapping ?? ""),
						})),
					});
				}
				return data;
			} catch {
				return null;
			}
		},
	};
}
