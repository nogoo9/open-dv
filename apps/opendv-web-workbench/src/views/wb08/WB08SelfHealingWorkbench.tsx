import type { VaultObservabilityEventDTO } from "@opendv/protocol-ts";
import type React from "react";
import type {
	GateCardPluginProps,
	ViewPluginProps,
	WorkbenchGateCardPlugin,
} from "../../plugins/plugin-types";
import { AttemptDiffViewer } from "./AttemptDiffViewer";
import { ToonTraceHighlighter } from "./ToonTraceHighlighter";

export type WB08SelfHealingWorkbenchProps = Partial<ViewPluginProps> & {
	readonly events?: readonly VaultObservabilityEventDTO[];
};

function WB08GateCard({ event }: GateCardPluginProps): React.ReactElement {
	return (
		<div
			data-testid="viz-gate-07-card"
			style={{
				padding: "8px 12px",
				borderRadius: "6px",
				border: "1px solid var(--dv-border-subtle)",
				background: "var(--dv-bg-surface)",
				fontSize: "12px",
			}}
		>
			<strong>VIZ-GATE-07 (Closed-Loop Self-Healing):</strong>{" "}
			<span>
				{event.layer1_story_banner?.headline ??
					"Converged in k = 2 attempts via @toon_v1 trace"}
			</span>
		</div>
	);
}

export const VIZ_GATE_07_PLUGIN: WorkbenchGateCardPlugin = {
	gateId: "VIZ-GATE-07",
	title: "Gate 1–4 Closed-Loop Self-Healing & TOON Trace",
	ruleIds: ["DV-COL-012", "ALG-A1"],
	component: WB08GateCard,
};

export function WB08SelfHealingWorkbench({
	currentEvent,
	eventsForView,
	events,
	onInspectProof,
}: WB08SelfHealingWorkbenchProps): React.ReactElement {
	const wb08Event =
		currentEvent ??
		eventsForView?.[0] ??
		events?.find((e) => e.workbench_view_id === "WB-08") ??
		null;

	return (
		<section
			aria-label="WB-08 Closed-Loop Self-Healing Diff & TOON Diagnostic Workbench"
			data-testid="wb08-self-healing-view"
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
						WB-08 · VIZ-GATE-07 · STAGE_05_AXI_DV_STATIC_GATES · Scene 5
					</div>
					<h2 style={{ margin: "4px 0", fontSize: "18px" }}>
						WB-08: Closed-Loop Self-Healing Diff &amp; <code>@toon_v1</code>{" "}
						Diagnostic Workbench
					</h2>
					<p
						style={{
							margin: 0,
							fontSize: "13px",
							color: "var(--dv-text-muted)",
						}}
					>
						{wb08Event?.layer1_story_banner.headline ??
							"Attempt 1 rejected by DV-COL-012 and auto-fixed by ALG-A1; converged on Attempt 2 via @toon_v1."}
					</p>
				</div>
				<div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
					<div
						data-testid="wb08-convergence-badge"
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
						CONVERGED · k = 1 → k = 2 (MAX_RETRIES = 4)
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
				data-testid="wb08-state-machine"
				style={{
					display: "grid",
					gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
					gap: "12px",
				}}
			>
				<div
					style={{
						padding: "12px",
						borderRadius: "8px",
						border: "1px solid var(--dv-status-fail)",
						background: "var(--dv-bg-surface)",
					}}
				>
					<div
						style={{
							fontSize: "11px",
							fontFamily: "var(--dv-font-mono)",
							color: "var(--dv-status-fail)",
							fontWeight: 700,
						}}
					>
						ATTEMPT k = 1 · REJECTED
					</div>
					<div style={{ fontSize: "13px", fontWeight: 600, marginTop: "4px" }}>
						Gate 1 <code>DV-COL-012</code> + Gate 4 <code>ALG-A1</code>
					</div>
					<div
						style={{
							fontSize: "12px",
							color: "var(--dv-text-muted)",
							marginTop: "4px",
						}}
					>
						Unqualified <code>hub_limited_partner</code> across N=2 sources +
						speculative <code>sat_lp_commitment_fx_high_freq</code>.
					</div>
				</div>

				<div
					style={{
						padding: "12px",
						borderRadius: "8px",
						border: "1px solid var(--dv-status-warn)",
						background: "var(--dv-bg-surface)",
					}}
				>
					<div
						style={{
							fontSize: "11px",
							fontFamily: "var(--dv-font-mono)",
							color: "var(--dv-status-warn)",
							fontWeight: 700,
						}}
					>
						@toon_v1 FEEDBACK INJECTION
					</div>
					<div style={{ fontSize: "13px", fontWeight: 600, marginTop: "4px" }}>
						48-Token Deterministic Repair Prescription
					</div>
					<div
						style={{
							fontSize: "12px",
							color: "var(--dv-text-muted)",
							marginTop: "4px",
						}}
					>
						Injects exact <code>set_require_src_qualifier=true</code> and{" "}
						<code>merge_into=sat_lp_commitment_terms</code> actions.
					</div>
				</div>

				<div
					style={{
						padding: "12px",
						borderRadius: "8px",
						border: "1px solid var(--dv-status-pass)",
						background: "var(--dv-bg-surface)",
					}}
				>
					<div
						style={{
							fontSize: "11px",
							fontFamily: "var(--dv-font-mono)",
							color: "var(--dv-status-pass)",
							fontWeight: 700,
						}}
					>
						ATTEMPT k = 2 · PASS (6/6 GATES)
					</div>
					<div style={{ fontSize: "13px", fontWeight: 600, marginTop: "4px" }}>
						Zero Unresolved Violations
					</div>
					<div
						style={{
							fontSize: "12px",
							color: "var(--dv-text-muted)",
							marginTop: "4px",
						}}
					>
						Disjoint hash keys verified (<code>5D15..0AE4</code> vs{" "}
						<code>6B7C..D8B9</code>) and satellite count reduced to 5.
					</div>
				</div>
			</div>

			<div
				data-testid="wb08-counterfactual-proof-card"
				style={{
					border: "1px solid var(--dv-border-subtle)",
					borderRadius: "8px",
					padding: "12px",
					background: "var(--dv-bg-surface)",
				}}
			>
				<h3 style={{ margin: "0 0 8px 0", fontSize: "14px" }}>
					4-Quadrant Counterfactual Proof Card (<code>DV-COL-012</code>)
				</h3>
				<div
					style={{
						display: "grid",
						gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))",
						gap: "10px",
						fontSize: "12px",
					}}
				>
					<div
						style={{
							padding: "8px",
							borderRadius: "6px",
							background: "var(--dv-bg-canvas)",
							border: "1px solid var(--dv-border-subtle)",
						}}
					>
						<strong>Q1 · Structural Invariant:</strong>{" "}
						<code>N = 2 &gt; 1 =&gt; require_src_qualifier == true</code> on{" "}
						<code>hub_limited_partner</code>.
					</div>
					<div
						style={{
							padding: "8px",
							borderRadius: "6px",
							background: "var(--dv-bg-canvas)",
							border: "1px solid var(--dv-border-subtle)",
						}}
					>
						<strong>Q2 · Unrepaired Consequence:</strong> Silent cross-continent
						PII/MNPI join between CalPERS (<code>LP-101</code>) and Bavarian
						Pension (<code>LP-101</code>).
					</div>
					<div
						style={{
							padding: "8px",
							borderRadius: "6px",
							background: "var(--dv-bg-canvas)",
							border: "1px solid var(--dv-border-subtle)",
						}}
					>
						<strong>Q3 · Repair Transformation:</strong> Prepend BKCC domain
						token (<code>GLOBAL</code> vs <code>LEGACY_GP_ADMIN</code>) prior to{" "}
						<code>SHA-256</code>.
					</div>
					<div
						style={{
							padding: "8px",
							borderRadius: "6px",
							background: "var(--dv-bg-canvas)",
							border: "1px solid var(--dv-border-subtle)",
						}}
					>
						<strong>Q4 · Post-Repair Proof:</strong> Disjoint digests{" "}
						<code>[HK: 5D15..0AE4 | GLOBAL:LP-101 CalPERS]</code> !={" "}
						<code>
							[HK: 6B7C..D8B9 | LEGACY_GP_ADMIN:LP-101 Bavarian Pension]
						</code>
						.
					</div>
				</div>
			</div>

			<AttemptDiffViewer />
			<ToonTraceHighlighter />
		</section>
	);
}
