import {
	CEGAR_POLICY_DEFAULTS,
	type CegarPolicy,
	type DocumentMap,
	type GraphAOntology,
	type OntologyBundleResp,
	type OntologyDelta,
	type OntologyValidationResp,
	type ProjectState,
	type ReadinessResponse,
	type RunStateResp,
	STEP4_DEFAULT_BUDGET,
	type SourceSummary,
	type WorkspaceSettings,
	isRunLiveStatus,
} from "../api/openapi";

export interface OntologyEditorState {
	draftChannel: "clean" | "operations" | "yaml_text";
	base_revision_id: string | null;
	baseWorkingOntology: GraphAOntology | null;
	baseYamlText: string;
	yamlText: string;
	pendingOps: OntologyDelta[];
	committedProposalIds: string[];
	dismissedProposalIds: string[];
	workingOntology: GraphAOntology | null;
	dirty: boolean;
	conflictHeadRevisionId: string | null;
	conflictServerOntology: OntologyBundleResp | null;
	conflictServerYaml: string | null;
	validationResp: OntologyValidationResp | null;
}

export interface DirtyFlags {
	step2Secret: boolean;
	step3Scan: boolean;
	step5Waivers: boolean;
	step6Decisions: boolean;
	settings: boolean;
}

export interface EffectiveBudgetView {
	mode: "idle" | "settings" | "run" | "projection";
	label: string;
	runId: string | null;
	step: "step4" | "step6" | null;
	turns: number;
	tokens: number;
	elapsed_seconds: number;
	max_turns: number;
	max_tokens: number;
	timeout_seconds: number;
	snapshotOverride: boolean;
	exceededTurns: boolean;
	exceededTokens: boolean;
	exceededTimeout: boolean;
	anyExceeded: boolean;
	isLive: boolean;
}

export interface ReadinessPillView {
	label: "Action Needed" | "Steward Review" | "Unverified" | "Ready";
	tone: "danger" | "warn" | "ok";
	detailLabel: string;
	reasons: string[];
}

export interface ParsedRoute {
	kind: "home" | "step" | "explorer" | "runs" | "settings";
	projectId: string | null;
	step: number | null;
	runId: string | null;
	normalizedUrl: string;
	query: URLSearchParams;
}

const EXEMPT_QUERY_KEYS = new Set(["mode", "sub", "view", "tab"]);

export const createInitialOntologyState = (): OntologyEditorState => ({
	draftChannel: "clean",
	base_revision_id: null,
	baseWorkingOntology: null,
	baseYamlText: "",
	yamlText: "",
	pendingOps: [],
	committedProposalIds: [],
	dismissedProposalIds: [],
	workingOntology: null,
	dirty: false,
	conflictHeadRevisionId: null,
	conflictServerOntology: null,
	conflictServerYaml: null,
	validationResp: null,
});

export const createInitialDirtyFlags = (): DirtyFlags => ({
	step2Secret: false,
	step3Scan: false,
	step5Waivers: false,
	step6Decisions: false,
	settings: false,
});

export function parseAppRoute(pathname: string, search = ""): ParsedRoute {
	const raw = search.startsWith("?") ? search.slice(1) : search;
	const query = new URLSearchParams(raw);
	const clean = pathname.replace(/\/+$/, "") || "/";
	const qs = raw ? `?${raw}` : "";

	if (clean === "/settings") {
		return {
			kind: "settings",
			projectId: null,
			step: null,
			runId: null,
			normalizedUrl: `/settings${qs}`,
			query,
		};
	}
	const mStep = /^\/projects\/([^/]+)\/step\/([1-7])$/.exec(clean);
	if (mStep?.[1] && mStep[2]) {
		const pid = decodeURIComponent(mStep[1]);
		const step = Number.parseInt(mStep[2], 10);
		return {
			kind: "step",
			projectId: pid,
			step,
			runId: null,
			normalizedUrl: `/projects/${encodeURIComponent(pid)}/step/${step}${qs}`,
			query,
		};
	}
	const mExp = /^\/projects\/([^/]+)\/explorer$/.exec(clean);
	if (mExp?.[1]) {
		const pid = decodeURIComponent(mExp[1]);
		return {
			kind: "explorer",
			projectId: pid,
			step: null,
			runId: null,
			normalizedUrl: `/projects/${encodeURIComponent(pid)}/explorer${qs}`,
			query,
		};
	}
	const mRun = /^\/projects\/([^/]+)\/runs(?:\/([^/]+))?$/.exec(clean);
	if (mRun?.[1]) {
		const pid = decodeURIComponent(mRun[1]);
		const rid = mRun[2] ? decodeURIComponent(mRun[2]) : null;
		const base = `/projects/${encodeURIComponent(pid)}/runs${rid ? `/${encodeURIComponent(rid)}` : ""}`;
		return {
			kind: "runs",
			projectId: pid,
			step: null,
			runId: rid,
			normalizedUrl: `${base}${qs}`,
			query,
		};
	}
	return {
		kind: "home",
		projectId: null,
		step: null,
		runId: null,
		normalizedUrl: clean === "/" ? `/${qs}` : "/",
		query: clean === "/" ? query : new URLSearchParams(),
	};
}

export function buildSwitcherTargetUrl(
	currentUrl: string,
	newId: string,
): string {
	const u = new URL(currentUrl, "http://localhost");
	const r = parseAppRoute(u.pathname, u.search);
	const enc = encodeURIComponent(newId);
	if (r.kind === "settings") return r.normalizedUrl;
	if (r.kind === "step" && r.step !== null) {
		const nq = new URLSearchParams(r.query);
		nq.delete("run_id");
		const qs = nq.toString();
		return `/projects/${enc}/step/${r.step}${qs ? `?${qs}` : ""}`;
	}
	if (r.kind === "explorer") {
		const qs = r.query.toString();
		return `/projects/${enc}/explorer${qs ? `?${qs}` : ""}`;
	}
	return r.kind === "runs"
		? `/projects/${enc}/runs`
		: `/projects/${enc}/step/1`;
}

export function isExemptIntraRouteChange(
	fromUrl: string,
	toUrl: string,
): boolean {
	const a = new URL(fromUrl, "http://localhost");
	const b = new URL(toUrl, "http://localhost");
	if (a.pathname !== b.pathname) return false;
	for (const k of new Set([
		...a.searchParams.keys(),
		...b.searchParams.keys(),
	])) {
		if (
			a.searchParams.get(k) !== b.searchParams.get(k) &&
			!EXEMPT_QUERY_KEYS.has(k)
		)
			return false;
	}
	return true;
}

export interface BudgetSelectorInput {
	settings: WorkspaceSettings | null;
	activeProject: ProjectState | null;
	activeStep4Run: RunStateResp | null;
	activeStep6Run: RunStateResp | null;
	activeRunPolicy: CegarPolicy;
	runOrderIds: string[];
	activeStep6OntologyRevId: string | null;
	supersededRunIds: string[];
}

const isStep6Current = (s: BudgetSelectorInput) => {
	if (!s.activeStep6Run || s.supersededRunIds.includes(s.activeStep6Run.run_id))
		return false;
	const rev = s.activeProject?.active_ontology_rev_id ?? null;
	return (
		s.activeStep6OntologyRevId === null ||
		rev === null ||
		s.activeStep6OntologyRevId === rev
	);
};

const isStep4Valid = (s: BudgetSelectorInput) =>
	s.activeStep4Run && !s.supersededRunIds.includes(s.activeStep4Run.run_id)
		? s.activeStep4Run
		: null;

const isNewer = (a: RunStateResp, b: RunStateResp, order: string[]) => {
	const ia = order.indexOf(a.run_id);
	const ib = order.indexOf(b.run_id);
	return ia !== -1 && (ib === -1 || ia < ib);
};

function projectRun(
	s: BudgetSelectorInput,
	run: RunStateResp | null,
	def: { max_turns: number; max_tokens: number; timeout_seconds: number },
	stepHint: "step4" | "step6",
): EffectiveBudgetView {
	const snap = s.activeProject?.settings_snapshot;
	const ws = s.activeProject ? null : s.settings;
	const max_turns =
		run?.effective_max_turns ??
		snap?.max_turns ??
		ws?.max_turns ??
		def.max_turns;
	const max_tokens =
		run?.effective_max_tokens ??
		snap?.max_tokens ??
		ws?.max_tokens ??
		def.max_tokens;
	const timeout_seconds =
		run?.effective_timeout_seconds ??
		snap?.timeout_seconds ??
		ws?.timeout_seconds ??
		def.timeout_seconds;
	const turns = run?.budget_turns_used ?? 0;
	const tokens = run?.budget_tokens_used ?? 0;
	const elapsed_seconds = run?.elapsed_seconds ?? 0;
	const exceededTurns = max_turns > 0 && turns > max_turns;
	const exceededTokens = max_tokens > 0 && tokens > max_tokens;
	const exceededTimeout =
		timeout_seconds > 0 && elapsed_seconds > timeout_seconds;
	return {
		mode: run ? "run" : "projection",
		label: run
			? `${run.step.toUpperCase()} ${run.status}`
			: `${stepHint.toUpperCase()} Budget`,
		runId: run?.run_id ?? null,
		step: run?.step ?? stepHint,
		turns,
		tokens,
		elapsed_seconds,
		max_turns,
		max_tokens,
		timeout_seconds,
		snapshotOverride: Boolean(
			snap &&
				(snap.max_turns != null ||
					snap.max_tokens != null ||
					snap.timeout_seconds != null),
		),
		exceededTurns,
		exceededTokens,
		exceededTimeout,
		anyExceeded: exceededTurns || exceededTokens || exceededTimeout,
		isLive: isRunLiveStatus(run?.status),
	};
}

export function resolveEffectiveBudget(
	s: BudgetSelectorInput,
	pathname: string,
	search = "",
): EffectiveBudgetView {
	const route = parseAppRoute(pathname, search);
	const polCaps = CEGAR_POLICY_DEFAULTS[s.activeRunPolicy];

	if (route.kind === "settings") {
		const ws = s.settings;
		const wsPol = CEGAR_POLICY_DEFAULTS[ws?.cegar_policy ?? s.activeRunPolicy];
		return {
			mode: "settings",
			label: "Default",
			runId: null,
			step: null,
			turns: 0,
			tokens: 0,
			elapsed_seconds: 0,
			max_turns: ws?.max_turns ?? wsPol.max_turns,
			max_tokens: ws?.max_tokens ?? wsPol.max_tokens,
			timeout_seconds: ws?.timeout_seconds ?? wsPol.timeout_seconds,
			snapshotOverride: false,
			exceededTurns: false,
			exceededTokens: false,
			exceededTimeout: false,
			anyExceeded: false,
			isLive: false,
		};
	}

	const live = isRunLiveStatus(s.activeStep6Run?.status)
		? s.activeStep6Run
		: isRunLiveStatus(s.activeStep4Run?.status)
			? s.activeStep4Run
			: null;
	if (live) {
		return projectRun(
			s,
			live,
			live.step === "step4"
				? STEP4_DEFAULT_BUDGET
				: CEGAR_POLICY_DEFAULTS[live.policy ?? s.activeRunPolicy],
			live.step,
		);
	}

	if (route.kind === "home" || (route.kind === "step" && route.step === 1)) {
		return {
			mode: "idle",
			label: "Budget: Idle · 0 active runs",
			runId: null,
			step: null,
			turns: 0,
			tokens: 0,
			elapsed_seconds: 0,
			max_turns: 0,
			max_tokens: 0,
			timeout_seconds: 0,
			snapshotOverride: false,
			exceededTurns: false,
			exceededTokens: false,
			exceededTimeout: false,
			anyExceeded: false,
			isLive: false,
		};
	}
	if (route.kind === "step" && (route.step === 2 || route.step === 3)) {
		return projectRun(s, null, STEP4_DEFAULT_BUDGET, "step4");
	}
	const s4 = isStep4Valid(s);
	const s6 = isStep6Current(s) ? s.activeStep6Run : null;
	if (route.kind === "step" && route.step === 4)
		return projectRun(s, s4, STEP4_DEFAULT_BUDGET, "step4");
	if (route.kind === "step" && route.step === 5) {
		if (s4 && (!s6 || isNewer(s4, s6, s.runOrderIds)))
			return projectRun(s, s4, STEP4_DEFAULT_BUDGET, "step4");
		return projectRun(
			s,
			null,
			CEGAR_POLICY_DEFAULTS[s.activeStep6Run?.policy ?? s.activeRunPolicy],
			"step6",
		);
	}
	if (route.kind === "step" && route.step === 6) {
		return projectRun(
			s,
			s6,
			CEGAR_POLICY_DEFAULTS[s6?.policy ?? s.activeRunPolicy],
			"step6",
		);
	}
	const chosen =
		s4 && s6 ? (isNewer(s4, s6, s.runOrderIds) ? s4 : s6) : (s6 ?? s4);
	if (chosen) {
		return projectRun(
			s,
			chosen,
			chosen.step === "step4"
				? STEP4_DEFAULT_BUDGET
				: CEGAR_POLICY_DEFAULTS[chosen.policy ?? s.activeRunPolicy],
			chosen.step,
		);
	}
	return projectRun(s, null, polCaps, "step6");
}

export interface ReadinessInput extends BudgetSelectorInput {
	healthOk: boolean;
	sources: SourceSummary[];
	missingSecretRefs: string[];
	documentMaps: DocumentMap[];
	ontologyBundle: OntologyBundleResp | null;
	ontologyState: OntologyEditorState;
	hiddenProposalIds: string[];
	readiness?: ReadinessResponse | null;
}

export function deriveReadinessPill(s: ReadinessInput): ReadinessPillView {
	const reasons: string[] = [];
	if (!s.healthOk) reasons.push("Server health degraded");
	if (
		s.missingSecretRefs.length > 0 ||
		s.sources.some(
			(x) =>
				x.status === "NEEDS_LOCAL_SECRET_BINDING" ||
				x.status === "METADATA_ONLY_BINARY_ABSENT",
		)
	) {
		reasons.push("Sources require secret binding or local files");
	}
	const s4 = isStep4Valid(s);
	const s6 = isStep6Current(s) ? s.activeStep6Run : null;
	const latest =
		s4 && s6 ? (isNewer(s4, s6, s.runOrderIds) ? s4 : s6) : (s6 ?? s4);
	if (latest?.status === "FAILED")
		reasons.push(`Latest run ${latest.run_id} failed`);

	const prov =
		s.activeProject?.settings_snapshot.harness_provider ??
		s.settings?.harness_provider ??
		null;
	const provRec = prov
		? s.readiness?.providers.find((p) => p.provider === prov)
		: undefined;
	if (
		provRec &&
		(provRec.status === "MISSING_PREREQ" || provRec.status === "AUTH_FAILED")
	) {
		reasons.push(
			provRec.remediation?.summary ||
				provRec.last_auth_error_summary ||
				(provRec.missing_prereqs?.length
					? `Harness ${prov} missing: ${provRec.missing_prereqs.join(", ")}`
					: `Harness ${prov} (${provRec.status})`),
		);
	}
	const fmt = (label: ReadinessPillView["label"]): ReadinessPillView => ({
		label,
		tone:
			label === "Action Needed" ? "danger" : label === "Ready" ? "ok" : "warn",
		detailLabel: prov ? `${label} · ${prov}` : label,
		reasons,
	});
	if (reasons.length > 0) return fmt("Action Needed");

	for (const r of [s6, s4]) {
		if (
			r &&
			(r.status === "HOLD_CHECKPOINTED" ||
				r.status === "UNCERTIFIED_DRAFT" ||
				r.status === "HOLD_REQUESTED")
		) {
			reasons.push(`Run ${r.run_id} requires steward attention (${r.status})`);
		}
	}
	if (
		s.documentMaps.some((d) =>
			d.pages?.some(
				(p) =>
					p.is_scanned_or_low_density &&
					!(d.approved_scan_pages ?? []).includes(p.page_number),
			),
		)
	) {
		reasons.push("Scanned document pages awaiting approval");
	}
	if (
		s.ontologyBundle?.staged_candidates?.some((c) => c.status === "QUARANTINED")
	) {
		reasons.push("Quarantined ontology candidates require review");
	}
	if (
		s.ontologyBundle?.ontology_proposals?.some(
			(p) =>
				p.status === "PENDING_STEWARD" &&
				!s.hiddenProposalIds.includes(p.proposal_id) &&
				!s.ontologyState.committedProposalIds.includes(p.proposal_id) &&
				!s.ontologyState.dismissedProposalIds.includes(p.proposal_id),
		)
	) {
		reasons.push("Pending alignment proposals in Step 5");
	}
	if (reasons.length > 0) return fmt("Steward Review");
	if (provRec?.status === "CONFIGURED_UNVERIFIED") {
		reasons.push(`Harness provider ${prov} is configured but unverified`);
		return fmt("Unverified");
	}
	return fmt("Ready");
}

export function deriveStep7Gate(s: {
	activeProject: ProjectState | null;
	vaultIr: unknown;
	activeStep6Run: RunStateResp | null;
	activeStep6OntologyRevId: string | null;
	supersededRunIds: string[];
	failedVerifyWaveId: string | null;
}): { unlocked: boolean; reason: string | null } {
	if (!s.activeProject?.active_wave_id || !s.vaultIr)
		return {
			unlocked: false,
			reason: "No certified vault wave available yet.",
		};
	if (
		s.activeStep6Run?.status === "FAILED" &&
		!s.supersededRunIds.includes(s.activeStep6Run.run_id) &&
		(s.activeStep6OntologyRevId === null ||
			s.activeStep6OntologyRevId === s.activeProject.active_ontology_rev_id)
	) {
		return { unlocked: false, reason: "Latest Step 6 run failed." };
	}
	if (s.failedVerifyWaveId === s.activeProject.active_wave_id)
		return {
			unlocked: false,
			reason: `Verify Vault failed on ${s.activeProject.active_wave_id}.`,
		};
	return { unlocked: true, reason: null };
}
