import type { ConnectorUIManifestDTO, SourceFeedDTO } from "@opendv/protocol-ts";
import { type ChangeEvent, useEffect, useState } from "react";
import { getStudioAuthHeaders } from "../../ontology/store/studio-store";
import { EXPLORER_COLORS, EXPLORER_FONTS } from "../design-tokens";
import { CatalogTablePickerModal } from "./CatalogTablePickerModal";
import type { Step2ConnectorSourcesProps } from "./wizard-types";

const STYLES = {
	section: { backgroundColor: EXPLORER_COLORS.bgSurface, borderRadius: 8, border: `1px solid ${EXPLORER_COLORS.borderLine}`, padding: 20 },
	panel: { padding: 14, borderRadius: 6, border: `1px solid ${EXPLORER_COLORS.borderLine}`, backgroundColor: EXPLORER_COLORS.bgPage, marginBottom: 16 },
	input: { padding: "5px 8px", borderRadius: 4, border: `1px solid ${EXPLORER_COLORS.borderLine}`, fontSize: 12 },
	card: { padding: 12, border: `1px solid ${EXPLORER_COLORS.borderSelected}`, borderRadius: 6, backgroundColor: "#F0F9FF", display: "flex", flexDirection: "column", justifyContent: "space-between", cursor: "pointer", textAlign: "left", font: "inherit" },
	btn: { padding: "5px 12px", borderRadius: 4, color: "#FFF", fontWeight: 600, fontSize: 12, border: "none", cursor: "pointer", whiteSpace: "nowrap" },
	td: { padding: "6px 10px" },
	del: { background: "none", border: "none", color: "#DC2626", cursor: "pointer", fontSize: 11 },
	badge: { padding: "2px 5px", borderRadius: 3, fontSize: 11, fontFamily: EXPLORER_FONTS.mono },
	rowBetween: { display: "flex", alignItems: "center", justifyContent: "space-between" },
	row: { display: "flex", alignItems: "center", gap: 6 },
} as const;

export function Step2ConnectorSources({
	formState,
	onChange,
	onOpenCatalogPicker,
	initialAdapters,
	apiUrl = "/api/v1",
	fetchImpl,
}: Step2ConnectorSourcesProps) {
	const [adapters, setAdapters] = useState<ConnectorUIManifestDTO[]>(initialAdapters ?? []);
	const [adaptersError, setAdaptersError] = useState<string | null>(null);
	const [pickerState, setPickerState] = useState<{
		isOpen: boolean;
		sourceType: "sqlite" | "databricks" | "duckdb" | "file";
	}>({ isOpen: false, sourceType: "sqlite" });

	const [filePathInput, setFilePathInput] = useState("");
	const [isUploadingCas, setIsUploadingCas] = useState(false);
	const [casUploadStatus, setCasUploadStatus] = useState<string | null>(null);
	const [lastUploadedFormat, setLastUploadedFormat] = useState<string | undefined>(undefined);
	const [docTitleInput, setDocTitleInput] = useState("");
	const [docTypeInput, setDocTypeInput] = useState("markdown");
	const [docContentInput, setDocContentInput] = useState("");

	useEffect(() => {
		let isMounted = true;
		const fetcher = fetchImpl ?? (typeof fetch !== "undefined" ? fetch : null);
		if (!fetcher) return;

		const adaptersUrl = `${apiUrl}/sources/adapters`;
		fetcher(adaptersUrl, {
			credentials: "include",
			headers: getStudioAuthHeaders(undefined, adaptersUrl),
		})
			.then(async (res) => {
				if (!res.ok) throw new Error(`HTTP ${res.status}`);
				return res.json();
			})
			.then((data) => {
				if (isMounted && data) {
					const list: ConnectorUIManifestDTO[] = Array.isArray(data) ? data : Array.isArray(data?.adapters) ? data.adapters : [];
					setAdapters(list);
					setAdaptersError(null);
				}
			})
			.catch((err) => {
				if (isMounted) setAdaptersError(`Failed to load connector adapters: ${err instanceof Error ? err.message : String(err)}`);
			});

		return () => { isMounted = false; };
	}, [apiUrl, fetchImpl]);

	const handleOpenPicker = (sourceType: "sqlite" | "databricks" | "duckdb" | "file") => {
		setPickerState({ isOpen: true, sourceType });
		onOpenCatalogPicker?.(sourceType as "sqlite" | "databricks");
	};

	const handleCasFileUpload = async (e: ChangeEvent<HTMLInputElement>) => {
		const file = e.target.files?.[0];
		if (!file) return;
		setIsUploadingCas(true);
		setCasUploadStatus("Uploading binary to CAS...");
		try {
			const fetcher = fetchImpl ?? (typeof fetch !== "undefined" ? fetch : null);
			if (!fetcher) throw new Error("Fetch unavailable");
			const buf = await file.arrayBuffer();
			const casUrl = `${apiUrl}/cas/blobs`;
			const res = await fetcher(casUrl, {
				method: "POST",
				headers: { ...getStudioAuthHeaders(undefined, casUrl), "Content-Type": "application/octet-stream" },
				credentials: "include",
				body: buf,
			});
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			const data = (await res.json()) as { uri?: string; sha256?: string };

			const originalFileName = file.name;
			const lowerFileName = originalFileName.toLowerCase();
			const m = lowerFileName.match(/\.(csv|tsv|parquet|pq|jsonl|json|sqlite3?|db|duckdb|ddb)$/);
			const ext = m?.[1];
			const fmt = ext ? (ext === "pq" ? "parquet" : ext.startsWith("sqlite") || ext === "db" ? "sqlite" : ext === "ddb" ? "duckdb" : ext) : undefined;
			setLastUploadedFormat(fmt);

			const baseUri = data.uri || `cas://${data.sha256}`;
			const uri = baseUri.includes(originalFileName)
				? baseUri
				: `${baseUri}/${originalFileName}`;
			setFilePathInput(uri);
			setCasUploadStatus(`Uploaded: ${uri}`);
		} catch (err) {
			setCasUploadStatus(`Upload failed: ${err instanceof Error ? err.message : String(err)}`);
		} finally {
			setIsUploadingCas(false);
		}
	};

	const handleApplyFeed = (feed: SourceFeedDTO) => {
		onChange({ configuredSources: [...formState.configuredSources, feed] });
	};

	const handleRemoveFeed = (feedId: string) => {
		onChange({ configuredSources: formState.configuredSources.filter((f) => f.feed_id !== feedId) });
	};

	const handleAddFileFeed = () => {
		const path = filePathInput.trim();
		if (!path) return;
		const fileName = path.split("/").pop()?.split("\\").pop() ?? "file_feed";
		const cleanName = fileName.replace(/\.[^/.]+$/, "");
		const sys = path.startsWith("cas://")
			? `cas_${path.slice(6, 14)}`
			: `${cleanName.toLowerCase().replace(/[^a-z0-9_]/g, "_") || "file"}_src`;

		let sourceFormat: string | undefined;
		const lowerPath = path.toLowerCase();
		const pathClean = lowerPath.split(/[?#]/)[0] ?? "";
		let effAdapter = "file";
		if (/\.csv$/i.test(pathClean)) {
			sourceFormat = "csv";
		} else if (/\.tsv$/i.test(pathClean)) {
			sourceFormat = "tsv";
		} else if (/\.(parquet|pq)$/i.test(pathClean)) {
			sourceFormat = "parquet";
		} else if (/\.jsonl$/i.test(pathClean)) {
			sourceFormat = "jsonl";
		} else if (/\.json$/i.test(pathClean)) {
			sourceFormat = "json";
		} else if (lowerPath.startsWith("sqlite:") || /\.(sqlite|sqlite3|db)$/i.test(pathClean)) {
			sourceFormat = "sqlite";
			effAdapter = "sqlite";
		} else if (lowerPath.startsWith("duckdb:") || /\.(duckdb|ddb)$/i.test(pathClean)) {
			sourceFormat = "duckdb";
			effAdapter = "duckdb";
		}

		const formatToUse = sourceFormat || lastUploadedFormat;
		const newFeed: SourceFeedDTO = {
			feed_id: `feed_${Date.now()}`,
			source_system: sys,
			uri: path,
			adapter: effAdapter,
			table_selection: { mode: "EXPLICIT", tables: [cleanName || "file_table"] },
			...(formatToUse
				? {
						options: {
							format: formatToUse,
							source_format: formatToUse,
						},
					}
				: {}),
		};
		onChange({ configuredSources: [...formState.configuredSources, newFeed] });
		setFilePathInput("");
		setLastUploadedFormat(undefined);
		setCasUploadStatus(null);
	};

	const handleAddDocument = () => {
		const title = docTitleInput.trim();
		if (!title) return;
		const mimeType = docTypeInput === "pdf" ? "application/pdf" : docTypeInput === "markdown" ? "text/markdown" : "text/plain";
		const doc = {
			doc_id: `doc_${Date.now()}`,
			document_path: title,
			mime_type: mimeType,
			content: docContentInput.trim() || undefined,
			title,
			doc_type: docTypeInput,
			added_at: new Date().toISOString(),
		};
		onChange({ unstructuredDocs: [...formState.unstructuredDocs, doc] });
		setDocTitleInput("");
		setDocContentInput("");
	};

	const handleRemoveDocument = (idx: number) => {
		onChange({ unstructuredDocs: formState.unstructuredDocs.filter((_, i) => i !== idx) });
	};

	return (
		<section data-testid="wizard-step-connectors" style={STYLES.section}>
			<h2 style={{ fontSize: 16, fontWeight: 700, margin: "0 0 6px 0" }}>Step 2: Data Sources & Connectors</h2>
			<p style={{ fontSize: 13, color: EXPLORER_COLORS.inkMuted, marginBottom: 16 }}>
				Configure SQLite, Databricks Unity Catalog, DuckDB/Delta, local files, or unstructured documents.
			</p>

			{adaptersError && (
				<div data-testid="adapters-error-banner" style={{ padding: "8px 12px", borderRadius: 4, backgroundColor: "#FEE2E2", color: "#991B1B", border: "1px solid #FECACA", fontSize: 12, marginBottom: 12 }}>
					{adaptersError}
				</div>
			)}

			<div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 10, marginBottom: 16 }}>
				{adapters.map((card) => {
					const aid = card.adapter_id;
					return (
						<button
							key={aid}
							type="button"
							data-testid={`connector-card-${aid}`}
							onClick={() => {
								if (aid === "file") document.getElementById("input-cas-file-upload")?.click();
								else handleOpenPicker(aid as "sqlite" | "databricks" | "duckdb" | "file");
							}}
							style={STYLES.card}
						>
							<div>
								<div style={{ fontWeight: 600, fontSize: 13, color: "#0369A1" }}>{card.display_name}</div>
								<div style={{ fontSize: 11, color: EXPLORER_COLORS.inkMuted, marginTop: 4 }}>{card.description || "Inspect and configure source feeds"}</div>
							</div>
							<div style={{ marginTop: 8, fontSize: 11, fontWeight: 600, color: EXPLORER_COLORS.borderSelected }}>
								{aid === "file" ? "Upload to CAS ➔" : "Inspect Tables ➔"}
							</div>
						</button>
					);
				})}
			</div>

			<div style={STYLES.panel}>
				<label htmlFor="input-file-feed-path" style={{ display: "block", fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
					Add Direct File Feed (Parquet, CSV, CAS URI):
				</label>
				<div style={{ display: "flex", gap: 8, alignItems: "center" }}>
					<input
						id="input-file-feed-path"
						type="text"
						data-testid="input-file-feed-path"
						value={filePathInput}
						onChange={(e: ChangeEvent<HTMLInputElement>) => setFilePathInput(e.target.value)}
						placeholder="e.g. data/sec_13f_holdings.parquet or cas://<hash>"
						style={{ ...STYLES.input, flex: 1, fontFamily: EXPLORER_FONTS.mono }}
					/>
					<label style={{ ...STYLES.btn, backgroundColor: "#0284C7", cursor: isUploadingCas ? "wait" : "pointer" }}>
						{isUploadingCas ? "Uploading..." : "Upload CAS"}
						<input
							id="input-cas-file-upload"
							data-testid="input-cas-file-upload"
							type="file"
							style={{ display: "none" }}
							onChange={handleCasFileUpload}
							disabled={isUploadingCas}
						/>
					</label>
					<button
						type="button"
						data-testid="btn-add-file-feed"
						onClick={handleAddFileFeed}
						disabled={!filePathInput.trim()}
						style={{ ...STYLES.btn, backgroundColor: filePathInput.trim() ? EXPLORER_COLORS.borderSelected : "#94A3B8", cursor: filePathInput.trim() ? "pointer" : "not-allowed" }}
					>
						+ Add File
					</button>
				</div>
				{casUploadStatus && (
					<div data-testid="cas-upload-status" style={{ marginTop: 6, fontSize: 11, color: casUploadStatus.includes("failed") ? "#DC2626" : "#0284C7", fontFamily: EXPLORER_FONTS.mono }}>
						{casUploadStatus}
					</div>
				)}
			</div>

			<div style={{ marginBottom: 16 }}>
				<div style={{ ...STYLES.rowBetween, marginBottom: 8 }}>
					<h3 style={{ fontSize: 13, fontWeight: 700, margin: 0 }}>Configured Data Feeds:</h3>
					<span data-testid="configured-sources-count" style={{ fontSize: 12, fontFamily: EXPLORER_FONTS.mono, color: EXPLORER_COLORS.inkMuted }}>
						{formState.configuredSources.length} sources configured
					</span>
				</div>

				{formState.configuredSources.length === 0 ? (
					<div data-testid="empty-sources-banner" style={{ padding: 14, borderRadius: 6, border: "1px dashed #CBD5E1", backgroundColor: "#F8FAFC", textAlign: "center", fontSize: 12, color: EXPLORER_COLORS.inkMuted }}>
						No tabular data sources configured yet. Click a connector above to inspect and select tables.
					</div>
				) : (
					<div data-testid="configured-sources-table" style={{ borderRadius: 6, border: `1px solid ${EXPLORER_COLORS.borderLine}`, overflow: "hidden" }}>
						<table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
							<thead style={{ backgroundColor: EXPLORER_COLORS.bgPage, borderBottom: `1px solid ${EXPLORER_COLORS.borderLine}`, textAlign: "left" }}>
								<tr style={{ color: EXPLORER_COLORS.inkMuted }}>
									<th style={STYLES.td}>Adapter</th>
									<th style={STYLES.td}>URI / Path</th>
									<th style={STYLES.td}>Tables</th>
									<th style={{ ...STYLES.td, textAlign: "right" }}>Action</th>
								</tr>
							</thead>
							<tbody>
								{formState.configuredSources.map((feed) => {
									const tableCount = feed.table_selection?.tables?.length ?? 1;
									const tableNames = feed.table_selection?.tables?.join(", ") ?? "all";
									const adp = feed.adapter ?? "source";
									const isSql = adp === "sqlite";
									const isDbx = adp === "databricks";
									return (
										<tr key={feed.feed_id} data-testid={`feed-row-${feed.feed_id}`} style={{ borderBottom: "1px solid #F1F5F9" }}>
											<td style={{ ...STYLES.td, fontWeight: 600 }}>
												<span style={{ ...STYLES.badge, backgroundColor: isSql ? "#E0E7FF" : isDbx ? "#FEF3C7" : "#F1F5F9", color: isSql ? "#3730A3" : isDbx ? "#92400E" : "#334155" }}>
													{adp.toUpperCase()}
												</span>
											</td>
											<td style={{ ...STYLES.td, fontFamily: EXPLORER_FONTS.mono, fontSize: 11 }}>{feed.uri}</td>
											<td style={STYLES.td}>
												<strong>{tableCount} tables</strong>{" "}
												<span style={{ fontSize: 11, color: EXPLORER_COLORS.inkMuted }}>
													({tableNames.length > 25 ? `${tableNames.substring(0, 25)}...` : tableNames})
												</span>
											</td>
											<td style={{ ...STYLES.td, textAlign: "right" }}>
												<button type="button" data-testid={`btn-remove-feed-${feed.feed_id}`} onClick={() => handleRemoveFeed(feed.feed_id)} style={STYLES.del}>
													Remove
												</button>
											</td>
										</tr>
									);
								})}
							</tbody>
						</table>
					</div>
				)}
			</div>

			<div style={STYLES.panel}>
				<h3 style={{ fontSize: 13, fontWeight: 700, margin: "0 0 6px 0" }}>Unstructured Document Feed (Optional)</h3>
				<p style={{ fontSize: 12, color: EXPLORER_COLORS.inkMuted, margin: "0 0 10px 0" }}>
					Add PDF filings, markdown specifications, or free-form domain notes for concept grounding.
				</p>

				<div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
					<input
						type="text"
						data-testid="input-doc-title"
						value={docTitleInput}
						onChange={(e: ChangeEvent<HTMLInputElement>) => setDocTitleInput(e.target.value)}
						placeholder="Document title or path (e.g. docs/form_10k.pdf)"
						style={{ ...STYLES.input, flex: 1 }}
					/>
					<select data-testid="select-doc-type" value={docTypeInput} onChange={(e) => setDocTypeInput(e.target.value)} style={STYLES.input}>
						<option value="markdown">Markdown (.md)</option>
						<option value="pdf">PDF</option>
						<option value="text">Plain Text</option>
						<option value="inline_note">Inline Note</option>
					</select>
					<button
						type="button"
						data-testid="btn-add-document"
						onClick={handleAddDocument}
						disabled={!docTitleInput.trim()}
						style={{ ...STYLES.btn, backgroundColor: docTitleInput.trim() ? EXPLORER_COLORS.borderSelected : "#94A3B8", cursor: docTitleInput.trim() ? "pointer" : "not-allowed" }}
					>
						+ Add Doc
					</button>
				</div>

				{docTypeInput === "inline_note" && (
					<textarea
						data-testid="textarea-doc-content"
						value={docContentInput}
						onChange={(e) => setDocContentInput(e.target.value)}
						placeholder="Enter domain notes..."
						rows={2}
						style={{ width: "100%", padding: 6, borderRadius: 4, border: `1px solid ${EXPLORER_COLORS.borderLine}`, fontSize: 12, boxSizing: "border-box", marginBottom: 6 }}
					/>
				)}

				{formState.unstructuredDocs.length > 0 && (
					<div data-testid="unstructured-docs-list" style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 4 }}>
						{formState.unstructuredDocs.map((doc, idx) => (
							<div key={String(doc.doc_id ?? idx)} data-testid={`doc-row-${idx}`} style={{ ...STYLES.rowBetween, padding: "5px 8px", backgroundColor: "#FFF", borderRadius: 4, border: `1px solid ${EXPLORER_COLORS.borderLine}`, fontSize: 12 }}>
								<div style={STYLES.row}>
									<span style={{ ...STYLES.badge, backgroundColor: "#F3E8FF", color: "#9333EA", fontWeight: 700 }}>
										{String(doc.doc_type ?? (String(doc.mime_type || "").includes("pdf") ? "PDF" : String(doc.mime_type || "").includes("markdown") ? "MARKDOWN" : "DOC")).toUpperCase()}
									</span>
									<span style={{ fontWeight: 600 }}>{String(doc.title ?? doc.document_path ?? "Untitled")}</span>
								</div>
								<button type="button" data-testid={`btn-remove-doc-${idx}`} onClick={() => handleRemoveDocument(idx)} style={STYLES.del}>
									Remove
								</button>
							</div>
						))}
					</div>
				)}
			</div>

			<CatalogTablePickerModal
				key={pickerState.sourceType}
				isOpen={pickerState.isOpen}
				onClose={() => setPickerState((prev) => ({ ...prev, isOpen: false }))}
				sourceType={pickerState.sourceType}
				apiUrl={apiUrl}
				fetchImpl={fetchImpl}
				onApply={handleApplyFeed}
			/>
		</section>
	);
}
