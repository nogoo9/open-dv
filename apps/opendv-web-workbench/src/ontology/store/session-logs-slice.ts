/**
 * Session Logs, Cost Aggregation & Budget Steward Hold Resume Slice
 * (apps/opendv-web-workbench/src/ontology/store/session-logs-slice.ts)
 */

import type { SessionLogEntryDTO, StoreGet, StoreSet } from "./store-types";
import type { StudioFetcher } from "./studio-fetch";

export function createSessionLogsSlice(
	set: StoreSet,
	get: StoreGet,
	studioFetch: StudioFetcher,
) {
	return {
		sessionLogEntries: [] as SessionLogEntryDTO[],
		sessionLogsTotal: 0,
		sessionCostTokens: 0,
		sessionCostUsd: 0,
		isLogsDrawerOpen: false,
		logsStageFilter: null as string | null,
		logsStepFilter: null as number | null,
		followLiveTail: true,

		openLogsDrawer: (opts?: {
			stageFilter?: string | null;
			stepFilter?: number | null;
		}) =>
			set({
				isLogsDrawerOpen: true,
				logsStageFilter: opts?.stageFilter ?? null,
				logsStepFilter: opts?.stepFilter ?? null,
			}),

		closeLogsDrawer: () => set({ isLogsDrawerOpen: false }),

		setFollowLiveTail: (follow: boolean) => set({ followLiveTail: follow }),

		loadSessionLogs: async (
			opts?: {
				stageId?: string | null;
				wizardStep?: number | null;
				persona?: string | null;
			},
			fetchImpl = fetch,
		): Promise<void> => {
			const sid = get().activeSession?.session_id;
			if (!sid) return;
			const qs = new URLSearchParams();
			if (opts?.stageId) qs.set("stage_id", opts.stageId);
			if (opts?.wizardStep !== undefined && opts?.wizardStep !== null) {
				qs.set("wizard_step", String(opts.wizardStep));
			}
			if (opts?.persona) qs.set("persona", opts.persona);
			const suffix = qs.toString() ? `?${qs.toString()}` : "";
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/logs${suffix}`,
				);
				if (!res.ok) return;
				const data = (await res.json()) as {
					entries?: SessionLogEntryDTO[];
					total?: number;
				};
				const entries = Array.isArray(data.entries) ? data.entries : [];
				const byJob = new Map<string, { tokens: number; usd: number }>();
				for (const e of entries) {
					if (!e.token_usage) continue;
					const jid = e.job_id || `seq-${e.seq}`;
					const prev = byJob.get(jid);
					const tok = Number(e.token_usage.total_tokens ?? 0);
					const usd = Number(e.token_usage.cost_usd ?? 0);
					if (!prev || e.event === "agent_telemetry" || tok >= prev.tokens) {
						byJob.set(jid, { tokens: tok, usd });
					}
				}
				let totalTokens = 0;
				let totalUsd = 0;
				for (const v of byJob.values()) {
					totalTokens += v.tokens;
					totalUsd += v.usd;
				}
				set({
					sessionLogEntries: entries,
					sessionLogsTotal: Number(data.total ?? entries.length),
					sessionCostTokens: totalTokens,
					sessionCostUsd: totalUsd,
				});
			} catch {
				// ignore network transient
			}
		},

		raiseBudgetAndResume: async (
			extraCostUsd = 5,
			fetchImpl = fetch,
		): Promise<boolean> => {
			const hold = get().budgetHold;
			try {
				const cfgRes = await studioFetch(fetchImpl, "/api/v1/platform/config");
				if (cfgRes.ok) {
					const cfg = (await cfgRes.json()) as Record<string, unknown>;
					const harness =
						(cfg.harness as Record<string, unknown> | undefined) ?? {};
					const budget =
						(harness.budget as Record<string, unknown> | undefined) ?? {};
					const currentMax = Number(budget.max_cost_usd ?? 2);
					const nextMax = Number((currentMax + extraCostUsd).toFixed(2));
					const reasonUp = String(hold?.reason ?? "").toUpperCase();
					const curOut = Number(budget.max_output_tokens ?? 32000);
					const curIn = Number(budget.max_input_tokens ?? 200000);
					const curTimeout = Number(budget.timeout_seconds ?? 300);
					const nextBudget: Record<string, unknown> = {
						...budget,
						max_cost_usd: nextMax,
						...(reasonUp.includes("TOKEN") || curOut < 64000
							? {
									max_output_tokens: Math.min(
										200000,
										Math.max(64000, curOut * 2),
									),
									...(curIn < 200000 ? { max_input_tokens: 200000 } : {}),
								}
							: {}),
						...(reasonUp.includes("TIMEOUT")
							? {
									timeout_seconds: Math.min(
										3600,
										Math.max(600, curTimeout * 2),
									),
								}
							: {}),
					};
					const putRes = await studioFetch(
						fetchImpl,
						"/api/v1/platform/config",
						{
							method: "PUT",
							headers: { "Content-Type": "application/json" },
							body: JSON.stringify({
								harness: {
									...harness,
									budget: nextBudget,
								},
							}),
						},
					);
					if (!putRes.ok) return false;
				}
				set({
					budgetHold: null,
					agentErrorCode: null,
					agentErrorDetail: null,
				});
				if (!hold || hold.reason) {
					const jobId = await get().startConstruct(
						{ mode: "agent" },
						fetchImpl,
					);
					return Boolean(jobId);
				}
				return true;
			} catch {
				return false;
			}
		},

		abortBudgetHold: async (fetchImpl = fetch): Promise<boolean> => {
			const hold = get().budgetHold;
			const sid = get().activeSession?.session_id;
			const runId = get().activeRunId;
			const jobId = hold?.job_id ?? get().activeSessionJobId;
			try {
				if (runId && (!jobId || hold?.job_id === runId)) {
					await get().abortPipelineRun(fetchImpl);
				} else if (sid && jobId && jobId !== "active-session-job") {
					await studioFetch(
						fetchImpl,
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/jobs/${encodeURIComponent(jobId)}/cancel`,
						{
							method: "POST",
							headers: { "Content-Type": "application/json" },
							body: JSON.stringify({
								reason: "Budget Steward Hold aborted by steward",
							}),
						},
					).catch(() => null);
				}
			} finally {
				set({
					activeSessionJobId: null,
					isClarifying: false,
					isConstructing: false,
					isRunningPipeline: false,
				});
				get().clearBudgetHold();
			}
			return true;
		},
	};
}
