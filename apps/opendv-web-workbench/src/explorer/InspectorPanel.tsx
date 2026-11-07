/**
 * Contextual Inspector Panel (`InspectorPanel.tsx`)
 * Shared right-hand inspector for Pipeline Overview and Stage Drill-Down views
 * (`docs/design/open-dv-workbench/HANDOFF.md`).
 * Features interactive Steward Hold resolution controls with Action, Candidates,
 * Justification notes, and Ontology Delta checklist (Phase P7 ONT-04 / IP-6).
 */

import {
	type Artifact,
	type CaptainHoldDecisionRequest,
	type CaptainHoldDecisionResponse,
	type HoldResolutionParams,
	type Stage,
	getArtifactConsumedBy,
	getArtifactProducedBy,
} from "@opendv/protocol-ts";
import { type CSSProperties, useState } from "react";
import {
	type ProblemDetails,
	type StewardHoldCandidate,
	StewardHoldModal,
	type StewardHoldOntologyOp,
	parseProblemDetails,
} from "./StewardHoldModal";
import {
	EXPLORER_FONTS,
	EXPLORER_GLYPHS,
	EXPLORER_VIZ_LABELS,
	type GlyphStyleSpec,
	getGlyphCssStyle,
} from "./design-tokens";
import type {
	ExplorerSelectionType,
	ExplorerViewLevel,
} from "./explorer-store";

export interface InspectorPanelProps {
	view: ExplorerViewLevel;
	selType: ExplorerSelectionType;
	selId: string | number | null;
	stages: readonly Stage[];
	currentStage?: Stage | undefined;
	artifacts: Readonly<Record<string, Artifact>>;
	onClearSelection: () => void;
	onOpenFullArtifact: (artifactId: string) => void;
	onResolveHold?: ((params: HoldResolutionParams) => void) | undefined;
	runId?: string | undefined;
	onSubmitDecision?:
		| ((
				decision: CaptainHoldDecisionRequest,
		  ) => Promise<CaptainHoldDecisionResponse>)
		| undefined;
	activeHoldId?: string | undefined;
	holdCandidates?: readonly StewardHoldCandidate[] | undefined;
	holdOntologyOps?: readonly StewardHoldOntologyOp[] | undefined;
}

function buildArtifactPreview(artifact: Artifact): string {
	if (artifact.id === "extract" && artifact.doc) {
		return artifact.doc.extractions
			.map(
				(e) =>
					`"${e.quote}"\n  → ${e.tier} · ${e.conf}${e.tier === "Tier-U3" ? " · hold" : ""}`,
			)
			.join("\n");
	}
	if (artifact.viz === "table" && artifact.cols && artifact.rows) {
		return [
			artifact.cols.join(" | "),
			...artifact.rows.slice(0, 6).map((r) => r.join(" | ")),
		].join("\n");
	}
	return artifact.raw.split("\n").slice(0, 10).join("\n");
}

const BTN_STYLE: CSSProperties = {
	all: "unset",
	cursor: "pointer",
	fontSize: "11px",
	padding: "4px 8px",
	borderRadius: "4px",
	textAlign: "center",
};

export function InspectorPanel({
	view,
	selType,
	selId,
	stages,
	currentStage,
	artifacts,
	onClearSelection,
	onOpenFullArtifact,
	onResolveHold,
	runId = "current-run",
	onSubmitDecision,
	activeHoldId: explicitHoldId,
	holdCandidates: explicitCandidates,
	holdOntologyOps: explicitOntologyOps,
}: InspectorPanelProps) {
	let hasSelection = false;
	let isArtifact = false;
	let glyph: GlyphStyleSpec = EXPLORER_GLYPHS.data;
	let kindLabel = "";
	let title = "";
	let desc = "";
	let meta: { k: string; v: string }[] = [];
	let preview = "";
	let vizName = "";
	let selectedArtifactId: string | null = null;
	let detectedHoldId: string | null = explicitHoldId ?? null;
	let detectedCandidates: StewardHoldCandidate[] = explicitCandidates
		? [...explicitCandidates]
		: [];
	let detectedOps: StewardHoldOntologyOp[] = explicitOntologyOps
		? [...explicitOntologyOps]
		: [];

	if (selType === "artifact" && typeof selId === "string" && artifacts[selId]) {
		const art = artifacts[selId];
		hasSelection = true;
		isArtifact = true;
		selectedArtifactId = selId;
		glyph =
			art.group === "Setting" ? EXPLORER_GLYPHS.setting : EXPLORER_GLYPHS.data;
		kindLabel = `Data · ${art.group}`;
		title = art.name;
		desc = art.desc;
		meta = [
			{ k: "Type", v: art.type },
			{ k: "Format", v: art.fmt },
			{ k: "Produced by", v: getArtifactProducedBy(stages, selId) },
			{ k: "Consumed by", v: getArtifactConsumedBy(stages, selId) },
		];
		preview = buildArtifactPreview(art);
		vizName = EXPLORER_VIZ_LABELS[art.viz];

		const u3Hold = art.doc?.extractions.find(
			(e) =>
				(e.tier === "Tier-U3" && e.holdId) ||
				(e.tier === "Tier-U3" && !e.resolved),
		);
		if (u3Hold?.holdId) {
			detectedHoldId = u3Hold.holdId;
			detectedCandidates = (u3Hold.candidates ?? []).map((c) => ({
				id: c.id,
				label: c.label,
				score: c.score,
			}));
			if (detectedCandidates.length === 0) {
				detectedCandidates = [
					{
						id: "TAX-DE-800202",
						label: "Rheinland Precision GmbH",
						score: 0.95,
					},
				];
			}
		}

		if (art.type === "ontology_proposal" || art.id.includes("proposal")) {
			try {
				const parsed = JSON.parse(art.raw) as {
					proposal_id?: string;
					delta?: { operations?: StewardHoldOntologyOp[] };
				};
				if (!detectedHoldId)
					detectedHoldId = parsed.proposal_id ?? `hold-${art.id}`;
				if (parsed.delta?.operations) detectedOps = parsed.delta.operations;
			} catch {
				// not json
			}
		}
	} else if (
		view === "stage" &&
		selType === "step" &&
		typeof selId === "number" &&
		currentStage?.steps[selId]
	) {
		const step = currentStage.steps[selId];
		hasSelection = true;
		glyph = EXPLORER_GLYPHS.control;
		kindLabel = `Control · step ${selId + 1} of ${currentStage.steps.length}`;
		title = step.name;
		desc = step.desc;
		meta = [
			{ k: "Kind", v: step.kind ?? "action" },
			...(step.branch ? [{ k: "Branches", v: step.branch }] : []),
			{
				k: "Reads",
				v: step.reads.map((r) => artifacts[r]?.name ?? r).join(", ") || "—",
			},
			{
				k: "Writes",
				v: step.writes.map((r) => artifacts[r]?.name ?? r).join(", ") || "—",
			},
		];
		if (
			currentStage.key === "STAGE_02_SCOUT_UNSTRUCTURED" &&
			step.name.toLowerCase().includes("steward hold")
		) {
			detectedHoldId = "hold-tier-u3-rheinland-precision";
			detectedCandidates = [
				{ id: "TAX-DE-800202", label: "Rheinland Precision GmbH", score: 0.95 },
			];
		}
	} else if (
		view === "stage" &&
		selType === "component" &&
		typeof selId === "number" &&
		currentStage?.comps[selId]
	) {
		const comp = currentStage.comps[selId];
		hasSelection = true;
		glyph = EXPLORER_GLYPHS.component;
		kindLabel = `Component · ${comp[1]}`;
		title = comp[0];
		desc = comp[2];
		meta = [
			{ k: "Source", v: comp[3] },
			{ k: "Stage", v: currentStage.key },
		];
	}

	if (!detectedHoldId && currentStage?.tag === "ASK-USER") {
		detectedHoldId = `hold-${currentStage.key.toLowerCase()}`;
	}

	const activeHoldId = detectedHoldId;

	const [action, setAction] = useState<"APPROVE" | "REJECT" | "ANSWER">(
		"APPROVE",
	);
	const [selectedCandidate, setSelectedCandidate] = useState<string>(
		detectedCandidates[0]?.id ?? "TAX-DE-800202",
	);
	const [rationale, setRationale] = useState<string>("");
	const [selectedOpIds, setSelectedOpIds] = useState<Set<string>>(
		() => new Set(detectedOps.map((o) => o.op_id)),
	);
	const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
	const [problemError, setProblemError] = useState<ProblemDetails | null>(null);
	const [resolvedMessage, setResolvedMessage] = useState<string | null>(null);
	const [isModalOpen, setIsModalOpen] = useState<boolean>(false);

	const handleToggleOp = (opId: string) => {
		setSelectedOpIds((prev) => {
			const next = new Set(prev);
			if (next.has(opId)) next.delete(opId);
			else next.add(opId);
			return next;
		});
	};

	const handleResolve = async (customAction?: "APPROVE" | "REJECT") => {
		if (!activeHoldId || isSubmitting) return;
		const effectiveAction = customAction ?? action;
		setIsSubmitting(true);
		setProblemError(null);
		const candidateId = selectedCandidate || "TAX-DE-800202";

		const request: CaptainHoldDecisionRequest = {
			decision_type: "RESOLVE_CAPTAIN_HOLD",
			action: effectiveAction,
			decision: effectiveAction,
			hold_id: activeHoldId,
			event_id: "evt-commerce-004",
			steward_id: "steward-captain",
			selected_candidate_id: candidateId,
			resolution_notes:
				rationale ||
				(effectiveAction === "APPROVE"
					? `Approved candidate ${candidateId}`
					: `Rejected hold ${activeHoldId}`),
			steward_rationale: rationale || undefined,
		};

		if (detectedOps.length > 0) {
			(request as unknown as Record<string, unknown>).hold_option = {
				kind:
					effectiveAction === "APPROVE"
						? "accept_ontology_delta"
						: "reject_ontology_delta",
				accepted_op_ids: Array.from(selectedOpIds),
				delta_sha256: "sha256-delta",
				steward_note: rationale || "Steward decision applied",
			};
		}

		try {
			if (onSubmitDecision) await onSubmitDecision(request);
			onResolveHold?.({
				holdId: activeHoldId,
				action: effectiveAction === "REJECT" ? "REJECT" : "APPROVE",
				selectedCandidateId: candidateId,
				resolutionNotes: rationale || undefined,
				eventId: "evt-commerce-004",
			});
			setResolvedMessage("ASK-USER → resolved · Resumes STAGE_03");
		} catch (err) {
			setProblemError(parseProblemDetails(err));
		} finally {
			setIsSubmitting(false);
		}
	};

	if (!hasSelection && view === "pipeline") {
		return null;
	}

	return (
		<aside
			aria-label="Details Inspector"
			data-testid="explorer-inspector"
			style={{ width: "340px", flexShrink: 0, position: "sticky", top: "24px" }}
		>
			{!hasSelection ? (
				<div
					data-testid="inspector-empty-state"
					style={{
						background: "#FFFFFF",
						border: "1px dashed #C9CDD3",
						borderRadius: "8px",
						padding: "16px",
						fontSize: "12.5px",
						color: "#6B7280",
					}}
				>
					Select a step, a data item or a component to see what it does.
				</div>
			) : (
				<div
					style={{
						background: "#FFFFFF",
						border: "1px solid #E3E5E8",
						borderRadius: "8px",
						padding: "16px",
						display: "flex",
						flexDirection: "column",
						gap: "12px",
					}}
				>
					{/* Header */}
					<div
						style={{
							display: "flex",
							alignItems: "center",
							justifyContent: "space-between",
						}}
					>
						<div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
							<span style={getGlyphCssStyle(glyph, 10)} />
							<span
								style={{
									fontSize: "11px",
									fontWeight: 600,
									textTransform: "uppercase",
									letterSpacing: "0.06em",
									color: "#6B7280",
								}}
							>
								{kindLabel}
							</span>
						</div>
						<button
							type="button"
							data-testid="btn-inspector-close"
							onClick={onClearSelection}
							style={{
								all: "unset",
								cursor: "pointer",
								fontSize: "12px",
								color: "#6B7280",
							}}
						>
							Close
						</button>
					</div>

					{/* Title & Description */}
					<div
						data-testid="inspector-title"
						style={{
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "14px",
							fontWeight: 600,
							color: "#16181D",
							wordBreak: "break-word",
						}}
					>
						{title}
					</div>
					<div
						style={{ fontSize: "12.5px", color: "#3B414C", lineHeight: 1.5 }}
					>
						{desc}
					</div>

					{/* Metadata List */}
					<dl
						style={{
							margin: 0,
							borderTop: "1px solid #ECEEF1",
							paddingTop: "10px",
							display: "flex",
							flexDirection: "column",
							gap: "6px",
						}}
					>
						{meta.map((row) => (
							<div
								key={row.k}
								style={{
									display: "flex",
									justifyContent: "space-between",
									gap: "10px",
									fontSize: "12px",
								}}
							>
								<dt style={{ color: "#6B7280" }}>{row.k}</dt>
								<dd
									style={{
										margin: 0,
										fontFamily: EXPLORER_FONTS.mono,
										fontSize: "11.5px",
										color: "#16181D",
									}}
								>
									{row.v}
								</dd>
							</div>
						))}
					</dl>

					{/* Steward Hold Resolution Section */}
					{activeHoldId && (onResolveHold || onSubmitDecision) && (
						<div
							data-testid="inspector-steward-hold-box"
							style={{
								padding: "12px",
								borderRadius: "6px",
								background: "#FFF9F0",
								border: "1px solid #F3D9B1",
								display: "flex",
								flexDirection: "column",
								gap: "10px",
							}}
						>
							<div
								style={{
									display: "flex",
									alignItems: "center",
									justifyContent: "space-between",
								}}
							>
								<span
									style={{
										fontFamily: EXPLORER_FONTS.mono,
										fontSize: "11px",
										fontWeight: 600,
										color: "#8A3B07",
									}}
								>
									Steward hold · {activeHoldId}
								</span>
								<button
									type="button"
									data-testid="btn-inspector-open-modal"
									onClick={() => setIsModalOpen(true)}
									style={{
										all: "unset",
										cursor: "pointer",
										fontSize: "10.5px",
										fontWeight: 600,
										color: "#1D4ED8",
										textDecoration: "underline",
									}}
								>
									Full Dialog ↗
								</button>
							</div>

							{problemError && (
								<div
									data-testid="inspector-problem-details-error"
									role="alert"
									style={{
										padding: "8px",
										borderRadius: "4px",
										background: "#FDECEA",
										border: "1px solid #F5C2C7",
										color: "#8F1D12",
										fontSize: "11.5px",
									}}
								>
									<strong data-testid="inspector-problem-title">
										{problemError.title ?? "Error"}
									</strong>
									<div data-testid="inspector-problem-detail">
										{problemError.detail ?? "Submission failed."}
									</div>
									{problemError.status && (
										<div
											data-testid="inspector-problem-status"
											style={{
												fontFamily: EXPLORER_FONTS.mono,
												fontSize: "10px",
											}}
										>
											HTTP {problemError.status}
										</div>
									)}
								</div>
							)}

							{resolvedMessage && (
								<output
									data-testid="inspector-hold-resolved-status"
									style={{
										padding: "6px 8px",
										borderRadius: "4px",
										background: "#EEF0F2",
										color: "#15803D",
										fontSize: "11.5px",
										fontWeight: 600,
										display: "block",
									}}
								>
									✓ {resolvedMessage}
								</output>
							)}

							<div style={{ display: "flex", gap: "4px" }}>
								{(["APPROVE", "REJECT", "ANSWER"] as const).map((act) => (
									<button
										key={act}
										type="button"
										data-testid={`inspector-hold-action-${act.toLowerCase()}`}
										onClick={() => setAction(act)}
										disabled={isSubmitting}
										style={{
											...BTN_STYLE,
											flex: 1,
											border:
												action === act
													? "1px solid #16181D"
													: "1px solid #E3E5E8",
											background: action === act ? "#16181D" : "#FFFFFF",
											color: action === act ? "#FFFFFF" : "#16181D",
										}}
									>
										{act}
									</button>
								))}
							</div>

							{detectedCandidates.length > 0 && action === "APPROVE" && (
								<div
									style={{
										display: "flex",
										flexDirection: "column",
										gap: "4px",
									}}
								>
									<label
										htmlFor="inspector-select-candidate"
										style={{
											fontSize: "10.5px",
											fontWeight: 600,
											color: "#8A3B07",
										}}
									>
										CANDIDATE
									</label>
									<select
										id="inspector-select-candidate"
										data-testid="inspector-select-candidate"
										value={selectedCandidate}
										onChange={(e) => setSelectedCandidate(e.target.value)}
										disabled={isSubmitting}
										style={{
											fontFamily: EXPLORER_FONTS.mono,
											fontSize: "11px",
											padding: "4px 6px",
											borderRadius: "4px",
											border: "1px solid #D4D7DC",
											background: "#FFFFFF",
										}}
									>
										{detectedCandidates.map((c) => (
											<option key={c.id} value={c.id}>
												{c.label} ({c.id})
											</option>
										))}
									</select>
								</div>
							)}

							{detectedOps.length > 0 && (
								<div
									data-testid="inspector-ontology-ops-checklist"
									style={{
										padding: "6px",
										borderRadius: "4px",
										background: "#FFFFFF",
										border: "1px solid #E3E5E8",
										display: "flex",
										flexDirection: "column",
										gap: "4px",
									}}
								>
									<div
										style={{
											fontSize: "10.5px",
											fontWeight: 600,
											color: "#6B7280",
										}}
									>
										PROPOSED OPS ({selectedOpIds.size}/{detectedOps.length})
									</div>
									{detectedOps.slice(0, 3).map((op) => (
										<label
											key={op.op_id}
											style={{
												display: "flex",
												alignItems: "center",
												gap: "6px",
												fontSize: "11px",
												cursor: "pointer",
											}}
										>
											<input
												type="checkbox"
												data-testid={`inspector-checkbox-op-${op.op_id}`}
												checked={selectedOpIds.has(op.op_id)}
												onChange={() => handleToggleOp(op.op_id)}
												disabled={isSubmitting || action === "REJECT"}
											/>
											<span style={{ fontFamily: EXPLORER_FONTS.mono }}>
												{op.op_id}
											</span>
										</label>
									))}
								</div>
							)}

							<input
								id="inspector-hold-rationale"
								type="text"
								data-testid="inspector-hold-rationale"
								value={rationale}
								onChange={(e) => setRationale(e.target.value)}
								placeholder="Steward decision rationale..."
								disabled={isSubmitting}
								style={{
									fontFamily: EXPLORER_FONTS.sans,
									fontSize: "11.5px",
									padding: "4px 6px",
									borderRadius: "4px",
									border: "1px solid #D4D7DC",
								}}
							/>

							<div style={{ display: "flex", gap: "6px" }}>
								<button
									type="button"
									data-testid="btn-inspector-approve-hold"
									disabled={isSubmitting}
									onClick={() => handleResolve("APPROVE")}
									style={{
										...BTN_STYLE,
										flex: 1,
										background: "#16181D",
										color: "#FFFFFF",
										fontWeight: 500,
									}}
								>
									Approve candidate
								</button>
								<button
									type="button"
									data-testid="btn-inspector-reject-hold"
									disabled={isSubmitting}
									onClick={() => handleResolve("REJECT")}
									style={{
										...BTN_STYLE,
										flex: 1,
										background: "#FDECEA",
										color: "#8F1D12",
										border: "1px solid #E3E5E8",
									}}
								>
									Reject
								</button>
							</div>

							<button
								type="button"
								data-testid="btn-inspector-submit-decision"
								disabled={isSubmitting}
								onClick={() => handleResolve()}
								style={{
									...BTN_STYLE,
									width: "100%",
									border: "1px solid #D4D7DC",
									background: "#FFFFFF",
									fontWeight: 600,
								}}
							>
								{isSubmitting ? "Submitting..." : `Submit Decision (${action})`}
							</button>

							<StewardHoldModal
								isOpen={isModalOpen}
								onClose={() => setIsModalOpen(false)}
								holdId={activeHoldId}
								runId={runId}
								stageKey={currentStage?.key ?? "STAGE_02_SCOUT_UNSTRUCTURED"}
								candidates={detectedCandidates}
								initialCandidateId={selectedCandidate}
								ontologyOps={detectedOps}
								onSubmit={onSubmitDecision}
								onResolveHold={onResolveHold}
								onSuccess={(resp) => {
									setResolvedMessage(
										`ASK-USER → resolved (${resp.decision_id}) · Resumes STAGE_03`,
									);
									setIsModalOpen(false);
								}}
							/>
						</div>
					)}

					{/* Artifact Preview */}
					{isArtifact && selectedArtifactId && (
						<div
							style={{
								borderTop: "1px solid #ECEEF1",
								paddingTop: "10px",
								display: "flex",
								flexDirection: "column",
								gap: "8px",
							}}
						>
							<div
								style={{
									fontSize: "11px",
									fontWeight: 600,
									textTransform: "uppercase",
									letterSpacing: "0.06em",
									color: "#6B7280",
								}}
							>
								Preview
							</div>
							<pre
								data-testid="inspector-artifact-preview"
								style={{
									margin: 0,
									padding: "10px",
									background: "#F6F7F8",
									border: "1px solid #E3E5E8",
									borderRadius: "6px",
									fontFamily: EXPLORER_FONTS.mono,
									fontSize: "11px",
									lineHeight: 1.5,
									overflow: "auto",
									maxHeight: "200px",
								}}
							>
								{preview}
							</pre>
							<button
								type="button"
								data-testid="btn-inspector-open-full-view"
								onClick={() => onOpenFullArtifact(selectedArtifactId)}
								style={{
									...BTN_STYLE,
									padding: "8px 12px",
									borderRadius: "6px",
									background: "#16181D",
									color: "#FFFFFF",
									fontSize: "12.5px",
									fontWeight: 500,
								}}
							>
								Open full view ({vizName}) →
							</button>
						</div>
					)}
				</div>
			)}
		</aside>
	);
}
