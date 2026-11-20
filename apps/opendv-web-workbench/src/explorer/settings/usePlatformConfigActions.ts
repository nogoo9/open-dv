/**
 * Platform & Agent Harness Configuration Actions (`usePlatformConfigActions.ts`)
 *
 * Typed REST client actions, readiness predicates, and baseline configuration factory
 * for `/api/v1/platform/config` and `/api/v1/platform/config/validate`.
 * Zero hardcoded loopback IP or forbidden ports (REQ-CFG-01).
 * Zero legacy X-OpenDV-Role headers (`FR-AUTH-01`).
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
import {
	CLAUDE_TOKENS,
	EXPLORER_COLORS,
	EXPLORER_FONTS,
} from "../design-tokens";
import { normalizeApiBaseUrl } from "../url-router";

export const SETTINGS_FIELDSET_STYLE: CSSProperties = {
	margin: 0,
	minWidth: 0,
	maxWidth: "100%",
	boxSizing: "border-box",
	padding: "18px 20px",
	backgroundColor: CLAUDE_TOKENS.colors.canvas,
	border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
	borderRadius: "12px",
	display: "flex",
	flexDirection: "column",
	gap: "14px",
};

export const SETTINGS_LEGEND_STYLE: CSSProperties = {
	padding: "0 6px",
	fontWeight: 500,
	fontSize: "14px",
	color: CLAUDE_TOKENS.colors.ink,
};

export const SETTINGS_FIELD_COL_STYLE: CSSProperties = {
	display: "flex",
	flexDirection: "column",
	gap: "5px",
};

export const SETTINGS_LABEL_STYLE: CSSProperties = {
	fontWeight: 500,
	fontSize: "12px",
	color: CLAUDE_TOKENS.colors.muted,
};

export const SETTINGS_INPUT_STYLE: CSSProperties = {
	padding: "8px 11px",
	fontFamily: EXPLORER_FONTS.mono,
	fontSize: "12.5px",
	border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
	borderRadius: "8px",
	backgroundColor: CLAUDE_TOKENS.colors.canvas,
	color: CLAUDE_TOKENS.colors.ink,
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

export const PROVIDER_LABELS: Readonly<Record<HarnessProviderId, string>> = {
	agy: "agy · Google Antigravity SDK",
	"agy-cli": "agy-cli · Antigravity CLI",
	antigravity: "antigravity · Antigravity Adapter",
	claude: "claude · Claude Agent SDK",
	"claude-cli": "claude-cli · Claude CLI",
	"claude-code": "claude-code · Claude Code SDK",
};

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

export const PERSONA_DESCRIPTIONS: Readonly<
	Record<CanonicalPersonaId, string>
> = {
	"vault-first-mate": "Stage 00 · Orchestrator & Hold Coordinator",
	"scout-source": "Stage 01 · Bronze Relational Profiler",
	"scout-unstructured": "Stage 02 · Unstructured Document Extractor",
	"ship-vault": "Stage 03–04 · Neuro-Symbolic DV 2.0 Modeler",
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

export function applyProviderSwitch(
	prev: PlatformConfigResponse,
	nextProvider: HarnessProviderId,
): PlatformConfigResponse {
	const oldEntry = prev.providers.find(
		(p) => p.provider === prev.harness.provider,
	);
	const nextEntry = prev.providers.find((p) => p.provider === nextProvider);
	const isClaude = nextProvider.startsWith("claude");

	const defaultReasoning =
		nextEntry?.default_reasoning_model ??
		(isClaude ? "claude-opus-4-6" : "gemini-4-argon-high");
	const defaultFast =
		nextEntry?.default_fast_model ??
		(isClaude ? "claude-haiku-4-5" : "gemini-3.8-flash-high");
	const defaultEnvVar =
		nextEntry?.api_key_env_var ??
		(isClaude ? "ANTHROPIC_API_KEY" : "GEMINI_API_KEY");

	const prevReasoning = prev.harness.model_overrides?.reasoning ?? "";
	const prevFast = prev.harness.model_overrides?.fast ?? "";
	const nextReasoning =
		!prevReasoning || prevReasoning === oldEntry?.default_reasoning_model
			? defaultReasoning
			: prevReasoning;
	const nextFast =
		!prevFast || prevFast === oldEntry?.default_fast_model
			? defaultFast
			: prevFast;

	const prevEnvVar = prev.harness.auth?.api_key_env_var ?? "";
	const nextEnvVar =
		!prevEnvVar || prevEnvVar === oldEntry?.api_key_env_var
			? defaultEnvVar
			: prevEnvVar;

	const nextHarness: HarnessConfigDTO = {
		...prev.harness,
		provider: nextProvider,
		model_overrides: {
			...prev.harness.model_overrides,
			reasoning: nextReasoning,
			fast: nextFast,
		},
		auth: {
			...prev.harness.auth,
			api_key_env_var: nextEnvVar,
		},
	};

	return {
		...prev,
		harness: nextHarness,
		resolved_persona_models: resolvePersonaModels(nextHarness, prev.providers),
	};
}

export function applyTierModelChange(
	prev: PlatformConfigResponse,
	tier: "reasoning" | "fast",
	modelId: string,
): PlatformConfigResponse {
	const nextHarness: HarnessConfigDTO = {
		...prev.harness,
		model_overrides: { ...prev.harness.model_overrides, [tier]: modelId },
	};
	return {
		...prev,
		harness: nextHarness,
		resolved_persona_models: resolvePersonaModels(nextHarness, prev.providers),
	};
}

export function applyPersonaModelChange(
	prev: PlatformConfigResponse,
	persona: CanonicalPersonaId,
	modelId: string,
): PlatformConfigResponse {
	const nextAgentModels: Record<string, string> = {
		...prev.harness.agent_models,
	};
	if (modelId.trim()) {
		nextAgentModels[persona] = modelId;
	} else {
		delete nextAgentModels[persona];
	}
	const nextHarness: HarnessConfigDTO = {
		...prev.harness,
		agent_models: nextAgentModels,
	};
	return {
		...prev,
		harness: nextHarness,
		resolved_persona_models: resolvePersonaModels(nextHarness, prev.providers),
	};
}

export function isActiveProviderReady(config: PlatformConfigResponse): {
	ready: boolean;
	activeProvider: ProviderReadinessDTO | undefined;
	activeModel: string;
	reason: string;
} {
	const activeProvider = config.providers.find(
		(p) => p.provider === config.harness.provider,
	);
	const ready = Boolean(activeProvider?.available);
	const activeModel =
		config.resolved_persona_models?.["ship-vault"] ||
		config.resolved_persona_models?.["vault-first-mate"] ||
		config.harness.model_overrides?.reasoning ||
		activeProvider?.default_reasoning_model ||
		"";
	const reason =
		activeProvider?.status_message ||
		(ready ? "Agent harness ready" : "Agent harness setup required");
	return { ready, activeProvider, activeModel, reason };
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
		workspace_root: "",
		state_dir: "",
		cas_dir: "",
		ledgers_dir: "",
		server_host: "",
		server_port: 0,
		max_inline_bytes: 262144,
		cors_origins: [],
		config_file_path: null,
		persisted_config_path: "",
		web_ui_bundled: true,
		remote_unauthenticated_mode: false,
		harness,
		resolved_persona_models: resolvePersonaModels(harness, providers),
		providers,
	};
}

export interface PlatformConfigActionOptions {
	apiUrl?: string | undefined;
	fetchImpl?: typeof fetch | undefined;
}

export interface VerifyAndSaveOptions extends PlatformConfigActionOptions {
	onVerifiedAndSaved?:
		| ((updated: PlatformConfigResponse, ready: boolean) => void)
		| undefined;
}

export interface VerifyAndSaveResult {
	ok: boolean;
	validation: PlatformConfigValidationResponse;
	saved: PlatformConfigResponse | null;
	ready: boolean;
}

function resolveFetch(customFetch?: typeof fetch | undefined): typeof fetch {
	if (customFetch) return customFetch;
	if (typeof globalThis.fetch === "function") {
		return globalThis.fetch.bind(globalThis);
	}
	throw new Error("No fetch implementation available for platform config API");
}

function normalizeBaseUrl(apiUrl?: string | undefined): string {
	return normalizeApiBaseUrl(apiUrl);
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

export async function verifyAndSaveHarnessConfig(
	config: PlatformConfigResponse,
	options: VerifyAndSaveOptions = {},
): Promise<VerifyAndSaveResult> {
	const validation = await validatePlatformConfig(config.harness, options);
	if (!validation.passed) {
		return { ok: false, validation, saved: null, ready: false };
	}

	const saved = await savePlatformConfig(
		{
			harness: {
				provider: config.harness.provider,
				model_overrides: config.harness.model_overrides,
				agent_models: config.harness.agent_models,
				auth: {
					auth_mode: config.harness.auth.auth_mode,
					api_key_env_var: config.harness.auth.api_key_env_var,
				},
			},
			persist: true,
		},
		options,
	);
	const { ready } = isActiveProviderReady(saved);
	options.onVerifiedAndSaved?.(saved, ready);
	return { ok: true, validation, saved, ready };
}

export { EXPLORER_COLORS };
