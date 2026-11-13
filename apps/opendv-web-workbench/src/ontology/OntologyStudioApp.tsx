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
import { useEffect, useRef, useState } from "react";
import { ClarificationPanel } from "./components/ClarificationPanel";
import { DiffOverlay } from "./components/DiffOverlay";
import { ExportPromoteModal } from "./components/ExportPromoteModal";
import { GraphCanvas } from "./components/GraphCanvas";
import { QualityFeedbackPanel } from "./components/QualityFeedbackPanel";
import { SourcesPanel } from "./components/SourcesPanel";
import { VisualEditorDrawer } from "./components/VisualEditorDrawer";
import {
	extractAndScrubLaunchNonce,
	studioStore,
	useStudioStore,
} from "./store/studio-store";

export interface OntologyStudioAppProps {
	className?: string;
}

function slugifyIdentifier(raw: string, fallback: string): string {
	const cleaned = raw
		.trim()
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, "_")
		.replace(/^_+|_+$/g, "");
	return cleaned || fallback;
}

function extractConceptTerminalSlug(
	conceptUri: string,
	fallback: string,
): string {
	const tail = conceptUri.split(/[:/#]/).filter(Boolean).pop() || "";
	return slugifyIdentifier(tail, fallback);
}

export function buildUniqueConceptIdentifiers(
	rawName: string,
	nodes: ReadonlyArray<{
		concept_uri: string;
		target_hub?: string | null | undefined;
	}>,
	committedNodes: ReadonlyArray<{
		concept_uri: string;
		target_hub?: string | null | undefined;
	}>,
): { slug: string; conceptUri: string; targetHub: string } {
	const baseSlug = slugifyIdentifier(rawName, "concept");
	const existingUris = new Set<string>();
	const existingHubs = new Set<string>();
	for (const n of [...nodes, ...committedNodes]) {
		if (n.concept_uri) existingUris.add(n.concept_uri.toLowerCase());
		if (n.target_hub) existingHubs.add(n.target_hub.toLowerCase());
	}

	let candidate = baseSlug;
	let suffix = 2;
	while (
		existingUris.has(`urn:ontology:entity:${candidate}`.toLowerCase()) ||
		existingHubs.has(`hub_${candidate}`.toLowerCase())
	) {
		candidate = `${baseSlug}_${suffix}`;
		suffix += 1;
	}
	return {
		slug: candidate,
		conceptUri: `urn:ontology:entity:${candidate}`,
		targetHub: `hub_${candidate}`,
	};
}

export function buildUniqueRelationshipIdentifiers(
	rawVerb: string,
	sourceConceptUri: string,
	targetConceptUri: string,
	edges: ReadonlyArray<{
		relationship_uri: string;
		target_link?: string | null | undefined;
	}>,
	committedEdges: ReadonlyArray<{
		relationship_uri: string;
		target_link?: string | null | undefined;
	}>,
): { slug: string; relationshipUri: string; targetLink: string } {
	const srcSlug = extractConceptTerminalSlug(sourceConceptUri, "src");
	const verbSlug = slugifyIdentifier(rawVerb, "relates_to");
	const tgtSlug = extractConceptTerminalSlug(targetConceptUri, "tgt");
	const baseSlug = `${srcSlug}_${verbSlug}_${tgtSlug}`;

	const existingUris = new Set<string>();
	const existingLinks = new Set<string>();
	for (const e of [...edges, ...committedEdges]) {
		if (e.relationship_uri) existingUris.add(e.relationship_uri.toLowerCase());
		if (e.target_link) existingLinks.add(e.target_link.toLowerCase());
	}

	let candidate = baseSlug;
	let suffix = 2;
	while (
		existingUris.has(`urn:ontology:relationship:${candidate}`.toLowerCase()) ||
		existingLinks.has(`lnk_${candidate}`.toLowerCase())
	) {
		candidate = `${baseSlug}_${suffix}`;
		suffix += 1;
	}
	return {
		slug: candidate,
		relationshipUri: `urn:ontology:relationship:${candidate}`,
		targetLink: `lnk_${candidate}`,
	};
}

export const OntologyStudioApp: React.FC<OntologyStudioAppProps> = ({
	className = "",
}) => {
	const activeSession = useStudioStore((s) => s.activeSession);
	const sessions = useStudioStore((s) => s.sessions);
	const activeMode = useStudioStore((s) => s.activeMode);
	const userRole = useStudioStore((s) => s.userRole);
	const authRequired = useStudioStore((s) => s.authRequired);
	const authInitConflict = useStudioStore((s) => s.authInitConflict);
	const authHint = useStudioStore((s) => s.authHint);
	const isBootstrappingAuth = useStudioStore((s) => s.isBootstrappingAuth);
	const layout = useStudioStore((s) => s.layout);
	const toasts = useStudioStore((s) => s.toasts);
	const sseEvents = useStudioStore((s) => s.sseEvents);
	const nodes = useStudioStore((s) => s.nodes);
	const edges = useStudioStore((s) => s.edges);
	const committedNodes = useStudioStore((s) => s.committedNodes);
	const committedEdges = useStudioStore((s) => s.committedEdges);
	const selectedEdgeId = useStudioStore((s) => s.selectedEdgeId);
	const hasUnsavedChanges = useStudioStore((s) => s.hasUnsavedChanges);
	const isCommitting = useStudioStore((s) => s.isCommitting);
	const isHistoricalView = useStudioStore((s) => s.isHistoricalView);
	const parsingState = useStudioStore((s) => s.parsingState);
	const setAuthToken = useStudioStore((s) => s.setAuthToken);
	const exchangeLaunchNonce = useStudioStore((s) => s.exchangeLaunchNonce);
	const bootstrapLoopbackAuth = useStudioStore((s) => s.bootstrapLoopbackAuth);
	const loadSessions = useStudioStore((s) => s.loadSessions);
	const selectSession = useStudioStore((s) => s.selectSession);
	const createSession = useStudioStore((s) => s.createSession);
	const addConcept = useStudioStore((s) => s.addConcept);
	const addRelationship = useStudioStore((s) => s.addRelationship);
	const deleteRelationship = useStudioStore((s) => s.deleteRelationship);
	const selectNode = useStudioStore((s) => s.selectNode);
	const commitChanges = useStudioStore((s) => s.commitChanges);
	const discardChanges = useStudioStore((s) => s.discardChanges);
	const setLeftDrawerTab = useStudioStore((s) => s.setLeftDrawerTab);
	const toggleLeftDrawer = useStudioStore((s) => s.toggleLeftDrawer);
	const toggleBottomDock = useStudioStore((s) => s.toggleBottomDock);
	const setActiveModal = useStudioStore((s) => s.setActiveModal);
	const dismissToast = useStudioStore((s) => s.dismissToast);
	const loadAndComputeDiff = useStudioStore((s) => s.loadAndComputeDiff);
	const diffState = useStudioStore((s) => s.diffState);
	const addToast = useStudioStore((s) => s.addToast);

	// Auth bootstrap token state
	const [tokenInput, setTokenInput] = useState("");
	const tokenInputRef = useRef<HTMLInputElement | null>(null);

	useEffect(() => {
		if (authRequired && authInitConflict) {
			tokenInputRef.current?.focus();
		}
	}, [authRequired, authInitConflict]);

	// Quick-create & commit toolbar state
	const [quickCreateMode, setQuickCreateMode] = useState<
		"concept" | "relationship" | null
	>(null);
	const [newConceptName, setNewConceptName] = useState("");
	const [newConceptDefinition, setNewConceptDefinition] = useState("");
	const [newConceptArchetype, setNewConceptArchetype] = useState(
		"CORE_BUSINESS_CONCEPT",
	);
	const [newRelName, setNewRelName] = useState("");
	const [newRelSourceUri, setNewRelSourceUri] = useState("");
	const [newRelTargetUri, setNewRelTargetUri] = useState("");
	const [newRelCardinality, setNewRelCardinality] = useState<
		"one_to_one" | "one_to_many" | "many_to_many"
	>("one_to_many");
	const [commitRationale, setCommitRationale] = useState("");

	// New Session form state
	const [newSessionId, setNewSessionId] = useState("");
	const [newNamespace, setNewNamespace] = useState("");
	const [newOwner, setNewOwner] = useState("");
	const [newWorkspace, setNewWorkspace] = useState("");
	const [isCreatingSession, setIsCreatingSession] = useState(false);

	// On mount, scrub any #token=<nonce> / ?nonce=<nonce>, exchange cookie, then load sessions
	useEffect(() => {
		let cancelled = false;
		const runBootstrap = async () => {
			const nonce = extractAndScrubLaunchNonce();
			if (nonce) {
				await exchangeLaunchNonce(nonce);
			}
			if (!cancelled) {
				await loadSessions();
			}
		};
		void runBootstrap();
		return () => {
			cancelled = true;
		};
	}, [exchangeLaunchNonce, loadSessions]);

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

	const handleAuthenticateToken = async (e: React.FormEvent) => {
		e.preventDefault();
		if (!tokenInput.trim()) return;
		setAuthToken(tokenInput.trim());
		setTokenInput("");
		await loadSessions();
	};

	const handleQuickAddConcept = (e: React.FormEvent) => {
		e.preventDefault();
		const cleanName = newConceptName.trim();
		if (!cleanName) return;
		const { conceptUri, targetHub } = buildUniqueConceptIdentifiers(
			cleanName,
			nodes,
			committedNodes,
		);
		addConcept({
			id: conceptUri,
			concept_uri: conceptUri,
			concept_name: cleanName,
			elm_archetype: newConceptArchetype,
			definition: newConceptDefinition.trim(),
			target_hub: targetHub,
			parent_concept_uri: null,
			grounding_status: "UNGROUNDED",
			attributes: [],
			synonyms: [],
			external_mappings: [],
		});
		selectNode(conceptUri);
		setNewConceptName("");
		setNewConceptDefinition("");
		setQuickCreateMode(null);
	};

	const handleQuickAddRelationship = (e: React.FormEvent) => {
		e.preventDefault();
		const cleanName = newRelName.trim();
		const srcUri = newRelSourceUri || nodes[0]?.concept_uri || "";
		const tgtUri =
			newRelTargetUri || nodes[1]?.concept_uri || nodes[0]?.concept_uri || "";
		if (!cleanName || !srcUri || !tgtUri) return;
		const { relationshipUri, targetLink } = buildUniqueRelationshipIdentifiers(
			cleanName,
			srcUri,
			tgtUri,
			edges,
			committedEdges,
		);
		addRelationship({
			id: relationshipUri,
			relationship_uri: relationshipUri,
			relationship_name: cleanName,
			target_link: targetLink,
			source_concept_uri: srcUri,
			target_concept_uri: tgtUri,
			endpoints: [srcUri, tgtUri],
			cardinality: newRelCardinality,
		});
		setNewRelName("");
		setQuickCreateMode(null);
	};

	const handleCommitToolbarClick = async () => {
		const ok = await commitChanges(
			commitRationale.trim() || "Workbench Studio visual edits",
		);
		if (ok) {
			setCommitRationale("");
		}
	};

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
		if (hasUnsavedChanges) {
			const baseVersion = activeSession.active_version || "0.0.0";
			void loadAndComputeDiff(baseVersion, "draft");
			setLeftDrawerTab("history");
			if (!layout.leftDrawerOpen) {
				toggleLeftDrawer();
			}
			return;
		}
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
		void loadAndComputeDiff(baseVersion, targetVersion);
		setLeftDrawerTab("history");
		if (!layout.leftDrawerOpen) {
			toggleLeftDrawer();
		}
	};

	const canMutateGraph =
		Boolean(activeSession) &&
		(userRole === "editor" || userRole === "admin") &&
		!isHistoricalView;
	const roleLabel =
		userRole === "admin"
			? "Admin"
			: userRole === "automated"
				? "Automated"
				: userRole === "viewer"
					? "Viewer"
					: "Editor";
	const selectedEdge = edges.find(
		(e) => e.id === selectedEdgeId || e.relationship_uri === selectedEdgeId,
	);
	const isSelectedEdgeCommitted = selectedEdge
		? studioStore
				.getState()
				.committedEdges.some(
					(ce) => ce.relationship_uri === selectedEdge.relationship_uri,
				)
		: false;

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
								title={`Authenticated RBAC role: ${roleLabel}`}
								data-testid="studio-role-badge"
							>
								Role: {roleLabel}
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
						className="btn-secondary btn-sm"
						onClick={() =>
							setQuickCreateMode((m) => (m === "concept" ? null : "concept"))
						}
						disabled={!canMutateGraph}
						data-testid="add-concept-btn"
						title="Add a new Canonical Business Concept to working draft"
					>
						+ Concept
					</button>
					<button
						type="button"
						className="btn-secondary btn-sm"
						onClick={() =>
							setQuickCreateMode((m) =>
								m === "relationship" ? null : "relationship",
							)
						}
						disabled={!canMutateGraph || nodes.length === 0}
						data-testid="add-relationship-btn"
						title="Add a new Relationship Link between concepts"
					>
						+ Relationship
					</button>
					{selectedEdge && (
						<button
							type="button"
							className="btn-danger btn-sm"
							onClick={() => deleteRelationship(selectedEdge.relationship_uri)}
							disabled={
								!canMutateGraph ||
								(isSelectedEdgeCommitted && userRole !== "admin")
							}
							data-testid="delete-relationship-btn"
							title={
								isSelectedEdgeCommitted && userRole !== "admin"
									? "Admin role required to remove committed relationships"
									: `Delete relationship ${selectedEdge.relationship_name}`
							}
						>
							Delete Link
						</button>
					)}
					<button
						type="button"
						className="btn-primary btn-sm"
						onClick={() => void handleCommitToolbarClick()}
						disabled={!canMutateGraph || !hasUnsavedChanges || isCommitting}
						data-testid="commit-changes-btn"
						title="Commit working draft as an OntologyDelta against session head"
					>
						{isCommitting ? "Committing..." : "Commit Changes"}
					</button>
					<button
						type="button"
						className="btn-secondary btn-sm"
						onClick={discardChanges}
						disabled={!hasUnsavedChanges || isCommitting}
						data-testid="discard-changes-btn"
						title="Discard uncommitted working draft edits"
					>
						Discard
					</button>
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

			{/* Authentication Bootstrap Banner when 401 Unauthorized */}
			{authRequired && (
				<div
					className="studio-auth-bootstrap-banner"
					role="region"
					aria-label="Studio Authentication Required"
					data-testid="studio-auth-bootstrap"
				>
					<form
						onSubmit={(e) => void handleAuthenticateToken(e)}
						className="auth-bootstrap-form"
					>
						<strong>Studio Authentication Required:</strong>
						<input
							ref={tokenInputRef}
							type="password"
							className="form-input auth-token-input"
							placeholder="Enter Studio API Bearer Token (OPENDV_TOKEN or .local_token)..."
							value={tokenInput}
							onChange={(e) => setTokenInput(e.target.value)}
							data-testid="auth-token-input"
						/>
						<button
							type="submit"
							className="btn-primary btn-sm"
							disabled={!tokenInput.trim() || isBootstrappingAuth}
							data-testid="auth-token-submit-btn"
						>
							Authenticate
						</button>
						<button
							type="button"
							className="btn-secondary btn-sm"
							onClick={() => void bootstrapLoopbackAuth()}
							disabled={isBootstrappingAuth}
							data-testid="loopback-init-btn"
						>
							{isBootstrappingAuth
								? "Initializing..."
								: "Initialize Local Loopback Session"}
						</button>
					</form>
					{authHint && (
						<output
							className="auth-conflict-hint"
							data-testid="auth-conflict-hint"
						>
							{authHint}
						</output>
					)}
				</div>
			)}

			{/* Quick-Create Concept / Relationship Inline Bar */}
			{quickCreateMode === "concept" && canMutateGraph && (
				<form
					onSubmit={handleQuickAddConcept}
					className="quick-create-bar"
					data-testid="quick-add-concept-panel"
				>
					<strong>New Concept:</strong>
					<input
						type="text"
						className="form-input"
						placeholder="Concept Name (e.g. Limited Partner)"
						value={newConceptName}
						onChange={(e) => setNewConceptName(e.target.value)}
						required
						data-testid="quick-concept-name-input"
					/>
					<select
						className="form-select"
						value={newConceptArchetype}
						onChange={(e) => setNewConceptArchetype(e.target.value)}
						data-testid="quick-concept-archetype-select"
					>
						<option value="CORE_BUSINESS_CONCEPT">CORE_BUSINESS_CONCEPT</option>
						<option value="EVENT">EVENT</option>
						<option value="PARTY">PARTY</option>
						<option value="AGREEMENT">AGREEMENT</option>
						<option value="PRODUCT">PRODUCT</option>
						<option value="ACCOUNT">ACCOUNT</option>
						<option value="LOCATION">LOCATION</option>
					</select>
					<input
						type="text"
						className="form-input"
						placeholder="Normative definition..."
						value={newConceptDefinition}
						onChange={(e) => setNewConceptDefinition(e.target.value)}
						data-testid="quick-concept-definition-input"
					/>
					<button
						type="submit"
						className="btn-primary btn-sm"
						disabled={!newConceptName.trim()}
						data-testid="confirm-add-concept-btn"
					>
						Create Concept
					</button>
					<button
						type="button"
						className="btn-secondary btn-sm"
						onClick={() => setQuickCreateMode(null)}
					>
						Cancel
					</button>
				</form>
			)}

			{quickCreateMode === "relationship" && canMutateGraph && (
				<form
					onSubmit={handleQuickAddRelationship}
					className="quick-create-bar"
					data-testid="quick-add-relationship-panel"
				>
					<strong>New Relationship:</strong>
					<input
						type="text"
						className="form-input"
						placeholder="Relationship Name (e.g. commits_capital_to)"
						value={newRelName}
						onChange={(e) => setNewRelName(e.target.value)}
						required
						data-testid="quick-rel-name-input"
					/>
					<select
						aria-label="Source Concept"
						className="form-select"
						value={newRelSourceUri || nodes[0]?.concept_uri || ""}
						onChange={(e) => setNewRelSourceUri(e.target.value)}
						data-testid="quick-rel-source-select"
					>
						{nodes.map((n) => (
							<option key={n.concept_uri} value={n.concept_uri}>
								From: {n.concept_name}
							</option>
						))}
					</select>
					<select
						aria-label="Target Concept"
						className="form-select"
						value={
							newRelTargetUri ||
							nodes[1]?.concept_uri ||
							nodes[0]?.concept_uri ||
							""
						}
						onChange={(e) => setNewRelTargetUri(e.target.value)}
						data-testid="quick-rel-target-select"
					>
						{nodes.map((n) => (
							<option key={n.concept_uri} value={n.concept_uri}>
								To: {n.concept_name}
							</option>
						))}
					</select>
					<select
						aria-label="Cardinality"
						className="form-select"
						value={newRelCardinality}
						onChange={(e) =>
							setNewRelCardinality(
								e.target.value as "one_to_one" | "one_to_many" | "many_to_many",
							)
						}
						data-testid="quick-rel-cardinality-select"
					>
						<option value="one_to_one">one_to_one</option>
						<option value="one_to_many">one_to_many</option>
						<option value="many_to_many">many_to_many</option>
					</select>
					<button
						type="submit"
						className="btn-primary btn-sm"
						disabled={!newRelName.trim() || nodes.length === 0}
						data-testid="confirm-add-relationship-btn"
					>
						Create Relationship
					</button>
					<button
						type="button"
						className="btn-secondary btn-sm"
						onClick={() => setQuickCreateMode(null)}
					>
						Cancel
					</button>
				</form>
			)}

			{hasUnsavedChanges && canMutateGraph && (
				<div className="unsaved-changes-bar" data-testid="unsaved-changes-bar">
					<span>● Uncommitted working draft changes</span>
					<input
						type="text"
						className="form-input commit-rationale-input"
						placeholder="Optional commit rationale..."
						value={commitRationale}
						onChange={(e) => setCommitRationale(e.target.value)}
						data-testid="commit-rationale-input"
					/>
				</div>
			)}

			{/* Standalone Mode Banner */}
			{activeMode === "local_standalone" && (
				<div
					className="standalone-mode-banner"
					role="status"
					data-testid="standalone-mode-banner"
				>
					<span>
						⚡ Standalone Mode — sessions & versions are persisted in local CAS
						(.opendv/ontology_studio)
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
									? `[SESSION] id=${activeSession.session_id} active_version=${activeSession.active_version} status=${activeSession.status}\n[GRAPH] concepts=${nodes.length} relationships=${edges.length}\n[PARSING] status=${parsingState.status} stage=${parsingState.stage || "idle"} progress=${parsingState.progress_pct}%${
											sseEvents.length > 0
												? `\n${sseEvents
														.slice(0, 12)
														.map(
															(ev) =>
																`[JOB ${ev.job_id || "studio"}] stage=${ev.stage}${ev.progress_pct !== undefined ? ` (${ev.progress_pct}%)` : ""} ${ev.message}`,
														)
														.join("\n")}`
												: ""
										}`
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
