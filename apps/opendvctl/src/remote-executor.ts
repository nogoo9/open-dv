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

import { OpenDVPlatformClient } from "@opendv/protocol-ts";
import { CliDomainError, errorHandler } from "./error-handler";
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
		this.role = config.role || "INTERNAL_MODELER";
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

	getHeaders(): Record<string, string> {
		return {
			"Content-Type": "application/json",
			Accept: "application/json",
			"X-OpenDV-Role": this.role,
		};
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
		let code = "INVALID_REQUEST";
		let msg = `HTTP ${res.status}`;
		try {
			const text = await res.text();
			if (text) {
				try {
					const errBody = JSON.parse(text) as Record<string, unknown>;
					if (typeof errBody.error_code === "string") {
						code = errBody.error_code;
					} else if (typeof errBody.type === "string") {
						const spec = loadCliSpec();
						const match = spec.error_map.find(
							(e) => e.problem_type_uri === errBody.type,
						);
						if (match) {
							code = match.error_code;
						}
					}
					if (typeof errBody.detail === "string") {
						msg = errBody.detail;
					} else if (typeof errBody.message === "string") {
						msg = errBody.message;
					} else if (typeof errBody.title === "string") {
						msg = errBody.title;
					}
				} catch {
					msg = text;
				}
			}
		} catch {
			// Body reading failed
		}
		return new CliDomainError(code, msg, res.status);
	}

	private async postJson(endpoint: string, payload: unknown): Promise<unknown> {
		const res = await this.fetchImpl(`${this.baseUrl}${endpoint}`, {
			method: "POST",
			headers: this.getHeaders(),
			body: JSON.stringify(payload),
		});

		if (!res.ok) {
			throw await this.parseErrorResponse(res);
		}

		return res.json();
	}

	private async getJson(endpoint: string): Promise<unknown> {
		const res = await this.fetchImpl(`${this.baseUrl}${endpoint}`, {
			headers: this.getHeaders(),
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
			const subDef = cmdDef.subcommands.find((s) => s.name === subcommand);
			if (subDef?.options) {
				specOptions.push(...subDef.options);
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
					if (enrichedOptions.input_spec_content)
						body.input_spec = enrichedOptions.input_spec_content;
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
					if (enrichedOptions.input_spec_content)
						body.input_spec = enrichedOptions.input_spec_content;
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
				if (sub === "suggest") {
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
			default:
				throw new CliDomainError(
					"INVALID_REQUEST",
					`Unrecognized command: ${command}`,
					400,
				);
		}

		const exitCode = errorHandler.resolvePayloadExitCode(payload);
		return { exitCode, payload };
	}
}
