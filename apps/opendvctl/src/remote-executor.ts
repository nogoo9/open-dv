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

		const serverVersion = health.contract_version ?? "unknown";
		if (serverVersion !== spec.contract_version) {
			throw new CliDomainError(
				"CONTRACT_VERSION_MISMATCH",
				`Contract version mismatch (client: ${spec.contract_version}, server: ${serverVersion})`,
				422,
			);
		}
	}

	private async postJson(endpoint: string, payload: unknown): Promise<unknown> {
		const res = await this.fetchImpl(`${this.baseUrl}${endpoint}`, {
			method: "POST",
			headers: this.getHeaders(),
			body: JSON.stringify(payload),
		});

		if (!res.ok) {
			let code = "INVALID_REQUEST";
			let msg = `HTTP ${res.status}`;
			try {
				const errBody = (await res.json()) as Record<string, unknown>;
				if (typeof errBody.error_code === "string") {
					code = errBody.error_code;
				}
				if (typeof errBody.detail === "string") {
					msg = errBody.detail;
				} else if (typeof errBody.message === "string") {
					msg = errBody.message;
				}
			} catch {
				msg = await res.text();
			}
			throw new CliDomainError(code, msg, res.status);
		}

		return res.json();
	}

	private async getJson(endpoint: string): Promise<unknown> {
		const res = await this.fetchImpl(`${this.baseUrl}${endpoint}`, {
			headers: this.getHeaders(),
		});

		if (!res.ok) {
			let code = "INVALID_REQUEST";
			let msg = `HTTP ${res.status}`;
			try {
				const errBody = (await res.json()) as Record<string, unknown>;
				if (typeof errBody.error_code === "string") {
					code = errBody.error_code;
				}
				if (typeof errBody.detail === "string") {
					msg = errBody.detail;
				} else if (typeof errBody.message === "string") {
					msg = errBody.message;
				}
			} catch {
				msg = await res.text();
			}
			throw new CliDomainError(code, msg, res.status);
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
				payload = await this.postJson("/api/v1/profile", enrichedOptions);
				break;
			}
			case "compile": {
				payload = await this.postJson("/api/v1/compile", enrichedOptions);
				break;
			}
			case "verify": {
				payload = await this.postJson("/api/v1/verify", enrichedOptions);
				break;
			}
			case "sandbox": {
				const runId = enrichedOptions.run_id as string | undefined;
				if (runId) {
					payload = await this.getJson(
						`/api/v1/runs/${encodeURIComponent(runId)}/sandbox`,
					);
				} else {
					payload = await this.postJson("/api/v1/sandbox", enrichedOptions);
				}
				break;
			}
			case "pipeline": {
				if (subcommand === "validate") {
					payload = await this.postJson(
						"/api/v1/pipelines/validate",
						enrichedOptions,
					);
				} else if (subcommand === "run") {
					// Async run lifecycle (Decision D2)
					const runHandle = (await this.postJson(
						"/api/v1/pipelines/runs",
						enrichedOptions,
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
							? ((enrichedOptions.action as string)?.toUpperCase() ?? "APPROVE")
							: subcommand.toUpperCase();
					const action: "APPROVE" | "REJECT" | "ANSWER" =
						rawAction === "REJECT" || rawAction === "ANSWER"
							? rawAction
							: "APPROVE";

					payload = await this.client.submitDecision(runId, {
						action,
						hold_id: enrichedOptions.hold_id as string | undefined,
						selected_candidate_id: enrichedOptions.selected_candidate_id as
							| string
							| undefined,
						resolution_notes: enrichedOptions.resolution_notes as
							| string
							| undefined,
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
