/**
 * Inspector Steward Hold Resolution Section (`InspectorHoldSection.tsx`)
 *
 * Interactive steward hold resolution section with action selector, dynamic candidate
 * selection, rationale notes, ontology ops checklist, and modal trigger.
 * Adheres to User Directive R9 and Anti-Hardcoding rules.
 */

import type {
	CaptainHoldDecisionRequest,
	CaptainHoldDecisionResponse,
	HoldResolutionParams,
	Stage,
} from "@opendv/protocol-ts";
import { type CSSProperties, useState } from "react";
import {
	type ProblemDetails,
	type StewardHoldCandidate,
	StewardHoldModal,
	type StewardHoldOntologyOp,
	parseProblemDetails,
} from "../StewardHoldModal";
import { EXPLORER_FONTS } from "../design-tokens";

const BTN_STYLE: CSSProperties = {
	all: "unset",
	cursor: "pointer",
	fontSize: "11px",
	padding: "4px 8px",
	borderRadius: "4px",
	textAlign: "center",
};

export interface InspectorHoldSectionProps {
	activeHoldId: string;
	runId: string;
	currentStage?: Stage | undefined;
	detectedCandidates: readonly StewardHoldCandidate[];
	detectedOps: readonly StewardHoldOntologyOp[];
	onResolveHold?: ((params: HoldResolutionParams) => void) | undefined;
	onSubmitDecision?:
		| ((
				decision: CaptainHoldDecisionRequest,
		  ) => Promise<CaptainHoldDecisionResponse>)
		| undefined;
}

export function InspectorHoldSection({
	activeHoldId,
	runId,
	currentStage,
	detectedCandidates,
	detectedOps,
	onResolveHold,
	onSubmitDecision,
}: InspectorHoldSectionProps) {
	const [action, setAction] = useState<"APPROVE" | "REJECT" | "ANSWER">(
		"APPROVE",
	);
	const [selectedCandidate, setSelectedCandidate] = useState<string>(
		detectedCandidates[0]?.id ?? "",
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
		const candidateId = selectedCandidate || detectedCandidates[0]?.id || "";

		const dynamicEventId = `evt-${activeHoldId}-${Date.now()}`;
		const request: CaptainHoldDecisionRequest = {
			decision_type: "RESOLVE_CAPTAIN_HOLD",
			action: effectiveAction,
			decision: effectiveAction,
			hold_id: activeHoldId,
			event_id: dynamicEventId,
			steward_id: "steward-captain",
			selected_candidate_id: candidateId,
			resolution_notes:
				rationale ||
				(effectiveAction === "APPROVE"
					? `Approved candidate ${candidateId}`
					: `Rejected hold ${activeHoldId}`),
			steward_rationale: rationale || null,
		};

		if (detectedOps.length > 0) {
			(request as unknown as Record<string, unknown>).hold_option = {
				kind:
					effectiveAction === "APPROVE"
						? "accept_ontology_delta"
						: "reject_ontology_delta",
				accepted_op_ids: Array.from(selectedOpIds),
				delta_sha256: "dynamic-delta-applied",
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
				eventId: dynamicEventId,
			});
			const resumeTarget = currentStage?.key ? `Resumes after ${currentStage.key}` : "Resumes pipeline";
			setResolvedMessage(`ASK-USER → resolved · ${resumeTarget}`);
		} catch (err) {
			setProblemError(parseProblemDetails(err));
		} finally {
			setIsSubmitting(false);
		}
	};

	return (
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
				stageKey={currentStage?.key ?? "STAGE_UNKNOWN"}
				candidates={detectedCandidates}
				initialCandidateId={selectedCandidate}
				ontologyOps={detectedOps}
				onSubmit={onSubmitDecision}
				onResolveHold={onResolveHold}
				onSuccess={(resp) => {
					setResolvedMessage(
						`ASK-USER → resolved (${resp.decision_id}) · Resumes next stage`,
					);
					setIsModalOpen(false);
				}}
			/>
		</div>
	);
}
