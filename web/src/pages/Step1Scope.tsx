import { useEffect, useId, useRef, useState } from "react";
import { api, parseProblemDetails } from "../api/client";
import {
	type ProblemDetails,
	type ProjectSummary,
	type StarterPackPreview,
	type StarterPackSummary,
	type WorkspaceSeedSummary,
	validateDomainNamespace,
	validateProjectId,
} from "../api/openapi";
import { useProjectStore } from "../store/projectStore";

type Mode = "blank" | "workspace_seed" | "starter_pack" | "uploaded_baseline";
const MODES: readonly [Mode, string][] = [
	["blank", "Blank Genesis (v0.0.0)"],
	["workspace_seed", "Workspace Seed"],
	["starter_pack", "Starter Pack"],
	["uploaded_baseline", "Uploaded Ontology Baseline"],
];
const NS_PRESETS = [
	"asset_management",
	"core_spine",
	"public_equities_oms",
	"fixed_income_rates",
	"multi_asset_ibor",
	"private_equity_buyout",
	"private_credit_direct_lending",
	"custody_clearing",
	"finance",
] as const;
const sortDesc = (xs: ProjectSummary[]) =>
	[...xs].sort((a, b) => b.updated_at.localeCompare(a.updated_at));

export function Step1Scope() {
	const {
		activeProject,
		projectsSummary,
		setProjectsSummary,
		hydrateProjectShell,
		navigate,
		openExportImportModal,
		announce,
	} = useProjectStore();
	const nsListId = useId();
	const delDlg = useRef<HTMLDialogElement>(null);
	const fileRef = useRef<HTMLInputElement>(null);

	const [pid, setPid] = useState("");
	const [ns, setNs] = useState("asset_management");
	const [mode, setMode] = useState<Mode>("blank");
	const [seeds, setSeeds] = useState<WorkspaceSeedSummary[]>([]);
	const [seedId, setSeedId] = useState("");
	const [packs, setPacks] = useState<StarterPackSummary[]>([]);
	const [packId, setPackId] = useState("core_spine");
	const [preview, setPreview] = useState<StarterPackPreview | null>(null);
	const [fmt, setFmt] = useState<"yaml" | "json">("yaml");
	const [baseTxt, setBaseTxt] = useState("");
	const [baseFile, setBaseFile] = useState<string | null>(null);
	const [notice, setNotice] = useState<{
		tone: "warn" | "ok";
		text: string;
	} | null>(null);
	const [tried, setTried] = useState(false);
	const [busy, setBusy] = useState(false);
	const [err, setErr] = useState<ProblemDetails | null>(null);
	const [delOpen, setDelOpen] = useState(false);
	const [deleting, setDeleting] = useState(false);

	useEffect(() => {
		let ok = true;
		void api
			.listStarterPacks()
			.then((r) => {
				if (!ok) return;
				setPacks(r);
				if (r[0] && !r.some((p) => p.pack_id === packId))
					setPackId(r[0].pack_id);
			})
			.catch(() => undefined);
		void api
			.listProjects()
			.then((r) => ok && setProjectsSummary(r))
			.catch(() => undefined);
		return () => {
			ok = false;
		};
	}, [packId, setProjectsSummary]);

	useEffect(() => {
		if (mode !== "workspace_seed") return;
		let ok = true;
		const f = validateDomainNamespace(ns) ? undefined : ns;
		void api
			.listWorkspaceSeeds(f)
			.then((r) => {
				if (!ok) return;
				setSeeds(r);
				if (r[0] && !r.some((s) => s.seed_id === seedId))
					setSeedId(r[0].seed_id);
			})
			.catch(() => ok && setSeeds([]));
		return () => {
			ok = false;
		};
	}, [mode, ns, seedId]);

	useEffect(() => {
		if (mode !== "starter_pack" || !packId) return;
		let ok = true;
		void api
			.previewStarterPack(packId)
			.then((r) => ok && setPreview(r))
			.catch(() => ok && setPreview(null));
		return () => {
			ok = false;
		};
	}, [mode, packId]);

	useEffect(() => {
		const el = delDlg.current;
		if (!el) return;
		if (delOpen && !el.open)
			el.showModal ? el.showModal() : el.setAttribute("open", "");
		else if (!delOpen && el.open) {
			el.returnValue = "";
			el.close ? el.close() : el.removeAttribute("open");
		}
		const onClk = (e: MouseEvent) => {
			if (e.target === el && !deleting) setDelOpen(false);
		};
		el.addEventListener("click", onClk);
		return () => el.removeEventListener("click", onClk);
	}, [delOpen, deleting]);

	const idErr = pid || tried ? validateProjectId(pid) : null;
	const nsErr = ns || tried ? validateDomainNamespace(ns) : null;

	const onPick = async (f: File | null | undefined) => {
		if (!f) return;
		const lo = f.name.toLowerCase();
		setNotice(null);
		if (lo.endsWith(".opendv.zip")) {
			openExportImportModal(f);
			setNotice({
				tone: "ok",
				text: `Staged ${f.name} in Export / Import modal.`,
			});
		} else if (lo.endsWith(".ttl")) {
			setNotice({
				tone: "warn",
				text: "Turtle (.ttl) is export-only in Step 5; drop .yaml, .json, or .opendv.zip.",
			});
		} else if (
			lo.endsWith(".yaml") ||
			lo.endsWith(".yml") ||
			lo.endsWith(".json")
		) {
			setMode("uploaded_baseline");
			setFmt(lo.endsWith(".json") ? "json" : "yaml");
			setBaseTxt(await f.text());
			setBaseFile(f.name);
			setNotice({
				tone: "ok",
				text: `Loaded ${f.name} into Uploaded Ontology Baseline.`,
			});
		} else {
			setNotice({
				tone: "warn",
				text: `Unsupported '${f.name}'. Use .opendv.zip, .yaml, or .json.`,
			});
		}
	};

	const onCreate = async (e: React.FormEvent) => {
		e.preventDefault();
		setTried(true);
		setErr(null);
		if (validateProjectId(pid) || validateDomainNamespace(ns)) return;
		if (mode === "uploaded_baseline" && !baseTxt.trim()) {
			setNotice({
				tone: "warn",
				text: "Provide a non-empty .yaml or .json baseline.",
			});
			return;
		}
		if (mode === "workspace_seed" && !seedId) {
			setNotice({ tone: "warn", text: "Select a matching workspace seed." });
			return;
		}
		setBusy(true);
		try {
			const c = await api.initProject({
				project_id: pid,
				domain_namespace: ns,
				baseline_format: fmt,
				starter_pack_id: mode === "starter_pack" ? packId : null,
				workspace_seed_id: mode === "workspace_seed" ? seedId : null,
				baseline_content: mode === "uploaded_baseline" ? baseTxt : null,
			});
			setProjectsSummary(await api.listProjects());
			await hydrateProjectShell(c.project_id);
			announce("polite", `Project ${c.project_id} initialized.`);
			navigate(`/projects/${encodeURIComponent(c.project_id)}/step/1`);
		} catch (x) {
			setErr(parseProblemDetails(x));
		} finally {
			setBusy(false);
		}
	};

	const onDelete = async () => {
		if (!activeProject || activeProject.has_active_lease) return;
		const victim = activeProject.project_id;
		setDeleting(true);
		setErr(null);
		try {
			await api.deleteProject(victim);
			const rest = sortDesc(await api.listProjects());
			setProjectsSummary(rest);
			setDelOpen(false);
			announce("polite", `Deleted project ${victim}.`);
			if (rest[0]) {
				await hydrateProjectShell(rest[0].project_id);
				navigate(`/projects/${encodeURIComponent(rest[0].project_id)}/step/1`, {
					replace: true,
				});
			} else {
				useProjectStore.setState({
					activeProject: null,
					sources: [],
					documentMaps: [],
					ontologyBundle: null,
					vaultIr: null,
					activeStep4Run: null,
					activeStep6Run: null,
					failedVerifyWaveId: null,
				});
				navigate("/", { replace: true });
			}
		} catch (x) {
			setErr(parseProblemDetails(x));
			setDelOpen(false);
		} finally {
			setDeleting(false);
		}
	};

	const sorted = sortDesc(projectsSummary);
	const concepts = preview?.ontology?.concepts ?? [];

	return (
		<section className="stack-md">
			<div className="row-between">
				<div>
					<h1 className="h1-serif">Step 1 · Scope &amp; Seeds</h1>
					<p className="wb-sub">
						Initialize a project scope or resume an existing project.
					</p>
				</div>
				{activeProject && (
					<button
						type="button"
						className="btn btn-primary"
						onClick={() =>
							navigate(
								`/projects/${encodeURIComponent(activeProject.project_id)}/step/2`,
							)
						}
					>
						Proceed to Step 2 Sources →
					</button>
				)}
			</div>

			{err && (
				<div className="banner banner-danger" role="alert">
					<strong>{err.title}</strong> ({err.error_code}): {err.detail}
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
				<form
					className="panel stack-md"
					style={{ flex: "58 1 340px", minWidth: 0 }}
					onSubmit={onCreate}
				>
					<h2 className="h2-serif">Initialize New Project</h2>
					<div className="grid-2">
						<label className="field-col">
							<span className="field-label">Project ID</span>
							<input
								className="input mono"
								value={pid}
								onChange={(e) => setPid(e.target.value)}
								placeholder="e.g. am-ibor-2026"
								aria-invalid={Boolean(idErr)}
							/>
							{idErr && (
								<span
									className="text-sm"
									style={{ color: "var(--danger-ink)" }}
								>
									{idErr}
								</span>
							)}
						</label>
						<label className="field-col">
							<span className="field-label">Domain Namespace</span>
							<input
								className="input mono"
								list={nsListId}
								value={ns}
								onChange={(e) => setNs(e.target.value)}
								aria-invalid={Boolean(nsErr)}
							/>
							<datalist id={nsListId}>
								{NS_PRESETS.map((n) => (
									<option key={n} value={n} />
								))}
							</datalist>
							{nsErr && (
								<span
									className="text-sm"
									style={{ color: "var(--danger-ink)" }}
								>
									{nsErr}
								</span>
							)}
						</label>
					</div>

					<fieldset className="radio-group">
						<legend className="text-sm font-semibold">Baseline Mode</legend>
						{MODES.map(([v, l]) => (
							<label key={v} className="radio-row">
								<input
									type="radio"
									name="step1_mode"
									checked={mode === v}
									onChange={() => setMode(v)}
								/>
								<span>{l}</span>
							</label>
						))}
					</fieldset>

					{mode === "workspace_seed" && (
						<label className="card-subtle field-col">
							<span className="field-label">Workspace Seeds</span>
							<select
								className="select"
								value={seedId}
								onChange={(e) => {
									setSeedId(e.target.value);
									const s = seeds.find((x) => x.seed_id === e.target.value);
									if (s) setNs(s.domain_namespace);
								}}
							>
								{seeds.length === 0 ? (
									<option value="">No seeds for '{ns}'</option>
								) : (
									seeds.map((s) => (
										<option key={s.seed_id} value={s.seed_id}>
											{s.relpath} ({s.domain_namespace} · {s.concept_count})
										</option>
									))
								)}
							</select>
						</label>
					)}

					{mode === "starter_pack" && (
						<div className="card-cream stack-sm">
							<label className="field-col">
								<span className="field-label">Starter Pack Preset</span>
								<select
									className="select"
									value={packId}
									onChange={(e) => setPackId(e.target.value)}
								>
									{packs.map((p) => (
										<option key={p.pack_id} value={p.pack_id}>
											{p.title} ({p.pack_id} · {p.hub_count} hubs)
										</option>
									))}
								</select>
							</label>
							{preview && (
								<div className="stack-sm">
									<div className="row-between text-sm">
										<strong>
											{preview.pack_id} (core_spine + planned stubs)
										</strong>
										<span className="pill">{concepts.length} concepts</span>
									</div>
									<div className="row" style={{ gap: 6 }}>
										{concepts.map((c) => (
											<span key={c.concept_uri} className="pill">
												{c.concept_name}
												{(c.table_bindings?.length ?? 0) === 0 ||
												c.governance_tags?.includes("planned_stub")
													? " · Planned Stub (is_planned_concept=true)"
													: ""}
											</span>
										))}
									</div>
								</div>
							)}
						</div>
					)}

					{mode === "uploaded_baseline" && (
						<div className="card-subtle stack-sm">
							<div className="row-between">
								<span className="field-label">
									Baseline ({fmt.toUpperCase()})
									{baseFile ? ` · ${baseFile}` : ""}
								</span>
								<select
									className="select"
									style={{ width: "auto" }}
									aria-label="Baseline format"
									value={fmt}
									onChange={(e) => setFmt(e.target.value as "yaml" | "json")}
								>
									<option value="yaml">YAML</option>
									<option value="json">JSON</option>
								</select>
							</div>
							<textarea
								rows={5}
								className="textarea mono"
								value={baseTxt}
								onChange={(e) => setBaseTxt(e.target.value)}
							/>
						</div>
					)}

					<div className="row-end">
						<button type="submit" className="btn btn-primary" disabled={busy}>
							{busy ? "Initializing..." : "Create Project"}
						</button>
					</div>
				</form>

				<div className="stack-md" style={{ flex: "42 1 300px", minWidth: 0 }}>
					<section
						className="panel stack-sm"
						aria-label="Baseline or Bundle Dropzone"
						onDragOver={(e) => e.preventDefault()}
						onDrop={(e) => {
							e.preventDefault();
							void onPick(e.dataTransfer.files?.[0]);
						}}
					>
						<h2 className="h2-serif">Drop Baseline or Archive</h2>
						<p className="text-sm text-muted">
							<span className="mono">.opendv.zip</span> opens Import modal ·{" "}
							<span className="mono">.yaml/.json</span> loads Baseline.
						</p>
						<input
							ref={fileRef}
							type="file"
							className="visually-hidden"
							aria-label="Choose baseline or bundle file"
							accept=".opendv.zip,.zip,.yaml,.yml,.json,.ttl"
							onChange={(e) => {
								void onPick(e.target.files?.[0]);
								e.target.value = "";
							}}
						/>
						<div className="row">
							<button
								type="button"
								className="btn btn-secondary"
								onClick={() => fileRef.current?.click()}
							>
								Choose File
							</button>
							<button
								type="button"
								className="btn"
								onClick={() => openExportImportModal(null)}
							>
								Open Bundle Modal
							</button>
						</div>
						{notice && (
							<div
								className={`banner ${notice.tone === "warn" ? "banner-warn" : "banner-ok"}`}
							>
								{notice.text}
							</div>
						)}
					</section>

					{activeProject && (
						<section className="card-selected stack-sm">
							<div className="row-between">
								<div>
									<h2 className="h2-serif">{activeProject.project_id}</h2>
									<span className="text-sm text-muted mono">
										{activeProject.domain_namespace} ·{" "}
										{activeProject.active_ontology_rev_id ?? "Genesis v0.0.0"}
										{activeProject.active_wave_id
											? ` · ${activeProject.active_wave_id}`
											: ""}
									</span>
								</div>
								<span className="pill">
									{activeProject.source_count}s · {activeProject.table_count}t
								</span>
							</div>
							{activeProject.has_active_lease && (
								<div className="banner banner-warn">
									Active run lease held; delete locked.
								</div>
							)}
							<div className="row-between">
								<span className="text-sm text-muted">
									{activeProject.updated_at}
								</span>
								<button
									type="button"
									className="btn btn-danger btn-sm"
									disabled={activeProject.has_active_lease || deleting}
									onClick={() => setDelOpen(true)}
								>
									Delete Project
								</button>
							</div>
						</section>
					)}

					<section className="panel stack-sm">
						<h2 className="h2-serif">Existing Projects ({sorted.length})</h2>
						{sorted.length === 0 ? (
							<p className="text-sm text-muted">No projects yet.</p>
						) : (
							sorted.map((p) => {
								const cur = activeProject?.project_id === p.project_id;
								return (
									<div
										key={p.project_id}
										className={
											cur
												? "card-selected row-between"
												: "card-subtle row-between"
										}
									>
										<div className="stack-sm" style={{ gap: 2 }}>
											<strong className="mono">{p.project_id}</strong>
											<span className="text-sm text-muted">
												{p.domain_namespace} ·{" "}
												{p.active_ontology_rev_id ?? "Genesis v0.0.0"}
												{p.active_wave_id ? ` · ${p.active_wave_id}` : ""}
											</span>
										</div>
										<button
											type="button"
											className="btn btn-sm"
											onClick={() => {
												if (cur) void hydrateProjectShell(p.project_id);
												navigate(
													`/projects/${encodeURIComponent(p.project_id)}/step/1`,
												);
											}}
										>
											Open
										</button>
									</div>
								);
							})
						)}
					</section>
				</div>
			</div>

			<dialog
				ref={delDlg}
				className="dialog-panel"
				aria-labelledby="step1-del-title"
				{...({ closedby: "closerequest" } as Record<string, string>)}
				onCancel={(e) => {
					e.preventDefault();
					if (!deleting) setDelOpen(false);
				}}
			>
				<div className="stack-md">
					<h2 id="step1-del-title" className="dialog-title">
						Delete Project
					</h2>
					<p className="text-muted">
						Permanently delete{" "}
						<strong className="mono">{activeProject?.project_id}</strong>?
					</p>
					<div className="row-end">
						<button
							type="button"
							className="btn"
							disabled={deleting}
							onClick={() => setDelOpen(false)}
						>
							Cancel
						</button>
						<button
							type="button"
							className="btn btn-danger"
							disabled={deleting || Boolean(activeProject?.has_active_lease)}
							onClick={onDelete}
						>
							{deleting ? "Deleting..." : "Confirm Delete"}
						</button>
					</div>
				</div>
			</dialog>
		</section>
	);
}
