/**
 * Sources & Ingestion Management Panel (Task 5D.2, #223)
 * (apps/opendv-web-workbench/src/ontology/components/SourcesPanel.tsx)
 *
 * Implements:
 * - Multi-file drag-and-drop upload zone
 * - MIME type validation (PDF, DOCX, XLSX, HTML, JSON, CSV, MD)
 * - Confidential toggle (do_not_send_to_agent)
 * - Source documents inventory table with size, MIME, SHA-256, confidentiality pill, and delete
 * - Parse trigger with OCR mode selection (auto, force, off)
 * - Parsing progress bar, stage indicator, and active job ID
 * - Evidence chunks preview with token counts and layout complexity badges
 */

import type React from "react";
import { useEffect, useRef, useState } from "react";
import { isSupportedSourceFile, useStudioStore } from "../store/studio-store";
import type { EvidenceChunk, SourceDocument } from "../store/types";

export interface SourcesPanelProps {
	className?: string;
	maxFileSizeBytes?: number;
	onSourceUploaded?: (source: SourceDocument) => void;
	onParseStarted?: (jobId: string) => void;
}

export function formatBytes(bytes: number): string {
	if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
	const k = 1024;
	const sizes = ["B", "KB", "MB", "GB", "TB", "PB"];
	const i = Math.min(
		Math.floor(Math.log(bytes) / Math.log(k)),
		sizes.length - 1,
	);
	return `${Number.parseFloat((bytes / k ** i).toFixed(1))} ${sizes[i]}`;
}

export function getComplexityBadgeClass(
	complexity: EvidenceChunk["layout_complexity"],
): string {
	switch (complexity) {
		case "SIMPLE":
			return "badge-simple";
		case "TABULAR":
			return "badge-tabular";
		case "MULTI_COLUMN":
			return "badge-multi-col";
		case "COMPLEX":
			return "badge-complex";
		default:
			return "badge-default";
	}
}

export const SourcesPanel: React.FC<SourcesPanelProps> = ({
	className = "",
	maxFileSizeBytes,
	onSourceUploaded,
	onParseStarted,
}) => {
	const activeSession = useStudioStore((s) => s.activeSession);
	const sources = useStudioStore((s) => s.sources);
	const isLoadingSources = useStudioStore((s) => s.isLoadingSources);
	const parsingState = useStudioStore((s) => s.parsingState);
	const uploadSource = useStudioStore((s) => s.uploadSource);
	const deleteSource = useStudioStore((s) => s.deleteSource);
	const startParse = useStudioStore((s) => s.startParse);
	const addToast = useStudioStore((s) => s.addToast);

	const [isDragging, setIsDragging] = useState(false);
	const dragCounterRef = useRef(0);
	const [confidential, setConfidential] = useState(false);
	const [ocrMode, setOcrMode] = useState<"auto" | "force" | "off">("auto");
	const [validationError, setValidationError] = useState<string | null>(null);

	useEffect(() => {
		if (typeof window === "undefined") return;
		const resetDrag = () => {
			dragCounterRef.current = 0;
			setIsDragging(false);
		};
		window.addEventListener("dragend", resetDrag);
		window.addEventListener("drop", resetDrag);
		return () => {
			window.removeEventListener("dragend", resetDrag);
			window.removeEventListener("drop", resetDrag);
		};
	}, []);

	const handleFiles = async (fileList: FileList | null) => {
		if (!fileList || fileList.length === 0) return;
		setValidationError(null);

		if (!activeSession) {
			setValidationError(
				"An active studio session is required to upload sources.",
			);
			return;
		}

		const maxBytes = maxFileSizeBytes ?? 50 * 1024 * 1024;

		const errors: string[] = [];

		for (let i = 0; i < fileList.length; i++) {
			const file = fileList[i];
			if (!file) continue;

			if (!isSupportedSourceFile(file.name, file.type)) {
				const msg = `Unsupported file '${file.name}'. Allowed: PDF, DOCX, XLSX, HTML, JSON, CSV, MD, TXT.`;
				errors.push(msg);
				addToast({
					type: "urn:opendv:error:unsupported-mime",
					title: "Unsupported File Format",
					status: 415,
					detail: msg,
					code: "SYNTAX_INVALID_ARGUMENT",
				});
				continue;
			}

			if (file.size > maxBytes) {
				const msg = `File '${file.name}' (${formatBytes(file.size)}) exceeds maximum allowed upload size of ${formatBytes(maxBytes)}.`;
				errors.push(msg);
				addToast({
					type: "urn:opendv:error:payload-too-large",
					title: "Payload Too Large",
					status: 413,
					detail: msg,
					code: "PAYLOAD_TOO_LARGE",
				});
				continue;
			}

			try {
				const content = await file.arrayBuffer();

				const uploaded = await uploadSource(
					{
						name: file.name,
						size: file.size,
						type: file.type || "application/octet-stream",
						content,
					},
					confidential,
				);
				if (uploaded && onSourceUploaded) {
					onSourceUploaded(uploaded);
				}
			} catch (err) {
				errors.push(`Upload failed for '${file.name}': ${String(err)}`);
			}
		}

		if (errors.length > 0) {
			setValidationError(errors.join("; "));
		}
	};

	const handleDeleteSource = async (sourceId: string, fileName?: string) => {
		if (
			typeof window !== "undefined" &&
			window.confirm &&
			!window.confirm(
				`Are you sure you want to delete '${fileName || sourceId}'? Any evidence citations referencing this document may become unlinked.`,
			)
		) {
			return;
		}
		try {
			await deleteSource(sourceId);
		} catch (err) {
			setValidationError(String(err));
		}
	};

	const handleDragEnter = (e: React.DragEvent) => {
		e.preventDefault();
		dragCounterRef.current += 1;
		setIsDragging(true);
	};

	const handleDragOver = (e: React.DragEvent) => {
		e.preventDefault();
		setIsDragging(true);
	};

	const handleDragLeave = () => {
		dragCounterRef.current = Math.max(0, dragCounterRef.current - 1);
		if (dragCounterRef.current === 0) {
			setIsDragging(false);
		}
	};

	const handleDrop = async (e: React.DragEvent) => {
		e.preventDefault();
		dragCounterRef.current = 0;
		setIsDragging(false);
		await handleFiles(e.dataTransfer.files);
	};

	const handleFileInputChange = async (
		e: React.ChangeEvent<HTMLInputElement>,
	) => {
		await handleFiles(e.target.files);
		e.target.value = "";
	};

	const handleStartParse = async () => {
		if (!activeSession) {
			setValidationError(
				"An active studio session is required to trigger parsing.",
			);
			return;
		}
		try {
			const jobId = await startParse(ocrMode);
			if (jobId && onParseStarted) {
				onParseStarted(jobId);
			}
		} catch (err) {
			setValidationError(String(err));
		}
	};

	return (
		<div className={`sources-panel ${className}`} data-testid="sources-panel">
			{/* Panel Header */}
			<div className="panel-header">
				<div className="panel-title-group">
					<h2 className="panel-title">Sources & Ingestion</h2>
					<span
						className="sources-count-badge"
						aria-label={`${sources.length} sources loaded`}
					>
						{sources.length} {sources.length === 1 ? "file" : "files"}
					</span>
				</div>
				<p className="panel-subtitle">
					Upload enterprise documents, filings, and schemas into
					content-addressable storage (CAS).
				</p>
			</div>

			{/* Drag & Drop Upload Zone */}
			<div
				className={`dropzone ${isDragging ? "dropzone-dragging" : ""}`}
				onDragEnter={handleDragEnter}
				onDragOver={handleDragOver}
				onDragLeave={handleDragLeave}
				onDrop={handleDrop}
				role="region"
				aria-label="File upload dropzone"
			>
				<div className="dropzone-content">
					<svg
						className="upload-icon"
						width="32"
						height="32"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						strokeWidth="2"
						strokeLinecap="round"
						strokeLinejoin="round"
						aria-hidden="true"
					>
						<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
						<polyline points="17 8 12 3 7 8" />
						<line x1="12" y1="3" x2="12" y2="15" />
					</svg>
					<div className="dropzone-text">
						<span className="dropzone-prompt">Drag & drop files here, or </span>
						<label className="file-input-label">
							browse
							<input
								type="file"
								multiple
								className="sr-only"
								onChange={handleFileInputChange}
								accept=".pdf,.docx,.xlsx,.html,.htm,.json,.csv,.md,.txt"
								data-testid="file-input"
							/>
						</label>
					</div>
					<span className="format-hint">
						Supported: PDF, DOCX, XLSX, HTML, JSON, CSV, Markdown, TXT
					</span>
				</div>

				<div className="upload-options">
					<label className="confidential-checkbox-label">
						<input
							type="checkbox"
							checked={confidential}
							onChange={(e) => setConfidential(e.target.checked)}
							data-testid="confidential-toggle"
						/>
						<span>Confidential Source (do not send to LLM agents)</span>
					</label>
				</div>
			</div>

			{validationError && (
				<div
					className="error-banner"
					role="alert"
					data-testid="upload-validation-error"
				>
					<span>{validationError}</span>
				</div>
			)}

			{/* Parse Controls & Progress */}
			<div className="parse-section" data-testid="parse-section">
				<div className="parse-controls">
					<div className="ocr-select-group">
						<label htmlFor="ocr-mode-select">OCR Mode:</label>
						<select
							id="ocr-mode-select"
							value={ocrMode}
							onChange={(e) =>
								setOcrMode(e.target.value as "auto" | "force" | "off")
							}
							className="ocr-mode-select"
							data-testid="ocr-mode-select"
						>
							<option value="auto">Auto (OCR scanned pages only)</option>
							<option value="force">Force (Full OCR pipeline)</option>
							<option value="off">Off (Native text extraction only)</option>
						</select>
					</div>

					<button
						type="button"
						className="btn-primary parse-btn"
						onClick={handleStartParse}
						disabled={sources.length === 0 || parsingState.status === "parsing"}
						data-testid="start-parse-btn"
					>
						{parsingState.status === "parsing" ? "Parsing..." : "Parse Sources"}
					</button>
				</div>

				{parsingState.status === "parsing" && (
					<div
						className="parsing-progress-box"
						data-testid="parsing-progress-box"
					>
						<div className="progress-header">
							<span className="stage-label">
								Stage: {parsingState.stage || "processing"}
							</span>
							<span className="pct-label">{parsingState.progress_pct}%</span>
						</div>
						<div
							className="progress-bar-track"
							role="progressbar"
							aria-valuenow={parsingState.progress_pct}
							aria-valuemin={0}
							aria-valuemax={100}
						>
							<div
								className="progress-bar-fill"
								style={{ width: `${parsingState.progress_pct}%` }}
							/>
						</div>
						{parsingState.active_job_id && (
							<span className="job-id-label">
								Job ID: {parsingState.active_job_id}
							</span>
						)}
					</div>
				)}

				{parsingState.status === "completed" && (
					<div
						className="parse-success-badge"
						data-testid="parse-success-badge"
					>
						✓ Parsing complete (
						{parsingState.evidence_chunks.length ||
							parsingState.chunks_count ||
							0}{" "}
						chunks extracted)
					</div>
				)}

				{parsingState.status === "failed" && parsingState.error && (
					<div className="error-banner" role="alert">
						<span>Parse error: {parsingState.error}</span>
					</div>
				)}
			</div>

			{/* Sources List Table */}
			<div className="sources-inventory" data-testid="sources-inventory">
				<h3 className="section-title">Uploaded Documents ({sources.length})</h3>
				{sources.length === 0 ? (
					<div className="empty-sources-msg" data-testid="empty-sources-msg">
						No documents uploaded yet. Upload source files to begin ontology
						discovery.
					</div>
				) : (
					<div className="table-responsive">
						<table
							className="sources-table"
							aria-label="Uploaded source documents"
						>
							<thead>
								<tr>
									<th>Document</th>
									<th>MIME Type</th>
									<th>Size</th>
									<th>SHA-256</th>
									<th>Privacy</th>
									<th>Action</th>
								</tr>
							</thead>
							<tbody>
								{sources.map((src) => (
									<tr
										key={src.source_id}
										data-testid={`source-row-${src.source_id}`}
									>
										<td className="doc-name-cell">
											<span className="doc-icon">📄</span>
											<span className="doc-name" title={src.file_name}>
												{src.file_name}
											</span>
										</td>
										<td className="mime-cell">{src.mime_type}</td>
										<td className="size-cell">{formatBytes(src.size_bytes)}</td>
										<td className="sha-cell" title={src.source_sha256 || ""}>
											<code>
												{src.source_sha256
													? `${src.source_sha256.slice(0, 8)}…`
													: "pending…"}
											</code>
										</td>
										<td className="privacy-cell">
											{src.do_not_send_to_agent || src.confidential ? (
												<span
													className="badge badge-confidential"
													title="Confidential: Never sent to external LLMs"
												>
													CONFIDENTIAL
												</span>
											) : (
												<span className="badge badge-standard">STANDARD</span>
											)}
										</td>
										<td className="action-cell">
											<button
												type="button"
												className="btn-danger btn-sm"
												onClick={() =>
													handleDeleteSource(src.source_id, src.file_name)
												}
												aria-label={`Delete ${src.file_name}`}
												data-testid={`delete-source-${src.source_id}`}
											>
												Delete
											</button>
										</td>
									</tr>
								))}
							</tbody>
						</table>
					</div>
				)}
			</div>

			{/* Evidence Chunks Preview */}
			{parsingState.evidence_chunks.length > 0 && (
				<div
					className="evidence-chunks-section"
					data-testid="evidence-chunks-section"
				>
					<h3 className="section-title">
						Extracted Evidence Chunks ({parsingState.evidence_chunks.length})
					</h3>
					<div className="chunks-list">
						{parsingState.evidence_chunks.map((chk) => (
							<div
								key={chk.chunk_id}
								className="chunk-card"
								data-testid={`chunk-card-${chk.chunk_id}`}
							>
								<div className="chunk-header">
									<span className="chunk-source">{chk.source_file}</span>
									<div className="chunk-badges">
										<span
											className={`complexity-badge ${getComplexityBadgeClass(chk.layout_complexity)}`}
										>
											{chk.layout_complexity}
										</span>
										<span className="tokens-badge">
											{chk.token_count} tokens
										</span>
									</div>
								</div>
								<div className="chunk-snippet">
									<blockquote>{chk.text_snippet}</blockquote>
								</div>
							</div>
						))}
					</div>
				</div>
			)}
		</div>
	);
};
