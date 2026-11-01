/**
 * WB-03: Unstructured PDF Span-Grounding & 3-Tier ER Inspector (`WB03UnstructuredView.tsx`)
 * Primary View Plugin for STAGE_02_SCOUT_UNSTRUCTURED (VIZ-STEP-04 + VIZ-GATE-06) (`docs/06` §3.5, `docs/10` §4).
 */

import { useState } from "react";
import type { ViewPluginProps } from "../../plugins/plugin-types";
import { useWorkbenchStore } from "../../store/workbench-store";
import { VERDICT_VISUAL_GRAMMAR } from "../../theme/theme-registry";
import {
	CaptainHoldDecisionModal,
	DEFAULT_CAPTAIN_HOLD_DATA,
} from "./CaptainHoldDecisionModal";
import { DocumentSpanHighlighter } from "./DocumentSpanHighlighter";
import { ThreeTierERFunnel } from "./ThreeTierERFunnel";

export function WB03UnstructuredView({
	currentEvent,
	onInspectProof,
}: ViewPluginProps) {
	const [isHoldModalOpen, setIsHoldModalOpen] = useState<boolean>(false);

	const activeHolds = useWorkbenchStore((s) => s.activeHoldIds);
	const payload = currentEvent?.layer2_visual_payload;
	const pills = payload?.semantic_hash_pills ?? [];
	const summaryMetrics = payload?.summary_metrics ?? {};
	const verdict = currentEvent?.status ?? "PASS";
	const verdictMeta =
		VERDICT_VISUAL_GRAMMAR[verdict] ?? VERDICT_VISUAL_GRAMMAR.PASS;

	const captainHoldData =
		(payload?.captain_hold as typeof DEFAULT_CAPTAIN_HOLD_DATA | undefined) ??
		DEFAULT_CAPTAIN_HOLD_DATA;

	const hasActiveHold =
		activeHolds.length > 0 ||
		verdict === "ASK-USER" ||
		summaryMetrics.tier_u3_held === 1;

	return (
		<div
			data-testid="wb03-unstructured-view"
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
							WB-03: Unstructured PDF Span-Grounding &amp; 3-Tier ER Inspector
						</h2>
					</div>
					<p
						style={{
							margin: "0.25rem 0 0",
							fontSize: "0.75rem",
							color: "var(--dv-text-secondary)",
						}}
					>
						VIZ-STEP-04 Character-offset span grounding, 3-Tier entity
						resolution, and VIZ-GATE-06 Captain-Hold modal
					</p>
				</div>

				<div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
					{hasActiveHold && (
						<button
							type="button"
							onClick={() => setIsHoldModalOpen(true)}
							data-testid="btn-top-captain-hold"
							style={{
								backgroundColor: "var(--dv-gate-askuser)",
								color: "#000",
								border: "none",
								borderRadius: "0.375rem",
								padding: "0.4rem 0.75rem",
								fontSize: "0.75rem",
								fontWeight: 700,
								cursor: "pointer",
							}}
						>
							Resolve Captain Hold (1 Active) ⚠
						</button>
					)}

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

			{/* Active Hold Alert Banner (if applicable) */}
			{hasActiveHold && (
				<section
					data-testid="active-hold-alert-banner"
					style={{
						backgroundColor: "rgba(245, 158, 11, 0.12)",
						border: "1.5px solid var(--dv-gate-askuser)",
						borderRadius: "0.5rem",
						padding: "0.75rem 1rem",
						display: "flex",
						justifyContent: "space-between",
						alignItems: "center",
						flexWrap: "wrap",
						gap: "0.5rem",
					}}
				>
					<div>
						<strong
							style={{ color: "var(--dv-gate-askuser)", fontSize: "0.8125rem" }}
						>
							▲ ACTIVE CAPTAIN HOLD: Tier-U3 Ambiguous Candidate Detected
						</strong>
						<div
							style={{
								fontSize: "0.75rem",
								color: "var(--dv-text-primary)",
								marginTop: "0.2rem",
							}}
						>
							"Horizon Sovereign Co-Invest Vehicle" on span [1112:1147] blocked
							from creating hub_fund. Margin: 0.02 &lt; 0.15 threshold.
						</div>
					</div>

					<button
						type="button"
						onClick={() => setIsHoldModalOpen(true)}
						style={{
							backgroundColor: "var(--dv-gate-askuser)",
							color: "#000",
							border: "none",
							borderRadius: "0.25rem",
							padding: "0.35rem 0.7rem",
							fontWeight: 700,
							fontSize: "0.75rem",
							cursor: "pointer",
						}}
					>
						Review &amp; Decide ↗
					</button>
				</section>
			)}

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
						Grounded Document &amp; Entity Semantic Hash Pills:
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

			{/* Verbatim PDF Span Highlighter */}
			<DocumentSpanHighlighter
				onOpenCaptainHold={() => setIsHoldModalOpen(true)}
			/>

			{/* 3-Tier ER Confidence Funnel */}
			<ThreeTierERFunnel onOpenCaptainHold={() => setIsHoldModalOpen(true)} />

			{/* Interactive Captain-Hold Decision Modal */}
			<CaptainHoldDecisionModal
				isOpen={isHoldModalOpen}
				onClose={() => setIsHoldModalOpen(false)}
				holdData={captainHoldData}
				eventId={currentEvent?.event_id ?? "evt-pe-004"}
				runId={currentEvent?.run_id}
			/>
		</div>
	);
}
