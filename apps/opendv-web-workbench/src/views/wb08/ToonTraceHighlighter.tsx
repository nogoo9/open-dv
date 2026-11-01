import type React from "react";
import { useState } from "react";

export interface ToonViolationRow {
	readonly gate: string;
	readonly rule: string;
	readonly action: "REJECT" | "AUTO-FIX" | "HOLD";
	readonly target: string;
	readonly fix: string;
	readonly detail: string;
}

export interface ToonTraceHighlighterProps {
	readonly attempt1Trace?: string;
	readonly attempt2Trace?: string;
	readonly violations?: readonly ToonViolationRow[];
	readonly tokenCount?: number;
	readonly initialAttempt?: 1 | 2;
}

export const DEFAULT_TOON_ATTEMPT_1_TRACE = `@toon_v1
verify_batch{attempt,verdict,token_count}:
1,REJECT,48
violations[2]{gate,rule,action,target,fix}:
G1,DV-COL-012,REJECT,hub_limited_partner,set_require_src_qualifier=true;set_bkcc_prefix=LEGACY_GP_ADMIN
G4,ALG-A1,AUTO-FIX,sat_lp_comm_fx,merge_into=sat_lp_commitment_terms;w_amp_mb_day=4.20`;

export const DEFAULT_TOON_ATTEMPT_2_TRACE = `@toon_v1
verify_batch{attempt,verdict,token_count}:
2,PASS,29
violations[0]{gate,rule,action,target,fix}:
gates_passed[6]{gate,status}:
G0,PASS
G1,PASS
G2,PASS
G3,PASS
G4,PASS
G5,PASS`;

export const DEFAULT_TOON_VIOLATIONS: readonly ToonViolationRow[] = [
	{
		gate: "G1",
		rule: "DV-COL-012",
		action: "REJECT",
		target: "hub_limited_partner",
		fix: "set_require_src_qualifier=true;set_bkcc_prefix=LEGACY_GP_ADMIN",
		detail:
			"N = 2 disjoint source feeds map to hub_limited_partner without BKCC prefix; LP-101 collides between CalPERS and Bavarian Pension.",
	},
	{
		gate: "G4",
		rule: "ALG-A1",
		action: "AUTO-FIX",
		target: "sat_lp_comm_fx",
		fix: "merge_into=sat_lp_commitment_terms;w_amp_mb_day=4.20",
		detail:
			"W_amp = 4.20 MB/day < 50.00 MB/day threshold; speculative FX satellite auto-consolidated into sat_lp_commitment_terms.",
	},
];

export function ToonTraceHighlighter({
	attempt1Trace = DEFAULT_TOON_ATTEMPT_1_TRACE,
	attempt2Trace = DEFAULT_TOON_ATTEMPT_2_TRACE,
	violations = DEFAULT_TOON_VIOLATIONS,
	tokenCount = 48,
	initialAttempt = 1,
}: ToonTraceHighlighterProps): React.ReactElement {
	const [activeAttempt, setActiveAttempt] = useState<1 | 2>(initialAttempt);
	const displayedTrace = activeAttempt === 1 ? attempt1Trace : attempt2Trace;

	return (
		<div
			data-testid="toon-trace-highlighter"
			style={{
				border: "1px solid var(--dv-border-subtle)",
				borderRadius: "8px",
				padding: "12px",
				background: "var(--dv-surface-1)",
			}}
		>
			<div
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					gap: "8px",
					flexWrap: "wrap",
					marginBottom: "10px",
				}}
			>
				<div>
					<h4 style={{ margin: 0, fontSize: "14px" }}>
						Token-Oriented Object Notation (<code>@toon_v1</code>) Diagnostic
						Trace
					</h4>
					<div style={{ fontSize: "12px", color: "var(--dv-text-muted)" }}>
						Compact structured verifier feedback ({tokenCount} tokens in Attempt
						1 vs ~210 JSON tokens)
					</div>
				</div>
				<div style={{ display: "flex", gap: "6px" }}>
					<button
						type="button"
						data-testid="toon-attempt-1-btn"
						onClick={() => setActiveAttempt(1)}
						style={{
							padding: "4px 10px",
							borderRadius: "4px",
							border: "1px solid var(--dv-border-subtle)",
							background:
								activeAttempt === 1
									? "var(--dv-surface-2)"
									: "var(--dv-bg-canvas)",
							color: "var(--dv-text-primary)",
							fontFamily: "ui-monospace, monospace",
							fontSize: "11px",
							cursor: "pointer",
						}}
					>
						Attempt k=1 (REJECT · {tokenCount} tok)
					</button>
					<button
						type="button"
						data-testid="toon-attempt-2-btn"
						onClick={() => setActiveAttempt(2)}
						style={{
							padding: "4px 10px",
							borderRadius: "4px",
							border: "1px solid var(--dv-border-subtle)",
							background:
								activeAttempt === 2
									? "var(--dv-surface-2)"
									: "var(--dv-bg-canvas)",
							color: "var(--dv-text-primary)",
							fontFamily: "ui-monospace, monospace",
							fontSize: "11px",
							cursor: "pointer",
						}}
					>
						Attempt k=2 (PASS · 0 violations)
					</button>
				</div>
			</div>

			<pre
				data-testid="toon-raw-trace"
				style={{
					margin: "0 0 12px 0",
					padding: "10px",
					borderRadius: "6px",
					background: "var(--dv-bg-canvas)",
					border: "1px solid var(--dv-border-subtle)",
					fontFamily: "ui-monospace, monospace",
					fontSize: "12px",
					lineHeight: 1.5,
					overflowX: "auto",
					color: "var(--dv-text-primary)",
				}}
			>
				{displayedTrace}
			</pre>

			<table
				data-testid="toon-violations-table"
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
						<th style={{ padding: "6px" }}>Gate</th>
						<th style={{ padding: "6px" }}>Rule ID</th>
						<th style={{ padding: "6px" }}>Action</th>
						<th style={{ padding: "6px" }}>Target Artifact</th>
						<th style={{ padding: "6px" }}>Deterministic Fix Prescription</th>
					</tr>
				</thead>
				<tbody>
					{violations.map((row) => (
						<tr
							key={`${row.gate}-${row.rule}`}
							data-testid={`toon-violation-${row.rule}`}
							style={{ borderBottom: "1px solid var(--dv-border-subtle)" }}
						>
							<td
								style={{
									padding: "6px",
									fontFamily: "ui-monospace, monospace",
								}}
							>
								{row.gate}
							</td>
							<td
								style={{
									padding: "6px",
									fontFamily: "ui-monospace, monospace",
									fontWeight: 700,
								}}
							>
								{row.rule}
							</td>
							<td
								style={{
									padding: "6px",
									fontFamily: "ui-monospace, monospace",
									fontWeight: 700,
									color:
										row.action === "REJECT"
											? "var(--dv-gate-reject)"
											: "var(--dv-gate-pass)",
								}}
							>
								{row.action}
							</td>
							<td
								style={{
									padding: "6px",
									fontFamily: "ui-monospace, monospace",
								}}
							>
								{row.target}
							</td>
							<td style={{ padding: "6px" }}>
								<div style={{ fontFamily: "ui-monospace, monospace" }}>
									{row.fix}
								</div>
								<div
									style={{ fontSize: "11px", color: "var(--dv-text-muted)" }}
								>
									{row.detail}
								</div>
							</td>
						</tr>
					))}
				</tbody>
			</table>
		</div>
	);
}
