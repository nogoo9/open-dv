/**
 * Step 1: Scope & Baseline — Option A (New Blank / Workspace Seed / Starter Packs)
 * & Option B (Evolve Existing v2 Sessions / Import .yaml|.json|.ttl) (`FR-ING-01`, `FR-SES-01`, `D25`)
 * (apps/opendv-web-workbench/src/ontology/wizard/Step1ScopeAndBaseline.tsx)
 */

import type React from "react";
import { CLAUDE_TOKENS, EXPLORER_FONTS } from "../../explorer/design-tokens";
import { safeUseState } from "../../explorer/modals/safeHooks";
import type { StarterPackMetadata, StudioSessionSummary } from "../store/types";
import type { WizardBaselineDigest } from "./wizard-types";

export interface Step1ScopeAndBaselineProps {
	baselineMode?: "new" | "evolve" | undefined;
	newSourceKind?: "blank" | "workspace_seed" | "starter_packs" | undefined;
	starterPacks?: StarterPackMetadata[] | undefined;
	selectedPackIds?: string[] | undefined;
	sessions?: StudioSessionSummary[] | undefined;
	legacySessionCount?: number | undefined;
	activeDigest?: WizardBaselineDigest | null | undefined;
	domainNamespace?: string | undefined;
	fetchImpl?: typeof fetch | undefined;
	apiUrl?: string | undefined;
	onModeChange?: ((mode: "new" | "evolve") => void) | undefined;
	onSourceKindChange?:
		| ((kind: "blank" | "workspace_seed" | "starter_packs") => void)
		| undefined;
	onTogglePack?: ((packId: string) => void) | undefined;
	onCreateSession?:
		| ((opts: {
				domainNamespace: string;
				fromWorkspace: boolean;
				packIds: string[];
		  }) => Promise<void>)
		| undefined;
	onImportOntology?:
		| ((req: {
				format: "yaml" | "json" | "ttl";
				content: string;
				domainNamespace: string;
		  }) => Promise<void>)
		| undefined;
	onSelectSession?: ((sessionId: string) => void) | undefined;
	onContinue?: (() => void) | undefined;
}

export const Step1ScopeAndBaseline: React.FC<Step1ScopeAndBaselineProps> = ({
	baselineMode = "new",
	newSourceKind = "blank",
	starterPacks = [],
	selectedPackIds = [],
	sessions = [],
	legacySessionCount = 0,
	activeDigest,
	domainNamespace: initialNamespace = "wealth_mgmt",
	onModeChange,
	onSourceKindChange,
	onTogglePack,
	onCreateSession,
	onImportOntology,
	onSelectSession,
	onContinue,
}) => {
	const [domainNs, setDomainNs] = safeUseState(initialNamespace);
	const [importContent, setImportContent] = safeUseState("");
	const [importFormat, setImportFormat] = safeUseState<"yaml" | "json" | "ttl">(
		"yaml",
	);

	const handleImportFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
		const file = e.target.files?.[0];
		if (!file) return;
		const name = file.name.toLowerCase();
		const fmt: "yaml" | "json" | "ttl" = name.endsWith(".ttl")
			? "ttl"
			: name.endsWith(".json")
				? "json"
				: "yaml";
		setImportFormat(fmt);
		const text = await file.text();
		setImportContent(text);
		if (onImportOntology && text.trim()) {
			await onImportOntology({
				format: fmt,
				content: text,
				domainNamespace: domainNs,
			});
		}
	};

	return (
		<section
			data-testid="wizard-step-1"
			style={{ display: "flex", flexDirection: "column", gap: "20px" }}
		>
			<header>
				<h1 className="claude-h1" style={{ margin: "0 0 6px" }}>
					Step 1 · Ontology Scope &amp; Baseline
				</h1>
				<p
					style={{
						margin: 0,
						fontSize: "14px",
						color: CLAUDE_TOKENS.colors.body,
					}}
				>
					Choose a starting ontology baseline or evolve an existing governed
					Studio v2 session.
				</p>
			</header>

			<div
				className="claude-responsive-grid"
				style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}
			>
				<div
					className={
						baselineMode === "new"
							? "claude-card-selected"
							: "claude-card-cream"
					}
					style={{
						minWidth: 0,
						padding: "18px",
						display: "flex",
						flexDirection: "column",
						gap: "12px",
					}}
				>
					<div
						style={{
							display: "flex",
							justifyContent: "space-between",
							alignItems: "center",
						}}
					>
						<h2 className="claude-h2" style={{ margin: 0 }}>
							Option A · Start New Baseline
						</h2>
						<button
							type="button"
							className={
								baselineMode === "new"
									? "claude-btn-primary"
									: "claude-btn-secondary"
							}
							data-testid="btn-mode-new-baseline"
							onClick={() => onModeChange?.("new")}
						>
							{baselineMode === "new" ? "Selected" : "Choose New"}
						</button>
					</div>

					<div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
						{(
							[
								{ kind: "blank", label: "Blank · v0.0.0" },
								{ kind: "workspace_seed", label: "Workspace Seed" },
								{ kind: "starter_packs", label: "Starter Packs" },
							] as const
						).map((opt) => (
							<button
								key={opt.kind}
								type="button"
								className={
									newSourceKind === opt.kind
										? "claude-btn-primary"
										: "claude-btn-secondary"
								}
								data-testid={`btn-source-kind-${opt.kind}`}
								onClick={() => {
									onModeChange?.("new");
									onSourceKindChange?.(opt.kind);
								}}
							>
								{opt.label}
							</button>
						))}
					</div>

					<label
						style={{
							display: "flex",
							flexDirection: "column",
							gap: "4px",
							fontSize: "12px",
							color: CLAUDE_TOKENS.colors.muted,
						}}
					>
						Domain Namespace
						<input
							type="text"
							data-testid="input-domain-namespace"
							value={domainNs}
							onChange={(e) => setDomainNs(e.target.value)}
							style={{
								padding: "7px 10px",
								borderRadius: "6px",
								border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
								background: CLAUDE_TOKENS.colors.canvas,
								color: CLAUDE_TOKENS.colors.ink,
							}}
						/>
					</label>

					{starterPacks.length > 0 && (
						<div
							style={{ display: "flex", flexDirection: "column", gap: "6px" }}
						>
							<span
								style={{
									fontSize: "12px",
									fontWeight: 600,
									color: CLAUDE_TOKENS.colors.ink,
								}}
							>
								Asset Management &amp; Domain Starter Packs
							</span>
							{starterPacks.map((pack) => {
								const active = selectedPackIds.includes(pack.pack_id);
								return (
									<label
										key={pack.pack_id}
										style={{
											display: "flex",
											justifyContent: "space-between",
											alignItems: "center",
											padding: "8px 10px",
											borderRadius: "6px",
											background: active
												? CLAUDE_TOKENS.colors.canvas
												: CLAUDE_TOKENS.colors.surfaceCreamStrong,
											border: `1px solid ${active ? CLAUDE_TOKENS.colors.primary : CLAUDE_TOKENS.colors.hairline}`,
											cursor: "pointer",
											fontSize: "12.5px",
										}}
									>
										<span>
											<input
												type="checkbox"
												checked={active}
												onChange={() => onTogglePack?.(pack.pack_id)}
												style={{ marginRight: "8px" }}
											/>
											<strong>{pack.display_name}</strong> — {pack.description}
										</span>
										<span
											style={{
												fontFamily: EXPLORER_FONTS.mono,
												fontSize: "11px",
												color: CLAUDE_TOKENS.colors.muted,
											}}
										>
											{pack.concept_count}c / {pack.relationship_count}r
										</span>
									</label>
								);
							})}
						</div>
					)}

					<button
						type="button"
						className="claude-btn-primary"
						data-testid="btn-create-wizard-session"
						onClick={() =>
							onCreateSession?.({
								domainNamespace: domainNs,
								fromWorkspace: newSourceKind === "workspace_seed",
								packIds:
									newSourceKind === "starter_packs" ? selectedPackIds : [],
							})
						}
					>
						Initialize Baseline Session
					</button>
				</div>

				<div
					className={
						baselineMode === "evolve"
							? "claude-card-selected"
							: "claude-card-cream"
					}
					style={{
						minWidth: 0,
						padding: "18px",
						display: "flex",
						flexDirection: "column",
						gap: "12px",
					}}
				>
					<div
						style={{
							display: "flex",
							justifyContent: "space-between",
							alignItems: "center",
						}}
					>
						<h2 className="claude-h2" style={{ margin: 0 }}>
							Option B · Evolve Existing / Import
						</h2>
						<button
							type="button"
							className={
								baselineMode === "evolve"
									? "claude-btn-primary"
									: "claude-btn-secondary"
							}
							data-testid="btn-mode-evolve-existing"
							onClick={() => onModeChange?.("evolve")}
						>
							{baselineMode === "evolve" ? "Selected" : "Evolve Existing"}
						</button>
					</div>

					<div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
						<span
							style={{
								fontSize: "12px",
								fontWeight: 600,
								color: CLAUDE_TOKENS.colors.ink,
							}}
						>
							Wizard Sessions (schema v2+)
						</span>
						{sessions.length === 0 ? (
							<div
								style={{ fontSize: "12px", color: CLAUDE_TOKENS.colors.muted }}
							>
								No existing v2 wizard sessions loaded yet.
							</div>
						) : (
							sessions.map((s) => (
								<button
									key={s.session_id}
									type="button"
									className="claude-btn-secondary"
									data-testid={`btn-select-session-${s.session_id}`}
									style={{
										display: "flex",
										justifyContent: "space-between",
										textAlign: "left",
									}}
									onClick={() => onSelectSession?.(s.session_id)}
								>
									<span>
										<strong>{s.session_id}</strong> ({s.domain_namespace})
									</span>
									<span
										style={{
											fontFamily: EXPLORER_FONTS.mono,
											fontSize: "11px",
										}}
									>
										{s.active_version} · {s.status}
									</span>
								</button>
							))
						)}
					</div>

					<div
						style={{
							display: "flex",
							flexDirection: "column",
							gap: "6px",
							borderTop: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
							paddingTop: "10px",
						}}
					>
						<span
							style={{
								fontSize: "12px",
								fontWeight: 600,
								color: CLAUDE_TOKENS.colors.ink,
							}}
						>
							Import Ontology File (.yaml, .json, .ttl)
						</span>
						<input
							type="file"
							accept=".yaml,.yml,.json,.ttl"
							data-testid="import-ontology-input"
							onChange={handleImportFile}
							style={{ fontSize: "12px" }}
						/>
						{importContent && (
							<button
								type="button"
								className="claude-btn-secondary"
								onClick={() =>
									onImportOntology?.({
										format: importFormat,
										content: importContent,
										domainNamespace: domainNs,
									})
								}
							>
								Import ({importFormat.toUpperCase()})
							</button>
						)}
					</div>

					{legacySessionCount > 0 && (
						<div
							data-testid="legacy-sessions-notice"
							style={{
								fontSize: "12px",
								padding: "8px 10px",
								borderRadius: "6px",
								background: CLAUDE_TOKENS.colors.surfaceCreamStrong,
								color: CLAUDE_TOKENS.colors.body,
							}}
						>
							{legacySessionCount} legacy studio session(s) hidden. Migrate via{" "}
							<code>opendv ontology studio bundle export</code>.
						</div>
					)}
				</div>
			</div>

			{activeDigest && (
				<div
					data-testid="selected-baseline-digest-card"
					className="claude-card-dark"
					style={{
						backgroundColor: CLAUDE_TOKENS.colors.surfaceDark,
						color: "#faf9f5",
						padding: "14px 18px",
						borderRadius: "10px",
						display: "flex",
						justifyContent: "space-between",
						alignItems: "center",
						gap: "16px",
					}}
				>
					<div>
						<div
							style={{
								fontSize: "11px",
								color: CLAUDE_TOKENS.colors.onDarkSoft,
								textTransform: "uppercase",
							}}
						>
							Selected Baseline Digest
						</div>
						<div style={{ fontSize: "14px", fontWeight: 600 }}>
							{activeDigest.label} · {activeDigest.sessionId}
						</div>
					</div>
					<div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
						<span
							style={{
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "12px",
								color: "#5db8a6",
							}}
						>
							{activeDigest.version}
						</span>
						<code
							style={{
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "12px",
								color: "#e8a55a",
							}}
						>
							{activeDigest.sha256.slice(0, 12)}
						</code>
						{onContinue && (
							<button
								type="button"
								className="claude-btn-primary"
								onClick={onContinue}
							>
								Continue to Sources
							</button>
						)}
					</div>
				</div>
			)}
		</section>
	);
};
