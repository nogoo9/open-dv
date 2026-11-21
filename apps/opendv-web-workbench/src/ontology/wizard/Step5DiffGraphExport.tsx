/**
 * Step 5: Diff Summary, GraphCanvas, Optional VisualEditorDrawer & Export/Commit (`D9`, `D15`, `D26`)
 * (apps/opendv-web-workbench/src/ontology/wizard/Step5DiffGraphExport.tsx)
 */

import type React from "react";
import { CLAUDE_TOKENS, EXPLORER_FONTS } from "../../explorer/design-tokens";
import { safeUseEffect, safeUseState } from "../../explorer/modals/safeHooks";
import { GraphCanvas } from "../components/GraphCanvas";
import { VisualEditorDrawer } from "../components/VisualEditorDrawer";
import type { WizardDiffSummary } from "./wizard-types";

export interface Step5DiffGraphExportProps {
	sessionId?: string | null | undefined;
	diffSummary?: WizardDiffSummary | undefined;
	isVisualEditorOpen?: boolean | undefined;
	viewportWidth?: number | undefined;
	hasUnsavedChanges?: boolean | undefined;
	onOpenVisualEditor?: (() => void) | undefined;
	onCloseVisualEditor?: (() => void) | undefined;
	onExportOnly?:
		| ((format: "yaml" | "json" | "ttl") => Promise<void> | void)
		| undefined;
	onCommit?: ((rationale?: string) => Promise<void> | void) | undefined;
	onOpenLogs?:
		| ((opts?: { stageId?: string; wizardStep?: number }) => void)
		| undefined;
}

const DEFAULT_DIFF_SUMMARY: WizardDiffSummary = {
	fiboAdded: 0,
	customAdded: 0,
	enrichedCount: 0,
	linksAdded: 0,
	validationPassed: true,
};

export const Step5DiffGraphExport: React.FC<Step5DiffGraphExportProps> = ({
	sessionId,
	diffSummary = DEFAULT_DIFF_SUMMARY,
	isVisualEditorOpen = false,
	viewportWidth,
	hasUnsavedChanges = false,
	onOpenVisualEditor,
	onCloseVisualEditor,
	onExportOnly,
	onCommit,
	onOpenLogs,
}) => {
	const [exportFmt, setExportFmt] = safeUseState<"yaml" | "json" | "ttl">(
		"yaml",
	);
	const [commitRationale, setCommitRationale] = safeUseState("");
	const [measuredWidth, setMeasuredWidth] = safeUseState<number>(
		typeof window !== "undefined" ? window.innerWidth : 1280,
	);
	safeUseEffect(() => {
		if (viewportWidth !== undefined || typeof window === "undefined") {
			return;
		}
		setMeasuredWidth(window.innerWidth);
		const onResize = () => setMeasuredWidth(window.innerWidth);
		window.addEventListener("resize", onResize);
		return () => window.removeEventListener("resize", onResize);
	}, [viewportWidth]);
	const effectiveWidth = viewportWidth ?? measuredWidth;
	const isMobileReadOnly = effectiveWidth < 768;

	return (
		<section
			data-testid="wizard-step-5"
			style={{ display: "flex", flexDirection: "column", gap: "18px" }}
		>
			<header
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "flex-start",
					gap: "12px",
					flexWrap: "wrap",
				}}
			>
				<div>
					<h1 className="claude-h1" style={{ margin: "0 0 6px" }}>
						Step 5 · Ontology Diff, Graph Canvas &amp; Export
					</h1>
					<p
						style={{
							margin: 0,
							fontSize: "14px",
							color: CLAUDE_TOKENS.colors.body,
						}}
					>
						Inspect delta counts, validate invariants, export ontology artifacts
						standalone, or commit to proceed to Data Vault build
						{sessionId ? ` · ${sessionId}` : ""}.
					</p>
				</div>
				<button
					type="button"
					className="claude-btn-secondary"
					data-testid="btn-step5-view-logs"
					onClick={() => onOpenLogs?.({ wizardStep: 5 })}
				>
					View Stage Logs ↗
				</button>
			</header>

			<div
				className="claude-card-cream"
				style={{
					padding: "12px 16px",
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					flexWrap: "wrap",
					gap: "12px",
				}}
			>
				<div
					style={{
						display: "flex",
						gap: "8px",
						flexWrap: "wrap",
						alignItems: "center",
					}}
				>
					<span className="claude-pill">+{diffSummary.fiboAdded} FIBO</span>
					<span className="claude-pill">+{diffSummary.customAdded} Custom</span>
					<span className="claude-pill">
						~{diffSummary.enrichedCount} Enriched
					</span>
					<span className="claude-pill">+{diffSummary.linksAdded} Links</span>
					<span
						className="claude-pill"
						style={{
							background: diffSummary.validationPassed ? "#e4f4ea" : "#fdf2ee",
							color: diffSummary.validationPassed
								? CLAUDE_TOKENS.colors.success
								: CLAUDE_TOKENS.colors.error,
							fontWeight: 600,
						}}
					>
						{diffSummary.validationPassed
							? "Validation PASS"
							: "Validation Review"}
					</span>
				</div>

				<div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
					{isMobileReadOnly && (
						<span
							className="claude-pill"
							style={{ fontSize: "11px", color: CLAUDE_TOKENS.colors.muted }}
						>
							View-only on narrow viewports (&lt; 768px)
						</span>
					)}
					<button
						type="button"
						className="claude-btn-secondary"
						data-testid="btn-open-visual-editor"
						disabled={isMobileReadOnly}
						onClick={() => onOpenVisualEditor?.()}
					>
						+ Add / Edit Concept, Attribute or Link
					</button>
				</div>
			</div>

			<div
				className="claude-card"
				style={{
					display: "grid",
					gridTemplateColumns:
						isVisualEditorOpen && !isMobileReadOnly ? "1.6fr 1fr" : "1fr",
					gap: "12px",
					padding: "12px",
					minHeight: "360px",
				}}
			>
				<div
					className={
						isMobileReadOnly ? "dag-dot-grid view-only-mobile" : "dag-dot-grid"
					}
					style={{
						minWidth: 0,
						minHeight: "340px",
						borderRadius: "8px",
						overflow: "hidden",
					}}
				>
					<GraphCanvas />
				</div>
				{isVisualEditorOpen && !isMobileReadOnly && (
					<VisualEditorDrawer
						hideProposalTab
						{...(onCloseVisualEditor ? { onClose: onCloseVisualEditor } : {})}
					/>
				)}
			</div>

			<div
				className="claude-card-cream"
				style={{
					padding: "14px 16px",
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					flexWrap: "wrap",
					gap: "12px",
				}}
			>
				<div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
					<select
						aria-label="Export ontology format"
						value={exportFmt}
						onChange={(e) =>
							setExportFmt(e.target.value as "yaml" | "json" | "ttl")
						}
						style={{
							padding: "6px 10px",
							borderRadius: "6px",
							border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
							background: CLAUDE_TOKENS.colors.canvas,
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "12px",
						}}
					>
						<option value="yaml">.yaml</option>
						<option value="json">.json</option>
						<option value="ttl">.ttl</option>
					</select>
					<button
						type="button"
						className="claude-btn-secondary"
						data-testid="btn-export-ontology-only"
						onClick={() => onExportOnly?.(exportFmt)}
					>
						Export / Promote Ontology Only
					</button>
				</div>

				<div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
					<input
						type="text"
						placeholder="Commit rationale (optional)..."
						value={commitRationale}
						onChange={(e) => setCommitRationale(e.target.value)}
						style={{
							padding: "7px 10px",
							borderRadius: "6px",
							border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
							background: CLAUDE_TOKENS.colors.canvas,
							fontSize: "12.5px",
						}}
					/>
					<button
						type="button"
						className="claude-btn-primary"
						data-testid="btn-commit-ontology"
						onClick={() => onCommit?.(commitRationale || undefined)}
					>
						{hasUnsavedChanges ? "Commit Draft & Continue" : "Commit Ontology"}
					</button>
				</div>
			</div>
		</section>
	);
};
