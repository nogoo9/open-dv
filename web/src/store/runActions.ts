import {
	api,
	fetchAllRunLogs,
	parseProblemDetails,
	runFailedToProblem,
	streamRunEvents,
} from "../api/client";
import {
	type CertifiedVaultIR,
	type GuidedRunView,
	type OntologyBundleResp,
	type ProblemDetails,
	type ProjectState,
	type RunControlReq,
	type RunStateResp,
	type RunStreamEvent,
	type VaultVerifyResp,
	isRunLiveStatus,
} from "../api/openapi";

export const SSE_RING_MAX = 2000;

export interface RunSlice {
	activeProject: ProjectState | null;
	activeStep4Run: RunStateResp | null;
	activeStep6Run: RunStateResp | null;
	runOrderIds: string[];
	activeStep6OntologyRevId: string | null;
	supersededRunIds: string[];
	sseEvents: RunStreamEvent[];
	lastReceivedSeq: number;
	sseDisconnected: boolean;
	sseActiveRunId: string | null;
	sseBackfillActive: boolean;
	failedVerifyWaveId: string | null;
	lastProblem: ProblemDetails | null;
	guidedView: GuidedRunView | null;
	vaultIr: CertifiedVaultIR | null;
	ontologyBundle: OntologyBundleResp | null;
}

export type StoreAccessor<S extends RunSlice> = {
	get: () => S & {
		announce: (lvl: "polite" | "assertive", m: string) => void;
	};
	set: (partial: Partial<S> | ((prev: S) => Partial<S>)) => void;
};

let sseAc: AbortController | null = null;
let verifyAc: AbortController | null = null;
let tickTimer: ReturnType<typeof setInterval> | null = null;

export function stopElapsedTicker(): void {
	if (tickTimer !== null) {
		clearInterval(tickTimer);
		tickTimer = null;
	}
}

export function mergeSseEvents(
	prev: RunStreamEvent[],
	incoming: RunStreamEvent[],
): { events: RunStreamEvent[]; lastSeq: number } {
	if (!incoming.length) return { events: prev, lastSeq: prev.at(-1)?.seq ?? 0 };
	const seen = new Set(prev.map((e) => e.seq));
	const next = [...prev];
	for (const ev of incoming) {
		if (!seen.has(ev.seq)) {
			seen.add(ev.seq);
			next.push(ev);
		}
	}
	next.sort((a, b) => a.seq - b.seq);
	const capped = next.length > SSE_RING_MAX ? next.slice(-SSE_RING_MAX) : next;
	return { events: capped, lastSeq: capped.at(-1)?.seq ?? 0 };
}

const prependRunId = (ids: string[], rid: string) =>
	ids[0] === rid ? ids : [rid, ...ids.filter((x) => x !== rid)];

export function createRunHelpers<S extends RunSlice>(acc: StoreAccessor<S>) {
	const syncTicker = () => {
		const s = acc.get();
		const live = (r: RunStateResp | null) =>
			r?.status === "RUNNING" || r?.status === "HOLD_REQUESTED";
		if (!live(s.activeStep6Run) && !live(s.activeStep4Run)) {
			stopElapsedTicker();
			return;
		}
		if (tickTimer !== null) return;
		tickTimer = setInterval(() => {
			acc.set((cur) => {
				const up = (r: RunStateResp | null) =>
					r && live(r) ? { ...r, elapsed_seconds: r.elapsed_seconds + 1 } : r;
				const n6 = up(cur.activeStep6Run);
				const n4 = up(cur.activeStep4Run);
				if (n6 === cur.activeStep6Run && n4 === cur.activeStep4Run) {
					stopElapsedTicker();
					return {};
				}
				return { activeStep6Run: n6, activeStep4Run: n4 } as Partial<S>;
			});
		}, 1000);
	};

	const applyRunState = (run: RunStateResp, ontRevId?: string | null) => {
		acc.set(
			(s) =>
				({
					activeStep4Run: run.step === "step4" ? run : s.activeStep4Run,
					activeStep6Run: run.step === "step6" ? run : s.activeStep6Run,
					activeStep6OntologyRevId:
						run.step === "step6" && ontRevId !== undefined
							? ontRevId
							: run.step === "step6" && s.activeStep6OntologyRevId === null
								? (s.activeProject?.active_ontology_rev_id ?? null)
								: s.activeStep6OntologyRevId,
					runOrderIds: prependRunId(s.runOrderIds, run.run_id),
					supersededRunIds: s.supersededRunIds.filter(
						(id) => id !== run.run_id,
					),
				}) as Partial<S>,
		);
		syncTicker();
	};

	const abortRunSse = () => {
		sseAc?.abort();
		sseAc = null;
		stopElapsedTicker();
		acc.set({ sseActiveRunId: null } as Partial<S>);
	};

	const abortVerify = () => {
		verifyAc?.abort();
		verifyAc = null;
	};

	const refreshStep6PostFinish = (pid: string, runId: string) => {
		void Promise.all([
			api.getProject(pid),
			api.getVaultIr(pid),
			api.getGuidedView(pid, runId),
			api.getOntologyBundle(pid),
		])
			.then(([p, ir, gv, ob]) =>
				acc.set({
					activeProject: p,
					vaultIr: ir,
					guidedView: gv,
					ontologyBundle: ob,
				} as Partial<S>),
			)
			.catch(() => undefined);
	};

	const startRunSse = async (
		pid: string,
		runId: string,
		step: "step4" | "step6",
		opts: { backfillLogs?: boolean; initialLastSeq?: number } = {},
	) => {
		abortRunSse();
		const ac = new AbortController();
		sseAc = ac;
		const seedSeq = opts.initialLastSeq ?? 0;
		acc.set({
			sseActiveRunId: runId,
			sseDisconnected: false,
			sseBackfillActive: Boolean(opts.backfillLogs),
			lastReceivedSeq: seedSeq,
		} as Partial<S>);
		syncTicker();

		if (opts.backfillLogs) {
			try {
				const hist = await fetchAllRunLogs(pid, runId, 0, ac.signal);
				if (ac.signal.aborted) return;
				acc.set((s) => {
					const m = mergeSseEvents(s.sseEvents, hist);
					return {
						sseEvents: m.events,
						lastReceivedSeq: Math.max(s.lastReceivedSeq, m.lastSeq, seedSeq),
					} as Partial<S>;
				});
			} catch (e) {
				if (ac.signal.aborted) return;
				acc.set({ lastProblem: parseProblemDetails(e) } as Partial<S>);
			}
		}
		if (ac.signal.aborted) return;

		await streamRunEvents({
			projectId: pid,
			runId,
			step,
			getLastSeq: () => acc.get().lastReceivedSeq,
			signal: ac.signal,
			onEvents: (batch) => {
				let failed: RunStreamEvent | null = null;
				let completed = false;
				let holdCp = false;
				let milestone = false;
				for (const ev of batch) {
					if (ev.event_type === "RUN_FAILED") failed = ev;
					if (ev.event_type === "RUN_COMPLETED") completed = true;
					if (ev.event_type === "HOLD_CHECKPOINTED") holdCp = true;
					if (
						ev.event_type === "STAGE_UPDATE" ||
						ev.event_type === "ATTEMPT_SNAPSHOT" ||
						ev.event_type === "HOLD_CHECKPOINTED" ||
						ev.event_type === "RUN_COMPLETED" ||
						ev.event_type === "RUN_FAILED"
					) {
						milestone = true;
					}
				}
				acc.set((s) => {
					const m = mergeSseEvents(s.sseEvents, batch);
					return {
						sseEvents: m.events,
						lastReceivedSeq: Math.max(s.lastReceivedSeq, m.lastSeq),
						...(failed
							? { lastProblem: runFailedToProblem(runId, failed.payload) }
							: {}),
					} as Partial<S>;
				});
				if (failed) {
					stopElapsedTicker();
					acc.get().announce("assertive", `Run ${runId} failed.`);
				}
				if (completed && step === "step6") {
					acc.set({ failedVerifyWaveId: null } as Partial<S>);
				}
				if (milestone && !ac.signal.aborted) {
					void api
						.getRun(pid, runId, ac.signal)
						.then((st) => {
							if (ac.signal.aborted) return;
							applyRunState(st);
							if (step === "step6" && (holdCp || st.status === "COMPLETED")) {
								void api
									.getGuidedView(pid, runId, ac.signal)
									.then((gv) => {
										if (!ac.signal.aborted)
											acc.set({ guidedView: gv } as Partial<S>);
									})
									.catch(() => undefined);
							}
							if (st.status === "COMPLETED") {
								void api
									.getOntologyBundle(pid, ac.signal)
									.then((b) => {
										if (!ac.signal.aborted)
											acc.set({ ontologyBundle: b } as Partial<S>);
									})
									.catch(() => undefined);
								if (step === "step6") {
									void Promise.all([
										api.getProject(pid, ac.signal),
										api.getVaultIr(pid, ac.signal),
									])
										.then(([p, ir]) => {
											if (!ac.signal.aborted)
												acc.set({
													activeProject: p,
													vaultIr: ir,
												} as Partial<S>);
										})
										.catch(() => undefined);
								}
							}
						})
						.catch(() => undefined);
				}
			},
			onReconciledState: (st) => {
				applyRunState(st);
				if (
					step === "step6" &&
					(st.status === "HOLD_CHECKPOINTED" ||
						st.status === "UNCERTIFIED_DRAFT")
				) {
					void api
						.getGuidedView(pid, runId, ac.signal)
						.then((gv) => {
							if (!ac.signal.aborted) acc.set({ guidedView: gv } as Partial<S>);
						})
						.catch(() => undefined);
				}
			},
			onDisconnected: () => {
				stopElapsedTicker();
				acc.set({ sseDisconnected: true, sseActiveRunId: null } as Partial<S>);
			},
		});
	};

	const runControlAction = async (
		step: "step4" | "step6",
		runId: string,
		req: RunControlReq,
	): Promise<RunStateResp> => {
		const pid = acc.get().activeProject?.project_id;
		if (!pid) throw new Error("No active project.");
		const resp =
			step === "step4"
				? await api.controlOntologyRun(pid, runId, req)
				: await api.controlVaultRun(pid, runId, req);
		applyRunState(resp);
		if (!isRunLiveStatus(resp.status)) {
			const s = acc.get();
			const needLogs = s.sseBackfillActive && s.lastReceivedSeq < resp.last_seq;
			abortRunSse();
			if (needLogs) {
				try {
					const missed = await fetchAllRunLogs(pid, runId, s.lastReceivedSeq);
					acc.set((cur) => {
						const m = mergeSseEvents(cur.sseEvents, missed);
						return {
							sseEvents: m.events,
							lastReceivedSeq: Math.max(cur.lastReceivedSeq, m.lastSeq),
						} as Partial<S>;
					});
				} catch {
					/* ignore backfill failure */
				}
			}
			if (step === "step6") {
				if (resp.status === "COMPLETED" || req.action === "accept-best") {
					acc.set({ failedVerifyWaveId: null } as Partial<S>);
					refreshStep6PostFinish(pid, runId);
				} else {
					void api
						.getGuidedView(pid, runId)
						.then((gv) => acc.set({ guidedView: gv } as Partial<S>))
						.catch(() => undefined);
				}
			}
		} else if (acc.get().sseActiveRunId !== runId) {
			void startRunSse(pid, runId, step, {
				backfillLogs: acc.get().sseBackfillActive,
				initialLastSeq: Math.max(acc.get().lastReceivedSeq, resp.last_seq),
			});
		}
		return resp;
	};

	const verifyVaultAction = async (pid: string): Promise<VaultVerifyResp> => {
		abortVerify();
		const ac = new AbortController();
		verifyAc = ac;
		try {
			const resp = await api.verifyVault(pid, ac.signal);
			if (!ac.signal.aborted) {
				const wave = acc.get().activeProject?.active_wave_id ?? null;
				acc.set({
					failedVerifyWaveId: resp.passed ? null : wave,
				} as Partial<S>);
			}
			return resp;
		} catch (e) {
			if (!ac.signal.aborted)
				acc.set({ lastProblem: parseProblemDetails(e) } as Partial<S>);
			throw e;
		}
	};

	return {
		syncTicker,
		applyRunState,
		abortRunSse,
		abortVerify,
		startRunSse,
		runControlAction,
		verifyVaultAction,
	};
}
