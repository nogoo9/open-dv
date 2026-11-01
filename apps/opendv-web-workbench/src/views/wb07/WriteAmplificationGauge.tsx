/**
 * Gate 4 YAGNI Write-Amplification Cost Guard Gauge (`WriteAmplificationGauge.tsx`)
 * Visualizes Algorithm A.1 Dual-Threshold Cost Guard (`W_amp = 4.20 MB/day < 50.0 MB/day`
 * and `|C_general| = 6 < 10` -> `auto-fix: merge_satellites`) (`VIZ-GATE-05` Part B) (`docs/06` §4.7).
 */

import type { VaultObservabilityEventDTO } from "@opendv/protocol-ts";
import { useState } from "react";
import type {
	GateCardPluginProps,
	WorkbenchGateCardPlugin,
} from "../../plugins/plugin-types";

export const VIZ_GATE_05_PART_B_ID = "VIZ-GATE-05-PART-B";

export interface YagniCostEvaluationCase {
	id: "COMMITMENT_FX_MERGE" | "PORTCO_MNPI_PRESERVED";
	title: string;
	candidateSatellite: string;
	parentSatellite: string;
	generalColumnCount: number;
	minColumnsThreshold: number;
	dailyRows: number;
	highFreqChangeProb: number;
	lowFreqRowBytes: number;
	writeAmpMbPerDay: number;
	thresholdMbPerDay: number;
	governanceClassification: string;
	action: "auto-fix: merge_satellites" | "PRESERVE_SPLIT";
	verdict: "AUTO-FIX" | "PASS";
	rationale: string;
}

export const YAGNI_EVALUATION_CASES: readonly YagniCostEvaluationCase[] = [
	{
		id: "COMMITMENT_FX_MERGE",
		title: "Case A: Commitment FX/NAV Ticker Over-Split (Rejected -> Merged)",
		candidateSatellite: "sat_lp_commitment_fx_high_freq",
		parentSatellite: "sat_lp_commitment_investran",
		generalColumnCount: 6,
		minColumnsThreshold: 10,
		dailyRows: 157000,
		highFreqChangeProb: 0.85,
		lowFreqRowBytes: 33,
		writeAmpMbPerDay: 4.2,
		thresholdMbPerDay: 50.0,
		governanceClassification: "CONFIDENTIAL (Matches Parent)",
		action: "auto-fix: merge_satellites",
		verdict: "AUTO-FIX",
		rationale:
			"Write amplification is 4.20 MB/day (91.6% below 50.0 MB/day threshold) and |C_general| = 6 < 10. Merging eur_usd_rate, gbp_usd_rate, and daily_interim_nav_usd into sat_lp_commitment_investran eliminates 1 unnecessary Point-in-Time (PIT) join.",
	},
	{
		id: "PORTCO_MNPI_PRESERVED",
		title:
			"Case B: PortCo Valuation MNPI Split (Governance + Telemetry Contrast)",
		candidateSatellite: "sat_portco_valuation_mnpi",
		parentSatellite: "sat_portfolio_company_dealcloud",
		generalColumnCount: 12,
		minColumnsThreshold: 10,
		dailyRows: 1250000,
		highFreqChangeProb: 0.9,
		lowFreqRowBytes: 78,
		writeAmpMbPerDay: 84.5,
		thresholdMbPerDay: 50.0,
		governanceClassification: "MNPI (Axis 1 Governance Override)",
		action: "PRESERVE_SPLIT",
		verdict: "PASS",
		rationale:
			"Preserved via Algorithm A.2 Axis 1 Governance Isolation (MNPI_CONFIDENTIAL vs INTERNAL) and satisfies |C_general| = 12 >= 10 with W_amp = 84.50 MB/day >= 50.0 MB/day.",
	},
];

export interface WriteAmplificationGaugeProps {
	event?: VaultObservabilityEventDTO | undefined;
	isExpanded?: boolean | undefined;
	onToggleExpand?: (() => void) | undefined;
}

export function WriteAmplificationGauge({
	isExpanded = true,
	onToggleExpand,
}: WriteAmplificationGaugeProps) {
	const [selectedCaseId, setSelectedCaseId] = useState<
		YagniCostEvaluationCase["id"]
	>("COMMITMENT_FX_MERGE");

	if (!isExpanded) {
		return null;
	}

	const activeCase =
		YAGNI_EVALUATION_CASES.find((c) => c.id === selectedCaseId) ??
		YAGNI_EVALUATION_CASES[0];
	if (!activeCase) {
		return null;
	}
	const fillPct = Math.min(
		100,
		Math.round((activeCase.writeAmpMbPerDay / 100) * 100),
	);
	const thresholdMarkerPct = 50;

	return (
		<section
			aria-label="Gate 4 YAGNI Write-Amplification Cost Guard Gauge"
			data-testid="write-amplification-gauge"
			data-gate-id={VIZ_GATE_05_PART_B_ID}
			style={{
				backgroundColor: "var(--dv-surface-1)",
				border: "1.5px solid var(--dv-gate-autofix)",
				borderRadius: "0.5rem",
				padding: "1rem",
				display: "flex",
				flexDirection: "column",
				gap: "0.875rem",
			}}
		>
			{/* Header */}
			<div
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					flexWrap: "wrap",
					gap: "0.5rem",
					borderBottom: "1px solid var(--dv-border-subtle)",
					paddingBottom: "0.5rem",
				}}
			>
				<div>
					<div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
						<span
							style={{
								color: "var(--dv-gate-autofix)",
								fontWeight: 700,
								fontSize: "0.75rem",
							}}
						>
							◆ [ALG-A1 AUTO-FIX: merge_satellites]
						</span>
						<h3
							style={{
								margin: 0,
								fontSize: "0.875rem",
								fontWeight: 700,
								color: "var(--dv-text-primary)",
							}}
						>
							Gate 4 YAGNI Write-Amplification Cost Guard (Algorithm A.1)
						</h3>
					</div>
					<div
						style={{ fontSize: "0.7rem", color: "var(--dv-text-secondary)" }}
					>
						Dual-Threshold Satellite Split Criterion: |C_general| &gt;= 10 AND
						W_amp &gt;= 50.0 MB/day
					</div>
				</div>

				<div style={{ display: "flex", gap: "0.35rem" }}>
					{YAGNI_EVALUATION_CASES.map((item) => (
						<button
							key={item.id}
							type="button"
							onClick={() => setSelectedCaseId(item.id)}
							data-testid={`yagni-case-btn-${item.id}`}
							style={{
								backgroundColor:
									selectedCaseId === item.id
										? "var(--dv-surface-3)"
										: "var(--dv-surface-2)",
								color: "var(--dv-text-primary)",
								border: "1px solid var(--dv-border-strong)",
								borderRadius: "0.25rem",
								padding: "0.25rem 0.5rem",
								fontSize: "0.7rem",
								fontWeight: selectedCaseId === item.id ? 700 : 500,
								cursor: "pointer",
							}}
						>
							{item.id === "COMMITMENT_FX_MERGE"
								? "4.20 MB/day (Auto-Merge)"
								: "84.50 MB/day (Preserved)"}
						</button>
					))}
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
								fontSize: "0.7rem",
								cursor: "pointer",
							}}
						>
							Collapse
						</button>
					)}
				</div>
			</div>

			{/* Visual Gauge Bar */}
			<div
				data-testid="w-amp-gauge-meter"
				style={{
					backgroundColor: "var(--dv-surface-2)",
					border: "1px solid var(--dv-border-subtle)",
					borderRadius: "0.375rem",
					padding: "0.75rem",
				}}
			>
				<div
					style={{
						display: "flex",
						justifyContent: "space-between",
						fontSize: "0.75rem",
						fontWeight: 700,
						marginBottom: "0.4rem",
					}}
				>
					<span>
						Write Amplification:{" "}
						<code style={{ color: "var(--dv-gate-autofix)" }}>
							W_amp = {activeCase.writeAmpMbPerDay.toFixed(2)} MB/day
						</code>
					</span>
					<span>
						Threshold:{" "}
						<code style={{ color: "var(--dv-gate-askuser)" }}>
							{activeCase.thresholdMbPerDay.toFixed(1)} MB/day
						</code>
					</span>
				</div>

				<div
					style={{
						position: "relative",
						height: "18px",
						backgroundColor: "var(--dv-surface-1)",
						borderRadius: "9999px",
						border: "1px solid var(--dv-border-strong)",
						overflow: "hidden",
					}}
				>
					<div
						style={{
							width: `${fillPct}%`,
							height: "100%",
							backgroundColor:
								activeCase.writeAmpMbPerDay < activeCase.thresholdMbPerDay
									? "var(--dv-gate-autofix)"
									: "var(--dv-gate-pass)",
							transition: "width 200ms ease",
						}}
					/>
					<div
						title="50.0 MB/day YAGNI Split Threshold"
						style={{
							position: "absolute",
							left: `${thresholdMarkerPct}%`,
							top: 0,
							bottom: 0,
							width: "2px",
							backgroundColor: "var(--dv-gate-reject)",
						}}
					/>
				</div>

				<div
					style={{
						display: "flex",
						justifyContent: "space-between",
						fontSize: "0.65rem",
						color: "var(--dv-text-secondary)",
						marginTop: "0.3rem",
						fontFamily: "ui-monospace, monospace",
					}}
				>
					<span>0.0 MB/day</span>
					<span>4.20 MB/day (Commitment FX)</span>
					<span>| 50.0 MB/day (YAGNI Budget)</span>
					<span>100.0 MB/day</span>
				</div>
			</div>

			{/* Formula & Parameter Breakdown */}
			<div
				style={{
					display: "grid",
					gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
					gap: "0.5rem",
					fontSize: "0.72rem",
				}}
			>
				<div
					style={{
						backgroundColor: "var(--dv-surface-2)",
						border: "1px solid var(--dv-border-subtle)",
						borderRadius: "0.375rem",
						padding: "0.5rem 0.65rem",
					}}
				>
					<div style={{ color: "var(--dv-text-secondary)" }}>
						General Column Count
					</div>
					<strong
						style={{
							fontFamily: "ui-monospace, monospace",
							fontSize: "0.85rem",
						}}
					>
						|C_general| = {activeCase.generalColumnCount} (min{" "}
						{activeCase.minColumnsThreshold})
					</strong>
				</div>

				<div
					style={{
						backgroundColor: "var(--dv-surface-2)",
						border: "1px solid var(--dv-border-subtle)",
						borderRadius: "0.375rem",
						padding: "0.5rem 0.65rem",
					}}
				>
					<div style={{ color: "var(--dv-text-secondary)" }}>
						Daily Volume x P(ΔC_high)
					</div>
					<strong
						style={{
							fontFamily: "ui-monospace, monospace",
							fontSize: "0.85rem",
						}}
					>
						{activeCase.dailyRows.toLocaleString()} ×{" "}
						{activeCase.highFreqChangeProb} × {activeCase.lowFreqRowBytes} B
					</strong>
				</div>

				<div
					style={{
						backgroundColor: "var(--dv-surface-2)",
						border: "1px solid var(--dv-border-subtle)",
						borderRadius: "0.375rem",
						padding: "0.5rem 0.65rem",
					}}
				>
					<div style={{ color: "var(--dv-text-secondary)" }}>
						Deterministic Action
					</div>
					<strong
						style={{
							fontFamily: "ui-monospace, monospace",
							fontSize: "0.8rem",
							color: "var(--dv-gate-autofix)",
						}}
					>
						{activeCase.action}
					</strong>
				</div>
			</div>

			{/* Explanation & YAML Remediation Note */}
			<div
				style={{
					backgroundColor: "var(--dv-surface-2)",
					border: "1px solid var(--dv-border-subtle)",
					borderRadius: "0.375rem",
					padding: "0.65rem 0.75rem",
					fontSize: "0.72rem",
					color: "var(--dv-text-secondary)",
					lineHeight: 1.45,
				}}
			>
				<div
					style={{
						fontWeight: 700,
						color: "var(--dv-text-primary)",
						marginBottom: "0.25rem",
					}}
				>
					{activeCase.title}: <code>{activeCase.candidateSatellite}</code> →{" "}
					<code>{activeCase.parentSatellite}</code>
				</div>
				<div>{activeCase.rationale}</div>
			</div>
		</section>
	);
}

export const VIZ_GATE_05_PART_B_PLUGIN: WorkbenchGateCardPlugin = {
	gateId: VIZ_GATE_05_PART_B_ID,
	title: "Gate 4 YAGNI Write-Amplification Cost Guard & Alg A.4 Link Splitter",
	ruleIds: ["ALG-A1", "ALG-A4"],
	component: (props: GateCardPluginProps) => (
		<WriteAmplificationGauge
			event={props.event}
			isExpanded={props.isExpanded}
			onToggleExpand={props.onToggleExpand}
		/>
	),
};
