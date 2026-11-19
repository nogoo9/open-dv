/**
 * Shared Agent Harness Configuration Card (`AgentHarnessConfigCard.tsx`, P2).
 *
 * Rendered identically inside `Settings` (`variant="settings"`) and
 * Step 4a `AgentSetupGate` (`variant="gate"`) using Claude `DESIGN.md` tokens.
 */

import type {
	HarnessAuthConfigDTO,
	HarnessProviderId,
	PlatformConfigResponse,
	PlatformConfigValidationResponse,
} from "@opendv/protocol-ts";
import type { CSSProperties } from "react";
import { CLAUDE_TOKENS, EXPLORER_FONTS } from "../design-tokens";
import { safeUseEffect, safeUseState } from "../modals/safeHooks";
import { AgentPersonaRoutingSection } from "./AgentPersonaRoutingSection";
import {
	type CanonicalPersonaId,
	SETTINGS_FIELDSET_STYLE as FIELDSET_STYLE,
	SETTINGS_FIELD_COL_STYLE as FIELD_COL_STYLE,
	SETTINGS_INPUT_STYLE as INPUT_STYLE,
	SETTINGS_LABEL_STYLE as LABEL_STYLE,
	SETTINGS_LEGEND_STYLE as LEGEND_STYLE,
	PROVIDER_LABELS,
	applyPersonaModelChange,
	applyProviderSwitch,
	applyTierModelChange,
	createDefaultPlatformConfig,
	isActiveProviderReady,
	validatePlatformConfig,
	verifyAndSaveHarnessConfig,
} from "./usePlatformConfigActions";

export interface AgentHarnessConfigCardProps {
	variant?: "gate" | "settings" | undefined;
	config?: PlatformConfigResponse | undefined;
	initialConfig?: PlatformConfigResponse | null | undefined;
	apiUrl?: string | undefined;
	fetchImpl?: typeof fetch | undefined;
	onProviderChange?: ((provider: HarnessProviderId) => void) | undefined;
	onTierModelChange?:
		| ((tier: "reasoning" | "fast", modelId: string) => void)
		| undefined;
	onPersonaModelChange?:
		| ((persona: CanonicalPersonaId, modelId: string) => void)
		| undefined;
	onAuthChange?: ((patch: Partial<HarnessAuthConfigDTO>) => void) | undefined;
	onVerifiedAndSaved?:
		| ((updated: PlatformConfigResponse, ready: boolean) => void)
		| undefined;
	showActionBar?: boolean | undefined;
	showPersonaRouting?: boolean | undefined;
	initialValidationResult?: PlatformConfigValidationResponse | null | undefined;
	initialErrorMessage?: string | null | undefined;
}

const HINT_STYLE: CSSProperties = {
	fontSize: "11.5px",
	color: CLAUDE_TOKENS.colors.body,
};

export function AgentHarnessConfigCard({
	variant = "settings",
	config: controlledConfig,
	initialConfig,
	apiUrl,
	fetchImpl,
	onProviderChange,
	onTierModelChange,
	onPersonaModelChange,
	onAuthChange,
	onVerifiedAndSaved,
	showActionBar,
	showPersonaRouting,
	initialValidationResult = null,
	initialErrorMessage = null,
}: AgentHarnessConfigCardProps) {
	const [localConfig, setLocalConfig] = safeUseState<PlatformConfigResponse>(
		() =>
			initialConfig
				? structuredClone(initialConfig)
				: createDefaultPlatformConfig(),
	);
	const [hasUserEdited, setHasUserEdited] = safeUseState(false);
	const [isBusy, setIsBusy] = safeUseState(false);
	const [validationResult, setValidationResult] =
		safeUseState<PlatformConfigValidationResponse | null>(
			initialValidationResult,
		);
	const [errorMessage, setErrorMessage] = safeUseState<string | null>(
		initialErrorMessage,
	);

	safeUseEffect(() => {
		if (!controlledConfig && initialConfig && !hasUserEdited) {
			setLocalConfig(structuredClone(initialConfig));
		}
	}, [controlledConfig, initialConfig, hasUserEdited]);

	const activeConfig = controlledConfig ?? localConfig;
	const { harness, providers, resolved_persona_models } = activeConfig;
	const effectiveShowActionBar = showActionBar ?? variant === "gate";
	const effectiveShowPersonaRouting =
		showPersonaRouting ?? variant === "settings";
	const effectiveShowInlineAuth = variant === "gate" || Boolean(onAuthChange);

	const handleProvider = (nextProvider: HarnessProviderId) => {
		if (onProviderChange) {
			onProviderChange(nextProvider);
			return;
		}
		setHasUserEdited(true);
		setLocalConfig((prev) => applyProviderSwitch(prev, nextProvider));
	};

	const handleTierModel = (tier: "reasoning" | "fast", modelId: string) => {
		if (onTierModelChange) {
			onTierModelChange(tier, modelId);
			return;
		}
		setHasUserEdited(true);
		setLocalConfig((prev) => applyTierModelChange(prev, tier, modelId));
	};

	const handlePersonaModel = (persona: CanonicalPersonaId, modelId: string) => {
		if (onPersonaModelChange) {
			onPersonaModelChange(persona, modelId);
			return;
		}
		setHasUserEdited(true);
		setLocalConfig((prev) => applyPersonaModelChange(prev, persona, modelId));
	};

	const handleAuth = (patch: Partial<HarnessAuthConfigDTO>) => {
		if (onAuthChange) {
			onAuthChange(patch);
			return;
		}
		setHasUserEdited(true);
		setLocalConfig((prev) => ({
			...prev,
			harness: { ...prev.harness, auth: { ...prev.harness.auth, ...patch } },
		}));
	};

	const handleValidateOnly = async () => {
		setIsBusy(true);
		setErrorMessage(null);
		try {
			const res = await validatePlatformConfig(activeConfig.harness, {
				apiUrl,
				fetchImpl,
			});
			setValidationResult(res);
			if (!controlledConfig && res.providers.length > 0) {
				setLocalConfig((prev) => ({
					...prev,
					providers: res.providers,
					resolved_persona_models: res.resolved_persona_models,
				}));
			}
		} catch (err) {
			setErrorMessage(err instanceof Error ? err.message : String(err));
		} finally {
			setIsBusy(false);
		}
	};

	const handleVerifyAndSave = async () => {
		setIsBusy(true);
		setErrorMessage(null);
		try {
			const result = await verifyAndSaveHarnessConfig(activeConfig, {
				apiUrl,
				fetchImpl,
				onVerifiedAndSaved,
			});
			setValidationResult(result.validation);
			if (result.saved && !controlledConfig) {
				setLocalConfig(result.saved);
			}
		} catch (err) {
			setErrorMessage(err instanceof Error ? err.message : String(err));
		} finally {
			setIsBusy(false);
		}
	};

	const reasoningOverride = harness.model_overrides?.reasoning ?? "";
	const fastOverride = harness.model_overrides?.fast ?? "";
	const readinessSummary = isActiveProviderReady(activeConfig);

	const routingNode = (
		<AgentPersonaRoutingSection
			harness={harness}
			resolvedPersonaModels={resolved_persona_models}
			reasoningOverride={reasoningOverride}
			fastOverride={fastOverride}
			onPersonaModelChange={handlePersonaModel}
		/>
	);

	return (
		<div
			data-testid="agent-harness-config-card"
			data-variant={variant}
			style={{ display: "flex", flexDirection: "column", gap: "18px" }}
		>
			{errorMessage && (
				<div
					role="alert"
					data-testid="agent-harness-error-banner"
					style={{
						padding: "10px 14px",
						borderRadius: "8px",
						backgroundColor: "#fdf2f2",
						border: `1px solid ${CLAUDE_TOKENS.colors.error}`,
						color: "#991b1b",
						fontSize: "12.5px",
						fontWeight: 500,
					}}
				>
					✕ {errorMessage}
				</div>
			)}

			{validationResult && (
				<output
					aria-live="polite"
					data-testid="agent-harness-validation-banner"
					style={{
						padding: "10px 14px",
						borderRadius: "8px",
						backgroundColor: validationResult.passed
							? CLAUDE_TOKENS.colors.surfaceSoft
							: "#fdf2f2",
						border: `1px solid ${
							validationResult.passed
								? CLAUDE_TOKENS.colors.success
								: CLAUDE_TOKENS.colors.error
						}`,
						color: CLAUDE_TOKENS.colors.ink,
						fontSize: "12.5px",
						display: "flex",
						flexDirection: "column",
						gap: "6px",
					}}
				>
					<strong>
						{validationResult.passed
							? "✓ Agent harness configuration valid"
							: "✕ Agent harness validation failed"}
					</strong>
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

			<fieldset data-testid="fieldset-harness-provider" style={FIELDSET_STYLE}>
				<legend style={LEGEND_STYLE}>
					1. Agent Harness Provider &amp; Readiness Status
				</legend>

				<div style={FIELD_COL_STYLE}>
					<label htmlFor="settings-select-provider" style={LABEL_STYLE}>
						Active Agent Harness Provider
					</label>
					<select
						id="settings-select-provider"
						name="provider"
						data-testid="settings-select-provider"
						value={harness.provider}
						onChange={(e) =>
							handleProvider(e.target.value as HarnessProviderId)
						}
						aria-describedby="settings-provider-hint"
						style={{ ...INPUT_STYLE, maxWidth: "420px" }}
					>
						{(Object.keys(PROVIDER_LABELS) as HarnessProviderId[]).map((id) => (
							<option key={id} value={id}>
								{PROVIDER_LABELS[id]}
							</option>
						))}
					</select>
					<span id="settings-provider-hint" style={HINT_STYLE}>
						Active readiness:{" "}
						<strong>
							{readinessSummary.ready ? "● Ready" : "○ Setup Required"}
						</strong>{" "}
						— {readinessSummary.reason}
					</span>
				</div>

				<div
					style={{
						display: "grid",
						gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
						gap: "10px",
					}}
				>
					{providers.map((p) => {
						const isSelected = p.provider === harness.provider;
						const modeLabel = p.execution_mode.toUpperCase();
						const availabilityLabel = p.available ? "✓ Ready" : "⚠ Unavailable";
						const envStatusLabel = p.credentials_present
							? "✓ Env Set"
							: "○ Env Not Set";
						const runtimeStatusLabel =
							p.execution_mode === "cli"
								? `Binary (${p.binary_name ?? p.provider}): ${p.binary_found ? "✓ Found" : "✕ Missing"}`
								: `SDK: ${p.sdk_installed ? "✓ Installed" : "✕ Missing"}`;

						return (
							<div
								key={p.provider}
								data-testid={`provider-readiness-${p.provider}`}
								style={{
									padding: "12px",
									borderRadius: "10px",
									border: isSelected
										? `1.5px solid ${CLAUDE_TOKENS.colors.primary}`
										: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
									backgroundColor: isSelected
										? CLAUDE_TOKENS.colors.surfaceCreamStrong
										: CLAUDE_TOKENS.colors.surfaceSoft,
									display: "flex",
									flexDirection: "column",
									gap: "6px",
								}}
							>
								<div
									style={{
										display: "flex",
										alignItems: "center",
										justifyContent: "space-between",
										gap: "6px",
									}}
								>
									<div
										style={{
											display: "flex",
											alignItems: "center",
											gap: "6px",
										}}
									>
										<strong
											style={{
												fontFamily: EXPLORER_FONTS.mono,
												fontSize: "12.5px",
												color: CLAUDE_TOKENS.colors.ink,
											}}
										>
											{p.provider}
										</strong>
										<span
											style={{
												padding: "1px 6px",
												borderRadius: "4px",
												fontSize: "10.5px",
												fontWeight: 600,
												fontFamily: EXPLORER_FONTS.mono,
												backgroundColor: CLAUDE_TOKENS.colors.canvas,
												border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
											}}
										>
											{modeLabel}
										</span>
									</div>
									<span
										style={{
											padding: "2px 8px",
											borderRadius: "999px",
											fontSize: "11px",
											fontWeight: 600,
											backgroundColor: p.available
												? CLAUDE_TOKENS.colors.surfaceCard
												: "#fdf2f2",
											color: p.available ? "#1f4d2c" : "#8f2424",
										}}
									>
										{availabilityLabel}
									</span>
								</div>

								<div
									style={{
										fontSize: "11.5px",
										color: CLAUDE_TOKENS.colors.body,
									}}
								>
									<div>{runtimeStatusLabel}</div>
									<div style={{ fontFamily: EXPLORER_FONTS.mono }}>
										{p.api_key_env_var}: <span>{envStatusLabel}</span>
									</div>
								</div>

								{p.status_message && (
									<div style={HINT_STYLE}>{p.status_message}</div>
								)}
							</div>
						);
					})}
				</div>
			</fieldset>

			<fieldset data-testid="fieldset-model-routing" style={FIELDSET_STYLE}>
				<legend style={LEGEND_STYLE}>
					2. Model Tier &amp; Per-Persona Model Routing
				</legend>

				{effectiveShowInlineAuth && (
					<div
						style={{
							display: "grid",
							gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
							gap: "12px",
						}}
					>
						<div style={FIELD_COL_STYLE}>
							<label htmlFor="settings-select-auth-mode" style={LABEL_STYLE}>
								Authentication Mode (<code>auth_mode</code>)
							</label>
							<select
								id="settings-select-auth-mode"
								name="auth_mode"
								data-testid="settings-select-auth-mode"
								value={harness.auth.auth_mode}
								onChange={(e) => handleAuth({ auth_mode: e.target.value })}
								style={INPUT_STYLE}
							>
								<option value="none">none · Unauthenticated / Local</option>
								<option value="cli_session">
									cli_session · Active CLI Login Session
								</option>
								<option value="api_key">
									api_key · Environment Variable Key
								</option>
								<option value="vertex_adc">
									vertex_adc · Google Cloud Vertex AI ADC
								</option>
								<option value="aws_bedrock">
									aws_bedrock · AWS Bedrock IAM Profile
								</option>
							</select>
						</div>

						<div style={FIELD_COL_STYLE}>
							<label htmlFor="settings-input-api-key-env" style={LABEL_STYLE}>
								API Key Env Var Name (<code>api_key_env_var</code>)
							</label>
							<input
								id="settings-input-api-key-env"
								name="api_key_env_var"
								type="text"
								data-testid="settings-input-api-key-env"
								value={harness.auth.api_key_env_var ?? ""}
								onChange={(e) =>
									handleAuth({ api_key_env_var: e.target.value })
								}
								placeholder="GEMINI_API_KEY or ANTHROPIC_API_KEY"
								autoCorrect="off"
								autoCapitalize="off"
								spellCheck={false}
								style={INPUT_STYLE}
							/>
						</div>
					</div>
				)}

				<div
					style={{
						display: "grid",
						gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
						gap: "14px",
					}}
				>
					<div style={FIELD_COL_STYLE}>
						<label htmlFor="settings-input-reasoning-model" style={LABEL_STYLE}>
							Reasoning Tier Model ID (<code>reasoning</code>)
						</label>
						<input
							id="settings-input-reasoning-model"
							name="model_overrides_reasoning"
							type="text"
							data-testid="settings-input-reasoning-model"
							value={reasoningOverride}
							onChange={(e) => handleTierModel("reasoning", e.target.value)}
							placeholder="e.g. gemini-4-argon-high or claude-opus-4-6"
							autoCorrect="off"
							autoCapitalize="off"
							spellCheck={false}
							style={INPUT_STYLE}
						/>
						<span style={HINT_STYLE}>
							Used by <code>vault-first-mate</code> and <code>ship-vault</code>{" "}
							unless overridden below.
						</span>
					</div>

					<div style={FIELD_COL_STYLE}>
						<label htmlFor="settings-input-fast-model" style={LABEL_STYLE}>
							Fast Tier Model ID (<code>fast</code>)
						</label>
						<input
							id="settings-input-fast-model"
							name="model_overrides_fast"
							type="text"
							data-testid="settings-input-fast-model"
							value={fastOverride}
							onChange={(e) => handleTierModel("fast", e.target.value)}
							placeholder="e.g. gemini-3.8-flash-high or claude-haiku-4-5"
							autoCorrect="off"
							autoCapitalize="off"
							spellCheck={false}
							style={INPUT_STYLE}
						/>
						<span style={HINT_STYLE}>
							Used by <code>scout-source</code> and{" "}
							<code>scout-unstructured</code> unless overridden below.
						</span>
					</div>
				</div>

				{effectiveShowPersonaRouting ? (
					routingNode
				) : (
					<details>
						<summary
							style={{ ...LABEL_STYLE, cursor: "pointer", padding: "4px 0" }}
						>
							Per-Persona Model Overrides &amp; Resolved Matrix
						</summary>
						<div
							style={{
								display: "flex",
								flexDirection: "column",
								gap: "12px",
								marginTop: "10px",
							}}
						>
							{routingNode}
						</div>
					</details>
				)}
			</fieldset>

			{effectiveShowActionBar && (
				<div
					data-testid="agent-harness-action-bar"
					style={{
						display: "flex",
						flexWrap: "wrap",
						alignItems: "center",
						justifyContent: "space-between",
						gap: "10px",
						padding: "12px 16px",
						borderRadius: "12px",
						backgroundColor: CLAUDE_TOKENS.colors.surfaceDark,
						color: CLAUDE_TOKENS.colors.onDark,
					}}
				>
					<span style={{ fontSize: "12.5px" }}>
						Verify &amp; save live agent credentials before launching discovery.
					</span>
					<div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
						<button
							type="button"
							data-testid="btn-validate-agent-harness"
							disabled={isBusy}
							onClick={() => void handleValidateOnly()}
							style={{
								padding: "8px 14px",
								borderRadius: "8px",
								border: `1px solid ${CLAUDE_TOKENS.colors.body}`,
								backgroundColor: CLAUDE_TOKENS.colors.surfaceDarkElevated,
								color: CLAUDE_TOKENS.colors.onDark,
								fontSize: "12.5px",
								fontWeight: 500,
								cursor: "pointer",
							}}
						>
							Validate Config
						</button>
						<button
							type="button"
							data-testid="btn-verify-save-agent-harness"
							disabled={isBusy}
							onClick={() => void handleVerifyAndSave()}
							style={{
								padding: "8px 16px",
								borderRadius: "8px",
								border: "none",
								backgroundColor: CLAUDE_TOKENS.colors.primary,
								color: CLAUDE_TOKENS.colors.onPrimary,
								fontSize: "12.5px",
								fontWeight: 600,
								cursor: "pointer",
							}}
						>
							{isBusy ? "Verifying..." : "Verify & Save Agent Harness"}
						</button>
					</div>
				</div>
			)}
		</div>
	);
}
