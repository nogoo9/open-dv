import type { VaultObservabilityEventDTO } from "@opendv/protocol-ts";
import type React from "react";
import type { ViewPluginProps } from "../../plugins/plugin-types";
import { DriftDeltaGrid } from "./DriftDeltaGrid";

export type WB09SchemaDriftRadarProps = Partial<ViewPluginProps> & {
	readonly events?: readonly VaultObservabilityEventDTO[];
};

interface RadarCategoryCard {
	readonly category: string;
	readonly count: number;
	readonly ruleSummary: string;
	readonly status: "AUTO-RESOLVED" | "ZERO-DRIFT";
}

const RADAR_CATEGORIES: readonly RadarCategoryCard[] = [
	{
		category: "1. Additive Column",
		count: 1,
		ruleSummary:
			"ilpa_esg_rating appended to sat_lp_commitment_terms without backfilling historical hash_diff.",
		status: "AUTO-RESOLVED",
	},
	{
		category: "2. Soft-Deprecated Column",
		count: 1,
		ruleSummary:
			"deprecated_fax_no projected as NULL in Zone 0 staging view; historical rows preserved.",
		status: "AUTO-RESOLVED",
	},
	{
		category: "3. Renamed Column (sim_Lev >= 0.85)",
		count: 1,
		ruleSummary:
			"committed_amt -> commitment_amount_usd (sim_Lev = 0.87, J_val = 0.994) aliased in Zone 0.",
		status: "AUTO-RESOLVED",
	},
	{
		category: "4. Type Widening",
		count: 1,
		ruleSummary:
			"company_name widened VARCHAR(128) -> VARCHAR(255); canonical Algorithm A.5 digest unchanged.",
		status: "AUTO-RESOLVED",
	},
	{
		category: "5. Key Drift Guard",
		count: 0,
		ruleSummary:
			"Zero primary/business key mutations across S_1(t) -> S_1(t+1); no Tier-U3 hold triggered.",
		status: "ZERO-DRIFT",
	},
];

export function WB09SchemaDriftRadar({
	currentEvent,
	eventsForView,
	events,
	onInspectProof,
}: WB09SchemaDriftRadarProps): React.ReactElement {
	const wb09Event =
		currentEvent ??
		eventsForView?.[0] ??
		events?.find((e) => e.workbench_view_id === "WB-09") ??
		null;

	return (
		<section
			aria-label="WB-09 Intra-Source Schema Drift Radar"
			data-testid="wb09-schema-drift-radar-view"
			style={{
				display: "flex",
				flexDirection: "column",
				gap: "16px",
				padding: "16px",
				color: "var(--dv-text-primary)",
				fontFamily: "var(--dv-font-sans)",
			}}
		>
			<header
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "flex-start",
					flexWrap: "wrap",
					gap: "12px",
					borderBottom: "1px solid var(--dv-border-subtle)",
					paddingBottom: "12px",
				}}
			>
				<div>
					<div
						style={{
							fontSize: "12px",
							fontFamily: "var(--dv-font-mono)",
							color: "var(--dv-text-muted)",
						}}
					>
						WB-09 · VIZ-STEP-03 · STAGE_01_SCOUT_SOURCE · Scene 2
					</div>
					<h2 style={{ margin: "4px 0", fontSize: "18px" }}>
						WB-09: Intra-Source Schema Drift Radar (
						<code>S_1(t) → S_1(t+1)</code> · Algorithm A.9a)
					</h2>
					<p
						style={{
							margin: 0,
							fontSize: "13px",
							color: "var(--dv-text-muted)",
						}}
					>
						{wb09Event?.layer1_story_banner.headline ??
							"Day-3 schema drift classified across 5 Algorithm A.9a categories; 0 historical hash_diff digests invalidated."}
					</p>
				</div>
				<div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
					<div
						data-testid="wb09-zero-hashdiff-breakage-badge"
						style={{
							padding: "6px 12px",
							borderRadius: "999px",
							background: "var(--dv-bg-elevated)",
							border: "1px solid var(--dv-status-pass)",
							color: "var(--dv-status-pass)",
							fontFamily: "var(--dv-font-mono)",
							fontSize: "12px",
							fontWeight: 700,
						}}
					>
						PASS · 4 DRIFTS AUTO-RESOLVED · 0 BROKEN HASH_DIFFS
					</div>
					{onInspectProof ? (
						<button
							type="button"
							onClick={onInspectProof}
							data-testid="btn-inspect-proof"
							style={{
								padding: "6px 10px",
								borderRadius: "6px",
								border: "1px solid var(--dv-border-subtle)",
								background: "var(--dv-bg-surface)",
								color: "var(--dv-text-primary)",
								fontSize: "12px",
								cursor: "pointer",
							}}
						>
							Inspect Layer 3 Formal Proof ↗
						</button>
					) : null}
				</div>
			</header>

			<div
				data-testid="wb09-radar-spokes"
				style={{
					display: "grid",
					gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))",
					gap: "12px",
				}}
			>
				{RADAR_CATEGORIES.map((spoke) => (
					<div
						key={spoke.category}
						style={{
							padding: "12px",
							borderRadius: "8px",
							border: "1px solid var(--dv-border-subtle)",
							background: "var(--dv-bg-surface)",
						}}
					>
						<div
							style={{
								display: "flex",
								justifyContent: "space-between",
								alignItems: "center",
								marginBottom: "6px",
							}}
						>
							<span style={{ fontWeight: 700, fontSize: "12px" }}>
								{spoke.category}
							</span>
							<span
								style={{
									fontFamily: "var(--dv-font-mono)",
									fontSize: "11px",
									color: "var(--dv-status-pass)",
									fontWeight: 700,
								}}
							>
								{spoke.count} ({spoke.status})
							</span>
						</div>
						<div style={{ fontSize: "12px", color: "var(--dv-text-muted)" }}>
							{spoke.ruleSummary}
						</div>
					</div>
				))}
			</div>

			<DriftDeltaGrid />
		</section>
	);
}
