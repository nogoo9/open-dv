/**
 * Platform & Agent Harness Configuration Actions (`usePlatformConfigActions.ts`)
 *
 * Typed REST client actions and deterministic baseline configuration factory
 * for `/api/v1/platform/config` and `/api/v1/platform/config/validate`.
 * Zero hardcoded loopback IP or forbidden ports (REQ-CFG-01).
 */

import type {
	HarnessConfigDTO,
	HarnessProviderId,
	PlatformConfigResponse,
	PlatformConfigUpdateRequest,
	PlatformConfigValidationResponse,
	ProviderReadinessDTO,
} from "@opendv/protocol-ts";
import type { CSSProperties } from "react";
import { EXPLORER_COLORS, EXPLORER_FONTS } from "../design-tokens";

export const SETTINGS_FIELDSET_STYLE: CSSProperties = {
	margin: 0,
	padding: "18px 20px",
	backgroundColor: EXPLORER_COLORS.bgSurface,
	border: `1px solid ${EXPLORER_COLORS.borderLine}`,
	borderRadius: "8px",
	display: "flex",
	flexDirection: "column",
	gap: "14px",
};

export const SETTINGS_LEGEND_STYLE: CSSProperties = {
	padding: "0 6px",
	fontWeight: 600,
	fontSize: "14px",
	color: EXPLORER_COLORS.inkPrimary,
};

export const SETTINGS_FIELD_COL_STYLE: CSSProperties = {
	display: "flex",
	flexDirection: "column",
	gap: "5px",
};

export const SETTINGS_LABEL_STYLE: CSSProperties = {
	fontWeight: 600,
	fontSize: "12px",
	color: EXPLORER_COLORS.inkLabel,
};

export const SETTINGS_INPUT_STYLE: CSSProperties = {
	padding: "7px 10px",
	fontFamily: EXPLORER_FONTS.mono,
	fontSize: "12.5px",
	border: `1px solid ${EXPLORER_COLORS.borderLine}`,
	borderRadius: "6px",
	backgroundColor: EXPLORER_COLORS.bgSurface,
	color: EXPLORER_COLORS.inkPrimary,
};

export type {
	HarnessConfigDTO,
	HarnessProviderId,
	PlatformConfigResponse,
	PlatformConfigUpdateRequest,
	PlatformConfigValidationResponse,
	ProviderReadinessDTO,
};

export const HARNESS_PROVIDERS: readonly HarnessProviderId[] = [
	"agy",
	"agy-cli",
	"antigravity",
	"claude",
	"claude-cli",
	"claude-code",
] as const;

export const CANONICAL_PERSONAS = [
	"vault-first-mate",
	"scout-source",
	"scout-unstructured",
	"ship-vault",
] as const;

export type CanonicalPersonaId = (typeof CANONICAL_PERSONAS)[number];

export const PERSONA_TIER_MAP: Readonly<
	Record<CanonicalPersonaId, "reasoning" | "fast">
> = {
	"vault-first-mate": "reasoning",
	"scout-source": "fast",
	"scout-unstructured": "fast",
	"ship-vault": "reasoning",
};

export function createDefaultProviders(): ProviderReadinessDTO[] {
	return [
		{
			provider: "agy",
			execution_mode: "sdk",
			available: true,
			binary_name: null,
			binary_found: false,
			sdk_installed: true,
			auth_mode: "api_key",
			api_key_env_var: "GEMINI_API_KEY",
			credentials_present: true,
			default_reasoning_model: "gemini-4-argon-high",
			default_fast_model: "gemini-3.8-flash-high",
			status_message: "Google Antigravity SDK ready",
		},
		{
			provider: "agy-cli",
			execution_mode: "cli",
			available: true,
			binary_name: "agy",
			binary_found: true,
			sdk_installed: false,
			auth_mode: "cli_session",
			api_key_env_var: "GEMINI_API_KEY",
			credentials_present: true,
			default_reasoning_model: "gemini-4-argon-high",
			default_fast_model: "gemini-3.8-flash-high",
			status_message: "Antigravity CLI binary available",
		},
		{
			provider: "antigravity",
			execution_mode: "sdk",
			available: true,
			binary_name: null,
			binary_found: false,
			sdk_installed: true,
			auth_mode: "api_key",
			api_key_env_var: "GEMINI_API_KEY",
			credentials_present: true,
			default_reasoning_model: "gemini-4-argon-high",
			default_fast_model: "gemini-3.8-flash-high",
			status_message: "Antigravity adapter ready",
		},
		{
			provider: "claude",
			execution_mode: "sdk",
			available: true,
			binary_name: null,
			binary_found: false,
			sdk_installed: true,
			auth_mode: "api_key",
			api_key_env_var: "ANTHROPIC_API_KEY",
			credentials_present: false,
			default_reasoning_model: "claude-opus-4-6",
			default_fast_model: "claude-haiku-4-5",
			status_message: "Claude Agent SDK installed (awaiting ANTHROPIC_API_KEY)",
		},
		{
			provider: "claude-cli",
			execution_mode: "cli",
			available: false,
			binary_name: "claude",
			binary_found: false,
			sdk_installed: false,
			auth_mode: "cli_session",
			api_key_env_var: "ANTHROPIC_API_KEY",
			credentials_present: false,
			default_reasoning_model: "claude-opus-4-6",
			default_fast_model: "claude-haiku-4-5",
			status_message: "Claude CLI binary not found on PATH",
		},
		{
			provider: "claude-code",
			execution_mode: "sdk",
			available: true,
			binary_name: null,
			binary_found: false,
			sdk_installed: true,
			auth_mode: "api_key",
			api_key_env_var: "ANTHROPIC_API_KEY",
			credentials_present: false,
			default_reasoning_model: "claude-opus-4-6",
			default_fast_model: "claude-haiku-4-5",
			status_message: "Claude Code SDK ready",
		},
	];
}

export function resolvePersonaModels(
	harness: HarnessConfigDTO,
	providers: readonly ProviderReadinessDTO[] = createDefaultProviders(),
): Record<string, string> {
	const providerEntry = providers.find((p) => p.provider === harness.provider);
	const isClaudeFamily = harness.provider.startsWith("claude");
	const fallbackReasoning =
		providerEntry?.default_reasoning_model ??
		(isClaudeFamily ? "claude-opus-4-6" : "gemini-4-argon-high");
	const fallbackFast =
		providerEntry?.default_fast_model ??
		(isClaudeFamily ? "claude-haiku-4-5" : "gemini-3.8-flash-high");

	const reasoningModel =
		harness.model_overrides?.reasoning?.trim() || fallbackReasoning;
	const fastModel = harness.model_overrides?.fast?.trim() || fallbackFast;

	const resolved: Record<string, string> = {};
	for (const persona of CANONICAL_PERSONAS) {
		const explicitPersonaModel = harness.agent_models?.[persona]?.trim();
		if (explicitPersonaModel) {
			resolved[persona] = explicitPersonaModel;
		} else {
			const tier = PERSONA_TIER_MAP[persona];
			resolved[persona] = tier === "reasoning" ? reasoningModel : fastModel;
		}
	}
	return resolved;
}

export function createDefaultPlatformConfig(): PlatformConfigResponse {
	const providers = createDefaultProviders();
	const harness: HarnessConfigDTO = {
		provider: "agy",
		model_overrides: {
			reasoning: "gemini-4-argon-high",
			fast: "gemini-3.8-flash-high",
		},
		agent_models: {},
		auth: {
			auth_mode: "api_key",
			api_key_env_var: "GEMINI_API_KEY",
			api_base_url: null,
			gcp_project: null,
			gcp_location: "us-central1",
			gcp_credentials_path: null,
			aws_region: "us-east-1",
			aws_profile: null,
			require_credentials_present: false,
		},
		budget: {
			max_turns: 12,
			max_input_tokens: 200000,
			max_output_tokens: 32000,
			timeout_seconds: 300,
			max_cost_usd: 5.0,
		},
		sandbox: {
			read_only_workspace: true,
			allow_shell_execution: false,
			allow_network_access: false,
			allowed_builtin_tools: [
				"view_file",
				"list_directory",
				"find_file",
				"search_directory",
			],
			denied_builtin_tools: ["run_command", "create_file", "edit_file"],
		},
		steward_hold_policy: "pause",
		ontology_policy: "recommend",
		stage_output_policy: "llm_only",
		allow_cli_schema_omission_retry: false,
	};

	return {
		workspace_root: "/workspace/open-dv",
		state_dir: "/workspace/open-dv/.opendv",
		cas_dir: "/workspace/open-dv/.opendv/cas",
		ledgers_dir: "/workspace/open-dv/.opendv/ledgers",
		server_host: "localhost",
		server_port: 9000,
		max_inline_bytes: 262144,
		cors_origins: [],
		config_file_path: null,
		persisted_config_path: "/workspace/open-dv/.opendv/platform_config.json",
		web_ui_bundled: true,
		harness,
		resolved_persona_models: resolvePersonaModels(harness, providers),
		providers,
	};
}

export interface PlatformConfigActionOptions {
	apiUrl?: string | undefined;
	fetchImpl?: typeof fetch | undefined;
	rbacRole?: string | undefined;
}

function resolveFetch(customFetch?: typeof fetch | undefined): typeof fetch {
	if (customFetch) return customFetch;
	if (typeof globalThis.fetch === "function") {
		return globalThis.fetch.bind(globalThis);
	}
	throw new Error("No fetch implementation available for platform config API");
}

function normalizeBaseUrl(apiUrl?: string | undefined): string {
	return (apiUrl ?? "").replace(/\/api\/v1\/?$/, "").replace(/\/+$/, "");
}

export async function fetchPlatformConfig(
	options: PlatformConfigActionOptions = {},
): Promise<PlatformConfigResponse> {
	const fetchFn = resolveFetch(options.fetchImpl);
	const base = normalizeBaseUrl(options.apiUrl);
	const resp = await fetchFn(`${base}/api/v1/platform/config`, {
		method: "GET",
		headers: {
			Accept: "application/json",
			"X-OpenDV-Role": options.rbacRole ?? "INTERNAL_MODELER",
		},
	});
	if (!resp.ok) {
		const errJson = (await resp.json().catch(() => ({}))) as {
			detail?: string;
		};
		throw new Error(
			errJson.detail ?? `Failed to fetch platform config: HTTP ${resp.status}`,
		);
	}
	return (await resp.json()) as PlatformConfigResponse;
}

export async function savePlatformConfig(
	req: PlatformConfigUpdateRequest,
	options: PlatformConfigActionOptions = {},
): Promise<PlatformConfigResponse> {
	const fetchFn = resolveFetch(options.fetchImpl);
	const base = normalizeBaseUrl(options.apiUrl);
	const resp = await fetchFn(`${base}/api/v1/platform/config`, {
		method: "PUT",
		headers: {
			"Content-Type": "application/json",
			Accept: "application/json",
			"X-OpenDV-Role": options.rbacRole ?? "INTERNAL_MODELER",
		},
		body: JSON.stringify(req),
	});
	if (!resp.ok) {
		const errJson = (await resp.json().catch(() => ({}))) as {
			detail?: string;
		};
		throw new Error(
			errJson.detail ?? `Failed to save platform config: HTTP ${resp.status}`,
		);
	}
	return (await resp.json()) as PlatformConfigResponse;
}

export async function validatePlatformConfig(
	harness: HarnessConfigDTO | Record<string, unknown>,
	options: PlatformConfigActionOptions = {},
): Promise<PlatformConfigValidationResponse> {
	const fetchFn = resolveFetch(options.fetchImpl);
	const base = normalizeBaseUrl(options.apiUrl);
	const resp = await fetchFn(`${base}/api/v1/platform/config/validate`, {
		method: "POST",
		headers: {
			"Content-Type": "application/json",
			Accept: "application/json",
			"X-OpenDV-Role": options.rbacRole ?? "INTERNAL_MODELER",
		},
		body: JSON.stringify({ harness }),
	});
	if (!resp.ok) {
		const errJson = (await resp.json().catch(() => ({}))) as {
			detail?: string;
			error_code?: string;
		};
		return {
			passed: false,
			diagnostics: [
				{
					code: errJson.error_code ?? "VALIDATION_FAILED",
					severity: "ERROR",
					message:
						errJson.detail ?? `HTTP ${resp.status} config validation failure`,
				},
			],
			resolved_persona_models: {},
			providers: [],
		};
	}
	return (await resp.json()) as PlatformConfigValidationResponse;
}
