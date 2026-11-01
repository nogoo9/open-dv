/**
 * Captain-Hold Decision Modal (`CaptainHoldDecisionModal.tsx`)
 * Resolves Tier-U3 ambiguous extraction holds via submitDecision (POST /api/v1/runs/{id}/decisions) (`docs/06` §3.5, `docs/10` §4).
 */

import type {
	CaptainHoldDecisionRequest,
	CaptainHoldDecisionResponse,
} from "@opendv/protocol-ts";
import {
	type FormEvent,
	useCallback,
	useEffect,
	useRef,
	useState,
} from "react";
import { useWorkbenchStore } from "../../store/workbench-store";

export interface CandidateItem {
	candidate_id: string;
	score: number;
	name?: string;
}

export interface CaptainHoldData {
	hold_id: string;
	mention_text: string;
	span_start: number;
	span_end: number;
	candidates: CandidateItem[];
}

export interface CaptainHoldDecisionModalProps {
	isOpen: boolean;
	onClose: () => void;
	holdData?: CaptainHoldData | null | undefined;
	eventId?: string | undefined;
	runId?: string | undefined;
	onSubmit?:
		| ((
				request: CaptainHoldDecisionRequest,
		  ) => Promise<CaptainHoldDecisionResponse | undefined> | undefined)
		| undefined;
}

export const DEFAULT_CAPTAIN_HOLD_DATA: CaptainHoldData = {
	hold_id: "hold-tier-u3-horizon-sovereign-vehicle",
	mention_text: "Horizon Sovereign Co-Invest Vehicle",
	span_start: 1112,
	span_end: 1147,
	candidates: [
		{
			candidate_id: "APEX_CO_I",
			score: 0.81,
			name: "Horizon Co-Investment Fund I, L.P.",
		},
		{
			candidate_id: "APEX_BO_IV",
			score: 0.79,
			name: "Apex Horizon Buyout Fund IV, L.P.",
		},
	],
};

export function CaptainHoldDecisionModal({
	isOpen,
	onClose,
	holdData = DEFAULT_CAPTAIN_HOLD_DATA,
	eventId = "evt-pe-004",
	runId,
	onSubmit,
}: CaptainHoldDecisionModalProps) {
	const hold = holdData ?? DEFAULT_CAPTAIN_HOLD_DATA;
	const defaultCandidateId = hold.candidates[0]?.candidate_id ?? "APEX_CO_I";
	const [action, setAction] = useState<"APPROVE" | "REJECT" | "ANSWER">(
		"APPROVE",
	);
	const [selectedCandidate, setSelectedCandidate] =
		useState<string>(defaultCandidateId);
	const [notes, setNotes] = useState<string>(
		"Confirmed as Horizon Co-Investment Fund I based on Side Letter Section 4 co-investment rights.",
	);
	const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
	const [submissionResult, setSubmissionResult] = useState<string | null>(null);
	const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

	const clearCloseTimer = useCallback(() => {
		if (closeTimerRef.current !== null) {
			clearTimeout(closeTimerRef.current);
			closeTimerRef.current = null;
		}
	}, []);

	useEffect(() => {
		clearCloseTimer();
		if (isOpen && hold.hold_id) {
			setAction("APPROVE");
			setSelectedCandidate(defaultCandidateId);
			setNotes(
				"Confirmed as Horizon Co-Investment Fund I based on Side Letter Section 4 co-investment rights.",
			);
			setIsSubmitting(false);
			setSubmissionResult(null);
		}
		return () => {
			clearCloseTimer();
		};
	}, [hold.hold_id, isOpen, defaultCandidateId, clearCloseTimer]);

	useEffect(() => {
		if (
			!isOpen ||
			typeof globalThis === "undefined" ||
			typeof globalThis.addEventListener !== "function"
		) {
			return;
		}
		const onKeyDown = (ev: KeyboardEvent) => {
			if (ev.key === "Escape" && !isSubmitting) {
				ev.preventDefault();
				clearCloseTimer();
				onClose();
			}
		};
		globalThis.addEventListener("keydown", onKeyDown);
		return () => {
			globalThis.removeEventListener("keydown", onKeyDown);
		};
	}, [isOpen, isSubmitting, onClose, clearCloseTimer]);

	const storeSubmit = useWorkbenchStore((s) => s.submitDecision);

	if (!isOpen) return null;

	const activeCandidateId = hold.candidates.some(
		(c) => c.candidate_id === selectedCandidate,
	)
		? selectedCandidate
		: defaultCandidateId;

	const handleSubmit = async (e: FormEvent) => {
		e.preventDefault();
		clearCloseTimer();
		setIsSubmitting(true);
		setSubmissionResult(null);

		const request: CaptainHoldDecisionRequest = {
			event_id: eventId,
			hold_id: hold.hold_id,
			decision_type: "RESOLVE_CAPTAIN_HOLD",
			action,
			selected_candidate_id: action === "APPROVE" ? activeCandidateId : null,
			resolution_notes: notes,
		};

		try {
			if (onSubmit) {
				await onSubmit(request);
			} else {
				await storeSubmit(request, runId);
			}
			setSubmissionResult("Decision submitted successfully.");
			closeTimerRef.current = setTimeout(() => {
				closeTimerRef.current = null;
				onClose();
			}, 300);
		} catch (err) {
			setSubmissionResult(
				err instanceof Error ? err.message : "Error submitting decision",
			);
		} finally {
			setIsSubmitting(false);
		}
	};

	return (
		<dialog
			open
			aria-labelledby="hold-modal-title"
			data-testid="captain-hold-decision-modal"
			onCancel={(e) => {
				e.preventDefault();
				if (!isSubmitting) {
					clearCloseTimer();
					onClose();
				}
			}}
			onKeyDown={(e) => {
				if (e.key === "Escape" && !isSubmitting) {
					e.preventDefault();
					e.stopPropagation();
					clearCloseTimer();
					onClose();
				}
			}}
			style={{
				position: "fixed",
				inset: 0,
				zIndex: 9999,
				backgroundColor: "rgba(0, 0, 0, 0.75)",
				display: "flex",
				alignItems: "center",
				justifyContent: "center",
				padding: "1rem",
				border: "none",
				width: "100%",
				height: "100%",
				maxWidth: "100vw",
				maxHeight: "100vh",
			}}
		>
			<div
				style={{
					backgroundColor: "var(--dv-surface-1)",
					border: "1.5px solid var(--dv-gate-askuser)",
					borderRadius: "0.5rem",
					width: "100%",
					maxWidth: "540px",
					padding: "1.25rem",
					color: "var(--dv-text-primary)",
					boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.5)",
				}}
			>
				{/* Modal Header */}
				<div
					style={{
						display: "flex",
						justifyContent: "space-between",
						alignItems: "center",
						marginBottom: "1rem",
						borderBottom: "1px solid var(--dv-border-subtle)",
						paddingBottom: "0.5rem",
					}}
				>
					<div>
						<div
							style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}
						>
							<span
								style={{
									fontSize: "0.75rem",
									fontWeight: 700,
									color: "var(--dv-gate-askuser)",
								}}
							>
								▲ [CAPTAIN HOLD]
							</span>
							<h3
								id="hold-modal-title"
								style={{ margin: 0, fontSize: "1rem", fontWeight: 700 }}
							>
								Resolve Tier-U3 Ambiguous Extraction Hold
							</h3>
						</div>
						<span
							style={{ fontSize: "0.75rem", color: "var(--dv-text-secondary)" }}
						>
							Zero-Hallucination Safe Review Gate (docs/06 §3.5 &amp; docs/07
							Scene 2)
						</span>
					</div>

					<button
						type="button"
						disabled={isSubmitting}
						onClick={() => {
							clearCloseTimer();
							onClose();
						}}
						data-testid="btn-close-hold-modal"
						style={{
							backgroundColor: "transparent",
							border: "none",
							color: "var(--dv-text-secondary)",
							fontSize: "1.25rem",
							cursor: isSubmitting ? "not-allowed" : "pointer",
							lineHeight: 1,
						}}
					>
						✕
					</button>
				</div>

				{/* Hold Context Details */}
				<div
					style={{
						backgroundColor: "var(--dv-surface-2)",
						border: "1px solid var(--dv-border-strong)",
						borderRadius: "0.375rem",
						padding: "0.75rem",
						marginBottom: "1rem",
						fontSize: "0.75rem",
					}}
				>
					<div style={{ marginBottom: "0.25rem" }}>
						<strong>Hold ID: </strong>
						<code style={{ color: "var(--dv-gate-askuser)" }}>
							{hold.hold_id}
						</code>
					</div>
					<div style={{ marginBottom: "0.25rem" }}>
						<strong>Mention Text: </strong>"{hold.mention_text}"
					</div>
					<div style={{ color: "var(--dv-text-secondary)" }}>
						<strong>Span Offsets: </strong>[{hold.span_start}:{hold.span_end}]
						in <code>calpers_side_letter_fund_iv_2026.pdf</code>
					</div>
				</div>

				<form onSubmit={handleSubmit}>
					{/* Decision Action Selection */}
					<div style={{ marginBottom: "1rem" }}>
						<div
							style={{
								display: "block",
								fontSize: "0.75rem",
								fontWeight: 700,
								marginBottom: "0.4rem",
							}}
						>
							Decision Action:
						</div>
						<div style={{ display: "flex", gap: "0.5rem" }}>
							<label
								data-testid="decision-action-approve"
								style={{
									display: "flex",
									alignItems: "center",
									gap: "0.35rem",
									fontSize: "0.75rem",
									cursor: "pointer",
									backgroundColor:
										action === "APPROVE"
											? "var(--dv-surface-2)"
											: "transparent",
									padding: "0.3rem 0.6rem",
									borderRadius: "0.25rem",
									border: "1px solid var(--dv-border-strong)",
								}}
							>
								<input
									type="radio"
									name="hold-action"
									value="APPROVE"
									checked={action === "APPROVE"}
									onChange={() => setAction("APPROVE")}
								/>
								<strong>APPROVE (Alias to Candidate)</strong>
							</label>

							<label
								data-testid="decision-action-reject"
								style={{
									display: "flex",
									alignItems: "center",
									gap: "0.35rem",
									fontSize: "0.75rem",
									cursor: "pointer",
									backgroundColor:
										action === "REJECT" ? "var(--dv-surface-2)" : "transparent",
									padding: "0.3rem 0.6rem",
									borderRadius: "0.25rem",
									border: "1px solid var(--dv-border-strong)",
								}}
							>
								<input
									type="radio"
									name="hold-action"
									value="REJECT"
									checked={action === "REJECT"}
									onChange={() => setAction("REJECT")}
								/>
								<strong>REJECT (Discard Span)</strong>
							</label>
						</div>
					</div>

					{/* Candidate Selection (if APPROVE) */}
					{action === "APPROVE" && (
						<div style={{ marginBottom: "1rem" }}>
							<div
								style={{
									display: "block",
									fontSize: "0.75rem",
									fontWeight: 700,
									marginBottom: "0.4rem",
								}}
							>
								Select Target Entity Candidate:
							</div>
							<div
								style={{
									display: "flex",
									flexDirection: "column",
									gap: "0.4rem",
								}}
							>
								{hold.candidates.map((cand) => (
									<label
										key={cand.candidate_id}
										data-testid={`candidate-option-${cand.candidate_id}`}
										style={{
											display: "flex",
											justifyContent: "space-between",
											alignItems: "center",
											padding: "0.4rem 0.6rem",
											backgroundColor:
												activeCandidateId === cand.candidate_id
													? "var(--dv-surface-2)"
													: "transparent",
											border:
												activeCandidateId === cand.candidate_id
													? "1.5px solid var(--dv-hub-accent)"
													: "1px solid var(--dv-border-subtle)",
											borderRadius: "0.25rem",
											cursor: "pointer",
											fontSize: "0.75rem",
										}}
									>
										<div
											style={{
												display: "flex",
												alignItems: "center",
												gap: "0.5rem",
											}}
										>
											<input
												type="radio"
												name="hold-candidate"
												value={cand.candidate_id}
												checked={activeCandidateId === cand.candidate_id}
												onChange={() => setSelectedCandidate(cand.candidate_id)}
											/>
											<div>
												<strong>{cand.candidate_id}</strong>
												{cand.name && (
													<span
														style={{
															color: "var(--dv-text-secondary)",
															marginLeft: "0.5rem",
														}}
													>
														({cand.name})
													</span>
												)}
											</div>
										</div>
										<span
											style={{
												fontFamily: "ui-monospace, monospace",
												color: "var(--dv-hub-accent)",
												fontWeight: 700,
											}}
										>
											Confidence: {(cand.score * 100).toFixed(0)}%
										</span>
									</label>
								))}
							</div>
						</div>
					)}

					{/* Resolution Notes */}
					<div style={{ marginBottom: "1rem" }}>
						<label
							htmlFor="resolution-notes"
							style={{
								display: "block",
								fontSize: "0.75rem",
								fontWeight: 700,
								marginBottom: "0.4rem",
							}}
						>
							Reviewer Rationale &amp; Audit Trail Notes:
						</label>
						<textarea
							id="resolution-notes"
							data-testid="textarea-resolution-notes"
							rows={3}
							value={notes}
							onChange={(e) => setNotes(e.target.value)}
							style={{
								width: "100%",
								backgroundColor: "var(--dv-surface-2)",
								color: "var(--dv-text-primary)",
								border: "1px solid var(--dv-border-strong)",
								borderRadius: "0.25rem",
								padding: "0.5rem",
								fontSize: "0.75rem",
								fontFamily: "inherit",
								boxSizing: "border-box",
							}}
						/>
					</div>

					{submissionResult && (
						<div
							style={{
								marginBottom: "0.75rem",
								fontSize: "0.75rem",
								color: submissionResult.includes("Error")
									? "var(--dv-gate-reject)"
									: "var(--dv-gate-pass)",
							}}
						>
							{submissionResult}
						</div>
					)}

					{/* Modal Footer / Buttons */}
					<div
						style={{
							display: "flex",
							justifyContent: "flex-end",
							gap: "0.5rem",
							borderTop: "1px solid var(--dv-border-subtle)",
							paddingTop: "0.75rem",
						}}
					>
						<button
							type="button"
							onClick={() => {
								clearCloseTimer();
								onClose();
							}}
							disabled={isSubmitting}
							style={{
								backgroundColor: "var(--dv-surface-2)",
								color: "var(--dv-text-primary)",
								border: "1px solid var(--dv-border-strong)",
								borderRadius: "0.25rem",
								padding: "0.4rem 0.75rem",
								fontSize: "0.75rem",
								cursor: isSubmitting ? "not-allowed" : "pointer",
							}}
						>
							Cancel
						</button>

						<button
							type="submit"
							disabled={isSubmitting}
							data-testid="btn-submit-decision"
							style={{
								backgroundColor: "var(--dv-gate-askuser)",
								color: "#000",
								border: "none",
								borderRadius: "0.25rem",
								padding: "0.4rem 0.85rem",
								fontSize: "0.75rem",
								fontWeight: 700,
								cursor: isSubmitting ? "not-allowed" : "pointer",
								opacity: isSubmitting ? 0.7 : 1,
							}}
						>
							{isSubmitting ? "Submitting..." : "Submit Captain Decision ✓"}
						</button>
					</div>
				</form>
			</div>
		</dialog>
	);
}
