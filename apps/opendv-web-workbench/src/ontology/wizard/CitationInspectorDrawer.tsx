/**
 * Evidence & Citation Inspector Drawer (`716px` Claude `DESIGN.md`, `FR-CIT-01..04`)
 * (apps/opendv-web-workbench/src/ontology/wizard/CitationInspectorDrawer.tsx)
 */

import type React from "react";
import { CLAUDE_TOKENS, EXPLORER_FONTS } from "../../explorer/design-tokens";
import type { ResolvedCitationItem } from "../store/types";

export interface CitationInspectorDrawerProps {
	isOpen: boolean;
	sessionId?: string | null | undefined;
	resolvedCitations?: ResolvedCitationItem[] | undefined;
	isLoading?: boolean | undefined;
	onJumpToStep3?:
		| ((target?: { chunkId?: string; tableName?: string }) => void)
		| undefined;
	onClose: () => void;
}

function renderHighlightedParagraph(
	surroundingText: string | null | undefined,
	highlightedSpan: string | null | undefined,
): React.ReactNode {
	if (!surroundingText) {
		return highlightedSpan ? <mark>{highlightedSpan}</mark> : null;
	}
	if (!highlightedSpan) {
		return surroundingText;
	}
	const idx = surroundingText.indexOf(highlightedSpan);
	if (idx < 0) {
		return (
			<>
				{surroundingText} <mark>{highlightedSpan}</mark>
			</>
		);
	}
	return (
		<>
			{surroundingText.slice(0, idx)}
			<mark
				style={{
					background: "rgba(204, 120, 92, 0.22)",
					color: CLAUDE_TOKENS.colors.ink,
					padding: "1px 3px",
					borderRadius: "3px",
				}}
			>
				{highlightedSpan}
			</mark>
			{surroundingText.slice(idx + highlightedSpan.length)}
		</>
	);
}

export const CitationInspectorDrawer: React.FC<
	CitationInspectorDrawerProps
> = ({
	isOpen,
	sessionId,
	resolvedCitations = [],
	isLoading = false,
	onJumpToStep3,
	onClose,
}) => {
	if (!isOpen) return null;

	return (
		<aside
			data-testid="citation-inspector-drawer"
			className="claude-drawer-panel"
			aria-label="Citation and FIBO Evidence Inspector"
			style={{
				width: CLAUDE_TOKENS.chrome.drawerWidth,
				maxWidth: "100vw",
				position: "fixed",
				top: 0,
				right: 0,
				bottom: 0,
				zIndex: 60,
				background: CLAUDE_TOKENS.colors.canvas,
				borderLeft: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
				boxShadow: "-8px 0 32px rgba(20, 20, 19, 0.14)",
				display: "flex",
				flexDirection: "column",
				overflow: "hidden",
			}}
		>
			<header
				style={{
					padding: "16px 20px",
					borderBottom: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
					background: CLAUDE_TOKENS.colors.surfaceSoft,
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
				}}
			>
				<div>
					<h2 className="claude-h2" style={{ margin: 0 }}>
						Evidence &amp; Citation Inspector
					</h2>
					<div style={{ fontSize: "12px", color: CLAUDE_TOKENS.colors.muted }}>
						Source grounding, highlighted document spans, and side-by-side FIBO
						spec
						{sessionId ? ` · ${sessionId}` : ""}
					</div>
				</div>
				<button
					type="button"
					className="claude-btn-secondary"
					data-testid="btn-close-citation-drawer"
					onClick={onClose}
				>
					Close
				</button>
			</header>

			<div
				style={{
					flex: 1,
					overflowY: "auto",
					padding: "20px",
					display: "flex",
					flexDirection: "column",
					gap: "16px",
				}}
			>
				{isLoading && (
					<div style={{ fontSize: "13px", color: CLAUDE_TOKENS.colors.muted }}>
						Resolving evidence spans and FIBO definitions...
					</div>
				)}

				{resolvedCitations.length === 0 && !isLoading && (
					<div
						className="claude-card-cream"
						style={{ padding: "16px", fontSize: "13px" }}
					>
						Select an evidence badge on any concept candidate or clarification
						to inspect source spans.
					</div>
				)}

				{(() => {
					const seen = new Map<string, number>();
					return resolvedCitations.map((item, idx) => {
						const cit = item.citation || {};
						const chunkId =
							typeof cit.chunk_id === "string" ? cit.chunk_id : undefined;
						const tableName =
							item.table_profile?.table_name ??
							(typeof cit.table_name === "string" ? cit.table_name : undefined);
						const baseKey = String(
							chunkId ?? tableName ?? cit.source_label ?? "cit",
						);
						const count = (seen.get(baseKey) ?? 0) + 1;
						seen.set(baseKey, count);
						const sourceLabel = String(
							cit.source_label ?? tableName ?? chunkId ?? `Citation #${count}`,
						);

						return (
							<article
								key={`${baseKey}#${count}`}
								className="claude-card"
								style={{
									padding: "16px",
									display: "flex",
									flexDirection: "column",
									gap: "12px",
									background: CLAUDE_TOKENS.colors.surfaceSoft,
								}}
							>
								<div
									style={{
										display: "flex",
										justifyContent: "space-between",
										alignItems: "center",
										gap: "8px",
									}}
								>
									<div
										style={{
											display: "flex",
											alignItems: "center",
											gap: "8px",
											flexWrap: "wrap",
										}}
									>
										<span className="claude-pill" style={{ fontSize: "11px" }}>
											{item.evidence_type}
										</span>
										<strong
											style={{
												fontSize: "13px",
												color: CLAUDE_TOKENS.colors.ink,
											}}
										>
											{sourceLabel}
										</strong>
										{typeof cit.page_number === "number" && (
											<span
												style={{
													fontSize: "11px",
													color: CLAUDE_TOKENS.colors.muted,
												}}
											>
												p.{cit.page_number}
											</span>
										)}
										{typeof cit.start_char === "number" &&
											typeof cit.end_char === "number" && (
												<span
													style={{
														fontFamily: EXPLORER_FONTS.mono,
														fontSize: "11px",
														color: CLAUDE_TOKENS.colors.muted,
													}}
												>
													[{cit.start_char}..{cit.end_char}]
												</span>
											)}
									</div>
									{onJumpToStep3 && (
										<button
											type="button"
											className="claude-btn-secondary"
											data-testid={`btn-jump-step3-${idx}`}
											style={{ fontSize: "12px", padding: "4px 10px" }}
											onClick={() =>
												onJumpToStep3({
													...(chunkId ? { chunkId } : {}),
													...(tableName ? { tableName } : {}),
												})
											}
										>
											Jump to Step 3
										</button>
									)}
								</div>

								<div
									style={{
										display: "grid",
										gridTemplateColumns: item.fibo_definition
											? "1fr 1fr"
											: "1fr",
										gap: "12px",
									}}
								>
									<div
										style={{
											padding: "12px",
											borderRadius: "8px",
											background: CLAUDE_TOKENS.colors.canvas,
											border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
										}}
									>
										<div
											style={{
												fontSize: "11px",
												fontWeight: 600,
												color: CLAUDE_TOKENS.colors.muted,
												marginBottom: "6px",
											}}
										>
											Evidence Span
										</div>
										<p
											style={{
												margin: 0,
												fontSize: "13px",
												lineHeight: 1.6,
												color: CLAUDE_TOKENS.colors.ink,
											}}
										>
											{renderHighlightedParagraph(
												item.surrounding_text,
												item.highlighted_span,
											)}
										</p>
										{item.table_profile && (
											<div
												style={{
													marginTop: "8px",
													fontSize: "12px",
													fontFamily: EXPLORER_FONTS.mono,
												}}
											>
												{item.table_profile.table_name}.
												{item.table_profile.column_name}
											</div>
										)}
									</div>

									{item.fibo_definition && (
										<div
											style={{
												padding: "12px",
												borderRadius: "8px",
												background: CLAUDE_TOKENS.colors.surfaceCreamStrong,
												border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
											}}
										>
											<div
												style={{
													fontSize: "11px",
													fontWeight: 600,
													color: CLAUDE_TOKENS.colors.primaryActive,
													marginBottom: "4px",
												}}
											>
												FIBO Specification ·{" "}
												{item.fibo_definition.module ?? "FIBO"}
											</div>
											<div
												style={{
													fontWeight: 600,
													fontSize: "13px",
													color: CLAUDE_TOKENS.colors.ink,
												}}
											>
												{item.fibo_definition.label}
											</div>
											<div
												style={{
													fontSize: "11px",
													fontFamily: EXPLORER_FONTS.mono,
													color: CLAUDE_TOKENS.colors.muted,
													wordBreak: "break-all",
													margin: "4px 0",
												}}
											>
												{item.fibo_definition.uri}
											</div>
											<p
												style={{
													margin: "6px 0 0",
													fontSize: "12.5px",
													lineHeight: 1.5,
													color: CLAUDE_TOKENS.colors.body,
												}}
											>
												{item.fibo_definition.definition}
											</p>
										</div>
									)}
								</div>
							</article>
						);
					});
				})()}
			</div>
		</aside>
	);
};
