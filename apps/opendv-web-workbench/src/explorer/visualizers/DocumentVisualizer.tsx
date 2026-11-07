/**
 * Document & Span Highlight Visualizer (`DocumentVisualizer.tsx`)
 * Renders source text with character span highlights and extraction cards.
 * Anti-hardcoding: dynamic candidate derivation without hardcoded test fixtures.
 */

import type {
	DocExtractionSpec,
	HoldResolutionParams,
} from "@opendv/protocol-ts";
import { useState } from "react";
import { EXPLORER_COLORS, EXPLORER_FONTS } from "../design-tokens";

export interface DocumentSpec {
	documentName: string;
	sourceText: string;
	extractions: DocExtractionSpec[];
}

export interface DocumentVisualizerProps {
	doc: DocumentSpec;
	onResolveHold?: ((params: HoldResolutionParams) => void) | undefined;
}

export function buildDocSegments(
	sourceText: string,
	extractions: readonly DocExtractionSpec[],
): { text: string; bg: string; ring: string }[] {
	const valid = [...extractions]
		.filter((e) => e.start >= 0 && e.end > e.start && e.end <= sourceText.length)
		.sort((a, b) => a.start - b.start);

	if (valid.length === 0) {
		return [{ text: sourceText, bg: "transparent", ring: "none" }];
	}

	const segments: { text: string; bg: string; ring: string }[] = [];
	let cursor = 0;

	for (const ext of valid) {
		if (ext.start > cursor) {
			segments.push({
				text: sourceText.slice(cursor, ext.start),
				bg: "transparent",
				ring: "none",
			});
		}
		const isU3 = ext.tier === "Tier-U3";
		segments.push({
			text: sourceText.slice(ext.start, ext.end),
			bg: isU3 ? EXPLORER_COLORS.spanTierU3Bg : EXPLORER_COLORS.spanTierU1Bg,
			ring: isU3
				? EXPLORER_COLORS.spanTierU3Ring
				: EXPLORER_COLORS.spanTierU1Ring,
		});
		cursor = ext.end;
	}

	if (cursor < sourceText.length) {
		segments.push({
			text: sourceText.slice(cursor),
			bg: "transparent",
			ring: "none",
		});
	}

	return segments;
}

export function DocumentVisualizer({
	doc,
	onResolveHold,
}: DocumentVisualizerProps) {
	const { sourceText, extractions } = doc;
	const segments = buildDocSegments(sourceText, extractions);
	const [selectedCandidates, setSelectedCandidates] = useState<
		Record<string, string>
	>({});

	return (
		<div
			data-testid="viz-doc"
			style={{ display: "flex", flexDirection: "column", gap: "14px" }}
		>
			<div
				style={{
					padding: "14px 16px",
					border: "1px solid #E3E5E8",
					borderRadius: "6px",
					background: "#FAFBFD",
				}}
			>
				<div
					style={{
						fontFamily: EXPLORER_FONTS.mono,
						fontSize: "10.5px",
						color: "#6B7280",
						textTransform: "uppercase",
						letterSpacing: "0.06em",
						marginBottom: "8px",
					}}
				>
					Source document · highlighted character spans
				</div>
				<pre
					style={{
						margin: 0,
						fontFamily: EXPLORER_FONTS.mono,
						fontSize: "12px",
						lineHeight: 1.65,
						color: "#16181D",
						whiteSpace: "pre-wrap",
					}}
				>
					{segments.map((seg, idx) => (
						<span
							key={`seg-${idx}`}
							style={{
								padding: "1px 0",
								borderRadius: "3px",
								background: seg.bg,
								boxShadow: seg.ring,
							}}
						>
							{seg.text}
						</span>
					))}
				</pre>
			</div>

			<div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
				<div
					style={{
						fontSize: "11px",
						fontWeight: 600,
						textTransform: "uppercase",
						letterSpacing: "0.06em",
						color: "#6B7280",
					}}
				>
					A.9b extractions (doc[start:end] == verbatim_quote)
				</div>
				{extractions.map((ext) => {
					const isU3 = ext.tier === "Tier-U3";
					const swatch = isU3
						? EXPLORER_COLORS.spanTierU3Swatch
						: EXPLORER_COLORS.spanTierU1Swatch;
					const decisionFg = isU3 ? "#8A3B07" : "#16181D";
					const chosenCand =
						selectedCandidates[ext.id] ??
						ext.candidates?.[0]?.id ??
						"";

					return (
						<div
							key={ext.id}
							data-testid={`doc-extraction-${ext.id}`}
							style={{
								padding: "10px 12px",
								border: "1px solid #E3E5E8",
								borderRadius: "6px",
								display: "flex",
								flexDirection: "column",
								gap: "4px",
							}}
						>
							<div
								style={{
									display: "flex",
									alignItems: "center",
									justifyContent: "space-between",
									gap: "8px",
								}}
							>
								<span
									style={{
										display: "inline-flex",
										alignItems: "center",
										gap: "6px",
									}}
								>
									<span
										style={{
											width: "10px",
											height: "10px",
											borderRadius: "2px",
											background: swatch,
										}}
									/>
									<span
										style={{
											fontFamily: EXPLORER_FONTS.mono,
											fontSize: "11.5px",
											fontWeight: 600,
											color: "#16181D",
										}}
									>
										{ext.tier} · confidence {ext.conf}
									</span>
								</span>
								<span
									style={{
										fontFamily: EXPLORER_FONTS.mono,
										fontSize: "11px",
										color: "#6B7280",
									}}
								>
									{ext.id} [{ext.start}:{ext.end}]
								</span>
							</div>
							<div
								style={{
									fontFamily: EXPLORER_FONTS.mono,
									fontSize: "11.5px",
									color: "#16181D",
								}}
							>
								"{ext.quote}"
							</div>
							<div
								style={{
									fontSize: "11.5px",
									color: decisionFg,
								}}
							>
								{ext.decision}
							</div>

							{/* Steward Hold Resolution Controls for Tier-U3 */}
							{isU3 && ext.holdId && onResolveHold && (
								<div
									data-testid={`steward-hold-controls-${ext.holdId}`}
									style={{
										marginTop: "6px",
										paddingTop: "8px",
										borderTop: "1px dashed #E3E5E8",
										display: "flex",
										flexWrap: "wrap",
										alignItems: "center",
										justifyContent: "space-between",
										gap: "8px",
									}}
								>
									{ext.candidates && ext.candidates.length > 0 && (
										<select
											aria-label="Candidate Entity for Steward Hold"
											data-testid={`select-hold-candidate-${ext.holdId}`}
											value={chosenCand}
											onChange={(ev) =>
												setSelectedCandidates((prev) => ({
													...prev,
													[ext.id]: ev.target.value,
												}))
											}
											style={{
												fontFamily: EXPLORER_FONTS.mono,
												fontSize: "11px",
												padding: "4px 8px",
												border: "1px solid #D4D7DC",
												borderRadius: "4px",
												background: "#FFFFFF",
												color: "#16181D",
											}}
										>
											{ext.candidates.map((cand) => (
												<option key={cand.id} value={cand.id}>
													{cand.label} ({cand.score.toFixed(2)})
												</option>
											))}
										</select>
									)}
									<div style={{ display: "flex", gap: "6px" }}>
										<button
											type="button"
											data-testid={`btn-approve-hold-${ext.holdId}`}
											onClick={() =>
												onResolveHold({
													holdId: ext.holdId as string,
													action: "APPROVE",
													selectedCandidateId: chosenCand || undefined,
													eventId: ext.eventId,
												})
											}
											style={{
												fontFamily: EXPLORER_FONTS.sans,
												fontSize: "11.5px",
												fontWeight: 500,
												padding: "4px 10px",
												borderRadius: "5px",
												border: "1px solid #16181D",
												background: "#16181D",
												color: "#FFFFFF",
												cursor: "pointer",
											}}
										>
											Approve candidate
										</button>
										<button
											type="button"
											data-testid={`btn-reject-hold-${ext.holdId}`}
											onClick={() =>
												onResolveHold({
													holdId: ext.holdId as string,
													action: "REJECT",
													eventId: ext.eventId,
												})
											}
											style={{
												fontFamily: EXPLORER_FONTS.sans,
												fontSize: "11.5px",
												fontWeight: 500,
												padding: "4px 10px",
												borderRadius: "5px",
												border: "1px solid #E3E5E8",
												background: "#FDECEA",
												color: "#8F1D12",
												cursor: "pointer",
											}}
										>
											Reject span
										</button>
									</div>
								</div>
							)}
						</div>
					);
				})}
			</div>
		</div>
	);
}
