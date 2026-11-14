/**
 * Shared UI building blocks for Clarification Q&A resolution and Candidate Proposal ratification.
 * Used by both ClarificationPanel (left drawer) and ProposalDetailsView (right drawer)
 * with parameterized `testIdPrefix` to avoid duplicate test IDs while eliminating duplicated logic.
 */

import type React from "react";
import { useCallback, useState } from "react";
import { useStudioStore } from "../store/studio-store";
import type {
	CandidateProposalSummary,
	ClarificationAnswer,
	ClarificationQuestion,
	StudioUserRole,
} from "../store/types";

export function getCertaintyBadgeClass(
	certainty: ClarificationQuestion["certainty"],
): string {
	switch (certainty) {
		case "HIGH":
			return "certainty-high";
		case "MEDIUM":
			return "certainty-medium";
		case "LOW":
			return "certainty-low";
		default:
			return "certainty-default";
	}
}

export function useClarificationDrafts() {
	const clarifications = useStudioStore((s) => s.clarifications);
	const answers = useStudioStore((s) => s.answers);
	const submitAnswer = useStudioStore((s) => s.submitAnswer);
	const setAnswerDraft = useStudioStore((s) => s.setAnswerDraft);

	const [activeDrafts, setActiveDrafts] = useState<
		Record<string, { selectedOptions: string[]; customText: string }>
	>({});
	const [submittingIds, setSubmittingIds] = useState<Record<string, boolean>>(
		{},
	);
	const [submissionErrors, setSubmissionErrors] = useState<
		Record<string, string>
	>({});

	const resolveDraft = useCallback(
		(questionId: string) => {
			const active = activeDrafts[questionId];
			if (active) return active;

			const localAnswer = answers[questionId];
			if (localAnswer) {
				return {
					selectedOptions: localAnswer.selected_option_ids || [],
					customText: localAnswer.custom_answer_text || "",
				};
			}

			const serverQuestion = clarifications.find(
				(c) => c.question_id === questionId,
			);
			return {
				selectedOptions: serverQuestion?.selected_option_ids || [],
				customText: serverQuestion?.custom_answer_text || "",
			};
		},
		[activeDrafts, answers, clarifications],
	);

	const handleOptionToggle = (questionId: string, optionId: string) => {
		const currentDraft = resolveDraft(questionId);
		const exists = currentDraft.selectedOptions.includes(optionId);
		const nextSelected = exists
			? currentDraft.selectedOptions.filter((id) => id !== optionId)
			: [...currentDraft.selectedOptions, optionId];
		const updated = { ...currentDraft, selectedOptions: nextSelected };
		setActiveDrafts((prev) => ({ ...prev, [questionId]: updated }));
		setAnswerDraft(questionId, nextSelected, updated.customText);
	};

	const handleCustomTextChange = (questionId: string, text: string) => {
		const currentDraft = resolveDraft(questionId);
		const updated = { ...currentDraft, customText: text };
		setActiveDrafts((prev) => ({ ...prev, [questionId]: updated }));
		setAnswerDraft(questionId, updated.selectedOptions, text);
	};

	const handleSubmit = async (
		question: ClarificationQuestion,
		onAnswerSubmitted?: (answer: ClarificationAnswer) => void,
	) => {
		const draft = resolveDraft(question.question_id);
		if (draft.selectedOptions.length === 0 && !draft.customText.trim()) {
			return;
		}
		setSubmittingIds((prev) => ({ ...prev, [question.question_id]: true }));
		setSubmissionErrors((prev) => {
			const next = { ...prev };
			delete next[question.question_id];
			return next;
		});
		try {
			const answerPayload: ClarificationAnswer = {
				question_id: question.question_id,
				selected_option_ids: draft.selectedOptions,
				custom_answer_text: draft.customText.trim(),
				answered_at: new Date().toISOString(),
			};
			const ok = await submitAnswer(answerPayload);
			if (ok) {
				setActiveDrafts((prev) => {
					const next = { ...prev };
					delete next[question.question_id];
					return next;
				});
				if (onAnswerSubmitted) {
					onAnswerSubmitted(answerPayload);
				}
			} else {
				setSubmissionErrors((prev) => ({
					...prev,
					[question.question_id]:
						"Submission rejected: conflict or concurrent edit (ONT-EVO-003).",
				}));
			}
		} catch (err) {
			console.error("Clarification answer submission failed:", err);
			setSubmissionErrors((prev) => ({
				...prev,
				[question.question_id]: `Submission failed: ${String(err)}`,
			}));
		} finally {
			setSubmittingIds((prev) => ({ ...prev, [question.question_id]: false }));
		}
	};

	return {
		clarifications,
		resolveDraft,
		submittingIds,
		submissionErrors,
		handleOptionToggle,
		handleCustomTextChange,
		handleSubmit,
	};
}

export interface ClarificationQuestionsListProps {
	testIdPrefix?: string | undefined;
	canHumanRatify: boolean;
	compact?: boolean | undefined;
	onAnswerSubmitted?: ((answer: ClarificationAnswer) => void) | undefined;
}

export const ClarificationQuestionsList: React.FC<
	ClarificationQuestionsListProps
> = ({
	testIdPrefix = "",
	canHumanRatify,
	compact = false,
	onAnswerSubmitted,
}) => {
	const {
		clarifications,
		resolveDraft,
		submittingIds,
		submissionErrors,
		handleOptionToggle,
		handleCustomTextChange,
		handleSubmit,
	} = useClarificationDrafts();

	const tid = (suffix: string) => `${testIdPrefix}${suffix}`;

	return (
		<div
			className={
				compact
					? "clarification-questions-list clarification-questions-compact"
					: "clarification-questions-list"
			}
			data-testid={tid("clarifications-list")}
		>
			{clarifications.length === 0 ? (
				<div
					className="empty-clarifications-box"
					data-testid={tid("empty-clarifications-box")}
				>
					<span className="empty-icon">✓</span>
					<h3 className="empty-title">All Semantic Questions Resolved</h3>
					<p className="empty-desc">
						No pending domain ambiguities detected for the active ontology
						session.
					</p>
				</div>
			) : (
				clarifications.map((q) => {
					const isAnswered = q.status === "ANSWERED";
					const currentDraft = resolveDraft(q.question_id);
					const isSubmitting = Boolean(submittingIds[q.question_id]);

					return (
						<div
							key={q.question_id}
							className={`question-card ${isAnswered ? "question-card-answered" : ""}`}
							data-testid={tid(`question-card-${q.question_id}`)}
						>
							<div className="question-header">
								<div className="concept-info">
									<span className="concept-badge">{q.concept_name}</span>
									<span
										className={`certainty-badge ${getCertaintyBadgeClass(q.certainty)}`}
									>
										{q.certainty} CERTAINTY
									</span>
								</div>
								{isAnswered && (
									<span
										className="answered-status-badge"
										data-testid={tid(`answered-badge-${q.question_id}`)}
									>
										✓ Answered
									</span>
								)}
							</div>

							<div className="question-body">
								<p className="question-text">{q.question_text}</p>
							</div>

							{!compact &&
								q.evidence_citations &&
								q.evidence_citations.length > 0 && (
									<div
										className="citations-box"
										data-testid={tid(`citations-${q.question_id}`)}
									>
										<span className="citations-title">Evidence Citations:</span>
										<div className="citations-list">
											{q.evidence_citations.map((cite, idx) => (
												<div
													key={`${cite.chunk_id}-${idx}`}
													className="citation-item"
												>
													<span className="citation-source">
														{cite.source_file} ({cite.chunk_id}):
													</span>
													<span className="citation-excerpt">
														"{cite.excerpt}"
													</span>
												</div>
											))}
										</div>
									</div>
								)}

							{q.rationale_options && q.rationale_options.length > 0 && (
								<div className="options-section">
									<span className="options-title">
										Suggested Semantic Rationales:
									</span>
									<div className="options-grid">
										{q.rationale_options.map((opt) => {
											const isSelected = currentDraft.selectedOptions.includes(
												opt.id,
											);
											return (
												<button
													key={opt.id}
													type="button"
													className={`option-btn ${isSelected ? "option-btn-selected" : ""}`}
													onClick={() =>
														handleOptionToggle(q.question_id, opt.id)
													}
													disabled={!canHumanRatify || isSubmitting}
													aria-pressed={isSelected}
													data-testid={tid(
														`option-btn-${q.question_id}-${opt.id}`,
													)}
												>
													<span className="option-label">{opt.label}</span>
													{opt.description && !compact && (
														<span className="option-desc">
															{opt.description}
														</span>
													)}
												</button>
											);
										})}
									</div>
								</div>
							)}

							<div className="custom-answer-section">
								<label
									htmlFor={`${testIdPrefix}custom-input-${q.question_id}`}
									className="custom-answer-label"
								>
									Custom Domain Rationale:
								</label>
								<textarea
									id={`${testIdPrefix}custom-input-${q.question_id}`}
									className="custom-answer-textarea"
									rows={2}
									placeholder="Provide additional enterprise domain context or regulatory constraints..."
									value={currentDraft.customText}
									disabled={!canHumanRatify || isSubmitting}
									onChange={(e) =>
										handleCustomTextChange(q.question_id, e.target.value)
									}
									data-testid={tid(`custom-answer-input-${q.question_id}`)}
								/>
							</div>

							{submissionErrors[q.question_id] && (
								<div
									className="error-banner"
									role="alert"
									data-testid={tid(`answer-error-${q.question_id}`)}
								>
									<span>{submissionErrors[q.question_id]}</span>
								</div>
							)}

							<div className="question-actions">
								<button
									type="button"
									className="btn-primary btn-sm submit-answer-btn"
									onClick={() => void handleSubmit(q, onAnswerSubmitted)}
									disabled={
										!canHumanRatify ||
										isSubmitting ||
										(currentDraft.selectedOptions.length === 0 &&
											!currentDraft.customText.trim())
									}
									data-testid={tid(`submit-answer-btn-${q.question_id}`)}
								>
									{isSubmitting
										? "Submitting..."
										: isAnswered
											? "Update Answer"
											: "Submit Answer"}
								</button>
								{q.answered_at && (
									<span className="answered-timestamp">
										Submitted {new Date(q.answered_at).toLocaleTimeString()}
									</span>
								)}
							</div>
						</div>
					);
				})
			)}
		</div>
	);
};

export interface ProposalRatificationControlsProps {
	proposal: CandidateProposalSummary;
	userRole: StudioUserRole;
	testIdPrefix?: string;
	showInspectButton?: boolean;
	acceptLabel?: string;
	rationaleValue: string;
	selfOverrideChecked: boolean;
	lowConfidenceWaiverChecked?: boolean;
	hasUnsavedDraftEdits?: boolean;
	onRationaleChange: (value: string) => void;
	onSelfOverrideChange: (checked: boolean) => void;
	onLowConfidenceWaiverChange?: (checked: boolean) => void;
	onInspect?: () => void;
	onAccept: () => void;
	onRebase: () => void;
	onReject: () => void;
}

export const ProposalRatificationControls: React.FC<
	ProposalRatificationControlsProps
> = ({
	proposal,
	userRole,
	testIdPrefix = "",
	showInspectButton = false,
	acceptLabel = "Accept",
	rationaleValue,
	selfOverrideChecked,
	lowConfidenceWaiverChecked = false,
	hasUnsavedDraftEdits = false,
	onRationaleChange,
	onSelfOverrideChange,
	onLowConfidenceWaiverChange,
	onInspect,
	onAccept,
	onRebase,
	onReject,
}) => {
	const tid = (suffix: string) => `${testIdPrefix}${suffix}`;
	const canHumanRatify = userRole === "editor" || userRole === "admin";
	const isNonPending = Boolean(
		proposal.status && proposal.status !== "PENDING",
	);
	const opsCount = Array.isArray(proposal.operations)
		? proposal.operations.length
		: (proposal.operations_count ?? proposal.ops_count ?? 0);
	const hasZeroOps = opsCount === 0;
	const acceptDisabled =
		isNonPending || !canHumanRatify || hasZeroOps || hasUnsavedDraftEdits;
	const rebaseDisabled =
		isNonPending || !canHumanRatify || hasUnsavedDraftEdits;

	return (
		<>
			{!isNonPending && canHumanRatify && (
				<div className="proposal-ratification-controls">
					<input
						type="text"
						className="form-input"
						placeholder="Optional ratification rationale..."
						value={rationaleValue}
						onChange={(e) => onRationaleChange(e.target.value)}
						data-testid={tid(
							`proposal-accept-rationale-${proposal.proposal_id}`,
						)}
					/>
					{userRole === "admin" && (
						<>
							<label className="checkbox-sm">
								<input
									type="checkbox"
									checked={selfOverrideChecked}
									onChange={(e) => onSelfOverrideChange(e.target.checked)}
									data-testid={tid(
										`proposal-self-accept-override-${proposal.proposal_id}`,
									)}
								/>
								<span>Admin self-acceptance override</span>
							</label>
							{onLowConfidenceWaiverChange && (
								<label className="checkbox-sm">
									<input
										type="checkbox"
										checked={lowConfidenceWaiverChecked}
										onChange={(e) =>
											onLowConfidenceWaiverChange(e.target.checked)
										}
										data-testid={tid(
											`proposal-low-conf-waiver-${proposal.proposal_id}`,
										)}
									/>
									<span>
										Admin low-confidence grounding waiver (ONT-EVO-002)
									</span>
								</label>
							)}
						</>
					)}
					{hasUnsavedDraftEdits && (
						<output
							className="proposal-empty-notice"
							data-testid={tid(`unsaved-draft-warning-${proposal.proposal_id}`)}
						>
							Unsaved proposal edits detected — click Save Edited Proposal or
							Reset Edits before accepting or rebasing.
						</output>
					)}
				</div>
			)}
			<div className="proposal-card-actions">
				{showInspectButton && onInspect && (
					<button
						type="button"
						className="btn-secondary btn-xs"
						onClick={onInspect}
						data-testid={tid(`inspect-proposal-btn-${proposal.proposal_id}`)}
					>
						View Details
					</button>
				)}
				<button
					type="button"
					className="btn-primary btn-xs"
					onClick={onAccept}
					disabled={acceptDisabled}
					title={
						hasUnsavedDraftEdits
							? "Save or reset your proposal edits before accepting"
							: hasZeroOps
								? "0 operations — upload and parse source documents first"
								: undefined
					}
					data-testid={tid(`accept-proposal-btn-${proposal.proposal_id}`)}
				>
					{acceptLabel}
				</button>
				<button
					type="button"
					className="btn-secondary btn-xs"
					onClick={onRebase}
					disabled={rebaseDisabled}
					title={
						hasUnsavedDraftEdits
							? "Save or reset your proposal edits before rebasing"
							: undefined
					}
					data-testid={tid(`rebase-proposal-btn-${proposal.proposal_id}`)}
				>
					Rebase
				</button>
				<button
					type="button"
					className="btn-danger btn-xs"
					onClick={onReject}
					disabled={isNonPending || !canHumanRatify}
					data-testid={tid(`reject-proposal-btn-${proposal.proposal_id}`)}
				>
					Reject
				</button>
			</div>
		</>
	);
};
