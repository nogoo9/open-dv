/**
 * Full-Page Platform & Agent Harness Settings View (`PlatformSettingsView.tsx`)
 *
 * Interactive configuration surface for Open-DV Agent Harness providers,
 * semantic model tiers, per-persona overrides, cloud auth routing, execution
 * budgets, sandbox isolation, and governance policies.
 */

import type {
	HarnessAuthConfigDTO,
	HarnessBudgetConfigDTO,
	HarnessConfigDTO,
	HarnessProviderId,
	HarnessSandboxPolicyDTO,
	PlatformConfigResponse,
	PlatformConfigValidationResponse,
} from "@opendv/protocol-ts";
import type { CSSProperties, FormEvent } from "react";
import { EXPLORER_COLORS, EXPLORER_FONTS } from "../design-tokens";
import { safeUseEffect, safeUseState } from "../modals/safeHooks";
import { AuthBudgetPolicySection } from "./AuthBudgetPolicySection";
import { HarnessModelSettingsSection } from "./HarnessModelSettingsSection";
import {
	type CanonicalPersonaId,
	createDefaultPlatformConfig,
	fetchPlatformConfig,
	resolvePersonaModels,
	savePlatformConfig,
	validatePlatformConfig,
} from "./usePlatformConfigActions";

export interface PlatformSettingsViewProps {
	initialConfig?: PlatformConfigResponse | null | undefined;
	apiUrl?: string | undefined;
	fetchImpl?: typeof fetch | undefined;
	onConfigSaved?: ((updated: PlatformConfigResponse) => void) | undefined;
	onBackToExplorer?: (() => void) | undefined;
	initialValidationResult?: PlatformConfigValidationResponse | null | undefined;
	initialSaveSuccess?: boolean | undefined;
	initialErrorMessage?: string | null | undefined;
}

const SECTION_STYLE: CSSProperties = {
	maxWidth: "1120px",
	margin: "0 auto",
	padding: "24px 20px 48px",
	fontFamily: EXPLORER_FONTS.sans,
	fontSize: "13px",
	color: EXPLORER_COLORS.inkPrimary,
	display: "flex",
	flexDirection: "column",
	gap: "18px",
};

const HEADER_STYLE: CSSProperties = {
	display: "flex",
	flexWrap: "wrap",
	alignItems: "center",
	justifyContent: "space-between",
	gap: "12px",
	paddingBottom: "14px",
	borderBottom: `1px solid ${EXPLORER_COLORS.borderLine}`,
};

const SECONDARY_BTN_STYLE: CSSProperties = {
	padding: "8px 14px",
	fontSize: "12.5px",
	fontWeight: 500,
	border: `1px solid ${EXPLORER_COLORS.borderLine}`,
	borderRadius: "6px",
	backgroundColor: EXPLORER_COLORS.bgSubtle,
	color: EXPLORER_COLORS.inkPrimary,
	cursor: "pointer",
};

const PRIMARY_BTN_STYLE: CSSProperties = {
	padding: "8px 16px",
	fontSize: "12.5px",
	fontWeight: 600,
	border: "none",
	borderRadius: "6px",
	backgroundColor: EXPLORER_COLORS.inkPrimary,
	color: "#FFFFFF",
	cursor: "pointer",
};

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

export function PlatformSettingsView({
	initialConfig,
	apiUrl,
	fetchImpl,
	onConfigSaved,
	onBackToExplorer,
	initialValidationResult,
	initialSaveSuccess = false,
	initialErrorMessage = null,
}: PlatformSettingsViewProps = {}) {
	const [config, setConfig] = safeUseState<PlatformConfigResponse>(() =>
		initialConfig
			? structuredClone(initialConfig)
			: createDefaultPlatformConfig(),
	);
	const [isLoading, setIsLoading] = safeUseState<boolean>(!initialConfig);
	const [isValidating, setIsValidating] = safeUseState<boolean>(false);
	const [isSaving, setIsSaving] = safeUseState<boolean>(false);
	const [validationResult, setValidationResult] =
		safeUseState<PlatformConfigValidationResponse | null>(
			initialValidationResult ?? null,
		);
	const [saveSuccess, setSaveSuccess] =
		safeUseState<boolean>(initialSaveSuccess);
	const [errorMessage, setErrorMessage] = safeUseState<string | null>(
		initialErrorMessage,
	);

	// Load configuration on mount when initialConfig is not supplied
	safeUseEffect(() => {
		if (initialConfig) {
			setIsLoading(false);
			return;
		}
		let cancelled = false;
		setIsLoading(true);
		void fetchPlatformConfig({ apiUrl, fetchImpl })
			.then((loaded) => {
				if (cancelled) return;
				setConfig(loaded);
				setErrorMessage(null);
			})
			.catch((err) => {
				if (cancelled) return;
				setErrorMessage(
					err instanceof Error ? err.message : "Failed to load platform config",
				);
			})
			.finally(() => {
				if (!cancelled) setIsLoading(false);
			});
		return () => {
			cancelled = true;
		};
	}, [initialConfig, apiUrl]);

	const clearTransientBanners = () => {
		setSaveSuccess(false);
		setErrorMessage(null);
	};

	const handleProviderChange = (nextProvider: HarnessProviderId) => {
		clearTransientBanners();
		setConfig((prev) => applyProviderSwitch(prev, nextProvider));
	};

	const handleTierModelChange = (
		tier: "reasoning" | "fast",
		modelId: string,
	) => {
		clearTransientBanners();
		setConfig((prev) => {
			const nextHarness: HarnessConfigDTO = {
				...prev.harness,
				model_overrides: { ...prev.harness.model_overrides, [tier]: modelId },
			};
			return {
				...prev,
				harness: nextHarness,
				resolved_persona_models: resolvePersonaModels(
					nextHarness,
					prev.providers,
				),
			};
		});
	};

	const handlePersonaModelChange = (
		persona: CanonicalPersonaId,
		modelId: string,
	) => {
		clearTransientBanners();
		setConfig((prev) => {
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
				resolved_persona_models: resolvePersonaModels(
					nextHarness,
					prev.providers,
				),
			};
		});
	};

	const handleAuthChange = (patch: Partial<HarnessAuthConfigDTO>) => {
		clearTransientBanners();
		setConfig((prev) => ({
			...prev,
			harness: { ...prev.harness, auth: { ...prev.harness.auth, ...patch } },
		}));
	};

	const handleBudgetChange = (patch: Partial<HarnessBudgetConfigDTO>) => {
		clearTransientBanners();
		setConfig((prev) => ({
			...prev,
			harness: {
				...prev.harness,
				budget: { ...prev.harness.budget, ...patch },
			},
		}));
	};

	const handleSandboxChange = (patch: Partial<HarnessSandboxPolicyDTO>) => {
		clearTransientBanners();
		setConfig((prev) => ({
			...prev,
			harness: {
				...prev.harness,
				sandbox: { ...prev.harness.sandbox, ...patch },
			},
		}));
	};

	const handlePolicyChange = (
		patch: Partial<
			Pick<
				HarnessConfigDTO,
				| "steward_hold_policy"
				| "ontology_policy"
				| "stage_output_policy"
				| "allow_cli_schema_omission_retry"
			>
		>,
	) => {
		clearTransientBanners();
		setConfig((prev) => ({
			...prev,
			harness: { ...prev.harness, ...patch },
		}));
	};

	const handleMaxInlineBytesChange = (bytes: number) => {
		clearTransientBanners();
		setConfig((prev) => ({ ...prev, max_inline_bytes: bytes }));
	};

	const handleValidate = async () => {
		setIsValidating(true);
		setErrorMessage(null);
		try {
			const res = await validatePlatformConfig(config.harness, {
				apiUrl,
				fetchImpl,
			});
			setValidationResult(res);
			if (
				res.resolved_persona_models &&
				Object.keys(res.resolved_persona_models).length > 0
			) {
				setConfig((prev) => ({
					...prev,
					resolved_persona_models: res.resolved_persona_models,
					...(res.providers && res.providers.length > 0
						? { providers: res.providers }
						: {}),
				}));
			}
		} catch (err) {
			setValidationResult({
				passed: false,
				diagnostics: [
					{
						code: "VALIDATE_ERROR",
						severity: "ERROR",
						message: err instanceof Error ? err.message : String(err),
					},
				],
				resolved_persona_models: config.resolved_persona_models,
				providers: config.providers,
			});
		} finally {
			setIsValidating(false);
		}
	};

	const handleSaveSubmit = async (e: FormEvent<HTMLFormElement>) => {
		e.preventDefault();
		setIsSaving(true);
		setSaveSuccess(false);
		setErrorMessage(null);
		try {
			const saved = await savePlatformConfig(
				{
					harness: config.harness,
					max_inline_bytes: config.max_inline_bytes,
					cors_origins: config.cors_origins,
					persist: true,
				},
				{ apiUrl, fetchImpl },
			);
			setConfig(saved);
			setSaveSuccess(true);
			onConfigSaved?.(saved);
		} catch (err) {
			setErrorMessage(
				err instanceof Error
					? err.message
					: "Failed to save platform configuration",
			);
		} finally {
			setIsSaving(false);
		}
	};

	return (
		<section
			data-testid="platform-settings-view"
			aria-label="Platform and Agent Harness Settings"
			style={SECTION_STYLE}
		>
			<header style={HEADER_STYLE}>
				<div>
					<h1 style={{ margin: 0, fontSize: "18px", fontWeight: 600 }}>
						Platform &amp; Agent Harness Settings
					</h1>
					<p
						style={{
							margin: "4px 0 0",
							fontSize: "12.5px",
							color: EXPLORER_COLORS.inkMuted,
						}}
					>
						Configure multi-provider agent harnesses (Google Antigravity &amp;
						Claude), semantic model tiers, per-persona routing, cloud auth, and
						execution budgets.
					</p>
				</div>

				{onBackToExplorer && (
					<button
						type="button"
						data-testid="btn-back-to-explorer"
						onClick={onBackToExplorer}
						style={SECONDARY_BTN_STYLE}
					>
						← Back to Pipeline Explorer
					</button>
				)}
			</header>

			{errorMessage && (
				<div
					role="alert"
					data-testid="settings-error-banner"
					style={{
						padding: "10px 14px",
						borderRadius: "6px",
						backgroundColor: "#FEF2F2",
						border: "1px solid #FECACA",
						color: "#991B1B",
						fontSize: "12.5px",
						fontWeight: 500,
					}}
				>
					✕ {errorMessage}
				</div>
			)}

			{saveSuccess && (
				<output
					aria-live="polite"
					data-testid="settings-save-success-banner"
					style={{
						padding: "10px 14px",
						borderRadius: "6px",
						backgroundColor: "#F0FDF4",
						border: "1px solid #BBF7D0",
						color: "#166534",
						fontSize: "12.5px",
						fontWeight: 600,
					}}
				>
					✓ Platform &amp; Agent Harness configuration saved
				</output>
			)}

			{validationResult && (
				<output
					aria-live="polite"
					data-testid="settings-validation-banner"
					style={{
						padding: "12px 14px",
						borderRadius: "6px",
						backgroundColor: validationResult.passed ? "#F0FDF4" : "#FFFBEB",
						border: `1px solid ${validationResult.passed ? "#BBF7D0" : "#FDE68A"}`,
						color: validationResult.passed ? "#166534" : "#92400E",
						display: "flex",
						flexDirection: "column",
						gap: "6px",
					}}
				>
					<div style={{ fontWeight: 600, fontSize: "12.5px" }}>
						{validationResult.passed
							? "✓ Configuration Validated Successfully"
							: "⚠ Configuration Validation Issues Detected"}
					</div>
					{validationResult.diagnostics.map((d, idx) => (
						<div
							key={d.code ? `${d.code}-${idx}` : `diag-${idx}`}
							style={{ fontFamily: EXPLORER_FONTS.mono, fontSize: "11.5px" }}
						>
							[{d.severity}] {d.code ? `${d.code}: ` : ""}
							{d.message}
							{d.target ? ` (${d.target})` : ""}
						</div>
					))}
				</output>
			)}

			<form
				aria-label="Platform and Agent Harness Configuration Form"
				onSubmit={(e) => {
					void handleSaveSubmit(e);
				}}
				style={{ display: "flex", flexDirection: "column", gap: "18px" }}
			>
				<HarnessModelSettingsSection
					harness={config.harness}
					providers={config.providers}
					resolvedPersonaModels={config.resolved_persona_models}
					onProviderChange={handleProviderChange}
					onTierModelChange={handleTierModelChange}
					onPersonaModelChange={handlePersonaModelChange}
				/>

				<AuthBudgetPolicySection
					config={config}
					onAuthChange={handleAuthChange}
					onBudgetChange={handleBudgetChange}
					onSandboxChange={handleSandboxChange}
					onPolicyChange={handlePolicyChange}
					onMaxInlineBytesChange={handleMaxInlineBytesChange}
				/>

				<footer
					style={{
						display: "flex",
						alignItems: "center",
						justifyContent: "space-between",
						gap: "12px",
						paddingTop: "10px",
						borderTop: `1px solid ${EXPLORER_COLORS.borderLine}`,
					}}
				>
					<span style={{ fontSize: "12px", color: EXPLORER_COLORS.inkMuted }}>
						{isLoading
							? "Loading active platform configuration..."
							: `Persisted target: ${config.persisted_config_path}`}
					</span>

					<div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
						<button
							type="button"
							data-testid="btn-validate-settings"
							onClick={() => void handleValidate()}
							disabled={isValidating || isSaving}
							style={SECONDARY_BTN_STYLE}
						>
							{isValidating ? "Validating..." : "Validate Settings"}
						</button>

						<button
							type="submit"
							data-testid="btn-save-settings"
							disabled={isSaving}
							style={PRIMARY_BTN_STYLE}
						>
							{isSaving ? "Saving..." : "Save Configuration"}
						</button>
					</div>
				</footer>
			</form>
		</section>
	);
}
