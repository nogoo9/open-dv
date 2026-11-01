import type React from "react";

export type SchemaDriftCategory =
	| "ADDITIVE"
	| "SOFT_DEPRECATED"
	| "RENAMED"
	| "TYPE_WIDENED"
	| "KEY_DRIFT";

export interface DriftDeltaRow {
	readonly driftId: string;
	readonly sourceTable: string;
	readonly columnT0: string;
	readonly columnT1: string;
	readonly typeT0: string;
	readonly typeT1: string;
	readonly category: SchemaDriftCategory;
	readonly levenshteinSimilarity: number | null;
	readonly jaccardValueOverlap: number | null;
	readonly resolutionAction: string;
	readonly historicalHashDiffBroken: number;
}

export interface DriftDeltaGridProps {
	readonly rows?: readonly DriftDeltaRow[];
}

export const DEFAULT_DRIFT_DELTA_ROWS: readonly DriftDeltaRow[] = [
	{
		driftId: "DRIFT-01",
		sourceTable: "src_ilpa_commitments_day3",
		columnT0: "— (absent in t)",
		columnT1: "ilpa_esg_rating",
		typeT0: "—",
		typeT1: "VARCHAR(16) NULL",
		category: "ADDITIVE",
		levenshteinSimilarity: null,
		jaccardValueOverlap: null,
		resolutionAction:
			"ALTER TABLE sat_lp_commitment_terms ADD COLUMN ilpa_esg_rating (non-breaking append)",
		historicalHashDiffBroken: 0,
	},
	{
		driftId: "DRIFT-02",
		sourceTable: "src_legacy_gp_admin_lp_day3",
		columnT0: "deprecated_fax_no",
		columnT1: "— (dropped in t+1)",
		typeT0: "VARCHAR(32) NULL",
		typeT1: "NULL AS deprecated_fax_no",
		category: "SOFT_DEPRECATED",
		levenshteinSimilarity: null,
		jaccardValueOverlap: null,
		resolutionAction:
			"Stage 0 projection injects typed NULL alias; historical satellite rows preserved intact",
		historicalHashDiffBroken: 0,
	},
	{
		driftId: "DRIFT-03",
		sourceTable: "src_ilpa_commitments_day3",
		columnT0: "committed_amt",
		columnT1: "commitment_amount_usd",
		typeT0: "DECIMAL(18,2)",
		typeT1: "DECIMAL(18,2)",
		category: "RENAMED",
		levenshteinSimilarity: 0.87,
		jaccardValueOverlap: 0.994,
		resolutionAction:
			"Stage 0 alias: commitment_amount_usd AS committed_amt (sim_Lev = 0.87 >= 0.85, J_val = 0.994 >= 0.90)",
		historicalHashDiffBroken: 0,
	},
	{
		driftId: "DRIFT-04",
		sourceTable: "src_portco_valuations_day3",
		columnT0: "company_name",
		columnT1: "company_name",
		typeT0: "VARCHAR(128)",
		typeT1: "VARCHAR(255)",
		category: "TYPE_WIDENED",
		levenshteinSimilarity: 1.0,
		jaccardValueOverlap: 1.0,
		resolutionAction:
			"Safe widening VARCHAR(128) -> VARCHAR(255); Algorithm A.5 canonical CAST(AS VARCHAR) unaffected",
		historicalHashDiffBroken: 0,
	},
	{
		driftId: "DRIFT-05",
		sourceTable: "all_feeds_s1_t_to_t1",
		columnT0: "lp_id / fund_code / portco_lei",
		columnT1: "lp_id / fund_code / portco_lei",
		typeT0: "PK / BK Invariant",
		typeT1: "PK / BK Invariant (0 key changes)",
		category: "KEY_DRIFT",
		levenshteinSimilarity: 1.0,
		jaccardValueOverlap: 1.0,
		resolutionAction:
			"0 Key Drift violations detected; no Tier-U3 Captain-Hold escalation required for S_1(t) -> S_1(t+1)",
		historicalHashDiffBroken: 0,
	},
];

export function DriftDeltaGrid({
	rows = DEFAULT_DRIFT_DELTA_ROWS,
}: DriftDeltaGridProps): React.ReactElement {
	return (
		<div
			data-testid="drift-delta-grid"
			style={{
				border: "1px solid var(--dv-border-subtle)",
				borderRadius: "8px",
				padding: "12px",
				background: "var(--dv-surface-1)",
				overflowX: "auto",
			}}
		>
			<h4 style={{ margin: "0 0 8px 0", fontSize: "14px" }}>
				Intra-Source Schema Evolution Delta Grid (<code>S_1(t) → S_1(t+1)</code>{" "}
				· Algorithm A.9a)
			</h4>
			<table
				style={{
					width: "100%",
					borderCollapse: "collapse",
					fontSize: "12px",
				}}
			>
				<thead>
					<tr
						style={{
							textAlign: "left",
							borderBottom: "1px solid var(--dv-border-subtle)",
							color: "var(--dv-text-muted)",
						}}
					>
						<th style={{ padding: "6px" }}>ID</th>
						<th style={{ padding: "6px" }}>Category</th>
						<th style={{ padding: "6px" }}>Source Feed</th>
						<th style={{ padding: "6px" }}>Column S₁(t) → S₁(t+1)</th>
						<th style={{ padding: "6px" }}>Type Delta</th>
						<th style={{ padding: "6px" }}>Similarity (Lev / J_val)</th>
						<th style={{ padding: "6px" }}>Non-Breaking Resolution</th>
						<th style={{ padding: "6px" }}>Broken HashDiffs</th>
					</tr>
				</thead>
				<tbody>
					{rows.map((row) => (
						<tr
							key={row.driftId}
							data-testid={`drift-row-${row.driftId}`}
							style={{ borderBottom: "1px solid var(--dv-border-subtle)" }}
						>
							<td
								style={{
									padding: "6px",
									fontFamily: "ui-monospace, monospace",
								}}
							>
								{row.driftId}
							</td>
							<td
								style={{
									padding: "6px",
									fontFamily: "ui-monospace, monospace",
									fontWeight: 700,
									color:
										row.category === "KEY_DRIFT"
											? "var(--dv-gate-pass)"
											: "var(--dv-gate-askuser)",
								}}
							>
								{row.category}
							</td>
							<td
								style={{
									padding: "6px",
									fontFamily: "ui-monospace, monospace",
								}}
							>
								{row.sourceTable}
							</td>
							<td
								style={{
									padding: "6px",
									fontFamily: "ui-monospace, monospace",
								}}
							>
								{row.columnT0} → {row.columnT1}
							</td>
							<td
								style={{
									padding: "6px",
									fontFamily: "ui-monospace, monospace",
								}}
							>
								{row.typeT0} → {row.typeT1}
							</td>
							<td
								style={{
									padding: "6px",
									fontFamily: "ui-monospace, monospace",
								}}
							>
								{row.levenshteinSimilarity !== null &&
								row.jaccardValueOverlap !== null
									? `sim=${row.levenshteinSimilarity.toFixed(2)} (>=0.85) · J=${row.jaccardValueOverlap.toFixed(3)} (>=0.90)`
									: "N/A"}
							</td>
							<td style={{ padding: "6px" }}>{row.resolutionAction}</td>
							<td
								style={{
									padding: "6px",
									fontFamily: "ui-monospace, monospace",
									fontWeight: 700,
									color: "var(--dv-gate-pass)",
								}}
							>
								{row.historicalHashDiffBroken}
							</td>
						</tr>
					))}
				</tbody>
			</table>
		</div>
	);
}
