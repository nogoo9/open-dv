import { create } from "zustand";
import { api, parseProblemDetails } from "../api/client";
import type {
	CegarPolicy,
	CertifiedVaultIR,
	DocumentMap,
	GuidedRunView,
	OntologyBundleResp,
	ProblemDetails,
	ProjectState,
	ProjectSummary,
	ReadinessResponse,
	RunControlReq,
	RunStateResp,
	RunStreamEvent,
	SourceSummary,
	VaultVerifyResp,
	WorkspaceSettings,
} from "../api/openapi";
import { createRunHelpers, stopElapsedTicker } from "./runActions";
import {
	type DirtyFlags,
	type EffectiveBudgetView,
	type OntologyEditorState,
	type ReadinessPillView,
	buildSwitcherTargetUrl,
	createInitialDirtyFlags,
	createInitialOntologyState,
	deriveReadinessPill,
	deriveStep7Gate,
	isExemptIntraRouteChange,
	parseAppRoute,
	resolveEffectiveBudget,
} from "./selectors";

export * from "./selectors";
export { SSE_RING_MAX, mergeSseEvents } from "./runActions";

export interface Step3ScanDraftEntry {
	pages: number[];
	rationale: string;
	allowExternalOcr: boolean;
}

export interface PendingNavigation {
	kind: "url" | "switch" | "custom";
	to?: string;
	replace?: boolean;
	newProjectId?: string;
	onConfirm?: () => void;
}

export interface ProjectStore {
	healthOk: boolean;
	healthVersion: string;
	bootstrapped: boolean;
	currentUrl: string;
	exportImportOpen: boolean;
	exportImportFile: File | null;
	activeProject: ProjectState | null;
	projectsSummary: ProjectSummary[];
	sources: SourceSummary[];
	documentMaps: DocumentMap[];
	ontologyBundle: OntologyBundleResp | null;
	vaultIr: CertifiedVaultIR | null;
	failedVerifyWaveId: string | null;
	missingSecretRefs: string[];
	step3ScanDraft: Record<string, Step3ScanDraftEntry>;
	hiddenProposalIds: string[];
	settings: WorkspaceSettings | null;
	settingsDraft: WorkspaceSettings | null;
	readiness: ReadinessResponse | null;
	sqlQuery: string;
	guidedView: GuidedRunView | null;
	lastProblem: ProblemDetails | null;
	liveAnnouncements: { polite: string; assertive: string };
	ontologyState: OntologyEditorState;
	dirtyFlags: DirtyFlags;
	activeStep4Run: RunStateResp | null;
	activeStep6Run: RunStateResp | null;
	activeRunPolicy: CegarPolicy;
	runOrderIds: string[];
	activeStep6OntologyRevId: string | null;
	supersededRunIds: string[];
	sseEvents: RunStreamEvent[];
	lastReceivedSeq: number;
	sseDisconnected: boolean;
	sseActiveRunId: string | null;
	sseBackfillActive: boolean;
	pendingNav: PendingNavigation | null;
	isDirty: () => boolean;
	applyDiscardReset: () => void;
	checkHealth: () => Promise<void>;
	fetchReadiness: () => Promise<void>;
	hydrateProjectShell: (
		pid: string,
		opts?: { preserveMissingSecretRefs?: boolean; loadVaultIr?: boolean },
	) => Promise<void>;
	bootstrapRoute: (pathname: string, search?: string) => Promise<void>;
	navigate: (
		to: string,
		opts?: { replace?: boolean; bypassDirtyGuard?: boolean },
	) => boolean;
	switchProject: (newId: string) => boolean;
	requestDirtyConfirm: (onConfirm: () => void) => void;
	confirmPendingNav: () => void;
	cancelPendingNav: () => void;
	openExportImportModal: (file?: File | null) => void;
	closeExportImportModal: () => void;
	resolveEffectiveBudget: (p?: string, q?: string) => EffectiveBudgetView;
	deriveReadinessPill: () => ReadinessPillView;
	deriveStep7Gate: () => { unlocked: boolean; reason: string | null };
	startRunSse: (
		pid: string,
		rid: string,
		step: "step4" | "step6",
		opts?: { backfillLogs?: boolean; initialLastSeq?: number },
	) => Promise<void>;
	abortRunSse: () => void;
	runControlAction: (
		step: "step4" | "step6",
		rid: string,
		req: RunControlReq,
	) => Promise<RunStateResp>;
	verifyVaultAction: (pid: string) => Promise<VaultVerifyResp>;
	clearLastProblem: () => void;
	setLastProblem: (p: ProblemDetails | null) => void;
	announce: (level: "polite" | "assertive", msg: string) => void;
	setStep3ScanDraft: (docId: string, entry: Step3ScanDraftEntry) => void;
	setDirtyFlag: (k: keyof DirtyFlags, val: boolean) => void;
	setSettingsDraft: (draft: WorkspaceSettings | null, dirty?: boolean) => void;
	updateOntologyState: (
		patch:
			| Partial<OntologyEditorState>
			| ((prev: OntologyEditorState) => OntologyEditorState),
	) => void;
	hideProposalInSession: (id: string) => void;
	markRunsSupersededOnCommit: (newRevId?: string | null) => void;
	setSqlQuery: (q: string) => void;
	setActiveStep4Run: (r: RunStateResp | null) => void;
	setActiveStep6Run: (r: RunStateResp | null, ontRevId?: string | null) => void;
	setActiveRunPolicy: (p: CegarPolicy) => void;
	setGuidedView: (v: GuidedRunView | null) => void;
	setVaultIr: (ir: CertifiedVaultIR | null) => void;
	setFailedVerifyWaveId: (id: string | null) => void;
	setMissingSecretRefs: (refs: string[]) => void;
	setSources: (s: SourceSummary[]) => void;
	setDocumentMaps: (d: DocumentMap[]) => void;
	setOntologyBundle: (b: OntologyBundleResp | null) => void;
	setProjectsSummary: (p: ProjectSummary[]) => void;
	setActiveProject: (p: ProjectState | null) => void;
	setSettings: (s: WorkspaceSettings | null) => void;
	setReadiness: (r: ReadinessResponse | null) => void;
}

let hydrateAc: AbortController | null = null;
let hydrateGen = 0;

const routeNeedsIr = (pathname: string) => {
	const r = parseAppRoute(pathname);
	return (
		r.kind === "explorer" ||
		(r.kind === "step" && (r.step === 6 || r.step === 7))
	);
};

export const useProjectStore = create<ProjectStore>()((set, get) => {
	const runs = createRunHelpers<ProjectStore>({ get, set });

	const execNav = (to: string, replace = false) => {
		const u = new URL(to, "http://localhost");
		const r = parseAppRoute(u.pathname, u.search);
		if (typeof window !== "undefined")
			window.history[replace ? "replaceState" : "pushState"](
				null,
				"",
				r.normalizedUrl,
			);
		set({ currentUrl: r.normalizedUrl });
		const curPid = get().activeProject?.project_id ?? null;
		if (!r.projectId) return;
		const needsIr = routeNeedsIr(r.normalizedUrl);
		if (curPid !== r.projectId) {
			void get().hydrateProjectShell(r.projectId, { loadVaultIr: needsIr });
		} else if (
			needsIr &&
			get().vaultIr === null &&
			get().activeProject?.active_wave_id
		) {
			void api
				.getVaultIr(r.projectId)
				.then((ir) => {
					if (get().activeProject?.project_id === r.projectId)
						set({ vaultIr: ir });
				})
				.catch(() => undefined);
		}
	};

	const execSwitch = (newId: string) => {
		const target = buildSwitcherTargetUrl(get().currentUrl, newId);
		runs.abortRunSse();
		if (typeof window !== "undefined" && !target.startsWith("/settings"))
			window.history.pushState(null, "", target);
		set({ currentUrl: target });
		void get().hydrateProjectShell(newId, {
			loadVaultIr: routeNeedsIr(target),
		});
	};

	const sp =
		<K extends keyof ProjectStore>(k: K) =>
		(v: ProjectStore[K]) =>
			set({ [k]: v } as Pick<ProjectStore, K>);

	return {
		healthOk: true,
		healthVersion: "",
		bootstrapped: false,
		currentUrl:
			typeof window !== "undefined"
				? `${window.location.pathname}${window.location.search}`
				: "/",
		exportImportOpen: false,
		exportImportFile: null,
		activeProject: null,
		projectsSummary: [],
		sources: [],
		documentMaps: [],
		ontologyBundle: null,
		vaultIr: null,
		failedVerifyWaveId: null,
		missingSecretRefs: [],
		step3ScanDraft: {},
		hiddenProposalIds: [],
		settings: null,
		settingsDraft: null,
		readiness: null,
		sqlQuery: "",
		guidedView: null,
		lastProblem: null,
		liveAnnouncements: { polite: "", assertive: "" },
		ontologyState: createInitialOntologyState(),
		dirtyFlags: createInitialDirtyFlags(),
		activeStep4Run: null,
		activeStep6Run: null,
		activeRunPolicy: "balanced",
		runOrderIds: [],
		activeStep6OntologyRevId: null,
		supersededRunIds: [],
		sseEvents: [],
		lastReceivedSeq: 0,
		sseDisconnected: false,
		sseActiveRunId: null,
		sseBackfillActive: false,
		pendingNav: null,

		isDirty: () => {
			const { dirtyFlags: d, ontologyState: o } = get();
			return (
				o.dirty ||
				o.draftChannel !== "clean" ||
				o.pendingOps.length > 0 ||
				o.committedProposalIds.length > 0 ||
				o.dismissedProposalIds.length > 0 ||
				d.step2Secret ||
				d.step3Scan ||
				d.step5Waivers ||
				d.step6Decisions ||
				d.settings
			);
		},

		applyDiscardReset: () => {
			stopElapsedTicker();
			set((s) => ({
				dirtyFlags: createInitialDirtyFlags(),
				step3ScanDraft: {},
				settingsDraft: s.settings ? structuredClone(s.settings) : null,
				ontologyState: {
					...createInitialOntologyState(),
					base_revision_id: s.ontologyState.base_revision_id,
					baseWorkingOntology: s.ontologyState.baseWorkingOntology,
					workingOntology: s.ontologyState.baseWorkingOntology,
					baseYamlText: s.ontologyState.baseYamlText,
					yamlText: s.ontologyState.baseYamlText,
				},
			}));
		},

		checkHealth: async () => {
			try {
				const h = await api.getHealth();
				set({ healthOk: h.status === "ok", healthVersion: h.version });
			} catch {
				set({ healthOk: false });
			}
		},

		fetchReadiness: async () => {
			try {
				const data = await api.getAgentReadiness();
				if (Array.isArray(data?.providers)) set({ readiness: data });
			} catch {
				/* non-throwing readiness refresh */
			}
		},

		hydrateProjectShell: async (pid, opts = {}) => {
			hydrateAc?.abort();
			runs.abortVerify();
			runs.abortRunSse();
			const ac = new AbortController();
			hydrateAc = ac;
			const gen = ++hydrateGen;
			set((s) => ({
				activeStep4Run: null,
				activeStep6Run: null,
				supersededRunIds: [],
				runOrderIds: [],
				activeStep6OntologyRevId: null,
				guidedView: null,
				sseEvents: [],
				lastReceivedSeq: 0,
				sseDisconnected: false,
				lastProblem: null,
				hiddenProposalIds: [],
				failedVerifyWaveId: null,
				vaultIr: null,
				missingSecretRefs: opts.preserveMissingSecretRefs
					? s.missingSecretRefs
					: [],
			}));
			try {
				const [proj, srcs, docs, bundle, runList] = await Promise.all([
					api.getProject(pid, ac.signal),
					api.listSources(pid, ac.signal),
					api.listDocumentMaps(pid, ac.signal),
					api.getOntologyBundle(pid, ac.signal),
					api.listRuns(pid, undefined, ac.signal),
				]);
				if (ac.signal.aborted || gen !== hydrateGen) return;
				let ir: CertifiedVaultIR | null = null;
				if (opts.loadVaultIr && proj.active_wave_id) {
					try {
						ir = await api.getVaultIr(pid, ac.signal);
					} catch (e) {
						if (parseProblemDetails(e).status !== 404) throw e;
					}
				}
				if (ac.signal.aborted || gen !== hydrateGen) return;
				set({
					activeProject: proj,
					sources: srcs,
					documentMaps: docs,
					ontologyBundle: bundle,
					vaultIr: ir,
					activeStep4Run: runList.find((r) => r.step === "step4") ?? null,
					activeStep6Run: runList.find((r) => r.step === "step6") ?? null,
					activeRunPolicy: proj.settings_snapshot.cegar_policy,
					runOrderIds: runList.map((r) => r.run_id),
					activeStep6OntologyRevId: proj.active_ontology_rev_id ?? null,
					ontologyState: {
						...createInitialOntologyState(),
						base_revision_id: proj.active_ontology_rev_id ?? null,
						baseWorkingOntology: bundle.ontology,
						workingOntology: bundle.ontology,
					},
				});
				runs.syncTicker();
			} catch (e) {
				if (!ac.signal.aborted && gen === hydrateGen)
					set({ lastProblem: parseProblemDetails(e) });
			}
		},

		bootstrapRoute: async (pathname, search = "") => {
			const r = parseAppRoute(pathname, search);
			set({ currentUrl: r.normalizedUrl, bootstrapped: true });
			const [hRes, sRes, pRes] = await Promise.allSettled([
				api.getHealth(),
				api.getSettings(),
				api.listProjects(),
				get().fetchReadiness(),
			]);
			set((cur) => {
				const h = hRes.status === "fulfilled" ? hRes.value : null;
				const ws = sRes.status === "fulfilled" ? sRes.value : cur.settings;
				const projs =
					pRes.status === "fulfilled" ? pRes.value : cur.projectsSummary;
				return {
					healthOk: h ? h.status === "ok" : false,
					healthVersion: h ? h.version : cur.healthVersion,
					settings: ws,
					settingsDraft:
						cur.dirtyFlags.settings || !ws
							? cur.settingsDraft
							: structuredClone(ws),
					projectsSummary: projs,
					activeRunPolicy:
						cur.activeProject?.settings_snapshot.cegar_policy ??
						ws?.cegar_policy ??
						cur.activeRunPolicy,
				};
			});
			if (r.projectId) {
				await get().hydrateProjectShell(r.projectId, {
					loadVaultIr: routeNeedsIr(r.normalizedUrl),
				});
			} else if (r.kind === "settings") {
				const latest = get().projectsSummary[0];
				if (latest)
					await get().hydrateProjectShell(latest.project_id, {
						loadVaultIr: false,
					});
			}
		},

		navigate: (to, opts = {}) => {
			const u = new URL(to, "http://localhost");
			const r = parseAppRoute(u.pathname, u.search);
			if (
				!opts.bypassDirtyGuard &&
				!isExemptIntraRouteChange(get().currentUrl, r.normalizedUrl) &&
				get().isDirty()
			) {
				set({
					pendingNav: {
						kind: "url",
						to: r.normalizedUrl,
						replace: opts.replace,
					},
				});
				return false;
			}
			execNav(r.normalizedUrl, opts.replace);
			return true;
		},

		switchProject: (newId) => {
			if (get().isDirty()) {
				set({ pendingNav: { kind: "switch", newProjectId: newId } });
				return false;
			}
			execSwitch(newId);
			return true;
		},

		requestDirtyConfirm: (onConfirm) =>
			set({ pendingNav: { kind: "custom", onConfirm } }),

		confirmPendingNav: () => {
			const p = get().pendingNav;
			set({ pendingNav: null });
			get().applyDiscardReset();
			if (p?.kind === "url" && p.to) execNav(p.to, p.replace);
			else if (p?.kind === "switch" && p.newProjectId)
				execSwitch(p.newProjectId);
			else if (p?.kind === "custom") p.onConfirm?.();
		},

		cancelPendingNav: () => set({ pendingNav: null }),
		openExportImportModal: (file = null) =>
			set({ exportImportOpen: true, exportImportFile: file }),
		closeExportImportModal: () =>
			set({ exportImportOpen: false, exportImportFile: null }),

		resolveEffectiveBudget: (pathname, search) => {
			const s = get();
			const u = new URL(s.currentUrl, "http://localhost");
			return resolveEffectiveBudget(
				s,
				pathname ?? u.pathname,
				search ?? u.search,
			);
		},
		deriveReadinessPill: () => deriveReadinessPill(get()),
		deriveStep7Gate: () => deriveStep7Gate(get()),

		startRunSse: runs.startRunSse,
		abortRunSse: runs.abortRunSse,
		runControlAction: runs.runControlAction,
		verifyVaultAction: runs.verifyVaultAction,

		clearLastProblem: () => set({ lastProblem: null }),
		setLastProblem: sp("lastProblem"),
		announce: (level, msg) =>
			set((s) => ({
				liveAnnouncements: { ...s.liveAnnouncements, [level]: msg },
			})),
		setStep3ScanDraft: (docId, entry) =>
			set((s) => ({
				step3ScanDraft: { ...s.step3ScanDraft, [docId]: entry },
				dirtyFlags: { ...s.dirtyFlags, step3Scan: true },
			})),
		setDirtyFlag: (k, val) =>
			set((s) => ({ dirtyFlags: { ...s.dirtyFlags, [k]: val } })),
		setSettingsDraft: (settingsDraft, dirty = true) =>
			set((s) => ({
				settingsDraft,
				dirtyFlags: { ...s.dirtyFlags, settings: dirty },
			})),
		updateOntologyState: (patch) =>
			set((s) => ({
				ontologyState:
					typeof patch === "function"
						? patch(s.ontologyState)
						: { ...s.ontologyState, ...patch },
			})),
		hideProposalInSession: (id) =>
			set((s) => ({
				hiddenProposalIds: s.hiddenProposalIds.includes(id)
					? s.hiddenProposalIds
					: [...s.hiddenProposalIds, id],
			})),
		markRunsSupersededOnCommit: (newRevId) =>
			set((s) => {
				const sup = new Set(s.supersededRunIds);
				if (s.activeStep4Run) sup.add(s.activeStep4Run.run_id);
				if (s.activeStep6Run) sup.add(s.activeStep6Run.run_id);
				return {
					supersededRunIds: [...sup],
					activeStep6OntologyRevId:
						newRevId !== undefined ? newRevId : s.activeStep6OntologyRevId,
				};
			}),
		setSqlQuery: sp("sqlQuery"),
		setActiveStep4Run: (r) => {
			if (r) runs.applyRunState(r);
			else {
				set({ activeStep4Run: null });
				runs.syncTicker();
			}
		},
		setActiveStep6Run: (r, ontRevId) => {
			if (r) runs.applyRunState(r, ontRevId);
			else {
				set({ activeStep6Run: null });
				runs.syncTicker();
			}
		},
		setActiveRunPolicy: sp("activeRunPolicy"),
		setGuidedView: sp("guidedView"),
		setVaultIr: sp("vaultIr"),
		setFailedVerifyWaveId: sp("failedVerifyWaveId"),
		setMissingSecretRefs: sp("missingSecretRefs"),
		setSources: sp("sources"),
		setDocumentMaps: sp("documentMaps"),
		setOntologyBundle: sp("ontologyBundle"),
		setProjectsSummary: sp("projectsSummary"),
		setActiveProject: sp("activeProject"),
		setSettings: sp("settings"),
		setReadiness: sp("readiness"),
	};
});
