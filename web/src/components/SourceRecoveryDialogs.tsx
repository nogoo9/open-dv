import { useEffect, useId, useRef, useState } from "react";
import { api, parseProblemDetails } from "../api/client";
import type {
	ConnectorConfigPayload,
	DocumentMap,
	ProblemDetails,
	SecretSummary,
	SourceSummary,
	TableIndexEntry,
	TablePreviewPage,
} from "../api/openapi";
import { useProjectStore } from "../store/projectStore";

export type RecoveryDialogMode = "preview" | "bind-secret" | "reattach" | null;
export interface SourceRecoveryDialogsProps {
	projectId: string;
	source: SourceSummary | null;
	mode: RecoveryDialogMode;
	documentMaps: DocumentMap[];
	connectionProfiles: Record<string, ConnectorConfigPayload>;
	secrets: SecretSummary[];
	onClose: () => void;
	onUpdated: () => Promise<void>;
}

const DOC_KINDS = new Set(["pdf", "markdown", "docx", "domain_note"]);
const SCROLL_ATTR: Record<string, unknown> = { tabIndex: 0 };
const TBL_ATTR: Record<string, string> = { role: "table" };
const ROW_ATTR: Record<string, string> = { role: "row" };
const CELL_ATTR: Record<string, string> = { role: "cell" };

export function SourceRecoveryDialogs({
	projectId,
	source,
	mode,
	documentMaps,
	connectionProfiles,
	secrets,
	onClose,
	onUpdated,
}: SourceRecoveryDialogsProps) {
	const { navigate, setDirtyFlag, setMissingSecretRefs, missingSecretRefs } =
		useProjectStore();
	const dlgRef = useRef<HTMLDialogElement>(null);
	const closeRef = useRef<() => void>(() => undefined);
	const titleId = useId();
	const open = Boolean(source && mode);

	const [tables, setTables] = useState<TableIndexEntry[]>([]);
	const [tid, setTid] = useState<string | null>(null);
	const [preview, setPreview] = useState<TablePreviewPage | null>(null);
	const [loading, setLoading] = useState(false);

	const [transport, setTransport] = useState<"path" | "upload">("path");
	const [uri, setUri] = useState("");
	const [host, setHost] = useState("");
	const [httpPath, setHttpPath] = useState("");
	const [catalog, setCatalog] = useState("");
	const [schema, setSchema] = useState("");
	const [tablesText, setTablesText] = useState("");
	const [refsText, setRefsText] = useState("");
	const [secKey, setSecKey] = useState("");
	const [secVal, setSecVal] = useState("");
	const [upFile, setUpFile] = useState<File | null>(null);
	const [dirtyAsk, setDirtyAsk] = useState(false);
	const [busy, setBusy] = useState(false);
	const [problem, setProblem] = useState<ProblemDetails | null>(null);

	useEffect(() => {
		if (!open || !source) return;
		setProblem(null);
		setDirtyAsk(false);
		setUpFile(null);
		setSecVal("");
		const cfg = source.connector_config;
		const o = cfg?.options ?? {};
		setUri(cfg?.uri_or_path ?? source.uri_or_filename);
		setHost(String(o.host ?? ""));
		setHttpPath(String(o.http_path ?? ""));
		setCatalog(String(o.catalog ?? ""));
		setSchema(String(o.schema ?? ""));
		setTablesText((cfg?.selected_tables ?? []).join(", "));
		const refs = cfg?.secret_refs ?? [];
		setRefsText(refs.join(", "));
		setSecKey(
			refs[0] ?? (source.kind === "databricks_uc" ? "DATABRICKS_TOKEN" : ""),
		);
		setTransport("path");
		setDirtyFlag("step2Secret", false);

		if (mode === "preview" && !DOC_KINDS.has(source.kind)) {
			setLoading(true);
			setPreview(null);
			void api
				.listTablesIndex(projectId)
				.then(async (idx) => {
					const m = idx.filter((t) => t.source_id === source.source_id);
					setTables(m);
					setTid(m[0]?.table_id ?? null);
					if (m[0])
						setPreview(
							await api.previewTable(projectId, m[0].table_id, {
								offset: 0,
								limit: 5,
								sourceId: source.source_id,
							}),
						);
				})
				.catch((e) => setProblem(parseProblemDetails(e)))
				.finally(() => setLoading(false));
		}
	}, [open, source, mode, projectId, setDirtyFlag]);

	const attemptClose = () => {
		if (busy) return;
		const cfg = source?.connector_config;
		const edited =
			mode !== "preview" &&
			source !== null &&
			(secVal.length > 0 ||
				upFile !== null ||
				uri !== (cfg?.uri_or_path ?? source.uri_or_filename) ||
				refsText !== (cfg?.secret_refs ?? []).join(", "));
		if (edited) {
			setDirtyAsk(true);
			return;
		}
		setDirtyFlag("step2Secret", false);
		onClose();
	};
	closeRef.current = mode === "preview" ? onClose : attemptClose;

	useEffect(() => {
		const el = dlgRef.current;
		if (!el) return;
		if (open && !el.open)
			el.showModal ? el.showModal() : el.setAttribute("open", "");
		else if (!open && el.open) {
			el.returnValue = "";
			el.close ? el.close() : el.removeAttribute("open");
		}
		const onClick = (e: MouseEvent) => {
			if (e.target === el) closeRef.current();
		};
		el.addEventListener("click", onClick);
		return () => el.removeEventListener("click", onClick);
	}, [open]);

	if (!source || !mode) return null;

	const isDoc = DOC_KINDS.has(source.kind);
	const docMap = isDoc
		? documentMaps.find((d) => d.doc_id === source.source_id)
		: undefined;
	const profiles = Object.entries(connectionProfiles).filter(
		([, p]) => p.adapter_id === source.kind,
	);
	const upd = (fn: (v: string) => void) => (v: string) => {
		fn(v);
		setDirtyFlag("step2Secret", true);
	};

	const applyProfile = (name: string) => {
		const p = connectionProfiles[name];
		if (!p) return;
		const o = { ...(p.options ?? {}) };
		const refs = [...(p.secret_refs ?? [])];
		setUri(p.uri_or_path);
		setHost(String(o.host ?? ""));
		setHttpPath(String(o.http_path ?? ""));
		setCatalog(String(o.catalog ?? ""));
		setSchema(String(o.schema ?? ""));
		setTablesText((p.selected_tables ?? []).join(", "));
		setRefsText(refs.join(", "));
		if (refs[0]) setSecKey(refs[0]);
		setDirtyFlag("step2Secret", true);
	};

	const onSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		setBusy(true);
		setProblem(null);
		try {
			const cleanKey = secKey.trim();
			if (secVal.trim() && cleanKey)
				await api.postSecret({ key: cleanKey, value: secVal });
			if (mode === "reattach" && transport === "upload") {
				if (!upFile) return;
				const fd = new FormData();
				fd.set("file", upFile);
				fd.set("kind", source.kind);
				await api.reattachSource(projectId, source.source_id, fd);
			} else {
				const cfg = source.connector_config;
				const prevOpts = cfg?.options ?? {};
				const nextOpts =
					source.kind === "databricks_uc"
						? {
								...prevOpts,
								host: host.trim().replace(/^https?:\/\//i, ""),
								http_path: httpPath.trim(),
								catalog: catalog.trim(),
								schema: schema.trim(),
							}
						: prevOpts;
				const prevUri = cfg?.uri_or_path ?? source.uri_or_filename;
				const nextUri =
					source.kind === "databricks_uc" && host.trim()
						? `https://${host
								.trim()
								.replace(/^https?:\/\//i, "")
								.replace(/\/+$/, "")}`
						: uri.trim();
				const refs = refsText
					.split(",")
					.map((x) => x.trim())
					.filter(Boolean);
				if (cleanKey && !refs.includes(cleanKey)) refs.push(cleanKey);
				const tbls = tablesText
					.split(",")
					.map((x) => x.trim())
					.filter(Boolean);
				await api.reattachSource(projectId, source.source_id, {
					uri_or_filename: nextUri && nextUri !== prevUri ? nextUri : null,
					options:
						JSON.stringify(nextOpts) !== JSON.stringify(prevOpts)
							? nextOpts
							: null,
					secret_refs:
						JSON.stringify(refs) !== JSON.stringify(cfg?.secret_refs ?? [])
							? refs
							: null,
					selected_tables:
						JSON.stringify(tbls) !== JSON.stringify(cfg?.selected_tables ?? [])
							? tbls
							: null,
				});
			}
			if (cleanKey && missingSecretRefs.includes(cleanKey)) {
				setMissingSecretRefs(missingSecretRefs.filter((k) => k !== cleanKey));
			}
			setDirtyFlag("step2Secret", false);
			await onUpdated();
			onClose();
		} catch (x) {
			setProblem(parseProblemDetails(x));
		} finally {
			setBusy(false);
		}
	};

	const previewRows = preview
		? preview.rows.map((cells, idx) => ({
				id: `${preview.table_id}-${idx}`,
				cells: preview.columns.map((col, ci) => ({
					id: `${col}-${idx}`,
					col,
					val: cells[ci] ?? "NULL",
				})),
			}))
		: [];
	const dbInputs: readonly [string, string, (v: string) => void][] = [
		["Workspace Host (HTTPS)", host, setHost],
		["SQL Warehouse HTTP Path", httpPath, setHttpPath],
		["Catalog", catalog, setCatalog],
		["Schema", schema, setSchema],
	];

	return (
		<dialog
			ref={dlgRef}
			className="dialog-panel"
			aria-labelledby={titleId}
			{...({
				closedby: mode === "preview" ? "any" : "closerequest",
			} as Record<string, string>)}
			onCancel={(e) => {
				e.preventDefault();
				closeRef.current();
			}}
		>
			<div className="stack-md">
				<div className="row-between">
					<div>
						<h2 id={titleId} className="dialog-title">
							{mode === "preview"
								? `Preview · ${source.uri_or_filename}`
								: mode === "bind-secret"
									? `Bind Secret Token · ${source.uri_or_filename}`
									: `Edit / Re-attach · ${source.uri_or_filename}`}
						</h2>
						<p className="text-sm text-muted mono">
							{source.source_id} · {source.kind} · {source.status}
						</p>
					</div>
					<button
						type="button"
						className="btn btn-ghost btn-sm"
						onClick={closeRef.current}
					>
						Close
					</button>
				</div>

				{problem && (
					<div className="banner banner-danger" role="alert">
						<strong>{problem.title}</strong> ({problem.error_code}):{" "}
						{problem.detail}
					</div>
				)}

				{mode === "preview" && isDoc && (
					<div className="stack-sm">
						{docMap ? (
							<>
								<div className="row">
									<span className="pill">Pages: {docMap.page_count}</span>
									<span className="pill">
										OCR boxes:{" "}
										{(docMap.pages ?? []).reduce(
											(a, p) => a + p.ocr_box_count,
											0,
										)}
									</span>
								</div>
								<ul style={{ margin: 0, paddingLeft: 18 }}>
									{(docMap.outline ?? []).map((n) => (
										<li key={`${n.page_start}-${n.title}`}>
											p.{n.page_start}: {n.title}
										</li>
									))}
								</ul>
							</>
						) : (
							<p className="text-sm text-muted">No DocumentMap cached.</p>
						)}
						<div className="row-end">
							<button
								type="button"
								className="btn btn-primary"
								onClick={() => {
									onClose();
									navigate(
										`/projects/${encodeURIComponent(projectId)}/step/3?sub=3b`,
									);
								}}
							>
								Inspect Pages in Step 3b →
							</button>
						</div>
					</div>
				)}

				{mode === "preview" && !isDoc && (
					<div className="stack-sm">
						{source.status === "METADATA_ONLY_BINARY_ABSENT" && (
							<div className="callout-teal">
								Offline preview from persisted evidence (binary absent).
							</div>
						)}
						{tables.length > 0 && (
							<select
								className="select"
								aria-label="Preview table"
								value={tid ?? ""}
								onChange={async (e) => {
									const nextTid = e.target.value;
									setTid(nextTid);
									setLoading(true);
									try {
										setPreview(
											await api.previewTable(projectId, nextTid, {
												offset: 0,
												limit: 5,
												sourceId: source.source_id,
											}),
										);
									} catch (x) {
										setProblem(parseProblemDetails(x));
									} finally {
										setLoading(false);
									}
								}}
							>
								{tables.map((t) => (
									<option key={t.table_id} value={t.table_id}>
										{t.table_name} ({t.col_count} cols)
									</option>
								))}
							</select>
						)}
						{loading ? (
							<p className="text-sm text-muted">Loading cached preview...</p>
						) : preview ? (
							<div className="table-scroll-region" {...SCROLL_ATTR}>
								<table className="responsive-table" {...TBL_ATTR}>
									<thead>
										<tr {...ROW_ATTR}>
											{preview.columns.map((c) => (
												<th key={c} scope="col">
													{c}
												</th>
											))}
										</tr>
									</thead>
									<tbody>
										{previewRows.map((row) => (
											<tr key={row.id} {...ROW_ATTR}>
												{row.cells.map((c) => (
													<td
														key={c.id}
														{...CELL_ATTR}
														data-label={c.col}
														className="mono"
													>
														{c.val}
													</td>
												))}
											</tr>
										))}
									</tbody>
								</table>
							</div>
						) : null}
					</div>
				)}

				{mode !== "preview" && (
					<form className="stack-md" onSubmit={onSubmit}>
						{dirtyAsk && (
							<div className="banner banner-warn row-between">
								<span>Discard unsaved edits?</span>
								<div className="row">
									<button
										type="button"
										className="btn btn-sm"
										onClick={() => setDirtyAsk(false)}
									>
										Keep Editing
									</button>
									<button
										type="button"
										className="btn btn-danger btn-sm"
										onClick={() => {
											setDirtyFlag("step2Secret", false);
											onClose();
										}}
									>
										Discard &amp; Close
									</button>
								</div>
							</div>
						)}

						{profiles.length > 0 && (
							<label className="field-col">
								<span className="field-label">Apply Saved Profile...</span>
								<select
									className="select"
									defaultValue=""
									onChange={(e) =>
										e.target.value && applyProfile(e.target.value)
									}
								>
									<option value="">Choose saved profile...</option>
									{profiles.map(([name]) => (
										<option key={name} value={name}>
											{name}
										</option>
									))}
								</select>
							</label>
						)}

						{mode === "reattach" && source.kind !== "databricks_uc" && (
							<div className="row">
								<button
									type="button"
									className={`btn btn-sm ${transport === "path" ? "btn-primary" : ""}`}
									onClick={() => setTransport("path")}
								>
									Workspace Path / URI
								</button>
								<button
									type="button"
									className={`btn btn-sm ${transport === "upload" ? "btn-primary" : ""}`}
									onClick={() => setTransport("upload")}
								>
									Upload Replacement File
								</button>
							</div>
						)}

						{mode === "reattach" && transport === "upload" ? (
							<label key="reattach-file" className="field-col">
								<span className="field-label">Replacement File</span>
								<input
									type="file"
									className="input"
									onChange={(e) => {
										setUpFile(e.target.files?.[0] ?? null);
										setDirtyFlag("step2Secret", true);
									}}
								/>
							</label>
						) : source.kind === "databricks_uc" ? (
							<div className="grid-2">
								{dbInputs.map(([lbl, val, setter]) => (
									<label key={lbl} className="field-col">
										<span className="field-label">{lbl}</span>
										<input
											className="input mono"
											value={val}
											onChange={(e) => upd(setter)(e.target.value)}
										/>
									</label>
								))}
							</div>
						) : (
							<label key="reattach-uri" className="field-col">
								<span className="field-label">Workspace File Path / URI</span>
								<input
									className="input mono"
									value={uri}
									onChange={(e) => upd(setUri)(e.target.value)}
								/>
							</label>
						)}

						<label className="field-col">
							<span className="field-label">Selected Tables (optional)</span>
							<input
								className="input mono"
								value={tablesText}
								onChange={(e) => upd(setTablesText)(e.target.value)}
							/>
						</label>

						{(source.kind === "databricks_uc" || mode === "bind-secret") && (
							<div className="card-cream grid-2">
								<label className="field-col">
									<span className="field-label">Secret Key Ref</span>
									<input
										className="input mono"
										value={secKey}
										onChange={(e) => upd(setSecKey)(e.target.value)}
									/>
								</label>
								<label className="field-col">
									<span className="field-label">Secret Token (Write-Only)</span>
									<input
										type="password"
										className="input mono"
										value={secVal}
										onChange={(e) => upd(setSecVal)(e.target.value)}
										placeholder={
											secrets.some((s) => s.key === secKey && s.present)
												? "Bound (enter to replace)"
												: "Paste token..."
										}
									/>
								</label>
							</div>
						)}

						<div className="row-end">
							<button
								type="button"
								className="btn"
								disabled={busy}
								onClick={attemptClose}
							>
								Cancel
							</button>
							<button
								type="submit"
								className="btn btn-primary"
								disabled={
									busy ||
									(mode === "reattach" && transport === "upload" && !upFile)
								}
							>
								{busy
									? "Saving..."
									: mode === "bind-secret"
										? "Save Secret & Bind"
										: "Save & Re-attach"}
							</button>
						</div>
					</form>
				)}
			</div>
		</dialog>
	);
}
