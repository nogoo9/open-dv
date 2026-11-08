/**
 * Agent Harness Provider & Model Routing Settings Section (`HarnessModelSettingsSection.tsx`)
 *
 * Renders two semantic `<fieldset>` cards:
 * 1. Agent Harness Provider & Readiness Status
 * 2. Model Tier & Per-Persona Model Routing + Resolved Persona Models Summary Table
 */

import type {
	HarnessConfigDTO,
	HarnessProviderId,
	ProviderReadinessDTO,
} from "@opendv/protocol-ts";
import type { CSSProperties } from "react";
import { EXPLORER_COLORS, EXPLORER_FONTS } from "../design-tokens";
import {
	CANONICAL_PERSONAS,
	type CanonicalPersonaId,
	SETTINGS_FIELDSET_STYLE as FIELDSET_STYLE,
	SETTINGS_FIELD_COL_STYLE as FIELD_COL_STYLE,
	SETTINGS_INPUT_STYLE as INPUT_STYLE,
	SETTINGS_LABEL_STYLE as LABEL_STYLE,
	SETTINGS_LEGEND_STYLE as LEGEND_STYLE,
	PERSONA_TIER_MAP,
} from "./usePlatformConfigActions";

export interface HarnessModelSettingsSectionProps {
	harness: HarnessConfigDTO;
	providers: readonly ProviderReadinessDTO[];
	resolvedPersonaModels: Readonly<Record<string, string>>;
	onProviderChange: (provider: HarnessProviderId) => void;
	onTierModelChange: (tier: "reasoning" | "fast", modelId: string) => void;
	onPersonaModelChange: (persona: CanonicalPersonaId, modelId: string) => void;
}

const PROVIDER_LABELS: Readonly<Record<HarnessProviderId, string>> = {
	agy: "agy · Google Antigravity SDK",
	"agy-cli": "agy-cli · Antigravity CLI",
	antigravity: "antigravity · Antigravity Adapter",
	claude: "claude · Claude Agent SDK",
	"claude-cli": "claude-cli · Claude CLI",
	"claude-code": "claude-code · Claude Code SDK",
};

const PERSONA_DESCRIPTIONS: Readonly<Record<CanonicalPersonaId, string>> = {
	"vault-first-mate": "Stage 00 · Orchestrator & Hold Coordinator",
	"scout-source": "Stage 01 · Bronze Relational Profiler",
	"scout-unstructured": "Stage 02 · Unstructured Document Extractor",
	"ship-vault": "Stage 03–04 · Neuro-Symbolic DV 2.0 Modeler",
};

const HINT_STYLE: CSSProperties = {
	fontSize: "11px",
	color: EXPLORER_COLORS.inkMuted,
};

const TH_STYLE: CSSProperties = { padding: "7px 10px", textAlign: "left" };
const TD_MONO_STYLE: CSSProperties = {
	padding: "7px 10px",
	fontFamily: EXPLORER_FONTS.mono,
};

export function HarnessModelSettingsSection({
	harness,
	providers,
	resolvedPersonaModels,
	onProviderChange,
	onTierModelChange,
	onPersonaModelChange,
}: HarnessModelSettingsSectionProps) {
	const reasoningOverride = harness.model_overrides?.reasoning ?? "";
	const fastOverride = harness.model_overrides?.fast ?? "";

	return (
		<div style={{ display: "flex", flexDirection: "column", gap: "18px" }}>
			{/* Card 1: Agent Harness Provider & Readiness Status */}
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
							onProviderChange(e.target.value as HarnessProviderId)
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
						Select the active SDK or CLI execution harness for agentic pipeline
						stages (STAGE_00 through STAGE_04).
					</span>
				</div>

				{/* Provider Readiness Grid */}
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
									borderRadius: "6px",
									border: isSelected
										? `2px solid ${EXPLORER_COLORS.borderSelected}`
										: `1px solid ${EXPLORER_COLORS.borderLine}`,
									backgroundColor: isSelected
										? "#F8FAFC"
										: EXPLORER_COLORS.bgSubtle,
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
												backgroundColor: EXPLORER_COLORS.bgSurface,
												border: `1px solid ${EXPLORER_COLORS.borderLine}`,
											}}
										>
											{modeLabel}
										</span>
									</div>
									<span
										style={{
											padding: "2px 7px",
											borderRadius: "999px",
											fontSize: "11px",
											fontWeight: 600,
											backgroundColor: p.available ? "#DCFCE7" : "#FEF3C7",
											color: p.available ? "#166534" : "#92400E",
										}}
									>
										{availabilityLabel}
									</span>
								</div>

								<div
									style={{ fontSize: "11.5px", color: EXPLORER_COLORS.inkBody }}
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

			{/* Card 2: Model Tier & Per-Persona Model Routing */}
			<fieldset data-testid="fieldset-model-routing" style={FIELDSET_STYLE}>
				<legend style={LEGEND_STYLE}>
					2. Model Tier &amp; Per-Persona Model Routing
				</legend>

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
							onChange={(e) => onTierModelChange("reasoning", e.target.value)}
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
							onChange={(e) => onTierModelChange("fast", e.target.value)}
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

				{/* Per-Persona Fine-Grained Overrides */}
				<div
					style={{
						display: "grid",
						gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
						gap: "12px",
					}}
				>
					{CANONICAL_PERSONAS.map((persona) => {
						const inputId = `settings-input-persona-${persona}`;
						const val = harness.agent_models?.[persona] ?? "";
						const defaultTier = PERSONA_TIER_MAP[persona];
						return (
							<div key={persona} style={FIELD_COL_STYLE}>
								<label htmlFor={inputId} style={LABEL_STYLE}>
									@{persona}{" "}
									<span
										style={{ fontWeight: 400, color: EXPLORER_COLORS.inkMuted }}
									>
										({defaultTier} tier)
									</span>
								</label>
								<input
									id={inputId}
									name={`agent_models_${persona}`}
									type="text"
									data-testid={inputId}
									value={val}
									onChange={(e) =>
										onPersonaModelChange(persona, e.target.value)
									}
									placeholder={`Inherit ${defaultTier} tier model`}
									autoCorrect="off"
									autoCapitalize="off"
									spellCheck={false}
									style={INPUT_STYLE}
								/>
								<span style={HINT_STYLE}>{PERSONA_DESCRIPTIONS[persona]}</span>
							</div>
						);
					})}
				</div>

				{/* Resolved Persona Models Summary Table */}
				<div style={{ overflowX: "auto" }}>
					<table
						data-testid="settings-resolved-models-table"
						style={{
							width: "100%",
							borderCollapse: "collapse",
							fontSize: "12px",
							border: `1px solid ${EXPLORER_COLORS.borderLine}`,
						}}
					>
						<caption
							style={{
								...LABEL_STYLE,
								textAlign: "left",
								paddingBottom: "6px",
							}}
						>
							Resolved Effective Model per Agent Persona
						</caption>
						<thead>
							<tr
								style={{
									backgroundColor: EXPLORER_COLORS.bgSubtle,
									borderBottom: `1px solid ${EXPLORER_COLORS.borderLine}`,
								}}
							>
								<th scope="col" style={TH_STYLE}>
									Persona
								</th>
								<th scope="col" style={TH_STYLE}>
									Semantic Tier
								</th>
								<th scope="col" style={TH_STYLE}>
									Resolution Source
								</th>
								<th scope="col" style={TH_STYLE}>
									Effective Model ID
								</th>
							</tr>
						</thead>
						<tbody>
							{CANONICAL_PERSONAS.map((persona) => {
								const tier = PERSONA_TIER_MAP[persona];
								const hasPersonaOverride = Boolean(
									harness.agent_models?.[persona]?.trim(),
								);
								const effectiveModel =
									resolvedPersonaModels[persona] ??
									(tier === "reasoning" ? reasoningOverride : fastOverride);
								return (
									<tr
										key={persona}
										data-testid={`resolved-model-row-${persona}`}
										style={{
											borderBottom: `1px solid ${EXPLORER_COLORS.borderLine}`,
										}}
									>
										<th
											scope="row"
											style={{
												...TD_MONO_STYLE,
												textAlign: "left",
												fontWeight: 600,
											}}
										>
											@{persona}
										</th>
										<td style={TD_MONO_STYLE}>{tier}</td>
										<td style={{ padding: "7px 10px" }}>
											{hasPersonaOverride
												? "Persona Override"
												: `Tier (${tier})`}
										</td>
										<td
											data-testid={`resolved-model-value-${persona}`}
											style={{
												...TD_MONO_STYLE,
												fontWeight: 600,
												color: EXPLORER_COLORS.glyphData,
											}}
										>
											{effectiveModel}
										</td>
									</tr>
								);
							})}
						</tbody>
					</table>
				</div>
			</fieldset>
		</div>
	);
}
