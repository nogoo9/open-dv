/**
 * 6-Gate Verification Funnel & Scoreboard Card (`SixGateFunnelCard.tsx`)
 * Visualizes the 6 axi-dv verification gates (Gate 0–5) and active exception scorecard (`docs/06` §4.1).
 */

import type {
	VaultObservabilityEventDTO,
	VerdictStatus,
} from "@opendv/protocol-ts";
import { useState } from "react";
import type {
	GateCardPluginProps,
	WorkbenchGateCardPlugin,
} from "../../plugins/plugin-types";
import { VERDICT_VISUAL_GRAMMAR } from "../../theme/theme-registry";

export const VIZ_GATE_01_ID = "VIZ-GATE-01";

export interface GateInfo {
	gateNumber: number;
	gateKey: string;
	title: string;
	stage: string;
	ruleFamily: string;
	description: string;
}

export const SIX_GATES_CATALOG: readonly GateInfo[] = [
	{
		gateNumber: 0,
		gateKey: "gate_0_profile_drift",
		title: "Profile & Schema Drift",
		stage: "STAGE_01_SCOUT_SOURCE",
		ruleFamily: "EVO-01..05, ALG-A9A",
		description:
			"Detects schema evolution, column types, and drift categories.",
	},
	{
		gateNumber: 1,
		gateKey: "gate_1_contract_grain",
		title: "Contract, Keys & Grain",
		stage: "STAGE_03 / STAGE_04",
		ruleFamily: "DV-COL-001..012, KEY-01..05",
		description:
			"Enforces 100% column accounting, BKCC isolation & grain consistency.",
	},
	{
		gateNumber: 2,
		gateKey: "gate_2_sqlglot_ast",
		title: "SQLGlot AST & Determinism",
		stage: "STAGE_07_DETERMINISTIC_COMPILER",
		ruleFamily: "DV-AST-001..006",
		description:
			"Validates AST syntax, deterministic formatting, and zero LLM SQL.",
	},
	{
		gateNumber: 3,
		gateKey: "gate_3_networkx_dag",
		title: "NetworkX DAG Topology",
		stage: "STAGE_05_AXI_DV_STATIC_GATES",
		ruleFamily: "DV-STR-001..014, INV-01..06",
		description:
			"Bipartite DAG validation: zero Hub-Hub edges, Link arity >= 2.",
	},
	{
		gateNumber: 4,
		gateKey: "gate_4_yagni_cost_guard",
		title: "YAGNI Cost Guard & Write-Amp",
		stage: "STAGE_05_AXI_DV_STATIC_GATES",
		ruleFamily: "ALG-A1, YAGNI-01",
		description:
			"Evaluates write amplification W_amp and merges low-volume satellites.",
	},
	{
		gateNumber: 5,
		gateKey: "gate_5_duckdb_sandbox",
		title: "DuckDB Dynamic Sandbox",
		stage: "STAGE_06_AXI_DV_DYNAMIC_SANDBOX",
		ruleFamily: "DV-DYN-001..015, TMP-01..04",
		description:
			"Replays 4 adversarial batches in memory; verifies PIT cut-offs.",
	},
];

export interface SixGateFunnelCardProps {
	event?: VaultObservabilityEventDTO | null;
	isExpanded?: boolean;
	onToggleExpand?: () => void;
}

export function SixGateFunnelCard({
	event,
	isExpanded = true,
	onToggleExpand,
}: SixGateFunnelCardProps) {
	const [selectedGate, setSelectedGate] = useState<number | null>(null);

	// Extract scorecard from event layer2 payload if present
	const scorecard = (event?.layer2_visual_payload?.gate_scorecard ??
		{}) as Record<string, VerdictStatus>;

	// Compute funnel counts
	let passCount = 0;
	let autofixCount = 0;
	let rejectCount = 0;
	let askuserCount = 0;

	for (const gate of SIX_GATES_CATALOG) {
		const verdict = scorecard[gate.gateKey] ?? "PASS";
		if (verdict === "PASS") passCount++;
		else if (verdict === "AUTO-FIX" || verdict === "AUTO_REPAIRED")
			autofixCount++;
		else if (verdict === "REJECT-WITH-TRACE") rejectCount++;
		else if (verdict === "ASK-USER") askuserCount++;
	}

	const activeExceptions: Array<{
		gateNumber: number;
		gateTitle: string;
		verdict: VerdictStatus;
		ruleId: string;
		remediationText: string;
	}> = [];

	if (scorecard.gate_1_contract_grain === "AUTO_REPAIRED") {
		activeExceptions.push({
			gateNumber: 1,
			gateTitle: "Contract, Keys & Grain",
			verdict: "AUTO_REPAIRED",
			ruleId: "DV-COL-012",
			remediationText:
				"Enforced require_src_qualifier=true and bkcc_prefix='LEGACY_GP_ADMIN' on legacy LP keys.",
		});
	}
	if (scorecard.gate_4_yagni_cost_guard === "AUTO-FIX") {
		activeExceptions.push({
			gateNumber: 4,
			gateTitle: "YAGNI Cost Guard",
			verdict: "AUTO-FIX",
			ruleId: "ALG-A1",
			remediationText:
				"Merged 3 low-volume FX columns into sat_lp_commitment_investran (W_amp = 4.20 MB/d < 50.0 MB/d).",
		});
	}

	return (
		<article
			aria-label="6-Gate Verification Funnel & Scoreboard"
			data-testid="six-gate-funnel-card"
			data-gate-id={VIZ_GATE_01_ID}
			style={{
				backgroundColor: "var(--dv-surface-1)",
				border: "1px solid var(--dv-border-subtle)",
				borderRadius: "0.5rem",
				padding: "1rem",
			}}
		>
			{/* Header & Toggle */}
			<div
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					marginBottom: "0.75rem",
				}}
			>
				<div>
					<div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
						<span
							style={{
								fontSize: "0.75rem",
								fontWeight: 700,
								color: "var(--dv-gate-pass)",
							}}
						>
							● [PASS]
						</span>
						<h3
							style={{
								margin: 0,
								fontSize: "0.875rem",
								fontWeight: 600,
								color: "var(--dv-text-primary)",
							}}
						>
							6-Gate axi-dv Verification Funnel &amp; Scorecard (VIZ-GATE-01)
						</h3>
					</div>
					<span
						style={{ fontSize: "0.75rem", color: "var(--dv-text-secondary)" }}
					>
						Formal deterministic verification gates guarding against data
						corruption
					</span>
				</div>
				{onToggleExpand && (
					<button
						type="button"
						onClick={onToggleExpand}
						style={{
							backgroundColor: "var(--dv-surface-2)",
							color: "var(--dv-text-primary)",
							border: "1px solid var(--dv-border-strong)",
							borderRadius: "0.25rem",
							padding: "0.25rem 0.5rem",
							fontSize: "0.75rem",
							cursor: "pointer",
						}}
					>
						{isExpanded ? "Collapse Funnel" : "Expand Funnel"}
					</button>
				)}
			</div>

			{/* Funnel Verdict Counters */}
			<div
				data-testid="funnel-counters"
				style={{
					display: "grid",
					gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))",
					gap: "0.5rem",
					marginBottom: "0.75rem",
				}}
			>
				<div
					style={{
						backgroundColor: "var(--dv-surface-2)",
						border: "1px solid var(--dv-border-subtle)",
						borderRadius: "0.375rem",
						padding: "0.5rem",
						textAlign: "center",
					}}
				>
					<div
						style={{ fontSize: "0.7rem", color: "var(--dv-text-secondary)" }}
					>
						PASS
					</div>
					<div
						style={{
							fontSize: "1.25rem",
							fontWeight: 700,
							color: "var(--dv-gate-pass)",
						}}
					>
						{passCount} / 6
					</div>
				</div>

				<div
					style={{
						backgroundColor: "var(--dv-surface-2)",
						border: "1px solid var(--dv-border-subtle)",
						borderRadius: "0.375rem",
						padding: "0.5rem",
						textAlign: "center",
					}}
				>
					<div
						style={{ fontSize: "0.7rem", color: "var(--dv-text-secondary)" }}
					>
						AUTO-FIX / REPAIRED
					</div>
					<div
						style={{
							fontSize: "1.25rem",
							fontWeight: 700,
							color: "var(--dv-gate-autofix)",
						}}
					>
						{autofixCount}
					</div>
				</div>

				<div
					style={{
						backgroundColor: "var(--dv-surface-2)",
						border: "1px solid var(--dv-border-subtle)",
						borderRadius: "0.375rem",
						padding: "0.5rem",
						textAlign: "center",
					}}
				>
					<div
						style={{ fontSize: "0.7rem", color: "var(--dv-text-secondary)" }}
					>
						REJECT
					</div>
					<div
						style={{
							fontSize: "1.25rem",
							fontWeight: 700,
							color: "var(--dv-gate-reject)",
						}}
					>
						{rejectCount}
					</div>
				</div>

				<div
					style={{
						backgroundColor: "var(--dv-surface-2)",
						border: "1px solid var(--dv-border-subtle)",
						borderRadius: "0.375rem",
						padding: "0.5rem",
						textAlign: "center",
					}}
				>
					<div
						style={{ fontSize: "0.7rem", color: "var(--dv-text-secondary)" }}
					>
						CAPTAIN HOLD
					</div>
					<div
						style={{
							fontSize: "1.25rem",
							fontWeight: 700,
							color: "var(--dv-gate-askuser)",
						}}
					>
						{askuserCount}
					</div>
				</div>
			</div>

			{isExpanded && (
				<>
					{/* 6 Gates Grid */}
					<div
						style={{
							display: "grid",
							gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
							gap: "0.5rem",
							marginBottom: "0.75rem",
						}}
					>
						{SIX_GATES_CATALOG.map((gate) => {
							const verdict = scorecard[gate.gateKey] ?? "PASS";
							const grammar =
								VERDICT_VISUAL_GRAMMAR[verdict] ?? VERDICT_VISUAL_GRAMMAR.PASS;
							const isSelected = selectedGate === gate.gateNumber;

							return (
								<button
									key={gate.gateNumber}
									type="button"
									data-testid={`gate-card-${gate.gateNumber}`}
									onClick={() =>
										setSelectedGate(isSelected ? null : gate.gateNumber)
									}
									style={{
										display: "block",
										width: "100%",
										textAlign: "left",
										backgroundColor: isSelected
											? "var(--dv-surface-3)"
											: "var(--dv-surface-2)",
										border: `1.5px solid var(${grammar.cssColorVar})`,
										borderRadius: "0.375rem",
										padding: "0.5rem 0.75rem",
										cursor: "pointer",
										color: "inherit",
									}}
								>
									<div
										style={{
											display: "flex",
											justifyContent: "space-between",
											alignItems: "center",
											marginBottom: "0.25rem",
										}}
									>
										<strong
											style={{
												fontSize: "0.75rem",
												color: "var(--dv-text-primary)",
											}}
										>
											Gate {gate.gateNumber}: {gate.title}
										</strong>
										<span
											style={{
												fontSize: "0.7rem",
												fontWeight: 700,
												color: `var(${grammar.cssColorVar})`,
											}}
										>
											{grammar.shapeSymbol} {grammar.prefixBadge}
										</span>
									</div>

									<div
										style={{
											fontSize: "0.6875rem",
											color: "var(--dv-text-secondary)",
											marginBottom: "0.25rem",
										}}
									>
										{gate.description}
									</div>

									<div
										style={{
											fontSize: "0.65rem",
											fontFamily: "ui-monospace, monospace",
											color: "var(--dv-text-muted)",
										}}
									>
										Rules: {gate.ruleFamily}
									</div>
								</button>
							);
						})}
					</div>

					{/* Active Exception Scorecard */}
					{activeExceptions.length > 0 && (
						<div
							data-testid="active-exception-scorecard"
							style={{
								backgroundColor: "var(--dv-surface-2)",
								border: "1px dashed var(--dv-border-strong)",
								borderRadius: "0.375rem",
								padding: "0.75rem",
							}}
						>
							<div
								style={{
									display: "flex",
									alignItems: "center",
									gap: "0.5rem",
									marginBottom: "0.5rem",
								}}
							>
								<span
									style={{
										color: "var(--dv-gate-autofix)",
										fontWeight: 700,
										fontSize: "0.75rem",
									}}
								>
									◆ [AUTO-FIX / AUTO_REPAIRED]
								</span>
								<strong
									style={{
										fontSize: "0.75rem",
										color: "var(--dv-text-primary)",
									}}
								>
									Active Exception Scorecard ({activeExceptions.length} Resolved
									Exceptions)
								</strong>
							</div>

							<div
								style={{
									display: "flex",
									flexDirection: "column",
									gap: "0.5rem",
								}}
							>
								{activeExceptions.map((ex) => (
									<div
										key={ex.ruleId}
										data-testid={`exception-${ex.ruleId}`}
										style={{
											backgroundColor: "var(--dv-surface-1)",
											border: "1px solid var(--dv-border-subtle)",
											borderRadius: "0.25rem",
											padding: "0.4rem 0.6rem",
											fontSize: "0.75rem",
										}}
									>
										<div
											style={{
												display: "flex",
												justifyContent: "space-between",
												marginBottom: "0.2rem",
											}}
										>
											<span style={{ fontWeight: 600 }}>
												Gate {ex.gateNumber} ({ex.gateTitle}) — {ex.ruleId}
											</span>
											<span
												style={{
													color: "var(--dv-gate-autofix)",
													fontFamily: "ui-monospace, monospace",
												}}
											>
												{ex.verdict}
											</span>
										</div>
										<p
											style={{
												margin: 0,
												color: "var(--dv-text-secondary)",
												fontSize: "0.7rem",
											}}
										>
											{ex.remediationText}
										</p>
									</div>
								))}
							</div>
						</div>
					)}
				</>
			)}
		</article>
	);
}

export const VIZ_GATE_01_PLUGIN: WorkbenchGateCardPlugin = {
	gateId: VIZ_GATE_01_ID,
	title: "6-Gate axi-dv Verification Funnel & Scoreboard",
	ruleIds: ["DV-STR-001", "KEY-01", "DV-COL-012", "ALG-A1", "DV-DYN-001"],
	component: (props: GateCardPluginProps) => (
		<SixGateFunnelCard
			event={props.event}
			isExpanded={props.isExpanded}
			onToggleExpand={props.onToggleExpand}
		/>
	),
};
