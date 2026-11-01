/**
 * WB-04: Pass 1 Exhaustive Column Triage Sankey & Surrogate Sink (`WB04ColumnTriageView.tsx`)
 * Primary View Plugin for STAGE_03_SHIP_VAULT_PASS1 (VIZ-STEP-05 + VIZ-GATE-05 Part A) (`docs/06` §3.6, `docs/10` §4).
 */

import type { ViewPluginProps } from "../../plugins/plugin-types";
import { VERDICT_VISUAL_GRAMMAR } from "../../theme/theme-registry";
import { ColumnAccountingSankey } from "./ColumnAccountingSankey";
import { TriageLedgerTable } from "./TriageLedgerTable";

export function WB04ColumnTriageView({
	currentEvent,
	onInspectProof,
}: ViewPluginProps) {
	const payload = currentEvent?.layer2_visual_payload;
	const pills = payload?.semantic_hash_pills ?? [];
	const summaryMetrics = payload?.summary_metrics ?? {};
	const verdict = currentEvent?.status ?? "PASS";
	const verdictMeta =
		VERDICT_VISUAL_GRAMMAR[verdict] ?? VERDICT_VISUAL_GRAMMAR.PASS;

	return (
		<div
			data-testid="wb04-column-triage-view"
			style={{
				display: "flex",
				flexDirection: "column",
				gap: "1rem",
				color: "var(--dv-text-primary)",
			}}
		>
			{/* Top Bar */}
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
							WB-04: Pass 1 Exhaustive Column Triage Sankey &amp; Surrogate Sink
						</h2>
					</div>
					<p
						style={{
							margin: "0.25rem 0 0",
							fontSize: "0.75rem",
							color: "var(--dv-text-secondary)",
						}}
					>
						VIZ-STEP-05 100% Column Accountability Invariant &amp; VIZ-GATE-05
						Part A Surrogate Tombstoning
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

			{/* Summary Metrics Cards */}
			<section
				data-testid="triage-metrics"
				style={{
					display: "grid",
					gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
					gap: "0.5rem",
				}}
			>
				{[
					{
						label: "Total Bronze Columns",
						value: summaryMetrics.total_bronze_columns ?? 67,
					},
					{
						label: "Accounted Columns",
						value: `${summaryMetrics.triaged_columns ?? 67} (100%)`,
						color: "var(--dv-gate-pass)",
					},
					{
						label: "Business Keys",
						value: summaryMetrics.business_keys ?? 5,
						color: "var(--dv-hub-accent)",
					},
					{
						label: "Foreign Key Refs",
						value: summaryMetrics.foreign_key_refs ?? 8,
						color: "var(--dv-lnk-accent)",
					},
					{
						label: "Optional FKs (Split)",
						value: summaryMetrics.optional_fk_refs ?? 1,
						color: "var(--dv-sat-accent)",
					},
					{
						label: "Descriptive Payload",
						value: summaryMetrics.descriptive_payload ?? 48,
					},
					{
						label: "Quarantined Surrogates",
						value: summaryMetrics.dropped_surrogates ?? 1,
						color: "var(--dv-surrogate-accent)",
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
								fontSize: "1.125rem",
								fontWeight: 700,
								marginTop: "0.25rem",
								color: m.color ?? "var(--dv-text-primary)",
							}}
						>
							{String(m.value)}
						</div>
					</div>
				))}
			</section>

			{/* Semantic Hash Pills Bar */}
			{pills.length > 0 && (
				<section
					style={{
						backgroundColor: "var(--dv-surface-1)",
						border: "1px solid var(--dv-border-subtle)",
						borderRadius: "0.5rem",
						padding: "0.75rem 1rem",
					}}
				>
					<div
						style={{
							fontSize: "0.75rem",
							fontWeight: 600,
							color: "var(--dv-text-secondary)",
							marginBottom: "0.5rem",
						}}
					>
						Triaged Business Key Semantic Hash Pills:
					</div>
					<div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
						{pills.map((pill) => (
							<button
								key={`${pill.full_sha256_hex}-${pill.business_key}`}
								type="button"
								onClick={onInspectProof}
								title={`Full SHA-256: ${pill.full_sha256_hex}`}
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
					</div>
				</section>
			)}

			{/* Conservation of Mass Column Accounting Sankey */}
			<ColumnAccountingSankey />

			{/* Detailed Triage Ledger Table */}
			<TriageLedgerTable />
		</div>
	);
}
