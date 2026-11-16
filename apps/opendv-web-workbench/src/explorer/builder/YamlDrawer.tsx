/**
 * Slide-Out Live YAML Spec Drawer (`YamlDrawer.tsx`).
 *
 * Provides a monospace editor/viewer for inspecting and editing the normative
 * single-run PipelineInputDTO spec in real time, with live syntax validation,
 * clipboard copy, file download, and direct two-way application back to the wizard.
 */

import { type CSSProperties, useEffect, useState } from "react";
import { EXPLORER_COLORS, EXPLORER_FONTS } from "../design-tokens";
import type { YamlDrawerProps } from "./wizard-types";
import { parseYamlToPipelineInputDto } from "./wizardSpecMapper";

const DRAWER_OVERLAY_STYLE: CSSProperties = {
	position: "fixed",
	top: 0,
	left: 0,
	right: 0,
	bottom: 0,
	backgroundColor: "rgba(22, 24, 29, 0.45)",
	zIndex: 9999,
	display: "flex",
	justifyContent: "flex-end",
	backdropFilter: "blur(2px)",
};

const DRAWER_PANEL_STYLE: CSSProperties = {
	width: "600px",
	maxWidth: "92vw",
	height: "100%",
	backgroundColor: EXPLORER_COLORS.bgSurface,
	boxShadow: "-8px 0 24px rgba(0, 0, 0, 0.18)",
	display: "flex",
	flexDirection: "column",
	fontFamily: EXPLORER_FONTS.sans,
	color: EXPLORER_COLORS.inkPrimary,
	animation: "drawerSlideIn 0.2s cubic-bezier(0.16, 1, 0.3, 1)",
};

const BUTTON_BASE: CSSProperties = {
	padding: "6px 12px",
	borderRadius: "5px",
	fontSize: "12px",
	fontFamily: EXPLORER_FONTS.sans,
	fontWeight: 500,
	cursor: "pointer",
	border: "none",
	display: "inline-flex",
	alignItems: "center",
	gap: "6px",
	transition: "all 0.15s ease",
};

export function YamlDrawer({
	isOpen,
	onClose,
	yamlText,
	onApplyYaml,
	validationError,
	readOnly = false,
}: YamlDrawerProps) {
	const [editedYaml, setEditedYaml] = useState(yamlText);
	const [localError, setLocalError] = useState<string | null>(null);
	const [copySuccess, setCopySuccess] = useState(false);
	const [isDirty, setIsDirty] = useState(false);

	// Synchronize when external yamlText updates and user has not typed dirty edits
	useEffect(() => {
		if (!isDirty) {
			setEditedYaml(yamlText);
			setLocalError(null);
		}
	}, [yamlText, isDirty]);

	if (!isOpen) {
		return null;
	}

	const handleTextChange = (newVal: string) => {
		setEditedYaml(newVal);
		setIsDirty(true);
		try {
			parseYamlToPipelineInputDto(newVal);
			setLocalError(null);
		} catch (err) {
			setLocalError(
				err instanceof Error ? err.message : "Syntax parsing error",
			);
		}
	};

	const handleApply = () => {
		try {
			parseYamlToPipelineInputDto(editedYaml);
			onApplyYaml(editedYaml);
			setIsDirty(false);
			setLocalError(null);
		} catch (err) {
			setLocalError(
				err instanceof Error ? err.message : "Invalid YAML cannot be applied",
			);
		}
	};

	const handleCopy = async () => {
		try {
			if (typeof navigator !== "undefined" && navigator.clipboard) {
				await navigator.clipboard.writeText(editedYaml);
				setCopySuccess(true);
				setTimeout(() => setCopySuccess(false), 2000);
			}
		} catch {
			// Clipboard fallback
		}
	};

	const handleDownload = () => {
		if (typeof document === "undefined") return;
		const blob = new Blob([editedYaml], { type: "text/yaml;charset=utf-8" });
		const url = URL.createObjectURL(blob);
		const link = document.createElement("a");
		link.href = url;
		link.download = "pipeline_spec.yaml";
		document.body.appendChild(link);
		link.click();
		document.body.removeChild(link);
		URL.revokeObjectURL(url);
	};

	const handleReset = () => {
		setEditedYaml(yamlText);
		setIsDirty(false);
		setLocalError(null);
	};

	const activeError = localError || validationError;

	return (
		<div
			style={DRAWER_OVERLAY_STYLE}
			data-testid="yaml-drawer-overlay"
			role="presentation"
			onClick={(e) => {
				if (e.target === e.currentTarget) {
					onClose();
				}
			}}
			onKeyDown={(e) => {
				if (e.key === "Escape") {
					onClose();
				}
			}}
		>
			<aside
				style={DRAWER_PANEL_STYLE}
				data-testid="yaml-drawer"
				aria-label="YAML Spec Drawer"
			>
				{/* Drawer Header */}
				<header
					style={{
						padding: "16px 20px",
						borderBottom: `1px solid ${EXPLORER_COLORS.borderLine}`,
						display: "flex",
						alignItems: "center",
						justifyContent: "space-between",
						backgroundColor: EXPLORER_COLORS.bgPage,
					}}
				>
					<div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
						<span
							style={{
								fontFamily: EXPLORER_FONTS.mono,
								fontWeight: 700,
								fontSize: "15px",
								color: EXPLORER_COLORS.inkPrimary,
							}}
						>
							{"{ }"} Pipeline Spec
						</span>
						<span
							data-testid="yaml-sync-badge"
							style={{
								fontSize: "11px",
								fontFamily: EXPLORER_FONTS.mono,
								padding: "2px 8px",
								borderRadius: "10px",
								backgroundColor: activeError
									? "#FEE2E2"
									: isDirty
										? "#FEF3C7"
										: "#DCFCE7",
								color: activeError
									? "#991B1B"
									: isDirty
										? "#92400E"
										: "#166534",
								fontWeight: 600,
							}}
						>
							{activeError
								? "Syntax Error"
								: isDirty
									? "Unapplied Edits"
									: "In Sync"}
						</span>
					</div>

					<div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
						<button
							type="button"
							data-testid="btn-yaml-copy"
							onClick={handleCopy}
							style={{
								...BUTTON_BASE,
								backgroundColor: EXPLORER_COLORS.bgSurface,
								color: EXPLORER_COLORS.inkPrimary,
								border: `1px solid ${EXPLORER_COLORS.borderLine}`,
							}}
							title="Copy YAML to clipboard"
						>
							{copySuccess ? "✓ Copied!" : "Copy"}
						</button>
						<button
							type="button"
							data-testid="btn-yaml-download"
							onClick={handleDownload}
							style={{
								...BUTTON_BASE,
								backgroundColor: EXPLORER_COLORS.bgSurface,
								color: EXPLORER_COLORS.inkPrimary,
								border: `1px solid ${EXPLORER_COLORS.borderLine}`,
							}}
							title="Download pipeline_spec.yaml"
						>
							Download
						</button>
						<button
							type="button"
							data-testid="btn-yaml-drawer-close"
							onClick={onClose}
							aria-label="Close YAML drawer"
							style={{
								...BUTTON_BASE,
								backgroundColor: "transparent",
								color: EXPLORER_COLORS.inkMuted,
								fontSize: "16px",
								padding: "4px 8px",
							}}
						>
							✕
						</button>
					</div>
				</header>

				{/* Error Chip / Banner on Parse Failure */}
				{activeError && (
					<div
						data-testid="yaml-drawer-error-banner"
						role="alert"
						style={{
							backgroundColor: "#FEF2F2",
							borderBottom: "1px solid #FCA5A5",
							color: "#991B1B",
							padding: "10px 16px",
							fontSize: "12px",
							fontFamily: EXPLORER_FONTS.mono,
							lineHeight: 1.4,
						}}
					>
						<strong>YAML Parse Error:</strong> {activeError}
					</div>
				)}

				{/* Editor Body */}
				<div
					style={{
						flex: 1,
						position: "relative",
						overflow: "hidden",
						display: "flex",
						flexDirection: "column",
					}}
				>
					<textarea
						data-testid="yaml-spec-textarea"
						value={editedYaml}
						onChange={(e) => handleTextChange(e.target.value)}
						readOnly={readOnly}
						spellCheck={false}
						style={{
							flex: 1,
							width: "100%",
							padding: "16px",
							border: "none",
							outline: "none",
							resize: "none",
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "12.5px",
							lineHeight: "1.6",
							color: "#1E293B",
							backgroundColor: "#FAFAFA",
							boxSizing: "border-box",
							whiteSpace: "pre",
						}}
						placeholder="# PipelineInputDTO YAML spec will appear here..."
					/>
				</div>

				{/* Drawer Footer Actions */}
				<footer
					style={{
						padding: "12px 20px",
						borderTop: `1px solid ${EXPLORER_COLORS.borderLine}`,
						backgroundColor: EXPLORER_COLORS.bgPage,
						display: "flex",
						alignItems: "center",
						justifyContent: "space-between",
					}}
				>
					<div style={{ fontSize: "11px", color: EXPLORER_COLORS.inkMuted }}>
						{isDirty
							? "Unapplied modifications"
							: "Matches current wizard state"}
					</div>

					<div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
						{isDirty && (
							<button
								type="button"
								data-testid="btn-yaml-reset"
								onClick={handleReset}
								style={{
									...BUTTON_BASE,
									backgroundColor: EXPLORER_COLORS.bgSurface,
									color: EXPLORER_COLORS.inkMuted,
									border: `1px solid ${EXPLORER_COLORS.borderLine}`,
								}}
							>
								Discard
							</button>
						)}
						<button
							type="button"
							data-testid="btn-yaml-apply"
							onClick={handleApply}
							disabled={Boolean(activeError) || !isDirty || readOnly}
							style={{
								...BUTTON_BASE,
								backgroundColor:
									activeError || !isDirty || readOnly
										? "#CBD5E1"
										: EXPLORER_COLORS.borderSelected,
								color: "#FFFFFF",
								cursor:
									activeError || !isDirty || readOnly
										? "not-allowed"
										: "pointer",
							}}
						>
							Apply to Wizard ➔
						</button>
					</div>
				</footer>
			</aside>
		</div>
	);
}
