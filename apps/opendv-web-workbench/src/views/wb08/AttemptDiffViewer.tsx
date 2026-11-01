import type React from "react";

export interface AttemptDiffLine {
	readonly lineNumber: number;
	readonly changeType: "context" | "removed" | "added";
	readonly content: string;
	readonly ruleAnnotation?: string;
}

export interface AttemptDiffViewerProps {
	readonly lines?: readonly AttemptDiffLine[];
	readonly contractArtifactId?: string;
}

export const DEFAULT_ATTEMPT_DIFF_LINES: readonly AttemptDiffLine[] = [
	{
		lineNumber: 1,
		changeType: "context",
		content: "kind: NoMistakeDataVault2ContractV5",
	},
	{
		lineNumber: 2,
		changeType: "context",
		content: "hubs:",
	},
	{
		lineNumber: 3,
		changeType: "context",
		content: "  - name: hub_limited_partner",
	},
	{
		lineNumber: 4,
		changeType: "context",
		content: "    business_key_columns: [lp_id]",
	},
	{
		lineNumber: 5,
		changeType: "removed",
		content: "    require_src_qualifier: false",
		ruleAnnotation: "DV-COL-012 REJECT (k=1)",
	},
	{
		lineNumber: 6,
		changeType: "removed",
		content: "    bkcc_prefix: null",
		ruleAnnotation: "DV-COL-012 REJECT (k=1)",
	},
	{
		lineNumber: 7,
		changeType: "added",
		content: "    require_src_qualifier: true",
		ruleAnnotation: "DV-COL-012 REPAIRED (k=2)",
	},
	{
		lineNumber: 8,
		changeType: "added",
		content: '    bkcc_prefix: "LEGACY_GP_ADMIN"',
		ruleAnnotation: "DV-COL-012 REPAIRED (k=2)",
	},
	{
		lineNumber: 9,
		changeType: "context",
		content: "satellites:",
	},
	{
		lineNumber: 10,
		changeType: "context",
		content: "  - name: sat_lp_commitment_terms",
	},
	{
		lineNumber: 11,
		changeType: "removed",
		content: "    payload_columns: [committed_amount_usd, management_fee_bps]",
		ruleAnnotation: "ALG-A1 AUTO-FIX (k=1)",
	},
	{
		lineNumber: 12,
		changeType: "added",
		content:
			"    payload_columns: [committed_amount_usd, management_fee_bps, settlement_currency_iso, spot_fx_rate_to_usd]",
		ruleAnnotation: "ALG-A1 Consolidated (k=2)",
	},
	{
		lineNumber: 13,
		changeType: "removed",
		content:
			"  - name: sat_lp_commitment_fx_high_freq # W_amp = 4.20 MB/day < 50 MB/day",
		ruleAnnotation: "ALG-A1 Pruned Satellite",
	},
];

export function AttemptDiffViewer({
	lines = DEFAULT_ATTEMPT_DIFF_LINES,
	contractArtifactId = "NoMistakeDataVault2ContractV5 (Attempt k=1 -> Attempt k=2)",
}: AttemptDiffViewerProps): React.ReactElement {
	return (
		<div
			data-testid="attempt-diff-viewer"
			style={{
				border: "1px solid var(--dv-border-subtle)",
				borderRadius: "8px",
				padding: "12px",
				background: "var(--dv-bg-surface)",
			}}
		>
			<div
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					marginBottom: "8px",
					flexWrap: "wrap",
					gap: "8px",
				}}
			>
				<h4 style={{ margin: 0, fontSize: "14px" }}>
					Contract Self-Healing YAML Diff (<code>{contractArtifactId}</code>)
				</h4>
				<span
					style={{
						fontSize: "11px",
						fontFamily: "var(--dv-font-mono)",
						color: "var(--dv-status-pass)",
					}}
				>
					-4 rejected lines / +3 repaired lines
				</span>
			</div>

			<div
				style={{
					border: "1px solid var(--dv-border-subtle)",
					borderRadius: "6px",
					overflow: "hidden",
					background: "var(--dv-bg-canvas)",
					fontFamily: "var(--dv-font-mono)",
					fontSize: "12px",
				}}
			>
				{lines.map((line) => {
					const prefix =
						line.changeType === "added"
							? "+"
							: line.changeType === "removed"
								? "-"
								: " ";
					const color =
						line.changeType === "added"
							? "var(--dv-status-pass)"
							: line.changeType === "removed"
								? "var(--dv-status-fail)"
								: "var(--dv-text-primary)";

					return (
						<div
							key={line.lineNumber}
							data-testid={`diff-line-${line.lineNumber}`}
							style={{
								display: "flex",
								justifyContent: "space-between",
								gap: "12px",
								padding: "4px 10px",
								borderBottom: "1px solid var(--dv-border-subtle)",
								color,
							}}
						>
							<div>
								<span
									style={{
										display: "inline-block",
										width: "28px",
										color: "var(--dv-text-muted)",
									}}
								>
									{line.lineNumber}
								</span>
								<span
									style={{
										display: "inline-block",
										width: "16px",
										fontWeight: 700,
									}}
								>
									{prefix}
								</span>
								<span>{line.content}</span>
							</div>
							{line.ruleAnnotation ? (
								<span
									style={{
										fontSize: "11px",
										color: "var(--dv-text-muted)",
										whiteSpace: "nowrap",
									}}
								>
									[{line.ruleAnnotation}]
								</span>
							) : null}
						</div>
					);
				})}
			</div>
		</div>
	);
}
