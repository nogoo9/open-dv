/**
 * Visual Diff Overlay Component (Task 5D.5, #226)
 * (apps/opendv-web-workbench/src/ontology/components/DiffOverlay.tsx)
 *
 * Implements:
 * - Color-coded version comparison between committed versions or pending proposals
 * - Green additions (+), red deletions (-), amber modifications (Δ)
 * - Side-by-side or unified overlay mode
 * - Summary statistics with breaking changes count
 * - Strictly Additive Guarantee banner when breaking_count == 0 (ONT-EVO-001)
 */

import type React from "react";
import { useStudioStore } from "../store/studio-store";
import type { DiffRecord } from "../store/types";

export interface DiffOverlayProps {
	className?: string;
	onClose?: () => void;
}

export function getChangeTypeBadge(changeType: DiffRecord["change_type"]) {
	switch (changeType) {
		case "added":
			return { label: "+ ADD", className: "diff-badge-added" };
		case "deleted":
			return { label: "- DEL", className: "diff-badge-deleted" };
		case "modified":
			return { label: "Δ MOD", className: "diff-badge-modified" };
		default:
			return { label: changeType, className: "diff-badge-default" };
	}
}

export const DiffOverlay: React.FC<DiffOverlayProps> = ({
	className = "",
	onClose,
}) => {
	const activeSession = useStudioStore((s) => s.activeSession);
	const diffState = useStudioStore((s) => s.diffState);
	const setDiffMode = useStudioStore((s) => s.setDiffMode);
	const loadAndComputeDiff = useStudioStore((s) => s.loadAndComputeDiff);

	const { records, summary, base_version, target_version, mode } = diffState;
	if (mode === "off" || !diffState.active) {
		return null;
	}

	const availableVersions = Array.from(
		new Set(
			[
				"0.0.0",
				...(activeSession?.commits || []).map((c) => c.version),
				...(activeSession?.active_version
					? [activeSession.active_version]
					: []),
				base_version,
				target_version,
			].filter(Boolean),
		),
	);

	const isStrictlyAdditive =
		summary.breaking_count === 0 &&
		summary.deleted_count === 0 &&
		!records.some((r) => r.is_breaking);

	return (
		<div className={`diff-overlay ${className}`} data-testid="diff-overlay">
			{/* Diff Header */}
			<div className="diff-header">
				<div className="diff-title-row">
					<h3 className="diff-title">
						Ontology Version Diff: v{base_version} → v{target_version}
					</h3>
					{onClose && (
						<button
							type="button"
							className="btn-close"
							onClick={onClose}
							aria-label="Close diff overlay"
							data-testid="close-diff-btn"
						>
							✕
						</button>
					)}
				</div>

				<div
					className="diff-version-selectors"
					data-testid="diff-version-selectors"
				>
					<label>
						Baseline:{" "}
						<select
							value={base_version}
							onChange={(e) =>
								void loadAndComputeDiff(e.target.value, target_version)
							}
							data-testid="diff-base-version-select"
						>
							{availableVersions.map((ver) => (
								<option key={`base-${ver}`} value={ver}>
									v{ver}
								</option>
							))}
						</select>
					</label>
					<label>
						Target:{" "}
						<select
							value={target_version}
							onChange={(e) =>
								void loadAndComputeDiff(base_version, e.target.value)
							}
							data-testid="diff-target-version-select"
						>
							{availableVersions.map((ver) => (
								<option key={`target-${ver}`} value={ver}>
									v{ver}
								</option>
							))}
						</select>
					</label>
				</div>

				{/* Strictly Additive Guarantee Banner */}
				{isStrictlyAdditive ? (
					<div
						className="strictly-additive-banner"
						data-testid="strictly-additive-banner"
					>
						<span className="banner-icon">🛡️</span>
						<div className="banner-text">
							<strong>STRICTLY ADDITIVE GUARANTEE (ONT-EVO-001)</strong>
							<p>
								All operations introduce non-breaking semantic additions. Zero
								deletions or contract breaks detected.
							</p>
						</div>
					</div>
				) : (
					<div
						className="breaking-warning-banner"
						data-testid="breaking-warning-banner"
					>
						<span className="banner-icon">⚠️</span>
						<div className="banner-text">
							<strong>CONTRACT MODIFICATION DETECTED</strong>
							<p>
								This diff contains {summary.breaking_count} breaking changes and{" "}
								{summary.deleted_count} deletions requiring steward/admin
								waiver.
							</p>
						</div>
					</div>
				)}

				{/* Summary Metrics & Mode Toggle */}
				<div className="diff-controls-bar">
					<div className="diff-summary-badges">
						<span
							className="summary-pill pill-added"
							data-testid="added-count-pill"
						>
							+{summary.added_count} Added
						</span>
						<span
							className="summary-pill pill-modified"
							data-testid="modified-count-pill"
						>
							Δ {summary.modified_count} Modified
						</span>
						<span
							className="summary-pill pill-deleted"
							data-testid="deleted-count-pill"
						>
							-{summary.deleted_count} Deleted
						</span>
					</div>

					<div
						className="diff-mode-toggle"
						role="group"
						aria-label="Diff view mode"
					>
						<button
							type="button"
							className={`mode-btn ${mode === "unified" ? "mode-btn-active" : ""}`}
							onClick={() => setDiffMode("unified")}
							data-testid="diff-mode-unified"
						>
							Unified
						</button>
						<button
							type="button"
							className={`mode-btn ${mode === "side-by-side" ? "mode-btn-active" : ""}`}
							onClick={() => setDiffMode("side-by-side")}
							data-testid="diff-mode-side-by-side"
						>
							Side-by-Side
						</button>
					</div>
				</div>
			</div>

			{/* Diff Records Body */}
			<div className="diff-body" data-testid="diff-records-container">
				{records.length === 0 ? (
					<div className="empty-diff-msg" data-testid="empty-diff-msg">
						Identical ontology versions: zero delta operations between v
						{base_version} and v{target_version}.
					</div>
				) : mode === "side-by-side" ? (
					<div
						className="diff-side-by-side-view"
						data-testid="diff-side-by-side-view"
					>
						<div className="side-column side-baseline">
							<h4 className="side-heading">Baseline (v{base_version})</h4>
							<ul className="side-list">
								{records
									.filter(
										(r) =>
											r.change_type === "deleted" ||
											r.change_type === "modified",
									)
									.map((r, idx) => (
										<li
											key={`base-${r.item_type}-${r.change_type}-${r.identifier}-${idx}`}
											className="side-record-item record-old"
										>
											<span className="record-label">{r.label}</span>
											<span className="record-details">{r.details}</span>
										</li>
									))}
								{records.filter(
									(r) =>
										r.change_type === "deleted" || r.change_type === "modified",
								).length === 0 && (
									<li className="side-empty">Zero baseline items modified.</li>
								)}
							</ul>
						</div>

						<div className="side-column side-target">
							<h4 className="side-heading">Target (v{target_version})</h4>
							<ul className="side-list">
								{records
									.filter(
										(r) =>
											r.change_type === "added" || r.change_type === "modified",
									)
									.map((r, idx) => (
										<li
											key={`target-${r.item_type}-${r.change_type}-${r.identifier}-${idx}`}
											className="side-record-item record-new"
										>
											<span className="record-label">{r.label}</span>
											<span className="record-details">{r.details}</span>
										</li>
									))}
								{records.filter(
									(r) =>
										r.change_type === "added" || r.change_type === "modified",
								).length === 0 && (
									<li className="side-empty">
										Zero target items added or modified.
									</li>
								)}
							</ul>
						</div>
					</div>
				) : (
					/* Unified List View */
					<div className="diff-unified-list" data-testid="diff-unified-list">
						{records.map((r, idx) => {
							const badge = getChangeTypeBadge(r.change_type);
							return (
								<div
									key={`${r.item_type}-${r.change_type}-${r.identifier}-${idx}`}
									className={`diff-record-row record-${r.change_type}`}
									data-testid={`diff-record-${r.identifier}`}
								>
									<span className={`change-badge ${badge.className}`}>
										{badge.label}
									</span>
									<span className="item-type-pill">{r.item_type}</span>
									<div className="record-main">
										<span className="record-identifier">{r.label}</span>
										<code className="record-uri">{r.identifier}</code>
										<p className="record-desc">{r.details}</p>
									</div>
									{r.is_breaking && (
										<span className="breaking-tag" title="Breaking change">
											BREAKING
										</span>
									)}
								</div>
							);
						})}
					</div>
				)}
			</div>
		</div>
	);
};
