/**
 * 3-Tier Entity Resolution Funnel & Gate 5 Unstructured Rules Card (`ThreeTierERFunnel.tsx`)
 * Visualizes Tier-U1 through Tier-U3 ER confidence funnel and VIZ-GATE-06 (`docs/06` §3.5, §4.8).
 */

import type { VaultObservabilityEventDTO } from "@opendv/protocol-ts";
import type {
	GateCardPluginProps,
	WorkbenchGateCardPlugin,
} from "../../plugins/plugin-types";

export const VIZ_GATE_06_ID = "VIZ-GATE-06";

export interface ThreeTierERFunnelProps {
	event?: VaultObservabilityEventDTO | null;
	isExpanded?: boolean;
	onToggleExpand?: () => void;
	onOpenCaptainHold?: () => void;
}

export function ThreeTierERFunnel({
	isExpanded = true,
	onToggleExpand,
	onOpenCaptainHold,
}: ThreeTierERFunnelProps) {
	if (!isExpanded) {
		return null;
	}
	return (
		<div
			aria-label="3-Tier Entity Resolution Confidence Funnel & Gate 5 Rules"
			data-testid="three-tier-er-funnel"
			data-gate-id={VIZ_GATE_06_ID}
			style={{
				backgroundColor: "var(--dv-surface-1)",
				border: "1px solid var(--dv-border-subtle)",
				borderRadius: "0.5rem",
				padding: "1rem",
				display: "flex",
				flexDirection: "column",
				gap: "1rem",
			}}
		>
			{/* Top Header */}
			<div
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
				}}
			>
				<div>
					<div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
						<span
							style={{
								color: "var(--dv-gate-askuser)",
								fontWeight: 700,
								fontSize: "0.75rem",
							}}
						>
							▲ [ASK-USER]
						</span>
						<strong
							style={{ fontSize: "0.875rem", color: "var(--dv-text-primary)" }}
						>
							3-Tier Entity Resolution Funnel &amp; VIZ-GATE-06
						</strong>
					</div>
					<div
						style={{ fontSize: "0.7rem", color: "var(--dv-text-secondary)" }}
					>
						Confidence gating preventing LLM hallucination and silent entity
						hijacking
					</div>
				</div>

				<div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
					<span
						style={{
							fontSize: "0.7rem",
							fontWeight: 700,
							color: "var(--dv-gate-askuser)",
							backgroundColor: "var(--dv-surface-2)",
							border: "1px solid var(--dv-gate-askuser)",
							padding: "0.15rem 0.5rem",
							borderRadius: "0.25rem",
						}}
					>
						1 Active Captain Hold
					</span>
					{onToggleExpand && (
						<button
							type="button"
							onClick={onToggleExpand}
							style={{
								backgroundColor: "var(--dv-surface-2)",
								color: "var(--dv-text-primary)",
								border: "1px solid var(--dv-border-strong)",
								borderRadius: "0.25rem",
								padding: "0.15rem 0.45rem",
								fontSize: "0.7rem",
								cursor: "pointer",
							}}
						>
							Collapse
						</button>
					)}
				</div>
			</div>

			{/* 3-Tier ER Funnel Cards */}
			<div
				style={{
					display: "grid",
					gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
					gap: "0.75rem",
				}}
			>
				{/* Tier-U1: Deterministic Match */}
				<div
					data-testid="tier-u1-card"
					style={{
						backgroundColor: "var(--dv-surface-2)",
						border: "1.5px solid var(--dv-hub-accent)",
						borderRadius: "0.375rem",
						padding: "0.75rem",
					}}
				>
					<div
						style={{
							display: "flex",
							justifyContent: "space-between",
							alignItems: "center",
							marginBottom: "0.4rem",
						}}
					>
						<strong
							style={{ fontSize: "0.75rem", color: "var(--dv-hub-accent)" }}
						>
							Tier-U1: Deterministic ID
						</strong>
						<span
							style={{
								fontSize: "0.7rem",
								fontWeight: 700,
								color: "var(--dv-gate-pass)",
							}}
						>
							3 Bound (S = 1.00)
						</span>
					</div>
					<p
						style={{
							margin: "0 0 0.4rem",
							fontSize: "0.7rem",
							color: "var(--dv-text-secondary)",
						}}
					>
						Exact regex matching on ISO 17442 LEI codes &amp; S3 URIs.
						Auto-binds directly to existing Hub without LLM.
					</p>
					<div
						style={{
							fontSize: "0.6875rem",
							fontFamily: "ui-monospace, monospace",
							color: "var(--dv-text-muted)",
						}}
					>
						• CalPERS LEI: 5493006MHB84DD0ZWV18
						<br />• Nordic Cloud LEI: 529900T8BM49AURSDO55
					</div>
				</div>

				{/* Tier-U2: High-Confidence Candidate */}
				<div
					data-testid="tier-u2-card"
					style={{
						backgroundColor: "var(--dv-surface-2)",
						border: "1.5px solid var(--dv-lnk-accent)",
						borderRadius: "0.375rem",
						padding: "0.75rem",
					}}
				>
					<div
						style={{
							display: "flex",
							justifyContent: "space-between",
							alignItems: "center",
							marginBottom: "0.4rem",
						}}
					>
						<strong
							style={{ fontSize: "0.75rem", color: "var(--dv-lnk-accent)" }}
						>
							Tier-U2: Fuzzy / String Match
						</strong>
						<span
							style={{
								fontSize: "0.7rem",
								fontWeight: 700,
								color: "var(--dv-gate-pass)",
							}}
						>
							3 Bound (S &gt;= 0.96)
						</span>
					</div>
					<p
						style={{
							margin: "0 0 0.4rem",
							fontSize: "0.7rem",
							color: "var(--dv-text-secondary)",
						}}
					>
						Fuzzy string matching with margin $\ge 0.15$. Resolves to known
						entities and companion satellites.
					</p>
					<div
						style={{
							fontSize: "0.6875rem",
							fontFamily: "ui-monospace, monospace",
							color: "var(--dv-text-muted)",
						}}
					>
						• CalPERS legal name (0.98)
						<br />• 125 bps fee cap (0.99)
						<br />• 14.5x EV multiple restatement (0.96)
					</div>
				</div>

				{/* Tier-U3: Ambiguous Candidate Hold */}
				<div
					data-testid="tier-u3-card"
					style={{
						backgroundColor: "var(--dv-surface-2)",
						border: "1.5px solid var(--dv-gate-askuser)",
						borderRadius: "0.375rem",
						padding: "0.75rem",
					}}
				>
					<div
						style={{
							display: "flex",
							justifyContent: "space-between",
							alignItems: "center",
							marginBottom: "0.4rem",
						}}
					>
						<strong
							style={{ fontSize: "0.75rem", color: "var(--dv-gate-askuser)" }}
						>
							Tier-U3: Ambiguous Hold
						</strong>
						<span
							style={{
								fontSize: "0.7rem",
								fontWeight: 700,
								color: "var(--dv-gate-askuser)",
							}}
						>
							1 Held (Margin = 0.02)
						</span>
					</div>
					<p
						style={{
							margin: "0 0 0.4rem",
							fontSize: "0.7rem",
							color: "var(--dv-text-secondary)",
						}}
					>
						Candidate scores close: APEX_CO_I (0.81) vs APEX_BO_IV (0.79).
						BLOCKED from creating hub_fund!
					</p>
					<div
						style={{
							fontSize: "0.6875rem",
							fontFamily: "ui-monospace, monospace",
							color: "var(--dv-text-muted)",
							marginBottom: "0.5rem",
						}}
					>
						• "Horizon Sovereign Co-Invest Vehicle" [1112:1147]
					</div>
					{onOpenCaptainHold && (
						<button
							type="button"
							onClick={onOpenCaptainHold}
							data-testid="btn-resolve-captain-hold"
							style={{
								width: "100%",
								backgroundColor: "var(--dv-gate-askuser)",
								color: "#000",
								border: "none",
								borderRadius: "0.25rem",
								padding: "0.3rem",
								fontSize: "0.75rem",
								fontWeight: 700,
								cursor: "pointer",
							}}
						>
							Open Captain Hold Decision Modal
						</button>
					)}
				</div>
			</div>

			{/* VIZ-GATE-06 Formal Invariants Checklist */}
			<div
				data-testid="gate-rules-status"
				style={{
					backgroundColor: "var(--dv-surface-2)",
					border: "1px solid var(--dv-border-subtle)",
					borderRadius: "0.375rem",
					padding: "0.75rem",
				}}
			>
				<div
					style={{
						fontSize: "0.75rem",
						fontWeight: 700,
						color: "var(--dv-text-primary)",
						marginBottom: "0.4rem",
					}}
				>
					Gate 5 Unstructured Invariants Status (VIZ-GATE-06):
				</div>
				<div
					style={{
						display: "grid",
						gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
						gap: "0.5rem",
						fontSize: "0.7rem",
					}}
				>
					<div>
						<span style={{ color: "var(--dv-gate-pass)", fontWeight: 700 }}>
							● [PASS]{" "}
						</span>
						<strong>Rule U1: </strong>PDFs stored in immutable S3 storage; URI
						&amp; sha256 manifest only.
					</div>
					<div>
						<span style={{ color: "var(--dv-gate-pass)", fontWeight: 700 }}>
							● [PASS]{" "}
						</span>
						<strong>Rule U2: </strong>hub_document + sat_document_manifest
						content-addressed.
					</div>
					<div>
						<span style={{ color: "var(--dv-gate-pass)", fontWeight: 700 }}>
							● [PASS]{" "}
						</span>
						<strong>Rule U3: </strong>All AI extractions stored in
						business_vault satellites (zero Raw Vault mutation).
					</div>
					<div>
						<span style={{ color: "var(--dv-gate-pass)", fontWeight: 700 }}>
							● [PASS]{" "}
						</span>
						<strong>Rule U4 + A.9b: </strong>Zero vector embeddings in Data
						Vault; model, confidence &amp; quote preserved.
					</div>
				</div>
			</div>
		</div>
	);
}

export const VIZ_GATE_06_PLUGIN: WorkbenchGateCardPlugin = {
	gateId: VIZ_GATE_06_ID,
	title: "Gate 5 Unstructured Rules & Bi-Temporal Cut-Offs",
	ruleIds: ["U1", "U2", "U3", "U4", "ALG-A9B", "TMP-01", "TMP-02"],
	component: (props: GateCardPluginProps) => (
		<ThreeTierERFunnel
			event={props.event}
			isExpanded={props.isExpanded}
			onToggleExpand={props.onToggleExpand}
		/>
	),
};
