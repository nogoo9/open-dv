/**
 * WB-05: Pass 2/3 ELM Topology & 3-Tier BKCC Collision Shield (`WB05ELMTopologyView.tsx`)
 * Primary View Plugin for STAGE_04_SHIP_VAULT_PASS2 (VIZ-STEP-06 + VIZ-GATE-04) (`docs/06` §3.7, `docs/10` §4).
 */

import { useState } from "react";
import type { ViewPluginProps } from "../../plugins/plugin-types";
import { VERDICT_VISUAL_GRAMMAR } from "../../theme/theme-registry";
import { BkccCollisionShieldDrawer } from "./BkccCollisionShieldDrawer";
import { TopologyCanvas } from "./TopologyCanvas";

export function WB05ELMTopologyView({
	currentEvent,
	onInspectProof,
}: ViewPluginProps) {
	const [isShieldOpen, setIsShieldOpen] = useState<boolean>(true);

	const payload = currentEvent?.layer2_visual_payload;
	const pills = payload?.semantic_hash_pills ?? [];
	const summaryMetrics = payload?.summary_metrics ?? {};
	const verdict = currentEvent?.status ?? "PASS";
	const verdictMeta =
		VERDICT_VISUAL_GRAMMAR[verdict] ?? VERDICT_VISUAL_GRAMMAR.PASS;

	return (
		<div
			data-testid="wb05-elm-topology-view"
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
							WB-05: Pass 2/3 ELM Topology &amp; 3-Tier BKCC Collision Shield
						</h2>
					</div>
					<p
						style={{
							margin: "0.25rem 0 0",
							fontSize: "0.75rem",
							color: "var(--dv-text-secondary)",
						}}
					>
						VIZ-STEP-06 Synthesized DV 2.0 Topology Graph &amp; VIZ-GATE-04
						CalPERS vs. Bavarian Pension BKCC Shield
					</p>
				</div>

				<div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
					<button
						type="button"
						onClick={() => setIsShieldOpen((prev) => !prev)}
						data-testid="btn-toggle-shield"
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
						{isShieldOpen ? "Hide BKCC Shield" : "Inspect BKCC Shield"}
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

			{/* Summary Metrics Cards */}
			<section
				data-testid="topology-metrics"
				style={{
					display: "grid",
					gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
					gap: "0.5rem",
				}}
			>
				{[
					{
						label: "Enterprise Hubs",
						value: summaryMetrics.hubs_count ?? 4,
						color: "var(--dv-hub-accent)",
					},
					{
						label: "Enterprise Links",
						value: summaryMetrics.links_count ?? 5,
						color: "var(--dv-lnk-accent)",
					},
					{
						label: "Satellites",
						value: summaryMetrics.satellites_count ?? 11,
						color: "var(--dv-sat-accent)",
					},
					{
						label: "PII Satellites Isolated",
						value: summaryMetrics.pii_satellites_isolated ?? 1,
						color: "var(--dv-sat-pii-accent)",
					},
					{
						label: "MNPI Satellites Isolated",
						value: summaryMetrics.mnpi_satellites_isolated ?? 1,
						color: "var(--dv-sat-pii-accent)",
					},
					{
						label: "Key Collisions Prevented",
						value: summaryMetrics.cross_source_collisions_prevented ?? 2,
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
						Collision-Shielded Semantic Hash Pills (CalPERS vs. Bavarian
						Pension):
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

			{/* Topology Viewport */}
			<TopologyCanvas />

			{/* BKCC Collision Shield Drawer */}
			<BkccCollisionShieldDrawer
				isOpen={isShieldOpen}
				onClose={() => setIsShieldOpen(false)}
			/>
		</div>
	);
}
