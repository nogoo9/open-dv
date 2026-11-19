/**
 * Per-Persona Model Overrides & Resolved Effective Models Matrix
 * (`AgentPersonaRoutingSection.tsx`), shared by `<AgentHarnessConfigCard />`.
 */

import type { HarnessConfigDTO } from "@opendv/protocol-ts";
import type { CSSProperties } from "react";
import { CLAUDE_TOKENS, EXPLORER_FONTS } from "../design-tokens";
import {
	CANONICAL_PERSONAS,
	type CanonicalPersonaId,
	SETTINGS_FIELD_COL_STYLE as FIELD_COL_STYLE,
	SETTINGS_INPUT_STYLE as INPUT_STYLE,
	SETTINGS_LABEL_STYLE as LABEL_STYLE,
	PERSONA_DESCRIPTIONS,
	PERSONA_TIER_MAP,
} from "./usePlatformConfigActions";

export interface AgentPersonaRoutingSectionProps {
	harness: HarnessConfigDTO;
	resolvedPersonaModels: Readonly<Record<string, string>>;
	reasoningOverride: string;
	fastOverride: string;
	onPersonaModelChange: (persona: CanonicalPersonaId, modelId: string) => void;
}

const HINT_STYLE: CSSProperties = {
	fontSize: "11.5px",
	color: CLAUDE_TOKENS.colors.muted,
};

const TH_STYLE: CSSProperties = { padding: "7px 10px", textAlign: "left" };
const TD_MONO_STYLE: CSSProperties = {
	padding: "7px 10px",
	fontFamily: EXPLORER_FONTS.mono,
};

export function AgentPersonaRoutingSection({
	harness,
	resolvedPersonaModels,
	reasoningOverride,
	fastOverride,
	onPersonaModelChange,
}: AgentPersonaRoutingSectionProps) {
	return (
		<>
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
									style={{
										fontWeight: 400,
										color: CLAUDE_TOKENS.colors.muted,
									}}
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
								onChange={(e) => onPersonaModelChange(persona, e.target.value)}
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

			<div style={{ overflowX: "auto" }}>
				<table
					data-testid="settings-resolved-models-table"
					style={{
						width: "100%",
						borderCollapse: "collapse",
						fontSize: "12px",
						border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
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
								backgroundColor: CLAUDE_TOKENS.colors.surfaceSoft,
								borderBottom: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
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
										borderBottom: `1px solid ${CLAUDE_TOKENS.colors.hairlineSoft}`,
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
										{hasPersonaOverride ? "Persona Override" : `Tier (${tier})`}
									</td>
									<td
										data-testid={`resolved-model-value-${persona}`}
										style={{
											...TD_MONO_STYLE,
											fontWeight: 600,
											color: CLAUDE_TOKENS.colors.primaryActive,
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
		</>
	);
}
