import { useEffect, useState } from "react";
import { api, fetchAllRunLogs, parseProblemDetails } from "../api/client";
import type {
	AttemptSnapshotSpec,
	CegarPolicy,
	RunStateResp,
	RunStreamEvent,
} from "../api/openapi";
import { useProjectStore } from "../store/projectStore";

const STAGES = [
	"S6-01 Preflight",
	"S6-02 @ship-vault",
	"S6-03 L1 & Triage",
	"S6-04 V1–V7",
	"S6-05 L3 Hold",
	"S6-06 V8 Horizon",
	"S6-07 Seal",
] as const;

const OBS_TABS = [
	["cegar", "1. CEGAR & Prover (S6-01..05)"],
	["horizon", "2. V8 Horizon & Seal (S6-06..07)"],
] as const;

type ObsTab = (typeof OBS_TABS)[number][0];

export interface ArchitectObservatoryProps {
	projectId: string;
	run: RunStateResp | null;
	policy: CegarPolicy;
	busy?: boolean;
	onVerifyVault: () => void;
}

export function ArchitectObservatory({
	projectId,
	run,
	policy,
	busy = false,
	onVerifyVault,
}: ArchitectObservatoryProps) {
	const activeProject = useProjectStore((s) => s.activeProject);
	const vaultIr = useProjectStore((s) => s.vaultIr);
	const ontologyBundle = useProjectStore((s) => s.ontologyBundle);
	const sseEvents = useProjectStore((s) => s.sseEvents);
	const setVaultIr = useProjectStore((s) => s.setVaultIr);
	const setLastProblem = useProjectStore((s) => s.setLastProblem);

	const [tab, setTab] = useState<ObsTab>("cegar");
	const [attempts, setAttempts] = useState<AttemptSnapshotSpec[]>([]);
	const [logs, setLogs] = useState<RunStreamEvent[]>([]);
	const [selectedAttemptIdx, setSelectedAttemptIdx] = useState(0);

	const runId = run?.run_id;
	const runAttempt = run?.attempt;
	const runStatus = run?.status;

	useEffect(() => {
		if (!projectId) return;
		const ac = new AbortController();
		if (activeProject?.active_wave_id && !vaultIr) {
			void api
				.getVaultIr(projectId, ac.signal)
				.then((ir) => {
					if (!ac.signal.aborted) setVaultIr(ir);
				})
				.catch(() => undefined);
		}
		if (!runId || !runStatus || runAttempt === undefined) {
			setAttempts([]);
			setLogs([]);
			return () => ac.abort();
		}
		void Promise.all([
			api.listVaultAttempts(projectId, runId, ac.signal),
			fetchAllRunLogs(projectId, runId, 0, ac.signal),
		])
			.then(([att, hist]) => {
				if (ac.signal.aborted) return;
				setAttempts(att);
				setSelectedAttemptIdx(Math.max(0, att.length - 1));
				setLogs(hist);
			})
			.catch((err) => {
				if (!ac.signal.aborted && parseProblemDetails(err).status !== 404) {
					setLastProblem(parseProblemDetails(err));
				}
			});
		return () => ac.abort();
	}, [
		projectId,
		runId,
		runAttempt,
		runStatus,
		activeProject?.active_wave_id,
		vaultIr,
		setVaultIr,
		setLastProblem,
	]);

	const liveEvents = useMemoEvents(logs, sseEvents, run?.run_id ?? null);
	const latestAttempt = attempts[attempts.length - 1] ?? null;
	const activeAttempt = attempts[selectedAttemptIdx] ?? latestAttempt;
	const isLive =
		run?.status === "QUEUED" ||
		run?.status === "RUNNING" ||
		run?.status === "HOLD_REQUESTED";
	const canVerify = Boolean(activeProject?.active_wave_id) && !isLive && !busy;
	const hideV7 = policy === "fast" || policy === "symbolic_only";
	const v8 = latestAttempt?.horizon_verdict ?? null;
	const locks = ontologyBundle?.evolution_locks ?? null;

	const onTabKeyDown = (e: React.KeyboardEvent, idx: number) => {
		const next =
			e.key === "ArrowRight"
				? (idx + 1) % OBS_TABS.length
				: e.key === "ArrowLeft"
					? (idx - 1 + OBS_TABS.length) % OBS_TABS.length
					: e.key === "Home"
						? 0
						: e.key === "End"
							? OBS_TABS.length - 1
							: -1;
		if (next >= 0) {
			e.preventDefault();
			const target = OBS_TABS[next];
			if (target) setTab(target[0]);
		}
	};

	return (
		<div className="stack-md">
			<div className="row-between">
				<div className="row">
					{STAGES.map((st) => (
						<span key={st} className="pill mono text-sm">
							{st}
						</span>
					))}
				</div>
				<button
					type="button"
					className="btn btn-primary btn-sm"
					disabled={!canVerify}
					onClick={onVerifyVault}
				>
					✓ Verify Vault
				</button>
			</div>

			<div
				className="tab-strip"
				role="tablist"
				aria-label="Observatory Sections"
			>
				{OBS_TABS.map(([id, label], idx) => (
					<button
						key={id}
						type="button"
						role="tab"
						id={`obs-tab-${id}`}
						aria-selected={tab === id}
						aria-controls={`obs-panel-${id}`}
						tabIndex={tab === id ? 0 : -1}
						className="tab-btn"
						onClick={() => setTab(id)}
						onKeyDown={(e) => onTabKeyDown(e, idx)}
					>
						{label}
					</button>
				))}
			</div>

			<div
				role="tabpanel"
				id={`obs-panel-${tab}`}
				aria-labelledby={`obs-tab-${tab}`}
				className="stack-md"
			>
				{tab === "cegar" && (
					<div className="stack-md">
						<section className="card-subtle stack-sm">
							<h2 className="h2-serif">
								CEGAR Energy Trajectory E(IR_k) ({attempts.length} attempts)
							</h2>
							{attempts.length === 0 ? (
								<p className="text-muted text-sm">
									No CEGAR attempt snapshots recorded yet.
								</p>
							) : (
								<div className="row">
									{attempts.map((a, idx) => {
										const eLabel =
											policy === "symbolic_only"
												? "Checks Only"
												: `E=${a.energy_e ?? 0}`;
										const v7Label = hideV7
											? 'V7="N/A"'
											: `V7=${a.v7_score ?? "—"}`;
										return (
											<button
												key={`${a.attempt}-${idx}`}
												type="button"
												className={
													idx === selectedAttemptIdx
														? "card-selected btn-sm"
														: "card-cream btn-sm"
												}
												onClick={() => setSelectedAttemptIdx(idx)}
											>
												<span className="mono">
													Attempt {a.attempt} · {eLabel} · {v7Label}
												</span>
											</button>
										);
									})}
								</div>
							)}
						</section>

						{activeAttempt && (
							<section className="split-grid">
								<div className="card-subtle stack-sm">
									<h3 className="font-semibold">
										V1–V6 Prover &amp; DV-DYN Diagnostics (Attempt{" "}
										{activeAttempt.attempt})
									</h3>
									{(activeAttempt.diagnostics ?? []).length === 0 ? (
										<div className="banner banner-ok">
											15/15 DV-DYN &amp; V1–V6 checks passed cleanly.
										</div>
									) : (
										<div className="stack-sm">
											{(activeAttempt.diagnostics ?? []).map((d, idx) => (
												<div
													key={`${d.rule_id}-${idx}`}
													className="card-cream stack-sm"
												>
													<div className="row-between">
														<span className="mono font-semibold">
															{d.gate} · {d.rule_id}
														</span>
														<span
															className={`pill ${d.severity === "BLOCKER" || d.severity === "ERROR" ? "pill-err" : "pill-warn"}`}
														>
															{d.severity}
														</span>
													</div>
													<div className="text-sm">{d.message}</div>
													{(d.counterexample_rows ?? []).length > 0 && (
														<pre
															className="card-dark mono text-sm"
															style={{ margin: 0, overflowX: "auto" }}
														>
															{JSON.stringify(d.counterexample_rows, null, 2)}
														</pre>
													)}
												</div>
											))}
										</div>
									)}
								</div>

								<div className="card-subtle stack-sm">
									<h3 className="font-semibold">
										V6b Audited Waivers &amp; Live Log Tail
									</h3>
									{(vaultIr?.justifications ?? []).length === 0 ? (
										<p className="text-muted text-sm">
											No V6b waivers recorded on this wave.
										</p>
									) : (
										(vaultIr?.justifications ?? []).map((j) => (
											<div key={j.waiver_id} className="card-cream text-sm">
												<strong className="mono">{j.waiver_id}</strong> ·{" "}
												{j.rule_id} on{" "}
												<span className="mono">{j.entity_name}</span>{" "}
												(reviewed_by_v7={String(j.reviewed_by_v7)}) —{" "}
												{j.rationale}
											</div>
										))
									)}
									<div
										className="card-dark mono text-sm"
										style={{ maxHeight: 220, overflowY: "auto" }}
									>
										{liveEvents.slice(-20).map((ev) => (
											<div key={ev.seq}>
												#{ev.seq} [{ev.event_type}] {JSON.stringify(ev.payload)}
											</div>
										))}
									</div>
								</div>
							</section>
						)}
					</div>
				)}

				{tab === "horizon" && (
					<div className="stack-md">
						<section className="card-subtle stack-md">
							<h2 className="h2-serif">S6-06 V8 Horizon Critic Verdict</h2>
							{!v8 ? (
								<p className="text-muted text-sm">
									No Horizon Critic verdict recorded for this run (run Balanced,
									Strict, or Quick Horizon Review).
								</p>
							) : (
								<div className="stack-md">
									<div className="row">
										<span
											className={`pill ${v8.score >= 85 ? "pill-ok" : "pill-warn"}`}
										>
											Resilience: {v8.score}/100 (&gt;= 85)
										</span>
										<span className="pill">{v8.classification}</span>
									</div>
									<div className="grid-3">
										<div className="card-cream stack-sm">
											<strong>
												Absorbed Natively ({v8.absorbed_natively?.length ?? 0})
											</strong>
											{(v8.absorbed_natively ?? []).map((item) => (
												<div key={item} className="text-sm mono">
													{item}
												</div>
											))}
										</div>
										<div className="card-cream stack-sm">
											<strong>
												Bucket A Auto-Repairs ({v8.bucket_a_ops?.length ?? 0})
											</strong>
											{(v8.bucket_a_ops ?? []).map((op) => (
												<div
													key={`${op.operator}:${op.target_hub ?? op.sat_name ?? ""}`}
													className="text-sm mono"
												>
													{op.operator} · {op.target_hub ?? op.sat_name ?? ""}
												</div>
											))}
										</div>
										<div className="card-cream stack-sm">
											<strong>
												Bucket B / C ({v8.bucket_b_questions?.length ?? 0} /{" "}
												{v8.bucket_c_watchlist?.length ?? 0})
											</strong>
											{(v8.bucket_b_questions ?? []).map((q) => (
												<div key={q.question_id} className="text-sm">
													{q.question_id} ({q.brk_code}): {q.title}
												</div>
											))}
											{(v8.bucket_c_watchlist ?? []).map((w) => (
												<div key={w} className="text-sm text-muted">
													{w}
												</div>
											))}
										</div>
									</div>
								</div>
							)}
						</section>

						<section className="card-dark stack-sm">
							<h2 className="h2-serif" style={{ color: "#faf9f5" }}>
								S6-07 Seal &amp; Evolution Locks
							</h2>
							{vaultIr ? (
								<div className="stack-sm text-sm">
									<div className="pill pill-ok">
										✓ Sealed at S6-07 · Wave {vaultIr.wave_number} ·{" "}
										{locks
											? `LOCK-W${String(locks.wave_number).padStart(3, "0")} immutable`
											: "Initial Wave"}{" "}
										· Graph A untouched (FR-DOM-06)
									</div>
									<div className="mono">
										Hubs: {vaultIr.hubs?.length ?? 0} · Links:{" "}
										{vaultIr.links?.length ?? 0} · Satellites:{" "}
										{vaultIr.satellites?.length ?? 0} · Locked BKs:{" "}
										{Object.keys(locks?.locked_hub_bks ?? {}).length}
									</div>
								</div>
							) : (
								<p className="text-sm" style={{ color: "#a09d96" }}>
									Not yet sealed into a certified wave.
								</p>
							)}
						</section>
					</div>
				)}
			</div>
		</div>
	);
}

function useMemoEvents(
	hist: RunStreamEvent[],
	sse: RunStreamEvent[],
	runId: string | null,
): RunStreamEvent[] {
	if (!runId) return [];
	const map = new Map<number, RunStreamEvent>();
	for (const e of hist) if (e.run_id === runId) map.set(e.seq, e);
	for (const e of sse) if (e.run_id === runId) map.set(e.seq, e);
	return [...map.values()].sort((a, b) => a.seq - b.seq);
}
