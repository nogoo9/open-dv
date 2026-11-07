/**
 * Interactive Steward Hold Modal (`StewardHoldModal.tsx`)
 * Resolves active pipeline holds (ASK-USER / HOLD) with Action (APPROVE / REJECT / ANSWER),
 * Candidate selection, Steward justification, and interactive Ontology Delta checklist (Phase P7 ONT-04 / IP-6).
 * Surfaces RFC 9457 ProblemDetails errors visibly on failure.
 */

import type {
	CaptainHoldDecisionRequest,
	CaptainHoldDecisionResponse,
	HoldResolutionParams,
} from "@opendv/protocol-ts";
import { type CSSProperties, useEffect, useId, useState } from "react";
import { EXPLORER_COLORS, EXPLORER_FONTS } from "./design-tokens";

export interface ProblemDetails {
	type?: string | undefined;
	title?: string | undefined;
	status?: number | undefined;
	detail?: string | undefined;
	instance?: string | undefined;
	[key: string]: unknown;
}

export interface StewardHoldCandidate {
	id: string;
	label: string;
	score?: number | undefined;
}

export interface StewardHoldOntologyOp {
	op_id: string;
	op_type: string;
	concept_name?: string | undefined;
	relationship_name?: string | undefined;
	concept_uri?: string | undefined;
	relationship_uri?: string | undefined;
	confidence?: number | undefined;
	rationale?: string | undefined;
}

export interface StewardHoldModalProps {
	isOpen: boolean;
	onClose: () => void;
	holdId: string;
	runId?: string | undefined;
	stageKey?: string | undefined;
	eventId?: string | undefined;
	headline?: string | undefined;
	description?: string | undefined;
	candidates?: readonly StewardHoldCandidate[] | undefined;
	initialCandidateId?: string | undefined;
	ontologyOps?: readonly StewardHoldOntologyOp[] | undefined;
	delta?: Record<string, unknown> | undefined;
	deltaSha256?: string | undefined;
	onSubmit?:
		| ((
				request: CaptainHoldDecisionRequest,
		  ) => Promise<CaptainHoldDecisionResponse>)
		| undefined;
	onSuccess?: ((response: CaptainHoldDecisionResponse) => void) | undefined;
	onResolveHold?: ((params: HoldResolutionParams) => void) | undefined;
}

export function parseProblemDetails(err: unknown): ProblemDetails {
	if (typeof err === "object" && err !== null) {
		const obj = err as Record<string, unknown>;
		if (obj.problemDetails && typeof obj.problemDetails === "object") {
			return obj.problemDetails as ProblemDetails;
		}
		if (typeof obj.title === "string" || typeof obj.detail === "string") {
			return {
				title:
					typeof obj.title === "string" ? obj.title : "Hold Resolution Failed",
				detail:
					typeof obj.detail === "string"
						? obj.detail
						: String(obj.message ?? "An error occurred"),
				status: typeof obj.status === "number" ? obj.status : 400,
				type: typeof obj.type === "string" ? obj.type : undefined,
			};
		}
		if (typeof obj.message === "string") {
			try {
				const parsed = JSON.parse(obj.message);
				if (typeof parsed === "object" && parsed !== null) {
					const p = parsed as Record<string, unknown>;
					return {
						title:
							typeof p.title === "string" ? p.title : "Hold Resolution Failed",
						detail:
							typeof p.detail === "string"
								? p.detail
								: typeof p.message === "string"
									? p.message
									: obj.message,
						status: typeof p.status === "number" ? p.status : 400,
						type: typeof p.type === "string" ? p.type : undefined,
					};
				}
			} catch {
				// not JSON string
			}
			return {
				title: "Hold Resolution Error",
				detail: obj.message,
				status: typeof obj.status === "number" ? obj.status : 500,
			};
		}
	}
	return { title: "Hold Resolution Error", detail: String(err), status: 500 };
}

const LBL: CSSProperties = {
	fontSize: "11px",
	fontWeight: 600,
	color: EXPLORER_COLORS.inkLabel,
	textTransform: "uppercase",
};
const INP: CSSProperties = {
	fontFamily: EXPLORER_FONTS.sans,
	fontSize: "12px",
	padding: "8px 10px",
	borderRadius: "6px",
	border: `1px solid ${EXPLORER_COLORS.borderLine}`,
	color: EXPLORER_COLORS.inkPrimary,
};

export function StewardHoldModal({
	isOpen,
	onClose,
	holdId,
	runId = "current-run",
	stageKey = "STAGE_02_SCOUT_UNSTRUCTURED",
	eventId = "evt-hold-001",
	headline,
	description,
	candidates,
	initialCandidateId,
	ontologyOps,
	delta,
	deltaSha256,
	onSubmit,
	onSuccess,
	onResolveHold,
}: StewardHoldModalProps) {
	const titleId = useId();
	const [action, setAction] = useState<"APPROVE" | "REJECT" | "ANSWER">(
		"APPROVE",
	);
	const [selectedCandidate, setSelectedCandidate] = useState<string>(
		initialCandidateId ?? candidates?.[0]?.id ?? "",
	);
	const [rationale, setRationale] = useState<string>("");
	const [selectedOpIds, setSelectedOpIds] = useState<Set<string>>(
		() => new Set(ontologyOps?.map((o) => o.op_id) ?? []),
	);
	const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
	const [problemError, setProblemError] = useState<ProblemDetails | null>(null);
	const [successMsg, setSuccessMsg] = useState<string | null>(null);

	useEffect(() => {
		if (isOpen) {
			setSelectedCandidate(initialCandidateId ?? candidates?.[0]?.id ?? "");
			setSelectedOpIds(new Set(ontologyOps?.map((o) => o.op_id) ?? []));
			setProblemError(null);
			setSuccessMsg(null);
			setIsSubmitting(false);
		}
	}, [isOpen, initialCandidateId, candidates, ontologyOps]);

	useEffect(() => {
		if (!isOpen) return;
		const onKeyDown = (ev: KeyboardEvent) => {
			if (ev.key === "Escape" && !isSubmitting) onClose();
		};
		globalThis.addEventListener("keydown", onKeyDown);
		return () => globalThis.removeEventListener("keydown", onKeyDown);
	}, [isOpen, isSubmitting, onClose]);

	if (!isOpen) return null;

	const handleToggleOp = (opId: string) => {
		setSelectedOpIds((prev) => {
			const next = new Set(prev);
			if (next.has(opId)) next.delete(opId);
			else next.add(opId);
			return next;
		});
	};

	const handleSubmit = async (ev: React.FormEvent) => {
		ev.preventDefault();
		if (isSubmitting) return;
		setIsSubmitting(true);
		setProblemError(null);

		const request: CaptainHoldDecisionRequest = {
			decision_type: "RESOLVE_CAPTAIN_HOLD",
			action,
			decision: action,
			hold_id: holdId,
			event_id: eventId,
			steward_id: "steward-captain",
			selected_candidate_id: selectedCandidate || undefined,
			resolution_notes:
				rationale ||
				(action === "APPROVE"
					? `Approved hold ${holdId}`
					: `Rejected hold ${holdId}`),
			steward_rationale: rationale || undefined,
		};

		if (ontologyOps && ontologyOps.length > 0) {
			(request as unknown as Record<string, unknown>).hold_option =
				action === "APPROVE"
					? {
							kind: "accept_ontology_delta",
							accepted_op_ids: Array.from(selectedOpIds),
							delta_sha256: deltaSha256 ?? "sha256-delta",
							steward_note: rationale || "Accepted ontology delta operations",
							delta: delta ?? {
								delta_id: `delta-${holdId}`,
								operations: ontologyOps.filter((o) =>
									selectedOpIds.has(o.op_id),
								),
							},
						}
					: {
							kind: "reject_ontology_delta",
							delta_sha256: deltaSha256 ?? "sha256-delta",
							rejection_reason: rationale || "Rejected by steward",
						};
		}

		try {
			const response: CaptainHoldDecisionResponse = onSubmit
				? await onSubmit(request)
				: {
						decision_id: `dec-${holdId}-${Date.now()}`,
						status: action === "REJECT" ? "REJECTED" : "ACCEPTED",
						resumed_run_id: runId,
						next_stage_id: "STAGE_03_SHIP_VAULT_PASS1",
						next_event_id: `evt-resumed-${Date.now()}`,
						action,
						hold_id: holdId,
					};

			setSuccessMsg("Hold resolved. Resuming pipeline from STAGE_03.");
			onSuccess?.(response);
			onResolveHold?.({
				holdId,
				action: action === "REJECT" ? "REJECT" : "APPROVE",
				selectedCandidateId: selectedCandidate || undefined,
				resolutionNotes: rationale || undefined,
				eventId,
			});
		} catch (err) {
			setProblemError(parseProblemDetails(err));
		} finally {
			setIsSubmitting(false);
		}
	};

	return (
		<div
			data-testid="steward-hold-modal-overlay"
			style={{
				position: "fixed",
				inset: 0,
				backgroundColor: "rgba(22, 24, 29, 0.4)",
				backdropFilter: "blur(2px)",
				display: "flex",
				alignItems: "center",
				justifyContent: "center",
				zIndex: 9999,
				padding: "16px",
			}}
		>
			<dialog
				open
				aria-modal="true"
				aria-labelledby={titleId}
				data-testid="steward-hold-modal"
				style={{
					backgroundColor: EXPLORER_COLORS.bgSurface,
					border: `1px solid ${EXPLORER_COLORS.borderLine}`,
					borderRadius: "8px",
					width: "100%",
					maxWidth: "540px",
					maxHeight: "90vh",
					overflowY: "auto",
					display: "flex",
					flexDirection: "column",
					fontFamily: EXPLORER_FONTS.sans,
					color: EXPLORER_COLORS.inkPrimary,
				}}
			>
				{/* Modal Header */}
				<div
					style={{
						padding: "16px 20px",
						borderBottom: `1px solid ${EXPLORER_COLORS.borderLine}`,
						display: "flex",
						alignItems: "center",
						justifyContent: "space-between",
					}}
				>
					<div>
						<h2
							id={titleId}
							data-testid="hold-modal-title"
							style={{ margin: 0, fontSize: "15px", fontWeight: 600 }}
						>
							{headline ?? "Resolve Steward Hold"}
						</h2>
						<div
							style={{
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "11px",
								color: "#8A3B07",
								marginTop: "2px",
							}}
						>
							{stageKey} · Hold ID: {holdId}
						</div>
					</div>
					<button
						type="button"
						data-testid="btn-close-hold-modal"
						aria-label="Close dialog"
						onClick={onClose}
						disabled={isSubmitting}
						style={{
							all: "unset",
							cursor: isSubmitting ? "not-allowed" : "pointer",
							fontSize: "13px",
							color: EXPLORER_COLORS.inkLabel,
							padding: "4px 8px",
						}}
					>
						✕
					</button>
				</div>

				{/* Modal Form */}
				<form
					onSubmit={handleSubmit}
					style={{
						padding: "20px",
						display: "flex",
						flexDirection: "column",
						gap: "14px",
					}}
				>
					{description && (
						<div
							data-testid="hold-modal-description"
							style={{ fontSize: "12.5px", color: EXPLORER_COLORS.inkBody }}
						>
							{description}
						</div>
					)}

					{problemError && (
						<div
							data-testid="problem-details-error"
							role="alert"
							style={{
								padding: "10px",
								borderRadius: "6px",
								background: "#FDECEA",
								border: "1px solid #F5C2C7",
								color: "#8F1D12",
								fontSize: "12px",
								display: "flex",
								flexDirection: "column",
								gap: "2px",
							}}
						>
							<div
								style={{
									display: "flex",
									justifyContent: "space-between",
									alignItems: "center",
								}}
							>
								<strong data-testid="problem-title">
									{problemError.title ?? "Error"}
								</strong>
								{problemError.status && (
									<span
										data-testid="problem-status"
										style={{
											fontFamily: EXPLORER_FONTS.mono,
											fontSize: "10px",
											padding: "1px 6px",
											borderRadius: "4px",
											background: "#8F1D12",
											color: "#FFFFFF",
										}}
									>
										HTTP {problemError.status}
									</span>
								)}
							</div>
							<div data-testid="problem-detail">
								{problemError.detail ?? "Submission failed."}
							</div>
							{problemError.type && (
								<div
									data-testid="problem-type"
									style={{
										fontFamily: EXPLORER_FONTS.mono,
										fontSize: "10px",
										color: "#6B7280",
									}}
								>
									Type: {problemError.type}
								</div>
							)}
						</div>
					)}

					{successMsg && (
						<output
							data-testid="hold-resolution-success"
							style={{
								padding: "10px",
								borderRadius: "6px",
								background: "#EEF0F2",
								color: "#15803D",
								fontSize: "12.5px",
								fontWeight: 500,
								display: "block",
							}}
						>
							✓ {successMsg}
						</output>
					)}

					{/* Action Buttons */}
					<div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
						<span style={LBL}>Steward Action</span>
						<div style={{ display: "flex", gap: "8px" }}>
							{(["APPROVE", "REJECT", "ANSWER"] as const).map((act) => (
								<button
									key={act}
									type="button"
									data-testid={`hold-action-${act.toLowerCase()}`}
									onClick={() => setAction(act)}
									disabled={isSubmitting}
									style={{
										flex: 1,
										padding: "8px",
										borderRadius: "6px",
										fontSize: "12px",
										fontWeight: 600,
										cursor: isSubmitting ? "not-allowed" : "pointer",
										border:
											action === act
												? "1px solid #16181D"
												: `1px solid ${EXPLORER_COLORS.borderLine}`,
										background:
											action === act ? "#16181D" : EXPLORER_COLORS.bgPage,
										color:
											action === act ? "#FFFFFF" : EXPLORER_COLORS.inkPrimary,
									}}
								>
									{act}
								</button>
							))}
						</div>
					</div>

					{/* Candidate Selection Dropdown */}
					{candidates && candidates.length > 0 && action === "APPROVE" && (
						<div
							style={{ display: "flex", flexDirection: "column", gap: "4px" }}
						>
							<label htmlFor="hold-candidate-select" style={LBL}>
								Candidate Entity
							</label>
							<select
								id="hold-candidate-select"
								data-testid="select-hold-candidate"
								value={selectedCandidate}
								onChange={(e) => setSelectedCandidate(e.target.value)}
								disabled={isSubmitting}
								style={{ ...INP, fontFamily: EXPLORER_FONTS.mono }}
							>
								{candidates.map((c) => (
									<option key={c.id} value={c.id}>
										{c.label} ({c.id})
									</option>
								))}
							</select>
						</div>
					)}

					{/* Ontology Delta Checklist */}
					{ontologyOps && ontologyOps.length > 0 && (
						<div
							data-testid="ontology-delta-checklist"
							style={{
								display: "flex",
								flexDirection: "column",
								gap: "6px",
								padding: "10px",
								borderRadius: "6px",
								background: EXPLORER_COLORS.bgPage,
								border: `1px solid ${EXPLORER_COLORS.borderLine}`,
							}}
						>
							<div
								style={{
									display: "flex",
									justifyContent: "space-between",
									alignItems: "center",
								}}
							>
								<span style={LBL}>
									Ontology Operations ({selectedOpIds.size}/{ontologyOps.length}
									)
								</span>
								<div style={{ display: "flex", gap: "6px" }}>
									<button
										type="button"
										data-testid="btn-select-all-ops"
										onClick={() =>
											setSelectedOpIds(new Set(ontologyOps.map((o) => o.op_id)))
										}
										disabled={isSubmitting}
										style={{
											all: "unset",
											cursor: "pointer",
											fontSize: "11px",
											color: "#1D4ED8",
										}}
									>
										Select all
									</button>
									<span style={{ color: EXPLORER_COLORS.borderLine }}>|</span>
									<button
										type="button"
										data-testid="btn-deselect-all-ops"
										onClick={() => setSelectedOpIds(new Set())}
										disabled={isSubmitting}
										style={{
											all: "unset",
											cursor: "pointer",
											fontSize: "11px",
											color: EXPLORER_COLORS.inkLabel,
										}}
									>
										Deselect all
									</button>
								</div>
							</div>
							<div
								style={{
									display: "flex",
									flexDirection: "column",
									gap: "4px",
									maxHeight: "160px",
									overflowY: "auto",
								}}
							>
								{ontologyOps.map((op) => (
									<label
										key={op.op_id}
										data-testid={`op-checkbox-label-${op.op_id}`}
										style={{
											display: "flex",
											alignItems: "center",
											gap: "6px",
											padding: "4px",
											fontSize: "11.5px",
											cursor: isSubmitting ? "not-allowed" : "pointer",
										}}
									>
										<input
											type="checkbox"
											data-testid={`checkbox-op-${op.op_id}`}
											checked={selectedOpIds.has(op.op_id)}
											onChange={() => handleToggleOp(op.op_id)}
											disabled={isSubmitting || action === "REJECT"}
										/>
										<span
											style={{
												fontFamily: EXPLORER_FONTS.mono,
												fontWeight: 600,
											}}
										>
											{op.op_id}
										</span>
										<span
											style={{
												fontSize: "10px",
												padding: "1px 5px",
												borderRadius: "3px",
												background: "#EEF2FF",
												color: "#1D4ED8",
											}}
										>
											{op.op_type.replace(/_/g, " ")}
										</span>
										<span style={{ color: EXPLORER_COLORS.inkBody }}>
											{op.concept_name ??
												op.relationship_name ??
												op.concept_uri ??
												op.relationship_uri}
										</span>
									</label>
								))}
							</div>
						</div>
					)}

					{/* Justification Notes */}
					<div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
						<label htmlFor="steward-rationale" style={LBL}>
							Steward Justification Notes
						</label>
						<textarea
							id="steward-rationale"
							data-testid="input-steward-rationale"
							rows={2}
							value={rationale}
							onChange={(e) => setRationale(e.target.value)}
							placeholder="Enter steward decision rationale..."
							disabled={isSubmitting}
							style={INP}
						/>
					</div>

					{/* Modal Actions */}
					<div
						style={{
							display: "flex",
							justifyContent: "flex-end",
							gap: "8px",
							marginTop: "4px",
						}}
					>
						<button
							type="button"
							data-testid="btn-cancel-hold"
							onClick={onClose}
							disabled={isSubmitting}
							style={{
								padding: "8px 14px",
								borderRadius: "6px",
								border: `1px solid ${EXPLORER_COLORS.borderLine}`,
								background: EXPLORER_COLORS.bgPage,
								fontSize: "12px",
								cursor: isSubmitting ? "not-allowed" : "pointer",
							}}
						>
							Cancel
						</button>
						<button
							type="submit"
							data-testid="btn-submit-decision"
							disabled={isSubmitting}
							style={{
								padding: "8px 16px",
								borderRadius: "6px",
								border: "1px solid #16181D",
								background: action === "REJECT" ? "#8F1D12" : "#16181D",
								color: "#FFFFFF",
								fontSize: "12px",
								fontWeight: 600,
								cursor: isSubmitting ? "not-allowed" : "pointer",
							}}
						>
							{isSubmitting ? "Submitting..." : `Submit ${action}`}
						</button>
					</div>
				</form>
			</dialog>
		</div>
	);
}
