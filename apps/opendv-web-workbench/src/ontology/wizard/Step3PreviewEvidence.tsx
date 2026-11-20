/**
 * Step 3: Preview Evidence — 3a Structured Profiles (<= 50 rows) & 3b Parsed Chunks (`D4`, `D6`, `D8`)
 * (apps/opendv-web-workbench/src/ontology/wizard/Step3PreviewEvidence.tsx)
 */

import type React from "react";
import { formatForeignKeyRef } from "../../explorer/builder/CatalogTablePickerModal";
import { CLAUDE_TOKENS, EXPLORER_FONTS } from "../../explorer/design-tokens";
import { safeUseState } from "../../explorer/modals/safeHooks";
import type { EvidenceChunk, EvidenceTableProfile } from "../store/types";

const MAX_SAMPLE_PREVIEW_ROWS = 50;

export interface Step3PreviewEvidenceProps {
	sessionId?: string | null | undefined;
	activeSubTab?: "structured" | "chunks" | undefined;
	tables?: EvidenceTableProfile[] | undefined;
	chunks?: EvidenceChunk[] | undefined;
	observedTermCounts?: Record<string, number> | undefined;
	highlightChunkId?: string | null | undefined;
	onFastForwardToStep6?: (() => void) | undefined;
}

export const Step3PreviewEvidence: React.FC<Step3PreviewEvidenceProps> = ({
	sessionId,
	activeSubTab: initialSubTab = "structured",
	tables = [],
	chunks = [],
	observedTermCounts = {},
	highlightChunkId,
	onFastForwardToStep6,
}) => {
	const [subTab, setSubTab] = safeUseState<"structured" | "chunks">(
		initialSubTab,
	);
	const observedTerms = Object.entries(observedTermCounts);

	return (
		<section
			data-testid="wizard-step-3"
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
						Step 3 · Preview Evidence &amp; Schema Profiles
					</h1>
					<p
						style={{
							margin: 0,
							fontSize: "14px",
							color: CLAUDE_TOKENS.colors.body,
						}}
					>
						Inspect profiled columns, sample rows (capped at{" "}
						{MAX_SAMPLE_PREVIEW_ROWS}), parsed chunks, and all observed domain
						terms
						{sessionId ? ` · ${sessionId}` : ""}.
					</p>
				</div>
				{onFastForwardToStep6 && (
					<button
						type="button"
						className="claude-btn-secondary"
						data-testid="btn-fast-forward-step6"
						onClick={onFastForwardToStep6}
					>
						Fast-Forward to Step 6 →
					</button>
				)}
			</header>

			<div
				style={{ display: "flex", gap: "8px" }}
				role="tablist"
				aria-label="Evidence Preview Tabs"
			>
				<button
					type="button"
					role="tab"
					aria-selected={subTab === "structured"}
					className={
						subTab === "structured"
							? "claude-btn-primary"
							: "claude-btn-secondary"
					}
					data-testid="tab-step3-structured"
					onClick={() => setSubTab("structured")}
				>
					3a · Structured Tables ({tables.length})
				</button>
				<button
					type="button"
					role="tab"
					aria-selected={subTab === "chunks"}
					className={
						subTab === "chunks" ? "claude-btn-primary" : "claude-btn-secondary"
					}
					data-testid="tab-step3-chunks"
					onClick={() => setSubTab("chunks")}
				>
					3b · Parsed Chunks ({chunks.length})
				</button>
			</div>

			{subTab === "structured" ? (
				<div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
					{tables.length === 0 ? (
						<div
							className="claude-card-cream"
							style={{ padding: "16px", fontSize: "13px" }}
						>
							No profiled tables available yet. Attach a database or tabular
							file in Step 2.
						</div>
					) : (
						tables.map((tbl) => {
							const cappedRows = (tbl.sample_rows || []).slice(
								0,
								MAX_SAMPLE_PREVIEW_ROWS,
							);
							return (
								<div
									key={tbl.table_id}
									className="claude-card"
									style={{
										padding: "16px",
										display: "flex",
										flexDirection: "column",
										gap: "12px",
									}}
								>
									<div
										style={{
											display: "flex",
											justifyContent: "space-between",
											alignItems: "center",
										}}
									>
										<h2 className="claude-h2" style={{ margin: 0 }}>
											{tbl.table_name}
										</h2>
										<span className="claude-pill" style={{ fontSize: "11px" }}>
											Showing {cappedRows.length} / max{" "}
											{MAX_SAMPLE_PREVIEW_ROWS} rows
										</span>
									</div>

									<div style={{ overflowX: "auto", minWidth: 0 }}>
										<table
											style={{
												width: "100%",
												borderCollapse: "collapse",
												fontSize: "12.5px",
											}}
										>
											<thead>
												<tr
													style={{
														textAlign: "left",
														borderBottom: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
													}}
												>
													<th style={{ padding: "6px 8px" }}>Column</th>
													<th style={{ padding: "6px 8px" }}>Type</th>
													<th style={{ padding: "6px 8px" }}>Keys / Hints</th>
													<th style={{ padding: "6px 8px" }}>Null %</th>
													<th style={{ padding: "6px 8px" }}>Entropy</th>
												</tr>
											</thead>
											<tbody>
												{tbl.columns.map((col) => {
													const fkRef = formatForeignKeyRef(col.foreign_key);
													return (
														<tr
															key={col.column_name}
															style={{
																borderBottom: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
															}}
														>
															<td
																style={{
																	padding: "6px 8px",
																	fontFamily: EXPLORER_FONTS.mono,
																}}
															>
																{col.column_name}
															</td>
															<td
																style={{
																	padding: "6px 8px",
																	fontFamily: EXPLORER_FONTS.mono,
																}}
															>
																{col.sql_type}
															</td>
															<td
																style={{
																	padding: "6px 8px",
																	display: "flex",
																	gap: "4px",
																	flexWrap: "wrap",
																}}
															>
																{col.is_primary_key && (
																	<span className="claude-pill">PK</span>
																)}
																{fkRef && (
																	<span className="claude-pill">
																		FK → {fkRef}
																	</span>
																)}
																{col.surrogate_hint && (
																	<span className="claude-pill">surrogate</span>
																)}
															</td>
															<td
																style={{
																	padding: "6px 8px",
																	fontFamily: EXPLORER_FONTS.mono,
																}}
															>
																{(Number(col.null_rate ?? 0) * 100).toFixed(1)}%
															</td>
															<td
																style={{
																	padding: "6px 8px",
																	fontFamily: EXPLORER_FONTS.mono,
																}}
															>
																{Number(col.shannon_entropy ?? 0).toFixed(2)}
															</td>
														</tr>
													);
												})}
											</tbody>
										</table>
									</div>

									{tbl.fibo_gap_hints && tbl.fibo_gap_hints.length > 0 && (
										<div
											className="claude-card-cream"
											style={{ padding: "10px 12px", fontSize: "12px" }}
										>
											<strong>FIBO Gap Hints:</strong>{" "}
											{tbl.fibo_gap_hints
												.map((h) => {
													const col = String(h.column_name ?? "");
													const msg = h.recommendation ?? h.hint;
													const text =
														msg == null
															? ""
															: typeof msg === "string"
																? msg
																: JSON.stringify(msg);
													return text ? `${col}: ${text}` : col;
												})
												.filter(Boolean)
												.join(" · ")}
										</div>
									)}

									{cappedRows.length > 0 && (
										<div style={{ overflowX: "auto", minWidth: 0 }}>
											<table
												style={{
													width: "100%",
													borderCollapse: "collapse",
													fontSize: "12px",
													fontFamily: EXPLORER_FONTS.mono,
												}}
											>
												<thead>
													<tr
														style={{
															background: CLAUDE_TOKENS.colors.surfaceSoft,
														}}
													>
														{tbl.headers.map((h) => (
															<th
																key={h}
																style={{
																	padding: "5px 8px",
																	textAlign: "left",
																}}
															>
																{h}
															</th>
														))}
													</tr>
												</thead>
												<tbody>
													{(() => {
														const seenRows = new Map<string, number>();
														return cappedRows.map((row) => {
															const rSig = row.join("\x1f");
															const rOcc = (seenRows.get(rSig) ?? 0) + 1;
															seenRows.set(rSig, rOcc);
															const rowKey = `${rSig}#${rOcc}`;
															const seenCells = new Map<string, number>();
															return (
																<tr
																	key={rowKey}
																	style={{
																		borderTop: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
																	}}
																>
																	{row.map((cell) => {
																		const cOcc = (seenCells.get(cell) ?? 0) + 1;
																		seenCells.set(cell, cOcc);
																		return (
																			<td
																				key={`${rowKey}:${cell}#${cOcc}`}
																				style={{ padding: "4px 8px" }}
																			>
																				{cell}
																			</td>
																		);
																	})}
																</tr>
															);
														});
													})()}
												</tbody>
											</table>
										</div>
									)}
								</div>
							);
						})
					)}
				</div>
			) : (
				<div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
					{observedTerms.length > 0 && (
						<div className="claude-card-cream" style={{ padding: "14px" }}>
							<div
								style={{
									fontSize: "12px",
									fontWeight: 600,
									marginBottom: "8px",
									color: CLAUDE_TOKENS.colors.ink,
								}}
							>
								Observed Domain Terms (Zero Frequency Floor)
							</div>
							<div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
								{observedTerms.map(([term, count]) => (
									<span
										key={term}
										className="claude-pill"
										style={{ fontSize: "11.5px" }}
									>
										{term} · <strong>{count}x</strong>
									</span>
								))}
							</div>
						</div>
					)}

					{chunks.length === 0 ? (
						<div
							className="claude-card-cream"
							style={{ padding: "16px", fontSize: "13px" }}
						>
							No parsed document chunks yet. Run document parsing in Step 2.
						</div>
					) : (
						chunks.map((chk) => {
							const highlighted = highlightChunkId === chk.chunk_id;
							return (
								<article
									key={chk.chunk_id}
									className={
										highlighted ? "claude-card-selected" : "claude-card"
									}
									style={{
										padding: "14px",
										display: "flex",
										flexDirection: "column",
										gap: "8px",
									}}
								>
									<div
										style={{
											display: "flex",
											justifyContent: "space-between",
											alignItems: "center",
											flexWrap: "wrap",
											gap: "8px",
											fontSize: "12px",
										}}
									>
										<div
											style={{
												display: "flex",
												gap: "8px",
												alignItems: "center",
											}}
										>
											<span className="claude-pill" style={{ fontWeight: 600 }}>
												{chk.is_table ? "Table" : "PROSE"}
											</span>
											<strong>{chk.document_id}</strong>
											{chk.page_number != null && (
												<span>p.{chk.page_number}</span>
											)}
											<span
												style={{
													fontFamily: EXPLORER_FONTS.mono,
													color: CLAUDE_TOKENS.colors.muted,
												}}
											>
												{chk.start_char}–{chk.end_char}
											</span>
										</div>
										<span style={{ fontFamily: EXPLORER_FONTS.mono }}>
											{chk.confidence != null
												? `${(chk.confidence * 100).toFixed(0)}% conf`
												: ""}
										</span>
									</div>
									<p
										style={{
											margin: 0,
											fontSize: "13px",
											lineHeight: 1.55,
											color: CLAUDE_TOKENS.colors.ink,
										}}
									>
										{chk.text}
									</p>
									{chk.table_data && (
										<div style={{ overflowX: "auto", minWidth: 0 }}>
											<table
												style={{
													width: "100%",
													borderCollapse: "collapse",
													fontSize: "12px",
													fontFamily: EXPLORER_FONTS.mono,
												}}
											>
												<thead>
													<tr>
														{chk.table_data.headers.map((h) => (
															<th
																key={h}
																style={{
																	padding: "4px 6px",
																	textAlign: "left",
																	borderBottom: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
																}}
															>
																{h}
															</th>
														))}
													</tr>
												</thead>
												<tbody>
													{(() => {
														const seenRows = new Map<string, number>();
														return chk.table_data.rows.map((row) => {
															const rSig = row.join("\x1f");
															const rOcc = (seenRows.get(rSig) ?? 0) + 1;
															seenRows.set(rSig, rOcc);
															const rowKey = `${rSig}#${rOcc}`;
															const seenCells = new Map<string, number>();
															return (
																<tr key={rowKey}>
																	{row.map((cell) => {
																		const cOcc = (seenCells.get(cell) ?? 0) + 1;
																		seenCells.set(cell, cOcc);
																		return (
																			<td
																				key={`${rowKey}:${cell}#${cOcc}`}
																				style={{ padding: "4px 6px" }}
																			>
																				{cell}
																			</td>
																		);
																	})}
																</tr>
															);
														});
													})()}
												</tbody>
											</table>
										</div>
									)}
								</article>
							);
						})
					)}
				</div>
			)}
		</section>
	);
};
