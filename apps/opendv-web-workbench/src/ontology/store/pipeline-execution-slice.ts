/**
 * Step 6 Data Vault Pipeline Execution, Baseline Picker & Steward Hold Slice
 * (apps/opendv-web-workbench/src/ontology/store/pipeline-execution-slice.ts)
 */

import {
	extractBaselineContractFromReportOrArtifacts,
	extractTopologyRowsFromReport,
	extractZoneSqlFromReport,
	partitionPipelineSources,
} from "../wizard/wizard-step6-adapter";
import type {
	PipelineBaselineOption,
	PipelineHoldSummary,
	PipelineTopologyRow,
	StoreGet,
	StoreSet,
	StudioStoreState,
} from "./store-types";
import type { StudioFetcher } from "./studio-fetch";

export function createPipelineExecutionSlice(
	set: StoreSet,
	get: StoreGet,
	studioFetch: StudioFetcher,
	resolveUrl: (path: string) => string,
	registerCleanup?: (closeFn: () => void) => void,
) {
	let activeRunEs: { close: () => void } | null = null;
	let baselineSelectionEpoch = 0;
	const closeRunStream = () => {
		if (activeRunEs) {
			try {
				activeRunEs.close();
			} catch {
				// ignore close error
			}
			activeRunEs = null;
		}
	};
	registerCleanup?.(closeRunStream);

	return {
		selectedDialects: ["duckdb"] as Array<"duckdb" | "databricks">,
		baselineOptions: [
			{
				run_id: "__cold_start__",
				label: "Fresh Cold-Start Vault",
				is_latest_certified: false,
			},
		] as PipelineBaselineOption[],
		selectedBaselineRunId: "__cold_start__",
		customBaselineContractContent: null as string | null,
		topologyRows: [] as PipelineTopologyRow[],
		stageStatuses: {} as StudioStoreState["stageStatuses"],
		zoneSqlByZone: {} as Record<string, string>,
		activeRunId: null as string | null,
		isRunningPipeline: false,
		activeHold: null as PipelineHoldSummary | null,
		driftColumns: [] as string[],

		setSelectedDialects: (dialects: Array<"duckdb" | "databricks">) =>
			set({ selectedDialects: dialects.length > 0 ? dialects : ["duckdb"] }),

		setSelectedBaselineRunId: (
			runId: string,
			customContractContent?: string | null,
		) => {
			baselineSelectionEpoch += 1;
			set({
				selectedBaselineRunId: runId,
				customBaselineContractContent:
					customContractContent !== undefined ? customContractContent : null,
			});
		},

		clearActiveHold: () => set({ activeHold: null }),

		loadPipelineBaselines: async (fetchImpl = fetch): Promise<void> => {
			const epochAtStart = baselineSelectionEpoch;
			try {
				const res = await studioFetch(fetchImpl, "/api/v1/runs");
				if (!res.ok) return;
				const runs = (await res.json()) as Array<Record<string, unknown>>;
				if (!Array.isArray(runs)) return;
				const certified = runs.filter(
					(r) => String(r.status ?? "").toUpperCase() === "COMPLETED",
				);
				const options: PipelineBaselineOption[] = [
					{
						run_id: "__cold_start__",
						label: "Fresh Cold-Start Vault",
						is_latest_certified: false,
					},
					...certified.map((r, idx) => ({
						run_id: String(r.run_id ?? ""),
						label: `${String(r.run_id ?? "")}${idx === 0 ? " (Latest Certified)" : ""}`,
						is_latest_certified: idx === 0,
					})),
				];
				if (baselineSelectionEpoch !== epochAtStart) {
					set({ baselineOptions: options });
					return;
				}
				set({
					baselineOptions: options,
					selectedBaselineRunId:
						options.find((o) => o.is_latest_certified)?.run_id ??
						"__cold_start__",
					customBaselineContractContent: null,
				});
			} catch {
				// retain default cold-start baseline
			}
		},

		startDvPipelineRun: async (
			opts?: { sourceUris?: string[] },
			fetchImpl = fetch,
		): Promise<string | null> => {
			const session = get().activeSession;
			const sid = session?.session_id;
			if (!sid) return null;
			set({
				isRunningPipeline: true,
				activeHold: null,
				agentErrorCode: null,
				agentErrorDetail: null,
				stageStatuses: { "00": "RUNNING" },
			});
			try {
				const normalizedVer = String(session?.active_version ?? "").replace(
					/^v/i,
					"",
				);
				const hasCommittedOntology =
					session?.status !== "EMPTY" &&
					((normalizedVer.length > 0 && normalizedVer !== "0.0.0") ||
						get().committedNodes.length > 0);
				const bpRes = hasCommittedOntology
					? await get().exportBlueprint(undefined, fetchImpl)
					: null;
				let initialContractContent = get().customBaselineContractContent;
				const baselineId = get().selectedBaselineRunId;
				if (
					!initialContractContent &&
					baselineId &&
					baselineId !== "__cold_start__" &&
					baselineId !== "__uploaded__"
				) {
					const repRes = await studioFetch(
						fetchImpl,
						`/api/v1/runs/${encodeURIComponent(baselineId)}/report`,
					).catch(() => null);
					const rep = repRes?.ok
						? ((await repRes.json().catch(() => null)) as Record<
								string,
								unknown
							> | null)
						: null;
					initialContractContent = extractBaselineContractFromReportOrArtifacts(
						rep,
						[],
					);
					if (!initialContractContent) {
						const artRes = await studioFetch(
							fetchImpl,
							`/api/v1/runs/${encodeURIComponent(baselineId)}/artifacts`,
						).catch(() => null);
						const arts = artRes?.ok
							? ((await artRes.json().catch(() => [])) as Array<
									Record<string, unknown>
								>)
							: [];
						initialContractContent =
							extractBaselineContractFromReportOrArtifacts(rep, arts);
					}
				}
				const {
					sources: sourceFeeds,
					unstructured_documents: unstructuredDocs,
				} = partitionPipelineSources(
					get().sources,
					get().evidenceChunks,
					opts?.sourceUris,
				);
				const targetDialect = get().selectedDialects.includes("databricks")
					? "databricks"
					: (get().selectedDialects[0] ?? "duckdb");
				const needsUnstructuredOnlyFlag =
					unstructuredDocs.length > 0 &&
					sourceFeeds.length === 0 &&
					!bpRes?.blueprint &&
					!initialContractContent;

				const payload: Record<string, unknown> = {
					session_id: sid,
					target_dialect: targetDialect,
					sources: sourceFeeds,
					...(unstructuredDocs.length > 0
						? { unstructured_documents: unstructuredDocs }
						: {}),
					...(needsUnstructuredOnlyFlag ? { unstructured_only: true } : {}),
					...(bpRes?.blueprint ? { blueprint: bpRes.blueprint } : {}),
					...(initialContractContent
						? { initial_contract_content: initialContractContent }
						: {}),
				};

				const res = await studioFetch(fetchImpl, "/api/v1/pipelines/runs", {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify(payload),
				});
				if (!res.ok) {
					const err = (await res.json().catch(() => ({}))) as {
						code?: string;
						error_code?: string;
						detail?: string;
					};
					const errCode = err.code || err.error_code || "PIPELINE_RUN_FAILED";
					const errDetail = err.detail || `Server returned HTTP ${res.status}`;
					set({
						isRunningPipeline: false,
						agentErrorCode: errCode,
						agentErrorDetail: errDetail,
						stageStatuses: { ...get().stageStatuses, "00": "FAILED" },
					});
					get().addToast({
						type: "urn:opendv:error:pipeline-run-failed",
						title: "Pipeline Execution Blocked",
						status: res.status,
						detail: errDetail,
						code: errCode,
					});
					return null;
				}
				const run = (await res.json()) as { run_id?: string };
				const runId = run.run_id ?? "";
				set({ activeRunId: runId });

				closeRunStream();
				const EsCtorRaw = (globalThis as Record<string, unknown>).EventSource;
				if (typeof EsCtorRaw === "function" && runId) {
					const EsCtor = EsCtorRaw as new (url: string) => EventSource;
					const es = new EsCtor(
						resolveUrl(`/api/v1/runs/${encodeURIComponent(runId)}/events`),
					);
					activeRunEs = es;
					const syncStage = (
						rawStage: unknown,
						status: "RUNNING" | "COMPLETED" | "FAILED" | "HOLD",
					) => {
						const st = String(rawStage ?? "");
						set((prev) => ({
							stageStatuses: { ...prev.stageStatuses, [st]: status },
						}));
					};
					es.addEventListener("STAGE_STARTED", (ev) => {
						const d = JSON.parse((ev as MessageEvent).data) as Record<
							string,
							unknown
						>;
						syncStage(d.stage_id ?? d.stage, "RUNNING");
					});
					es.addEventListener("STAGE_COMPLETED", (ev) => {
						const d = JSON.parse((ev as MessageEvent).data) as Record<
							string,
							unknown
						>;
						syncStage(d.stage_id ?? d.stage, "COMPLETED");
					});
					es.addEventListener("STAGE_FAILED", (ev) => {
						const d = JSON.parse((ev as MessageEvent).data) as Record<
							string,
							unknown
						>;
						syncStage(d.stage_id ?? d.stage, "FAILED");
					});
					es.addEventListener("hold", async (ev) => {
						const d = JSON.parse((ev as MessageEvent).data) as Record<
							string,
							unknown
						>;
						const decRes = await studioFetch(
							fetchImpl,
							`/api/v1/runs/${encodeURIComponent(runId)}/decisions`,
						).catch(() => null);
						let hold: PipelineHoldSummary = {
							hold_id: String(d.hold_id ?? `hold-${runId}`),
							run_id: runId,
							stage_id: String(d.stage_id ?? "STAGE_04"),
							headline: String(d.headline ?? "Steward Hold Required"),
							status: "PENDING",
						};
						if (decRes?.ok) {
							const dec = (await decRes.json()) as {
								active_holds?: Array<Record<string, unknown>>;
							};
							const first = dec.active_holds?.[0];
							if (first) {
								hold = {
									hold_id: String(first.hold_id ?? hold.hold_id),
									run_id: runId,
									stage_id: String(first.stage_id ?? hold.stage_id),
									headline: String(first.headline ?? hold.headline),
									status: String(first.status ?? "PENDING"),
								};
							}
						}
						set({ activeHold: hold, isRunningPipeline: false });
					});
					es.addEventListener("run_complete", async (ev) => {
						const d = JSON.parse((ev as MessageEvent).data) as Record<
							string,
							unknown
						>;
						if (Array.isArray(d.drift_columns)) {
							set({ driftColumns: d.drift_columns.map(String) });
						}
						try {
							const repRes = await studioFetch(
								fetchImpl,
								`/api/v1/runs/${encodeURIComponent(runId)}/report`,
							).catch(() => null);
							const rep = repRes?.ok
								? ((await repRes.json().catch(() => null)) as Record<
										string,
										unknown
									> | null)
								: null;
							let arts: Array<Record<string, unknown>> = [];
							let hydratedSql = rep
								? extractZoneSqlFromReport(rep, get().selectedDialects, arts)
								: {};
							let hydratedRows = rep
								? extractTopologyRowsFromReport(rep, arts)
								: [];
							if (
								Object.keys(hydratedSql).length === 0 ||
								hydratedRows.length === 0
							) {
								const artRes = await studioFetch(
									fetchImpl,
									`/api/v1/runs/${encodeURIComponent(runId)}/artifacts`,
								).catch(() => null);
								if (artRes?.ok) {
									const rawArts = await artRes.json().catch(() => []);
									if (Array.isArray(rawArts)) {
										arts = rawArts as Array<Record<string, unknown>>;
										if (Object.keys(hydratedSql).length === 0) {
											hydratedSql = extractZoneSqlFromReport(
												rep ?? {},
												get().selectedDialects,
												arts,
											);
										}
										if (hydratedRows.length === 0) {
											hydratedRows = extractTopologyRowsFromReport(
												rep ?? {},
												arts,
											);
										}
									}
								}
							}
							if (Object.keys(hydratedSql).length > 0) {
								set({ zoneSqlByZone: hydratedSql });
							}
							if (hydratedRows.length > 0) {
								set({ topologyRows: hydratedRows });
							}
						} finally {
							set({ isRunningPipeline: false });
							closeRunStream();
						}
					});
				}
				return runId;
			} catch (err) {
				set({ isRunningPipeline: false });
				get().addToast({
					type: "urn:opendv:error:network",
					title: "Pipeline Network Error",
					status: 503,
					detail: String(err),
					code: "NETWORK_ERROR",
				});
				return null;
			}
		},

		resolveActiveHold: async (
			params: {
				holdId: string;
				action: "APPROVE" | "REJECT" | "ANSWER";
				selectedCandidateId?: string;
				resolutionNotes?: string;
			},
			fetchImpl = fetch,
		): Promise<boolean> => {
			const runId = get().activeRunId || get().activeHold?.run_id;
			if (!runId) return false;
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/runs/${encodeURIComponent(runId)}/decisions`,
					{
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({
							hold_id: params.holdId,
							decision_type: "RESOLVE_CAPTAIN_HOLD",
							action: params.action,
							...(params.selectedCandidateId
								? { selected_candidate_id: params.selectedCandidateId }
								: {}),
							...(params.resolutionNotes
								? { resolution_notes: params.resolutionNotes }
								: {}),
						}),
					},
				);
				if (!res.ok) return false;
				set({
					activeHold: null,
					isRunningPipeline: params.action !== "REJECT",
				});
				return true;
			} catch {
				return false;
			}
		},

		abortPipelineRun: async (fetchImpl = fetch): Promise<boolean> => {
			const runId = get().activeRunId;
			if (!runId) return false;
			closeRunStream();
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/runs/${encodeURIComponent(runId)}/abort`,
					{ method: "POST" },
				);
				set({ isRunningPipeline: false, activeHold: null });
				return res.ok;
			} catch {
				set({ isRunningPipeline: false });
				return false;
			}
		},

		syncDriftToStep4: async (fetchImpl = fetch): Promise<boolean> => {
			const drift = get().driftColumns;
			const steer =
				drift.length > 0
					? `Schema drift detected unmapped columns: ${drift.join(", ")}. Align ontology concepts and attributes.`
					: "Re-evaluate new source columns discovered during pipeline execution.";
			const jobId = await get().startConstruct(
				{ mode: "agent", steering_prompt: steer },
				fetchImpl,
			);
			return Boolean(jobId);
		},
	};
}
