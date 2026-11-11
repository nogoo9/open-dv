/**
 * Quality Scorecard & Steward Feedback Panel (Task 5D.6, #227)
 * (apps/opendv-web-workbench/src/ontology/components/QualityFeedbackPanel.tsx)
 *
 * Implements:
 * - Contract rule checklist (BM25 grounding >= 0.6500, ONT-EVO-001, DAG acyclicity, YAGNI)
 * - Metric gauges for grounding, attribute coverage, and concept density
 * - Administrative waiver inputs (rationale >= 10 chars, waiver key authorization)
 * - 5-star rating feedback recorder persisting to feedback.jsonl
 */

import type React from "react";
import { useEffect, useRef, useState } from "react";
import { useStudioStore } from "../store/studio-store";
import type { QualityCheck } from "../store/types";

export interface QualityFeedbackPanelProps {
	className?: string;
	onWaiverSubmitted?: (rationale: string) => void;
	onFeedbackSubmitted?: (rating: number, comment: string) => void;
}

export function getCheckStatusBadge(status: QualityCheck["status"]) {
	switch (status) {
		case "PASS":
			return { label: "PASS", className: "badge-pass", icon: "✓" };
		case "WARN":
			return { label: "WARN", className: "badge-warn", icon: "⚠" };
		case "FAIL":
			return { label: "FAIL", className: "badge-fail", icon: "✕" };
		default:
			return { label: status, className: "badge-default", icon: "•" };
	}
}

export const QualityFeedbackPanel: React.FC<QualityFeedbackPanelProps> = ({
	className = "",
	onWaiverSubmitted,
	onFeedbackSubmitted,
}) => {
	const activeSession = useStudioStore((s) => s.activeSession);
	const nodes = useStudioStore((s) => s.nodes);
	const qualityScorecard = useStudioStore((s) => s.qualityScorecard);
	const submitWaiver = useStudioStore((s) => s.submitWaiver);
	const submitFeedback = useStudioStore((s) => s.submitFeedback);

	// Waiver form state
	const [waiverRequested, setWaiverRequested] = useState(
		qualityScorecard.waiver_applied,
	);
	const [waiverRationale, setWaiverRationale] = useState(
		qualityScorecard.waiver_rationale,
	);
	const [waiverKey, setWaiverKey] = useState("");
	const [waiverError, setWaiverError] = useState<string | null>(null);
	const [isSubmittingWaiver, setIsSubmittingWaiver] = useState(false);

	// Feedback state
	const [rating, setRating] = useState(qualityScorecard.feedback_rating || 0);
	const [hoverRating, setHoverRating] = useState(0);
	const [comment, setComment] = useState(
		qualityScorecard.feedback_comment || "",
	);
	const [isSubmittingFeedback, setIsSubmittingFeedback] = useState(false);

	const activeSessionId = activeSession?.session_id;
	const prevSessionIdRef = useRef<string | undefined>(activeSessionId);
	const prevWaiverAppliedRef = useRef(qualityScorecard.waiver_applied);

	useEffect(() => {
		if (prevSessionIdRef.current !== activeSessionId) {
			prevSessionIdRef.current = activeSessionId;
			setWaiverRequested(qualityScorecard.waiver_applied);
			setWaiverRationale(qualityScorecard.waiver_rationale || "");
			setRating(qualityScorecard.feedback_rating || 0);
			setComment(qualityScorecard.feedback_comment || "");
			setWaiverKey("");
			setWaiverError(null);
		}
	}, [activeSessionId, qualityScorecard]);

	useEffect(() => {
		if (!prevWaiverAppliedRef.current && qualityScorecard.waiver_applied) {
			setWaiverRequested(true);
			setWaiverRationale(qualityScorecard.waiver_rationale || "");
		}
		prevWaiverAppliedRef.current = qualityScorecard.waiver_applied;
	}, [qualityScorecard.waiver_applied, qualityScorecard.waiver_rationale]);

	const handleWaiverSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		if (!waiverKey.trim()) {
			setWaiverError("Waiver key is mandatory for steward authorization.");
			return;
		}
		if (!waiverRationale || waiverRationale.trim().length < 10) {
			setWaiverError("Waiver rationale must be at least 10 characters.");
			return;
		}

		setIsSubmittingWaiver(true);
		setWaiverError(null);
		try {
			const ok = await submitWaiver(waiverRationale.trim(), waiverKey.trim());
			if (ok) {
				setWaiverKey("");
				if (onWaiverSubmitted) {
					onWaiverSubmitted(waiverRationale.trim());
				}
			} else {
				setWaiverError("Waiver submission rejected by server or preconditions not met.");
			}
		} catch (err) {
			setWaiverError(String(err));
		} finally {
			setIsSubmittingWaiver(false);
		}
	};

	const handleFeedbackSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		if (rating === 0) return;

		setIsSubmittingFeedback(true);
		try {
			const ok = await submitFeedback(rating, comment.trim());
			if (ok && onFeedbackSubmitted) {
				onFeedbackSubmitted(rating, comment.trim());
			}
		} catch (err) {
			console.error("Steward feedback submission error:", err);
		} finally {
			setIsSubmittingFeedback(false);
		}
	};

	const hasNodes = nodes.length > 0;
	const groundedNodes = nodes.filter(
		(n) => n.grounding_status === "GROUNDED",
	).length;
	const groundingPct = hasNodes
		? (qualityScorecard.grounding_score !== undefined
				? qualityScorecard.grounding_score * 100
				: (groundedNodes / nodes.length) * 100)
		: null;

	return (
		<div
			className={`quality-feedback-panel ${className}`}
			data-testid="quality-feedback-panel"
		>
			{/* Panel Header */}
			<div className="panel-header">
				<h2 className="panel-title">Quality & Governance</h2>
				<p className="panel-subtitle">
					Verify architectural rules, contract compliance, grounding thresholds,
					and steward feedback.
				</p>
			</div>

			{/* Metric Gauges */}
			<div className="metric-gauges-grid" data-testid="metric-gauges">
				<div className="gauge-card">
					<span className="gauge-label">Grounding Ratio</span>
					<div className="gauge-value-row">
						<span className="gauge-number" data-testid="grounding-gauge-value">
							{groundingPct !== null ? `${groundingPct.toFixed(0)}%` : "N/A"}
						</span>
						<span className="gauge-sub">
							{hasNodes ? `(${groundedNodes}/${nodes.length})` : "(No nodes)"}
						</span>
					</div>
					<div className="gauge-meter-track">
						<div
							className={`gauge-meter-fill ${
								groundingPct !== null && groundingPct >= 65.0
									? "fill-pass"
									: "fill-fail"
							}`}
							style={{
								width: `${Math.min(100, Math.max(0, groundingPct ?? 0))}%`,
							}}
						/>
					</div>
					<span className="gauge-target">Target: ≥ 65% (BM25)</span>
				</div>

				<div className="gauge-card">
					<span className="gauge-label">Acyclicity</span>
					<div className="gauge-value-row">
						<span
							className={
								qualityScorecard.dag_acyclic
									? "gauge-status-pass"
									: "gauge-status-fail"
							}
							data-testid="acyclicity-gauge-value"
						>
							{qualityScorecard.dag_acyclic ? "PASS" : "FAIL"}
						</span>
					</div>
					<p className="gauge-sub">
						{qualityScorecard.dag_acyclic
							? "Zero DAG inheritance cycles"
							: "DAG cycle detected (ONT-DAG-001)"}
					</p>
				</div>

				<div className="gauge-card">
					<span className="gauge-label">Table Contracts</span>
					<div className="gauge-value-row">
						<span
							className={
								qualityScorecard.deployed_contracts_ok
									? "gauge-status-pass"
									: "gauge-status-fail"
							}
							data-testid="table-contracts-gauge-value"
						>
							{qualityScorecard.deployed_contracts_ok
								? "COMPLIANT"
								: "VIOLATION"}
						</span>
					</div>
					<p className="gauge-sub">
						{qualityScorecard.deployed_contracts_ok
							? "ONT-EVO-001 preserved"
							: "Breaking schema changes detected"}
					</p>
				</div>
			</div>

			{/* Contract Rule Checklist */}
			<div className="rules-checklist-section" data-testid="rules-checklist">
				<h3 className="section-title">Contract Rules Checklist</h3>
				<div className="rules-list">
					{qualityScorecard.checks.map((check) => {
						const badge = getCheckStatusBadge(check.status);
						return (
							<div
								key={check.rule_id}
								className={`rule-row rule-${check.status.toLowerCase()}`}
								data-testid={`rule-row-${check.rule_id}`}
							>
								<div className="rule-badge-col">
									<span className={`status-badge ${badge.className}`}>
										{badge.icon} {badge.label}
									</span>
								</div>
								<div className="rule-info-col">
									<strong className="rule-name">
										[{check.rule_id}] {check.rule_name}
									</strong>
									<p className="rule-details">{check.details}</p>
								</div>
							</div>
						);
					})}
				</div>
			</div>

			{/* Administrative Waiver Section */}
			<div className="waiver-section" data-testid="waiver-section">
				<h3 className="section-title">Administrative Steward Waiver</h3>
				<p className="section-desc">
					Steward/Admin override for ungrounded concepts or low-confidence
					proposals requiring explicit rationale (≥ 10 chars).
				</p>

				{qualityScorecard.waiver_applied && (
					<div
						className="waiver-applied-badge"
						data-testid="waiver-applied-badge"
					>
						✓ Active Waiver Authorized ({qualityScorecard.waiver_rationale})
					</div>
				)}

				<form onSubmit={handleWaiverSubmit} className="waiver-form">
					<label className="waiver-toggle-label">
						<input
							type="checkbox"
							checked={waiverRequested}
							onChange={(e) => setWaiverRequested(e.target.checked)}
							data-testid="waiver-requested-checkbox"
						/>
						<span>Request Low-Confidence / Grounding Waiver</span>
					</label>

					{waiverRequested && (
						<div className="waiver-fields">
							<div className="form-group">
								<label htmlFor="waiver-rationale-input">
									Waiver Rationale (minimum 10 characters):
								</label>
								<textarea
									id="waiver-rationale-input"
									className="form-textarea"
									rows={3}
									placeholder="Provide specific enterprise justification and regulatory grounding citations..."
									value={waiverRationale}
									onChange={(e) => {
										setWaiverRationale(e.target.value);
										if (e.target.value.trim().length >= 10) {
											setWaiverError(null);
										}
									}}
									data-testid="waiver-rationale-input"
								/>
								<div className="char-count-row">
									<span
										className={`char-count ${waiverRationale.trim().length < 10 ? "text-fail" : "text-pass"}`}
									>
										{waiverRationale.trim().length} / 10 chars minimum
									</span>
								</div>
							</div>

							<div className="form-group">
								<label htmlFor="waiver-key-input">
									Admin Waiver Key / Credential:
								</label>
								<input
									id="waiver-key-input"
									type="password"
									className="form-input"
									placeholder="Enter admin credential or waiver signing key"
									value={waiverKey}
									onChange={(e) => setWaiverKey(e.target.value)}
									data-testid="waiver-key-input"
								/>
							</div>

							{waiverError && (
								<div
									className="error-banner"
									role="alert"
									data-testid="waiver-error-banner"
								>
									<span>{waiverError}</span>
								</div>
							)}

							<button
								type="submit"
								className="btn-primary"
								disabled={
									isSubmittingWaiver || waiverRationale.trim().length < 10
								}
								data-testid="submit-waiver-btn"
							>
								{isSubmittingWaiver ? "Authorizing..." : "Authorize Waiver"}
							</button>
						</div>
					)}
				</form>
			</div>

			{/* Steward Feedback Section */}
			<div className="feedback-section" data-testid="feedback-section">
				<h3 className="section-title">Steward Experience Rating</h3>
				<p className="section-desc">
					Rate semantic accuracy and grounding confidence for session
					feedback.jsonl audit trail.
				</p>

				<form onSubmit={handleFeedbackSubmit} className="feedback-form">
					{/* 5-Star Rating Widget */}
					<div
						className="star-rating-widget"
						role="group"
						aria-label="Rating from 1 to 5 stars"
						data-testid="star-rating-widget"
					>
						{[1, 2, 3, 4, 5].map((star) => (
							<button
								key={star}
								type="button"
								className={`star-btn ${star <= (hoverRating || rating) ? "star-active" : ""}`}
								onClick={() => setRating(star)}
								onMouseEnter={() => setHoverRating(star)}
								onMouseLeave={() => setHoverRating(0)}
								aria-label={`${star} star${star > 1 ? "s" : ""}`}
								data-testid={`star-${star}`}
							>
								★
							</button>
						))}
						<span className="rating-text" data-testid="rating-display-text">
							{rating > 0 ? `${rating} of 5 stars` : "Select a rating"}
						</span>
					</div>

					<div className="form-group">
						<label htmlFor="feedback-comment-input">
							Steward Notes / Feedback:
						</label>
						<textarea
							id="feedback-comment-input"
							className="form-textarea"
							rows={2}
							placeholder="Comments on LLM agent reasoning or terminology accuracy..."
							value={comment}
							onChange={(e) => setComment(e.target.value)}
							data-testid="feedback-comment-input"
						/>
					</div>

					<button
						type="submit"
						className="btn-secondary"
						disabled={rating === 0 || isSubmittingFeedback}
						data-testid="submit-feedback-btn"
					>
						{isSubmittingFeedback ? "Saving..." : "Submit Steward Feedback"}
					</button>

					{qualityScorecard.feedback_submitted && (
						<div
							className="feedback-submitted-pill"
							data-testid="feedback-submitted-pill"
						>
							✓ Recorded {qualityScorecard.feedback_rating}-star rating to
							feedback.jsonl
						</div>
					)}
				</form>
			</div>
		</div>
	);
};
