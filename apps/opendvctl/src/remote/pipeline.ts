/**
 * Remote execution handlers for pipeline, run, hold, sandbox, compile, verify, and profile.
 *
 * buildSingleRunPayload has been decomposed into ./pipeline-payload.ts per ADR-020.
 */

import type { OpenDVPlatformClient } from "@opendv/protocol-ts";
import { CliDomainError } from "../error-handler";
import { followEventStream } from "../sse-follower";
import { handleDvSchema } from "./dv-schema";
import type { RemoteHttpClient } from "./http-client";
import { buildSingleRunPayload } from "./pipeline-payload";

export { buildSingleRunPayload };

export async function handleProfile(
	client: RemoteHttpClient,
	enrichedOptions: Record<string, unknown>,
): Promise<unknown> {
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
	return await client.postJson("/api/v1/profile", body);
}

export async function handleCompile(
	client: RemoteHttpClient,
	enrichedOptions: Record<string, unknown>,
): Promise<unknown> {
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
	return await client.postJson("/api/v1/compile", body);
}

export async function handleVerify(
	client: RemoteHttpClient,
	enrichedOptions: Record<string, unknown>,
): Promise<unknown> {
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
	return await client.postJson("/api/v1/verify", body);
}

export async function handleSandbox(
	client: RemoteHttpClient,
	enrichedOptions: Record<string, unknown>,
): Promise<unknown> {
	const runId = enrichedOptions.run_id as string | undefined;
	const hasSql = Boolean(
		enrichedOptions.sql && String(enrichedOptions.sql).trim().length > 0,
	);
	const body: Record<string, unknown> = {
		query_type: hasSql
			? "CUSTOM_SQL"
			: (enrichedOptions.query_type ?? "ADVERSARIAL_BATCHES_1_TO_4"),
		assertion_cutoff_ts:
			enrichedOptions.cutoff ??
			enrichedOptions.assertion_cutoff_ts ??
			"2026-11-15 23:59:59",
	};
	if (runId) body.run_id = runId;
	if (enrichedOptions.contract_path || enrichedOptions.contract)
		body.contract_path =
			enrichedOptions.contract_path || enrichedOptions.contract;
	if (enrichedOptions.contract_content)
		body.contract_content = enrichedOptions.contract_content;
	if (enrichedOptions.canonical_ir_path || enrichedOptions.canonical_ir)
		body.canonical_ir_path =
			enrichedOptions.canonical_ir_path || enrichedOptions.canonical_ir;
	if (enrichedOptions.canonical_ir_content)
		body.canonical_ir_content = enrichedOptions.canonical_ir_content;
	if (enrichedOptions.sql) body.sql = String(enrichedOptions.sql);

	if (runId) {
		if (enrichedOptions.sql) {
			return await client.postJson(
				`/api/v1/runs/${encodeURIComponent(runId)}/sandbox`,
				body,
			);
		}
		return await client.getJson(
			`/api/v1/runs/${encodeURIComponent(runId)}/sandbox`,
		);
	}
	return await client.postJson("/api/v1/sandbox", body);
}

function buildStreamWriters(
	client: RemoteHttpClient,
	enrichedOptions: Record<string, unknown>,
): {
	writer: (line: string) => void;
	tokenWriter?: (tok: string) => void;
} {
	const isJsonOut =
		enrichedOptions.json === true || enrichedOptions.format === "json";
	if (!isJsonOut) {
		return { writer: client.writer };
	}
	return {
		writer: client.errWriter,
		tokenWriter: (tok: string) => {
			if (typeof process !== "undefined" && process.stderr?.write) {
				process.stderr.write(tok);
			} else {
				client.errWriter(tok);
			}
		},
	};
}

export async function handlePipeline(
	client: RemoteHttpClient,
	subcommand: string | undefined,
	enrichedOptions: Record<string, unknown>,
): Promise<unknown> {
	const isSingleRun = Boolean(
		enrichedOptions.blueprint ||
			enrichedOptions.ontology ||
			(enrichedOptions.source && !enrichedOptions.input) ||
			(enrichedOptions.contract && !enrichedOptions.input),
	);

	if (
		!isSingleRun &&
		!enrichedOptions.input &&
		!enrichedOptions.blueprint &&
		!enrichedOptions.ontology
	) {
		throw new CliDomainError(
			"SYNTAX_INVALID_ARGUMENT",
			`One of --input, --blueprint, or --ontology is required for 'pipeline ${subcommand}'`,
			400,
		);
	}

	let payload: unknown;

	if (subcommand === "validate") {
		if (isSingleRun) {
			const body = await buildSingleRunPayload(client, enrichedOptions);
			try {
				payload = await client.postJson("/api/v1/runs/validate", body);
			} catch (err) {
				if (err instanceof CliDomainError && err.httpStatus === 404) {
					payload = await client.postJson(
						"/api/v1/pipelines/validate",
						body,
					);
				} else {
					throw err;
				}
			}
			return payload;
		}

		const body: Record<string, unknown> = {};
		if (enrichedOptions.input_spec_content) {
			if (typeof enrichedOptions.input_spec_content === "object") {
				body.input_spec = enrichedOptions.input_spec_content;
			} else if (typeof enrichedOptions.input_spec_content === "string") {
				try {
					body.input_spec = JSON.parse(
						enrichedOptions.input_spec_content,
					);
				} catch {
					body.input_spec_content = enrichedOptions.input_spec_content;
				}
			}
		} else if (enrichedOptions.input_spec_path || enrichedOptions.input) {
			body.input_spec_path =
				enrichedOptions.input_spec_path || enrichedOptions.input;
		}
		if (enrichedOptions.initial_contract_content) {
			body.initial_contract = enrichedOptions.initial_contract_content;
		} else if (enrichedOptions.initial_contract_path) {
			body.initial_contract_path = enrichedOptions.initial_contract_path;
		}
		if (enrichedOptions.provider)
			body.provider_override = enrichedOptions.provider;
		if (enrichedOptions.hold_policy)
			body.steward_hold_policy = enrichedOptions.hold_policy;
		if (enrichedOptions.unstructured_only !== undefined)
			body.unstructured_only = Boolean(enrichedOptions.unstructured_only);
		payload = await client.postJson("/api/v1/pipelines/validate", body);
		return payload;
	}

	if (subcommand === "run") {
		if (isSingleRun) {
			const body = await buildSingleRunPayload(client, enrichedOptions);
			const runHandle = (await client.postJson(
				"/api/v1/runs",
				body,
			)) as Record<string, unknown>;

			if (runHandle.status_url && runHandle.run_id) {
				const rawStreamUrl =
					(runHandle.events_sse_url as string) ||
					(runHandle.event_stream_url as string) ||
					`/api/v1/runs/${encodeURIComponent(String(runHandle.run_id))}/events`;
				const streamUrl = rawStreamUrl.startsWith("http")
					? rawStreamUrl
					: `${client.baseUrl}${rawStreamUrl.startsWith("/") ? "" : "/"}${rawStreamUrl}`;

				const streamWriters = buildStreamWriters(client, enrichedOptions);
				const streamResult = await followEventStream({
					streamUrl,
					headers: client.getHeaders(),
					streamEnabled: enrichedOptions.stream !== false,
					tokensEnabled: Boolean(enrichedOptions.stream_tokens),
					writer: streamWriters.writer,
					tokenWriter: streamWriters.tokenWriter,
					fetchImpl: client.fetchImpl,
				});

				try {
					const report = await client.getJson(
						`/api/v1/runs/${encodeURIComponent(String(runHandle.run_id))}/report`,
					);
					payload = report;
				} catch {
					payload = { ...runHandle, ...streamResult };
				}
			} else {
				payload = runHandle;
			}
			return payload;
		}

		const body: Record<string, unknown> = {};
		if (enrichedOptions.input_spec_content) {
			if (typeof enrichedOptions.input_spec_content === "object") {
				body.input_spec = enrichedOptions.input_spec_content;
			} else if (typeof enrichedOptions.input_spec_content === "string") {
				try {
					body.input_spec = JSON.parse(
						enrichedOptions.input_spec_content,
					);
				} catch {
					body.input_spec_content = enrichedOptions.input_spec_content;
				}
			}
		} else if (enrichedOptions.input_spec_path || enrichedOptions.input) {
			body.input_spec_path =
				enrichedOptions.input_spec_path || enrichedOptions.input;
		}
		if (enrichedOptions.initial_contract_content) {
			body.initial_contract = enrichedOptions.initial_contract_content;
		} else if (enrichedOptions.initial_contract_path) {
			body.initial_contract_path = enrichedOptions.initial_contract_path;
		}
		if (enrichedOptions.provider)
			body.provider_override = enrichedOptions.provider;
		if (enrichedOptions.hold_policy)
			body.steward_hold_policy = enrichedOptions.hold_policy;
		if (enrichedOptions.project_id)
			body.project_id = enrichedOptions.project_id;
		if (enrichedOptions.output_dir)
			body.output_dir = enrichedOptions.output_dir;
		if (enrichedOptions.unstructured_only !== undefined)
			body.unstructured_only = Boolean(enrichedOptions.unstructured_only);

		const runHandle = (await client.postJson(
			"/api/v1/pipelines/runs",
			body,
		)) as {
			run_id: string;
			event_stream_url?: string;
			events_sse_url?: string;
		};

		const rawStreamUrl =
			runHandle.events_sse_url ||
			runHandle.event_stream_url ||
			`/api/v1/runs/${encodeURIComponent(runHandle.run_id)}/events`;
		const streamUrl = rawStreamUrl.startsWith("http")
			? rawStreamUrl
			: `${client.baseUrl}${rawStreamUrl.startsWith("/") ? "" : "/"}${rawStreamUrl}`;

		const streamWriters = buildStreamWriters(client, enrichedOptions);
		const streamResult = await followEventStream({
			streamUrl,
			headers: client.getHeaders(),
			streamEnabled: enrichedOptions.stream !== false,
			tokensEnabled: Boolean(enrichedOptions.stream_tokens),
			writer: streamWriters.writer,
			tokenWriter: streamWriters.tokenWriter,
			fetchImpl: client.fetchImpl,
		});

		try {
			const report = await client.getJson(
				`/api/v1/runs/${encodeURIComponent(runHandle.run_id)}/report`,
			);
			payload = report;
		} catch {
			payload = { ...runHandle, ...streamResult };
		}
		return payload;
	}

	throw new CliDomainError(
		"INVALID_REQUEST",
		`Unsupported pipeline subcommand: ${subcommand}`,
		400,
	);
}

export async function handleRun(
	client: RemoteHttpClient,
	subcommand: string | undefined,
	enrichedOptions: Record<string, unknown>,
): Promise<unknown> {
	const runId = enrichedOptions.run_id as string | undefined;
	let payload: unknown;
	if (subcommand === "list") {
		const projId = (enrichedOptions.project_id ||
			enrichedOptions.project) as string | undefined;
		const query = projId
			? `?project_id=${encodeURIComponent(projId)}`
			: "";
		payload = await client.getJson(`/api/v1/runs${query}`);
	} else if (subcommand === "get") {
		if (!runId) throw new Error("Missing --run-id");
		payload = await client.client.getRun(runId);
	} else if (subcommand === "create") {
		const allowedKeys = new Set([
			"project_id",
			"harness_provider",
			"model_tier_override",
			"max_repair_retries",
			"run_sandbox_batches",
			"execute_duckdb_sandbox",
			"assertion_cutoff_ts",
			"initial_contract_content",
			"initial_contract",
			"blueprint_content",
		]);
		const filtered: Record<string, unknown> = {};
		for (const [k, v] of Object.entries(enrichedOptions)) {
			if (allowedKeys.has(k) && v !== undefined && !k.endsWith("_path")) {
				filtered[k] = v;
			}
		}
		if (enrichedOptions.contract_content) {
			filtered.initial_contract_content = enrichedOptions.contract_content;
		} else if (enrichedOptions.initial_contract_content) {
			filtered.initial_contract_content = enrichedOptions.initial_contract_content;
		}
		if (enrichedOptions.harness) {
			filtered.harness_provider = enrichedOptions.harness;
		} else if (enrichedOptions.provider && !filtered.harness_provider) {
			filtered.harness_provider = enrichedOptions.provider;
		}
		if (enrichedOptions.project && !filtered.project_id) {
			filtered.project_id = enrichedOptions.project;
		}
		for (const key of Object.keys(filtered)) {
			if (key.endsWith("_path")) {
				delete filtered[key];
			}
		}
		payload = await client.postJson("/api/v1/runs", filtered);
	} else if (subcommand === "abort") {
		if (!runId) throw new Error("Missing --run-id");
		payload = await client.postJson(
			`/api/v1/runs/${encodeURIComponent(runId)}/abort`,
			{},
		);
	} else if (subcommand === "events") {
		if (!runId) throw new Error("Missing --run-id");
		const lastEventId = (enrichedOptions.last_event_id ||
			enrichedOptions.last_id) as string | undefined;
		const role = enrichedOptions.role as string | undefined;
		const params = new URLSearchParams();
		if (lastEventId) params.set("last_event_id", lastEventId);
		if (role) params.set("role", role);
		const qs = params.toString() ? `?${params.toString()}` : "";
		payload = await client.getJson(
			`/api/v1/runs/${encodeURIComponent(runId)}/events${qs}`,
		);
	} else if (subcommand === "explorer") {
		if (!runId) throw new Error("Missing --run-id");
		payload = await client.getJson(
			`/api/v1/runs/${encodeURIComponent(runId)}/explorer`,
		);
	} else if (subcommand === "dv-schema") {
		payload = await handleDvSchema(client, runId, enrichedOptions);
	} else if (subcommand === "artifact") {
		const artifactId = (enrichedOptions.sha256 ||
			enrichedOptions.artifact_id) as string;
		if (!artifactId) throw new Error("Missing --sha256 or --artifact-id");
		payload = await client.client.getArtifact(artifactId);
	} else {
		throw new CliDomainError(
			"INVALID_REQUEST",
			`Unknown run subcommand: ${subcommand}`,
			400,
		);
	}
	return payload;
}

export async function handleHold(
	client: RemoteHttpClient,
	subcommand: string | undefined,
	enrichedOptions: Record<string, unknown>,
): Promise<unknown> {
	const runId = enrichedOptions.run_id as string | undefined;
	let payload: unknown;
	if (subcommand === "list") {
		if (!runId) throw new Error("Missing --run-id");
		payload = await client.getJson(
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
		const holdId = (enrichedOptions.hold_id as string | undefined) ?? null;
		const eventId = (enrichedOptions.event_id as string | undefined) ?? null;
		const candId =
			((enrichedOptions.candidate_id ||
				enrichedOptions.selected_candidate_id) as string | undefined) ??
			null;
		const notes =
			((enrichedOptions.notes ||
				enrichedOptions.resolution_notes) as string | undefined) ?? null;
		const stewardId =
			(enrichedOptions.steward_id as string | undefined) ?? "steward-captain";

		payload = await client.postJson(
			`/api/v1/runs/${encodeURIComponent(runId)}/decisions`,
			{
				action,
				decision_type: "RESOLVE_CAPTAIN_HOLD",
				steward_id: stewardId,
				hold_id: holdId,
				event_id: eventId,
				selected_candidate_id: candId,
				resolution_notes: notes,
			},
		);
	} else {
		throw new CliDomainError(
			"INVALID_REQUEST",
			`Unknown hold subcommand: ${subcommand}`,
			400,
		);
	}
	return payload;
}
