import { useCallback, useEffect, useRef, useState } from "react";
import { api, parseProblemDetails } from "../api/client";
import {
	type GuidedDecisionCard as GuidedDecisionCardDTO,
	type GuidedTableExplainerSpec,
	type HorizonActionReq,
	type RunStateResp,
	type WaiverSubmitReq,
	isRunLiveStatus,
} from "../api/openapi";
import { ArchitectObservatory } from "../components/ArchitectObservatory";
import {
	ExplainerSections,
	GuidedBlueprintPanel,
	GuidedDecisionsPanel,
	Step6PolicyToolbar,
} from "../components/GuidedReadinessBrief";
import {
	RunControlDialog,
	type RunControlMode,
} from "../components/RunControlDialog";
import { useProjectStore } from "../store/projectStore";

const GUIDED_SUBS = [
	["blueprint", "1. Blueprint & Explainer"],
	["decisions", "2. Decisions & Readiness"],
] as const;

type GuidedSub = (typeof GUIDED_SUBS)[number][0];

export function Step6Vault() {
	const activeProject = useProjectStore((s) => s.activeProject);
	const currentUrl = useProjectStore((s) => s.currentUrl);
	const activeStep6Run = useProjectStore((s) => s.activeStep6Run);
	const activeRunPolicy = useProjectStore((s) => s.activeRunPolicy);
	const guidedView = useProjectStore((s) => s.guidedView);
	const vaultIr = useProjectStore((s) => s.vaultIr);
	const ontologyBundle = useProjectStore((s) => s.ontologyBundle);
	const navigate = useProjectStore((s) => s.navigate);
	const setActiveProject = useProjectStore((s) => s.setActiveProject);
	const setOntologyBundle = useProjectStore((s) => s.setOntologyBundle);
	const setActiveStep6Run = useProjectStore((s) => s.setActiveStep6Run);
	const setActiveRunPolicy = useProjectStore((s) => s.setActiveRunPolicy);
	const setGuidedView = useProjectStore((s) => s.setGuidedView);
	const setVaultIr = useProjectStore((s) => s.setVaultIr);
	const startRunSse = useProjectStore((s) => s.startRunSse);
	const runControlAction = useProjectStore((s) => s.runControlAction);
	const verifyVaultAction = useProjectStore((s) => s.verifyVaultAction);
	const deriveStep7Gate = useProjectStore((s) => s.deriveStep7Gate);
	const setLastProblem = useProjectStore((s) => s.setLastProblem);

	const parsed = new URL(currentUrl, "http://localhost");
	const pid =
		activeProject?.project_id ??
		/^\/projects\/([^/]+)/.exec(parsed.pathname)?.[1] ??
		"";

	const [viewMode, setViewMode] = useState<"guided" | "observatory">("guided");
	const [guidedSub, setGuidedSub] = useState<GuidedSub>("blueprint");
	const [runsList, setRunsList] = useState<RunStateResp[]>([]);
	const [selectedEntityId, setSelectedEntityId] = useState<string | null>(null);
	const [explainerOpen, setExplainerOpen] = useState(false);
	const [ctrl, setCtrl] = useState<{
		open: boolean;
		mode: RunControlMode;
		hint: string;
	}>({
		open: false,
		mode: "hint",
		hint: "",
	});
	const [busy, setBusy] = useState(false);
	const [isMobile, setIsMobile] = useState(
		() => typeof window !== "undefined" && window.innerWidth < 768,
	);
	const explainerDlgRef = useRef<HTMLDialogElement>(null);

	useEffect(() => {
		if (typeof window === "undefined") return;
		const onResize = () => setIsMobile(window.innerWidth < 768);
		window.addEventListener("resize", onResize);
		return () => window.removeEventListener("resize", onResize);
	}, []);

	useEffect(() => {
		const el = explainerDlgRef.current;
		if (!el) return;
		if (explainerOpen && !el.open) el.showModal();
		else if (!explainerOpen && el.open) el.close();
		const onClick = (e: MouseEvent) =>
			e.target === el && setExplainerOpen(false);
		el.addEventListener("click", onClick);
		return () => el.removeEventListener("click", onClick);
	}, [explainerOpen]);

	const hydrateStep6 = useCallback(
		async (sig?: AbortSignal) => {
			if (!pid) return;
			const [runs, proj, bundle] = await Promise.all([
				api.listRuns(pid, "step6", sig),
				api.getProject(pid, sig).catch(() => null),
				api.getOntologyBundle(pid, sig).catch(() => null),
			]);
			if (sig?.aborted) return;
			if (proj?.project_id) setActiveProject(proj);
			if (bundle?.ontology) setOntologyBundle(bundle);
			setRunsList(runs);
			const chosen =
				runs.find((r) => isRunLiveStatus(r.status)) ??
				runs.find((r) => r.status === "HOLD_CHECKPOINTED") ??
				runs[0] ??
				null;
			setActiveStep6Run(chosen);
			if (chosen?.policy) setActiveRunPolicy(chosen.policy);
			const waveId = proj?.project_id
				? proj.active_wave_id
				: useProjectStore.getState().activeProject?.active_wave_id;
			if (waveId) {
				try {
					const ir = await api.getVaultIr(pid, sig);
					if (!sig?.aborted) setVaultIr(ir);
				} catch {
					/* 404 before first certification */
				}
			}
			if (chosen) {
				const gv = await api.getGuidedView(pid, chosen.run_id, sig);
				if (!sig?.aborted) setGuidedView(gv);
				if (isRunLiveStatus(chosen.status)) {
					void startRunSse(pid, chosen.run_id, "step6", {
						backfillLogs: viewMode === "observatory",
						initialLastSeq: chosen.last_seq,
					});
				}
			}
		},
		[
			pid,
			viewMode,
			setActiveProject,
			setOntologyBundle,
			setActiveStep6Run,
			setActiveRunPolicy,
			setGuidedView,
			setVaultIr,
			startRunSse,
		],
	);

	useEffect(() => {
		if (!pid) return;
		const ac = new AbortController();
		void hydrateStep6(ac.signal).catch(
			(err) => !ac.signal.aborted && setLastProblem(parseProblemDetails(err)),
		);
		return () => ac.abort();
	}, [pid, hydrateStep6, setLastProblem]);

	const run = activeStep6Run;
	const isLive = isRunLiveStatus(run?.status);
	const isDraining = run?.status === "HOLD_REQUESTED";

	const handleRunResp = async (next: RunStateResp) => {
		setActiveStep6Run(next);
		if (isRunLiveStatus(next.status)) {
			void startRunSse(pid, next.run_id, "step6", {
				backfillLogs: viewMode === "observatory",
				initialLastSeq: next.last_seq,
			});
		} else {
			await hydrateStep6();
		}
	};

	const withBusy = async (fn: () => Promise<void>) => {
		setBusy(true);
		try {
			await fn();
		} catch (err) {
			setLastProblem(parseProblemDetails(err));
		} finally {
			setBusy(false);
		}
	};

	const startSynthesis = async () => {
		if (!pid || isLive || isDraining) return;
		if (
			run?.status === "HOLD_CHECKPOINTED" &&
			typeof window !== "undefined" &&
			typeof window.confirm === "function" &&
			!window.confirm(
				"Re-running abandons the current checkpointed Hold. Continue?",
			)
		) {
			return;
		}
		await withBusy(async () =>
			handleRunResp(await api.buildVault(pid, { policy: activeRunPolicy })),
		);
	};

	const handleAskAiRefactor = async (card: GuidedDecisionCardDTO) => {
		if (!pid || !run || isDraining) return;
		const hint = `Refactor design for ${card.title} without requiring a manual waiver.`;
		if (run.status === "QUEUED" || run.status === "RUNNING") {
			await runControlAction("step6", run.run_id, { action: "hint", hint });
		} else if (run.status === "HOLD_CHECKPOINTED") {
			await runControlAction("step6", run.run_id, { action: "hint", hint });
			await handleRunResp(
				await api.submitVaultDecisions(pid, run.run_id, { answers: {} }),
			);
		} else if (run.status === "UNCERTIFIED_DRAFT") {
			setCtrl({ open: true, mode: "raise-budget", hint });
		} else {
			await startSynthesis();
		}
	};

	const entities = guidedView?.entities ?? [];
	const activeEntity =
		entities.find((e) => e.entity_id === selectedEntityId) ??
		entities[0] ??
		null;
	const activeExplainer: GuidedTableExplainerSpec | null = activeEntity
		? (guidedView?.explainers?.[activeEntity.entity_id] ?? null)
		: null;

	const isStaleOntology = Boolean(
		activeProject?.active_ontology_rev_id &&
			guidedView?.ontology_rev_id &&
			guidedView.ontology_rev_id !== activeProject.active_ontology_rev_id,
	);
	const showHorizonReview =
		run?.policy === "fast" &&
		run.status === "COMPLETED" &&
		guidedView?.readiness_brief.certification_label === "CERTIFIED_FAST" &&
		!isLive;

	const step7Gate = deriveStep7Gate();
	const td = guidedView?.technical_drawer as
		| Record<string, unknown>
		| undefined;
	const diagCodes = Array.isArray(td?.diagnostic_codes)
		? (td.diagnostic_codes as string[])
		: [];
	const v1V6FailCount = diagCodes.filter((c) => /^V[1-6]:/.test(c)).length;
	const v6bUnwaivedCount = Object.keys(
		guidedView?.v6b_waiver_targets ?? {},
	).length;
	const v7Score = typeof td?.v7_score === "number" ? td.v7_score : null;
	const acceptBestDisabled =
		busy ||
		isDraining ||
		run?.status !== "UNCERTIFIED_DRAFT" ||
		v1V6FailCount > 0 ||
		(activeRunPolicy === "strict" &&
			(v6bUnwaivedCount > 0 || v7Score === null || v7Score < 95));

	const onSubTabKey = (e: React.KeyboardEvent, idx: number) => {
		const map: Record<string, number> = {
			ArrowRight: (idx + 1) % GUIDED_SUBS.length,
			ArrowLeft: (idx - 1 + GUIDED_SUBS.length) % GUIDED_SUBS.length,
			Home: 0,
			End: GUIDED_SUBS.length - 1,
		};
		const next = map[e.key];
		if (next !== undefined) {
			e.preventDefault();
			const target = GUIDED_SUBS[next];
			if (target) setGuidedSub(target[0]);
		}
	};

	return (
		<div className="stack-md">
			<div className="row-between">
				<div>
					<h1 className="h1-serif">
						{guidedView?.headline || "Step 6 · Vault Synthesis & Verification"}
					</h1>
					<p className="wb-sub">
						Certify storage-neutral Data Vault 2.0 blueprints with plain-English
						Guided guidance or Architect Observatory telemetry.
					</p>
				</div>
				<div className="row">
					{(["guided", "observatory"] as const).map((m) => (
						<button
							key={m}
							type="button"
							className={`btn btn-sm ${viewMode === m ? "btn-primary" : "btn-secondary"}`}
							onClick={() => setViewMode(m)}
						>
							{m === "guided"
								? "Guided View (Default)"
								: "Architect Observatory"}
						</button>
					))}
				</div>
			</div>

			{isDraining && (
				<output className="banner banner-warn">
					Draining active work... Launch, control, and resume actions are locked
					until the current checkpoint settles.
				</output>
			)}

			{isStaleOntology && (
				<output className="callout-coral row-between">
					<span>
						Ontology advanced to{" "}
						<strong>{activeProject?.active_ontology_rev_id}</strong> (synthesis
						ran on <strong>{guidedView?.ontology_rev_id}</strong>). Start a
						fresh Step 6 run before answering old holds.
					</span>
					<button
						type="button"
						className="btn btn-primary btn-sm"
						disabled={busy || isLive}
						onClick={() => void startSynthesis()}
					>
						▶ Start / Re-run Step 6 Vault Synthesis
					</button>
				</output>
			)}

			<Step6PolicyToolbar
				run={run}
				policy={activeRunPolicy}
				isLive={isLive}
				isDraining={isDraining}
				busy={busy}
				showHorizonReview={showHorizonReview}
				acceptBestDisabled={acceptBestDisabled}
				onSelectPolicy={setActiveRunPolicy}
				onStart={() => void startSynthesis()}
				onPause={() =>
					run && void runControlAction("step6", run.run_id, { action: "pause" })
				}
				onOpenCtrl={(mode) => setCtrl({ open: true, mode, hint: "" })}
				onResumeEmpty={() =>
					run &&
					void withBusy(async () =>
						handleRunResp(
							await api.submitVaultDecisions(pid, run.run_id, { answers: {} }),
						),
					)
				}
				onHorizonReview={() =>
					run &&
					void withBusy(async () =>
						handleRunResp(
							await runControlAction("step6", run.run_id, {
								action: "horizon-review",
							}),
						),
					)
				}
				onAcceptBest={() =>
					run &&
					void runControlAction("step6", run.run_id, { action: "accept-best" })
				}
			/>

			{viewMode === "guided" ? (
				<div className="stack-md">
					<div
						className="tab-strip"
						role="tablist"
						aria-label="Guided Subviews"
					>
						{GUIDED_SUBS.map(([id, label], idx) => (
							<button
								key={id}
								type="button"
								role="tab"
								id={`guided-tab-${id}`}
								aria-selected={guidedSub === id}
								aria-controls={`guided-panel-${id}`}
								tabIndex={guidedSub === id ? 0 : -1}
								className="tab-btn"
								onClick={() => setGuidedSub(id)}
								onKeyDown={(e) => onSubTabKey(e, idx)}
							>
								{label}
							</button>
						))}
					</div>

					<div
						role="tabpanel"
						id={`guided-panel-${guidedSub}`}
						aria-labelledby={`guided-tab-${guidedSub}`}
						className="stack-md"
					>
						{guidedSub === "blueprint" ? (
							<GuidedBlueprintPanel
								entities={entities}
								activeEntity={activeEntity}
								activeExplainer={activeExplainer}
								isMobile={isMobile}
								onSelectEntity={(id) => {
									setSelectedEntityId(id);
									if (isMobile) setExplainerOpen(true);
								}}
								onOpenExplainer={() => setExplainerOpen(true)}
							/>
						) : (
							<GuidedDecisionsPanel
								guidedView={guidedView}
								run={run}
								policy={activeRunPolicy}
								vaultIr={vaultIr}
								evolutionLocks={ontologyBundle?.evolution_locks}
								runsCount={runsList.length}
								v1V6FailCount={v1V6FailCount}
								v6bUnwaivedCount={v6bUnwaivedCount}
								v7Score={v7Score}
								step7Unlocked={step7Gate.unlocked}
								step7LockReason={step7Gate.reason}
								isMobile={isMobile}
								busy={busy}
								onAcceptBest={() =>
									run &&
									void runControlAction("step6", run.run_id, {
										action: "accept-best",
									})
								}
								onVerifyVault={() => void verifyVaultAction(pid)}
								onSubmitDecision={(answers) =>
									withBusy(async () => {
										if (run)
											await handleRunResp(
												await api.submitVaultDecisions(pid, run.run_id, {
													answers,
												}),
											);
									})
								}
								onSubmitHorizon={(req: HorizonActionReq) =>
									withBusy(async () => {
										if (run)
											await handleRunResp(
												await api.submitHorizonSteering(pid, run.run_id, req),
											);
									})
								}
								onSubmitWaiver={(req: WaiverSubmitReq) =>
									withBusy(async () => {
										await api.submitVaultWaiver(pid, req);
										await hydrateStep6();
									})
								}
								onAskAiRefactor={handleAskAiRefactor}
								onNavigateStep4={(s4Id) =>
									navigate(
										`/projects/${encodeURIComponent(pid)}/step/4${s4Id ? `?run_id=${encodeURIComponent(s4Id)}` : ""}`,
									)
								}
							/>
						)}
					</div>
				</div>
			) : (
				<ArchitectObservatory
					projectId={pid}
					run={run}
					policy={activeRunPolicy}
					busy={busy}
					onVerifyVault={() => void verifyVaultAction(pid)}
				/>
			)}

			<div className="card-subtle row-between" style={{ minHeight: 52 }}>
				<button
					type="button"
					className="btn btn-secondary"
					onClick={() =>
						navigate(`/projects/${encodeURIComponent(pid)}/step/5`)
					}
				>
					← Step 5 Ontology
				</button>
				<button
					type="button"
					className="btn btn-primary"
					disabled={!step7Gate.unlocked}
					title={step7Gate.reason ?? undefined}
					onClick={() =>
						navigate(`/projects/${encodeURIComponent(pid)}/step/7`)
					}
				>
					Proceed to Step 7 Target Build →
				</button>
			</div>

			<dialog
				ref={explainerDlgRef}
				className="dialog-panel"
				aria-label="Why Does This Table Exist"
				{...({ closedby: "any" } as Record<string, string>)}
				onCancel={(e) => {
					e.preventDefault();
					setExplainerOpen(false);
				}}
			>
				<div className="stack-md">
					<div className="row-between">
						<h2 className="dialog-title">
							Why Does This Table Exist? · {activeEntity?.title ?? ""}
						</h2>
						<button
							type="button"
							className="btn btn-sm"
							onClick={() => setExplainerOpen(false)}
						>
							Close
						</button>
					</div>
					{activeExplainer && <ExplainerSections exp={activeExplainer} />}
				</div>
			</dialog>

			{run && (
				<RunControlDialog
					open={ctrl.open}
					onClose={() => setCtrl((p) => ({ ...p, open: false }))}
					run={run}
					initialMode={ctrl.mode}
					initialHint={ctrl.hint}
				/>
			)}
		</div>
	);
}
