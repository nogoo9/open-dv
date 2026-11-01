/**
 * WB-07: Nullable-FK Link Splitter & Gate 4 YAGNI Cost Guard (`WB07LinkSplitterYagniView.tsx`)
 * Primary View Plugin for STAGE_05_AXI_DV_STATIC_GATES (VIZ-GATE-05 Part B, Scene 5) (`docs/06` §4.7, `docs/10` §4).
 */

import type { ViewPluginProps } from "../../plugins/plugin-types";
import { VERDICT_VISUAL_GRAMMAR } from "../../theme/theme-registry";
import { WriteAmplificationGauge } from "./WriteAmplificationGauge";

export function WB07LinkSplitterYagniView({
	currentEvent,
	onInspectProof,
}: ViewPluginProps) {
	const payload = currentEvent?.layer2_visual_payload;
	const pills = payload?.semantic_hash_pills ?? [];
	const summaryMetrics = payload?.summary_metrics ?? {};
	const verdict = currentEvent?.status ?? "AUTO-FIX";
	const verdictMeta =
		VERDICT_VISUAL_GRAMMAR[verdict] ?? VERDICT_VISUAL_GRAMMAR["AUTO-FIX"];

	return (
		<div
			data-testid="wb07-link-splitter-yagni-view"
			style={{
				display: "flex",
				flexDirection: "column",
				gap: "1rem",
				color: "var(--dv-text-primary)",
			}}
		>
			{/* Header Bar */}
			<section
				style={{
					backgroundColor: "var(--dv-surface-1)",
					border: "1px solid var(--dv-border-subtle)",
					borderRadius: "0.5rem",
					padding: "1rem",
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					flexWrap: "wrap",
					gap: "0.75rem",
				}}
			>
				<div>
					<div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
						<span
							style={{
								fontWeight: 700,
								fontSize: "0.8125rem",
								color: `var(${verdictMeta.cssColorVar})`,
							}}
						>
							{verdictMeta.shapeSymbol} {verdictMeta.prefixBadge}
						</span>
						<h2 style={{ margin: 0, fontSize: "1rem", fontWeight: 700 }}>
							WB-07: Nullable-FK Link Splitter &amp; Gate 4 YAGNI Cost Guard
						</h2>
					</div>
					<p
						style={{
							margin: "0.25rem 0 0",
							fontSize: "0.75rem",
							color: "var(--dv-text-secondary)",
						}}
					>
						VIZ-GATE-05 Part B: Algorithm A.4 Sparse FK Split (U_nn = 0.286 &lt;
						0.95) &amp; Algorithm A.1 Write-Amp Guard (W_amp = 4.20 MB/day &lt;
						50 MB/day)
					</p>
				</div>

				<button
					type="button"
					onClick={onInspectProof}
					data-testid="btn-inspect-proof"
					style={{
						backgroundColor: "var(--dv-surface-2)",
						color: "var(--dv-text-primary)",
						border: "1px solid var(--dv-border-strong)",
						borderRadius: "0.375rem",
						padding: "0.4rem 0.75rem",
						fontSize: "0.75rem",
						fontWeight: 600,
						cursor: "pointer",
					}}
				>
					Inspect Layer 3 Formal Proof ↗
				</button>
			</section>

			{/* Summary Metrics Row */}
			<section
				data-testid="wb07-summary-metrics"
				style={{
					display: "grid",
					gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
					gap: "0.5rem",
				}}
			>
				{[
					{
						label: "Sparse FK Non-Null Ratio",
						value: `U_nn = ${Number(summaryMetrics.sparse_fk_non_null_ratio ?? 0.286).toFixed(3)} (< 0.95)`,
						color: "var(--dv-lnk-accent)",
					},
					{
						label: "Zero-Key Pollution Prevented",
						value: "71.4% (5 / 7 rows)",
						color: "var(--dv-gate-pass)",
					},
					{
						label: "Write Amplification (W_amp)",
						value: `${Number(summaryMetrics.write_amplification_mb_per_day ?? 4.2).toFixed(2)} MB/day`,
						color: "var(--dv-gate-autofix)",
					},
					{
						label: "YAGNI Split Threshold",
						value: `${Number(summaryMetrics.write_amplification_threshold_mb_per_day ?? 50.0).toFixed(1)} MB/day`,
						color: "var(--dv-sat-accent)",
					},
					{
						label: "PIT Joins Saved",
						value: `${summaryMetrics.pit_joins_saved ?? 1} Join Eliminated`,
						color: "var(--dv-gate-pass)",
					},
				].map((m) => (
					<div
						key={m.label}
						style={{
							backgroundColor: "var(--dv-surface-1)",
							border: "1px solid var(--dv-border-subtle)",
							borderRadius: "0.375rem",
							padding: "0.6rem 0.75rem",
						}}
					>
						<div
							style={{
								fontSize: "0.6875rem",
								color: "var(--dv-text-secondary)",
							}}
						>
							{m.label}
						</div>
						<div
							style={{
								fontSize: "0.95rem",
								fontWeight: 700,
								marginTop: "0.25rem",
								fontFamily: "ui-monospace, monospace",
								color: m.color,
							}}
						>
							{m.value}
						</div>
					</div>
				))}
			</section>

			{/* 2-Column Split: Left = Algorithm A.4 Link Splitter, Right = WriteAmplificationGauge */}
			<div
				style={{
					display: "grid",
					gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))",
					gap: "1rem",
				}}
			>
				{/* Left Pane: Algorithm A.4 Nullable-FK Link Splitter */}
				<section
					aria-label="Algorithm A.4 Nullable-FK Link Splitter"
					data-testid="nullable-fk-link-splitter-pane"
					style={{
						backgroundColor: "var(--dv-surface-1)",
						border: "1.5px solid var(--dv-lnk-accent)",
						borderRadius: "0.5rem",
						padding: "1rem",
						display: "flex",
						flexDirection: "column",
						gap: "0.875rem",
					}}
				>
					<div
						style={{
							borderBottom: "1px solid var(--dv-border-subtle)",
							paddingBottom: "0.5rem",
						}}
					>
						<div
							style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}
						>
							<span
								style={{
									color: "var(--dv-lnk-accent)",
									fontWeight: 700,
									fontSize: "0.75rem",
								}}
							>
								● [ALG-A4 LINK SPLITTER]
							</span>
							<h3 style={{ margin: 0, fontSize: "0.875rem", fontWeight: 700 }}>
								Part 1: Nullable Foreign Key Link Splitter (Algorithm A.4)
							</h3>
						</div>
						<div
							style={{ fontSize: "0.7rem", color: "var(--dv-text-secondary)" }}
						>
							Source:{" "}
							<code>
								investran.src_lp_commitments_and_calls.co_invest_portco_lei
							</code>{" "}
							(U_nn = 2/7 = 0.286 &lt; 0.95)
						</div>
					</div>

					{/* Before Split: Anti-Pattern */}
					<div
						data-testid="link-split-before-card"
						style={{
							backgroundColor: "var(--dv-surface-2)",
							border: "1px solid var(--dv-gate-reject)",
							borderRadius: "0.375rem",
							padding: "0.75rem",
						}}
					>
						<div
							style={{
								fontWeight: 700,
								fontSize: "0.75rem",
								color: "var(--dv-gate-reject)",
								marginBottom: "0.25rem",
							}}
						>
							■ Before (Naive 3-Way Driving Link — 71.4% Zero-Key Pollution)
						</div>
						<code
							style={{
								display: "block",
								fontSize: "0.7rem",
								color: "var(--dv-text-primary)",
								marginBottom: "0.3rem",
							}}
						>
							lnk_lp_commitment(hk_fund, hk_limited_partner,
							hk_portfolio_company [-1 on 5/7 rows])
						</code>
						<div
							style={{ fontSize: "0.7rem", color: "var(--dv-text-secondary)" }}
						>
							Embedding optional <code>co_invest_portco_lei</code> (NULL on
							71.43% of standard fund commitments) inside the core commitment
							Link forces 5 of 7 rows to bind to the <code>0000..0000</code>{" "}
							Zero-Key sentinel, violating Link Unit-of-Work grain invariance (
							<code>INV-04</code>).
						</div>
					</div>

					{/* After Split: Deterministic Decomposition */}
					<div
						data-testid="link-split-after-card"
						style={{
							backgroundColor: "var(--dv-surface-2)",
							border: "1.5px solid var(--dv-gate-pass)",
							borderRadius: "0.375rem",
							padding: "0.75rem",
							display: "flex",
							flexDirection: "column",
							gap: "0.5rem",
						}}
					>
						<div
							style={{
								fontWeight: 700,
								fontSize: "0.75rem",
								color: "var(--dv-gate-pass)",
							}}
						>
							● After Algorithm A.4 Deterministic Decomposition (2 Links)
						</div>

						<div
							style={{
								backgroundColor: "var(--dv-surface-1)",
								border: "1px solid var(--dv-border-strong)",
								borderRadius: "0.25rem",
								padding: "0.5rem",
								fontSize: "0.7rem",
							}}
						>
							<strong>1. Core Driving Relationship:</strong>{" "}
							<code>lnk_lp_commitment</code>
							<div
								style={{
									fontFamily: "ui-monospace, monospace",
									color: "var(--dv-text-secondary)",
									marginTop: "0.15rem",
								}}
							>
								Hubs: (hub_fund, hub_limited_partner) + Degenerate:
								commitment_id [7/7 rows, 100% Non-Null]
							</div>
						</div>

						<div
							style={{
								backgroundColor: "var(--dv-surface-1)",
								border: "1px solid var(--dv-lnk-accent)",
								borderRadius: "0.25rem",
								padding: "0.5rem",
								fontSize: "0.7rem",
							}}
						>
							<strong>2. Split Contextual Co-Invest Link:</strong>{" "}
							<code>lnk_lp_commitment_co_invest</code>
							<div
								style={{
									fontFamily: "ui-monospace, monospace",
									color: "var(--dv-text-secondary)",
									marginTop: "0.15rem",
								}}
							>
								Hubs: (hub_fund, hub_limited_partner, hub_portfolio_company) +
								commitment_id
							</div>
							<div
								style={{
									fontFamily: "ui-monospace, monospace",
									color: "var(--dv-lnk-accent)",
									marginTop: "0.15rem",
									fontWeight: 700,
								}}
							>
								WHERE co_invest_portco_lei IS NOT NULL [2/7 rows, 0% Zero-Key
								Pollution]
							</div>
						</div>
					</div>
				</section>

				{/* Right Pane: Gate 4 YAGNI Write-Amplification Gauge */}
				<WriteAmplificationGauge
					event={currentEvent ?? undefined}
					isExpanded={true}
				/>
			</div>

			{/* Semantic Hash Pills */}
			{pills.length > 0 && (
				<section
					style={{
						backgroundColor: "var(--dv-surface-1)",
						border: "1px solid var(--dv-border-subtle)",
						borderRadius: "0.5rem",
						padding: "0.75rem 1rem",
						display: "flex",
						flexWrap: "wrap",
						gap: "0.5rem",
						alignItems: "center",
					}}
				>
					<span
						style={{
							fontSize: "0.75rem",
							fontWeight: 600,
							color: "var(--dv-text-secondary)",
						}}
					>
						Affected Entity Hash Pills:
					</span>
					{pills.map((pill) => (
						<button
							key={`${pill.full_sha256_hex}-${pill.business_key}`}
							type="button"
							onClick={onInspectProof}
							data-testid="semantic-hash-pill"
							style={{
								backgroundColor: "var(--dv-surface-2)",
								color: "var(--dv-text-primary)",
								border: "1px solid var(--dv-hub-accent)",
								borderRadius: "9999px",
								padding: "0.2rem 0.6rem",
								fontFamily: "ui-monospace, monospace",
								fontSize: "0.75rem",
								cursor: "pointer",
							}}
						>
							{pill.pill_label}
						</button>
					))}
				</section>
			)}
		</div>
	);
}
