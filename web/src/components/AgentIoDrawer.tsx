import { useEffect, useRef, useState } from "react";
import { fetchAllRunLogs, parseProblemDetails } from "../api/client";
import { type RunStateResp, isRunLiveStatus } from "../api/openapi";
import { mergeSseEvents, useProjectStore } from "../store/projectStore";

export interface AgentIoDrawerProps {
	projectId: string;
	run: RunStateResp | null;
}

export function AgentIoDrawer({ projectId, run }: AgentIoDrawerProps) {
	const sseEvents = useProjectStore((s) => s.sseEvents);
	const lastReceivedSeq = useProjectStore((s) => s.lastReceivedSeq);
	const sseDisconnected = useProjectStore((s) => s.sseDisconnected);
	const sseActiveRunId = useProjectStore((s) => s.sseActiveRunId);
	const startRunSse = useProjectStore((s) => s.startRunSse);
	const setLastProblem = useProjectStore((s) => s.setLastProblem);

	const [collapsed, setCollapsed] = useState(false);
	const [loadingLogs, setLoadingLogs] = useState(false);
	const backfilledRunRef = useRef<string | null>(null);

	const runId = run?.run_id ?? null;
	const runStatus = run?.status ?? null;

	useEffect(() => {
		if (!projectId || !runId || !runStatus) return;
		if (isRunLiveStatus(runStatus)) {
			if (sseActiveRunId !== runId) {
				backfilledRunRef.current = runId;
				void startRunSse(projectId, runId, "step4", {
					backfillLogs: true,
					initialLastSeq: 0,
				});
			}
			return;
		}
		if (backfilledRunRef.current === runId) return;
		const hasExisting = useProjectStore
			.getState()
			.sseEvents.some((ev) => ev.run_id === runId);
		if (hasExisting) {
			backfilledRunRef.current = runId;
			return;
		}
		backfilledRunRef.current = runId;
		const ac = new AbortController();
		setLoadingLogs(true);
		void fetchAllRunLogs(projectId, runId, 0, ac.signal)
			.then((hist) => {
				if (ac.signal.aborted || !hist.length) return;
				useProjectStore.setState((s) => {
					const m = mergeSseEvents(s.sseEvents, hist);
					return {
						sseEvents: m.events,
						lastReceivedSeq: Math.max(s.lastReceivedSeq, m.lastSeq),
					};
				});
			})
			.catch((err) => {
				if (!ac.signal.aborted) setLastProblem(parseProblemDetails(err));
			})
			.finally(() => {
				if (!ac.signal.aborted) setLoadingLogs(false);
			});
		return () => ac.abort();
	}, [
		projectId,
		runId,
		runStatus,
		sseActiveRunId,
		startRunSse,
		setLastProblem,
	]);

	const runEvents = runId ? sseEvents.filter((ev) => ev.run_id === runId) : [];

	return (
		<aside
			className="card-dark stack-sm"
			aria-label="Broker Agent I/O Trace Drawer"
		>
			<div className="row-between">
				<div className="row">
					<strong>Broker Trace</strong>
					<span className="pill pill-dark mono">
						Last-Event-ID: {lastReceivedSeq}
					</span>
				</div>
				<button
					type="button"
					className="btn btn-sm"
					style={{
						backgroundColor: "var(--surface-dark-elevated)",
						color: "var(--on-dark)",
						borderColor: "var(--dark-divider)",
					}}
					aria-expanded={!collapsed}
					onClick={() => setCollapsed((c) => !c)}
				>
					{collapsed ? "▸ Expand Trace" : "▾ Collapse Trace"}
				</button>
			</div>

			{sseDisconnected && run && isRunLiveStatus(run.status) && (
				<div className="banner banner-danger row-between" role="alert">
					<span>Live event stream disconnected after retries.</span>
					<button
						type="button"
						className="btn btn-sm"
						onClick={() =>
							void startRunSse(projectId, run.run_id, "step4", {
								backfillLogs: true,
								initialLastSeq: lastReceivedSeq,
							})
						}
					>
						Reconnect
					</button>
				</div>
			)}

			{!collapsed && (
				<div className="stack-sm" style={{ maxHeight: 460, overflowY: "auto" }}>
					{!run ? (
						<p
							className="text-sm"
							style={{ color: "var(--on-dark-soft)", margin: 0 }}
						>
							Start Step 4 discovery to stream scout dispatches, MCP receipts,
							and checkpoint transitions.
						</p>
					) : loadingLogs && runEvents.length === 0 ? (
						<p
							className="text-sm"
							style={{ color: "var(--on-dark-soft)", margin: 0 }}
						>
							Backfilling historical events...
						</p>
					) : runEvents.length === 0 ? (
						<p
							className="text-sm"
							style={{ color: "var(--on-dark-soft)", margin: 0 }}
						>
							No trace events recorded yet for{" "}
							<span className="mono">{run.run_id}</span>.
						</p>
					) : (
						runEvents.map((ev) => (
							<div
								key={`${ev.run_id}-${ev.seq}`}
								className="card-dark-inner stack-sm"
								style={{ gap: 4 }}
							>
								<div className="row-between text-sm">
									<span
										className="mono"
										style={{ color: "var(--accent-teal)" }}
									>
										#{ev.seq} · {ev.event_type}
									</span>
									<span
										className="mono"
										style={{ color: "var(--on-dark-soft)" }}
									>
										{ev.ts}
									</span>
								</div>
								<pre
									className="mono text-sm"
									style={{
										margin: 0,
										whiteSpace: "pre-wrap",
										wordBreak: "break-word",
										color: "var(--on-dark)",
									}}
								>
									{JSON.stringify(ev.payload)}
								</pre>
							</div>
						))
					)}
				</div>
			)}
		</aside>
	);
}
