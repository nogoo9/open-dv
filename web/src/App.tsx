import { useEffect, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { ExportImportModal } from "./components/ExportImportModal";
import { RemediationList } from "./components/RemediationList";
import { DVSchemaExplorerPage } from "./pages/DVSchemaExplorerPage";
import { RunExplorerPage } from "./pages/RunExplorerPage";
import { SettingsHub } from "./pages/SettingsHub";
import { Step1Scope } from "./pages/Step1Scope";
import { Step2Sources } from "./pages/Step2Sources";
import { Step3Preview } from "./pages/Step3Preview";
import { Step4Discovery } from "./pages/Step4Discovery";
import { Step5Explorer } from "./pages/Step5Explorer";
import { Step6Vault } from "./pages/Step6Vault";
import { Step7Storage } from "./pages/Step7Storage";
import {
	isExemptIntraRouteChange,
	parseAppRoute,
	useProjectStore,
} from "./store/projectStore";

const STEPS = [
	[1, "Scope & Seeds"],
	[2, "Sources & Secrets"],
	[3, "Preview & Clusters"],
	[4, "Discovery"],
	[5, "Ontology Explorer"],
	[6, "Vault Synthesis"],
	[7, "Storage & Targets"],
] as const;

const fmtTok = (n: number) =>
	n >= 1000 ? `${Math.round(n / 1000)}k` : String(n);

const PAGES = [
	null,
	Step1Scope,
	Step2Sources,
	Step3Preview,
	Step4Discovery,
	Step5Explorer,
	Step6Vault,
	Step7Storage,
] as const;

function useDialogSync(
	ref: React.RefObject<HTMLDialogElement | null>,
	open: boolean,
	onClose: () => void,
) {
	useEffect(() => {
		const el = ref.current;
		if (!el) return;
		if (open && !el.open) el.showModal();
		else if (!open && el.open) el.close();
		const onClick = (e: MouseEvent) => {
			if (e.target === el) onClose();
		};
		el.addEventListener("click", onClick);
		return () => el.removeEventListener("click", onClick);
	}, [ref, open, onClose]);
}

function renderActivePage(route: ReturnType<typeof parseAppRoute>) {
	if (route.kind === "settings") return <SettingsHub />;
	if (route.kind === "explorer") return <DVSchemaExplorerPage />;
	if (route.kind === "runs") return <RunExplorerPage />;
	const Comp =
		(route.kind === "step" && route.step ? PAGES[route.step] : null) ??
		Step1Scope;
	return <Comp />;
}

export function App() {
	const currentUrl = useProjectStore((s) => s.currentUrl);
	const bootstrapped = useProjectStore((s) => s.bootstrapped);
	const healthOk = useProjectStore((s) => s.healthOk);
	const healthVersion = useProjectStore((s) => s.healthVersion);
	const activeProject = useProjectStore((s) => s.activeProject);
	const projectsSummary = useProjectStore((s) => s.projectsSummary);
	const lastProblem = useProjectStore((s) => s.lastProblem);
	const pendingNav = useProjectStore((s) => s.pendingNav);
	const liveAnnouncements = useProjectStore((s) => s.liveAnnouncements);
	const navigate = useProjectStore((s) => s.navigate);
	const switchProject = useProjectStore((s) => s.switchProject);
	const openExportImport = useProjectStore((s) => s.openExportImportModal);
	const clearLastProblem = useProjectStore((s) => s.clearLastProblem);
	const confirmPendingNav = useProjectStore((s) => s.confirmPendingNav);
	const cancelPendingNav = useProjectStore((s) => s.cancelPendingNav);

	useProjectStore(
		useShallow((s) => [
			s.settings,
			s.readiness,
			s.sources,
			s.missingSecretRefs,
			s.documentMaps,
			s.ontologyBundle,
			s.ontologyState,
			s.hiddenProposalIds,
			s.activeStep4Run,
			s.activeStep6Run,
			s.activeRunPolicy,
			s.runOrderIds,
			s.activeStep6OntologyRevId,
			s.supersededRunIds,
			s.vaultIr,
			s.failedVerifyWaveId,
		]),
	);

	const [mobileNavOpen, setMobileNavOpen] = useState(false);
	const [mobileStepperOpen, setMobileStepperOpen] = useState(false);

	const dirtyDlgRef = useRef<HTMLDialogElement>(null);
	const mobileNavRef = useRef<HTMLDialogElement>(null);
	const mobileStepRef = useRef<HTMLDialogElement>(null);

	useDialogSync(dirtyDlgRef, pendingNav !== null, cancelPendingNav);
	useDialogSync(mobileNavRef, mobileNavOpen, () => setMobileNavOpen(false));
	useDialogSync(mobileStepRef, mobileStepperOpen, () =>
		setMobileStepperOpen(false),
	);

	useEffect(() => {
		if (!bootstrapped && typeof window !== "undefined") {
			void useProjectStore
				.getState()
				.bootstrapRoute(window.location.pathname, window.location.search);
		}
	}, [bootstrapped]);

	useEffect(() => {
		if (typeof window === "undefined") return;
		const onBeforeUnload = (e: BeforeUnloadEvent) => {
			if (useProjectStore.getState().isDirty()) {
				e.preventDefault();
				e.returnValue = "";
			}
		};
		const onPopState = () => {
			const st = useProjectStore.getState();
			const target = `${window.location.pathname}${window.location.search}`;
			if (st.isDirty() && !isExemptIntraRouteChange(st.currentUrl, target)) {
				window.history.pushState(null, "", st.currentUrl);
				st.navigate(target);
				return;
			}
			st.navigate(target, { replace: true, bypassDirtyGuard: true });
		};
		window.addEventListener("beforeunload", onBeforeUnload);
		window.addEventListener("popstate", onPopState);
		return () => {
			window.removeEventListener("beforeunload", onBeforeUnload);
			window.removeEventListener("popstate", onPopState);
		};
	}, []);

	const parsedUrl = new URL(currentUrl, "http://localhost");
	const route = parseAppRoute(parsedUrl.pathname, parsedUrl.search);
	const store = useProjectStore.getState();
	const budget = store.resolveEffectiveBudget(
		parsedUrl.pathname,
		parsedUrl.search,
	);
	const readiness = store.deriveReadinessPill();
	const step7Gate = store.deriveStep7Gate();

	const pid = route.projectId ?? activeProject?.project_id ?? null;
	const encPid = pid ? encodeURIComponent(pid) : null;
	const activeStep = route.kind === "step" ? (route.step ?? 1) : 1;
	const stepHref = (n: number) =>
		encPid ? `/projects/${encPid}/step/${n}` : "/";
	const workflowHref = stepHref(activeStep);
	const explorerHref = encPid ? `/projects/${encPid}/explorer` : "/";
	const runsHref = encPid ? `/projects/${encPid}/runs` : "/";

	const go = (to: string) => (e: React.MouseEvent) => {
		e.preventDefault();
		setMobileNavOpen(false);
		setMobileStepperOpen(false);
		navigate(to);
	};

	const revLabel = !activeProject?.active_ontology_rev_id
		? "Genesis v0.0.0"
		: activeProject.active_wave_id
			? `${activeProject.active_ontology_rev_id} · ${activeProject.active_wave_id}`
			: activeProject.active_ontology_rev_id;

	const bLabel =
		budget.mode === "idle"
			? budget.label
			: budget.mode === "settings"
				? `Default: ${budget.max_turns}t · ${fmtTok(budget.max_tokens)} · ${budget.timeout_seconds}s`
				: `${budget.turns}/${budget.max_turns}${budget.exceededTurns ? "+" : ""}t · ${fmtTok(budget.tokens)}/${fmtTok(budget.max_tokens)}${budget.exceededTokens ? "+" : ""} · ${budget.elapsed_seconds}/${budget.timeout_seconds}${budget.exceededTimeout ? "+" : ""}s`;

	const readinessClass = `pill ${readiness.tone === "danger" ? "pill-err" : readiness.tone === "warn" ? "pill-warn" : "pill-ok"}`;

	const knownIds = new Set(projectsSummary.map((p) => p.project_id));
	const switcherOptions =
		activeProject && !knownIds.has(activeProject.project_id)
			? [
					{
						project_id: activeProject.project_id,
						domain_namespace: activeProject.domain_namespace,
					},
					...projectsSummary,
				]
			: projectsSummary;

	const showStepper = route.kind === "home" || route.kind === "step";
	const stepMeta = STEPS[activeStep - 1] ?? STEPS[0];

	return (
		<div className="wb-shell">
			<header className="wb-topbar">
				<div className="wb-topbar-left">
					<button
						type="button"
						className="btn btn-sm mobile-only"
						aria-expanded={mobileNavOpen}
						aria-controls="mobile-nav-dialog"
						aria-label="Open navigation menu"
						onClick={() => setMobileNavOpen(true)}
					>
						☰
					</button>
					<a href="/" className="wb-brand" onClick={go("/")}>
						<svg
							width="16"
							height="16"
							viewBox="0 0 24 24"
							fill="none"
							style={{ flexShrink: 0 }}
							aria-hidden="true"
						>
							<g stroke="var(--ink)" strokeWidth="2.1" strokeLinecap="round">
								<line x1="12" y1="2.5" x2="12" y2="21.5" />
								<line x1="2.5" y1="12" x2="21.5" y2="12" />
								<line x1="5.3" y1="5.3" x2="18.7" y2="18.7" />
								<line x1="18.7" y1="5.3" x2="5.3" y2="18.7" />
							</g>
						</svg>
						<span>Open-DV</span>
					</a>
					<span className="wb-sep desktop-only" aria-hidden="true" />
					<nav
						className="wb-nav-list desktop-only"
						aria-label="Primary Workbench Navigation"
					>
						<a
							href={workflowHref}
							className="nav-link"
							aria-current={
								route.kind === "home" || route.kind === "step"
									? "page"
									: undefined
							}
							onClick={go(workflowHref)}
						>
							Project Workflow
						</a>
						<a
							href={explorerHref}
							className="nav-link"
							aria-current={route.kind === "explorer" ? "page" : undefined}
							onClick={go(explorerHref)}
						>
							DV Schema Explorer
						</a>
						<a
							href={runsHref}
							className="nav-link"
							aria-current={route.kind === "runs" ? "page" : undefined}
							onClick={go(runsHref)}
						>
							Run Explorer
						</a>
						<a
							href="/settings"
							className="nav-link"
							aria-current={route.kind === "settings" ? "page" : undefined}
							onClick={go("/settings")}
						>
							Settings
						</a>
					</nav>
					<label className="desktop-only">
						<span className="visually-hidden">Project Switcher</span>
						<select
							className="select"
							style={{ width: "auto", minWidth: 140 }}
							aria-label="Project Switcher"
							value={activeProject?.project_id ?? ""}
							onChange={(e) => {
								if (e.target.value) switchProject(e.target.value);
							}}
						>
							<option value="" disabled>
								{switcherOptions.length ? "Select project..." : "No projects"}
							</option>
							{switcherOptions.map((p) => (
								<option key={p.project_id} value={p.project_id}>
									{p.project_id} ({p.domain_namespace})
								</option>
							))}
						</select>
					</label>
					<button
						type="button"
						className="btn btn-sm desktop-only"
						onClick={() => openExportImport(null)}
					>
						Export / Import
					</button>
				</div>

				<div className="wb-topbar-right">
					<span className={readinessClass} title={readiness.reasons.join("; ")}>
						<span className="desktop-only">{readiness.detailLabel}</span>
						<span className="mobile-only">{readiness.label}</span>
					</span>
					<span
						className="pill pill-dark"
						data-exceeded={budget.anyExceeded ? "true" : "false"}
						title={
							budget.snapshotOverride
								? "Project Snapshot Override active"
								: undefined
						}
					>
						<span>{bLabel}</span>
						{budget.snapshotOverride && (
							<span className="pill pill-warn">Project Snapshot Override</span>
						)}
					</span>
					<span className="pill">{revLabel}</span>
				</div>
			</header>

			{showStepper && (
				<nav className="wb-stepper" aria-label="Workflow Steps">
					<div className="wb-nav-list desktop-only">
						{STEPS.map(([n, label]) => {
							const locked = n === 7 && !step7Gate.unlocked;
							return (
								<a
									key={n}
									href={stepHref(n)}
									className="step-item"
									aria-current={activeStep === n ? "step" : undefined}
									aria-disabled={locked ? "true" : undefined}
									title={locked ? (step7Gate.reason ?? undefined) : undefined}
									onClick={go(stepHref(n))}
								>
									<span className="step-num">{n}</span>
									<span>{label}</span>
								</a>
							);
						})}
					</div>
					<button
						type="button"
						className="btn btn-sm mobile-only"
						aria-expanded={mobileStepperOpen}
						aria-controls="mobile-stepper-dialog"
						onClick={() => setMobileStepperOpen(true)}
					>
						Step {stepMeta[0]} of 7 · {stepMeta[1]} ▾
					</button>
				</nav>
			)}

			<main className="wb-main">
				{lastProblem && (
					<div className="banner banner-danger stack-sm" role="alert">
						<div className="row-between">
							<div>
								<strong>{lastProblem.title}</strong> ({lastProblem.error_code} ·{" "}
								{lastProblem.status ?? "N/A"}): {lastProblem.detail}
							</div>
							<button
								type="button"
								className="btn btn-sm"
								onClick={clearLastProblem}
							>
								Dismiss
							</button>
						</div>
						<RemediationList remediation={lastProblem.remediation} />
					</div>
				)}
				{renderActivePage(route)}
			</main>

			<footer className="wb-footer text-sm text-muted">
				<div className="row">
					<span className={healthOk ? "pill pill-ok" : "pill pill-err"}>
						{healthOk ? "Loopback Ready" : "Server Offline"}
					</span>
					<span>{healthVersion ? `v${healthVersion}` : "Open-DV V2"}</span>
				</div>
				<div className="row">
					<span>
						{activeProject
							? `${activeProject.project_id} · ${activeProject.domain_namespace}`
							: "No active project"}
					</span>
					<span className="mono">{revLabel}</span>
				</div>
			</footer>

			<output className="visually-hidden" aria-live="polite">
				{liveAnnouncements.polite}
			</output>
			<div className="visually-hidden" role="alert" aria-live="assertive">
				{liveAnnouncements.assertive}
			</div>

			<dialog
				ref={mobileNavRef}
				id="mobile-nav-dialog"
				className="sheet-dialog"
				aria-label="Mobile Navigation"
				onCancel={(e) => {
					e.preventDefault();
					setMobileNavOpen(false);
				}}
			>
				<div className="sheet-panel">
					<div className="row-between">
						<strong>Navigation</strong>
						<button
							type="button"
							className="btn btn-sm"
							onClick={() => setMobileNavOpen(false)}
						>
							Close
						</button>
					</div>
					<a
						href={workflowHref}
						className="nav-link"
						onClick={go(workflowHref)}
					>
						Project Workflow
					</a>
					<a
						href={explorerHref}
						className="nav-link"
						onClick={go(explorerHref)}
					>
						DV Schema Explorer
					</a>
					<a href={runsHref} className="nav-link" onClick={go(runsHref)}>
						Run Explorer
					</a>
					<a href="/settings" className="nav-link" onClick={go("/settings")}>
						Settings
					</a>
					<label className="field-col">
						<span className="field-label">Project Switcher</span>
						<select
							className="select"
							aria-label="Project Switcher"
							value={activeProject?.project_id ?? ""}
							onChange={(e) => {
								if (e.target.value) {
									setMobileNavOpen(false);
									switchProject(e.target.value);
								}
							}}
						>
							<option value="" disabled>
								{switcherOptions.length ? "Select project..." : "No projects"}
							</option>
							{switcherOptions.map((p) => (
								<option key={p.project_id} value={p.project_id}>
									{p.project_id} ({p.domain_namespace})
								</option>
							))}
						</select>
					</label>
					<button
						type="button"
						className="btn btn-secondary"
						onClick={() => {
							setMobileNavOpen(false);
							openExportImport(null);
						}}
					>
						Export / Import
					</button>
				</div>
			</dialog>

			<dialog
				ref={mobileStepRef}
				id="mobile-stepper-dialog"
				className="sheet-dialog"
				aria-label="Workflow Steps Sheet"
				onCancel={(e) => {
					e.preventDefault();
					setMobileStepperOpen(false);
				}}
			>
				<div className="sheet-panel">
					<div className="row-between">
						<strong>Workflow Steps</strong>
						<button
							type="button"
							className="btn btn-sm"
							onClick={() => setMobileStepperOpen(false)}
						>
							Close
						</button>
					</div>
					{STEPS.map(([n, label]) => (
						<a
							key={n}
							href={stepHref(n)}
							className="step-item"
							aria-current={activeStep === n ? "step" : undefined}
							onClick={go(stepHref(n))}
						>
							<span className="step-num">{n}</span>
							<span>{label}</span>
						</a>
					))}
				</div>
			</dialog>

			<dialog
				ref={dirtyDlgRef}
				className="dialog-panel"
				aria-labelledby="dirty-nav-title"
				{...({ closedby: "closerequest" } as Record<string, string>)}
				onCancel={(e) => {
					e.preventDefault();
					cancelPendingNav();
				}}
			>
				<div className="stack-md">
					<h2 id="dirty-nav-title" className="dialog-title">
						Unsaved Changes
					</h2>
					<p className="text-muted">
						You have unsaved edits, unsubmitted answers, or draft changes.
						Discard them and continue?
					</p>
					<div className="row-end">
						<button type="button" className="btn" onClick={cancelPendingNav}>
							Stay Here
						</button>
						<button
							type="button"
							className="btn btn-danger"
							onClick={confirmPendingNav}
						>
							Discard &amp; Continue
						</button>
					</div>
				</div>
			</dialog>

			<ExportImportModal />
		</div>
	);
}
