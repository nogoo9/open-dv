/**
 * Step 3: Modeling Governance & Target SQL Dialects (`Step3GovernanceAndDialect.tsx`).
 *
 * Configures the reasoning agent model, target output SQL dialects (DuckDB canonical +
 * Databricks Delta), sandbox sampling policy, steward hold behavior, ontology delta
 * evolution policy, and advanced hub / business key override hints.
 */

import type { SamplingStrategy } from "@opendv/protocol-ts";
import { type CSSProperties, useEffect, useState } from "react";
import { EXPLORER_COLORS, EXPLORER_FONTS } from "../design-tokens";
import type { Step3GovernanceProps, WizardFormState } from "./wizard-types";

const C = EXPLORER_COLORS;
const F = EXPLORER_FONTS;

const S_CARD: CSSProperties = {
	backgroundColor: C.bgSurface,
	borderRadius: "8px",
	border: `1px solid ${C.borderLine}`,
	padding: "24px",
};

const S_LABEL: CSSProperties = {
	display: "block",
	fontSize: "12px",
	fontWeight: 600,
	marginBottom: "6px",
	color: C.inkPrimary,
};

const S_INPUT: CSSProperties = {
	width: "100%",
	padding: "8px 12px",
	borderRadius: "5px",
	border: `1px solid ${C.borderLine}`,
	fontFamily: F.sans,
	fontSize: "13px",
	backgroundColor: C.bgPage,
	color: C.inkPrimary,
	boxSizing: "border-box",
};

const S_RADIO_ROW: CSSProperties = {
	display: "flex",
	flexDirection: "column",
	gap: "8px",
	marginTop: "6px",
};

const S_RADIO_LABEL: CSSProperties = {
	display: "flex",
	alignItems: "flex-start",
	gap: "8px",
	fontSize: "13px",
	cursor: "pointer",
	color: C.inkPrimary,
};

export function Step3GovernanceAndDialect({
	formState,
	onChange,
	availableModels,
	initialAdvancedOpen = false,
	apiUrl = "/api/v1",
	fetchImpl,
}: Step3GovernanceProps) {
	const [dynamicModels, setDynamicModels] = useState<string[]>(
		availableModels ?? [],
	);
	const [isAdvancedOpen, setIsAdvancedOpen] = useState(initialAdvancedOpen);

	useEffect(() => {
		if (availableModels && availableModels.length > 0) {
			setDynamicModels(availableModels);
			return;
		}
		let isMounted = true;
		const fetcher = fetchImpl ?? (typeof fetch !== "undefined" ? fetch : null);
		if (!fetcher) return;

		fetcher(`${apiUrl}/platform/config`)
			.then(async (r) => (r.ok ? r.json() : null))
			.then((cfg) => {
				if (!isMounted || !cfg) return;
				const fetched: string[] = [];
				if (cfg.resolved_persona_models && typeof cfg.resolved_persona_models === "object") {
					fetched.push(...Object.values(cfg.resolved_persona_models as Record<string, string>));
				}
				if (Array.isArray(cfg.available_models)) {
					fetched.push(...cfg.available_models);
				}
				if (fetched.length > 0) {
					setDynamicModels(Array.from(new Set(fetched)));
				}
			})
			.catch(() => {});

		return () => {
			isMounted = false;
		};
	}, [apiUrl, fetchImpl, availableModels]);

	const modelOptions = dynamicModels.length > 0
		? dynamicModels.map((m) => ({ id: m, label: m }))
		: (formState.agentModel ? [{ id: formState.agentModel, label: formState.agentModel }] : []);

	const handleDialectChange = (
		dialect: "duckdb" | "databricks",
		checked: boolean,
	) => {
		let current = [...formState.targetDialects];
		if (dialect === "duckdb") {
			if (!current.includes("duckdb")) current.push("duckdb");
		} else if (checked) {
			if (!current.includes("databricks")) current.push("databricks");
		} else {
			current = current.filter((d) => d !== "databricks");
			if (!current.includes("duckdb")) current.push("duckdb");
		}
		onChange({ targetDialects: current });
	};

	const advancedOverrides = formState.advancedOverrides ?? {};
	const targetHubOverrides =
		(advancedOverrides.target_hub_overrides as string) ?? "";
	const bkOverrides =
		(advancedOverrides.business_key_overrides as string) ?? "";

	const handleAdvancedOverrideChange = (key: string, value: string) => {
		const updated = {
			...advancedOverrides,
			[key]: value,
		};
		onChange({ advancedOverrides: updated });
	};

	return (
		<section data-testid="wizard-step-governance" style={S_CARD}>
			<h2
				style={{
					fontSize: "16px",
					fontWeight: 700,
					marginTop: 0,
					marginBottom: "8px",
				}}
			>
				Step 3: Governance & Dialect
			</h2>
			<p
				style={{
					fontSize: "13px",
					color: C.inkMuted,
					marginBottom: "20px",
				}}
			>
				Configure LLM agent reasoning model, target SQL dialects, sandbox
				sampling row policy, steward hold resolution, and ontology drift policy.
			</p>

			{/* 1. Agent Model Selection */}
			<div style={{ marginBottom: "20px" }}>
				<label htmlFor="select-agent-model" style={S_LABEL}>
					Agent Harness Model:
				</label>
				<select
					id="select-agent-model"
					data-testid="select-agent-model"
					value={formState.agentModel}
					onChange={(e) => onChange({ agentModel: e.target.value })}
					style={S_INPUT}
				>
					{modelOptions.map((opt) => (
						<option key={opt.id} value={opt.id}>
							{opt.label}
						</option>
					))}
				</select>
				<div style={{ fontSize: "11px", color: C.inkMuted, marginTop: "4px" }}>
					Governs the primary agent persona across Scout, Ship-Vault, and AXI-DV
					critique passes.
				</div>
			</div>

			{/* 2. Target SQL Dialects */}
			<div style={{ marginBottom: "20px" }}>
				<span style={S_LABEL}>Target SQL Output Dialects:</span>
				<div
					style={{
						display: "flex",
						gap: "20px",
						flexWrap: "wrap",
						marginTop: "6px",
					}}
				>
					<label
						style={{
							display: "flex",
							alignItems: "center",
							gap: "6px",
							fontSize: "13px",
							cursor: "not-allowed",
							color: C.inkPrimary,
						}}
					>
						<input
							type="checkbox"
							data-testid="checkbox-dialect-duckdb"
							checked={true}
							disabled={true}
						/>
						<span>
							<strong>DuckDB</strong> (Canonical 4-Zone Sandbox — Mandatory)
						</span>
					</label>

					<label
						style={{
							display: "flex",
							alignItems: "center",
							gap: "6px",
							fontSize: "13px",
							cursor: "pointer",
							color: C.inkPrimary,
						}}
					>
						<input
							type="checkbox"
							data-testid="checkbox-dialect-databricks"
							checked={formState.targetDialects.includes("databricks")}
							onChange={(e) =>
								handleDialectChange("databricks", e.target.checked)
							}
						/>
						<span>
							<strong>Databricks</strong> (Databricks Delta Lake SQL DDL)
						</span>
					</label>
				</div>
			</div>

			{/* 3. Sampling Policy */}
			<div
				style={{
					display: "grid",
					gridTemplateColumns: "1fr 1fr",
					gap: "16px",
					marginBottom: "20px",
				}}
			>
				<div>
					<label htmlFor="input-sampling-rows" style={S_LABEL}>
						Max Sandbox Rows (Profiling Budget):
					</label>
					<input
						type="number"
						id="input-sampling-rows"
						data-testid="input-sampling-rows"
						min={100}
						max={1000000}
						step={1000}
						value={formState.samplingRows}
						onChange={(e) =>
							onChange({
								samplingRows: Number.parseInt(e.target.value, 10) || 10000,
							})
						}
						style={S_INPUT}
					/>
					<div
						style={{ fontSize: "11px", color: C.inkMuted, marginTop: "4px" }}
					>
						Default 10,000 rows. Limits in-memory sample size for sandbox
						compilation.
					</div>
				</div>

				<div>
					<label htmlFor="select-sampling-strategy" style={S_LABEL}>
						Sampling Strategy:
					</label>
					<select
						id="select-sampling-strategy"
						data-testid="select-sampling-strategy"
						value={formState.samplingStrategy}
						onChange={(e) =>
							onChange({
								samplingStrategy: e.target.value as SamplingStrategy,
							})
						}
						style={S_INPUT}
					>
						<option value="DETERMINISTIC_LIMIT">
							DETERMINISTIC_LIMIT (Head N Rows)
						</option>
						<option value="TABLESAMPLE_BERNOULLI">
							TABLESAMPLE_BERNOULLI (Bernoulli Random Sampling)
						</option>
					</select>
					<div
						style={{ fontSize: "11px", color: C.inkMuted, marginTop: "4px" }}
					>
						Determines sampling method when pulling profiling slices.
					</div>
				</div>
			</div>

			{/* 4. Steward Hold Policy */}
			<div style={{ marginBottom: "20px" }}>
				<label htmlFor="select-hold-policy" style={S_LABEL}>
					Steward Hold Policy (Ambiguity Resolution):
				</label>
				<div style={S_RADIO_ROW}>
					<label style={S_RADIO_LABEL}>
						<input
							type="radio"
							name="stewardHoldPolicy"
							data-testid="radio-hold-pause"
							value="pause"
							checked={formState.stewardHoldPolicy === "pause"}
							onChange={() => onChange({ stewardHoldPolicy: "pause" })}
						/>
						<div>
							<strong>Pause for Human Review (Default)</strong>
							<div style={{ fontSize: "12px", color: C.inkMuted }}>
								Emits HOLD_ENTERED event; pauses execution until steward
								resolves candidate.
							</div>
						</div>
					</label>

					<label style={S_RADIO_LABEL}>
						<input
							type="radio"
							name="stewardHoldPolicy"
							data-testid="radio-hold-auto-approve"
							value="auto_approve_top_candidate"
							checked={
								formState.stewardHoldPolicy === "auto_approve_top_candidate"
							}
							onChange={() =>
								onChange({ stewardHoldPolicy: "auto_approve_top_candidate" })
							}
						/>
						<div>
							<strong>Auto-Approve Top Grounded Candidate</strong>
							<div style={{ fontSize: "12px", color: C.inkMuted }}>
								Automatically selects candidate with highest semantic grounding
								score.
							</div>
						</div>
					</label>

					<label style={S_RADIO_LABEL}>
						<input
							type="radio"
							name="stewardHoldPolicy"
							data-testid="radio-hold-reject"
							value="reject_on_hold"
							checked={formState.stewardHoldPolicy === "reject_on_hold"}
							onChange={() => onChange({ stewardHoldPolicy: "reject_on_hold" })}
						/>
						<div>
							<strong>Fail Closed on Ambiguity (Reject on Hold)</strong>
							<div style={{ fontSize: "12px", color: C.inkMuted }}>
								Immediately aborts pipeline with error trace if ambiguity
								arises.
							</div>
						</div>
					</label>
				</div>
				<select
					id="select-hold-policy"
					data-testid="select-hold-policy"
					value={formState.stewardHoldPolicy}
					onChange={(e) =>
						onChange({
							stewardHoldPolicy: e.target
								.value as WizardFormState["stewardHoldPolicy"],
						})
					}
					style={{ ...S_INPUT, display: "none" }}
				>
					<option value="pause">Pause for Human Review (Default)</option>
					<option value="auto_approve_top_candidate">
						Auto-Approve Top Grounded Candidate
					</option>
					<option value="reject_on_hold">Fail Closed on Ambiguity</option>
				</select>
			</div>

			{/* 5. Delta Drift & Ontology Evolution Policy */}
			<div style={{ marginBottom: "20px" }}>
				<span style={S_LABEL}>Ontology Evolution Policy (Drift Handling):</span>
				<div style={S_RADIO_ROW}>
					<label style={S_RADIO_LABEL}>
						<input
							type="radio"
							name="ontologyEvolutionPolicy"
							data-testid="radio-evolution-recommend"
							value="recommend"
							checked={formState.ontologyEvolutionPolicy === "recommend"}
							onChange={() =>
								onChange({ ontologyEvolutionPolicy: "recommend" })
							}
						/>
						<div>
							<strong>Propose OntologyDeltas (Default: Recommend)</strong>
							<div style={{ fontSize: "12px", color: C.inkMuted }}>
								Generates additive OntologyDelta operations for steward
								inspection without mutating baseline.
							</div>
						</div>
					</label>

					<label style={S_RADIO_LABEL}>
						<input
							type="radio"
							name="ontologyEvolutionPolicy"
							data-testid="radio-evolution-auto-accept"
							value="auto_accept"
							checked={formState.ontologyEvolutionPolicy === "auto_accept"}
							onChange={() =>
								onChange({ ontologyEvolutionPolicy: "auto_accept" })
							}
						/>
						<div>
							<strong>Auto-Accept Additive Deltas</strong>
							<div style={{ fontSize: "12px", color: C.inkMuted }}>
								Automatically applies non-breaking additive concepts and
								attributes into output blueprint.
							</div>
						</div>
					</label>

					<label style={S_RADIO_LABEL}>
						<input
							type="radio"
							name="ontologyEvolutionPolicy"
							data-testid="radio-evolution-strict-lock"
							value="strict_lock"
							checked={formState.ontologyEvolutionPolicy === "strict_lock"}
							onChange={() =>
								onChange({ ontologyEvolutionPolicy: "strict_lock" })
							}
						/>
						<div>
							<strong>Strict Lock (Reject Drift)</strong>
							<div style={{ fontSize: "12px", color: C.inkMuted }}>
								Refuses any schema or ontology changes; enforces strict contract
								lockdown.
							</div>
						</div>
					</label>
				</div>
				<select
					id="select-evolution-policy"
					data-testid="select-evolution-policy"
					value={formState.ontologyEvolutionPolicy}
					onChange={(e) =>
						onChange({
							ontologyEvolutionPolicy: e.target
								.value as WizardFormState["ontologyEvolutionPolicy"],
						})
					}
					style={{ ...S_INPUT, display: "none" }}
				>
					<option value="recommend">Propose OntologyDeltas</option>
					<option value="auto_accept">Auto-Accept Additive Deltas</option>
					<option value="strict_lock">Strict Lock</option>
				</select>
			</div>

			{/* 6. Advanced Drawer (Collapsible) */}
			<div
				style={{
					borderTop: `1px solid ${C.borderLine}`,
					paddingTop: "16px",
					marginTop: "16px",
				}}
			>
				<button
					type="button"
					data-testid="toggle-advanced-governance"
					onClick={() => setIsAdvancedOpen((prev) => !prev)}
					style={{
						background: "none",
						border: "none",
						padding: "4px 0",
						fontSize: "13px",
						fontWeight: 600,
						color: C.borderSelected,
						cursor: "pointer",
						display: "flex",
						alignItems: "center",
						gap: "6px",
					}}
				>
					<span>{isAdvancedOpen ? "▼" : "▶"}</span>
					<span>Advanced Governance Overrides & Hints</span>
				</button>

				{isAdvancedOpen && (
					<div
						data-testid="advanced-governance-drawer"
						style={{
							marginTop: "12px",
							padding: "16px",
							backgroundColor: C.bgPage,
							borderRadius: "6px",
							border: `1px solid ${C.borderLine}`,
							display: "flex",
							flexDirection: "column",
							gap: "12px",
						}}
					>
						<div>
							<label htmlFor="input-target-hub-overrides" style={S_LABEL}>
								Target Hub Override Hints (e.g. table:HubEntity):
							</label>
							<input
								type="text"
								id="input-target-hub-overrides"
								data-testid="input-target-hub-overrides"
								placeholder="positions:HubPosition, security_master:HubInstrument"
								value={targetHubOverrides}
								onChange={(e) =>
									handleAdvancedOverrideChange(
										"target_hub_overrides",
										e.target.value,
									)
								}
								style={S_INPUT}
							/>
							<div
								style={{
									fontSize: "11px",
									color: C.inkMuted,
									marginTop: "2px",
								}}
							>
								Overrides auto-inferred concept mapping for specified source
								tables.
							</div>
						</div>

						<div>
							<label htmlFor="input-business-key-overrides" style={S_LABEL}>
								Business Key Override Hints (e.g. table:col1,col2):
							</label>
							<input
								type="text"
								id="input-business-key-overrides"
								data-testid="input-business-key-overrides"
								placeholder="fund_portfolio:fund_code,portfolio_id"
								value={bkOverrides}
								onChange={(e) =>
									handleAdvancedOverrideChange(
										"business_key_overrides",
										e.target.value,
									)
								}
								style={S_INPUT}
							/>
							<div
								style={{
									fontSize: "11px",
									color: C.inkMuted,
									marginTop: "2px",
								}}
							>
								Forces explicit business keys when physical PK priors are absent
								or ambiguous.
							</div>
						</div>

						<div>
							<label htmlFor="select-harness-provider" style={S_LABEL}>
								Harness Provider:
							</label>
							<select
								id="select-harness-provider"
								data-testid="select-harness-provider"
								value={formState.harnessProvider || "agy"}
								onChange={(e) => onChange({ harnessProvider: e.target.value })}
								style={S_INPUT}
							>
								<option value="agy">
									Google Antigravity SDK (agy) — Recommended
								</option>
								<option value="agy-cli">
									Standard CLI Runner (agy-cli)
								</option>
							</select>
						</div>
					</div>
				)}
			</div>
		</section>
	);
}
