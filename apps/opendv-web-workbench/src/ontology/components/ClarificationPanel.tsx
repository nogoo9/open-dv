/**
 * Clarification Q&A Panel & Answer Submissions (Task 5D.3, #224)
 * (apps/opendv-web-workbench/src/ontology/components/ClarificationPanel.tsx)
 *
 * Implements:
 * - Stack of domain ambiguity question cards from @domain-clarifier
 * - Semantic rationale options with interactive selection
 * - Certainty badge (HIGH / MEDIUM / LOW)
 * - Custom answer textarea for free-text domain rationale
 * - Evidence citations viewer with source attribution and text snippets
 * - Optimistic locking / OCC answer submission
 * - Instant answered state updates with timestamp
 */

import type React from "react";
import { useCallback, useState } from "react";
import { useStudioStore } from "../store/studio-store";
import type {
	ClarificationAnswer,
	ClarificationQuestion,
} from "../store/types";

export interface ClarificationPanelProps {
	className?: string;
	onAnswerSubmitted?: (answer: ClarificationAnswer) => void;
}

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

export const ClarificationPanel: React.FC<ClarificationPanelProps> = ({
	className = "",
	onAnswerSubmitted,
}) => {
	const activeSession = useStudioStore((s) => s.activeSession);
	const userRole = useStudioStore((s) => s.userRole);
	const clarifications = useStudioStore((s) => s.clarifications);
	const answers = useStudioStore((s) => s.answers);
	const proposals = useStudioStore((s) => s.proposals);
	const isLoadingClarifications = useStudioStore(
		(s) => s.isLoadingClarifications,
	);
	const isClarifying = useStudioStore((s) => s.isClarifying);
	const isConstructing = useStudioStore((s) => s.isConstructing);
	const isLoadingProposals = useStudioStore((s) => s.isLoadingProposals);
	const loadClarifications = useStudioStore((s) => s.loadClarifications);
	const submitAnswer = useStudioStore((s) => s.submitAnswer);
	const setAnswerDraft = useStudioStore((s) => s.setAnswerDraft);
	const startClarify = useStudioStore((s) => s.startClarify);
	const startConstruct = useStudioStore((s) => s.startConstruct);
	const loadProposals = useStudioStore((s) => s.loadProposals);
	const acceptProposal = useStudioStore((s) => s.acceptProposal);
	const rejectProposal = useStudioStore((s) => s.rejectProposal);
	const rebaseProposal = useStudioStore((s) => s.rebaseProposal);

	// Local drafts state
	const [activeDrafts, setActiveDrafts] = useState<
		Record<string, { selectedOptions: string[]; customText: string }>
	>({});
	const [submittingIds, setSubmittingIds] = useState<Record<string, boolean>>(
		{},
	);
	const [submissionErrors, setSubmissionErrors] = useState<
		Record<string, string>
	>({});

	const pendingCount = clarifications.filter(
		(q) => q.status === "PENDING",
	).length;
	const answeredCount = clarifications.filter(
		(q) => q.status === "ANSWERED",
	).length;
	const canHumanRatify = userRole === "editor" || userRole === "admin";
	const canTriggerJobs = userRole !== "viewer";

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

		const updated = {
			...currentDraft,
			selectedOptions: nextSelected,
		};

		setActiveDrafts((prev) => ({ ...prev, [questionId]: updated }));
		setAnswerDraft(questionId, nextSelected, updated.customText);
	};

	const handleCustomTextChange = (questionId: string, text: string) => {
		const currentDraft = resolveDraft(questionId);

		const updated = {
			...currentDraft,
			customText: text,
		};

		setActiveDrafts((prev) => ({ ...prev, [questionId]: updated }));
		setAnswerDraft(questionId, updated.selectedOptions, text);
	};

	const handleSubmit = async (question: ClarificationQuestion) => {
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

	return (
		<div
			className={`clarification-panel ${className}`}
			data-testid="clarification-panel"
		>
			{/* Panel Header */}
			<div className="panel-header">
				<div className="panel-title-group">
					<h2 className="panel-title">Clarification Q&A</h2>
					<div className="panel-badges">
						<span
							className="badge badge-pending"
							aria-label={`${pendingCount} pending questions`}
							data-testid="pending-count-badge"
						>
							{pendingCount} Pending
						</span>
						<span
							className="badge badge-answered"
							aria-label={`${answeredCount} answered questions`}
							data-testid="answered-count-badge"
						>
							{answeredCount} Answered
						</span>
					</div>
				</div>
				<p className="panel-subtitle">
					Review and resolve domain ambiguities identified by @domain-clarifier.
				</p>
				<div className="clarification-toolbar-actions">
					<button
						type="button"
						className="btn-secondary btn-sm refresh-clarifications-btn"
						onClick={() => void loadClarifications()}
						disabled={isLoadingClarifications || !activeSession}
						data-testid="refresh-clarifications-btn"
					>
						{isLoadingClarifications ? "Loading..." : "Refresh Questions"}
					</button>
					<button
						type="button"
						className="btn-secondary btn-sm"
						onClick={() => void startClarify()}
						disabled={isClarifying || !activeSession || !canTriggerJobs}
						data-testid="start-clarify-btn"
					>
						{isClarifying ? "Clarifying..." : "Generate Clarifications"}
					</button>
					<button
						type="button"
						className="btn-primary btn-sm"
						onClick={() => void startConstruct()}
						disabled={isConstructing || !activeSession || !canTriggerJobs}
						data-testid="start-construct-btn"
					>
						{isConstructing ? "Constructing..." : "Construct Proposal"}
					</button>
				</div>
			</div>

			{/* Candidate Proposals Section */}
			<div className="proposals-section" data-testid="proposals-section">
				<div className="panel-title-group">
					<h3 className="section-title">
						Candidate Proposals ({proposals.length})
					</h3>
					<button
						type="button"
						className="btn-secondary btn-xs"
						onClick={() => void loadProposals()}
						disabled={isLoadingProposals || !activeSession}
						data-testid="refresh-proposals-btn"
					>
						{isLoadingProposals ? "Loading..." : "Refresh Proposals"}
					</button>
				</div>
				{proposals.length > 0 && (
					<div className="proposals-list" data-testid="proposals-list">
						{proposals.map((prop) => (
							<div
								key={prop.proposal_id}
								className="proposal-card"
								data-testid={`proposal-card-${prop.proposal_id}`}
							>
								<div className="proposal-card-header">
									<strong>{prop.proposal_id}</strong>
									<span className="badge">{prop.status}</span>
								</div>
								{prop.rationale && (
									<p className="proposal-rationale">{prop.rationale}</p>
								)}
								<div className="proposal-card-actions">
									<button
										type="button"
										className="btn-primary btn-xs"
										onClick={() => void acceptProposal(prop.proposal_id)}
										disabled={prop.status === "ACCEPTED" || !canHumanRatify}
										data-testid={`accept-proposal-btn-${prop.proposal_id}`}
									>
										Accept
									</button>
									<button
										type="button"
										className="btn-secondary btn-xs"
										onClick={() => void rebaseProposal(prop.proposal_id)}
										disabled={prop.status === "ACCEPTED" || !canHumanRatify}
										data-testid={`rebase-proposal-btn-${prop.proposal_id}`}
									>
										Rebase
									</button>
									<button
										type="button"
										className="btn-danger btn-xs"
										onClick={() => void rejectProposal(prop.proposal_id)}
										disabled={
											prop.status === "ACCEPTED" ||
											prop.status === "REJECTED" ||
											!canHumanRatify
										}
										data-testid={`reject-proposal-btn-${prop.proposal_id}`}
									>
										Reject
									</button>
								</div>
							</div>
						))}
					</div>
				)}
			</div>

			{/* Questions Card Stack */}
			<div
				className="clarification-questions-list"
				data-testid="clarifications-list"
			>
				{clarifications.length === 0 ? (
					<div
						className="empty-clarifications-box"
						data-testid="empty-clarifications-box"
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
								data-testid={`question-card-${q.question_id}`}
							>
								{/* Card Header */}
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
											data-testid={`answered-badge-${q.question_id}`}
										>
											✓ Answered
										</span>
									)}
								</div>

								{/* Question Text */}
								<div className="question-body">
									<p className="question-text">{q.question_text}</p>
								</div>

								{/* Evidence Citations */}
								{q.evidence_citations && q.evidence_citations.length > 0 && (
									<div
										className="citations-box"
										data-testid={`citations-${q.question_id}`}
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

								{/* Rationale Options */}
								{q.rationale_options && q.rationale_options.length > 0 && (
									<div className="options-section">
										<span className="options-title">
											Suggested Semantic Rationales:
										</span>
										<div className="options-grid">
											{q.rationale_options.map((opt) => {
												const isSelected =
													currentDraft.selectedOptions.includes(opt.id);
												return (
													<button
														key={opt.id}
														type="button"
														className={`option-btn ${isSelected ? "option-btn-selected" : ""}`}
														onClick={() =>
															handleOptionToggle(q.question_id, opt.id)
														}
														aria-pressed={isSelected}
														data-testid={`option-btn-${q.question_id}-${opt.id}`}
													>
														<span className="option-label">{opt.label}</span>
														{opt.description && (
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

								{/* Custom Answer Input */}
								<div className="custom-answer-section">
									<label
										htmlFor={`custom-input-${q.question_id}`}
										className="custom-answer-label"
									>
										Custom Domain Rationale:
									</label>
									<textarea
										id={`custom-input-${q.question_id}`}
										className="custom-answer-textarea"
										rows={2}
										placeholder="Provide additional enterprise domain context or regulatory constraints..."
										value={currentDraft.customText}
										onChange={(e) =>
											handleCustomTextChange(q.question_id, e.target.value)
										}
										data-testid={`custom-answer-input-${q.question_id}`}
									/>
								</div>

								{submissionErrors[q.question_id] && (
									<div
										className="error-banner"
										role="alert"
										data-testid={`answer-error-${q.question_id}`}
									>
										<span>{submissionErrors[q.question_id]}</span>
									</div>
								)}

								{/* Actions */}
								<div className="question-actions">
									<button
										type="button"
										className="btn-primary btn-sm submit-answer-btn"
										onClick={() => handleSubmit(q)}
										disabled={
											!canHumanRatify ||
											isSubmitting ||
											(currentDraft.selectedOptions.length === 0 &&
												!currentDraft.customText.trim())
										}
										data-testid={`submit-answer-btn-${q.question_id}`}
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
		</div>
	);
};
