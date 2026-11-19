/**
 * Sources, Connector Attachment, Inline Notes, Parsing & Step 3 Evidence Previews Slice
 * (apps/opendv-web-workbench/src/ontology/store/sources-preview-slice.ts)
 */

import type { JobMonitorController } from "./job-monitor";
import { extractActiveSessionJobId } from "./job-monitor-helpers";
import type { StoreGet, StoreSet } from "./store-types";
import type { StudioFetcher } from "./studio-fetch";
import type {
	ConnectorSourceInput,
	EvidenceChunk,
	EvidenceTableProfile,
	ParsingState,
	SourceDocument,
} from "./types";

const MAX_LOCAL_PREVIEW_ROWS = 50;

const EXTENSION_TO_MIMES: Record<string, string[]> = {
	".pdf": ["application/pdf"],
	".docx": [
		"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
	],
	".xlsx": [
		"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
	],
	".html": ["text/html"],
	".htm": ["text/html"],
	".json": ["application/json"],
	".jsonl": ["application/jsonl", "application/x-ndjson", "text/plain"],
	".csv": ["text/csv"],
	".tsv": ["text/tab-separated-values", "text/plain"],
	".parquet": [
		"application/vnd.apache.parquet",
		"application/x-parquet",
		"application/octet-stream",
	],
	".md": ["text/markdown", "text/plain"],
	".markdown": ["text/markdown", "text/plain"],
	".txt": ["text/plain"],
};

export function isSupportedSourceFile(
	filename: string,
	mimeType: string,
): boolean {
	const lower = filename.toLowerCase();
	const entry = Object.entries(EXTENSION_TO_MIMES).find(([ext]) =>
		lower.endsWith(ext),
	);
	if (!entry) return false;
	const [, validMimes] = entry;
	if (!mimeType || mimeType === "application/octet-stream") return true;
	return validMimes.includes(mimeType);
}

export function normalizeSourceDoc(
	raw: SourceDocument & { confidential?: boolean },
): SourceDocument {
	const flag = Boolean(raw.do_not_send_to_agent ?? raw.confidential);
	return {
		...raw,
		do_not_send_to_agent: flag,
		confidential: flag,
	};
}

export function createDefaultParsingState(): ParsingState {
	return {
		status: "idle",
		progress_pct: 0,
		active_job_id: null,
		stage: "",
		ocr_mode: "auto",
		evidence_chunks: [],
		error: null,
	};
}

export function createSourcesPreviewSlice(
	set: StoreSet,
	get: StoreGet,
	studioFetch: StudioFetcher,
	jobMonitor: JobMonitorController,
	bumpSourcesReqId: () => number,
	getSourcesReqId: () => number,
) {
	return {
		sources: [] as SourceDocument[],
		isLoadingSources: false,
		parsingState: createDefaultParsingState(),
		evidenceTables: [] as EvidenceTableProfile[],
		evidenceChunks: [] as EvidenceChunk[],
		observedTermCounts: {} as Record<string, number>,

		uploadSource: async (
			file:
				| File
				| {
						name: string;
						size?: number;
						type: string;
						content?:
							| File
							| Blob
							| ArrayBuffer
							| Uint8Array
							| string
							| undefined;
				  },
			confidential = false,
			fetchImpl = fetch,
			ocrMode: "auto" | "force" | "off" = "auto",
		) => {
			const activeSession = get().activeSession;
			if (!activeSession) {
				get().addToast({
					type: "urn:opendv:error:no-session",
					title: "No Active Session",
					status: 400,
					detail:
						"Please select or create a session before uploading documents.",
					code: "SYNTAX_INVALID_ARGUMENT",
				});
				return null;
			}

			if (!isSupportedSourceFile(file.name, file.type)) {
				get().addToast({
					type: "urn:opendv:error:unsupported-mime",
					title: "Unsupported File Format",
					status: 415,
					detail: `The file format '${file.name}' is not supported. Please upload PDF, DOCX, XLSX, Parquet, HTML, JSON, JSONL, CSV, TSV, Markdown, or TXT.`,
					code: "SYNTAX_INVALID_ARGUMENT",
				});
				return null;
			}

			set({ isLoadingSources: true });
			try {
				const rawContent =
					file instanceof Blob ||
					(typeof File !== "undefined" && file instanceof File)
						? file
						: file.content;
				let filePayload: Blob | File;
				if (
					rawContent instanceof Blob ||
					(typeof File !== "undefined" && rawContent instanceof File)
				) {
					filePayload = rawContent;
				} else if (
					rawContent instanceof ArrayBuffer ||
					ArrayBuffer.isView(rawContent)
				) {
					filePayload = new Blob([rawContent as BlobPart], {
						type: file.type || "application/octet-stream",
					});
				} else if (typeof rawContent === "string" && rawContent.length > 0) {
					filePayload = new Blob([rawContent], {
						type: file.type || "text/plain",
					});
				} else {
					get().addToast({
						type: "urn:opendv:error:empty-source",
						title: "Empty Source File",
						status: 400,
						detail: "Cannot upload an empty source document.",
						code: "SYNTAX_INVALID_ARGUMENT",
					});
					set({ isLoadingSources: false });
					return null;
				}

				const formData = new FormData();
				formData.append("file", filePayload, file.name);
				formData.append(
					"do_not_send_to_agent",
					confidential ? "true" : "false",
				);
				formData.append("ocr_mode", ocrMode);

				const sid = activeSession.session_id;
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/sources`,
					{ method: "POST", body: formData },
				);
				if (get().activeSession?.session_id !== sid) {
					set({ isLoadingSources: false });
					return null;
				}

				if (!res.ok) {
					const err = (await res.json().catch(() => ({}))) as {
						detail?: string;
						code?: string;
						title?: string;
					};
					set({ isLoadingSources: false });
					if (get().activeSession?.session_id !== sid) return null;
					get().addToast({
						type: "urn:opendv:error:source-upload",
						title: err.title || "Failed to Upload Source",
						status: res.status,
						detail: err.detail || `Upload failed with status ${res.status}`,
						code: err.code || "CORRUPT_SOURCE_BLOB",
					});
					return null;
				}

				const uploadedRaw = (await res.json()) as SourceDocument & {
					confidential?: boolean;
				};
				if (get().activeSession?.session_id !== sid) {
					set({ isLoadingSources: false });
					return null;
				}
				const uploaded = normalizeSourceDoc(uploadedRaw);
				set((state) => ({
					sources: [uploaded, ...state.sources],
					isLoadingSources: false,
				}));
				return uploaded;
			} catch (err) {
				set({ isLoadingSources: false });
				if (get().activeSession?.session_id === activeSession.session_id) {
					get().addToast({
						type: "urn:opendv:error:network",
						title: "Network Error",
						status: 503,
						detail: String(err),
						code: "NETWORK_ERROR",
					});
				}
				return null;
			}
		},

		attachConnectorSource: async (
			req: ConnectorSourceInput,
			fetchImpl = fetch,
		) => {
			const activeSession = get().activeSession;
			if (!activeSession) return false;
			const sid = activeSession.session_id;
			set({ isLoadingSources: true });
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/sources/connectors`,
					{
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify(req),
					},
				);
				if (!res.ok) {
					const err = (await res.json().catch(() => ({}))) as {
						detail?: string;
						code?: string;
					};
					set({ isLoadingSources: false });
					get().addToast({
						type: "urn:opendv:error:connector-attach",
						title: "Connector Attach Failed",
						status: res.status,
						detail: err.detail || `Server returned HTTP ${res.status}`,
						code: err.code || "CORRUPT_SOURCE_BLOB",
					});
					return false;
				}
				await Promise.all([
					get().loadSources(fetchImpl, sid),
					get().loadEvidenceTables(fetchImpl, sid),
				]);
				set({ isLoadingSources: false });
				return true;
			} catch (err) {
				set({ isLoadingSources: false });
				get().addToast({
					type: "urn:opendv:error:network",
					title: "Network Error",
					status: 503,
					detail: String(err),
					code: "NETWORK_ERROR",
				});
				return false;
			}
		},

		addInlineNote: async (
			text: string,
			sourceName = "Inline Domain Note",
			doNotSendToAgent = false,
			fetchImpl = fetch,
		) => {
			const activeSession = get().activeSession;
			if (!activeSession || !text.trim()) return null;
			const sid = activeSession.session_id;
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/sources/text`,
					{
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({
							text,
							source_name: sourceName,
							format: "markdown",
							do_not_send_to_agent: doNotSendToAgent,
						}),
					},
				);
				if (!res.ok) return null;
				const uploaded = normalizeSourceDoc(
					(await res.json()) as SourceDocument & { confidential?: boolean },
				);
				set((state) => ({ sources: [uploaded, ...state.sources] }));
				return uploaded;
			} catch {
				return null;
			}
		},

		deleteSource: async (sourceId: string, fetchImpl = fetch) => {
			const activeSession = get().activeSession;
			if (!activeSession) return false;

			const sid = activeSession.session_id;
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/sources/${encodeURIComponent(sourceId)}`,
					{ method: "DELETE" },
				);
				if (get().activeSession?.session_id !== sid) return false;

				if (!res.ok) {
					const err = (await res.json().catch(() => ({}))) as {
						detail?: string;
						code?: string;
					};
					if (get().activeSession?.session_id !== sid) return false;
					get().addToast({
						type: "urn:opendv:error:source-delete",
						title: "Failed to Delete Source",
						status: res.status,
						detail: err.detail || `Delete failed with status ${res.status}`,
						code: err.code || "SOURCE_NOT_FOUND",
					});
					return false;
				}

				set((state) => ({
					sources: state.sources.filter((s) => s.source_id !== sourceId),
				}));
				return true;
			} catch (err) {
				if (get().activeSession?.session_id === sid) {
					get().addToast({
						type: "urn:opendv:error:network",
						title: "Network Error",
						status: 503,
						detail: String(err),
						code: "NETWORK_ERROR",
					});
				}
				return false;
			}
		},

		startParse: async (
			ocrMode: "auto" | "force" | "off" = "auto",
			fetchImpl = fetch,
		) => {
			const activeSession = get().activeSession;
			if (!activeSession) return null;

			const sid = activeSession.session_id;
			const myGen = jobMonitor.nextJobGeneration();
			jobMonitor.cancelActiveJobWatchers();

			set({
				parsingState: {
					status: "parsing",
					progress_pct: 10,
					active_job_id: null,
					stage: "queued",
					ocr_mode: ocrMode,
					evidence_chunks: [],
					error: null,
				},
			});

			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/parse`,
					{
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({ ocr_mode: ocrMode }),
					},
				);
				if (
					myGen !== jobMonitor.currentJobGeneration() ||
					get().activeSession?.session_id !== sid
				)
					return null;

				if (!res.ok) {
					const err = (await res.json().catch(() => ({}))) as {
						detail?: string;
						code?: string;
						error_code?: string;
						active_job_id?: string;
						job_id?: string;
					};
					if (
						myGen !== jobMonitor.currentJobGeneration() ||
						get().activeSession?.session_id !== sid
					)
						return null;
					const activeJobId = extractActiveSessionJobId(res.status, err);
					set({
						...(activeJobId ? { activeSessionJobId: activeJobId } : {}),
						parsingState: {
							status: "failed",
							progress_pct: 0,
							active_job_id: activeJobId,
							stage: "error",
							ocr_mode: ocrMode,
							evidence_chunks: [],
							error: err.detail || `Parse failed with status ${res.status}`,
						},
					});
					get().addToast({
						type: "urn:opendv:error:parse-failed",
						title: "Document Parsing Failed",
						status: res.status,
						detail: err.detail || `Failed with HTTP ${res.status}`,
						code: err.code || "SYNTAX_INVALID_ARGUMENT",
					});
					return null;
				}

				const job = (await res.json()) as {
					job_id: string;
					status?: string;
					stage?: string;
					pct_complete?: number;
					progress_pct?: number;
					progress?: number;
					chunks_count?: number;
					evidence_chunks?: EvidenceChunk[];
					result?: {
						evidence_chunks?: EvidenceChunk[];
						chunks_count?: number;
						pct_complete?: number;
						progress_pct?: number;
					};
				};
				if (
					myGen !== jobMonitor.currentJobGeneration() ||
					get().activeSession?.session_id !== sid
				)
					return null;
				const initialChunks =
					job.result?.evidence_chunks || job.evidence_chunks || [];
				const initialPct =
					job.pct_complete ??
					job.progress_pct ??
					job.progress ??
					job.result?.pct_complete ??
					job.result?.progress_pct;
				const initialChunksCount =
					job.chunks_count ??
					job.result?.chunks_count ??
					(initialChunks.length > 0 ? initialChunks.length : undefined);
				const isAlreadyDone =
					String(job.status || "").toUpperCase() === "COMPLETED" ||
					String(job.status || "").toUpperCase() === "SUCCEEDED";
				set((state) => ({
					activeSessionJobId: isAlreadyDone ? null : job.job_id,
					parsingState: {
						...state.parsingState,
						status: isAlreadyDone ? "completed" : "parsing",
						progress_pct: isAlreadyDone ? 100 : (initialPct ?? 35),
						active_job_id: job.job_id,
						stage: isAlreadyDone ? "completed" : job.stage || "layout_eval",
						evidence_chunks:
							initialChunks.length > 0
								? initialChunks
								: state.parsingState.evidence_chunks,
						...(initialChunksCount !== undefined
							? { chunks_count: initialChunksCount }
							: {}),
					},
				}));
				jobMonitor.appendSseEvent({
					job_id: job.job_id,
					stage: job.stage || "layout_eval",
					message: `Parse job ${job.job_id} started`,
					progress_pct: initialPct ?? 35,
					level: "info",
				});
				if (!isAlreadyDone && job.job_id) {
					jobMonitor.monitorStudioJob(sid, job.job_id, "parse", fetchImpl, {
						myGen,
					});
				} else if (isAlreadyDone) {
					void Promise.all([
						get().loadEvidenceTables(fetchImpl, sid),
						get().loadEvidenceChunks(fetchImpl, sid),
					]);
				}
				return job.job_id;
			} catch (err) {
				if (
					myGen === jobMonitor.currentJobGeneration() &&
					get().activeSession?.session_id === sid
				) {
					set({
						parsingState: {
							status: "failed",
							progress_pct: 0,
							active_job_id: null,
							stage: "error",
							ocr_mode: ocrMode,
							evidence_chunks: [],
							error: String(err),
						},
					});
					get().addToast({
						type: "urn:opendv:error:network",
						title: "Network Error",
						status: 503,
						detail: String(err),
						code: "NETWORK_ERROR",
					});
				}
				return null;
			}
		},

		updateParsingProgress: (update: Partial<ParsingState>) => {
			set((state) => ({
				parsingState: { ...state.parsingState, ...update },
			}));
		},

		loadSources: async (fetchImpl = fetch, targetSessionId?: string) => {
			const activeSession = get().activeSession;
			if (!activeSession) return;
			const expectedId = targetSessionId ?? activeSession.session_id;
			if (activeSession.session_id !== expectedId) return;

			const reqId = bumpSourcesReqId();
			set({ isLoadingSources: true });
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(expectedId)}/sources`,
				);
				if (!res.ok) {
					if (
						reqId === getSourcesReqId() &&
						get().activeSession?.session_id === expectedId
					) {
						set({ isLoadingSources: false });
						const err = (await res.json().catch(() => ({}))) as {
							detail?: string;
							code?: string;
						};
						get().addToast({
							type: "urn:opendv:error:sources-load-failed",
							title: "Failed to Load Sources",
							status: res.status,
							detail: err.detail || `Server returned HTTP ${res.status}`,
							code: err.code || "SOURCES_LOAD_FAILED",
						});
					}
					return;
				}
				if (
					reqId !== getSourcesReqId() ||
					get().activeSession?.session_id !== expectedId
				)
					return;

				const data = (await res.json()) as
					| Array<SourceDocument & { confidential?: boolean }>
					| { sources: Array<SourceDocument & { confidential?: boolean }> };

				if (
					reqId !== getSourcesReqId() ||
					get().activeSession?.session_id !== expectedId
				)
					return;

				const rawList = Array.isArray(data) ? data : data.sources || [];
				set({
					sources: rawList.map((item) => normalizeSourceDoc(item)),
					isLoadingSources: false,
				});
			} catch (err) {
				if (
					reqId === getSourcesReqId() &&
					get().activeSession?.session_id === expectedId
				) {
					set({ isLoadingSources: false });
					get().addToast({
						type: "urn:opendv:error:network",
						title: "Network Error",
						status: 503,
						detail: String(err),
						code: "NETWORK_ERROR",
					});
				}
			}
		},

		loadEvidenceTables: async (fetchImpl = fetch, targetSessionId?: string) => {
			const activeSession = get().activeSession;
			if (!activeSession) return;
			const sid = targetSessionId ?? activeSession.session_id;
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/evidence/tables`,
				);
				if (!res.ok || get().activeSession?.session_id !== sid) return;
				const data = (await res.json()) as {
					tables?: EvidenceTableProfile[];
				};
				const cappedTables = (data.tables ?? []).map((tbl) => ({
					...tbl,
					sample_rows: (tbl.sample_rows ?? []).slice(0, MAX_LOCAL_PREVIEW_ROWS),
				}));
				set({ evidenceTables: cappedTables });
			} catch {
				// ignore preview load failure
			}
		},

		loadEvidenceChunks: async (fetchImpl = fetch, targetSessionId?: string) => {
			const activeSession = get().activeSession;
			if (!activeSession) return;
			const sid = targetSessionId ?? activeSession.session_id;
			try {
				const res = await studioFetch(
					fetchImpl,
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/evidence/chunks`,
				);
				if (!res.ok || get().activeSession?.session_id !== sid) return;
				const data = (await res.json()) as {
					chunks?: EvidenceChunk[];
					observed_term_counts?: Record<string, number>;
				};
				set({
					evidenceChunks: data.chunks ?? [],
					observedTermCounts: data.observed_term_counts ?? {},
				});
			} catch {
				// ignore chunk load failure
			}
		},
	};
}
