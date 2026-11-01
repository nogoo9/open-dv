/**
 * WB-01: Dual-Graph Grounding & Anti-Pattern Counterfactual Inspector (`WB01DualGraphView.tsx`)
 * Primary View Plugin for STAGE_00_ORCHESTRATION (VIZ-STEP-01 + VIZ-GATE-02) (`docs/06` §3.2, `docs/10` §4).
 */

import { useState } from "react";
import type { ViewPluginProps } from "../../plugins/plugin-types";
import { VERDICT_VISUAL_GRAMMAR } from "../../theme/theme-registry";
import { BipartiteMapCanvas } from "./BipartiteMapCanvas";
import { CounterfactualTrapDrawer } from "./CounterfactualTrapDrawer";

export function WB01DualGraphView({
	currentEvent,
	onInspectProof,
}: ViewPluginProps) {
	const [isTrapDrawerOpen, setIsTrapDrawerOpen] = useState<boolean>(true);
	const payload = currentEvent?.layer2_visual_payload;
	const pills = payload?.semantic_hash_pills ?? [];
	const verdict = currentEvent?.status ?? "PASS";
	const verdictMeta =
		VERDICT_VISUAL_GRAMMAR[verdict] ?? VERDICT_VISUAL_GRAMMAR.PASS;

	return (
		<div
			data-testid="wb01-dual-graph-view"
			style={{
				display: "flex",
				flexDirection: "column",
				gap: "1rem",
				color: "var(--dv-text-primary)",
			}}
		>
			{/* Top Overview Bar */}
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
							WB-01: Dual-Graph Grounding &amp; Anti-Pattern Counterfactual
							Inspector
						</h2>
					</div>
					<p
						style={{
							margin: "0.25rem 0 0",
							fontSize: "0.75rem",
							color: "var(--dv-text-secondary)",
						}}
					>
						VIZ-STEP-01 Bipartite Concept Grounding (Graph A ↔ Graph B) &amp;
						VIZ-GATE-02 5 Fatal Traps Simulator
					</p>
				</div>

				<div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
					<button
						type="button"
						onClick={() => setIsTrapDrawerOpen((prev) => !prev)}
						data-testid="btn-toggle-trap-drawer"
						style={{
							backgroundColor: "var(--dv-surface-2)",
							color: "var(--dv-text-primary)",
							border: "1px solid var(--dv-gate-autofix)",
							borderRadius: "0.375rem",
							padding: "0.4rem 0.75rem",
							fontSize: "0.75rem",
							fontWeight: 600,
							cursor: "pointer",
						}}
					>
						{isTrapDrawerOpen ? "Hide 5 Fatal Traps" : "Simulate 5 Fatal Traps"}
					</button>

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
				</div>
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
						Grounded Semantic Hash Pills:
					</div>
					<div
						style={{
							display: "flex",
							flexWrap: "wrap",
							gap: "0.5rem",
						}}
					>
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

			{/* Bipartite Concept Grounding Canvas */}
			<BipartiteMapCanvas />

			{/* Anti-Pattern Counterfactual Simulator Drawer */}
			<CounterfactualTrapDrawer
				isOpen={isTrapDrawerOpen}
				onClose={() => setIsTrapDrawerOpen(false)}
			/>
		</div>
	);
}
