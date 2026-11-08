/**
 * Authentication, Execution Budgets, Sandbox Isolation & Governance Policies Section
 * (`AuthBudgetPolicySection.tsx`)
 *
 * Renders three semantic `<fieldset>` cards:
 * 3. Authentication & Cloud Provider Routing
 * 4. Execution Budgets & Sandbox Isolation
 * 5. Governance Policies & Platform Runtime
 */

import type {
	HarnessAuthConfigDTO,
	HarnessBudgetConfigDTO,
	HarnessConfigDTO,
	HarnessSandboxPolicyDTO,
	PlatformConfigResponse,
} from "@opendv/protocol-ts";
import type { CSSProperties } from "react";
import { EXPLORER_COLORS, EXPLORER_FONTS } from "../design-tokens";
import {
	SETTINGS_FIELDSET_STYLE as FIELDSET_STYLE,
	SETTINGS_FIELD_COL_STYLE as FIELD_COL_STYLE,
	SETTINGS_INPUT_STYLE as INPUT_STYLE,
	SETTINGS_LABEL_STYLE as LABEL_STYLE,
	SETTINGS_LEGEND_STYLE as LEGEND_STYLE,
} from "./usePlatformConfigActions";

export interface AuthBudgetPolicySectionProps {
	config: PlatformConfigResponse;
	onAuthChange: (patch: Partial<HarnessAuthConfigDTO>) => void;
	onBudgetChange: (patch: Partial<HarnessBudgetConfigDTO>) => void;
	onSandboxChange: (patch: Partial<HarnessSandboxPolicyDTO>) => void;
	onPolicyChange: (
		patch: Partial<
			Pick<
				HarnessConfigDTO,
				| "steward_hold_policy"
				| "ontology_policy"
				| "stage_output_policy"
				| "allow_cli_schema_omission_retry"
			>
		>,
	) => void;
	onMaxInlineBytesChange: (bytes: number) => void;
}

const GRID_STYLE: CSSProperties = {
	display: "grid",
	gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))",
	gap: "12px",
};

const CHECKBOX_ROW_STYLE: CSSProperties = {
	display: "flex",
	alignItems: "center",
	gap: "8px",
};

const CHECKBOX_LABEL_STYLE: CSSProperties = {
	fontSize: "12.5px",
	color: EXPLORER_COLORS.inkBody,
};

export function AuthBudgetPolicySection({
	config,
	onAuthChange,
	onBudgetChange,
	onSandboxChange,
	onPolicyChange,
	onMaxInlineBytesChange,
}: AuthBudgetPolicySectionProps) {
	const { harness } = config;
	const { auth, budget, sandbox } = harness;

	return (
		<div style={{ display: "flex", flexDirection: "column", gap: "18px" }}>
			{/* Card 3: Authentication & Cloud Provider Routing */}
			<fieldset data-testid="fieldset-auth-routing" style={FIELDSET_STYLE}>
				<legend style={LEGEND_STYLE}>
					3. Authentication &amp; Cloud Provider Routing
				</legend>

				<div style={GRID_STYLE}>
					<div style={FIELD_COL_STYLE}>
						<label htmlFor="settings-select-auth-mode" style={LABEL_STYLE}>
							Authentication Mode (<code>auth_mode</code>)
						</label>
						<select
							id="settings-select-auth-mode"
							name="auth_mode"
							data-testid="settings-select-auth-mode"
							value={auth.auth_mode}
							onChange={(e) => onAuthChange({ auth_mode: e.target.value })}
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
							value={auth.api_key_env_var ?? ""}
							onChange={(e) =>
								onAuthChange({ api_key_env_var: e.target.value })
							}
							placeholder="GEMINI_API_KEY or ANTHROPIC_API_KEY"
							autoCorrect="off"
							autoCapitalize="off"
							spellCheck={false}
							style={INPUT_STYLE}
						/>
					</div>

					<div style={FIELD_COL_STYLE}>
						<label htmlFor="settings-input-api-base-url" style={LABEL_STYLE}>
							Custom API Base URL (<code>api_base_url</code>)
						</label>
						<input
							id="settings-input-api-base-url"
							name="api_base_url"
							type="url"
							data-testid="settings-input-api-base-url"
							value={auth.api_base_url ?? ""}
							onChange={(e) =>
								onAuthChange({ api_base_url: e.target.value || null })
							}
							placeholder="https://generativelanguage.googleapis.com"
							autoCorrect="off"
							autoCapitalize="off"
							spellCheck={false}
							style={INPUT_STYLE}
						/>
					</div>

					<div style={FIELD_COL_STYLE}>
						<label htmlFor="settings-input-gcp-project" style={LABEL_STYLE}>
							GCP Project ID (<code>gcp_project</code>)
						</label>
						<input
							id="settings-input-gcp-project"
							name="gcp_project"
							type="text"
							data-testid="settings-input-gcp-project"
							value={auth.gcp_project ?? ""}
							onChange={(e) =>
								onAuthChange({ gcp_project: e.target.value || null })
							}
							placeholder="my-gcp-vertex-project"
							autoCorrect="off"
							autoCapitalize="off"
							spellCheck={false}
							style={INPUT_STYLE}
						/>
					</div>

					<div style={FIELD_COL_STYLE}>
						<label htmlFor="settings-input-gcp-location" style={LABEL_STYLE}>
							GCP Location (<code>gcp_location</code>)
						</label>
						<input
							id="settings-input-gcp-location"
							name="gcp_location"
							type="text"
							data-testid="settings-input-gcp-location"
							value={auth.gcp_location ?? ""}
							onChange={(e) =>
								onAuthChange({ gcp_location: e.target.value || null })
							}
							placeholder="us-central1"
							autoCorrect="off"
							autoCapitalize="off"
							spellCheck={false}
							style={INPUT_STYLE}
						/>
					</div>

					<div style={FIELD_COL_STYLE}>
						<label htmlFor="settings-input-aws-region" style={LABEL_STYLE}>
							AWS Region (<code>aws_region</code>)
						</label>
						<input
							id="settings-input-aws-region"
							name="aws_region"
							type="text"
							data-testid="settings-input-aws-region"
							value={auth.aws_region ?? ""}
							onChange={(e) =>
								onAuthChange({ aws_region: e.target.value || null })
							}
							placeholder="us-east-1"
							autoCorrect="off"
							autoCapitalize="off"
							spellCheck={false}
							style={INPUT_STYLE}
						/>
					</div>

					<div style={FIELD_COL_STYLE}>
						<label htmlFor="settings-input-aws-profile" style={LABEL_STYLE}>
							AWS Profile (<code>aws_profile</code>)
						</label>
						<input
							id="settings-input-aws-profile"
							name="aws_profile"
							type="text"
							data-testid="settings-input-aws-profile"
							value={auth.aws_profile ?? ""}
							onChange={(e) =>
								onAuthChange({ aws_profile: e.target.value || null })
							}
							placeholder="default"
							autoCorrect="off"
							autoCapitalize="off"
							spellCheck={false}
							style={INPUT_STYLE}
						/>
					</div>
				</div>

				<div style={CHECKBOX_ROW_STYLE}>
					<input
						id="settings-checkbox-require-creds"
						name="require_credentials_present"
						type="checkbox"
						data-testid="settings-checkbox-require-creds"
						checked={Boolean(auth.require_credentials_present)}
						onChange={(e) =>
							onAuthChange({ require_credentials_present: e.target.checked })
						}
					/>
					<label
						htmlFor="settings-checkbox-require-creds"
						style={CHECKBOX_LABEL_STYLE}
					>
						Require credentials present before starting pipeline runs (
						<code>require_credentials_present</code>)
					</label>
				</div>
			</fieldset>

			{/* Card 4: Execution Budgets & Sandbox Isolation */}
			<fieldset data-testid="fieldset-budgets-sandbox" style={FIELDSET_STYLE}>
				<legend style={LEGEND_STYLE}>
					4. Execution Budgets &amp; Sandbox Isolation
				</legend>

				<div style={GRID_STYLE}>
					<div style={FIELD_COL_STYLE}>
						<label htmlFor="settings-input-max-turns" style={LABEL_STYLE}>
							Max Turns (<code>max_turns</code>)
						</label>
						<input
							id="settings-input-max-turns"
							name="max_turns"
							type="number"
							min={1}
							max={100}
							data-testid="settings-input-max-turns"
							value={budget.max_turns}
							onChange={(e) =>
								onBudgetChange({
									max_turns: Math.max(1, Number(e.target.value) || 1),
								})
							}
							style={INPUT_STYLE}
						/>
					</div>

					<div style={FIELD_COL_STYLE}>
						<label
							htmlFor="settings-input-max-input-tokens"
							style={LABEL_STYLE}
						>
							Max Input Tokens (<code>max_input_tokens</code>)
						</label>
						<input
							id="settings-input-max-input-tokens"
							name="max_input_tokens"
							type="number"
							min={1000}
							step={1000}
							data-testid="settings-input-max-input-tokens"
							value={budget.max_input_tokens}
							onChange={(e) =>
								onBudgetChange({
									max_input_tokens: Math.max(
										1000,
										Number(e.target.value) || 1000,
									),
								})
							}
							style={INPUT_STYLE}
						/>
					</div>

					<div style={FIELD_COL_STYLE}>
						<label
							htmlFor="settings-input-max-output-tokens"
							style={LABEL_STYLE}
						>
							Max Output Tokens (<code>max_output_tokens</code>)
						</label>
						<input
							id="settings-input-max-output-tokens"
							name="max_output_tokens"
							type="number"
							min={512}
							step={512}
							data-testid="settings-input-max-output-tokens"
							value={budget.max_output_tokens}
							onChange={(e) =>
								onBudgetChange({
									max_output_tokens: Math.max(
										512,
										Number(e.target.value) || 512,
									),
								})
							}
							style={INPUT_STYLE}
						/>
					</div>

					<div style={FIELD_COL_STYLE}>
						<label htmlFor="settings-input-timeout-seconds" style={LABEL_STYLE}>
							Timeout Seconds (<code>timeout_seconds</code>)
						</label>
						<input
							id="settings-input-timeout-seconds"
							name="timeout_seconds"
							type="number"
							min={5}
							step={5}
							data-testid="settings-input-timeout-seconds"
							value={budget.timeout_seconds}
							onChange={(e) =>
								onBudgetChange({
									timeout_seconds: Math.max(5, Number(e.target.value) || 5),
								})
							}
							style={INPUT_STYLE}
						/>
					</div>

					<div style={FIELD_COL_STYLE}>
						<label htmlFor="settings-input-max-cost-usd" style={LABEL_STYLE}>
							Max Cost USD (<code>max_cost_usd</code>)
						</label>
						<input
							id="settings-input-max-cost-usd"
							name="max_cost_usd"
							type="number"
							min={0.01}
							step={0.5}
							data-testid="settings-input-max-cost-usd"
							value={budget.max_cost_usd}
							onChange={(e) =>
								onBudgetChange({
									max_cost_usd: Math.max(0.01, Number(e.target.value) || 0.01),
								})
							}
							style={INPUT_STYLE}
						/>
					</div>
				</div>

				<div
					style={{
						display: "flex",
						flexWrap: "wrap",
						gap: "18px",
						paddingTop: "4px",
					}}
				>
					<div style={CHECKBOX_ROW_STYLE}>
						<input
							id="settings-checkbox-readonly-ws"
							name="read_only_workspace"
							type="checkbox"
							data-testid="settings-checkbox-readonly-ws"
							checked={Boolean(sandbox.read_only_workspace)}
							onChange={(e) =>
								onSandboxChange({ read_only_workspace: e.target.checked })
							}
						/>
						<label
							htmlFor="settings-checkbox-readonly-ws"
							style={CHECKBOX_LABEL_STYLE}
						>
							Read-Only Workspace (<code>read_only_workspace</code>)
						</label>
					</div>

					<div style={CHECKBOX_ROW_STYLE}>
						<input
							id="settings-checkbox-allow-shell"
							name="allow_shell_execution"
							type="checkbox"
							data-testid="settings-checkbox-allow-shell"
							checked={Boolean(sandbox.allow_shell_execution)}
							onChange={(e) =>
								onSandboxChange({ allow_shell_execution: e.target.checked })
							}
						/>
						<label
							htmlFor="settings-checkbox-allow-shell"
							style={CHECKBOX_LABEL_STYLE}
						>
							Allow Shell Execution (<code>allow_shell_execution</code>)
						</label>
					</div>

					<div style={CHECKBOX_ROW_STYLE}>
						<input
							id="settings-checkbox-allow-network"
							name="allow_network_access"
							type="checkbox"
							data-testid="settings-checkbox-allow-network"
							checked={Boolean(sandbox.allow_network_access)}
							onChange={(e) =>
								onSandboxChange({ allow_network_access: e.target.checked })
							}
						/>
						<label
							htmlFor="settings-checkbox-allow-network"
							style={CHECKBOX_LABEL_STYLE}
						>
							Allow Outbound Network Access (<code>allow_network_access</code>)
						</label>
					</div>
				</div>
			</fieldset>

			{/* Card 5: Governance Policies & Platform Runtime */}
			<fieldset
				data-testid="fieldset-governance-runtime"
				style={FIELDSET_STYLE}
			>
				<legend style={LEGEND_STYLE}>
					5. Governance Policies &amp; Platform Runtime
				</legend>

				<div style={GRID_STYLE}>
					<div style={FIELD_COL_STYLE}>
						<label htmlFor="settings-select-hold-policy" style={LABEL_STYLE}>
							Steward Hold Policy (<code>steward_hold_policy</code>)
						</label>
						<select
							id="settings-select-hold-policy"
							name="steward_hold_policy"
							data-testid="settings-select-hold-policy"
							value={harness.steward_hold_policy}
							onChange={(e) =>
								onPolicyChange({
									steward_hold_policy: e.target
										.value as HarnessConfigDTO["steward_hold_policy"],
								})
							}
							style={INPUT_STYLE}
						>
							<option value="pause">pause · Pause for Steward Decision</option>
							<option value="auto_approve_top_candidate">
								auto_approve_top_candidate · Auto-Approve Top Candidate
							</option>
							<option value="reject_on_hold">
								reject_on_hold · Reject on Steward Hold
							</option>
						</select>
					</div>

					<div style={FIELD_COL_STYLE}>
						<label
							htmlFor="settings-select-ontology-policy"
							style={LABEL_STYLE}
						>
							Ontology Grounding Policy (<code>ontology_policy</code>)
						</label>
						<select
							id="settings-select-ontology-policy"
							name="ontology_policy"
							data-testid="settings-select-ontology-policy"
							value={harness.ontology_policy}
							onChange={(e) =>
								onPolicyChange({
									ontology_policy: e.target
										.value as HarnessConfigDTO["ontology_policy"],
								})
							}
							style={INPUT_STYLE}
						>
							<option value="off">off · Disabled</option>
							<option value="recommend">recommend · Recommend Deltas</option>
							<option value="auto_accept">
								auto_accept · Auto-Accept High Confidence
							</option>
						</select>
					</div>

					<div style={FIELD_COL_STYLE}>
						<label
							htmlFor="settings-select-stage-output-policy"
							style={LABEL_STYLE}
						>
							Stage Output Policy (<code>stage_output_policy</code>)
						</label>
						<select
							id="settings-select-stage-output-policy"
							name="stage_output_policy"
							data-testid="settings-select-stage-output-policy"
							value={harness.stage_output_policy}
							onChange={(e) =>
								onPolicyChange({
									stage_output_policy: e.target
										.value as HarnessConfigDTO["stage_output_policy"],
								})
							}
							style={INPUT_STYLE}
						>
							<option value="llm_only">
								llm_only · Strict LLM Schema Output Only
							</option>
							<option value="symbolic_completion">
								symbolic_completion · Allow Symbolic Completion
							</option>
						</select>
					</div>

					<div style={FIELD_COL_STYLE}>
						<label
							htmlFor="settings-input-max-inline-bytes"
							style={LABEL_STYLE}
						>
							Max Inline Payload Bytes (<code>max_inline_bytes</code>)
						</label>
						<input
							id="settings-input-max-inline-bytes"
							name="max_inline_bytes"
							type="number"
							min={4096}
							step={4096}
							data-testid="settings-input-max-inline-bytes"
							value={config.max_inline_bytes}
							onChange={(e) =>
								onMaxInlineBytesChange(
									Math.max(4096, Number(e.target.value) || 4096),
								)
							}
							style={INPUT_STYLE}
						/>
					</div>
				</div>

				<div style={CHECKBOX_ROW_STYLE}>
					<input
						id="settings-checkbox-cli-schema-retry"
						name="allow_cli_schema_omission_retry"
						type="checkbox"
						data-testid="settings-checkbox-cli-schema-retry"
						checked={Boolean(harness.allow_cli_schema_omission_retry)}
						onChange={(e) =>
							onPolicyChange({
								allow_cli_schema_omission_retry: e.target.checked,
							})
						}
					/>
					<label
						htmlFor="settings-checkbox-cli-schema-retry"
						style={CHECKBOX_LABEL_STYLE}
					>
						Allow CLI Schema Omission Retry (
						<code>allow_cli_schema_omission_retry</code>)
					</label>
				</div>

				{/* Read-Only Runtime Paths & Bundle Info */}
				<div
					data-testid="settings-runtime-info"
					style={{
						marginTop: "4px",
						padding: "12px",
						borderRadius: "6px",
						backgroundColor: EXPLORER_COLORS.bgSubtle,
						border: `1px solid ${EXPLORER_COLORS.borderLine}`,
						display: "grid",
						gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
						gap: "8px",
						fontSize: "11.5px",
						fontFamily: EXPLORER_FONTS.mono,
						color: EXPLORER_COLORS.inkBody,
					}}
				>
					<div>
						<strong>workspace_root:</strong> {config.workspace_root}
					</div>
					<div>
						<strong>state_dir:</strong> {config.state_dir}
					</div>
					<div>
						<strong>cas_dir:</strong> {config.cas_dir}
					</div>
					<div>
						<strong>ledgers_dir:</strong> {config.ledgers_dir}
					</div>
					<div>
						<strong>persisted_config_path:</strong>{" "}
						{config.persisted_config_path}
					</div>
					<div>
						<strong>web_ui_bundled:</strong>{" "}
						{config.web_ui_bundled ? "✓ Yes (Bundled)" : "○ External"}
					</div>
				</div>
			</fieldset>
		</div>
	);
}
