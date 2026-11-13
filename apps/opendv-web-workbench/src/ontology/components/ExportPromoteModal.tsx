/**
 * Multi-Format Export & Workspace Promotion Modal (Task 5D.6, #227)
 * (apps/opendv-web-workbench/src/ontology/components/ExportPromoteModal.tsx)
 *
 * Implements:
 * - Multi-format export: Graph A YAML (.yaml), Canonical JSON / RFC 8785 JCS (.json), W3C Turtle (.ttl), JSON-LD (.jsonld), N-Triples (.nt)
 * - Version snapshot selector
 * - Copy to clipboard & file download
 * - Workspace pipeline promotion modal with OCC hash check and rationale >= 10 chars
 * - Accessible modal dialog with focus management and keyboard esc support
 */

import type React from "react";
import { useEffect, useRef, useState } from "react";
import { useStudioStore } from "../store/studio-store";

export interface ExportPromoteModalProps {
	isOpen: boolean;
	initialTab?: "export" | "promote";
	onClose: () => void;
	onExportComplete?: (format: string) => void;
	onPromoteComplete?: () => void;
}

const EXPORT_FORMATS = [
	{
		id: "yaml",
		label: "Graph A YAML (.yaml)",
		ext: "yaml",
		mime: "application/yaml",
	},
	{
		id: "json",
		label: "Canonical JSON / RFC 8785 JCS (.json)",
		ext: "json",
		mime: "application/json",
	},
	{ id: "turtle", label: "W3C Turtle (.ttl)", ext: "ttl", mime: "text/turtle" },
	{
		id: "jsonld",
		label: "JSON-LD (.jsonld)",
		ext: "jsonld",
		mime: "application/ld+json",
	},
	{
		id: "ntriples",
		label: "N-Triples (.nt)",
		ext: "nt",
		mime: "application/n-triples",
	},
] as const;

export const ExportPromoteModal: React.FC<ExportPromoteModalProps> = ({
	isOpen,
	initialTab = "export",
	onClose,
	onExportComplete,
	onPromoteComplete,
}) => {
	const activeMode = useStudioStore((s) => s.activeMode);
	const activeSession = useStudioStore((s) => s.activeSession);
	const exportOntology = useStudioStore((s) => s.exportOntology);
	const promoteWorkspace = useStudioStore((s) => s.promoteWorkspace);
	const hasUnsavedChanges = useStudioStore((s) => s.hasUnsavedChanges);
	const addToast = useStudioStore((s) => s.addToast);
	const isRemotePlatform = activeMode === "remote_platform";

	const [activeTab, setActiveTab] = useState<"export" | "promote">(initialTab);

	// Export tab state
	const [selectedFormat, setSelectedFormat] = useState<string>("yaml");
	const [selectedVersion, setSelectedVersion] = useState<string>(
		activeSession?.active_version || "",
	);
	const [exportPreview, setExportPreview] = useState<string>("");
	const [exportError, setExportError] = useState<string | null>(null);
	const [isLoadingExport, setIsLoadingExport] = useState(false);
	const [copySuccess, setCopySuccess] = useState(false);
	const copyTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

	useEffect(() => {
		return () => {
			if (copyTimeoutRef.current) {
				clearTimeout(copyTimeoutRef.current);
			}
		};
	}, []);

	// Promote tab state
	const [workspacePath, setWorkspacePath] = useState(
		isRemotePlatform
			? ""
			: activeSession?.from_workspace ||
					(activeSession?.domain_namespace
						? `workspaces/${activeSession.domain_namespace}`
						: ""),
	);
	const [workspaceSha, setWorkspaceSha] = useState("");
	const [promoteRationale, setPromoteRationale] = useState("");
	const [ackWarning, setAckWarning] = useState(false);
	const [historicalContractRationale, setHistoricalContractRationale] =
		useState("");
	const [promoteError, setPromoteError] = useState<string | null>(null);
	const [isPromoting, setIsPromoting] = useState(false);

	useEffect(() => {
		setActiveTab(initialTab);
	}, [initialTab]);

	const sessionId = activeSession?.session_id;
	const activeVersion = activeSession?.active_version;

	// Reset promotion fields whenever session changes or modal opens (MAJ-01 & MIN-01)
	useEffect(() => {
		if (!isOpen) return;
		const targetPath = isRemotePlatform
			? ""
			: activeSession?.from_workspace ||
				(activeSession?.domain_namespace
					? `workspaces/${activeSession.domain_namespace}`
					: "");
		setWorkspacePath(targetPath);
		setWorkspaceSha("");
		setPromoteRationale("");
		setAckWarning(false);
		setHistoricalContractRationale("");
		setPromoteError(null);
	}, [
		isOpen,
		isRemotePlatform,
		sessionId,
		activeSession?.from_workspace,
		activeSession?.domain_namespace,
	]);

	useEffect(() => {
		if (activeVersion) {
			setSelectedVersion(activeVersion);
		}
	}, [sessionId, activeVersion]);

	// Escape key closes modal only when not promoting (MIN-02)
	useEffect(() => {
		if (!isOpen) return;
		const handleKeyDown = (e: KeyboardEvent) => {
			if (e.key === "Escape" && !isPromoting) {
				onClose();
			}
		};
		window.addEventListener("keydown", handleKeyDown);
		return () => window.removeEventListener("keydown", handleKeyDown);
	}, [isOpen, isPromoting, onClose]);

	// Load export preview when tab or format changes
	useEffect(() => {
		if (!isOpen || activeTab !== "export" || !activeSession) return;
		if (!selectedVersion) {
			setExportError("No ontology version available to export.");
			setExportPreview("");
			setIsLoadingExport(false);
			return;
		}

		let cancelled = false;
		setIsLoadingExport(true);
		setExportError(null);
		exportOntology(selectedFormat, selectedVersion)
			.then((text) => {
				if (!cancelled) {
					setExportPreview(text);
					setExportError(null);
					setIsLoadingExport(false);
				}
			})
			.catch((err) => {
				if (!cancelled) {
					const msg = err instanceof Error ? err.message : String(err);
					setExportError(msg);
					setExportPreview("");
					setIsLoadingExport(false);
				}
			});

		return () => {
			cancelled = true;
		};
	}, [
		isOpen,
		activeTab,
		selectedFormat,
		selectedVersion,
		sessionId,
		exportOntology,
		activeSession,
	]);

	if (!isOpen) return null;

	const handleDownload = () => {
		if (!exportPreview || exportError) return;
		const fmt = EXPORT_FORMATS.find((f) => f.id === selectedFormat);
		const filename = `ontology-${activeSession?.session_id || "model"}-v${selectedVersion}.${fmt?.ext || "txt"}`;

		const blob = new Blob([exportPreview], { type: fmt?.mime || "text/plain" });
		const url = URL.createObjectURL(blob);
		const a = document.createElement("a");
		a.href = url;
		a.download = filename;
		document.body.appendChild(a);
		a.click();
		document.body.removeChild(a);
		URL.revokeObjectURL(url);

		if (onExportComplete) {
			onExportComplete(selectedFormat);
		}
	};

	const handleCopy = async () => {
		if (!exportPreview || exportError) return;
		try {
			await navigator.clipboard.writeText(exportPreview);
			setCopySuccess(true);
			if (copyTimeoutRef.current) {
				clearTimeout(copyTimeoutRef.current);
			}
			copyTimeoutRef.current = setTimeout(() => setCopySuccess(false), 2000);
		} catch {
			addToast({
				type: "urn:opendv:notice:clipboard",
				title: "Clipboard Access Unavailable",
				status: 200,
				detail:
					"Could not write text to clipboard. Please select and copy text manually from preview.",
				code: "OK",
			});
		}
	};

	const nonWsHistoricalRationaleLen = historicalContractRationale.replace(
		/\s+/g,
		"",
	).length;

	const handlePromoteSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		if (!promoteRationale || promoteRationale.trim().length < 10) {
			setPromoteError("Promotion rationale must be at least 10 characters.");
			return;
		}

		if (ackWarning && nonWsHistoricalRationaleLen < 10) {
			setPromoteError(
				"Historical contract warning rationale must contain at least 10 non-whitespace characters.",
			);
			return;
		}

		if (hasUnsavedChanges) {
			setPromoteError(
				"Cannot promote workspace with uncommitted changes. Please commit or discard changes first.",
			);
			return;
		}

		const SHA256_REGEX = /^[a-f0-9]{64}$/i;
		const trimmedPath = workspacePath.trim();
		if (!isRemotePlatform && !trimmedPath) {
			setPromoteError("Target workspace path must be specified.");
			return;
		}

		if (
			trimmedPath &&
			(trimmedPath.startsWith("/") ||
				trimmedPath.startsWith("\\") ||
				/^[a-zA-Z]:/.test(trimmedPath) ||
				trimmedPath.includes(".."))
		) {
			setPromoteError(
				"Workspace path must be a relative path without directory traversal ('..').",
			);
			return;
		}

		if (!workspaceSha.trim() || !SHA256_REGEX.test(workspaceSha.trim())) {
			setPromoteError(
				"Expected Workspace SHA-256 must be a valid 64-character hex string.",
			);
			return;
		}

		if (
			!activeSession?.head_sha256 ||
			!SHA256_REGEX.test(activeSession.head_sha256)
		) {
			setPromoteError("Active session has no valid 64-character head SHA-256.");
			return;
		}

		setIsPromoting(true);
		setPromoteError(null);
		try {
			const ok = await promoteWorkspace({
				...(trimmedPath ? { workspace: trimmedPath } : {}),
				workspace_sha256: workspaceSha.trim(),
				head_sha256: activeSession.head_sha256,
				rationale: promoteRationale.trim(),
				acknowledge_historical_contract_warning: ackWarning,
				...(ackWarning
					? {
							historical_contract_rationale: historicalContractRationale.trim(),
						}
					: {}),
			});

			if (ok) {
				if (onPromoteComplete) {
					onPromoteComplete();
				}
				onClose();
			} else {
				setPromoteError(
					"Promotion rejected by server or OCC concurrency check failed. Check error notifications.",
				);
			}
		} catch (err) {
			setPromoteError(err instanceof Error ? err.message : String(err));
		} finally {
			setIsPromoting(false);
		}
	};

	return (
		<div
			className="modal-backdrop"
			role="presentation"
			onClick={(e) => {
				if (e.target === e.currentTarget && !isPromoting) onClose();
			}}
		>
			<div
				className="modal-dialog export-promote-dialog"
				role="dialog"
				aria-modal="true"
				aria-labelledby="modal-headline"
				data-testid="export-promote-modal"
			>
				{/* Modal Top Bar */}
				<div className="modal-header">
					<div className="tab-buttons-group" role="tablist">
						<button
							type="button"
							role="tab"
							aria-selected={activeTab === "export"}
							className={`tab-btn ${activeTab === "export" ? "tab-btn-active" : ""}`}
							onClick={() => setActiveTab("export")}
							data-testid="tab-export-btn"
						>
							Multi-Format Export
						</button>
						<button
							type="button"
							role="tab"
							aria-selected={activeTab === "promote"}
							className={`tab-btn ${activeTab === "promote" ? "tab-btn-active" : ""}`}
							onClick={() => setActiveTab("promote")}
							data-testid="tab-promote-btn"
						>
							Workspace Promotion
						</button>
					</div>
					<button
						type="button"
						className="btn-close"
						onClick={onClose}
						aria-label="Close dialog"
						data-testid="close-modal-btn"
					>
						✕
					</button>
				</div>

				{/* Tab 1: Multi-Format Export */}
				{activeTab === "export" && (
					<div
						className="modal-body export-tab-body"
						data-testid="export-tab-body"
					>
						<h3 id="modal-headline" className="modal-title">
							Export Ontology Representation
						</h3>
						<p className="modal-desc">
							Generate canonical schemas, semantic web RDF graphs, or
							human-readable Data Vault models.
						</p>

						<div className="export-options-grid">
							<div className="form-group">
								<label htmlFor="export-format-select">Format:</label>
								<select
									id="export-format-select"
									className="form-select"
									value={selectedFormat}
									onChange={(e) => setSelectedFormat(e.target.value)}
									data-testid="export-format-select"
								>
									{EXPORT_FORMATS.map((fmt) => (
										<option key={fmt.id} value={fmt.id}>
											{fmt.label}
										</option>
									))}
								</select>
							</div>

							<div className="form-group">
								<label htmlFor="export-version-select">Version Snapshot:</label>
								<select
									id="export-version-select"
									className="form-select"
									value={selectedVersion}
									onChange={(e) => setSelectedVersion(e.target.value)}
									data-testid="export-version-select"
								>
									{(() => {
										const versions = Array.from(
											new Set([
												...(activeSession?.active_version
													? [activeSession.active_version]
													: []),
												...(activeSession?.commits?.map((c) => c.version) ||
													[]),
											]),
										);
										if (versions.length === 0) {
											return (
												<option value="" disabled>
													No versions available
												</option>
											);
										}
										return versions.map((v) => (
											<option key={v} value={v}>
												v{v}
												{v === activeSession?.active_version
													? " (Current Head)"
													: ""}
											</option>
										));
									})()}
								</select>
							</div>
						</div>

						{/* Preview Code Viewport */}
						<div className="preview-container">
							<div className="preview-header">
								<span className="preview-label">
									Preview Output ({selectedFormat.toUpperCase()}):
								</span>
								<div className="preview-actions">
									<button
										type="button"
										className="btn-secondary btn-sm"
										onClick={handleCopy}
										disabled={
											!exportPreview || isLoadingExport || Boolean(exportError)
										}
										data-testid="copy-export-btn"
									>
										{copySuccess ? "✓ Copied" : "Copy"}
									</button>
								</div>
							</div>
							{exportError ? (
								<div
									className="export-error-box error-banner"
									role="alert"
									data-testid="export-error-banner"
								>
									<span>Export generation failed: {exportError}</span>
								</div>
							) : (
								<pre
									className="export-code-block"
									data-testid="export-preview-block"
								>
									<code>
										{isLoadingExport
											? "Loading exported artifact..."
											: exportPreview}
									</code>
								</pre>
							)}
						</div>

						{/* Modal Footer */}
						<div className="modal-footer">
							<button type="button" className="btn-secondary" onClick={onClose}>
								Cancel
							</button>
							<button
								type="button"
								className="btn-primary"
								onClick={handleDownload}
								disabled={
									!exportPreview || isLoadingExport || Boolean(exportError)
								}
								data-testid="download-export-btn"
							>
								Download File
							</button>
						</div>
					</div>
				)}

				{/* Tab 2: Workspace Promotion */}
				{activeTab === "promote" && (
					<form
						onSubmit={handlePromoteSubmit}
						className="modal-body promote-tab-body"
						data-testid="promote-tab-body"
					>
						<h3 id="modal-headline" className="modal-title">
							Promote to Data Vault Workspace
						</h3>
						<div className="promotion-warning-box">
							<span className="warn-icon">⚠️</span>
							<p>
								Promotion promotes the session's head ontology into the
								enterprise Data Vault repository, updating{" "}
								<code>
									{activeSession?.domain_namespace
										? `${activeSession.domain_namespace}_graph_a_ontology.yaml`
										: "graph_a_ontology.yaml"}
								</code>{" "}
								and locking version lineage.
							</p>
						</div>

						<div className="form-group">
							<label htmlFor="workspace-path-input">
								{isRemotePlatform
									? "Target Workspace Path (Optional — Server Managed):"
									: "Target Workspace Path:"}
							</label>
							<input
								id="workspace-path-input"
								type="text"
								className="form-input"
								placeholder={
									isRemotePlatform
										? "Leave empty to use server-configured workspace"
										: "workspaces/domain_namespace"
								}
								value={workspacePath}
								onChange={(e) => setWorkspacePath(e.target.value)}
								data-testid="workspace-path-input"
							/>
						</div>

						{/* OCC Verification Card */}
						<div className="occ-card" data-testid="occ-verification-card">
							<div className="occ-field">
								<div className="occ-label-row">
									<label htmlFor="workspace-sha-input" className="occ-label">
										Expected Workspace SHA-256:
									</label>
									<button
										type="button"
										className="btn-secondary btn-xs"
										onClick={() => setWorkspaceSha("0".repeat(64))}
										data-testid="greenfield-workspace-sha-btn"
									>
										Greenfield (64 zeros)
									</button>
								</div>
								<input
									id="workspace-sha-input"
									type="text"
									className="form-input occ-code-input"
									value={workspaceSha}
									onChange={(e) => setWorkspaceSha(e.target.value)}
									placeholder="64-character target workspace SHA-256..."
									data-testid="workspace-sha-input"
								/>
							</div>
							<div className="occ-field">
								<span className="occ-label">Session Head SHA-256:</span>
								<code className="occ-code">
									{activeSession?.head_sha256 || "(None)"}
								</code>
							</div>
						</div>

						<div className="form-group">
							<label htmlFor="promote-rationale-input">
								Promotion Rationale (minimum 10 characters):
							</label>
							<textarea
								id="promote-rationale-input"
								className="form-textarea"
								rows={3}
								placeholder="Provide formal audit rationale for workspace promotion..."
								value={promoteRationale}
								onChange={(e) => {
									setPromoteRationale(e.target.value);
									if (e.target.value.trim().length >= 10) {
										setPromoteError(null);
									}
								}}
								data-testid="promote-rationale-input"
							/>
							<span
								className={`char-count ${promoteRationale.trim().length < 10 ? "text-fail" : "text-pass"}`}
							>
								{promoteRationale.trim().length} / 10 chars minimum
							</span>
						</div>

						<label className="checkbox-label ack-warning-label">
							<input
								type="checkbox"
								checked={ackWarning}
								onChange={(e) => setAckWarning(e.target.checked)}
								data-testid="ack-warning-checkbox"
							/>
							<span>
								Acknowledge historical contract warnings and line-item changes
							</span>
						</label>

						{ackWarning && (
							<div className="form-group">
								<label htmlFor="historical-contract-rationale-input">
									Historical Contract Warning Rationale (minimum 10
									non-whitespace characters):
								</label>
								<textarea
									id="historical-contract-rationale-input"
									className="form-textarea"
									rows={2}
									placeholder="Document explicit impact justification for overriding historical contract warnings..."
									value={historicalContractRationale}
									onChange={(e) => {
										setHistoricalContractRationale(e.target.value);
										if (e.target.value.replace(/\s+/g, "").length >= 10) {
											setPromoteError(null);
										}
									}}
									data-testid="historical-contract-rationale-input"
								/>
								<span
									className={`char-count ${nonWsHistoricalRationaleLen < 10 ? "text-fail" : "text-pass"}`}
								>
									{nonWsHistoricalRationaleLen} / 10 non-whitespace chars
									minimum
								</span>
							</div>
						)}

						{promoteError && (
							<div
								className="error-banner"
								role="alert"
								data-testid="promote-error-banner"
							>
								<span>{promoteError}</span>
							</div>
						)}

						<div className="modal-footer">
							<button type="button" className="btn-secondary" onClick={onClose}>
								Cancel
							</button>
							<button
								type="submit"
								className="btn-primary"
								disabled={
									isPromoting ||
									promoteRationale.trim().length < 10 ||
									(ackWarning && nonWsHistoricalRationaleLen < 10)
								}
								data-testid="confirm-promote-btn"
							>
								{isPromoting ? "Promoting..." : "Promote to Workspace"}
							</button>
						</div>
					</form>
				)}
			</div>
		</div>
	);
};
