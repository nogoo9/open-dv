/**
 * Step 2: Structured Connectors, Unstructured Document Upload & Inline Notes (`FR-ING-02..03`)
 * (apps/opendv-web-workbench/src/ontology/wizard/Step2SourcesAndNotes.tsx)
 */

import type { SourceFeedDTO } from "@opendv/protocol-ts";
import type React from "react";
import { CatalogTablePickerModal } from "../../explorer/builder/CatalogTablePickerModal";
import { CLAUDE_TOKENS, EXPLORER_FONTS } from "../../explorer/design-tokens";
import { safeUseState } from "../../explorer/modals/safeHooks";
import type { ConnectorSourceInput, SourceDocument } from "../store/types";

export interface WizardAttachedSourceItem extends SourceDocument {
	status_badge?: string | undefined;
}

export interface Step2SourcesAndNotesProps {
	sessionId?: string | null | undefined;
	apiUrl?: string | undefined;
	fetchImpl?: typeof fetch | undefined;
	ocrMode?: "auto" | "force" | "off" | undefined;
	doNotSendToAgent?: boolean | undefined;
	attachedSources?: WizardAttachedSourceItem[] | undefined;
	activeSessionJobId?: string | null | undefined;
	onOcrModeChange?: ((mode: "auto" | "force" | "off") => void) | undefined;
	onDoNotSendChange?: ((val: boolean) => void) | undefined;
	onAttachConnector?:
		| ((req: ConnectorSourceInput) => Promise<void> | void)
		| undefined;
	onUploadFile?:
		| ((
				file: File,
				opts: { ocrMode: "auto" | "force" | "off"; doNotSend: boolean },
		  ) => Promise<void> | void)
		| undefined;
	onUploadStructuredFile?:
		| ((file: File, opts: { doNotSend: boolean }) => Promise<void> | void)
		| undefined;
	onAddInlineNote?:
		| ((text: string, opts: { doNotSend: boolean }) => Promise<void> | void)
		| undefined;
	onParseSources?:
		| ((ocrMode: "auto" | "force" | "off") => Promise<void> | void)
		| undefined;
	onCancelJob?: (() => Promise<void> | void) | undefined;
	onOpenLogs?:
		| ((opts?: { stageId?: string; wizardStep?: number }) => void)
		| undefined;
}

const CONNECTOR_TILES = [
	{
		key: "sqlite",
		sourceType: "sqlite" as const,
		title: "SQLite",
		desc: "Local database tables & schemas",
	},
	{
		key: "databricks",
		sourceType: "databricks" as const,
		title: "Databricks UC",
		desc: "Unity Catalog tables & lineage",
	},
	{
		key: "duckdb",
		sourceType: "duckdb" as const,
		title: "DuckDB",
		desc: "Analytical DuckDB tables & views",
	},
	{
		key: "files",
		sourceType: "file" as const,
		title: "Parquet · CSV · XLSX · JSON",
		desc: "Tabular files & structured feeds",
	},
];

export const Step2SourcesAndNotes: React.FC<Step2SourcesAndNotesProps> = ({
	sessionId,
	apiUrl,
	fetchImpl,
	ocrMode: initialOcrMode = "auto",
	doNotSendToAgent: initialDoNotSend = false,
	attachedSources = [],
	activeSessionJobId,
	onOcrModeChange,
	onDoNotSendChange,
	onAttachConnector,
	onUploadFile,
	onUploadStructuredFile,
	onAddInlineNote,
	onParseSources,
	onCancelJob,
	onOpenLogs,
}) => {
	const [pickerSourceType, setPickerSourceType] = safeUseState<
		"sqlite" | "databricks" | "duckdb" | "file" | null
	>(null);
	const [ocrMode, setOcrMode] = safeUseState<"auto" | "force" | "off">(
		initialOcrMode,
	);
	const [doNotSend, setDoNotSend] = safeUseState<boolean>(initialDoNotSend);
	const [noteText, setNoteText] = safeUseState("");

	const handlePickerApply = async (feed: SourceFeedDTO) => {
		setPickerSourceType(null);
		if (!onAttachConnector) return;
		const ns = feed.namespace;
		const hasNs = Boolean(ns && (ns.catalog || ns.schema_name));
		const tables = feed.table_selection?.tables ?? [];
		const baseReq: ConnectorSourceInput = {
			adapter: feed.adapter || "sqlite",
			...(feed.uri ? { uri: feed.uri } : {}),
			...(hasNs && ns
				? {
						namespace: {
							...(ns.catalog ? { catalog: ns.catalog } : {}),
							...(ns.schema_name ? { schema_name: ns.schema_name } : {}),
						},
					}
				: {}),
			...(feed.source_system ? { source_name: feed.source_system } : {}),
			...(feed.credentials_ref
				? { credentials_ref: feed.credentials_ref }
				: {}),
			...(feed.options ? { options: feed.options } : {}),
		};
		if (tables.length > 0) {
			for (const tbl of tables) {
				await onAttachConnector({
					...baseReq,
					table: tbl,
					options: { ...feed.options, selected_tables: tables },
				});
			}
		} else {
			await onAttachConnector(baseReq);
		}
	};

	const handleStructuredFileInput = async (
		e: React.ChangeEvent<HTMLInputElement>,
	) => {
		const file = e.target.files?.[0];
		if (!file) return;
		if (onUploadStructuredFile) {
			await onUploadStructuredFile(file, { doNotSend });
		} else if (onUploadFile) {
			await onUploadFile(file, { ocrMode, doNotSend });
		}
	};

	const handleFileInput = async (e: React.ChangeEvent<HTMLInputElement>) => {
		const file = e.target.files?.[0];
		if (!file || !onUploadFile) return;
		await onUploadFile(file, { ocrMode, doNotSend });
	};

	const handleSaveNote = async () => {
		const trimmed = noteText.trim();
		if (!trimmed || !onAddInlineNote) return;
		await onAddInlineNote(trimmed, { doNotSend });
		setNoteText("");
	};

	return (
		<section
			data-testid="wizard-step-2"
			style={{ display: "flex", flexDirection: "column", gap: "20px" }}
		>
			<header
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "flex-start",
					gap: "12px",
				}}
			>
				<div>
					<h1 className="claude-h1" style={{ margin: "0 0 6px" }}>
						Step 2 · Sources, Connectors &amp; Domain Notes
					</h1>
					<p
						style={{
							margin: 0,
							fontSize: "14px",
							color: CLAUDE_TOKENS.colors.body,
						}}
					>
						Connect structured databases or tabular files, upload policy PDFs
						with OCR, and record inline domain notes.
					</p>
				</div>
				<button
					type="button"
					className="claude-btn-secondary"
					data-testid="btn-step2-view-logs"
					onClick={() => onOpenLogs?.({ wizardStep: 2 })}
				>
					View Stage Logs ↗
				</button>
			</header>

			<div>
				<h2 className="claude-h2" style={{ margin: "0 0 10px" }}>
					Structured Connectors
				</h2>
				<div
					className="claude-responsive-grid"
					style={{
						display: "grid",
						gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
						gap: "12px",
					}}
				>
					{CONNECTOR_TILES.map((tile) => (
						<button
							key={tile.key}
							type="button"
							data-testid={`connector-tile-${tile.key}`}
							className="claude-card-cream"
							onClick={() => setPickerSourceType(tile.sourceType)}
							style={{
								minWidth: 0,
								padding: "14px",
								textAlign: "left",
								cursor: "pointer",
								display: "flex",
								flexDirection: "column",
								gap: "4px",
							}}
						>
							<strong
								style={{ fontSize: "13.5px", color: CLAUDE_TOKENS.colors.ink }}
							>
								{tile.title}
							</strong>
							<span
								style={{ fontSize: "12px", color: CLAUDE_TOKENS.colors.muted }}
							>
								{tile.desc}
							</span>
						</button>
					))}
				</div>
				<div
					className="claude-card-cream"
					style={{
						marginTop: "10px",
						padding: "10px 14px",
						display: "flex",
						alignItems: "center",
						justifyContent: "space-between",
						flexWrap: "wrap",
						gap: "10px",
					}}
				>
					<span style={{ fontSize: "12.5px", color: CLAUDE_TOKENS.colors.ink }}>
						Upload local structured tabular file (.parquet, .csv, .tsv, .xlsx,
						.json, .jsonl)
					</span>
					<input
						type="file"
						accept=".parquet,.csv,.tsv,.xlsx,.json,.jsonl"
						data-testid="input-upload-structured-file"
						onChange={handleStructuredFileInput}
						style={{ fontSize: "12px" }}
					/>
				</div>
			</div>

			<div
				className="claude-responsive-grid"
				style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}
			>
				<div
					className="claude-card-cream"
					style={{
						minWidth: 0,
						padding: "16px",
						display: "flex",
						flexDirection: "column",
						gap: "12px",
					}}
				>
					<h2 className="claude-h2" style={{ margin: 0 }}>
						Unstructured Documents &amp; OCR
					</h2>
					<input
						type="file"
						accept=".pdf,.docx,.md,.markdown,.txt,.html,.json"
						data-testid="input-upload-document"
						onChange={handleFileInput}
						style={{ fontSize: "12.5px" }}
					/>
					<div
						style={{
							display: "flex",
							gap: "12px",
							alignItems: "center",
							flexWrap: "wrap",
						}}
					>
						<label
							style={{
								fontSize: "12.5px",
								color: CLAUDE_TOKENS.colors.ink,
								display: "flex",
								alignItems: "center",
								gap: "6px",
							}}
						>
							OCR Mode
							<select
								data-testid="select-ocr-mode"
								value={ocrMode}
								onChange={(e) => {
									const next = e.target.value as "auto" | "force" | "off";
									setOcrMode(next);
									onOcrModeChange?.(next);
								}}
								style={{
									padding: "5px 8px",
									borderRadius: "6px",
									border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
									background: CLAUDE_TOKENS.colors.canvas,
									color: CLAUDE_TOKENS.colors.ink,
								}}
							>
								<option value="auto">auto</option>
								<option value="force">force</option>
								<option value="off">off</option>
							</select>
						</label>

						<label
							style={{
								fontSize: "12.5px",
								color: CLAUDE_TOKENS.colors.ink,
								display: "flex",
								alignItems: "center",
								gap: "6px",
							}}
						>
							<input
								type="checkbox"
								data-testid="toggle-do-not-send-to-agent"
								checked={doNotSend}
								onChange={(e) => {
									setDoNotSend(e.target.checked);
									onDoNotSendChange?.(e.target.checked);
								}}
							/>
							Do not send to agent (Local Only)
						</label>
					</div>

					<div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
						<button
							type="button"
							className="claude-btn-primary"
							data-testid="btn-parse-sources"
							disabled={
								Boolean(activeSessionJobId) || attachedSources.length === 0
							}
							onClick={() => onParseSources?.(ocrMode)}
						>
							{activeSessionJobId
								? "Run in progress — view live logs"
								: "Parse Attached Documents"}
						</button>
						{activeSessionJobId && (
							<button
								type="button"
								className="claude-btn-secondary"
								data-testid="btn-cancel-active-job"
								onClick={() => onCancelJob?.()}
							>
								Cancel
							</button>
						)}
					</div>
				</div>

				<div
					className="claude-card-cream"
					style={{
						minWidth: 0,
						padding: "16px",
						display: "flex",
						flexDirection: "column",
						gap: "10px",
					}}
				>
					<h2 className="claude-h2" style={{ margin: 0 }}>
						Inline Domain Note
					</h2>
					<textarea
						data-testid="inline-domain-note-textarea"
						rows={4}
						placeholder="Capture business rules, identifiers, SLA cutoffs, or regulatory domain notes..."
						value={noteText}
						onChange={(e) => setNoteText(e.target.value)}
						style={{
							width: "100%",
							padding: "8px 10px",
							borderRadius: "6px",
							border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
							background: CLAUDE_TOKENS.colors.canvas,
							color: CLAUDE_TOKENS.colors.ink,
							fontFamily: EXPLORER_FONTS.sans,
							fontSize: "13px",
						}}
					/>
					<button
						type="button"
						className="claude-btn-secondary"
						data-testid="btn-add-inline-note"
						disabled={!noteText.trim()}
						onClick={handleSaveNote}
					>
						Attach Inline Note
					</button>
				</div>
			</div>

			<div className="claude-card" style={{ padding: "16px" }}>
				<h2 className="claude-h2" style={{ margin: "0 0 10px" }}>
					Attached Sources ({attachedSources.length})
					{sessionId ? (
						<span
							style={{
								fontSize: "12px",
								fontWeight: 400,
								color: CLAUDE_TOKENS.colors.muted,
								marginLeft: "8px",
							}}
						>
							Session {sessionId}
						</span>
					) : null}
				</h2>
				{attachedSources.length === 0 ? (
					<div style={{ fontSize: "13px", color: CLAUDE_TOKENS.colors.muted }}>
						No sources attached yet. Select a connector tile or upload a
						document above.
					</div>
				) : (
					<ul
						style={{
							listStyle: "none",
							margin: 0,
							padding: 0,
							display: "flex",
							flexDirection: "column",
							gap: "8px",
						}}
					>
						{attachedSources.map((src) => {
							const badge =
								src.status_badge ||
								(src.do_not_send_to_agent ? "Local Only" : "Attached");
							return (
								<li
									key={src.source_id}
									style={{
										display: "flex",
										justifyContent: "space-between",
										alignItems: "center",
										padding: "8px 12px",
										borderRadius: "8px",
										background: CLAUDE_TOKENS.colors.canvas,
										border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
									}}
								>
									<div>
										<strong
											style={{
												fontSize: "13px",
												color: CLAUDE_TOKENS.colors.ink,
											}}
										>
											{src.file_name || src.filename}
										</strong>
										<span
											style={{
												marginLeft: "8px",
												fontSize: "11px",
												color: CLAUDE_TOKENS.colors.muted,
											}}
										>
											{src.mime_type} · {src.byte_count} bytes
										</span>
									</div>
									<span
										className="claude-pill"
										style={{
											fontSize: "11px",
											fontWeight: 600,
											background: src.do_not_send_to_agent
												? "#f8e2e2"
												: CLAUDE_TOKENS.colors.surfaceCreamStrong,
											color: src.do_not_send_to_agent
												? CLAUDE_TOKENS.colors.error
												: CLAUDE_TOKENS.colors.ink,
										}}
									>
										{badge}
									</span>
								</li>
							);
						})}
					</ul>
				)}
			</div>

			{pickerSourceType && (
				<CatalogTablePickerModal
					isOpen={Boolean(pickerSourceType)}
					onClose={() => setPickerSourceType(null)}
					sourceType={pickerSourceType}
					apiUrl={apiUrl}
					fetchImpl={fetchImpl}
					onApply={handlePickerApply}
				/>
			)}
		</section>
	);
};
