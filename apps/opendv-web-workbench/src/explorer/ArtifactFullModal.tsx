/**
 * View 3 — Artifact Full View (`ArtifactFullModal.tsx`)
 * Full-screen dialog (`role="dialog"`, `aria-modal="true"`) displaying Visual, Raw, or Split panes
 * (`docs/design/open-dv-workbench/HANDOFF.md`).
 */

import type {
	Artifact,
	HoldResolutionParams,
	RbacRole,
} from "@opendv/protocol-ts";
import { useEffect, useRef } from "react";
import { ArtifactVisualizer } from "./ArtifactVisualizers";
import {
	EXPLORER_A11Y,
	EXPLORER_FONTS,
	EXPLORER_GLYPHS,
	EXPLORER_VIZ_LABELS,
	getGlyphCssStyle,
} from "./design-tokens";
import type { ArtifactFullTab } from "./explorer-store";

export interface ArtifactFullModalProps {
	artifact: Artifact;
	activeTab: ArtifactFullTab;
	rbacRole: RbacRole;
	onClose: () => void;
	onSelectTab: (tab: ArtifactFullTab) => void;
	onResolveHold?: ((params: HoldResolutionParams) => void) | undefined;
}

const TABS: readonly { label: string; id: ArtifactFullTab }[] = [
	{ label: "Visual", id: "viz" },
	{ label: "Raw", id: "raw" },
	{ label: "Split", id: "split" },
] as const;

export function ArtifactFullModal({
	artifact,
	activeTab,
	rbacRole,
	onClose,
	onSelectTab,
	onResolveHold,
}: ArtifactFullModalProps) {
	const modalRef = useRef<HTMLDivElement | null>(null);
	const backBtnRef = useRef<HTMLButtonElement | null>(null);
	const prevActiveElementRef = useRef<HTMLElement | null>(null);

	// Focus trap & focus restoration lifecycle
	useEffect(() => {
		prevActiveElementRef.current =
			typeof document !== "undefined"
				? (document.activeElement as HTMLElement | null)
				: null;

		// Move initial focus to the back button
		if (backBtnRef.current && typeof backBtnRef.current.focus === "function") {
			backBtnRef.current.focus();
		}

		const handleKeyDown = (ev: KeyboardEvent) => {
			if (ev.key === "Escape") {
				ev.preventDefault();
				ev.stopPropagation();
				onClose();
				return;
			}

			if (ev.key === "Tab" && modalRef.current) {
				const focusable = Array.from(
					modalRef.current.querySelectorAll<HTMLElement>(
						'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
					),
				).filter(
					(el) =>
						el === backBtnRef.current ||
						el.offsetParent !== null ||
						el.offsetWidth > 0 ||
						el.offsetHeight > 0,
				);

				if (focusable.length === 0) {
					ev.preventDefault();
					return;
				}

				const first = focusable[0];
				const last = focusable[focusable.length - 1];

				if (ev.shiftKey) {
					if (document.activeElement === first) {
						ev.preventDefault();
						last?.focus();
					}
				} else {
					if (document.activeElement === last) {
						ev.preventDefault();
						first?.focus();
					}
				}
			}
		};

		if (typeof window !== "undefined") {
			window.addEventListener("keydown", handleKeyDown);
		}
		return () => {
			if (typeof window !== "undefined") {
				window.removeEventListener("keydown", handleKeyDown);
			}
			// Restore focus to trigger element
			if (
				prevActiveElementRef.current &&
				typeof prevActiveElementRef.current.focus === "function"
			) {
				prevActiveElementRef.current.focus();
			}
		};
	}, [onClose]);

	const glyphSpec =
		artifact.group === "Setting"
			? EXPLORER_GLYPHS.setting
			: EXPLORER_GLYPHS.data;
	const vizLabel =
		EXPLORER_VIZ_LABELS[artifact.viz] ??
		(artifact.type === "ontology_coverage"
			? "Ontology Grounding & Coverage"
			: artifact.type === "ontology_proposal"
				? "Proposed Ontology Delta"
				: artifact.type === "ontology_diff"
					? "Ontology Structural Diff"
					: "Visual View");

	const showViz =
		artifact.viz === "code" && activeTab === "raw"
			? false
			: activeTab !== "raw";
	const showRaw =
		artifact.viz === "code" && activeTab === "raw"
			? true
			: activeTab !== "viz" && artifact.viz !== "code";

	const handleTabKeyDown = (key: string, currentIdx: number) => {
		if (key === "ArrowRight") {
			const next = TABS[(currentIdx + 1) % TABS.length];
			if (next) {
				onSelectTab(next.id);
			}
		} else if (key === "ArrowLeft") {
			const prev = TABS[(currentIdx - 1 + TABS.length) % TABS.length];
			if (prev) {
				onSelectTab(prev.id);
			}
		}
	};

	return (
		<div
			ref={modalRef}
			role="dialog"
			aria-modal="true"
			aria-labelledby="artifact-full-modal-title"
			data-testid="artifact-full-modal"
			style={{
				position: "fixed",
				inset: 0,
				zIndex: 40,
				background: "#F6F7F8",
				display: "flex",
				flexDirection: "column",
				fontFamily: EXPLORER_FONTS.sans,
			}}
		>
			{/* Header bar (52px) */}
			<div
				style={{
					height: "52px",
					padding: "0 24px",
					background: "#FFFFFF",
					borderBottom: "1px solid #E3E5E8",
					display: "flex",
					alignItems: "center",
					justifyContent: "space-between",
					gap: "16px",
				}}
			>
				<div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
					<button
						ref={backBtnRef}
						type="button"
						data-testid="btn-artifact-modal-back"
						onClick={onClose}
						aria-label="Back to previous view (Press Escape to dismiss)"
						style={{
							all: "unset",
							cursor: "pointer",
							fontSize: "12.5px",
							fontWeight: 500,
							color: "#16181D",
							padding: "5px 10px",
							border: "1px solid #E3E5E8",
							borderRadius: "6px",
							background: "#FFFFFF",
						}}
					>
						← Back
					</button>
					<span style={getGlyphCssStyle(glyphSpec, 10)} />
					<h1
						id="artifact-full-modal-title"
						style={{
							margin: 0,
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "16px",
							fontWeight: 600,
							color: "#16181D",
						}}
					>
						{artifact.name}
					</h1>
					<span
						style={{
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "12px",
							color: "#525866",
						}}
					>
						{artifact.type} · {artifact.fmt} · {artifact.group}
					</span>
				</div>

				{/* Tablist: Visual | Raw | Split */}
				<div
					role="tablist"
					aria-label="Artifact display mode"
					style={{
						display: "inline-flex",
						border: "1px solid #E3E5E8",
						borderRadius: "6px",
						overflow: "hidden",
					}}
				>
					{TABS.map((t, idx) => {
						const isSelected = activeTab === t.id;
						return (
							<button
								key={t.id}
								type="button"
								role="tab"
								aria-selected={isSelected}
								data-testid={`tab-artifact-${t.id}`}
								onClick={() => onSelectTab(t.id)}
								onKeyDown={(ev) => handleTabKeyDown(ev.key, idx)}
								style={{
									all: "unset",
									cursor: "pointer",
									fontSize: "12px",
									fontWeight: 500,
									padding: "5px 12px",
									background: isSelected ? "#16181D" : "#FFFFFF",
									color: isSelected ? "#FFFFFF" : "#16181D",
								}}
							>
								{t.label}
							</button>
						);
					})}
				</div>
			</div>

			{/* Sub-bar: description */}
			<div
				style={{
					padding: "10px 24px",
					background: "#FFFFFF",
					borderBottom: "1px solid #E3E5E8",
					fontSize: "13px",
					color: "#3B414C",
				}}
			>
				{artifact.desc}
			</div>

			{/* Body */}
			<div
				style={{
					flex: 1,
					overflow: "auto",
					padding: "20px 24px",
					display: "grid",
					gridTemplateColumns:
						showViz && showRaw ? "minmax(0, 1fr) minmax(0, 1fr)" : "minmax(0, 1fr)",
					gap: "20px",
					alignItems: "start",
				}}
			>
				{/* Visual Pane */}
				{showViz && (
					<div
						data-testid="artifact-visual-pane"
						style={{
							background: "#FFFFFF",
							border: "1px solid #E3E5E8",
							borderRadius: "8px",
							padding: "18px",
							minWidth: 0,
						}}
					>
						<div
							style={{
								fontSize: "11px",
								fontWeight: 600,
								textTransform: "uppercase",
								letterSpacing: "0.06em",
								color: "#6B7280",
								marginBottom: "12px",
							}}
						>
							{vizLabel}
						</div>
						<ArtifactVisualizer
							artifact={artifact}
							onResolveHold={onResolveHold ?? undefined}
						/>
					</div>
				)}

				{/* Raw Content Pane */}
				{showRaw && (
					<div
						data-testid="artifact-raw-pane"
						style={{
							background: "#FFFFFF",
							border: "1px solid #E3E5E8",
							borderRadius: "8px",
							padding: "18px",
							minWidth: 0,
						}}
					>
						<div
							style={{
								display: "flex",
								alignItems: "center",
								justifyContent: "space-between",
								marginBottom: "10px",
							}}
						>
							<span
								style={{
									fontSize: "11px",
									fontWeight: 600,
									textTransform: "uppercase",
									letterSpacing: "0.06em",
									color: "#6B7280",
								}}
							>
								Raw content · {artifact.fmt}
							</span>
							<span
								style={{
									fontFamily: EXPLORER_FONTS.mono,
									fontSize: "11px",
									color: "#6B7280",
								}}
							>
								role: {rbacRole}
							</span>
						</div>
						<pre
							style={{
								margin: 0,
								padding: "14px",
								background: "#16181D",
								color: "#E6E8EB",
								borderRadius: "6px",
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "12px",
								lineHeight: 1.6,
								overflow: "auto",
								maxHeight: "640px",
							}}
						>
							{artifact.raw}
						</pre>
					</div>
				)}
			</div>
		</div>
	);
}
