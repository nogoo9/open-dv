import { useCallback, useEffect, useRef, useState } from "react";
import { api, buildSourceInspectReq, parseProblemDetails } from "../api/client";
import {
	type ConnectorAdapterSpec,
	MAX_UPLOAD_BYTES,
	type ProblemDetails,
	type SecretSummary,
	type SourceProfile,
	type SourceSummary,
	inferUploadKind,
} from "../api/openapi";
import {
	type RecoveryDialogMode,
	SourceRecoveryDialogs,
} from "../components/SourceRecoveryDialogs";
import { parseAppRoute, useProjectStore } from "../store/projectStore";

const FALLBACK_ADAPTERS: ConnectorAdapterSpec[] = [
	{ adapter_id: "sqlite", display_name: "SQLite Database", fields: [] },
	{ adapter_id: "duckdb", display_name: "DuckDB Database", fields: [] },
	{ adapter_id: "parquet", display_name: "Apache Parquet File", fields: [] },
	{ adapter_id: "csv", display_name: "CSV Table", fields: [] },
	{ adapter_id: "xlsx", display_name: "Excel Workbook (XLSX)", fields: [] },
	{
		adapter_id: "databricks_uc",
		display_name: "Databricks Unity Catalog",
		fields: [],
	},
];

const noteName = (raw: string) => {
	const slug = raw
		.trim()
		.toLowerCase()
		.replace(/[^a-z0-9_-]+/g, "-")
		.replace(/^-+|-+$/g, "");
	return slug ? `${slug}.md` : "domain-note.md";
};

export function Step2Sources() {
	const {
		currentUrl,
		activeProject,
		sources,
		documentMaps,
		settings,
		ontologyBundle,
		missingSecretRefs,
		setSources,
		setDocumentMaps,
		setActiveProject,
		setSettings,
		navigate,
		announce,
	} = useProjectStore();
	const projectId =
		activeProject?.project_id ??
		parseAppRoute(new URL(currentUrl, "http://localhost").pathname).projectId;
	const fileRef = useRef<HTMLInputElement>(null);

	const [adapters, setAdapters] =
		useState<ConnectorAdapterSpec[]>(FALLBACK_ADAPTERS);
	const [secrets, setSecrets] = useState<SecretSummary[]>([]);
	const [kind, setKind] = useState("sqlite");
	const [uri, setUri] = useState("");
	const [host, setHost] = useState("");
	const [httpPath, setHttpPath] = useState("");
	const [catalog, setCatalog] = useState("");
	const [schema, setSchema] = useState("default");
	const [tablesText, setTablesText] = useState("");
	const [refsText, setRefsText] = useState("DATABRICKS_TOKEN");
	const [timeoutSec, setTimeoutSec] = useState(30);
	const [profile, setProfile] = useState<SourceProfile | null>(null);
	const [noteTitle, setNoteTitle] = useState("");
	const [noteBody, setNoteBody] = useState("");
	const [busy, setBusy] = useState(false);
	const [problem, setProblem] = useState<ProblemDetails | null>(null);
	const [notice, setNotice] = useState<string | null>(null);
	const [dlg, setDlg] = useState<{
		source: SourceSummary | null;
		mode: RecoveryDialogMode;
	}>({ source: null, mode: null });

	const refresh = useCallback(async () => {
		if (!projectId) return;
		const [srcs, docs, sec, proj] = await Promise.all([
			api.listSources(projectId),
			api.listDocumentMaps(projectId),
			api.listSecrets().catch(() => [] as SecretSummary[]),
			api.getProject(projectId).catch(() => null),
		]);
		setSources(srcs);
		setDocumentMaps(docs);
		setSecrets(sec);
		if (proj) setActiveProject(proj);
	}, [projectId, setSources, setDocumentMaps, setActiveProject]);

	useEffect(() => {
		let ok = true;
		void api
			.listSourceAdapters()
			.then((r) => ok && r.length > 0 && setAdapters(r))
			.catch(() => undefined);
		void api
			.listSecrets()
			.then((r) => ok && setSecrets(r))
			.catch(() => undefined);
		if (!settings) {
			void api
				.getSettings()
				.then((ws) => ok && setSettings(ws))
				.catch(() => undefined);
		}
		if (projectId) {
			void Promise.all([
				api.listSources(projectId),
				api.listDocumentMaps(projectId),
			])
				.then(([srcs, docs]) => {
					if (!ok) return;
					setSources(srcs);
					setDocumentMaps(docs);
				})
				.catch(() => undefined);
		}
		return () => {
			ok = false;
		};
	}, [projectId, settings, setSettings, setSources, setDocumentMaps]);

	if (!projectId) {
		return (
			<section className="panel stack-md">
				<h1 className="h1-serif">Step 2 · Sources &amp; Secrets</h1>
				<p className="text-muted">Select or create a project in Step 1.</p>
				<button
					type="button"
					className="btn btn-primary"
					onClick={() => navigate("/")}
				>
					Go to Step 1
				</button>
			</section>
		);
	}

	const refs =
		kind === "databricks_uc"
			? refsText
					.split(",")
					.map((x) => x.trim())
					.filter(Boolean)
			: [];
	const inspectReq = () =>
		buildSourceInspectReq({
			kind,
			uri_or_path: uri,
			host,
			options:
				kind === "databricks_uc"
					? {
							host: host.trim().replace(/^https?:\/\//i, ""),
							http_path: httpPath.trim(),
							catalog: catalog.trim(),
							schema: schema.trim() || "default",
						}
					: {},
			secret_refs: refs,
		});

	const onInspect = async () => {
		setBusy(true);
		setProblem(null);
		setProfile(null);
		try {
			setProfile(await api.inspectSource(inspectReq()));
		} catch (x) {
			setProblem(parseProblemDetails(x));
		} finally {
			setBusy(false);
		}
	};

	const onAttach = async (e: React.FormEvent) => {
		e.preventDefault();
		setBusy(true);
		setProblem(null);
		try {
			const req = inspectReq();
			const tbls = tablesText
				.split(",")
				.map((x) => x.trim())
				.filter(Boolean);
			const added = await api.attachSource(projectId, {
				kind,
				uri_or_filename: req.uri_or_path,
				options: req.options ?? {},
				secret_refs: req.secret_refs ?? [],
				selected_tables: tbls.length ? tbls : null,
				profile_timeout_seconds: Math.max(1, Math.trunc(timeoutSec || 30)),
			});
			await refresh();
			setProfile(null);
			announce("polite", `Attached ${added.uri_or_filename}.`);
		} catch (x) {
			setProblem(parseProblemDetails(x));
		} finally {
			setBusy(false);
		}
	};

	const onUpload = async (file: File | null | undefined) => {
		if (!file) return;
		setProblem(null);
		setNotice(null);
		if (file.name.toLowerCase().endsWith(".ttl")) {
			setNotice("Turtle (.ttl) is export-only in Step 5.");
			return;
		}
		if (file.size > MAX_UPLOAD_BYTES) {
			setNotice(`File exceeds 250 MB (${MAX_UPLOAD_BYTES} bytes) limit.`);
			return;
		}
		const upKind = inferUploadKind(file.name);
		if (!upKind) {
			setNotice(`Unsupported extension for '${file.name}'.`);
			return;
		}
		setBusy(true);
		try {
			const fd = new FormData();
			fd.set("file", file);
			fd.set("kind", upKind);
			const added = await api.uploadSource(projectId, fd);
			await refresh();
			announce("polite", `Uploaded ${added.uri_or_filename}.`);
		} catch (x) {
			setProblem(parseProblemDetails(x));
		} finally {
			setBusy(false);
		}
	};

	const onSaveNote = async (e: React.FormEvent) => {
		e.preventDefault();
		if (!noteBody.trim()) {
			setNotice("Enter domain note text before saving.");
			return;
		}
		setBusy(true);
		setProblem(null);
		setNotice(null);
		try {
			const fname = noteName(noteTitle);
			const fd = new FormData();
			fd.set(
				"file",
				new File([noteBody], fname, { type: "text/markdown;charset=utf-8" }),
			);
			fd.set("kind", "domain_note");
			await api.uploadSource(projectId, fd);
			setNoteTitle("");
			setNoteBody("");
			await refresh();
			announce("polite", `Saved domain note ${fname}.`);
		} catch (x) {
			setProblem(parseProblemDetails(x));
		} finally {
			setBusy(false);
		}
	};

	const totalTables = sources.reduce((sum, s) => sum + s.table_count, 0);
	const totalPages = sources.reduce((sum, s) => sum + (s.page_count ?? 0), 0);
	const totalOcr = documentMaps.reduce(
		(sum, d) =>
			sum + (d.pages ?? []).reduce((ps, p) => ps + p.ocr_box_count, 0),
		0,
	);
	const missingDb =
		kind === "databricks_uc"
			? refs.filter((r) => !secrets.some((s) => s.key === r && s.present))
			: [];
	const dbFields: readonly [string, string, (v: string) => void][] = [
		["Workspace Host (HTTPS)", host, setHost],
		["SQL Warehouse HTTP Path", httpPath, setHttpPath],
		["Unity Catalog", catalog, setCatalog],
		["Schema", schema, setSchema],
	];

	return (
		<section className="stack-md">
			<div className="row-between">
				<div>
					<h1 className="h1-serif">Step 2 · Sources &amp; Secrets</h1>
					<p className="wb-sub">
						Zero-LLM profiling &amp; synchronous DocumentMap indexing (no parse
						queues or VLM toggles).
					</p>
				</div>
				<button
					type="button"
					className="btn btn-primary"
					onClick={() =>
						navigate(`/projects/${encodeURIComponent(projectId)}/step/3`)
					}
				>
					Proceed to Step 3 Preview →
				</button>
			</div>

			{problem && (
				<div className="banner banner-danger" role="alert">
					<strong>{problem.title}</strong> ({problem.error_code}):{" "}
					{problem.detail}
				</div>
			)}

			{missingSecretRefs.length > 0 && (
				<div className="banner banner-warn">
					Missing local secret bindings:{" "}
					<span className="mono">{missingSecretRefs.join(", ")}</span>.
				</div>
			)}

			<div
				style={{
					display: "flex",
					flexWrap: "wrap",
					gap: 14,
					alignItems: "flex-start",
				}}
			>
				<div className="stack-md" style={{ flex: "48 1 320px", minWidth: 0 }}>
					<form className="panel stack-sm" onSubmit={onAttach}>
						<h2 className="h2-serif">
							Structured Connectors ({adapters.length})
						</h2>
						<div className="row">
							{adapters.map((a) => (
								<button
									key={a.adapter_id}
									type="button"
									className={`btn btn-sm ${kind === a.adapter_id ? "btn-primary" : ""}`}
									onClick={() => {
										setKind(a.adapter_id);
										setProfile(null);
									}}
								>
									{a.display_name}
								</button>
							))}
						</div>

						{kind === "databricks_uc" ? (
							<div className="stack-sm">
								<div className="grid-2">
									{dbFields.map(([lbl, val, setter]) => (
										<label key={lbl} className="field-col">
											<span className="field-label">{lbl}</span>
											<input
												className="input mono"
												value={val}
												onChange={(e) => setter(e.target.value)}
											/>
										</label>
									))}
									<label className="field-col">
										<span className="field-label">Secret Key Refs</span>
										<input
											className="input mono"
											value={refsText}
											onChange={(e) => setRefsText(e.target.value)}
										/>
									</label>
									<label className="field-col">
										<span className="field-label">Profile Timeout (s)</span>
										<input
											type="number"
											min={1}
											className="input"
											value={timeoutSec}
											onChange={(e) =>
												setTimeoutSec(Number(e.target.value) || 30)
											}
										/>
									</label>
								</div>
								{missingDb.length > 0 && (
									<div className="banner banner-warn">
										Missing keyring secret(s):{" "}
										<span className="mono">{missingDb.join(", ")}</span>
									</div>
								)}
							</div>
						) : (
							<label className="field-col">
								<span className="field-label">Workspace File Path</span>
								<input
									className="input mono"
									value={uri}
									onChange={(e) => setUri(e.target.value)}
									placeholder="data/public_am/am_sec_treasury_ibor.sqlite"
								/>
							</label>
						)}

						<label className="field-col">
							<span className="field-label">Selected Tables (optional)</span>
							<input
								className="input mono"
								value={tablesText}
								onChange={(e) => setTablesText(e.target.value)}
							/>
						</label>
						<div className="row-end">
							<button
								type="button"
								className="btn"
								disabled={busy}
								onClick={onInspect}
							>
								Inspect Source
							</button>
							<button type="submit" className="btn btn-primary" disabled={busy}>
								Attach Source
							</button>
						</div>
						{profile && (
							<div className="card-cream text-sm">
								<strong>Preview: {profile.table_count} table(s)</strong> ·{" "}
								<span className="mono">
									fp {profile.schema_fingerprint.slice(0, 12)}
								</span>
							</div>
						)}
					</form>

					<section
						className="panel stack-sm"
						onDragOver={(e) => e.preventDefault()}
						onDrop={(e) => {
							e.preventDefault();
							void onUpload(e.dataTransfer.files?.[0]);
						}}
					>
						<h2 className="h2-serif">Upload File (≤ 250 MB)</h2>
						<p className="text-sm text-muted mono">
							.sqlite .db .sqlite3 .duckdb .ddb .parquet .csv .xlsx .pdf .md
							.markdown .docx
						</p>
						<input
							ref={fileRef}
							type="file"
							className="visually-hidden"
							aria-label="Choose source file to upload"
							accept=".sqlite,.db,.sqlite3,.duckdb,.ddb,.parquet,.csv,.xlsx,.pdf,.md,.markdown,.docx,.ttl"
							onChange={(e) => {
								void onUpload(e.target.files?.[0]);
								e.target.value = "";
							}}
						/>
						<button
							type="button"
							className="btn btn-secondary"
							style={{ alignSelf: "flex-start" }}
							disabled={busy}
							onClick={() => fileRef.current?.click()}
						>
							Choose File
						</button>
						{notice && <div className="banner banner-warn">{notice}</div>}
					</section>

					<form className="panel stack-sm" onSubmit={onSaveNote}>
						<h2 className="h2-serif">Inline Domain Note (UTF-8)</h2>
						<div className="callout-amber text-sm">
							<strong>MNPI Compliance Notice:</strong> Do not paste contextual
							Material Non-Public Information when using hosted LLM providers.
						</div>
						<label className="field-col">
							<span className="field-label">Note Slug (optional)</span>
							<input
								className="input"
								value={noteTitle}
								onChange={(e) => setNoteTitle(e.target.value)}
								placeholder="defaults to domain-note.md"
							/>
						</label>
						<label className="field-col">
							<span className="field-label">Markdown Note Body</span>
							<textarea
								rows={3}
								className="textarea"
								value={noteBody}
								onChange={(e) => setNoteBody(e.target.value)}
							/>
						</label>
						<div className="row-end">
							<button
								type="submit"
								className="btn btn-secondary"
								disabled={busy}
							>
								Save Domain Note
							</button>
						</div>
					</form>
				</div>

				<section
					className="panel stack-md"
					style={{ flex: "52 1 320px", minWidth: 0 }}
				>
					<div className="row-between">
						<h2 className="h2-serif">Attached Sources ({sources.length})</h2>
						<div className="row">
							<span className="pill">{totalTables} tables</span>
							<span className="pill">{totalPages} pages</span>
							<span className="pill">{totalOcr} OCR boxes</span>
						</div>
					</div>
					{sources.length === 0 ? (
						<p className="text-sm text-muted">No sources attached yet.</p>
					) : (
						sources.map((raw) => {
							const evo = ontologyBundle?.evolution_locks;
							const inferred =
								raw.locked_in_wave ??
								(evo &&
								Object.keys(evo.locked_source_columns ?? {}).some((tid) =>
									tid.startsWith(`${raw.source_id}:`),
								)
									? `W${String(evo.wave_number).padStart(3, "0")}`
									: null);
							const s =
								inferred === raw.locked_in_wave
									? raw
									: { ...raw, locked_in_wave: inferred };
							const lock = s.locked_in_wave
								? s.locked_in_wave.startsWith("LOCK-")
									? s.locked_in_wave
									: `LOCK-${s.locked_in_wave}`
								: null;
							const needsSec =
								s.status === "NEEDS_LOCAL_SECRET_BINDING" ||
								(s.connector_config?.secret_refs?.length ?? 0) > 0;
							return (
								<article key={s.source_id} className="card-subtle stack-sm">
									<div className="row-between">
										<div>
											<strong className="mono">{s.uri_or_filename}</strong>
											<div className="text-sm text-muted mono">
												{s.source_id} · {s.kind} · {s.table_count}t ·{" "}
												{s.page_count ?? 0}p
											</div>
										</div>
										<div className="row">
											{lock && <span className="pill pill-dark">{lock}</span>}
											<span
												className={`pill ${s.status === "READY_OFFLINE" ? "pill-ok" : s.status === "NEEDS_LOCAL_SECRET_BINDING" ? "pill-warn" : "pill-err"}`}
											>
												{s.status}
											</span>
										</div>
									</div>
									<div className="row-end">
										<button
											type="button"
											className="btn btn-sm"
											onClick={() => setDlg({ source: s, mode: "preview" })}
										>
											Preview
										</button>
										{needsSec && (
											<button
												type="button"
												className="btn btn-sm btn-secondary"
												onClick={() =>
													setDlg({ source: s, mode: "bind-secret" })
												}
											>
												Bind Secret Token
											</button>
										)}
										<button
											type="button"
											className="btn btn-sm"
											onClick={() => setDlg({ source: s, mode: "reattach" })}
										>
											{s.locked_in_wave ? "Edit" : "Re-attach"}
										</button>
										<button
											type="button"
											className="btn btn-danger btn-sm"
											disabled={busy || Boolean(s.locked_in_wave)}
											onClick={async () => {
												setBusy(true);
												try {
													await api.detachSource(projectId, s.source_id);
													await refresh();
													announce("polite", `Detached ${s.uri_or_filename}.`);
												} catch (x) {
													setProblem(parseProblemDetails(x));
												} finally {
													setBusy(false);
												}
											}}
										>
											{s.locked_in_wave ? "Detach 🔒" : "Detach"}
										</button>
									</div>
								</article>
							);
						})
					)}
				</section>
			</div>

			<SourceRecoveryDialogs
				projectId={projectId}
				source={dlg.source}
				mode={dlg.mode}
				documentMaps={documentMaps}
				connectionProfiles={settings?.connection_profiles ?? {}}
				secrets={secrets}
				onClose={() => setDlg({ source: null, mode: null })}
				onUpdated={refresh}
			/>
		</section>
	);
}
