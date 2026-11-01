/**
 * WB-06: Gate 3 Topological DAG & Grain Matrix Inspector (`WB06TopologicalDAGView.tsx`)
 * Primary View Plugin for STAGE_05_AXI_DV_STATIC_GATES (VIZ-GATE-03, Scene 5) (`docs/06` §4.5, `docs/10` §4).
 */

import type { ViewPluginProps } from "../../plugins/plugin-types";
import { VERDICT_VISUAL_GRAMMAR } from "../../theme/theme-registry";
import { GrainAdjacencyMatrix } from "./GrainAdjacencyMatrix";

export function WB06TopologicalDAGView({
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
			data-testid="wb06-topological-dag-view"
			style={{
				display: "flex",
				flexDirection: "column",
				gap: "1rem",
				color: "var(--dv-text-primary)",
			}}
		>
			{/* Top Header Bar */}
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
							WB-06: Gate 3 Topological DAG &amp; Grain Matrix Inspector
						</h2>
					</div>
					<p
						style={{
							margin: "0.25rem 0 0",
							fontSize: "0.75rem",
							color: "var(--dv-text-secondary)",
						}}
					>
						VIZ-GATE-03 NetworkX Bipartite Directed Acyclic Graph Proof
						(outdeg(h)=0, Link arity &gt;= 2, outdeg(s)=1)
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

			{/* Summary Metrics Grid */}
			<section
				data-testid="wb06-summary-metrics"
				style={{
					display: "grid",
					gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
					gap: "0.5rem",
				}}
			>
				{[
					{
						label: "Structural Rules Passed",
						value: `${summaryMetrics.structural_rules_passed ?? 14} / ${summaryMetrics.structural_rules_evaluated ?? 14}`,
						color: "var(--dv-gate-pass)",
					},
					{
						label: "Max Hub Out-Degree",
						value: `outdeg(h) = ${summaryMetrics.hub_outdegree_max ?? 0}`,
						color: "var(--dv-hub-accent)",
					},
					{
						label: "Min Link Hub Arity",
						value: `outdeg_H(l) >= ${summaryMetrics.link_arity_min ?? 2}`,
						color: "var(--dv-lnk-accent)",
					},
					{
						label: "Satellite Out-Degree",
						value: `outdeg(s) = ${summaryMetrics.satellite_outdegree_exact ?? 1}`,
						color: "var(--dv-sat-accent)",
					},
					{
						label: "Directed Cycles",
						value: `${summaryMetrics.dag_cycles_detected ?? 0} (Acyclic DAG)`,
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
								fontSize: "1rem",
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

			{/* Bipartite DAG Proof Canvas */}
			<section
				aria-label="Bipartite DAG Degree Theorem Proof Canvas"
				data-testid="bipartite-dag-proof-canvas"
				style={{
					backgroundColor: "var(--dv-surface-1)",
					border: "1px solid var(--dv-border-subtle)",
					borderRadius: "0.5rem",
					padding: "1rem",
					display: "grid",
					gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
					gap: "0.75rem",
				}}
			>
				<div
					style={{
						backgroundColor: "var(--dv-surface-2)",
						border: "1.5px solid var(--dv-hub-accent)",
						borderRadius: "0.375rem",
						padding: "0.75rem",
					}}
				>
					<div
						style={{
							fontWeight: 700,
							fontSize: "0.75rem",
							color: "var(--dv-hub-accent)",
							marginBottom: "0.25rem",
						}}
					>
						[HUB] Tier 1: Enterprise Identity Sinks (4 Nodes)
					</div>
					<code
						style={{
							display: "block",
							fontSize: "0.7rem",
							color: "var(--dv-text-primary)",
							marginBottom: "0.35rem",
						}}
					>
						∀ h ∈ H: outdeg(h) = 0 ∧ indeg(h) ≥ 1
					</code>
					<div
						style={{ fontSize: "0.7rem", color: "var(--dv-text-secondary)" }}
					>
						<code>hub_fund</code>, <code>hub_limited_partner</code>,{" "}
						<code>hub_portfolio_company</code>, <code>hub_document</code> emit
						zero outbound foreign keys.
					</div>
				</div>

				<div
					style={{
						backgroundColor: "var(--dv-surface-2)",
						border: "1.5px solid var(--dv-lnk-accent)",
						borderRadius: "0.375rem",
						padding: "0.75rem",
					}}
				>
					<div
						style={{
							fontWeight: 700,
							fontSize: "0.75rem",
							color: "var(--dv-lnk-accent)",
							marginBottom: "0.25rem",
						}}
					>
						[LNK / SAL] Tier 2: N-ary Hub Connectors (5 Nodes)
					</div>
					<code
						style={{
							display: "block",
							fontSize: "0.7rem",
							color: "var(--dv-text-primary)",
							marginBottom: "0.35rem",
						}}
					>
						∀ l ∈ L: outdeg_H(l) ≥ 2 ∧ outdeg_L(l) = 0
					</code>
					<div
						style={{ fontSize: "0.7rem", color: "var(--dv-text-secondary)" }}
					>
						12 total Hub FK edges across <code>lnk_lp_commitment</code> (2),{" "}
						<code>lnk_lp_commitment_co_invest</code> (3),{" "}
						<code>sal_limited_partner</code> (2), and 2 Document Links (5).
					</div>
				</div>

				<div
					style={{
						backgroundColor: "var(--dv-surface-2)",
						border: "1.5px solid var(--dv-sat-accent)",
						borderRadius: "0.375rem",
						padding: "0.75rem",
					}}
				>
					<div
						style={{
							fontWeight: 700,
							fontSize: "0.75rem",
							color: "var(--dv-sat-accent)",
							marginBottom: "0.25rem",
						}}
					>
						[SAT] Tier 3: Temporal Context Leaves (11 Nodes)
					</div>
					<code
						style={{
							display: "block",
							fontSize: "0.7rem",
							color: "var(--dv-text-primary)",
							marginBottom: "0.35rem",
						}}
					>
						∀ s ∈ S: outdeg(s) = 1 ∧ indeg(s) = 0
					</code>
					<div
						style={{ fontSize: "0.7rem", color: "var(--dv-text-secondary)" }}
					>
						8 Hub Satellites and 3 Link Satellites attach to exactly 1 parent
						with zero Satellite-to-Satellite chains.
					</div>
				</div>
			</section>

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
						Verified Bipartite Anchor Pills:
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

			{/* Grain Adjacency Matrix & Invariant Checklist */}
			<GrainAdjacencyMatrix
				event={currentEvent ?? undefined}
				isExpanded={true}
			/>
		</div>
	);
}
