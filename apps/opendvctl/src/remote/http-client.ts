/**
 * HTTP client and transport helper for remote platform API calls.
 */

import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { OpenDVPlatformClient } from "@opendv/protocol-ts";
import { CliDomainError } from "../error-handler";
import { parseHttpErrorResponse } from "../http-executor";
import { loadCliSpec } from "../spec";
import {
	StudioSsePrematureEofError,
	followStudioJobEvents,
} from "../sse-follower";
import {
	ADMIN_ROLE_NAMES,
	MIN_RATIONALE_LENGTH,
	type RemoteExecutorConfig,
} from "./types";

export class RemoteHttpClient {
	readonly baseUrl: string;
	readonly role: string;
	readonly explicitRole: boolean;
	readonly fetchImpl: typeof fetch;
	readonly writer: (msg: string) => void;
	readonly errWriter: (msg: string) => void;
	readonly pollIntervalMs: number;
	readonly client: OpenDVPlatformClient;

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
						(config.role && config.role.trim() !== "") ||
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

	isLoopback(): boolean {
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

	isInsecureHttp(): boolean {
		try {
			const u = new URL(this.baseUrl);
			return u.protocol === "http:" && !this.isLoopback();
		} catch {
			return false;
		}
	}

	assertLoopbackForServerPath(
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

	async resolveWaiverHeaders(
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

		const effectiveStudioRole = this.explicitRole
			? (this.role || "INTERNAL_MODELER").toUpperCase()
			: "ADMIN";
		if (!ADMIN_ROLE_NAMES.has(effectiveStudioRole)) {
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
			waiverKey = (process.env.OPENDV_WAIVER_KEY || "").trim();
		}

		const baseHeaders = this.getHeaders(undefined, { isStudioRequest: true });
		const hasRemoteBearerAuth =
			!this.isLoopback() &&
			Boolean(baseHeaders.Authorization || baseHeaders.authorization);

		if (!waiverKey && !hasRemoteBearerAuth) {
			throw new CliDomainError(
				"FORBIDDEN_ADMIN_REQUIRED",
				"Low-confidence waiver requires steward credentials via --waiver-key-file, OPENDV_WAIVER_KEY, or remote admin Authorization token.",
				403,
			);
		}

		const rationale = rawRationale;
		if (!rationale || String(rationale).trim().length < MIN_RATIONALE_LENGTH) {
			throw new CliDomainError(
				"INVALID_GROUNDING_WAIVER",
				`Low-confidence grounding waiver requires a rationale of at least ${MIN_RATIONALE_LENGTH} characters.`,
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

	getHeaders(
		extraHeaders?: Record<string, string>,
		opts?: { isStudioRequest?: boolean },
	): Record<string, string> {
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

		const omitImplicitStudioRole =
			opts?.isStudioRequest === true && !this.explicitRole;
		const headers: Record<string, string> = {
			"Content-Type": "application/json",
			Accept: "application/json",
			...(omitImplicitStudioRole ? {} : { "X-OpenDV-Role": normalizedRole }),
			...extraHeaders,
		};

		if (!headers.Authorization && !headers.authorization) {
			if (this.isLoopback()) {
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
				const remoteToken = (process.env.OPENDV_TOKEN || "").trim();
				if (remoteToken) {
					if (
						this.isInsecureHttp() &&
						process.env.OPENDV_ALLOW_INSECURE_HTTP !== "true"
					) {
						// Refuse to attach bearer credentials over plaintext http
					} else {
						headers.Authorization = `Bearer ${remoteToken}`;
					}
				}
			}
		}
		return headers;
	}

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

	async parseErrorResponse(res: Response): Promise<CliDomainError> {
		return parseHttpErrorResponse(res);
	}

	async postJson(
		endpoint: string,
		payload: unknown,
		extraHeaders?: Record<string, string>,
	): Promise<unknown> {
		const isStudioRequest = endpoint.startsWith("/api/v1/ontology/studio");
		const res = await this.fetchImpl(`${this.baseUrl}${endpoint}`, {
			method: "POST",
			headers: this.getHeaders(extraHeaders, { isStudioRequest }),
			body: JSON.stringify(payload),
		});

		if (!res.ok) {
			throw await this.parseErrorResponse(res);
		}

		return res.json();
	}

	async getJson(
		endpoint: string,
		extraHeaders?: Record<string, string>,
	): Promise<unknown> {
		const isStudioRequest = endpoint.startsWith("/api/v1/ontology/studio");
		const res = await this.fetchImpl(`${this.baseUrl}${endpoint}`, {
			headers: this.getHeaders(extraHeaders, { isStudioRequest }),
		});

		if (!res.ok) {
			throw await this.parseErrorResponse(res);
		}

		return res.json();
	}

	async deleteRequest(
		endpoint: string,
		extraHeaders?: Record<string, string>,
	): Promise<unknown> {
		const isStudioRequest = endpoint.startsWith("/api/v1/ontology/studio");
		const res = await this.fetchImpl(`${this.baseUrl}${endpoint}`, {
			method: "DELETE",
			headers: this.getHeaders(extraHeaders, { isStudioRequest }),
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

	async postFormData(
		endpoint: string,
		formData: FormData,
		extraHeaders?: Record<string, string>,
	): Promise<unknown> {
		const isStudioRequest = endpoint.startsWith("/api/v1/ontology/studio");
		const {
			"Content-Type": _ct1,
			"content-type": _ct2,
			...headers
		} = this.getHeaders(extraHeaders, { isStudioRequest });

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

	throwIfFailedJobRecord(jobObj: Record<string, unknown>): void {
		const statusUpper = String(jobObj.status ?? "").toUpperCase();
		if (statusUpper === "FAILED") {
			const errObj =
				typeof jobObj.error === "object" && jobObj.error !== null
					? (jobObj.error as Record<string, unknown>)
					: jobObj;
			const code = String(errObj.code || errObj.error_code || "INTERNAL_ERROR");
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
				errObj.detail ||
					errObj.message ||
					"Studio background job was cancelled.",
			);
			throw new CliDomainError("JOB_ALREADY_TERMINAL", detail, 409);
		}
	}

	async waitForStudioJob(
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
			const isJsonOut = options.json === true || options.format === "json";
			const streamLogger = isJsonOut ? this.errWriter : this.writer;
			const streamUrl = `${this.baseUrl}${jobStatusEndpoint}/events`;

			try {
				const streamSummary = await followStudioJobEvents({
					streamUrl,
					headers: this.getHeaders(undefined, { isStudioRequest: true }),
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
					if (
						err instanceof CliDomainError &&
						err.errorCode !== "JOB_NOT_FOUND"
					) {
						throw err;
					}
					return { ...initObj, ...streamSummary, status: "COMPLETED" };
				}
			} catch (err) {
				if (!(err instanceof StudioSsePrematureEofError)) {
					throw err;
				}
			}
		}

		if (initStatus === "COMPLETED") {
			return initialPayload;
		}

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

	async fetchSessionDetail(
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

	async readLocalJsonFile(
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
}
