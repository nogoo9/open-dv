/**
 * Remote execution handlers for source and config commands.
 */

import { CliDomainError } from "../error-handler";
import { executeConfigCommand } from "../http-executor";
import { isClientUploadableFile, stageCasBlob } from "./cas";
import type { RemoteHttpClient } from "./http-client";

export async function handleSource(
	client: RemoteHttpClient,
	subcommand: string | undefined,
	enrichedOptions: Record<string, unknown>,
): Promise<unknown> {
	const action = subcommand || "inspect";
	if (action !== "inspect") {
		throw new CliDomainError(
			"INVALID_REQUEST",
			`Unknown source subcommand: '${action}'`,
			400,
		);
	}

	const rawUri = enrichedOptions.uri
		? String(enrichedOptions.uri).trim()
		: "";
	if (!rawUri) {
		throw new CliDomainError(
			"SYNTAX_INVALID_ARGUMENT",
			"Missing required --uri flag",
			400,
		);
	}

	if (enrichedOptions.credentials_ref) {
		const cred = String(enrichedOptions.credentials_ref).trim();
		if (!/^(env|secret):[A-Za-z0-9_-]+$/.test(cred)) {
			throw new CliDomainError(
				"SYNTAX_INVALID_ARGUMENT",
				`Invalid credentials_ref '${cred}'. Must match '^(env|secret):[A-Za-z0-9_-]+$'`,
				400,
			);
		}
	}

	let effUri = rawUri;
	let casHash: string | undefined = enrichedOptions.cas_hash
		? String(enrichedOptions.cas_hash).trim().toLowerCase()
		: undefined;

	let origAdapter = enrichedOptions.adapter
		? String(enrichedOptions.adapter)
		: undefined;
	// Infer detected format strictly from file extension, ignoring CLI global --format
	const lowerUri = rawUri.toLowerCase();
	const pathPart = lowerUri.split(/[?#]/)[0] ?? "";
	let detectedFormat: string | undefined;

	if (/\.csv$/i.test(pathPart)) {
		detectedFormat = "csv";
		if (!origAdapter) origAdapter = "filesystem";
	} else if (/\.tsv$/i.test(pathPart)) {
		detectedFormat = "tsv";
		if (!origAdapter) origAdapter = "filesystem";
	} else if (/\.(parquet|pq)$/i.test(pathPart)) {
		detectedFormat = "parquet";
		if (!origAdapter) origAdapter = "filesystem";
	} else if (/\.jsonl$/i.test(pathPart)) {
		detectedFormat = "jsonl";
		if (!origAdapter) origAdapter = "filesystem";
	} else if (/\.json$/i.test(pathPart)) {
		detectedFormat = "json";
		if (!origAdapter) origAdapter = "filesystem";
	} else if (
		lowerUri.startsWith("sqlite:") ||
		/\.(sqlite|sqlite3|db)$/i.test(pathPart)
	) {
		detectedFormat = "sqlite";
		if (!origAdapter) origAdapter = "sqlite";
	} else if (
		lowerUri.startsWith("duckdb:") ||
		/\.(duckdb|ddb)$/i.test(pathPart)
	) {
		detectedFormat = "duckdb";
		if (!origAdapter) origAdapter = "duckdb";
	}

	if (!casHash && isClientUploadableFile(rawUri)) {
		casHash = await stageCasBlob(client, rawUri);
		if (casHash) {
			const baseName = rawUri.split(/[?#]/)[0]?.split("/").pop()?.split("\\").pop() ?? "";
			effUri = baseName ? `cas://${casHash}/${baseName}` : `cas://${casHash}`;
		}
	}

	const body: Record<string, unknown> = {
		uri: effUri,
		options: {},
	};
	if (origAdapter) {
		body.adapter = origAdapter;
	}
	if (detectedFormat) {
		(body.options as Record<string, unknown>).format = detectedFormat;
		(body.options as Record<string, unknown>).source_format = detectedFormat;
	}

	if (casHash) {
		body.cas_hash = casHash;
		(body.options as Record<string, unknown>).cas_hash = casHash;
	}

	if (enrichedOptions.credentials_ref) {
		body.credentials_ref = String(enrichedOptions.credentials_ref).trim();
	}

	if (enrichedOptions.namespace) {
		const parts = String(enrichedOptions.namespace).split(".");
		if (parts.length >= 3) {
			body.namespace = {
				catalog: parts[0],
				schema_name: parts[1],
			};
			if (!enrichedOptions.table) {
				const tbl = parts[2];
				body.options = {
					...(body.options as Record<string, unknown>),
					table: tbl,
					table_selection: {
						mode: "EXPLICIT",
						tables: [tbl],
					},
				};
			}
		} else if (parts.length === 2) {
			body.namespace = {
				catalog: parts[0],
				schema_name: parts[1],
			};
		} else {
			body.namespace = {
				catalog: null,
				schema_name: parts[0],
			};
		}
	}

	if (enrichedOptions.table) {
		const tbl = String(enrichedOptions.table);
		body.options = {
			...(body.options as Record<string, unknown>),
			table: tbl,
			table_selection: {
				mode: "EXPLICIT",
				tables: [tbl],
			},
		};
	}

	return await client.postJson("/api/v1/sources/inspect", body);
}

export async function handleConfig(
	client: RemoteHttpClient,
	subcommand: string | undefined,
	enrichedOptions: Record<string, unknown>,
): Promise<{ exitCode: number; payload: unknown }> {
	const [cfgExitCode, cfgData] = await executeConfigCommand(
		client.baseUrl,
		subcommand,
		enrichedOptions,
		client.getHeaders(),
		client.fetchImpl,
	);
	return { exitCode: cfgExitCode, payload: cfgData };
}
