/**
 * HTTP Executor & Platform Configuration (`config`) Command Dispatcher for `opendvctl`.
 *
 * Implements:
 * - `opendvctl config show`     -> `GET  ${baseUrl}/api/v1/platform/config`
 * - `opendvctl config set`      -> `PUT  ${baseUrl}/api/v1/platform/config`
 * - `opendvctl config validate` -> `POST ${baseUrl}/api/v1/platform/config/validate`
 */

import rawErrorCodes from "../../../schemas/v1/error-codes.json" with { type: "json" };
import { CliDomainError } from "./error-handler";
import { loadCliSpec } from "./spec";

type BunWithYaml = typeof Bun & {
	YAML?: {
		parse: (input: string) => unknown;
	};
};

/**
 * Parse a YAML or JSON string into a plain dictionary object.
 */
export function parseStructuredConfigText(
	rawText: string,
	sourceLabel = "harness_config",
): Record<string, unknown> {
	const trimmed = rawText.trim();
	if (!trimmed) {
		return {};
	}

	try {
		const parsedJson = JSON.parse(trimmed) as unknown;
		if (
			typeof parsedJson === "object" &&
			parsedJson !== null &&
			!Array.isArray(parsedJson)
		) {
			return { ...(parsedJson as Record<string, unknown>) };
		}
	} catch {
		// Fall through to Bun.YAML parser
	}

	const yamlParser = (Bun as BunWithYaml).YAML;
	if (yamlParser && typeof yamlParser.parse === "function") {
		try {
			const parsedYaml = yamlParser.parse(trimmed);
			if (
				typeof parsedYaml === "object" &&
				parsedYaml !== null &&
				!Array.isArray(parsedYaml)
			) {
				return { ...(parsedYaml as Record<string, unknown>) };
			}
		} catch (err) {
			const msg = err instanceof Error ? err.message : String(err);
			throw new CliDomainError(
				"INPUT_VALIDATION_ERROR",
				`Failed to parse ${sourceLabel}: ${msg}`,
				422,
			);
		}
	}

	throw new CliDomainError(
		"INPUT_VALIDATION_ERROR",
		`Invalid YAML/JSON object in ${sourceLabel}`,
		422,
	);
}

/**
 * Load base harness dictionary from `--harness-config` option or inline content if provided.
 */
export async function loadHarnessConfigOption(
	options: Record<string, unknown>,
): Promise<Record<string, unknown>> {
	if (
		typeof options.harness_config_content === "string" &&
		options.harness_config_content.trim().length > 0
	) {
		const parsed = parseStructuredConfigText(
			options.harness_config_content,
			String(options.harness_config_path || "harness_config"),
		);
		if (
			typeof parsed.harness === "object" &&
			parsed.harness !== null &&
			!Array.isArray(parsed.harness)
		) {
			return { ...(parsed.harness as Record<string, unknown>) };
		}
		return parsed;
	}

	const rawPath = options.harness_config;
	if (typeof rawPath === "string" && rawPath.trim().length > 0) {
		const filePath = rawPath.trim();
		const file = Bun.file(filePath);
		if (!(await file.exists())) {
			throw new CliDomainError(
				"RESOURCE_NOT_FOUND",
				`Harness config file not found: ${filePath}`,
				404,
			);
		}
		const content = await file.text();
		const parsed = parseStructuredConfigText(content, filePath);
		if (
			typeof parsed.harness === "object" &&
			parsed.harness !== null &&
			!Array.isArray(parsed.harness)
		) {
			return { ...(parsed.harness as Record<string, unknown>) };
		}
		return parsed;
	}

	return {};
}

/**
 * Merge CLI flags (`--provider`, `--reasoning-model`, `--fast-model`, `--auth-mode`,
 * `--api-key-env-var`, `--hold-policy`, `--ontology-policy`, `--stage-output-policy`,
 * `--max-turns`, `--timeout-seconds`) onto a candidate harness configuration dictionary.
 */
export async function buildHarnessCandidateFromOptions(
	options: Record<string, unknown>,
): Promise<Record<string, unknown>> {
	const harness = await loadHarnessConfigOption(options);

	if (options.provider !== undefined && options.provider !== null) {
		harness.provider = String(options.provider);
	}

	if (
		(options.reasoning_model !== undefined &&
			options.reasoning_model !== null) ||
		(options.fast_model !== undefined && options.fast_model !== null)
	) {
		const existingOverrides =
			typeof harness.model_overrides === "object" &&
			harness.model_overrides !== null
				? { ...(harness.model_overrides as Record<string, unknown>) }
				: {};
		if (
			options.reasoning_model !== undefined &&
			options.reasoning_model !== null
		) {
			existingOverrides.reasoning = String(options.reasoning_model);
		}
		if (options.fast_model !== undefined && options.fast_model !== null) {
			existingOverrides.fast = String(options.fast_model);
		}
		harness.model_overrides = existingOverrides;
	}

	if (
		(options.auth_mode !== undefined && options.auth_mode !== null) ||
		(options.api_key_env_var !== undefined && options.api_key_env_var !== null)
	) {
		const existingAuth =
			typeof harness.auth === "object" && harness.auth !== null
				? { ...(harness.auth as Record<string, unknown>) }
				: {};
		if (options.auth_mode !== undefined && options.auth_mode !== null) {
			existingAuth.auth_mode = String(options.auth_mode);
			existingAuth.mode = String(options.auth_mode);
		}
		if (
			options.api_key_env_var !== undefined &&
			options.api_key_env_var !== null
		) {
			existingAuth.api_key_env_var = String(options.api_key_env_var);
		}
		harness.auth = existingAuth;
	}

	if (options.hold_policy !== undefined && options.hold_policy !== null) {
		harness.steward_hold_policy = String(options.hold_policy);
	}

	if (
		options.ontology_policy !== undefined &&
		options.ontology_policy !== null
	) {
		harness.ontology_policy = String(options.ontology_policy);
	}

	if (
		options.stage_output_policy !== undefined &&
		options.stage_output_policy !== null
	) {
		harness.stage_output_policy = String(options.stage_output_policy);
	}

	if (
		(options.max_turns !== undefined && options.max_turns !== null) ||
		(options.timeout_seconds !== undefined && options.timeout_seconds !== null)
	) {
		const existingBudget =
			typeof harness.budget === "object" && harness.budget !== null
				? { ...(harness.budget as Record<string, unknown>) }
				: {};
		if (options.max_turns !== undefined && options.max_turns !== null) {
			existingBudget.max_turns = Number(options.max_turns);
		}
		if (
			options.timeout_seconds !== undefined &&
			options.timeout_seconds !== null
		) {
			existingBudget.timeout_seconds = Number(options.timeout_seconds);
		}
		harness.budget = existingBudget;
	}

	return harness;
}

/**
 * Build a `PlatformConfigUpdateRequest` payload from parsed CLI options.
 */
export async function buildConfigUpdateRequest(
	options: Record<string, unknown>,
): Promise<Record<string, unknown>> {
	const harness = await buildHarnessCandidateFromOptions(options);
	return {
		harness,
		persist: options.persist !== false,
	};
}

/**
 * Build a `PlatformConfigValidateRequest` payload from parsed CLI options.
 */
export async function buildConfigValidateRequest(
	options: Record<string, unknown>,
): Promise<Record<string, unknown>> {
	const harness = await buildHarnessCandidateFromOptions(options);
	return {
		harness,
	};
}

export async function parseHttpErrorResponse(
	res: Response,
): Promise<CliDomainError> {
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
					} else {
						const catalog = (rawErrorCodes as {
							error_codes?: Array<{ code: string; problem_type_uri: string }>;
						}).error_codes ?? [];
						const catMatch = catalog.find((e) => e.problem_type_uri === errBody.type);
						if (catMatch) {
							code = catMatch.code;
						}
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
		// Ignore body read error
	}
	return new CliDomainError(code, msg, res.status);
}

/**
 * Execute `opendvctl config [show|set|validate]` against the Platform REST API.
 * Returns `[exitCode, data]`.
 */
export async function executeConfigCommand(
	baseUrl: string,
	subcommand: string | undefined,
	options: Record<string, unknown>,
	headers: Record<string, string>,
	fetchImpl: typeof fetch = globalThis.fetch.bind(globalThis),
): Promise<[number, Record<string, unknown>]> {
	const normalizedBase = baseUrl.replace(/\/+$/, "");
	const sub = (subcommand ?? "show").toLowerCase();
	const jsonHeaders: Record<string, string> = {
		"Content-Type": "application/json",
		Accept: "application/json",
		...headers,
	};

	if (sub === "show") {
		const res = await fetchImpl(`${normalizedBase}/api/v1/platform/config`, {
			method: "GET",
			headers: jsonHeaders,
		});
		if (!res.ok) {
			throw await parseHttpErrorResponse(res);
		}
		const data = (await res.json()) as Record<string, unknown>;
		return [0, data];
	}

	if (sub === "set") {
		const body = await buildConfigUpdateRequest(options);
		const res = await fetchImpl(`${normalizedBase}/api/v1/platform/config`, {
			method: "PUT",
			headers: jsonHeaders,
			body: JSON.stringify(body),
		});
		if (!res.ok) {
			throw await parseHttpErrorResponse(res);
		}
		const data = (await res.json()) as Record<string, unknown>;
		return [0, data];
	}

	if (sub === "validate") {
		const body = await buildConfigValidateRequest(options);
		const res = await fetchImpl(
			`${normalizedBase}/api/v1/platform/config/validate`,
			{
				method: "POST",
				headers: jsonHeaders,
				body: JSON.stringify(body),
			},
		);
		if (!res.ok) {
			throw await parseHttpErrorResponse(res);
		}
		const data = (await res.json()) as Record<string, unknown>;
		const passed = Boolean(data.passed);
		return [passed ? 0 : 1, data];
	}

	throw new CliDomainError(
		"INVALID_REQUEST",
		`Unknown config subcommand: ${subcommand}`,
		400,
	);
}
