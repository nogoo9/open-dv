/**
 * Open-DV Ontology Construction Studio SPA Shell (Task 5D.1, #222)
 * (apps/opendv-web-workbench/src/ontology/OntologyStudioApp.tsx)
 *
 * Implements:
 * - Decoupled React 19 SPA mounted at /ontology
 * - Five persistent layout zones: Header Bar, Left Drawer, Center Canvas, Right Drawer, Bottom Dock
 * - Integration of SourcesPanel, ClarificationPanel, GraphCanvas, DiffOverlay, VisualEditorDrawer, QualityFeedbackPanel, and ExportPromoteModal
 * - RFC 9457 Error Toasts Container
 */

import type React from "react";
import { useEffect, useState } from "react";
import { ClarificationPanel } from "./components/ClarificationPanel";
import { DiffOverlay } from "./components/DiffOverlay";
import { ExportPromoteModal } from "./components/ExportPromoteModal";
import { GraphCanvas } from "./components/GraphCanvas";
import { QualityFeedbackPanel } from "./components/QualityFeedbackPanel";
import { SourcesPanel } from "./components/SourcesPanel";
import { VisualEditorDrawer } from "./components/VisualEditorDrawer";
import { studioStore, useStudioStore } from "./store/studio-store";

export interface OntologyStudioAppProps {
	className?: string;
}

export const OntologyStudioApp: React.FC<OntologyStudioAppProps> = ({
	className = "",
}) => {
	const activeSession = useStudioStore((s) => s.activeSession);
	const sessions = useStudioStore((s) => s.sessions);
	const activeMode = useStudioStore((s) => s.activeMode);
	const userRole = useStudioStore((s) => s.userRole);
	const layout = useStudioStore((s) => s.layout);
	const toasts = useStudioStore((s) => s.toasts);
	const nodes = useStudioStore((s) => s.nodes);
	const edges = useStudioStore((s) => s.edges);
	const parsingState = useStudioStore((s) => s.parsingState);
	const loadSessions = useStudioStore((s) => s.loadSessions);
	const selectSession = useStudioStore((s) => s.selectSession);
	const createSession = useStudioStore((s) => s.createSession);
	const setLeftDrawerTab = useStudioStore((s) => s.setLeftDrawerTab);
	const toggleLeftDrawer = useStudioStore((s) => s.toggleLeftDrawer);
	const toggleBottomDock = useStudioStore((s) => s.toggleBottomDock);
	const setActiveModal = useStudioStore((s) => s.setActiveModal);
	const dismissToast = useStudioStore((s) => s.dismissToast);
	const computeDiff = useStudioStore((s) => s.computeDiff);
	const diffState = useStudioStore((s) => s.diffState);
	const isPromoting = useStudioStore((s) => s.isPromoting);
	const addToast = useStudioStore((s) => s.addToast);

	// New Session form state
	const [newSessionId, setNewSessionId] = useState("");
	const [newNamespace, setNewNamespace] = useState("");
	const [newOwner, setNewOwner] = useState("");
	const [newWorkspace, setNewWorkspace] = useState("");
	const [isCreatingSession, setIsCreatingSession] = useState(false);

	// On mount, load sessions
	useEffect(() => {
		loadSessions();
	}, [loadSessions]);

	// Close new_session modal on Escape key (ExportPromoteModal manages its own Escape lifecycle, MIN-01)
	useEffect(() => {
		const handleKeyDown = (e: KeyboardEvent) => {
			if (e.key === "Escape" && layout.activeModal === "new_session") {
				setActiveModal(null);
			}
		};
		if (typeof window !== "undefined") {
			window.addEventListener("keydown", handleKeyDown);
			return () => window.removeEventListener("keydown", handleKeyDown);
		}
	}, [layout.activeModal, setActiveModal]);

	const handleCreateSessionSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		const sid = newSessionId.trim();
		if (!sid) return;

		const SESSION_ID_REGEX = /^[a-z0-9_-]+$/;
		if (!SESSION_ID_REGEX.test(sid)) {
			addToast({
				type: "urn:opendv:error:syntax",
				title: "Invalid Session ID",
				status: 400,
				detail:
					"Session ID must match ^[a-z0-9_-]+$ (lowercase alphanumeric, underscores, hyphens).",
				code: "SYNTAX_INVALID_ARGUMENT",
			});
			return;
		}

		setIsCreatingSession(true);
		try {
			await createSession({
				session_id: sid,
				domain_namespace: newNamespace.trim(),
				enterprise_owner: newOwner.trim(),
				from_workspace: newWorkspace.trim() || undefined,
			});
			setNewSessionId("");
			setNewNamespace("");
			setNewOwner("");
			setNewWorkspace("");
			setActiveModal(null);
		} catch (err) {
			addToast({
				type: "urn:opendv:error:execution",
				title: "Failed to Create Session",
				status: 500,
				detail: err instanceof Error ? err.message : String(err),
				code: "EXECUTION_INTERNAL_ERROR",
			});
		} finally {
			setIsCreatingSession(false);
		}
	};

	const handleOpenDiff = () => {
		if (!activeSession) return;
		const commits = activeSession.commits || [];
		const targetVersion = activeSession.active_version;
		const activeCommitIdx = commits.findIndex(
			(c) => c.version === targetVersion,
		);
		let baseVersion = "0.0.0";
		if (activeCommitIdx > 0) {
			baseVersion = commits[activeCommitIdx - 1]?.version || "0.0.0";
		} else if (activeCommitIdx === -1 && commits.length >= 2) {
			baseVersion = commits[commits.length - 2]?.version || "0.0.0";
		} else if (commits.length === 1 && commits[0]?.version !== targetVersion) {
			baseVersion = commits[0]?.version || "0.0.0";
		}
		computeDiff(baseVersion, targetVersion);
		setLeftDrawerTab("history");
		if (!layout.leftDrawerOpen) {
			toggleLeftDrawer();
		}
	};

	return (
		<div
			className={`ontology-studio-app dv-canvas-body ${className}`}
			data-testid="ontology-studio-app"
		>
			{/* 1. Header Bar */}
			<header className="studio-header" data-testid="studio-header">
				<div className="header-brand">
					<span className="brand-logo" aria-hidden="true">
						🏛️
					</span>
					<div className="brand-text">
						<h1 className="brand-title">
							Open-DV Ontology Construction Studio
						</h1>
						<span className="brand-subtitle">
							Enterprise Semantic Engineering & FIBO Alignment
						</span>
					</div>
				</div>

				{/* Session & Version Controls */}
				<div className="header-session-group">
					<div className="session-select-box">
						<label htmlFor="session-selector" className="sr-only">
							Select Studio Session
						</label>
						<select
							id="session-selector"
							className="session-selector-input"
							value={activeSession?.session_id || ""}
							onChange={(e) => selectSession(e.target.value)}
							data-testid="session-selector"
						>
							{sessions.length === 0 && (
								<option value="">No Active Sessions</option>
							)}
							{sessions.map((s) => (
								<option key={s.session_id} value={s.session_id}>
									{s.session_id} ({s.domain_namespace} - v{s.active_version})
								</option>
							))}
						</select>
					</div>

					<button
						type="button"
						className="btn-secondary btn-sm"
						onClick={() => setActiveModal("new_session")}
						data-testid="open-new-session-modal-btn"
					>
						+ New Session
					</button>

					{activeSession && (
						<div className="header-meta-pills">
							<span
								className="meta-pill version-pill"
								title={`Head SHA: ${activeSession.head_sha256 || "genesis"}`}
								data-testid="active-version-pill"
							>
								v{activeSession.active_version}
							</span>
							<span
								className="meta-pill role-pill"
								data-testid="user-role-pill"
							>
								{userRole.toUpperCase()}
							</span>
							<span
								className="meta-pill mode-pill"
								data-testid="studio-mode-pill"
							>
								{activeMode}
							</span>
						</div>
					)}
				</div>

				{/* Header Actions */}
				<div className="header-actions">
					<button
						type="button"
						className="btn-toolbar"
						onClick={() => {
							studioStore.getState().triggerRelayout();
						}}
						disabled={!activeSession}
						data-testid="header-relayout-btn"
						title="Re-run ELK Layered Layout"
					>
						Re-layout
					</button>
					<button
						type="button"
						className="btn-toolbar"
						onClick={handleOpenDiff}
						disabled={!activeSession}
						data-testid="header-diff-btn"
						title="Compare Active Ontology Version Diff"
					>
						Visual Diff
					</button>
					<button
						type="button"
						className="btn-toolbar"
						onClick={() => setActiveModal("export")}
						disabled={!activeSession}
						data-testid="header-export-btn"
						title="Export Multi-Format Ontology Schemas"
					>
						Export
					</button>
					<button
						type="button"
						className="btn-primary btn-sm"
						onClick={() => setActiveModal("promote")}
						disabled={!activeSession}
						data-testid="header-promote-btn"
						title="Promote Head Version to Enterprise Workspace"
					>
						Promote
					</button>
				</div>
			</header>

			{/* Offline Standalone Banner */}
			{activeMode === "local_standalone" && (
				<div
					className="standalone-mode-banner"
					role="status"
					data-testid="standalone-mode-banner"
				>
					<span>
						⚡ Standalone Mode (Local In-Memory Engine) — changes are stored in-memory
					</span>
				</div>
			)}

			{/* Main Studio Body (Left Drawer + Canvas + Right Context Drawer) */}
			<div className="studio-main-layout">
				{/* 2. Left Drawer (280px) */}
				<aside
					className={`studio-left-drawer ${layout.leftDrawerOpen ? "open" : "collapsed"}`}
					aria-label="Studio Navigation and Tooling Panels"
					data-testid="studio-left-drawer"
				>
					{/* Left Drawer Navigation Tabs */}
					<div className="drawer-nav-tabs" role="tablist">
						<button
							type="button"
							role="tab"
							aria-selected={layout.leftDrawerTab === "sessions"}
							className={`nav-tab-btn ${layout.leftDrawerTab === "sessions" ? "active" : ""}`}
							onClick={() => setLeftDrawerTab("sessions")}
							data-testid="nav-tab-sessions"
							title="Sessions Inventory"
						>
							Sessions
						</button>
						<button
							type="button"
							role="tab"
							aria-selected={layout.leftDrawerTab === "sources"}
							className={`nav-tab-btn ${layout.leftDrawerTab === "sources" ? "active" : ""}`}
							onClick={() => setLeftDrawerTab("sources")}
							data-testid="nav-tab-sources"
							title="Sources & Documents"
						>
							Sources
						</button>
						<button
							type="button"
							role="tab"
							aria-selected={layout.leftDrawerTab === "questions"}
							className={`nav-tab-btn ${layout.leftDrawerTab === "questions" ? "active" : ""}`}
							onClick={() => setLeftDrawerTab("questions")}
							data-testid="nav-tab-questions"
							title="Clarification Q&A"
						>
							Q&A
						</button>
						<button
							type="button"
							role="tab"
							aria-selected={layout.leftDrawerTab === "quality"}
							className={`nav-tab-btn ${layout.leftDrawerTab === "quality" ? "active" : ""}`}
							onClick={() => setLeftDrawerTab("quality")}
							data-testid="nav-tab-quality"
							title="Quality & Scorecard"
						>
							Quality
						</button>
						<button
							type="button"
							role="tab"
							aria-selected={layout.leftDrawerTab === "history"}
							className={`nav-tab-btn ${layout.leftDrawerTab === "history" ? "active" : ""}`}
							onClick={() => setLeftDrawerTab("history")}
							data-testid="nav-tab-history"
							title="Version Diff & History"
						>
							Diff
						</button>
					</div>

					{/* Drawer Tab Content */}
					<div className="drawer-tab-content">
						{layout.leftDrawerTab === "sessions" && (
							<div
								className="sessions-list-panel"
								data-testid="sessions-list-panel"
							>
								<div className="panel-header">
									<h3 className="panel-title">Studio Sessions</h3>
									<button
										type="button"
										className="btn-secondary btn-xs"
										onClick={() => setActiveModal("new_session")}
									>
										+ Create
									</button>
								</div>
								<div className="sessions-items-list">
									{sessions.map((s) => (
										<button
											key={s.session_id}
											type="button"
											className={`session-card ${s.session_id === activeSession?.session_id ? "active-session" : ""}`}
											onClick={() => selectSession(s.session_id)}
											data-testid={`session-card-${s.session_id}`}
										>
											<div className="session-card-header">
												<strong className="session-id">{s.session_id}</strong>
												<span className="session-ver">v{s.active_version}</span>
											</div>
											<span className="session-ns">{s.domain_namespace}</span>
											<span className="session-owner">
												{s.enterprise_owner}
											</span>
										</button>
									))}
								</div>
							</div>
						)}

						{layout.leftDrawerTab === "sources" && <SourcesPanel />}
						{layout.leftDrawerTab === "questions" && <ClarificationPanel />}
						{layout.leftDrawerTab === "quality" && <QualityFeedbackPanel />}
						{layout.leftDrawerTab === "history" && (
							<>
								<DiffOverlay />
								{!diffState.active && (
									<div
										className="diff-overlay-empty-state"
										data-testid="diff-empty-prompt"
										style={{ padding: "1.5rem", textAlign: "center" }}
									>
										<p style={{ color: "#6b7280", marginBottom: "1rem" }}>
											No active version comparison loaded.
										</p>
										<button
											type="button"
											className="btn-primary btn-sm"
											onClick={handleOpenDiff}
											data-testid="drawer-compute-diff-btn"
										>
											Compute Visual Diff
										</button>
									</div>
								)}
							</>
						)}
					</div>

					{/* Toggle Drawer Handle */}
					<button
						type="button"
						className="drawer-collapse-btn"
						onClick={() => toggleLeftDrawer()}
						aria-label={
							layout.leftDrawerOpen
								? "Collapse Left Drawer"
								: "Expand Left Drawer"
						}
						data-testid="toggle-left-drawer-btn"
					>
						{layout.leftDrawerOpen ? "◀" : "▶"}
					</button>
				</aside>

				{/* 3. Center Canvas (React Flow + elkjs) */}
				<main className="studio-canvas-zone" data-testid="studio-canvas-zone">
					<GraphCanvas />
				</main>

				{/* 4. Right Context Drawer (Visual Editor) */}
				<VisualEditorDrawer />
			</div>

			{/* 5. Bottom Dock: Diagnostics & Event Log */}
			<footer className="studio-bottom-dock" data-testid="studio-bottom-dock">
				<div className="dock-header">
					<button
						type="button"
						className="dock-toggle-btn"
						onClick={() => toggleBottomDock()}
						data-testid="toggle-bottom-dock-btn"
					>
						{layout.bottomDockOpen
							? "▼ Diagnostic Event Stream"
							: "▲ Diagnostic Event Stream"}
					</button>
					<span className="dock-status">
						Session: <code>{activeSession?.session_id || "none"}</code> |
						Status: {activeSession?.status || "READY"}
					</span>
				</div>
				{layout.bottomDockOpen && (
					<div className="dock-content" data-testid="dock-content">
						<pre className="event-stream-log">
							<code>
								{activeSession
									? `[SESSION] id=${activeSession.session_id} active_version=${activeSession.active_version} status=${activeSession.status}\n[GRAPH] concepts=${nodes.length} relationships=${edges.length}\n[PARSING] status=${parsingState.status} stage=${parsingState.stage || "idle"} progress=${parsingState.progress_pct}%`
									: "[IDLE] No active session loaded. Select or create a session above."}
							</code>
						</pre>
					</div>
				)}
			</footer>

			{/* New Session Creation Modal */}
			{layout.activeModal === "new_session" && (
				<div className="modal-backdrop" role="presentation">
					<div
						className="modal-dialog"
						role="dialog"
						aria-modal="true"
						aria-labelledby="new-session-headline"
						data-testid="new-session-modal"
					>
						<div className="modal-header">
							<h3 id="new-session-headline" className="modal-title">
								Create Ontology Studio Session
							</h3>
							<button
								type="button"
								className="btn-close"
								onClick={() => setActiveModal(null)}
							>
								✕
							</button>
						</div>
						<form onSubmit={handleCreateSessionSubmit} className="modal-body">
							<div className="form-group">
								<label htmlFor="new-session-id-input">
									Session Identifier (regex ^[a-z0-9_-]+$):
								</label>
								<input
									id="new-session-id-input"
									type="text"
									className="form-input"
									placeholder="e.g. wealth-mgmt-2026"
									value={newSessionId}
									onChange={(e) => setNewSessionId(e.target.value)}
									required
									data-testid="new-session-id-input"
								/>
							</div>

							<div className="form-group">
								<label htmlFor="new-namespace-input">Domain Namespace:</label>
								<input
									id="new-namespace-input"
									type="text"
									className="form-input"
									placeholder="e.g. wealth_management"
									value={newNamespace}
									onChange={(e) => setNewNamespace(e.target.value)}
									required
									data-testid="new-session-ns-input"
								/>
							</div>

							<div className="form-group">
								<label htmlFor="new-owner-input">Enterprise Owner:</label>
								<input
									id="new-owner-input"
									type="text"
									className="form-input"
									placeholder="e.g. Enterprise Data Governance"
									value={newOwner}
									onChange={(e) => setNewOwner(e.target.value)}
									required
									data-testid="new-session-owner-input"
								/>
							</div>

							<div className="form-group">
								<label htmlFor="new-ws-input">
									Seed From Workspace (Optional):
								</label>
								<input
									id="new-ws-input"
									type="text"
									className="form-input"
									placeholder="e.g. workspaces/private_equity"
									value={newWorkspace}
									onChange={(e) => setNewWorkspace(e.target.value)}
									data-testid="new-session-ws-input"
								/>
							</div>

							<div className="modal-footer">
								<button
									type="button"
									className="btn-secondary"
									onClick={() => setActiveModal(null)}
								>
									Cancel
								</button>
								<button
									type="submit"
									className="btn-primary"
									disabled={isCreatingSession || !newSessionId.trim()}
									data-testid="create-session-submit-btn"
								>
									{isCreatingSession ? "Creating..." : "Create Session"}
								</button>
							</div>
						</form>
					</div>
				</div>
			)}

			{/* Multi-Format Export & Promotion Modal (MAJ-01: mounted conditionally) */}
			{(layout.activeModal === "export" ||
				layout.activeModal === "promote") && (
				<ExportPromoteModal
					isOpen={true}
					initialTab={layout.activeModal}
					onClose={() => setActiveModal(null)}
				/>
			)}

			{/* RFC 9457 Toast Notifications Container */}
			<div
				className="studio-toasts-container"
				role="region"
				aria-label="Error notifications"
				data-testid="studio-toasts-container"
			>
				{toasts.map((toast) => (
					<div
						key={toast.id}
						className={`studio-toast toast-${toast.status >= 500 ? "fatal" : toast.status >= 400 ? "warn" : "info"}`}
						role="alert"
						data-testid={`toast-${toast.id}`}
					>
						<div className="toast-header">
							<strong className="toast-title">{toast.title}</strong>
							<button
								type="button"
								className="toast-dismiss-btn"
								onClick={() => dismissToast(toast.id)}
								aria-label="Dismiss notification"
							>
								✕
							</button>
						</div>
						<p className="toast-detail">{toast.detail}</p>
						<code className="toast-code">
							[{toast.code || `HTTP-${toast.status}`}]
						</code>
					</div>
				))}
			</div>
		</div>
	);
};
