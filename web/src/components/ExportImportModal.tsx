import { useEffect, useId, useRef, useState } from "react";
import { api, parseProblemDetails } from "../api/client";
import type { ImportReadinessReport } from "../api/openapi";
import { parseAppRoute, useProjectStore } from "../store/projectStore";

export function ExportImportModal() {
	const open = useProjectStore((s) => s.exportImportOpen);
	const stagedFile = useProjectStore((s) => s.exportImportFile);
	const activeProject = useProjectStore((s) => s.activeProject);
	const currentUrl = useProjectStore((s) => s.currentUrl);
	const close = useProjectStore((s) => s.closeExportImportModal);
	const isDirty = useProjectStore((s) => s.isDirty);
	const applyDiscardReset = useProjectStore((s) => s.applyDiscardReset);
	const abortRunSse = useProjectStore((s) => s.abortRunSse);
	const setMissingSecretRefs = useProjectStore((s) => s.setMissingSecretRefs);
	const setProjectsSummary = useProjectStore((s) => s.setProjectsSummary);
	const hydrateProjectShell = useProjectStore((s) => s.hydrateProjectShell);
	const navigate = useProjectStore((s) => s.navigate);
	const setLastProblem = useProjectStore((s) => s.setLastProblem);

	const dlgRef = useRef<HTMLDialogElement>(null);
	const titleId = useId();

	const [exportMode, setExportMode] = useState<
		"core_project_state" | "full_demo"
	>("core_project_state");
	const [importFile, setImportFile] = useState<File | null>(null);
	const [bundlePath, setBundlePath] = useState("");
	const [conflict, setConflict] = useState<"fail" | "rename" | "replace">(
		"fail",
	);
	const [trustWorkspace, setTrustWorkspace] = useState(false);
	const [confirmReplaceDirty, setConfirmReplaceDirty] = useState(false);
	const [report, setReport] = useState<ImportReadinessReport | null>(null);
	const [busy, setBusy] = useState(false);
	const [errorMsg, setErrorMsg] = useState<string | null>(null);

	useEffect(() => {
		if (stagedFile) setImportFile(stagedFile);
	}, [stagedFile]);

	useEffect(() => {
		if (!open) {
			setConfirmReplaceDirty(false);
			setErrorMsg(null);
			return;
		}
		setReport(null);
	}, [open]);

	useEffect(() => {
		const el = dlgRef.current;
		if (!el) return;
		if (open && !el.open) el.showModal();
		else if (!open && el.open) {
			el.returnValue = "";
			el.close();
		}
		const onClick = (e: MouseEvent) => {
			if (e.target === el && !busy) close();
		};
		el.addEventListener("click", onClick);
		return () => el.removeEventListener("click", onClick);
	}, [open, busy, close]);

	const handleExport = async () => {
		if (!activeProject || busy) return;
		setBusy(true);
		setErrorMsg(null);
		try {
			const blob = await api.exportBundle(activeProject.project_id, exportMode);
			const url = URL.createObjectURL(blob);
			const a = document.createElement("a");
			a.href = url;
			a.download = `${activeProject.project_id}.${exportMode === "full_demo" ? "full_demo" : "core"}.opendv.zip`;
			a.click();
			URL.revokeObjectURL(url);
		} catch (err) {
			setErrorMsg(parseProblemDetails(err).detail);
		} finally {
			setBusy(false);
		}
	};

	const executeImport = async () => {
		if (busy || (!importFile && !bundlePath.trim())) return;
		setBusy(true);
		setErrorMsg(null);
		setConfirmReplaceDirty(false);
		try {
			let res: ImportReadinessReport;
			if (importFile) {
				const fd = new FormData();
				fd.append("file", importFile);
				fd.append("conflict", conflict);
				fd.append("trust_workspace", String(trustWorkspace));
				res = await api.importProject(fd);
			} else {
				res = await api.importProject({
					bundle_path: bundlePath.trim(),
					conflict,
					trust_workspace: trustWorkspace,
				});
			}
			const list = await api.listProjects();
			setProjectsSummary(list);
			if (
				res.resolved_conflict === "replaced" &&
				activeProject &&
				res.project_id === activeProject.project_id
			) {
				applyDiscardReset();
				abortRunSse();
				setMissingSecretRefs(res.missing_secret_refs);
				const route = parseAppRoute(
					new URL(currentUrl, "http://localhost").pathname,
				);
				await hydrateProjectShell(res.project_id, {
					preserveMissingSecretRefs: true,
					loadVaultIr:
						route.kind === "explorer" ||
						(route.kind === "step" && (route.step === 6 || route.step === 7)),
				});
			}
			setReport(res);
		} catch (err) {
			const p = parseProblemDetails(err);
			if (p.error_code === "PROJECT_ID_CONFLICT" || p.status === 409) {
				setConflict("rename");
			}
			setErrorMsg(`${p.error_code}: ${p.detail}`);
		} finally {
			setBusy(false);
		}
	};

	const submitImport = (e: React.FormEvent) => {
		e.preventDefault();
		if (busy || (!importFile && !bundlePath.trim())) return;
		if (conflict === "replace" && activeProject !== null && isDirty()) {
			setConfirmReplaceDirty(true);
			return;
		}
		void executeImport();
	};

	const openImportedAtStep = async (step: 1 | 2) => {
		if (!report) return;
		abortRunSse();
		setMissingSecretRefs(report.missing_secret_refs);
		try {
			await hydrateProjectShell(report.project_id, {
				preserveMissingSecretRefs: true,
			});
		} catch (err) {
			setLastProblem(parseProblemDetails(err));
		}
		close();
		navigate(
			`/projects/${encodeURIComponent(report.project_id)}/step/${step}`,
			{
				bypassDirtyGuard: true,
			},
		);
	};

	if (!open) return null;

	return (
		<dialog
			ref={dlgRef}
			className="dialog-panel"
			style={{ width: "min(760px, 94dvw)" }}
			aria-labelledby={titleId}
			{...({ closedby: "closerequest" } as Record<string, string>)}
			onCancel={(e) => {
				e.preventDefault();
				if (!busy) close();
			}}
		>
			<div className="stack-md">
				<div className="row-between">
					<h2 id={titleId} className="dialog-title">
						Export / Import Portable .opendv.zip Bundle
					</h2>
					<button
						type="button"
						className="btn btn-secondary btn-sm"
						disabled={busy}
						onClick={close}
					>
						Close
					</button>
				</div>

				{errorMsg && (
					<div className="banner banner-danger" role="alert">
						{errorMsg}
					</div>
				)}

				<div className="split-grid">
					<section className="card-subtle stack-sm">
						<h3 className="font-semibold">1. Export Archive Profile</h3>
						<fieldset className="radio-group" disabled={busy || !activeProject}>
							<legend className="field-label">Bundle Mode</legend>
							<label className="radio-row">
								<input
									type="radio"
									name="export_mode"
									checked={exportMode === "core_project_state"}
									onChange={() => setExportMode("core_project_state")}
								/>
								<span>
									<strong>Core Project State</strong> (default ·{" "}
									<code>core_project_state</code>, zero binaries/secrets)
								</span>
							</label>
							<label className="radio-row">
								<input
									type="radio"
									name="export_mode"
									checked={exportMode === "full_demo"}
									onChange={() => setExportMode("full_demo")}
								/>
								<span>
									<strong>Full Self-Contained Demo</strong> (
									<code>full_demo</code>, includes DuckDB &amp; attached files
									&le; 250 MB)
								</span>
							</label>
						</fieldset>
						<button
							type="button"
							className="btn btn-primary"
							disabled={busy || !activeProject}
							onClick={() => void handleExport()}
						>
							Export {activeProject ? `${activeProject.project_id}` : "Project"}
						</button>
					</section>

					<section className="card-subtle stack-sm">
						{!report ? (
							<form className="stack-sm" onSubmit={submitImport}>
								<h3 className="font-semibold">2A. Import .opendv.zip</h3>
								<label className="field-col">
									<span className="field-label">Choose .opendv.zip…</span>
									<input
										type="file"
										accept=".zip,.opendv.zip"
										disabled={busy}
										onChange={(e) => setImportFile(e.target.files?.[0] ?? null)}
									/>
								</label>
								<label className="field-col">
									<span className="field-label">Or Server Bundle Path</span>
									<input
										type="text"
										className="input mono"
										disabled={busy}
										placeholder="bundles/am_core.core.opendv.zip"
										value={bundlePath}
										onChange={(e) => setBundlePath(e.target.value)}
									/>
								</label>
								<fieldset className="radio-group" disabled={busy}>
									<legend className="field-label">Conflict Resolution</legend>
									{(["fail", "rename", "replace"] as const).map((c) => (
										<label key={c} className="radio-row">
											<input
												type="radio"
												name="import_conflict"
												checked={conflict === c}
												onChange={() => setConflict(c)}
											/>
											<span className="mono">{c}</span>
										</label>
									))}
								</fieldset>
								<label className="radio-row text-sm">
									<input
										type="checkbox"
										checked={trustWorkspace}
										disabled={busy}
										onChange={(e) => setTrustWorkspace(e.target.checked)}
									/>
									<span>
										Trust workspace override packs (<code>trust_workspace</code>
										)
									</span>
								</label>

								{confirmReplaceDirty && (
									<div className="callout-coral stack-sm" role="alert">
										<div>
											Active project has unsaved edits. Confirm replacing it
											once import succeeds?
										</div>
										<div className="row-end">
											<button
												type="button"
												className="btn btn-sm"
												onClick={() => setConfirmReplaceDirty(false)}
											>
												Cancel
											</button>
											<button
												type="button"
												className="btn btn-danger btn-sm"
												onClick={() => void executeImport()}
											>
												Confirm Replace &amp; Import
											</button>
										</div>
									</div>
								)}

								{!confirmReplaceDirty && (
									<button
										type="submit"
										className="btn btn-primary"
										disabled={busy || (!importFile && !bundlePath.trim())}
									>
										{busy ? "Importing..." : "Import Bundle"}
									</button>
								)}
							</form>
						) : (
							<div className="stack-sm">
								<h3 className="font-semibold">2B. Import Readiness Report</h3>
								<div className="card-cream stack-sm text-sm">
									<div>
										<strong>project_id:</strong>{" "}
										<span className="mono">{report.project_id}</span>
									</div>
									<div>
										<strong>resolved_conflict:</strong>{" "}
										<span className="mono">{report.resolved_conflict}</span>
									</div>
									<div>
										<strong>readiness_flags:</strong>{" "}
										<span className="mono">
											{report.readiness_flags.join(", ") || "NONE"}
										</span>
									</div>
									<div>
										<strong>missing_secret_refs:</strong>{" "}
										<span className="mono">
											{report.missing_secret_refs.join(", ") || "None"}
										</span>
									</div>
									<div>
										<strong>missing_source_ids:</strong>{" "}
										<span className="mono">
											{report.missing_source_ids.join(", ") || "None"}
										</span>
									</div>
									<div>
										<strong>unverified_receipt_ids:</strong>{" "}
										<span className="mono">
											{report.unverified_receipt_ids.join(", ") || "None"}
										</span>
									</div>
									<div className="banner banner-warn">
										<strong>audit_claim_notice:</strong>{" "}
										{report.audit_claim_notice}
									</div>
								</div>
								<div className="row-between">
									<button
										type="button"
										className="btn btn-secondary btn-sm"
										onClick={() => void openImportedAtStep(2)}
									>
										Open Step 2 (Bind Secrets)
									</button>
									<button
										type="button"
										className="btn btn-primary btn-sm"
										onClick={() => void openImportedAtStep(1)}
									>
										Open Hydrated Project →
									</button>
								</div>
							</div>
						)}
					</section>
				</div>
			</div>
		</dialog>
	);
}
