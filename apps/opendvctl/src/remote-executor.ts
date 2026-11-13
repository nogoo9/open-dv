/**
 * REST & SSE Execution Coordinator (`RemoteExecutor`).
 *
 * Implements:
 * - Mandatory API endpoint verification (REQ-CFG-01).
 * - Contract version guard against `GET /api/v1/health` (REQ-FAIL-01).
 * - Decision D1 inline uploads attached to text contract requests (skipped for binary Studio uploads).
 * - Decision D2 async pipeline runs & Studio jobs (`--wait` polling / `--stream` SSE).
 * - Command dispatch across all platform API endpoints.
 */

import { createHash, randomUUID } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { OpenDVPlatformClient } from "@opendv/protocol-ts";
import { CliDomainError, errorHandler } from "./error-handler";
import { executeConfigCommand, parseHttpErrorResponse } from "./http-executor";
import { processInlineUploads } from "./inline-uploader";
import { dumpsJcs } from "./jcs";
import {
	type CliCommand,
	type CliOption,
	type CliSubcommand,
	loadCliSpec,
} from "./spec";
import {
	StudioSsePrematureEofError,
	followEventStream,
	followStudioJobEvents,
} from "./sse-follower";

export interface RemoteExecutorConfig {
	baseUrl?: string | undefined;
	role?: string | undefined;
	explicitRole?: boolean | undefined;
	fetchImpl?: typeof fetch | undefined;
	writer?: ((msg: string) => void) | undefined;
	errWriter?: ((msg: string) => void) | undefined;
	pollIntervalMs?: number | undefined;
}

const ADMIN_ROLE_NAMES = new Set([
	"ADMIN",
	"DATA_STEWARD",
	"RESTRICTED_PII_CUSTODIAN",
]);

export const DESTRUCTIVE_OP_TYPES = new Set([
	"remove_concept",
	"remove_relationship",
	"remove_attribute",
	"update_concept_urn",
	"change_relationship_cardinality",
	"reparent_concept",
	"retype_attribute",
	"split_concept",
	"merge_concepts",
]);

export function isDestructiveDeltaOperation(
	op: Record<string, unknown>,
	newlyAddedConceptUris?: ReadonlySet<string>,
	baseConceptsByUri?: ReadonlyMap<string, Record<string, unknown>>,
): boolean {
	const opType = normalizeOpType(op.op_type ?? op.op);
	if (DESTRUCTIVE_OP_TYPES.has(opType)) {
		return true;
	}
	if (opType === "add_attribute" && op.is_optional === false) {
		const conceptUri = String(op.concept_uri ?? "").trim();
		if (!conceptUri || !newlyAddedConceptUris?.has(conceptUri)) {
			return true;
		}
	}
	if (opType === "set_parent_concept") {
		const conceptUri = String(op.concept_uri ?? "").trim();
		const existing = conceptUri ? baseConceptsByUri?.get(conceptUri) : undefined;
		if (op.parent_concept_uri == null) {
			if (!baseConceptsByUri || existing?.parent_concept_uri != null) {
				return true;
			}
		} else if (
			existing?.parent_concept_uri != null &&
			existing.parent_concept_uri !== op.parent_concept_uri
		) {
			return true;
		}
	}
	if (opType === "set_concept_elm_archetype") {
		const conceptUri = String(op.concept_uri ?? "").trim();
		const existing = conceptUri ? baseConceptsByUri?.get(conceptUri) : undefined;
		if (
			existing?.elm_archetype != null &&
			existing.elm_archetype !== op.elm_archetype
		) {
			return true;
		}
	}
	return false;
}

function normalizeOpType(rawType: unknown): string {
	const str = String(rawType ?? "").trim();
	if (!str) return "";
	if (str === "AddConceptOp") return "add_concept";
	if (str.endsWith("Op") && str.length > 2) {
		const base = str.slice(0, -2);
		return base.replace(/([a-z0-9])([A-Z])/g, "$1_$2").toLowerCase();
	}
	return str;
}

function normalizeDeltaOperations(
	rawOps: unknown,
): Array<Record<string, unknown>> {
	if (!Array.isArray(rawOps)) {
		return [];
	}
	return rawOps.map((item, idx) => {
		if (typeof item !== "object" || item === null || Array.isArray(item)) {
			return {} as Record<string, unknown>;
		}
		const opDict: Record<string, unknown> = {
			...(item as Record<string, unknown>),
		};
		const resolvedType = normalizeOpType(opDict.op_type ?? opDict.op);
		delete opDict.op;
		if (resolvedType) {
			opDict.op_type = resolvedType;
		}
		if (!opDict.op_id) {
			opDict.op_id = `op-${idx + 1}-${randomUUID().replace(/-/g, "").slice(0, 8)}`;
		}
		if (
			opDict.op_type === "add_concept" &&
			typeof opDict.concept === "object" &&
			opDict.concept !== null &&
			!Array.isArray(opDict.concept)
		) {
			const conceptFields = {
				...(opDict.concept as Record<string, unknown>),
			};
			delete opDict.concept;
			return {
				...conceptFields,
				...opDict,
				op_type: "add_concept",
			};
		}
		return opDict;
	});
}

function computeCanonicalAnswersSha256(items: unknown[]): string {
	if (!items || items.length === 0) {
		return createHash("sha256").update(dumpsJcs([]), "utf-8").digest("hex");
	}
	const cleaned = items
		.filter(
			(it): it is Record<string, unknown> =>
				typeof it === "object" && it !== null && !Array.isArray(it),
		)
		.map((it) => {
			const row: Record<string, unknown> = {};
			for (const [k, v] of Object.entries(it)) {
				if (v !== null && v !== undefined) {
					row[k] = v;
				}
			}
			return row;
		})
		.sort((a, b) =>
			String(a.question_id ?? "").localeCompare(String(b.question_id ?? "")),
		);
	return createHash("sha256").update(dumpsJcs(cleaned), "utf-8").digest("hex");
}

function normalizeStringArrayOption(val: unknown): string[] {
	if (val === undefined || val === null) {
		return [];
	}
	const rawItems = Array.isArray(val) ? val : [val];
	const out: string[] = [];
	for (const item of rawItems) {
		for (const part of String(item).split(",")) {
			const trimmed = part.trim();
			if (trimmed) out.push(trimmed);
		}
	}
	return out;
}

export class RemoteExecutor {
	private readonly baseUrl: string;
	private readonly role: string;
	private readonly explicitRole: boolean;
	private readonly fetchImpl: typeof fetch;
	private readonly writer: (msg: string) => void;
	private readonly errWriter: (msg: string) => void;
	private readonly pollIntervalMs: number;
	private readonly client: OpenDVPlatformClient;

	constructor(config: RemoteExecutorConfig = {}) {
		const rawUrl =
			config.baseUrl ||
			(typeof process !== "undefined" ? process.env.OPENDV_API_URL : undefined);

		if (!rawUrl || rawUrl.trim() === "" || rawUrl === "undefined") {
			throw new CliDomainError(
				"INVALID_REQUEST",
				"Missing required --api-url flag or OPENDV_API_URL environment variable.",
				400,
			);
		}

		this.baseUrl = rawUrl.replace(/\/+$/, "");
		const envRole =
			typeof process !== "undefined" && process.env.OPENDV_ROLE
				? process.env.OPENDV_ROLE
				: undefined;
		this.explicitRole =
			config.explicitRole !== undefined
				? config.explicitRole
				: Boolean(
						(config.role && config.role !== "INTERNAL_MODELER") ||
							(envRole && envRole.trim() !== "" && envRole !== "undefined"),
					);
		this.role = config.role || envRole || "INTERNAL_MODELER";
		this.fetchImpl = config.fetchImpl ?? globalThis.fetch.bind(globalThis);
		this.writer = config.writer ?? ((s) => console.log(s));
		this.errWriter = config.errWriter ?? ((s) => console.error(s));
		this.pollIntervalMs = config.pollIntervalMs ?? 50;

		this.client = new OpenDVPlatformClient({
			baseUrl: this.baseUrl,
			rbacRole: this.role as
				| "PUBLIC"
				| "INTERNAL_MODELER"
				| "DATA_STEWARD"
				| "AUDITOR_FINANCE"
				| "RESTRICTED_PII_CUSTODIAN",
			fetchImpl: this.fetchImpl,
		});
	}

	getBaseUrl(): string {
		return this.baseUrl;
	}

	private isLoopback(): boolean {
		try {
			const u = new URL(this.baseUrl);
			return (
				u.hostname === "127.0.0.1" ||
				u.hostname === "localhost" ||
				u.hostname === "::1" ||
				u.hostname === "[::1]"
			);
		} catch {
			return false;
		}
	}

	private isInsecureHttp(): boolean {
		try {
			const u = new URL(this.baseUrl);
			return u.protocol === "http:" && !this.isLoopback();
		} catch {
			return false;
		}
	}

	private assertLoopbackForServerPath(
		flagName: string,
		pathValue: unknown,
	): void {
		if (
			pathValue !== undefined &&
			pathValue !== null &&
			String(pathValue).trim() !== "" &&
			!this.isLoopback()
		) {
			throw new CliDomainError(
				"REMOTE_SERVER_PATH_FORBIDDEN",
				`${flagName} specifies a server-local filesystem path and is forbidden against non-loopback remote endpoints (${this.baseUrl}).`,
				400,
			);
		}
	}

	private async resolveWaiverHeaders(
		options: Record<string, unknown>,
	): Promise<{
		low_confidence_waiver: boolean;
		grounding_rationale?: string;
		headers: Record<string, string>;
	}> {
		const rawRationale =
			(typeof options.grounding_rationale === "string" &&
			options.grounding_rationale.trim() !== ""
				? options.grounding_rationale
				: undefined) ??
			(typeof options.waiver_rationale === "string" &&
			options.waiver_rationale.trim() !== ""
				? options.waiver_rationale
				: (options.grounding_rationale ?? options.waiver_rationale));

		if (!options.low_confidence_waiver) {
			return {
				low_confidence_waiver: false,
				...(typeof rawRationale === "string" && rawRationale.trim() !== ""
					? { grounding_rationale: rawRationale.trim() }
					: {}),
				headers: {},
			};
		}

		if (
			this.isInsecureHttp() &&
			process.env.OPENDV_ALLOW_INSECURE_HTTP !== "true"
		) {
			throw new CliDomainError(
				"INSECURE_TRANSPORT_REFUSED",
				"Refusing to transmit privileged steward waiver credentials over plaintext HTTP to non-loopback endpoint. Use HTTPS or set OPENDV_ALLOW_INSECURE_HTTP=true.",
				403,
			);
		}

		// Check caller role BEFORE rationale length (`P13::MAJ-01`, `MAJ-03`):
		// if an explicit non-admin role was provided, fail immediately with exit 7.
		const normalizedRole = (this.role || "INTERNAL_MODELER").toUpperCase();
		if (this.explicitRole && !ADMIN_ROLE_NAMES.has(normalizedRole)) {
			throw new CliDomainError(
				"FORBIDDEN_ADMIN_REQUIRED",
				`Administrative grounding waiver requires admin/steward role clearance (got '${this.role}').`,
				403,
			);
		}
		if (
			options.authorized_role &&
			!ADMIN_ROLE_NAMES.has(String(options.authorized_role).toUpperCase())
		) {
			throw new CliDomainError(
				"FORBIDDEN_ADMIN_REQUIRED",
				`Authorized waiver role '${options.authorized_role}' is not an administrator/steward role.`,
				403,
			);
		}

		let waiverKey = "";
		const keyFile = options.waiver_key_file;
		if (keyFile && typeof keyFile === "string") {
			let rawContent: string;
			try {
				const fs = await import("node:fs/promises");
				rawContent = await fs.readFile(keyFile, "utf-8");
			} catch (err: unknown) {
				if (
					(err instanceof Error &&
						"code" in err &&
						(err as { code: string }).code === "ENOENT") ||
					String(err).includes("ENOENT")
				) {
					throw new CliDomainError(
						"FILE_NOT_FOUND",
						`Waiver key file not found: ${keyFile}`,
						404,
					);
				}
				throw new CliDomainError(
					"SYNTAX_INVALID_ARGUMENT",
					`Failed to read waiver key file '${keyFile}': ${err}`,
					400,
				);
			}
			waiverKey = rawContent.trim();
			if (!waiverKey) {
				throw new CliDomainError(
					"FORBIDDEN_ADMIN_REQUIRED",
					`Waiver key file '${keyFile}' is empty.`,
					403,
				);
			}
		} else {
			// Read OPENDV_WAIVER_KEY strictly as a raw secret (never as a file path!)
			waiverKey = (process.env.OPENDV_WAIVER_KEY || "").trim();
		}

		const baseHeaders = this.getHeaders();
		const hasRemoteBearerAuth =
			!this.isLoopback() &&
			Boolean(baseHeaders.Authorization || baseHeaders.authorization);

		// On non-loopback remote endpoints with an RS256 Bearer JWT, do not block remote admins
		// client-side for lacking a local workstation waiver key file; on loopback or without
		// remote Bearer auth, require local waiver credentials first (exit 7).
		if (!waiverKey && !hasRemoteBearerAuth) {
			throw new CliDomainError(
				"FORBIDDEN_ADMIN_REQUIRED",
				"Low-confidence waiver requires steward credentials via --waiver-key-file, OPENDV_WAIVER_KEY, or remote admin Authorization token.",
				403,
			);
		}

		const rationale = rawRationale;
		if (!rationale || String(rationale).trim().length < 10) {
			throw new CliDomainError(
				"INVALID_GROUNDING_WAIVER",
				"Low-confidence grounding waiver requires a rationale of at least 10 characters.",
				422,
			);
		}

		const headers: Record<string, string> = {};
		if (waiverKey) {
			headers["X-OpenDV-Waiver-Key"] = waiverKey
				.replace(/^Bearer\s+/i, "")
				.trim();
		}

		return {
			low_confidence_waiver: true,
			grounding_rationale: String(rationale).trim(),
			headers,
		};
	}

	getHeaders(extraHeaders?: Record<string, string>): Record<string, string> {
		const normalizedRole = this.role
			? this.role.toUpperCase()
			: "INTERNAL_MODELER";
		const allowedRoles = new Set([
			"PUBLIC",
			"INTERNAL_MODELER",
			"DATA_STEWARD",
			"RESTRICTED_PII_CUSTODIAN",
			"AUDITOR_FINANCE",
			"ADMIN",
			"EDITOR",
			"VIEWER",
			"AUTOMATED",
		]);
		if (!allowedRoles.has(normalizedRole)) {
			throw new CliDomainError(
				"SYNTAX_INVALID_ARGUMENT",
				`Invalid or unsupported role: ${this.role}. Supported roles: PUBLIC, INTERNAL_MODELER, DATA_STEWARD, RESTRICTED_PII_CUSTODIAN, AUDITOR_FINANCE, ADMIN, EDITOR, VIEWER, AUTOMATED`,
				400,
			);
		}

		const headers: Record<string, string> = {
			"Content-Type": "application/json",
			Accept: "application/json",
			"X-OpenDV-Role": normalizedRole,
			...extraHeaders,
		};

		if (!headers.Authorization && !headers.authorization) {
			if (this.isLoopback()) {
				// Loopback: OPENDV_STUDIO_TOKEN -> OPENDV_TOKEN -> OPENDV_STUDIO_DIR/.local_token
				const studioToken = (process.env.OPENDV_STUDIO_TOKEN || "").trim();
				const genericToken = (process.env.OPENDV_TOKEN || "").trim();
				if (studioToken) {
					headers.Authorization = `Bearer ${studioToken}`;
				} else if (genericToken) {
					headers.Authorization = `Bearer ${genericToken}`;
				} else if (process.env.OPENDV_STUDIO_DIR) {
					const tokenPath = path.join(
						process.env.OPENDV_STUDIO_DIR,
						".local_token",
					);
					if (existsSync(tokenPath)) {
						try {
							const tok = readFileSync(tokenPath, "utf-8").trim();
							if (tok) {
								headers.Authorization = `Bearer ${tok}`;
							}
						} catch {
							// ignore unreadable token
						}
					}
				}
			} else {
				// Remote endpoints: ONLY generic OPENDV_TOKEN (never leak local workstation OPENDV_STUDIO_TOKEN or .local_token)
				const remoteToken = (process.env.OPENDV_TOKEN || "").trim();
				if (remoteToken) {
					if (
						this.isInsecureHttp() &&
						process.env.OPENDV_ALLOW_INSECURE_HTTP !== "true"
					) {
						// Refuse to attach bearer credentials over plaintext http to remote hosts without opt-in
					} else {
						headers.Authorization = `Bearer ${remoteToken}`;
					}
				}
			}
		}
		return headers;
	}

	/**
	 * REQ-FAIL-01: Probes /api/v1/health and asserts server contract_version === client contract_version.
	 */
	async verifyContractVersion(): Promise<void> {
		const spec = loadCliSpec();
		let health: { contract_version?: unknown; [key: string]: unknown };
		try {
			health = (await this.client.getHealth()) as Record<string, unknown>;
		} catch (err) {
			const msg = err instanceof Error ? err.message : String(err);
			throw new CliDomainError(
				"API_UNREACHABLE",
				`Failed to reach Open-DV Platform API at ${this.baseUrl}: ${msg}`,
				503,
			);
		}

		const serverVersion = String(health.contract_version ?? "unknown");
		if (serverVersion !== spec.contract_version) {
			throw new CliDomainError(
				"CONTRACT_VERSION_MISMATCH",
				`Contract version mismatch (client: ${spec.contract_version}, server: ${serverVersion})`,
				422,
			);
		}
	}

	private async parseErrorResponse(res: Response): Promise<CliDomainError> {
		return parseHttpErrorResponse(res);
	}

	private async postJson(
		endpoint: string,
		payload: unknown,
		extraHeaders?: Record<string, string>,
	): Promise<unknown> {
		const res = await this.fetchImpl(`${this.baseUrl}${endpoint}`, {
			method: "POST",
			headers: this.getHeaders(extraHeaders),
			body: JSON.stringify(payload),
		});

		if (!res.ok) {
			throw await this.parseErrorResponse(res);
		}

		return res.json();
	}

	private async getJson(
		endpoint: string,
		extraHeaders?: Record<string, string>,
	): Promise<unknown> {
		const res = await this.fetchImpl(`${this.baseUrl}${endpoint}`, {
			headers: this.getHeaders(extraHeaders),
		});

		if (!res.ok) {
			throw await this.parseErrorResponse(res);
		}

		return res.json();
	}

	private async deleteRequest(
		endpoint: string,
		extraHeaders?: Record<string, string>,
	): Promise<unknown> {
		const res = await this.fetchImpl(`${this.baseUrl}${endpoint}`, {
			method: "DELETE",
			headers: this.getHeaders(extraHeaders),
		});

		if (!res.ok) {
			throw await this.parseErrorResponse(res);
		}

		if (res.status === 204) {
			return null;
		}
		const text = await res.text();
		if (!text.trim()) {
			return null;
		}
		return JSON.parse(text);
	}

	private async postFormData(
		endpoint: string,
		formData: FormData,
		extraHeaders?: Record<string, string>,
	): Promise<unknown> {
		const headers = { ...this.getHeaders(extraHeaders) };
		delete headers["Content-Type"];
		delete headers["content-type"];

		const res = await this.fetchImpl(`${this.baseUrl}${endpoint}`, {
			method: "POST",
			headers,
			body: formData,
		});

		if (!res.ok) {
			throw await this.parseErrorResponse(res);
		}

		return res.json();
	}

	private throwIfFailedJobRecord(jobObj: Record<string, unknown>): void {
		const statusUpper = String(jobObj.status ?? "").toUpperCase();
		if (statusUpper === "FAILED") {
			const errObj =
				typeof jobObj.error === "object" && jobObj.error !== null
					? (jobObj.error as Record<string, unknown>)
					: jobObj;
			const code = String(
				errObj.code || errObj.error_code || "INTERNAL_ERROR",
			);
			const detail = String(
				errObj.detail || errObj.message || "Studio background job failed.",
			);
			const httpStatus =
				typeof errObj.status === "number" ? errObj.status : undefined;
			throw new CliDomainError(code, detail, httpStatus);
		}
		if (statusUpper === "CANCELLED") {
			const errObj =
				typeof jobObj.error === "object" && jobObj.error !== null
					? (jobObj.error as Record<string, unknown>)
					: {};
			const detail = String(
				errObj.detail || errObj.message || "Studio background job was cancelled.",
			);
			throw new CliDomainError("JOB_ALREADY_TERMINAL", detail, 409);
		}
	}

	private async waitForStudioJob(
		sid: string,
		initialPayload: unknown,
		options: Record<string, unknown>,
	): Promise<unknown> {
		const wantsWait = Boolean(options.wait);
		const wantsStream = Boolean(options.stream);
		if (!wantsWait && !wantsStream) {
			return initialPayload;
		}

		if (typeof initialPayload !== "object" || initialPayload === null) {
			return initialPayload;
		}

		const initObj = initialPayload as Record<string, unknown>;
		this.throwIfFailedJobRecord(initObj);

		const initStatus = String(initObj.status ?? "").toUpperCase();
		const jobId = initObj.job_id ? String(initObj.job_id) : "";
		if (!jobId) {
			return initialPayload;
		}

		const encodedSid = encodeURIComponent(sid);
		const encodedJobId = encodeURIComponent(jobId);
		const jobStatusEndpoint = `/api/v1/ontology/studio/sessions/${encodedSid}/jobs/${encodedJobId}`;

		if (wantsStream) {
			const isJsonOut =
				options.json === true || options.format === "json";
			const streamLogger = isJsonOut ? this.errWriter : this.writer;
			const streamUrl = `${this.baseUrl}${jobStatusEndpoint}/events`;

			try {
				const streamSummary = await followStudioJobEvents({
					streamUrl,
					headers: this.getHeaders(),
					streamEnabled: true,
					writer: streamLogger,
					fetchImpl: this.fetchImpl,
				});
				try {
					const finalJob = (await this.getJson(jobStatusEndpoint)) as Record<
						string,
						unknown
					>;
					this.throwIfFailedJobRecord(finalJob);
					return finalJob;
				} catch (err) {
					if (err instanceof CliDomainError && err.errorCode !== "JOB_NOT_FOUND") {
						throw err;
					}
					return { ...initObj, ...streamSummary, status: "COMPLETED" };
				}
			} catch (err) {
				if (!(err instanceof StudioSsePrematureEofError)) {
					throw err;
				}
				// Premature SSE EOF without terminal event: fall through to poll job status
				// so disconnects are never masked as COMPLETED!
			}
		}

		if (initStatus === "COMPLETED") {
			return initialPayload;
		}

		// Poll job status until terminal state with adaptive backoff + wall-clock deadline (P13 MAJ-03)
		const configuredTimeoutMs = Number(
			process.env.OPENDV_JOB_WAIT_TIMEOUT_MS || 600_000,
		);
		const maxWaitMs =
			Number.isFinite(configuredTimeoutMs) && configuredTimeoutMs > 0
				? configuredTimeoutMs
				: 600_000;
		const startedAt = Date.now();
		for (let attempt = 0; ; attempt++) {
			const jobRecord = (await this.getJson(jobStatusEndpoint)) as Record<
				string,
				unknown
			>;
			this.throwIfFailedJobRecord(jobRecord);
			const st = String(jobRecord.status ?? "").toUpperCase();
			if (st === "COMPLETED") {
				return jobRecord;
			}
			const elapsedMs = Date.now() - startedAt;
			if (elapsedMs >= maxWaitMs) {
				break;
			}
			const baseDelayMs =
				attempt < 10
					? this.pollIntervalMs
					: elapsedMs < 5_000
						? Math.max(this.pollIntervalMs, 250)
						: Math.max(this.pollIntervalMs, 1_000);
			const sleepMs = Math.min(baseDelayMs, Math.max(1, maxWaitMs - elapsedMs));
			await new Promise((resolve) => setTimeout(resolve, sleepMs));
		}

		throw new CliDomainError(
			"API_UNREACHABLE",
			`Timed out waiting for Studio job '${jobId}' in session '${sid}' to reach terminal state.`,
			503,
		);
	}

	private async fetchSessionDetail(
		sid: string,
	): Promise<Record<string, unknown>> {
		const encodedSid = encodeURIComponent(sid);
		try {
			return (await this.getJson(
				`/api/v1/ontology/studio/sessions/${encodedSid}`,
			)) as Record<string, unknown>;
		} catch (err) {
			if (
				err instanceof CliDomainError &&
				err.httpStatus === 404 &&
				err.errorCode !== "SESSION_NOT_FOUND"
			) {
				return (await this.getJson(
					`/api/v1/ontology/studio/sessions/${encodedSid}/status`,
				)) as Record<string, unknown>;
			}
			throw err;
		}
	}

	private async readLocalJsonFile(
		filePath: string,
		label: string,
	): Promise<unknown> {
		let rawText: string;
		try {
			const fs = await import("node:fs/promises");
			rawText = await fs.readFile(filePath, "utf-8");
		} catch (err: unknown) {
			if (
				(err instanceof Error &&
					"code" in err &&
					(err as { code: string }).code === "ENOENT") ||
				String(err).includes("ENOENT")
			) {
				throw new CliDomainError(
					"FILE_NOT_FOUND",
					`${label} file not found: ${filePath}`,
					404,
				);
			}
			throw new CliDomainError(
				"SYNTAX_INVALID_ARGUMENT",
				`Failed to read ${label} file '${filePath}': ${err}`,
				400,
			);
		}

		const trimmed = rawText.trim();
		if (!trimmed) {
			throw new CliDomainError(
				"SYNTAX_INVALID_ARGUMENT",
				`${label} file '${filePath}' is empty.`,
				400,
			);
		}

		try {
			return JSON.parse(trimmed);
		} catch (err) {
			const yamlApi = (
				Bun as typeof Bun & { YAML?: { parse: (s: string) => unknown } }
			).YAML;
			if (
				yamlApi &&
				(filePath.endsWith(".yaml") || filePath.endsWith(".yml"))
			) {
				try {
					return yamlApi.parse(trimmed);
				} catch {
					// fall through
				}
			}
			throw new CliDomainError(
				"SYNTAX_INVALID_ARGUMENT",
				`Malformed ${label} JSON in '${filePath}': ${err}`,
				400,
			);
		}
	}

	async execute(
		command: string,
		subcommand: string | undefined,
		rawOptions: Record<string, unknown>,
	): Promise<{ exitCode: number; payload: unknown }> {
		// 1. Contract version guard
		await this.verifyContractVersion();

		// 2. Skip text `processInlineUploads` on `ontology studio` commands (`MAJ-04`)
		// so binary PDFs, Parquet, Office, and .odvstudio.zip archives are never UTF-8 decoded.
		const isStudioCommand =
			command === "ontology" &&
			Boolean(subcommand && subcommand.toLowerCase().startsWith("studio"));

		let enrichedOptions: Record<string, unknown>;
		if (isStudioCommand) {
			enrichedOptions = { ...rawOptions };
		} else {
			const spec = loadCliSpec();
			const cmdDef = spec.commands.find((c) => c.name === command);
			const specOptions: CliOption[] = [...spec.global_options];
			if (cmdDef?.options) {
				specOptions.push(...cmdDef.options);
			}
			if (subcommand && cmdDef?.subcommands) {
				const subTokens = subcommand.split(" ");
				let currentCmd: CliCommand | CliSubcommand | undefined = cmdDef;
				for (const tok of subTokens) {
					if (currentCmd?.subcommands) {
						const found: CliSubcommand | undefined = currentCmd.subcommands.find(
							(s) => s.name === tok || (s.aliases && s.aliases.includes(tok)),
						);
						if (found) {
							if (found.options) {
								specOptions.push(...found.options);
							}
							currentCmd = found;
						} else {
							break;
						}
					}
				}
			}
			enrichedOptions = await processInlineUploads(rawOptions, specOptions);
		}

		let payload: unknown;

		switch (command) {
			case "profile": {
				const body: Record<string, unknown> = {
					source_system:
						(enrichedOptions.source_system as string) || "BRONZE_SOURCE",
				};
				if (enrichedOptions.source) body.source = enrichedOptions.source;
				if (enrichedOptions.source_path || enrichedOptions.source_csv_path) {
					body.source_path =
						enrichedOptions.source_path || enrichedOptions.source_csv_path;
				}
				if (
					enrichedOptions.file_content ||
					enrichedOptions.source_csv_content
				) {
					body.file_content =
						enrichedOptions.file_content || enrichedOptions.source_csv_content;
				}
				if (enrichedOptions.client_path || enrichedOptions.file_path) {
					body.client_path =
						enrichedOptions.client_path || enrichedOptions.file_path;
				}
				if (enrichedOptions.graph_b_path)
					body.graph_b_path = enrichedOptions.graph_b_path;
				if (enrichedOptions.graph_b_content)
					body.graph_b_content = enrichedOptions.graph_b_content;
				if (enrichedOptions.project_id)
					body.project_id = enrichedOptions.project_id;
				if (enrichedOptions.table_name) {
					body.table_name = enrichedOptions.table_name;
				} else if (body.client_path || body.source_path) {
					const p = String(body.client_path || body.source_path);
					const base = p.split(/[\\/]/).pop() ?? "";
					const stem = base.replace(/\.[^/.]+$/, "");
					if (stem) body.table_name = stem;
				}
				payload = await this.postJson("/api/v1/profile", body);
				break;
			}
			case "compile": {
				const body: Record<string, unknown> = {};
				if (enrichedOptions.contract_path)
					body.contract_path = enrichedOptions.contract_path;
				if (enrichedOptions.contract_content)
					body.contract_content = enrichedOptions.contract_content;
				if (enrichedOptions.canonical_ir_path)
					body.canonical_ir_path = enrichedOptions.canonical_ir_path;
				if (enrichedOptions.canonical_ir_content)
					body.canonical_ir_content = enrichedOptions.canonical_ir_content;
				if (enrichedOptions.run_id) body.run_id = enrichedOptions.run_id;
				if (enrichedOptions.project_id)
					body.project_id = enrichedOptions.project_id;
				payload = await this.postJson("/api/v1/compile", body);
				break;
			}
			case "verify": {
				const body: Record<string, unknown> = {
					apply_codemods: enrichedOptions.apply_codemods !== false,
				};
				if (enrichedOptions.contract_path)
					body.contract_path = enrichedOptions.contract_path;
				if (enrichedOptions.contract_content)
					body.contract_content = enrichedOptions.contract_content;
				if (enrichedOptions.graph_a_path)
					body.graph_a_path = enrichedOptions.graph_a_path;
				if (enrichedOptions.graph_a_content)
					body.graph_a_content = enrichedOptions.graph_a_content;
				if (enrichedOptions.graph_b_path)
					body.graph_b_path = enrichedOptions.graph_b_path;
				if (enrichedOptions.graph_b_content)
					body.graph_b_content = enrichedOptions.graph_b_content;
				if (enrichedOptions.prior_contract_path)
					body.prior_contract_path = enrichedOptions.prior_contract_path;
				if (enrichedOptions.prior_contract_content)
					body.prior_contract_content = enrichedOptions.prior_contract_content;
				if (enrichedOptions.compiled_sql)
					body.compiled_sql = enrichedOptions.compiled_sql;
				if (enrichedOptions.compiled_sql_models)
					body.compiled_sql_models = enrichedOptions.compiled_sql_models;
				if (enrichedOptions.run_id) body.run_id = enrichedOptions.run_id;
				if (enrichedOptions.project_id)
					body.project_id = enrichedOptions.project_id;
				payload = await this.postJson("/api/v1/verify", body);
				break;
			}
			case "sandbox": {
				const runId = enrichedOptions.run_id as string | undefined;
				if (runId) {
					payload = await this.getJson(
						`/api/v1/runs/${encodeURIComponent(runId)}/sandbox`,
					);
				} else {
					const body: Record<string, unknown> = {
						query_type:
							enrichedOptions.query_type ?? "ADVERSARIAL_BATCHES_1_TO_4",
						assertion_cutoff_ts:
							enrichedOptions.cutoff ??
							enrichedOptions.assertion_cutoff_ts ??
							"2026-11-15 23:59:59",
					};
					if (enrichedOptions.contract_path)
						body.contract_path = enrichedOptions.contract_path;
					if (enrichedOptions.contract_content)
						body.contract_content = enrichedOptions.contract_content;
					if (enrichedOptions.canonical_ir_path)
						body.canonical_ir_path = enrichedOptions.canonical_ir_path;
					if (enrichedOptions.canonical_ir_content)
						body.canonical_ir_content = enrichedOptions.canonical_ir_content;
					if (enrichedOptions.sql) body.sql = enrichedOptions.sql;
					payload = await this.postJson("/api/v1/sandbox", body);
				}
				break;
			}
			case "pipeline": {
				if (subcommand === "validate") {
					const body: Record<string, unknown> = {};
					if (enrichedOptions.input_spec_path || enrichedOptions.input) {
						body.input_spec_path =
							enrichedOptions.input_spec_path || enrichedOptions.input;
					}
					if (enrichedOptions.input_spec_content) {
						if (typeof enrichedOptions.input_spec_content === "object") {
							body.input_spec = enrichedOptions.input_spec_content;
						} else if (typeof enrichedOptions.input_spec_content === "string") {
							try {
								body.input_spec = JSON.parse(
									enrichedOptions.input_spec_content,
								);
							} catch {
								// Non-JSON input spec (e.g. YAML) resolved via input_spec_path
							}
						}
					}
					if (enrichedOptions.initial_contract_path)
						body.initial_contract_path = enrichedOptions.initial_contract_path;
					if (enrichedOptions.initial_contract_content)
						body.initial_contract = enrichedOptions.initial_contract_content;
					if (enrichedOptions.wave_id) body.wave_id = enrichedOptions.wave_id;
					if (enrichedOptions.provider)
						body.provider_override = enrichedOptions.provider;
					if (enrichedOptions.hold_policy)
						body.steward_hold_policy = enrichedOptions.hold_policy;
					payload = await this.postJson("/api/v1/pipelines/validate", body);
				} else if (subcommand === "run") {
					const body: Record<string, unknown> = {};
					if (enrichedOptions.input_spec_path || enrichedOptions.input) {
						body.input_spec_path =
							enrichedOptions.input_spec_path || enrichedOptions.input;
					}
					if (enrichedOptions.input_spec_content) {
						if (typeof enrichedOptions.input_spec_content === "object") {
							body.input_spec = enrichedOptions.input_spec_content;
						} else if (typeof enrichedOptions.input_spec_content === "string") {
							try {
								body.input_spec = JSON.parse(
									enrichedOptions.input_spec_content,
								);
							} catch {
								// Non-JSON input spec (e.g. YAML) resolved via input_spec_path
							}
						}
					}
					if (enrichedOptions.initial_contract_path)
						body.initial_contract_path = enrichedOptions.initial_contract_path;
					if (enrichedOptions.initial_contract_content)
						body.initial_contract = enrichedOptions.initial_contract_content;
					if (enrichedOptions.wave_id) body.wave_id = enrichedOptions.wave_id;
					if (enrichedOptions.provider)
						body.provider_override = enrichedOptions.provider;
					if (enrichedOptions.hold_policy)
						body.steward_hold_policy = enrichedOptions.hold_policy;
					if (enrichedOptions.project_id)
						body.project_id = enrichedOptions.project_id;
					if (enrichedOptions.output_dir)
						body.output_dir = enrichedOptions.output_dir;

					// Async run lifecycle (Decision D2)
					const runHandle = (await this.postJson(
						"/api/v1/pipelines/runs",
						body,
					)) as {
						run_id: string;
						event_stream_url?: string;
						events_sse_url?: string;
					};

					const streamUrl =
						runHandle.events_sse_url ||
						runHandle.event_stream_url ||
						`${this.baseUrl}/api/v1/runs/${encodeURIComponent(runHandle.run_id)}/events`;

					// Follow live SSE stream
					const streamResult = await followEventStream({
						streamUrl,
						headers: this.getHeaders(),
						streamEnabled: enrichedOptions.stream !== false,
						tokensEnabled: Boolean(enrichedOptions.stream_tokens),
						writer: this.writer,
						fetchImpl: this.fetchImpl,
					});

					// Upon completion, fetch final report if available
					try {
						const report = await this.getJson(
							`/api/v1/runs/${encodeURIComponent(runHandle.run_id)}/report`,
						);
						payload = report;
					} catch {
						payload = { ...runHandle, ...streamResult };
					}
				} else {
					throw new CliDomainError(
						"INVALID_REQUEST",
						`Unsupported pipeline subcommand: ${subcommand}`,
						400,
					);
				}
				break;
			}
			case "run": {
				const runId = enrichedOptions.run_id as string | undefined;
				if (subcommand === "list") {
					payload = await this.client.listRuns();
				} else if (subcommand === "get") {
					if (!runId) throw new Error("Missing --run-id");
					payload = await this.client.getRun(runId);
				} else if (subcommand === "create") {
					payload = await this.client.createRun(
						enrichedOptions as unknown as Parameters<
							OpenDVPlatformClient["createRun"]
						>[0],
					);
				} else if (subcommand === "abort") {
					if (!runId) throw new Error("Missing --run-id");
					payload = await this.postJson(
						`/api/v1/runs/${encodeURIComponent(runId)}/abort`,
						{},
					);
				} else if (subcommand === "events") {
					if (!runId) throw new Error("Missing --run-id");
					payload = await this.getJson(
						`/api/v1/runs/${encodeURIComponent(runId)}/events`,
					);
				} else if (subcommand === "explorer") {
					if (!runId) throw new Error("Missing --run-id");
					payload = await this.getJson(
						`/api/v1/runs/${encodeURIComponent(runId)}/explorer`,
					);
				} else if (subcommand === "artifact") {
					const artifactId = (enrichedOptions.sha256 ||
						enrichedOptions.artifact_id) as string;
					if (!artifactId) throw new Error("Missing --sha256 or --artifact-id");
					payload = await this.client.getArtifact(artifactId);
				} else {
					throw new CliDomainError(
						"INVALID_REQUEST",
						`Unknown run subcommand: ${subcommand}`,
						400,
					);
				}
				break;
			}
			case "hold": {
				const runId = enrichedOptions.run_id as string | undefined;
				if (subcommand === "list") {
					if (!runId) throw new Error("Missing --run-id");
					payload = await this.getJson(
						`/api/v1/runs/${encodeURIComponent(runId)}/holds`,
					);
				} else if (
					subcommand === "approve" ||
					subcommand === "reject" ||
					subcommand === "answer" ||
					subcommand === "decide"
				) {
					if (!runId) throw new Error("Missing --run-id");
					const rawAction =
						subcommand === "decide"
							? (enrichedOptions.action as string | undefined)?.toUpperCase()
							: subcommand.toUpperCase();
					if (
						!rawAction ||
						(rawAction !== "APPROVE" &&
							rawAction !== "REJECT" &&
							rawAction !== "ANSWER")
					) {
						throw new CliDomainError(
							"INVALID_REQUEST",
							"Hold decision action must be APPROVE, REJECT, or ANSWER",
							400,
						);
					}
					const action: "APPROVE" | "REJECT" | "ANSWER" = rawAction;

					payload = await this.client.submitDecision(runId, {
						action,
						decision_type: "RESOLVE_CAPTAIN_HOLD",
						steward_id: "steward-captain",
						hold_id: (enrichedOptions.hold_id as string | undefined) ?? null,
						selected_candidate_id:
							(enrichedOptions.selected_candidate_id as string | undefined) ??
							null,
						resolution_notes:
							(enrichedOptions.resolution_notes as string | undefined) ?? null,
					});
				} else {
					throw new CliDomainError(
						"INVALID_REQUEST",
						`Unknown hold subcommand: ${subcommand}`,
						400,
					);
				}
				break;
			}
			case "ontology": {
				if (!subcommand) {
					throw new CliDomainError(
						"INVALID_REQUEST",
						"Missing ontology subcommand (suggest, validate-delta, diff, accept, reject, studio)",
						400,
					);
				}
				const sub = subcommand.toLowerCase();
				if (sub.startsWith("studio")) {
					payload = await this.executeStudioCommand(sub, enrichedOptions);
				} else if (sub === "suggest") {
					const body: Record<string, unknown> = {};
					if (enrichedOptions.base)
						body.base_ontology_path = enrichedOptions.base;
					if (enrichedOptions.base_content)
						body.base_ontology_content = enrichedOptions.base_content;
					if (enrichedOptions.base_ontology_content)
						body.base_ontology_content = enrichedOptions.base_ontology_content;
					if (enrichedOptions.profile)
						body.empirical_profile_path = enrichedOptions.profile;
					if (enrichedOptions.profile_content)
						body.empirical_profile_content = enrichedOptions.profile_content;
					if (enrichedOptions.empirical_profile_content)
						body.empirical_profile_content =
							enrichedOptions.empirical_profile_content;
					if (enrichedOptions.source)
						body.bronze_source_path = enrichedOptions.source;
					if (enrichedOptions.source_content)
						body.bronze_source_content = enrichedOptions.source_content;
					if (enrichedOptions.bronze_source_content)
						body.bronze_source_content = enrichedOptions.bronze_source_content;
					if (enrichedOptions.context_doc)
						body.context_doc_path = enrichedOptions.context_doc;
					if (enrichedOptions.context_doc_content)
						body.context_doc_content = enrichedOptions.context_doc_content;
					if (enrichedOptions.mode) body.mode = enrichedOptions.mode;
					if (enrichedOptions.domain_namespace)
						body.domain_namespace = enrichedOptions.domain_namespace;
					if (enrichedOptions.recommender_id)
						body.recommender_id = enrichedOptions.recommender_id;

					payload = await this.postJson("/api/v1/ontology/suggestions", body);

					if (
						enrichedOptions.output &&
						typeof enrichedOptions.output === "string"
					) {
						const fs = await import("node:fs/promises");
						await fs.writeFile(
							enrichedOptions.output,
							JSON.stringify(payload, null, 2),
							"utf-8",
						);
					}
				} else if (sub === "validate-delta") {
					const body: Record<string, unknown> = {};
					if (enrichedOptions.delta) body.delta_path = enrichedOptions.delta;
					if (enrichedOptions.delta_content)
						body.delta_content = enrichedOptions.delta_content;
					if (enrichedOptions.base)
						body.base_ontology_path = enrichedOptions.base;
					if (enrichedOptions.base_content)
						body.base_ontology_content = enrichedOptions.base_content;
					if (enrichedOptions.base_ontology_content)
						body.base_ontology_content = enrichedOptions.base_ontology_content;
					if (enrichedOptions.contract)
						body.contract_path = enrichedOptions.contract;
					if (enrichedOptions.contract_content)
						body.contract_content = enrichedOptions.contract_content;

					payload = await this.postJson(
						"/api/v1/ontology/validate-delta",
						body,
					);
				} else if (sub === "diff") {
					const body: Record<string, unknown> = {};
					if (enrichedOptions.base)
						body.base_ontology_path = enrichedOptions.base;
					if (enrichedOptions.base_content)
						body.base_ontology_content = enrichedOptions.base_content;
					if (enrichedOptions.base_ontology_content)
						body.base_ontology_content = enrichedOptions.base_ontology_content;
					if (enrichedOptions.target)
						body.target_ontology_path = enrichedOptions.target;
					if (enrichedOptions.target_content)
						body.target_ontology_content = enrichedOptions.target_content;
					if (enrichedOptions.target_ontology_content)
						body.target_ontology_content =
							enrichedOptions.target_ontology_content;

					payload = await this.postJson("/api/v1/ontology/diff", body);
				} else if (sub === "accept") {
					const body: Record<string, unknown> = {
						action: "ACCEPT",
					};
					if (enrichedOptions.delta) body.delta_path = enrichedOptions.delta;
					if (enrichedOptions.delta_content)
						body.delta_content = enrichedOptions.delta_content;
					if (enrichedOptions.base)
						body.base_ontology_path = enrichedOptions.base;
					if (enrichedOptions.base_content)
						body.base_ontology_content = enrichedOptions.base_content;
					if (enrichedOptions.base_ontology_content)
						body.base_ontology_content = enrichedOptions.base_ontology_content;
					if (enrichedOptions.contract)
						body.contract_path = enrichedOptions.contract;
					if (enrichedOptions.contract_content)
						body.contract_content = enrichedOptions.contract_content;
					if (enrichedOptions.op_id) {
						const ops = String(enrichedOptions.op_id)
							.split(",")
							.map((s) => s.trim())
							.filter(Boolean);
						body.accepted_op_ids = ops;
					}
					if (enrichedOptions.decided_by)
						body.decided_by = enrichedOptions.decided_by;
					if (enrichedOptions.notes) body.steward_notes = enrichedOptions.notes;

					payload = await this.postJson("/api/v1/ontology/decisions", body);

					if (
						enrichedOptions.output &&
						typeof enrichedOptions.output === "string"
					) {
						const resp = payload as Record<string, unknown>;
						if (typeof resp.resulting_ontology_yaml === "string") {
							const fs = await import("node:fs/promises");
							await fs.writeFile(
								enrichedOptions.output,
								resp.resulting_ontology_yaml,
								"utf-8",
							);
						}
					}
				} else if (sub === "reject") {
					const body: Record<string, unknown> = {
						action: "REJECT",
					};
					if (enrichedOptions.proposal_id)
						body.proposal_id = enrichedOptions.proposal_id;
					if (enrichedOptions.delta) body.delta_path = enrichedOptions.delta;
					if (enrichedOptions.delta_content)
						body.delta_content = enrichedOptions.delta_content;
					if (enrichedOptions.base)
						body.base_ontology_path = enrichedOptions.base;
					if (enrichedOptions.base_content)
						body.base_ontology_content = enrichedOptions.base_content;
					if (enrichedOptions.base_ontology_content)
						body.base_ontology_content = enrichedOptions.base_ontology_content;
					if (enrichedOptions.decided_by)
						body.decided_by = enrichedOptions.decided_by;
					if (enrichedOptions.notes) body.steward_notes = enrichedOptions.notes;

					payload = await this.postJson("/api/v1/ontology/decisions", body);
				} else {
					throw new CliDomainError(
						"INVALID_REQUEST",
						`Unknown ontology subcommand: ${subcommand}`,
						400,
					);
				}
				break;
			}
			case "config": {
				const [cfgExitCode, cfgData] = await executeConfigCommand(
					this.baseUrl,
					subcommand,
					enrichedOptions,
					this.getHeaders(),
					this.fetchImpl,
				);
				return { exitCode: cfgExitCode, payload: cfgData };
			}
			default:
				throw new CliDomainError(
					"INVALID_REQUEST",
					`Unrecognized command: ${command}`,
					400,
				);
		}

		let exitCode = errorHandler.resolvePayloadExitCode(payload);
		if (
			exitCode === 0 &&
			typeof payload === "object" &&
			payload !== null &&
			(payload as Record<string, unknown>).passed === false
		) {
			exitCode = 1;
		}
		return { exitCode, payload };
	}

	private async executeStudioCommand(
		sub: string,
		options: Record<string, unknown>,
	): Promise<unknown> {
		const studioSub = sub.replace(/^studio\s*/, "").trim();

		switch (studioSub) {
			case "init": {
				throw new CliDomainError(
					"LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE",
					"The 'ontology studio init' command generates a local workstation loopback secret and is prohibited against remote platform API endpoints.",
					403,
				);
			}

			case "create": {
				this.assertLoopbackForServerPath(
					"--from-workspace",
					options.from_workspace,
				);
				const body: Record<string, unknown> = {
					domain_namespace: options.domain_namespace || "general",
					enterprise_owner:
						options.enterprise_owner || "Enterprise Architecture",
				};
				if (options.session) body.session_id = options.session;
				if (options.description) body.description = options.description;
				if (options.ontology_id) body.ontology_id = options.ontology_id;
				if (options.from_workspace)
					body.from_workspace = options.from_workspace;
				if (options.session_label) body.session_label = options.session_label;
				return await this.postJson("/api/v1/ontology/studio/sessions", body);
			}

			case "delete": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --session flag",
						400,
					);
				}
				const forceParam = options.force ? "?force=true" : "";
				const resp = await this.deleteRequest(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}${forceParam}`,
				);
				return resp ?? { session_id: String(sid), status: "DELETED" };
			}

			case "list": {
				const params = new URLSearchParams();
				const limit =
					options.limit !== undefined &&
					Number.isInteger(Number(options.limit)) &&
					Number(options.limit) >= 0
						? String(options.limit)
						: "50";
				const offset =
					options.offset !== undefined &&
					Number.isInteger(Number(options.offset)) &&
					Number(options.offset) >= 0
						? String(options.offset)
						: "0";
				params.set("limit", limit);
				params.set("offset", offset);
				if (options.status) {
					params.set("status", String(options.status));
				}
				return await this.getJson(
					`/api/v1/ontology/studio/sessions?${params.toString()}`,
				);
			}

			case "status": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --session flag",
						400,
					);
				}
				const encodedSid = encodeURIComponent(String(sid));
				try {
					return await this.getJson(
						`/api/v1/ontology/studio/sessions/${encodedSid}/status`,
					);
				} catch (err) {
					if (
						err instanceof CliDomainError &&
						err.httpStatus === 404 &&
						err.errorCode !== "SESSION_NOT_FOUND"
					) {
						return await this.fetchSessionDetail(String(sid));
					}
					throw err;
				}
			}

			case "upload": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --session flag",
						400,
					);
				}
				const filePath = (options.file_path || options.file) as
					| string
					| undefined;
				let rawBuffer: Buffer | Uint8Array | null = null;
				let fileName = "upload";

				if (filePath) {
					const fs = await import("node:fs/promises");
					try {
						const stat = await fs.stat(filePath);
						if (stat.size === 0) {
							throw new CliDomainError(
								"CORRUPT_SOURCE_BLOB",
								`Source file '${filePath}' is empty (0 bytes).`,
								422,
							);
						}
						rawBuffer = await fs.readFile(filePath);
					} catch (err: unknown) {
						if (err instanceof CliDomainError) throw err;
						if (
							(err instanceof Error &&
								"code" in err &&
								(err as { code: string }).code === "ENOENT") ||
							String(err).includes("ENOENT")
						) {
							throw new CliDomainError(
								"FILE_NOT_FOUND",
								`Source file not found: ${filePath}`,
								404,
							);
						}
						throw new CliDomainError(
							"SYNTAX_INVALID_ARGUMENT",
							`Failed to read source file '${filePath}': ${err}`,
							400,
						);
					}
					fileName = filePath.split(/[\\/]/).pop() || "upload";
				} else if (options.file_content !== undefined) {
					const strContent = String(options.file_content);
					if (strContent.length === 0) {
						throw new CliDomainError(
							"CORRUPT_SOURCE_BLOB",
							"Source content is empty (0 bytes).",
							422,
						);
					}
					rawBuffer = Buffer.from(strContent, "utf-8");
				} else {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --file or source content",
						400,
					);
				}

				const formData = new FormData();
				const blob = new Blob([new Uint8Array(rawBuffer)], {
					type: "application/octet-stream",
				});
				formData.append("file", blob, fileName);
				const isConfidential = Boolean(
					options.confidential ||
						options["do-not-send-to-agent"] ||
						options.do_not_send_to_agent,
				);
				if (isConfidential) {
					formData.append("do_not_send_to_agent", "true");
				}
				if (options.description) {
					formData.append("description", String(options.description));
				}
				return await this.postFormData(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/sources`,
					formData,
				);
			}

			case "parse": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --session flag",
						400,
					);
				}
				const body: Record<string, unknown> = {
					ocr_mode: options.ocr_mode || "auto",
				};
				const combinedSourceIds: string[] = [];
				if (options.source_id) {
					combinedSourceIds.push(...normalizeStringArrayOption(options.source_id));
				}
				if (options.source_ids) {
					combinedSourceIds.push(
						...normalizeStringArrayOption(options.source_ids),
					);
				}
				if (combinedSourceIds.length > 0) {
					body.source_ids = combinedSourceIds;
				}
				const handle = await this.postJson(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/parse`,
					body,
				);
				return await this.waitForStudioJob(String(sid), handle, options);
			}

			case "clarify": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --session flag",
						400,
					);
				}
				const body: Record<string, unknown> = {};
				if (options.focus_concept) {
					body.focus_concept = options.focus_concept;
				}
				if (options.max_questions !== undefined) {
					body.max_questions = options.max_questions;
				}
				if (options.mode) {
					body.mode = options.mode;
				}
				const handle = await this.postJson(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/clarify`,
					body,
				);
				return await this.waitForStudioJob(String(sid), handle, options);
			}

			case "answer": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --session flag",
						400,
					);
				}

				const normalizedItems: Array<Record<string, unknown>> = [];
				let fileExpectedSha: string | undefined;

				if (options.file || options.file_content !== undefined) {
					let parsedRaw: unknown = options.file_content;
					if (parsedRaw === undefined && options.file) {
						parsedRaw = await this.readLocalJsonFile(
							String(options.file),
							"Answers",
						);
					} else if (typeof parsedRaw === "string") {
						try {
							parsedRaw = JSON.parse(parsedRaw);
						} catch (err) {
							throw new CliDomainError(
								"SYNTAX_INVALID_ARGUMENT",
								`Malformed answers JSON: ${err}`,
								400,
							);
						}
					}

					let rawList: unknown[] = [];
					if (Array.isArray(parsedRaw)) {
						rawList = parsedRaw;
					} else if (
						typeof parsedRaw === "object" &&
						parsedRaw !== null &&
						Array.isArray((parsedRaw as Record<string, unknown>).answers)
					) {
						const wrapper = parsedRaw as Record<string, unknown>;
						rawList = wrapper.answers as unknown[];
						if (typeof wrapper.expected_answers_sha256 === "string") {
							fileExpectedSha = wrapper.expected_answers_sha256;
						}
					} else if (typeof parsedRaw === "object" && parsedRaw !== null) {
						// Map dict of `{ [question_id]: value }` to ClarificationAnswerItem[]
						for (const [qid, val] of Object.entries(
							parsedRaw as Record<string, unknown>,
						)) {
							if (Array.isArray(val)) {
								rawList.push({
									question_id: qid,
									selected_options: val.map(String),
								});
							} else if (typeof val === "object" && val !== null) {
								rawList.push({
									question_id: qid,
									...(val as Record<string, unknown>),
								});
							} else {
								rawList.push({ question_id: qid, user_text: String(val ?? "") });
							}
						}
					}

					for (const entry of rawList) {
						if (typeof entry !== "object" || entry === null) {
							throw new CliDomainError(
								"SYNTAX_INVALID_ARGUMENT",
								"Each clarification answer must be an object.",
								400,
							);
						}
						const eObj = entry as Record<string, unknown>;
						const qid = String(eObj.question_id ?? "").trim();
						if (!qid) {
							throw new CliDomainError(
								"SYNTAX_INVALID_ARGUMENT",
								"Clarification answer item is missing 'question_id'.",
								400,
							);
						}
						const selectedOptions = Array.isArray(eObj.selected_options)
							? eObj.selected_options.map(String)
							: eObj.option !== undefined
								? normalizeStringArrayOption(eObj.option)
								: [];
						const userText =
							eObj.user_text !== undefined && eObj.user_text !== null
								? String(eObj.user_text)
								: eObj.answer !== undefined && eObj.answer !== null
									? String(eObj.answer)
									: eObj.text !== undefined && eObj.text !== null
										? String(eObj.text)
										: "";
						normalizedItems.push({
							question_id: qid,
							selected_options: selectedOptions,
							user_text: userText,
						});
					}
				}

				if (options.question_id) {
					const qid = String(options.question_id).trim();
					const selectedOptions = normalizeStringArrayOption(options.option);
					const userText =
						options.text !== undefined && options.text !== null
							? String(options.text)
							: "";
					if (selectedOptions.length === 0 && !userText.trim()) {
						throw new CliDomainError(
							"SYNTAX_INVALID_ARGUMENT",
							"Single-question answer requires --option (-o) and/or --text (-t).",
							400,
						);
					}
					normalizedItems.push({
						question_id: qid,
						selected_options: selectedOptions,
						user_text: userText,
					});
				}

				if (normalizedItems.length === 0) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing clarification answers: provide --file (-f) or --question-id (-q) with --option/-o or --text/-t.",
						400,
					);
				}

				const explicitAnswersShaRaw =
					options.expected_answers_sha256 !== undefined
						? String(options.expected_answers_sha256)
						: fileExpectedSha;
				let expectedAnswersSha = explicitAnswersShaRaw
					? explicitAnswersShaRaw.trim().replace(/^"|"$/g, "")
					: undefined;

				if (!expectedAnswersSha) {
					const encodedSid = encodeURIComponent(String(sid));
					const currentResp = await this.getJson(
						`/api/v1/ontology/studio/sessions/${encodedSid}/answers`,
					);
					const currentArr = Array.isArray(currentResp)
						? currentResp
						: typeof currentResp === "object" &&
								currentResp !== null &&
								Array.isArray((currentResp as Record<string, unknown>).answers)
							? ((currentResp as Record<string, unknown>).answers as unknown[])
							: [];
					if (currentArr.length > 0) {
						throw new CliDomainError(
							"MISSING_AUTO_COMMIT_PRECONDITION",
							"Existing clarification answers require explicit --expected-answers-sha256 precondition.",
							400,
						);
					}
					if (
						typeof currentResp === "object" &&
						currentResp !== null &&
						!Array.isArray(currentResp) &&
						typeof (currentResp as Record<string, unknown>).answers_sha256 ===
							"string"
					) {
						expectedAnswersSha = String(
							(currentResp as Record<string, unknown>).answers_sha256,
						);
					} else {
						expectedAnswersSha = computeCanonicalAnswersSha256(currentArr);
					}
				}

				const body = {
					answers: normalizedItems,
					expected_answers_sha256: expectedAnswersSha,
				};
				return await this.postJson(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/answers`,
					body,
				);
			}

			case "construct": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --session flag",
						400,
					);
				}
				const mode = String(options.mode || "auto");
				const autoCommit = Boolean(options.auto_commit);
				const extraHeaders: Record<string, string> = {};

				if (autoCommit) {
					if (
						mode !== "deterministic" ||
						!options.base_version ||
						options.base_sha256 === undefined ||
						options.base_sha256 === null ||
						String(options.base_sha256).trim() === ""
					) {
						throw new CliDomainError(
							"MISSING_AUTO_COMMIT_PRECONDITION",
							"--auto-commit requires --mode deterministic, --base-version, and --base-sha256.",
							400,
						);
					}
					extraHeaders["If-Match"] = `"${String(options.base_sha256)}"`;
				}

				const body: Record<string, unknown> = {
					base_version: options.base_version,
					base_sha256: options.base_sha256,
					mode,
					auto_commit: autoCommit,
				};
				const handle = await this.postJson(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/construct`,
					body,
					extraHeaders,
				);
				return await this.waitForStudioJob(String(sid), handle, options);
			}

			case "delta validate": {
				let deltaContent = options.file_content as unknown;
				const deltaPath = options.file || options.delta;
				if (deltaContent === undefined && deltaPath) {
					deltaContent = await this.readLocalJsonFile(
						String(deltaPath),
						"Delta",
					);
				} else if (typeof deltaContent === "string") {
					try {
						deltaContent = JSON.parse(deltaContent);
					} catch (err) {
						throw new CliDomainError(
							"SYNTAX_INVALID_ARGUMENT",
							`Malformed delta JSON: ${err}`,
							400,
						);
					}
				}
				if (!deltaContent || typeof deltaContent !== "object") {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --file (-f) or --delta JSON payload.",
						400,
					);
				}

				const rawDeltaObj: Record<string, unknown> = Array.isArray(deltaContent)
					? { operations: deltaContent }
					: { ...(deltaContent as Record<string, unknown>) };
				const normalizedOps = normalizeDeltaOperations(rawDeltaObj.operations);

				const sid = options.session;
				if (sid) {
					const waiverInfo = await this.resolveWaiverHeaders(options);
					let resolvedBaseVer = options.base_version
						? String(options.base_version)
						: typeof rawDeltaObj.base_version === "string" &&
								rawDeltaObj.base_version.trim() !== ""
							? rawDeltaObj.base_version.trim()
							: typeof rawDeltaObj.base_ontology_version === "string" &&
									rawDeltaObj.base_ontology_version.trim() !== ""
								? rawDeltaObj.base_ontology_version.trim()
								: undefined;
					const explicitTargetVer =
						typeof options.target_version === "string" &&
						options.target_version.trim() !== ""
							? options.target_version.trim()
							: typeof rawDeltaObj.target_version === "string" &&
									rawDeltaObj.target_version.trim() !== ""
								? rawDeltaObj.target_version.trim()
								: typeof rawDeltaObj.target_ontology_version === "string" &&
										rawDeltaObj.target_ontology_version.trim() !== ""
									? rawDeltaObj.target_ontology_version.trim()
									: undefined;
					const deltaProvidedSha =
						typeof rawDeltaObj.parent_sha256 === "string" &&
						rawDeltaObj.parent_sha256.trim() !== ""
							? rawDeltaObj.parent_sha256.trim().replace(/^"|"$/g, "")
							: typeof rawDeltaObj.base_sha256 === "string" &&
									rawDeltaObj.base_sha256.trim() !== ""
								? rawDeltaObj.base_sha256.trim().replace(/^"|"$/g, "")
								: undefined;
					const callerProvidedSha =
						options.base_sha256 !== undefined &&
						String(options.base_sha256).trim() !== ""
							? String(options.base_sha256).trim().replace(/^"|"$/g, "")
							: deltaProvidedSha;
					let resolvedParentSha = callerProvidedSha;

					if (resolvedBaseVer === undefined || resolvedParentSha === undefined) {
						const sessionDetail = await this.fetchSessionDetail(String(sid));
						if (resolvedBaseVer === undefined) {
							resolvedBaseVer = String(
								sessionDetail.active_version ??
									sessionDetail.head_version ??
									"0.0.0",
							);
						}
						if (resolvedParentSha === undefined) {
							resolvedParentSha = String(sessionDetail.head_sha256 ?? "")
								.trim()
								.replace(/^"|"$/g, "");
						}
					}

					const {
						base_ontology_version: _ignoredBaseOntVer,
						target_ontology_version: _ignoredTargetOntVer,
						target_version: _ignoredTargetVer,
						...restDeltaObj
					} = rawDeltaObj;
					const body: Record<string, unknown> = {
						...restDeltaObj,
						delta_id:
							typeof rawDeltaObj.delta_id === "string" && rawDeltaObj.delta_id
								? rawDeltaObj.delta_id
								: `delta-${randomUUID().replace(/-/g, "").slice(0, 12)}`,
						base_version: resolvedBaseVer,
						parent_sha256: resolvedParentSha,
						...(explicitTargetVer !== undefined
							? { target_version: explicitTargetVer }
							: {}),
						operations: normalizedOps,
					};
					if (options.verify_grounding) {
						body.verify_grounding = true;
					}
					if (waiverInfo.low_confidence_waiver) {
						body.low_confidence_waiver = true;
						body.grounding_rationale = waiverInfo.grounding_rationale;
					}

					const encodedSid = encodeURIComponent(String(sid));
					let valRes: unknown;
					try {
						valRes = await this.postJson(
							`/api/v1/ontology/studio/sessions/${encodedSid}/validate`,
							body,
							waiverInfo.headers,
						);
					} catch (err) {
						if (
							err instanceof CliDomainError &&
							err.httpStatus === 404 &&
							err.errorCode !== "SESSION_NOT_FOUND"
						) {
							valRes = await this.postJson(
								`/api/v1/ontology/studio/sessions/${encodedSid}/deltas/validate`,
								body,
								waiverInfo.headers,
							);
						} else {
							throw err;
						}
					}
					if (
						typeof valRes === "object" &&
						valRes !== null &&
						(valRes as Record<string, unknown>).valid === false &&
						!(valRes as Record<string, unknown>).error_code
					) {
						return {
							...(valRes as Record<string, unknown>),
							error_code: "ONT_EVO_CONTRACT_VIOLATION",
						};
					}
					return valRes;
				}

				const body: Record<string, unknown> = {
					delta: {
						...rawDeltaObj,
						operations: normalizedOps,
					},
				};
				let baseContent = options.base_ontology_content;
				if (!baseContent && (options.base_ontology_path || options.base)) {
					const basePath = String(options.base_ontology_path || options.base);
					try {
						const fs = await import("node:fs/promises");
						baseContent = await fs.readFile(basePath, "utf-8");
					} catch {
						throw new CliDomainError(
							"FILE_NOT_FOUND",
							`Base ontology file not found: ${basePath}`,
							404,
						);
					}
				}
				if (baseContent) {
					body.base_ontology_content = baseContent;
				}
				if (options.contract) {
					body.contract_path = options.contract;
				}
				const valRes = await this.postJson(
					"/api/v1/ontology/validate-delta",
					body,
				);
				if (
					typeof valRes === "object" &&
					valRes !== null &&
					(valRes as Record<string, unknown>).valid === false &&
					!(valRes as Record<string, unknown>).error_code
				) {
					return {
						...(valRes as Record<string, unknown>),
						error_code: "ONT_EVO_CONTRACT_VIOLATION",
					};
				}
				return valRes;
			}

			case "delta apply": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --session flag",
						400,
					);
				}

				// Resolve waiver credentials/role check BEFORE reading/applying if requested
				const waiverInfo = await this.resolveWaiverHeaders(options);

				let deltaContent = options.file_content as unknown;
				const deltaPath = options.file || options.delta;
				if (deltaContent === undefined && deltaPath) {
					deltaContent = await this.readLocalJsonFile(
						String(deltaPath),
						"Delta",
					);
				} else if (typeof deltaContent === "string") {
					try {
						deltaContent = JSON.parse(deltaContent);
					} catch (err) {
						throw new CliDomainError(
							"SYNTAX_INVALID_ARGUMENT",
							`Malformed delta JSON: ${err}`,
							400,
						);
					}
				}
				if (!deltaContent || typeof deltaContent !== "object") {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --file (-f) or --delta JSON payload.",
						400,
					);
				}

				const rawDeltaObj: Record<string, unknown> = Array.isArray(deltaContent)
					? { operations: deltaContent }
					: { ...(deltaContent as Record<string, unknown>) };
				const normalizedOps = normalizeDeltaOperations(rawDeltaObj.operations);
				if (normalizedOps.length === 0) {
					throw new CliDomainError(
						"NO_OP_DELTA",
						"Cannot apply delta with zero operations.",
						400,
					);
				}

				let resolvedBaseVer = options.base_version
					? String(options.base_version)
					: typeof rawDeltaObj.base_version === "string" &&
							rawDeltaObj.base_version.trim() !== ""
						? rawDeltaObj.base_version.trim()
						: typeof rawDeltaObj.base_ontology_version === "string" &&
								rawDeltaObj.base_ontology_version.trim() !== ""
							? rawDeltaObj.base_ontology_version.trim()
							: undefined;
				const explicitTargetVer =
					typeof options.target_version === "string" &&
					options.target_version.trim() !== ""
						? options.target_version.trim()
						: typeof rawDeltaObj.target_version === "string" &&
								rawDeltaObj.target_version.trim() !== ""
							? rawDeltaObj.target_version.trim()
							: typeof rawDeltaObj.target_ontology_version === "string" &&
									rawDeltaObj.target_ontology_version.trim() !== ""
								? rawDeltaObj.target_ontology_version.trim()
								: undefined;
				const explicitBaseSha =
					options.base_sha256 !== undefined
						? String(options.base_sha256).trim().replace(/^"|"$/g, "")
						: "";
				const deltaBaseSha =
					typeof rawDeltaObj.base_sha256 === "string" &&
					rawDeltaObj.base_sha256.trim() !== ""
						? rawDeltaObj.base_sha256.trim().replace(/^"|"$/g, "")
						: "";
				const deltaParentSha =
					typeof rawDeltaObj.parent_sha256 === "string" &&
					rawDeltaObj.parent_sha256.trim() !== ""
						? rawDeltaObj.parent_sha256.trim().replace(/^"|"$/g, "")
						: "";
				if (deltaBaseSha && deltaParentSha && deltaBaseSha !== deltaParentSha) {
					throw new CliDomainError(
						"PRECONDITION_BASE_SHA_MISMATCH",
						`Delta base_sha256 '${deltaBaseSha}' conflicts with parent_sha256 '${deltaParentSha}'.`,
						400,
					);
				}
				const deltaSha = deltaBaseSha || deltaParentSha;
				if (explicitBaseSha && deltaSha && explicitBaseSha !== deltaSha) {
					throw new CliDomainError(
						"PRECONDITION_BASE_SHA_MISMATCH",
						`CLI --base-sha256 '${explicitBaseSha}' conflicts with delta SHA '${deltaSha}'.`,
						400,
					);
				}
				const effectiveSha = explicitBaseSha || deltaSha;
				let sessionHeadSha: string | undefined;

				if (resolvedBaseVer === undefined || !effectiveSha) {
					const sessionDetail = await this.fetchSessionDetail(String(sid));
					sessionHeadSha = String(sessionDetail.head_sha256 ?? "")
						.trim()
						.replace(/^"|"$/g, "");
					if (resolvedBaseVer === undefined) {
						resolvedBaseVer = String(
							sessionDetail.active_version ??
								sessionDetail.head_version ??
								"0.0.0",
						);
					}
				}

				const isGenesis = resolvedBaseVer.replace(/^v/i, "") === "0.0.0";
				if (!effectiveSha && !isGenesis) {
					throw new CliDomainError(
						"MISSING_PRECONDITION_HEADER",
						"Missing mandatory --base-sha256 (or delta parent_sha256) precondition for non-genesis delta apply.",
						400,
					);
				}

				const resolvedHeadSha = effectiveSha || (sessionHeadSha ?? "");
				if (!resolvedHeadSha) {
					throw new CliDomainError(
						"MISSING_PRECONDITION_HEADER",
						"Missing mandatory --base-sha256, delta parent_sha256, or session head_sha256 precondition for delta apply.",
						400,
					);
				}

				const parentSha = resolvedHeadSha;
				const ifMatchSha = resolvedHeadSha;

				const {
					base_ontology_version: _ignoredApplyBaseOntVer,
					target_ontology_version: _ignoredApplyTargetOntVer,
					target_version: _ignoredApplyTargetVer,
					...restApplyDeltaObj
				} = rawDeltaObj;
				const body: Record<string, unknown> = {
					...restApplyDeltaObj,
					delta_id:
						typeof rawDeltaObj.delta_id === "string" && rawDeltaObj.delta_id
							? rawDeltaObj.delta_id
							: `delta-${randomUUID().replace(/-/g, "").slice(0, 12)}`,
					base_version: resolvedBaseVer,
					parent_sha256: parentSha,
					...(explicitTargetVer !== undefined
						? { target_version: explicitTargetVer }
						: {}),
					operations: normalizedOps,
				};
				if (options.rationale) {
					body.rationale = options.rationale;
				}
				if (options.ungrounded_concept) {
					body.ungrounded_concept = normalizeStringArrayOption(
						options.ungrounded_concept,
					);
				}
				if (options.evidence_grounding_file) {
					body.evidence_grounding_file = String(options.evidence_grounding_file);
				}

				const headers: Record<string, string> = {
					"If-Match": `"${ifMatchSha}"`,
				};
				if (waiverInfo.low_confidence_waiver) {
					body.low_confidence_waiver = true;
					body.grounding_rationale = waiverInfo.grounding_rationale;
					Object.assign(headers, waiverInfo.headers);
				}

				return await this.postJson(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/delta`,
					body,
					headers,
				);
			}

			case "proposal list": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --session flag",
						400,
					);
				}
				return await this.getJson(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/proposals`,
				);
			}

			case "proposal show": {
				const sid = options.session;
				const pid = options.proposal_id;
				if (!sid || !pid) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --session or --proposal-id flag",
						400,
					);
				}
				return await this.getJson(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/proposals/${encodeURIComponent(String(pid))}`,
				);
			}

			case "proposal accept": {
				const sid = options.session;
				const pid = options.proposal_id;
				if (!sid || !pid) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --session or --proposal-id flag",
						400,
					);
				}

				const extraHeaders: Record<string, string> = {};
				if (options.base_sha256) {
					extraHeaders["If-Match"] = `"${options.base_sha256}"`;
				}

				const waiverInfo = await this.resolveWaiverHeaders(options);
				if (waiverInfo.low_confidence_waiver) {
					Object.assign(extraHeaders, waiverInfo.headers);
				}

				const body: Record<string, unknown> = {
					base_sha256: options.base_sha256,
					allow_self_accepted_proposals: Boolean(
						options.allow_self_accepted_proposals,
					),
					low_confidence_waiver: waiverInfo.low_confidence_waiver,
					grounding_rationale: waiverInfo.grounding_rationale,
					self_acceptance_override: Boolean(
						options.allow_self_accepted_proposals,
					),
				};
				if (options.ungrounded_concept) {
					body.ungrounded_concept = normalizeStringArrayOption(
						options.ungrounded_concept,
					);
				}
				if (options.evidence_grounding_file) {
					body.evidence_grounding_file = options.evidence_grounding_file;
				}

				return await this.postJson(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/proposals/${encodeURIComponent(String(pid))}/accept`,
					body,
					extraHeaders,
				);
			}

			case "proposal reject": {
				const sid = options.session;
				const pid = options.proposal_id;
				if (!sid || !pid) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --session or --proposal-id flag",
						400,
					);
				}
				const body: Record<string, unknown> = {};
				if (options.reason) {
					body.reason = options.reason;
				}
				return await this.postJson(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/proposals/${encodeURIComponent(String(pid))}/reject`,
					body,
				);
			}

			case "proposal rebase": {
				const sid = options.session;
				const pid = options.proposal_id;
				if (!sid || !pid) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --session or --proposal-id flag",
						400,
					);
				}
				let targetSha = (options.base_sha256 || options.head_sha256) as
					| string
					| undefined;
				if (!targetSha) {
					const sessionDetail = await this.fetchSessionDetail(String(sid));
					targetSha = String(sessionDetail.head_sha256 ?? "");
				}
				const extraHeaders: Record<string, string> = {};
				if (targetSha) {
					extraHeaders["If-Match"] = `"${targetSha}"`;
				}
				const body: Record<string, unknown> = {
					base_sha256: targetSha,
				};
				return await this.postJson(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/proposals/${encodeURIComponent(String(pid))}/rebase`,
					body,
					extraHeaders,
				);
			}

			case "export": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --session flag",
						400,
					);
				}
				// NEVER fall back to global `options.format` (`toon`) — `CRIT-03`!
				const fmt = String(options.export_format || "yaml");
				const ver =
					options.version && typeof options.version === "string"
						? `&version=${encodeURIComponent(options.version)}`
						: "";
				const headers = { ...this.getHeaders(), Accept: "*/*" };
				const res = await this.fetchImpl(
					`${this.baseUrl}/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/export?export_format=${encodeURIComponent(fmt)}${ver}`,
					{ headers },
				);
				if (!res.ok) {
					throw await this.parseErrorResponse(res);
				}
				const rawText = await res.text();
				const sha256 = createHash("sha256")
					.update(rawText, "utf-8")
					.digest("hex");

				if (options.out && typeof options.out === "string") {
					const fs = await import("node:fs/promises");
					await fs.writeFile(options.out, rawText, "utf-8");
				}

				const wantsJson =
					options.json === true || options.format === "json";
				if (wantsJson) {
					return {
						session_id: String(sid),
						format: fmt,
						export_format: fmt,
						...(options.version && typeof options.version === "string"
							? { version: options.version }
							: {}),
						...(options.out && typeof options.out === "string"
							? { out_path: options.out }
							: {}),
						sha256,
						content: rawText,
					};
				}
				return rawText;
			}

			case "promote": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --session flag",
						400,
					);
				}
				this.assertLoopbackForServerPath("--workspace", options.workspace);
				const rationale = options.rationale;
				if (!rationale || String(rationale).trim().length < 10) {
					throw new CliDomainError(
						"PROMOTION_RATIONALE_REQUIRED",
						"Promotion requires rationale (min 10 chars)",
						400,
					);
				}
				const headSha = String(options.head_sha256 ?? "");
				const wsSha = String(options.workspace_sha256 ?? "");
				const extraHeaders: Record<string, string> = {
					"If-Match": `"${headSha}"`,
					"X-OpenDV-Workspace-If-Match": `"${wsSha}"`,
				};
				const body: Record<string, unknown> = {
					workspace_sha256: wsSha,
					head_sha256: headSha,
					rationale: options.rationale,
					workspace: options.workspace,
					acknowledge_historical_contract_warning: Boolean(
						options.acknowledge_historical_contract_warning,
					),
					historical_contract_rationale: options.historical_contract_rationale,
				};
				return await this.postJson(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/promote`,
					body,
					extraHeaders,
				);
			}

			case "bundle export": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --session flag",
						400,
					);
				}
				const body: Record<string, unknown> = {
					acknowledge_confidential_source_export: Boolean(
						options.acknowledge_confidential_source_export,
					),
					export_rationale: options.export_rationale,
				};
				const res = await this.fetchImpl(
					`${this.baseUrl}/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/bundle`,
					{
						method: "POST",
						headers: this.getHeaders(),
						body: JSON.stringify(body),
					},
				);
				if (!res.ok) {
					throw await this.parseErrorResponse(res);
				}
				const contentType = res.headers?.get("content-type") || "";
				if (contentType.includes("json")) {
					return await res.json();
				}
				const arrayBuffer = await res.arrayBuffer();
				const buffer = Buffer.from(arrayBuffer);
				const outPath =
					(options.out as string) ||
					(options.out_path as string) ||
					`${sid}.odvstudio.zip`;
				const fs = await import("node:fs/promises");
				await fs.writeFile(outPath, buffer);
				return {
					session_id: sid,
					bundle_path: outPath,
					size_bytes: buffer.byteLength,
				};
			}

			case "bundle import": {
				const filePath = (options.file_path || options.file) as string;
				if (!filePath) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --file flag",
						400,
					);
				}
				let fileData: Uint8Array;
				try {
					const fs = await import("node:fs/promises");
					fileData = await fs.readFile(filePath);
				} catch (err: unknown) {
					if (
						(err instanceof Error &&
							"code" in err &&
							(err as { code: string }).code === "ENOENT") ||
						String(err).includes("ENOENT")
					) {
						throw new CliDomainError(
							"FILE_NOT_FOUND",
							`Bundle file not found: ${filePath}`,
							404,
						);
					}
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						`Failed to read bundle file: ${err}`,
						400,
					);
				}
				const fileName = filePath.split(/[\\/]/).pop() || "bundle.zip";
				const formData = new FormData();
				formData.append("file", new Blob([new Uint8Array(fileData)]), fileName);

				const queryParams = new URLSearchParams();
				if (options.new_session_id) {
					queryParams.set("new_session_id", String(options.new_session_id));
					formData.append("new_session_id", String(options.new_session_id));
				}
				if (options.force) {
					queryParams.set("force", "true");
					formData.append("force", "true");
				}
				if (options.acknowledge_unverified_import_attribution) {
					queryParams.set(
						"acknowledge_unverified_import_attribution",
						"true",
					);
				}
				if (options.acknowledgement_rationale) {
					queryParams.set(
						"acknowledgement_rationale",
						String(options.acknowledgement_rationale),
					);
				}
				if (options.acknowledge_historical_contract_warning) {
					queryParams.set("acknowledge_historical_contract_warning", "true");
				}
				if (options.historical_contract_rationale) {
					queryParams.set(
						"historical_contract_rationale",
						String(options.historical_contract_rationale),
					);
				}
				const q = queryParams.toString() ? `?${queryParams.toString()}` : "";
				return await this.postFormData(
					`/api/v1/ontology/studio/sessions/bundle/import${q}`,
					formData,
				);
			}

			case "bundle clone": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --session flag",
						400,
					);
				}
				const body: Record<string, unknown> = {
					new_session_id: options.new_session_id,
					session_label: options.session_label,
				};
				return await this.postJson(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/bundle/clone`,
					body,
				);
			}

			default:
				throw new CliDomainError(
					"INVALID_REQUEST",
					`Unknown ontology studio subcommand: ${studioSub}`,
					400,
				);
		}
	}
}
