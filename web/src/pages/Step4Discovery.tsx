import { useEffect, useMemo, useRef, useState } from "react";
import { api, parseProblemDetails } from "../api/client";
import {
	type ClarificationQuestion,
	type DiscoveryMode,
	type ExternalMcpReceipt,
	type ProblemDetails,
	type RunStateResp,
	type StagedCandidateSpec,
	isRunLiveStatus,
} from "../api/openapi";
import { AgentIoDrawer } from "../components/AgentIoDrawer";
import { AskFiboDrawer } from "../components/AskFiboDrawer";
import { CitationInspectorDrawer } from "../components/CitationInspectorDrawer";
import { RemediationList } from "../components/RemediationList";
import {
	RunControlDialog,
	type RunControlMode,
} from "../components/RunControlDialog";
import {
	GroundingMatrix,
	HoldQuestionCard,
} from "../components/Step4HoldPanels";
import { parseAppRoute, useProjectStore } from "../store/projectStore";

type SubTab = "4a" | "4b";
type ModalState =
	| { kind: "abandon" }
	| { kind: "ctrl"; mode: RunControlMode }
	| { kind: "fibo"; query: string }
	| { kind: "cite"; c: StagedCandidateSpec }
	| null;

const MODES: readonly [DiscoveryMode, string][] = [
	["incremental_new", "Incremental New"],
	["incremental_reverify", "Reverify"],
	["full_rebuild", "Full Rebuild"],
];
const isClarQ = (q: unknown): q is ClarificationQuestion =>
	Boolean(
		q && typeof q === "object" && "question_id" in q && "impact_if_locked" in q,
	);

export function Step4Discovery() {
	const {
		currentUrl,
		activeProject,
		documentMaps,
		ontologyBundle,
		activeStep4Run,
		setActiveProject,
		setActiveStep4Run,
		setOntologyBundle,
		setDirtyFlag,
		startRunSse,
		runControlAction,
		navigate,
		announce,
	} = useProjectStore();
	const parsed = useMemo(() => {
		const u = new URL(currentUrl, "http://localhost");
		return parseAppRoute(u.pathname, u.search);
	}, [currentUrl]);
	const pid = activeProject?.project_id ?? parsed.projectId;
	const sub: SubTab = parsed.query.get("sub") === "4b" ? "4b" : "4a";
	const urlRunId = parsed.query.get("run_id");

	const tab4aRef = useRef<HTMLButtonElement>(null);
	const tab4bRef = useRef<HTMLButtonElement>(null);
	const abandonDlg = useRef<HTMLDialogElement>(null);
	const [mode, setMode] = useState<DiscoveryMode>(
		activeProject?.settings_snapshot?.discovery_mode ?? "incremental_new",
	);
	const [hint, setHint] = useState("");
	const [receipts, setReceipts] = useState<ExternalMcpReceipt[]>([]);
	const [busy, setBusy] = useState(false);
	const [problem, setProblem] = useState<ProblemDetails | null>(null);
	const [modal, setModal] = useState<ModalState>(null);

	useEffect(() => {
		if (!pid) return;
		let ok = true;
		setProblem(null);
		void Promise.all([
			api.listRuns(pid, "step4"),
			api.getOntologyBundle(pid).catch(() => null),
			api.listReceipts(pid).catch(() => [] as ExternalMcpReceipt[]),
			api.getProject(pid).catch(() => null),
		])
			.then(async ([runs, ob, rc, proj]) => {
				if (!ok) return;
				if (proj?.project_id) setActiveProject(proj);
				if (ob) setOntologyBundle(ob);
				setReceipts(rc);
				const chosen: RunStateResp | null = urlRunId
					? await api.getRun(pid, urlRunId)
					: (runs.find(
							(r) =>
								isRunLiveStatus(r.status) || r.status === "HOLD_CHECKPOINTED",
						) ??
						runs[0] ??
						null);
				if (ok && chosen) setActiveStep4Run(chosen);
			})
			.catch((err) => ok && setProblem(parseProblemDetails(err)));
		return () => {
			ok = false;
		};
	}, [pid, urlRunId, setActiveProject, setActiveStep4Run, setOntologyBundle]);

	const snapProjectId = activeProject?.project_id;
	const snapMode = activeProject?.settings_snapshot?.discovery_mode;
	useEffect(() => {
		if (snapProjectId && snapMode) setMode(snapMode);
	}, [snapProjectId, snapMode]);

	const runStatus = activeStep4Run?.status;
	useEffect(() => {
		if (!pid || runStatus !== "COMPLETED") return;
		void Promise.all([
			api.getOntologyBundle(pid).catch(() => null),
			api.listReceipts(pid).catch(() => [] as ExternalMcpReceipt[]),
		]).then(([ob, rc]) => {
			if (ob) setOntologyBundle(ob);
			setReceipts(rc);
		});
	}, [pid, runStatus, setOntologyBundle]);

	const abandonOpen = modal?.kind === "abandon";
	useEffect(() => {
		const el = abandonDlg.current;
		if (!el) return;
		if (abandonOpen && !el.open)
			el.showModal ? el.showModal() : el.setAttribute("open", "");
		else if (!abandonOpen && el.open) {
			el.returnValue = "";
			el.close ? el.close() : el.removeAttribute("open");
		}
		const onClick = (e: MouseEvent) => {
			if (e.target === el) setModal(null);
		};
		el.addEventListener("click", onClick);
		return () => el.removeEventListener("click", onClick);
	}, [abandonOpen]);

	const questions = useMemo(
		() => (activeStep4Run?.pending_questions ?? []).filter(isClarQ),
		[activeStep4Run],
	);

	if (!pid) {
		return (
			<section className="panel stack-md">
				<h1 className="h1-serif">Step 4 · Agent Discovery</h1>
				<p className="text-muted">Select or create a project in Step 1.</p>
				<button
					type="button"
					className="btn btn-primary"
					onClick={() => navigate("/")}
				>
					Go to Step 1
				</button>
			</section>
		);
	}

	const switchSub = (next: SubTab) => {
		const keep = urlRunId ? `&run_id=${encodeURIComponent(urlRunId)}` : "";
		navigate(`/projects/${encodeURIComponent(pid)}/step/4?sub=${next}${keep}`);
	};
	const onTabKey = (e: React.KeyboardEvent<HTMLButtonElement>) => {
		if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
		e.preventDefault();
		const next: SubTab =
			e.key === "Home"
				? "4a"
				: e.key === "End"
					? "4b"
					: sub === "4a"
						? "4b"
						: "4a";
		switchSub(next);
		(next === "4a" ? tab4aRef : tab4bRef).current?.focus();
	};

	const draining = runStatus === "HOLD_REQUESTED";
	const live = isRunLiveStatus(runStatus);
	const held = runStatus === "HOLD_CHECKPOINTED";
	const uncert = runStatus === "UNCERTIFIED_DRAFT";
	const canCtrl = runStatus === "QUEUED" || runStatus === "RUNNING";
	const canRaise =
		Boolean(activeStep4Run) && !draining && (canCtrl || held || uncert);

	const wrap = async (fn: () => Promise<void>) => {
		setBusy(true);
		setProblem(null);
		try {
			await fn();
		} catch (x) {
			setProblem(parseProblemDetails(x));
		} finally {
			setBusy(false);
		}
	};
	const finishRun = (r: RunStateResp, msg: string, seq: number) => {
		setDirtyFlag("step6Decisions", false);
		setActiveStep4Run(r);
		announce("polite", `${msg} ${r.run_id}.`);
		if (isRunLiveStatus(r.status)) {
			void startRunSse(pid, r.run_id, "step4", {
				backfillLogs: true,
				initialLastSeq: seq,
			});
		}
	};
	const launch = () =>
		wrap(async () => {
			setModal(null);
			const r = await api.startOntologyConstruct(pid, {
				discovery_mode: mode,
				hint: hint.trim() || null,
			});
			finishRun(r, "Started Step 4 discovery run", 0);
		});
	const submitAnswers = (answers: Record<string, string>) => {
		if (!activeStep4Run || draining) return Promise.resolve();
		const runId = activeStep4Run.run_id;
		return wrap(async () => {
			const r = await api.answerOntologyHold(pid, runId, { answers });
			finishRun(r, "Resumed Step 4 run", Math.max(0, r.last_seq));
		});
	};
	const bindSec = (e: React.FormEvent<HTMLFormElement>) => {
		e.preventDefault();
		const fd = new FormData(e.currentTarget);
		const key = String(fd.get("key") ?? "").trim();
		const value = String(fd.get("val") ?? "");
		const form = e.currentTarget;
		if (!key || !value.trim()) return;
		void wrap(async () => {
			await api.postSecret({ key, value });
			form.reset();
		});
	};

	const candidates = ontologyBundle?.staged_candidates ?? [];

	return (
		<section className="stack-md">
			<div className="row-between">
				<div>
					<h1 className="h1-serif">Step 4 · Agent Discovery</h1>
					<p className="wb-sub">
						Brokered evidence scouts, Clarification Holds, and 4-path grounding.
					</p>
				</div>
				<div className="row">
					<span className="pill">{receipts.length} MCP Receipts</span>
					<button
						type="button"
						className="btn"
						onClick={() => setModal({ kind: "fibo", query: "" })}
					>
						Ask FIBO
					</button>
					<button
						type="button"
						className="btn btn-primary"
						onClick={() =>
							navigate(`/projects/${encodeURIComponent(pid)}/step/5`)
						}
					>
						Proceed to Step 5 Explorer →
					</button>
				</div>
			</div>

			<div className="row-between">
				<div
					className="tab-strip"
					role="tablist"
					aria-label="Step 4 Discovery Sections"
				>
					<button
						ref={tab4aRef}
						type="button"
						role="tab"
						id="step4-tab-4a"
						className="tab-btn"
						aria-selected={sub === "4a"}
						aria-controls="step4-tabpanel"
						tabIndex={sub === "4a" ? 0 : -1}
						onClick={() => switchSub("4a")}
						onKeyDown={onTabKey}
					>
						4a Hold
					</button>
					<button
						ref={tab4bRef}
						type="button"
						role="tab"
						id="step4-tab-4b"
						className="tab-btn"
						aria-selected={sub === "4b"}
						aria-controls="step4-tabpanel"
						tabIndex={sub === "4b" ? 0 : -1}
						onClick={() => switchSub("4b")}
						onKeyDown={onTabKey}
					>
						4b Grounding ({candidates.length})
					</button>
				</div>

				<div className="row">
					<button
						type="button"
						className="btn btn-primary"
						disabled={busy || live || draining}
						onClick={() =>
							held ? setModal({ kind: "abandon" }) : void launch()
						}
					>
						{held ? "Re-run (Abandons Hold)" : "Start Discovery"}
					</button>
					{activeStep4Run && (
						<>
							<button
								type="button"
								className="btn"
								disabled={busy || !canCtrl || draining}
								onClick={() =>
									void wrap(async () => {
										await runControlAction("step4", activeStep4Run.run_id, {
											action: "pause",
										});
									})
								}
							>
								Pause
							</button>
							<button
								type="button"
								className="btn"
								disabled={busy || !canCtrl || draining}
								onClick={() => setModal({ kind: "ctrl", mode: "hint" })}
							>
								Send Hint
							</button>
							<button
								type="button"
								className="btn"
								disabled={busy || !canRaise || draining}
								onClick={() => setModal({ kind: "ctrl", mode: "raise-budget" })}
							>
								Raise Budget
							</button>
						</>
					)}
				</div>
			</div>

			{draining && (
				<output className="banner banner-warn">
					Draining active work... Launch, control, and resume actions locked.
				</output>
			)}
			{uncert && activeStep4Run && (
				<div className="banner banner-warn row-between">
					<span>Discovery paused (UNCERTIFIED_DRAFT).</span>
					<div className="row">
						<button
							type="button"
							className="btn btn-sm btn-primary"
							disabled={draining}
							onClick={() => setModal({ kind: "ctrl", mode: "raise-budget" })}
						>
							Raise Budget
						</button>
						<button
							type="button"
							className="btn btn-sm"
							disabled={busy || draining}
							onClick={() => void launch()}
						>
							Re-run
						</button>
					</div>
				</div>
			)}

			{problem && (
				<div className="banner banner-danger stack-sm" role="alert">
					<div>
						<strong>{problem.title}</strong> ({problem.error_code}):{" "}
						{problem.detail}
					</div>
					<RemediationList remediation={problem.remediation} />
					{problem.error_code === "NEEDS_LOCAL_SECRET_BINDING" && (
						<form className="row" onSubmit={bindSec}>
							<input
								name="key"
								className="input mono"
								style={{ maxWidth: 160 }}
								placeholder="Secret key"
							/>
							<input
								name="val"
								type="password"
								className="input mono"
								style={{ maxWidth: 160 }}
								placeholder="Token"
							/>
							<button type="submit" className="btn btn-sm btn-primary">
								Bind Secret
							</button>
							<button
								type="button"
								className="btn btn-sm"
								onClick={() => navigate("/settings?tab=secrets")}
							>
								Settings Secrets
							</button>
						</form>
					)}
				</div>
			)}

			<div
				id="step4-tabpanel"
				role="tabpanel"
				aria-labelledby={sub === "4a" ? "step4-tab-4a" : "step4-tab-4b"}
			>
				{sub === "4b" ? (
					<GroundingMatrix
						candidates={candidates}
						onInspect={(c) => setModal({ kind: "cite", c })}
					/>
				) : (
					<div
						style={{
							display: "flex",
							flexWrap: "wrap",
							gap: 14,
							alignItems: "flex-start",
						}}
					>
						<div
							className="stack-md"
							style={{ flex: "56 1 340px", minWidth: 0 }}
						>
							<section className="panel stack-sm">
								<fieldset className="radio-group" disabled={live || draining}>
									<legend className="text-sm font-semibold">
										Discovery Mode
									</legend>
									<div className="row">
										{MODES.map(([val, lbl]) => (
											<label key={val} className="radio-row">
												<input
													type="radio"
													name="step4_mode"
													checked={mode === val}
													onChange={() => setMode(val)}
												/>
												<span>{lbl}</span>
											</label>
										))}
									</div>
								</fieldset>
								<label className="field-col">
									<span className="field-label">
										Initial Steward Hint (optional)
									</span>
									<input
										className="input"
										value={hint}
										disabled={live || draining}
										onChange={(e) => setHint(e.target.value)}
									/>
								</label>
								{activeStep4Run && (
									<div className="row-between text-sm">
										<span className="mono">
											{activeStep4Run.run_id} · {activeStep4Run.phase}
										</span>
										<span className="pill">{activeStep4Run.status}</span>
									</div>
								)}
							</section>
							{held && questions.length === 0 && (
								<section className="card-cream stack-sm">
									<h2 className="h2-serif">Paused at Checkpoint</h2>
									<p className="text-sm text-muted">
										Discovery paused with zero open clarification questions.
									</p>
									<div className="row-end">
										<button
											type="button"
											className="btn btn-primary"
											disabled={busy || draining}
											onClick={() => void submitAnswers({})}
										>
											▶ Resume Discovery →
										</button>
									</div>
								</section>
							)}
							{held && questions.length > 0 && (
								<HoldQuestionCard
									key={questions.map((q) => q.question_id).join("|")}
									questions={questions}
									busy={busy}
									draining={draining}
									onSubmit={submitAnswers}
								/>
							)}
						</div>
						<div style={{ flex: "44 1 300px", minWidth: 0 }}>
							<AgentIoDrawer projectId={pid} run={activeStep4Run} />
						</div>
					</div>
				)}
			</div>

			<dialog
				ref={abandonDlg}
				className="dialog-panel"
				aria-labelledby="step4-abandon-title"
				{...({ closedby: "closerequest" } as Record<string, string>)}
				onCancel={(e) => {
					e.preventDefault();
					setModal(null);
				}}
			>
				<div className="stack-md">
					<h2 id="step4-abandon-title" className="dialog-title">
						Abandon Checkpointed Hold &amp; Re-run?
					</h2>
					<p className="text-muted">
						Starting a new run abandons hold on{" "}
						<strong className="mono">{activeStep4Run?.run_id}</strong>.
					</p>
					<div className="row-end">
						<button
							type="button"
							className="btn"
							onClick={() => setModal(null)}
						>
							Cancel
						</button>
						<button
							type="button"
							className="btn btn-danger"
							onClick={() => void launch()}
						>
							Confirm Re-run
						</button>
					</div>
				</div>
			</dialog>

			{activeStep4Run && (
				<RunControlDialog
					open={modal?.kind === "ctrl"}
					onClose={() => setModal(null)}
					run={activeStep4Run}
					initialMode={modal?.kind === "ctrl" ? modal.mode : "hint"}
				/>
			)}
			<CitationInspectorDrawer
				open={modal?.kind === "cite"}
				onClose={() => setModal(null)}
				projectId={pid}
				domainNamespace={activeProject?.domain_namespace}
				candidate={modal?.kind === "cite" ? modal.c : null}
				documentMaps={documentMaps}
				receipts={receipts}
				onOpenAskFibo={(q) => setModal({ kind: "fibo", query: q })}
			/>
			<AskFiboDrawer
				open={modal?.kind === "fibo"}
				onClose={() => setModal(null)}
				projectId={pid}
				domainNamespace={activeProject?.domain_namespace}
				initialQuery={modal?.kind === "fibo" ? modal.query : ""}
				canStageToDraft={false}
			/>
		</section>
	);
}
