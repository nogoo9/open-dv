import type React from "react";
import type {
	GateCardPluginProps,
	ViewPluginProps,
	WorkbenchGateCardPlugin,
} from "../../plugins/plugin-types";
import {
	AdversarialBatchGrid,
	DEFAULT_DYNAMIC_RULES_001_015,
} from "./AdversarialBatchGrid";
import { BiTemporalTimeline } from "./BiTemporalTimeline";

export type WB10DuckDBSandboxViewProps = Partial<ViewPluginProps>;

function WB10GateCard({
	event,
	isExpanded = true,
	onToggleExpand,
}: GateCardPluginProps): React.ReactElement | null {
	if (!isExpanded) {
		return null;
	}

	return (
		<div
			data-testid="viz-gate-08-card"
			style={{
				padding: "8px 12px",
				borderRadius: "6px",
				border: "1px solid var(--dv-border-subtle)",
				background: "var(--dv-surface-1)",
				fontSize: "12px",
				display: "flex",
				justifyContent: "space-between",
				alignItems: "center",
				gap: "8px",
			}}
		>
			<div>
				<strong>VIZ-GATE-08 (Gate 5 Dynamic DuckDB Sandbox):</strong>{" "}
				<span>
					{event.layer1_story_banner?.headline ??
						"4 Adversarial Batches & DV-DYN-001..015 passed (Δrows = 0 on replay)"}
				</span>
			</div>
			{onToggleExpand ? (
				<button
					type="button"
					onClick={onToggleExpand}
					style={{
						backgroundColor: "var(--dv-surface-2)",
						color: "var(--dv-text-primary)",
						border: "1px solid var(--dv-border-strong)",
						borderRadius: "4px",
						padding: "2px 8px",
						fontSize: "11px",
						cursor: "pointer",
					}}
				>
					Collapse
				</button>
			) : null}
		</div>
	);
}

export const VIZ_GATE_08_PLUGIN: WorkbenchGateCardPlugin = {
	gateId: "VIZ-GATE-08",
	title: "Gate 5 Dynamic DuckDB Shadow Sandbox (Batches 1–4)",
	ruleIds: DEFAULT_DYNAMIC_RULES_001_015.map((r) => r.ruleId),
	component: WB10GateCard,
};

export function WB10DuckDBSandboxView({
	currentEvent,
	eventsForView,
	onInspectProof,
}: WB10DuckDBSandboxViewProps): React.ReactElement {
	const wb10Event = currentEvent ?? eventsForView?.[0] ?? null;

	return (
		<section
			aria-label="WB-10 Gate 5 Dynamic DuckDB Shadow Sandbox & Evolution Matrix"
			data-testid="wb10-duckdb-sandbox-view"
			style={{
				display: "flex",
				flexDirection: "column",
				gap: "16px",
				padding: "16px",
				color: "var(--dv-text-primary)",
				fontFamily: "ui-sans-serif, system-ui, sans-serif",
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
							fontFamily: "ui-monospace, monospace",
							color: "var(--dv-text-muted)",
						}}
					>
						WB-10 · VIZ-GATE-08 · STAGE_06_AXI_DV_DYNAMIC_SANDBOX · Scene 6
					</div>
					<h2 style={{ margin: "4px 0", fontSize: "18px" }}>
						WB-10: Gate 5 Dynamic DuckDB Shadow Sandbox &amp; Bi-Temporal
						Evolution Matrix
					</h2>
					<p
						style={{
							margin: 0,
							fontSize: "13px",
							color: "var(--dv-text-muted)",
						}}
					>
						{wb10Event?.layer1_story_banner.headline ??
							"4-Batch Adversarial Execution passed all 15 dynamic invariants (DV-DYN-001..015) with Δrows = 0 on idempotent replay."}
					</p>
				</div>
				<div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
					<div
						data-testid="wb10-pass-badge"
						style={{
							padding: "6px 12px",
							borderRadius: "999px",
							background: "var(--dv-surface-2)",
							border: "1px solid var(--dv-gate-pass)",
							color: "var(--dv-gate-pass)",
							fontFamily: "ui-monospace, monospace",
							fontSize: "12px",
							fontWeight: 700,
						}}
					>
						PASS · BATCHES 1–4 · DV-DYN-001..015 · Δrows = 0
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
								background: "var(--dv-surface-1)",
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

			<AdversarialBatchGrid />
			<BiTemporalTimeline />
		</section>
	);
}
