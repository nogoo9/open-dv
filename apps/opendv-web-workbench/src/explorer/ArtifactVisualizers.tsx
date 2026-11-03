/**
 * 8 Visual Renderers (`ontology`, `dv`, `table`, `kv`, `checks`, `events`, `doc`, `code`)
 * for the Open-DV 3-Level Pipeline Explorer (`docs/design/open-dv-workbench/HANDOFF.md`).
 */

import type { Artifact, DocExtractionSpec } from "@opendv/protocol-ts";
import { useState } from "react";
import {
	EXPLORER_COLORS,
	EXPLORER_FONTS,
	getStatusTagStyle,
} from "./design-tokens";

export interface ArtifactVisualizerProps {
	artifact: Artifact;
	onResolveHold?: (params: {
		holdId: string;
		action: "APPROVE" | "REJECT";
		selectedCandidateId?: string;
		resolutionNotes?: string;
		eventId?: string;
	}) => void;
}

function buildDocSegments(
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

export function ArtifactVisualizer({
	artifact,
	onResolveHold,
}: ArtifactVisualizerProps) {
	const [selectedCandidates, setSelectedCandidates] = useState<
		Record<string, string>
	>({});

	// 1. viz === 'ontology'
	if (artifact.viz === "ontology" && artifact.ontology) {
		const { concepts, nodes, edges } = artifact.ontology;
		return (
			<div
				data-testid="viz-ontology"
				style={{ display: "flex", flexDirection: "column", gap: "16px" }}
			>
				<svg
					viewBox="0 0 640 245"
					role="img"
					aria-label="Graph A Ontology Concept Diagram"
					style={{
						width: "100%",
						height: "auto",
						background: "#FAFBFD",
						border: "1px solid #E3E5E8",
						borderRadius: "6px",
					}}
				>
					<title>Graph A Ontology Concept Diagram</title>
					<g stroke="#9CA3AF" strokeWidth="1.2" fill="none">
						{edges.map((edge, idx) => (
							<path
								key={`edge-${idx}-${edge.path}`}
								d={edge.path}
								strokeDasharray="3 2"
							/>
						))}
					</g>
					<g
						fontFamily="IBM Plex Mono, monospace"
						fontSize="9.5"
						fill="#545A66"
					>
						{edges.map((edge, idx) =>
							edge.label ? (
								<text
									key={`edge-lbl-${idx}-${edge.label}`}
									x={edge.lx ?? 0}
									y={edge.ly ?? 0}
									textAnchor={
										(edge.lx ?? 0) > 150 && (edge.ly ?? 0) < 60
											? "middle"
											: undefined
									}
								>
									{edge.label}
								</text>
							) : null,
						)}
					</g>
					<g fontFamily="IBM Plex Mono, monospace">
						{nodes.map((node) => (
							<g key={node.id}>
								<rect
									x={node.x}
									y={node.y}
									width={node.w}
									height={node.h ?? 56}
									rx="5"
									fill="#FFFFFF"
									stroke="#16181D"
									strokeWidth="1.4"
								/>
								<text
									x={node.x + 12}
									y={node.y + 22}
									fontSize="11.5"
									fontWeight="600"
									fill="#16181D"
								>
									{node.label}
								</text>
								<text
									x={node.x + 12}
									y={node.y + 40}
									fontSize="10"
									fill="#545A66"
								>
									{node.sub}
								</text>
							</g>
						))}
					</g>
				</svg>

				<div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
					{concepts.map((c) => (
						<div
							key={c.name}
							data-testid={`ontology-concept-${c.name}`}
							style={{
								padding: "10px 12px",
								border: "1px solid #E3E5E8",
								borderRadius: "6px",
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
										fontFamily: EXPLORER_FONTS.mono,
										fontSize: "12.5px",
										fontWeight: 600,
										color: "#16181D",
									}}
								>
									{c.name}
								</span>
								<span
									style={{
										fontFamily: EXPLORER_FONTS.mono,
										fontSize: "10.5px",
										padding: "1px 7px",
										borderRadius: "999px",
										background: "#EEF0F2",
										color: "#3B414C",
									}}
								>
									{c.arch}
								</span>
							</div>
							<div
								style={{
									fontSize: "12px",
									color: "#3B414C",
									marginTop: "4px",
								}}
							>
								{c.def}
							</div>
							<div
								style={{
									fontFamily: EXPLORER_FONTS.mono,
									fontSize: "11px",
									color: "#6B7280",
									marginTop: "4px",
								}}
							>
								{c.keys}
							</div>
						</div>
					))}
				</div>
			</div>
		);
	}

	// 2. viz === 'dv'
	if (artifact.viz === "dv") {
		const dvSpec = artifact.dv;
		return (
			<div
				data-testid="viz-dv"
				style={{ display: "flex", flexDirection: "column", gap: "14px" }}
			>
				<svg
					viewBox="0 0 660 285"
					role="img"
					aria-label="Data Vault 2.0 Topology Diagram"
					style={{
						width: "100%",
						height: "auto",
						background: "#FAFBFD",
						border: "1px solid #E3E5E8",
						borderRadius: "6px",
					}}
				>
					<title>Data Vault 2.0 Topology Diagram</title>
					<g stroke="#9CA3AF" strokeWidth="1.2" fill="none">
						{(dvSpec?.edges ?? []).map((edge, idx) => (
							<line
								key={`dv-edge-${idx}`}
								x1={edge.x1}
								y1={edge.y1}
								x2={edge.x2}
								y2={edge.y2}
								strokeDasharray={edge.dashed ? "3 2" : undefined}
							/>
						))}
					</g>
					<g fontFamily="IBM Plex Mono, monospace">
						{(dvSpec?.nodes ?? []).map((node) => {
							if (node.kind === "hub") {
								return (
									<g key={node.id}>
										<rect
											x={node.x}
											y={node.y}
											width={node.w}
											height={node.h}
											rx="5"
											fill="#1D4ED8"
										/>
										<text
											x={node.x + 12}
											y={node.y + 19}
											fontSize="11"
											fontWeight="600"
											fill="#FFFFFF"
										>
											{node.label}
										</text>
										<text
											x={node.x + 12}
											y={node.y + 35}
											fontSize="9.5"
											fill="#DBEAFE"
										>
											{node.sub}
										</text>
									</g>
								);
							}
							if (node.kind === "link") {
								return (
									<g key={node.id}>
										<rect
											x={node.x}
											y={node.y}
											width={node.w}
											height={node.h}
											rx="18"
											fill="#FFF4E5"
											stroke="#B45309"
											strokeWidth="1.3"
										/>
										<text
											x={node.x + node.w / 2}
											y={node.y + 15}
											textAnchor="middle"
											fontSize="10.5"
											fontWeight="600"
											fill="#8A3B07"
										>
											{node.label}
										</text>
										<text
											x={node.x + node.w / 2}
											y={node.y + 28}
											textAnchor="middle"
											fontSize="9"
											fill="#8A3B07"
										>
											{node.sub}
										</text>
									</g>
								);
							}
							const isPii = node.kind === "sat_pii";
							return (
								<g key={node.id}>
									<rect
										x={node.x}
										y={node.y}
										width={node.w}
										height={node.h}
										rx="4"
										fill="#F2F3F5"
										stroke={isPii ? "#B45309" : "#D4D7DC"}
										strokeDasharray={isPii ? "3 2" : undefined}
									/>
									<text
										x={node.x + 8}
										y={node.y + 14}
										fontSize="9.5"
										fontWeight="600"
										fill={isPii ? "#8A3B07" : "#16181D"}
									>
										{node.label}
									</text>
									<text
										x={node.x + 8}
										y={node.y + 26}
										fontSize="8.5"
										fill={isPii ? "#8A3B07" : "#545A66"}
									>
										{node.sub}
									</text>
								</g>
							);
						})}
					</g>
				</svg>

				<div
					style={{
						display: "flex",
						flexWrap: "wrap",
						gap: "16px",
						fontSize: "11.5px",
						color: "#545A66",
					}}
				>
					<span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
						<span
							style={{
								width: "12px",
								height: "12px",
								borderRadius: "3px",
								background: "#1D4ED8",
							}}
						/>
						Hub (business key)
					</span>
					<span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
						<span
							style={{
								width: "12px",
								height: "12px",
								borderRadius: "999px",
								background: "#FFF4E5",
								border: "1.2px solid #B45309",
							}}
						/>
						Link (relationship)
					</span>
					<span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
						<span
							style={{
								width: "12px",
								height: "12px",
								borderRadius: "3px",
								background: "#F2F3F5",
								border: "1px solid #D4D7DC",
							}}
						/>
						Satellite (attributes)
					</span>
					<span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
						<span
							style={{
								width: "12px",
								height: "12px",
								borderRadius: "3px",
								background: "#F2F3F5",
								border: "1px dashed #B45309",
							}}
						/>
						Satellite (RESTRICTED_PII)
					</span>
				</div>
			</div>
		);
	}

	// 3. viz === 'table'
	if (artifact.viz === "table" && artifact.cols && artifact.rows) {
		const cols = artifact.cols;
		const rows = artifact.rows;
		const gridCols = cols
			.map((col, ci) => {
				let maxLen = col.length;
				for (const row of rows) {
					maxLen = Math.max(maxLen, String(row[ci] ?? "").length);
				}
				return `${Math.max(72, Math.ceil(maxLen * 7.4) + 28)}px`;
			})
			.join(" ");

		return (
			<div
				data-testid="viz-table"
				style={{ display: "flex", flexDirection: "column", gap: "10px" }}
			>
				<div
					role="table"
					aria-label={artifact.name}
					style={{
						overflowX: "auto",
						border: "1px solid #E3E5E8",
						borderRadius: "6px",
					}}
				>
					<div
						role="row"
						style={{
							display: "grid",
							gridTemplateColumns: gridCols,
							background: "#F6F7F8",
							borderBottom: "1px solid #E3E5E8",
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "11px",
							fontWeight: 600,
							color: "#3B414C",
						}}
					>
						{cols.map((c) => (
							<div
								key={c}
								role="columnheader"
								style={{
									padding: "8px 10px",
									borderRight: "1px solid #ECEEF1",
									whiteSpace: "nowrap",
									overflow: "hidden",
									textOverflow: "ellipsis",
								}}
							>
								{c}
							</div>
						))}
					</div>
					{rows.map((row, ri) => (
						<div
							key={`row-${ri}`}
							role="row"
							style={{
								display: "grid",
								gridTemplateColumns: gridCols,
								borderBottom: "1px solid #F2F3F5",
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "11.5px",
							}}
						>
							{row.map((cell, ci) => (
								<div
									key={`cell-${ri}-${ci}`}
									role="cell"
									style={{
										padding: "7px 10px",
										borderRight: "1px solid #F6F7F8",
										whiteSpace: "nowrap",
										overflow: "hidden",
										textOverflow: "ellipsis",
										color: cell === "‹PII›" ? "#8A3B07" : "#16181D",
									}}
								>
									{cell}
								</div>
							))}
						</div>
					))}
				</div>
				{artifact.foot && (
					<div style={{ fontSize: "11.5px", color: "#545A66" }}>
						{artifact.foot}
					</div>
				)}
			</div>
		);
	}

	// 4. viz === 'kv'
	if (artifact.viz === "kv" && artifact.kv) {
		return (
			<dl
				data-testid="viz-kv"
				style={{
					margin: 0,
					border: "1px solid #E3E5E8",
					borderRadius: "6px",
					overflow: "hidden",
				}}
			>
				{artifact.kv.map(([k, v]) => (
					<div
						key={k}
						style={{
							display: "grid",
							gridTemplateColumns: "180px 1fr",
							gap: "12px",
							padding: "9px 12px",
							borderBottom: "1px solid #F2F3F5",
							fontSize: "12px",
						}}
					>
						<dt
							style={{
								fontFamily: EXPLORER_FONTS.mono,
								color: "#545A66",
							}}
						>
							{k}
						</dt>
						<dd
							style={{
								margin: 0,
								fontFamily: EXPLORER_FONTS.mono,
								color: "#16181D",
								fontWeight: 500,
							}}
						>
							{v}
						</dd>
					</div>
				))}
			</dl>
		);
	}

	// 5. viz === 'checks'
	if (artifact.viz === "checks" && artifact.checks) {
		return (
			<div
				data-testid="viz-checks"
				style={{ display: "flex", flexDirection: "column", gap: "8px" }}
			>
				{artifact.checks.map(([id, name, status]) => {
					const tagStyle = getStatusTagStyle(status);
					return (
						<div
							key={id}
							style={{
								display: "flex",
								alignItems: "center",
								justifyContent: "space-between",
								gap: "12px",
								padding: "10px 12px",
								border: "1px solid #E3E5E8",
								borderRadius: "6px",
							}}
						>
							<div
								style={{
									display: "flex",
									alignItems: "center",
									gap: "10px",
									minWidth: 0,
								}}
							>
								<span
									style={{
										fontFamily: EXPLORER_FONTS.mono,
										fontSize: "11.5px",
										fontWeight: 600,
										color: "#16181D",
									}}
								>
									{id}
								</span>
								<span style={{ fontSize: "12.5px", color: "#3B414C" }}>
									{name}
								</span>
							</div>
							<span
								style={{
									fontFamily: EXPLORER_FONTS.mono,
									fontSize: "10.5px",
									fontWeight: 500,
									padding: "2px 8px",
									borderRadius: "999px",
									flexShrink: 0,
									background: tagStyle.bg,
									color: tagStyle.fg,
								}}
							>
								{status}
							</span>
						</div>
					);
				})}
			</div>
		);
	}

	// 6. viz === 'events'
	if (artifact.viz === "events" && artifact.events) {
		return (
			<div
				data-testid="viz-events"
				style={{
					position: "relative",
					paddingLeft: "18px",
					display: "flex",
					flexDirection: "column",
					gap: "8px",
				}}
			>
				<div
					style={{
						position: "absolute",
						left: "5px",
						top: "8px",
						bottom: "8px",
						width: "1px",
						background: "#D4D7DC",
					}}
				/>
				{artifact.events.map(([stage, msg, status], idx) => {
					const tagStyle = getStatusTagStyle(status);
					const dotColor = status === "PASS" ? "#16181D" : "#B45309";
					return (
						<div
							key={`evt-${idx}-${stage}`}
							style={{
								position: "relative",
								padding: "9px 12px",
								border: "1px solid #E3E5E8",
								borderRadius: "6px",
								background: "#FFFFFF",
							}}
						>
							<span
								style={{
									position: "absolute",
									left: "-16px",
									top: "14px",
									width: "7px",
									height: "7px",
									borderRadius: "999px",
									background: dotColor,
								}}
							/>
							<div
								style={{
									display: "flex",
									alignItems: "center",
									justifyContent: "space-between",
									gap: "8px",
								}}
							>
								<div
									style={{
										display: "flex",
										alignItems: "center",
										gap: "8px",
									}}
								>
									<span
										style={{
											fontFamily: EXPLORER_FONTS.mono,
											fontSize: "10.5px",
											color: "#6B7280",
										}}
									>
										#{idx + 1}
									</span>
									<span
										style={{
											fontFamily: EXPLORER_FONTS.mono,
											fontSize: "11px",
											fontWeight: 600,
											color: "#16181D",
										}}
									>
										{stage}
									</span>
								</div>
								<span
									style={{
										fontFamily: EXPLORER_FONTS.mono,
										fontSize: "10px",
										padding: "1px 7px",
										borderRadius: "999px",
										background: tagStyle.bg,
										color: tagStyle.fg,
									}}
								>
									{status}
								</span>
							</div>
							<div
								style={{
									fontSize: "12px",
									color: "#3B414C",
									marginTop: "3px",
								}}
							>
								{msg}
							</div>
						</div>
					);
				})}
			</div>
		);
	}

	// 7. viz === 'doc'
	if (artifact.viz === "doc" && artifact.doc) {
		const { sourceText, extractions } = artifact.doc;
		const segments = buildDocSegments(sourceText, extractions);

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
							"TAX-DE-800202";

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
														selectedCandidateId: chosenCand,
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

	// 8. viz === 'code'
	if (artifact.viz === "code") {
		return (
			<div
				data-testid="viz-code"
				style={{
					display: "grid",
					gridTemplateColumns: "220px 1fr",
					gap: "12px",
					alignItems: "start",
				}}
			>
				<div
					style={{
						border: "1px solid #E3E5E8",
						borderRadius: "6px",
						overflow: "hidden",
					}}
				>
					<div
						style={{
							padding: "8px 10px",
							background: "#F6F7F8",
							borderBottom: "1px solid #E3E5E8",
							fontSize: "11px",
							fontWeight: 600,
							color: "#545A66",
							textTransform: "uppercase",
							letterSpacing: "0.06em",
						}}
					>
						Outline
					</div>
					{(artifact.outline ?? []).map(([k, v]) => (
						<div
							key={k}
							style={{
								padding: "8px 10px",
								borderBottom: "1px solid #F2F3F5",
							}}
						>
							<div
								style={{
									fontFamily: EXPLORER_FONTS.mono,
									fontSize: "11.5px",
									fontWeight: 600,
									color: "#16181D",
								}}
							>
								{k}
							</div>
							<div
								style={{
									fontSize: "11px",
									color: "#545A66",
									marginTop: "2px",
								}}
							>
								{v}
							</div>
						</div>
					))}
				</div>
				<pre
					style={{
						margin: 0,
						padding: "12px 14px",
						background: "#16181D",
						color: "#E6E8EB",
						borderRadius: "6px",
						fontFamily: EXPLORER_FONTS.mono,
						fontSize: "11.5px",
						lineHeight: 1.55,
						overflow: "auto",
					}}
				>
					{artifact.raw}
				</pre>
			</div>
		);
	}

	return null;
}
