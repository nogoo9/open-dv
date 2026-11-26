import type * as T from "./openapi";
import { API_ROUTES as R, buildApiRoute as br } from "./openapi";
import type { paths } from "./types.generated";

export * from "./sse";

export type ErrorCategory = T.ProblemDetails["category"];
type HttpM = "get" | "post";
type Rq<P extends keyof paths, M extends HttpM> = paths[P] extends Record<
	M,
	{ requestBody: { content: { "application/json": infer B } } }
>
	? B
	: never;
type Rb<P extends keyof paths, M extends HttpM> = paths[P] extends Record<
	M,
	{ responses: { 200: { content: { "application/json": infer A } } } }
>
	? A
	: paths[P] extends Record<
				M,
				{ responses: { 202: { content: { "application/json": infer B } } } }
			>
		? B
		: unknown;
type S = AbortSignal;
type Q = Record<string, string | number | boolean | string[] | undefined>;
type P = T.ProblemDetails;

const CATS: readonly ErrorCategory[] = [
	"Execution",
	"Preconditions",
	"Syntax",
	"Resolution",
	"Verification",
];
const PACK =
	"200:0:OK|202:0:HOLD_CHECKPOINTED UNCERTIFIED_DRAFT|400:2:SYNTAX_INVALID_ARGUMENT|400:1:UNSAFE_WAL_FILESYSTEM DATABRICKS_SSRF_BLOCKED|403:1:*UNTRUSTED_WORKSPACE_OVERRIDE LOOPBACK_ORIGIN_FORBIDDEN *EXTERNAL_MCP_DOC_OPT_IN_REQUIRED|404:3:RESOURCE_NOT_FOUND|409:3:PROJECT_JOB_ACTIVE SOURCE_COMMITTED_IN_VAULT PROJECT_ID_CONFLICT REVISION_CONFLICT SOURCE_BINARY_ABSENT|413:2:ARCHIVE_SIZE_LIMIT_EXCEEDED|415:2:UNSUPPORTED_MEDIA_TYPE|422:1:DRIVER_MISSING NEEDS_LOCAL_SECRET_BINDING AGENT_NOT_CONFIGURED|422:4:*UNREDACTED_SCAN_BLOCKED REDACTED_PLACEHOLDER_LEAK *UNGROUNDED_CANDIDATE_QUARANTINED ONT_EVO_CONTRACT_VIOLATION VERIFICATION_GATE_FAILED ARCHIVE_SECURITY_VIOLATION BUNDLE_HASH_MISMATCH|422:3:CITATION_SPAN_UNRESOLVED|500:0:REFERENCE_PACK_INTEGRITY_FAILURE INTERNAL_SERVER_ERROR EMITTER_VERIFICATION_FAILED|502:0:AGENT_EXECUTION_FAILED EXTERNAL_MCP_GATEWAY_ERROR|null:1:REMOTE_BIND_UNAUTHENTICATED";
const SPEC = new Map<T.ErrorCode, [number | null, ErrorCategory, boolean]>();
for (const s of PACK.split("|")) {
	const [st, ci, cs = ""] = s.split(":");
	for (const r of cs.split(" ")) {
		const w = r[0] === "*";
		SPEC.set((w ? r.slice(1) : r) as T.ErrorCode, [
			st === "null" ? null : Number(st),
			CATS[Number(ci)] ?? "Execution",
			w,
		]);
	}
}

const asCode = (v: unknown): T.ErrorCode =>
	typeof v === "string" && SPEC.has(v as T.ErrorCode)
		? (v as T.ErrorCode)
		: "INTERNAL_SERVER_ERROR";
const str = (v: unknown) => (typeof v === "string" ? v : undefined);

export function createProblemDetails(
	code: T.ErrorCode,
	detail: string,
	x?: Record<string, unknown>,
): P {
	const [status, category, waivable] = SPEC.get(code) ?? [
		500,
		"Execution" as const,
		false,
	];
	const lo = code.toLowerCase();
	const out: P = {
		type:
			code === "OK"
				? "urn:opendv:ok"
				: `urn:opendv:error:${lo.replace(/_/g, "-")}`,
		title:
			code === "OK"
				? "OK"
				: lo.replace(/\b\w/g, (c) => c.toUpperCase()).replace(/_/g, " "),
		status,
		detail,
		error_code: code,
		category,
		waivable,
		rationale_required: code === "UNGROUNDED_CANDIDATE_QUARANTINED",
		instance: null,
		run_id: null,
		diagnostics: [],
		defect_report: null,
		remediation: null,
	};
	if (x) {
		for (const [k, v] of Object.entries(x)) {
			if (v !== undefined && k in out) (out as Record<string, unknown>)[k] = v;
		}
	}
	return out;
}

export class ApiProblemError extends Error {
	readonly problem: P;
	readonly status: number;
	constructor(problem: P) {
		super(problem.detail || problem.title || "API Problem");
		this.name = "ApiProblemError";
		this.problem = problem;
		this.status = problem.status ?? 500;
	}
}

const coerce = (o: Record<string, unknown>) =>
	createProblemDetails(
		asCode(o.error_code),
		str(o.detail) ?? str(o.message) ?? "Unexpected error.",
		o,
	);

export function parseProblemDetails(err: unknown): P {
	if (err instanceof ApiProblemError) return err.problem;
	if (err && typeof err === "object") {
		const o = err as Record<string, unknown>;
		const n = (o.problem ?? o.problemDetails) as
			| Record<string, unknown>
			| undefined;
		if (n && typeof n === "object") return coerce(n);
		if (str(o.error_code) || str(o.title) || str(o.detail)) return coerce(o);
		if (typeof o.message === "string") {
			try {
				const parsed = JSON.parse(o.message) as unknown;
				if (parsed && typeof parsed === "object")
					return coerce(parsed as Record<string, unknown>);
			} catch {
				/* plain text */
			}
			return createProblemDetails("INTERNAL_SERVER_ERROR", o.message);
		}
	}
	return createProblemDetails(
		"INTERNAL_SERVER_ERROR",
		err instanceof Error ? err.message : String(err),
	);
}

export const runFailedToProblem = (
	runId: string,
	p: Record<string, unknown>,
): P =>
	createProblemDetails(
		asCode(p.error_code),
		str(p.detail) || `Run ${runId} failed (${asCode(p.error_code)}).`,
		{ run_id: runId },
	);

export interface ApiFetchOptions {
	method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
	body?: unknown;
	signal?: S;
	query?: Q;
	responseType?: "json" | "text" | "blob" | "void";
	headers?: Record<string, string>;
	fetchImpl?: typeof fetch;
}

export async function apiRequest<Out>(
	url: string,
	o: ApiFetchOptions = {},
): Promise<Out> {
	let target = url;
	if (o.query) {
		const sp = new URLSearchParams();
		for (const [k, v] of Object.entries(o.query)) {
			if (Array.isArray(v)) for (const x of v) sp.append(k, x);
			else if (v !== undefined && v !== "") sp.set(k, String(v));
		}
		const q = sp.toString();
		if (q) target = `${url}?${q}`;
	}
	const headers: Record<string, string> = { ...(o.headers ?? {}) };
	let body: BodyInit | undefined;
	if (o.body !== undefined) {
		if (o.body instanceof FormData) body = o.body;
		else {
			headers["Content-Type"] ??= "application/json";
			body = typeof o.body === "string" ? o.body : JSON.stringify(o.body);
		}
	}
	const res = await (o.fetchImpl ?? globalThis.fetch)(target, {
		method: o.method ?? "GET",
		headers,
		body,
		signal: o.signal,
	});
	if (!res.ok) {
		const txt = await res.text().catch(() => "");
		try {
			const parsed = txt ? (JSON.parse(txt) as unknown) : null;
			if (parsed && typeof parsed === "object")
				throw new ApiProblemError(
					coerce({
						status: res.status,
						...(parsed as Record<string, unknown>),
					}),
				);
		} catch (e) {
			if (e instanceof ApiProblemError) throw e;
		}
		throw new ApiProblemError(
			createProblemDetails(
				res.status === 404 ? "RESOURCE_NOT_FOUND" : "INTERNAL_SERVER_ERROR",
				txt || res.statusText || `HTTP ${res.status}`,
				{ status: res.status, instance: target },
			),
		);
	}
	if (res.status === 204 || o.responseType === "void") return undefined as Out;
	if (o.responseType === "blob") return (await res.blob()) as Out;
	return o.responseType === "text"
		? ((await res.text()) as Out)
		: ((await res.json()) as Out);
}

export function buildSourceInspectReq(i: {
	kind: string;
	uri_or_path?: string;
	host?: string;
	options?: Record<string, string | number | boolean>;
	secret_refs?: string[];
}): T.SourceInspectReq {
	const options = { ...(i.options ?? {}) };
	let uri_or_path = (i.uri_or_path ?? "").trim();
	if (i.kind === "databricks_uc") {
		const h = (
			(i.host ?? "").trim() ||
			(str(options.host) ?? "").trim() ||
			uri_or_path
		)
			.trim()
			.replace(/^https?:\/\//i, "")
			.replace(/\/+$/, "");
		uri_or_path = h ? `https://${h}` : "";
	}
	return {
		kind: i.kind,
		uri_or_path,
		options,
		secret_refs: [...(i.secret_refs ?? [])],
	};
}

export const askFiboQuery = (
	pid: string | null,
	req: T.FiboQueryReq,
	s?: S,
): Promise<T.FiboQueryResp> =>
	pid ? api.fiboQaProject(pid, req, s) : api.fiboQaGlobal(req, s);

const g = <O>(u: string, s?: S, query?: Q, rt?: "json" | "text" | "blob") =>
	apiRequest<O>(u, { query, responseType: rt, signal: s });
const p = <O>(
	u: string,
	body?: unknown,
	s?: S,
	method: "POST" | "PUT" | "PATCH" = "POST",
) => apiRequest<O>(u, { method, body, signal: s });
const sub = (pid: string, t: string, x?: Record<string, string>) =>
	br(t, x ? { project_id: pid, ...x } : { project_id: pid });
const del = (t: string, pr: Record<string, string>, s?: S) =>
	apiRequest<undefined>(br(t, pr), {
		method: "DELETE",
		responseType: "void",
		signal: s,
	});
const g0 =
	<P0 extends keyof paths>(t: P0) =>
	(s?: S) =>
		g<Rb<P0, "get">>(t, s);
const p0 =
	<P0 extends keyof paths>(t: P0) =>
	(b: Rq<P0, "post">, s?: S) =>
		p<Rb<P0, "post">>(t, b, s);
const pG =
	<P0 extends keyof paths>(t: P0) =>
	(pid: string, s?: S) =>
		g<Rb<P0, "get">>(sub(pid, t), s);
const pP =
	<P0 extends keyof paths>(t: P0) =>
	(pid: string, b: Rq<P0, "post">, s?: S) =>
		p<Rb<P0, "post">>(sub(pid, t), b, s);
const rG =
	<P0 extends keyof paths>(t: P0) =>
	(pid: string, rid: string, s?: S) =>
		g<Rb<P0, "get">>(sub(pid, t, { rid }), s);
const rP =
	<P0 extends keyof paths>(t: P0) =>
	(pid: string, rid: string, b: Rq<P0, "post">, s?: S) =>
		p<Rb<P0, "post">>(sub(pid, t, { rid }), b, s);

export type TablePreviewOpts = {
	offset?: number;
	limit?: number;
	sourceId?: string;
};
export type BundleMode = "core_project_state" | "full_demo";
export type OntExportFmt = "yaml" | "json" | "ttl";

export const api = {
	getHealth: g0(R.health),
	getSettings: g0(R.settings),
	putSettings: (b: T.WorkspaceSettings, s?: S) =>
		p<T.WorkspaceSettings>(R.settings, b, s, "PUT"),
	revokeTrustedWorkspace: p0(R.settingsRevokeTrustedWorkspace),
	postSecret: p0(R.secrets),
	listSecrets: (keys?: string[], s?: S) =>
		g<T.SecretSummary[]>(R.secrets, s, keys?.length ? { keys } : undefined),
	deleteSecret: (key: string, s?: S) => del(R.secret, { key }, s),
	listMcpServers: g0(R.mcpServers),
	upsertMcpServer: p0(R.mcpServers),
	deleteMcpServer: (server_id: string, s?: S) =>
		del(R.mcpServer, { server_id }, s),
	listAgents: g0(R.agents),
	getAgentReadiness: g0(R.agentsReadiness),
	testAgentReadiness: p0(R.agentsReadinessTest),
	getAgent: (agent_id: string, s?: S) =>
		g<T.AgentPackDetail>(br(R.agent, { agent_id }), s),
	listProjects: g0(R.projects),
	initProject: p0(R.projects),
	getProject: pG(R.project),
	deleteProject: (pid: string, s?: S) => del(R.project, { project_id: pid }, s),
	exportBundle: (pid: string, mode: BundleMode = "core_project_state", s?: S) =>
		g<Blob>(sub(pid, R.projectBundle), s, { mode }, "blob"),
	importProject: (b: FormData | T.ProjectImportPathReq, s?: S) =>
		p<T.ImportReadinessReport>(R.projectImport, b, s),
	listWorkspaceSeeds: (ns?: string, s?: S) =>
		g<T.WorkspaceSeedSummary[]>(R.workspaceSeeds, s, { domain_namespace: ns }),
	listStarterPacks: g0(R.starterPacks),
	previewStarterPack: (pack_id: string, s?: S) =>
		g<T.StarterPackPreview>(br(R.starterPackPreview, { pack_id }), s),
	listSourceAdapters: g0(R.sourceAdapters),
	inspectSource: p0(R.sourceInspect),
	listSources: pG(R.projectSources),
	attachSource: pP(R.projectSources),
	uploadSource: (pid: string, b: FormData, s?: S) =>
		p<T.SourceSummary>(sub(pid, R.projectSourceUpload), b, s),
	reattachSource: (
		pid: string,
		sid: string,
		b: T.SourceReattachReq | FormData,
		s?: S,
	) => p<T.SourceSummary>(sub(pid, R.projectSource, { sid }), b, s, "PATCH"),
	detachSource: (pid: string, sid: string, s?: S) =>
		del(R.projectSource, { project_id: pid, sid }, s),
	listTablesIndex: pG(R.tablesIndex),
	previewTable: (pid: string, tid: string, o: TablePreviewOpts = {}, s?: S) =>
		g<T.TablePreviewPage>(sub(pid, R.tablePreview, { tid }), s, {
			offset: o.offset,
			limit: o.limit,
			source_id: o.sourceId,
		}),
	listDocumentMaps: pG(R.documentsMap),
	getDocumentPages: (pid: string, did: string, ps = 1, pe?: number, s?: S) =>
		g<T.PageTextView[]>(sub(pid, R.documentPages, { did }), s, {
			page_start: ps,
			page_end: pe,
		}),
	approveScan: (pid: string, did: string, b: T.ScanApprovalReq, s?: S) =>
		p<T.DocumentMap>(sub(pid, R.approveScan, { did }), b, s),
	getConceptMemory: pG(R.conceptMemory),
	upsertConceptMemory: pP(R.conceptMemory),
	getOntologyBundle: pG(R.ontology),
	startOntologyConstruct: pP(R.ontologyConstruct),
	getOntologyRun: rG(R.ontologyRun),
	controlOntologyRun: rP(R.ontologyRunControls),
	answerOntologyHold: rP(R.ontologyRunAnswers),
	fiboQaGlobal: p0(R.fiboQaGlobal),
	fiboQaProject: pP(R.ontologyFiboQa),
	resolveCitationSpan: pP(R.ontologyCitationsResolve),
	validateOntology: pP(R.ontologyValidate),
	commitOntology: pP(R.ontologyCommit),
	exportOntology: (
		pid: string,
		fmt: OntExportFmt = "yaml",
		inc = false,
		s?: S,
	) =>
		g<string>(
			sub(pid, R.ontologyExport),
			s,
			{ format: fmt, include_staged: inc },
			"text",
		),
	listRuns: (pid: string, step?: "step4" | "step6", s?: S) =>
		g<T.RunStateResp[]>(sub(pid, R.runs), s, { step }),
	buildVault: pP(R.runs),
	getRun: rG(R.run),
	getRunLogs: (pid: string, rid: string, ss = 0, limit?: number, s?: S) =>
		g<T.RunEventLogPage>(sub(pid, R.runLogs, { rid }), s, {
			since_seq: ss,
			limit,
		}),
	listVaultAttempts: rG(R.runAttempts),
	controlVaultRun: rP(R.runControls),
	submitVaultDecisions: rP(R.runDecisions),
	submitHorizonSteering: rP(R.runHorizon),
	submitVaultWaiver: pP(R.vaultWaivers),
	getGuidedView: rG(R.runGuidedView),
	getVaultIr: pG(R.vaultIr),
	verifyVault: (pid: string, s?: S) =>
		p<T.VaultVerifyResp>(sub(pid, R.vaultVerify), undefined, s),
	listReceipts: pG(R.receipts),
	listStorageTargets: pG(R.storageTargets),
	compileStorageTarget: pP(R.storageCompile),
	migrateStorageTarget: pP(R.storageMigrate),
	regenerateStorageTarget: pP(R.storageRegenerate),
	queryStorage: pP(R.storageQuery),
};
