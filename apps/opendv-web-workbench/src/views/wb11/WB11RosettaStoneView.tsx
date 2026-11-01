import type { VaultObservabilityEventDTO } from "@opendv/protocol-ts";
import type React from "react";
import type { ViewPluginProps } from "../../plugins/plugin-types";
import { FourZoneSQLViewer } from "./FourZoneSQLViewer";
import { PreHashStepper } from "./PreHashStepper";

export type WB11RosettaStoneViewProps = Partial<ViewPluginProps> & {
	readonly events?: readonly VaultObservabilityEventDTO[];
};

export function WB11RosettaStoneView({
	currentEvent,
	eventsForView,
	events,
	onInspectProof,
}: WB11RosettaStoneViewProps): React.ReactElement {
	const wb11Event =
		currentEvent ??
		eventsForView?.[0] ??
		events?.find((e) => e.workbench_view_id === "WB-11") ??
		null;

	return (
		<section
			aria-label="WB-11 Pre-Hash Stepper & 4-Zone SQL Rosetta Stone"
			data-testid="wb11-rosetta-stone-view"
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
						WB-11 · VIZ-STEP-07 + VIZ-STEP-08 · STAGE_07_DETERMINISTIC_COMPILER
						· Scene 7
					</div>
					<h2 style={{ margin: "4px 0", fontSize: "18px" }}>
						WB-11: Pre-Hash Stepper (<code>Algorithm A.5</code>) &amp; 4-Zone
						SQL Rosetta Stone
					</h2>
					<p
						style={{
							margin: 0,
							fontSize: "13px",
							color: "var(--dv-text-muted)",
						}}
					>
						{wb11Event?.layer1_story_banner.headline ??
							"6-stage length-prefixed SHA-256 canonicalization and 4-Zone DuckDB DDL/DML/PIT compilation verified."}
					</p>
				</div>
				<div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
					<div
						data-testid="wb11-rosetta-badge"
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
						PASS · ZONES 0–4 COMPILED · DV-AST-001..006
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

			<PreHashStepper />
			<FourZoneSQLViewer />
		</section>
	);
}
