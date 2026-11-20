/**
 * Step 6 Data Vault Pipeline Execution, Baseline Picker & Steward Hold Slice
 * (apps/opendv-web-workbench/src/ontology/store/pipeline-execution-slice.ts)
 */

import { normalizeStageFilterToPill } from "../wizard/logs-types";
import {
	createEmptyZoneSqlByDialect,
	fetchInitialBaselineContract,
	fetchPipelineBaselines,
	fetchRunHydrationBundle,
	formatDriftSteeringPrompt,
	pickLatestSessionRun,
	pollRunUntilSettled,
} from "../wizard/step6-run-hydration";
import {
	partitionPipelineSources,
	reconcileStageStatuses,
} from "../wizard/wizard-step6-adapter";
import type {
	PipelineBaselineOption,
	PipelineHoldSummary,
	PipelineTopologyRow,
	StoreGet,
	StoreSet,
	StudioStoreState,
	ZoneSqlByDialect,
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
	let runHydrationEpoch = 0;
	let lastDriftFindings: Array<Record<string, unknown>> = [];
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
		zoneSqlByDialect: createEmptyZoneSqlByDialect() as ZoneSqlByDialect,
		gateScorecard: null as Record<string, unknown> | null,
		sessionRuns: [] as Array<Record<string, unknown>>,
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
				const options = await fetchPipelineBaselines(
					studioFetch,
					fetchImpl,
					get().activeSession?.session_id ?? null,
				);
				if (!options) return;
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

		hydrateSessionLatestRun: async (
			fetchImpl = fetch,
			targetSessionId?: string,
		): Promise<void> => {
			const sid = targetSessionId ?? get().activeSession?.session_id ?? null;
			if (!sid || get().isRunningPipeline) return;
			const epochAtStart = ++runHydrationEpoch;
			try {
				const res = await studioFetch(fetchImpl, "/api/v1/runs");
				if (!res.ok) return;
				const runs = (await res.json()) as Array<Record<string, unknown>>;
				if (!Array.isArray(runs)) return;
				if (
					epochAtStart !== runHydrationEpoch ||
					get().isRunningPipeline ||
					(get().activeSession && get().activeSession?.session_id !== sid)
				) {
					return;
				}
				const { latestRun, sessionRuns } = pickLatestSessionRun(runs, sid);
				if (!latestRun) {
					lastDriftFindings = [];
					set({
						sessionRuns: [],
						activeRunId: null,
						topologyRows: [],
						stageStatuses: {},
						zoneSqlByZone: {},
						zoneSqlByDialect: createEmptyZoneSqlByDialect(),
						gateScorecard: null,
						activeHold: null,
						driftColumns: [],
					});
					return;
				}
				const bundle = await fetchRunHydrationBundle(
					studioFetch,
					fetchImpl,
					latestRun,
					get().selectedDialects,
				);
				if (
					epochAtStart !== runHydrationEpoch ||
					get().isRunningPipeline ||
					(get().activeSession && get().activeSession?.session_id !== sid)
				) {
					return;
				}
				lastDriftFindings = bundle.driftFindings;
				set({
					sessionRuns,
					activeRunId: bundle.runId,
					stageStatuses: bundle.stageStatuses,
					topologyRows: bundle.topologyRows,
					zoneSqlByDialect: bundle.zoneSqlByDialect,
					zoneSqlByZone: bundle.zoneSqlByZone,
					gateScorecard: bundle.gateScorecard,
					activeHold: bundle.activeHold,
					driftColumns: bundle.driftColumns,
					isRunningPipeline: bundle.isRunning,
				});
			} catch {
				// retain existing state on transient network error
			}
		},

		selectViewedRun: async (
			runId: string,
			fetchImpl = fetch,
		): Promise<void> => {
			if (!runId || get().isRunningPipeline) return;
			const epochAtStart = ++runHydrationEpoch;
			const match = get().sessionRuns.find(
				(r) => String(r.run_id ?? "") === runId,
			) ?? { run_id: runId };
			try {
				const bundle = await fetchRunHydrationBundle(
					studioFetch,
					fetchImpl,
					match,
					get().selectedDialects,
				);
				if (epochAtStart !== runHydrationEpoch || get().isRunningPipeline) {
					return;
				}
				lastDriftFindings = bundle.driftFindings;
				set({
					activeRunId: bundle.runId,
					stageStatuses: bundle.stageStatuses,
					topologyRows: bundle.topologyRows,
					zoneSqlByDialect: bundle.zoneSqlByDialect,
					zoneSqlByZone: bundle.zoneSqlByZone,
					gateScorecard: bundle.gateScorecard,
					activeHold: bundle.activeHold,
					driftColumns: bundle.driftColumns,
				});
			} catch {
				// ignore transient error
			}
		},

		startDvPipelineRun: async (
			opts?: { sourceUris?: string[] },
			fetchImpl = fetch,
		): Promise<string | null> => {
			const session = get().activeSession;
			const sid = session?.session_id;
			if (!sid) return null;
			runHydrationEpoch += 1;
			lastDriftFindings = [];
			set({
				isRunningPipeline: true,
				activeHold: null,
				driftColumns: [],
				gateScorecard: null,
				agentErrorCode: null,
				agentErrorDetail: null,
				stageStatuses: { STAGE_00_ORCHESTRATION: "RUNNING" },
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
					initialContractContent = await fetchInitialBaselineContract(
						studioFetch,
						fetchImpl,
						baselineId,
					);
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
						stageStatuses: reconcileStageStatuses(
							get().stageStatuses,
							null,
							"FAILED",
						),
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
						const st = String(rawStage ?? "").trim();
						if (!st) return;
						set((prev) => {
							const next = { ...prev.stageStatuses, [st]: status };
							const pill = normalizeStageFilterToPill(st);
							if (pill !== "all" && pill !== st && pill in next) {
								delete next[pill];
							}
							return { stageStatuses: next };
						});
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
							stage_id: String(d.stage_id ?? "STAGE_04_SHIP_VAULT_PASS2"),
							headline: String(
								d.headline ?? d.reason ?? d.summary ?? "Steward Hold Required",
							),
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
									headline: String(
										first.headline ??
											first.reason ??
											first.summary ??
											hold.headline,
									),
									status: String(first.status ?? "PENDING"),
								};
							}
						}
						set((prev) => {
							const next = { ...prev.stageStatuses };
							for (const [k, v] of Object.entries(next)) {
								if (v === "RUNNING") next[k] = "PENDING";
							}
							next[hold.stage_id] = "HOLD";
							return {
								activeHold: hold,
								isRunningPipeline: false,
								stageStatuses: next,
							};
						});
					});
					es.addEventListener("run_complete", async (ev) => {
						const d = JSON.parse((ev as MessageEvent).data) as Record<
							string,
							unknown
						>;
						const sseCols = Array.isArray(d.drift_columns)
							? d.drift_columns.map(String)
							: [];
						const sseFindings = Array.isArray(d.drift_findings)
							? (d.drift_findings as Array<Record<string, unknown>>)
							: [];
						if (sseFindings.length > 0) {
							lastDriftFindings = sseFindings;
						}
						if (sseCols.length > 0) {
							set({ driftColumns: sseCols });
						}
						let rep: Record<string, unknown> | null = null;
						try {
							const bundle = await fetchRunHydrationBundle(
								studioFetch,
								fetchImpl,
								{ run_id: runId, status: String(d.status ?? "COMPLETED") },
								get().selectedDialects,
							);
							rep = bundle.report;
							const mergedFindings =
								bundle.driftFindings.length > 0
									? bundle.driftFindings
									: sseFindings;
							lastDriftFindings = mergedFindings;
							const mergedCols = Array.from(
								new Set([...bundle.driftColumns, ...sseCols]),
							);
							set({ driftColumns: mergedCols });
							if (Object.keys(bundle.zoneSqlByZone).length > 0) {
								set({ zoneSqlByZone: bundle.zoneSqlByZone });
							}
							if (
								Object.keys(bundle.zoneSqlByDialect.duckdb).length > 0 ||
								Object.keys(bundle.zoneSqlByDialect.databricks).length > 0
							) {
								set({ zoneSqlByDialect: bundle.zoneSqlByDialect });
							}
							if (bundle.gateScorecard) {
								set({ gateScorecard: bundle.gateScorecard });
							}
							if (bundle.topologyRows.length > 0) {
								set({ topologyRows: bundle.topologyRows });
							}
						} finally {
							set((prev) => ({
								isRunningPipeline: false,
								stageStatuses: reconcileStageStatuses(
									prev.stageStatuses,
									rep,
									d.status,
								),
							}));
							closeRunStream();
						}
					});
				}
				return runId;
			} catch (err) {
				set((prev) => ({
					isRunningPipeline: false,
					stageStatuses: reconcileStageStatuses(
						prev.stageStatuses,
						null,
						"FAILED",
					),
				}));
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
				const resuming = params.action !== "REJECT";
				const epochAtResolve = runHydrationEpoch;
				set({
					activeHold: null,
					isRunningPipeline: resuming,
				});
				if (!resuming) {
					await get().hydrateSessionLatestRun(
						fetchImpl,
						get().activeSession?.session_id,
					);
				} else {
					void pollRunUntilSettled(
						studioFetch,
						fetchImpl,
						runId,
						() =>
							get().isRunningPipeline && epochAtResolve === runHydrationEpoch,
						async () => {
							set({ isRunningPipeline: false });
							await get().hydrateSessionLatestRun(
								fetchImpl,
								get().activeSession?.session_id,
							);
						},
					);
				}
				return true;
			} catch {
				return false;
			}
		},

		abortPipelineRun: async (fetchImpl = fetch): Promise<boolean> => {
			const runId = get().activeRunId;
			if (!runId) return false;
			runHydrationEpoch += 1;
			closeRunStream();
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/runs/${encodeURIComponent(runId)}/abort`,
					{ method: "POST" },
				);
				set((prev) => ({
					isRunningPipeline: false,
					activeHold: null,
					stageStatuses: reconcileStageStatuses(
						prev.stageStatuses,
						null,
						"FAILED",
					),
				}));
				return res.ok;
			} catch {
				set((prev) => ({
					isRunningPipeline: false,
					stageStatuses: reconcileStageStatuses(
						prev.stageStatuses,
						null,
						"FAILED",
					),
				}));
				return false;
			}
		},

		syncDriftToStep4: async (fetchImpl = fetch): Promise<boolean> => {
			const steer = formatDriftSteeringPrompt(
				get().driftColumns,
				lastDriftFindings,
			);
			const jobId = await get().startConstruct(
				{ mode: "agent", steering_prompt: steer },
				fetchImpl,
			);
			return Boolean(jobId);
		},
	};
}
