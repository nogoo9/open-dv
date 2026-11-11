/**
 * REST & SSE Execution Coordinator (`RemoteExecutor`).
 *
 * Implements:
 * - Mandatory API endpoint verification (REQ-CFG-01).
 * - Contract version guard against `GET /api/v1/health` (REQ-FAIL-01).
 * - Decision D1 inline uploads attached to requests.
 * - Decision D2 async pipeline runs with live SSE streaming via `followEventStream`.
 * - Command dispatch across all platform API endpoints.
 */

import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { OpenDVPlatformClient } from "@opendv/protocol-ts";
import { CliDomainError, errorHandler } from "./error-handler";
import { executeConfigCommand, parseHttpErrorResponse } from "./http-executor";
import { processInlineUploads } from "./inline-uploader";
import { type CliCommand, type CliOption, loadCliSpec } from "./spec";
import { followEventStream } from "./sse-follower";

export interface RemoteExecutorConfig {
	baseUrl?: string | undefined;
	role?: string | undefined;
	fetchImpl?: typeof fetch | undefined;
	writer?: ((msg: string) => void) | undefined;
}

export class RemoteExecutor {
	private readonly baseUrl: string;
	private readonly role: string;
	private readonly fetchImpl: typeof fetch;
	private readonly writer: (msg: string) => void;
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
		this.role =
			config.role ||
			(typeof process !== "undefined" && process.env.OPENDV_ROLE
				? process.env.OPENDV_ROLE
				: undefined) ||
			"INTERNAL_MODELER";
		this.fetchImpl = config.fetchImpl ?? globalThis.fetch.bind(globalThis);
		this.writer = config.writer ?? ((s) => console.log(s));

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

	private async resolveWaiverHeaders(options: Record<string, unknown>): Promise<{
		low_confidence_waiver: boolean;
		grounding_rationale?: string;
		headers: Record<string, string>;
	}> {
		if (!options.low_confidence_waiver) {
			return { low_confidence_waiver: false, headers: {} };
		}

		const rationale = options.waiver_rationale || options.grounding_rationale;
		if (!rationale || String(rationale).trim().length < 10) {
			throw new CliDomainError(
				"INVALID_GROUNDING_WAIVER",
				"Low-confidence grounding waiver requires a rationale of at least 10 characters.",
				422,
			);
		}

		if (this.isInsecureHttp() && process.env.OPENDV_ALLOW_INSECURE_HTTP !== "true") {
			throw new CliDomainError(
				"INSECURE_TRANSPORT_REFUSED",
				"Refusing to transmit privileged steward waiver key over plaintext HTTP to non-loopback endpoint. Use HTTPS or set OPENDV_ALLOW_INSECURE_HTTP=true.",
				403,
			);
		}

		let waiverKey = (process.env.OPENDV_WAIVER_KEY || "").trim();
		const keyFile = options.waiver_key_file;
		if (keyFile && typeof keyFile === "string") {
			try {
				const fs = await import("node:fs/promises");
				waiverKey = (await fs.readFile(keyFile, "utf-8")).trim();
			} catch {
				throw new CliDomainError("FORBIDDEN_ADMIN_REQUIRED", "Waiver key file could not be read", 403);
			}
		}

		if (!waiverKey) {
			throw new CliDomainError(
				"FORBIDDEN_ADMIN_REQUIRED",
				"Low-confidence waiver requires steward credentials via --waiver-key-file or OPENDV_WAIVER_KEY.",
				403,
			);
		}

		waiverKey = waiverKey.replace(/^Bearer\s+/i, "").trim();
		return {
			low_confidence_waiver: true,
			grounding_rationale: String(rationale).trim(),
			headers: { "X-OpenDV-Waiver-Key": waiverKey },
		};
	}

	getHeaders(extraHeaders?: Record<string, string>): Record<string, string> {
		const normalizedRole = this.role ? this.role.toUpperCase() : "INTERNAL_MODELER";
		const allowedRoles = new Set([
			"PUBLIC",
			"INTERNAL_MODELER",
			"DATA_STEWARD",
			"RESTRICTED_PII_CUSTODIAN",
			"AUDITOR_FINANCE",
			"ADMIN",
			"EDITOR",
			"VIEWER",
		]);
		if (!allowedRoles.has(normalizedRole)) {
			throw new CliDomainError(
				"SYNTAX_INVALID_ARGUMENT",
				`Invalid or unsupported role: ${this.role}. Supported roles: PUBLIC, INTERNAL_MODELER, DATA_STEWARD, RESTRICTED_PII_CUSTODIAN, AUDITOR_FINANCE, ADMIN, EDITOR, VIEWER`,
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
					const tokenPath = path.join(process.env.OPENDV_STUDIO_DIR, ".local_token");
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
					if (this.isInsecureHttp() && process.env.OPENDV_ALLOW_INSECURE_HTTP !== "true") {
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

	async execute(
		command: string,
		subcommand: string | undefined,
		rawOptions: Record<string, unknown>,
	): Promise<{ exitCode: number; payload: unknown }> {
		// 1. Contract version guard
		await this.verifyContractVersion();

		// 2. Resolve spec options for inline file uploads (Decision D1)
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
					const found = currentCmd.subcommands.find(
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

		const enrichedOptions = await processInlineUploads(rawOptions, specOptions);

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
						"Missing ontology subcommand (suggest, validate-delta, diff, accept, reject)",
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
				// Task 5C.3 / Task 5C.2: Prohibited against remote platform API endpoints
				throw new CliDomainError(
					"LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE",
					"The 'ontology studio init' command generates a local workstation loopback secret and is prohibited against remote platform API endpoints.",
					403,
				);
			}

			case "create": {
				const body: Record<string, unknown> = {};
				if (options.session) body.session_id = options.session;
				if (options.domain_namespace) body.domain_namespace = options.domain_namespace;
				if (options.enterprise_owner) body.enterprise_owner = options.enterprise_owner;
				if (options.description) body.description = options.description;
				if (options.ontology_id) body.ontology_id = options.ontology_id;
				if (options.from_workspace) body.from_workspace = options.from_workspace;
				if (options.session_label) body.session_label = options.session_label;
				return await this.postJson("/api/v1/ontology/studio/sessions", body);
			}

			case "list": {
				const params = new URLSearchParams();
				const limit =
					options.limit !== undefined && Number.isInteger(Number(options.limit)) && Number(options.limit) >= 0
						? String(options.limit)
						: "50";
				const offset =
					options.offset !== undefined && Number.isInteger(Number(options.offset)) && Number(options.offset) >= 0
						? String(options.offset)
						: "0";
				params.set("limit", limit);
				params.set("offset", offset);
				if (options.status) {
					params.set("status", String(options.status));
				}
				return await this.getJson(`/api/v1/ontology/studio/sessions?${params.toString()}`);
			}

			case "status": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError("SYNTAX_INVALID_ARGUMENT", "Missing required --session flag", 400);
				}
				return await this.getJson(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/status`,
				);
			}

			case "upload": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError("SYNTAX_INVALID_ARGUMENT", "Missing required --session flag", 400);
				}
				const filePath = (options.file_path || options.file) as string | undefined;
				let rawBuffer: Buffer | Uint8Array | null = null;
				let fileName = "upload";

				if (filePath) {
					const fs = await import("node:fs/promises");
					try {
						const stat = await fs.stat(filePath);
						if (stat.size === 0) {
							throw new CliDomainError("CORRUPT_SOURCE_BLOB", `Source file '${filePath}' is empty (0 bytes).`, 422);
						}
						rawBuffer = await fs.readFile(filePath);
					} catch (err: unknown) {
						if (err instanceof CliDomainError) throw err;
						if (
							(err instanceof Error && "code" in err && (err as { code: string }).code === "ENOENT") ||
							String(err).includes("ENOENT")
						) {
							throw new CliDomainError("FILE_NOT_FOUND", `Source file not found: ${filePath}`, 404);
						}
						throw new CliDomainError("SYNTAX_INVALID_ARGUMENT", `Failed to read source file '${filePath}': ${err}`, 400);
					}
					fileName = filePath.split(/[\\/]/).pop() || "upload";
				} else if (options.file_content !== undefined) {
					const strContent = String(options.file_content);
					if (strContent.length === 0) {
						throw new CliDomainError("CORRUPT_SOURCE_BLOB", "Source content is empty (0 bytes).", 422);
					}
					rawBuffer = Buffer.from(strContent, "utf-8");
				} else {
					throw new CliDomainError("SYNTAX_INVALID_ARGUMENT", "Missing required --file or source content", 400);
				}

				const formData = new FormData();
				const blob = new Blob([rawBuffer], { type: "application/octet-stream" });
				formData.append("file", blob, fileName);
				const isConfidential = Boolean(
					options.confidential ||
					options["do-not-send-to-agent"] ||
					options.do_not_send_to_agent
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
					throw new CliDomainError("SYNTAX_INVALID_ARGUMENT", "Missing required --session flag", 400);
				}
				const body: Record<string, unknown> = {
					ocr_mode: options.ocr_mode || "auto",
				};
				if (options.source_id) {
					body.source_ids = [options.source_id];
				}
				if (options.source_ids) {
					body.source_ids = Array.isArray(options.source_ids)
						? options.source_ids
						: String(options.source_ids).split(",").map((s) => s.trim()).filter(Boolean);
				}
				return await this.postJson(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/parse`,
					body,
				);
			}

			case "clarify": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError("SYNTAX_INVALID_ARGUMENT", "Missing required --session flag", 400);
				}
				const body: Record<string, unknown> = {};
				if (options.focus_concept) {
					body.focus_concept = options.focus_concept;
				}
				if (options.max_questions !== undefined) {
					body.max_questions = options.max_questions;
				}
				return await this.postJson(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/clarify`,
					body,
				);
			}

			case "answer": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError("SYNTAX_INVALID_ARGUMENT", "Missing required --session flag", 400);
				}
				let answersData: unknown = options.file_content;
				if (!answersData && options.file) {
					const filePath = String(options.file);
					try {
						const fs = await import("node:fs/promises");
						answersData = await fs.readFile(filePath, "utf-8");
					} catch {
						throw new CliDomainError("FILE_NOT_FOUND", `Answers file not found: ${filePath}`, 404);
					}
				}
				if (typeof answersData === "string") {
					try {
						answersData = JSON.parse(answersData);
					} catch (err) {
						throw new CliDomainError("SYNTAX_INVALID_ARGUMENT", `Malformed answers JSON: ${err}`, 400);
					}
				}
				if (!answersData || (Array.isArray(answersData) && answersData.length === 0)) {
					throw new CliDomainError("SYNTAX_INVALID_ARGUMENT", "Missing required answers content or file", 400);
				}
				const body = { answers: answersData };
				return await this.postJson(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/answers`,
					body,
				);
			}

			case "construct": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError("SYNTAX_INVALID_ARGUMENT", "Missing required --session flag", 400);
				}
				const body: Record<string, unknown> = {
					base_version: options.base_version,
					base_sha256: options.base_sha256,
					mode: options.mode || "auto",
					auto_commit: Boolean(options.auto_commit),
				};
				return await this.postJson(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/construct`,
					body,
				);
			}

			case "delta validate": {
				let deltaContent = options.file_content as unknown;
				if (!deltaContent && (options.file || options.delta)) {
					const filePath = String(options.file || options.delta);
					try {
						const fs = await import("node:fs/promises");
						deltaContent = await fs.readFile(filePath, "utf-8");
					} catch {
						throw new CliDomainError("FILE_NOT_FOUND", `Delta file not found: ${filePath}`, 404);
					}
				}
				if (typeof deltaContent === "string") {
					try {
						deltaContent = JSON.parse(deltaContent);
					} catch (err) {
						throw new CliDomainError("SYNTAX_INVALID_ARGUMENT", `Malformed delta JSON: ${err}`, 400);
					}
				}
				const sid = options.session;
				if (sid) {
					const body: Record<string, unknown> =
						typeof deltaContent === "object" && deltaContent !== null
							? { ...(deltaContent as Record<string, unknown>) }
							: {};
					if (options.base_version) body.base_version = options.base_version;
					if (options.base_sha256) body.base_sha256 = options.base_sha256;
					return await this.postJson(
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/deltas/validate`,
						body,
					);
				}
				const body: Record<string, unknown> = {};
				if (typeof deltaContent === "object" && deltaContent !== null) {
					body.delta = deltaContent;
				} else if (typeof deltaContent === "string") {
					body.delta_content = deltaContent;
				} else if (typeof options.file_content === "string") {
					body.delta_content = options.file_content;
				}
				let baseContent = options.base_ontology_content;
				if (!baseContent && (options.base_ontology_path || options.base)) {
					const basePath = String(options.base_ontology_path || options.base);
					try {
						const fs = await import("node:fs/promises");
						baseContent = await fs.readFile(basePath, "utf-8");
					} catch {
						throw new CliDomainError("FILE_NOT_FOUND", `Base ontology file not found: ${basePath}`, 404);
					}
				}
				if (baseContent) {
					body.base_ontology_content = baseContent;
				}
				if (options.contract) {
					body.contract_path = options.contract;
				}
				return await this.postJson("/api/v1/ontology/validate-delta", body);
			}

			case "delta apply": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError("SYNTAX_INVALID_ARGUMENT", "Missing required --session flag", 400);
				}
				let deltaContent = options.file_content as unknown;
				if (!deltaContent && (options.file || options.delta)) {
					const filePath = String(options.file || options.delta);
					try {
						const fs = await import("node:fs/promises");
						const raw = await fs.readFile(filePath, "utf-8");
						deltaContent = JSON.parse(raw);
					} catch (err: unknown) {
						if (
							(err instanceof Error && "code" in err && (err as { code: string }).code === "ENOENT") ||
							String(err).includes("ENOENT")
						) {
							throw new CliDomainError("FILE_NOT_FOUND", `Delta file not found: ${filePath}`, 404);
						}
						throw new CliDomainError("SYNTAX_INVALID_ARGUMENT", `Failed to read or parse delta JSON: ${err}`, 400);
					}
				}
				if (typeof deltaContent === "string") {
					try {
						deltaContent = JSON.parse(deltaContent);
					} catch (err) {
						throw new CliDomainError("SYNTAX_INVALID_ARGUMENT", `Malformed delta JSON: ${err}`, 400);
					}
				}
				const body: Record<string, unknown> =
					typeof deltaContent === "object" && deltaContent !== null
						? { ...(deltaContent as Record<string, unknown>) }
						: {};
				if (options.base_version) body.base_version = options.base_version;
				if (options.base_sha256) body.base_sha256 = options.base_sha256;
				if (options.rationale) body.rationale = options.rationale;
				const headers: Record<string, string> = {};
				if (options.base_sha256) {
					headers["If-Match"] = `"${options.base_sha256}"`;
				}
				const waiverInfo = await this.resolveWaiverHeaders(options);
				if (waiverInfo.low_confidence_waiver) {
					body.low_confidence_waiver = true;
					body.grounding_rationale = waiverInfo.grounding_rationale;
					Object.assign(headers, waiverInfo.headers);
				}
				return await this.postJson(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/deltas/apply`,
					body,
					headers,
				);
			}

			case "proposal list": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError("SYNTAX_INVALID_ARGUMENT", "Missing required --session flag", 400);
				}
				return await this.getJson(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/proposals`,
				);
			}

			case "proposal show": {
				const sid = options.session;
				const pid = options.proposal_id;
				if (!sid || !pid) {
					throw new CliDomainError("SYNTAX_INVALID_ARGUMENT", "Missing required --session or --proposal-id flag", 400);
				}
				return await this.getJson(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/proposals/${encodeURIComponent(String(pid))}`,
				);
			}

			case "proposal accept": {
				const sid = options.session;
				const pid = options.proposal_id;
				if (!sid || !pid) {
					throw new CliDomainError("SYNTAX_INVALID_ARGUMENT", "Missing required --session or --proposal-id flag", 400);
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
					allow_self_accepted_proposals: Boolean(options.allow_self_accepted_proposals),
					low_confidence_waiver: waiverInfo.low_confidence_waiver,
					grounding_rationale: waiverInfo.grounding_rationale,
					self_acceptance_override: Boolean(options.allow_self_accepted_proposals),
				};
				if (options.ungrounded_concept) body.ungrounded_concept = options.ungrounded_concept;
				if (options.evidence_grounding_file) body.evidence_grounding_file = options.evidence_grounding_file;

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
					throw new CliDomainError("SYNTAX_INVALID_ARGUMENT", "Missing required --session or --proposal-id flag", 400);
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
					throw new CliDomainError("SYNTAX_INVALID_ARGUMENT", "Missing required --session or --proposal-id flag", 400);
				}
				const extraHeaders: Record<string, string> = {};
				if (options.base_sha256) {
					extraHeaders["If-Match"] = `"${options.base_sha256}"`;
				}
				const body: Record<string, unknown> = {
					base_sha256: options.base_sha256,
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
					throw new CliDomainError("SYNTAX_INVALID_ARGUMENT", "Missing required --session flag", 400);
				}
				const fmt = options.export_format || options.format || "yaml";
				const ver = options.version ? `&version=${encodeURIComponent(String(options.version))}` : "";
				const payload = await this.getJson(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/export?export_format=${encodeURIComponent(String(fmt))}${ver}`,
				);
				if (options.out && typeof options.out === "string") {
					const fs = await import("node:fs/promises");
					let content: string;
					if (typeof payload === "string") {
						content = payload;
					} else if (
						payload &&
						typeof payload === "object" &&
						"content" in payload &&
						typeof (payload as any).content === "string"
					) {
						content = (payload as any).content;
					} else {
						content = JSON.stringify(payload, null, 2);
					}
					await fs.writeFile(options.out, content, "utf-8");
				}
				return payload;
			}

			case "promote": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError("SYNTAX_INVALID_ARGUMENT", "Missing required --session flag", 400);
				}
				const rationale = options.rationale;
				if (!rationale || String(rationale).trim().length < 10) {
					throw new CliDomainError("PROMOTION_RATIONALE_REQUIRED", "Promotion requires rationale (min 10 chars)", 400);
				}
				const body: Record<string, unknown> = {
					workspace_sha256: options.workspace_sha256,
					head_sha256: options.head_sha256,
					rationale: options.rationale,
					workspace: options.workspace,
					acknowledge_historical_contract_warning: Boolean(options.acknowledge_historical_contract_warning),
					historical_contract_rationale: options.historical_contract_rationale,
				};
				return await this.postJson(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/promote`,
					body,
				);
			}

			case "bundle export": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError("SYNTAX_INVALID_ARGUMENT", "Missing required --session flag", 400);
				}
				const body: Record<string, unknown> = {
					acknowledge_confidential_source_export: Boolean(options.acknowledge_confidential_source_export),
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
				const outPath = (options.out as string) || (options.out_path as string) || `${sid}.odvstudio.zip`;
				const fs = await import("node:fs/promises");
				await fs.writeFile(outPath, buffer);
				return { session_id: sid, bundle_path: outPath, size_bytes: buffer.byteLength };
			}

			case "bundle import": {
				const filePath = (options.file_path || options.file) as string;
				if (!filePath) {
					throw new CliDomainError("SYNTAX_INVALID_ARGUMENT", "Missing required --file flag", 400);
				}
				let fileData: Uint8Array;
				try {
					const fs = await import("node:fs/promises");
					fileData = await fs.readFile(filePath);
				} catch (err: unknown) {
					if (
						(err instanceof Error && "code" in err && (err as { code: string }).code === "ENOENT") ||
						String(err).includes("ENOENT")
					) {
						throw new CliDomainError("FILE_NOT_FOUND", `Bundle file not found: ${filePath}`, 404);
					}
					throw new CliDomainError("SYNTAX_INVALID_ARGUMENT", `Failed to read bundle file: ${err}`, 400);
				}
				const fileName = filePath.split(/[\\/]/).pop() || "bundle.zip";
				const formData = new FormData();
				formData.append("file", new Blob([fileData]), fileName);

				const queryParams = new URLSearchParams();
				if (options.acknowledge_unverified_import_attribution) {
					queryParams.set("acknowledge_unverified_import_attribution", "true");
				}
				if (options.acknowledgement_rationale) {
					queryParams.set("acknowledgement_rationale", String(options.acknowledgement_rationale));
				}
				const q = queryParams.toString() ? `?${queryParams.toString()}` : "";
				return await this.postFormData(`/api/v1/ontology/studio/sessions/bundle/import${q}`, formData);
			}

			case "bundle clone": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError("SYNTAX_INVALID_ARGUMENT", "Missing required --session flag", 400);
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
