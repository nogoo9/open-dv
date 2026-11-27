import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
	api,
	fetchAllRunLogs,
	parseProblemDetails,
	streamRunEvents,
} from "../api/client";
import {
	type AttemptSnapshotSpec,
	type ExternalMcpReceipt,
	type RunStateResp,
	type RunStreamEvent,
	isRunHaltStatus,
	isRunLiveStatus,
} from "../api/openapi";
import { useProjectStore } from "../store/projectStore";

const TRACE_TABS = [
	["payload", "1 Event Payload & Prompts"],
	["attempts", "2 Attempt IR & Gate Diffs"],
	["receipts", "3 Broker, OCR & MCP Receipts"],
	["redaction", "4 Redaction & Budget Telemetry"],
] as const;

type TraceTab = (typeof TRACE_TABS)[number][0];

export function RunExplorerPage() {
	const activeProject = useProjectStore((s) => s.activeProject);
	const currentUrl = useProjectStore((s) => s.currentUrl);
	const navigate = useProjectStore((s) => s.navigate);
	const setLastProblem = useProjectStore((s) => s.setLastProblem);

	const parsed = new URL(currentUrl, "http://localhost");
	const match = /^\/projects\/([^/]+)\/runs(?:\/([^/]+))?$/.exec(
		parsed.pathname,
	);
	const pid = activeProject?.project_id ?? match?.[1] ?? "";
	const routeRunId = match?.[2] ? decodeURIComponent(match[2]) : null;

	const [runs, setRuns] = useState<RunStateResp[]>([]);
	const [inspectedRun, setInspectedRun] = useState<RunStateResp | null>(null);
	const [inspectedEvents, setInspectedEvents] = useState<RunStreamEvent[]>([]);
	const [selectedEventSeq, setSelectedEventSeq] = useState<number | null>(null);
	const [attempts, setAttempts] = useState<AttemptSnapshotSpec[]>([]);
	const [selectedAttemptIdx, setSelectedAttemptIdx] = useState(0);
	const [receipts, setReceipts] = useState<ExternalMcpReceipt[]>([]);
	const [activeTab, setActiveTab] = useState<TraceTab>("payload");
	const [followTail, setFollowTail] = useState(true);
	const sseAcRef = useRef<AbortController | null>(null);
	const lastSeqRef = useRef(0);

	const syncStoreIfMatching = useCallback((run: RunStateResp) => {
		const st = useProjectStore.getState();
		if (run.step === "step4" && st.activeStep4Run?.run_id === run.run_id) {
			st.setActiveStep4Run(run);
		} else if (
			run.step === "step6" &&
			st.activeStep6Run?.run_id === run.run_id
		) {
			st.setActiveStep6Run(run);
		}
	}, []);

	const loadRunDetails = useCallback(
		async (run: RunStateResp, sig: AbortSignal) => {
			setInspectedRun(run);
			syncStoreIfMatching(run);
			const logs = await fetchAllRunLogs(pid, run.run_id, 0, sig);
			if (sig.aborted) return;
			lastSeqRef.current = logs.at(-1)?.seq ?? 0;
			setInspectedEvents(logs);
			setSelectedEventSeq(logs.at(-1)?.seq ?? null);
			if (run.step === "step6") {
				const att = await api.listVaultAttempts(pid, run.run_id, sig);
				if (!sig.aborted) {
					setAttempts(att);
					setSelectedAttemptIdx(Math.max(0, att.length - 1));
				}
			} else {
				setAttempts([]);
			}
			const rcpts = await api.listReceipts(pid, sig).catch(() => []);
			if (!sig.aborted) setReceipts(rcpts);
		},
		[pid, syncStoreIfMatching],
	);

	useEffect(() => {
		if (!pid) return;
		const ac = new AbortController();
		const load = async () => {
			const list = await api.listRuns(pid, undefined, ac.signal);
			if (ac.signal.aborted) return;
			setRuns(list);
			let target =
				(routeRunId ? list.find((r) => r.run_id === routeRunId) : null) ??
				list[0] ??
				null;
			if (!target && routeRunId) {
				target = await api.getRun(pid, routeRunId, ac.signal);
			}
			if (target && !ac.signal.aborted) {
				await loadRunDetails(target, ac.signal);
			} else if (!ac.signal.aborted) {
				lastSeqRef.current = 0;
				setInspectedRun(null);
				setInspectedEvents([]);
				setAttempts([]);
			}
		};
		void load().catch((err) => {
			if (!ac.signal.aborted) setLastProblem(parseProblemDetails(err));
		});
		return () => ac.abort();
	}, [pid, routeRunId, loadRunDetails, setLastProblem]);

	const inspectedRunId = inspectedRun?.run_id ?? null;
	const inspectedRunStatus = inspectedRun?.status ?? null;
	const inspectedRunStep = inspectedRun?.step ?? null;

	useEffect(() => {
		sseAcRef.current?.abort();
		sseAcRef.current = null;
		if (
			!pid ||
			!inspectedRunId ||
			!inspectedRunStep ||
			!followTail ||
			!isRunLiveStatus(inspectedRunStatus)
		)
			return;
		const ac = new AbortController();
		sseAcRef.current = ac;
		void streamRunEvents({
			projectId: pid,
			runId: inspectedRunId,
			step: inspectedRunStep,
			getLastSeq: () => lastSeqRef.current,
			signal: ac.signal,
			onEvents: (batch) => {
				setInspectedEvents((prev) => {
					const map = new Map(prev.map((e) => [e.seq, e]));
					for (const ev of batch) map.set(ev.seq, ev);
					const next = [...map.values()].sort((a, b) => a.seq - b.seq);
					lastSeqRef.current = next.at(-1)?.seq ?? lastSeqRef.current;
					return next;
				});
				if (
					inspectedRunStep === "step6" &&
					batch.some(
						(e) =>
							e.event_type === "ATTEMPT_SNAPSHOT" ||
							e.event_type === "RUN_COMPLETED",
					)
				) {
					void api
						.listVaultAttempts(pid, inspectedRunId, ac.signal)
						.then((att) => {
							if (!ac.signal.aborted) setAttempts(att);
						})
						.catch(() => undefined);
				}
			},
			onReconciledState: (st) => {
				setInspectedRun(st);
				syncStoreIfMatching(st);
				if (isRunHaltStatus(st.status)) ac.abort();
			},
		});
		return () => ac.abort();
	}, [
		pid,
		inspectedRunId,
		inspectedRunStatus,
		inspectedRunStep,
		followTail,
		syncStoreIfMatching,
	]);

	const selectedEvent =
		inspectedEvents.find((e) => e.seq === selectedEventSeq) ??
		inspectedEvents.at(-1) ??
		null;
	const activeAttempt = attempts[selectedAttemptIdx] ?? attempts.at(-1) ?? null;

	const maskedPiiCount = useMemo(
		() =>
			inspectedEvents.reduce((acc, ev) => {
				const txt = JSON.stringify(ev.payload);
				const hits = txt.match(/<MASKED_PII(?:_#\d+)?>/g);
				return acc + (hits?.length ?? 0);
			}, 0),
		[inspectedEvents],
	);

	const downloadJsonl = () => {
		if (!inspectedRun) return;
		const text = inspectedEvents.map((e) => JSON.stringify(e)).join("\n");
		const blob = new Blob([text], { type: "application/x-ndjson" });
		const url = URL.createObjectURL(blob);
		const a = document.createElement("a");
		a.href = url;
		a.download = `${inspectedRun.run_id}.jsonl`;
		a.click();
		URL.revokeObjectURL(url);
	};

	const onTabKeyDown = (e: React.KeyboardEvent, idx: number) => {
		const next =
			e.key === "ArrowRight"
				? (idx + 1) % TRACE_TABS.length
				: e.key === "ArrowLeft"
					? (idx - 1 + TRACE_TABS.length) % TRACE_TABS.length
					: e.key === "Home"
						? 0
						: e.key === "End"
							? TRACE_TABS.length - 1
							: -1;
		if (next >= 0) {
			e.preventDefault();
			const target = TRACE_TABS[next];
			if (target) setActiveTab(target[0]);
		}
	};

	const hideV7 =
		inspectedRun?.policy === "fast" || inspectedRun?.policy === "symbolic_only";

	return (
		<div className="stack-md">
			<div className="row-between">
				<div>
					<h1 className="h1-serif">
						Run Explorer (Last-Event-ID Ledger &amp; Attempts)
					</h1>
					<p className="wb-sub">
						Inspect historical or live Step 4 Discovery and Step 6 Synthesis
						streams without mutating active workflow drafts.
					</p>
				</div>
				<div className="row">
					<label className="row text-sm">
						<span>Run:</span>
						<select
							className="select"
							style={{ width: "auto" }}
							value={inspectedRun?.run_id ?? ""}
							onChange={(e) => {
								if (e.target.value && pid) {
									navigate(
										`/projects/${encodeURIComponent(pid)}/runs/${encodeURIComponent(e.target.value)}`,
									);
								}
							}}
						>
							{runs.length === 0 ? (
								<option value="">No runs yet</option>
							) : (
								runs.map((r) => (
									<option key={r.run_id} value={r.run_id}>
										{r.run_id} · {r.step.toUpperCase()} · {r.status}
									</option>
								))
							)}
						</select>
					</label>
					<label className="radio-row text-sm">
						<input
							type="checkbox"
							checked={followTail}
							onChange={(e) => setFollowTail(e.target.checked)}
						/>
						<span>Follow Live Tail</span>
					</label>
					<button
						type="button"
						className="btn btn-sm"
						disabled={!inspectedRun}
						onClick={downloadJsonl}
					>
						Download .jsonl
					</button>
					{inspectedRun && (
						<button
							type="button"
							className="btn btn-secondary btn-sm"
							onClick={() =>
								navigate(
									`/projects/${encodeURIComponent(pid)}/step/${
										inspectedRun.step === "step4" ? "4" : "6"
									}`,
								)
							}
						>
							Open {inspectedRun.step.toUpperCase()} →
						</button>
					)}
				</div>
			</div>

			{!inspectedRun ? (
				<div className="card-subtle text-sm text-muted">
					Select or start a Step 4 or Step 6 run to inspect its ledger.
				</div>
			) : (
				<div
					className="split-grid"
					style={{ gridTemplateColumns: "0.85fr 1.15fr" }}
				>
					<section className="card-subtle stack-sm">
						<div className="row-between">
							<h2 className="h2-serif">
								Event Timeline ({inspectedEvents.length})
							</h2>
							<span className="pill mono">
								Last-Event-ID: {inspectedEvents.at(-1)?.seq ?? 0}
							</span>
						</div>
						<div
							className="stack-sm"
							style={{ maxHeight: 440, overflowY: "auto" }}
						>
							{inspectedEvents.map((ev) => (
								<button
									key={ev.seq}
									type="button"
									className={
										selectedEvent?.seq === ev.seq
											? "card-selected stack-sm"
											: "card-cream stack-sm"
									}
									style={{ textAlign: "left" }}
									onClick={() => setSelectedEventSeq(ev.seq)}
								>
									<div className="row-between">
										<span className="mono text-sm font-semibold">
											seq {ev.seq} · {ev.event_type}
										</span>
										<span className="text-sm text-muted">{ev.ts}</span>
									</div>
								</button>
							))}
						</div>
					</section>

					<section className="card-dark stack-md">
						<div
							className="tab-strip"
							role="tablist"
							aria-label="Deep Trace Tabs"
						>
							{TRACE_TABS.map(([id, label], idx) => (
								<button
									key={id}
									type="button"
									role="tab"
									id={`run-tab-${id}`}
									aria-selected={activeTab === id}
									aria-controls={`run-panel-${id}`}
									tabIndex={activeTab === id ? 0 : -1}
									className="tab-btn"
									style={{
										color: activeTab === id ? "#faf9f5" : "#a09d96",
										background: activeTab === id ? "#252320" : "transparent",
									}}
									onClick={() => setActiveTab(id)}
									onKeyDown={(e) => onTabKeyDown(e, idx)}
								>
									{label}
								</button>
							))}
						</div>

						<div
							role="tabpanel"
							id={`run-panel-${activeTab}`}
							aria-labelledby={`run-tab-${activeTab}`}
							className="stack-sm"
						>
							{activeTab === "payload" && (
								<pre
									className="card-dark-inner mono text-sm"
									style={{ margin: 0, overflowX: "auto", maxHeight: 380 }}
								>
									{selectedEvent
										? JSON.stringify(selectedEvent, null, 2)
										: "No event selected."}
								</pre>
							)}

							{activeTab === "attempts" && (
								<div className="stack-sm">
									{inspectedRun.step !== "step6" ? (
										<p className="text-sm" style={{ color: "#a09d96" }}>
											Attempt snapshots apply to Step 6 CEGAR runs only.
										</p>
									) : attempts.length === 0 ? (
										<p className="text-sm" style={{ color: "#a09d96" }}>
											No attempt snapshots recorded yet.
										</p>
									) : (
										<>
											<div className="row">
												{attempts.map((a, i) => (
													<button
														key={`${a.attempt}-${i}`}
														type="button"
														className={`btn btn-sm ${i === selectedAttemptIdx ? "btn-coral-fill" : "btn-secondary"}`}
														onClick={() => setSelectedAttemptIdx(i)}
													>
														Attempt {a.attempt} · E={a.energy_e ?? 0} ·{" "}
														{hideV7 ? 'V7="N/A"' : `V7=${a.v7_score ?? "—"}`}
													</button>
												))}
											</div>
											{activeAttempt && (
												<pre
													className="card-dark-inner mono text-sm"
													style={{
														margin: 0,
														overflow: "auto",
														maxHeight: 320,
													}}
												>
													{JSON.stringify(activeAttempt, null, 2)}
												</pre>
											)}
										</>
									)}
								</div>
							)}

							{activeTab === "receipts" && (
								<div className="stack-sm">
									<div className="text-sm">
										External MCP &amp; OCR Receipts ({receipts.length})
									</div>
									{receipts.map((r) => (
										<div key={r.receipt_id} className="card-dark-inner text-sm">
											<strong className="mono">{r.receipt_id}</strong> ·{" "}
											{r.server_id}/{r.tool_name} · sha256=
											{r.content_sha256.slice(0, 12)}
										</div>
									))}
								</div>
							)}

							{activeTab === "redaction" && (
								<div className="card-dark-inner stack-sm text-sm">
									<div>
										<strong>&lt;MASKED_PII&gt; Tokens Observed:</strong>{" "}
										{maskedPiiCount}
									</div>
									<div>
										<strong>Budget Turns Used:</strong>{" "}
										{inspectedRun.budget_turns_used} /{" "}
										{inspectedRun.effective_max_turns ?? "default"}
									</div>
									<div>
										<strong>Budget Tokens Used:</strong>{" "}
										{inspectedRun.budget_tokens_used} /{" "}
										{inspectedRun.effective_max_tokens ?? "default"}
									</div>
									<div>
										<strong>Elapsed Active Seconds:</strong>{" "}
										{inspectedRun.elapsed_seconds}s
									</div>
								</div>
							)}
						</div>
					</section>
				</div>
			)}
		</div>
	);
}
