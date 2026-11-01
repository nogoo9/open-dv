import type React from "react";
import { useState } from "react";

export interface BiTemporalPoint {
	readonly versionLabel: string;
	readonly portfolioCompany: string;
	readonly portcoLei: string;
	readonly effectiveDts: string;
	readonly loadDts: string;
	readonly assertionCutoffTs: string;
	readonly evEbitdaMultiple: string;
	readonly enterpriseValueUsd: string;
	readonly hashDiffPrefix: string;
	readonly auditNote: string;
}

export interface BiTemporalTimelineProps {
	readonly points?: readonly BiTemporalPoint[];
}

export const DEFAULT_BITEMPORAL_POINTS: readonly BiTemporalPoint[] = [
	{
		versionLabel: "v1 · Original Q3 Valuation Filing",
		portfolioCompany: "Nordic Cloud AB",
		portcoLei: "529900T8BM49AURSDO55",
		effectiveDts: "2026-09-30 23:59:59 UTC",
		loadDts: "2026-10-05 08:15:00 UTC",
		assertionCutoffTs: "2026-10-15 12:00:00",
		evEbitdaMultiple: "12.80x",
		enterpriseValueUsd: "$216,280,000.00",
		hashDiffPrefix: "8A41..C109",
		auditNote:
			"Query D.10b at cutoff 2026-10-15 12:00:00 reproduces pre-restatement 12.80x multiple for October IC deck.",
	},
	{
		versionLabel: "v2 · Late-Arriving Q3 Audit Restatement (Batch 3)",
		portfolioCompany: "Nordic Cloud AB",
		portcoLei: "529900T8BM49AURSDO55",
		effectiveDts: "2026-09-30 23:59:59 UTC",
		loadDts: "2026-11-14 19:40:00 UTC",
		assertionCutoffTs: "2026-11-15 23:59:59",
		evEbitdaMultiple: "14.50x",
		enterpriseValueUsd: "$245,000,000.00",
		hashDiffPrefix: "F39E..7B22",
		auditNote:
			"Query D.10b at cutoff 2026-11-15 23:59:59 selects restated 14.50x multiple without overwriting v1 row.",
	},
];

export function BiTemporalTimeline({
	points = DEFAULT_BITEMPORAL_POINTS,
}: BiTemporalTimelineProps): React.ReactElement {
	const [selectedCutoffIndex, setSelectedCutoffIndex] = useState<number>(1);
	const activePoint = points[selectedCutoffIndex] ?? points[0];

	return (
		<div
			data-testid="bitemporal-timeline"
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
					flexWrap: "wrap",
					gap: "8px",
					marginBottom: "10px",
				}}
			>
				<div>
					<h4 style={{ margin: 0, fontSize: "14px" }}>
						Bi-Temporal Timeline (<code>load_dts</code> vs{" "}
						<code>effective_dts</code> · <code>sat_portco_valuation</code>)
					</h4>
					<div style={{ fontSize: "12px", color: "var(--dv-text-muted)" }}>
						Point-in-Time (PIT) auditability for Nordic Cloud AB (
						<code>529900T8BM49AURSDO55</code>): <code>12.80x → 14.50x</code> Q3
						valuation restatement
					</div>
				</div>
				<div style={{ display: "flex", gap: "6px" }}>
					{points.map((pt, idx) => (
						<button
							key={pt.assertionCutoffTs}
							type="button"
							data-testid={`bitemporal-cutoff-btn-${idx}`}
							onClick={() => setSelectedCutoffIndex(idx)}
							style={{
								padding: "4px 10px",
								borderRadius: "4px",
								border: "1px solid var(--dv-border-subtle)",
								background:
									selectedCutoffIndex === idx
										? "var(--dv-bg-elevated)"
										: "var(--dv-bg-canvas)",
								color: "var(--dv-text-primary)",
								fontFamily: "var(--dv-font-mono)",
								fontSize: "11px",
								cursor: "pointer",
							}}
						>
							As-Of {pt.assertionCutoffTs} ({pt.evEbitdaMultiple})
						</button>
					))}
				</div>
			</div>

			{activePoint ? (
				<div
					data-testid="bitemporal-active-cut-banner"
					style={{
						padding: "10px",
						borderRadius: "6px",
						background: "var(--dv-bg-canvas)",
						border: "1px solid var(--dv-border-subtle)",
						marginBottom: "10px",
						fontSize: "12px",
					}}
				>
					<strong>
						Selected PIT Snapshot ({activePoint.assertionCutoffTs}):
					</strong>{" "}
					<span style={{ fontFamily: "var(--dv-font-mono)" }}>
						{activePoint.portfolioCompany} · EV/EBITDA ={" "}
						{activePoint.evEbitdaMultiple} ({activePoint.enterpriseValueUsd})
					</span>{" "}
					— {activePoint.auditNote}
				</div>
			) : null}

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
						<th style={{ padding: "6px" }}>Satellite Version</th>
						<th style={{ padding: "6px" }}>effective_dts (Valuation As-Of)</th>
						<th style={{ padding: "6px" }}>load_dts (Vault Arrival)</th>
						<th style={{ padding: "6px" }}>PIT Cutoff Tested</th>
						<th style={{ padding: "6px" }}>EV/EBITDA Multiple</th>
						<th style={{ padding: "6px" }}>Enterprise Value (USD)</th>
						<th style={{ padding: "6px" }}>hash_diff</th>
					</tr>
				</thead>
				<tbody>
					{points.map((pt) => (
						<tr
							key={pt.versionLabel}
							style={{ borderBottom: "1px solid var(--dv-border-subtle)" }}
						>
							<td style={{ padding: "6px", fontWeight: 600 }}>
								{pt.versionLabel}
							</td>
							<td style={{ padding: "6px", fontFamily: "var(--dv-font-mono)" }}>
								{pt.effectiveDts}
							</td>
							<td style={{ padding: "6px", fontFamily: "var(--dv-font-mono)" }}>
								{pt.loadDts}
							</td>
							<td style={{ padding: "6px", fontFamily: "var(--dv-font-mono)" }}>
								{pt.assertionCutoffTs}
							</td>
							<td
								style={{
									padding: "6px",
									fontFamily: "var(--dv-font-mono)",
									fontWeight: 700,
									color: "var(--dv-status-pass)",
								}}
							>
								{pt.evEbitdaMultiple}
							</td>
							<td style={{ padding: "6px", fontFamily: "var(--dv-font-mono)" }}>
								{pt.enterpriseValueUsd}
							</td>
							<td style={{ padding: "6px", fontFamily: "var(--dv-font-mono)" }}>
								{pt.hashDiffPrefix}
							</td>
						</tr>
					))}
				</tbody>
			</table>
		</div>
	);
}
