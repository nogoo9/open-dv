/**
 * Interactive Steward Hold Modal (`StewardHoldModal.tsx`)
 * Resolves active pipeline holds (ASK-USER / HOLD) with Action (APPROVE / REJECT / ANSWER),
 * Candidate selection, Steward justification, and interactive Ontology Delta checklist (Phase P7 ONT-04 / IP-6).
 * Surfaces RFC 9457 ProblemDetails errors visibly on failure.
 * User Directive R9 compliant (<= 450 lines).
 */

import type {
	CaptainHoldDecisionRequest,
	CaptainHoldDecisionResponse,
	HoldResolutionParams,
} from "@opendv/protocol-ts";
import { type CSSProperties, useEffect, useId, useState } from "react";
import { EXPLORER_COLORS, EXPLORER_FONTS } from "../design-tokens";
import {
	type ProblemDetails,
	StewardHoldErrorAlert,
	parseProblemDetails,
} from "./StewardHoldErrorAlert";
import {
	StewardDecisionFields,
	type StewardHoldCandidate,
} from "./StewardDecisionFields";
import { StewardHoldOntologyChecklist } from "./StewardHoldOntologyChecklist";

export type { ProblemDetails, StewardHoldCandidate };
export { parseProblemDetails, StewardHoldErrorAlert };

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


export function StewardHoldModal({
	isOpen,
	onClose,
	holdId,
	runId = "current-run",
	stageKey = "",
	eventId = "",
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

		const effectiveEventId = eventId || `evt-hold-${holdId}-${Date.now()}`;
		const effectiveDeltaSha = deltaSha256 || `sha256-delta-${holdId}`;

		const request: CaptainHoldDecisionRequest = {
			decision_type: "RESOLVE_CAPTAIN_HOLD",
			action,
			decision: action,
			hold_id: holdId,
			event_id: effectiveEventId,
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
							delta_sha256: effectiveDeltaSha,
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
							delta_sha256: effectiveDeltaSha,
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
						next_stage_id: stageKey ? `STAGE_NEXT_AFTER_${stageKey}` : "STAGE_NEXT",
						next_event_id: `evt-resumed-${Date.now()}`,
						action,
						hold_id: holdId,
					};

			setSuccessMsg("Hold resolved. Resuming pipeline.");
			onSuccess?.(response);
			onResolveHold?.({
				holdId,
				action: action === "REJECT" ? "REJECT" : "APPROVE",
				selectedCandidateId: selectedCandidate || undefined,
				resolutionNotes: rationale || undefined,
				eventId: effectiveEventId,
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
							{stageKey ? `${stageKey} · ` : ""}Hold ID: {holdId}
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
						<StewardHoldErrorAlert problemError={problemError} />
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

					<StewardDecisionFields
						action={action}
						setAction={setAction}
						candidates={candidates}
						selectedCandidate={selectedCandidate}
						setSelectedCandidate={setSelectedCandidate}
						rationale={rationale}
						setRationale={setRationale}
						isSubmitting={isSubmitting}
					>
						{ontologyOps && ontologyOps.length > 0 && (
							<StewardHoldOntologyChecklist
								ontologyOps={ontologyOps}
								selectedOpIds={selectedOpIds}
								onToggleOp={handleToggleOp}
								onSelectAll={() =>
									setSelectedOpIds(new Set(ontologyOps.map((o) => o.op_id)))
								}
								onDeselectAll={() => setSelectedOpIds(new Set())}
								isSubmitting={isSubmitting}
								action={action}
							/>
						)}
					</StewardDecisionFields>

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
